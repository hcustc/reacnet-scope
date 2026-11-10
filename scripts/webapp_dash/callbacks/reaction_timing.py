"""Exact reaction timing distribution and paging callbacks."""

from __future__ import annotations

from typing import Any
import plotly.graph_objects as go
from dash import (
    Input,
    Output,
    State,
    ctx,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc

from .common import (
    _event_columns,
    _event_table_rows,
    _selected_table_row,
)


def register(app: Any) -> None:
    """Register reaction timing callbacks."""

    # ── Exact reaction timing ───────────────────────────────────────

    @app.callback(
        Output("rxn-timing-distribution-store", "data"),
        Output("rxn-timing-graph", "figure"),
        Output("rxn-timing-status", "children"),
        Output("rxn-timing-card", "style", allow_duplicate=True),
        Output("rxn-timing-start", "value"),
        Output("rxn-timing-end", "value"),
        Output("rxn-timing-width", "value"),
        Output("rxn-timing-graph", "clickData"),
        Input("rxn-grid", "selectedRows"),
        Input("rxn-channel-selection-store", "data"),
        Input("rxn-timing-apply-btn", "n_clicks"),
        State("rxn-grid", "rowData"),
        State("rxn-timing-distribution-store", "data"),
        State("rxn-timing-start", "value"),
        State("rxn-timing-end", "value"),
        State("rxn-timing-width", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[(
            Output({"type": "dataset-bound-operation", "name": "reaction-timing-chart"}, "data"),
            True, False,
        )],
    )
    def _show_reaction_timing(
        selected, channel_selection, _apply, rows, previous,
        start, end, width, store,
    ):
        trigger = ctx.triggered_id
        dataset_id = (store or {}).get("dataset_id")
        if trigger == "rxn-timing-apply-btn":
            if not previous or previous.get("dataset_id") != dataset_id:
                raise PreventUpdate
            row = previous["selection"]
            requested = {"start": start, "end": end, "width": width}
        elif trigger == "rxn-channel-selection-store":
            row = ((channel_selection or {}).get("row") or {})
            requested = {}
        else:
            row = _selected_table_row(selected, rows) or {}
            requested = {}
        if not row:
            return None, go.Figure(), "", {"display": "none"}, None, None, None, None
        reactants = list(row.get("reactant_smiles") or [])
        products = list(row.get("product_smiles") or [])
        try:
            result = svc.reaction_time_distribution(
                (store or {}).get("artifacts") or {}, reactants, products,
                **requested,
            )
        except svc.ServiceError as exc:
            return None, go.Figure(), exc.message, {"display": "block"}, start, end, width, None
        bins = result["bins"]
        fig = go.Figure()
        for direction, label, color in (
            ("forward", "正向", "#337ab7"),
            ("reverse", "严格逆向", "#d88442"),
        ):
            if direction == "reverse" and result["self_reverse"]:
                continue
            fig.add_bar(
                name=label,
                x=[(item["start"] + item["end"]) / 2 for item in bins],
                y=[item[direction] for item in bins],
                width=[item["end"] - item["start"] for item in bins],
                marker_color=color,
                customdata=[[item["index"], direction] for item in bins],
                hovertemplate="%{y} 次<extra>" + label + "</extra>",
            )
        fig.update_layout(
            barmode="group", xaxis_title=f"事件后帧时间 / {result['unit']}",
            yaxis_title="发生次数", xaxis_range=[result["start"], result["end"]],
            margin={"l": 50, "r": 15, "t": 15, "b": 45},
        )
        note = (
            f"时间窗 [{result['start']:g}, {result['end']:g}) {result['unit']}；"
            f"分箱宽度 {result['width']:g} {result['unit']}；"
            f"窗内正向 {result['forward_total']} 次、逆向 {result['reverse_total']} 次。"
            "反应发生于前后采样帧之间，后帧时间仅用于定位。"
        )
        if result["self_reverse"]:
            note += "此反应完全自反，只显示一组事件。"
        if result["unit"] != "ps":
            note += "未确认 timestep → ps 换算或仅有分析帧序号；当前不显示 ps。"
        return (
            {**result, "selection": {"reactant_smiles": reactants,
                                    "product_smiles": products,
                                    "reaction_smiles": row.get("reaction_smiles")},
             "dataset_id": dataset_id},
            fig, note, {"display": "block"},
            result["start"], result["end"], result["width"], None,
        )

    @app.callback(
        Output("rxn-timing-page-store", "data", allow_duplicate=True),
        Output("rxn-timing-event-grid", "rowData", allow_duplicate=True),
        Output("rxn-timing-event-grid", "columnDefs"),
        Output("rxn-timing-bin-status", "children"),
        Input("rxn-timing-graph", "clickData"),
        Input("rxn-timing-prev-btn", "n_clicks"),
        Input("rxn-timing-next-btn", "n_clicks"),
        Input("rxn-timing-distribution-store", "data"),
        State("rxn-timing-page-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[(
            Output({"type": "dataset-bound-operation", "name": "reaction-timing-page"}, "data"),
            True, False,
        )],
    )
    def _page_reaction_timing(click_data, _prev, _next, distribution, previous, store):
        empty = (None, [], _event_columns(), "点击时间箱查看该方向的全部事件，可逐页核查。")
        if ctx.triggered_id == "rxn-timing-distribution-store" or not distribution:
            return empty
        dataset_id = (store or {}).get("dataset_id")
        if distribution.get("dataset_id") != dataset_id:
            return empty
        if ctx.triggered_id == "rxn-timing-graph":
            point = ((click_data or {}).get("points") or [{}])[0]
            custom = point.get("customdata") or []
            if len(custom) != 2:
                return empty
            index, direction = int(custom[0]), str(custom[1])
            if not 0 <= index < len(distribution["bins"]) or direction not in {"forward", "reverse"}:
                return empty
            item = distribution["bins"][index]
            key = distribution["reaction_key"] if direction == "forward" else distribution["reverse_reaction_key"]
            offset = 0
        else:
            if not previous or previous.get("dataset_id") != dataset_id:
                return empty
            item = previous["bin"]
            direction = previous["direction"]
            key = previous["reaction_key"]
            offset = max(0, int(previous["offset"]) + (25 if ctx.triggered_id == "rxn-timing-next-btn" else -25))
            if offset >= int(previous["total"]):
                offset = int(previous["offset"])
        try:
            page = svc.reaction_time_events(
                (store or {}).get("artifacts") or {}, key,
                start_raw=item["start_raw"], end_raw=item["end_raw"],
                offset=offset, limit=25,
            )
        except svc.ServiceError as exc:
            return None, [], _event_columns(), exc.message
        for event in page["rows"]:
            event["selected_direction"] = direction
            event["bin_start_raw"] = item["start_raw"]
            event["bin_end_raw"] = item["end_raw"]
            event["bin_start"] = item["start"]
            event["bin_end"] = item["end"]
        text = (
            f"{direction} [{item['start']:g}, {item['end']:g}) {distribution['unit']}："
            f"{page['total']} 个事件；当前第 {offset + 1 if page['total'] else 0}–"
            f"{offset + len(page['rows'])} 个。"
        )
        return (
            {**page, "bin": item, "direction": direction,
             "reaction_key": key, "dataset_id": dataset_id,
             "reaction_smiles": distribution["selection"].get("reaction_smiles")},
            _event_table_rows(page["rows"]), _event_columns(), text,
        )

    @app.callback(
        Output("rxn-timing-prev-btn", "disabled"),
        Output("rxn-timing-next-btn", "disabled"),
        Output("rxn-timing-open-events-btn", "disabled"),
        Output("rxn-timing-csv-btn", "disabled"),
        Input("rxn-timing-page-store", "data"),
    )
    def _timing_page_actions(page):
        page = page or {}
        total = int(page.get("total") or 0)
        offset = int(page.get("offset") or 0)
        length = len(page.get("rows") or [])
        return offset == 0, offset + length >= total, length == 0, length == 0

    @app.callback(
        Output("event-grid-store", "data", allow_duplicate=True),
        Output("event-grid", "rowData", allow_duplicate=True),
        Output("event-grid", "columnDefs", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-reaction-text", "value", allow_duplicate=True),
        Input("rxn-timing-open-events-btn", "n_clicks"),
        State("rxn-timing-page-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _open_timing_events(_clicks, page, store):
        if not page or page.get("dataset_id") != (store or {}).get("dataset_id"):
            raise PreventUpdate
        rows = page.get("rows") or []
        return (
            {"rows": rows, "kind": "rng_event", "config": {"max_events": 25},
             "meta": {"message": "来自所选时间箱；可选择事件查看详情与轨迹。"}},
            _event_table_rows(rows), _event_columns(),
            "来自所选时间箱；请选择具体事件查看详情与轨迹。",
            page.get("reaction_smiles") or "",
        )

    @app.callback(
        Output("rxn-timing-csv-download", "data"),
        Input("rxn-timing-csv-btn", "n_clicks"),
        State("rxn-timing-page-store", "data"),
        prevent_initial_call=True,
    )
    def _download_timing_events(_clicks, page):
        if not page or not page.get("rows"):
            raise PreventUpdate
        return {"content": svc.rows_to_csv(page["rows"]),
                "filename": "reaction_time_events.csv", "type": "text/csv"}
