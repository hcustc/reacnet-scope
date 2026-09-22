from __future__ import annotations

import json
import time
from typing import Any

import reacnet_scope.dir_browser as dir_browser
from reacnet_scope import services as svc
from scripts.webapp_dash import callbacks as cb
from scripts.webapp_dash.app import create_app


def _payload(
    client: Any,
    *,
    output_contains: str,
    changed: str,
    inputs: dict[str, Any],
    states: dict[str, Any],
) -> dict[str, Any]:
    dependencies = [
        item
        for item in client.get("/_dash-dependencies").get_json()
        if output_contains in str(item.get("output") or "")
    ]
    dependency = next(
        (
            item
            for item in dependencies
            if changed
            in {
                f"{value['id']}.{value['property']}"
                for value in item["inputs"]
                if isinstance(value["id"], str)
            }
        ),
        dependencies[0],
    )
    output_spec = dependency["output"]
    if output_spec.startswith(".."):
        outputs: Any = [
            {
                "id": token.split(".")[0],
                "property": token.split(".")[1].split("@")[0],
            }
            for token in output_spec.strip(".").split("...")
        ]
    else:
        outputs = {
            "id": output_spec.split(".")[0],
            "property": output_spec.split(".")[1].split("@")[0],
        }

    def value_for(item: dict[str, Any], values: dict[str, Any]) -> Any:
        component_id = item["id"]
        key = component_id if isinstance(component_id, str) else json.dumps(component_id)
        return values.get(
            f"{key}.{item['property']}",
            values.get(key),
        )

    return {
        "output": output_spec,
        "outputs": outputs,
        "changedPropIds": [changed],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": value_for(item, inputs),
            }
            for item in dependency["inputs"]
        ],
        "state": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": value_for(item, states),
            }
            for item in dependency["state"]
        ],
    }


def _dependency_outputs(dependency: dict[str, Any]) -> set[str]:
    output_spec = str(dependency.get("output") or "")
    tokens = (
        output_spec.strip(".").split("...")
        if output_spec.startswith("..")
        else [output_spec]
    )
    return {token.split("@", 1)[0] for token in tokens}


def test_validation_result_is_resolved_by_a_distinct_dash_callback() -> None:
    """Dataset switching must form a one-way request-to-result graph."""
    client = create_app().server.test_client()
    dependencies = client.get("/_dash-dependencies").get_json()

    transaction_writers = [
        item
        for item in dependencies
        if "dataset-switch-transaction.data" in _dependency_outputs(item)
    ]
    click_writer = next(
        item
        for item in transaction_writers
        if any(value["id"] == "data-apply-btn" for value in item["inputs"])
    )
    validation_writer = next(
        item
        for item in transaction_writers
        if any(
            value["id"] == "dataset-switch-validation"
            for value in item["inputs"]
        )
    )

    assert click_writer["output"] != validation_writer["output"]
    assert "dataset-switch-request.data" in _dependency_outputs(click_writer)

    validation_worker = next(
        item
        for item in dependencies
        if _dependency_outputs(item) == {"dataset-switch-validation.data"}
    )
    worker_inputs = {value["id"] for value in validation_worker["inputs"]}
    assert worker_inputs == {"dataset-switch-request"}
    assert "dataset-switch-transaction" not in worker_inputs


def test_explicit_apply_starts_visible_background_validation(monkeypatch) -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    expected = {
        "state": "validating",
        "request_id": "request-1",
        "candidate": candidate,
        "origin": {},
        "started_ns": 1,
        "deadline_ns": 2,
    }
    monkeypatch.setattr(svc, "begin_dataset_switch", lambda *_args, **_kwargs: expected)

    def fail_if_called_inline(*_args):
        raise AssertionError("validation must not run in the click request")

    monkeypatch.setattr(svc, "validate_dataset_candidate", fail_if_called_inline)
    client = create_app().server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "dataset-switch-transaction.data" in _dependency_outputs(item)
        and any(value["id"] == "data-apply-btn" for value in item["inputs"])
    )
    assert dependency.get("background") is None
    assert "dataset-switch-validation" not in {
        item["id"] for item in dependency["inputs"]
    }
    validation_dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if item.get("output") == "dataset-switch-validation.data"
    )
    assert validation_dependency.get("background") is not None
    assert validation_dependency["inputs"] == [
        {"id": "dataset-switch-request", "property": "data"}
    ]

    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="data-apply-btn.n_clicks",
            inputs={
                "data-apply-btn": 1,
                "dir-browser-cancel-btn": 0,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
                "dataset-switch-validation": {},
            },
            states={"dataset-switch-transaction": {}},
        ),
    )

    transaction = response.get_json()["response"]["dataset-switch-transaction"][
        "data"
    ]
    assert transaction == expected


