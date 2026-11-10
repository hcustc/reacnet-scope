"""Batch condition comparison callbacks."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
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












def _batch_empty_state(
    title: str = "选择条件组开始对比",
    hint: str = "可直接选择已管理RNG 数据，也可扫描目录并选择自动识别的重复实验组。",
) -> Any:
    return html.Div(
        [
            html.Div(title, className="rs-batch-empty-title"),
            html.Div(hint, className="rs-batch-empty-hint"),
        ],
        className="rs-batch-empty-state",
    )


def _batch_managed_dataset_catalog(
    app_store: dict[str, Any] | None,
    recent_records: list[dict[str, Any]] | None,
    library_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build stable choices from the applied and browser-local recent data."""
    current = app_store if isinstance(app_store, dict) else {}
    candidates: list[dict[str, Any]] = []
    current_folder = str(current.get("folder") or "").strip()
    current_base = str(current.get("base") or "").strip()
    if current_folder and current_base:
        candidates.append(
            {
                "folder": current_folder,
                "base": current_base,
                "label": str(current.get("label") or Path(current_base).name),
                "current": True,
                "reaction_ready": bool((current.get("artifacts") or {}).get("reaction")),
                "dataset_id": str(current.get("dataset_id") or ""),
                "source_revision": current.get("source_revision") or {},
            }
        )
    for record in svc.normalise_dataset_library(library_records):
        candidates.append({**record, "current": False, "imported": True, "reaction_ready": None})
    for record in svc.normalise_recent_datasets(recent_records or []):
        candidates.append({**record, "current": False, "reaction_ready": None})

    datasets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        folder = str(candidate.get("folder") or "").strip()
        base = str(candidate.get("base") or "").strip()
        key = (folder, base)
        if not folder or not base or key in seen:
            continue
        seen.add(key)
        datasets.append(
            {
                "id": base,
                "folder": folder,
                "base": base,
                "label": str(candidate.get("label") or Path(base).name),
                "current": bool(candidate.get("current")),
                "imported": bool(candidate.get("imported")),
                "reaction_ready": candidate.get("reaction_ready"),
                "dataset_id": str(candidate.get("dataset_id") or ""),
                "source_revision": candidate.get("source_revision") or {},
            }
        )
    options = []
    for dataset in datasets:
        prefix = "当前 · " if dataset["current"] else "已导入 · " if dataset.get("imported") else "最近 · "
        missing = dataset["current"] and dataset["reaction_ready"] is False
        suffix = "（缺少 reactionabcd）" if missing else ""
        options.append(
            {
                "label": f"{prefix}{dataset['label']}{suffix}",
                "value": dataset["id"],
                "disabled": missing,
            }
        )
    return {"datasets": datasets, "options": options}


