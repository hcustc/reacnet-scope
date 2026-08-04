from __future__ import annotations

import base64
import csv
import io
import json
from pathlib import Path
from typing import Any

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope import dir_browser
from scripts import rng_query_cli as cli
from scripts.webapp_dash import callbacks as cb
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.navigation import NAV_GROUPS, TOP_NAV_PAGE_IDS
from reacnet_scope import services as svc


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


def _layout_node_by_class(node: Any, class_name: str) -> dict[str, Any] | None:
    if isinstance(node, dict):
        classes = str((node.get("props") or {}).get("className") or "").split()
        if class_name in classes:
            return node
        for value in node.values():
            found = _layout_node_by_class(value, class_name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _layout_node_by_class(value, class_name)
            if found is not None:
                return found
    return None


def test_dash_layout_and_callback_dependencies_are_loadable() -> None:
    app = create_app()
    client = app.server.test_client()

    root = client.get("/")
    assert root.status_code == 200
    root_body = root.get_data(as_text=True)
    assert "bootstrap-local.css" in root_body
    assert "jsdelivr" not in root_body
    assert "cdnjs" not in root_body
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["service"] == "reacnet-scope"
    layout_response = client.get("/_dash-layout")
    dependency_response = client.get("/_dash-dependencies")
    assert layout_response.status_code == 200
    assert dependency_response.status_code == 200

    layout = layout_response.get_json()
    layout_ids = _layout_string_ids(layout)
    for navigation_id in {
        "nav-species",
        "nav-intermediate",
        "inter-to-pathway-btn",
        "inter-to-evolution-btn",
        "species-to-channels-btn",
        "species-to-evolution-btn",
        "species-structure-grid",
        "species-structure-results",
        "species-structure-csv-btn",
        "rxn-production-grid",
        "rxn-consumption-grid",
        "event-back-btn",
        "nav-trajectory",
        "page-trajectory",
        "trajectory-back-events-btn",
        "trajectory-refresh-btn",
        "nav-data-management",
        "page-data-management",
        "data-open-batch-compare-btn",
    }:
        assert navigation_id in layout_ids
    for removed_id in {
        "nav-workflow",
        "page-workflow",
        "nav-literature",
        "page-literature",
        "nav-batch-compare",
        "data-modal",
        "species-mass-mode",
        "species-top",
    }:
        assert removed_id not in layout_ids
    assert "page-home" not in layout_ids
    assert "nav-home" not in layout_ids
    assert "page-description" in layout_ids
    assert "page-eyebrow-section" in layout_ids
    assert "按分子式、SMILES" in str(
        ((_layout_node_by_id(layout, "page-description") or {}).get("props") or {}).get(
            "children"
        )
    )
    assert (
        (_layout_node_by_id(layout, "species-to-event-btn") or {})["props"][
            "children"
        ]
        == "经反应通道定位事件"
    )
    assert "rs-top-nav-item" in str(
        ((_layout_node_by_id(layout, "nav-species") or {}).get("props") or {}).get(
            "className"
        )
    )
    species_grid = _layout_node_by_id(layout, "species-grid") or {}
    structure_grid = _layout_node_by_id(layout, "species-structure-grid") or {}
    event_grid = _layout_node_by_id(layout, "event-grid") or {}
    assert (species_grid.get("props") or {}).get("page_action") == "native"
    assert (species_grid.get("props") or {}).get("page_size") == 20
    assert (structure_grid.get("props") or {}).get("page_action") == "native"
    assert (structure_grid.get("props") or {}).get("page_size") == 50
    assert (event_grid.get("props") or {}).get("page_action") == "native"
    assert (event_grid.get("props") or {}).get("page_size") == 25
    event_results_card = _layout_node_by_id(layout, "event-results-card") or {}
    event_results_classes = str(
        (event_results_card.get("props") or {}).get("className") or ""
    ).split()
    assert "rs-event-results-card" in event_results_classes
    assert "rs-flex-fill" not in event_results_classes
    assert (
        ((_layout_node_by_id(layout, "page-store") or {}).get("props") or {}).get(
            "data"
        )
        == {"page": "species"}
    )
    species_grid = _layout_node_by_id(layout, "species-grid")
    assert species_grid is not None
    assert (species_grid.get("props") or {}).get("row_selectable") == "single"
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
    assert "data-rng-event-command" not in layout_ids
    assert "data-clear-event-btn" in layout_ids
    assert "data-clear-trajectory-btn" in layout_ids
    assert "data-clear-composition-btn" in layout_ids
    assert "data-advanced-tools" not in layout_ids
    assert "data-global-min-tp" not in layout_ids
    assert "data-overrides-apply-btn" not in layout_ids
    assert "global-operation-progress" in layout_ids
    assert "data-override-reaction" not in layout_ids
    assert "data-override-reactionevent" not in layout_ids
    assert "element-distribution-reference-smiles" in layout_ids
    assert "element-distribution-timestep" in layout_ids
    assert "element-distribution-parent-name" not in layout_ids
    assert "element-distribution-group-element" in layout_ids
    assert "element-distribution-max-count" in layout_ids
    assert "element-distribution-include-zero" in layout_ids
    assert "element-distribution-filter-element" in layout_ids
    assert "element-distribution-filter-mode" in layout_ids
    assert "inter-product-ratio" in layout_ids
    assert "inter-reactant-ratio" in layout_ids
    for removed_advanced_id in {
        "element-distribution-advanced-search-btn",
        "element-distribution-advanced-species-files",
        "element-distribution-advanced-mode",
        "element-distribution-advanced-layout",
        "element-distribution-advanced-smoothing",
        "element-distribution-advanced-viewer",
        "element-distribution-advanced-csv-download",
        "element-distribution-advanced-svg-download",
    }:
        assert removed_advanced_id not in layout_ids
    for event_tool_id in {
        "event-frames-csv-download",
        "event-package-btn",
        "event-package-download",
        "event-trajectory-download",
        "event-ovito-download",
        "event-ovito-open-btn",
        "event-ovito-launch-status",
        "event-vmd-download",
        "event-atom-ids-copy",
        "event-ovito-expression-copy",
        "event-type-map-editor",
        "event-type-map-status",
        "event-type-map-clear-btn",
        "event-environment-radius",
        "event-trajectory-3dmol",
        "event-3dmol-status",
        "event-core-label-toggle",
        "event-atom-inspector",
        "event-core-atom-list",
        "event-atom-inspector-body",
    }:
        assert event_tool_id in layout_ids
    event_scope = _layout_node_by_id(layout, "event-view-scope") or {}
    assert [
        option["value"]
        for option in (event_scope.get("props") or {}).get("options", [])
    ] == ["context", "participants", "core"]
    assert (event_scope.get("props") or {}).get("value") == "participants"
    events_page = _layout_node_by_id(layout, "page-events") or {}
    trajectory_page = _layout_node_by_id(layout, "page-trajectory") or {}
    event_page_ids = _layout_string_ids(events_page)
    trajectory_page_ids = _layout_string_ids(trajectory_page)
    assert "event-extract-btn" in event_page_ids
    assert "event-viewer-card" not in event_page_ids
    assert "event-viewer-card" in trajectory_page_ids
    assert "event-trajectory-3dmol" in trajectory_page_ids
    trajectory_body = _layout_node_by_class(trajectory_page, "rs-trajectory-card-body")
    trajectory_tools = _layout_node_by_class(trajectory_page, "rs-trajectory-tools")
    assert trajectory_body is not None
    assert trajectory_tools is not None
    assert (trajectory_tools.get("props") or {}).get("open") is not True
    for pathway_id in {
        "pathway-start-smiles",
        "pathway-direction",
        "pathway-max-depth",
        "pathway-max-branches",
        "pathway-max-paths",
        "pathway-max-expansions",
        "pathway-min-net-tp",
        "pathway-min-directionality",
        "pathway-search-btn",
        "pathway-grid",
        "pathway-cytoscape",
        "pathway-json-download",
        "pathway-csv-download",
        "pathway-open-events-btn",
        "pathway-evidence-alert",
        "pathway-evidence-grid",
        "pathway-store",
        "pathway-context-store",
    }:
        assert pathway_id in layout_ids
    assert (_layout_node_by_id(layout, "pathway-max-depth") or {})["props"]["value"] == 3
    assert (_layout_node_by_id(layout, "pathway-max-branches") or {})["props"]["value"] == 5
    assert (_layout_node_by_id(layout, "pathway-max-paths") or {})["props"]["value"] == 20
    assert (_layout_node_by_id(layout, "pathway-max-expansions") or {})["props"]["value"] == 5000
    assert (
        (_layout_node_by_id(layout, "pathway-goal") or {})["props"]["value"]
        == "ranked"
    )
    assert "page-transitions" not in layout_ids
    assert "nav-transitions" not in layout_ids
    assert "page-network" not in layout_ids
    assert "nav-network" not in layout_ids

    missing: list[str] = []
    for dependency in dependency_response.get_json():
        for item in dependency.get("inputs", []) + dependency.get("state", []):
            component_id = str(item.get("id") or "")
            if component_id.startswith("{"):
                continue
            if component_id not in layout_ids:
                missing.append(component_id)
    assert missing == []
    clientside_3dmol = next(
        item
        for item in dependency_response.get_json()
        if item.get("output") == "event-3dmol-status.children"
    )
    assert clientside_3dmol["clientside_function"] == {
        "namespace": "reacnetScope",
        "function_name": "renderEventTrajectory",
    }
    assert {
        "id": "event-core-label-toggle",
        "property": "value",
    } in clientside_3dmol["inputs"]
    overview = _layout_node_by_id(layout, "data-overview-view")
    browser = _layout_node_by_id(layout, "data-browser-view")
    assert overview is not None
    assert browser is not None
    assert "d-none" not in str((overview.get("props") or {}).get("className") or "")
    assert "d-none" in str((browser.get("props") or {}).get("className") or "")
    assert "data-recent-datasets" in _layout_string_ids(overview)
    element_distribution_refresh = _layout_node_by_id(layout, "element-distribution-index-refresh")
    operation_progress = _layout_node_by_id(layout, "global-operation-progress")
    assert element_distribution_refresh is not None
    assert operation_progress is not None
    assert (element_distribution_refresh.get("props") or {}).get("disabled") is True
    assert (operation_progress.get("props") or {}).get("role") == "status"
    assert "progressbar" in json.dumps(operation_progress)
    layout_text = json.dumps(layout, ensure_ascii=False)
    assert "rs-advanced-menu" not in layout_text
    assert "rs-tool-menu" not in layout_text
    assert "运行组 (base)" not in layout_text
    assert "使用此数据集" in layout_text


def test_navigation_groups_cover_each_tool_once() -> None:
    grouped_pages = [
        page_id
        for _group_label, page_ids in NAV_GROUPS
        for page_id in page_ids
    ]

    assert len(grouped_pages) == 8
    assert len(set(grouped_pages)) == len(grouped_pages)
    assert tuple(grouped_pages) == TOP_NAV_PAGE_IDS


def _callback_payload(
    client: Any,
    *,
    input_ids: list[str],
    changed: str | list[str],
    input_values: dict[str, Any],
    state_values: dict[str, Any],
    output_id: str = "",
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if [value["id"] for value in item["inputs"]] == input_ids
        and (
            not output_id
            or f"{output_id}." in str(item.get("output") or "")
        )
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
        "changedPropIds": (
            list(changed) if isinstance(changed, list) else [changed]
        ),
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


def test_top_navigation_opens_automatic_analysis_tool() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["nav-intermediate"] = 1
    payload = _callback_payload(
        client,
        input_ids=input_ids,
        changed="nav-intermediate.n_clicks",
        input_values=input_values,
        state_values={"page-store": {"page": "species"}},
        output_id="page-species",
    )

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "intermediate"}
    assert body["page-intermediate"]["className"] == "rs-page active"
    assert body["nav-intermediate"]["className"] == "rs-top-nav-item active"
    assert body["app-body"]["className"] == "rs-body rs-tool-shell"


def test_mass_formula_selection_restores_structure_results(tmp_path: Path) -> None:
    reaction = tmp_path / "mass-structures.reactionabcd"
    structures = [
        "[H][C]([H])[C]([H])[C]([H])[C][C][O]",
        "[O][C][C][C]([H])[C]([H])[C]([H])[H]",
        "[C]([H])([H])[C]([H])[C]([H])[C][C][O]",
    ]
    reaction.write_text(
        "\n".join(
            [
                f"10 {structures[0]}->[C]",
                f"6 {structures[1]}->[C]",
                f"2 {structures[2]}->[C]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = {"reaction": str(reaction)}
    client = create_app().server.test_client()

    search_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-search-btn"],
            changed="species-search-btn.n_clicks",
            input_values={"species-search-btn": 1},
            state_values={
                "species-query": "80",
                "species-query-kind": "mass",
                "species-mass-tol": 0.1,
                "app-store": {"artifacts": artifacts},
            },
            output_id="species-grid",
        ),
    )
    assert search_response.status_code == 200
    search_body = search_response.get_json()["response"]
    formula_rows = search_body["species-grid"]["data"]
    grid_store = search_body["species-grid-store"]["data"]
    assert formula_rows[0]["formula"] == "C5H4O"
    assert formula_rows[0]["structure_count"] == 3
    assert search_body["species-grid"]["page_size"] == 20
    assert "选择分子式可查看原始结构结果" in search_body["species-alert"]["children"]

    structure_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-grid"],
            changed="species-grid.selected_rows",
            input_values={"species-grid.selected_rows": [0]},
            state_values={
                "species-grid.data": formula_rows,
                "species-grid-store": grid_store,
                "app-store": {"artifacts": artifacts},
            },
            output_id="species-structure-results",
        ),
    )
    assert structure_response.status_code == 200
    structure_body = structure_response.get_json()["response"]
    structure_rows = structure_body["species-structure-grid"]["data"]
    assert structure_body["species-structure-results"]["style"] == {
        "display": "block"
    }
    assert len(structure_rows) == 3
    assert [row["smiles"] for row in structure_rows] == structures
    assert "共 3 个结构" in structure_body["species-structure-alert"]["children"]
    assert "每页显示 50 条" in structure_body["species-structure-alert"]["children"]


