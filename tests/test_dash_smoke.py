from __future__ import annotations

import json
from typing import Any

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from rng_tools import dir_browser
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash import services as svc


def _layout_string_ids(node: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(node, dict):
        props = node.get("props") or {}
        component_id = props.get("id")
        if isinstance(component_id, str):
            ids.add(component_id)
        for value in node.values():
            ids.update(_layout_string_ids(value))
    elif isinstance(node, list):
        for value in node:
            ids.update(_layout_string_ids(value))
    return ids


def test_dash_layout_and_callback_dependencies_are_loadable() -> None:
    app = create_app()
    client = app.server.test_client()

    assert client.get("/").status_code == 200
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["service"] == "reacnet-scope-web-dash"
    layout_response = client.get("/_dash-layout")
    dependency_response = client.get("/_dash-dependencies")
    assert layout_response.status_code == 200
    assert dependency_response.status_code == 200

    layout_ids = _layout_string_ids(layout_response.get_json())
    assert "dir-browser-back-btn" in layout_ids
    assert "dir-browser-path-input" in layout_ids
    assert "dir-browser-breadcrumbs" in layout_ids
    assert "dir-browser-datasets" in layout_ids
    assert "dir-browser-recent" in layout_ids
    assert "dataset-browser-candidate" in layout_ids
    assert "recent-datasets" in layout_ids
    assert "data-rungroup" in layout_ids
    assert "data-scan-btn" not in layout_ids
    assert "data-prep-status" in layout_ids
    assert "data-prep-refresh-btn" in layout_ids
    assert "data-rng-event-command" in layout_ids
    assert "data-clear-trajectory-btn" in layout_ids
    assert "carbon-reference-smiles" in layout_ids
    assert "carbon-timestep" in layout_ids
    assert "carbon-parent-name" not in layout_ids

    missing: list[str] = []
    for dependency in dependency_response.get_json():
        for item in dependency.get("inputs", []) + dependency.get("state", []):
            component_id = str(item.get("id") or "")
            if component_id.startswith("{"):
                continue
            if component_id not in layout_ids:
                missing.append(component_id)
    assert missing == []
    layout_text = json.dumps(layout_response.get_json(), ensure_ascii=False)
    assert "运行组 (base)" not in layout_text
    assert "加载数据集" in layout_text


def test_carbon_callback_passes_explicit_reference_and_timestep(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(_artifacts, **kwargs):
        captured.update(kwargs)
        return {
            "carbon_skeleton_rows": [],
            "summary": {},
            "meta": {},
            "filters": {},
            "x_name": "Time (ps)",
        }

    monkeypatch.setattr(svc, "build_elemental_composition_evolution", fake_build)
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]] == ["carbon-search-btn"]
    )
    state_values = {
        "carbon-max-c": 8,
        "carbon-chlorine-state": "all",
        "carbon-oxygen-state": "all",
        "carbon-reference-smiles": "[C][C]",
        "carbon-timestep": 0.002,
        "app-store": {"artifacts": {"species": "/tmp/example.species"}},
    }
    payload = {
        "output": dependency["output"],
        "outputs": [
            {"id": "carbon-alert", "property": "children"},
            {"id": "carbon-highlights", "property": "children"},
            {"id": "carbon-payload-store", "property": "data"},
            {"id": "carbon-composition-trend", "property": "figure"},
        ],
        "changedPropIds": ["carbon-search-btn.n_clicks"],
        "inputs": [{"id": "carbon-search-btn", "property": "n_clicks", "value": 1}],
        "state": [
            {"id": item["id"], "property": item["property"], "value": state_values[item["id"]]}
            for item in dependency["state"]
        ],
    }

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    assert captured["reference_smiles"] == "[C][C]"
    assert captured["timestep_ps"] == 0.002


