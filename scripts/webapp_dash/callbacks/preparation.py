"""Index preparation, task progress and index clearing callbacks."""

from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any
import dash_bootstrap_components as dbc
from dash import (
    ALL,
    Input,
    Output,
    State,
    ctx,
    dcc,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc

from .common import (
    _CAPABILITY_LABELS,
    _CAPABILITY_STATE_LABELS,
    _capability_state_class,
    _format_bytes,
    _triggered_click_value,
    _validated_dataset_target,
)


def _render_analysis_capabilities(
    capabilities: dict[str, Any] | None,
    *,
    class_name: str = "rs-analysis-capability-list",
) -> Any:
    values = dict(capabilities or {})
    if not values:
        return html.Div(
            "尚无可检查的分析功能。",
            className="rs-preparation-task-empty",
        )
    items: list[Any] = []
    for key, label in _CAPABILITY_LABELS.items():
        evidence = dict(values.get(key) or {})
        state = _capability_state_class(evidence.get("state") or "missing-source")
        items.append(
            html.Div(
                [
                    html.Span(label, className="rs-capability-name"),
                    html.Span(
                        _CAPABILITY_STATE_LABELS.get(state, state),
                        className=f"rs-capability-state is-{state}",
                    ),
                    html.Span(
                        str(evidence.get("reason") or "状态原因暂不可用。"),
                        className="rs-capability-reason",
                    ),
                ],
                className="rs-analysis-capability-item",
                **{"data-capability": key, "data-state": state},
            )
        )
    return html.Div(items, className=class_name)


def _preparation_state_text(item: dict[str, Any]) -> tuple[str, str]:
    state = str(item.get("state") or "missing")
    task = dict(item.get("task") or {})
    trusted_progress = task.get("progress") if task.get("progress_trusted") else None
    preparing_text = (
        f"准备中 · {float(trusted_progress) * 100:.0f}%"
        if isinstance(trusted_progress, (int, float))
        else "准备中"
    )
    labels = {
        "ready": ("可用", "success"),
        "building": (preparing_text, "warning"),
        "stale": ("需要重建", "warning"),
        "invalid": ("索引无效", "danger"),
        "needs_preparation": ("尚未建立", "secondary"),
        "missing_source": ("缺少源文件", "secondary"),
        "missing": ("尚未建立", "secondary"),
    }
    return labels.get(state, (state, "secondary"))


def _preparation_item_detail(label: str, item: dict[str, Any]) -> str:
    if item.get("capability_reason"):
        return str(item["capability_reason"])
    detail = ""
    if label == "事件索引" and item.get("source_size"):
        if item.get("state") == "building":
            detail = (
                f"{_format_bytes(item.get('source_offset'))} / "
                f"{_format_bytes(item.get('source_size'))}"
            )
        elif item.get("state") == "ready":
            detail = (
                f"{int(item.get('event_count') or 0):,} 个事件 · "
                f"{_format_bytes(item.get('index_size'))}"
            )
        else:
            detail = f"源文件 {_format_bytes(item.get('source_size'))}"
    elif label == "轨迹帧索引" and item.get("trajectory_size"):
        detail = (
            f"{_format_bytes(item.get('source_offset'))} / "
            f"{_format_bytes(item.get('trajectory_size'))}"
        )
    elif label == "元素分布索引" and item.get("species_size"):
        detail = (
            f"{_format_bytes(item.get('source_offset'))} / "
            f"{_format_bytes(item.get('species_size'))}"
        )
    if item.get("state") == "ready":
        records = (
            item.get("frames")
            if label == "轨迹帧索引"
            else item.get("timepoints")
            if label == "元素分布索引"
            else None
        )
        if records is not None:
            detail = (
                f"{int(records):,} 条记录 · "
                f"{_format_bytes(item.get('index_size'))}"
            )
    if item.get("message"):
        detail = str(item["message"])
    if label == "事件索引" and item.get("source_kind"):
        source_label = (
            "原生 HDF5"
            if item.get("source_kind") == "native_hdf5"
            else "兼容 CSV"
        )
        schema = str(item.get("source_schema_version") or "")
        capabilities = set(item.get("capabilities") or [])
        capability_label = (
            "反应 + 分子证据"
            if "molecule" in capabilities
            else "仅反应证据"
        )
        source_detail = " · ".join(
            value
            for value in (
                source_label,
                f"schema {schema}" if schema else "",
                capability_label,
            )
            if value
        )
        detail = f"{source_detail} · {detail}" if detail else source_detail
    return detail


def _render_preparation_item(label: str, item: dict[str, Any]) -> Any:
    text, _color = _preparation_state_text(item)
    state = str(item.get("state") or "missing")
    return html.Div(
        [
            html.Span(
                text,
                className=f"rs-index-state is-{state.replace('_', '-')}",
            ),
            html.Span(
                _preparation_item_detail(label, item),
                className="rs-index-status-detail",
            ),
        ],
        className="rs-index-status-value",
    )


def _recommended_preparation_kind(payload: dict[str, Any]) -> str | None:
    for kind, key in (
        ("event", "events"),
        ("trajectory", "trajectory"),
        ("composition", "composition"),
    ):
        item = payload.get(key) or {}
        if (
            str(item.get("state") or "missing") != "ready"
            and item.get("source_available") is not False
        ):
            return kind
    return None


def _render_next_preparation_action(
    recommended_kind: str | None,
) -> Any:
    if recommended_kind is None:
        return html.Div(
            [
                html.Div("当前状态", className="rs-next-action-kicker"),
                html.Div("已就绪的能力可以直接使用", className="rs-next-action-title"),
                html.Div(
                    "各项分析功能独立可用，无需等待无关索引。",
                    className="rs-next-action-copy",
                ),
            ]
        )
    title, copy = {
        "event": (
            "建立事件索引",
            "启用反应事件检索、路径证据与事件跳转。",
        ),
        "trajectory": (
            "建立轨迹帧索引",
            "启用按时间步定位帧和局部反应轨迹提取。",
        ),
        "composition": (
            "建立物种丰度 / 元素分布索引",
            "启用物种时间演化、元素分布演化分析和代表物种下钻。",
        ),
    }[recommended_kind]
    return html.Div(
        [
            html.Div("建议下一步", className="rs-next-action-kicker"),
            html.Div(title, className="rs-next-action-title"),
            html.Div(copy, className="rs-next-action-copy"),
            html.Div(
                "可在下方对应卡片中准备；物种检索可立即开始。",
                className="rs-next-action-direction",
            ),
        ]
    )


def _render_preparation_status(payload: dict[str, Any]) -> dict[str, Any]:
    alert: Any = ""
    if payload.get("workspace_resolved") is False:
        alert = dbc.Alert(
            "无法为当前RNG 数据确定 Dataset Workspace；请检查RNG 数据路径和访问权限。",
            color="warning",
            className="py-2 mb-0",
        )
    elif payload.get("workspace_writable") is False:
        alert = dbc.Alert(
            "Dataset Workspace 不可写；请检查RNG 数据目录，或由管理员配置集中位置。",
            color="danger",
            className="py-2 mb-0",
        )
    updated = payload.get("last_updated_epoch")
    updated_text = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
        if updated
        else "-"
    )
    workspace_path = str(payload.get("workspace_path") or "未配置")
    workspace_meta = html.Div(
        [
            html.Div(
                [
                    html.Span("实际 Workspace 位置"),
                    html.Code(workspace_path, className="rs-cache-path"),
                    dcc.Clipboard(
                        content=workspace_path,
                        title="复制 Workspace 路径",
                    ),
                ],
                className="rs-cache-meta-row",
            ),
            html.Div(
                [
                    html.Span("已发布索引占用"),
                    html.Code(_format_bytes(payload.get("index_bytes"))),
                ],
                className="rs-cache-meta-row",
            ),
            html.Div(
                [
                    html.Span("RNG 数据 ID"),
                    html.Code(payload.get("dataset_id") or "-"),
                ],
                className="rs-cache-meta-row",
            ),
            html.Div(
                [
                    html.Span("最后更新"),
                    html.Code(updated_text),
                ],
                className="rs-cache-meta-row",
            ),
        ],
        className="rs-cache-meta-details",
    )
    recommended_kind = _recommended_preparation_kind(payload)
    capabilities = dict(payload.get("analysis_capabilities") or {})

    def with_reason(item: dict[str, Any], capability_key: str) -> dict[str, Any]:
        return {
            **item,
            "capability_reason": str(
                (capabilities.get(capability_key) or {}).get("reason") or ""
            ),
        }

    items = {
        "basic": ("基础分析文件", payload.get("basic") or {}),
        "event": (
            "事件索引",
            with_reason(payload.get("events") or {}, "event_search"),
        ),
        "trajectory": (
            "轨迹帧索引",
            with_reason(payload.get("trajectory") or {}, "trajectory_evidence"),
        ),
        "composition": (
            "元素分布索引",
            with_reason(payload.get("composition") or {}, "element_distribution"),
        ),
    }
    building_kind = next(
        (
            kind
            for kind, (_label, item) in items.items()
            if item.get("state") == "building"
        ),
        None,
    )
    global_status = (
        f"索引任务 · {items[building_kind][0]}"
        if building_kind
        else "分析功能按项显示"
    )
    return {
        "basic": _render_analysis_capabilities(payload.get("analysis_capabilities") or {}),
        "event": _render_preparation_item(*items["event"]),
        "trajectory": _render_preparation_item(*items["trajectory"]),
        "composition": _render_preparation_item(*items["composition"]),
        "meta": workspace_meta,
        "alert": alert,
        "next_action": _render_next_preparation_action(recommended_kind),
        "global_status": global_status,
        "global_class": "rs-index-global-state",
        "refresh_label": f"状态自动刷新 · {updated_text}",
        "recommended_kind": recommended_kind,
    }


