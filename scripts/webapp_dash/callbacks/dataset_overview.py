"""Data-management overview, recent datasets and candidate status callbacks."""

from __future__ import annotations

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
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc
from scripts.webapp_dash.navigation import (
    PAGE_LABELS,
    WORKSPACE_PAGE_IDS,
)

from .common import (
    _CAPABILITY_LABELS,
    _CAPABILITY_STATE_LABELS,
    _candidate_for_base,
    _capabilities_from_store,
    _capability_state_class,
    _pattern_trigger_type,
    _triggered_click_value,
    _validated_dataset_target,
)

_WORKSPACE_CAPABILITIES = {
    "species": ("species_abundance", "reaction_search"),
    "reactions": ("reaction_search", "event_search"),
    "evolution": ("species_abundance", "element_distribution"),
    "events": ("event_search", "trajectory_evidence"),
}


def _browser_error_copy(reason: str) -> str:
    """Return actionable, path-safe feedback for one browser failure."""
    return {
        "empty_path": (
            "尚未输入路径。浏览位置和所选RNG 数据均未改变；"
            "请输入路径，或选择“切换起始位置”。"
        ),
        "path_out_of_bounds": (
            "该位置不可浏览。原浏览位置和当前RNG 数据已保留；"
            "请修正路径，或选择“切换起始位置”。"
        ),
        "permission_denied": (
            "没有读取目标位置的权限。原浏览位置和当前RNG 数据已保留；"
            "请选择其他起始位置或联系管理员授权。"
        ),
        "not_found": (
            "目标位置已不存在。原浏览位置和当前RNG 数据已保留；"
            "请输入其他路径或切换起始位置。"
        ),
        "not_directory": (
            "目标不是可浏览目录。原浏览位置和当前RNG 数据已保留；"
            "请输入一个RNG 数据文件夹。"
        ),
        "recent_missing": (
            "最近记录已失效。原浏览位置和当前RNG 数据已保留；"
            "可移除该记录并切换起始位置重新查找。"
        ),
        "candidate_missing": (
            "所选RNG 数据已消失。当前目录和当前RNG 数据已保留；"
            "请选择仍然存在的候选或继续浏览。"
        ),
        "root_boundary": (
            "已到达当前起始位置。浏览位置和当前RNG 数据均未改变。"
        ),
        "no_roots": (
            "当前没有可用的起始位置。当前RNG 数据已保留；"
            "请联系管理员检查数据路径配置。"
        ),
        "read_error": (
            "目标位置暂时无法读取。原浏览位置和当前RNG 数据已保留；"
            "请重试或选择其他允许位置。"
        ),
    }.get(
        str(reason or ""),
        "无法打开目标位置。原浏览位置和当前RNG 数据已保留；请修正输入后重试。",
    )


