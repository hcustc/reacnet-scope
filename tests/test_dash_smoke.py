from __future__ import annotations

import json
from pathlib import Path
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


def _layout_node_by_id(node: Any, component_id: str) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if (node.get("props") or {}).get("id") == component_id:
            return node
        for value in node.values():
            found = _layout_node_by_id(value, component_id)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _layout_node_by_id(value, component_id)
            if found is not None:
                return found
    return None


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

    layout = layout_response.get_json()
    layout_ids = _layout_string_ids(layout)
    assert "data-modal" in layout_ids
    assert "dir-browser-modal" not in layout_ids
    assert "data-overview-view" in layout_ids
    assert "data-browser-view" in layout_ids
    assert "data-modal-view" not in layout_ids
    assert "dir-browser-breadcrumbs" not in layout_ids
    assert "dir-browser-datasets" not in layout_ids
    assert "dir-browser-recent" not in layout_ids
    assert "dir-browser-back-btn" in layout_ids
    assert "dir-browser-path-input" in layout_ids
    assert "dir-browser-current" in layout_ids
    assert "dir-browser-body" in layout_ids
    assert "dir-browser-select-btn" not in layout_ids
    assert "dataset-browser-candidate" in layout_ids
    assert "recent-datasets" in layout_ids
    assert "data-recent-datasets" in layout_ids
    assert "data-rungroup" in layout_ids
    assert "data-scan-btn" not in layout_ids
    assert "data-prep-status" in layout_ids
    assert "data-prep-refresh-btn" in layout_ids
    assert "data-rng-event-command" in layout_ids
    assert "data-clear-trajectory-btn" in layout_ids
    assert "carbon-reference-smiles" in layout_ids
    assert "carbon-timestep" in layout_ids
    assert "carbon-parent-name" not in layout_ids
    for pathway_id in {
        "pathway-start-smiles",
        "pathway-direction",
        "pathway-max-depth",
        "pathway-max-branches",
        "pathway-max-paths",
        "pathway-min-net-tp",
        "pathway-min-directionality",
        "pathway-search-btn",
        "pathway-grid",
        "pathway-cytoscape",
        "pathway-json-download",
        "pathway-csv-download",
        "pathway-open-events-btn",
        "pathway-highlight-network-btn",
        "pathway-store",
    }:
        assert pathway_id in layout_ids

    missing: list[str] = []
    for dependency in dependency_response.get_json():
        for item in dependency.get("inputs", []) + dependency.get("state", []):
            component_id = str(item.get("id") or "")
            if component_id.startswith("{"):
                continue
            if component_id not in layout_ids:
                missing.append(component_id)
    assert missing == []
    overview = _layout_node_by_id(layout, "data-overview-view")
    browser = _layout_node_by_id(layout, "data-browser-view")
    assert overview is not None
    assert browser is not None
    assert "d-none" not in str((overview.get("props") or {}).get("className") or "")
    assert "d-none" in str((browser.get("props") or {}).get("className") or "")
    assert "data-recent-datasets" in _layout_string_ids(overview)
    layout_text = json.dumps(layout, ensure_ascii=False)
    assert "运行组 (base)" not in layout_text
    assert "加载数据集" in layout_text


def _callback_payload(
    client: Any,
    *,
    input_ids: list[str],
    changed: str,
    input_values: dict[str, Any],
    state_values: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]] == input_ids
    )
    output_spec = dependency["output"]
    outputs: Any
    if output_spec.startswith(".."):
        outputs = [
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
    return {
        "output": output_spec,
        "outputs": outputs,
        "changedPropIds": [changed],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": input_values.get(
                    f"{item['id']}.{item['property']}",
                    input_values.get(item["id"]),
                ),
            }
            for item in dependency["inputs"]
        ],
        "state": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": state_values.get(
                    f"{item['id']}.{item['property']}",
                    state_values.get(item["id"]),
                ),
            }
            for item in dependency["state"]
        ],
    }


def _pathway_payload() -> dict[str, Any]:
    return {
        "paths": [
            {
                "rank": 1,
                "species": ["[H]", "[H][O]"],
                "formulas": ["H", "HO"],
                "score": 0.7,
                "evidence_status": "evidence_linked",
                "steps": [
                    {
                        "reaction_key": "[H]+[O]->[H][O]",
                        "reactants": ["[H]", "[O]"],
                        "products": ["[H][O]"],
                        "focal_input": "[H]",
                        "focal_output": "[H][O]",
                        "score": 0.6,
                        "evidence_status": "evidence_linked",
                    }
                ],
            }
        ],
        "query": {"start_smiles": "[H]"},
        "reason": "ok",
        "truncated": False,
        "expansions": 1,
        "evidence_status": "evidence_linked",
        "score_version": "candidate-path/v1",
        "source_signatures": {},
    }