def _render_preparation_tasks(tasks: list[dict[str, Any]]) -> Any:
    if not tasks:
        return None
    capability_labels = {
        "event": "事件检索",
        "trajectory": "轨迹证据",
        "composition": "元素分布",
        "lewis": "Lewis 注释",
    }
    state_labels = {
        "running": "running · 运行中",
        "cancel_requested": "running · 正在取消",
        "completed": "succeeded · 已完成",
        "canceled": "cancelled · 已取消",
        "interrupted": "failed · 已中断，可续建",
        "failed": "failed · 失败",
        "superseded": "superseded · 源修订已变化",
    }
    active_cards: list[Any] = []
    history_cards: list[Any] = []
    for task in tasks:
        state = str(task.get("state") or "failed")
        revision = dict(task.get("source_revision") or {})
        fingerprint = str(revision.get("fingerprint") or "")
        phase = str(task.get("phase") or task.get("message") or "未报告阶段")
        progress = task.get("progress") if task.get("progress_trusted") else None
        progress_text = (
            f"可信进度 {float(progress) * 100:.0f}%"
            if isinstance(progress, (int, float))
            else "未提供可信百分比"
        )
        capability = str(task.get("capability") or "")
        is_active = state in {"running", "cancel_requested"}
        task_action = (
            dbc.Button(
                "取消任务",
                id={
                    "type": "preparation-task-cancel",
                    "dataset": str(task.get("dataset_id") or ""),
                    "capability": capability,
                },
                color="secondary",
                size="sm",
                outline=True,
            )
            if is_active
            else dbc.Button(
                "移除记录",
                id={
                    "type": "preparation-task-dismiss",
                    "dataset": str(task.get("dataset_id") or ""),
                    "capability": capability,
                },
                color="secondary",
                size="sm",
                outline=True,
                title="仅移除任务记录；保留索引、检查点和源数据",
            )
        )
        card = html.Div(
            [
                html.Div(
                    [
                        html.Strong(str(task.get("dataset_label") or "未命名RNG 数据")),
                        html.Span(
                            f"Dataset Identity {task.get('dataset_id') or '-'}",
                            className="rs-preparation-task-meta",
                        ),
                        html.Span(
                            f"源修订 {fingerprint[:12] or '-'}",
                            className="rs-preparation-task-meta",
                        ),
                    ],
                    className="rs-preparation-task-identity",
                ),
                html.Div(
                    [
                        html.Strong(capability_labels.get(capability, capability)),
                        html.Span(
                            state_labels.get(state, state),
                            className="rs-preparation-task-state",
                        ),
                    ],
                    className="rs-preparation-task-identity",
                ),
                html.Div(
                    [
                        html.Span(f"阶段：{phase}"),
                        html.Span(progress_text, className="rs-preparation-task-meta"),
                    ],
                    className="rs-preparation-task-progress",
                ),
                task_action,
            ],
            className="rs-preparation-task-card",
            **{
                "data-dataset-id": str(task.get("dataset_id") or ""),
                "data-source-revision": fingerprint,
                "data-capability": capability,
                "data-state": state,
            },
        )
        if is_active:
            active_cards.append(card)
        else:
            history_cards.append(card)
    history = (
        html.Details(
            [
                html.Summary(f"历史任务（{len(history_cards)}）"),
                html.Div(history_cards, className="rs-preparation-task-history-list"),
            ],
            className="rs-preparation-task-history",
        )
        if history_cards
        else None
    )
    return html.Div(
        [
            *active_cards,
            history,
        ],
        className="rs-preparation-task-groups",
    )