def test_batch_compare_opens_from_data_management() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["data-open-batch-compare-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="data-open-batch-compare-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "species"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "batch-compare"}
    assert body["page-batch-compare"]["className"] == "rs-page active"
    assert all(
        body[f"nav-{page_id}"]["className"] == "rs-top-nav-item"
        for page_id in TOP_NAV_PAGE_IDS
    )


def test_data_management_opens_as_workspace_page() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["open-data-modal"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="open-data-modal.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "species"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "data-management"}
    assert body["page-species"]["className"] == "rs-page"
    assert body["page-data-management"]["className"] == "rs-page rs-data-page active"
    assert body["nav-data-management"]["className"] == (
        "rs-top-nav-item rs-nav-utility active"
    )
    assert body["page-title"]["children"] == "管理数据"
    assert body["page-eyebrow-section"]["children"] == "数据工作区"


def test_selected_species_channel_action_opens_reaction_search() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["species-to-channels-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="species-to-channels-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "species"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "reactions"}
    assert body["page-reactions"]["className"] == "rs-page active"
    assert body["nav-reactions"]["className"] == "rs-top-nav-item active"


def test_selected_species_opens_prefilled_time_evolution(monkeypatch) -> None:
    smiles = "[H][O][H]"
    row = {"formula": "H2O", "smiles": smiles}
    monkeypatch.setattr(
        svc,
        "species_detail",
        lambda _artifacts, _smiles: {
            "ok": True,
            "formula": "H2O",
            "smiles": _smiles,
        },
    )
    monkeypatch.setattr(
        svc,
        "render_species_svg",
        lambda _smiles: {"ok": False, "message": "structure unavailable"},
    )
    app = create_app()
    client = app.server.test_client()

    detail_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-grid", "species-structure-grid"],
            changed="species-grid.selected_rows",
            input_values={
                "species-grid.selected_rows": [0],
                "species-structure-grid.selected_rows": [],
            },
            state_values={
                "species-grid.data": [row],
                "species-structure-grid.data": [],
                "app-store.data": {"artifacts": {"species": "/tmp/run.species"}},
                "species-grid-store.data": {
                    "query_kind": "smiles",
                    "rows": [row],
                },
            },
            output_id="detail-panel",
        ),
    )

    assert detail_response.status_code == 200
    detail = detail_response.get_json()["response"]
    assert detail["species-to-evolution-btn"]["disabled"] is False
    assert detail["evolution-targets"]["value"] == smiles
    assert detail["app-store"]["data"]["selected_smiles"] == smiles

    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["species-to-evolution-btn"] = 1
    navigation_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="species-to-evolution-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "species"}},
            output_id="page-species",
        ),
    )

    assert navigation_response.status_code == 200
    navigation = navigation_response.get_json()["response"]
    assert navigation["page-store"]["data"] == {"page": "evolution"}
    assert navigation["page-evolution"]["className"] == "rs-page active"
    assert navigation["nav-evolution"]["className"] == "rs-top-nav-item active"


def test_selected_species_event_action_opens_reaction_channels() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["species-to-event-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="species-to-event-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "species"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "reactions"}
    assert body["page-reactions"]["className"] == "rs-page active"
    assert body["nav-reactions"]["className"] == "rs-top-nav-item active"

    view_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "species-to-channels-btn",
                "species-to-event-btn",
                "rxn-channel-back-btn",
                "nav-reactions",
            ],
            changed="species-to-event-btn.n_clicks",
            input_values={
                "species-to-channels-btn": 0,
                "species-to-event-btn": 1,
                "rxn-channel-back-btn": 0,
                "nav-reactions": 0,
            },
            state_values={},
            output_id="rxn-channel-view",
        ),
    )
    assert view_response.status_code == 200
    view = view_response.get_json()["response"]
    assert view["rxn-query-card"]["style"] == {"display": "none"}
    assert view["rxn-results-card"]["style"] == {"display": "none"}
    assert view["rxn-channel-view"]["style"] == {"display": "block"}


def test_channel_view_returns_to_species_search() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["rxn-channel-back-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="rxn-channel-back-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "reactions"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "species"}
    assert body["page-species"]["className"] == "rs-page active"
    assert body["page-reactions"]["className"] == "rs-page"
    assert body["nav-species"]["className"] == "rs-top-nav-item active"


def test_selected_species_loads_exact_production_and_consumption_channels(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(artifacts, smiles, *, top):
        captured.update({"artifacts": artifacts, "smiles": smiles, "top": top})
        base = {
            "rank": 1,
            "reaction_formulas": "C + O -> CO",
            "reaction_smiles": "[C] + [O] -> [C][O]",
            "reactant_smiles": ["[C]", "[O]"],
            "product_smiles": ["[C][O]"],
            "forward_tp": 10,
            "reverse_tp": 2,
            "net_tp": 8,
            "ratio_pct": 80.0,
        }
        return {
            "production_rows": [{**base, "role_label": "生成"}],
            "consumption_rows": [
                {
                    **base,
                    "role_label": "消耗",
                    "reaction_formulas": "CO -> C + O",
                    "reaction_smiles": "[C][O] -> [C] + [O]",
                }
            ],
        }

    monkeypatch.setattr(svc, "collect_species_channels", fake_collect)
    client = create_app().server.test_client()
    view_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "species-to-channels-btn",
                "species-to-event-btn",
                "rxn-channel-back-btn",
                "nav-reactions",
            ],
            changed="species-to-channels-btn.n_clicks",
            input_values={
                "species-to-channels-btn": 1,
                "species-to-event-btn": 0,
                "rxn-channel-back-btn": 0,
                "nav-reactions": 0,
            },
            state_values={},
            output_id="rxn-channel-view",
        ),
    )
    assert view_response.status_code == 200
    view = view_response.get_json()["response"]
    assert view["rxn-query-card"]["style"] == {"display": "none"}
    assert view["rxn-results-card"]["style"] == {"display": "none"}
    assert view["rxn-channel-view"]["style"] == {"display": "block"}

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-to-channels-btn", "species-to-event-btn"],
            changed="species-to-channels-btn.n_clicks",
            input_values={
                "species-to-channels-btn": 1,
                "species-to-event-btn": 0,
            },
            state_values={
                "rxn-top": 12,
                "app-store": {
                    "selected_smiles": "[C][O]",
                    "selected_formula": "CO",
                    "artifacts": {"reaction": "run.reactionabcd"},
                },
            },
            output_id="rxn-production-grid",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert captured == {
        "artifacts": {"reaction": "run.reactionabcd"},
        "smiles": "[C][O]",
        "top": 12,
    }
    assert body["rxn-production-grid"]["data"][0]["role_label"] == "生成"
    assert body["rxn-consumption-grid"]["data"][0]["role_label"] == "消耗"
    assert body["rxn-production-grid"]["columns"][0] == {
        "id": "reaction_formulas",
        "name": "反应式",
    }
    assert "生成通道 1 条，消耗通道 1 条" in body["rxn-channel-alert"]["children"]
    assert body["rxn-production-grid"]["selected_rows"] == []
    assert body["rxn-consumption-grid"]["selected_rows"] == []


