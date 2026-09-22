"""Shared, offline workbench components; scientific data stays in the services."""

from __future__ import annotations

import json
from typing import Any

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html


HIDDEN_RATE_FIELDS = {"event_frequency_per_ps", "k_app_display", "reverse_k_app_display"}


def row_identity(row: dict[str, Any]) -> str:
    """Identity within one result, independent of sort order and display position."""
    if "structure_count" in row and row.get("formula"):
        parts = ["formula", row["formula"]]
    elif row.get("folder") and row.get("name"):
        parts = ["source", row["folder"], row["name"]]
    else:
        parts = next(
            ([key, row[key]] for key in (
                "event_id", "id", "reaction_key", "reaction_smiles", "species_file",
                "smiles", "target_smiles", "formula", "name",
            ) if row.get(key) is not None and row.get(key) != ""),
            ["row", row],
        )
    return json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def selected_row(selected: list[dict] | None, rows: list[dict] | None) -> dict | None:
    """Re-resolve a selection against the current result, never a visible index."""
    if not selected or not rows or not isinstance(selected[0], dict):
        return None
    key = row_identity(selected[0])
    return next((dict(row) for row in rows if row_identity(row) == key), None)


def selected_ids(selected: list[dict] | None, field: str = "id") -> list[str]:
    return [str(row[field]) for row in selected or [] if isinstance(row, dict) and row.get(field) is not None]


def columns(definitions: list[dict]) -> list[dict]:
    """Normalize service column metadata into Community grid definitions."""
    result = []
    for item in definitions:
        field = str(item.get("field") or item.get("id") or "")
        if not field:
            continue
        col = {key: value for key, value in item.items() if key not in {"id", "name", "presentation"}}
        col.update(field=field, headerName=str(item.get("headerName") or item.get("name") or field),
                   tooltipField=field)
        if col.get("type") in {"numeric", "numericColumn"}:
            col.update(type="numericColumn", cellDataType="number")
        elif col.get("type") == "text":
            col.pop("type")
        if field in HIDDEN_RATE_FIELDS:
            col["hide"] = True
        if field in {"smiles", "reaction_smiles", "reaction_formulas", "formula"}:
            col.update(tooltipComponent="ScopeStructureTooltip")
        result.append(col)
    return result


def result_grid(
    grid_id: str, *, definitions: list[dict] | None = None,
    selection: str | None = "single", page_size: int | None = None,
    height: int = 400, sortable: bool = True, hidden: tuple[str, ...] = (),
) -> html.Div:
    defs = columns(definitions or [])
    for col in defs:
        if col["field"] in hidden:
            col["hide"] = True
    options: dict[str, Any] = {
        "theme": "themeQuartz", "rowHeight": 36, "headerHeight": 36,
        "animateRows": False, "tooltipShowDelay": 300, "tooltipHideDelay": 10000,
        "pagination": page_size is not None,
        "paginationPageSize": page_size or 50,
        "paginationPageSizeSelector": False,
        "suppressCellFocus": False,
        "localeText": {"noRowsToShow": "暂无结果", "loadingOoo": "正在读取…",
                       "page": "页", "of": "/", "to": "–", "more": "更多",
                       "firstPage": "第一页", "lastPage": "最后一页",
                       "nextPage": "下一页", "previousPage": "上一页"},
    }
    if selection:
        options["rowSelection"] = {
            "mode": "multiRow" if selection == "multi" else "singleRow",
            "enableClickSelection": True, "checkboxes": selection == "multi",
            "headerCheckbox": False,
        }
    return html.Div([
        dag.AgGrid(
            id=grid_id, rowData=[], columnDefs=defs, selectedRows=[],
            getRowId="scopeRowId(params.data)",
            defaultColDef={
                "resizable": True, "sortable": sortable, "filter": False,
                "minWidth": 100, "flex": 1,
            },
            dashGridOptions=options, enableEnterpriseModules=False,
            style={"height": f"{height}px", "width": "100%"},
            className="rs-result-grid",
        ),
        dcc.Store(id=f"{grid_id}-previews", data=[]),
        dcc.Store(id=f"{grid_id}-page-size", data=page_size or 50),
    ], className="rs-result-grid-container")


def feedback(title: str, message: str, *, state: str = "idle", action: Any = None) -> html.Div:
    return html.Div([
        html.Strong(title), html.Span(message), action,
    ], className=f"rs-feedback is-{state}", role="alert" if state == "error" else "status",
        **{"aria-live": "polite"})


def result_toolbar(summary: Any, actions: Any) -> html.Div:
    return html.Div([html.Div(summary, className="rs-result-summary"),
                     html.Div(actions, className="rs-result-actions")], className="rs-result-toolbar")


def query_context(store: dict | None) -> dict:
    current = store or {}
    return {key: current.get(key) for key in ("dataset_id", "source_revision", "artifacts")}


def guarded_query(app, *dependencies, **options):
    """Commit query results in the browser only while their request is current.

    The query button is the first Input and Current Dataset is the last State.
    This keeps expensive work in the existing callback/service while a tiny
    client commit prevents a late HTTP response from reviving an old dataset.
    """
    outputs = [item for item in dependencies if isinstance(item, Output)]
    inputs = [item for item in dependencies if not isinstance(item, Output)]
    button = next(item for item in inputs if isinstance(item, Input))
    response_id = f"{button.component_id}-response"

    def decorate(function):
        @app.callback(Output(response_id, "data"), *inputs, **options)
        def run(*args):
            return {"request": args[0], "context": query_context(args[-1]),
                    "values": function(*args)}

        app.clientside_callback(
            """function(response, request, store) {
                const skip = () => OUTPUTS.map(() => window.dash_clientside.no_update);
                if (!response || response.request !== request) return skip();
                const current = store || {};
                const context = Object.fromEntries(['dataset_id', 'source_revision', 'artifacts'].map(k => [k, current[k] ?? null]));
                const stable = x => x && typeof x === 'object' ? (Array.isArray(x) ? x.map(stable) : Object.fromEntries(Object.keys(x).sort().map(k => [k, stable(x[k])]))) : x;
                if (JSON.stringify(stable(context)) !== JSON.stringify(stable(response.context))) return skip();
                return response.values;
            }""".replace("OUTPUTS", json.dumps([str(output) for output in outputs])),
            *outputs, Input(response_id, "data"),
            State(button.component_id, button.component_property), State("app-store", "data"),
            prevent_initial_call=True,
        )
        return function
    return decorate


def register_grid_callbacks(app: Any) -> None:
    def visit(node):
        if isinstance(node, dag.AgGrid):
            yield node.id
        children = getattr(node, "children", None)
        for child in children if isinstance(children, (tuple, list)) else [children]:
            if child is not None:
                yield from visit(child)

    for grid_id in visit(app.layout):
        app.clientside_callback(
            """function(previews, size, rows, options) {
                const next = {...options, paginationPageSize: size || 50};
                const byId = {};
                (rows || []).forEach((row, i) => {
                    byId[window.dashAgGridFunctions.scopeRowId(row)] = (previews || [])[i] || {};
                });
                next.context = {...(options.context || {}), previews: byId};
                if (next.paginationPageSize === options.paginationPageSize &&
                    JSON.stringify(next.context) === JSON.stringify(options.context)) {
                    return window.dash_clientside.no_update;
                }
                return next;
            }""",
            Output(grid_id, "dashGridOptions"),
            Input(f"{grid_id}-previews", "data"), Input(f"{grid_id}-page-size", "data"),
            Input(grid_id, "rowData"), State(grid_id, "dashGridOptions"),
        )
