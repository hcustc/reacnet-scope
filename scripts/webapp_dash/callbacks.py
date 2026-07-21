"""Dash callback registration for ReacNet Scope WebUI V1.

All callbacks are registered in ``register_callbacks(app)``.  Each callback
delegates to ``scripts.webapp_dash.services`` for data operations and never
re-implements analysis logic.
"""

from __future__ import annotations

from typing import Any

from dash import Input, Output, State, callback, ctx, no_update
from dash.exceptions import PreventUpdate

from scripts.webapp_dash import services as svc


PAGE_IDS = ["species", "transitions", "reactions", "intermediate", "evolution", "carbon", "events", "network"]


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
        Output("nav-species", "className"),
        Output("nav-transitions", "className"),
        Output("nav-reactions", "className"),
        Output("nav-intermediate", "className"),
        Output("nav-evolution", "className"),
        Output("nav-carbon", "className"),
        Output("nav-events", "className"),
        Output("nav-network", "className"),
        Input("nav-species", "n_clicks"),
        Input("nav-transitions", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        Input("nav-intermediate", "n_clicks"),
        Input("nav-evolution", "n_clicks"),
        Input("nav-carbon", "n_clicks"),
        Input("nav-events", "n_clicks"),
        Input("nav-network", "n_clicks"),
        State("page-store", "data"),
        prevent_initial_call=True,
    )
    def _navigate(*_args):
        triggered_id = ctx.triggered_id
        if not triggered_id:
            raise PreventUpdate
        page_id = triggered_id.removeprefix("nav-")
        if page_id not in PAGE_IDS:
            raise PreventUpdate
        page_classes = {
            pid: "rs-page active" if pid == page_id else "rs-page"
            for pid in PAGE_IDS
        }
        nav_classes = {
            pid: f"rs-nav-item{' active' if pid == page_id else ''}"
            for pid in PAGE_IDS
        }
        return tuple(page_classes[pid] for pid in PAGE_IDS) + tuple(nav_classes[pid] for pid in PAGE_IDS)

    # ── Data modal open / close ─────────────────────────────────────

    @app.callback(
        Output("data-modal", "is_open"),
        Input("open-data-modal", "n_clicks"),
        Input("data-close-btn", "n_clicks"),
        Input("data-apply-btn", "n_clicks"),
        State("data-modal", "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_data_modal(open_btn, close_btn, apply_btn, is_open):
        triggered = ctx.triggered_id
        if triggered == "open-data-modal":
            return True
        if triggered in ("data-close-btn", "data-apply-btn"):
            return False
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
        Input("species-search-btn", "n_clicks"),
        State("species-query", "value"),
        State("species-query-kind", "value"),
        State("species-mass-tol", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_species(n_clicks, query, kind, mass_tol, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        if not artifacts.get("reaction"):
            return [], _species_columns(), '请先在「管理数据」中导入包含 reactionabcd 的数据目录。', {"rows": []}
        try:
            result = svc.search_species(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=float(mass_tol or 0.5),
            )
        except svc.ServiceError as exc:
            return [], _species_columns(), str(exc.message), {"rows": []}

        rows = result.get("rows") or []
        message = f"找到 {len(rows)} 条匹配物种" if rows else "未找到匹配物种；可以放宽质量容差或切换查询类型。"
        return rows, _species_columns(), message, {"rows": rows, "query_kind": result.get("query_kind")}

    # ── Species detail panel ────────────────────────────────────────

    @app.callback(
        Output("detail-body", "style"),
        Output("detail-body", "children"),
        Output("detail-empty", "style"),
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
                [],
                {"display": "block"},
                no_update,
                no_update,
                no_update,
                no_update,
            )
        table_rows = table_rows or (grid_store or {}).get("rows") or []
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate
        row = table_rows[row_idx]
        smiles = (row.get("smiles") or "").strip()
        if not smiles:
            return (
                {"display": "none"},
                [],
                {"display": "block"},
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
        children = [
            html_dl(
                {
                    "SMILES": detail.get("smiles") or smiles,
                    "分子式": formula,
                    "Exact mass": _fmt_num(detail.get("exact_mass")),
                    "Nominal mass": _fmt_num(detail.get("nominal_mass")),
                    "TP as reactant": _fmt_num(detail.get("tp_as_reactant")),
                    "TP as product": _fmt_num(detail.get("tp_as_product")),
                    "Total throughput": _fmt_num(detail.get("total_throughput")),
                    "Consume rxns": _fmt_num(detail.get("n_consume_rxns")),
                    "Produce rxns": _fmt_num(detail.get("n_produce_rxns")),
                }
            ),
        ]
        if svg_result.get("ok") and svg_result.get("svg"):
            from dash import html

            children.append(html.Div(html.Iframe(srcDoc=svg_result["svg"], style={"border": "none", "width": "100%", "height": "210px"}), className="rs-svg-wrap"))
        elif svg_result.get("message"):
            from dash import html

            children.append(html.Div(svg_result["message"], className="rs-empty"))

        updated_store = {**store, "selected_smiles": smiles, "selected_formula": formula}
        return (
            {"display": "block"},
            children,
            {"display": "none"},
            updated_store,
            smiles,
            formula,
            smiles,
        )

    # ── Transitions ─────────────────────────────────────────────────

    @app.callback(
        Output("transitions-grid", "data"),
        Output("transitions-grid", "columns"),
        Output("transitions-alert", "children"),
        Output("transitions-grid-store", "data"),
        Input("transitions-search-btn", "n_clicks"),
        State("transitions-smiles", "value"),
        State("transitions-direction", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_transitions(n_clicks, smiles, direction, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        smi = (smiles or store.get("selected_smiles") or "").strip()
        if not smi:
            return [], _transitions_columns(), "请先在物种检索中选择一个物种。", {"rows": []}
        try:
            result = svc.collect_transitions(artifacts, smi, direction=direction or "both")
        except svc.ServiceError as exc:
            return [], _transitions_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _transitions_columns(), None, {"rows": rows}

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
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _build_evolution(n_clicks, targets_text, x_axis, smooth, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        targets = [t.strip() for t in (targets_text or "").split(",") if t.strip()]
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
                x_axis=x_axis or "ps",
                smooth_window=int(smooth or 1),
            )
        except svc.ServiceError as exc:
            from plotly.graph_objects import Figure

            return Figure(), str(exc.message), None

        curves = payload.get("curves") or []
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
            yaxis_title="丰度",
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
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.build_carbon_evolution(
                artifacts,
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
                layout=layout or "single",
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
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _locate_species_events(n_clicks, target, match_mode, event_mode, before, after, max_events, store):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.locate_species_events(
                artifacts,
                target or "",
                match_mode=match_mode or "auto",
                event_mode=event_mode or "appear",
                before_frames=int(before or 3),
                after_frames=int(after or 3),
                max_events=int(max_events or 12),
            )
        except svc.ServiceError as exc:
            return [], _event_columns(), str(exc.message), {"rows": []}
        rows = payload.get("rows") or []
        message = (payload.get("meta") or {}).get("message") or None
        return rows, _event_columns(rows), message, {"rows": rows, "meta": payload.get("meta", {}), "kind": "species"}

    @app.callback(
        Output("event-grid", "data", allow_duplicate=True),
        Output("event-grid", "columns", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Input("event-rxn-btn", "n_clicks"),
        State("event-reaction-text", "value"),
        State("event-rxn-before", "value"),
        State("event-rxn-after", "value"),
        State("event-rxn-max", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _locate_reaction_events(n_clicks, reaction_text, before, after, max_events, store):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.locate_reaction_events(
                artifacts,
                reaction_text or "",
                before_frames=int(before or 5),
                after_frames=int(after or 5),
                max_events=int(max_events or 12),
            )
        except svc.ServiceError as exc:
            return [], _event_columns(), str(exc.message), {"rows": []}
        rows = payload.get("rows") or []
        if not rows:
            rows = payload.get("candidate_rows") or []
        message = (payload.get("meta") or {}).get("message") or None
        return rows, _event_columns(rows), message, {"rows": rows, "meta": payload.get("meta", {}), "kind": "reaction"}

    @app.callback(
        Output("event-extract-id", "value"),
        Input("event-grid", "selected_rows"),
        State("event-grid", "data"),
        State("event-extract-id", "value"),
        prevent_initial_call=True,
    )
    def _fill_event_id(selected_rows, table_rows, current_value):
        if not selected_rows:
            raise PreventUpdate
        table_rows = table_rows or []
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate
        event_id = str((table_rows[row_idx] or {}).get("event_id") or "").strip()
        if not event_id or event_id == (current_value or ""):
            raise PreventUpdate
        return event_id

    @app.callback(
        Output("event-grid", "data", allow_duplicate=True),
        Output("event-grid", "columns", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Input("event-extract-btn", "n_clicks"),
        State("event-reaction-text", "value"),
        State("event-extract-id", "value"),
        State("event-rxn-before", "value"),
        State("event-rxn-after", "value"),
        State("event-rxn-max", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _extract_reaction_event(n_clicks, reaction_text, event_id, before, after, max_events, store):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.extract_reaction_event(
                artifacts,
                reaction_text or "",
                event_id or "",
                before_frames=int(before or 5),
                after_frames=int(after or 5),
                max_events=int(max_events or 200),
            )
        except svc.ServiceError as exc:
            return [], _event_columns(), str(exc.message), {"rows": []}
        rows = payload.get("frame_rows") or payload.get("rows") or []
        meta = payload.get("meta") or {}
        paths = [
            f"trajectory: {payload.get('trajectory_saved_path') or meta.get('trajectory_saved_path') or '-'}",
            f"vmd: {payload.get('vmd_script_saved_path') or meta.get('vmd_script_saved_path') or '-'}",
        ]
        type_map = payload.get("type_map_saved_path") or meta.get("type_map_saved_path") or ""
        if type_map:
            paths.append(f"type_map: {type_map}")
        message = "抽取完成；" + "；".join(paths)
        return rows, _event_columns(rows), message, {"rows": rows, "meta": meta, "kind": "extract"}

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

    # ── Modal pre-populate on open ──────────────────────────────────

    @app.callback(
        Output("data-folder-input", "value", allow_duplicate=True),
        Output("data-rungroup", "options", allow_duplicate=True),
        Output("data-scan-status", "children", allow_duplicate=True),
        Output("data-artifacts", "children", allow_duplicate=True),
        Input("open-data-modal", "n_clicks"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _pre_populate_data_modal(n_clicks, store):
        if n_clicks is None:
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


def _species_columns():
    return _dt_columns([
        {"field": "formula", "headerName": "分子式", "width": 110},
        {"field": "smiles", "headerName": "SMILES", "flex": 2, "minWidth": 200},
        {"field": "exact_mass", "headerName": "精确质量", "width": 110, "type": "numericColumn"},
        {"field": "nominal_mass", "headerName": "标称质量", "width": 95, "type": "numericColumn"},
        {"field": "tp_as_reactant", "headerName": "TP(反应物)", "width": 105, "type": "numericColumn"},
        {"field": "tp_as_product", "headerName": "TP(产物)", "width": 100, "type": "numericColumn"},
        {"field": "total_throughput", "headerName": "总通量", "width": 100, "type": "numericColumn"},
        {"field": "n_consume_rxns", "headerName": "消耗反应", "width": 95, "type": "numericColumn"},
        {"field": "n_produce_rxns", "headerName": "生成反应", "width": 95, "type": "numericColumn"},
    ])


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