def test_selected_species_channel_can_be_sent_to_event_search(monkeypatch) -> None:
    row = {
        "role_label": "消耗",
        "reaction_formulas": "CO -> C + O",
        "reaction_smiles": "[C][O] -> [C] + [O]",
    }
    client = create_app().server.test_client()
    selection_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "rxn-production-grid",
                "rxn-consumption-grid",
            ],
            changed="rxn-consumption-grid.selected_rows",
            input_values={
                "rxn-production-grid.selected_rows": [],
                "rxn-consumption-grid.selected_rows": [0],
            },
            state_values={
                "rxn-production-grid.data": [],
                "rxn-consumption-grid.data": [row],
            },
            output_id="rxn-channel-selection-store",
        ),
    )
    assert selection_response.status_code == 200
    selection = selection_response.get_json()["response"][
        "rxn-channel-selection-store"
    ]["data"]
    assert selection == {"lane": "consumption", "row": row}

    monkeypatch.setattr(
        svc,
        "build_channel_structure_detail",
        lambda *_args, **_kwargs: {"ok": False},
    )
    render_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "rxn-channel-selection-store",
                "rxn-channel-show-h",
            ],
            changed="rxn-channel-selection-store.data",
            input_values={
                "rxn-channel-selection-store": selection,
                "rxn-channel-show-h": True,
            },
            state_values={},
            output_id="rxn-channel-detail",
        ),
    )
    assert render_response.status_code == 200
    rendered = render_response.get_json()["response"]
    assert rendered["rxn-channel-to-event-btn"]["disabled"] is False
    assert "已选消耗通道" in rendered["rxn-channel-choice"]["children"]

    event_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "rxn-to-event-btn",
                "rxn-channel-to-event-btn",
            ],
            changed="rxn-channel-to-event-btn.n_clicks",
            input_values={
                "rxn-to-event-btn": 0,
                "rxn-channel-to-event-btn": 1,
            },
            state_values={
                "rxn-grid.selected_rows": [],
                "rxn-grid.data": [],
                "rxn-channel-selection-store": selection,
            },
            output_id="event-reaction-text",
        ),
    )
    assert event_response.status_code == 200
    assert (
        event_response.get_json()["response"]["event-reaction-text"]["value"]
        == "[C][O] -> [C] + [O]"
    )


def test_event_page_returns_to_originating_reaction_channel() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["rxn-channel-to-event-btn"] = 1

    entered_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="rxn-channel-to-event-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "reactions"}},
            output_id="page-species",
        ),
    )
    assert entered_response.status_code == 200
    entered = entered_response.get_json()["response"]
    event_context = entered["page-store"]["data"]
    assert event_context == {
        "page": "events",
        "return_page": "reactions",
        "return_label": "返回反应通道",
    }

    button_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["page-store"],
            changed="page-store.data",
            input_values={"page-store": event_context},
            state_values={},
            output_id="event-back-btn",
        ),
    )
    assert button_response.status_code == 200
    button = button_response.get_json()["response"]["event-back-btn"]
    assert button["children"] == "← 返回反应通道"
    assert button["style"] == {}

    back_values = {item["id"]: 0 for item in dependency["inputs"]}
    back_values["event-back-btn"] = 1
    back_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="event-back-btn.n_clicks",
            input_values=back_values,
            state_values={"page-store": event_context},
            output_id="page-species",
        ),
    )
    assert back_response.status_code == 200
    returned = back_response.get_json()["response"]
    assert returned["page-store"]["data"] == {"page": "reactions"}
    assert returned["page-reactions"]["className"] == "rs-page active"
    assert returned["nav-reactions"]["className"] == "rs-top-nav-item active"


def test_event_selection_opens_independent_trajectory_page_and_returns() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    event_context = {
        "page": "events",
        "return_page": "reactions",
        "return_label": "返回反应通道",
    }

    open_values = {item["id"]: 0 for item in dependency["inputs"]}
    open_values["event-extract-btn"] = 1
    open_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="event-extract-btn.n_clicks",
            input_values=open_values,
            state_values={"page-store": event_context},
            output_id="page-species",
        ),
    )

    assert open_response.status_code == 200
    opened = open_response.get_json()["response"]
    trajectory_context = {
        **event_context,
        "page": "trajectory",
    }
    assert opened["page-store"]["data"] == trajectory_context
    assert opened["page-events"]["className"] == "rs-page"
    assert opened["page-trajectory"]["className"] == "rs-page active"
    assert opened["nav-trajectory"]["className"] == "rs-top-nav-item active"

    back_values = {item["id"]: 0 for item in dependency["inputs"]}
    back_values["trajectory-back-events-btn"] = 1
    back_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="trajectory-back-events-btn.n_clicks",
            input_values=back_values,
            state_values={"page-store": trajectory_context},
            output_id="page-species",
        ),
    )

    assert back_response.status_code == 200
    returned = back_response.get_json()["response"]
    assert returned["page-store"]["data"] == event_context
    assert returned["page-events"]["className"] == "rs-page active"
    assert returned["page-trajectory"]["className"] == "rs-page"
    assert returned["nav-events"]["className"] == "rs-top-nav-item active"


def test_selected_intermediate_can_become_pathway_start() -> None:
    app = create_app()
    client = app.server.test_client()
    input_ids = [
        "species-to-pathway-btn",
        "rxn-to-pathway-btn",
        "inter-to-pathway-btn",
    ]
    payload = _callback_payload(
        client,
        input_ids=input_ids,
        changed="inter-to-pathway-btn.n_clicks",
        input_values={
            "species-to-pathway-btn": 0,
            "rxn-to-pathway-btn": 0,
            "inter-to-pathway-btn": 1,
        },
        state_values={
            "species-grid": [],
            "rxn-grid": [],
            "inter-grid.selected_rows": [0],
            "inter-grid.data": [{"formula": "HO", "smiles": "[H][O]"}],
            "app-store": {},
        },
        output_id="pathway-start-smiles",
    )

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["pathway-start-smiles"]["value"] == "[H][O]"


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
                        "traversal_direction": "downstream",
                        "reactants": ["[H]", "[O]"],
                        "products": ["[H][O]"],
                        "focal_input": "[H]",
                        "focal_output": "[H][O]",
                        "forward_tp": 7,
                        "reverse_tp": 2,
                        "net_tp": 5,
                        "net_share": 0.625,
                        "directionality": 5 / 9,
                        "event_coverage": 0.8,
                        "time_coverage": 0.4,
                        "event_total": 5,
                        "matched_event_total": 4,
                        "distinct_intervals": 3,
                        "score": 0.6,
                        "evidence_status": "evidence_linked",
                        "score_version": "candidate-path/v1",
                        "source_references": ["/cache/events.sqlite3"],
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


def _event_path_dash_payload() -> dict[str, Any]:
    chemistry_keys = ["A->B", "B->C", "C->D"]
    hydrogen_keys = [
        "[H]+[H]->[H][H]",
        "[H][H]->[H]+[H]",
        "[H]+[H]->[H][H]",
    ]
    return {
        "schema_version": "event-path/v1",
        "query": {"path_length": 3},
        "summary": {
            "replicate_count": 2,
            "actual_path_occurrence_count": 3,
            "actual_path_signature_count": 2,
            "independent_atom_lineage_support_count": 6,
            "statistics_complete": True,
            "traversal_truncated": False,
        },
        "sources": [
            {"replicate": "rep1", "event_node_count": 5},
            {"replicate": "rep2", "event_node_count": 6},
        ],
        "paths": [
            {
                "signature_id": "sig-chemistry",
                "reaction_keys": chemistry_keys,
                "occurrence_count": 2,
                "independent_atom_lineage_support_count": 4,
                "independent_lineage_set_support_count": 2,
                "replicate_support_count": 2,
                "replicate_reproduction_rate": 1.0,
                "interval_gap_by_edge": [
                    {"count": 2, "min": 1, "median": 1, "mean": 1, "max": 1},
                    {"count": 2, "min": 1, "median": 1, "mean": 1, "max": 1},
                ],
                "idle_timestep_gap_by_edge": [
                    {"count": 2, "min": 0, "median": 0, "mean": 0, "max": 0},
                    {"count": 2, "min": 0, "median": 0, "mean": 0, "max": 0},
                ],
                "anchor_timestep_gap_by_edge": [
                    {"count": 2, "min": 10, "median": 10, "mean": 10, "max": 10},
                    {"count": 2, "min": 10, "median": 10, "mean": 10, "max": 10},
                ],
                "anchor_timestep_span": {"median": 20},
                "support_is_lower_bound": False,
            },
            {
                "signature_id": "sig-hydrogen",
                "reaction_keys": hydrogen_keys,
                "occurrence_count": 1,
                "independent_atom_lineage_support_count": 2,
                "independent_lineage_set_support_count": 1,
                "replicate_support_count": 1,
                "replicate_reproduction_rate": 0.5,
                "anchor_timestep_span": {"median": 20},
                "support_is_lower_bound": False,
            },
        ],
        "occurrences": [
            {
                "path_id": "path-1",
                "replicate": "rep1",
                "event_ids": ["event-1", "event-2", "event-3"],
                "reaction_keys": chemistry_keys,
                "lineage_atom_ids": [7],
                "lineage_atom_support_count": 1,
                "events": [
                    {
                        "event_id": "event-1",
                        "timestep_index": 0,
                        "before_timestep": 0,
                        "after_timestep": 10,
                        "reaction_smiles": "A -> B",
                        "atom_ids": [7],
                    },
                    {
                        "event_id": "event-2",
                        "timestep_index": 1,
                        "before_timestep": 10,
                        "after_timestep": 20,
                        "reaction_smiles": "B -> C",
                        "atom_ids": [7],
                    },
                    {
                        "event_id": "event-3",
                        "timestep_index": 2,
                        "before_timestep": 20,
                        "after_timestep": 30,
                        "reaction_smiles": "C -> D",
                        "atom_ids": [7],
                    },
                ],
                "edges": [
                    {
                        "from_event_id": "event-1",
                        "to_event_id": "event-2",
                        "molecule_instances": [{"species": "B", "atom_ids": [7]}],
                        "carrier_atom_ids": [7],
                        "interval_gap": 1,
                        "idle_timestep_gap": 0,
                        "anchor_timestep_gap": 10,
                    },
                    {
                        "from_event_id": "event-2",
                        "to_event_id": "event-3",
                        "molecule_instances": [{"species": "C", "atom_ids": [7]}],
                        "carrier_atom_ids": [7],
                        "interval_gap": 1,
                        "idle_timestep_gap": 0,
                        "anchor_timestep_gap": 10,
                    },
                ],
            }
        ],
        "occurrence_details_truncated": False,
        "comparison": {
            "comparison_available": True,
            "comparison_complete": True,
            "aggregate_reachable_pair_count": 10,
            "confirmed_pair_count": 1,
            "aggregate_only_pair_count": 9,
            "actual_only_pair_count": 0,
            "realization_rate": 0.1,
            "per_replicate": [
                {
                    "replicate": "rep1",
                    "aggregate_reachable_path_count": 10,
                    "actual_path_signature_count": 1,
                    "confirmed_actual_path_count": 1,
                    "aggregate_only_path_count": 9,
                    "actual_only_path_count": 0,
                    "realization_rate": 0.1,
                    "comparison_complete": True,
                    "confirmed": [
                        {"signature_id": "sig-chemistry", "reaction_keys": chemistry_keys}
                    ],
                    "aggregate_only": [],
                    "actual_only": [],
                }
            ],
        },
    }


