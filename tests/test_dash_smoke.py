from __future__ import annotations

import base64
import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope import dir_browser
from scripts import rng_query_cli as cli
from scripts.webapp_dash import callbacks as cb
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash import ui_components as ui
from scripts.webapp_dash.candidate_workbench import context_key
from scripts.webapp_dash.navigation import (
    LEGACY_PAGE_REDIRECTS,
    NAV_GROUPS,
    PAGE_SECTIONS,
    PAGE_WORKSPACES,
    TOP_NAV_PAGE_IDS,
    WORKSPACE_PAGE_IDS,
    WORKSPACE_TOOL_PAGES,
    resolve_page_id,
)
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


def _component_pattern_ids(node: Any) -> list[dict[str, Any]]:
    if hasattr(node, "to_plotly_json"):
        node = node.to_plotly_json()
    ids: list[dict[str, Any]] = []
    if isinstance(node, dict):
        props = node.get("props") or {}
        component_id = props.get("id")
        if isinstance(component_id, dict):
            ids.append(component_id)
        for value in node.values():
            ids.extend(_component_pattern_ids(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            ids.extend(_component_pattern_ids(value))
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


def _loading_descendant_ids(node: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(node, dict):
        if node.get("type") == "Loading":
            ids.update(
                _layout_string_ids((node.get("props") or {}).get("children"))
            )
        for value in node.values():
            ids.update(_loading_descendant_ids(value))
    elif isinstance(node, list):
        for value in node:
            ids.update(_loading_descendant_ids(value))
    return ids


@pytest.mark.parametrize("compact_nav", ["0", "1"])
def test_dash_layout_and_callback_dependencies_are_loadable(monkeypatch, compact_nav) -> None:
    monkeypatch.setenv("REACNET_SCOPE_COMPACT_NAV", compact_nav)
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
    dependencies = dependency_response.get_json()
    layout_ids = _layout_string_ids(layout)
    for query_control_id in (
        "species-query",
        "rxn-reactants",
        "evolution-targets",
        "element-distribution-group-element",
        "event-reaction-text",
    ):
        props = (_layout_node_by_id(layout, query_control_id) or {})["props"]
        assert props["persistence"] is True
        assert props["persistence_type"] == "session"
    for dataset_bound_control_id in (
        "event-extract-id",
        "event-frame-slider",
        "evolution-species-file",
    ):
        props = (_layout_node_by_id(layout, dataset_bound_control_id) or {})[
            "props"
        ]
        assert props.get("persistence") is not True
    trajectory_dependency = next(
        dependency
        for dependency in dependencies
        if "running" in dependency
        and any(
            item.get("id") == "event-extract-btn"
            for item in dependency.get("inputs") or []
        )
    )
    assert trajectory_dependency["running"]["running"] == {
        '{"name":"trajectory","type":"dataset-bound-operation"}.data': True
    }
    species_detail_dependency = next(
        dependency
        for dependency in dependencies
        if "running" in dependency
        and "app-store.data" in str(dependency.get("output") or "")
        and any(
            item.get("id") == "species-structure-grid"
            for item in dependency.get("inputs") or []
        )
    )
    assert species_detail_dependency["running"]["running"] == {
        '{"name":"species-detail","type":"dataset-bound-operation"}.data': True
    }
    for navigation_id in {
        "topbar-page-context",
        "topbar-rungroup",
        "topbar-folder",
        "topbar-status",
        "topbar-index-status",
        "nav-species",
        "species-to-channels-btn",
        "species-to-evolution-btn",
        "evolution-open-compare-btn",
        "species-structure-grid",
        "species-structure-results",
        "species-structure-csv-btn",
        "species-workspace-stage",
        "species-stage-results-btn",
        "species-stage-structures-btn",
        "species-stage-detail-btn",
        "species-stage-back-btn",
        "rxn-production-grid",
        "rxn-consumption-grid",
        "rxn-production-csv-btn",
        "rxn-production-csv-download",
        "rxn-consumption-csv-btn",
        "rxn-consumption-csv-download",
        "event-back-btn",
        "nav-trajectory",
        "page-trajectory",
        "trajectory-back-events-btn",
        "trajectory-refresh-btn",
        "nav-data-management",
        "page-data-management",
        "data-open-batch-compare-btn",
        "nav-batch-compare",
        "page-batch-compare",
    }:
        assert navigation_id in layout_ids
    for removed_id in {
        "nav-workflow",
        "page-workflow",
        "nav-literature",
        "page-literature",
        "data-modal",
        "species-mass-mode",
        "species-top",
    }:
        assert removed_id not in layout_ids
    assert "page-home" not in layout_ids
    assert "nav-home" not in layout_ids
    assert "page-description" in layout_ids
    assert "page-eyebrow-section" in layout_ids
    assert "选择当前RNG 数据" in str(
        ((_layout_node_by_id(layout, "page-description") or {}).get("props") or {}).get(
            "children"
        )
    )
    reaction_channel_view = str(_layout_node_by_id(layout, "rxn-channel-view"))
    assert "直接生成/消耗通道" not in reaction_channel_view
    assert "这里是围绕焦点物种的单步" not in reaction_channel_view
    assert "pathway-csv-btn" not in layout_ids
    assert "species-to-event-btn" not in layout_ids
    assert "rs-top-nav-item" in str(
        ((_layout_node_by_id(layout, "nav-species") or {}).get("props") or {}).get(
            "className"
        )
    )
    species_grid = _layout_node_by_id(layout, "species-grid") or {}
    structure_grid = _layout_node_by_id(layout, "species-structure-grid") or {}
    event_grid = _layout_node_by_id(layout, "event-grid") or {}
    assert species_grid["props"]["dashGridOptions"]["pagination"] is True
    assert species_grid["props"]["dashGridOptions"]["paginationPageSize"] == 20
    assert structure_grid["props"]["dashGridOptions"]["pagination"] is True
    assert structure_grid["props"]["dashGridOptions"]["paginationPageSize"] == 50
    assert event_grid["props"]["dashGridOptions"]["pagination"] is True
    assert event_grid["props"]["dashGridOptions"]["paginationPageSize"] == 25
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
        == {"page": "data-management"}
    )
    assert (_layout_node_by_id(layout, "page-species") or {})["props"][
        "className"
    ] == "rs-page"
    assert (_layout_node_by_id(layout, "page-data-management") or {})["props"][
        "className"
    ] == "rs-page rs-data-page active"
    assert "active" in str(
        ((_layout_node_by_id(layout, "nav-data-management") or {}).get("props") or {}).get(
            "className"
        )
    ).split()
    assert (_layout_node_by_id(layout, "app-store") or {})["props"][
        "storage_type"
    ] == "memory"
    assert (_layout_node_by_id(layout, "app-store") or {})["props"]["data"][
        "context_state"
    ] == "restoring"
    assert (_layout_node_by_id(layout, "dataset-session-store") or {})["props"][
        "storage_type"
    ] == "session"
    assert (_layout_node_by_id(layout, "event-bookmark-store") or {})["props"][
        "storage_type"
    ] == "session"
    species_grid = _layout_node_by_id(layout, "species-grid")
    assert species_grid is not None
    assert species_grid["props"]["dashGridOptions"]["rowSelection"]["mode"] == "singleRow"
    assert "dir-browser-modal" not in layout_ids
    assert "data-overview-view" in layout_ids
    assert "data-browser-view" in layout_ids
    assert "data-modal-view" not in layout_ids
    assert "dir-browser-breadcrumbs" not in layout_ids
    assert "dir-browser-datasets" not in layout_ids
    assert "dir-browser-recent" not in layout_ids
    assert "import-browse-up" in layout_ids
    assert "import-path" in layout_ids
    assert "import-location" in layout_ids
    assert "import-directories" in layout_ids
    assert "import-add-folder" in layout_ids
    assert "dataset-browser-candidate" in layout_ids
    assert "recent-datasets" in layout_ids
    assert "data-recent-datasets" in layout_ids
    assert "data-rungroup" not in layout_ids
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
    assert "global-operation-progress" not in layout_ids
    assert "page-capability-manage-btn" in layout_ids
    assert "data-preparation-tasks" in layout_ids
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
    assert "species-structure-timing-hint" in layout_ids
    assert (
        (_layout_node_by_id(layout, "species-to-channels-btn") or {})["props"][
            "children"
        ]
        == "直接反应与事件时间"
    )
    for removed_intermediate_id in {
        "nav-intermediate",
        "page-intermediate",
        "inter-kind",
        "inter-search-btn",
        "inter-grid",
        "inter-to-pathway-btn",
        "inter-to-evolution-btn",
    }:
        assert removed_intermediate_id not in layout_ids
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
        "event-distances-csv-btn",
        "event-distances-csv-download",
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
        "event-dft-card",
        "event-dft-reactants",
        "event-dft-products",
        "event-dft-layout",
        "event-dft-unit-confirmation",
        "event-dft-preview-btn",
        "event-dft-download-btn",
        "event-dft-download",
        "event-dft-preview-file",
        "event-dft-preview-text",
        "event-dft-copy",
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
    }:
        assert pathway_id not in layout_ids
    assert "page-transitions" not in layout_ids
    assert "nav-transitions" not in layout_ids
    assert "page-network" not in layout_ids
    assert "nav-network" not in layout_ids
    layout_text = json.dumps(layout, ensure_ascii=False)
    assert "直接反应与事件时间" in layout_text
    assert "species-detail-close" not in layout_ids
    assert (_layout_node_by_id(layout, "species-candidate-menu") or {})["props"]["label"] == "候选路径"
    for removed_text in (
        "从所选反应继续探索",
        "搜索候选路径",
        "Reaction Type 序列（2–8 步）",
        "运行 Species Fate Analysis",
        "候选路径发现",
    ):
        assert removed_text not in layout_text

    retired_dash_ids = {
        "page-candidate-paths",
        "page-pathway",
        "page-species-fate",
        "candidate-path-start-species",
        "candidate-path-search-btn",
        "candidate-path-store",
        "event-path-reaction-sequence",
        "event-path-run-btn",
        "event-path-store",
        "fate-target-species",
        "fate-run-btn",
        "fate-result-store",
        "nav-candidate-paths",
        "nav-pathway",
        "nav-species-fate",
    }
    assert retired_dash_ids.isdisjoint(layout_ids)
    assert not any(
        any(
            str(item.get("id") or "").startswith(
                ("candidate-path", "event-path", "fate-")
            )
            for item in (dependency.get("inputs") or [])
            + (dependency.get("state") or [])
        )
        for dependency in dependencies
    )

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
    assert operation_progress is None
    assert (element_distribution_refresh.get("props") or {}).get("disabled") is True
    layout_text = json.dumps(layout, ensure_ascii=False)
    assert "浏览软件运行机器上的文件夹" in layout_text
    assert "浏览此电脑上的文件夹" not in layout_text
    assert "rs-advanced-menu" not in layout_text
    assert "rs-tool-menu" not in layout_text
    assert "运行组 (base)" not in layout_text
    assert "开始分析" in layout_text
    library_workspace = _layout_node_by_id(layout, "data-library-workspace")
    assert library_workspace is not None
    assert {"data-candidate-summary", "library-management-panel", "library-add-more"} <= _layout_string_ids(library_workspace)
    assert "RNG 数据管理" in json.dumps(library_workspace, ensure_ascii=False)
    assert "data-empty-pick-btn" not in layout_ids
    assert "data-change-pick-btn" in layout_ids
    assert "data-open-species-btn" in layout_ids
    assert "dir-browser-recent-datasets" in layout_ids
    assert "import-paths" in layout_ids
    assert "import-browser-panel" in layout_ids
    assert "import-local-panel" not in layout_ids
    assert "import-upload-intake" not in layout_ids
    assert "上一级" in layout_text
    assert "直接定位" not in layout_text
    assert "返回上级目录" not in layout_text
    cache_management = _layout_node_by_id(layout, "data-cache-management") or {}
    assert cache_management.get("type") == "Section"
    assert "当前RNG 数据的分析索引" in layout_text
    overview_text = json.dumps(overview, ensure_ascii=False)
    assert "data-overview-actions" in overview_text
    assert "开始物种检索" not in overview_text
    assert "rs-data-next-step-panel" not in overview_text
    assert "最近使用" not in overview_text
    assert "确认加载" not in layout_text




def test_navigation_groups_cover_each_tool_once() -> None:
    grouped_pages = [
        page_id
        for _group_label, page_ids in NAV_GROUPS
        for page_id in page_ids
    ]

    assert len(grouped_pages) == 4
    assert len(set(grouped_pages)) == len(grouped_pages)
    assert tuple(grouped_pages) == TOP_NAV_PAGE_IDS
    assert WORKSPACE_PAGE_IDS == (
        "data-management",
        "species",
        "reactions",
        "trajectory",
        "batch-compare",
    )
    assert set(LEGACY_PAGE_REDIRECTS).isdisjoint(WORKSPACE_PAGE_IDS)
    assert LEGACY_PAGE_REDIRECTS == {
        "candidate-paths": "reactions",
        "pathway": "reactions",
        "species-fate": "trajectory",
    }


def test_element_distribution_is_a_task_inside_batch_compare_workspace() -> None:
    assert "element-distribution" in WORKSPACE_TOOL_PAGES["batch-compare"]
    assert PAGE_WORKSPACES["element-distribution"] == "batch-compare"
    assert PAGE_SECTIONS["element-distribution"] == "物种趋势"


def test_workspace_task_navigation_exposes_only_owned_active_tools() -> None:
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json={
            "output": "workspace-task-nav.children",
            "outputs": {"id": "workspace-task-nav", "property": "children"},
            "inputs": [
                {
                    "id": "page-store",
                    "property": "data",
                    "value": {"page": "evolution"},
                },
                {"id": "reaction-task-tabs", "property": "value", "value": "direct"}
            ],
            "state": [{"id": "workspace-task-nav", "property": "children", "value": []}],
            "changedPropIds": ["page-store.data"],
        },
    )

    assert response.status_code == 200
    children = response.get_json()["response"]["workspace-task-nav"]["children"]
    ids = _component_pattern_ids(children)
    assert {item["page"] for item in ids} == {
        "batch-compare",
        "evolution",
        "element-distribution",
    }


def test_retired_page_session_restores_owning_workspace_without_mounting_page() -> None:
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["page-store"],
            changed="page-store.data",
            input_values={"page-store": {"page": "candidate-paths"}},
            state_values={},
            output_id="page-title",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-reactions"]["className"].endswith(" active")
    assert body["nav-reactions"]["aria-current"] == "page"
    assert body["page-title"]["children"] == "反应路径"
    assert resolve_page_id("candidate-paths") == "reactions"
    assert resolve_page_id("pathway") == "reactions"
    assert resolve_page_id("species-fate") == "trajectory"


def test_navigation_waits_for_session_restore_on_initial_load() -> None:
    client = create_app().server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-store.data" in str(item.get("output") or "")
        and any(
            input_item["id"] == "nav-species"
            for input_item in item.get("inputs") or []
        )
    )

    assert dependency["prevent_initial_call"] is True


def test_zero_click_hydration_does_not_overwrite_restored_page() -> None:
    client = create_app().server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-store.data" in str(item.get("output") or "")
        and any(
            input_item["id"] == "nav-species"
            for input_item in item.get("inputs") or []
        )
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="species-to-channels-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "candidate-paths"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 204


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
        if [value["id"] for value in item["inputs"] if value["id"] != "import-auto-result"] == input_ids
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