def test_pathway_search_preserves_exact_zero_threshold(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_find(_artifacts, start_smiles, **limits):
        captured["start_smiles"] = start_smiles
        captured.update(limits)
        return _pathway_payload()

    monkeypatch.setattr(svc, "find_pathways", fake_find)
    app = create_app()
    client = app.server.test_client()
    inputs = ["pathway-search-btn"]
    states = {
        "pathway-start-smiles": "[H]",
        "pathway-direction": "upstream",
        "pathway-max-depth": 4,
        "pathway-max-branches": 6,
        "pathway-max-paths": 7,
        "pathway-min-net-tp": 2,
        "pathway-min-directionality": 0,
        "app-store": {"artifacts": {"reaction": "/tmp/run.reactionabcd"}},
    }

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=inputs,
            changed="pathway-search-btn.n_clicks",
            input_values={"pathway-search-btn": 1},
            state_values=states,
        ),
    )

    assert response.status_code == 200
    assert captured == {
        "start_smiles": "[H]",
        "direction": "upstream",
        "max_depth": 4,
        "max_branches": 6,
        "max_paths": 7,
        "min_net_tp": 2,
        "min_directionality": 0,
    }
    result = response.get_json()["response"]
    row = result["pathway-grid"]["data"][0]
    assert row["formula_chain"] == "H → HO"
    assert row["smiles_chain"] == "[H] → [H][O]"
    assert row["path_score"] == 0.7
    assert row["weakest_step_score"] == 0.6
    assert row["depth"] == 1
    assert row["evidence_badge"] == "事件已关联"
    assert result["pathway-store"]["data"] == _pathway_payload()


def test_pathway_search_reports_distinct_empty_reasons_and_truncation(monkeypatch) -> None:
    app = create_app()
    client = app.server.test_client()
    base = _pathway_payload()
    cases = [
        ("species_absent", "不在当前反应网络"),
        ("no_positive_net_continuation", "没有正净通量"),
        ("filtered_by_thresholds", "阈值过滤"),
    ]
    for reason, expected in cases:
        payload = {**base, "paths": [], "reason": reason, "expansions": 3}
        monkeypatch.setattr(
            svc,
            "find_pathways",
            lambda *_args, _payload=payload, **_kwargs: _payload,
        )
        response = client.post(
            "/_dash-update-component",
            json=_callback_payload(
                client,
                input_ids=["pathway-search-btn"],
                changed="pathway-search-btn.n_clicks",
                input_values={"pathway-search-btn": 1},
                state_values={
                    "pathway-start-smiles": "[H]",
                    "pathway-direction": "downstream",
                    "pathway-max-depth": 3,
                    "pathway-max-branches": 5,
                    "pathway-max-paths": 20,
                    "pathway-min-net-tp": 1,
                    "pathway-min-directionality": 0.05,
                    "app-store": {"artifacts": {"reaction": "run.reactionabcd"}},
                },
            ),
        )
        assert response.status_code == 200
        assert expected in str(
            response.get_json()["response"]["pathway-alert"]["children"]
        )

    truncated = {**base, "truncated": True, "expansions": 5000}
    monkeypatch.setattr(
        svc,
        "find_pathways",
        lambda *_args, **_kwargs: truncated,
    )
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["pathway-search-btn"],
            changed="pathway-search-btn.n_clicks",
            input_values={"pathway-search-btn": 1},
            state_values={
                "pathway-start-smiles": "[H]",
                "pathway-direction": "downstream",
                "pathway-max-depth": 3,
                "pathway-max-branches": 5,
                "pathway-max-paths": 20,
                "pathway-min-net-tp": 1,
                "pathway-min-directionality": 0.05,
                "app-store": {"artifacts": {"reaction": "run.reactionabcd"}},
            },
        ),
    )
    alert = str(response.get_json()["response"]["pathway-alert"]["children"])
    assert "截断" in alert
    assert "expansions=5000" in alert