def test_event_path_dash_layout_exposes_analysis_and_audit_controls() -> None:
    app = create_app()
    layout = app.server.test_client().get("/_dash-layout").get_json()
    ids = _layout_string_ids(layout)

    for component_id in {
        "pathway-concept-guide",
        "pathway-concept-aggregate",
        "pathway-concept-actual",
        "pathway-analysis-tabs",
        "event-path-wizard-step",
        "event-path-progress-1",
        "event-path-step-1",
        "event-path-step1-next",
        "event-path-step-2",
        "event-path-step2-next",
        "event-path-step-3",
        "event-path-run-btn",
        "event-path-additional-sources",
        "event-path-signature-grid",
        "event-path-comparison-chart",
        "event-path-comparison-grid",
        "event-path-summary-explanation",
        "event-path-occurrence-selector",
        "event-path-time-grid",
        "event-path-cytoscape",
        "event-path-event-grid",
        "event-path-edge-grid",
        "event-path-store",
    }:
        assert component_id in ids


def test_event_path_wizard_auto_detects_current_data_and_advances(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "validate_event_path_sources_for_dash",
        lambda *_args, **_kwargs: {
            "replicate_count": 1,
            "total_event_count": 3406,
            "sources": [],
        },
    )
    app = create_app()
    client = app.server.test_client()
    app_store = {
        "base": "/data/rp3.lammpstrj",
        "label": "rp3.lammpstrj",
        "artifacts": {
            "reactionevent": "/data/rp3.lammpstrj.reactionevent.csv",
            "molecules": "/data/rp3.lammpstrj.molecules.csv",
        },
    }
    source_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store"],
            changed="app-store.data",
            input_values={"app-store": app_store},
            state_values={},
            output_id="event-path-current-replicate",
        ),
    )
    assert source_response.status_code == 200
    source_body = source_response.get_json()["response"]
    assert source_body["event-path-current-replicate"]["value"] == "rp3"
    assert source_body["event-path-index-status"]["children"] == (
        "事件索引已就绪 · 3,406 个事件"
    )

    input_ids = [
        "event-path-step1-next",
        "event-path-step2-back",
        "event-path-step2-next",
        "event-path-step3-back",
        "event-path-step4-edit",
        "event-path-store",
        "app-store",
    ]
    advance_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="event-path-step1-next.n_clicks",
            input_values={
                "event-path-step1-next": 1,
                "event-path-step2-back": 0,
                "event-path-step2-next": 0,
                "event-path-step3-back": 0,
                "event-path-step4-edit": 0,
                "event-path-store": None,
                "app-store": app_store,
            },
            state_values={
                "event-path-wizard-step": 1,
                "event-path-current-replicate": "rp3",
                "event-path-source-mode": "current",
                "event-path-additional-sources": "",
                "event-path-length": 3,
            },
            output_id="event-path-wizard-step",
        ),
    )
    assert advance_response.status_code == 200
    advance_body = advance_response.get_json()["response"]
    assert advance_body["event-path-wizard-step"]["data"] == 2
    assert "数据检查通过" in str(
        advance_body["event-path-wizard-feedback"]["children"]
    )

    render_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-path-wizard-step"],
            changed="event-path-wizard-step.data",
            input_values={"event-path-wizard-step": 2},
            state_values={},
            output_id="event-path-step-1",
        ),
    )
    assert render_response.status_code == 200
    render_body = render_response.get_json()["response"]
    assert render_body["event-path-step-1"]["style"] == {"display": "none"}
    assert render_body["event-path-step-2"]["style"] == {}
    assert "is-active" in render_body["event-path-progress-2"]["className"]


def test_event_path_tab_reports_its_own_data_requirements() -> None:
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["page-store", "app-store", "pathway-analysis-tabs"],
            changed="pathway-analysis-tabs.active_tab",
            input_values={
                "page-store": {"page": "pathway"},
                "app-store": {
                    "artifacts": {
                        "reactionevent": "/data/run.reactionevent.csv",
                        "molecules": "/data/run.molecules.csv",
                    }
                },
                "pathway-analysis-tabs": "concrete-event-paths",
            },
            state_values={},
            output_id="page-data-status",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-data-status"]["children"] == "事件轨迹证据已就绪"
    assert body["page-data-status"]["className"] == "rs-page-status is-ready"


def test_event_path_dash_analysis_filters_and_renders_concrete_occurrence(
    monkeypatch,
) -> None:
    report = _event_path_dash_payload()
    captured = {}

    def fake_analyze(artifacts, **query):
        captured["artifacts"] = artifacts
        captured["query"] = query
        return report

    monkeypatch.setattr(svc, "analyze_event_paths_for_dash", fake_analyze)
    app = create_app()
    client = app.server.test_client()
    run_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-path-run-btn"],
            changed="event-path-run-btn.n_clicks",
            input_values={"event-path-run-btn": 1},
            state_values={
                "event-path-current-replicate": "rep1",
                "event-path-source-mode": "multiple",
                "event-path-additional-sources": "rep2=/data/rep2/run.lammpstrj",
                "event-path-length": 3,
                "event-path-start-smiles": "A",
                "event-path-max-interval-gap": 2,
                "event-path-max-timestep-gap": 100,
                "event-path-max-details": 50,
                "app-store": {
                    "artifacts": {
                        "reactionevent": "/data/rep1/run.lammpstrj.reactionevent.csv",
                        "molecules": "/data/rep1/run.lammpstrj.molecules.csv",
                    }
                },
            },
            output_id="event-path-alert",
        ),
    )

    assert run_response.status_code == 200
    assert captured["query"] == {
        "current_replicate": "rep1",
        "additional_sources": "rep2=/data/rep2/run.lammpstrj",
        "path_length": 3,
        "start_smiles": "A",
        "max_interval_gap": 2,
        "max_timestep_gap": 100,
        "max_occurrence_details": 50,
    }
    run_body = run_response.get_json()["response"]
    assert run_body["event-path-store"]["data"] == report
    assert run_body["event-path-comparison-grid"]["data"][0]["confirmed"] == 1
    reading_guide = str(
        run_body["event-path-summary-explanation"]["children"]
    )
    assert "10.00%" in reading_guide
    assert "不是产率、转化率或事件占比" in reading_guide

    filter_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "event-path-store",
                "event-path-filter-flags",
                "event-path-min-reproduction",
                "event-path-min-lineages",
            ],
            changed="event-path-store.data",
            input_values={
                "event-path-store": report,
                "event-path-filter-flags": ["hide_pure_h"],
                "event-path-min-reproduction": 0,
                "event-path-min-lineages": 1,
            },
            state_values={},
            output_id="event-path-signature-grid",
        ),
    )
    assert filter_response.status_code == 200
    filter_body = filter_response.get_json()["response"]
    rows = filter_body["event-path-signature-grid"]["data"]
    assert [row["signature_id"] for row in rows] == ["sig-chemistry"]

    select_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-path-signature-grid"],
            changed="event-path-signature-grid.selected_rows",
            input_values={"event-path-signature-grid": [0]},
            state_values={
                "event-path-signature-grid.data": rows,
                "event-path-store": report,
            },
            output_id="event-path-occurrence-selector",
        ),
    )
    assert select_response.status_code == 200
    select_body = select_response.get_json()["response"]
    assert select_body["event-path-occurrence-selector"]["value"] == "path-1"
    assert select_body["event-path-time-grid"]["data"][0]["anchor_median"] == 10

    occurrence_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-path-occurrence-selector"],
            changed="event-path-occurrence-selector.value",
            input_values={"event-path-occurrence-selector": "path-1"},
            state_values={"event-path-store": report},
            output_id="event-path-cytoscape",
        ),
    )
    assert occurrence_response.status_code == 200
    occurrence_body = occurrence_response.get_json()["response"]
    elements = occurrence_body["event-path-cytoscape"]["elements"]
    assert [item["data"]["id"] for item in elements[:3]] == [
        "event-1",
        "event-2",
        "event-3",
    ]
    assert occurrence_body["event-path-edge-grid"]["data"][0][
        "carrier_atom_ids"
    ] == "7"


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
        "app-store": {
            "dataset_id": "dataset-A",
            "artifacts": {"reaction": "/tmp/run.reactionabcd"},
        },
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
        "max_expansions": 5000,
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
    assert row["evidence_badge"] == "各步可查事件（未证整链）"
    assert result["pathway-store"]["data"] == _pathway_payload()
    assert result["pathway-context-store"]["data"] == {
        "schema_version": "reacnet-scope/pathway-context/v1",
        "dataset_id": "dataset-A",
        "source_signatures": {},
    }