def test_background_validation_returns_a_request_bound_result(monkeypatch) -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    validation = {
        **candidate,
        "label": "internal-name",
        "dataset_id": "dataset-new",
        "source_revision": {"fingerprint": "revision-new", "artifacts": []},
        "artifacts": {},
        "capabilities": {},
        "readiness": {},
        "analysis_capabilities": {},
    }
    monkeypatch.setattr(svc, "validate_dataset_candidate", lambda *_args: validation)
    app = create_app()
    worker = app.callback_map["dataset-switch-validation.data"]["callback"].__wrapped__

    result = worker(
        {
            "state": "validating",
            "request_id": "request-1",
            "candidate": candidate,
        }
    )

    assert result["request_id"] == "request-1"
    assert result["ok"] is True
    assert result["validation"]["dataset_id"] == "dataset-new"
    assert result["validation"]["label"] == "new"


def test_visible_loading_state_disables_apply_and_explains_progress() -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    transaction = {
        "state": "validating",
        "request_id": "request-1",
        "candidate": candidate,
        "origin": {},
    }
    client = create_app().server.test_client()

    actions = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="data-current-refresh-btn.children",
            changed="dataset-switch-transaction.data",
            inputs={
                "app-store": {},
                "dataset-browser-candidate": candidate,
                "dataset-switch-transaction": transaction,
            },
            states={},
        ),
    ).get_json()["response"]
    reason = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="data-apply-reason.children",
            changed="dataset-switch-transaction.data",
            inputs={
                "app-store": {},
                "dataset-browser-candidate": candidate,
                "dataset-switch-transaction": transaction,
            },
            states={},
        ),
    ).get_json()["response"]

    assert actions["data-apply-btn"]["children"] == "正在检查…"
    assert actions["data-apply-btn"]["disabled"] is True
    assert "正在检查文件" in reason["data-apply-reason"]["children"]


def test_empty_data_workspace_is_an_onboarding_state_not_an_error_report() -> None:
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="data-candidate-summary.children",
            changed="app-store.data",
            inputs={
                "dataset-browser-candidate": None,
                "app-store": cb.initial_store(),
            },
            states={},
        ),
    )

    assert response.status_code == 200
    rendered = json.dumps(response.get_json()["response"], ensure_ascii=False)
    assert "尚未加载RNG 数据" in rendered
    assert "missing-source" not in rendered
    assert "缺少源数据" not in rendered


def test_use_dataset_finishes_two_phase_validation_and_commits_current_context(
    tmp_path,
    monkeypatch,
) -> None:
    """A ready candidate must not leave the UI stuck in ``validating``."""
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base = tmp_path / "ready.lammpstrj"
    base.with_suffix(base.suffix + ".reactionabcd").touch()
    base.with_suffix(base.suffix + ".species").touch()
    candidate = {
        "folder": str(tmp_path),
        "base": str(base),
        "label": base.name,
    }
    client = create_app().server.test_client()

    started = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="data-apply-btn.n_clicks",
            inputs={
                "data-apply-btn": 1,
                "dir-browser-cancel-btn": 0,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
            },
            states={"dataset-switch-transaction": {}},
        ),
    )
    transaction = started.get_json()["response"]["dataset-switch-transaction"][
        "data"
    ]
    assert transaction["state"] == "validating"

    validation = svc.validate_dataset_candidate(
        str(candidate["folder"]),
        str(candidate["base"]),
    )
    finished = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="dataset-switch-validation.data",
            inputs={
                "data-apply-btn": 1,
                "dir-browser-cancel-btn": 0,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
                "dataset-switch-validation": {
                    "request_id": transaction["request_id"],
                    "ok": True,
                    "validation": validation,
                    "completed_ns": time.time_ns(),
                },
            },
            states={"dataset-switch-transaction": transaction},
        ),
    )
    transaction = finished.get_json()["response"]["dataset-switch-transaction"][
        "data"
    ]
    assert transaction["state"] == "succeeded"

    committed = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-context-commit.data",
            changed="dataset-switch-transaction.data",
            inputs={"dataset-switch-transaction": transaction},
            states={"app-store": {}, "recent-datasets": []},
        ),
    )
    current = committed.get_json()["response"]["app-store"]["data"]
    assert current["base"] == str(base)
    assert current["context_state"] == "active"