def test_species_and_reaction_handoffs_preserve_exact_smiles() -> None:
    app = create_app()
    client = app.server.test_client()

    species_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-to-pathway-btn", "rxn-to-pathway-btn"],
            changed="species-to-pathway-btn.n_clicks",
            input_values={
                "species-to-pathway-btn": 1,
                "rxn-to-pathway-btn": None,
            },
            state_values={
                "species-grid.selected_rows": [0],
                "species-grid.data": [{"smiles": "[C@@H](O)[Cl]"}],
                "rxn-grid.selected_rows": [],
                "rxn-grid.data": [],
                "app-store": {"selected_smiles": "wrong"},
            },
        ),
    )
    assert species_response.status_code == 200
    assert (
        species_response.get_json()["response"]["pathway-start-smiles"]["value"]
        == "[C@@H](O)[Cl]"
    )

    reaction_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-to-pathway-btn", "rxn-to-pathway-btn"],
            changed="rxn-to-pathway-btn.n_clicks",
            input_values={
                "species-to-pathway-btn": None,
                "rxn-to-pathway-btn": 1,
            },
            state_values={
                "species-grid.selected_rows": [],
                "species-grid.data": [],
                "rxn-grid.selected_rows": [0],
                "rxn-grid.data": [
                    {
                        "reactant_smiles": ["[13CH3]", "[OH-]"],
                        "reaction_smiles": "[13CH3] + [OH-] -> [13CH3][OH-]",
                    }
                ],
                "app-store": {},
            },
        ),
    )
    assert reaction_response.status_code == 200
    assert (
        reaction_response.get_json()["response"]["pathway-start-smiles"]["value"]
        == "[13CH3]"
    )


def test_selected_pathway_step_handoff_uses_full_reaction_text() -> None:
    app = create_app()
    client = app.server.test_client()
    reaction_text = "[H] + [O] + [O] -> [H][O] + [O]"
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["pathway-open-events-btn"],
            changed="pathway-open-events-btn.n_clicks",
            input_values={"pathway-open-events-btn": 1},
            state_values={
                "pathway-selected-step": {
                    "reaction_text": reaction_text,
                    "reaction_key": "[H]+[O]+[O]->[H][O]+[O]",
                }
            },
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["event-reaction-text"]["value"] == reaction_text


def test_pathway_downloads_use_store_payload_without_search(monkeypatch) -> None:
    payload = _pathway_payload()

    def forbidden_search(*_args, **_kwargs):
        raise AssertionError("downloads must not recompute the search")

    monkeypatch.setattr(svc, "find_pathways", forbidden_search)
    app = create_app()
    client = app.server.test_client()
    json_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["pathway-json-btn"],
            changed="pathway-json-btn.n_clicks",
            input_values={"pathway-json-btn": 1},
            state_values={"pathway-store": payload},
        ),
    )
    assert json_response.status_code == 200
    json_download = json_response.get_json()["response"]["pathway-json-download"]["data"]
    assert json.loads(json_download["content"]) == payload

    csv_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["pathway-csv-btn"],
            changed="pathway-csv-btn.n_clicks",
            input_values={"pathway-csv-btn": 1},
            state_values={"pathway-store": payload},
        ),
    )
    assert csv_response.status_code == 200
    csv_download = csv_response.get_json()["response"]["pathway-csv-download"]["data"]
    assert "[H]+[O]->[H][O]" in csv_download["content"]


def test_selected_path_handoff_keeps_exact_stable_ids() -> None:
    app = create_app()
    client = app.server.test_client()
    payload = _pathway_payload()
    rows = [
        {
            "rank": 1,
            "formula_chain": "H → HO",
            "smiles_chain": "[H] → [H][O]",
        }
    ]
    selection_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "pathway-store",
                "pathway-grid",
                "pathway-cytoscape",
            ],
            changed="pathway-grid.selected_rows",
            input_values={
                "pathway-store": payload,
                "pathway-grid": [0],
                "pathway-cytoscape": None,
            },
            state_values={"pathway-grid": rows},
        ),
    )
    assert selection_response.status_code == 200
    selected = selection_response.get_json()["response"]["pathway-selected-path"]["data"]
    assert selected == {
        "path_rank": 1,
        "species_ids": ["[H]", "[H][O]"],
        "reaction_keys": ["[H]+[O]->[H][O]"],
    }

    network_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["pathway-highlight-network-btn"],
            changed="pathway-highlight-network-btn.n_clicks",
            input_values={"pathway-highlight-network-btn": 1},
            state_values={"pathway-selected-path": selected},
        ),
    )
    assert network_response.status_code == 200
    handoff = network_response.get_json()["response"]["network-store"]["data"]
    assert handoff["species_ids"] == ["[H]", "[H][O]"]
    assert handoff["reaction_keys"] == ["[H]+[O]->[H][O]"]


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
            {"id": "workflow-species-grid", "property": "selected_rows"},
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