def test_pathway_search_passes_small_fragment_carbon_goal(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_find(_artifacts, _start_smiles, **limits):
        captured.update(limits)
        return _pathway_payload()

    monkeypatch.setattr(svc, "find_pathways", fake_find)
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["pathway-search-btn"],
            changed="pathway-search-btn.n_clicks",
            input_values={"pathway-search-btn": 1},
            state_values={
                "pathway-start-smiles": "[C][C][C][C][C][C]",
                "pathway-direction": "downstream",
                "pathway-max-depth": 6,
                "pathway-max-branches": 10,
                "pathway-max-paths": 20,
                "pathway-max-expansions": 300,
                "pathway-min-net-tp": 1,
                "pathway-min-directionality": 0,
                "pathway-goal": "small_fragments",
                "pathway-target-max-carbon": 3,
                "app-store": {
                    "dataset_id": "dataset-A",
                    "artifacts": {"reaction": "/tmp/run.reactionabcd"},
                },
            },
        ),
    )

    assert response.status_code == 200
    assert captured["target_max_carbon"] == 3
    assert captured["max_expansions"] == 300
    assert captured["evidence_mode"] == "network_only"


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
            input_ids=[
                "species-to-pathway-btn",
                "rxn-to-pathway-btn",
                "inter-to-pathway-btn",
            ],
            changed="species-to-pathway-btn.n_clicks",
            input_values={
                "species-to-pathway-btn": 1,
                "rxn-to-pathway-btn": None,
                "inter-to-pathway-btn": None,
            },
            state_values={
                "species-grid.selected_rows": [0],
                "species-grid.data": [{"smiles": "[C@@H](O)[Cl]"}],
                "rxn-grid.selected_rows": [],
                "rxn-grid.data": [],
                "inter-grid.selected_rows": [],
                "inter-grid.data": [],
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
            input_ids=[
                "species-to-pathway-btn",
                "rxn-to-pathway-btn",
                "inter-to-pathway-btn",
            ],
            changed="rxn-to-pathway-btn.n_clicks",
            input_values={
                "species-to-pathway-btn": None,
                "rxn-to-pathway-btn": 1,
                "inter-to-pathway-btn": None,
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
                "inter-grid.selected_rows": [],
                "inter-grid.data": [],
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
    assert json.loads(json_download["content"]) == cli._pathway_document(payload)

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
    with io.StringIO(csv_download["content"]) as handle:
        reader = csv.DictReader(handle)
        dash_rows = list(reader)
        assert reader.fieldnames == cli.PATHWAY_CSV_FIELDS
    expected_rows = [
        {field: "" if value is None else str(value) for field, value in row.items()}
        for row in cli._pathway_csv_rows(payload)
    ]
    assert dash_rows == expected_rows


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
    selected_step = selection_response.get_json()["response"][
        "pathway-selected-step"
    ]["data"]
    assert selected_step["step_index"] == 1
    assert selected_step["reaction_text"] == "[H] + [O] -> [H][O]"
    assert (
        selection_response.get_json()["response"]["pathway-open-events-btn"][
            "disabled"
        ]
        is False
    )
    clear_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "pathway-store",
                "pathway-grid",
                "pathway-cytoscape",
            ],
            changed="pathway-store.data",
            input_values={
                "pathway-store": _pathway_payload(),
                "pathway-grid": [],
                "pathway-cytoscape": None,
            },
            state_values={"pathway-grid": []},
        ),
    )
    assert clear_response.status_code == 200
    cleared = clear_response.get_json()["response"]
    assert cleared["pathway-selected-path"]["data"] is None


def test_selected_single_step_path_queries_time_evidence(monkeypatch) -> None:
    def fake_validate(artifacts, step, *, max_occurrences):
        assert artifacts == {"reactionevent": "/tmp/run.reactionevent.csv"}
        assert step["reaction_text"] == "[H] + [O] -> [H][O]"
        assert max_occurrences == 20
        return {
            "rows": [
                {
                    "occurrence_rank": 1,
                    "evidence_source": "RNG 事件",
                    "event_id": "evt-1",
                    "timestep_index": 10,
                    "reaction_smiles": step["reaction_text"],
                }
            ],
            "evidence_level": "rng_event",
            "message": "找到 1 条精确匹配的 RNG 反应事件。",
        }

    monkeypatch.setattr(
        svc,
        "validate_pathway_step_occurrences",
        fake_validate,
    )
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "pathway-selected-path",
                "pathway-selected-step",
                "app-store",
            ],
            changed="pathway-selected-step.data",
            input_values={
                "pathway-selected-path": {
                    "path_rank": 1,
                    "species_ids": ["[H]", "[H][O]"],
                    "reaction_keys": ["[H]+[O]->[H][O]"],
                },
                "pathway-selected-step": {
                    "step_index": 1,
                    "reaction_text": "[H] + [O] -> [H][O]",
                },
                "app-store": {
                    "artifacts": {
                        "reactionevent": "/tmp/run.reactionevent.csv"
                    },
                },
            },
            state_values={},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["pathway-evidence-grid"]["data"][0]["event_id"] == "evt-1"
    assert "只有 1 步，不存在两步连续性" in result[
        "pathway-evidence-alert"
    ]["children"]
    assert "精确匹配的 RNG 反应事件" in result[
        "pathway-evidence-alert"
    ]["children"]


def test_loading_another_dataset_drops_cross_dataset_species_selection(
    monkeypatch,
) -> None:
    status = {
        "dataset": {"selected_base": "new.lammpstrj"},
    }
    monkeypatch.setattr(svc, "scan_dataset", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(
        svc,
        "artifacts_from_status",
        lambda _status: {"reaction": "/new.reactionabcd"},
    )
    monkeypatch.setattr(svc, "dataset_capabilities", lambda _status: {})
    monkeypatch.setattr(svc, "dataset_readiness", lambda _status: {})
    monkeypatch.setattr(svc, "dataset_ready_count", lambda _status: 0)
    monkeypatch.setattr(svc, "dataset_label", lambda _status: "new")
    monkeypatch.setattr(
        svc,
        "normalise_recent_datasets",
        lambda records: records,
    )
    monkeypatch.setattr(
        "scripts.webapp_dash.callbacks._validated_dataset_target",
        lambda selected: selected,
    )
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["data-apply-btn"],
            changed="data-apply-btn.n_clicks",
            input_values={"data-apply-btn": 1},
            state_values={
                "dataset-browser-candidate": {
                    "folder": "/new",
                    "base": "new.lammpstrj",
                },
                "app-store": {
                    "dataset_id": "old",
                    "selected_smiles": "[OLD]",
                    "selected_formula": "OLD",
                },
                "recent-datasets": [],
            },
        ),
    )
    assert response.status_code == 200
    store = response.get_json()["response"]["app-store"]["data"]
    assert store["selected_smiles"] == ""
    assert store["selected_formula"] == ""


def test_loading_same_basename_from_two_directories_has_distinct_stable_ids(
    tmp_path,
    monkeypatch,
) -> None:
    folders = [tmp_path / "run-a", tmp_path / "run-b"]
    for folder in folders:
        folder.mkdir()
    alias = tmp_path / "run-a-alias"
    alias.symlink_to(folders[0], target_is_directory=True)

    monkeypatch.setattr(
        "scripts.webapp_dash.callbacks._validated_dataset_target",
        lambda selected: selected,
    )
    monkeypatch.setattr(
        svc,
        "scan_dataset",
        lambda folder, *, base: {
            "dataset": {"selected_base": Path(base).name}
        },
    )
    monkeypatch.setattr(
        svc,
        "artifacts_from_status",
        lambda _status: {"reaction": "run.lammpstrj.reactionabcd"},
    )
    monkeypatch.setattr(svc, "dataset_capabilities", lambda _status: {})
    monkeypatch.setattr(svc, "dataset_readiness", lambda _status: {})
    monkeypatch.setattr(svc, "dataset_ready_count", lambda _status: 0)
    monkeypatch.setattr(svc, "dataset_label", lambda _status: "run")
    monkeypatch.setattr(
        svc,
        "normalise_recent_datasets",
        lambda records: records,
    )
    app = create_app()
    client = app.server.test_client()

    ids: list[str] = []
    loaded_stores: list[dict[str, Any]] = []
    store: dict[str, Any] = {}
    for folder in (folders[0], folders[0], folders[1], alias):
        response = client.post(
            "/_dash-update-component",
            json=_callback_payload(
                client,
                input_ids=["data-apply-btn"],
                changed="data-apply-btn.n_clicks",
                input_values={"data-apply-btn": 1},
                state_values={
                    "dataset-browser-candidate": {
                        "folder": str(folder),
                        "base": "run.lammpstrj",
                    },
                    "app-store": store,
                    "recent-datasets": [],
                },
            ),
        )
        assert response.status_code == 200
        store = response.get_json()["response"]["app-store"]["data"]
        loaded_stores.append(store)
        ids.append(store["dataset_id"])
        assert store["selected_smiles"] == ""
        assert store["dataset_id"] == dataset_id_for_source(
            str((folder / "run.lammpstrj").resolve(strict=False))
        )

    assert ids[0] == ids[1]
    assert ids[0] != ids[2]
    assert ids[0] == ids[3]

def test_pathway_reset_trigger_kind_prioritizes_dataset_changes() -> None:
    class FakeContext:
        def __init__(
            self,
            prop_ids: list[str],
            *,
            triggered_id: str | None = None,
        ) -> None:
            self.triggered_prop_ids = {
                prop_id: prop_id.split(".", 1)[0]
                for prop_id in prop_ids
            }
            self.triggered_id = triggered_id

    old_context = {
        "schema_version": "reacnet-scope/pathway-context/v1",
        "dataset_id": "dataset-A",
    }
    assert cb._pathway_reset_trigger_kind(
        FakeContext(["app-store.data"], triggered_id="app-store"),
        {"dataset_id": "dataset-B"},
        old_context,
    ) == "dataset"
    assert cb._pathway_reset_trigger_kind(
        FakeContext(["app-store.data"], triggered_id="app-store"),
        {"dataset_id": "dataset-A", "selected_smiles": "[H]"},
        old_context,
    ) is None
    assert cb._pathway_reset_trigger_kind(
        FakeContext(["app-store.data"], triggered_id="app-store"),
        {},
        old_context,
    ) == "dataset"
    assert cb._pathway_reset_trigger_kind(
        FakeContext([], triggered_id=None),
        {"dataset_id": "dataset-A"},
        old_context,
    ) is None


def test_dataset_change_clears_pathway_selection() -> None:
    app = create_app()
    client = app.server.test_client()
    old_context = {
        "schema_version": "reacnet-scope/pathway-context/v1",
        "dataset_id": "dataset-A",
        "source_signatures": {},
    }

    changed_dataset = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store"],
            changed="app-store.data",
            input_values={"app-store": {"dataset_id": "dataset-B"}},
            state_values={
                "pathway-context-store": old_context,
            },
            output_id="pathway-context-store",
        ),
    )
    assert changed_dataset.status_code == 200
    changed = changed_dataset.get_json()["response"]
    assert changed["pathway-store"]["data"] is None
    assert changed["pathway-context-store"]["data"] is None
    assert changed["pathway-selected-path"]["data"] is None
    assert changed["pathway-selected-step"]["data"] is None
    assert changed["pathway-grid"]["selected_rows"] == []
    assert changed["pathway-grid"]["data"] == []
    assert changed["pathway-cytoscape"]["elements"] == []
    assert changed["pathway-cytoscape"]["tapNodeData"] is None
    assert changed["pathway-open-events-btn"]["disabled"] is True