def test_species_search_preserves_zero_mass_tolerance(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_search(_artifacts, _query, **kwargs):
        captured.update(kwargs)
        return {"rows": [], "meta": {"catalog_size": 0, "moname_available": False}}

    monkeypatch.setattr(svc, "search_species_catalog", fake_search)
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]]
        == ["workflow-species-search", "app-store"]
    )
    input_values = {
        "workflow-species-search": 1,
        "app-store": {"artifacts": {"species": "/tmp/example.species"}},
    }
    state_values = {
        "workflow-species-query": "31",
        "workflow-species-kind": "mass",
        "workflow-mass-tolerance": 0,
        "workflow-mass-mode": "exact",
    }
    payload = {
        "output": dependency["output"],
        "outputs": [
            {"id": "workflow-species-grid", "property": "data"},
            {"id": "workflow-species-grid", "property": "columns"},
            {"id": "workflow-species-alert", "property": "children"},
        ],
        "changedPropIds": ["workflow-species-search.n_clicks"],
        "inputs": [
            {"id": item["id"], "property": item["property"], "value": input_values[item["id"]]}
            for item in dependency["inputs"]
        ],
        "state": [
            {"id": item["id"], "property": item["property"], "value": state_values[item["id"]]}
            for item in dependency["state"]
        ],
    }

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    assert captured["mass_tolerance"] == 0


def _browser_callback_payload(
    client,
    *,
    changed: str,
    values: dict[str, Any],
    state_values: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if any(value["id"] == "data-pick-btn" for value in item["inputs"])
    )
    outputs = [
        {"id": "dir-browser-modal", "property": "is_open"},
        {"id": "dir-browser-path-input", "property": "value"},
        {"id": "dir-browser-breadcrumbs", "property": "children"},
        {"id": "dir-browser-recent", "property": "children"},
        {"id": "dir-browser-datasets", "property": "children"},
        {"id": "dir-browser-body", "property": "children"},
        {"id": "dir-browser-path", "property": "data"},
        {"id": "dataset-browser-candidate", "property": "data"},
        {"id": "dir-browser-select-btn", "property": "disabled"},
        {"id": "data-folder-input", "property": "value"},
        {"id": "data-rungroup", "property": "value"},
    ]

    def value_for(item: dict[str, Any]) -> Any:
        component_id = item["id"]
        key = component_id if isinstance(component_id, str) else json.dumps(component_id, sort_keys=True)
        return values.get(key, [] if not isinstance(component_id, str) else None)

    return {
        "output": dependency["output"],
        "outputs": outputs,
        "changedPropIds": [changed],
        "inputs": [
            {"id": item["id"], "property": item["property"], "value": value_for(item)}
            for item in dependency["inputs"]
        ],
        "state": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": state_values.get(item["id"], []),
            }
            for item in dependency["state"]
        ],
    }


def _load_dataset_callback_payload(
    client,
    *,
    candidate: dict[str, str] | None,
    store: dict[str, Any],
    recent_records: Any,
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if any(value["id"] == "data-apply-btn" for value in item["inputs"])
        and "recent-datasets.data" in item["output"]
    )
    outputs = [
        {"id": "app-store", "property": "data"},
        {"id": "topbar-folder", "property": "children"},
        {"id": "topbar-rungroup", "property": "children"},
        {"id": "topbar-status", "property": "children"},
        {"id": "topbar-status", "property": "className"},
        {"id": "data-modal", "property": "is_open"},
        {"id": "recent-datasets", "property": "data"},
        {"id": "dataset-browser-candidate", "property": "data"},
        {"id": "data-load-feedback", "property": "children"},
    ]
    state_values = {
        "dataset-browser-candidate": candidate,
        "app-store": store,
        "recent-datasets": recent_records,
    }
    return {
        "output": dependency["output"],
        "outputs": outputs,
        "changedPropIds": ["data-apply-btn.n_clicks"],
        "inputs": [
            {"id": item["id"], "property": item["property"], "value": 1}
            for item in dependency["inputs"]
        ],
        "state": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": state_values[item["id"]],
            }
            for item in dependency["state"]
        ],
    }


def _candidate_status_callback_payload(
    client,
    *,
    candidate: dict[str, str] | None,
    store: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "data-candidate-summary.children" in item["output"]
    )
    return {
        "output": dependency["output"],
        "outputs": [
            {"id": "data-candidate-summary", "property": "children"},
            {"id": "data-scan-status", "property": "children"},
            {"id": "data-artifacts", "property": "children"},
            {"id": "data-apply-btn", "property": "disabled"},
        ],
        "changedPropIds": ["dataset-browser-candidate.data"],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": candidate,
            }
            for item in dependency["inputs"]
        ],
        "state": [
            {"id": item["id"], "property": item["property"], "value": store}
            for item in dependency["state"]
        ],
    }