def test_species_search_clears_stale_workflow_choices_before_new_selection(monkeypatch) -> None:
    def fake_search(_artifacts, query, **_kwargs):
        assert query == "CH3"
        return {
            "rows": [{"formula": "CH3", "smiles": "[H][C]([H])[H]"}],
            "meta": {"catalog_size": 1, "moname_available": False},
        }

    monkeypatch.setattr(svc, "search_species_catalog", fake_search)
    app = create_app()
    client = app.server.test_client()
    workflow = {
        "dataset_key": "rp3",
        "current_step": 2,
        "species": {"formula": "H2", "smiles": "[H][H]"},
        "channel": {"reaction_smiles": "[H]+[H]->[H][H]"},
        "event": {"event_id": "old"},
        "validations": [],
    }

    search_dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]]
        == ["workflow-species-search", "app-store"]
    )
    search_payload = {
        "output": search_dependency["output"],
        "outputs": [
            {"id": "workflow-species-grid", "property": "data"},
            {"id": "workflow-species-grid", "property": "columns"},
            {"id": "workflow-species-alert", "property": "children"},
            {"id": "workflow-species-grid", "property": "selected_rows"},
        ],
        "changedPropIds": ["workflow-species-search.n_clicks"],
        "inputs": [
            {"id": "workflow-species-search", "property": "n_clicks", "value": 1},
            {"id": "app-store", "property": "data", "value": {"base": "rp3", "artifacts": {"species": "/tmp/example.species"}}},
        ],
        "state": [
            {"id": "workflow-species-query", "property": "value", "value": "CH3"},
            {"id": "workflow-species-kind", "property": "value", "value": "auto"},
            {"id": "workflow-mass-tolerance", "property": "value", "value": 0.5},
            {"id": "workflow-mass-mode", "property": "value", "value": "exact"},
        ],
    }
    search_response = client.post("/_dash-update-component", json=search_payload)
    assert search_response.status_code == 200
    search_data = search_response.get_json()["response"]
    assert search_data["workflow-species-grid"]["selected_rows"] == []

    workflow_dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]][:2]
        == ["app-store", "workflow-species-search"]
    )
    input_values = {
        "app-store": {"base": "rp3", "artifacts": {"species": "/tmp/example.species"}},
        "workflow-species-search": 1,
        "workflow-species-grid": [],
        "workflow-species-confirm": None,
        "workflow-production-grid": [],
        "workflow-consumption-grid": [],
        "workflow-channel-confirm": None,
        "workflow-event-grid": [],
        "workflow-event-confirm": None,
        "workflow-validation-save": None,
        "workflow-step-1": None,
        "workflow-step-2": None,
        "workflow-step-3": None,
        "workflow-step-4": None,
    }
    state_values = {
        "workflow-store": workflow,
        "workflow-species-grid": search_data["workflow-species-grid"]["data"],
        "workflow-production-grid": [],
        "workflow-consumption-grid": [],
        "workflow-event-grid": [],
        "workflow-validation-outcome": "support",
        "workflow-validation-note": "",
    }
    workflow_payload = {
        "output": workflow_dependency["output"],
        "outputs": {"id": "workflow-store", "property": "data"},
        "changedPropIds": ["workflow-species-search.n_clicks"],
        "inputs": [
            {"id": item["id"], "property": item["property"], "value": input_values[item["id"]]}
            for item in workflow_dependency["inputs"]
        ],
        "state": [
            {"id": item["id"], "property": item["property"], "value": state_values[item["id"]]}
            for item in workflow_dependency["state"]
        ],
    }
    workflow_response = client.post("/_dash-update-component", json=workflow_payload)
    assert workflow_response.status_code == 200
    workflow_data = workflow_response.get_json()["response"]["workflow-store"]["data"]
    assert workflow_data["species"] is None
    assert workflow_data["channel"] is None
    assert workflow_data["event"] is None
    assert workflow_data["current_step"] == 1

    selection_payload = dict(workflow_payload)
    selection_payload["changedPropIds"] = ["workflow-species-grid.selected_rows"]
    selection_inputs = [dict(item) for item in workflow_payload["inputs"]]
    for item in selection_inputs:
        if item["id"] == "workflow-species-grid":
            item["value"] = [0]
    selection_payload["inputs"] = selection_inputs
    selection_response = client.post("/_dash-update-component", json=selection_payload)
    assert selection_response.status_code == 200
    selection_data = selection_response.get_json()["response"]["workflow-store"]["data"]
    assert selection_data["species"]["formula"] == "CH3"

    state_dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]] == ["workflow-store"]
        and "workflow-species-choice.children" in item["output"]
    )
    state_payload = {
        "output": state_dependency["output"],
        "outputs": [
            *[
                {"id": f"workflow-panel-{number}", "property": "style"}
                for number in range(1, 5)
            ],
            *[
                {"id": f"workflow-step-{number}", "property": "className"}
                for number in range(1, 5)
            ],
            {"id": "workflow-summary", "property": "children"},
            {"id": "workflow-species-choice", "property": "children"},
            {"id": "workflow-channel-choice", "property": "children"},
            {"id": "workflow-event-choice", "property": "children"},
            {"id": "workflow-species-confirm", "property": "disabled"},
            {"id": "workflow-channel-confirm", "property": "disabled"},
            {"id": "workflow-event-confirm", "property": "disabled"},
            {"id": "workflow-validation-status", "property": "children"},
        ],
        "changedPropIds": ["workflow-store.data"],
        "inputs": [{"id": "workflow-store", "property": "data", "value": selection_data}],
        "state": [],
    }
    state_response = client.post("/_dash-update-component", json=state_payload)
    assert state_response.status_code == 200
    rendered = state_response.get_json()["response"]
    assert "CH3" in rendered["workflow-species-choice"]["children"]
    assert rendered["workflow-species-confirm"]["disabled"] is False


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
        and "dir-browser-path.data" in item["output"]
    )
    outputs = [
        {"id": "dir-browser-path-input", "property": "value"},
        {"id": "dir-browser-back-btn", "property": "disabled"},
        {"id": "dir-browser-current", "property": "children"},
        {"id": "dir-browser-body", "property": "children"},
        {"id": "dir-browser-path", "property": "data"},
        {"id": "dataset-browser-candidate", "property": "data"},
        {"id": "data-apply-btn", "property": "disabled"},
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
                "value": state_values.get(
                    item["id"], {} if item["id"] == "app-store" else []
                ),
            }
            for item in dependency["state"]
        ],
    }