def _build_batch_group_requests(
    managed_selected: list[str] | None,
    managed_payload: dict[str, Any] | None,
    scanned_selected: list[str] | None,
    scanned_payload: dict[str, Any] | None,
    *,
    reviewed_sources: list[dict[str, Any]] | None = None,
    inferred_metadata_confirmed: bool = False,
) -> list[dict[str, Any]]:
    """Resolve UI selections only from their corresponding server payloads."""
    requests: list[dict[str, Any]] = []
    used_names: dict[str, int] = {}

    def unique_group_name(raw_name: str) -> str:
        name = str(raw_name or "条件组").strip() or "条件组"
        count = used_names.get(name, 0) + 1
        used_names[name] = count
        return name if count == 1 else f"{name} ({count})"

    managed_by_id = {
        str(item.get("id") or ""): item
        for item in (managed_payload or {}).get("datasets") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    for dataset_id in managed_selected or []:
        dataset = managed_by_id.get(str(dataset_id))
        if dataset is None:
            raise svc.ServiceError(
                "已管理RNG 数据选择已过期，请重新选择",
                reason="stale_managed_selection",
            )
        label = str(dataset.get("label") or Path(str(dataset.get("base") or "")).name)
        requests.append(
            {
                "group_name": unique_group_name(label),
                "metadata_status": "unknown",
                "conditions": [
                    {
                        "name": label,
                        "label": label,
                        "folder": dataset.get("folder"),
                        "base": dataset.get("base"),
                        "dataset_id": dataset.get("dataset_id"),
                        "source_revision": dataset.get("source_revision") or {},
                        "replicate": None,
                        "metadata_status": "unknown",
                        "simulation_condition_status": "unknown",
                        "replicate_status": "unknown",
                    }
                ],
            }
        )

    scan_data = scanned_payload if isinstance(scanned_payload, dict) else {}
    scanned_conditions = {
        str(item.get("name") or ""): item
        for item in scan_data.get("conditions") or []
        if isinstance(item, dict) and str(item.get("name") or "")
    }
    scanned_groups = {
        str(item.get("group_name") or ""): item
        for item in scan_data.get("groups") or []
        if isinstance(item, dict) and str(item.get("group_name") or "")
    }
    selected_condition_names: list[str] = []
    for selected_group in scanned_selected or []:
        group = scanned_groups.get(str(selected_group))
        if group is None:
            raise svc.ServiceError(
                "扫描条件组选择已过期，请重新扫描",
                reason="stale_scanned_selection",
            )
        for condition_name in group.get("conditions") or []:
            condition = scanned_conditions.get(str(condition_name))
            if condition is None:
                raise svc.ServiceError(
                    f"条件组 {selected_group} 的扫描结果不完整，请重新扫描",
                    reason="incomplete_scanned_group",
                )
            selected_condition_names.append(str(condition_name))

    if selected_condition_names and not inferred_metadata_confirmed:
        raise svc.ServiceError(
            "目录名推断只是不确定建议；请检查表格并勾选确认后再运行",
            reason="unconfirmed_inferred_metadata",
        )

    reviewed_by_name = {
        str(item.get("name") or ""): item
        for item in reviewed_sources or []
        if isinstance(item, dict) and str(item.get("name") or "")
    }
    confirmed_groups: dict[str, list[dict[str, Any]]] = {}
    for condition_name in selected_condition_names:
        original = scanned_conditions[condition_name]
        reviewed = reviewed_by_name.get(condition_name)
        if reviewed is None:
            raise svc.ServiceError(
                f"来源 {condition_name} 缺少检查记录，请重新扫描",
                reason="incomplete_scanned_review",
            )
        confirmed_condition = str(
            reviewed.get("simulation_condition") or ""
        ).strip()
        if not confirmed_condition:
            raise svc.ServiceError(
                f"来源 {condition_name} 必须明确 Simulation Condition",
                reason="missing_simulation_condition",
            )
        try:
            replicate_number = int(reviewed.get("replicate"))
        except (TypeError, ValueError) as exc:
            raise svc.ServiceError(
                f"来源 {condition_name} 的 Replicate 必须是正整数",
                reason="invalid_replicate",
            ) from exc
        if replicate_number < 1:
            raise svc.ServiceError(
                f"来源 {condition_name} 的 Replicate 必须是正整数",
                reason="invalid_replicate",
            )
        model_iteration = str(reviewed.get("model_iteration") or "").strip()
        confirmed_groups.setdefault(confirmed_condition, []).append(
            {
                "name": original.get("name"),
                "label": original.get("name"),
                "folder": original.get("folder"),
                "reaction_file": original.get("reaction_file"),
                "model_iteration": model_iteration,
                "model_iteration_status": (
                    "confirmed" if model_iteration else "unknown"
                ),
                "simulation_condition": confirmed_condition,
                "simulation_condition_status": "confirmed",
                "replicate": replicate_number,
                "replicate_status": "confirmed",
                "metadata_status": "confirmed",
            }
        )
    for condition_name, conditions in confirmed_groups.items():
        requests.append(
            {
                "group_name": unique_group_name(condition_name),
                "metadata_status": "confirmed",
                "conditions": conditions,
            }
        )
    return requests


def register(app: Any) -> None:
    """Register batch callbacks."""

    @app.callback(
        Output("batch-managed-selector", "options"),
        Output("batch-managed-store", "data"),
        Output("batch-managed-status", "children"),
        Input("app-store", "data"),
        Input("recent-datasets", "data"),
        Input("dataset-library", "data"),
    )
    def _refresh_batch_managed_datasets(app_store, recent_records, library_records):
        catalog = _batch_managed_dataset_catalog(app_store, recent_records, library_records)
        options = catalog["options"]
        enabled_count = sum(not bool(option.get("disabled")) for option in options)
        if enabled_count:
            status = (
                f"可选择 {enabled_count} 个已管理RNG 数据；默认只按独立来源比较，"
                "不会把它们称为 Replicate。"
            )
        else:
            status = "暂无含 reactionabcd 的当前RNG 数据；可前往RNG 数据页面选择，或使用下方目录扫描。"
        return options, {"datasets": catalog["datasets"]}, status

    @app.callback(
        Output("batch-root-dir", "value"),
        Input("page-store", "data"),
        Input("batch-use-current-parent-btn", "n_clicks"),
        State("app-store", "data"),
        State("batch-root-dir", "value"),
        prevent_initial_call=True,
    )
    def _suggest_batch_root(page_store, parent_clicks, app_store, current_value):
        triggered = ctx.triggered_id
        if triggered == "page-store" and (page_store or {}).get("page") != "reaction-compare":
            raise PreventUpdate
        if triggered == "page-store" and str(current_value or "").strip():
            raise PreventUpdate
        if triggered == "batch-use-current-parent-btn" and parent_clicks is None:
            raise PreventUpdate
        folder = str((app_store or {}).get("folder") or "").strip()
        if not folder:
            raise PreventUpdate
        return str(Path(folder).expanduser().resolve().parent)

    @app.callback(
        Output("batch-condition-selector", "options"),
        Output("batch-condition-selector", "value"),
        Output("batch-conditions-store", "data"),
        Output("batch-conditions-status", "children"),
        Output("batch-scan-review", "rowData"),
        Output("batch-confirm-inferred-metadata", "value"),
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
            return [], [], None, dbc.Alert(
                f"扫描失败：{exc.message}",
                color="danger",
                className="py-1 px-2 mb-0",
            ), [], []
        except Exception as exc:
            return [], [], None, dbc.Alert(
                f"扫描失败：{exc}",
                color="danger",
                className="py-1 px-2 mb-0",
            ), [], []
        groups = payload.get("groups") or []
        options = [
            {
                "label": f"{g['group_name']} ({g.get('n_sources', g['n_replicates'])} 个来源，待确认)",
                "value": g["group_name"],
            }
            for g in groups
        ]
        warnings = payload.get("warnings") or []
        root_count = int(payload.get("root_count") or 1)
        root_prefix = f"{root_count} 个根目录，" if root_count > 1 else ""
        status_text = (
            f"扫描完成：{root_prefix}{payload.get('total_conditions', 0)} 个来源，"
            f"{payload.get('total_groups', 0)} 个分组建议。目录名结果尚未确认，"
            "请检查/修改上表后勾选确认。"
        )
        status = html.Div(
            [
                html.Span(status_text),
                html.Span(
                    f" 注意：{'；'.join(str(item) for item in warnings[:3])}",
                    className="text-warning",
                )
                if warnings
                else None,
            ]
        )
        review_rows = [
            {
                **condition,
                "source_group": condition.get("group_key"),
                "simulation_condition": condition.get("group_key") or "",
                "metadata_status": "待确认",
            }
            for condition in payload.get("conditions") or []
        ]
        return options, [], payload, status, review_rows, []

    @app.callback(
        Output("batch-confirm-inferred-metadata", "value", allow_duplicate=True),
        Input("batch-scan-review", "cellValueChanged"),
        prevent_initial_call=True,
    )
    def _reset_batch_metadata_confirmation(data_timestamp):
        if data_timestamp is None:
            raise PreventUpdate
        return []

    @app.callback(
        Output("batch-selection-summary", "children"),
        Output("batch-compare-btn", "disabled"),
        Input("batch-managed-selector", "value"),
        Input("batch-condition-selector", "value"),
        Input("batch-scan-review", "rowData"),
        Input("batch-confirm-inferred-metadata", "value"),
        State("batch-managed-store", "data"),
        State("batch-conditions-store", "data"),
    )
    def _summarize_batch_selection(
        managed_selected,
        scanned_selected,
        reviewed_sources,
        confirmation,
        managed_payload,
        scanned_payload,
    ):
        try:
            requests = _build_batch_group_requests(
                managed_selected,
                managed_payload,
                scanned_selected,
                scanned_payload,
                reviewed_sources=reviewed_sources,
                inferred_metadata_confirmed="confirmed" in (confirmation or []),
            )
        except svc.ServiceError as exc:
            return f"选择不可用：{exc.message}", True
        group_count = len(requests)
        source_count = sum(len(item.get("conditions") or []) for item in requests)
        if not group_count:
            return "请选择已管理RNG 数据，或扫描并选择条件组。", True
        comparison_hint = "建议至少选择两个条件组。" if group_count < 2 else "可以开始对比。"
        return (
            f"已选择 {group_count} 个比较组、{source_count} 个来源；{comparison_hint}",
            False,
        )

    @app.callback(
        Output("batch-matrix-grid", "rowData"),
        Output("batch-matrix-grid", "columnDefs"),
        Output("batch-matrix-grid", "selectedRows"),
        Output("batch-alert", "children"),
        Output("batch-matrix-grid-store", "data"),
        Output("batch-grid-container", "style"),
        Output("batch-csv-btn", "disabled"),
        Output("batch-detail-card", "style", allow_duplicate=True),
        Input("batch-compare-btn", "n_clicks"),
        Input("batch-managed-selector", "value"),
        Input("batch-condition-selector", "value"),
        Input("batch-min-detection", "value"),
        Input("batch-top-n", "value"),
        Input("batch-managed-store", "data"),
        Input("batch-conditions-store", "data"),
        Input("batch-scan-review", "rowData"),
        Input("batch-confirm-inferred-metadata", "value"),
        prevent_initial_call=True,
    )
    def _run_batch_comparison(
        n_clicks,
        managed_selected,
        scanned_selected,
        min_detection,
        top_n,
        managed_payload,
        conditions_payload,
        reviewed_sources,
        confirmation,
    ):
        empty_store = {"rows": [], "columns": [], "details": {}, "groups": []}
        if ctx.triggered_id != "batch-compare-btn":
            has_selection = bool(managed_selected or scanned_selected)
            return (
                [],
                [],
                [],
                _batch_empty_state(
                    "对比条件已变化" if has_selection else "选择条件组开始对比",
                    (
                        "确认选择后点击“对比”生成新的统计结果。"
                        if has_selection
                        else "可直接选择已管理RNG 数据，也可扫描目录并检查、确认分组建议。"
                    ),
                ),
                empty_store,
                {"display": "none"},
                True,
                {"display": "none"},
            )
        if n_clicks is None:
            raise PreventUpdate
        try:
            group_requests = _build_batch_group_requests(
                managed_selected,
                managed_payload,
                scanned_selected,
                conditions_payload,
                reviewed_sources=reviewed_sources,
                inferred_metadata_confirmed="confirmed" in (confirmation or []),
            )
            payload = svc.run_grouped_batch_comparison(
                group_requests,
                min_detection_rate=min_detection if min_detection is not None else 0,
                top_n=top_n if top_n is not None else 50,
            )
        except svc.ServiceError as exc:
            return (
                [],
                [],
                [],
                dbc.Alert(str(exc.message), color="danger", className="py-2 mb-0"),
                empty_store,
                {"display": "none"},
                True,
                {"display": "none"},
            )
        except Exception as exc:
            return (
                [],
                [],
                [],
                dbc.Alert(f"批量对比失败：{exc}", color="danger", className="py-2 mb-0"),
                empty_store,
                {"display": "none"},
                True,
                {"display": "none"},
            )

        rows = payload.get("rows") or []
        columns = _dt_columns(payload.get("columns") or [])
        message = (payload.get("meta") or {}).get("message") or "对比完成"
        store = {
            "rows": rows,
            "columns": payload.get("columns") or [],
            "details": payload.get("details") or {},
            "groups": payload.get("groups") or [],
            "meta": payload.get("meta") or {},
            "source_records": payload.get("source_records") or [],
            "identity_basis": payload.get("identity_basis") or "",
            "evidence_scope": payload.get("evidence_scope") or {},
        }
        evidence_message = str(
            (payload.get("evidence_scope") or {}).get("message") or ""
        )
        return (
            rows,
            columns,
            [],
            dbc.Alert(
                [html.Div(message), html.Div(evidence_message, className="small")],
                color="success",
                className="py-2 rs-batch-summary-alert",
            ),
            store,
            {},
            False,
            {"display": "none"},
        )

    @app.callback(
        Output("batch-reaction-chart", "figure"),
        Output("batch-reaction-stats", "children"),
        Output("batch-detail-card", "style"),
        Input("batch-matrix-grid", "selectedRows"),
        State("batch-matrix-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_reaction_detail(selected_row_ids, grid_store):
        if not selected_row_ids:
            return go.Figure(), None, {"display": "none"}
        reaction_id = str((selected_row_ids[0] or {}).get("id") or "")
        detail = ((grid_store or {}).get("details") or {}).get(reaction_id)
        if not isinstance(detail, dict):
            return go.Figure(), None, {"display": "none"}
        groups = detail.get("groups") or []
        group_names = [str(item.get("group_name") or "条件组") for item in groups]
        mean_values = [float(item.get("mean_tp") or 0) for item in groups]
        std_values = [float(item.get("std_tp") or 0) for item in groups]

        fig = go.Figure()
        has_replicate_statistics = any(
            (
                item.get("comparison_mode") == "replicate_statistics"
                or (
                    not item.get("comparison_mode")
                    and item.get("n_replicates") is not None
                )
            )
            for item in groups
        )
        fig.add_trace(
            go.Bar(
                name="组平均 TP" if has_replicate_statistics else "来源 TP",
                x=group_names,
                y=mean_values,
                error_y={"type": "data", "array": std_values, "visible": True},
                text=[f"{value:.2f}" for value in mean_values],
                textposition="auto",
                marker_color="#4f6fdc",
            )
        )
        replicate_x: list[str] = []
        replicate_y: list[float] = []
        replicate_text: list[str] = []
        for group in groups:
            group_name = str(group.get("group_name") or "条件组")
            for replicate in group.get("replicates") or []:
                replicate_x.append(group_name)
                replicate_y.append(float(replicate.get("tp") or 0))
                replicate_text.append(str(replicate.get("name") or "重复实验"))
        if replicate_x:
            fig.add_trace(
                go.Scatter(
                    name="已确认 Replicate" if has_replicate_statistics else "来源",
                    x=replicate_x,
                    y=replicate_y,
                    text=replicate_text,
                    hovertemplate="%{text}<br>TP=%{y}<extra></extra>",
                    mode="markers",
                    marker={"color": "#17243a", "size": 7, "opacity": 0.72},
                )
            )
        fig.update_layout(
            title=f"反应通量对比 — {str(detail.get('reaction_smiles') or '')[:80]}",
            xaxis_title="条件组",
            yaxis_title="TP (Total Passages)",
            barmode="group",
            height=340,
            margin={"l": 50, "r": 20, "t": 40, "b": 80},
            legend={"orientation": "h", "y": 1.12, "x": 0},
        )

        stat_cards = []
        for group in groups:
            replicate_statistics = (
                group.get("comparison_mode") == "replicate_statistics"
                or (
                    not group.get("comparison_mode")
                    and group.get("n_replicates") is not None
                )
            )
            ci_lower = group.get("ci_95_lower", "-")
            ci_upper = group.get("ci_95_upper", "-")
            stat_cards.append(
                html.Div(
                    [
                        html.Div(str(group.get("group_name") or "条件组"), className="rs-batch-stat-title"),
                        html.Div(
                            f"检出 {group.get('detected_count', 0)}/{group.get('n_replicates', 0)} 个来源 · "
                            f"检出率 {group.get('detection_rate', 0):.3f}",
                            className="rs-batch-stat-line",
                        ),
                        html.Div(
                            (
                                f"平均 TP {group.get('mean_tp', 0):.2f} ± {group.get('std_tp', 0):.2f}"
                                if replicate_statistics
                                else f"TP {group.get('mean_tp', 0):.2f}；未声明重复统计"
                            ),
                            className="rs-batch-stat-line",
                        ),
                        html.Div(
                            (
                                f"平均净 TP {group.get('mean_net_tp', 0):.2f} · 95% CI [{ci_lower}, {ci_upper}]"
                                if replicate_statistics
                                else f"净 TP {group.get('mean_net_tp', 0):.2f} · 置信区间不适用"
                            ),
                            className="rs-batch-stat-line",
                        ),
                    ],
                    className="rs-batch-stat-card",
                )
            )
        stats = html.Div(
            [
                html.Div(f"反应式：{detail.get('reaction_smiles', '')}", className="mb-1"),
                html.Div(
                    f"分子式：{detail.get('reaction_formulas', '-') or '-'} · "
                    f"总体检出率：{float(detail.get('detection_rate') or 0):.3f}",
                    className="small text-muted",
                ),
                html.Div(stat_cards, className="rs-batch-stat-grid"),
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
        try:
            content = svc.batch_comparison_package(grid_store)
        except svc.ServiceError:
            raise PreventUpdate
        return dcc.send_bytes(content, "batch_comparison.zip")
