"""Startup callback contract: restore first, and skip first runs that change nothing.

Every callback that runs on page load makes the Dash renderer schedule, send and
apply one more update before the workbench is stable. These tests keep the
deferrals exact: a deferred callback's first run would have produced the layout
defaults, and none of its values can differ from those defaults at load time.
"""
from __future__ import annotations

import json
from typing import Any

import plotly.utils
import pytest
from dash import dcc

from scripts.webapp_dash import ui_components as ui
from scripts.webapp_dash.app import create_app

# Callbacks whose first run returned exactly the layout defaults. Later
# Input changes (including the restored ``app-store``) still trigger them.
DEFERRED_FIRST_RUNS = (
    "rxn-channel-timestep-ps.value",
    "..rxn-production-csv-btn.disabled...rxn-consumption-csv-btn.disabled..",
    "evolution-group-member-open-btn.disabled",
    "evolution-csv-btn.disabled",
    "event-type-map-editor.children",
    "event-dft-review-confirmation.options",
    "..molecule-lineage-anchor-value.disabled...molecule-lineage-anchor-value.placeholder..",
    "evolution-rank-open-btn.disabled",
    "species-compare-switch-btn.disabled",
    "cp-return-controls.style",
    "..cp-results.style...cp-search-settings.open...cp-search-heading.children..",
    "cp-graph-base-style.data",
    "..cp-graph.layout...cp-graph-layout-state.data..",
    "cp-graph-pick.data",
    "cp-from-species.disabled",
    "cp-back.disabled",
    "..cp-page-prev.disabled...cp-page-next.disabled..",
    "cp-report.data",
    "lx-state.data",
    "lx-focus.data",
    "import-add-folder.disabled",
    "import-list-page.data",
    "..evolution-target-add.children...evolution-target-cancel.style..",
    "..evolution-filter-required.options...evolution-filter-excluded.options"
    "...evolution-filter-element.options...evolution-group-filter-required.options"
    "...evolution-group-filter-excluded.options...evolution-group-filter-element.options..",
    "evolution-filter-grid-previews.data",
    "..evolution-filter-replace.disabled...evolution-filter-add.disabled..",
)


@pytest.fixture(scope="module")
def app() -> Any:
    return create_app()


def _components(layout: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def visit(component: Any) -> None:
        if isinstance(getattr(component, "id", None), str):
            found[component.id] = component
        children = getattr(component, "children", None)
        for child in children if isinstance(children, (list, tuple)) else [children]:
            if hasattr(child, "to_plotly_json"):
                visit(child)

    visit(layout)
    return found


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=plotly.utils.PlotlyJSONEncoder))


def _layout_value(components: dict[str, Any], component_id: str, prop: str) -> Any:
    component = components[component_id]
    return _plain(getattr(component, prop, None)) if prop in component._prop_names else None


def _browser_restored(component: Any) -> bool:
    """Values a page load restores without a change event Dash would see."""
    return bool(getattr(component, "persistence", None)) or (
        isinstance(component, dcc.Store)
        and getattr(component, "storage_type", "memory") != "memory"
    )


def _dependency(app: Any, output: str) -> dict[str, Any]:
    dependencies = app.server.test_client().get("/_dash-dependencies").get_json()
    return next(item for item in dependencies if item["output"].split("@")[0] == output)


def test_session_restore_runs_with_the_initial_callbacks(app) -> None:
    components = _components(app.layout)
    # No delayed tick: listeners of app-store wait for the restored context
    # instead of running once with the placeholder and again after restore.
    assert components["dataset-session-restore"].disabled is True
    for marker in ("dataset-restore-result.data", "page-store.data"):
        dependency = next(
            item
            for item in app.server.test_client().get("/_dash-dependencies").get_json()
            if marker in item["output"]
            and [i["id"] for i in item["inputs"]] == ["dataset-session-restore"]
        )
        # Dash publishes ``initial_duplicate`` as "runs on page load".
        assert dependency["prevent_initial_call"] is False


@pytest.mark.parametrize("output", DEFERRED_FIRST_RUNS)
def test_deferred_first_run_would_not_change_the_page(app, output) -> None:
    components = _components(app.layout)
    dependency = _dependency(app, output)
    assert dependency["prevent_initial_call"] is True
    reads = dependency["inputs"] + dependency["state"]
    restored = [item["id"] for item in reads if _browser_restored(components[item["id"]])]
    assert not restored, f"{output} reads browser-restored values {restored}"

    def supplied(items):
        return [dict(item, value=_layout_value(components, item["id"], item["property"]))
                for item in items]

    outputs = [
        {"id": part.rsplit(".", 1)[0], "property": part.rsplit(".", 1)[1]}
        for part in (output[2:-2].split("...") if output.startswith("..") else [output])
    ]
    response = app.server.test_client().post("/_dash-update-component", json={
        "output": dependency["output"],
        "outputs": outputs if output.startswith("..") else outputs[0],
        "inputs": supplied(dependency["inputs"]),
        "state": supplied(dependency["state"]),
        "changedPropIds": [],
    })
    if response.status_code == 204:
        return
    assert response.status_code == 200
    for component_id, props in response.get_json()["response"].items():
        for prop, value in props.items():
            assert _plain(value) == _layout_value(components, component_id, prop), (
                f"{output}: {component_id}.{prop} differs from its layout default"
            )


def test_grid_options_start_with_the_preview_sync_result() -> None:
    grid = ui.result_grid("probe-grid", page_size=20).children[0]
    options = grid.dashGridOptions
    # The clientside preview sync derives exactly this from an empty grid.
    assert options["paginationPageSize"] == 20
    assert options["context"] == {"previews": {}}