@pytest.mark.parametrize(
    ("side", "shortcut"),
    [("start", "cp-from-species"), ("target", "cp-to-species")],
)
def test_candidate_shortcut_carries_exact_species_without_candidate_index(
    monkeypatch, side: str, shortcut: str,
) -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise svc.ServiceError("候选路径索引版本不兼容；请重建事件索引。")

    monkeypatch.setattr(svc, "search_candidate_species", unavailable)
    client = create_app().server.test_client()
    input_ids = [
        f"cp-{side}-find", f"cp-{side}-current", "cp-context",
        "cp-from-species", "cp-to-species", "cp-nav-anchor",
    ]
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client, input_ids=input_ids, changed=f"{shortcut}.n_clicks",
            input_values={shortcut: 1},
            state_values={
                "app-store": {"selected_smiles": "CCO", "artifacts": {}},
                f"cp-{side}-query": "", f"cp-{side}": [],
                f"cp-{side}.value": None,
            },
            output_id=f"cp-{side}",
        ),
    )
    assert response.status_code == 200
    selected = response.get_json()["response"][f"cp-{side}"]
    assert selected["value"] == "CCO"
    assert selected["options"][0]["value"] == "CCO"


def _candidate_instance_state() -> tuple[dict[str, Any], dict[str, Any]]:
    store = {
        "dataset_id": "dataset-1",
        "source_revision": {"fingerprint": "revision-1"},
        "artifacts": {"reactionevent": "/data/run.reactionevent.csv"},
    }
    report = {
        "context": context_key(store),
        "query_request_id": "candidate-query-1",
        "source_revision": {"fingerprint": "revision-1"},
        "paths": [
            {
                "signature_id": "route-1",
                "steps": [
                    {
                        "reaction_key": "[C]+[O]->[C][O]",
                        "reactants": ["[C]", "[O]"],
                        "products": ["[C][O]"],
                        "carried_from": "[C]",
                        "carried_to": "[C][O]",
                        "event_count": 26,
                        "transfer_event_count": 26,
                        "transfer_basis": "event_local_dominant_atom_descendant",
                        "max_shared_atoms": 1,
                        "quality": {
                            "rapid_return_events": 0,
                            "reclosure_events": 0,
                            "retained_events": 26,
                        },
                    }
                ],
            }
        ],
    }
    return store, report


def _candidate_instance_page(offset: int) -> dict[str, Any]:
    store, report = _candidate_instance_state()
    stop = min(offset + 25, 26)
    rows = [
        {
            "event_id": f"event-{index + 1}",
            "timestep_index": index,
            "association_status": "matched",
            "atom_id_list": [1, 2],
            "shared_atom_count": 1,
            "anchor_frame": index,
            "reactant": "[C]+[O]",
            "product": "[C][O]",
            "candidate_evidence": {},
        }
        for index in range(offset, stop)
    ]
    return {
        "signature_id": "route-1",
        "step_index": 0,
        "context": context_key(store),
        "query_request_id": report["query_request_id"],
        "source_revision": report["source_revision"],
        "offset": offset,
        "total": 26,
        "has_more": stop < 26,
        "reaction_key": "[C]+[O]->[C][O]",
        "rows": rows,
    }


def test_candidate_instances_navigate_across_evidence_pages(monkeypatch) -> None:
    calls: list[int] = []

    def fake_events(_artifacts, _report, _signature, _step_index, offset=0):
        calls.append(offset)
        return _candidate_instance_page(offset)

    monkeypatch.setattr(svc, "candidate_step_events", fake_events)
    client = create_app().server.test_client()
    store, report = _candidate_instance_state()
    input_ids = [
        "cp-step",
        "cp-focus",
        "cp-report",
        "cp-prev",
        "cp-next",
        "cp-instance-prev",
        "cp-instance-next",
        "app-store",
    ]

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="cp-instance-next.n_clicks",
            input_values={
                "cp-step": 0,
                "cp-focus": "route-1",
                "cp-report": report,
                "cp-prev": 0,
                "cp-next": 0,
                "cp-instance-prev": 0,
                "cp-instance-next": 1,
                "app-store": store,
            },
            state_values={
                "cp-event-page": _candidate_instance_page(0),
                "cp-actual-event": "event-25",
            },
            output_id="cp-event-page",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert calls == [25]
    assert result["cp-event-page"]["data"]["offset"] == 25
    assert result["cp-actual-event"]["value"] == "event-26"
    assert result["cp-instance-position"]["children"] == "实例 26/26"
    assert result["cp-instance-prev"]["disabled"] is False
    assert result["cp-instance-next"]["disabled"] is True
    assert result["cp-open-events"]["disabled"] is False
    assert result["cp-track-instance"]["disabled"] is False


def test_new_candidate_query_rereads_page_with_same_route_signature(monkeypatch) -> None:
    calls: list[int] = []

    def fake_events(_artifacts, _report, _signature, _step_index, offset=0):
        calls.append(offset)
        page = _candidate_instance_page(offset)
        page["rows"][0]["event_id"] = "fresh-event"
        return page

    monkeypatch.setattr(svc, "candidate_step_events", fake_events)
    client = create_app().server.test_client()
    store, report = _candidate_instance_state()
    report["query_request_id"] = "candidate-query-2"
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["cp-step", "cp-focus", "cp-report", "cp-prev", "cp-next",
                       "cp-instance-prev", "cp-instance-next", "app-store"],
            changed="cp-report.data",
            input_values={"cp-step": 0, "cp-focus": "route-1", "cp-report": report,
                          "cp-prev": 0, "cp-next": 0, "cp-instance-prev": 0,
                          "cp-instance-next": 0, "app-store": store},
            state_values={"cp-event-page": _candidate_instance_page(0),
                          "cp-actual-event": "event-1"},
            output_id="cp-event-page",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert calls == [0]
    assert result["cp-event-page"]["data"]["query_request_id"] == "candidate-query-2"
    assert result["cp-actual-event"]["value"] == "fresh-event"


def test_candidate_instance_handoff_rereads_exact_event_and_clears_stale_views(
    monkeypatch,
) -> None:
    fresh_page = _candidate_instance_page(0)
    fresh_page["rows"][1]["anchor_frame"] = 999
    calls: list[tuple[str, int, int]] = []

    def fake_events(_artifacts, _report, signature, step_index, offset=0):
        calls.append((signature, step_index, offset))
        return fresh_page

    monkeypatch.setattr(svc, "candidate_step_events", fake_events)
    monkeypatch.setattr(
        svc,
        "create_event_bookmark",
        lambda _store, row, **_kwargs: {"event_id": row["event_id"]},
    )
    client = create_app().server.test_client()
    store, report = _candidate_instance_state()
    stale_page = _candidate_instance_page(0)
    stale_page["rows"][1]["anchor_frame"] = 2

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["cp-open-events", "cp-track-instance"],
            changed="cp-open-events.n_clicks",
            input_values={"cp-open-events": 1, "cp-track-instance": 0},
            state_values={
                "cp-event-page": stale_page,
                "cp-actual-event": "event-2",
                "cp-report": report,
                "app-store": store,
            },
            output_id="event-selected-store",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    selected = result["event-selected-store"]["data"]
    assert calls == [("route-1", 0, 0)]
    assert selected["row"]["event_id"] == "event-2"
    assert selected["row"]["anchor_frame"] == 999
    assert selected["origin"] == {
        "kind": "candidate_step",
        "query_request_id": "candidate-query-1",
        "signature_id": "route-1",
        "step_index": 0,
        "offset": 0,
        "action": "cp-open-events",
    }
    assert result["event-bookmark-store"]["data"] == {"event_id": "event-2"}
    assert result["event-viewer-store"]["data"] is None
    assert result["event-dft-store"]["data"] is None
    assert result["molecule-lineage-store"]["data"] is None
    assert result["event-viewer-card"]["style"] == {"display": "none"}


def test_candidate_instance_opens_trajectory_and_returns_to_route() -> None:
    client = create_app().server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["cp-track-instance"] = 1

    opened_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="cp-track-instance.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "reactions"}},
            output_id="page-species",
        ),
    )
    assert opened_response.status_code == 200
    opened = opened_response.get_json()["response"]
    trajectory_context = {
        "page": "trajectory",
        "return_page": "reactions",
        "return_label": "返回候选路线",
        "candidate_direct_return": True,
    }
    assert opened["page-store"]["data"] == trajectory_context
    assert opened["dataset-focus-request"]["data"]["target"] == "lx-card"

    back_values = {item["id"]: 0 for item in dependency["inputs"]}
    back_values["trajectory-back-events-btn"] = 1
    returned_response = client.post(
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
    assert returned_response.status_code == 200
    returned = returned_response.get_json()["response"]
    assert returned["page-store"]["data"] == {"page": "reactions"}
    assert returned["page-reactions"]["className"] == "rs-page active"


@pytest.mark.parametrize(
    ("artifacts", "association_status", "atom_ids", "disabled", "label"),
    [
        ({"trajectory": "/data/run.lammpstrj"}, "matched", [1, 2], False, "载入当前实例"),
        ({}, "matched", [1, 2], True, "缺少轨迹坐标"),
        ({"trajectory": "/data/run.lammpstrj"}, "unmatched", [], True, "无法定位具体分子"),
    ],
)
def test_selected_instance_workspace_explains_trajectory_availability(
    artifacts, association_status, atom_ids, disabled, label,
) -> None:
    client = create_app().server.test_client()
    selected = {
        "row": {
            "event_id": "event-2",
            "timestep_index": 7,
            "association_status": association_status,
            "atom_id_list": atom_ids,
            "candidate_evidence": {
                "molecular_evidence_available": False,
            },
        },
        "origin": {"kind": "candidate_step", "step_index": 1},
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-selected-store", "app-store", "page-store"],
            changed="event-selected-store.data",
            input_values={
                "event-selected-store": selected,
                "app-store": {"artifacts": artifacts},
                "page-store": {"page": "trajectory", "candidate_direct_return": True},
            },
            state_values={},
            output_id="trajectory-selection-summary",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["trajectory-open-selected-btn"]["disabled"] is disabled
    assert label in result["trajectory-open-selected-btn"]["children"]
    assert result["trajectory-back-events-btn"]["children"] == "← 返回候选路线"
    assert result["lx-card"]["open"] is False
    assert "RNG 前后键结构" in json.dumps(
        result["trajectory-selection-structure"]["children"], ensure_ascii=False
    )
    assert "候选路线步骤 2" in json.dumps(
        result["trajectory-selection-summary"]["children"], ensure_ascii=False
    )


def test_tracking_action_opens_optional_molecule_change_panel() -> None:
    client = create_app().server.test_client()
    selected = {
        "row": {
            "event_id": "event-2",
            "timestep_index": 7,
            "association_status": "matched",
            "atom_id_list": [1, 2],
        },
        "origin": {
            "kind": "candidate_step",
            "step_index": 1,
            "action": "cp-track-instance",
        },
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-selected-store", "app-store", "page-store"],
            changed="event-selected-store.data",
            input_values={
                "event-selected-store": selected,
                "app-store": {"artifacts": {}},
                "page-store": {"page": "trajectory", "candidate_direct_return": True},
            },
            state_values={},
            output_id="trajectory-selection-summary",
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["lx-card"]["open"] is True


def test_reaction_timing_click_pages_and_exports_exact_events(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = tmp_path / "timing.reactionevent.csv"
    source.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[H]+[O],[H][O]\n"
        "0,[O]+[H],[H][O]\n"
        "1,[H][O],[H]+[O]\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(source))
    client = create_app().server.test_client()
    row = {"reactant_smiles": ["[H]", "[O]"],
           "product_smiles": ["[H][O]"],
           "reaction_smiles": "[H] + [O] -> [H][O]"}
    store = {"dataset_id": "fixture", "artifacts": {"reactionevent": str(source)}}
    chart_request = _callback_payload(
        client,
        input_ids=["rxn-grid", "rxn-channel-selection-store", "rxn-timing-apply-btn"],
        changed="rxn-grid.selectedRows",
        input_values={"rxn-grid": [row], "rxn-channel-selection-store": None,
                      "rxn-timing-apply-btn": None},
        state_values={"rxn-grid": [row], "rxn-timing-distribution-store": None,
                      "rxn-timing-start": None, "rxn-timing-end": None,
                      "rxn-timing-width": None, "app-store": store},
        output_id="rxn-timing-distribution-store",
    )
    chart_response = client.post("/_dash-update-component", json=chart_request)
    assert chart_response.status_code == 200
    distribution = chart_response.get_json()["response"]["rxn-timing-distribution-store"]["data"]
    assert distribution["forward_total"] == 2
    assert distribution["reverse_total"] == 1
    assert distribution["unit"] == "analyzed_frame"

    page_request = _callback_payload(
        client,
        input_ids=["rxn-timing-graph", "rxn-timing-prev-btn",
                   "rxn-timing-next-btn", "rxn-timing-distribution-store"],
        changed="rxn-timing-graph.clickData",
        input_values={"rxn-timing-graph": {"points": [{"customdata": [0, "forward"]}]},
                      "rxn-timing-distribution-store": distribution},
        state_values={"rxn-timing-page-store": None, "app-store": store},
        output_id="rxn-timing-page-store",
    )
    page_response = client.post("/_dash-update-component", json=page_request)
    assert page_response.status_code == 200
    page = page_response.get_json()["response"]["rxn-timing-page-store"]["data"]
    assert page["total"] == 2
    assert len({item["event_id"] for item in page["rows"]}) == 2
    assert all(item["time_unit"] == "analyzed_frame" for item in page["rows"])

    csv_request = _callback_payload(
        client, input_ids=["rxn-timing-csv-btn"],
        changed="rxn-timing-csv-btn.n_clicks",
        input_values={"rxn-timing-csv-btn": 1},
        state_values={"rxn-timing-page-store": page},
        output_id="rxn-timing-csv-download",
    )
    csv_response = client.post("/_dash-update-component", json=csv_request)
    assert csv_response.status_code == 200
    csv_data = csv_response.get_json()["response"]["rxn-timing-csv-download"]["data"]["content"]
    assert "event_id" in csv_data and "time_basis" in csv_data
    assert "reaction_key" in csv_data and "analyzed_frame" in csv_data

    handoff_request = _callback_payload(
        client, input_ids=["rxn-timing-open-events-btn"],
        changed="rxn-timing-open-events-btn.n_clicks",
        input_values={"rxn-timing-open-events-btn": 1},
        state_values={"rxn-timing-page-store": page, "app-store": store},
        output_id="event-grid-store",
    )
    handoff_response = client.post("/_dash-update-component", json=handoff_request)
    assert handoff_response.status_code == 200
    workflow = handoff_response.get_json()["response"]["event-grid-store"]["data"]
    assert workflow["kind"] == "rng_event"
    assert len(workflow["rows"]) == 2

    stale_request = dict(page_request)
    stale_request["state"] = [
        {**item, "value": {"dataset_id": "replacement", "artifacts": {}}}
        if item["id"] == "app-store" else item
        for item in page_request["state"]
    ]
    stale_response = client.post("/_dash-update-component", json=stale_request)
    assert stale_response.status_code == 200
    assert stale_response.get_json()["response"]["rxn-timing-page-store"]["data"] is None

    reset_ids = {output.component_id for output, _ in cb._dataset_bound_resets()}
    assert {"rxn-timing-distribution-store", "rxn-timing-page-store",
            "rxn-timing-event-grid"}.issubset(reset_ids)




def test_evolution_catalog_callback_populates_searchable_formula_picker(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_catalog(artifacts, **kwargs):
        captured["artifacts"] = artifacts
        captured.update(kwargs)
        return {
            "options": [
                {
                    "label": "C6H5ClO · 2 SMILES · 2/2 文件",
                    "value": "formula:C6H5ClO",
                    "search": "C6H5ClO smiles-a smiles-b",
                }
            ],
            "meta": {
                "n_sources": 2,
                "n_formulas": 1,
                "warnings": [],
            },
        }

    monkeypatch.setattr(svc, "species_evolution_catalog", fake_catalog)
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["evolution-load-species-btn"],
            changed="evolution-load-species-btn.n_clicks",
            input_values={"evolution-load-species-btn": 1},
            state_values={
                "evolution-species-file": "",
                "evolution-species-files": "a::/tmp/a.species\nb::/tmp/b.species",
                "evolution-species-picker": ["formula:C6H5ClO"],
                "app-store": {"artifacts": {}},
            },
            output_id="evolution-species-picker",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert (
        body["evolution-species-picker"]["options"][0]["value"]
        == "formula:C6H5ClO"
    )
    assert body["evolution-species-picker"]["value"] == ["formula:C6H5ClO"]
    assert body["evolution-catalog-alert"]["children"] == "已从 2 个文件读取 1 种分子式"
    assert captured["species_files"].startswith("a::")


def test_evolution_picker_targets_are_forwarded_to_plot_service(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_evolution(artifacts, targets, **kwargs):
        captured["artifacts"] = artifacts
        captured["targets"] = targets
        captured.update(kwargs)
        return {
            "x_name": "timestep",
            "x_values": [0, 1],
            "curves": [
                {
                    "name": "2500K | C6H5ClO",
                    "query": "formula:C6H5ClO",
                    "values": [100, 80],
                }
            ],
            "meta": {"warnings": []},
        }

    monkeypatch.setattr(svc, "build_species_evolution", fake_evolution)
    app = create_app()
    client = app.server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["evolution-search-btn"],
            changed="evolution-search-btn.n_clicks",
            input_values={"evolution-search-btn": 1},
            state_values={
                "evolution-species-picker": ["formula:C6H5ClO"],
                "evolution-targets": "formula:O2\nformula:C6H5ClO",
                "evolution-xaxis": "step",
                "evolution-smooth": 1,
                "evolution-species-file": "",
                "evolution-species-files": "2500K::/tmp/2500K.species",
                "evolution-formula-mode": "sum",
                "evolution-max-smiles": 0,
                "evolution-normalize": "none",
                "evolution-time-align": "raw",
                "evolution-timestep": None,
                "evolution-downsample": 0,
                "evolution-max-curves": 30,
                "evolution-curve-filter": "",
                "app-store": {"artifacts": {}},
            },
            output_id="evolution-payload-store",
        ),
    )

    assert response.status_code == 200
    assert captured["targets"] == ["formula:C6H5ClO", "formula:O2"]
    assert captured["species_files"] == "2500K::/tmp/2500K.species"
    payload = response.get_json()["response"]["evolution-payload-store"]["data"]
    assert payload["visible_curve_names"] == ["2500K | C6H5ClO"]


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
            output_id="species-search-btn-response",
        ),
    )
    assert search_response.status_code == 200
    envelope = search_response.get_json()["response"]["species-search-btn-response"]["data"]
    assert envelope["request"] == 1
    assert envelope["context"]["artifacts"] == artifacts
    commit = next(item for item in client.get("/_dash-dependencies").json
                  if item["inputs"] == [{"id": "species-search-btn-response", "property": "data"}])
    search_body = {}
    for output, value in zip(commit["output"].strip(".").split("..."), envelope["values"]):
        component, prop = output.split(".")
        search_body.setdefault(component, {})[prop.split("@")[0]] = value
    formula_rows = search_body["species-grid"]["rowData"]
    grid_store = search_body["species-grid-store"]["data"]
    assert formula_rows[0]["formula"] == "C5H4O"
    assert formula_rows[0]["structure_count"] == 3
    assert search_body["species-grid-page-size"]["data"] == 20
    formula_tooltip = search_body["species-grid-previews"]["data"][0]
    assert formula_tooltip["formula"]["type"] == "markdown"
    assert "/api/structure.svg?smiles=" in formula_tooltip["formula"]["value"]
    assert "width=280&height=190" in formula_tooltip["formula"]["value"]
    assert "选择分子式可查看原始结构结果" in search_body["species-alert"]["children"]

    structure_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["species-grid"],
            changed="species-grid.selectedRows",
            input_values={"species-grid.selectedRows": [formula_rows[0]]},
            state_values={
                "species-grid.rowData": formula_rows,
                "species-grid-store": grid_store,
                "app-store": {"artifacts": artifacts},
            },
            output_id="species-structure-results",
        ),
    )
    assert structure_response.status_code == 200
    structure_body = structure_response.get_json()["response"]
    structure_rows = structure_body["species-structure-grid"]["rowData"]
    assert structure_body["species-structure-results"]["style"] == {
        "display": "block"
    }
    assert len(structure_rows) == 3
    assert [row["smiles"] for row in structure_rows] == structures
    structure_tooltip = structure_body["species-structure-grid-previews"]["data"][0]
    assert "%5BH%5D" in structure_tooltip["smiles"]["value"]
    assert "共 3 个结构" in structure_body["species-structure-alert"]["children"]
    assert "每页显示 50 条" in structure_body["species-structure-alert"]["children"]