def test_element_distribution_callback_passes_generic_filters(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(_artifacts, **kwargs):
        captured.update(kwargs)
        return {
            "distribution_rows": [],
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
        if [value["id"] for value in item["inputs"]] == ["element-distribution-search-btn"]
    )
    state_values = {
        "element-distribution-group-element": "N",
        "element-distribution-max-count": 8,
        "element-distribution-include-zero": True,
        "element-distribution-filter-element": "S",
        "element-distribution-filter-mode": "range",
        "element-distribution-filter-min": 1,
        "element-distribution-filter-max": 3,
        "element-distribution-reference-smiles": "[C][C]",
        "element-distribution-timestep": 0.002,
        "app-store": {"artifacts": {"species": "/tmp/example.species"}},
    }
    payload = {
        "output": dependency["output"],
        "outputs": [
            {"id": "element-distribution-alert", "property": "children"},
            {"id": "element-distribution-highlights", "property": "children"},
            {"id": "element-distribution-payload-store", "property": "data"},
            {"id": "element-distribution-composition-trend", "property": "figure"},
        ],
        "changedPropIds": ["element-distribution-search-btn.n_clicks"],
        "inputs": [{"id": "element-distribution-search-btn", "property": "n_clicks", "value": 1}],
        "state": [
            {"id": item["id"], "property": item["property"], "value": state_values[item["id"]]}
            for item in dependency["state"]
        ],
    }

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    assert captured["reference_smiles"] == "[C][C]"
    assert captured["timestep_ps"] == 0.002
    assert captured["group_element"] == "N"
    assert captured["max_group_count"] == 8
    assert captured["include_zero"] is True
    assert captured["element_filters"] == {"S": {"mode": "range", "min": 1, "max": 3}}


def test_element_distribution_index_refresh_only_polls_while_visible_and_building(monkeypatch) -> None:
    calls: list[dict[str, str]] = []
    current_state = {"value": "building"}

    def fake_status(artifacts):
        calls.append(artifacts)
        return {
            "state": current_state["value"],
            "progress": 0.5,
            "timepoints": 3,
            "unique_species": 4,
            "available_elements": ["N", "S"],
        }

    monkeypatch.setattr(svc, "composition_index_status", fake_status)
    app = create_app()
    client = app.server.test_client()
    input_ids = ["app-store", "page-store", "element-distribution-index-refresh"]
    store = {
        "label": "example",
        "artifacts": {"species": "/tmp/example.species"},
    }

    hidden_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="page-store.data",
            input_values={
                "app-store": store,
                "page-store": {"page": "species"},
                "element-distribution-index-refresh": 0,
            },
            state_values={
                "element-distribution-group-element": "C",
                "element-distribution-filter-element": None,
            },
            output_id="element-distribution-index-refresh",
        ),
    )
    assert hidden_response.status_code == 200
    assert hidden_response.get_json()["response"]["element-distribution-index-refresh"]["disabled"] is True
    assert calls == []

    building_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="page-store.data",
            input_values={
                "app-store": store,
                "page-store": {"page": "element-distribution"},
                "element-distribution-index-refresh": 0,
            },
            state_values={
                "element-distribution-group-element": "C",
                "element-distribution-filter-element": None,
            },
            output_id="element-distribution-index-refresh",
        ),
    )
    assert building_response.status_code == 200
    assert building_response.get_json()["response"]["element-distribution-index-refresh"]["disabled"] is False

    current_state["value"] = "ready"
    ready_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="element-distribution-index-refresh.n_intervals",
            input_values={
                "app-store": store,
                "page-store": {"page": "element-distribution"},
                "element-distribution-index-refresh": 1,
            },
            state_values={
                "element-distribution-group-element": "N",
                "element-distribution-filter-element": "S",
            },
            output_id="element-distribution-index-refresh",
        ),
    )
    assert ready_response.status_code == 200
    ready_payload = ready_response.get_json()["response"]
    assert ready_payload["element-distribution-index-refresh"]["disabled"] is True
    assert ready_payload["element-distribution-index-status"]["children"].startswith("元素分布索引已就绪")
    assert ready_payload["element-distribution-group-element"]["value"] == "N"
    assert ready_payload["element-distribution-filter-element"]["value"] == "S"
    assert len(calls) == 2


def test_structure_endpoint_honors_selected_channel_preview_dimensions(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_render(smiles, *, width, height, show_h):
        captured.update(
            {
                "smiles": smiles,
                "width": width,
                "height": height,
                "show_h": show_h,
            }
        )
        return {"ok": True, "svg": "<svg></svg>", "message": ""}

    monkeypatch.setattr(svc, "render_species_svg", fake_render)
    client = create_app().server.test_client()
    response = client.get(
        "/api/structure.svg?smiles=%5BH%5D&width=180&height=116"
    )

    assert response.status_code == 200
    assert captured == {
        "smiles": "[H]",
        "width": 180,
        "height": 116,
        "show_h": True,
    }


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
                    item["id"],
                    values.get(
                        item["id"],
                        {} if item["id"] == "app-store" else [],
                    ),
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
        and any(value["id"] == "data-pick-btn" for value in item["inputs"])
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
        {"id": "recent-datasets", "property": "data"},
        {"id": "dataset-browser-candidate", "property": "data"},
        {"id": "data-load-feedback", "property": "children"},
        {"id": "data-overview-view", "property": "className"},
        {"id": "data-browser-view", "property": "className"},
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
                "value": candidate if item["id"] == "dataset-browser-candidate" else store,
            }
            for item in dependency["inputs"]
        ],
        "state": [],
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
        if "data-prep-basic-status.children" in item["output"]
    )
    input_values = {
        "page-store": {"page": "data-management"},
        "data-prep-refresh-btn": 1,
        "data-prep-refresh": 0,
        "dataset-browser-candidate": candidate,
        "data-prep-cancel-result": None,
    }
    return {
        "output": dependency["output"],
        "outputs": [
            {"id": "data-prep-basic-status", "property": "children"},
            {"id": "data-prep-event-status", "property": "children"},
            {"id": "data-prep-trajectory-status", "property": "children"},
            {"id": "data-prep-composition-status", "property": "children"},
            {"id": "data-prep-cache-meta", "property": "children"},
            {"id": "data-prep-status-alert", "property": "children"},
            {"id": "data-next-action", "property": "children"},
            {"id": "topbar-index-status", "property": "children"},
            {"id": "topbar-index-status", "property": "className"},
            {"id": "data-prep-refresh-label", "property": "children"},
            {"id": "data-prep-event-command", "property": "children"},
            {"id": "data-prep-trajectory-command", "property": "children"},
            {"id": "data-prep-composition-command", "property": "children"},
            {"id": "data-prep-event-copy", "property": "content"},
            {"id": "data-prep-trajectory-copy", "property": "content"},
            {"id": "data-prep-composition-copy", "property": "content"},
            {"id": "data-clear-event-btn", "property": "disabled"},
            {"id": "data-clear-trajectory-btn", "property": "disabled"},
            {"id": "data-clear-composition-btn", "property": "disabled"},
            {"id": "data-prep-refresh", "property": "disabled"},
            {"id": "data-prep-event-btn", "property": "className"},
            {"id": "data-prep-trajectory-btn", "property": "className"},
            {"id": "data-prep-composition-btn", "property": "className"},
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
    kind: str = "trajectory",
) -> dict[str, Any]:
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if any(value["id"] == "data-clear-trajectory-btn" for value in item["inputs"])
    )
    trigger_id = f"data-clear-{kind}-btn"
    input_values = {
        "data-clear-event-btn": 1 if kind == "event" else None,
        "data-clear-trajectory-btn": 1 if kind == "trajectory" else None,
        "data-clear-composition-btn": 1 if kind == "composition" else None,
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
        "changedPropIds": [f"{trigger_id}.n_clicks"],
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
            "dataset_id": "dataset-public-id",
            "workspace_path": "/workspace/dataset-public-id",
            "workspace_resolved": True,
            "workspace_writable": True,
            "index_bytes": 2048,
            "event_command": "reacnet-scope prepare event",
            "trajectory_command": "reacnet-scope prepare trajectory",
            "composition_command": "reacnet-scope prepare element-distribution",
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
    result = response.get_json()["response"]
    assert result["data-clear-trajectory-btn"]["disabled"] is False
    workspace_meta = json.dumps(
        result["data-prep-cache-meta"]["children"],
        ensure_ascii=False,
    )
    assert "dataset-public-id" in workspace_meta
    assert "/workspace/dataset-public-id" in workspace_meta
    assert "2.0 KiB" in workspace_meta
    for component_id, command in (
        ("data-prep-event-command", "reacnet-scope prepare event"),
        ("data-prep-trajectory-command", "reacnet-scope prepare trajectory"),
        (
            "data-prep-composition-command",
            "reacnet-scope prepare element-distribution",
        ),
        ("data-prep-event-copy", "reacnet-scope prepare event"),
        ("data-prep-trajectory-copy", "reacnet-scope prepare trajectory"),
        (
            "data-prep-composition-copy",
            "reacnet-scope prepare element-distribution",
        ),
    ):
        property_name = "content" if component_id.endswith("-copy") else "children"
        assert result[component_id][property_name] == command


def test_preparation_refresh_uses_local_sidecar_workspace_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    candidate = _discovered_candidate(tmp_path)
    app = create_app()
    client = app.server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_preparation_status_callback_payload(
            client,
            candidate=candidate,
            store={},
        ),
    )

    assert response.status_code == 200
    workspace_meta = json.dumps(
        response.get_json()["response"]["data-prep-cache-meta"]["children"],
        ensure_ascii=False,
    )
    assert str(tmp_path / ".reacnet-scope") in workspace_meta
    assert "未配置" not in workspace_meta


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