def test_load_selected_dataset_updates_store_closes_modal_and_remembers_it(
    tmp_path, monkeypatch
) -> None:
    candidate = {
        "folder": str(tmp_path),
        "base": str(tmp_path / "rp3.lammpstrj"),
        "label": "rp3.lammpstrj",
    }
    captured: dict[str, str] = {}

    def fake_scan(folder: str, *, base: str = "") -> dict[str, Any]:
        captured.update({"folder": folder, "base": base})
        return {
            "dataset": {
                "selected_base": base,
                "label": "rp3.lammpstrj",
                "ready_count": 2,
                "artifacts": {},
                "capabilities": {},
                "readiness": {},
            }
        }

    monkeypatch.setattr(svc, "scan_dataset", fake_scan)
    app = create_app()
    client = app.server.test_client()
    old_store = {"folder": "old", "base": "old/base", "label": "old"}
    response = client.post(
        "/_dash-update-component",
        json=_load_dataset_callback_payload(
            client,
            candidate=candidate,
            store=old_store,
            recent_records=[],
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert captured == {"folder": str(tmp_path), "base": candidate["base"]}
    assert result["app-store"]["data"]["base"] == candidate["base"]
    assert result["data-modal"]["is_open"] is False
    assert result["recent-datasets"]["data"][0]["folder"] == str(tmp_path)
    assert result["recent-datasets"]["data"][0]["base"] == candidate["base"]


def test_load_failure_keeps_current_dataset_modal_and_recents(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "scan_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(svc.ServiceError("已不存在")),
    )
    app = create_app()
    client = app.server.test_client()
    old_store = {"folder": "old", "base": "old/base", "label": "old"}
    old_recent = [{"folder": "/old", "base": "/old/run", "label": "run", "loaded_at": 1}]
    response = client.post(
        "/_dash-update-component",
        json=_load_dataset_callback_payload(
            client,
            candidate={"folder": "/gone", "base": "/gone/run", "label": "run"},
            store=old_store,
            recent_records=old_recent,
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["app-store"]["data"] == old_store
    assert result["data-modal"]["is_open"] is True
    assert result["recent-datasets"]["data"] == old_recent
    assert result["dataset-browser-candidate"]["data"] is None
    assert "不可用" in json.dumps(result["data-load-feedback"]["children"], ensure_ascii=False)


def test_final_rescan_failure_clears_candidate_and_disables_loading(
    tmp_path, monkeypatch
) -> None:
    """A candidate can vanish after its initial validation but before loading."""
    candidate = {
        "folder": str(tmp_path),
        "base": str(tmp_path / "rp3.lammpstrj"),
        "label": "rp3.lammpstrj",
    }
    old_store = {"folder": "old", "base": "old/base", "label": "old"}
    old_recent = [{"folder": "/old", "base": "/old/run", "label": "run", "loaded_at": 1}]
    scans = 0

    def fake_scan(_folder: str, *, base: str = "") -> dict[str, Any]:
        nonlocal scans
        scans += 1
        if scans > 1:
            raise svc.ServiceError("源文件已被移除")
        return {
            "dataset": {
                "selected_base": base,
                "label": candidate["label"],
                "ready_count": 2,
                "artifacts": {},
                "capabilities": {},
                "readiness": {},
            }
        }

    monkeypatch.setattr(svc, "scan_dataset", fake_scan)
    app = create_app()
    client = app.server.test_client()
    validated = client.post(
        "/_dash-update-component",
        json=_candidate_status_callback_payload(client, candidate=candidate, store=old_store),
    )
    assert validated.status_code == 200
    assert validated.get_json()["response"]["data-apply-btn"]["disabled"] is False

    failed_load = client.post(
        "/_dash-update-component",
        json=_load_dataset_callback_payload(
            client, candidate=candidate, store=old_store, recent_records=old_recent
        ),
    )
    assert failed_load.status_code == 200
    result = failed_load.get_json()["response"]
    assert result["app-store"]["data"] == old_store
    assert result["recent-datasets"]["data"] == old_recent
    assert result["dataset-browser-candidate"]["data"] is None
    assert "未切换当前数据" in json.dumps(result["data-load-feedback"]["children"], ensure_ascii=False)

    cleared = client.post(
        "/_dash-update-component",
        json=_candidate_status_callback_payload(client, candidate=None, store=old_store),
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["response"]["data-apply-btn"]["disabled"] is True


def test_selected_card_survives_browser_confirmation_and_loads_exact_base(
    tmp_path, monkeypatch
) -> None:
    """Selecting a card must not feed its folder back through manual parsing."""
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    for name in ("rp3.lammpstrj", "rp4.lammpstrj"):
        (tmp_path / f"{name}.reactionabcd").touch()
        (tmp_path / f"{name}.species").touch()
    app = create_app()
    client = app.server.test_client()
    selected = {
        "folder": str(tmp_path),
        "base": str(tmp_path / "rp4.lammpstrj"),
        "label": "rp4.lammpstrj",
    }
    browser_response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-select-btn.n_clicks",
            values={"dir-browser-select-btn": 1},
            state_values={
                "dir-browser-path": str(tmp_path),
                "data-folder-input": str(tmp_path),
                "recent-datasets": [],
                "dataset-browser-candidate": selected,
            },
        ),
    )
    assert browser_response.status_code == 200
    browser_result = browser_response.get_json()["response"]
    assert browser_result["dataset-browser-candidate"]["data"] == selected
    assert "data-folder-input" not in browser_result

    def fake_scan(folder: str, *, base: str = "") -> dict[str, Any]:
        assert folder == str(tmp_path)
        assert base == selected["base"]
        return {
            "dataset": {
                "selected_base": base,
                "label": selected["label"],
                "ready_count": 2,
                "artifacts": {},
                "capabilities": {},
                "readiness": {},
            }
        }

    monkeypatch.setattr(svc, "scan_dataset", fake_scan)
    load_response = client.post(
        "/_dash-update-component",
        json=_load_dataset_callback_payload(
            client,
            candidate=selected,
            store={"folder": "old", "base": "old/base", "label": "old"},
            recent_records=[],
        ),
    )
    assert load_response.status_code == 200
    assert load_response.get_json()["response"]["app-store"]["data"]["base"] == selected["base"]


def test_manual_dataset_prefix_selects_exact_candidate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    for name in ("rp3.lammpstrj", "rp4.lammpstrj"):
        (tmp_path / f"{name}.reactionabcd").touch()
        (tmp_path / f"{name}.species").touch()
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-folder-input.value",
            values={"data-folder-input": str(tmp_path / "rp4.lammpstrj")},
            state_values={
                "dir-browser-path": str(tmp_path),
                "data-folder-input": str(tmp_path / "rp4.lammpstrj"),
                "recent-datasets": [],
                "dataset-browser-candidate": None,
            },
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dataset-browser-candidate"]["data"] == {
        "folder": str(tmp_path),
        "base": str(tmp_path / "rp4.lammpstrj"),
        "label": "rp4.lammpstrj",
    }

    browser_response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-pick-btn.n_clicks",
            values={"data-pick-btn": 1},
            state_values={
                "dir-browser-path": "",
                "data-folder-input": str(tmp_path / "rp4.lammpstrj"),
                "recent-datasets": [],
                "dataset-browser-candidate": result["dataset-browser-candidate"]["data"],
            },
        ),
    )
    assert browser_response.status_code == 200
    assert browser_response.get_json()["response"]["dir-browser-path"]["data"] == str(tmp_path)

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-folder-input.value",
            values={"data-folder-input": str(tmp_path)},
            state_values={
                "dir-browser-path": str(tmp_path),
                "data-folder-input": str(tmp_path),
                "recent-datasets": [],
                "dataset-browser-candidate": None,
            },
        ),
    )
    assert response.status_code == 200
    assert response.get_json()["response"]["dataset-browser-candidate"]["data"] is None

    one_dataset = tmp_path / "only-one"
    one_dataset.mkdir()
    (one_dataset / "run.lammpstrj.reactionabcd").touch()
    (one_dataset / "run.lammpstrj.species").touch()
    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-folder-input.value",
            values={"data-folder-input": str(one_dataset)},
            state_values={
                "dir-browser-path": str(tmp_path),
                "data-folder-input": str(one_dataset),
                "recent-datasets": [],
                "dataset-browser-candidate": None,
            },
        ),
    )
    assert response.status_code == 200
    assert response.get_json()["response"]["dataset-browser-candidate"]["data"] == {
        "folder": str(one_dataset),
        "base": str(one_dataset / "run.lammpstrj"),
        "label": "run.lammpstrj",
    }