def test_validation_failure_retains_candidate_and_old_current() -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    transaction = svc.begin_dataset_switch(candidate)
    client = create_app().server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="dataset-switch-validation.data",
            inputs={
                "data-apply-btn": 1,
                "dir-browser-cancel-btn": 0,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
                "dataset-switch-validation": {
                    "request_id": transaction["request_id"],
                    "ok": False,
                    "reason": "candidate_missing",
                    "message": "所选RNG 数据已不存在。当前RNG 数据未改变；请重新选择。",
                    "completed_ns": time.time_ns(),
                },
            },
            states={"dataset-switch-transaction": transaction},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]["dataset-switch-transaction"]["data"]
    assert result["state"] == "failed"
    assert result["candidate"] == candidate
    assert "当前RNG 数据未改变" in result["message"]
    assert "app-store" not in response.get_json()["response"]


def test_repeat_submit_is_blocked_while_validation_is_active() -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    transaction = {
        "state": "validating",
        "request_id": "request-1",
        "candidate": candidate,
        "origin": {},
    }
    client = create_app().server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="data-apply-btn.n_clicks",
            inputs={
                "data-apply-btn": 2,
                "dir-browser-cancel-btn": 0,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
            },
            states={"dataset-switch-transaction": transaction},
        ),
    )

    assert response.status_code == 204


def test_cancel_supersedes_an_active_request_and_ignores_its_late_result() -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    transaction = {
        "state": "validating",
        "request_id": "request-1",
        "candidate": candidate,
        "origin": {"page": "species", "trigger": "open-data-modal"},
    }
    client = create_app().server.test_client()
    cancelled_response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="dir-browser-cancel-btn.n_clicks",
            inputs={
                "data-apply-btn": 1,
                "dir-browser-cancel-btn": 1,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
            },
            states={"dataset-switch-transaction": transaction},
        ),
    )
    assert cancelled_response.status_code == 200
    cancelled = cancelled_response.get_json()["response"][
        "dataset-switch-transaction"
    ]["data"]
    assert cancelled["state"] == "superseded"

    late_response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="dataset-switch-validation.data",
            inputs={
                "data-apply-btn": 1,
                "dir-browser-cancel-btn": 1,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
                "dataset-switch-validation": {
                    "request_id": "request-1",
                    "ok": True,
                    "validation": {"dataset_id": "dataset-new"},
                    "completed_ns": time.time_ns(),
                },
            },
            states={"dataset-switch-transaction": cancelled},
        ),
    )
    assert late_response.status_code == 204


def test_return_to_index_management_supersedes_browser_request() -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-switch-transaction.data",
            changed="data-browser-index-btn.n_clicks",
            inputs={
                "data-apply-btn": 0,
                "data-browser-index-btn": 1,
                "dir-browser-cancel-btn": 0,
                "dataset-browser-candidate": candidate,
                "page-store": {"page": "data-management"},
            },
            states={
                "dataset-switch-transaction": {
                    "state": "candidate-selected",
                    "candidate": candidate,
                }
            },
        ),
    )

    assert response.status_code == 200
    transaction = response.get_json()["response"]["dataset-switch-transaction"][
        "data"
    ]
    assert transaction["state"] == "superseded"
    assert transaction["reason"] == "returned_to_index_management"