def test_species_workspace_widgets_switch_and_return_between_stages() -> None:
    app = create_app()
    client = app.server.test_client()
    stage_inputs = [
        "species-search-btn",
        "species-grid",
        "species-structure-grid",
        "species-stage-results-btn",
        "species-stage-structures-btn",
        "species-stage-detail-btn",
        "species-stage-back-btn",
    ]
    base_values = {
        "species-search-btn.n_clicks": 1,
        "species-grid.selectedRows": [0],
        "species-structure-grid.selectedRows": [],
        "species-stage-results-btn.n_clicks": 0,
        "species-stage-structures-btn.n_clicks": 0,
        "species-stage-detail-btn.n_clicks": 0,
        "species-stage-back-btn.n_clicks": 0,
    }
    grid_store = {"query_kind": "mass", "rows": [{"formula": "H2O"}]}

    select_formula = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=stage_inputs,
            changed="species-grid.selectedRows",
            input_values=base_values,
            state_values={
                "species-workspace-stage": "results",
                "species-grid-store": grid_store,
                "species-structure-grid": [],
            },
            output_id="species-workspace-stage",
        ),
    )
    assert select_formula.status_code == 200
    assert select_formula.get_json()["response"]["species-workspace-stage"][
        "data"
    ] == "structures"

    structure_rows = [{"formula": "H2O", "smiles": "[H]O[H]"}]
    render_inputs = [
        "species-workspace-stage",
        "species-grid-store",
        "species-structure-grid",
        "species-grid",
        "species-structure-grid",
    ]
    render_structures = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=render_inputs,
            changed="species-workspace-stage.data",
            input_values={
                "species-workspace-stage.data": "structures",
                "species-grid-store.data": grid_store,
                "species-structure-grid.rowData": structure_rows,
                "species-grid.selectedRows": [0],
                "species-structure-grid.selectedRows": [],
            },
            state_values={},
            output_id="species-result-stage",
        ),
    )
    assert render_structures.status_code == 200
    structures_view = render_structures.get_json()["response"]
    assert structures_view["species-result-stage"]["style"] == {
        "display": "none"
    }
    assert structures_view["species-structure-stage"]["style"] == {}
    assert structures_view["species-stage-structures-btn"]["active"] is True
    assert structures_view["species-stage-back-btn"]["children"] == (
        "← 返回候选分子式"
    )

    select_detail_values = {
        **base_values,
        "species-structure-grid.selectedRows": [0],
    }
    select_detail = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=stage_inputs,
            changed="species-structure-grid.selectedRows",
            input_values=select_detail_values,
            state_values={
                "species-workspace-stage": "structures",
                "species-grid-store": grid_store,
                "species-structure-grid": structure_rows,
            },
            output_id="species-workspace-stage",
        ),
    )
    assert select_detail.status_code == 200
    assert select_detail.get_json()["response"]["species-workspace-stage"][
        "data"
    ] == "detail"

    render_detail = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=render_inputs,
            changed="species-workspace-stage.data",
            input_values={
                "species-workspace-stage.data": "detail",
                "species-grid-store.data": grid_store,
                "species-structure-grid.rowData": structure_rows,
                "species-grid.selectedRows": [0],
                "species-structure-grid.selectedRows": [0],
            },
            state_values={},
            output_id="species-result-stage",
        ),
    )
    assert render_detail.status_code == 200
    detail_view = render_detail.get_json()["response"]
    assert detail_view["species-detail-stage"]["style"] == {}
    assert detail_view["species-structure-stage"]["style"] == {"display": "none"}
    assert detail_view["species-stage-detail-btn"]["active"] is True
    assert detail_view["species-stage-back-btn"]["children"] == (
        "← 返回结构列表"
    )

    back_values = {
        **select_detail_values,
        "species-stage-back-btn.n_clicks": 1,
    }
    back_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=stage_inputs,
            changed="species-stage-back-btn.n_clicks",
            input_values=back_values,
            state_values={
                "species-workspace-stage": "detail",
                "species-grid-store": grid_store,
                "species-structure-grid": structure_rows,
            },
            output_id="species-workspace-stage",
        ),
    )
    assert back_response.status_code == 200
    assert back_response.get_json()["response"]["species-workspace-stage"][
        "data"
    ] == "structures"


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
    assert body["nav-batch-compare"]["aria-current"] == "page"
    assert body["data-open-batch-compare-btn"]["aria-current"] == "page"


def test_evolution_page_exposes_and_opens_multi_source_comparison() -> None:
    app = create_app()
    client = app.server.test_client()
    layout = client.get("/_dash-layout").get_json()

    compare_button = _layout_node_by_id(layout, "evolution-open-compare-btn")
    assert compare_button is not None
    assert compare_button["props"]["children"] == "打开多来源对比"
    evolution_page = _layout_node_by_id(layout, "page-evolution")
    assert evolution_page is not None
    rendered_page = json.dumps(evolution_page, ensure_ascii=False)
    assert "兼容：临时多文件叠加" in rendered_page
    assert "曲线处理设置" in rendered_page

    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["evolution-open-compare-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="evolution-open-compare-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "evolution"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "batch-compare"}
    assert body["page-batch-compare"]["className"] == "rs-page active"
    assert body["nav-batch-compare"]["aria-current"] == "page"


def test_species_empty_state_mentions_comparison_workflow() -> None:
    app = create_app()
    client = app.server.test_client()
    layout = client.get("/_dash-layout").get_json()
    empty_state = _layout_node_by_id(layout, "species-empty-state")
    assert empty_state is not None
    assert "多来源趋势对比可直接选择多个已导入来源" in json.dumps(empty_state, ensure_ascii=False)
    assert "batch-compare" not in WORKSPACE_TOOL_PAGES["species"]
    assert "batch-compare" in WORKSPACE_PAGE_IDS


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
    assert body["page-store"]["data"] == {
        "page": "data-management",
        "dataset_return": {
            "page": "species",
            "trigger": "open-data-modal",
        },
    }
    assert body["page-species"]["className"] == "rs-page"
    assert body["page-data-management"]["className"] == "rs-page rs-data-page active"
    assert body["nav-data-management"]["className"] == (
        "rs-top-nav-item rs-nav-utility active"
    )
    assert body["page-title"]["children"] == "RNG 数据"
    assert body["page-eyebrow-section"]["children"] == "RNG 数据"


def test_direct_data_workspace_navigation_has_no_false_return_source() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["nav-data-management"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="nav-data-management.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "species"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["response"]["page-store"]["data"] == {
        "page": "data-management"
    }


def test_data_workspace_next_step_opens_species_search() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["data-open-species-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="data-open-species-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "data-management"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "species"}
    assert body["page-species"]["className"] == "rs-page active"


@pytest.mark.parametrize(
    "target",
    ["species", "reactions", "trajectory", "batch-compare"],
)
def test_data_workspace_overview_card_opens_workspace(target: str) -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    pattern_input_id = next(
        item for item in input_ids if "data-overview-open-page" in item
    )
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values[pattern_input_id] = [1]
    triggered_component_id = json.dumps(
        {"page": target, "type": "data-overview-open-page"},
        separators=(",", ":"),
        sort_keys=True,
    )

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed=f"{triggered_component_id}.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "data-management"}},
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": target}
    assert body[f"page-{target}"]["className"].endswith(" active")
    nav_id = f"nav-{target}"
    assert body[nav_id]["aria-current"] == "page"


def test_data_workspace_next_step_syncs_restored_page_chrome() -> None:
    app = create_app()
    client = app.server.test_client()
    navigation_dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in navigation_dependency["inputs"]]
    input_values = {item["id"]: 0 for item in navigation_dependency["inputs"]}
    input_values["data-open-species-btn"] = 1

    navigation_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="data-open-species-btn.n_clicks",
            input_values=input_values,
            state_values={"page-store": {"page": "data-management"}},
            output_id="page-species",
        ),
    )
    assert navigation_response.status_code == 200
    page_store = navigation_response.get_json()["response"]["page-store"]["data"]

    sync_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["page-store"],
            changed="page-store.data",
            input_values={"page-store": page_store},
            state_values={},
            output_id="page-title",
        ),
    )

    assert sync_response.status_code == 200
    body = sync_response.get_json()["response"]
    assert body["page-species"]["className"] == "rs-page active"
    assert body["nav-species"]["aria-current"] == "page"
    assert body["nav-data-management"]["aria-current"] == "false"
    assert body["data-open-batch-compare-btn"]["aria-current"] == "false"
    assert body["page-title"]["children"] == "物种发现"


def test_review_source_files_are_collapsed_by_default() -> None:
    details = cb._render_artifacts(
        {"reaction": "/data/run.lammpstrj.reaction"},
    )

    assert not getattr(details, "open", False)


