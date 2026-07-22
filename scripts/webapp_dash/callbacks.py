"""Dash callback registration for ReacNet Scope WebUI V1.

All callbacks are registered in ``register_callbacks(app)``.  Each callback
delegates to ``scripts.webapp_dash.services`` for data operations and never
re-implements analysis logic.
"""

from __future__ import annotations

import re
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, html, no_update
from dash.exceptions import PreventUpdate

from scripts.webapp_dash import services as svc


PAGE_IDS = ["species", "transitions", "reactions", "intermediate", "evolution", "carbon", "events", "network", "literature", "batch-compare"]
PAGE_LABELS = {
    "species": "物种检索",
    "transitions": "转化关系",
    "reactions": "反应式检索",
    "intermediate": "中间体筛选",
    "evolution": "时间演化",
    "carbon": "碳数演化",
    "events": "事件证据",
    "network": "观察网络",
    "literature": "文献验证",
    "batch-compare": "批量对比",
}
PAGE_DESCRIPTIONS = {
    "species": "按分子式、SMILES 或精确质量定位物种，并查看结构与通量。",
    "transitions": "围绕已选物种查看生成、消耗及净通量关系。",
    "reactions": "按反应物和产物组合检索反应，比较正反向通量。",
    "intermediate": "基于丰度、寿命与通量条件筛选关键中间体。",
    "evolution": "绘制目标物种随帧数或模拟时间变化的丰度曲线。",
    "carbon": "聚合不同碳数区间，观察体系碳骨架的演化过程。",
    "events": "定位物种和反应事件，并抽取对应的轨迹证据。",
    "network": "从观测表构建可交互的全局物种-反应网络。",
    "literature": "将文献反应式与当前网络逐条比对并生成证据矩阵。",
    "batch-compare": "扫描多组模拟结果，对比反应通量与检出率。",
}
PAGE_DATA_REQUIREMENTS = {
    "species": ("reaction", "reactionabcd"),
    "transitions": ("reaction", "reactionabcd"),
    "reactions": ("reaction", "reactionabcd"),
    "intermediate": ("species", ".species"),
    "evolution": ("species", ".species"),
    "carbon": ("species", ".species"),
    "events": ("route", ".route"),
    "network": ("table", ".lammpstrj.table"),
    "literature": ("reaction", "reactionabcd"),
}


def initial_store() -> dict[str, Any]:
    return {
        "folder": "",
        "base": "",
        "label": "未选择",
        "ready_count": 0,
        "capabilities": {},
        "artifacts": {},
        "selected_smiles": "",
        "selected_formula": "",
    }


_EVENT_GROUP_STYLE = {
    "core": ("反应核", "#dc2626"),
    "reactant": ("反应物原子", "#2563eb"),
    "product": ("产物原子", "#16a34a"),
    "shared": ("前后共有原子", "#7c3aed"),
    "context": ("局部上下文", "#94a3b8"),
}


def _event_frame_figure(viewer: dict[str, Any], frame_index: int, scope: str, *, compact: bool = False):
    """Render one local trajectory frame as a grouped Plotly 3D scene."""
    import plotly.graph_objects as go

    frames = viewer.get("frames") or []
    if not frames:
        return go.Figure()
    safe_index = max(0, min(int(frame_index or 0), len(frames) - 1))
    frame = frames[safe_index]
    atoms = list(frame.get("atoms") or [])
    if scope == "core":
        core_atoms = [atom for atom in atoms if atom.get("group") == "core"]
        atoms = core_atoms or atoms

    fig = go.Figure()
    for group, (label, color) in _EVENT_GROUP_STYLE.items():
        group_atoms = [atom for atom in atoms if atom.get("group") == group]
        if not group_atoms:
            continue
        symbols = [atom.get("element") or f"T{atom.get('type') or '?'}" for atom in group_atoms]
        fig.add_trace(
            go.Scatter3d(
                x=[atom.get("x") for atom in group_atoms],
                y=[atom.get("y") for atom in group_atoms],
                z=[atom.get("z") for atom in group_atoms],
                mode="markers",
                name=label,
                marker={"size": 5 if compact else 7, "color": color, "opacity": 0.92, "line": {"color": "#ffffff", "width": 0.6}},
                text=[f"Atom {atom.get('id')} · {symbol}" for atom, symbol in zip(group_atoms, symbols)],
                hovertemplate="%{text}<br>x=%{x:.3f}, y=%{y:.3f}, z=%{z:.3f}<extra>" + label + "</extra>",
            )
        )

    title = f"Frame {frame.get('frame')}"
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "font": {"size": 13 if compact else 15}, "x": 0.01, "xanchor": "left"},
        height=220 if compact else 460,
        margin={"l": 0, "r": 0, "t": 30 if compact else 36, "b": 0},
        showlegend=not compact,
        legend={"orientation": "h", "y": -0.04, "x": 0},
        scene={
            "aspectmode": "data",
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
            "zaxis": {"visible": False},
            "bgcolor": "#fbfcfe",
            "camera": {"eye": {"x": 1.45, "y": 1.45, "z": 1.05}},
        },
    )
    return fig


def _event_selection_summary(selected: dict[str, Any]) -> Any:
    row = selected.get("row") or {}
    kind = selected.get("kind") or ""
    is_candidate = row.get("event_class") == "相关候选"
    label = "反应事件" if kind == "reaction" else "物种事件"
    anchor = row.get("anchor_frame", "-")
    details = [f"{label}", f"锚定帧 {anchor}"]
    if row.get("event_id"):
        details.append(str(row["event_id"]))
    if is_candidate:
        details.append("相关候选：提取后请人工核查")
    return html.Div(
        [
            html.Span("已选", className="rs-selection-label"),
            html.Span(" · ".join(details), className="rs-selection-main"),
            html.Span(str(row.get("reaction_smiles") or row.get("matched_smiles_at_anchor") or ""), className="rs-selection-query"),
        ],
        className="rs-selection-line",
    )


def _transition_selection_summary(row: dict[str, Any]) -> Any:
    """Render the selected transition before it is handed to event evidence."""
    return html.Div(
        [
            html.Span("已选反应", className="rs-selection-label"),
            html.Span(str(row.get("reaction_formulas") or ""), className="rs-selection-main"),
            html.Span(str(row.get("reaction_smiles") or ""), className="rs-selection-query"),
        ],
        className="rs-selection-line",
    )


