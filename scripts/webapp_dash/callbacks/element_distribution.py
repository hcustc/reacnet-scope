"""Element distribution evolution callbacks."""

from __future__ import annotations

from typing import Any
import dash_bootstrap_components as dbc
from dash import (
    Input,
    Output,
    State,
    no_update,
)
from dash.exceptions import PreventUpdate
from scripts.webapp_dash.file_import import defer_query
from reacnet_scope import services as svc

from .common import (
    _composition_trend_figure,
    _empty_plotly_figure,
    _fmt_num,
    _selected_table_row,
    _species_structure_detail_children,
)


def _composition_highlights(payload: dict[str, Any]) -> Any:
    from dash import html

    meta = payload.get("meta") or {}
    summary = payload.get("summary") or {}
    filters = payload.get("filters") or {}
    filter_summary = ", ".join(
        f"{element}: {str((rule or {}).get('mode') or 'all')}"
        for element, rule in sorted(filters.items())
    )
    items = [
        ("索引时间点", meta.get("source_timepoints")),
        ("绘图采样点", meta.get("sampled_timepoints")),
        ("索引查询", f"{meta.get('query_seconds')} s" if meta.get("query_seconds") is not None else None),
        ("总耗时", f"{meta.get('analysis_seconds')} s" if meta.get("analysis_seconds") is not None else None),
        ("分组元素", summary.get("group_element")),
        ("最大原子数", summary.get("max_group_count")),
        ("筛选", filter_summary or "无"),
        ("Timestep", f"{summary.get('timestep_ps')} ps" if summary.get("timestep_ps") is not None else None),
        ("参考物种", summary.get("reference_formula") or summary.get("reference_smiles")),
    ]
    return [
        html.Span([html.Strong(label), html.Span(_fmt_num(value))], className="rs-stat-chip")
        for label, value in items
        if value not in (None, "")
    ]