def register(app: Any) -> None:
    """Register preparation callbacks."""

    @app.callback(
        Output("data-prep-basic-status", "children"),
        Output("data-prep-event-status", "children"),
        Output("data-prep-trajectory-status", "children"),
        Output("data-prep-composition-status", "children"),
        Output("data-prep-cache-meta", "children"),
        Output("data-prep-status-alert", "children"),
        Output("data-next-action", "children"),
        Output("topbar-index-status", "children"),
        Output("topbar-index-status", "className"),
        Output("data-prep-refresh-label", "children"),
        Output("data-prep-event-command", "children"),
        Output("data-prep-trajectory-command", "children"),
        Output("data-prep-composition-command", "children"),
        Output("data-prep-event-copy", "content"),
        Output("data-prep-trajectory-copy", "content"),
        Output("data-prep-composition-copy", "content"),
        Output("data-clear-event-btn", "disabled"),
        Output("data-clear-trajectory-btn", "disabled"),
        Output("data-clear-composition-btn", "disabled"),
        Output("data-prep-refresh", "disabled"),
        Output("data-prep-event-btn", "className"),
        Output("data-prep-trajectory-btn", "className"),
        Output("data-prep-composition-btn", "className"),
        Output("data-prep-event-btn", "children"),
        Output("data-prep-trajectory-btn", "children"),
        Output("data-prep-composition-btn", "children"),
        Input("page-store", "data"),
        Input("data-prep-refresh-btn", "n_clicks"),
        Input("data-prep-refresh", "n_intervals"),
        Input("dataset-browser-candidate", "data"),
        Input("data-prep-cancel-result", "data"),
        State("app-store", "data"),
    )
    def _refresh_preparation_status(
        page_store,
        _refresh_clicks,
        _tick,
        candidate,
        cancel_result,
        app_store,
    ):
        if (page_store or {}).get("page") != "data-management":
            return (
                "", "", "", "", "", "", "", "",
                "rs-index-global-state", "状态自动刷新",
                "", "", "", "", "", "",
                True, True, True, True,
                "rs-index-action", "rs-index-action", "rs-index-action",
                "准备索引", "准备索引", "准备索引",
            )
        selected = {}
        current = app_store if isinstance(app_store, dict) else {}
        if not (
            (selected.get("folder") and selected.get("base"))
            or (current.get("folder") and current.get("base"))
        ):
            return (
                "", "", "", "", "", "", "", "未加载RNG 数据",
                "rs-index-global-state", "等待选择RNG 数据",
                "", "", "", "", "", "",
                True, True, True, True,
                "rs-index-action", "rs-index-action", "rs-index-action",
                "准备索引", "准备索引", "准备索引",
            )
        try:
            target = _validated_dataset_target(None, app_store=app_store)
            payload = svc.dataset_preparation_status(
                target["folder"],
                base=target["base"],
            )
        except svc.ServiceError as exc:
            error = str(exc.message)
            return (
                "", "", "", "", "", error, "", "状态不可用",
                "rs-index-global-state is-partial", "状态读取失败",
                "", "", "", "", "", "",
                True, True, True, True,
                "rs-index-action", "rs-index-action", "rs-index-action",
                "准备索引", "准备索引", "准备索引",
            )
        except Exception as exc:
            error = f"读取准备状态失败: {exc}"
            return (
                "", "", "", "", "", error, "", "状态不可用",
                "rs-index-global-state is-partial", "状态读取失败",
                "", "", "", "", "", "",
                True, True, True, True,
                "rs-index-action", "rs-index-action", "rs-index-action",
                "准备索引", "准备索引", "准备索引",
            )

        events = payload.get("events") or {}
        trajectory = payload.get("trajectory") or {}
        composition = payload.get("composition") or {}

        def clear_disabled(item: dict[str, Any]) -> bool:
            return str(item.get("state") or "missing") not in {
                "ready",
                "stale",
                "invalid",
                # Keep the action reachable while a task is running so the
                # click can explain why clearing must wait and how to proceed.
                "building",
            }

        rendered = _render_preparation_status(payload)
        status_alert = rendered["alert"]
        if isinstance(cancel_result, dict) and cancel_result:
            status_alert = dbc.Alert(
                str(cancel_result.get("message") or "取消请求已提交。"),
                color="info" if cancel_result.get("ok") else "danger",
                className="py-2 mb-0",
            )
        recommended_kind = rendered["recommended_kind"]

        def action_class(kind: str) -> str:
            item = {
                "event": events,
                "trajectory": trajectory,
                "composition": composition,
            }.get(kind, {})
            if str(item.get("state") or "") == "ready":
                return "rs-index-action is-ready"
            return (
                "rs-index-action is-recommended"
                if kind == recommended_kind
                else "rs-index-action"
            )

        def action_label(item: dict[str, Any]) -> str:
            task_state = str((item.get("task") or {}).get("state") or "")
            if task_state in {"interrupted", "canceled", "failed", "superseded"}:
                return "续建索引"
            if str(item.get("state") or "") in {"ready", "stale", "invalid"}:
                return "重新构建"
            if task_state in {"running", "cancel_requested"}:
                return "任务运行中"
            return "准备索引"

        refresh_active = any(
            str(item.get("state") or "") == "building"
            or str((item.get("task") or {}).get("state") or "")
            in {"running", "cancel_requested"}
            for item in (events, trajectory, composition)
        )
        refresh_label = (
            rendered["refresh_label"]
            if refresh_active
            else str(rendered["refresh_label"]).replace("状态自动刷新", "状态已更新")
        )

        return (
            rendered["basic"],
            rendered["event"],
            rendered["trajectory"],
            rendered["composition"],
            rendered["meta"],
            status_alert,
            rendered["next_action"],
            rendered["global_status"],
            rendered["global_class"],
            refresh_label,
            payload.get("event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
            payload.get("event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
            clear_disabled(events),
            clear_disabled(trajectory),
            clear_disabled(composition),
            not refresh_active,
            action_class("event"),
            action_class("trajectory"),
            action_class("composition"),
            action_label(events),
            action_label(trajectory),
            action_label(composition),
        )

    @app.callback(
        Output("data-preparation-tasks", "children"),
        Output("preparation-task-snapshot", "data"),
        Output("global-dataset-notice", "children", allow_duplicate=True),
        Input("preparation-task-refresh", "n_intervals"),
        Input("dataset-browser-candidate", "data"),
        Input("app-store", "data"),
        Input("recent-datasets", "data"),
        Input("dataset-library", "data"),
        Input("data-prep-cancel-result", "data"),
        State("preparation-task-snapshot", "data"),
        prevent_initial_call=True,
    )
    def _refresh_persisted_preparation_tasks(
        _tick,
        candidate,
        app_store,
        recent_records,
        library_records,
        _task_action_result,
        previous_tasks,
    ):
        targets = [
            candidate if isinstance(candidate, dict) else {},
            app_store if isinstance(app_store, dict) else {},
            *(recent_records if isinstance(recent_records, list) else []),
            *(library_records if isinstance(library_records, list) else []),
            *(previous_tasks if isinstance(previous_tasks, list) else []),
        ]
        tasks = svc.list_preparation_tasks(targets)
        previous = {
            (str(item.get("dataset_id") or ""), str(item.get("capability") or "")): item
            for item in (previous_tasks if isinstance(previous_tasks, list) else [])
            if isinstance(item, dict)
        }
        current_dataset_id = str((app_store or {}).get("dataset_id") or "")
        notice: Any = no_update
        if ctx.triggered_id == "preparation-task-refresh":
            completed = next(
                (
                    task
                    for task in tasks
                    if task.get("state") == "completed"
                    and str(task.get("dataset_id") or "") != current_dataset_id
                    and str(
                        previous.get(
                            (
                                str(task.get("dataset_id") or ""),
                                str(task.get("capability") or ""),
                            ),
                            {},
                        ).get("state")
                        or ""
                    )
                    in {"running", "cancel_requested"}
                ),
                None,
            )
            if completed:
                capability_label = {
                    "event": "事件检索",
                    "trajectory": "轨迹证据",
                    "composition": "元素分布",
                    "lewis": "Lewis 注释",
                }.get(str(completed.get("capability") or ""), "分析功能")
                notice = dbc.Alert(
                    f"{completed.get('dataset_label') or '其他RNG 数据'} 的"
                    f"{capability_label}索引任务已完成。",
                    color="info",
                    className="mb-0",
                )
        return _render_preparation_tasks(tasks), tasks, notice

    @app.callback(
        Output("preparation-task-refresh", "disabled"),
        Input("preparation-task-snapshot", "data"),
        Input("import-auto-request", "data"),
        Input({"type": "library-build-request", "base": ALL, "kind": ALL}, "data"),
        Input("data-prep-event-btn", "n_clicks"),
        Input("data-prep-trajectory-btn", "n_clicks"),
        Input("data-prep-composition-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _toggle_preparation_task_refresh(
        task_snapshot,
        _auto_request,
        _library_request,
        _event_clicks,
        _trajectory_clicks,
        _composition_clicks,
    ):
        trigger = ctx.triggered_id
        if (isinstance(trigger, dict) and trigger.get("type") == "library-build-request"):
            return False
        if trigger in {
            "import-auto-request",
            "data-prep-event-btn",
            "data-prep-trajectory-btn",
            "data-prep-composition-btn",
        }:
            return False
        tasks = task_snapshot if isinstance(task_snapshot, list) else []
        return not any(
            isinstance(item, dict)
            and str(item.get("state") or "") in {"running", "cancel_requested"}
            for item in tasks
        )

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Input("preparation-task-refresh", "n_intervals"),
        State("app-store", "data"),
        State("dataset-session-store", "data"),
        State("preparation-task-snapshot", "data"),
        prevent_initial_call=True,
    )
    def _refresh_current_capabilities_from_workspace(
        _tick,
        app_store,
        session_store,
        task_snapshot,
    ):
        current = app_store if isinstance(app_store, dict) else {}
        dataset_id = str(current.get("dataset_id") or "")
        if not dataset_id:
            raise PreventUpdate
        tasks = [
            item
            for item in (task_snapshot if isinstance(task_snapshot, list) else [])
            if isinstance(item, dict)
            and str(item.get("dataset_id") or "") == dataset_id
        ]
        if not tasks:
            raise PreventUpdate
        task_epoch = max(int(item.get("updated_at_epoch") or 0) for item in tasks)
        task_token = json.dumps(
            sorted(
                (
                    str(item.get("capability") or ""),
                    str(item.get("state") or ""),
                    int(item.get("updated_at_epoch") or 0),
                    item.get("progress") if item.get("progress_trusted") else None,
                )
                for item in tasks
            ),
            separators=(",", ":"),
        )
        if task_token == str(current.get("capability_checked_task_token") or ""):
            raise PreventUpdate
        try:
            validation = svc.validate_dataset_candidate(
                str(current.get("folder") or ""),
                str(current.get("base") or ""),
            )
        except (svc.ServiceError, OSError, RuntimeError):
            raise PreventUpdate
        if not svc.is_same_dataset_revision(current, validation):
            raise PreventUpdate
        updated = {
            **current,
            "analysis_capabilities": dict(
                validation.get("analysis_capabilities") or {}
            ),
            "readiness": dict(validation.get("readiness") or {}),
            "capability_checked_task_epoch": task_epoch,
            "capability_checked_task_token": task_token,
        }
        persisted = (
            {**dict(session_store or {}), **updated}
            if str((session_store or {}).get("dataset_id") or "") == dataset_id
            else updated
        )
        return updated, persisted

    @app.callback(
        Output("data-prep-action-alert", "children"),
        Input("data-prep-event-btn", "n_clicks"),
        Input("data-prep-trajectory-btn", "n_clicks"),
        Input("data-prep-composition-btn", "n_clicks"),
        State("dataset-browser-candidate", "data"),
        State("app-store", "data"),
        background=True,
        progress=Output("data-prep-action-progress", "children"),
        progress_default="",
        running=[
            (Output("data-prep-event-btn", "disabled"), True, False),
            (Output("data-prep-trajectory-btn", "disabled"), True, False),
            (Output("data-prep-composition-btn", "disabled"), True, False),
            (Output("data-prep-cancel-btn", "disabled"), False, True),
        ],
        prevent_initial_call=True,
    )
    def _prepare_dataset_workspace(
        set_progress,
        _event_clicks,
        _trajectory_clicks,
        _composition_clicks,
        candidate,
        app_store,
    ):
        triggered = ctx.triggered_id
        kind = {
            "data-prep-event-btn": "event",
            "data-prep-trajectory-btn": "trajectory",
            "data-prep-composition-btn": "composition",
        }.get(triggered)
        if not kind:
            raise PreventUpdate
        labels = {
            "event": "事件索引",
            "trajectory": "轨迹帧索引",
            "composition": "元素分布索引",
        }
        try:
            target = _validated_dataset_target(
                candidate,
                app_store=app_store,
            )
            set_progress(
                dbc.Alert(
                    f"{target.get('label') or Path(target['base']).name} · "
                    f"{labels[kind]}索引任务已启动；"
                    "可在任务状态区查看阶段与可信进度。",
                    color="info",
                    className="py-2 mb-0",
                )
            )
            result = svc.prepare_dataset_workspace(
                target["folder"],
                base=target["base"],
                kind=kind,
            )
        except svc.ServiceError as exc:
            return dbc.Alert(
                str(exc.message),
                color="danger",
                className="py-2 mb-0",
            )
        except Exception as exc:
            return dbc.Alert(
                f"索引任务失败：{exc}",
                color="danger",
                className="py-2 mb-0",
            )

        status = result.get("status") or {}
        if result.get("existing_task"):
            return dbc.Alert(
                "同类索引任务已在运行；继续显示现有任务进度。",
                color="info",
                className="py-2 mb-0",
            )
        if result.get("canceled"):
            return dbc.Alert(
                "索引任务已取消；最近检查点已保留。",
                color="warning",
                className="py-2 mb-0",
            )
        count = (
            status.get("event_count")
            if kind == "event"
            else status.get("frames")
            if kind == "trajectory"
            else status.get("timepoints")
        )
        action = "已重建" if result.get("rebuilt") else "已建立"
        count_text = f" · {int(count):,} 条记录" if count is not None else ""
        return dbc.Alert(
            f"{labels[kind]}{action}{count_text}。",
            color="success",
            className="py-2 mb-0",
        )

    @app.callback(
        Output("data-prep-cancel-result", "data"),
        Input("data-prep-cancel-btn", "n_clicks"),
        Input({"type": "preparation-task-cancel", "dataset": ALL, "capability": ALL}, "n_clicks"),
        Input({"type": "preparation-task-dismiss", "dataset": ALL, "capability": ALL}, "n_clicks"),
        State("dataset-browser-candidate", "data"),
        State("app-store", "data"),
        State("preparation-task-snapshot", "data"),
        prevent_initial_call=True,
    )
    def _cancel_preparation_task(
        n_clicks,
        task_clicks,
        dismiss_clicks,
        candidate,
        app_store,
        task_snapshot,
    ):
        triggered = ctx.triggered_id
        del task_clicks, dismiss_clicks
        try:
            if isinstance(triggered, dict) and triggered.get("type") in {
                "preparation-task-cancel",
                "preparation-task-dismiss",
            }:
                if not _triggered_click_value():
                    raise PreventUpdate
                task = next(
                    (
                        item
                        for item in (task_snapshot or [])
                        if str(item.get("dataset_id") or "")
                        == str(triggered.get("dataset") or "")
                        and str(item.get("capability") or "")
                        == str(triggered.get("capability") or "")
                    ),
                    None,
                )
                if not task:
                    raise PreventUpdate
                if triggered.get("type") == "preparation-task-dismiss":
                    return svc.dismiss_dataset_preparation_task(
                        str(task.get("folder") or ""),
                        base=str(task.get("base") or ""),
                        kind=str(task.get("capability") or ""),
                    )
                return svc.cancel_dataset_preparation(
                    str(task.get("folder") or ""),
                    base=str(task.get("base") or ""),
                    kind=str(task.get("capability") or ""),
                )
            if not n_clicks:
                raise PreventUpdate
            target = _validated_dataset_target(candidate, app_store=app_store)
            return svc.cancel_dataset_preparation(
                target["folder"],
                base=target["base"],
                kind="all",
            )
        except svc.ServiceError as exc:
            return {"ok": False, "message": str(exc.message)}

    @app.callback(
        Output("data-clear-confirm-modal", "is_open"),
        Output("data-clear-confirm-text", "children"),
        Output("data-clear-kind-store", "data"),
        Output("data-prep-clear-alert", "children"),
        Input("data-clear-event-btn", "n_clicks"),
        Input("data-clear-trajectory-btn", "n_clicks"),
        Input("data-clear-composition-btn", "n_clicks"),
        Input("data-clear-cancel-btn", "n_clicks"),
        State("dataset-browser-candidate", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _confirm_index_clear(
        event_clicks,
        trajectory_clicks,
        composition_clicks,
        cancel_clicks,
        candidate,
        app_store,
    ):
        del event_clicks, trajectory_clicks, composition_clicks, cancel_clicks
        if ctx.triggered_id == "data-clear-cancel-btn":
            return False, no_update, {}, None
        kind = {
            "data-clear-event-btn": "event",
            "data-clear-trajectory-btn": "trajectory",
            "data-clear-composition-btn": "composition",
        }.get(ctx.triggered_id)
        if not kind:
            raise PreventUpdate
        try:
            target = _validated_dataset_target(candidate, app_store=app_store)
            payload = svc.dataset_preparation_status(
                target["folder"],
                base=target["base"],
            )
        except Exception as exc:
            return False, no_update, {}, dbc.Alert(f"无法读取索引状态: {exc}", color="danger", className="py-2")
        item_key = "events" if kind == "event" else kind
        item = payload.get(item_key) or {}
        if str(item.get("state") or "") == "building":
            return (
                False,
                no_update,
                {},
                dbc.Alert(
                    "索引仍在构建。请先取消后台任务（或停止外部准备程序），"
                    "等待任务结束后再清理；最近检查点会保留。",
                    color="warning",
                    className="py-2 mb-0",
                ),
            )
        size = _format_bytes(item.get("index_size"))
        label = {
            "event": "事件",
            "trajectory": "轨迹帧",
            "composition": "组成",
        }[kind]
        message = html.Div(
            [
                html.P(f"将清理当前RNG 数据的 {label} 索引，预计释放 {size}。"),
                html.P(
                    "只删除 Dataset Workspace 中的派生文件，不会删除轨迹、.species、事件 CSV 或任何 ReacNetGenerator 输出文件。",
                    className="text-muted mb-0",
                ),
            ]
        )
        return (
            True,
            message,
            {
                "kind": kind,
                "folder": target["folder"],
                "base": target["base"],
            },
            None,
        )

    @app.callback(
        Output("data-clear-confirm-modal", "is_open", allow_duplicate=True),
        Output("data-prep-clear-alert", "children", allow_duplicate=True),
        Input("data-clear-confirm-btn", "n_clicks"),
        State("data-clear-kind-store", "data"),
        prevent_initial_call=True,
    )
    def _clear_confirmed_index(n_clicks, clear_request):
        if n_clicks is None:
            raise PreventUpdate
        request = clear_request or {}
        try:
            target = _validated_dataset_target(request)
            result = svc.clear_dataset_index(
                target["folder"],
                base=target["base"],
                kind=str(request.get("kind") or ""),
            )
        except svc.ServiceError as exc:
            return False, dbc.Alert(str(exc.message), color="danger", className="py-2")
        return (
            False,
            dbc.Alert(
                f"已清理 {len(result.get('removed') or [])} 个索引文件，释放 {_format_bytes(result.get('released_bytes'))}。",
                color="success",
                className="py-2",
            ),
        )