def test_directory_browser_ignores_corrupt_recent_store_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-pick-btn.n_clicks",
            values={"data-pick-btn": 1},
            state_values={
                "dir-browser-path": "",
                "data-folder-input": str(tmp_path),
                "recent-datasets": {"unexpected": "mapping"},
                "dataset-browser-candidate": None,
            },
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["dir-browser-modal"]["is_open"] is True


def test_directory_browser_go_subdir_parent_recent_and_revalidates_selection(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    dataset = tmp_path / "case"
    dataset.mkdir()
    reaction = dataset / "rp3.lammpstrj.reactionabcd"
    species = dataset / "rp3.lammpstrj.species"
    reaction.touch()
    species.touch()
    candidate = {
        "folder": str(dataset),
        "base": str(dataset / "rp3.lammpstrj"),
        "label": "rp3.lammpstrj",
    }
    app = create_app()
    client = app.server.test_client()
    state = {
        "dir-browser-path": str(tmp_path),
        "data-folder-input": str(tmp_path),
        "recent-datasets": [{**candidate, "loaded_at": 1}],
        "dataset-browser-candidate": None,
    }

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-go-btn.n_clicks",
            values={
                "dir-browser-go-btn": 1,
                "dir-browser-path-input": str(dataset),
            },
            state_values=state,
        ),
    )
    assert response.status_code == 200
    assert response.get_json()["response"]["dir-browser-path"]["data"] == str(dataset)

    entry = {"type": "dir-browser-entry", "path": str(dataset)}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(entry, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"path":["ALL"],"type":"dir-browser-entry"}': [1]},
        state_values=state,
    )
    for item in payload["inputs"]:
        if item["id"] == '{"path":["ALL"],"type":"dir-browser-entry"}':
            item["id"] = entry
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    assert response.get_json()["response"]["dir-browser-path"]["data"] == str(dataset)

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-back-btn.n_clicks",
            values={"dir-browser-back-btn": 1},
            state_values={**state, "dir-browser-path": str(dataset)},
        ),
    )
    assert response.status_code == 200
    assert response.get_json()["response"]["dir-browser-path"]["data"] == str(tmp_path)

    recent = {"type": "dir-browser-recent-entry", "folder": str(dataset), "base": candidate["base"]}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(recent, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"base":["ALL"],"folder":["ALL"],"type":"dir-browser-recent-entry"}': [1]},
        state_values=state,
    )
    for item in payload["inputs"]:
        if item["id"] == '{"base":["ALL"],"folder":["ALL"],"type":"dir-browser-recent-entry"}':
            item["id"] = recent
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dataset-browser-candidate"]["data"] == candidate

    reaction.unlink()
    species.unlink()
    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-select-btn.n_clicks",
            values={"dir-browser-select-btn": 1},
            state_values={
                **state,
                "dir-browser-path": str(dataset),
                "dataset-browser-candidate": candidate,
            },
        ),
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-modal"]["is_open"] is True
    assert result["dataset-browser-candidate"]["data"] is None