def test_clear_confirmation_maps_each_visible_index_button(
    tmp_path, monkeypatch
) -> None:
    candidate = _discovered_candidate(tmp_path)

    def fake_preparation(folder: str, *, base: str = "") -> dict[str, Any]:
        assert (folder, base) == (candidate["folder"], candidate["base"])
        return {
            "events": {"state": "ready", "index_size": 10},
            "trajectory": {"state": "ready", "index_size": 20},
            "composition": {"state": "ready", "index_size": 30},
        }

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(svc, "dataset_preparation_status", fake_preparation)
    app = create_app()
    client = app.server.test_client()

    for kind in ("event", "trajectory", "composition"):
        response = client.post(
            "/_dash-update-component",
            json=_clear_confirmation_callback_payload(
                client,
                candidate=candidate,
                store={},
                kind=kind,
            ),
        )
        assert response.status_code == 200
        request = response.get_json()["response"]["data-clear-kind-store"]["data"]
        assert request == {
            "kind": kind,
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


def test_load_selected_dataset_updates_store_returns_to_overview_and_remembers_it(
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
    assert result["data-overview-view"]["className"] == "rs-data-view"
    assert "d-none" in result["data-browser-view"]["className"]
    assert result["dataset-browser-candidate"]["data"] is None
    assert result["topbar-folder"]["children"] == candidate["label"]
    assert str(tmp_path) not in result["topbar-folder"]["children"]
    assert "当前数据集已切换为" in json.dumps(
        result["data-load-feedback"]["children"], ensure_ascii=False
    )
    assert result["recent-datasets"]["data"][0]["folder"] == str(tmp_path)
    assert result["recent-datasets"]["data"][0]["base"] == candidate["base"]


def test_load_failure_keeps_current_dataset_browser_and_recents(monkeypatch) -> None:
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
    assert "data-overview-view" not in result
    assert "data-browser-view" not in result
    assert result["recent-datasets"]["data"] == old_recent
    assert result["dataset-browser-candidate"]["data"] is None
    assert "验证失败" in json.dumps(result["data-load-feedback"]["children"], ensure_ascii=False)


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
    assert "Current Dataset" in json.dumps(result["data-load-feedback"]["children"], ensure_ascii=False)

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
    assert result["data-overview-view"]["className"] == "rs-data-view"
    assert "d-none" in result["data-browser-view"]["className"]


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
    assert "data-overview-view" not in result
    assert "data-browser-view" not in result
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
            changed="dir-browser-path-input.n_submit",
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

    entry = {"type": "dir-browser-entry", "name": "case"}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(entry, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"name":["ALL"],"type":"dir-browser-entry"}': [1]},
        state_values=state,
    )
    for item in payload["inputs"]:
        if item["id"] == '{"name":["ALL"],"type":"dir-browser-entry"}':
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

    recent = {"type": "dir-browser-recent-entry", "index": 0}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(recent, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"index":["ALL"],"type":"dir-browser-recent-entry"}': [1]},
        state_values=state,
    )
    for item in payload["inputs"]:
        if item["id"] == '{"index":["ALL"],"type":"dir-browser-recent-entry"}':
            item["id"] = recent
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dataset-browser-candidate"]["data"] == candidate


def test_missing_recent_candidate_preserves_browser_draft(tmp_path, monkeypatch) -> None:
    current_folder = tmp_path / "current"
    recent_folder = tmp_path / "recent"
    current_folder.mkdir()
    recent_folder.mkdir()
    current = _discovered_candidate(current_folder, "current.lammpstrj")
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    client = create_app().server.test_client()
    recent = {
        "folder": str(recent_folder),
        "base": str(recent_folder / "vanished.lammpstrj"),
        "label": "vanished.lammpstrj",
        "loaded_at": 1,
    }
    recent_id = {"type": "dir-browser-recent-entry", "index": 0}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(recent_id, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"index":["ALL"],"type":"dir-browser-recent-entry"}': [1]},
        state_values={
            "dir-browser-path": str(current_folder),
            "data-folder-input": "",
            "recent-datasets": [recent],
            "dataset-browser-candidate": current,
            "app-store": {},
        },
    )
    for item in payload["inputs"]:
        if item["id"] == '{"index":["ALL"],"type":"dir-browser-recent-entry"}':
            item["id"] = recent_id

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-path"]["data"] == str(current_folder)
    assert result["dataset-browser-candidate"]["data"] == current
    rendered = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    assert "最近记录已失效" in rendered


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
            changed="dir-browser-path-input.n_submit",
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

    selected = {"type": "dir-browser-dataset", "name": "rp4.lammpstrj"}
    card_payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(selected, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"name":["ALL"],"type":"dir-browser-dataset"}': [1]},
        state_values={**state, "dir-browser-path": str(tmp_path)},
    )
    for item in card_payload["inputs"]:
        if item["id"] == '{"name":["ALL"],"type":"dir-browser-dataset"}':
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
            changed="dir-browser-filter-input.value",
            values={"dir-browser-filter-input": ""},
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
    selected = {"type": "dir-browser-dataset", "name": candidate["label"]}
    payload = _browser_callback_payload(
        client,
        changed=f"{json.dumps(selected, sort_keys=True, separators=(',', ':'))}.n_clicks",
        values={'{"name":["ALL"],"type":"dir-browser-dataset"}': [1]},
        state_values={
            "dir-browser-path": str(tmp_path),
            "data-folder-input": "",
            "recent-datasets": [],
            "dataset-browser-candidate": None,
        },
    )
    for item in payload["inputs"]:
        if item["id"] == '{"name":["ALL"],"type":"dir-browser-dataset"}':
            item["id"] = selected

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    assert browse_calls == [str(tmp_path)]
    assert response.get_json()["response"]["dataset-browser-candidate"]["data"] == candidate


def test_expert_path_navigates_only_on_submit_or_go() -> None:
    client = create_app().server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "dir-browser-path.data" in item["output"]
        and any(value["id"] == "data-pick-btn" for value in item["inputs"])
    )

    path_inputs = [
        item["property"]
        for item in dependency["inputs"]
        if item["id"] == "dir-browser-path-input"
    ]
    path_states = [
        item["property"]
        for item in dependency["state"]
        if item["id"] == "dir-browser-path-input"
    ]

    assert path_inputs == ["n_submit"]
    assert path_states == ["value"]
    assert any(
        item["id"] == "dir-browser-go-btn" and item["property"] == "n_clicks"
        for item in dependency["inputs"]
    )


def test_browser_restores_current_dataset_candidate_in_ambiguous_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    for name in ("alpha.lammpstrj", "beta.lammpstrj"):
        (tmp_path / f"{name}.reactionabcd").touch()
    current = {
        "folder": str(tmp_path),
        "base": str(tmp_path / "beta.lammpstrj"),
        "label": "beta.lammpstrj",
    }
    client = create_app().server.test_client()

    response = client.post(
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
                "app-store": current,
            },
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dataset-browser-candidate"]["data"] == current
    rendered = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    assert '"role": "radio"' in rendered
    assert '"aria-checked": "true"' in rendered
    assert "文件完整度" not in rendered
    assert "反应检索" in rendered


def test_invalid_expert_path_preserves_browser_candidate_and_hides_attempted_path(
    tmp_path, monkeypatch
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "private-server-path"
    allowed.mkdir()
    outside.mkdir()
    candidate = _discovered_candidate(allowed)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [allowed])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [allowed])
    client = create_app().server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-go-btn.n_clicks",
            values={
                "dir-browser-go-btn": 1,
                "dir-browser-path-input": str(outside),
            },
            state_values={
                "dir-browser-path": str(allowed),
                "data-folder-input": "",
                "dataset-browser-candidate": candidate,
                "recent-datasets": [],
                "app-store": {},
            },
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["dir-browser-path"]["data"] == str(allowed)
    assert result["dataset-browser-candidate"]["data"] == candidate
    rendered = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    assert "不在允许根目录内" in rendered
    assert str(outside) not in rendered
    assert "Current Dataset" in rendered


def test_large_browser_rendering_is_bounded_counted_and_filterable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    for index in range(150):
        (tmp_path / f"folder-{index:03d}").mkdir()
    for index in range(200):
        (tmp_path / f"candidate-{index:03d}.lammpstrj.reactionabcd").touch()
    client = create_app().server.test_client()
    state = {
        "dir-browser-path": str(tmp_path),
        "data-folder-input": str(tmp_path),
        "dataset-browser-candidate": None,
        "recent-datasets": [],
        "app-store": {},
    }

    opened = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="data-pick-btn.n_clicks",
            values={"data-pick-btn": 1},
            state_values=state,
        ),
    )
    assert opened.status_code == 200
    result = opened.get_json()["response"]
    candidates = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    directories = json.dumps(result["dir-browser-body"]["children"], ensure_ascii=False)
    assert candidates.count("rs-browser-candidate-row") == 100
    assert directories.count("rs-browser-directory-entry") == 100
    assert "显示 100 / 共 200" in candidates
    assert "显示 100 / 共 150" in directories

    filtered = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-filter-input.value",
            values={"dir-browser-filter-input": "candidate-199"},
            state_values=state,
        ),
    )
    assert filtered.status_code == 200
    result = filtered.get_json()["response"]
    candidates = json.dumps(result["dir-browser-current"]["children"], ensure_ascii=False)
    directories = json.dumps(result["dir-browser-body"]["children"], ensure_ascii=False)
    assert candidates.count("rs-browser-candidate-row") == 1
    assert "显示 1 / 匹配 1 / 共 200" in candidates
    assert "没有子目录匹配当前筛选" in directories


def test_filter_keeps_one_visible_candidate_in_radio_tab_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    alpha = _discovered_candidate(tmp_path, "alpha.lammpstrj")
    _discovered_candidate(tmp_path, "beta.lammpstrj")
    client = create_app().server.test_client()

    response = client.post(
        "/_dash-update-component",
        json=_browser_callback_payload(
            client,
            changed="dir-browser-filter-input.value",
            values={"dir-browser-filter-input": "beta"},
            state_values={
                "dir-browser-path": str(tmp_path),
                "data-folder-input": "",
                "dataset-browser-candidate": alpha,
                "recent-datasets": [],
                "app-store": {},
            },
        ),
    )

    assert response.status_code == 200
    rendered = json.dumps(
        response.get_json()["response"]["dir-browser-current"]["children"],
        ensure_ascii=False,
    )
    assert "beta.lammpstrj" in rendered
    assert '"tabIndex": 0' in rendered


def test_browser_keyboard_model_asset_supports_radio_arrow_navigation() -> None:
    asset = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "dataset_browser.js"
    ).read_text(encoding="utf-8")

    assert '[role="radiogroup"]' in asset
    assert '[role="radio"]' in asset
    for key in ("ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"):
        assert key in asset