def _render_recent_datasets(
    records: list[dict[str, Any]] | None,
    *,
    interactive: bool = True,
) -> Any:
    """Render revalidated recent records with a recoverable remove action."""
    entries: list[Any] = []
    for index, record in enumerate(svc.normalise_recent_datasets(records or [])):
        folder = str(record.get("folder") or "")
        base = str(record.get("base") or "")
        try:
            snapshot = svc.browse_dataset_location(folder)
            available = _candidate_for_base(snapshot, base) is not None
        except svc.ServiceError:
            available = False
        label = str(record.get("label") or Path(base).name or folder)
        location = folder or str(Path(base).parent)
        visible_label = label if available else f"{label}（不可用）"
        if not interactive:
            entries.append(
                html.Span(
                    visible_label,
                    className=(
                        "rs-browser-recent-label"
                        if available
                        else "rs-browser-recent-unavailable"
                    ),
                )
            )
            continue
        entries.append(
            html.Div(
                [
                    html.Button(
                        [
                            html.Strong(
                                visible_label,
                                className="rs-browser-recent-name",
                            ),
                            html.Span(
                                location,
                                className="rs-browser-recent-path",
                            ),
                        ],
                        id={"type": "dir-browser-recent-entry", "index": index},
                        type="button",
                        disabled=not available,
                        className=(
                            "rs-browser-recent-entry"
                            if available
                            else "rs-browser-recent-unavailable"
                        ),
                        title=base or folder,
                    ),
                    html.Button(
                        "移除",
                        id={"type": "dir-browser-recent-remove", "index": index},
                        type="button",
                        className="rs-browser-recent-remove",
                        **{"aria-label": f"从最近RNG 数据中移除 {label}"},
                    ),
                ],
                className="rs-browser-recent-item",
            )
        )
    if not entries:
        return html.Div(
            "暂无最近RNG 数据记录。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    return html.Div(entries, className="rs-browser-recent-list")


def _render_artifacts(artifacts: dict[str, str]) -> Any:
    labels = [
        ("reaction", "Reaction"),
        ("species", "Species"),
        ("trajectory", "Trajectory"),
    ]
    if artifacts.get("timeline"):
        labels.append(("timeline", "Timeline"))
    else:
        labels.extend(
            [
                ("reactionevent", "Legacy Reaction Occurrence CSV"),
                ("molecules", "Legacy Molecular Evidence CSV"),
            ]
        )
    rows: list[Any] = []
    for key, label in labels:
        path = artifacts.get(key)
        if not path:
            continue
        rows.append(
            html.Div(
                [
                    html.Span(label, className="rs-artifact-label"),
                    html.Span(Path(path).name, className="rs-artifact-name"),
                    html.Code(path, className="rs-artifact-path"),
                    dcc.Clipboard(content=path, title=f"复制 {label} 路径"),
                ],
                className="rs-artifact-row",
            )
        )
    return html.Details(
        [
            html.Summary("源文件与路径"),
            html.Div(rows, className="rs-artifact-list"),
        ],
        className="rs-artifact-details",
    )


def register(app: Any) -> None:
    """Register dataset overview callbacks."""

    @app.callback(
        Output("data-overview-view", "className"),
        Output("data-browser-view", "className"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("nav-data-management", "n_clicks"),
        Input("data-pick-btn", "n_clicks"),
        Input("library-add-more", "n_clicks"),
        Input("data-change-pick-btn", "n_clicks"),
        Input("data-browser-index-btn", "n_clicks"),
        Input("page-capability-manage-btn", "n_clicks"),
        Input("cp-prepare", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _switch_data_management_view(
        _topbar_open,
        _species_open,
        _sidebar_open,
        _pick_clicks,
        _library_add_clicks,
        _change_pick_clicks,
        _index_clicks,
        _capability_clicks,
        _candidate_prepare_clicks,
        _return_clicks,
        _recent_clicks,
    ):
        triggered = ctx.triggered_id
        opens_browser = isinstance(triggered, str) and triggered in {
            "species-open-data-modal",
            "data-pick-btn",
            "library-add-more",
            "data-change-pick-btn",
        }
        recent_entry_triggered = (
            _pattern_trigger_type(triggered) == "dir-browser-recent-entry"
        )
        if recent_entry_triggered:
            try:
                recent_index = int(triggered.get("index"))
                recent_clicks = _recent_clicks[recent_index]
            except (AttributeError, IndexError, TypeError, ValueError):
                raise PreventUpdate
            if not recent_clicks:
                raise PreventUpdate
        if opens_browser or recent_entry_triggered:
            return "rs-data-view d-none", "rs-data-view"
        return "rs-data-view", "rs-data-view d-none"




    @app.callback(
        Output("data-overview-actions", "children"),
        Input("app-store", "data"),
    )
    def _render_dataset_overview_actions(app_store):
        current = app_store if isinstance(app_store, dict) else {}
        loaded = bool(current.get("dataset_id"))
        capabilities = _capabilities_from_store(current) if loaded else {}
        cards: list[Any] = []
        for page_id in WORKSPACE_PAGE_IDS:
            if page_id == "data-management":
                continue
            capability_keys = _WORKSPACE_CAPABILITIES.get(page_id, ())
            workspace_evidence = [
                (key, dict(capabilities.get(key) or {}))
                for key in capability_keys
            ]
            states = [
                _capability_state_class(evidence.get("state"))
                for _key, evidence in workspace_evidence
            ]
            if not loaded:
                state = "no-dataset"
            elif states and all(item == "ready" for item in states):
                state = "ready"
            elif "ready" in states:
                state = "partial"
            elif "needs-preparation" in states:
                state = "needs-preparation"
            else:
                state = next(
                    (item for item in states if item != "ready"),
                    "unknown",
                )
            state_label = ("部分能力可用" if state == "partial" else
                           _CAPABILITY_STATE_LABELS.get(state, "状态待检查")
                           if loaded else "先选择RNG 数据")
            reasons = [
                f"{_CAPABILITY_LABELS[key]}：{evidence.get('reason')}"
                for key, evidence in workspace_evidence
                if evidence.get("reason") and _capability_state_class(evidence.get("state")) != "ready"
            ]
            reason = "；".join(reasons) or (
                "尚无该工作区的能力状态，请刷新RNG 数据状态。"
                if loaded
                else "选择并使用RNG 数据后，在此查看证据条件。"
            )
            cards.append(html.Div([
                html.Div([
                    html.Strong(PAGE_LABELS[page_id]),
                    html.Span(state_label, className=f"rs-capability-state is-{state}"),
                ], className="rs-workflow-card-heading"),
                html.Div(reason, className="rs-workflow-reason")
                if loaded and state != "ready"
                else None,
                dbc.Button(
                    "打开入口",
                    id={"type": "data-overview-open-page", "page": page_id},
                    color="primary" if state == "ready" else "secondary",
                    outline=True,
                    size="sm",
                ),
            ], className=f"rs-workflow-card is-{state}"))
        return html.Div([
            html.Section([
                html.H3("分析工具"),
                html.Div(cards, className="rs-workflow-grid"),
            ], className="rs-workflow-group"),
        ], className="rs-workflow-launcher")

    @app.callback(
        Output("data-recent-datasets", "children"),
        Output("dir-browser-recent-datasets", "children"),
        Input("recent-datasets", "data"),
    )
    def _show_recent_datasets(recent_records):
        return (
            _render_recent_datasets(recent_records, interactive=False),
            _render_recent_datasets(recent_records, interactive=True),
        )

    @app.callback(
        Output("recent-datasets", "data", allow_duplicate=True),
        Input({"type": "dir-browser-recent-remove", "index": ALL}, "n_clicks"),
        State("recent-datasets", "data"),
        prevent_initial_call=True,
    )
    def _remove_recent_dataset(_clicks, recent_records):
        triggered = ctx.triggered_id
        if (
            _pattern_trigger_type(triggered) != "dir-browser-recent-remove"
            or not _triggered_click_value()
        ):
            raise PreventUpdate
        records = svc.normalise_recent_datasets(recent_records)
        try:
            index = int(triggered.get("index"))
        except (AttributeError, TypeError, ValueError):
            raise PreventUpdate
        if not 0 <= index < len(records):
            raise PreventUpdate
        return [record for position, record in enumerate(records) if position != index]

    @app.callback(
        Output("data-candidate-summary", "children"),
        Output("data-scan-status", "children"),
        Output("data-artifacts", "children"),
        Input("dataset-browser-candidate", "data"),
        Input("app-store", "data"),
    )
    def _show_candidate_status(candidate, app_store):
        selected = {}  # The overview always describes Current Dataset.
        folder = str(selected.get("folder") or "").strip()
        base = str(selected.get("base") or "").strip()
        if not folder or not base:
            loaded = app_store or {}
            current_label = str(loaded.get("label") or "").strip()
            has_loaded_dataset = bool(loaded.get("dataset_id"))
            if has_loaded_dataset:
                if loaded.get("context_state") == "revision-changed":
                    scan_status: Any = html.Span(
                        "源修订已变化",
                        className="rs-context-status is-pending",
                    )
                else:
                    scan_status = html.Span(
                        "已加载",
                        className="rs-context-status is-ready",
                    )
                summary = html.Div(
                    [
                        html.Span(className="rs-current-dataset-dot"),
                        html.Strong(current_label),
                    ],
                    className="rs-current-dataset-name",
                )
            else:
                summary = html.Div(
                    [
                        html.Strong("当前未选择用于分析的数据"),
                    ],
                    className="rs-current-dataset-empty",
                )
            return (
                summary,
                (
                    scan_status
                    if has_loaded_dataset
                    else html.Span(
                        "未加载",
                        className="rs-context-status is-empty",
                    )
                ),
                _render_artifacts(loaded.get("artifacts") or {}),
            )
        try:
            target = _validated_dataset_target(selected)
            folder = target["folder"]
            base = target["base"]
            status = svc.scan_dataset(folder, base=base)
        except svc.ServiceError as exc:
            return (
                dbc.Alert(
                    _browser_error_copy(str(exc.reason or "candidate_missing")),
                    color="danger",
                    className="py-2",
                ),
                "RNG 数据检查失败；当前RNG 数据未改变。",
                _render_artifacts({}),
            )
        except Exception:
            return (
                dbc.Alert(
                    "暂时无法检查所选RNG 数据。当前RNG 数据已保留；请重试或重新选择。",
                    color="danger",
                    className="py-2",
                ),
                "RNG 数据检查失败；当前RNG 数据未改变。",
                _render_artifacts({}),
            )
        dataset = status.get("dataset") or {}
        selected_base = str(dataset.get("selected_base") or "")
        if selected_base != base:
            return (
                dbc.Alert("所选RNG 数据已不存在，请重新选择。", color="danger", className="py-2"),
                "所选RNG 数据已不存在。",
                _render_artifacts({}),
            )
        artifact_html = _render_artifacts(svc.artifacts_from_status(status))
        display_label = target["label"] or svc.dataset_label(status)
        return (
            html.Div(
                [
                    html.Span(className="rs-current-dataset-dot is-pending"),
                    html.Strong(display_label),
                ],
                className="rs-current-dataset-name",
            ),
            html.Span("待加载", className="rs-context-status is-pending"),
            artifact_html,
        )

    @app.callback(
        Output("data-current-refresh-btn", "children"),
        Output("data-current-refresh-btn", "color"),
        Output("data-current-refresh-btn", "outline"),
        Output("data-current-refresh-btn", "disabled"),
        Output("data-open-species-btn", "color"),
        Output("data-open-species-btn", "outline"),
        Output("data-apply-btn", "children", allow_duplicate=True),
        Output("data-apply-btn", "disabled", allow_duplicate=True),
        Input("app-store", "data"),
        Input("dataset-browser-candidate", "data"),
        Input("dataset-switch-transaction", "data"),
        Input({"type": "dataset-bound-operation", "name": ALL}, "data"),
        prevent_initial_call=True,
    )
    def _render_dataset_context_actions(
        app_store,
        candidate,
        transaction,
        bound_operations,
    ):
        current = app_store if isinstance(app_store, dict) else {}
        selected = candidate if isinstance(candidate, dict) else {}
        switch_state = str((transaction or {}).get("state") or "idle")
        validating = switch_state == "validating"
        has_candidate = bool(selected.get("folder") and selected.get("base"))
        inspected: dict[str, Any] = {}
        if has_candidate:
            try:
                inspected = svc.inspect_dataset_candidate(
                    str(selected.get("folder") or ""),
                    str(selected.get("base") or ""),
                )
            except svc.ServiceError:
                inspected = {}
        same_revision = svc.is_same_dataset_revision(current, inspected)
        is_different_candidate = has_candidate and not (
            str(inspected.get("dataset_id") or "")
            and str(inspected.get("dataset_id") or "")
            == str(current.get("dataset_id") or "")
        )
        context_state = str(current.get("context_state") or "none")

        if any(bool(value) for value in bound_operations or []):
            return (
                (
                    "更新当前RNG 数据状态"
                    if context_state == "revision-changed"
                    else "刷新状态"
                ),
                "secondary",
                True,
                True,
                "primary",
                False,
                "等待当前分析完成",
                True,
            )

        if selected.get("collection_id"):
            same_revision = False
            is_different_candidate = True

        if validating:
            apply_label = "正在检查…"
            apply_disabled = True
        elif switch_state == "failed" and has_candidate:
            apply_label = "重试"
            apply_disabled = False
        elif same_revision or (
            switch_state == "succeeded" and not is_different_candidate
        ):
            apply_label = "当前RNG 数据"
            apply_disabled = True
        else:
            apply_label = "开始分析"
            apply_disabled = not has_candidate

        if context_state == "revision-changed":
            refresh_label = "更新当前RNG 数据状态"
            species_action_color = "secondary"
            species_action_outline = True
            if is_different_candidate:
                return (
                    refresh_label,
                    "secondary",
                    True,
                    validating,
                    species_action_color,
                    species_action_outline,
                    apply_label,
                    apply_disabled,
                )
            return (
                refresh_label,
                "primary",
                False,
                validating,
                species_action_color,
                species_action_outline,
                apply_label,
                True,
            )
        return (
            "刷新状态",
            "secondary",
            True,
            not bool(current.get("dataset_id")) or validating,
            "primary",
            False,
            apply_label,
            apply_disabled,
        )

    @app.callback(
        Output("data-apply-reason", "children"),
        Input("dataset-browser-candidate", "data"),
        Input("dataset-switch-transaction", "data"),
        Input("app-store", "data"),
        Input({"type": "dataset-bound-operation", "name": ALL}, "data"),
        Input("import-preview", "data"), Input("import-group", "value"),
    )
    def _render_apply_reason(candidate, transaction, app_store, bound_operations, preview, group):
        if any(bool(value) for value in bound_operations or []):
            return "等待当前分析完成后即可开始。所选文件会保留。"
        state = (transaction or {}).get("state")
        if state == "validating":
            return "正在检查文件；完成后进入分析。"
        if state == "failed":
            return (transaction or {}).get("message") or "检查失败，请修正后重试。"
        if not candidate:
            if (preview or {}).get("folder_truncated"):
                return "请选择更具体的输出文件夹，当前目录超过识别上限。"
            groups = (preview or {}).get("groups", [])
            if not groups:
                return "请选择包含 RNG 结果的文件夹。"
            selected = next((item for item in groups if item["key"] == group), None)
            if not selected:
                return "请选择本次分析的RNG 数据。"
            return "；".join(selected.get("errors", [])) or "正在检查所选文件。"
        return ""
