from __future__ import annotations

import json
from typing import Any

from reacnet_scope import services as svc
from scripts.webapp_dash.app import create_app


def _payload(
    client: Any,
    *,
    output_contains: str,
    changed: str,
    inputs: dict[str, Any],
    states: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if output_contains in str(item.get("output") or "")
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


def test_explicit_apply_starts_one_tab_local_validation_request(monkeypatch) -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    expected = {
        "state": "validating",
        "request_id": "request-1",
        "candidate": candidate,
        "origin": {"page": "reactions", "trigger": "open-data-modal"},
        "started_ns": 1,
        "deadline_ns": 2,
    }
    monkeypatch.setattr(svc, "begin_dataset_switch", lambda *_args, **_kwargs: expected)
    client = create_app().server.test_client()

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
                "page-store": {
                    "page": "data-management",
                    "dataset_return": {
                        "page": "reactions",
                        "trigger": "open-data-modal",
                    },
                },
                "dataset-switch-validation": {},
            },
            states={"dataset-switch-transaction": {}},
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["dataset-switch-transaction"]["data"] == expected


def test_validation_failure_retains_candidate_and_old_current(monkeypatch) -> None:
    candidate = {"folder": "/data", "base": "/data/new", "label": "new"}
    transaction = {
        "state": "validating",
        "request_id": "request-1",
        "candidate": candidate,
        "origin": {},
    }
    failed = {
        **transaction,
        "state": "failed",
        "reason": "candidate_missing",
        "message": "候选已不存在；旧 Current Dataset 已保留，请重试。",
    }
    monkeypatch.setattr(svc, "resolve_dataset_switch", lambda *_args, **_kwargs: failed)
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
                    "request_id": "request-1",
                    "ok": False,
                },
            },
            states={"dataset-switch-transaction": transaction},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]["dataset-switch-transaction"]["data"]
    assert result["state"] == "failed"
    assert result["candidate"] == candidate
    assert "已保留" in result["message"]
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
                "dataset-switch-validation": {},
            },
            states={"dataset-switch-transaction": transaction},
        ),
    )

    assert response.status_code == 204


def test_cancel_supersedes_request_and_late_validation_is_ignored() -> None:
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
                "dataset-switch-validation": {},
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
                "page-store": {"page": "species"},
                "dataset-switch-validation": {
                    "request_id": "request-1",
                    "ok": True,
                    "validation": {"dataset_id": "dataset-new"},
                },
            },
            states={"dataset-switch-transaction": cancelled},
        ),
    )
    assert late_response.status_code == 204


def test_successful_switch_commits_context_resets_results_and_returns_to_source(
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
    assert result["dataset-switch-navigation"]["data"]["page"] == "reactions"
    assert result["dataset-context-commit"]["data"]["request_id"] == "request-1"
    assert "当前数据集已切换为" in json.dumps(
        result["global-dataset-notice"]["children"],
        ensure_ascii=False,
    )
    assert result["species-grid-store"]["data"] == {"rows": []}
    assert result["rxn-grid-store"]["data"] == {"rows": []}
    assert result["event-selected-store"]["data"] is None
    assert result["pathway-store"]["data"] is None
    assert result["event-path-store"]["data"] is None
    assert result["event-frame-slider"]["value"] == 0
    assert result["evolution-species-file"]["value"] == ""


def test_direct_workspace_switch_stays_in_workspace(monkeypatch) -> None:
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
    assert "相同源修订" in json.dumps(
        result["global-dataset-notice"]["children"], ensure_ascii=False
    )


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
    assert result["species-grid-store"]["data"] == {"rows": []}
    assert result["event-selected-store"]["data"] is None
    assert result["event-frame-slider"]["value"] == 0
    assert "最近记录" in json.dumps(
        result["global-dataset-notice"]["children"], ensure_ascii=False
    )
    assert "recent-datasets" not in result


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
    assert result["data-current-refresh-btn"]["children"] == "更新当前数据集状态"
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
    assert result["data-apply-btn"]["children"] == "使用此数据集"
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
    assert result["data-apply-btn"]["children"] == "当前正在使用"
    assert result["data-apply-btn"]["disabled"] is True
