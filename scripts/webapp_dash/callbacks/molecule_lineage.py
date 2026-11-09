"""Molecule lineage run, rendering and download callbacks."""

from __future__ import annotations

import re
import time
import json
from typing import Any
import dash_bootstrap_components as dbc
from dash import (
    Input,
    Output,
    State,
    ctx,
    dcc,
    html,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc

from .common import (
    _dt_columns,
)


def _molecule_lineage_event_rows(
    report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    labels = {
        "merge": "合并",
        "split": "拆分",
        "fragment gain": "片段增加",
        "fragment loss": "片段离去",
        "rearrangement": "重排",
        "substitution/transfer": "取代/转移",
        "fast recrossing": "快速回穿",
        "complex": "复杂变化",
    }
    rows = []
    for index, event in enumerate((report or {}).get("event_nodes") or [], 1):
        rows.append(
            {
                "id": str(event.get("event_id") or f"lineage-event-{index}"),
                "event_index": index,
                "event_id": str(event.get("event_id") or ""),
                "timestep_index": event.get("timestep_index"),
                "before_timestep": event.get("before_timestep"),
                "after_timestep": event.get("after_timestep"),
                "category": labels.get(
                    str(event.get("category") or "complex"),
                    str(event.get("category") or "复杂变化"),
                ),
                "reaction_smiles": str(event.get("reaction_smiles") or ""),
                "formed_bonds": ";".join(event.get("formed_bonds") or []),
                "broken_bonds": ";".join(event.get("broken_bonds") or []),
                "recrossing": (
                    "是" if event.get("recrossing_episode_ids") else "否"
                ),
            }
        )
    return rows


def _molecule_lineage_event_columns() -> list[dict[str, Any]]:
    return _dt_columns(
        [
            {"field": "event_index", "headerName": "#", "type": "numericColumn"},
            {"field": "event_id", "headerName": "事件 ID"},
            {"field": "timestep_index", "headerName": "分析帧", "type": "numericColumn"},
            {"field": "before_timestep", "headerName": "反应前 timestep", "type": "numericColumn"},
            {"field": "after_timestep", "headerName": "反应后 timestep", "type": "numericColumn"},
            {"field": "category", "headerName": "结构变化类型"},
            {"field": "reaction_smiles", "headerName": "具体反应"},
            {"field": "formed_bonds", "headerName": "形成键"},
            {"field": "broken_bonds", "headerName": "断裂键"},
            {"field": "recrossing", "headerName": "快速回穿"},
        ]
    )


def _molecule_lineage_result_presentation(
    report: dict[str, Any],
) -> tuple[list[Any], str]:
    summary = report.get("summary") or {}
    trend_labels = {
        "growth": "总体增长",
        "degradation": "总体降解",
        "mixed": "增长/降解并存",
        "structurally stable": "重原子规模不变",
        "undetermined": "趋势未判定",
    }
    summary_chips = [
        html.Span(
            f"查询片段 {int(summary.get('segment_count') or 1)}",
            className="rs-stat-chip",
        ),
        html.Span(
            f"分子节点 {int(summary.get('molecule_node_count') or 0)}",
            className="rs-stat-chip",
        ),
        html.Span(
            f"事件 {int(summary.get('event_count') or 0)}",
            className="rs-stat-chip",
        ),
        html.Span(
            f"快速回穿 {int(summary.get('recrossing_episode_count') or 0)}",
            className="rs-stat-chip",
        ),
        html.Span(
            trend_labels.get(
                str(summary.get("aggregate_trend") or "undetermined"),
                "趋势未判定",
            ),
            className="rs-stat-chip",
        ),
    ]
    branches = report.get("branch_summaries") or []
    continuable = sum(bool(row.get("can_continue")) for row in branches)
    if branches:
        truncation_text = (
            f"当前有 {len(branches)} 条停止分支：{continuable} 条因预算停止、"
            f"可继续追踪；其余 {len(branches) - continuable} 条停在证据边界或连续性断点。"
        )
    else:
        truncation_text = "当前没有未交接的停止分支。"
    return summary_chips, truncation_text


def _molecule_lineage_branch_controls(
    report: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str | None, bool, Any]:
    directions = {"backward": "向前溯源", "forward": "向后追踪"}
    branches = (report or {}).get("branch_summaries") or []
    options = []
    items = []
    first_continuable = None
    for row in branches:
        instance = row.get("molecule_instance") or {}
        branch_id = str(row.get("branch_id") or "")
        can_continue = bool(row.get("can_continue"))
        if can_continue and first_continuable is None:
            first_continuable = branch_id
        direction = directions.get(
            str(row.get("direction") or ""),
            str(row.get("direction") or "方向未知"),
        )
        label = (
            f"{direction} · {instance.get('species') or '-'} · "
            f"frame {instance.get('frame', '-')} · {row.get('stop_reason_label') or '-'}"
        )
        options.append(
            {
                "label": label,
                "value": branch_id,
                "disabled": not can_continue,
            }
        )
        queried = row.get("queried_range") or {}
        range_text = (
            f"frame {queried.get('frame_min', '-')}–{queried.get('frame_max', '-')}"
        )
        items.append(
            html.Li(
                [
                    html.Code(branch_id),
                    f" · {label} · 已查 {range_text}",
                    " · 可继续" if can_continue else " · 不能越过此证据边界",
                ]
            )
        )
    summary = (
        html.Ul(items, className="mb-0 ps-3")
        if items
        else "当前证据内没有停止分支。"
    )
    return options, first_continuable, first_continuable is None, summary


def register(app: Any) -> None:
    """Register molecule lineage callbacks."""

    def _lineage_report_current(report, selected, dataset):
        if not isinstance(report, dict):
            return False
        event_id = str((((selected or {}).get("row") or {}).get("event_id")) or "")
        store = dataset or {}
        revision = store.get("source_revision") or {}
        context = report.get("context") or {}
        return bool(event_id) and (
            str((report.get("query") or {}).get("event_id") or "") == event_id
            and str(context.get("dataset_id") or "") == str(store.get("dataset_id") or "")
            and str((context.get("source_revision") or {}).get("fingerprint") or "")
            == str(revision.get("fingerprint") or "")
        )

    @app.callback(
        Output("molecule-lineage-card", "style"),
        Output("molecule-lineage-participant", "options"),
        Output("molecule-lineage-participant", "value"),
        Output("molecule-lineage-run-btn", "disabled"),
        Input("event-selected-store", "data"),
        Input("event-viewer-store", "data"),
        State("molecule-lineage-store", "data"),
    )
    def _prepare_molecule_lineage(selected, viewer, report):
        row = (selected or {}).get("row") or {}
        report_event_ids = {
            str(event.get("event_id") or "")
            for event in (report or {}).get("event_nodes") or []
        }
        viewer_event_id = str((viewer or {}).get("event_id") or "")
        if (
            not row
            or row.get("association_status") not in {None, "matched"}
            or not viewer
            or (
                viewer_event_id != str(row.get("event_id") or "")
                and viewer_event_id not in report_event_ids
            )
        ):
            return {"display": "none"}, [], None, True
        options = []
        side_labels = {"reactant": "反应物", "product": "产物"}
        for side in ("reactant", "product"):
            for index, participant in enumerate(
                row.get(f"{side}_participants") or []
            ):
                atom_ids = ",".join(
                    map(str, participant.get("atom_ids") or [])
                )
                options.append(
                    {
                        "label": (
                            f"{side_labels[side]} {index + 1} · "
                            f"{participant.get('species') or '?'} · Atom IDs {atom_ids}"
                        ),
                        "value": f"{side}:{index}",
                    }
                )
        return (
            {"display": "block"},
            options,
            options[0]["value"] if options else None,
            not bool(options),
        )

    @app.callback(
        Output("molecule-lineage-anchor-value", "disabled"),
        Output("molecule-lineage-anchor-value", "placeholder"),
        Input("molecule-lineage-anchor-mode", "value"),
    )
    def _configure_lineage_anchor(mode):
        if mode == "elements":
            return False, "元素符号，用逗号分隔，例如 C,O"
        if mode == "atom_ids":
            return False, "Atom IDs，用逗号分隔，例如 6162,6165"
        return True, "当前规则无需填写"

    @app.callback(
        Output("molecule-lineage-response", "data"),
        Input("molecule-lineage-run-btn", "n_clicks"),
        Input("molecule-lineage-continue-btn", "n_clicks"),
        State("molecule-lineage-participant", "value"),
        State("molecule-lineage-anchor-mode", "value"),
        State("molecule-lineage-anchor-value", "value"),
        State("molecule-lineage-depth-backward", "value"),
        State("molecule-lineage-depth-forward", "value"),
        State("molecule-lineage-node-limit", "value"),
        State("molecule-lineage-recross-window", "value"),
        State("molecule-lineage-branch", "value"),
        State("molecule-lineage-continue-depth", "value"),
        State("molecule-lineage-continue-node-limit", "value"),
        State("event-selected-store", "data"),
        State("event-viewer-store", "data"),
        State("app-store", "data"),
        State("molecule-lineage-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output(
                    {
                        "type": "dataset-bound-operation",
                        "name": "molecule-lineage",
                    },
                    "data",
                ),
                True,
                False,
            ),
        ],
    )
    def _run_molecule_lineage(
        n_clicks,
        continue_clicks,
        participant_value,
        anchor_mode,
        anchor_value,
        depth_backward,
        depth_forward,
        node_limit,
        recross_window,
        branch_id,
        continue_depth,
        continue_node_limit,
        selected,
        viewer,
        app_store,
        existing_report,
    ):
        triggered = ctx.triggered_id
        if triggered not in {
            "molecule-lineage-run-btn",
            "molecule-lineage-continue-btn",
        }:
            raise PreventUpdate
        store = app_store or {}
        revision = store.get("source_revision") or {}
        response_key = {
            "selected_event_id": str((((selected or {}).get("row") or {}).get("event_id")) or ""),
            "selected_token": (selected or {}).get("selection_token"),
            "viewer_event_id": str((viewer or {}).get("event_id") or ""),
            "dataset_id": str(store.get("dataset_id") or ""),
            "fingerprint": str(revision.get("fingerprint") or "") if isinstance(revision, dict) else "",
            "clicks": [int(n_clicks or 0), int(continue_clicks or 0)],
        }
        try:
            if triggered == "molecule-lineage-continue-btn":
                if not _lineage_report_current(existing_report, selected, app_store):
                    raise ValueError("事件或来源已变化，请重新运行分子追踪")
                if not branch_id:
                    raise ValueError("请先选择一条可继续追踪的分支")
                report = svc.continue_molecule_lineage_analysis(
                    (app_store or {}).get("artifacts") or {},
                    existing_report,
                    branch_id=str(branch_id),
                    persistent_depth=int(continue_depth),
                    max_molecule_nodes=int(continue_node_limit),
                    recrossing_window=int(recross_window),
                    dataset_id=str((app_store or {}).get("dataset_id") or ""),
                    source_revision=(app_store or {}).get("source_revision") or {},
                )
            else:
                if n_clicks is None:
                    raise PreventUpdate
                if not viewer or str(viewer.get("event_id") or "") != str(
                    (((selected or {}).get("row") or {}).get("event_id")) or ""
                ):
                    raise ValueError("事件轨迹已变化，请重新打开当前事件")
                side, index_text = str(participant_value or "").split(":", 1)
                participant_index = int(index_text)
                tokens = [
                    value
                    for value in re.split(
                        r"[,;\s]+", str(anchor_value or "").strip()
                    )
                    if value
                ]
                anchor_elements = tokens if anchor_mode == "elements" else []
                anchor_atom_ids = (
                    [int(value) for value in tokens]
                    if anchor_mode == "atom_ids"
                    else []
                )
                report = svc.build_molecule_lineage_analysis(
                    (app_store or {}).get("artifacts") or {},
                    (selected or {}).get("row") or {},
                    side=side,
                    participant_index=participant_index,
                    viewer=viewer,
                    anchor_mode=str(anchor_mode or "non_hydrogen"),
                    anchor_elements=anchor_elements,
                    anchor_atom_ids=anchor_atom_ids,
                    depth_backward=int(depth_backward),
                    depth_forward=int(depth_forward),
                    max_molecule_nodes=int(node_limit),
                    recrossing_window=int(recross_window),
                    dataset_id=str((app_store or {}).get("dataset_id") or ""),
                    source_revision=(app_store or {}).get("source_revision") or {},
                )
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            if triggered == "molecule-lineage-continue-btn":
                return {"key": response_key, "preserve_report": True, "values": [
                    None,
                    dbc.Alert(message, color="danger", className="py-2 mb-0"),
                    None, None, None, None, None,
                ]}
            return {"key": response_key, "values": [
                None,
                dbc.Alert(message, color="danger", className="py-2 mb-0"),
                [],
                "",
                {"display": "none"},
                True,
                True,
            ]}
        summary_chips, truncation_text = _molecule_lineage_result_presentation(
            report
        )
        message = (
            "所选分支已继续追踪，并按稳定事件和分子身份合并；既有片段保持不变。"
            if triggered == "molecule-lineage-continue-btn"
            else "分支追踪已完成。图用于总览；下方事件表和导出文件是审计依据。"
        )
        return {"key": response_key, "values": [
            report,
            dbc.Alert(
                message,
                color="success",
                className="py-2 mb-0",
            ),
            summary_chips,
            truncation_text,
            {},
            False,
            False,
        ]}

    app.clientside_callback(
        """function(response, selected, viewer, dataset, runClicks, continueClicks) {
            const none = window.dash_clientside.no_update;
            const discard = () => Array(7).fill(none);
            if (!response || !Array.isArray(response.values) || response.values.length !== 7) {
                return discard();
            }
            const key = response.key || {};
            const revision = (dataset || {}).source_revision || {};
            const clicks = [runClicks, continueClicks].map(value => Number(value || 0));
            if (key.selected_event_id !== String(((((selected || {}).row || {}).event_id) || '')) ||
                key.selected_token !== ((selected || {}).selection_token ?? null) ||
                key.viewer_event_id !== String((viewer || {}).event_id || '') ||
                key.dataset_id !== String((dataset || {}).dataset_id || '') ||
                key.fingerprint !== String(revision.fingerprint || '') ||
                JSON.stringify(key.clicks) !== JSON.stringify(clicks)) {
                return discard();
            }
            if (response.preserve_report) {
                return [none, response.values[1], none, none, none, none, none];
            }
            return response.values;
        }""",
        Output("molecule-lineage-store", "data"),
        Output("molecule-lineage-alert", "children"),
        Output("molecule-lineage-summary", "children"),
        Output("molecule-lineage-truncation", "children"),
        Output("molecule-lineage-results", "style"),
        Output("molecule-lineage-json-btn", "disabled"),
        Output("molecule-lineage-csv-btn", "disabled"),
        Input("molecule-lineage-response", "data"),
        Input("event-selected-store", "data"),
        Input("event-viewer-store", "data"),
        Input("app-store", "data"),
        Input("molecule-lineage-run-btn", "n_clicks"),
        Input("molecule-lineage-continue-btn", "n_clicks"),
    )

    @app.callback(
        Output("molecule-lineage-cytoscape", "elements"),
        Output("molecule-lineage-event-grid", "rowData"),
        Output("molecule-lineage-event-grid", "columnDefs"),
        Output("molecule-lineage-event-grid", "selectedRows"),
        Output("molecule-lineage-branch", "options"),
        Output("molecule-lineage-branch", "value"),
        Output("molecule-lineage-continue-btn", "disabled"),
        Output("molecule-lineage-branch-summary", "children"),
        Input("molecule-lineage-store", "data"),
        Input("molecule-lineage-view", "value"),
        Input("event-selected-store", "data"),
        Input("app-store", "data"),
    )
    def _render_molecule_lineage(report, view, selected, dataset):
        if not _lineage_report_current(report, selected, dataset):
            return (
                [],
                [],
                _molecule_lineage_event_columns(),
                [],
                [],
                None,
                True,
                "当前还没有分支追踪结果。",
            )
        selected_view = (
            str(view) if str(view) in {"raw", "persistent"} else "persistent"
        )
        rows = _molecule_lineage_event_rows(report)
        options, branch_value, disabled, branch_summary = (
            _molecule_lineage_branch_controls(report)
        )
        return (
            ((report.get("views") or {}).get(selected_view) or {}).get(
                "elements"
            )
            or [],
            rows,
            _molecule_lineage_event_columns(),
            [],
            options,
            branch_value,
            disabled,
            branch_summary,
        )

    app.clientside_callback(
        """function(report, selected, dataset) {
            const none = window.dash_clientside.no_update;
            if (!report) return [none, none, none];
            const eventId = String(((((selected || {}).row || {}).event_id) || ''));
            const context = report.context || {};
            const revision = (dataset || {}).source_revision || {};
            if (!eventId || String(((report.query || {}).event_id) || '') !== eventId ||
                String(context.dataset_id || '') !== String((dataset || {}).dataset_id || '') ||
                String(((context.source_revision || {}).fingerprint) || '') !==
                    String(revision.fingerprint || '')) {
                return [{display: 'none'}, true, true];
            }
            return [none, none, none];
        }""",
        Output("molecule-lineage-results", "style", allow_duplicate=True),
        Output("molecule-lineage-json-btn", "disabled", allow_duplicate=True),
        Output("molecule-lineage-csv-btn", "disabled", allow_duplicate=True),
        Input("molecule-lineage-store", "data"),
        Input("event-selected-store", "data"),
        Input("app-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("molecule-lineage-drilldown-store", "data"),
        Input("molecule-lineage-cytoscape", "tapNodeData"),
        Input("molecule-lineage-event-grid", "selectedRows"),
        State("molecule-lineage-event-grid", "rowData"),
        State("molecule-lineage-store", "data"),
        State("event-selected-store", "data"),
        prevent_initial_call=True,
    )
    def _drill_into_lineage_event(
        tapped_node,
        selected_row_ids,
        table_rows,
        report,
        current_selected,
    ):
        event_id = ""
        if ctx.triggered_id == "molecule-lineage-cytoscape":
            if (tapped_node or {}).get("kind") not in {"event", "molecule"}:
                raise PreventUpdate
            event_id = str((tapped_node or {}).get("event_id") or "")
        elif selected_row_ids:
            event_id = str((selected_row_ids[0] or {}).get("id") or "")
        if not event_id:
            raise PreventUpdate
        event = next(
            (
                row
                for row in (report or {}).get("event_nodes") or []
                if str(row.get("event_id") or "") == event_id
            ),
            None,
        )
        if event is None:
            raise PreventUpdate
        selected = {
            "row": event,
            "kind": "rng_event",
            "config": (current_selected or {}).get("config") or {},
            "selection_token": str(time.time_ns()),
        }
        return selected

    @app.callback(
        Output("molecule-lineage-json-download", "data"),
        Input("molecule-lineage-json-btn", "n_clicks"),
        State("molecule-lineage-store", "data"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _download_molecule_lineage_json(n_clicks, report, selected, dataset):
        if n_clicks is None or not _lineage_report_current(report, selected, dataset):
            raise PreventUpdate
        event_id = str((report.get("query") or {}).get("event_id") or "lineage")
        return dcc.send_string(
            json.dumps(report, ensure_ascii=False, indent=2),
            f"molecule-lineage-{event_id}.json",
        )

    @app.callback(
        Output("molecule-lineage-csv-download", "data"),
        Input("molecule-lineage-csv-btn", "n_clicks"),
        State("molecule-lineage-store", "data"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _download_molecule_lineage_csv(n_clicks, report, selected, dataset):
        if n_clicks is None or not _lineage_report_current(report, selected, dataset):
            raise PreventUpdate
        event_id = str((report.get("query") or {}).get("event_id") or "lineage")
        return {
            "content": svc.molecule_lineage_to_csv(report),
            "filename": f"molecule-lineage-{event_id}.csv",
            "type": "text/csv",
        }