def test_successful_switch_commits_context_resets_results_and_opens_overview(
    monkeypatch,
) -> None:
    validation = {
        "folder": "/data",
        "base": "/data/new",
        "label": "new",
        "dataset_id": "dataset-new",
        "source_revision": {"fingerprint": "revision-new", "artifacts": []},
        "artifacts": {"reaction": "/data/new.reactionabcd"},
        "capabilities": {"reaction": True},
        "readiness": {},
        "ready_count": 1,
    }
    transaction = {
        "state": "succeeded",
        "request_id": "request-1",
        "candidate": {"folder": "/data", "base": "/data/new", "label": "new"},
        "origin": {"page": "reactions", "trigger": "open-data-modal"},
        "validation": validation,
    }
    monkeypatch.setattr(
        svc,
        "normalise_recent_datasets",
        lambda records: records,
    )
    client = create_app().server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-context-commit.data",
            changed="dataset-switch-transaction.data",
            inputs={"dataset-switch-transaction": transaction},
            states={
                "app-store": {
                    "dataset_id": "dataset-old",
                    "selected_smiles": "[OLD]",
                },
                "recent-datasets": [],
            },
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["app-store"]["data"]["dataset_id"] == "dataset-new"
    assert result["app-store"]["data"]["selected_smiles"] == ""
    assert result["app-store"]["data"]["inputs_pending"] is True
    assert result["recent-datasets"]["data"][0]["base"] == "/data/new"
    assert result["dataset-switch-navigation"]["data"]["page"] == "data-management"
    assert result["dataset-context-commit"]["data"]["request_id"] == "request-1"
    assert "当前RNG 数据已切换为" in json.dumps(
        result["data-load-feedback"]["children"],
        ensure_ascii=False,
    )
    assert "global-dataset-notice" not in result

    assert result["species-grid-store"]["data"] == {"rows": []}
    assert result["rxn-grid-store"]["data"] == {"rows": []}
    assert result["event-selected-store"]["data"] is None
    assert "pathway-store" not in result
    assert "event-path-store" not in result
    assert "candidate-path-store" not in result
    assert "fate-result-store" not in result


def test_direct_workspace_switch_opens_dataset_overview(monkeypatch) -> None:
    validation = {
        "folder": "/data",
        "base": "/data/new",
        "label": "new",
        "dataset_id": "dataset-new",
        "source_revision": {"fingerprint": "revision-new", "artifacts": []},
        "artifacts": {},
        "capabilities": {},
        "readiness": {},
        "ready_count": 0,
    }
    monkeypatch.setattr(svc, "normalise_recent_datasets", lambda records: records)
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-context-commit.data",
            changed="dataset-switch-transaction.data",
            inputs={
                "dataset-switch-transaction": {
                    "state": "succeeded",
                    "request_id": "request-direct",
                    "candidate": validation,
                    "origin": {},
                    "validation": validation,
                }
            },
            states={"app-store": {}, "recent-datasets": []},
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["dataset-switch-navigation"]["data"][
        "page"
    ] == "data-management"


def test_same_identity_and_revision_commit_is_a_visible_noop(monkeypatch) -> None:
    validation = {
        "folder": "/data",
        "base": "/data/current",
        "label": "current",
        "dataset_id": "dataset-current",
        "source_revision": {"fingerprint": "revision-current", "artifacts": []},
        "artifacts": {},
        "capabilities": {},
        "readiness": {},
        "ready_count": 0,
    }
    transaction = {
        "state": "succeeded",
        "request_id": "request-noop",
        "candidate": {
            "folder": "/data",
            "base": "/data/current",
            "label": "current",
        },
        "origin": {},
        "validation": validation,
    }
    current = svc.current_dataset_from_validation(validation)
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-context-commit.data",
            changed="dataset-switch-transaction.data",
            inputs={"dataset-switch-transaction": transaction},
            states={"app-store": current, "recent-datasets": []},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert "app-store" not in result
    assert result["dataset-browser-candidate"]["data"] == transaction["candidate"]
    assert result["dataset-context-commit"]["data"] == {}
    assert "当前使用的RNG 数据" in json.dumps(
        result["data-load-feedback"]["children"], ensure_ascii=False
    )
    assert "global-dataset-notice" not in result