def test_cancelling_dataset_selection_returns_to_source_page() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "page-species.className" in str(item.get("output") or "")
    )
    input_ids = [item["id"] for item in dependency["inputs"]]
    input_values = {item["id"]: 0 for item in dependency["inputs"]}
    input_values["dir-browser-cancel-btn"] = 1

    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="dir-browser-cancel-btn.n_clicks",
            input_values=input_values,
            state_values={
                "page-store": {
                    "page": "data-management",
                    "dataset_return": {
                        "page": "reactions",
                        "trigger": "open-data-modal",
                    },
                }
            },
            output_id="page-species",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["page-store"]["data"] == {"page": "reactions"}
    assert body["page-reactions"]["className"] == "rs-page active"


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
    assert body["page-title"]["children"] == "反应路径"
    assert body["page-header"]["className"] == (
        "rs-page-header is-title-only"
    )


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
            changed="species-grid.selectedRows",
            input_values={
                "species-grid.selectedRows": [row],
                "species-structure-grid.selectedRows": [],
            },
            state_values={
                "species-grid.rowData": [row],
                "species-structure-grid.rowData": [],
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
    rendered_detail = json.dumps(
        detail["detail-body"]["children"],
        ensure_ascii=False,
    )
    assert "身份、处理设置与 Molecular Evidence" in rendered_detail
    assert "分子式仅用于检索与分组" in rendered_detail

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
    assert navigation["nav-batch-compare"]["className"] == "rs-top-nav-item active"


def test_channel_view_back_button_uses_navigation_history() -> None:
    app = create_app()
    client = app.server.test_client()
    dependencies = client.get("/_dash-dependencies").get_json()
    back_dependencies = [
        item
        for item in dependencies
        if any(
            input_item.get("id") == "rxn-channel-back-btn"
            for input_item in item.get("inputs") or []
        )
    ]

    assert len(back_dependencies) == 1
    assert back_dependencies[0].get("clientside_function") is not None
    assert "rxn-channel-history-store.data" in str(
        back_dependencies[0].get("output") or ""
    )
    label_dependency = next(
        item
        for item in dependencies
        if [input_item.get("id") for input_item in item.get("inputs") or []]
        == ["rxn-channel-history-store"]
        and "rxn-channel-back-btn.children" in str(item.get("output") or "")
    )
    assert "rxn-channel-back-btn.title" in str(
        label_dependency.get("output") or ""
    )


def test_reaction_structure_species_cards_expose_channel_focus_actions() -> None:
    detail = {
        "reaction_formulas": "CO -> C + O",
        "reaction_smiles": "[C][O] -> [C] + [O]",
        "reactants": [
            {
                "index": 0,
                "smiles": "[C][O]",
                "formula": "CO",
                "occurrence": 1,
                "occurrence_total": 1,
                "structure_url": "/api/structure.svg?smiles=CO",
            }
        ],
        "products": [
            {
                "index": 0,
                "smiles": "[C]",
                "formula": "C",
                "occurrence": 1,
                "occurrence_total": 1,
                "structure_url": "/api/structure.svg?smiles=C",
            }
        ],
    }

    children = cb._reaction_structure_detail_children(
        detail,
        action_scope="channel",
    )

    assert _component_pattern_ids(children) == [
        {
            "type": "rxn-structure-species",
            "scope": "channel",
            "side": "reactant",
            "index": 0,
            "smiles": "[C][O]",
            "formula": "CO",
        },
        {
            "type": "rxn-structure-species",
            "scope": "channel",
            "side": "product",
            "index": 0,
            "smiles": "[C]",
            "formula": "C",
        },
    ]


def test_clicking_reaction_structure_species_loads_its_direct_channels(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    production = {
        "reaction_formulas": "C + O -> CO",
        "reaction_smiles": "[C] + [O] -> [C][O]",
    }
    consumption = {
        "reaction_formulas": "CO -> C + O",
        "reaction_smiles": "[C][O] -> [C] + [O]",
    }

    def fake_collect(artifacts, smiles, *, top, include_kinetics=True):
        captured.update(
            {
                "artifacts": artifacts,
                "smiles": smiles,
                "top": top,
                "include_kinetics": include_kinetics,
            }
        )
        return {
            "production_rows": [production],
            "consumption_rows": [consumption],
        }

    monkeypatch.setattr(svc, "collect_species_channels", fake_collect)
    client = create_app().server.test_client()
    pattern_id = (
        '{"formula":["ALL"],"index":["ALL"],"scope":["ALL"],'
        '"side":["ALL"],"smiles":["ALL"],'
        '"type":"rxn-structure-species"}'
    )
    clicked_id = {
        "type": "rxn-structure-species",
        "scope": "channel",
        "side": "product",
        "index": 0,
        "smiles": "[C]",
        "formula": "C",
    }
    payload = _callback_payload(
        client,
        input_ids=[pattern_id],
        changed=(
            f"{json.dumps(clicked_id, sort_keys=True, separators=(',', ':'))}"
            ".n_clicks"
        ),
        input_values={pattern_id: [1]},
        state_values={
            "rxn-top": 7,
            "app-store": {
                "artifacts": {"reaction": "/tmp/example.reaction"},
                "selected_smiles": "[C][O]",
                "selected_formula": "CO",
                "selected_species_source": "species_grid",
            },
            "rxn-channel-history-store": [
                {"kind": "species", "label": "返回物种检索"}
            ],
            "rxn-production-grid.rowData": [production],
            "rxn-production-grid.columnDefs": [{"id": "reaction_formulas"}],
            "rxn-production-grid.selectedRows": [0],
            "rxn-consumption-grid.rowData": [consumption],
            "rxn-consumption-grid.columnDefs": [{"id": "reaction_formulas"}],
            "rxn-consumption-grid.selectedRows": [],
            "rxn-channel-selection-store": {
                "lane": "production",
                "row": production,
            },
            "rxn-channel-alert": "CO 的通道",
        },
        output_id="rxn-production-grid",
    )
    for input_item in payload["inputs"]:
        if input_item["id"] == pattern_id:
            input_item["id"] = clicked_id

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert captured == {
        "artifacts": {"reaction": "/tmp/example.reaction"},
        "smiles": "[C]",
        "top": 7,
        "include_kinetics": False,
    }
    assert result["rxn-production-grid"]["rowData"] == [production]
    assert result["rxn-consumption-grid"]["rowData"] == [consumption]
    assert result["rxn-production-grid"]["selectedRows"] == []
    assert result["rxn-consumption-grid"]["selectedRows"] == []
    assert result["rxn-production-grid"]["cellClicked"] is None
    assert result["rxn-consumption-grid"]["cellClicked"] is None
    assert result["rxn-channel-selection-store"]["data"] is None
    assert result["rxn-channel-history-store"]["data"] == [
        {"kind": "species", "label": "返回物种检索"},
        {
            "kind": "channel",
            "label": "返回 CO 的通道",
            "focus": {
                "selected_smiles": "[C][O]",
                "selected_formula": "CO",
                "selected_species_source": "species_grid",
            },
            "production_data": [production],
            "production_columns": [{"id": "reaction_formulas"}],
            "production_selected_rows": [0],
            "consumption_data": [consumption],
            "consumption_columns": [{"id": "reaction_formulas"}],
            "consumption_selected_rows": [],
            "selection": {"lane": "production", "row": production},
            "alert": "CO 的通道",
        },
    ]
    assert result["app-store"]["data"]["selected_smiles"] == "[C]"
    assert result["app-store"]["data"]["selected_formula"] == "C"
    assert result["rxn-query-card"]["style"] == {"display": "none"}
    assert result["rxn-results-card"]["style"] == {"display": "none"}
    assert result["rxn-channel-view"]["style"] == {"display": "block"}

    search_clicked_id = {**clicked_id, "scope": "search"}
    search_payload = _callback_payload(
        client,
        input_ids=[pattern_id],
        changed=(
            f"{json.dumps(search_clicked_id, sort_keys=True, separators=(',', ':'))}"
            ".n_clicks"
        ),
        input_values={pattern_id: [1]},
        state_values={
            "rxn-top": 7,
            "app-store": {
                "artifacts": {"reaction": "/tmp/example.reaction"},
                "selected_smiles": "[O]",
                "selected_formula": "O",
                "selected_species_source": "reaction_search",
            },
            "rxn-channel-history-store": [],
        },
        output_id="rxn-production-grid",
    )
    for input_item in search_payload["inputs"]:
        if input_item["id"] == pattern_id:
            input_item["id"] = search_clicked_id

    search_response = client.post(
        "/_dash-update-component",
        json=search_payload,
    )

    assert search_response.status_code == 200
    search_result = search_response.get_json()["response"]
    assert search_result["rxn-channel-history-store"]["data"] == [
        {
            "kind": "reaction-search",
            "label": "返回反应式检索",
            "focus": {
                "selected_smiles": "[O]",
                "selected_formula": "O",
                "selected_species_source": "reaction_search",
            },
        }
    ]


def test_mounting_channel_species_cards_does_not_change_focus(monkeypatch) -> None:
    calls: list[str] = []

    def fake_collect(_artifacts, smiles, *, top):
        calls.append(smiles)
        return {"production_rows": [], "consumption_rows": []}

    monkeypatch.setattr(svc, "collect_species_channels", fake_collect)
    client = create_app().server.test_client()
    pattern_id = (
        '{"formula":["ALL"],"index":["ALL"],"scope":["ALL"],'
        '"side":["ALL"],"smiles":["ALL"],'
        '"type":"rxn-structure-species"}'
    )
    mounted_id = {
        "type": "rxn-structure-species",
        "scope": "channel",
        "side": "reactant",
        "index": 0,
        "smiles": "[H]",
        "formula": "H",
    }
    payload = _callback_payload(
        client,
        input_ids=[pattern_id],
        changed=(
            f"{json.dumps(mounted_id, sort_keys=True, separators=(',', ':'))}"
            ".n_clicks"
        ),
        input_values={pattern_id: [0]},
        state_values={
            "rxn-top": 50,
            "app-store": {
                "artifacts": {"reaction": "/tmp/example.reaction"},
                "selected_smiles": "C6H5ClO",
                "selected_formula": "C6H5ClO",
                "selected_species_source": "reaction_structure",
            },
            "rxn-channel-history-store": [],
            "rxn-production-grid.rowData": [],
            "rxn-production-grid.columnDefs": [],
            "rxn-production-grid.selectedRows": [],
            "rxn-consumption-grid.rowData": [],
            "rxn-consumption-grid.columnDefs": [],
            "rxn-consumption-grid.selectedRows": [1],
            "rxn-channel-selection-store": {
                "lane": "consumption",
                "row": {"reaction_formulas": "H + C6H5ClO -> HCl + C6H5O"},
            },
            "rxn-channel-alert": "C6H5ClO 的通道",
        },
        output_id="rxn-production-grid",
    )
    for input_item in payload["inputs"]:
        if input_item["id"] == pattern_id:
            input_item["id"] = mounted_id

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 204
    assert calls == []


def test_selected_species_loads_exact_production_and_consumption_channels(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_collect(artifacts, smiles, *, top, include_kinetics=True):
        captured.update(
            {
                "artifacts": artifacts,
                "smiles": smiles,
                "top": top,
                "include_kinetics": include_kinetics,
            }
        )
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
                "nav-reactions",
            ],
            changed="species-to-channels-btn.n_clicks",
            input_values={
                "species-to-channels-btn": 1,
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
            input_ids=["species-to-channels-btn"],
            changed="species-to-channels-btn.n_clicks",
            input_values={
                "species-to-channels-btn": 1,
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
        "include_kinetics": False,
    }
    assert body["rxn-production-grid"]["rowData"][0]["role_label"] == "生成"
    assert body["rxn-consumption-grid"]["rowData"][0]["role_label"] == "消耗"
    assert body["rxn-production-grid"]["columnDefs"][0]["field"] == "reaction_formulas"
    assert body["rxn-production-grid"]["columnDefs"][0]["headerName"] == "反应式"
    assert {
        column["field"] for column in body["rxn-production-grid"]["columnDefs"]
    } >= {"event_frequency_per_ps", "k_app_display", "reverse_k_app_display"}
    assert body["rxn-channel-alert"]["children"] == ""
    assert body["rxn-production-grid"]["selectedRows"] == []
    assert body["rxn-consumption-grid"]["selectedRows"] == []
    assert body["rxn-production-grid"]["cellClicked"] is None
    assert body["rxn-consumption-grid"]["cellClicked"] is None


def test_reaction_channel_view_exposes_inline_time_conversion() -> None:
    layout = create_app().server.test_client().get("/_dash-layout").get_json()
    input_node = _layout_node_by_id(layout, "rxn-channel-timestep-ps") or {}
    save_node = (
        _layout_node_by_id(layout, "rxn-channel-timestep-save-btn") or {}
    )
    progress_node = (
        _layout_node_by_id(layout, "rxn-channel-timestep-progress") or {}
    )
    trajectory_node = (
        _layout_node_by_id(layout, "rxn-channel-trajectory-path") or {}
    )
    unit_node = (
        _layout_node_by_id(layout, "rxn-channel-coordinate-unit-confirm")
        or {}
    )
    volume_save_node = (
        _layout_node_by_id(layout, "rxn-channel-volume-save-btn") or {}
    )
    volume_status_node = (
        _layout_node_by_id(layout, "rxn-channel-volume-status") or {}
    )
    volume_refresh_node = (
        _layout_node_by_id(layout, "rxn-channel-volume-refresh") or {}
    )
    rate_compatibility = (
        _layout_node_by_id(layout, "rxn-channel-rate-compatibility") or {}
    )
    channel_view = str(_layout_node_by_id(layout, "rxn-channel-view"))

    assert (input_node.get("props") or {}).get("type") == "number"
    assert (input_node.get("props") or {}).get("min") > 0
    assert (save_node.get("props") or {}).get("children") == "保存并刷新时间"
    assert (progress_node.get("props") or {}).get("aria-live") == "polite"
    assert (trajectory_node.get("props") or {}).get("type") == "text"
    assert (unit_node.get("props") or {}).get("value") is False
    assert (volume_save_node.get("props") or {}).get("children") == "兼容保留"
    assert (rate_compatibility.get("props") or {}).get("hidden") is True
    assert (volume_refresh_node.get("props") or {}).get("interval") == 1000
    assert (volume_refresh_node.get("props") or {}).get("disabled") is True
    assert not {
        "rxn-production-grid",
        "rxn-consumption-grid",
    } & _loading_descendant_ids(layout)
    assert "source timestep 每增加 1" in channel_view
    assert "0.25 fs" in channel_view
    assert "可选设置 · 不计算表观速率" in channel_view
    assert "模拟盒体积" not in channel_view

    dependency = next(
        item
        for item in create_app().server.test_client().get(
            "/_dash-dependencies"
        ).get_json()
        if [input_item["id"] for input_item in item.get("inputs") or []]
        == ["rxn-channel-timestep-save-btn"]
    )
    assert dependency["running"]["running"] == {
        "rxn-channel-timestep-save-btn.disabled": True,
        "rxn-channel-timestep-save-btn.children": "正在刷新…",
        "rxn-channel-timestep-progress.children": "正在读取索引并刷新通道时间…",
        "rxn-channel-timestep-progress.className": "rs-kinetics-progress is-running",
    }

    volume_dependency = next(
        item
        for item in create_app().server.test_client().get(
            "/_dash-dependencies"
        ).get_json()
        if [input_item["id"] for input_item in item.get("inputs") or []]
        == ["rxn-channel-volume-save-btn"]
    )
    assert volume_dependency["background"] is not None
    assert volume_dependency["running"]["running"] == {
        "rxn-channel-volume-save-btn.disabled": True,
        "rxn-channel-volume-save-btn.children": "正在准备…",
        "rxn-channel-volume-progress.children": "正在关联轨迹并检查索引…",
        "rxn-channel-volume-progress.className": "rs-kinetics-progress is-running",
        "rxn-channel-volume-refresh.disabled": False,
    }


def test_channel_volume_progress_polls_workspace_task(monkeypatch) -> None:
    captured: list[list[dict[str, Any]]] = []

    def fake_tasks(targets):
        captured.append(targets)
        return [
            {
                "dataset_id": "dataset-1",
                "capability": "trajectory",
                "state": "running",
                "phase": "indexing_trajectory",
                "progress": 0.652,
                "progress_trusted": True,
                "source_artifact_revision": {"size": 162_000_000_000},
            }
        ]

    monkeypatch.setattr(svc, "list_preparation_tasks", fake_tasks)
    client = create_app().server.test_client()
    current = {
        "dataset_id": "dataset-1",
        "folder": "/data/run",
        "base": "/data/run/run.lammpstrj",
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["rxn-channel-volume-refresh"],
            changed="rxn-channel-volume-refresh.n_intervals",
            input_values={"rxn-channel-volume-refresh": 1},
            state_values={"app-store": current},
            output_id="rxn-channel-volume-progress",
        ),
    )

    assert response.status_code == 200
    assert captured == [[current]]
    result = response.get_json()["response"]["rxn-channel-volume-progress"]
    assert "轨迹索引" in result["children"]
    assert "65.2%" in result["children"]
    assert "GiB" in result["children"]
    assert result["className"] == "rs-kinetics-progress is-running"


def test_channel_time_conversion_prefills_from_current_dataset(monkeypatch) -> None:
    captured: list[dict[str, str]] = []

    def fake_load(artifacts):
        captured.append(artifacts)
        return 0.002

    monkeypatch.setattr(svc, "channel_timestep_ps", fake_load)
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store"],
            changed="app-store.data",
            input_values={
                "app-store": {
                    "artifacts": {"species": "/data/run.species"},
                }
            },
            state_values={},
            output_id="rxn-channel-timestep-ps",
        ),
    )

    assert response.status_code == 200
    assert captured == [{"species": "/data/run.species"}]
    assert response.get_json()["response"]["rxn-channel-timestep-ps"][
        "value"
    ] == 0.002