def _data_view_callback_payload(
    client,
    *,
    changed: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "data-overview-view.className" in item["output"]
        and "data-browser-view.className" in item["output"]
    )
    return {
        "output": dependency["output"],
        "outputs": [
            {"id": "data-overview-view", "property": "className"},
            {"id": "data-browser-view", "property": "className"},
        ],
        "changedPropIds": [changed],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": values.get(
                    item["id"] if isinstance(item["id"], str) else json.dumps(item["id"], sort_keys=True),
                    [],
                ),
            }
            for item in dependency["inputs"]
        ],
        "state": [],
    }


def test_data_picker_swaps_views_and_return_preserves_applied_data() -> None:
    app = create_app()
    client = app.server.test_client()

    opened = client.post(
        "/_dash-update-component",
        json=_data_view_callback_payload(
            client,
            changed="data-pick-btn.n_clicks",
            values={"data-pick-btn": 1},
        ),
    )
    assert opened.status_code == 200
    opened_result = opened.get_json()["response"]
    assert "d-none" in opened_result["data-overview-view"]["className"]
    assert "d-none" not in opened_result["data-browser-view"]["className"]
    assert "app-store" not in opened_result

    returned = client.post(
        "/_dash-update-component",
        json=_data_view_callback_payload(
            client,
            changed="dir-browser-cancel-btn.n_clicks",
            values={"dir-browser-cancel-btn": 1},
        ),
    )
    assert returned.status_code == 200
    returned_result = returned.get_json()["response"]
    assert "d-none" not in returned_result["data-overview-view"]["className"]
    assert "d-none" in returned_result["data-browser-view"]["className"]
    assert "app-store" not in returned_result


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


def _preparation_status_callback_payload(
    client,
    *,
    candidate: dict[str, str] | None,
    store: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "data-prep-status.children" in item["output"]
    )
    input_values = {
        "data-modal": True,
        "data-prep-refresh-btn": 1,
        "data-prep-refresh": 0,
        "dataset-browser-candidate": candidate,
    }
    return {
        "output": dependency["output"],
        "outputs": [
            {"id": "data-prep-status", "property": "children"},
            {"id": "data-rng-event-command", "property": "children"},
            {"id": "data-prep-trajectory-command", "property": "children"},
            {"id": "data-prep-composition-command", "property": "children"},
            {"id": "data-rng-event-copy", "property": "content"},
            {"id": "data-prep-trajectory-copy", "property": "content"},
            {"id": "data-prep-composition-copy", "property": "content"},
            {"id": "data-clear-trajectory-btn", "property": "disabled"},
            {"id": "data-prep-refresh", "property": "disabled"},
        ],
        "changedPropIds": ["data-prep-refresh-btn.n_clicks"],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": input_values[item["id"]],
            }
            for item in dependency["inputs"]
        ],
        "state": [
            {"id": item["id"], "property": item["property"], "value": store}
            for item in dependency["state"]
        ],
    }


