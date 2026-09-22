"""Presentation state shared by the five workspaces."""

from __future__ import annotations

import math
from typing import Any

from dash import ALL, Input, Output, State, ctx, no_update

from reacnet_scope import services as svc
from . import ui_components as ui


def species_query(query: Any, kind: Any, tolerance: Any) -> dict:
    text = str(query or "").strip()
    mode = str(kind or "auto")
    resolved = svc.detect_query_kind(text) if mode == "auto" else mode
    tol = 0.5 if resolved != "mass" or tolerance in (None, "") else float(tolerance)
    if resolved == "mass" and (not math.isfinite(tol) or tol < 0):
        raise ValueError("质量容差必须是大于或等于 0 的有限数值。")
    return {"query": text, "kind": mode, "resolved_kind": resolved, "mass_tolerance": tol}


def reaction_query(reactants, products, mode, top, share, metric, absolute, positive):
    return {"reactants": str(reactants or "").strip(), "products": str(products or "").strip(),
            "mode": mode or "exact", "top": top if top is not None else 50,
            "share": bool(share), "metric": metric or "net_tp",
            "absolute": bool(absolute), "positive": bool(positive)}


def register_callbacks(app):
    app.clientside_callback(
        """function(children, page, task, ids) {
            let active = (page || {}).page;
            if (active === 'reactions' && task === 'candidates') active = 'reaction-candidates';
            return [(ids || []).map(id => 'rs-task-tab nav-link' + (id.page === active ? ' active' : '')),
                    (ids || []).map(id => id.page === active ? 'page' : 'false')];
        }""",
        Output({"type": "workspace-open-page", "page": ALL}, "className"),
        Output({"type": "workspace-open-page", "page": ALL}, "aria-current"),
        Input("workspace-task-nav", "children"), Input("page-store", "data"),
        Input("reaction-task-tabs", "value"),
        State({"type": "workspace-open-page", "page": ALL}, "id"),
    )
    # One submission path: Enter updates the same button event as an explicit click.
    for button, fields in {
        "species-search-btn": ("species-query", "species-mass-tol"),
        "rxn-search-btn": ("rxn-reactants", "rxn-products"),
        "cp-start-find": ("cp-start-query",), "cp-target-find": ("cp-target-query",),
    }.items():
        app.clientside_callback(
            """function(...args) {
                const disabled = args[args.length - 2], count = args[args.length - 1];
                if (disabled || !args.slice(0, -2).some(Boolean)) return window.dash_clientside.no_update;
                return (count || 0) + 1;
            }""",
            Output(button, "n_clicks", allow_duplicate=True),
            *(Input(field, "n_submit") for field in fields),
            State(button, "disabled"), State(button, "n_clicks"), prevent_initial_call=True,
        )

    @app.callback(Output("reaction-task-tabs", "value", allow_duplicate=True),
                  Input({"type": "workspace-open-page", "page": ALL}, "n_clicks"),
                  State("reaction-task-tabs", "value"),
                  prevent_initial_call=True)
    def reaction_task(_clicks, current):
        trigger = ctx.triggered_id or {}
        if not isinstance(trigger, dict) or not any(_clicks or []):
            return no_update
        target = {"reactions": "direct", "reaction-candidates": "candidates"}.get(trigger.get("page"))
        return target if target and target != current else no_update

    @app.callback(Output("rxn-query-feedback", "children"),
                  Input("rxn-reactants", "value"), Input("rxn-products", "value"),
                  Input("rxn-mode", "value"), Input("rxn-top", "value"),
                  Input("rxn-with-share", "value"), Input("rxn-share-metric", "value"),
                  Input("rxn-share-abs", "value"), Input("rxn-share-positive", "value"),
                  Input("rxn-grid-store", "data"),
                  Input({"type": "dataset-bound-operation", "name": "reactions"}, "data"))
    def reaction_feedback(*args):
        result, running = args[-2] or {}, args[-1]
        if running:
            return ui.feedback("正在查询", "完成后显示本次结果。", state="running")
        if result.get("state") == "error":
            return ui.feedback("查询失败", result.get("message", ""), state="error")
        if result.get("query") and reaction_query(*args[:-2]) != result["query"]:
            return ui.feedback("条件已修改", "下方保留上次结果；点击查询更新。", state="dirty")
        if result.get("state") == "empty":
            return ui.feedback("没有匹配反应", "检查分子式，或尝试包含匹配。", state="empty")
        return []

    @app.callback(Output("species-mass-field", "style"),
                  Input("species-query-kind", "value"), Input("species-query", "value"))
    def mass_field(kind, query):
        resolved = svc.detect_query_kind(str(query or "")) if kind == "auto" else kind
        return {} if resolved == "mass" else {"display": "none"}

    @app.callback(Output("species-query-feedback", "children"),
                  Input("species-query", "value"), Input("species-query-kind", "value"),
                  Input("species-mass-tol", "value"), Input("species-grid-store", "data"),
                  Input({"type": "dataset-bound-operation", "name": "species"}, "data"))
    def species_feedback(query, kind, tolerance, result, running):
        if running:
            return ui.feedback("正在查询", "完成后显示本次结果。", state="running")
        try:
            current = species_query(query, kind, tolerance)
        except (TypeError, ValueError):
            return ui.feedback("检查质量容差", "请输入大于或等于 0 的数值。", state="error")
        result = result or {}
        if result.get("state") in {"error", "blocked"}:
            return ui.feedback("查询失败" if result["state"] == "error" else "暂时无法查询",
                               result.get("message", ""), state=result["state"])
        submitted = result.get("query")
        if submitted and current != submitted:
            return ui.feedback("条件已修改", f"下方仍是“{submitted['query'] or '全部物种'}”的结果；点击查询更新。", state="dirty")
        return []

    @app.callback(Output("species-workspace-stage", "data", allow_duplicate=True),
                  Input("species-detail-close", "n_clicks"), State("species-grid-store", "data"),
                  prevent_initial_call=True)
    def close_species_detail(clicks, result):
        if not clicks:
            return no_update
        return "structures" if (result or {}).get("query_kind") == "mass" else "results"

    @app.callback(Output("species-workspace-stage", "data", allow_duplicate=True),
                  Input("species-grid", "cellClicked"), Input("species-structure-grid", "cellClicked"),
                  State("species-grid-store", "data"),
                  State("species-structure-grid", "rowData"), prevent_initial_call=True)
    def reopen_detail(main_click, structure_click, result, structures):
        is_main = ctx.triggered_id == "species-grid"
        click = main_click if is_main else structure_click
        rows = (result or {}).get("rows", []) if is_main else structures or []
        if not click or not any(ui.row_identity(row) == click.get("rowId") for row in rows):
            return no_update
        return "structures" if is_main and (result or {}).get("query_kind") == "mass" else "detail"

    app.clientside_callback(
        """function(stage, detailStyle, panelStyle) {
            const detail = document.getElementById('species-detail-stage');
            if (detail) detail.setAttribute('aria-hidden', stage === 'detail' ? 'false' : 'true');
            requestAnimationFrame(() => {
                if (stage === 'detail') {
                    if (detailStyle?.display !== 'none' && panelStyle?.display !== 'none') {
                        document.getElementById('species-detail-close')?.focus({preventScroll: true});
                    }
                }
                else {
                    const grid = [...document.querySelectorAll('#species-results .ag-cell')].find(el => el.getClientRects().length);
                    if (grid) grid.focus({preventScroll: true});
                }
            });
            return window.dash_clientside.no_update;
        }""",
        Output("species-detail-focus", "data"), Input("species-workspace-stage", "data"),
        Input("species-detail-stage", "style"), Input("detail-panel", "style"),
        prevent_initial_call=True,
    )