def test_channel_volume_source_prefills_from_current_dataset(monkeypatch) -> None:
    captured: list[dict[str, str]] = []

    def fake_evidence(artifacts):
        captured.append(artifacts)
        return {
            "ready": False,
            "trajectory": "/raw/run.lammpstrj",
            "source": "workspace_link",
            "coordinate_length_unit": "angstrom",
            "index_state": "missing",
            "reason": "trajectory_index_not_ready",
            "message": "轨迹已关联；请建立轨迹帧索引。",
        }

    monkeypatch.setattr(svc, "channel_volume_evidence", fake_evidence)
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store"],
            changed="app-store.data",
            input_values={
                "app-store": {
                    "artifacts": {"species": "/data/run.species"},
                }
            },
            state_values={},
            output_id="rxn-channel-trajectory-path",
        ),
    )

    assert response.status_code == 200
    assert captured == [{"species": "/data/run.species"}]
    result = response.get_json()["response"]
    assert result["rxn-channel-trajectory-path"]["value"] == (
        "/raw/run.lammpstrj"
    )
    assert result["rxn-channel-coordinate-unit-confirm"]["value"] is True
    rendered = json.dumps(
        result["rxn-channel-volume-status"]["children"],
        ensure_ascii=False,
    )
    assert "Dataset Workspace 显式关联" in rendered
    assert "轨迹已关联" in rendered


def test_channel_time_conversion_saves_and_refreshes_tables(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_confirm(artifacts, value):
        captured["confirmation"] = (artifacts, value)
        return float(value)

    def fake_collect(artifacts, smiles, *, top, include_kinetics=True):
        captured["query"] = (artifacts, smiles, top, include_kinetics)
        return {
            "production_rows": [{"reaction_formulas": "H + OH -> H2O"}],
            "consumption_rows": [{"reaction_formulas": "H2O -> H + OH"}],
            "timing": {"message": "物理时间已刷新。"},
        }

    monkeypatch.setattr(svc, "confirm_channel_timestep_ps", fake_confirm)
    monkeypatch.setattr(svc, "collect_species_channels", fake_collect)
    client = create_app().server.test_client()
    artifacts = {"species": "/data/run.species"}
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["rxn-channel-timestep-save-btn"],
            changed="rxn-channel-timestep-save-btn.n_clicks",
            input_values={"rxn-channel-timestep-save-btn": 1},
            state_values={
                "rxn-channel-timestep-ps": 0.00025,
                "rxn-top": 12,
                "app-store": {
                    "artifacts": artifacts,
                    "selected_smiles": "O",
                },
            },
            output_id="rxn-channel-timestep-status",
        ),
    )

    assert response.status_code == 200
    assert captured == {
        "confirmation": (artifacts, 0.00025),
        "query": (artifacts, "O", 12, False),
    }
    result = response.get_json()["response"]
    assert result["rxn-production-grid"]["rowData"] == [
        {"reaction_formulas": "H + OH -> H2O"}
    ]
    assert result["rxn-consumption-grid"]["rowData"] == [
        {"reaction_formulas": "H2O -> H + OH"}
    ]
    assert result["rxn-channel-alert"]["children"] == "物理时间已刷新。"
    assert "0.00025 ps" in str(
        result["rxn-channel-timestep-status"]["children"]
    )


def test_species_channel_tables_export_loaded_rows_as_csv() -> None:
    client = create_app().server.test_client()
    production = {
        "reaction_formulas": "C + O -> CO",
        "reaction_smiles": "[C] + [O] -> [C][O]",
        "forward_tp": 10,
        "reverse_tp": 2,
        "net_tp": 8,
        "ratio_pct": 80.0,
        "event_count": 9,
        "event_frequency_per_ps": 4.5,
        "observation_time_ps": 2.0,
        "k_app": 0.5,
        "k_app_unit": "ps⁻¹",
        "k_app_ci95_low": 0.2,
        "k_app_ci95_high": 0.9,
        "kinetic_exposure": 18.0,
        "kinetic_exposure_unit": "molecule·ps",
        "kinetic_model": "stoichiometric_mass_action",
        "kinetics_status": "estimated",
    }
    consumption = {
        "reaction_formulas": "CO -> C + O",
        "reaction_smiles": "[C][O] -> [C] + [O]",
        "forward_tp": 7,
        "reverse_tp": 3,
        "net_tp": 4,
        "ratio_pct": 40.0,
    }
    input_ids = ["rxn-production-csv-btn", "rxn-consumption-csv-btn"]
    state_values = {
        "rxn-production-grid": [production],
        "rxn-consumption-grid": [consumption],
        "app-store": {
            "selected_formula": "CO",
            "selected_smiles": "[C][O]",
        },
    }

    production_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="rxn-production-csv-btn.n_clicks",
            input_values={
                "rxn-production-csv-btn": 1,
                "rxn-consumption-csv-btn": 0,
            },
            state_values=state_values,
            output_id="rxn-production-csv-download",
        ),
    )
    assert production_response.status_code == 200
    production_download = production_response.get_json()["response"][
        "rxn-production-csv-download"
    ]["data"]
    assert production_download["filename"] == "CO-production-channels.csv"
    production_rows = list(
        csv.DictReader(io.StringIO(production_download["content"]))
    )
    assert production_rows[0]["channel_role"] == "production"
    assert production_rows[0]["reaction_formula"] == "C + O -> CO"
    assert production_rows[0]["net_tp"] == "8"
    assert production_rows[0]["k_app"] == "0.5"
    assert production_rows[0]["kinetic_model"] == "stoichiometric_mass_action"

    consumption_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=input_ids,
            changed="rxn-consumption-csv-btn.n_clicks",
            input_values={
                "rxn-production-csv-btn": 1,
                "rxn-consumption-csv-btn": 1,
            },
            state_values=state_values,
            output_id="rxn-production-csv-download",
        ),
    )
    assert consumption_response.status_code == 200
    consumption_download = consumption_response.get_json()["response"][
        "rxn-consumption-csv-download"
    ]["data"]
    assert consumption_download["filename"] == "CO-consumption-channels.csv"
    consumption_rows = list(
        csv.DictReader(io.StringIO(consumption_download["content"]))
    )
    assert consumption_rows[0]["channel_role"] == "consumption"
    assert consumption_rows[0]["reaction_formula"] == "CO -> C + O"
    assert consumption_rows[0]["share_pct"] == "40.0"


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
                "rxn-production-grid",
                "rxn-consumption-grid",
            ],
            changed="rxn-consumption-grid.selectedRows",
            input_values={
                "rxn-production-grid.cellClicked": None,
                "rxn-consumption-grid.cellClicked": None,
                "rxn-production-grid.selectedRows": [],
                "rxn-consumption-grid.selectedRows": [row],
            },
            state_values={
                "rxn-production-grid.rowData": [],
                "rxn-consumption-grid.rowData": [row],
                "rxn-production-grid.virtualRowData": [],
                "rxn-consumption-grid.virtualRowData": [0],
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
        lambda *_args, **_kwargs: {
            "ok": True,
            "reaction_formulas": "CO -> C + O",
            "reaction_smiles": "[C][O] -> [C] + [O]",
            "reactants": [],
            "products": [],
            "kinetics": {
                "k_app_display": "0.5 ps⁻¹",
                "event_frequency_per_ps": 4.5,
            },
        },
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
    assert "表观 k" in str(rendered["rxn-channel-detail"]["children"])
    assert "0.5 ps⁻¹" in str(rendered["rxn-channel-detail"]["children"])

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
                "rxn-grid.selectedRows": [],
                "rxn-grid.rowData": [],
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


def test_clicking_channel_row_switches_selection_in_place() -> None:
    production = {
        "reaction_formulas": "C + O -> CO",
        "reaction_smiles": "[C] + [O] -> [C][O]",
    }
    consumption_rows = [
        {
            "reaction_formulas": "CO -> C + O",
            "reaction_smiles": "[C][O] -> [C] + [O]",
        },
        {
            "reaction_formulas": "CO + H -> C + OH",
            "reaction_smiles": "[C][O] + [H] -> [C] + [O][H]",
        },
    ]
    client = create_app().server.test_client()
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "rxn-production-grid",
                "rxn-consumption-grid",
                "rxn-production-grid",
                "rxn-consumption-grid",
            ],
            changed="rxn-consumption-grid.cellClicked",
            input_values={
                "rxn-production-grid.cellClicked": None,
                "rxn-consumption-grid.cellClicked": {
                    "rowId": ui.row_identity(consumption_rows[1]),
                    "rowIndex": 0,
                    "colId": "reaction_formulas",
                },
                "rxn-production-grid.selectedRows": [production],
                "rxn-consumption-grid.selectedRows": [],
            },
            state_values={
                "rxn-production-grid.rowData": [production],
                "rxn-consumption-grid.rowData": consumption_rows,
                "rxn-production-grid.virtualRowData": [0],
                "rxn-consumption-grid.virtualRowData": [1, 0],
            },
            output_id="rxn-channel-selection-store",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert result["rxn-channel-selection-store"]["data"] == {
        "lane": "consumption",
        "row": {**consumption_rows[1], "role_label": "消耗"},
    }
    assert result["rxn-production-grid"]["selectedRows"] == []
    assert result["rxn-consumption-grid"]["selectedRows"] == [consumption_rows[1]]


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
    assert returned["nav-reactions"]["className"] == "rs-top-nav-item active"
































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
        json=_load_dataset_callback_payload(
            client,
            candidate={
                "folder": "/new",
                "base": "/new/new.lammpstrj",
                "label": "new",
            },
            store={
                "dataset_id": "old",
                "selected_smiles": "[OLD]",
                "selected_formula": "OLD",
            },
            recent_records=[],
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
            json=_load_dataset_callback_payload(
                client,
                candidate={
                    "folder": str(folder),
                    "base": "run.lammpstrj",
                    "label": "run",
                },
                store=store,
                recent_records=[],
            ),
        )
        assert response.status_code == 200
        store = (
            response.get_json()["response"].get("app-store", {}).get("data")
            or store
        )
        loaded_stores.append(store)
        ids.append(store["dataset_id"])
        assert store["selected_smiles"] == ""
        assert store["dataset_id"] == dataset_id_for_source(
            str((folder / "run.lammpstrj").resolve(strict=False))
        )

    assert ids[0] == ids[1]
    assert ids[0] != ids[2]
    assert ids[0] == ids[3]





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
    payload = _callback_payload(client, input_ids=["element-distribution-search-btn"],
        changed="element-distribution-search-btn.n_clicks", input_values={"element-distribution-search-btn": 1},
        state_values=state_values, output_id="element-distribution-payload-store")

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


def test_reaction_endpoint_honors_hover_card_dimensions_and_hydrogen_toggle(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_render(reaction_smiles, *, width, height, show_h):
        captured.update(
            {
                "reaction_smiles": reaction_smiles,
                "width": width,
                "height": height,
                "show_h": show_h,
            }
        )
        return {"ok": True, "svg": "<svg></svg>", "message": ""}

    monkeypatch.setattr(svc, "render_reaction_svg", fake_render)
    client = create_app().server.test_client()
    response = client.get(
        "/api/reaction.svg?reaction_smiles=%5BH%5D%20%2B%20%5BCl%5D%20-%3E%20%5BH%5D%5BCl%5D"
        "&width=720&height=220&show_h=0"
    )

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert captured == {
        "reaction_smiles": "[H] + [Cl] -> [H][Cl]",
        "width": 720,
        "height": 220,
        "show_h": False,
    }


def test_reaction_channel_rows_build_lazy_complete_structure_hover_cards() -> None:
    client = create_app().server.test_client()
    production = {
        "reaction_formulas": "H + Cl -> HCl",
        "reaction_smiles": "[H] + [Cl] -> [H][Cl]",
    }
    consumption = {
        "reaction_formulas": "HCl -> H + Cl",
        "reaction_smiles": "[H][Cl] -> [H] + [Cl]",
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "rxn-production-grid",
                "rxn-consumption-grid",
                "rxn-channel-show-h",
            ],
            changed="rxn-production-grid.rowData",
            input_values={
                "rxn-production-grid.rowData": [production],
                "rxn-consumption-grid.rowData": [consumption],
                "rxn-channel-show-h.value": False,
            },
            state_values={},
            output_id="rxn-production-grid-previews",
        ),
    )

    assert response.status_code == 200
    body = response.get_json()["response"]
    production_tooltip = body["rxn-production-grid-previews"]["data"][0]
    assert production_tooltip["reaction_formulas"]["type"] == "markdown"
    assert "完整结构反应式" in production_tooltip["reaction_formulas"]["value"]
    assert "/api/reaction.svg?reaction_smiles=" in production_tooltip[
        "reaction_formulas"
    ]["value"]
    assert "show_h=0" in production_tooltip["reaction_formulas"]["value"]
    assert body["rxn-consumption-grid-previews"]["data"][0]


def test_reaction_search_hover_preview_uses_row_fields_without_channel_detail(
    monkeypatch,
) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("hover previews must not build selected-channel detail")

    monkeypatch.setattr(svc, "build_channel_structure_detail", fail_if_called)
    client = create_app().server.test_client()
    row = {
        "reaction_formulas": "H2 + O -> H + HO",
        "reaction_smiles": "[H][H] + [O] -> [H] + [H][O]",
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["rxn-grid", "rxn-structure-show-h"],
            changed="rxn-grid.rowData",
            input_values={
                "rxn-grid.rowData": [row],
                "rxn-structure-show-h.value": True,
            },
            state_values={},
            output_id="rxn-grid-previews",
        ),
    )

    assert response.status_code == 200
    tooltip = response.get_json()["response"]["rxn-grid-previews"]["data"][0]
    assert tooltip["reaction_formulas"]["type"] == "markdown"
    assert "show_h=1" in tooltip["reaction_formulas"]["value"]
    assert "%5BH%5D%5BH%5D" in tooltip["reaction_formulas"]["value"]




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