def _clear_confirmation_callback_payload(
    client,
    *,
    candidate: dict[str, str] | None,
    store: dict[str, Any],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if any(value["id"] == "data-clear-trajectory-btn" for value in item["inputs"])
    )
    input_values = {
        "data-clear-trajectory-btn": 1,
        "data-clear-cancel-btn": None,
    }
    state_values = {
        "dataset-browser-candidate": candidate,
        "app-store": store,
    }
    return {
        "output": dependency["output"],
        "outputs": [
            {"id": "data-clear-confirm-modal", "property": "is_open"},
            {"id": "data-clear-confirm-text", "property": "children"},
            {"id": "data-clear-kind-store", "property": "data"},
            {"id": "data-prep-clear-alert", "property": "children"},
        ],
        "changedPropIds": ["data-clear-trajectory-btn.n_clicks"],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": input_values[item["id"]],
            }
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


def _clear_confirmed_callback_payload(
    client,
    *,
    request: dict[str, str],
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if any(value["id"] == "data-clear-confirm-btn" for value in item["inputs"])
    )
    return {
        "output": dependency["output"],
        "outputs": [
            {"id": "data-clear-confirm-modal", "property": "is_open"},
            {"id": "data-prep-clear-alert", "property": "children"},
        ],
        "changedPropIds": ["data-clear-confirm-btn.n_clicks"],
        "inputs": [
            {"id": item["id"], "property": item["property"], "value": 1}
            for item in dependency["inputs"]
        ],
        "state": [
            {"id": item["id"], "property": item["property"], "value": request}
            for item in dependency["state"]
        ],
    }


def _discovered_candidate(folder: Path, name: str = "run.lammpstrj") -> dict[str, str]:
    (folder / f"{name}.reactionabcd").touch()
    (folder / f"{name}.species").touch()
    return {
        "folder": str(folder),
        "base": str(folder / name),
        "label": name,
    }


