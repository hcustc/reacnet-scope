"""Reaction search, previews, reaction events and reaction CSV callbacks."""

from __future__ import annotations

from .. import ui_components as ui
import time
from typing import Any
from urllib.parse import quote
from dash import (
    Input,
    Output,
    State,
    ctx,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc

from .common import (
    _dt_columns,
    _event_columns,
    _event_table_rows,
    _selected_table_row,
)


def _reaction_columns(*, with_share: bool = False):
    cols = [
        {"field": "rank", "headerName": "#", "width": 70, "type": "numericColumn"},
        {"field": "reaction_formulas", "headerName": "反应式", "flex": 2, "minWidth": 240},
        {"field": "reaction_smiles", "headerName": "Reaction SMILES", "flex": 2, "minWidth": 260},
        {"field": "tp", "headerName": "TP", "width": 85, "type": "numericColumn"},
        {"field": "reverse_tp", "headerName": "Reverse", "width": 95, "type": "numericColumn"},
        {"field": "net_tp", "headerName": "Net", "width": 85, "type": "numericColumn"},
        {"field": "first_time", "headerName": "首次后帧时间", "width": 120, "type": "numericColumn"},
        {"field": "last_time", "headerName": "末次后帧时间", "width": 120, "type": "numericColumn"},
        {"field": "timing_unit", "headerName": "时间单位", "width": 115},
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


def register(app: Any) -> None:
    """Register reaction search callbacks."""

    # ── Species search ──────────────────────────────────────────────

    def _reaction_preview_tooltips(
        rows: list[dict[str, Any]] | None,
        *,
        show_h: bool,
    ) -> list[dict[str, Any]]:
        """Build lazy full-reaction hover cards for reaction-expression cells."""
        tooltips: list[dict[str, Any]] = []
        for row in rows or []:
            reaction_smiles = str(row.get("reaction_smiles") or "").strip()
            if not reaction_smiles:
                tooltips.append({})
                continue
            reaction_formulas = str(
                row.get("reaction_formulas") or reaction_smiles
            ).strip()
            preview_url = (
                "/api/reaction.svg?"
                f"reaction_smiles={quote(reaction_smiles, safe='')}"
                "&width=720&height=220"
                f"&show_h={1 if show_h else 0}"
            )
            preview = {
                "value": (
                    "**完整结构反应式**\n\n"
                    f"![完整结构反应式]({preview_url})\n\n"
                    f"**分子式**  `{reaction_formulas}`\n\n"
                    f"**SMILES**  `{reaction_smiles}`"
                ),
                "type": "markdown",
            }
            tooltips.append(
                {
                    "reaction_formulas": preview,
                    "reaction_smiles": preview,
                }
            )
        return tooltips

    @app.callback(
        Output("rxn-grid-previews", "data"),
        Input("rxn-grid", "rowData"),
        Input("rxn-structure-show-h", "value"),
    )
    def _preview_searched_reactions(rows, show_h):
        return _reaction_preview_tooltips(rows, show_h=bool(show_h))

    @app.callback(
        Output("rxn-production-grid-previews", "data"),
        Output("rxn-consumption-grid-previews", "data"),
        Input("rxn-production-grid", "rowData"),
        Input("rxn-consumption-grid", "rowData"),
        Input("rxn-channel-show-h", "value"),
    )
    def _preview_species_channels(production_rows, consumption_rows, show_h):
        return (
            _reaction_preview_tooltips(production_rows, show_h=bool(show_h)),
            _reaction_preview_tooltips(consumption_rows, show_h=bool(show_h)),
        )

    # ── Reaction formula search ─────────────────────────────────────

    @ui.guarded_query(app,
        Output("rxn-grid", "rowData"),
        Output("rxn-grid", "columnDefs"),
        Output("rxn-grid", "selectedRows"),
        Output("rxn-grid", "cellClicked"),
        Output("rxn-alert", "children"),
        Output("rxn-grid-store", "data"),
        Output("rxn-initial-state", "style"),
        Output("rxn-results-content", "style"),
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
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "reactions"}, "data"),
                True,
                False,
            ),
        ],
    )
    def _search_reactions(
        n_clicks,
        reactants,
        products,
        mode,
        top,
        with_share,
        share_metric,
        share_abs,
        share_positive,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        from ..ui_state import reaction_query

        submitted = reaction_query(reactants, products, mode, top, with_share,
                                   share_metric, share_abs, share_positive)
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            result = svc.search_reactions_by_formula(
                artifacts,
                reactants or "",
                products or "",
                mode=mode or "exact",
                top=int(top if top is not None else 50),
                with_share=bool(with_share),
                share_metric=share_metric or "net_tp",
                share_abs_metric=bool(share_abs),
                share_positive_only=bool(share_positive),
            )
        except (svc.ServiceError, ValueError, TypeError) as exc:
            message = str(getattr(exc, "message", exc))
            return (
                [],
                _reaction_columns(),
                [],
                None,
                message,
                {"rows": [], "state": "error", "message": message, "query": submitted},
                {"display": "none"},
                {"display": "block"},
            )
        rows = result.get("rows") or []
        timing = (result.get("meta") or {}).get("timing") or {}
        timing_notice = (
            "事件索引未就绪，首次/末次时间不可用；请在管理数据中准备或重建事件索引。"
            if str(timing.get("status") or "").startswith("event_index_") or timing.get("status") == "missing_reactionevent"
            else "未确认 timestep → ps 换算；时间列保留原始坐标。"
            if timing.get("status") == "time_conversion_missing"
            else "当前事件源只有分析帧序号；时间列单位为 analyzed_frame。"
            if timing.get("status") == "analyzed_frame_only" else None
        )
        return (
            rows,
            _reaction_columns(with_share=bool(with_share)),
            [],
            None,
            f"找到 {len(rows)} 条反应" + (f" · {timing_notice}" if timing_notice else ""),
            {"rows": rows, "meta": result.get("meta", {}), "query": submitted,
             "state": "ready" if rows else "empty"},
            {"display": "none"},
            {"display": "block"},
        )

    @app.callback(
        Output("reaction-event-request", "data"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("rxn-channel-to-event-btn", "n_clicks"),
        State("rxn-grid", "selectedRows"),
        State("rxn-grid", "rowData"),
        State("rxn-channel-selection-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _request_reaction_events(_search_clicks, _channel_clicks, selected_rows,
                                 table_rows, channel_selection, dataset):
        trigger = ctx.triggered_id
        if trigger == "rxn-to-event-btn" and _search_clicks:
            row = _selected_table_row(selected_rows, table_rows)
        elif trigger == "rxn-channel-to-event-btn" and _channel_clicks:
            row = (channel_selection or {}).get("row")
        else:
            raise PreventUpdate
        store = dataset or {}
        revision = store.get("source_revision") or {}
        return {
            "request_id": str(time.time_ns()),
            "reaction_smiles": str((row or {}).get("reaction_smiles") or ""),
            "dataset_id": str(store.get("dataset_id") or ""),
            "fingerprint": str(revision.get("fingerprint") or "") if isinstance(revision, dict) else "",
            "origin": "channel" if trigger == "rxn-channel-to-event-btn" else "reaction_search",
        }

    @app.callback(
        Output("reaction-event-request", "data", allow_duplicate=True),
        Input("reaction-event-close-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _close_reaction_events(clicks):
        if not clicks:
            raise PreventUpdate
        return None

    @app.callback(
        Output("reaction-event-request", "data", allow_duplicate=True),
        Input("rxn-grid", "selectedRows"),
        Input("rxn-channel-selection-store", "data"),
        State("reaction-event-request", "data"),
        prevent_initial_call=True,
    )
    def _clear_reaction_events_for_new_selection(selected_rows, channel_selection, request):
        if not request:
            raise PreventUpdate
        origin = request.get("origin")
        if origin == "reaction_search" and ctx.triggered_id == "rxn-grid":
            row = (selected_rows or [None])[0] or {}
        elif origin == "channel" and ctx.triggered_id == "rxn-channel-selection-store":
            row = (channel_selection or {}).get("row") or {}
        else:
            raise PreventUpdate
        if str(row.get("reaction_smiles") or "") != request.get("reaction_smiles"):
            return None
        raise PreventUpdate

    @app.callback(
        Output("reaction-event-response", "data"),
        Input("reaction-event-request", "data"),
        State("app-store", "data"),
    )
    def _read_reaction_events(request, dataset):
        if not request:
            return None
        store = dataset or {}
        revision = store.get("source_revision") or {}
        if (request.get("dataset_id") != str(store.get("dataset_id") or "")
                or request.get("fingerprint") != (str(revision.get("fingerprint") or "") if isinstance(revision, dict) else "")):
            return {"request": request, "error": "来源已经变化，请重新选择反应。"}
        reaction = str(request.get("reaction_smiles") or "").strip()
        if not reaction:
            return {"request": request, "error": "请先选择一条精确反应类型。"}
        try:
            result = svc.locate_rng_events(store.get("artifacts") or {}, reaction, max_events=100)
        except svc.ServiceError as exc:
            return {"request": request, "error": exc.message}
        return {"request": request, "rows": result.get("rows") or [], "meta": result.get("meta") or {}}

    app.clientside_callback(
        """function(response, request, dataset) {
            if (!request || !response || !response.request ||
                response.request.request_id !== request.request_id) return null;
            const revision = (dataset || {}).source_revision || {};
            if (request.dataset_id !== ((dataset || {}).dataset_id || '') ||
                request.fingerprint !== (revision.fingerprint || '')) return null;
            return response;
        }""",
        Output("reaction-event-store", "data"),
        Input("reaction-event-response", "data"),
        Input("reaction-event-request", "data"),
        Input("app-store", "data"),
    )

    @app.callback(
        Output("reaction-event-panel", "style"),
        Output("reaction-event-status", "children"),
        Output("reaction-event-grid", "rowData"),
        Output("reaction-event-grid", "columnDefs"),
        Output("reaction-event-grid", "selectedRows"),
        Input("reaction-event-store", "data"),
    )
    def _render_reaction_events(response):
        if not response:
            return {"display": "none"}, "", [], _event_columns(), []
        if response.get("error"):
            return {}, response["error"], [], _event_columns(), []
        rows = response.get("rows") or []
        status = str((response.get("meta") or {}).get("message") or f"找到 {len(rows)} 条事件")
        if len(rows) >= 100:
            status += "；当前仅请求前 100 条，列表可能被截断。"
        return {}, status, _event_table_rows(rows), _event_columns(), []

    @app.callback(
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-bookmark-store", "data", allow_duplicate=True),
        Input("reaction-event-grid", "selectedRows"),
        State("reaction-event-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _select_reaction_event(selected_rows, response, dataset):
        if not selected_rows or not response or response.get("error"):
            raise PreventUpdate
        event_id = str((selected_rows[0] or {}).get("id") or "")
        row = next((item for item in response.get("rows") or []
                    if str(item.get("event_id") or "") == event_id), None)
        if row is None:
            raise PreventUpdate
        request = response.get("request") or {}
        selected = {"row": row, "kind": "rng_event",
                    "config": {"reaction_text": request.get("reaction_smiles") or "",
                               "before_frames": 3, "after_frames": 3},
                    "origin": {"kind": request.get("origin") or "reaction_search"},
                    "selection_token": str(time.time_ns())}
        try:
            bookmark = svc.create_event_bookmark(dataset or {}, row)
        except svc.ServiceError:
            bookmark = None
        return selected, bookmark

    @app.callback(
        Output("rxn-query-card", "style"),
        Output("rxn-results-card", "style"),
        Output("rxn-channel-view", "style"),
        Output("rxn-channel-history-store", "data"),
        Input("species-to-channels-btn", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        prevent_initial_call=True,
    )
    def _toggle_reaction_view(
        _channel_clicks,
        _nav_clicks,
    ):
        if ctx.triggered_id == "species-to-channels-btn":
            return (
                {"display": "none"},
                {"display": "none"},
                {"display": "block"},
                [{"kind": "species", "label": "返回物种检索"}],
            )
        return {}, {}, {"display": "none"}, []

    @app.callback(
        Output("event-reaction-text", "value"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("rxn-channel-to-event-btn", "n_clicks"),
        State("rxn-grid", "selectedRows"),
        State("rxn-grid", "rowData"),
        State("rxn-channel-selection-store", "data"),
        prevent_initial_call=True,
    )
    def _send_reaction_to_event(
        n_clicks,
        channel_clicks,
        selected_rows,
        rows,
        channel_selection,
    ):
        if ctx.triggered_id == "rxn-channel-to-event-btn":
            if channel_clicks is None:
                raise PreventUpdate
            reaction = str(
                (
                    (channel_selection or {}).get("row") or {}
                ).get("reaction_smiles")
                or ""
            )
            if not reaction:
                raise PreventUpdate
            return reaction
        if n_clicks is None or not selected_rows:
            raise PreventUpdate
        rows = rows or []
        row = _selected_table_row(selected_rows, rows)
        if row is None:
            raise PreventUpdate
        return str(row.get("reaction_smiles") or "")

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