def test_dataset_picker_keeps_index_management_reachable() -> None:
    app = create_app()
    client = app.server.test_client()
    layout = client.get("/_dash-layout").get_json()

    index_button = _layout_node_by_id(layout, "data-browser-index-btn") or {}
    assert (index_button.get("props") or {}).get("children") == "当前数据与准备任务"

    response = client.post(
        "/_dash-update-component",
        json=_data_view_callback_payload(
            client,
            changed="data-browser-index-btn.n_clicks",
            values={"data-browser-index-btn": 1},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert "d-none" not in result["data-overview-view"]["className"]
    assert "d-none" in result["data-browser-view"]["className"]
    index_management = _layout_node_by_id(layout, "data-cache-management") or {}
    assert index_management.get("type") == "Section"
    assert "当前RNG 数据的分析索引" in json.dumps(
        index_management,
        ensure_ascii=False,
    )


def test_recent_dataset_click_opens_browser_without_pattern_id_error() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "data-overview-view.className" in item["output"]
        and "data-browser-view.className" in item["output"]
        and any(value["id"] == "data-pick-btn" for value in item["inputs"])
    )
    recent_input_id = next(
        item["id"]
        for item in dependency["inputs"]
        if "dir-browser-recent-entry" in str(item["id"])
    )
    changed = (
        json.dumps(
            {"index": 0, "type": "dir-browser-recent-entry"},
            separators=(",", ":"),
        )
        + ".n_clicks"
    )

    response = client.post(
        "/_dash-update-component",
        json=_data_view_callback_payload(
            client,
            changed=changed,
            values={recent_input_id: [1]},
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    assert "d-none" in result["data-overview-view"]["className"]
    assert "d-none" not in result["data-browser-view"]["className"]


def test_recent_dataset_mount_does_not_reopen_browser() -> None:
    app = create_app()
    client = app.server.test_client()
    dependency = next(
        item
        for item in client.get("/_dash-dependencies").get_json()
        if "data-overview-view.className" in item["output"]
        and "data-browser-view.className" in item["output"]
        and any(value["id"] == "data-pick-btn" for value in item["inputs"])
    )
    recent_input_id = next(
        item["id"]
        for item in dependency["inputs"]
        if "dir-browser-recent-entry" in str(item["id"])
    )
    changed = (
        json.dumps(
            {"index": 0, "type": "dir-browser-recent-entry"},
            separators=(",", ":"),
        )
        + ".n_clicks"
    )

    response = client.post(
        "/_dash-update-component",
        json=_data_view_callback_payload(
            client,
            changed=changed,
            values={recent_input_id: [0]},
        ),
    )

    assert response.status_code == 204




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
        if "dataset-context-commit.data" in item["output"]
    )
    outputs = [
        {
            "id": token.split(".")[0],
            "property": token.split(".")[1].split("@")[0],
        }
        for token in dependency["output"].strip(".").split("...")
    ]
    selected = candidate or {}
    folder = str(selected.get("folder") or "")
    base = str(selected.get("base") or "")
    base_path = Path(base)
    if base and not base_path.is_absolute():
        base = str((Path(folder) / base_path).resolve(strict=False))
    validation = {
        "folder": folder,
        "base": base,
        "label": str(selected.get("label") or Path(base).name),
        "dataset_id": dataset_id_for_source(base) if base else "",
        "source_revision": {
            "fingerprint": f"test:{base}",
            "artifacts": [],
        },
        "artifacts": {},
        "capabilities": {},
        "readiness": {},
        "ready_count": 0,
    }
    transaction = {
        "state": "succeeded",
        "request_id": f"test:{base}",
        "candidate": selected,
        "origin": {},
        "validation": validation,
    }
    state_values = {
        "app-store": store,
        "recent-datasets": recent_records,
    }
    return {
        "output": dependency["output"],
        "outputs": outputs,
        "changedPropIds": ["dataset-switch-transaction.data"],
        "inputs": [
            {
                "id": item["id"],
                "property": item["property"],
                "value": transaction,
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
            {"id": "data-prep-event-btn", "property": "children"},
            {"id": "data-prep-trajectory-btn", "property": "children"},
            {"id": "data-prep-composition-btn", "property": "children"},
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
    assert result["data-prep-refresh"]["disabled"] is True
    assert result["data-prep-trajectory-btn"]["className"] == (
        "rs-index-action is-ready"
    )
    assert result["data-prep-trajectory-btn"]["children"] == "重新构建"
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


def test_running_index_task_keeps_clear_action_clickable_and_explains_next_step(
    tmp_path, monkeypatch
) -> None:
    candidate = _discovered_candidate(tmp_path)

    def fake_preparation(folder: str, *, base: str = "") -> dict[str, Any]:
        assert (folder, base) == (candidate["folder"], candidate["base"])
        return {
            "events": {
                "state": "building",
                "index_size": 1024,
                "task": {"state": "running"},
            },
            "trajectory": {"state": "missing"},
            "composition": {"state": "missing"},
        }

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(svc, "dataset_preparation_status", fake_preparation)
    app = create_app()
    client = app.server.test_client()

    status_response = client.post(
        "/_dash-update-component",
        json=_preparation_status_callback_payload(
            client,
            candidate=candidate,
            store=candidate,
        ),
    )

    assert status_response.status_code == 200
    status = status_response.get_json()["response"]
    assert status["data-clear-event-btn"]["disabled"] is False
    assert status["data-prep-refresh"]["disabled"] is False

    click_response = client.post(
        "/_dash-update-component",
        json=_clear_confirmation_callback_payload(
            client,
            candidate=candidate,
            store=candidate,
            kind="event",
        ),
    )

    assert click_response.status_code == 200
    clicked = click_response.get_json()["response"]
    assert clicked["data-clear-confirm-modal"]["is_open"] is False
    feedback = json.dumps(
        clicked["data-prep-clear-alert"]["children"],
        ensure_ascii=False,
    )
    assert "先取消后台任务" in feedback


def test_terminal_preparation_task_record_can_be_dismissed(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_dismiss(folder: str, *, base: str, kind: str) -> dict[str, Any]:
        calls.append((folder, base, kind))
        return {"ok": True, "removed": True, "message": "任务记录已移除。"}

    monkeypatch.setattr(svc, "dismiss_dataset_preparation_task", fake_dismiss)
    client = create_app().server.test_client()
    cancel_pattern = (
        '{"capability":["ALL"],"dataset":["ALL"],'
        '"type":"preparation-task-cancel"}'
    )
    dismiss_pattern = (
        '{"capability":["ALL"],"dataset":["ALL"],'
        '"type":"preparation-task-dismiss"}'
    )
    dismiss_id = {
        "type": "preparation-task-dismiss",
        "dataset": "dataset-a",
        "capability": "event",
    }
    task = {
        "dataset_id": "dataset-a",
        "folder": "/data/run-a",
        "base": "/data/run-a/run.lammpstrj",
        "capability": "event",
        "state": "interrupted",
    }
    payload = _callback_payload(
        client,
        input_ids=["data-prep-cancel-btn", cancel_pattern, dismiss_pattern],
        changed=(
            f"{json.dumps(dismiss_id, sort_keys=True, separators=(',', ':'))}"
            ".n_clicks"
        ),
        input_values={
            "data-prep-cancel-btn": None,
            cancel_pattern: [0],
            dismiss_pattern: [1],
        },
        state_values={
            "dataset-browser-candidate": None,
            "app-store": {},
            "preparation-task-snapshot": [task],
        },
        output_id="data-prep-cancel-result",
    )
    for input_item in payload["inputs"]:
        if input_item["id"] == dismiss_pattern:
            input_item["id"] = dismiss_id

    response = client.post("/_dash-update-component", json=payload)

    assert response.status_code == 200
    assert calls == [
        ("/data/run-a", "/data/run-a/run.lammpstrj", "event")
    ]
    assert response.get_json()["response"]["data-prep-cancel-result"]["data"] == {
        "ok": True,
        "removed": True,
        "message": "任务记录已移除。",
    }


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
            store=candidate,
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
    assert result["app-store"]["data"]["base"] == candidate["base"]
    assert result["data-overview-view"]["className"] == "rs-data-view"
    assert "d-none" in result["data-browser-view"]["className"]
    assert result["dataset-browser-candidate"]["data"] is None
    assert result["topbar-folder"]["children"] == candidate["label"]
    assert str(tmp_path) not in result["topbar-folder"]["children"]
    assert "当前RNG 数据已切换为" in json.dumps(
        result["data-load-feedback"]["children"], ensure_ascii=False
    )
    assert result["recent-datasets"]["data"][0]["folder"] == str(tmp_path)
    assert result["recent-datasets"]["data"][0]["base"] == candidate["base"]


def test_browser_validation_commit_applies_selected_candidate_atomically(
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






























def test_dataset_browser_has_no_candidate_radio_asset() -> None:
    asset = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "dataset_browser.js"
    )

    assert not asset.exists()


def test_sidebar_navigation_has_an_immediate_browser_fallback() -> None:
    """Primary page switches must not wait behind analysis HTTP requests."""
    asset = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "navigation.js"
    )

    source = asset.read_text(encoding="utf-8")
    assert 'document.addEventListener("click"' in source
    assert 'closest("[id^=\\"nav-\\"]")' in source
    assert "page-${pageId}" in source
    assert "classList.add(\"active\")" in source
    assert 'setAttribute("aria-current", "page")' in source

    response = create_app().server.test_client().get("/assets/navigation.js")
    assert response.status_code == 200
    assert b"activatePage" in response.data


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
    assert "/missing" in payload
    assert "rs-browser-recent-path" in payload
    assert '"disabled": true' in payload
    assert "dir-browser-recent-remove" in payload
    assert "从最近RNG 数据中移除 old run" in payload


def test_starting_location_switcher_uses_friendly_names_and_full_paths(
    monkeypatch,
) -> None:
    roots = [
        Path.home().resolve(),
        Path("/media/huangchen"),
        Path("/data"),
        Path("/mnt"),
    ]
    monkeypatch.setattr(cb, "_allowed_roots", lambda: roots)

    rendered = cb._render_allowed_roots()
    payload = json.dumps(
        rendered,
        default=lambda value: value.to_plotly_json(),
        ensure_ascii=False,
    )

    for label in ("主目录", "外接数据", "共享数据", "其他挂载"):
        assert label in payload
    for root in roots:
        assert str(root) in payload
    assert "切换起始位置" in payload
    assert "可访问位置" not in payload
    assert "huangchen 2" not in payload


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
    assert 'body:has([data-dash-is-loading="true"]) .rs-global-operation-progress' not in css
    assert ".rs-analysis-progress.is-running::after" in css


def test_evolution_advanced_controls_have_visible_expand_affordances() -> None:
    css = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "app.css"
    ).read_text(encoding="utf-8")

    assert '.rs-evolution-advanced .accordion-button::after {' in css
    assert 'content: "＋  展开";' in css
    assert 'content: "−  收起";' in css
    assert ".rs-evolution-advanced .accordion-button:focus-visible" in css


def test_species_tooltips_can_overflow_their_grid_wrappers() -> None:
    client = create_app().server.test_client()
    layout = json.dumps(client.get("/_dash-layout").get_json())
    css = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "app.css"
    ).read_text(encoding="utf-8")

    assert layout.count("rs-grid-wrap rs-species-grid-wrap") == 2
    assert ".rs-species-grid-wrap {\n    overflow: visible;\n}" in css


def test_species_query_kind_uses_one_segment_border_layer() -> None:
    client = create_app().server.test_client()
    node = _layout_node_by_id(
        client.get("/_dash-layout").get_json(),
        "species-query-kind",
    )
    assert node is not None
    props = node["props"]
    css = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "app.css"
    ).read_text(encoding="utf-8")

    # Dash 4 applies labelStyle to the inner text span, while the option's
    # outer <label> is styled by CSS.  A border in both places produces the
    # nested rectangles seen in the species query toolbar.
    assert "labelStyle" not in props
    assert ".rs-segmented > .dash-options-list-option {" in css
    assert ".rs-segmented .dash-options-list-option-text {" in css


def test_compact_sidebar_override_follows_all_desktop_shell_rules() -> None:
    css = (
        Path(__file__).parents[1]
        / "scripts"
        / "webapp_dash"
        / "assets"
        / "app.css"
    ).read_text(encoding="utf-8")

    compact_marker = "/* Final compact-sidebar breakpoint contract"
    assert compact_marker in css
    compact_css = css[css.index(compact_marker) :]
    assert "@media (max-width: 1180px) and (min-width: 769px)" in compact_css
    assert "--rs-nav-width: 76px;" in compact_css
    assert "flex-direction: column;" in compact_css
    assert ".rs-sidebar-brand .rs-brand-copy" in compact_css
    assert "display: none;" in compact_css


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
    payload = _callback_payload(client, input_ids=["event-rxn-btn"],
        changed="event-rxn-btn.n_clicks", input_values={"event-rxn-btn": 1},
        state_values=state_values, output_id="event-grid-store")

    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    result = response.get_json()["response"]
    table_row = result["event-grid"]["rowData"][0]
    raw_row = result["event-grid-store"]["data"]["rows"][0]
    assert table_row["atom_ids"] == "1,2"
    assert table_row["id"] == raw_row["event_id"]
    assert raw_row["before_time_ps"] is None
    assert raw_row["after_time_ps"] is None
    assert table_row["before_time_ps"] is None
    assert table_row["after_time_ps"] is None
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
            changed="event-grid.selectedRows",
            input_values={
                "event-grid.selectedRows": [table_row],
            },
            state_values={
                "event-grid-store": result["event-grid-store"]["data"],
                "app-store": {
                    "dataset_id": "dataset-1",
                    "source_revision": {"fingerprint": "revision-1"},
                    "artifacts": {"trajectory": "/data/run.lammpstrj"},
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
    bookmark = selected["event-bookmark-store"]["data"]
    assert bookmark["dataset_id"] == "dataset-1"
    assert bookmark["source_revision"] == {"fingerprint": "revision-1"}
    assert bookmark["event_id"] == table_row["id"]

    restored_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store", "event-bookmark-store", "page-store"],
            changed="page-store.data",
            input_values={
                "app-store.data": {
                    "dataset_id": "dataset-1",
                    "source_revision": {"fingerprint": "revision-1"},
                    "artifacts": {
                        "reactionevent": str(reactionevent),
                        "molecules": str(molecules),
                        "trajectory": "/data/run.lammpstrj",
                    },
                },
                "event-bookmark-store.data": bookmark,
                "page-store.data": {"page": "events"},
            },
            state_values={"event-selected-store": None},
            output_id="event-bookmark-validation-store",
        ),
    )
    assert restored_response.status_code == 200
    restored = restored_response.get_json()["response"]
    assert restored["event-bookmark-validation-store"]["data"]["state"] == "restored"
    assert restored["event-selected-store"]["data"]["row"]["event_id"] == table_row["id"]
    assert "重新核验" in restored["event-bookmark-status"]["children"]

    rejected_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["app-store", "event-bookmark-store", "page-store"],
            changed="app-store.data",
            input_values={
                "app-store.data": {
                    "dataset_id": "dataset-1",
                    "source_revision": {"fingerprint": "revision-2"},
                    "artifacts": {
                        "reactionevent": str(reactionevent),
                        "molecules": str(molecules),
                    },
                },
                "event-bookmark-store.data": bookmark,
                "page-store.data": {"page": "events"},
            },
            state_values={"event-selected-store": None},
            output_id="event-bookmark-validation-store",
        ),
    )
    assert rejected_response.status_code == 200
    rejected = rejected_response.get_json()["response"]
    assert rejected["event-bookmark-validation-store"]["data"] == {
        "state": "rejected",
        "event_id": table_row["id"],
        "reason": "event_bookmark_revision_mismatch",
        "message": "数据来源修订已经变化，旧事件书签已拒绝恢复；请重新查询事件。",
    }
    assert "来源修订已经变化" in rejected["event-alert"]["children"]


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
            changed="event-grid.selectedRows",
            input_values={
                "event-grid.selectedRows": [{"id": row["event_id"]}],
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


def test_molecule_lineage_workspace_runs_from_a_concrete_participant(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    report = {
        "query": {"event_id": "rngevt-root"},
        "summary": {
            "molecule_node_count": 4,
            "event_count": 2,
            "recrossing_episode_count": 1,
            "aggregate_trend": "growth",
        },
        "truncations": [{"reason": "persistent_depth_limit"}],
        "views": {"persistent": {"elements": []}, "raw": {"elements": []}},
        "event_nodes": [],
    }

    def fake_lineage(artifacts, event_row, **kwargs):
        captured.update(artifacts=artifacts, event_row=event_row, **kwargs)
        return report

    monkeypatch.setattr(svc, "build_molecule_lineage_analysis", fake_lineage)
    client = create_app().server.test_client()
    row = {
        "event_id": "rngevt-root",
        "association_status": "matched",
        "reactant_participants": [
            {"species": "[C][H]", "atom_ids": [1, 2]}
        ],
        "product_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[H]", "atom_ids": [2]},
        ],
    }
    selected = {"row": row, "kind": "rng_event", "config": {}}
    viewer = {"event_id": "rngevt-root", "frames": [], "meta": {}}

    prepared_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-selected-store", "event-viewer-store"],
            changed="event-viewer-store.data",
            input_values={
                "event-selected-store": selected,
                "event-viewer-store": viewer,
            },
            state_values={},
            output_id="molecule-lineage-participant",
        ),
    )
    assert prepared_response.status_code == 200
    prepared = prepared_response.get_json()["response"]
    assert prepared["molecule-lineage-card"]["style"] == {"display": "block"}
    assert prepared["molecule-lineage-participant"]["value"] == "reactant:0"
    assert len(prepared["molecule-lineage-participant"]["options"]) == 3
    assert prepared["molecule-lineage-run-btn"]["disabled"] is False

    run_response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "molecule-lineage-run-btn",
                "molecule-lineage-continue-btn",
            ],
            changed="molecule-lineage-run-btn.n_clicks",
            input_values={
                "molecule-lineage-run-btn": 1,
                "molecule-lineage-continue-btn": None,
            },
            state_values={
                "molecule-lineage-participant": "reactant:0",
                "molecule-lineage-anchor-mode": "atom_ids",
                "molecule-lineage-anchor-value": "1",
                "molecule-lineage-depth-backward": 3,
                "molecule-lineage-depth-forward": 3,
                "molecule-lineage-node-limit": 100,
                "molecule-lineage-recross-window": 5,
                "molecule-lineage-branch": None,
                "molecule-lineage-continue-depth": 3,
                "molecule-lineage-continue-node-limit": 100,
                "event-selected-store": selected,
                "event-viewer-store": viewer,
                "app-store": {
                    "artifacts": {
                        "reactionevent": "/data/run.reactionevent.csv",
                        "molecules": "/data/run.molecules.csv",
                    }
                },
                "molecule-lineage-store": None,
            },
            output_id="molecule-lineage-store",
        ),
    )
    assert run_response.status_code == 200
    result = run_response.get_json()["response"]
    assert result["molecule-lineage-store"]["data"] == report
    assert result["molecule-lineage-results"]["style"] == {}
    assert result["molecule-lineage-json-btn"]["disabled"] is False
    assert captured["side"] == "reactant"
    assert captured["participant_index"] == 0
    assert captured["anchor_mode"] == "atom_ids"
    assert captured["anchor_atom_ids"] == [1]
    assert captured["recrossing_window"] == 5
    assert captured["dataset_id"] == ""
    assert captured["source_revision"] == {}