def test_candidate_preview_rejects_untrusted_candidates_before_scan(
    tmp_path, monkeypatch
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    scan_calls: list[tuple[str, str]] = []

    def fake_scan(folder: str, *, base: str = "") -> dict[str, Any]:
        scan_calls.append((folder, base))
        return {"dataset": {"selected_base": base}}

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(svc, "scan_dataset", fake_scan)
    app = create_app()
    client = app.server.test_client()
    candidates = [
        {
            "folder": str(outside),
            "base": str(outside / "run.lammpstrj"),
            "label": "outside",
        },
        {
            "folder": str(allowed),
            "base": str(allowed / "forged.lammpstrj"),
            "label": "forged",
        },
    ]

    for candidate in candidates:
        response = client.post(
            "/_dash-update-component",
            json=_candidate_status_callback_payload(
                client,
                candidate=candidate,
                store={"folder": "", "base": "", "label": "未选择"},
            ),
        )
        assert response.status_code == 200
        assert response.get_json()["response"]["data-apply-btn"]["disabled"] is True

    assert scan_calls == []


def test_preparation_refresh_rejects_untrusted_candidate_and_app_store_before_service(
    tmp_path, monkeypatch
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    preparation_calls: list[tuple[str, str]] = []

    def fake_preparation(folder: str, *, base: str = "") -> dict[str, Any]:
        preparation_calls.append((folder, base))
        return {"trajectory": {"state": "missing"}}

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(svc, "dataset_preparation_status", fake_preparation)
    app = create_app()
    client = app.server.test_client()
    forged = {
        "folder": str(outside),
        "base": str(outside / "run.lammpstrj"),
        "label": "outside",
    }

    candidate_response = client.post(
        "/_dash-update-component",
        json=_preparation_status_callback_payload(
            client,
            candidate=forged,
            store={"folder": str(allowed), "base": "", "label": "old"},
        ),
    )
    store_response = client.post(
        "/_dash-update-component",
        json=_preparation_status_callback_payload(
            client,
            candidate=None,
            store=forged,
        ),
    )

    assert candidate_response.status_code == 200
    assert store_response.status_code == 200
    assert preparation_calls == []
    for response in (candidate_response, store_response):
        result = response.get_json()["response"]
        assert result["data-clear-trajectory-btn"]["disabled"] is True


def test_preparation_refresh_keeps_discovered_app_store_fallback_usable(
    tmp_path, monkeypatch
) -> None:
    candidate = _discovered_candidate(tmp_path)
    preparation_calls: list[tuple[str, str]] = []

    def fake_preparation(folder: str, *, base: str = "") -> dict[str, Any]:
        preparation_calls.append((folder, base))
        return {
            "trajectory": {"state": "ready"},
            "rng_event_command": "rng",
            "trajectory_command": "trajectory",
            "composition_command": "composition",
        }

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(svc, "dataset_preparation_status", fake_preparation)
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_preparation_status_callback_payload(
            client,
            candidate=None,
            store=candidate,
        ),
    )

    assert response.status_code == 200
    assert preparation_calls == [(candidate["folder"], candidate["base"])]
    assert response.get_json()["response"]["data-clear-trajectory-btn"]["disabled"] is False


def test_clear_confirmation_rejects_untrusted_candidate_before_status_service(
    tmp_path, monkeypatch
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    preparation_calls: list[tuple[str, str]] = []

    def fake_preparation(folder: str, *, base: str = "") -> dict[str, Any]:
        preparation_calls.append((folder, base))
        return {"trajectory": {"state": "ready", "index_size": 10}}

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(svc, "dataset_preparation_status", fake_preparation)
    app = create_app()
    client = app.server.test_client()
    forged = {
        "folder": str(outside),
        "base": str(outside / "run.lammpstrj"),
        "label": "outside",
    }

    response = client.post(
        "/_dash-update-component",
        json=_clear_confirmation_callback_payload(
            client,
            candidate=forged,
            store={"folder": str(allowed), "base": "", "label": "old"},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert preparation_calls == []
    assert result["data-clear-confirm-modal"]["is_open"] is False
    assert result["data-clear-kind-store"]["data"] == {}


def test_clear_confirmation_keeps_discovered_app_store_fallback_usable(
    tmp_path, monkeypatch
) -> None:
    candidate = _discovered_candidate(tmp_path)
    preparation_calls: list[tuple[str, str]] = []

    def fake_preparation(folder: str, *, base: str = "") -> dict[str, Any]:
        preparation_calls.append((folder, base))
        return {"trajectory": {"state": "ready", "index_size": 10}}

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(svc, "dataset_preparation_status", fake_preparation)
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_clear_confirmation_callback_payload(
            client,
            candidate=None,
            store=candidate,
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert preparation_calls == [(candidate["folder"], candidate["base"])]
    assert result["data-clear-confirm-modal"]["is_open"] is True
    assert result["data-clear-kind-store"]["data"] == {
        "kind": "trajectory",
        "folder": candidate["folder"],
        "base": candidate["base"],
    }


def test_confirmed_clear_rejects_forged_request_before_clear_service(
    tmp_path, monkeypatch
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    clear_calls: list[tuple[str, str, str]] = []

    def fake_clear(folder: str, *, base: str = "", kind: str) -> dict[str, Any]:
        clear_calls.append((folder, base, kind))
        return {"removed": [], "released_bytes": 0}

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(svc, "clear_dataset_index", fake_clear)
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_clear_confirmed_callback_payload(
            client,
            request={
                "folder": str(outside),
                "base": str(outside / "run.lammpstrj"),
                "kind": "trajectory",
            },
        ),
    )

    assert response.status_code == 200
    assert clear_calls == []


def test_load_selected_dataset_updates_store_closes_modal_and_remembers_it(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    (tmp_path / "rp3.lammpstrj.reactionabcd").touch()
    (tmp_path / "rp3.lammpstrj.species").touch()
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
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    (tmp_path / "rp3.lammpstrj.reactionabcd").touch()
    (tmp_path / "rp3.lammpstrj.species").touch()
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


def test_browser_load_applies_selected_candidate_atomically_in_one_click(
    tmp_path, monkeypatch
) -> None:
    """The browser load action performs the final scan and exact-base apply."""
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
    result = load_response.get_json()["response"]
    assert result["app-store"]["data"]["base"] == selected["base"]
    assert result["data-modal"]["is_open"] is False


def test_browser_load_rejects_forged_out_of_root_candidate_before_scan(
    tmp_path, monkeypatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    forged = tmp_path / "outside"
    forged.mkdir()
    candidate = {
        "folder": str(forged),
        "base": str(forged / "run.lammpstrj"),
        "label": "run.lammpstrj",
    }
    old_store = {"folder": str(allowed), "base": "", "label": "old"}
    old_recent = [
        {
            "folder": str(allowed),
            "base": str(allowed / "old.lammpstrj"),
            "label": "old",
            "loaded_at": 1,
        }
    ]
    scan_calls = 0

    def fake_scan(_folder: str, *, base: str = "") -> dict[str, Any]:
        nonlocal scan_calls
        scan_calls += 1
        return {
            "dataset": {
                "selected_base": base,
                "label": "run.lammpstrj",
                "ready_count": 2,
                "artifacts": {},
                "capabilities": {},
                "readiness": {},
            }
        }

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(svc, "scan_dataset", fake_scan)
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_load_dataset_callback_payload(
            client,
            candidate=candidate,
            store=old_store,
            recent_records=old_recent,
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert scan_calls == 0
    assert result["app-store"]["data"] == old_store
    assert result["recent-datasets"]["data"] == old_recent
    assert result["data-modal"]["is_open"] is True
    assert result["dataset-browser-candidate"]["data"] is None


def test_browser_path_bar_resolves_exact_dataset_prefix(tmp_path, monkeypatch) -> None:
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
            changed="dir-browser-path-input.value",
            values={"dir-browser-path-input": str(tmp_path / "rp4.lammpstrj")},
            state_values={
                "dir-browser-path": str(tmp_path),
                "data-folder-input": "",
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
    assert result["dir-browser-path"]["data"] == str(tmp_path)
    assert result["data-apply-btn"]["disabled"] is False


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
    assert response.get_json()["response"]["dir-browser-path"]["data"] == str(tmp_path)


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
    assert result["dir-browser-path"]["data"] == str(dataset.resolve())
    assert result["dir-browser-back-btn"]["disabled"] is False
    assert result["dataset-browser-candidate"]["data"] == {
        "folder": str(dataset),
        "base": str(dataset / "rp3.lammpstrj"),
        "label": "rp3.lammpstrj",
    }
    assert result["data-apply-btn"]["disabled"] is False
    rendered = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    assert "rs-browser-candidate-row is-selected" in rendered


def test_directory_browser_reopens_at_applied_dataset_when_manual_path_blank(
    tmp_path, monkeypatch
) -> None:
    """A normal load supplies the next browser start path without hidden input state."""
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    dataset = tmp_path / "campaign" / "run"
    dataset.mkdir(parents=True)
    (dataset / "rp3.lammpstrj.reactionabcd").touch()
    (dataset / "rp3.lammpstrj.species").touch()
    candidate = {
        "folder": str(dataset),
        "base": str(dataset / "rp3.lammpstrj"),
        "label": "rp3.lammpstrj",
    }

    def fake_scan(folder: str, *, base: str = "") -> dict[str, Any]:
        assert folder == str(dataset)
        assert base == candidate["base"]
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
    loaded = client.post(
        "/_dash-update-component",
        json=_load_dataset_callback_payload(
            client,
            candidate=candidate,
            store={"folder": "", "base": "", "label": ""},
            recent_records=[],
        ),
    )
    assert loaded.status_code == 200
    applied = loaded.get_json()["response"]["app-store"]["data"]

    reopened = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-pick-btn.n_clicks",
            values={"data-pick-btn": 1},
            state_values={
                "dir-browser-path": "",
                "data-folder-input": "",
                "dataset-browser-candidate": None,
                "recent-datasets": [],
                "app-store": applied,
            },
        ),
    )
    assert reopened.status_code == 200
    assert reopened.get_json()["response"]["dir-browser-path"]["data"] == str(dataset)


def test_directory_browser_navigation_does_not_output_applied_data(tmp_path, monkeypatch) -> None:
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
    assert "app-store" not in result

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-back-btn.n_clicks",
            values={"dir-browser-back-btn": 1},
            state_values=common_state,
        ),
    )
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-path"]["data"] == str(tmp_path)
    assert "app-store" not in result


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
    assert result["data-apply-btn"]["disabled"] is True
    rendered = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    assert rendered.count("rs-browser-candidate-row") == 2
    assert "is-selected" not in rendered

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
    assert result["data-apply-btn"]["disabled"] is False
    rendered = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    assert "rs-browser-candidate-row is-selected" in rendered

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


def test_explicit_browser_selection_uses_one_directory_snapshot(
    tmp_path, monkeypatch
) -> None:
    candidate = _discovered_candidate(tmp_path)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    real_browse = svc.browse_dataset_location
    browse_calls: list[str] = []

    def counted_browse(folder: str) -> dict[str, Any]:
        browse_calls.append(folder)
        return real_browse(folder)

    monkeypatch.setattr(svc, "browse_dataset_location", counted_browse)
    app = create_app()
    client = app.server.test_client()
    selected = {"type": "dir-browser-dataset", "base": candidate["base"]}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(selected, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"base":["ALL"],"type":"dir-browser-dataset"}': [1]},
        state_values={
            "dir-browser-path": str(tmp_path),
            "data-folder-input": "",
            "recent-datasets": [],
            "dataset-browser-candidate": None,
        },
    )
    for item in payload["inputs"]:
        if item["id"] == '{"base":["ALL"],"type":"dir-browser-dataset"}':
            item["id"] = selected

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    assert browse_calls == [str(tmp_path)]
    assert response.get_json()["response"]["dataset-browser-candidate"]["data"] == candidate


def test_mobile_browser_css_targets_input_group_component_and_has_no_obsolete_card_rule() -> None:
    css = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "app.css"
    ).read_text(encoding="utf-8")

    assert ".rs-browser-path-control .input-group" not in css
    assert ".rs-dataset-card" not in css
    assert ".rs-browser-path-control { flex-wrap: wrap; }" in css


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