def test_directory_browser_open_selects_one_dataset_without_applying_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "rp3.lammpstrj.reactionabcd").touch()
    (dataset / "rp3.lammpstrj.species").touch()
    app = create_app()
    client = app.server.test_client()
    payload = _browser_callback_payload(
        client,
        changed="data-pick-btn.n_clicks",
        values={
        "data-pick-btn": 1,
        },
        state_values={
            "dir-browser-path": "",
            "data-folder-input": str(dataset),
            "recent-datasets": [],
            "dataset-browser-candidate": None,
        },
    )

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-modal"]["is_open"] is True
    assert result["dir-browser-path"]["data"] == str(dataset.resolve())
    assert result["dataset-browser-candidate"]["data"] == {
        "folder": str(dataset),
        "base": str(dataset / "rp3.lammpstrj"),
        "label": "rp3.lammpstrj",
    }
    assert result["dir-browser-select-btn"]["disabled"] is False


def test_directory_browser_navigation_and_cancel_preserve_applied_dataset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    nested = dataset / "nested"
    nested.mkdir()
    (dataset / "rp3.lammpstrj.reactionabcd").touch()
    (dataset / "rp3.lammpstrj.species").touch()
    app = create_app()
    client = app.server.test_client()
    common_state = {
        "dir-browser-path": str(dataset),
        "data-folder-input": str(dataset),
        "recent-datasets": [],
        "dataset-browser-candidate": None,
    }

    breadcrumb = {"type": "dir-browser-crumb", "path": str(tmp_path)}
    breadcrumb_payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(breadcrumb, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"path":["ALL"],"type":"dir-browser-crumb"}': [1]},
        state_values=common_state,
    )
    for item in breadcrumb_payload["inputs"]:
        if item["id"] == '{"path":["ALL"],"type":"dir-browser-crumb"}':
            item["id"] = breadcrumb
    response = client.post(
        "/_dash-update-component",
        json=breadcrumb_payload,
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-path"]["data"] == str(tmp_path)

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-path-input.value",
            values={"dir-browser-path-input": str(dataset)},
            state_values={**common_state, "dir-browser-path": str(tmp_path)},
        ),
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-path"]["data"] == str(dataset)

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-cancel-btn.n_clicks",
            values={"dir-browser-cancel-btn": 1},
            state_values={**common_state, "dataset-browser-candidate": {"base": "old"}},
        ),
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-modal"]["is_open"] is False
    assert "data-folder-input" not in result