def test_lineage_branch_continuation_merges_and_preserves_result_on_failure(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    existing = {
        "query": {"event_id": "rngevt-root"},
        "summary": {"segment_count": 1},
        "branch_summaries": [],
    }
    merged = {
        "query": {"event_id": "rngevt-root"},
        "summary": {"segment_count": 2},
        "branch_summaries": [],
    }

    def fake_continue(artifacts, report, **kwargs):
        captured.update(artifacts=artifacts, report=report, **kwargs)
        return merged

    monkeypatch.setattr(
        svc, "continue_molecule_lineage_analysis", fake_continue
    )
    client = create_app().server.test_client()
    inputs = {
        "molecule-lineage-run-btn": 1,
        "molecule-lineage-continue-btn": 1,
    }
    states = {
        "molecule-lineage-recross-window": 5,
        "molecule-lineage-branch": "branch::stop-1",
        "molecule-lineage-continue-depth": 4,
        "molecule-lineage-continue-node-limit": 120,
        "app-store": {
            "dataset_id": "dataset-1",
            "source_revision": {"fingerprint": "revision-1"},
            "artifacts": {"reactionevent": "/data/run.reactionevent.csv"},
        },
        "molecule-lineage-store": existing,
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "molecule-lineage-run-btn",
                "molecule-lineage-continue-btn",
            ],
            changed="molecule-lineage-continue-btn.n_clicks",
            input_values=inputs,
            state_values=states,
            output_id="molecule-lineage-store",
        ),
    )

    assert response.status_code == 200
    payload = response.get_json()["response"]
    assert payload["molecule-lineage-store"]["data"] == merged
    assert captured["report"] == existing
    assert captured["branch_id"] == "branch::stop-1"
    assert captured["persistent_depth"] == 4
    assert captured["max_molecule_nodes"] == 120
    assert captured["dataset_id"] == "dataset-1"

    def fail_continue(*_args, **_kwargs):
        raise svc.ServiceError("来源修订已变化", reason="revision_changed")

    monkeypatch.setattr(
        svc, "continue_molecule_lineage_analysis", fail_continue
    )
    failed = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "molecule-lineage-run-btn",
                "molecule-lineage-continue-btn",
            ],
            changed="molecule-lineage-continue-btn.n_clicks",
            input_values=inputs,
            state_values=states,
            output_id="molecule-lineage-store",
        ),
    )

    assert failed.status_code == 200
    failed_payload = failed.get_json()["response"]
    assert "molecule-lineage-store" not in failed_payload
    assert "来源修订已变化" in json.dumps(failed_payload, ensure_ascii=False)


def test_lineage_workspace_uses_continue_branch_wording() -> None:
    client = create_app().server.test_client()
    layout = json.dumps(client.get("/_dash-layout").get_json(), ensure_ascii=False)

    assert "继续追踪分支" in layout
    assert "继续追踪所选分支" in layout
    assert "谱系续查" not in layout


def test_dft_geometry_card_prepares_exact_molecule_instances(monkeypatch) -> None:
    monkeypatch.setattr(svc, "load_coordinate_length_unit", lambda _path: "angstrom")
    client = create_app().server.test_client()
    row = {
        "event_id": "rngevt-dft",
        "association_status": "matched",
        "before_timestep": 10,
        "after_timestep": 20,
        "reactant_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[O]", "atom_ids": [2]},
        ],
        "product_participants": [
            {"species": "[C][O]", "atom_ids": [1, 2]},
        ],
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-selected-store", "event-viewer-store"],
            changed="event-viewer-store.data",
            input_values={
                "event-selected-store": {"row": row, "kind": "rng_event"},
                "event-viewer-store": {"event_id": "rngevt-dft"},
            },
            state_values={
                "app-store": {"artifacts": {"trajectory": "/data/run.lammpstrj"}}
            },
            output_id="event-dft-reactants",
        ),
    )

    assert response.status_code == 200
    payload = response.get_json()["response"]
    assert payload["event-dft-card"]["style"] == {"display": "block"}
    assert payload["event-dft-reactants"]["value"] == [0, 1]
    assert payload["event-dft-products"]["value"] == [0]
    assert payload["event-dft-unit-confirmation"]["value"] == ["angstrom"]
    assert "timestep 10" in payload["event-dft-alert"]["children"]


def test_dft_output_stems_and_electronic_states_follow_selection() -> None:
    row = {
        "reactant_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[O]", "atom_ids": [2, 4]},
        ],
        "product_participants": [
            {"species": "[C][O]", "atom_ids": [1, 2, 4]},
        ],
    }

    assert cb._dft_output_stems(row, [1], [0], "both") == [
        "reactants",
        "reactant-02-atoms-2-4",
        "products",
        "product-01-atoms-1-4",
    ]
    assert cb._dft_electronic_states_from_controls(
        [0],
        [{"type": "event-dft-charge", "stem": "reactants"}],
        [1],
        [{"type": "event-dft-multiplicity", "stem": "reactants"}],
    ) == {"reactants": (0, 1)}


def _indexed_qc_case(tmp_path, monkeypatch, *, molecular=True):
    from tests.test_reaction_readiness import _case
    from tests.test_timed_evidence import write_timeline
    from reacnet_scope.dataset_context import inspect_dataset_candidate

    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    artifacts, _event = _case(tmp_path, monkeypatch)
    timeline = write_timeline(
        Path(f"{artifacts['trajectory']}.timeline.h5"), schema_version="2",
        molecule_enabled=molecular,
    )
    EVENT_EVIDENCE_STORE.build(str(timeline))
    artifacts["timeline"] = str(timeline)
    reaction = Path(f"{artifacts['trajectory']}.reactionabcd")
    reaction.write_text("source marker\n", encoding="utf-8")
    artifacts["reaction"] = str(reaction)
    event_id = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=1
    )["rows"][0]["event_id"]
    event = EVENT_EVIDENCE_STORE.get_event(str(timeline), "", event_id)
    dataset = inspect_dataset_candidate(str(tmp_path), str(Path(artifacts["trajectory"])))
    return artifacts, event, {**dataset, "label": "rep-01", "artifacts": artifacts}


@pytest.mark.parametrize("message", ["ASE unavailable", "Trajectory index is stale"])
def test_dft_preview_turns_runtime_failures_into_alerts(
    tmp_path,
    monkeypatch,
    message: str,
) -> None:
    artifacts, event, dataset = _indexed_qc_case(tmp_path, monkeypatch)
    def fail(*_args, **_kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(svc, "build_dft_geometry_bundle", fail)
    client = create_app().server.test_client()
    selected = {"row": event, "kind": "rng_event"}
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-dft-request"],
            changed="event-dft-request.data",
            input_values={"event-dft-request": {
                "id": "runtime-preview",
                "controls": {
                    "reactant_indices": [0, 1],
                    "product_indices": [0],
                    "layout": "combined",
                    "unit_confirmation": [],
                    "isolated_cluster_confirmation": [],
                    "charge_values": [],
                    "charge_ids": [],
                    "multiplicity_values": [],
                    "multiplicity_ids": [],
                    "selected": selected,
                    "app_store": dataset,
                },
            }},
            state_values={},
            output_id="event-dft-response",
        ),
    )

    assert response.status_code == 200
    payload = response.get_json()["response"]["event-dft-response"]["data"]
    rendered = json.dumps(payload["validation"], ensure_ascii=False)
    assert message in rendered
    assert payload["disabled"] is True


def test_qc_preview_reloads_published_event_and_rejects_tampered_row(
    tmp_path, monkeypatch,
) -> None:
    artifacts, event, dataset = _indexed_qc_case(tmp_path, monkeypatch)
    original_get_event = EVENT_EVIDENCE_STORE.get_event
    resolved: list[tuple[str, str]] = []

    def trace_get_event(source, molecules, event_id):
        resolved.append((source, event_id))
        return original_get_event(source, molecules, event_id)

    monkeypatch.setattr(EVENT_EVIDENCE_STORE, "get_event", trace_get_event)
    controls = {
        "reactant_indices": [0, 1], "product_indices": [0],
        "layout": "combined", "unit_confirmation": ["angstrom"],
        "isolated_cluster_confirmation": ["confirmed"],
        "charge_values": [0, 0],
        "charge_ids": [{"type": "event-dft-charge", "stem": stem}
                       for stem in ("reactants", "products")],
        "multiplicity_values": [1, 1],
        "multiplicity_ids": [{"type": "event-dft-multiplicity", "stem": stem}
                             for stem in ("reactants", "products")],
        "selected": {"row": {**event, "reaction_key": "[O]->[C]"},
                     "kind": "rng_event"},
        "app_store": dataset,
    }
    client = create_app().server.test_client()
    preview = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["event-dft-request"], changed="event-dft-request.data",
        input_values={"event-dft-request": {"id": "tampered", "controls": controls}},
        state_values={}, output_id="event-dft-response",
    ))

    assert preview.status_code == 200
    response = preview.get_json()["response"]["event-dft-response"]["data"]
    assert resolved == [(artifacts["timeline"], event["event_id"])]
    assert response["payload"] is None
    assert response["disabled"] is True
    assert "已发布证据不一致" in json.dumps(response["validation"], ensure_ascii=False)


def test_qc_preview_rejects_stale_current_dataset_revision(tmp_path, monkeypatch):
    artifacts, event, dataset = _indexed_qc_case(tmp_path, monkeypatch)
    with Path(artifacts["reaction"]).open("a", encoding="utf-8") as stream:
        stream.write("changed after dataset validation\n")

    with pytest.raises(svc.ServiceError, match="来源已变化"):
        cb._build_dft_bundle_from_controls(
            selected={"row": event, "kind": "rng_event"},
            app_store=dataset,
            reactant_indices=[0, 1], product_indices=[0], layout="combined",
            unit_confirmation=["angstrom"],
            isolated_cluster_confirmation=["confirmed"],
            charge_values=[0, 0], charge_ids=[
                {"type": "event-dft-charge", "stem": stem}
                for stem in ("reactants", "products")
            ],
            multiplicity_values=[1, 1], multiplicity_ids=[
                {"type": "event-dft-multiplicity", "stem": stem}
                for stem in ("reactants", "products")
            ],
        )


@pytest.mark.parametrize("change", [
    None, "event", "dataset", "revision", "charge", "source", "candidate-source",
    "review-missing", "review-old", "review-current",
])
def test_qc_download_rechecks_current_preview_before_export(
    tmp_path, monkeypatch, change,
) -> None:
    from reacnet_scope.trajectory import save_type_element_map

    artifacts, event, dataset = _indexed_qc_case(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "save_coordinate_length_unit", lambda *_args: None)
    save_type_element_map(artifacts["trajectory"], {"1": "C", "2": "O"})
    client = create_app().server.test_client()
    charge_pattern = '{"stem":["ALL"],"type":"event-dft-charge"}'
    multiplicity_pattern = '{"stem":["ALL"],"type":"event-dft-multiplicity"}'
    charge_ids = [{"type": "event-dft-charge", "stem": stem}
                  for stem in ("reactants", "products")]
    multiplicity_ids = [{"type": "event-dft-multiplicity", "stem": stem}
                        for stem in ("reactants", "products")]
    controls = {
        "reactant_indices": [0, 1], "product_indices": [0],
        "layout": "combined", "unit_confirmation": ["angstrom"],
        "isolated_cluster_confirmation": ["confirmed"],
        "charge_values": [0, 0], "charge_ids": charge_ids,
        "multiplicity_values": [1, 3] if str(change).startswith("review-") else [1, 1],
        "multiplicity_ids": multiplicity_ids,
        "selected": {"row": event, "kind": "rng_event"},
        "app_store": dataset,
    }
    preview = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["event-dft-request"], changed="event-dft-request.data",
        input_values={"event-dft-request": {"id": "preview-1", "controls": controls}},
        state_values={}, output_id="event-dft-response",
    ))
    assert preview.status_code == 200
    pending = preview.get_json()["response"]["event-dft-response"]["data"]
    payload = pending["payload"]
    review_case = str(change).startswith("review-")
    assert payload["readiness_report"]["qc_handoff"]["status"] == (
        "review_required" if review_case else "ready"
    )
    confirmation = []
    if review_case:
        options_response = client.post("/_dash-update-component", json=_callback_payload(
            client, input_ids=["event-dft-store"], changed="event-dft-store.data",
            input_values={"event-dft-store": payload}, state_values={},
            output_id="event-dft-review-confirmation",
        ))
        options = options_response.get_json()["response"]["event-dft-review-confirmation"]["options"]
        if change == "review-current":
            confirmation = [options[0]["value"]]
        elif change == "review-old":
            confirmation = ["acknowledged", "review:another-report"]
    if change == "event":
        controls["selected"] = {"row": {**event, "event_id": "another-event"}}
    elif change == "dataset":
        controls["app_store"] = {**controls["app_store"], "dataset_id": "another-dataset"}
    elif change == "revision":
        controls["app_store"] = {**controls["app_store"], "source_revision": {"fingerprint": "new"}}
    elif change == "charge":
        controls["charge_values"] = [1, 1]
    elif change == "source":
        with Path(artifacts["trajectory"]).open("a") as stream:
            stream.write("\n")
    elif change == "candidate-source":
        with Path(artifacts["reaction"]).open("ab") as stream:
            stream.write(b"\n")
    state_values = {
        "event-dft-store": payload,
        "event-selected-store": controls["selected"],
        "event-dft-review-confirmation": confirmation,
        "app-store": controls["app_store"],
        "event-dft-reactants": controls["reactant_indices"],
        "event-dft-products": controls["product_indices"],
        "event-dft-layout": controls["layout"],
        "event-dft-unit-confirmation": controls["unit_confirmation"],
        "event-dft-isolated-cluster-confirmation": controls["isolated_cluster_confirmation"],
        f"{charge_pattern}.value": controls["charge_values"],
        f"{charge_pattern}.id": charge_ids,
        f"{multiplicity_pattern}.value": controls["multiplicity_values"],
        f"{multiplicity_pattern}.id": multiplicity_ids,
    }
    response = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["event-dft-download-btn"],
        changed="event-dft-download-btn.n_clicks",
        input_values={"event-dft-download-btn": 1},
        state_values=state_values, output_id="event-dft-download",
    ))
    assert response.status_code == 200
    result = response.get_json()["response"]
    if change and change != "review-current":
        assert "event-dft-download" not in result
        assert result["event-dft-download-btn"]["disabled"] is True
        assert "重新" in json.dumps(result["event-dft-validation"], ensure_ascii=False)
    else:
        from zipfile import ZipFile
        download = result["event-dft-download"]["data"]
        with ZipFile(io.BytesIO(base64.b64decode(download["content"]))) as archive:
            occurrence = json.loads(archive.read("occurrence.json"))
            readiness = json.loads(archive.read("reaction_readiness.json"))
            manifest = json.loads(archive.read("manifest.json"))
            assert occurrence["event_id"] == manifest["event"]["event_id"] == event["event_id"]
            assert readiness == payload["readiness_report"]
            assert readiness["subject"]["dataset_id"] == dataset["dataset_id"]
            assert readiness["subject"]["replicate"] == "rep-01"
            assert readiness["subject"]["atom_ids"] == {"reactant": [1, 2], "product": [1, 2]}
            assert manifest["cross_side_atom_ids_match"] is True
            assert manifest["source_signatures"] == payload["manifest"]["source_signatures"]