def test_unavailable_recent_dataset_is_distinct_and_removable() -> None:
    rendered = cb._render_recent_datasets(
        [
            {
                "folder": "/missing",
                "base": "/missing/run.lammpstrj",
                "label": "old run",
                "loaded_at": 1,
            }
        ]
    )
    payload = json.dumps(
        rendered,
        default=lambda value: value.to_plotly_json(),
        ensure_ascii=False,
    )

    assert "old run（不可用）" in payload
    assert '"disabled": true' in payload
    assert "dir-browser-recent-remove" in payload
    assert "从最近数据集中移除 old run" in payload


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
    assert 'body:has([data-dash-is-loading="true"]) .rs-global-operation-progress' in css
    assert ".rs-analysis-progress.is-running::after" in css


def test_event_viewer_assets_are_vendored_and_offline_ready() -> None:
    assets = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
    )
    library = assets / "3Dmol-min.js"
    license_file = assets / "3Dmol-min.js.LICENSE.txt"
    integration = assets / "event_viewer.js"

    assert library.stat().st_size > 500_000
    assert "3dmol v2.5.5" in license_file.read_text(encoding="utf-8")
    integration_text = integration.read_text(encoding="utf-8")
    assert "renderEventTrajectory" in integration_text
    assert "assignBonds" not in integration_text
    assert "display_${axis}" in integration_text
    assert "setHoverable" in integration_text
    assert "setClickable" in integration_text
    assert "renderAtomInspector" in integration_text
    assert "addCoreHalo" in integration_text
    assert "event-core-atom-list" in integration_text

    client = create_app().server.test_client()
    library_response = client.get("/assets/3Dmol-min.js")
    integration_response = client.get("/assets/event_viewer.js")
    assert library_response.status_code == 200
    assert len(library_response.data) == library.stat().st_size
    assert integration_response.status_code == 200
    assert b"renderEventTrajectory" in integration_response.data


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
    table_row = result["event-grid"]["data"][0]
    raw_row = result["event-grid-store"]["data"]["rows"][0]
    assert table_row["atom_ids"] == "1,2"
    assert table_row["id"] == raw_row["event_id"]
    assert "atom_id_list" not in table_row
    assert "reactant_participants" not in table_row
    assert all(
        isinstance(value, (str, int, float, bool)) or value is None
        for value in table_row.values()
    )
    assert raw_row["atom_id_list"] == [1, 2]
    assert result["event-grid-store"]["data"]["kind"] == "rng_event"

    selection_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-grid"],
            changed="event-grid.selected_row_ids",
            input_values={
                "event-grid.selected_row_ids": [table_row["id"]],
            },
            state_values={
                "event-grid-store": result["event-grid-store"]["data"],
                "app-store": {
                    "artifacts": {"trajectory": "/data/run.lammpstrj"}
                },
            },
            output_id="event-selected-store",
        ),
    )
    assert selection_response.status_code == 200
    selected = selection_response.get_json()["response"]
    assert selected["event-selected-store"]["data"]["row"]["atom_id_list"] == [1, 2]
    assert selected["event-extract-id"]["value"] == table_row["id"]
    assert selected["event-selection-card"]["style"] == {"display": "block"}
    assert selected["event-extract-btn"]["disabled"] is False
    assert selected["event-extract-btn"]["children"] == "打开轨迹查看"


def test_unresolved_event_selection_does_not_open_a_blank_trajectory() -> None:
    client = create_app().server.test_client()
    row = {
        "event_id": "rngevt-unresolved",
        "association_status": "unresolved_hmm_timeline",
        "atom_id_list": [],
        "before_timestep": 10,
        "after_timestep": 20,
        "reaction_smiles": "A -> B",
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-grid"],
            changed="event-grid.selected_row_ids",
            input_values={
                "event-grid.selected_row_ids": [row["event_id"]],
            },
            state_values={
                "event-grid-store": {
                    "rows": [row],
                    "kind": "rng_event",
                    "config": {},
                },
                "app-store": {
                    "artifacts": {"trajectory": "/data/run.lammpstrj"}
                },
            },
            output_id="event-selected-store",
        ),
    )

    assert response.status_code == 200
    selected = response.get_json()["response"]
    assert selected["event-extract-btn"]["disabled"] is True
    assert selected["event-extract-btn"]["children"] == "该事件无法定位原子"
    assert "轨迹不可用" in str(
        selected["event-selected-summary"]["children"]
    )


def test_trajectory_refresh_reextracts_the_selected_event(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(artifacts, row, **kwargs):
        captured.update(
            {
                "artifacts": artifacts,
                "row": row,
                **kwargs,
            }
        )
        return {
            "event_id": row["event_id"],
            "frames": [
                {
                    "frame": 20,
                    "atoms": [],
                    "bonds": [],
                }
            ],
            "atom_groups": {
                "core": [1],
                "participants": [1, 2],
                "context": [1, 2, 3],
            },
            "storyboard_frames": [],
            "storyboard_labels": {},
            "meta": {
                "verification_status": "matched",
                "environment": {
                    "selected_environment_count": 1,
                    "raw_environment_count": 1,
                    "truncated": False,
                },
            },
            "paths": {"trajectory": "/data/run.lammpstrj"},
        }

    monkeypatch.setattr(svc, "build_rng_event_visualization", fake_build)
    client = create_app().server.test_client()
    selected_row = {
        "event_id": "rngevt-1",
        "anchor_frame": 20,
        "reactant": "[H]+[O]",
        "product": "[H][O]",
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "event-extract-btn",
                "trajectory-refresh-btn",
                "event-type-map-clear-btn",
            ],
            changed="trajectory-refresh-btn.n_clicks",
            input_values={
                "event-extract-btn": 1,
                "trajectory-refresh-btn": 1,
                "event-type-map-clear-btn": 0,
            },
            state_values={
                "event-selected-store": {
                    "row": selected_row,
                    "kind": "rng_event",
                    "config": {"before_frames": 2, "after_frames": 4},
                },
                "app-store": {
                    "artifacts": {"trajectory": "/data/run.lammpstrj"}
                },
                '{"atom_type":["ALL"],"type":"event-type-element-select"}.value': [
                    "H",
                    "O",
                ],
                '{"atom_type":["ALL"],"type":"event-type-element-select"}.id': [
                    {
                        "type": "event-type-element-select",
                        "atom_type": "1",
                    },
                    {
                        "type": "event-type-element-select",
                        "atom_type": "2",
                    },
                ],
                "event-environment-radius": 5.5,
            },
            output_id="event-viewer-store",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert captured == {
        "artifacts": {"trajectory": "/data/run.lammpstrj"},
        "row": selected_row,
        "before_frames": 2,
        "after_frames": 4,
        "environment_radius": 5.5,
        "atom_type_map": {"1": "H", "2": "O"},
    }
    assert result["event-viewer-card"]["style"] == {"display": "block"}
    assert result["event-frame-slider"]["value"] == 0
    assert "局部轨迹已按 PBC 重定位" in result["trajectory-alert"]["children"]

    captured.clear()
    clear_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "event-extract-btn",
                "trajectory-refresh-btn",
                "event-type-map-clear-btn",
            ],
            changed="event-type-map-clear-btn.n_clicks",
            input_values={
                "event-extract-btn": 1,
                "trajectory-refresh-btn": 1,
                "event-type-map-clear-btn": 1,
            },
            state_values={
                "event-selected-store": {
                    "row": selected_row,
                    "kind": "rng_event",
                    "config": {"before_frames": 2, "after_frames": 4},
                },
                "app-store": {
                    "artifacts": {"trajectory": "/data/run.lammpstrj"}
                },
                '{"atom_type":["ALL"],"type":"event-type-element-select"}.value': [
                    "H",
                    "O",
                ],
                '{"atom_type":["ALL"],"type":"event-type-element-select"}.id': [
                    {
                        "type": "event-type-element-select",
                        "atom_type": "1",
                    },
                    {
                        "type": "event-type-element-select",
                        "atom_type": "2",
                    },
                ],
                "event-environment-radius": 5.5,
            },
            output_id="event-viewer-store",
        ),
    )

    assert clear_response.status_code == 200
    assert captured["atom_type_map"] == {}


def test_event_type_map_editor_renders_detected_types_and_saved_values() -> None:
    client = create_app().server.test_client()
    viewer = {
        "frames": [
            {
                "frame": 20,
                "atoms": [
                    {"id": 1, "type": "1"},
                    {"id": 2, "type": "2"},
                    {"id": 3, "type": "2"},
                ],
            }
        ],
        "meta": {
            "anchor_frame": 20,
            "type_element_map": {"1": "H"},
            "native_element_column": False,
        },
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-viewer-store"],
            changed="event-viewer-store.data",
            input_values={"event-viewer-store": viewer},
            state_values={},
            output_id="event-type-map-editor",
        ),
    )

    assert response.status_code == 200
    rendered = json.dumps(response.get_json()["response"], ensure_ascii=False)
    assert "Type 1" in rendered
    assert "Type 2" in rendered
    assert "2 原子" in rendered
    assert '"value": "H"' in rendered


def test_event_package_download_uses_current_view_scope(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_package(viewer, *, scope):
        captured.update(viewer=viewer, scope=scope)
        return b"event-package-bytes"

    monkeypatch.setattr(svc, "build_event_package", fake_package)
    client = create_app().server.test_client()
    viewer = {"event_id": "event-42", "frames": [{"frame": 10}]}
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-package-btn"],
            changed="event-package-btn.n_clicks",
            input_values={"event-package-btn": 1},
            state_values={
                "event-viewer-store": viewer,
                "event-view-scope": "context",
            },
            output_id="event-package-download",
        ),
    )

    assert response.status_code == 200
    download = response.get_json()["response"]["event-package-download"]["data"]
    assert captured == {"viewer": viewer, "scope": "environment"}
    assert download["filename"] == "event-42_evidence.zip"
    assert download["type"] == "application/zip"
    assert base64.b64decode(download["content"]) == b"event-package-bytes"


def test_legacy_core_queries_are_available_through_dash_services(tmp_path) -> None:
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    reaction.write_text(
        "10 [C]+[O]->[C][O]\n4 [C][O]->[C]+[O]\n",
        encoding="utf-8",
    )
    artifacts = {"reaction": str(reaction), "species": "", "route": "", "trajectory": ""}

    assert svc.search_species(artifacts, "CO", kind="formula")["n_rows"] == 1
    assert len(svc.search_reactions_by_formula(artifacts, "C+O", "CO")["rows"]) == 1
