from __future__ import annotations

from typing import Any

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


def test_directory_browser_open_callback_runs_from_initial_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    (tmp_path / "dataset").mkdir()
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if any(value["id"] == "data-pick-btn" for value in item["inputs"])
    )
    input_values = {
        "data-pick-btn": 1,
        '{"path":["ALL"],"type":"dir-browser-entry"}': [],
        "dir-browser-back-btn": None,
        "dir-browser-select-btn": None,
        "dir-browser-cancel-btn": None,
    }
    payload = {
        "output": dependency["output"],
        "outputs": [
            {"id": "dir-browser-modal", "property": "is_open"},
            {"id": "dir-browser-body", "property": "children"},
            {"id": "dir-browser-path", "property": "data"},
            {"id": "data-folder-input", "property": "value"},
        ],
        "changedPropIds": ["data-pick-btn.n_clicks"],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": input_values[item["id"]],
            }
            for item in dependency["inputs"]
        ],
        "state": [
            {"id": "dir-browser-path", "property": "data", "value": ""},
            {"id": "data-folder-input", "property": "value", "value": str(tmp_path)},
        ],
    }

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-modal"]["is_open"] is True
    assert result["dir-browser-path"]["data"] == str(tmp_path.resolve())


def test_rng_event_query_callback_renders_rng_rows(tmp_path) -> None:
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