@pytest.mark.parametrize("blocked", [False, True])
def test_qc_http_needs_input_or_blocked_never_exports(tmp_path, monkeypatch, blocked):
    artifacts, event, dataset = _indexed_qc_case(
        tmp_path, monkeypatch, molecular=not blocked
    )
    controls = {
        "reactant_indices": [0, 1], "product_indices": [0],
        "layout": "combined", "unit_confirmation": [] if not blocked else ["angstrom"],
        "isolated_cluster_confirmation": ["confirmed"],
        "charge_values": [0, 0],
        "charge_ids": [{"type": "event-dft-charge", "stem": stem}
                       for stem in ("reactants", "products")],
        "multiplicity_values": [1, 1],
        "multiplicity_ids": [{"type": "event-dft-multiplicity", "stem": stem}
                             for stem in ("reactants", "products")],
        "selected": {"row": event, "kind": "rng_event"},
        "app_store": dataset,
    }
    client = create_app().server.test_client()
    preview = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["event-dft-request"], changed="event-dft-request.data",
        input_values={"event-dft-request": {"id": "blocked-preview", "controls": controls}},
        state_values={}, output_id="event-dft-response",
    ))
    assert preview.status_code == 200
    response = preview.get_json()["response"]["event-dft-response"]["data"]
    payload = response["payload"]
    assert payload["readiness_report"]["qc_handoff"]["status"] == (
        "blocked" if blocked else "needs_input"
    )
    assert response["disabled"] is True
    download = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["event-dft-download-btn"],
        changed="event-dft-download-btn.n_clicks",
        input_values={"event-dft-download-btn": 1},
        state_values={
            "event-dft-store": payload,
            "event-selected-store": controls["selected"],
            "event-dft-review-confirmation": [],
            "app-store": controls["app_store"],
            "event-dft-reactants": controls["reactant_indices"],
            "event-dft-products": controls["product_indices"],
            "event-dft-layout": controls["layout"],
            "event-dft-unit-confirmation": controls["unit_confirmation"],
            "event-dft-isolated-cluster-confirmation": controls["isolated_cluster_confirmation"],
            '{"stem":["ALL"],"type":"event-dft-charge"}.value': controls["charge_values"],
            '{"stem":["ALL"],"type":"event-dft-charge"}.id': controls["charge_ids"],
            '{"stem":["ALL"],"type":"event-dft-multiplicity"}.value': controls["multiplicity_values"],
            '{"stem":["ALL"],"type":"event-dft-multiplicity"}.id': controls["multiplicity_ids"],
        }, output_id="event-dft-download",
    ))
    assert download.status_code == 200
    result = download.get_json()["response"]
    assert "event-dft-download" not in result
    assert result["event-dft-download-btn"]["disabled"] is True


def test_lineage_event_click_updates_selection_and_drilldown_request() -> None:
    client = create_app().server.test_client()
    event = {
        "event_id": "rngevt-next",
        "node_id": "event::rngevt-next",
        "kind": "event",
        "association_status": "matched",
        "atom_id_list": [1, 3],
        "reactant_participants": [{"species": "[C]", "atom_ids": [1]}],
        "product_participants": [{"species": "[C][O]", "atom_ids": [1, 3]}],
    }
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=[
                "molecule-lineage-cytoscape",
                "molecule-lineage-event-grid",
            ],
            changed="molecule-lineage-cytoscape.tapNodeData",
            input_values={
                "molecule-lineage-cytoscape.tapNodeData": {
                    "kind": "event",
                    "event_id": "rngevt-next",
                },
                "molecule-lineage-event-grid.selectedRows": [],
            },
            state_values={
                "molecule-lineage-event-grid": [],
                "molecule-lineage-store": {"event_nodes": [event]},
                "event-selected-store": {
                    "config": {"before_frames": 2, "after_frames": 4}
                },
            },
            output_id="molecule-lineage-drilldown-store",
        ),
    )

    assert response.status_code == 200
    result = response.get_json()["response"]
    drilldown = result["molecule-lineage-drilldown-store"]["data"]
    assert drilldown["row"] == event
    assert drilldown["kind"] == "rng_event"
    assert drilldown["config"] == {"before_frames": 2, "after_frames": 4}


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
                "trajectory-open-selected-btn",
                "trajectory-refresh-btn",
                "event-type-map-clear-btn",
                "molecule-lineage-drilldown-store",
            ],
            changed="trajectory-refresh-btn.n_clicks",
            input_values={
                "event-extract-btn": 1,
                "trajectory-open-selected-btn": 0,
                "trajectory-refresh-btn": 1,
                "event-type-map-clear-btn": 0,
                "molecule-lineage-drilldown-store": None,
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
                "trajectory-open-selected-btn",
                "trajectory-refresh-btn",
                "event-type-map-clear-btn",
                "molecule-lineage-drilldown-store",
            ],
            changed="event-type-map-clear-btn.n_clicks",
            input_values={
                "event-extract-btn": 1,
                "trajectory-open-selected-btn": 0,
                "trajectory-refresh-btn": 1,
                "event-type-map-clear-btn": 1,
                "molecule-lineage-drilldown-store": None,
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


def test_changed_bond_distance_download_uses_current_viewer(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_distances(viewer):
        captured.append(viewer)
        return "source_timestep,distance\n10,1.5\n"

    monkeypatch.setattr(
        svc,
        "event_viewer_changed_bond_distances_csv",
        fake_distances,
    )
    client = create_app().server.test_client()
    viewer = {"event_id": "event-42", "frames": [{"frame": 10}]}
    response = client.post(
        "/_dash-update-component",
        json=_callback_payload(
            client,
            input_ids=["event-distances-csv-btn"],
            changed="event-distances-csv-btn.n_clicks",
            input_values={"event-distances-csv-btn": 1},
            state_values={"event-viewer-store": viewer},
            output_id="event-distances-csv-download",
        ),
    )

    assert response.status_code == 200
    download = response.get_json()["response"]["event-distances-csv-download"]["data"]
    assert captured == [viewer]
    assert download["filename"] == "event-42_changed_bond_distances.csv"
    assert download["content"] == "source_timestep,distance\n10,1.5\n"


def test_legacy_core_queries_are_available_through_dash_services(tmp_path) -> None:
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    reaction.write_text(
        "10 [C]+[O]->[C][O]\n4 [C][O]->[C]+[O]\n",
        encoding="utf-8",
    )
    artifacts = {"reaction": str(reaction), "species": "", "route": "", "trajectory": ""}

    assert svc.search_species(artifacts, "CO", kind="formula")["n_rows"] == 1
    assert len(svc.search_reactions_by_formula(artifacts, "C+O", "CO")["rows"]) == 1


def test_species_detail_reports_only_explicit_rng_identity_context(tmp_path) -> None:
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    timeline = tmp_path / "run.lammpstrj.timeline.h5"
    metadata = tmp_path / "rng_run.json"
    reaction.write_text("10 [C]+[O]->[C][O]\n", encoding="utf-8")
    timeline.touch()
    metadata.write_text(
        json.dumps(
            {
                "parameters": {
                    "miso": 1,
                    "runHMM": False,
                    "stepinterval": 2,
                },
                "source_revision": "rng-revision",
                "timestep_ps": 0.0001,
            }
        ),
        encoding="utf-8",
    )

    detail = svc.species_detail(
        {"reaction": str(reaction), "timeline": str(timeline)},
        "[C]",
    )
    identity = detail["identity_context"]
    fields = identity["processing"]["fields"]

    assert identity["identity_kind"] == "exact_rng_species"
    assert identity["rng_species_label"] == "[C]"
    assert identity["formula_role"] == "search_and_grouping_only"
    assert identity["molecular_evidence"]["kind"] == "native_timeline_hdf5"
    assert fields["miso"] == {"value": 1, "source": str(metadata)}
    assert fields["run_hmm"] == {"value": False, "source": str(metadata)}
    assert fields["step_interval"] == {"value": 2, "source": str(metadata)}
    assert fields["timestep_ps"] == {"value": 0.0001, "source": str(metadata)}
    assert fields["reacnetgenerator_version"] == {"value": None, "source": ""}


@pytest.mark.parametrize("store, expected", [
    ({}, "先选择RNG 数据"),
    ({"dataset_id": "test", "analysis_capabilities": {
        "reaction_search": {"state": "ready", "reason": "Reaction Evidence available"},
        "event_search": {"state": "needs-preparation", "reason": "Prepare event index"},
    }}, "Prepare event index"),
])
def test_workflow_launcher_contains_only_analysis_workspaces(store, expected):
    app = create_app()
    client = app.server.test_client()
    response = client.post("/_dash-update-component", json={
        "output": "data-overview-actions.children",
        "outputs": {"id": "data-overview-actions", "property": "children"},
        "inputs": [{"id": "app-store", "property": "data", "value": store}],
        "state": [], "changedPropIds": ["app-store.data"],
    })
    assert response.status_code == 200
    content = response.get_json()["response"]["data-overview-actions"]["children"]
    assert expected in json.dumps(content, ensure_ascii=False)
    ids = _component_pattern_ids(content)
    analysis_pages = set(WORKSPACE_PAGE_IDS) - {"data-management"}
    assert {item["page"] for item in ids} == analysis_pages
    assert len(ids) == len(analysis_pages)
    assert set(LEGACY_PAGE_REDIRECTS).isdisjoint(
        {item["page"] for item in ids}
    )


@pytest.mark.parametrize("state, label, reason", [
    ("ready", "可用", ""),
    ("needs-preparation", "需准备索引", "请准备丰度索引"),
    ("preparing", "准备中", "正在准备丰度索引"),
    ("stale", "需更新", "来源已变化"),
    ("missing-source", "缺少源数据", "缺少物种丰度源文件"),
])
def test_trend_workspace_reports_current_dataset_capabilities(state, label, reason):
    client = create_app().server.test_client()
    response = client.post("/_dash-update-component", json={
        "output": "data-overview-actions.children",
        "outputs": {"id": "data-overview-actions", "property": "children"},
        "inputs": [{"id": "app-store", "property": "data", "value": {
            "dataset_id": "single-source",
            "analysis_capabilities": {
                "species_abundance": {"state": state, "reason": reason},
                "element_distribution": {"state": state, "reason": reason},
            },
        }}],
        "state": [], "changedPropIds": ["app-store.data"],
    })
    assert response.status_code == 200
    content = response.get_json()["response"]["data-overview-actions"]["children"]
    cards = content["props"]["children"][0]["props"]["children"][1]["props"]["children"]
    card = next(card for card in cards if any(
        item.get("page") == "batch-compare" for item in _component_pattern_ids(card)
    ))
    heading = card["props"]["children"][0]["props"]["children"]
    assert heading[1]["props"]["children"] == label
    rendered = json.dumps(card, ensure_ascii=False)
    assert reason in rendered
    assert "状态待检查" not in rendered
    assert "请刷新RNG 数据状态" not in rendered


@pytest.mark.parametrize("payload, disabled", [(None, True), ({"curves": []}, True), ({"curves": [{"values": [1]}]}, False)])
def test_evolution_export_requires_result(payload, disabled):
    client = create_app().server.test_client()
    response = client.post("/_dash-update-component", json={
        "output": "evolution-csv-btn.disabled",
        "outputs": {"id": "evolution-csv-btn", "property": "disabled"},
        "inputs": [{"id": "evolution-payload-store", "property": "data", "value": payload}],
        "state": [], "changedPropIds": ["evolution-payload-store.data"],
    })
    assert response.status_code == 200
    assert response.get_json()["response"]["evolution-csv-btn"]["disabled"] is disabled


def test_evolution_dataset_reset_preserves_intentional_empty_state():
    resets = {(output.component_id, output.component_property): value
              for output, value in cb._dataset_bound_resets()}
    figure = resets[("evolution-graph", "figure")]
    assert not figure["data"]
    assert figure["layout"]["xaxis"]["visible"] is False
    assert figure["layout"]["annotations"]
    assert resets[("evolution-payload-store", "data")] is None


@pytest.mark.parametrize("mode", ["no-target", "no-curves", "failure"])
def test_evolution_empty_and_failed_queries_do_not_render_default_axes(monkeypatch, mode):
    def build(*args, **kwargs):
        if mode == "failure":
            raise svc.ServiceError("Test failure")
        return {"curves": [], "x_values": [], "meta": {}}

    monkeypatch.setattr(svc, "build_species_evolution", build)
    client = create_app().server.test_client()
    response = client.post("/_dash-update-component", json=_callback_payload(
        client, input_ids=["evolution-search-btn"],
        changed="evolution-search-btn.n_clicks", input_values={"evolution-search-btn": 1},
        state_values={"evolution-targets": "" if mode == "no-target" else "CO2",
                      "app-store": {"artifacts": {}}},
        output_id="evolution-payload-store",
    ))
    assert response.status_code == 200
    body = response.get_json()["response"]
    figure = body["evolution-graph"]["figure"]
    assert not figure["data"]
    assert figure["layout"]["xaxis"]["visible"] is False
    assert figure["layout"]["annotations"]
    if mode != "no-curves":
        assert body["evolution-payload-store"]["data"] is None
        assert body["evolution-alert"]["children"]


@pytest.mark.parametrize("mode, rates", [("stacked", False), ("compare", True), ("compare", False)])
def test_channel_display_controls_only_change_presentation(mode, rates):
    client = create_app().server.test_client()
    response = client.post("/_dash-update-component", json=_callback_payload(
        client,
        input_ids=["rxn-channel-layout", "rxn-channel-show-rates"],
        changed="rxn-channel-layout.value",
        input_values={"rxn-channel-layout": mode, "rxn-channel-show-rates": rates},
        state_values={}, output_id="rxn-channel-lanes",
    ))
    assert response.status_code == 200
    body = response.get_json()["response"]
    assert body["rxn-channel-lanes"]["className"] == f"rs-channel-lanes is-{mode}"
    for grid in ("rxn-production-grid", "rxn-consumption-grid"):
        assert set(body[grid]) == {"columnState"}
        assert [item["colId"] for item in body[grid]["columnState"] if item["hide"]] == [
            "event_frequency_per_ps", "k_app_display", "reverse_k_app_display",
        ]