def register(app: Any) -> None:
    """Register element distribution callbacks."""

    # ── Element Distribution Evolution ─────────────────────────────

    @app.callback(
        Output("element-distribution-alert", "children"),
        Output("element-distribution-highlights", "children"),
        Output("element-distribution-payload-store", "data"),
        Output("element-distribution-composition-trend", "figure"),
        Output("import-pending-elements", "data"),
        Input("element-distribution-search-btn", "n_clicks"),
        Input("import-auto-result", "data"),
        State("element-distribution-group-element", "value"),
        State("element-distribution-max-count", "value"),
        State("element-distribution-include-zero", "value"),
        State("element-distribution-filter-element", "value"),
        State("element-distribution-filter-mode", "value"),
        State("element-distribution-filter-min", "value"),
        State("element-distribution-filter-max", "value"),
        State("element-distribution-reference-smiles", "value"),
        State("element-distribution-timestep", "value"),
        State("app-store", "data"),
        State("import-pending-elements", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "element-distribution"}, "data"),
                True,
                False,
            ),
            (
                Output("element-distribution-progress", "children"),
                "正在读取元素分布索引并应用筛选…",
                "",
            ),
            (
                Output("element-distribution-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    @defer_query("composition", 4)
    def _build_element_distribution(
        n_clicks,
        group_element,
        max_group_count,
        include_zero,
        filter_element,
        filter_mode,
        filter_min,
        filter_max,
        reference_smiles,
        timestep,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        filters: dict[str, dict[str, Any]] = {}
        selected_filter = str(filter_element or "").strip()
        selected_mode = str(filter_mode or "all")
        if selected_filter and selected_mode != "all":
            rule: dict[str, Any] = {"mode": selected_mode}
            if selected_mode == "range":
                if filter_min is not None:
                    rule["min"] = int(filter_min)
                if filter_max is not None:
                    rule["max"] = int(filter_max)
            filters[selected_filter] = rule
        try:
            species_path = str(artifacts.get("species") or "").strip()
            confirmed_timestep = (
                float(timestep)
                if timestep is not None
                else svc.load_timestep_ps(species_path)
            )
            if timestep is not None and species_path:
                svc.save_timestep_ps(species_path, float(timestep))
            payload = svc.build_elemental_composition_evolution(
                artifacts,
                x_axis="ps" if confirmed_timestep is not None else "step",
                timestep_ps=confirmed_timestep,
                group_element=str(group_element or "C"),
                max_group_count=int(max_group_count if max_group_count is not None else 6),
                element_filters=filters,
                include_zero=bool(include_zero),
                reference_smiles=str(reference_smiles or "").strip(),
            )
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            empty = _empty_plotly_figure(message)
            return dbc.Alert(message, color="warning"), [], None, empty
        return None, _composition_highlights(payload), payload, _composition_trend_figure(payload)

    @app.callback(
        Output("element-distribution-composition-table", "selectedRows"),
        Output("element-distribution-composition-trend", "clickData"),
        Input("element-distribution-search-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_element_distribution_selection(n_clicks):
        if n_clicks is None:
            raise PreventUpdate
        return [], None

    @app.callback(
        Output("element-distribution-composition-table", "columnDefs"),
        Output("element-distribution-composition-table", "rowData"),
        Output("element-distribution-composition-table-title", "children"),
        Input("element-distribution-composition-trend", "clickData"),
        Input("element-distribution-payload-store", "data"),
        running=[
            (
                Output("element-distribution-drilldown-progress", "children"),
                "正在读取所选元素计数组的当前值与全程峰值…",
                "",
            ),
            (
                Output("element-distribution-drilldown-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _render_composition_detail(click_data, payload):
        if not payload:
            return [], [], "绘制后，点击主图中的参考物种或元素计数曲线查看代表物种。"
        points = (click_data or {}).get("points") or []
        if not points:
            return [], [], "点击主图中的参考物种或元素计数曲线，查看该时间点的代表物种。"
        point = points[0]
        custom = point.get("customdata") or []
        try:
            timestep = int(custom[0])
            series = str(custom[1])
            detail = svc.build_element_distribution_species_drilldown(
                payload,
                series=series,
                timestep=timestep,
            )
        except (IndexError, TypeError, ValueError, svc.ServiceError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return [], [], f"无法读取所选元素计数组：{message}"
        unit = str(detail.get("x_unit") or "timestep")
        columns = [
            {"name": "分子式", "id": "formula"},
            {"name": "SMILES", "id": "smiles"},
            {"name": "当前数量", "id": "current_count", "type": "numeric"},
            {"name": "峰值数量", "id": "peak_count", "type": "numeric"},
            {"name": f"峰值位置 ({unit})", "id": "peak_time", "type": "numeric"},
        ]
        title = (
            f"{detail['series']} · 当前 {detail['current_time']:.6g} {unit}"
            f" · {len(detail['rows'])} 个代表物种"
            f" · 查询 {float(detail.get('query_seconds') or 0):.4f} s"
        )
        return columns, detail["rows"], title

    @app.callback(
        Output("element-distribution-structure-detail", "children"),
        Input("element-distribution-composition-table", "selectedRows"),
        Input("element-distribution-structure-show-h", "value"),
        State("element-distribution-composition-table", "rowData"),
    )
    def _show_element_distribution_species_structure(selected_rows, show_h, rows):
        row = _selected_table_row(selected_rows, rows)
        smiles = str((row or {}).get("smiles") or "").strip()
        if not smiles:
            return "选择一个代表物种后显示结构、分子式与 SMILES。"
        items = svc.build_species_structure_items(
            [smiles],
            formula_values=[(row or {}).get("formula") or ""],
            show_h=bool(show_h),
        )
        return _species_structure_detail_children(
            items,
            title="代表物种结构",
        )

    @app.callback(
        Output("element-distribution-dataset-name", "value"),
        Output("element-distribution-index-status", "children"),
        Output("element-distribution-index-status", "className"),
        Output("element-distribution-index-progress", "value"),
        Output("element-distribution-index-refresh", "disabled"),
        Output("element-distribution-group-element", "options"),
        Output("element-distribution-group-element", "value"),
        Output("element-distribution-filter-element", "options"),
        Output("element-distribution-filter-element", "value"),
        Input("app-store", "data"),
        Input("page-store", "data"),
        Input("element-distribution-index-refresh", "n_intervals"),
        State("element-distribution-group-element", "value"),
        State("element-distribution-filter-element", "value"),
    )
    def _refresh_element_distribution_index_status(
        store,
        page_store,
        _n_intervals,
        current_group_element,
        current_filter_element,
    ):
        if str((page_store or {}).get("page") or "") != "element-distribution":
            return (
                no_update,
                no_update,
                no_update,
                no_update,
                True,
                no_update,
                no_update,
                no_update,
                no_update,
            )
        store = store or {}
        label = str(store.get("label") or store.get("folder") or "未选择")
        status = svc.composition_index_status(store.get("artifacts") or {})
        state = str(status.get("state") or "missing")
        percent = int(round(float(status.get("progress") or 0.0) * 100))
        if state == "ready":
            text = (
                f"元素分布索引已就绪 · {int(status.get('timepoints') or 0)} 个时间点"
                f" · {int(status.get('unique_species') or 0)} 个物种"
            )
            percent = 100
            class_name = "rs-index-status is-ready"
        elif state == "building":
            text = f"正在建立元素分布索引 · {percent}%"
            class_name = "rs-index-status is-building"
        elif state == "missing_source":
            text = "请先在“RNG 数据”中选择包含 .species 的RNG 数据"
            class_name = "rs-index-status is-warning"
        elif state in {"stale", "invalid"}:
            text = "元素分布索引需要重建：运行 reacnet-scope prepare rebuild element-distribution <目录>"
            class_name = "rs-index-status is-warning"
        else:
            text = "元素分布索引尚未建立：运行 reacnet-scope prepare build element-distribution <目录>"
            class_name = "rs-index-status is-warning"
        elements = [str(value) for value in status.get("available_elements") or []]
        if not elements:
            elements = ["C"]
        options = [{"label": value, "value": value} for value in elements]
        group_value = (
            current_group_element
            if current_group_element in elements
            else ("C" if "C" in elements else elements[0])
        )
        filter_value = current_filter_element if current_filter_element in elements else None
        return (
            label,
            text,
            class_name,
            percent,
            state != "building",
            options,
            group_value,
            options,
            filter_value,
        )

    @app.callback(
        Output("element-distribution-open-species-btn", "disabled"),
        Input("element-distribution-composition-table", "selectedRows"),
        State("element-distribution-composition-table", "rowData"),
    )
    def _distribution_open_disabled(selected_rows, rows):
        row = _selected_table_row(selected_rows, rows)
        return not bool(row and row.get("smiles"))