def test_directory_browser_requires_explicit_choice_for_multiple_datasets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    for name in ("rp3.lammpstrj", "rp4.lammpstrj"):
        (tmp_path / f"{name}.reactionabcd").touch()
        (tmp_path / f"{name}.species").touch()
    app = create_app()
    client = app.server.test_client()
    state = {
        "dir-browser-path": "",
        "data-folder-input": str(tmp_path),
        "recent-datasets": [],
        "dataset-browser-candidate": None,
    }
    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-pick-btn.n_clicks",
            values={"data-pick-btn": 1},
            state_values=state,
        ),
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dataset-browser-candidate"]["data"] is None
    assert result["dir-browser-select-btn"]["disabled"] is True

    selected = {"type": "dir-browser-dataset", "base": str(tmp_path / "rp4.lammpstrj")}
    card_payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(selected, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"base":["ALL"],"type":"dir-browser-dataset"}': [1]},
        state_values={**state, "dir-browser-path": str(tmp_path)},
    )
    for item in card_payload["inputs"]:
        if item["id"] == '{"base":["ALL"],"type":"dir-browser-dataset"}':
            item["id"] = selected
    response = client.post("/_dash-update-component", json=card_payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dataset-browser-candidate"]["data"]["base"] == str(tmp_path / "rp4.lammpstrj")
    assert result["dir-browser-select-btn"]["disabled"] is False

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-path-input.value",
            values={"dir-browser-path-input": ""},
            state_values={**state, "dir-browser-path": str(tmp_path)},
        ),
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-path"]["data"] == str(tmp_path)


def test_rng_event_query_callback_renders_rng_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n0,[O],1,\n10,[C][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]] == ["event-rxn-btn"]
    )
    input_values = {"event-rxn-btn": 1}
    state_values = {
        "event-reaction-text": "[O] + [C] -> [C][O]",
        "event-rxn-before": 3,
        "event-rxn-after": 3,
        "event-rxn-max": 100,
        "app-store": {
            "artifacts": {
                "reactionevent": str(reactionevent),
                "molecules": str(molecules),
            }
        },
    }
    payload = {
        "output": dependency["output"],
        "outputs": [
            {"id": "event-grid", "property": "data"},
            {"id": "event-grid", "property": "columns"},
            {"id": "event-alert", "property": "children"},
            {"id": "event-grid-store", "property": "data"},
        ],
        "changedPropIds": ["event-rxn-btn.n_clicks"],
        "inputs": [
            {"id": item["id"], "property": item["property"], "value": input_values[item["id"]]}
            for item in dependency["inputs"]
        ],
        "state": [
            {"id": item["id"], "property": item["property"], "value": state_values[item["id"]]}
            for item in dependency["state"]
        ],
    }

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["event-grid"]["data"][0]["atom_ids"] == "1,2"
    assert result["event-grid-store"]["data"]["kind"] == "rng_event"


def test_legacy_core_queries_are_available_through_dash_services(tmp_path) -> None:
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    reaction.write_text(
        "10 [C]+[O]->[C][O]\n4 [C][O]->[C]+[O]\n",
        encoding="utf-8",
    )
    artifacts = {"reaction": str(reaction), "species": "", "route": "", "trajectory": "", "table": ""}

    assert svc.search_species(artifacts, "CO", kind="formula")["n_rows"] == 1
    assert len(svc.collect_transitions(artifacts, "[C][O]")["rows"]) == 2
    assert len(svc.search_reactions_by_formula(artifacts, "C+O", "CO")["rows"]) == 1
    assert svc.verify_literature_mechanism(artifacts, ["C + O -> CO"])["ok"] is True
