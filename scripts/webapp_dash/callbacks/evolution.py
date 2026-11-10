"""Species evolution and abundance ranking callbacks."""

from __future__ import annotations

import re
from typing import Any
from dash import (
    Input,
    Output,
    State,
    ctx,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from scripts.webapp_dash.file_import import defer_query
from reacnet_scope import services as svc
from scripts.webapp_dash.chart_presentation import empty_chart_figure
from scripts.webapp_dash.candidate_workbench import context_key
from scripts.webapp_dash.navigation import (
    migrate_page_state,
)

from .common import (
    _composition_trend_figure,
    _empty_plotly_figure,
    _evolution_comparison_active,
    _selected_table_row,
    _species_preview_tooltips,
    _species_structure_detail_children,
)


def register(app: Any) -> None:
    """Register evolution callbacks."""

    # ── Evolution ───────────────────────────────────────────────────

    @app.callback(
        Output("evolution-species-picker", "options"),
        Output("evolution-species-picker", "value"),
        Output("evolution-catalog-alert", "children"),
        Input("evolution-load-species-btn", "n_clicks"),
        State("evolution-species-file", "value"),
        State("evolution-species-files", "value"),
        State("evolution-species-picker", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (Output("evolution-load-species-btn", "disabled"), True, False),
        ],
    )
    def _load_evolution_species_catalog(
        n_clicks,
        species_file,
        species_files,
        selected_values,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = ((store or {}).get("artifacts") or {})
        try:
            catalog = svc.species_evolution_catalog(
                artifacts,
                species_file=species_file or "",
                species_files=species_files or "",
            )
        except svc.ServiceError as exc:
            return [], [], str(exc.message)

        options = catalog.get("options") or []
        valid_values = {str(option.get("value")) for option in options}
        kept_values = [
            str(value)
            for value in (selected_values or [])
            if str(value) in valid_values
        ]
        meta = catalog.get("meta") or {}
        message = (
            f"已从 {int(meta.get('n_sources') or 0)} 个文件读取 "
            f"{int(meta.get('n_formulas') or 0)} 种分子式"
        )
        warnings = [
            str(item)
            for item in (meta.get("warnings") or [])
            if str(item).strip()
        ]
        if warnings:
            message = f"{message}；{'；'.join(warnings)}"
        return options, kept_values, message

    @app.callback(
        Output("evolution-graph", "figure"),
        Output("evolution-alert", "children"),
        Output("evolution-payload-store", "data"),
        Output("evolution-group-payload-store", "data"),
        Output("evolution-focus-store", "data", allow_duplicate=True),
        Output("evolution-targets", "value", allow_duplicate=True),
        Output("evolution-species-picker", "value", allow_duplicate=True),
        Output("evolution-curve-filter", "value", allow_duplicate=True),
        Output("import-pending-evolution", "data"),
        Input("evolution-search-btn", "n_clicks"),
        Input("import-auto-result", "data"),
        Input("species-to-evolution-btn", "n_clicks"),
        Input("evolution-rank-request", "data"),
        Input("evolution-browse-mode", "value"),
        Input("evolution-group-element", "value"),
        Input("evolution-group-count", "value"),
        Input("evolution-xaxis", "value"),
        Input("evolution-group-members-grid", "selectedRows"),
        State("evolution-species-picker", "value"),
        State("evolution-targets", "value"),
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
        State("evolution-rank-store", "data"),
        State("evolution-rank-grid", "rowData"),
        State("evolution-group-payload-store", "data"),
        State("evolution-group-members-grid", "rowData"),
        State("evolution-focus-store", "data"),
        State("evolution-start-timestep", "value"),
        State("evolution-end-timestep", "value"),
        *[State(f"evolution-group-filter-{field}", "value") for field in ("required", "excluded", "element", "min", "max")],
        State("app-store", "data"),
        State("import-pending-evolution", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "evolution"}, "data"),
                True,
                False,
            ),
            (
                Output("evolution-progress", "children"),
                "正在读取当前目标的丰度时间序列…",
                "",
            ),
            (
                Output("evolution-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    @defer_query("composition", 8, submit_triggers=frozenset({
        "species-to-evolution-btn",
        "evolution-rank-request", "evolution-browse-mode", "evolution-group-element",
        "evolution-group-count", "evolution-xaxis", "evolution-group-members-grid",
    }), replay_trigger_kw="submitted_trigger",
        trigger_priority=("evolution-search-btn", "species-to-evolution-btn"))
    def _build_evolution(
        n_clicks,
        shortcut_clicks,
        rank_request,
        browse_mode,
        group_element,
        group_count,
        x_axis,
        member_selection,
        selected_species,
        targets_text,
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
        rank_store,
        rank_rows,
        group_payload,
        member_rows,
        focus,
        start_timestep,
        end_timestep,
        group_required, group_excluded, filter_element, filter_min, filter_max,
        store,
        submitted_trigger=None,
    ):
        trigger = submitted_trigger or ctx.triggered_id
        if n_clicks is None and trigger not in {
            "species-to-evolution-btn",
            "evolution-rank-request", "evolution-browse-mode", "evolution-group-element",
            "evolution-group-count", "evolution-xaxis", "evolution-group-members-grid", "import-auto-result",
        }:
            raise PreventUpdate
        store = store or {}
        rank_selection = (
            rank_request.get("selection")
            if rank_request and rank_request.get("context") == context_key(store)
            else None
        )
        artifacts = store.get("artifacts", {}) or {}
        from_detail = submitted_trigger == "species-to-evolution-btn" and bool(shortcut_clicks)
        if from_detail:
            browse_mode = "species"
            species_file = ""
            species_files = ""
        if browse_mode != "group" and trigger in {
            "evolution-group-element", "evolution-group-count", "evolution-group-members-grid",
        }:
            raise PreventUpdate
        if browse_mode == "group":
            from ..evolution_workbench import element_filter_query
            selected_element = str(group_element or "C")
            try:
                selected_count = int(group_count)
                if selected_count < 0 or selected_count > 200:
                    raise ValueError("元素原子数须在 0–200 之间")
                member = _selected_table_row(member_selection, member_rows)
                group_name = f"{selected_element}{selected_count}"
                member_context_matches = (
                    isinstance(group_payload, dict)
                    and group_payload.get("context") == context_key(store)
                    and group_payload.get("selected_group") == group_name
                )
                reference = (
                    str((member or {}).get("smiles") or "")
                    if member_context_matches and trigger in {
                        "evolution-group-members-grid", "evolution-xaxis", "import-auto-result",
                    }
                    else ""
                )
                species_path = str(artifacts.get("species") or "")
                confirmed_timestep = (
                    float(timestep) if timestep is not None
                    else svc.load_timestep_ps(species_path) if species_path else None
                )
                payload = svc.build_elemental_composition_evolution(
                    artifacts,
                    element_filters=element_filter_query(group_required, group_excluded, filter_element, filter_min, filter_max),
                    group_element=selected_element,
                    max_group_count=selected_count,
                    include_zero=selected_count == 0,
                    reference_smiles=reference,
                    x_axis=x_axis or "step",
                    timestep_ps=confirmed_timestep,
                )
                visible_rows = [
                    row for row in payload.get("distribution_rows") or []
                    if row.get("series") in {group_name, "参考物种", f"{group_name} 其他物种"}
                ]
                if not any(row.get("series") == group_name for row in visible_rows):
                    return (_empty_plotly_figure(f"{group_name} 没有可显示的丰度"),
                            f"当前筛选中没有 {group_name} 物种。", None, None,
                            None, no_update, no_update, no_update)
                figure = _composition_trend_figure({**payload, "distribution_rows": visible_rows})
                figure.update_layout(title={"text": f"{group_name} · 丰度随时间变化", "x": 0.01})
                return (figure, None, None, {**payload, "context": context_key(store), "selected_group": group_name},
                        None, no_update, no_update, no_update)
            except (svc.ServiceError, TypeError, ValueError) as exc:
                message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
                return _empty_plotly_figure(message), message, None, None, None, no_update, no_update, no_update
        rank_context_matches = (
            isinstance(rank_store, dict)
            and rank_store.get("context") == context_key(store)
        )
        selected_rank = (
            _selected_table_row(rank_selection, rank_rows)
            if rank_context_matches and trigger == "evolution-rank-request" else None
        )
        if trigger == "evolution-rank-request" and selected_rank is None:
            raise PreventUpdate
        picked_targets = [
            str(item).strip()
            for item in (selected_species or [])
            if str(item).strip()
        ]
        manual_targets = [
            t.strip()
            for t in re.split(r"[,;\n]+", targets_text or "")
            if t.strip()
        ]
        targets = list(dict.fromkeys([*picked_targets, *manual_targets]))
        species = ""
        selected_from_rank = selected_rank is not None and trigger == "evolution-rank-request"
        if from_detail:
            species = str(store.get("selected_smiles") or "").strip()
            if not species:
                raise PreventUpdate
            targets = [species]
        elif selected_from_rank:
            species = str(selected_rank.get("smiles") or "")
            targets = [species]
        elif trigger != "evolution-search-btn" and (focus or {}).get("context") == context_key(store):
            targets = list(focus.get("targets") or [])
            species = str(focus.get("species") or "")
        if not targets:
            targets_text_default = store.get("selected_formula") or store.get("selected_smiles") or ""
            targets = [targets_text_default] if targets_text_default else []
        if not targets:
            message = "请先选择或输入目标物种。" if trigger == "evolution-search-btn" else None
            return (empty_chart_figure("尚未选择目标物种", "输入元素条件、分子式或精确结构，例如 C6。"),
                    message, None, None, None, no_update, no_update, no_update)
        selection = {"context": context_key(store), "targets": targets, "species": species}
        target_update = f"smiles:{species}" if from_detail or selected_from_rank else no_update
        picker_update = [] if from_detail or selected_from_rank else no_update
        filter_update = "" if from_detail or selected_from_rank else no_update
        if from_detail or selected_from_rank:
            curve_filter = ""
        try:
            payload = svc.build_species_evolution(
                artifacts,
                [f"smiles:{species}"] if species and targets == [species] else targets,
                species_file=species_file or "",
                species_files=species_files or "",
                x_axis=x_axis or "step",
                timestep_ps=float(timestep) if timestep is not None else None,
                normalize=normalize or "none",
                smooth_window=int(smooth or 1),
                downsample=int(downsample or 0),
                max_curves=int(max_curves or 30),
                formula_mode=formula_mode or "sum",
                max_smiles_per_formula=int(max_smiles or 0),
                time_align=time_align or "raw",
                start_timestep=start_timestep, end_timestep=end_timestep,
            )
        except svc.ServiceError as exc:
            return (empty_chart_figure("本次绘图未完成", "请查看上方提示，调整设置后重试。"), str(exc.message),
                    None, None, selection, target_update, picker_update, filter_update)

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
        if not fig.data:
            fig = go.Figure(empty_chart_figure("没有可显示的曲线", "请检查目标物种、曲线筛选及数据范围。"))
        visible_names = [
            str(curve.get("name") or curve.get("query") or "")
            for curve in curves
        ]
        warnings = [
            str(item)
            for item in ((payload.get("meta") or {}).get("warnings") or [])
            if str(item).strip()
        ]
        members = list(dict.fromkeys(member for curve in curves for member in curve.get("members") or []))
        selection["element_summaries"] = [
            f"{curve['name']} · {curve['element_condition']['element']} 原子数 = "
            f"{curve['element_condition']['count']} · 汇总 {curve['n_members']} 个精确物种"
            for curve in curves if curve.get("query_type") == "element_sum"
        ]
        if len(members) == 1 and not selection["element_summaries"]:
            selection["species"] = members[0]
        return (
            fig,
            "；".join(warnings) if warnings else None,
            {**payload, "visible_curve_names": visible_names},
            None,
            selection, target_update, picker_update, filter_update,
        )

    @app.callback(
        Output("evolution-group-members-grid", "columnDefs"),
        Output("evolution-group-members-grid", "rowData"),
        Output("evolution-group-members-status", "children"),
        Input("evolution-graph", "clickData"),
        State("evolution-group-payload-store", "data"),
        State("evolution-browse-mode", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _show_abundance_group_members(click_data, payload, mode, dataset):
        columns = [
            {"field": "formula", "headerName": "分子式", "minWidth": 110},
            {"field": "smiles", "headerName": "精确结构 (RNG SMILES)", "minWidth": 190},
            {"field": "current_count", "headerName": "当前数量", "minWidth": 110, "type": "numericColumn"},
            {"field": "peak_count", "headerName": "峰值", "minWidth": 90, "type": "numericColumn"},
        ]
        if mode != "group" or not payload or payload.get("context") != context_key(dataset):
            return columns, [], ""
        points = (click_data or {}).get("points") or []
        if not points:
            return columns, [], "点击曲线上的时间点，查看组内精确物种。"
        custom = points[0].get("customdata") or []
        try:
            timestep, series = int(custom[0]), str(custom[1])
            detail = svc.build_element_distribution_species_drilldown(
                payload, series=series, timestep=timestep,
            )
        except (IndexError, TypeError, ValueError, svc.ServiceError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return columns, [], f"无法读取该时间点：{message}"
        rows = list(detail.get("rows") or [])
        return columns, rows, f"{series} · {detail['current_time']:.6g} {detail['x_unit']} · {len(rows)} 个精确物种（最多 100 个）"

    @app.callback(
        Output("evolution-group-members-grid", "selectedRows"),
        Output("evolution-group-members-grid", "rowData", allow_duplicate=True),
        Output("evolution-group-members-status", "children", allow_duplicate=True),
        Input("evolution-browse-mode", "value"),
        Input("evolution-group-element", "value"),
        Input("evolution-group-count", "value"),
        prevent_initial_call=True,
    )
    def _clear_abundance_group_member_selection(_mode, _element, _count):
        return [], [], "点击曲线上的时间点，查看组内精确物种。"

    @app.callback(
        Output("evolution-group-member-open-btn", "disabled"),
        Input("evolution-group-members-grid", "selectedRows"),
        State("evolution-group-members-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _group_member_open_disabled(selected_rows, rows):
        return _selected_table_row(selected_rows, rows) is None

    @app.callback(
        Output("evolution-csv-btn", "disabled"),
        Input("evolution-payload-store", "data"),
        Input("evolution-csv-busy", "data"),
        prevent_initial_call=True,
    )
    def _evolution_export_available(payload, busy=False):
        if busy or not payload or not payload.get("curves"):
            return True
        names = payload.get("visible_curve_names")
        if names is None:
            return False
        return not any((curve.get("name") or curve.get("query") or f"curve_{index}") in names
                       for index, curve in enumerate(payload["curves"]))

    # ── CSV export: evolution ───────────────────────────────────────

    @app.callback(
        Output("evolution-csv-download", "data"),
        Input("evolution-csv-btn", "n_clicks"),
        State("evolution-payload-store", "data"),
        prevent_initial_call=True,
        running=[(Output("evolution-csv-busy", "data"), True, False),
                 (Output("evolution-csv-btn", "children"), "正在准备 CSV…", "下载数据 CSV")],
    )
    def _export_evolution_csv(n_clicks, payload):
        if not n_clicks or _evolution_export_available(payload):
            raise PreventUpdate
        csv_text = svc.evolution_to_csv(payload, curve_names=payload.get("visible_curve_names"))
        return {"content": "\ufeff" + csv_text, "filename": "abundance_raw.csv", "type": "text/csv;charset=utf-8"}

    @app.callback(
        Output("evolution-graph", "clickData"),
        Output(
            "evolution-structure-detail",
            "children",
            allow_duplicate=True,
        ),
        Output("evolution-rank-grid", "selectedRows", allow_duplicate=True),
        Input("evolution-search-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_evolution_curve_selection(n_clicks):
        if n_clicks is None:
            raise PreventUpdate
        return None, None, []

    @app.callback(
        Output("evolution-structure-detail", "children"),
        Input("evolution-graph", "clickData"),
        Input("evolution-structure-show-h", "value"),
        State("evolution-payload-store", "data"),
    )
    def _show_evolution_curve_structures(click_data, show_h, payload):
        points = (click_data or {}).get("points") or []
        if not points or not payload:
            return None
        curve_number = int(points[0].get("curveNumber") or 0)
        visible_names = payload.get("visible_curve_names") or []
        if curve_number < 0 or curve_number >= len(visible_names):
            return "无法定位所选曲线。"
        selected_name = str(visible_names[curve_number])
        curve = next(
            (
                item
                for item in (payload.get("curves") or [])
                if str(item.get("name") or item.get("query") or "") == selected_name
            ),
            None,
        )
        members = list((curve or {}).get("members") or [])
        if not members:
            return "所选曲线没有可显示的 SMILES 成员。"
        items = svc.build_species_structure_items(
            members,
            show_h=bool(show_h),
            max_items=24,
        )
        note = (
            f"显示前 24 / {len(members)} 个成员"
            if len(members) > 24
            else f"{len(members)} 个成员"
        )
        return _species_structure_detail_children(
            items,
            title=selected_name,
            note=note,
        )

    @app.callback(
        Output("evolution-species-browser", "style"),
        Output("evolution-group-browser", "style"),
        Output("evolution-csv-btn", "style"),
        Output("evolution-structure-detail", "style"),
        Output("evolution-manual-settings", "style"),
        Output("evolution-structure-show-h", "style"),
        Input("evolution-browse-mode", "value"),
    )
    def _render_abundance_browser(mode):
        grouped = mode == "group"
        hidden = {"display": "none"}
        return (hidden if grouped else {},
                {} if grouped else hidden,
                hidden if grouped else {},
                hidden if grouped else {},
                hidden if grouped else {},
                hidden if grouped else {})

    @app.callback(
        Output("evolution-browse-mode", "value"),
        Output("evolution-focus-store", "data"),
        Output("evolution-targets", "value", allow_duplicate=True),
        Output("evolution-species-picker", "value", allow_duplicate=True),
        Output("evolution-curve-filter", "value"),
        Output("evolution-species-file", "value", allow_duplicate=True),
        Output("evolution-species-files", "value", allow_duplicate=True),
        Output("evolution-rank-grid", "selectedRows", allow_duplicate=True),
        Output("evolution-ranking", "active_item"),
        Input("species-to-evolution-btn", "n_clicks"),
        State("app-store", "data"),
        State("evolution-rank-store", "data"),
        State("evolution-rank-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _open_species_abundance(_clicks, dataset, ranking, rows):
        species = str((dataset or {}).get("selected_smiles") or "").strip()
        if not _clicks or not species:
            raise PreventUpdate
        context = context_key(dataset or {})
        return ("species", {"species": species, "targets": [species], "context": context},
                f"smiles:{species}", [], "", "", "", {"ids": []}, None)

    @app.callback(
        Output("evolution-focus", "children"),
        Output("evolution-focus", "style"),
        Input("evolution-focus-store", "data"),
        Input("app-store", "data"),
        Input("evolution-browse-mode", "value"),
    )
    def _render_species_abundance_focus(focus, dataset, mode):
        if not focus or mode == "group":
            return [], {"display": "none"}
        if focus.get("context") != context_key(dataset or {}):
            return "当前物种来源已变化，请重新选择对象。", {}
        if focus.get("element_summaries"):
            summaries = focus["element_summaries"]
            return html.Details([
                html.Summary(["匹配物种详情", html.Span(f"{len(summaries)} 个条件")]),
                html.Div([html.Div(item) for item in summaries], className="rs-evolution-match-body"),
            ], id="evolution-match-details", className="rs-evolution-match-details", open=False), {}
        species = str(focus.get("species") or "")
        if not species:
            return html.Div([html.Strong("当前查询"), html.Code("；".join(focus.get("targets") or []))]), {}
        item = svc.build_species_structure_items([species], show_h=True, max_items=1)[0]
        return html.Div([
            html.Img(src=item.get("structure_url"), alt=f"{item.get('formula') or '?'} 结构"),
            html.Div([html.Strong(f"当前物种 · {item.get('formula') or '?'}"), html.Code(species)]),
        ], className="rs-evolution-focus-summary"), {}

    @app.callback(
        Output("evolution-group-element", "options"),
        Output("evolution-group-element", "value"),
        Input("evolution-rank-store", "data"),
        State("evolution-group-element", "value"),
        State("app-store", "data"),
    )
    def _group_element_options(_ranking, current, dataset):
        status = svc.composition_index_status((dataset or {}).get("artifacts") or {})
        elements = [str(value) for value in status.get("available_elements") or []] or ["C"]
        return ([{"label": element, "value": element} for element in elements],
                current if current in elements else ("C" if "C" in elements else elements[0]))

    @app.callback(
        Output("evolution-rank-store", "data"),
        Output("evolution-rank-grid", "rowData"),
        Output("evolution-rank-grid", "columnDefs"),
        Output("evolution-rank-grid-previews", "data"),
        Output("evolution-rank-status", "children"),
        Output("evolution-rank-grid", "selectedRows", allow_duplicate=True),
        Input("page-store", "data"),
        Input("app-store", "data"),
        Input("evolution-ranking", "active_item"),
        State("evolution-rank-store", "data"),
        prevent_initial_call=True,
    )
    def _load_abundance_ranking(page, dataset, opened, previous):
        state = migrate_page_state(page)
        if not opened or state["page"] != "evolution" or _evolution_comparison_active(state):
            raise PreventUpdate
        store = dataset or {}
        if not store.get("dataset_id"):
            return None, [], [], [], "请先选择当前 RNG 数据，或选择多个来源进行丰度比较。", []
        current_context = context_key(store)
        if isinstance(previous, dict) and previous.get("context") == current_context and not previous.get("error"):
            raise PreventUpdate
        columns = [
            {"field": "formula", "headerName": "分子式", "minWidth": 90, "flex": 0.8},
            {"field": "smiles", "headerName": "精确结构 (RNG SMILES)", "minWidth": 150, "flex": 1.6},
            {"field": "total_count", "headerName": "累计采样丰度", "minWidth": 130, "flex": 1, "type": "numericColumn"},
        ]
        try:
            result = svc.ranked_species_abundance(store.get("artifacts") or {}, limit=50)
        except svc.ServiceError as exc:
            return {"context": current_context, "error": exc.message}, [], columns, [], exc.message, []
        rows = result["rows"]
        count = int(result["total_species"])
        shown = len(rows)
        frame_range = (
            f"分析帧对应的 source timestep {result['first_timestep']}–{result['last_timestep']}"
            if result.get("sampled_frames") else "无已分析帧"
        )
        status = (
            f"显示前 {shown} / {count} 个精确物种；指标为 {result['sampled_frames']} 个已分析帧的丰度计数之和；"
            f"范围：{frame_range}。" + (" 本页仅显示前 50 名。" if count > shown else "")
        )
        return ({"context": current_context, "rows": rows}, rows, columns, _species_preview_tooltips(rows), status,
                {"ids": []})

    @app.callback(
        Output("evolution-rank-open-btn", "disabled"),
        Input("evolution-rank-grid", "selectedRows"),
        State("evolution-rank-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _rank_open_disabled(selected_rows, rows):
        return _selected_table_row(selected_rows, rows) is None