def register_callbacks(app: Any) -> None:
    # ── Navigation ──────────────────────────────────────────────────

    @app.callback(
        Output("page-species", "className"),
        Output("page-transitions", "className"),
        Output("page-reactions", "className"),
        Output("page-intermediate", "className"),
        Output("page-evolution", "className"),
        Output("page-carbon", "className"),
        Output("page-events", "className"),
        Output("page-network", "className"),
        Output("page-literature", "className"),
        Output("page-batch-compare", "className"),
        Output("nav-species", "className"),
        Output("nav-transitions", "className"),
        Output("nav-reactions", "className"),
        Output("nav-intermediate", "className"),
        Output("nav-evolution", "className"),
        Output("nav-carbon", "className"),
        Output("nav-events", "className"),
        Output("nav-network", "className"),
        Output("nav-literature", "className"),
        Output("nav-batch-compare", "className"),
        Output("page-store", "data"),
        Output("page-title", "children"),
        Output("page-description", "children"),
        Output("app-body", "className"),
        Input("nav-species", "n_clicks"),
        Input("nav-transitions", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        Input("nav-intermediate", "n_clicks"),
        Input("nav-evolution", "n_clicks"),
        Input("nav-carbon", "n_clicks"),
        Input("nav-events", "n_clicks"),
        Input("nav-network", "n_clicks"),
        Input("nav-literature", "n_clicks"),
        Input("nav-batch-compare", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("transitions-to-event-btn", "n_clicks"),
        State("page-store", "data"),
    )
    def _navigate(*_args):
        triggered_id = ctx.triggered_id
        stored_page = (_args[-1] or {}).get("page") if _args else None
        if triggered_id in {"species-to-event-btn", "rxn-to-event-btn", "transitions-to-event-btn"}:
            page_id = "events"
        else:
            page_id = triggered_id.removeprefix("nav-") if triggered_id else stored_page
        if page_id not in PAGE_IDS:
            page_id = "species"
        page_classes = {
            pid: "rs-page active" if pid == page_id else "rs-page"
            for pid in PAGE_IDS
        }
        nav_classes = {
            pid: f"rs-nav-item{' active' if pid == page_id else ''}"
            for pid in PAGE_IDS
        }
        shell_class = "rs-body"
        return (
            tuple(page_classes[pid] for pid in PAGE_IDS)
            + tuple(nav_classes[pid] for pid in PAGE_IDS)
            + ({"page": page_id}, PAGE_LABELS[page_id], PAGE_DESCRIPTIONS[page_id], shell_class)
        )

    @app.callback(
        Output("page-data-status", "children"),
        Output("page-data-status", "className"),
        Input("page-store", "data"),
        Input("app-store", "data"),
    )
    def _update_page_data_status(page_store, app_store):
        page_id = (page_store or {}).get("page") or "species"
        if page_id == "batch-compare":
            return "独立目录分析", "rs-page-status is-independent"
        artifact_key, artifact_label = PAGE_DATA_REQUIREMENTS.get(page_id, ("", ""))
        artifacts = (app_store or {}).get("artifacts") or {}
        if artifact_key and artifacts.get(artifact_key):
            return f"{artifact_label} 已就绪", "rs-page-status is-ready"
        return f"需要 {artifact_label or '数据文件'}", "rs-page-status is-blocked"

    @app.callback(
        Output("transitions-search-btn", "disabled"),
        Output("rxn-search-btn", "disabled"),
        Output("inter-search-btn", "disabled"),
        Output("evolution-search-btn", "disabled"),
        Output("carbon-search-btn", "disabled"),
        Output("event-species-btn", "disabled"),
        Output("event-rxn-btn", "disabled"),
        Output("event-extract-btn", "disabled"),
        Output("event-dedup-btn", "disabled"),
        Output("network-search-btn", "disabled"),
        Output("literature-verify-btn", "disabled"),
        Input("app-store", "data"),
    )
    def _update_data_dependent_actions(app_store):
        artifacts = (app_store or {}).get("artifacts") or {}
        no_reaction = not bool(artifacts.get("reaction"))
        no_species = not bool(artifacts.get("species"))
        no_events = not any(artifacts.get(key) for key in ("route", "trajectory", "species"))
        no_route = not bool(artifacts.get("route"))
        no_table = not bool(artifacts.get("table"))
        return (
            no_reaction,
            no_reaction,
            no_species,
            no_species,
            no_species,
            no_events,
            no_events,
            no_route,
            no_route,
            no_table,
            no_reaction,
        )

    # ── Data modal open / close ─────────────────────────────────────

    @app.callback(
        Output("data-modal", "is_open"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("data-close-btn", "n_clicks"),
        Input("data-apply-btn", "n_clicks"),
        State("data-modal", "is_open"),
        State("data-folder-input", "value"),
        State("data-rungroup", "value"),
        prevent_initial_call=True,
    )
    def _toggle_data_modal(topbar_open, species_open, close_btn, apply_btn, is_open, folder_text, selected_base):
        triggered = ctx.triggered_id
        if triggered in ("open-data-modal", "species-open-data-modal"):
            return True
        if triggered == "data-close-btn":
            return False
        if triggered == "data-apply-btn":
            try:
                status = svc.scan_dataset((folder_text or "").strip(), base=(selected_base or "").strip())
            except Exception:
                return True
            return not bool(svc.dataset_ready_count(status))
        return is_open

    @app.callback(
        Output("data-folder-input", "value"),
        Output("data-rungroup", "options"),
        Output("data-scan-status", "children"),
        Output("data-artifacts", "children"),
        Input("data-scan-btn", "n_clicks"),
        State("data-folder-input", "value"),
        prevent_initial_call=True,
    )
    def _scan_data_folder(n_clicks, folder_text):
        if n_clicks is None:
            raise PreventUpdate
        try:
            status = svc.scan_dataset(folder_text or "")
        except svc.ServiceError as exc:
            artifact_html = _render_artifacts({})
            return (
                no_update,
                [],
                f"扫描失败: {exc.message}",
                artifact_html,
            )
        except Exception as exc:
            artifact_html = _render_artifacts({})
            return (no_update, [], f"扫描失败: {exc}", artifact_html)

        candidates = svc.candidates_from_status(status)
        options = [
            {"label": f"{c.get('label') or c.get('base')} ({c.get('score', 0)}/5)", "value": c.get("base", "")}
            for c in candidates
        ]
        artifact_html = _render_artifacts(svc.artifacts_from_status(status))
        ready = svc.dataset_ready_count(status)
        label = svc.dataset_label(status)
        scan_msg = f"扫描完成 — {label}，就绪 {ready}/5"
        return (no_update, options, scan_msg, artifact_html)

    @app.callback(
        Output("data-folder-input", "value", allow_duplicate=True),
        Input("data-pick-btn", "n_clicks"),
        State("data-folder-input", "value"),
        prevent_initial_call=True,
    )
    def _pick_folder(n_clicks, current_folder):
        if n_clicks is None:
            raise PreventUpdate
        try:
            result = svc.pick_folder_macos(current_folder or "")
        except svc.ServiceError as exc:
            return no_update
        except Exception:
            return no_update
        if result.get("canceled"):
            return no_update
        path = result.get("path") or ""
        if path and path != current_folder:
            return path
        return no_update

    @app.callback(
        Output("app-store", "data"),
        Output("topbar-folder", "children"),
        Output("topbar-rungroup", "children"),
        Output("topbar-status", "children"),
        Output("topbar-status", "className"),
        Input("data-apply-btn", "n_clicks"),
        State("data-folder-input", "value"),
        State("data-rungroup", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _apply_data_folder(n_clicks, folder_text, selected_base, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        folder = (folder_text or "").strip()
        base = (selected_base or "").strip()
        if not folder:
            return (
                {**store, "folder": "", "base": "", "label": "未选择", "ready_count": 0, "artifacts": {}, "capabilities": {}},
                "未选择",
                "未选择",
                "未加载数据",
                "rs-badge rs-bad",
            )
        try:
            status = svc.scan_dataset(folder, base=base)
        except Exception:
            return (
                {**store, "folder": folder, "base": base, "label": folder, "ready_count": 0, "artifacts": {}, "capabilities": {}},
                folder,
                base or folder,
                "加载失败",
                "rs-badge rs-bad",
            )
        dataset = status.get("dataset", {}) or {}
        artifacts = svc.artifacts_from_status(status)
        capabilities = svc.dataset_capabilities(status)
        ready = svc.dataset_ready_count(status)
        label = svc.dataset_label(status)
        selected_base_new = dataset.get("selected_base") or base
        new_store = {
            **store,
            "folder": folder,
            "base": selected_base_new,
            "label": label,
            "ready_count": ready,
            "capabilities": capabilities,
            "artifacts": artifacts,
        }
        status_class = "rs-badge" if ready >= 3 else ("rs-badge rs-bad" if ready <= 1 else "rs-badge")
        return (
            new_store,
            folder,
            label,
            f"就绪 {ready}/5",
            status_class,
        )

    # ── Species search ──────────────────────────────────────────────

    @app.callback(
        Output("species-grid", "data"),
        Output("species-grid", "columns"),
        Output("species-alert", "children"),
        Output("species-grid-store", "data"),
        Output("species-grid", "selected_rows"),
        Input("species-search-btn", "n_clicks"),
        State("species-query", "value"),
        State("species-query-kind", "value"),
        State("species-mass-tol", "value"),
        State("species-mass-mode", "value"),
        State("species-top", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_species(n_clicks, query, kind, mass_tol, mass_mode, top, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        if not artifacts.get("reaction"):
            return [], _species_columns(), '请先在「管理数据」中导入包含 reactionabcd 的数据目录。', {"rows": []}, []
        try:
            result = svc.search_species(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=float(mass_tol or 0.5),
                mass_mode=mass_mode or "exact",
                top=int(top or 0),
            )
        except svc.ServiceError as exc:
            return [], _species_columns(), str(exc.message), {"rows": []}, []

        rows = result.get("rows") or []
        message = f"找到 {len(rows)} 条匹配物种" if rows else "未找到匹配物种；可以放宽质量容差或切换查询类型。"
        return (
            rows,
            _species_columns(result.get("query_kind")),
            message,
            {
                "rows": rows,
                "query_kind": result.get("query_kind"),
                "searched": True,
                "message": message,
            },
            [],
        )

    @app.callback(
        Output("species-empty-copy", "children"),
        Output("species-empty-state", "style"),
        Output("species-results", "style"),
        Output("species-open-data-modal", "style"),
        Output("species-search-btn", "disabled"),
        Output("species-csv-btn", "disabled"),
        Output("species-query-card", "style"),
        Input("app-store", "data"),
        Input("species-grid-store", "data"),
    )
    def _update_species_state(store, grid_store):
        store = store or {}
        grid_store = grid_store or {}
        has_reaction_data = bool((store.get("artifacts") or {}).get("reaction"))
        rows = grid_store.get("rows") or []
        searched = bool(grid_store.get("searched"))

        if not has_reaction_data:
            empty = [
                html.Div("开始分析", className="rs-empty-eyebrow"),
                html.H5("导入反应网络数据", className="rs-empty-title"),
                html.P(
                    "选择包含 reactionabcd 文件的数据目录后，即可按分子式、SMILES 或质量数检索物种。",
                    className="rs-empty-copy",
                ),
            ]
            return empty, {"display": "flex"}, {"display": "none"}, {}, True, True, {"display": "none"}

        if rows:
            return [], {"display": "none"}, {"display": "block"}, {"display": "none"}, False, False, {}

        if searched:
            text = grid_store.get("message") or "未找到匹配物种；可以放宽质量容差或切换查询类型。"
            title = "没有匹配结果"
        else:
            text = "输入查询内容并执行检索。支持分子式、SMILES 和质量数。"
            title = "等待查询"
        empty = [
            html.Div(title, className="rs-empty-title"),
            html.P(text, className="rs-empty-copy"),
        ]
        return empty, {"display": "flex"}, {"display": "none"}, {"display": "none"}, False, True, {}

    # ── Species detail panel ────────────────────────────────────────

    @app.callback(
        Output("detail-panel", "style"),
        Output("detail-body", "style"),
        Output("detail-body", "children"),
        Output("detail-empty", "style"),
        Output("species-to-event-btn", "disabled"),
        Output("app-store", "data", allow_duplicate=True),
        Output("transitions-smiles", "value"),
        Output("evolution-targets", "value"),
        Output("network-smiles", "value"),
        Input("species-grid", "selected_rows"),
        State("species-grid", "data"),
        State("app-store", "data"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_species_detail(selected_rows, table_rows, store, grid_store):
        store = store or {}
        if not selected_rows or len(selected_rows) == 0:
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                no_update,
                no_update,
                no_update,
                no_update,
            )
        table_rows = table_rows or (grid_store or {}).get("rows") or []
        selected_indices = [
            int(index)
            for index in selected_rows
            if isinstance(index, int) and 0 <= int(index) < len(table_rows)
        ]
        if not selected_indices:
            raise PreventUpdate
        row_idx = selected_indices[0]
        row = table_rows[row_idx]
        smiles = (row.get("smiles") or "").strip()
        if not smiles:
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                no_update,
                no_update,
                no_update,
                no_update,
            )
        artifacts = store.get("artifacts", {}) or {}
        try:
            detail = svc.species_detail(artifacts, smiles)
        except svc.ServiceError:
            detail = {"ok": True, "smiles": smiles, "formula": row.get("formula") or "?"}

        svg_result = svc.render_species_svg(smiles)

        formula = detail.get("formula") or "?"
        smiles_value = detail.get("smiles") or smiles
        evolution_targets = "\n".join(
            dict.fromkeys(
                str((table_rows[index] or {}).get("smiles") or "").strip()
                for index in selected_indices
                if str((table_rows[index] or {}).get("smiles") or "").strip()
            )
        )

        info_panel = html.Div(
            [
                html.Div(
                    [
                        html.Span(formula, className="rs-detail-formula"),
                        html.Code(smiles_value, className="rs-detail-smiles"),
                    ],
                    className="rs-detail-identity",
                ),
                html.Dl(
                    [
                        html.Dt("精确质量"),
                        html.Dd(_fmt_num(detail.get("exact_mass"))),
                        html.Dt("标称质量"),
                        html.Dd(_fmt_num(detail.get("nominal_mass"))),
                        html.Dt("反应物通量"),
                        html.Dd(_fmt_num(detail.get("tp_as_reactant"))),
                        html.Dt("产物通量"),
                        html.Dd(_fmt_num(detail.get("tp_as_product"))),
                        html.Dt("总通量"),
                        html.Dd(_fmt_num(detail.get("total_throughput"))),
                        html.Dt("消耗反应数"),
                        html.Dd(_fmt_num(detail.get("n_consume_rxns"))),
                        html.Dt("生成反应数"),
                        html.Dd(_fmt_num(detail.get("n_produce_rxns"))),
                    ]
                ),
            ],
            className="rs-detail-stats",
        )

        if svg_result.get("ok") and svg_result.get("svg"):
            svg_raw = svg_result["svg"]
            svg_wrapped = _wrap_svg_doc(svg_raw)
            structure_panel = html.Div(
                html.Iframe(
                    srcDoc=svg_wrapped,
                    style={"border": "none", "width": "100%", "height": "100%"},
                ),
                className="rs-svg-wrap",
            )
        elif svg_result.get("message"):
            structure_panel = html.Div(svg_result["message"], className="rs-svg-wrap rs-empty")
        else:
            structure_panel = html.Div("暂无可用结构图", className="rs-svg-wrap rs-empty")

        children = [structure_panel, info_panel]

        updated_store = {**store, "selected_smiles": smiles, "selected_formula": formula}
        return (
            {"display": "block"},
            {"display": "grid"},
            children,
            {"display": "none"},
            False,
            updated_store,
            smiles,
            evolution_targets,
            smiles,
        )

    @app.callback(
        Output("event-species-target", "value"),
        Input("species-to-event-btn", "n_clicks"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _send_species_to_event(n_clicks, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        return store.get("selected_smiles") or store.get("selected_formula") or ""

    # ── Transitions ─────────────────────────────────────────────────

    @app.callback(
        Output("transitions-grid", "data"),
        Output("transitions-grid", "columns"),
        Output("transitions-alert", "children"),
        Output("transitions-grid-store", "data"),
        Input("transitions-search-btn", "n_clicks"),
        State("transitions-smiles", "value"),
        State("transitions-direction", "value"),
        State("transitions-top", "value"),
        State("transitions-net-positive", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_transitions(n_clicks, smiles, direction, top, net_positive, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        smi = (smiles or store.get("selected_smiles") or "").strip()
        if not smi:
            return [], _transitions_columns(), "请先在物种检索中选择一个物种。", {"rows": []}
        try:
            result = svc.collect_transitions(
                artifacts,
                smi,
                direction=direction or "both",
                top=int(top or 0),
                net_positive_only=bool(net_positive),
            )
        except svc.ServiceError as exc:
            return [], _transitions_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _transitions_columns(), None, {"rows": rows}

    @app.callback(
        Output("transitions-selection-card", "style"),
        Output("transitions-selected-summary", "children"),
        Output("transitions-to-event-btn", "disabled"),
        Input("transitions-grid", "selected_rows"),
        State("transitions-grid", "data"),
    )
    def _show_selected_transition(selected_rows, rows):
        if not selected_rows:
            return {"display": "none"}, [], True
        rows = rows or []
        index = int(selected_rows[0])
        if index < 0 or index >= len(rows):
            return {"display": "none"}, [], True
        row = rows[index] or {}
        if not row.get("reaction_smiles"):
            return {"display": "none"}, [], True
        return {"display": "flex"}, _transition_selection_summary(row), False

    # ── Reaction formula search ─────────────────────────────────────

    @app.callback(
        Output("rxn-grid", "data"),
        Output("rxn-grid", "columns"),
        Output("rxn-alert", "children"),
        Output("rxn-grid-store", "data"),
        Input("rxn-search-btn", "n_clicks"),
        State("rxn-reactants", "value"),
        State("rxn-products", "value"),
        State("rxn-mode", "value"),
        State("rxn-top", "value"),
        State("rxn-with-share", "value"),
        State("rxn-share-metric", "value"),
        State("rxn-share-abs", "value"),
        State("rxn-share-positive", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_reactions(n_clicks, reactants, products, mode, top, with_share, share_metric, share_abs, share_positive, store):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            result = svc.search_reactions_by_formula(
                artifacts,
                reactants or "",
                products or "",
                mode=mode or "exact",
                top=int(top or 50),
                with_share=bool(with_share),
                share_metric=share_metric or "net_tp",
                share_abs_metric=bool(share_abs),
                share_positive_only=bool(share_positive),
            )
        except svc.ServiceError as exc:
            return [], _reaction_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _reaction_columns(with_share=bool(with_share)), None, {"rows": rows, "meta": result.get("meta", {})}

    @app.callback(
        Output("event-reaction-text", "value"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("transitions-to-event-btn", "n_clicks"),
        State("rxn-grid", "selected_rows"),
        State("rxn-grid", "data"),
        State("transitions-grid", "selected_rows"),
        State("transitions-grid", "data"),
        prevent_initial_call=True,
    )
    def _send_reaction_to_event(rxn_clicks, transition_clicks, rxn_selected_rows, rxn_rows, transition_selected_rows, transition_rows):
        if ctx.triggered_id == "transitions-to-event-btn":
            selected_rows, rows, n_clicks = transition_selected_rows, transition_rows, transition_clicks
        else:
            selected_rows, rows, n_clicks = rxn_selected_rows, rxn_rows, rxn_clicks
        if n_clicks is None or not selected_rows:
            raise PreventUpdate
        rows = rows or []
        index = int(selected_rows[0])
        if index < 0 or index >= len(rows):
            raise PreventUpdate
        return str((rows[index] or {}).get("reaction_smiles") or "")

    # ── Intermediate candidates ─────────────────────────────────────

    @app.callback(
        Output("inter-grid", "data"),
        Output("inter-grid", "columns"),
        Output("inter-alert", "children"),
        Output("inter-grid-store", "data"),
        Input("inter-search-btn", "n_clicks"),
        State("inter-kind", "value"),
        State("inter-top", "value"),
        State("inter-abundance", "value"),
        State("inter-start-ratio", "value"),
        State("inter-decay-alpha", "value"),
        State("inter-fwhm", "value"),
        State("inter-timestep", "value"),
        State("inter-require-fwhm", "value"),
        State("inter-with-flux", "value"),
        State("inter-flux-top", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_intermediates(
        n_clicks,
        kind,
        top,
        abundance,
        start_ratio,
        decay_alpha,
        fwhm,
        timestep,
        require_fwhm,
        with_flux,
        flux_top,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            result = svc.build_intermediate_candidates(
                artifacts,
                kind=kind or "intermediate",
                top=int(top or 120),
                abundance_threshold=float(abundance or 5.0),
                start_ratio_max=float(start_ratio or 0.1),
                decay_alpha=float(decay_alpha or 0.8),
                fwhm_min_ps=float(fwhm or 0.5),
                timestep_ps=float(timestep or 0.0001),
                require_fwhm=bool(require_fwhm),
                with_flux=bool(with_flux),
                flux_top=int(flux_top or 10),
            )
        except svc.ServiceError as exc:
            return [], _intermediate_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _intermediate_columns(rows), None, {"rows": rows, "meta": result.get("meta", {})}

    # ── CSV export: species ─────────────────────────────────────────

    @app.callback(
        Output("species-csv-download", "data"),
        Input("species-csv-btn", "n_clicks"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_species_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        grid_store = grid_store or {}
        rows = grid_store.get("rows") or []
        if not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        keys = ["smiles", "formula", "exact_mass", "nominal_mass", "tp_as_reactant", "tp_as_product", "total_throughput", "n_consume_rxns", "n_produce_rxns"]
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return {"content": buf.getvalue(), "filename": "species_search.csv", "type": "text/csv"}

    # ── CSV export: transitions ─────────────────────────────────────

    @app.callback(
        Output("transitions-csv-download", "data"),
        Input("transitions-csv-btn", "n_clicks"),
        State("transitions-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_transitions_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        grid_store = grid_store or {}
        rows = grid_store.get("rows") or []
        if not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        keys = ["role", "reaction_smiles", "reaction_formulas", "forward_tp", "reverse_tp", "net_tp", "ratio_pct", "tp"]
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return {"content": buf.getvalue(), "filename": "transitions.csv", "type": "text/csv"}

    @app.callback(
        Output("rxn-csv-download", "data"),
        Input("rxn-csv-btn", "n_clicks"),
        State("rxn-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_rxn_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "reaction_formula_search.csv", "type": "text/csv"}

    @app.callback(
        Output("inter-csv-download", "data"),
        Input("inter-csv-btn", "n_clicks"),
        State("inter-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_intermediate_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "intermediate_candidates.csv", "type": "text/csv"}

    # ── Evolution ───────────────────────────────────────────────────

    @app.callback(
        Output("evolution-graph", "figure"),
        Output("evolution-alert", "children"),
        Output("evolution-payload-store", "data"),
        Input("evolution-search-btn", "n_clicks"),
        State("evolution-targets", "value"),
        State("evolution-xaxis", "value"),
        State("evolution-smooth", "value"),
        State("evolution-species-file", "value"),
        State("evolution-species-files", "value"),
        State("evolution-formula-mode", "value"),
        State("evolution-max-smiles", "value"),
        State("evolution-normalize", "value"),
        State("evolution-time-align", "value"),
        State("evolution-timestep", "value"),
        State("evolution-downsample", "value"),
        State("evolution-max-curves", "value"),
        State("evolution-curve-filter", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _build_evolution(
        n_clicks,
        targets_text,
        x_axis,
        smooth,
        species_file,
        species_files,
        formula_mode,
        max_smiles,
        normalize,
        time_align,
        timestep,
        downsample,
        max_curves,
        curve_filter,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        targets = [t.strip() for t in re.split(r"[,;\n]+", targets_text or "") if t.strip()]
        if not targets:
            targets_text_default = store.get("selected_formula") or store.get("selected_smiles") or ""
            targets = [targets_text_default] if targets_text_default else []
        if not targets:
            from plotly.graph_objects import Figure

            return Figure(), "请先输入目标物种或分子式（或用物种检索中选择的物种）。", None
        try:
            payload = svc.build_species_evolution(
                artifacts,
                targets,
                species_file=species_file or "",
                species_files=species_files or "",
                x_axis=x_axis or "ps",
                timestep_ps=float(timestep or 0.0001),
                normalize=normalize or "none",
                smooth_window=int(smooth or 1),
                downsample=int(downsample or 0),
                max_curves=int(max_curves or 30),
                formula_mode=formula_mode or "sum",
                max_smiles_per_formula=int(max_smiles or 0),
                time_align=time_align or "raw",
            )
        except svc.ServiceError as exc:
            from plotly.graph_objects import Figure

            return Figure(), str(exc.message), None

        curves = payload.get("curves") or []
        curve_filter_text = (curve_filter or "").strip().casefold()
        if curve_filter_text:
            curves = [
                curve
                for curve in curves
                if curve_filter_text in str(curve.get("name") or curve.get("query") or "").casefold()
            ]
        x_values = payload.get("x_values") or []
        x_name = payload.get("x_name") or "x"

        import plotly.graph_objects as go

        fig = go.Figure()
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
        for i, curve in enumerate(curves):
            vals = curve.get("values") or []
            if len(vals) != len(x_values):
                continue
            name = curve.get("name") or curve.get("query") or f"curve_{i}"
            color = colors[i % len(colors)]
            fig.add_trace(go.Scatter(x=x_values, y=vals, mode="lines", name=name, line={"color": color, "width": 1.6}))
        fig.update_layout(
            xaxis_title=x_name,
            yaxis_title="相对丰度" if normalize in {"initial", "max"} else "丰度",
            template="plotly_white",
            margin={"l": 48, "r": 16, "t": 12, "b": 38},
            font={"size": 11},
            legend={"orientation": "h", "yanchor": "top", "y": -0.12, "xanchor": "left", "x": 0},
            hovermode="x unified",
        )
        return fig, None, payload

    # ── CSV export: evolution ───────────────────────────────────────

    @app.callback(
        Output("evolution-csv-download", "data"),
        Input("evolution-csv-btn", "n_clicks"),
        State("evolution-payload-store", "data"),
        prevent_initial_call=True,
    )
    def _export_evolution_csv(n_clicks, payload):
        if n_clicks is None or not payload:
            raise PreventUpdate
        csv_text = svc.evolution_to_csv(payload)
        return {"content": csv_text, "filename": "evolution.csv", "type": "text/csv"}

    # ── Carbon-number evolution ────────────────────────────────────

    @app.callback(
        Output("carbon-svg-frame", "srcDoc"),
        Output("carbon-alert", "children"),
        Output("carbon-highlights", "children"),
        Output("carbon-payload-store", "data"),
        Input("carbon-search-btn", "n_clicks"),
        State("carbon-xaxis", "value"),
        State("carbon-timestep", "value"),
        State("carbon-mode", "value"),
        State("carbon-topk", "value"),
        State("carbon-max-exact", "value"),
        State("carbon-display-ranges", "value"),
        State("carbon-merge-ranges", "value"),
        State("carbon-bins", "value"),
        State("carbon-parent", "value"),
        State("carbon-small", "value"),
        State("carbon-large", "value"),
        State("carbon-smoothing", "value"),
        State("carbon-smooth-window", "value"),
        State("carbon-layout", "value"),
        State("carbon-data-path", "value"),
        State("carbon-species-file", "value"),
        State("carbon-species-files", "value"),
        State("carbon-theme", "value"),
        State("carbon-palette", "value"),
        State("carbon-time-align", "value"),
        State("carbon-system-mode", "value"),
        State("carbon-legend-mode", "value"),
        State("carbon-smooth-polyorder", "value"),
        State("carbon-layout-regions", "value"),
        State("carbon-fig-width", "value"),
        State("carbon-fig-height", "value"),
        State("carbon-max-formula", "value"),
        State("carbon-show-uncertainty", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _build_carbon(
        n_clicks,
        x_axis,
        timestep,
        mode,
        topk,
        max_exact,
        display_ranges,
        merge_ranges,
        bins,
        parent,
        small,
        large,
        smoothing,
        smooth_window,
        layout,
        data_path,
        species_file,
        species_files,
        theme,
        palette,
        time_align,
        system_mode,
        legend_mode,
        smooth_polyorder,
        layout_regions,
        fig_width,
        fig_height,
        max_formula_list,
        show_uncertainty,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.build_carbon_evolution(
                artifacts,
                data_path=data_path or "",
                species_file=species_file or "",
                species_files=species_files or "",
                x_axis=x_axis or "ps",
                timestep_ps=float(timestep or 0.0001),
                mode=mode or "exact",
                top_k=int(topk or 12),
                max_exact_lines=int(max_exact or 24),
                display_ranges=display_ranges or "",
                merge_ranges=merge_ranges or "",
                carbon_bins=bins or "",
                parent_carbon_number=int(parent) if str(parent or "").strip() else None,
                highlight_small=small or "1-4",
                highlight_large=int(large or 30),
                smoothing=smoothing or "none",
                smooth_window=int(smooth_window or 5),
                smooth_polyorder=int(smooth_polyorder or 2),
                layout=layout or "single",
                layout_regions=layout_regions or "",
                theme=theme or "light",
                palette=palette or "viridis",
                time_align=time_align or "raw",
                system_mode=system_mode or "",
                legend_mode=legend_mode or "compact",
                fig_width=float(fig_width or 11.5),
                fig_height=float(fig_height or 8),
                max_formula_list=int(max_formula_list or 30),
                show_uncertainty=bool(show_uncertainty),
            )
        except svc.ServiceError as exc:
            return "", str(exc.message), [], None
        svg = payload.get("svg") or ""
        highlights = _carbon_highlights(payload.get("summary") or {}, payload.get("meta") or {})
        return svg, None, highlights, payload

    @app.callback(
        Output("carbon-csv-download", "data"),
        Input("carbon-csv-btn", "n_clicks"),
        State("carbon-payload-store", "data"),
        prevent_initial_call=True,
    )
    def _export_carbon_csv(n_clicks, payload):
        if n_clicks is None or not payload:
            raise PreventUpdate
        return {"content": svc.carbon_plot_to_csv(payload), "filename": "carbon_plot_data.csv", "type": "text/csv"}

    @app.callback(
        Output("carbon-svg-download", "data"),
        Input("carbon-svg-btn", "n_clicks"),
        State("carbon-payload-store", "data"),
        prevent_initial_call=True,
    )
    def _export_carbon_svg(n_clicks, payload):
        if n_clicks is None or not payload or not payload.get("svg"):
            raise PreventUpdate
        return {"content": payload.get("svg"), "filename": "carbon_number_evolution.svg", "type": "image/svg+xml"}

    # ── Event evidence ──────────────────────────────────────────────

    @app.callback(
        Output("event-grid", "data"),
        Output("event-grid", "columns"),
        Output("event-alert", "children"),
        Output("event-grid-store", "data"),
        Input("event-species-btn", "n_clicks"),
        State("event-species-target", "value"),
        State("event-match-mode", "value"),
        State("event-mode", "value"),
        State("event-before", "value"),
        State("event-after", "value"),
        State("event-max", "value"),
        State("event-species-file", "value"),
        State("event-trajectory-file", "value"),
        State("event-route-file", "value"),
        State("event-include-route", "value"),
        State("event-atom-scope", "value"),
        State("event-type-element-map", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _locate_species_events(
        n_clicks,
        target,
        match_mode,
        event_mode,
        before,
        after,
        max_events,
        species_file,
        trajectory_file,
        route_file,
        include_route,
        atom_scope,
        type_element_map,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        config = {
            "target": target or "",
            "match_mode": match_mode or "auto",
            "event_mode": event_mode or "appear",
            "before_frames": int(before or 3),
            "after_frames": int(after or 3),
            "species_file": species_file or "",
            "trajectory_file": trajectory_file or "",
            "route_file": route_file or "",
            "include_route_trace": bool(include_route),
            "trajectory_atom_scope": atom_scope or "event",
            "type_element_map": type_element_map or "",
        }
        try:
            request = {key: value for key, value in config.items() if key != "target"}
            payload = svc.locate_species_events(artifacts, config["target"], max_events=int(max_events or 12), **request)
        except svc.ServiceError as exc:
            return [], _event_columns(), str(exc.message), {"rows": [], "kind": "species", "config": config}
        rows = [{**row, "event_class": "物种事件"} for row in (payload.get("rows") or [])]
        message = (payload.get("meta") or {}).get("message") or f"找到 {len(rows)} 个物种事件"
        return rows, _event_columns(rows), message, {"rows": rows, "meta": payload.get("meta", {}), "kind": "species", "config": config}

    @app.callback(
        Output("event-grid", "data", allow_duplicate=True),
        Output("event-grid", "columns", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Input("event-rxn-btn", "n_clicks"),
        Input("transitions-to-event-btn", "n_clicks"),
        State("event-reaction-text", "value"),
        State("event-rxn-before", "value"),
        State("event-rxn-after", "value"),
        State("event-rxn-max", "value"),
        State("event-species-file", "value"),
        State("event-trajectory-file", "value"),
        State("event-route-file", "value"),
        State("event-type-element-map", "value"),
        State("app-store", "data"),
        State("transitions-grid", "selected_rows"),
        State("transitions-grid", "data"),
        prevent_initial_call=True,
    )
    def _locate_reaction_events(
        rxn_clicks,
        transition_clicks,
        reaction_text,
        before,
        after,
        max_events,
        species_file,
        trajectory_file,
        route_file,
        type_element_map,
        store,
        transition_selected_rows,
        transition_rows,
    ):
        if ctx.triggered_id == "transitions-to-event-btn":
            if not transition_selected_rows:
                raise PreventUpdate
            transition_rows = transition_rows or []
            index = int(transition_selected_rows[0])
            if index < 0 or index >= len(transition_rows):
                raise PreventUpdate
            reaction_text = str((transition_rows[index] or {}).get("reaction_smiles") or "")
            n_clicks = transition_clicks
        else:
            n_clicks = rxn_clicks
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        config = {
            "reaction_text": reaction_text or "",
            "before_frames": int(before or 5),
            "after_frames": int(after or 5),
            "max_events": int(max_events or 12),
            "species_file": species_file or "",
            "trajectory_file": trajectory_file or "",
            "route_file": route_file or "",
            "type_element_map": type_element_map or "",
        }
        try:
            request = {key: value for key, value in config.items() if key != "reaction_text"}
            payload = svc.locate_reaction_events(artifacts, config["reaction_text"], **request)
        except svc.ServiceError as exc:
            return [], _event_columns(), str(exc.message), {"rows": [], "kind": "reaction", "config": config}
        verified = [{**row, "event_class": "已验证"} for row in (payload.get("rows") or [])]
        candidates = [{**row, "event_class": "相关候选"} for row in (payload.get("candidate_rows") or [])]
        rows = [*verified, *candidates]
        meta = payload.get("meta") or {}
        discarded = len(payload.get("discarded_rows") or [])
        message = meta.get("message") or f"已验证 {len(verified)} 个；相关候选 {len(candidates)} 个；已丢弃 {discarded} 个"
        return rows, _event_columns(rows), message, {
            "rows": rows,
            "meta": meta,
            "kind": "reaction",
            "config": config,
            "verified_rows": verified,
            "candidate_rows": candidates,
            "discarded_rows": payload.get("discarded_rows") or [],
        }

    @app.callback(
        Output("event-grid", "selected_rows"),
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-selection-card", "style", allow_duplicate=True),
        Output("event-viewer-store", "data", allow_duplicate=True),
        Output("event-viewer-card", "style", allow_duplicate=True),
        Input("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_event_workspace(_workflow):
        """A new locate/dedup run invalidates the former selection and viewer."""
        return [], None, {"display": "none"}, None, {"display": "none"}

    @app.callback(
        Output("event-selected-store", "data"),
        Output("event-extract-id", "value"),
        Output("event-selected-summary", "children"),
        Output("event-selection-card", "style"),
        Input("event-grid", "selected_rows"),
        State("event-grid", "data"),
        State("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _select_event(selected_rows, table_rows, grid_store):
        if not selected_rows:
            raise PreventUpdate
        table_rows = table_rows or []
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate
        workflow = grid_store or {}
        kind = workflow.get("kind") or ""
        if kind not in {"reaction", "species"}:
            raise PreventUpdate
        selected = {"row": table_rows[row_idx] or {}, "kind": kind, "config": workflow.get("config") or {}}
        event_id = str(selected["row"].get("event_id") or "")
        return selected, event_id, _event_selection_summary(selected), {"display": "block"}

    @app.callback(
        Output("event-viewer-store", "data"),
        Output("event-viewer-card", "style"),
        Output("event-viewer-summary", "children"),
        Output("event-viewer-paths", "children"),
        Output("event-frame-slider", "min"),
        Output("event-frame-slider", "max"),
        Output("event-frame-slider", "value"),
        Output("event-frame-slider", "marks"),
        Output("event-storyboard", "children"),
        Output("event-alert", "children", allow_duplicate=True),
        Input("event-extract-btn", "n_clicks"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _extract_selected_event(n_clicks, selected, store):
        if n_clicks is None:
            raise PreventUpdate
        selected = selected or {}
        row = selected.get("row") or {}
        config = selected.get("config") or {}
        kind = selected.get("kind") or ""
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            if kind == "reaction":
                payload = svc.extract_reaction_event(
                    artifacts,
                    config.get("reaction_text") or row.get("reaction_smiles") or "",
                    str(row.get("event_id") or ""),
                    species_file=config.get("species_file") or "",
                    trajectory_file=config.get("trajectory_file") or "",
                    route_file=config.get("route_file") or "",
                    type_element_map=config.get("type_element_map") or "",
                    before_frames=int(config.get("before_frames") or 5),
                    after_frames=int(config.get("after_frames") or 5),
                    max_events=int(config.get("max_events") or 12),
                    inline_viewer=True,
                )
            elif kind == "species":
                payload = svc.extract_species_event(
                    artifacts,
                    config.get("target") or "",
                    int(row.get("anchor_frame")),
                    species_file=config.get("species_file") or "",
                    trajectory_file=config.get("trajectory_file") or "",
                    route_file=config.get("route_file") or "",
                    match_mode=config.get("match_mode") or "auto",
                    event_mode=config.get("event_mode") or "appear",
                    before_frames=int(config.get("before_frames") or 3),
                    after_frames=int(config.get("after_frames") or 3),
                    include_route_trace=bool(config.get("include_route_trace")),
                    trajectory_atom_scope=config.get("trajectory_atom_scope") or "event",
                    type_element_map=config.get("type_element_map") or "",
                )
            else:
                raise svc.ServiceError("请先从定位结果中选择一个事件", reason="missing_selection")
            viewer = svc.build_event_visualization(payload)
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return None, {"display": "none"}, [], [], 0, 0, 0, {}, [], message

        frames = viewer.get("frames") or []
        anchor = row.get("anchor_frame")
        anchor_index = next((idx for idx, item in enumerate(frames) if int(item.get("frame")) == int(anchor)), 0) if anchor is not None else 0
        marks = {idx: str(item.get("frame")) for idx, item in enumerate(frames)}
        storyboard = []
        for frame_number in viewer.get("storyboard_frames") or []:
            idx = next((i for i, item in enumerate(frames) if int(item.get("frame")) == int(frame_number)), None)
            if idx is None:
                continue
            label = (viewer.get("storyboard_labels") or {}).get(str(frame_number), f"Frame {frame_number}")
            storyboard.append(
                html.Div(
                    [html.Div(label, className="rs-storyboard-label"), dcc.Graph(figure=_event_frame_figure(viewer, idx, "context", compact=True), config={"displayModeBar": False})],
                    className="rs-storyboard-item",
                )
            )
        meta = viewer.get("meta") or {}
        paths = viewer.get("paths") or {}
        summary = html.Div(
            [
                html.Span(f"{len(frames)} 帧", className="rs-stat-chip"),
                html.Span(f"反应核 {len((viewer.get('atom_groups') or {}).get('core') or [])} 原子", className="rs-stat-chip"),
                html.Span(f"局部上下文 {len((viewer.get('atom_groups') or {}).get('context') or [])} 原子", className="rs-stat-chip"),
                html.Span(str(meta.get("verification_status") or meta.get("status") or "已提取"), className="rs-stat-chip"),
            ],
            className="rs-stat-row",
        )
        path_items = [f"局部轨迹: {paths.get('trajectory') or '-'}", f"VMD: {paths.get('vmd') or '-'}"]
        if paths.get("type_map"):
            path_items.append(f"类型映射: {paths['type_map']}")
        return viewer, {"display": "block"}, summary, " · ".join(path_items), 0, len(frames) - 1, anchor_index, marks, storyboard, "局部轨迹已提取，可在下方逐帧核查反应上下文。"

    @app.callback(
        Output("event-trajectory-3d", "figure"),
        Output("event-frame-label", "children"),
        Input("event-frame-slider", "value"),
        Input("event-view-scope", "value"),
        Input("event-viewer-store", "data"),
    )
    def _render_event_frame(frame_index, scope, viewer):
        if not viewer or not (viewer.get("frames") or []):
            from plotly.graph_objects import Figure

            return Figure(), ""
        frames = viewer.get("frames") or []
        safe_index = max(0, min(int(frame_index or 0), len(frames) - 1))
        frame = frames[safe_index]
        return _event_frame_figure(viewer, safe_index, scope or "context"), f"Frame {frame.get('frame')} · {len(frame.get('atoms') or [])} atoms"

    @app.callback(
        Output("event-csv-download", "data"),
        Input("event-csv-btn", "n_clicks"),
        State("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_event_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "event_evidence.csv", "type": "text/csv"}

    # ── Observation network ─────────────────────────────────────────

    @app.callback(
        Output("network-cytoscape", "elements"),
        Output("network-cytoscape", "layout"),
        Output("network-alert", "children"),
        Output("network-store", "data"),
        Input("network-search-btn", "n_clicks"),
        State("network-min-count", "value"),
        State("network-max-species", "value"),
        State("network-top-edges", "value"),
        State("network-layout", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _build_network(n_clicks, min_count, max_species, top_edges, layout_name, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        try:
            result = svc.build_observation_elements(
                artifacts,
                min_count=int(min_count or 1),
                max_species=int(max_species or 60),
                top_edges=int(top_edges or 40),
            )
        except svc.ServiceError as exc:
            return [], {"name": "concentric"}, str(exc.message), None
        elements = result.get("elements") or []
        layout = {"name": layout_name if layout_name in {"concentric", "cose", "grid", "circle", "breadthfirst"} else "concentric"}
        return elements, layout, None, result

    @app.callback(
        Output("network-cytoscape", "generateImage"),
        Input("network-png-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _export_network_png(n_clicks):
        if n_clicks is None:
            raise PreventUpdate
        return {"type": "png", "action": "download", "filename": "observation_network"}

    # ── Event dedup analysis ────────────────────────────────────────

    @app.callback(
        Output("event-grid", "data", allow_duplicate=True),
        Output("event-grid", "columns", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Input("event-dedup-btn", "n_clicks"),
        State("event-reaction-text", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _run_event_dedup(n_clicks, reaction_text, store):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.analyze_event_recrossing(
                artifacts,
                reaction_text or "",
            )
        except svc.ServiceError as exc:
            return [], _event_columns(), str(exc.message), {"rows": []}
        rows = payload.get("rows") or []
        message = (payload.get("meta") or {}).get("message") or None
        return rows, _event_columns(rows), message, {"rows": rows, "meta": payload.get("meta", {}), "kind": "dedup"}

    # ── Literature mechanism verification ────────────────────────────

    @app.callback(
        Output("literature-grid", "data"),
        Output("literature-grid", "columns"),
        Output("literature-alert", "children"),
        Output("literature-grid-store", "data"),
        Output("literature-summary", "children"),
        Output("literature-summary-card", "style"),
        Input("literature-verify-btn", "n_clicks"),
        State("literature-reactions-input", "value"),
        State("literature-verify-mode", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _verify_literature_mechanism(n_clicks, reaction_text, verify_mode, store):
        if n_clicks is None:
            raise PreventUpdate
        if not reaction_text or not str(reaction_text).strip():
            from dash import no_update
            return no_update, no_update, "请输入至少一个反应式", no_update, no_update, no_update

        from rng_tools.mechanism_verify import parse_literature_reaction_text
        reaction_lines = parse_literature_reaction_text(str(reaction_text))
        if not reaction_lines:
            from dash import no_update
            return no_update, no_update, "未能解析任何有效反应式", no_update, no_update, no_update

        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.verify_literature_mechanism(
                artifacts,
                reaction_lines,
                verify_mode=verify_mode or "species",
            )
        except svc.ServiceError as exc:
            return [], _literature_columns(), str(exc.message), {"rows": []}, None, {"display": "none"}

        rows = payload.get("rows") or []
        summary = payload.get("summary") or {}
        from dash import html

        summary_children = html.Div(
            [
                html.Div(
                    [
                        html.Span(f"共 {summary.get('total_reactions', 0)} 个反应", className="me-3"),
                        html.Span(f"检出 {summary.get('detected', 0)} 个", className="me-3 rs-evidence-detected", style={"padding": "2px 8px", "borderRadius": 4}),
                        html.Span(f"净通量 {summary.get('has_net_flux', 0)} 个", className="me-3 rs-evidence-net-flux", style={"padding": "2px 8px", "borderRadius": 4}),
                        html.Span(f"未检出 {summary.get('not_detected', 0)} 个", className="rs-evidence-not-detected", style={"padding": "2px 8px", "borderRadius": 4}),
                    ],
                    className="rs-stat-row",
                ),
                html.Div(
                    f"检出率: {summary.get('detection_rate', 0) * 100:.1f}%",
                    className="small text-muted mt-2",
                ),
            ]
        )
        meta = payload.get("meta") or {}
        message = meta.get("message") or None
        return rows, _literature_columns(), message, {"rows": rows, "summary": summary}, summary_children, {"display": "block"}

    @app.callback(
        Output("literature-csv-download", "data"),
        Input("literature-csv-btn", "n_clicks"),
        State("literature-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_literature_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "literature_evidence_matrix.csv", "type": "text/csv"}

    # ── Batch comparison ────────────────────────────────────────────

    @app.callback(
        Output("batch-condition-selector", "options"),
        Output("batch-conditions-store", "data"),
        Output("batch-conditions-status", "children"),
        Input("batch-scan-btn", "n_clicks"),
        State("batch-root-dir", "value"),
        prevent_initial_call=True,
    )
    def _scan_batch_conditions(n_clicks, root_dir):
        if n_clicks is None:
            raise PreventUpdate
        try:
            payload = svc.scan_batch_conditions(root_dir or "")
        except svc.ServiceError as exc:
            return [], None, f"扫描失败: {exc.message}"
        groups = payload.get("groups") or []
        options = [
            {
                "label": f"{g['group_name']} ({g['n_replicates']} 个重复)",
                "value": g["group_name"],
            }
            for g in groups
        ]
        status = f"扫描完成: {payload.get('total_conditions', 0)} 个条件, {payload.get('total_groups', 0)} 个条件组"
        return options, payload, status

    @app.callback(
        Output("batch-matrix-grid", "data"),
        Output("batch-matrix-grid", "columns"),
        Output("batch-alert", "children"),
        Output("batch-matrix-grid-store", "data"),
        Input("batch-compare-btn", "n_clicks"),
        State("batch-condition-selector", "value"),
        State("batch-conditions-store", "data"),
        State("batch-min-detection", "value"),
        State("batch-top-n", "value"),
        prevent_initial_call=True,
    )
    def _run_batch_comparison(n_clicks, selected_groups, conditions_payload, min_detection, top_n):
        if n_clicks is None:
            raise PreventUpdate
        if not selected_groups:
            return [], [], "请选择至少一个条件组", {"rows": []}

        all_conditions = (conditions_payload or {}).get("conditions") or []
        groups_dict = (conditions_payload or {}).get("groups") or []

        # Find folders for selected groups
        selected_folders = []
        selected_names = []
        for grp in groups_dict:
            if grp["group_name"] in selected_groups:
                for cname in grp.get("conditions", []):
                    for c in all_conditions:
                        if c["name"] == cname:
                            selected_folders.append(c["folder"])
                            selected_names.append(cname)
                            break

        if not selected_folders:
            return [], [], "未找到选中条件的目录", {"rows": []}

        try:
            payload = svc.run_batch_comparison(
                selected_folders,
                selected_names,
                min_detection_rate=float(min_detection or 0),
                top_n=int(top_n or 50),
            )
        except svc.ServiceError as exc:
            return [], _batch_comparison_columns([]), str(exc.message), {"rows": []}

        rows = payload.get("rows") or []
        columns = _columns_from_rows(rows, []) if rows else _batch_comparison_columns(payload.get("condition_names") or [])
        message = (payload.get("meta") or {}).get("message") or None
        return rows, columns, message, {"rows": rows, "condition_names": payload.get("condition_names", [])}

    @app.callback(
        Output("batch-reaction-chart", "figure"),
        Output("batch-reaction-stats", "children"),
        Output("batch-detail-card", "style"),
        Input("batch-matrix-grid", "selected_rows"),
        State("batch-matrix-grid", "data"),
        State("batch-matrix-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_reaction_detail(selected_rows, table_rows, grid_store):
        if not selected_rows:
            raise PreventUpdate
        table_rows = table_rows or []
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate

        row = table_rows[row_idx] or {}
        rxn_smiles = str(row.get("reaction_smiles", ""))

        condition_names = (grid_store or {}).get("condition_names") or []
        tp_values = []
        for cn in condition_names:
            tp = float(row.get(f"tp_{cn}", 0) or 0)
            tp_values.append((cn, tp))

        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=[t[0] for t in tp_values],
                y=[t[1] for t in tp_values],
                text=[str(int(t[1])) for t in tp_values],
                textposition="auto",
            )
        )
        fig.update_layout(
            title=f"反应通量对比 — {rxn_smiles[:80]}",
            xaxis_title="条件",
            yaxis_title="TP (Total Passages)",
            height=300,
            margin={"l": 50, "r": 20, "t": 40, "b": 80},
        )

        stats = html.Div(
            [
                html.Div(f"反应式: {rxn_smiles}", className="mb-2"),
                html.Div(f"检出率: {row.get('detection_rate', '-')}", className="small text-muted"),
            ]
        )
        return fig, stats, {"display": "block"}

    @app.callback(
        Output("batch-csv-download", "data"),
        Input("batch-csv-btn", "n_clicks"),
        State("batch-matrix-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_batch_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "batch_comparison.csv", "type": "text/csv"}

    # ── Modal pre-populate on open ──────────────────────────────────

    @app.callback(
        Output("data-folder-input", "value", allow_duplicate=True),
        Output("data-rungroup", "options", allow_duplicate=True),
        Output("data-scan-status", "children", allow_duplicate=True),
        Output("data-artifacts", "children", allow_duplicate=True),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _pre_populate_data_modal(topbar_open, species_open, store):
        if not topbar_open and not species_open:
            raise PreventUpdate
        store = store or {}
        folder = store.get("folder") or ""
        artifacts = store.get("artifacts", {}) or {}
        ready = store.get("ready_count") or 0
        options = []
        scan_msg = ""
        if folder:
            try:
                status = svc.scan_dataset(folder, base=store.get("base") or "")
                candidates = svc.candidates_from_status(status)
                options = [
                    {"label": f"{c.get('label') or c.get('base')} ({c.get('score', 0)}/5)", "value": c.get("base", "")}
                    for c in candidates
                ]
                artifacts = svc.artifacts_from_status(status)
                ready = svc.dataset_ready_count(status)
                label = svc.dataset_label(status)
                scan_msg = f"已扫描 — {label}，就绪 {ready}/5"
            except Exception:
                pass
        artifact_html = _render_artifacts(artifacts)
        return (folder or "", options, scan_msg, artifact_html)


# ── Shared column factories ─────────────────────────────────────────


def _species_columns(query_kind: str = ""):
    columns = [
        {"field": "formula", "headerName": "分子式", "width": 110},
        {"field": "smiles", "headerName": "SMILES", "flex": 2, "minWidth": 200},
        {"field": "exact_mass", "headerName": "精确质量", "width": 110, "type": "numericColumn"},
        {"field": "nominal_mass", "headerName": "标称质量", "width": 95, "type": "numericColumn"},
    ]
    if query_kind == "mass":
        columns.extend([
            {"field": "mass_error", "headerName": "质量误差", "width": 100, "type": "numericColumn"},
            {"field": "ppm_error", "headerName": "误差 ppm", "width": 95, "type": "numericColumn"},
        ])
    columns.extend([
        {"field": "tp_as_reactant", "headerName": "TP(反应物)", "width": 105, "type": "numericColumn"},
        {"field": "tp_as_product", "headerName": "TP(产物)", "width": 100, "type": "numericColumn"},
        {"field": "total_throughput", "headerName": "总通量", "width": 100, "type": "numericColumn"},
        {"field": "n_consume_rxns", "headerName": "消耗反应", "width": 95, "type": "numericColumn"},
        {"field": "n_produce_rxns", "headerName": "生成反应", "width": 95, "type": "numericColumn"},
    ])
    return _dt_columns(columns)


def _transitions_columns():
    return _dt_columns([
        {"field": "role", "headerName": "方向", "width": 75},
        {"field": "reaction_formulas", "headerName": "反应式", "flex": 2, "minWidth": 220},
        {"field": "forward_tp", "headerName": "TP(正向)", "width": 100, "type": "numericColumn"},
        {"field": "reverse_tp", "headerName": "TP(反向)", "width": 100, "type": "numericColumn"},
        {"field": "net_tp", "headerName": "净 TP", "width": 90, "type": "numericColumn"},
        {"field": "ratio_pct", "headerName": "占比%", "width": 85, "type": "numericColumn"},
        {"field": "tp", "headerName": "总 TP", "width": 90, "type": "numericColumn"},
    ])


def _reaction_columns(*, with_share: bool = False):
    cols = [
        {"field": "rank", "headerName": "#", "width": 70, "type": "numericColumn"},
        {"field": "reaction_formulas", "headerName": "反应式", "flex": 2, "minWidth": 240},
        {"field": "reaction_smiles", "headerName": "Reaction SMILES", "flex": 2, "minWidth": 260},
        {"field": "tp", "headerName": "TP", "width": 85, "type": "numericColumn"},
        {"field": "reverse_tp", "headerName": "Reverse", "width": 95, "type": "numericColumn"},
        {"field": "net_tp", "headerName": "Net", "width": 85, "type": "numericColumn"},
        {"field": "delta_exact_mass", "headerName": "Δ Exact", "width": 105, "type": "numericColumn"},
        {"field": "delta_nominal_mass", "headerName": "Δ Nominal", "width": 110, "type": "numericColumn"},
    ]
    if with_share:
        cols.extend(
            [
                {"field": "metric_value", "headerName": "Metric", "width": 95, "type": "numericColumn"},
                {"field": "share_pct", "headerName": "Share%", "width": 90, "type": "numericColumn"},
                {"field": "cumulative_pct", "headerName": "Cum%", "width": 90, "type": "numericColumn"},
            ]
        )
    return _dt_columns(cols)


def _intermediate_columns(rows=None):
    preferred = [
        "rank",
        "class",
        "formula",
        "smiles",
        "score",
        "c_start",
        "c_max",
        "c_end",
        "start_ratio",
        "end_ratio",
        "peak_timestep",
        "peak_time_ps",
        "fwhm_ps",
        "tp_consume",
        "tp_produce",
        "net_production",
        "top_sources",
        "top_sinks",
    ]
    return _columns_from_rows(rows or [], preferred)


def _event_columns(rows=None):
    preferred = [
        "event_class",
        "event_index",
        "candidate_index",
        "event_id",
        "event_type",
        "anchor_frame",
        "route_event_start_frame",
        "route_event_end_frame",
        "window_start",
        "window_end",
        "n_window_frames",
        "count_at_frame",
        "delta_from_prev",
        "matched_smiles_at_anchor",
        "event_resolution_label",
        "verification_status",
        "route_context_atom_count",
        "route_context_atom_ids",
        "route_event_atom_count",
        "route_event_atom_ids",
        "reaction_smiles",
    ]
    return _columns_from_rows(rows or [], preferred)


def _literature_columns():
    return _dt_columns([
        {"field": "index", "headerName": "#", "width": 50},
        {"field": "reaction_text", "headerName": "文献反应式", "flex": 2, "minWidth": 200},
        {"field": "evidence_label", "headerName": "证据等级", "width": 130},
        {"field": "detected", "headerName": "是否检测到", "width": 100},
        {"field": "forward_tp", "headerName": "正向次数", "width": 90},
        {"field": "net_tp", "headerName": "净次数", "width": 90},
        {"field": "is_transient", "headerName": "瞬时过程", "width": 90},
        {"field": "atom_confirmed_count", "headerName": "原子确认数", "width": 100},
        {"field": "notes", "headerName": "备注", "flex": 1, "minWidth": 150},
    ])


def _batch_comparison_columns(condition_names=None):
    condition_names = condition_names or []
    base = [
        {"field": "index", "headerName": "#", "width": 50},
        {"field": "reaction_smiles", "headerName": "反应式 (SMILES)", "flex": 2, "minWidth": 200},
        {"field": "detection_rate", "headerName": "检出率", "width": 80},
    ]
    for cn in condition_names:
        base.append({"field": f"tp_{cn}", "headerName": f"{cn} (TP)", "width": 100})
    return _dt_columns(base)


def _columns_from_rows(rows: list[dict[str, Any]], preferred: list[str]):
    seen = set()
    fields: list[str] = []
    if rows:
        all_keys = {key for row in rows for key in row.keys()}
        for key in preferred:
            if key in all_keys and key not in seen:
                seen.add(key)
                fields.append(key)
        for key in sorted(all_keys):
            if key not in seen:
                seen.add(key)
                fields.append(key)
    else:
        fields = list(preferred[:8])

    cols = []
    for field in fields:
        is_num = field.endswith("_count") or field.endswith("_tp") or field in {
            "rank",
            "event_index",
            "candidate_index",
            "anchor_frame",
            "window_start",
            "window_end",
            "n_window_frames",
            "count_at_frame",
            "delta_from_prev",
            "score",
            "c_start",
            "c_max",
            "c_end",
            "start_ratio",
            "end_ratio",
            "peak_timestep",
            "peak_time_ps",
            "fwhm_ps",
            "net_production",
        }
        cols.append(
            {
                "field": field,
                "headerName": field,
                "minWidth": 120 if field not in {"smiles", "reaction_smiles", "top_sources", "top_sinks"} else 220,
                "flex": 2 if field in {"smiles", "reaction_smiles", "top_sources", "top_sinks", "route_context_atom_ids"} else 1,
                **({"type": "numericColumn"} if is_num else {}),
            }
        )
    return _dt_columns(cols)


def _dt_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for col in columns:
        field = str(col.get("field") or col.get("id") or "")
        if not field:
            continue
        dtype = "numeric" if col.get("type") == "numericColumn" else "text"
        out.append({"id": field, "name": str(col.get("headerName") or col.get("name") or field), "type": dtype})
    return out


# ── Helpers ─────────────────────────────────────────────────────────


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _render_artifacts(artifacts: dict[str, str]) -> Any:
    from dash import html

    labels = {
        "reaction": "Reaction",
        "species": "Species",
        "trajectory": "Trajectory",
        "route": "Route",
        "table": "Table",
    }
    chips: list[Any] = []
    for key, label in labels.items():
        path = artifacts.get(key)
        if path:
            chips.append(html.Span(f"{label}: {path}", style={"display": "block", "fontSize": 12, "color": "#4b5563", "wordBreak": "break-all"}))
        else:
            chips.append(html.Span(f"{label}: 缺失", style={"display": "block", "fontSize": 12, "color": "#9ca3af"}))
    return html.Div(chips, style={"lineHeight": 1.7})


def _carbon_highlights(summary: dict[str, Any], meta: dict[str, Any]) -> Any:
    from dash import html

    base = summary.get("base") if isinstance(summary.get("base"), dict) else summary
    items = [
        ("Rows", meta.get("n_plot_rows")),
        ("Systems", meta.get("n_systems")),
        ("Regions", meta.get("n_regions")),
        ("Plot", meta.get("plot_mode")),
        ("Parent", f"C{base.get('parent_carbon_number')}" if base.get("parent_carbon_number") else None),
        ("Max C", base.get("max_carbon_number_observed")),
        ("Large peak", base.get("large_hydrocarbon_peak_time")),
    ]
    chips = [
        html.Span([html.Strong(label), html.Span(_fmt_num(value))], className="rs-stat-chip")
        for label, value in items
        if value not in (None, "")
    ]
    return chips


def html_dl(items: dict[str, str]) -> Any:
    from dash import html

    children: list[Any] = []
    for key, value in items.items():
        children.extend([html.Dt(key), html.Dd(value)])
    return html.Dl(children)


def _wrap_svg_doc(svg: str) -> str:
    """Wrap an SVG string in a full HTML document with reset CSS and viewBox fix."""
    import re

    # Ensure the SVG has a viewBox attribute
    if "viewBox" not in svg:
        w_match = re.search(r'width=["\']?(\d+)', svg)
        h_match = re.search(r'height=["\']?(\d+)', svg)
        if w_match and h_match:
            w, h = w_match.group(1), h_match.group(1)
            svg = svg.replace("<svg", f'<svg viewBox="0 0 {w} {h}"', 1)

    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\"><style>\n"
        "html,body{margin:0;padding:0;overflow:hidden;width:100%;height:100%}\n"
        "svg{max-width:100%;max-height:100%;display:block;margin:0 auto}\n"
        "</style></head><body>\n"
        + svg +
        "\n</body></html>"
    )