def test_session_restore_failure_clears_only_current_context(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "revalidate_current_dataset",
        lambda _current: {
            "state": "unavailable",
            "context": None,
            "reason": "candidate_missing",
            "message": "路径已不存在",
        },
    )
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="dataset-restore-result.data",
            changed="dataset-session-restore.n_intervals",
            inputs={"dataset-session-restore": 1},
            states={
                "dataset-session-store": {
                    "dataset_id": "dataset-old",
                    "folder": "/gone",
                    "base": "/gone/run",
                    "selected_smiles": "[OLD]",
                }
            },
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["app-store"]["data"]["dataset_id"] == ""
    assert result["dataset-restore-result"]["data"]["state"] == "unavailable"
    assert result["dataset-context-commit"]["data"]["reason"] == "restore-unavailable"
    assert "最近记录" in json.dumps(
        result["global-dataset-notice"]["children"], ensure_ascii=False
    )
    assert "recent-datasets" not in result


def test_missing_restored_dataset_routes_analysis_page_to_data_workspace() -> None:
    client = create_app().server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "dataset-switch-navigation.data" in str(item.get("output") or "")
        and any(
            input_item.get("id") == "dataset-restore-result"
            for input_item in item.get("inputs") or []
        )
    )

    response = client.post(
        "/_dash-update-component",
        json={
            "output": dependency["output"],
            "outputs": {
                "id": "dataset-switch-navigation",
                "property": "data",
            },
            "changedPropIds": ["dataset-restore-result.data"],
            "inputs": [
                {
                    "id": "dataset-restore-result",
                    "property": "data",
                    "value": {"state": "none"},
                }
            ],
            "state": [
                {
                    "id": "page-store",
                    "property": "data",
                    "value": {"page": "species"},
                }
            ],
        },
    )

    assert response.status_code == 200
    navigation = response.get_json()["response"]["dataset-switch-navigation"][
        "data"
    ]
    assert navigation["page"] == "data-management"


def test_revision_changed_without_candidate_has_one_primary_update_action() -> None:
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="data-current-refresh-btn.children",
            changed="app-store.data",
            inputs={
                "app-store": {"context_state": "revision-changed"},
                "dataset-browser-candidate": None,
                "dataset-switch-transaction": {},
            },
            states={},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["data-current-refresh-btn"]["children"] == "更新当前RNG 数据状态"
    assert result["data-current-refresh-btn"]["color"] == "primary"
    assert result["data-current-refresh-btn"]["outline"] is False
    assert result["data-apply-btn"]["disabled"] is True


def test_different_candidate_makes_switch_primary_during_revision_change() -> None:
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="data-current-refresh-btn.children",
            changed="dataset-browser-candidate.data",
            inputs={
                "app-store": {
                    "context_state": "revision-changed",
                    "dataset_id": "current",
                    "base": "/data/current",
                },
                "dataset-browser-candidate": {
                    "folder": "/data",
                    "base": "/data/other",
                    "label": "other",
                },
                "dataset-switch-transaction": {"state": "candidate-selected"},
            },
            states={},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["data-apply-btn"]["disabled"] is False
    assert result["data-apply-btn"]["children"] == "开始分析"
    assert result["data-current-refresh-btn"]["color"] == "secondary"
    assert result["data-current-refresh-btn"]["outline"] is True


def test_unchanged_candidate_is_labelled_noop_before_submit(monkeypatch) -> None:
    revision = {"fingerprint": "revision-current", "artifacts": []}
    monkeypatch.setattr(
        svc,
        "inspect_dataset_candidate",
        lambda *_args: {
            "dataset_id": "dataset-current",
            "source_revision": revision,
        },
    )
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_payload(
            client,
            output_contains="data-current-refresh-btn.children",
            changed="dataset-browser-candidate.data",
            inputs={
                "app-store": {
                    "context_state": "active",
                    "dataset_id": "dataset-current",
                    "source_revision": revision,
                    "base": "/data/current",
                },
                "dataset-browser-candidate": {
                    "folder": "/data",
                    "base": "/data/current",
                    "label": "current",
                },
                "dataset-switch-transaction": {"state": "candidate-selected"},
            },
            states={},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["data-apply-btn"]["children"] == "当前RNG 数据"
    assert result["data-apply-btn"]["disabled"] is True
