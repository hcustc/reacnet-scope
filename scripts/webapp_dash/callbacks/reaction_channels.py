"""Species reaction-channel tables, volume evidence and channel history callbacks."""

from __future__ import annotations

from .. import ui_components as ui
import re
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
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope import services as svc
from scripts.webapp_dash.dataset_scope import (
    context_key,
    current_species_focus,
    species_focus_for,
)
from scripts.webapp_dash.navigation import (
    PAGE_CLASS_NAMES,
    PAGE_DESCRIPTIONS,
    PAGE_IDS,
    PAGE_LABELS,
    PAGE_SECTIONS,
    TOP_NAV_PAGE_IDS,
)

from .common import (
    _format_bytes,
    _selected_table_row,
    _structure_species_card,
)


def _channel_focus_species(channel_focus, store, species_focus=None):
    focus = channel_focus if isinstance(channel_focus, dict) else {}
    if focus.get("context") == context_key(store):
        return str(focus.get("species") or "").strip()
    return current_species_focus(species_focus, store)["selected_smiles"].strip()


def _current_dataset_id(store: dict[str, Any] | None) -> str:
    value = store or {}
    dataset_id = str(value.get("dataset_id") or "")
    if dataset_id:
        return dataset_id
    folder = str(value.get("folder") or "")
    base = str(value.get("base") or "")
    if folder and base:
        return _dataset_id_from_selection(folder, base)
    return str(base or value.get("label") or "")


def _render_channel_volume_status(evidence: dict[str, Any]) -> Any:
    """Render simulation-box readiness without exposing internal exceptions."""
    value = evidence if isinstance(evidence, dict) else {}
    reason = str(value.get("reason") or "")
    color = (
        "success"
        if value.get("ready")
        else "secondary"
        if reason == "missing_trajectory"
        else "warning"
    )
    source_label = {
        "workspace_link": "Dataset Workspace 显式关联",
        "dataset_artifact": "当前RNG 数据自动识别",
    }.get(str(value.get("source") or ""), "未关联")
    trajectory = str(value.get("trajectory") or "")
    details = [
        html.Span(str(value.get("message") or "")),
        html.Span(f"来源：{source_label}"),
        html.Code(trajectory, className="rs-kinetics-source-path")
        if trajectory
        else None,
    ]
    return dbc.Alert(
        [item for item in details if item is not None],
        color=color,
        className="mb-0 py-1 px-2 rs-kinetics-volume-status-alert",
    )


def _dataset_id_from_selection(folder: str, base: str) -> str:
    """Return the workspace-compatible ID for one fully qualified dataset base."""
    source = Path(str(base or "")).expanduser()
    if not source.is_absolute():
        source = Path(str(folder or "")).expanduser() / source
    try:
        source = source.resolve(strict=False)
    except (OSError, RuntimeError):
        source = source.absolute()
    return dataset_id_for_source(str(source))


def _triggered_property_ids(callback_context: Any) -> frozenset[str]:
    """Return every property reported for the current callback invocation."""
    try:
        triggered_prop_ids = getattr(
            callback_context,
            "triggered_prop_ids",
            None,
        )
    except RuntimeError:
        triggered_prop_ids = None
    if triggered_prop_ids is not None:
        keys = getattr(triggered_prop_ids, "keys", None)
        if callable(keys):
            property_ids = frozenset(str(value) for value in keys())
            if property_ids:
                return property_ids

    try:
        triggered = getattr(callback_context, "triggered", None)
    except RuntimeError:
        triggered = None
    if triggered:
        property_ids = frozenset(
            str(item.get("prop_id") or "")
            for item in triggered
            if isinstance(item, dict) and item.get("prop_id")
        )
        if property_ids:
            return property_ids

    try:
        triggered_id = getattr(callback_context, "triggered_id", None)
    except RuntimeError:
        triggered_id = None
    if isinstance(triggered_id, str) and triggered_id:
        return frozenset({triggered_id})
    return frozenset()


def _channel_volume_progress_state(task: dict[str, Any] | None) -> tuple[str, str]:
    """Render persisted trajectory preparation facts for the channel page."""
    value = task if isinstance(task, dict) else {}
    state = str(value.get("state") or "")
    class_name = "rs-kinetics-progress"
    if not value:
        return "正在启动轨迹索引任务…", f"{class_name} is-running"

    trusted_progress = (
        value.get("progress") if value.get("progress_trusted") else None
    )
    progress = (
        min(max(float(trusted_progress), 0.0), 1.0)
        if isinstance(trusted_progress, (int, float))
        else None
    )
    source_artifact = dict(value.get("source_artifact_revision") or {})
    source_size = int(source_artifact.get("size") or 0)
    progress_detail = ""
    if progress is not None:
        progress_detail = f"：{progress * 100:.1f}%"
        if source_size > 0:
            progress_detail += (
                f"（{_format_bytes(round(progress * source_size))} / "
                f"{_format_bytes(source_size)}）"
            )

    if state in {"running", "cancel_requested"}:
        action = (
            "正在取消轨迹索引任务"
            if state == "cancel_requested"
            else "正在构建轨迹索引"
        )
        return f"{action}{progress_detail}", f"{class_name} is-running"
    if state == "completed":
        return "轨迹索引已完成，正在更新通道结果…", f"{class_name} is-running"
    if state == "canceled":
        return "轨迹索引任务已取消；已保留可续建检查点。", class_name
    if state == "interrupted":
        return "轨迹索引任务已中断；重新准备时可从检查点续建。", class_name
    if state == "superseded":
        return "轨迹源文件已变化，本次索引任务已停止。", class_name
    if state == "failed":
        message = str(value.get("message") or "未知错误")
        return f"轨迹索引任务失败：{message}", class_name
    return "正在检查轨迹索引状态…", f"{class_name} is-running"


def _structure_reaction_side(
    items: list[dict[str, Any]],
    side_label: str,
    *,
    action_scope: str = "",
    side: str = "",
) -> Any:
    children: list[Any] = []
    for index, item in enumerate(items):
        if index:
            children.append(html.Span("+", className="rs-channel-operator"))
        children.append(
            _structure_species_card(
                item,
                side_label,
                action_scope=action_scope,
                side=side,
            )
        )
    return html.Div(children, className="rs-channel-reaction-side")


def _reaction_structure_detail_children(
    detail: dict[str, Any],
    *,
    title: str = "完整结构反应式",
    role_label: str = "",
    action_scope: str = "",
) -> list[Any]:
    kinetics = detail.get("kinetics") or {}
    kinetics_lines: list[Any] = []
    if kinetics.get("k_app_display"):
        interval = ""
        if (
            kinetics.get("k_app_ci95_low") is not None
            and kinetics.get("k_app_ci95_high") is not None
        ):
            interval = (
                f"{float(kinetics['k_app_ci95_low']):.4g}–"
                f"{float(kinetics['k_app_ci95_high']):.4g} "
                f"{kinetics.get('k_app_unit') or ''}"
            ).strip()
        kinetics_lines.extend(
            [
                html.Div(
                    [
                        html.Span("表观 k"),
                        html.Code(str(kinetics["k_app_display"])),
                    ],
                    className="rs-channel-detail-line",
                ),
                html.Div(
                    [
                        html.Span("事件证据"),
                        html.Code(
                            f"n={int(kinetics.get('event_count') or 0)}；"
                            f"{float(kinetics.get('event_frequency_per_ps') or 0):.4g} ps⁻¹；"
                            f"观察 {float(kinetics.get('observation_time_ps') or 0):.4g} ps"
                        ),
                    ],
                    className="rs-channel-detail-line",
                ),
                html.Div(
                    [html.Span("95% CI"), html.Code(interval)],
                    className="rs-channel-detail-line",
                )
                if interval
                else None,
                html.Div(
                    [
                        html.Span("暴露量"),
                        html.Code(
                            f"{float(kinetics.get('kinetic_exposure') or 0):.6g} "
                            f"{kinetics.get('kinetic_exposure_unit') or ''}"
                        ),
                    ],
                    className="rs-channel-detail-line",
                ),
                html.Div(
                    [
                        html.Span("模型"),
                        html.Code("化学计量质量作用（表观估计）"),
                    ],
                    className="rs-channel-detail-line",
                ),
            ]
        )
        if kinetics.get("reverse_k_app_display"):
            kinetics_lines.append(
                html.Div(
                    [
                        html.Span("逆向表观 k"),
                        html.Code(str(kinetics["reverse_k_app_display"])),
                    ],
                    className="rs-channel-detail-line",
                )
            )
    elif kinetics.get("kinetics_reason_message") or kinetics.get(
        "kinetics_reason"
    ):
        kinetics_lines.append(
            html.Div(
                [
                    html.Span("表观 k"),
                    html.Code(
                        str(
                            kinetics.get("kinetics_reason_message")
                            or kinetics.get("kinetics_reason")
                        )
                    ),
                ],
                className="rs-channel-detail-line",
            )
        )
    return [
        html.Div(
            [
                html.Div(
                    [
                        html.Span(title, className="rs-channel-detail-kicker"),
                        html.Strong(role_label, className="rs-channel-role")
                        if role_label
                        else None,
                    ]
                ),
                html.Div(
                    [
                        html.Span("分子式"),
                        html.Code(str(detail.get("reaction_formulas") or "")),
                    ],
                    className="rs-channel-detail-line",
                ),
                html.Div(
                    [
                        html.Span("SMILES"),
                        html.Code(str(detail.get("reaction_smiles") or "")),
                    ],
                    className="rs-channel-detail-line",
                ),
                *kinetics_lines,
            ],
            className="rs-channel-detail-header",
        ),
        html.Div(
            [
                _structure_reaction_side(
                    detail.get("reactants") or [],
                    "反应物",
                    action_scope=action_scope,
                    side="reactant",
                ),
                html.Span(
                    "→",
                    className="rs-channel-arrow",
                    **{"aria-label": "生成"},
                ),
                _structure_reaction_side(
                    detail.get("products") or [],
                    "产物",
                    action_scope=action_scope,
                    side="product",
                ),
            ],
            className="rs-channel-structure-reaction",
        ),
    ]


def register(app: Any) -> None:
    """Register reaction channels callbacks."""
    @app.callback(
        Output("rxn-channel-lanes", "className"),
        Output("rxn-production-grid", "columnState"),
        Output("rxn-consumption-grid", "columnState"),
        Input("rxn-channel-layout", "value"),
        Input("rxn-channel-show-rates", "value"),
    )
    def _channel_display_options(layout, show_rates):
        # P1 keeps legacy result fields compatible but removes apparent-rate
        # columns from the ordinary product surface.
        mode = "compare" if layout == "compare" else "stacked"
        hidden = [
            "event_frequency_per_ps", "k_app_display", "reverse_k_app_display",
        ]
        return f"rs-channel-lanes is-{mode}", [{"colId": field, "hide": True} for field in hidden], [{"colId": field, "hide": True} for field in hidden]


    @app.callback(
        Output("rxn-channel-timestep-ps", "value"),
        Input("app-store", "data"),
        prevent_initial_call=True,
    )
    def _load_channel_timestep_ps(store):
        artifacts = (store or {}).get("artifacts", {}) or {}
        return svc.channel_timestep_ps(artifacts)

    @app.callback(
        Output("rxn-channel-trajectory-path", "value"),
        Output("rxn-channel-coordinate-unit-confirm", "value"),
        Output("rxn-channel-volume-status", "children"),
        Input("app-store", "data"),
    )
    def _load_channel_volume_evidence(store):
        artifacts = (store or {}).get("artifacts", {}) or {}
        evidence = svc.channel_volume_evidence(artifacts)
        return (
            str(evidence.get("trajectory") or ""),
            evidence.get("coordinate_length_unit") == "angstrom",
            _render_channel_volume_status(evidence),
        )

    def _channel_columns(items: list[tuple[str, str, int | None]]) -> list[dict[str, Any]]:
        return ui.columns([
            {"name": label, "id": field, **({"presentation": "markdown"} if field == "structure" else {}), **({"type": "numeric"} if field not in {"structure", "smiles", "formula", "reaction_formulas", "recommendation", "association_status", "structure_source", "timing_unit"} else {})}
            for field, label, _width in items
        ])

    def _direct_channel_columns() -> list[dict[str, Any]]:
        return _channel_columns(
            [
                ("reaction_formulas", "反应式", 240),
                ("forward_tp", "频次", 72),
                ("reverse_tp", "逆向", 72),
                ("net_tp", "净频次", 76),
                ("first_time", "首次后帧时间", 110),
                ("last_time", "末次后帧时间", 110),
                ("timing_unit", "时间单位", 110),
                ("ratio_pct", "占比%", 68),
                ("event_frequency_per_ps", "事件频率/ps⁻¹", 110),
                ("k_app_display", "表观 k", 150),
                ("reverse_k_app_display", "逆向表观 k", 150),
            ]
        )

    species_return_updates = {
        **{
            f"page-{page_id}": {
                "className": (
                    f"{PAGE_CLASS_NAMES.get(page_id, 'rs-page')} active"
                    if page_id == "species"
                    else PAGE_CLASS_NAMES.get(page_id, "rs-page")
                )
            }
            for page_id in PAGE_IDS
        },
        **{
            f"nav-{page_id}": {
                "className": (
                    "rs-top-nav-item active"
                    if page_id == "species"
                    else "rs-top-nav-item"
                ),
                "aria-current": "page" if page_id == "species" else "false",
            }
            for page_id in TOP_NAV_PAGE_IDS
        },
        "nav-data-management": {
            "className": "rs-top-nav-item rs-nav-utility",
            "aria-current": "false",
        },
        "data-open-batch-compare-btn": {
            "className": "rs-top-nav-item rs-nav-utility",
            "aria-current": "false",
        },
        "topbar-page-context": {"children": PAGE_LABELS["species"]},
        "rxn-query-card": {"style": {}},
        "rxn-results-card": {"style": {}},
        "rxn-channel-view": {"style": {"display": "none"}},
        "page-title": {"children": PAGE_LABELS["species"]},
        "page-eyebrow-section": {"children": PAGE_SECTIONS["species"]},
        "page-description": {"children": PAGE_DESCRIPTIONS["species"]},
        "page-header": {
            "className": "rs-page-header",
            "style": {},
        },
        "app-body": {"className": "rs-body rs-tool-shell"},
        "page-store": {"data": {"page": "species"}},
    }
    app.clientside_callback(
        """
        function(history) {
            const stack = Array.isArray(history) ? history : [];
            const frame = stack.length ? stack[stack.length - 1] : null;
            const label = (frame && frame.label) || "返回";
            return [`← ${label}`, label];
        }
        """,
        Output("rxn-channel-back-btn", "children"),
        Output("rxn-channel-back-btn", "title"),
        Input("rxn-channel-history-store", "data"),
    )

    app.clientside_callback(
        f"""
        function(nClicks, history, appStore) {{
            if (!nClicks) {{
                return window.dash_clientside.no_update;
            }}
            const stack = Array.isArray(history) ? history.slice() : [];
            const frame = stack.pop();
            if (!frame) {{
                return window.dash_clientside.no_update;
            }}
            let updates = {{}};
            if (frame.kind === "species") {{
                updates = {json.dumps(species_return_updates, ensure_ascii=False)};
            }} else if (frame.kind === "reaction-search") {{
                updates = {{
                    "rxn-query-card": {{style: {{}}}},
                    "rxn-results-card": {{style: {{}}}},
                    "rxn-channel-view": {{style: {{display: "none"}}}},
                }};
            }} else if (frame.kind === "channel") {{
                updates = {{
                    "rxn-production-grid": {{
                        rowData: frame.production_data || [],
                        columnDefs: frame.production_columns || [],
                        selectedRows: frame.production_selected_rows || [],
                    }},
                    "rxn-consumption-grid": {{
                        rowData: frame.consumption_data || [],
                        columnDefs: frame.consumption_columns || [],
                        selectedRows: frame.consumption_selected_rows || [],
                    }},
                    "rxn-channel-selection-store": {{data: frame.selection || null}},
                    "rxn-channel-alert": {{children: frame.alert || ""}},
                    "rxn-query-card": {{style: {{display: "none"}}}},
                    "rxn-results-card": {{style: {{display: "none"}}}},
                    "rxn-channel-view": {{style: {{display: "block"}}}},
                }};
            }}
            if (frame.focus) {{
                updates["app-store"] = {{
                    data: {{...(appStore || {{}}), ...frame.focus}},
                }};
            }}
            if (frame.channel_focus) {{
                updates["rxn-channel-focus-store"] = {{data: frame.channel_focus}};
            }}
            for (const [componentId, props] of Object.entries(updates)) {{
                window.dash_clientside.set_props(componentId, props);
            }}
            return stack;
        }}
        """,
        Output("rxn-channel-history-store", "data", allow_duplicate=True),
        Input("rxn-channel-back-btn", "n_clicks"),
        State("rxn-channel-history-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("rxn-production-grid", "rowData"),
        Output("rxn-production-grid", "columnDefs"),
        Output("rxn-consumption-grid", "rowData"),
        Output("rxn-consumption-grid", "columnDefs"),
        Output("rxn-channel-alert", "children"),
        Output("rxn-production-grid", "selectedRows"),
        Output("rxn-consumption-grid", "selectedRows"),
        Output("rxn-production-grid", "cellClicked"),
        Output("rxn-consumption-grid", "cellClicked"),
        Output("rxn-channel-focus-store", "data"),
        Input("species-to-channels-btn", "n_clicks"),
        State("rxn-top", "value"),
        State("app-store", "data"),
        State("species-focus", "data"),
        prevent_initial_call=True,
    )
    def _load_selected_species_channels(
        _channel_clicks,
        top,
        store,
        species_focus,
    ):
        if ctx.triggered_id != "species-to-channels-btn":
            raise PreventUpdate
        store = store or {}
        selected_smiles = current_species_focus(species_focus, store)["selected_smiles"].strip()
        if not selected_smiles:
            raise PreventUpdate
        columns = _direct_channel_columns()
        try:
            result = svc.collect_species_channels(
                store.get("artifacts", {}) or {},
                selected_smiles,
                top=max(1, int(top or 50)),
                include_kinetics=False,
            )
        except svc.ServiceError as exc:
            return (
                [],
                columns,
                [],
                columns,
                str(exc.message),
                [],
                [],
                None,
                None,
                {"species": selected_smiles, "context": context_key(store)},
        )
        production_rows = result.get("production_rows") or []
        consumption_rows = result.get("consumption_rows") or []
        timing = result.get("timing") or {}
        message = str(timing.get("message") or "")
        if str(timing.get("status") or "").startswith("event_index_") or timing.get("status") == "missing_reactionevent":
            message += " 事件索引未就绪；首次/末次时间不可用，请准备或重建事件索引。"
        elif timing.get("status") == "time_conversion_missing":
            message += " 未确认 timestep → ps；时间列保留原始 timestep。"
        elif timing.get("status") == "analyzed_frame_only":
            message += " 当前事件源仅有分析帧序号；时间列单位为 analyzed_frame。"
        return (
            production_rows,
            columns,
            consumption_rows,
            columns,
            message,
            [],
            [],
            None,
            None,
            {"species": selected_smiles, "context": context_key(store)},
        )

    @app.callback(
        Output("rxn-channel-timestep-status", "children", allow_duplicate=True),
        Output("rxn-production-grid", "rowData", allow_duplicate=True),
        Output("rxn-production-grid", "columnDefs", allow_duplicate=True),
        Output("rxn-consumption-grid", "rowData", allow_duplicate=True),
        Output("rxn-consumption-grid", "columnDefs", allow_duplicate=True),
        Output("rxn-channel-alert", "children", allow_duplicate=True),
        Output("rxn-production-grid", "selectedRows", allow_duplicate=True),
        Output("rxn-consumption-grid", "selectedRows", allow_duplicate=True),
        Output("rxn-production-grid", "cellClicked", allow_duplicate=True),
        Output("rxn-consumption-grid", "cellClicked", allow_duplicate=True),
        Output("rxn-channel-selection-store", "data", allow_duplicate=True),
        Input("rxn-channel-timestep-save-btn", "n_clicks"),
        State("rxn-channel-timestep-ps", "value"),
        State("rxn-top", "value"),
        State("app-store", "data"),
        State("rxn-channel-focus-store", "data"),
        State("species-focus", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output("rxn-channel-timestep-save-btn", "disabled"),
                True,
                False,
            ),
            (
                Output("rxn-channel-timestep-save-btn", "children"),
                "正在刷新…",
                "保存并刷新时间",
            ),
            (
                Output("rxn-channel-timestep-progress", "children"),
                "正在读取索引并刷新通道时间…",
                "",
            ),
            (
                Output("rxn-channel-timestep-progress", "className"),
                "rs-kinetics-progress is-running",
                "rs-kinetics-progress",
            ),
        ],
    )
    def _save_channel_timestep_ps(n_clicks, timestep_ps, top, store, channel_focus, species_focus):
        if not n_clicks:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        try:
            confirmed = svc.confirm_channel_timestep_ps(artifacts, timestep_ps)
        except svc.ServiceError as exc:
            return (
                dbc.Alert(
                    str(exc.message),
                    color="warning",
                    className="mb-0 py-1 px-2",
                ),
                *(no_update for _ in range(10)),
            )

        try:
            selected_smiles = _channel_focus_species(channel_focus, store, species_focus)
            result = svc.collect_species_channels(
                artifacts,
                selected_smiles,
                top=max(1, int(top or 50)),
                include_kinetics=False,
            )
        except svc.ServiceError as exc:
            return (
                dbc.Alert(
                    f"已保存 1 timestep = {confirmed:g} ps，但刷新通道时间失败。",
                    color="warning",
                    className="mb-0 py-1 px-2",
                ),
                no_update,
                no_update,
                no_update,
                no_update,
                str(exc.message),
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
            )

        columns = _direct_channel_columns()
        timing_message = str((result.get("timing") or {}).get("message") or "")
        return (
            dbc.Alert(
                f"已保存：1 timestep = {confirmed:g} ps，并已刷新通道时间。",
                color="success",
                className="mb-0 py-1 px-2",
            ),
            result.get("production_rows") or [],
            columns,
            result.get("consumption_rows") or [],
            columns,
            timing_message,
            [],
            [],
            None,
            None,
            None,
        )

    @app.callback(
        Output(
            "rxn-channel-volume-progress",
            "children",
            allow_duplicate=True,
        ),
        Output(
            "rxn-channel-volume-progress",
            "className",
            allow_duplicate=True,
        ),
        Input("rxn-channel-volume-refresh", "n_intervals"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _refresh_channel_volume_progress(_tick, store):
        current = store if isinstance(store, dict) else {}
        current_dataset_id = _current_dataset_id(current)
        tasks = svc.list_preparation_tasks([current])
        task = next(
            (
                item
                for item in tasks
                if str(item.get("capability") or "") == "trajectory"
                and (
                    not current_dataset_id
                    or str(item.get("dataset_id") or "")
                    == current_dataset_id
                )
            ),
            None,
        )
        return _channel_volume_progress_state(task)

    @app.callback(
        Output("rxn-channel-volume-status", "children", allow_duplicate=True),
        Output("rxn-production-grid", "rowData", allow_duplicate=True),
        Output("rxn-production-grid", "columnDefs", allow_duplicate=True),
        Output("rxn-consumption-grid", "rowData", allow_duplicate=True),
        Output("rxn-consumption-grid", "columnDefs", allow_duplicate=True),
        Output("rxn-channel-alert", "children", allow_duplicate=True),
        Output("rxn-production-grid", "selectedRows", allow_duplicate=True),
        Output("rxn-consumption-grid", "selectedRows", allow_duplicate=True),
        Output("rxn-production-grid", "cellClicked", allow_duplicate=True),
        Output("rxn-consumption-grid", "cellClicked", allow_duplicate=True),
        Output("rxn-channel-selection-store", "data", allow_duplicate=True),
        Input("rxn-channel-volume-save-btn", "n_clicks"),
        State("rxn-channel-trajectory-path", "value"),
        State("rxn-channel-coordinate-unit-confirm", "value"),
        State("rxn-top", "value"),
        State("app-store", "data"),
        State("rxn-channel-focus-store", "data"),
        State("species-focus", "data"),
        background=True,
        prevent_initial_call=True,
        running=[
            (
                Output("rxn-channel-volume-save-btn", "disabled"),
                True,
                False,
            ),
            (
                Output("rxn-channel-volume-save-btn", "children"),
                "正在准备…",
                "关联、准备并重新计算",
            ),
            (
                Output("rxn-channel-volume-progress", "children"),
                "正在关联轨迹并检查索引…",
                "",
            ),
            (
                Output("rxn-channel-volume-progress", "className"),
                "rs-kinetics-progress is-running",
                "rs-kinetics-progress",
            ),
            (
                Output("rxn-channel-volume-refresh", "disabled"),
                False,
                True,
            ),
        ],
    )
    def _configure_channel_volume(
        n_clicks,
        trajectory_path,
        confirm_angstrom,
        top,
        store,
        channel_focus,
        species_focus,
    ):
        if not n_clicks:
            raise PreventUpdate
        if not confirm_angstrom:
            return (
                dbc.Alert(
                    "请先确认轨迹坐标长度单位为 Å。",
                    color="warning",
                    className="mb-0 py-1 px-2",
                ),
                *(no_update for _ in range(10)),
            )
        current = store if isinstance(store, dict) else {}
        artifacts = current.get("artifacts", {}) or {}
        try:
            evidence = svc.configure_channel_volume_source(
                artifacts,
                trajectory_path,
                confirm_angstrom=True,
            )
            if not evidence.get("ready"):
                if evidence.get("reason") not in {
                    "trajectory_index_not_ready",
                    "trajectory_index_stale",
                    "trajectory_index_invalid",
                    "trajectory_index_building",
                }:
                    raise svc.ServiceError(
                        str(evidence.get("message") or "模拟盒体积证据不可用"),
                        reason=str(evidence.get("reason") or "volume_unavailable"),
                    )
                folder = str(current.get("folder") or "")
                base = str(current.get("base") or "")
                if not folder or not base:
                    raise svc.ServiceError(
                        "当前RNG 数据上下文不完整，无法建立轨迹索引。",
                        reason="missing_dataset_context",
                    )
                svc.prepare_dataset_workspace(
                    folder,
                    base=base,
                    kind="trajectory",
                )
                evidence = svc.channel_volume_evidence(artifacts)
            if not evidence.get("ready"):
                raise svc.ServiceError(
                    str(evidence.get("message") or "模拟盒体积证据仍未就绪"),
                    reason=str(evidence.get("reason") or "volume_unavailable"),
                )
            selected_smiles = _channel_focus_species(channel_focus, current, species_focus)
            result = svc.collect_species_channels(
                artifacts,
                selected_smiles,
                top=max(1, int(top or 50)),
                include_kinetics=False,
            )
        except svc.ServiceError as exc:
            return (
                dbc.Alert(
                    str(exc.message),
                    color="warning",
                    className="mb-0 py-1 px-2",
                ),
                *(no_update for _ in range(10)),
            )
        except Exception as exc:
            return (
                dbc.Alert(
                    f"准备模拟盒体积失败：{exc}",
                    color="danger",
                    className="mb-0 py-1 px-2",
                ),
                *(no_update for _ in range(10)),
            )

        columns = _direct_channel_columns()
        return (
            _render_channel_volume_status(evidence),
            result.get("production_rows") or [],
            columns,
            result.get("consumption_rows") or [],
            columns,
            str((result.get("kinetics") or {}).get("message") or ""),
            [],
            [],
            None,
            None,
            None,
        )

    @app.callback(
        Output("rxn-production-csv-btn", "disabled"),
        Output("rxn-consumption-csv-btn", "disabled"),
        Input("rxn-production-grid", "rowData"),
        Input("rxn-consumption-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _toggle_species_channel_exports(production_rows, consumption_rows):
        return not bool(production_rows), not bool(consumption_rows)

    @app.callback(
        Output("rxn-production-csv-download", "data"),
        Output("rxn-consumption-csv-download", "data"),
        Input("rxn-production-csv-btn", "n_clicks"),
        Input("rxn-consumption-csv-btn", "n_clicks"),
        State("rxn-production-grid", "rowData"),
        State("rxn-consumption-grid", "rowData"),
        State("app-store", "data"),
        State("rxn-channel-focus-store", "data"),
        State("species-focus", "data"),
        prevent_initial_call=True,
    )
    def _download_species_channels(
        production_clicks,
        consumption_clicks,
        production_rows,
        consumption_rows,
        store,
        channel_focus,
        species_focus,
    ):
        triggered_id = ctx.triggered_id
        if triggered_id == "rxn-production-csv-btn":
            if not production_clicks or not production_rows:
                raise PreventUpdate
            role = "production"
            rows = production_rows
        elif triggered_id == "rxn-consumption-csv-btn":
            if not consumption_clicks or not consumption_rows:
                raise PreventUpdate
            role = "consumption"
            rows = consumption_rows
        else:
            raise PreventUpdate

        store = store if isinstance(store, dict) else {}
        focus_smiles = _channel_focus_species(channel_focus, store, species_focus)
        focus_formula = str(svc.candidate_formula(focus_smiles) or "").strip()
        export_rows = [
            {
                "channel_role": role,
                "focus_formula": focus_formula,
                "focus_smiles": focus_smiles,
                "reaction_formula": row.get("reaction_formulas"),
                "reaction_smiles": row.get("reaction_smiles"),
                "reactant_smiles": json.dumps(row.get("reactant_smiles") or [], ensure_ascii=False),
                "product_smiles": json.dumps(row.get("product_smiles") or [], ensure_ascii=False),
                "timing_status": row.get("timing_status"),
                "timing_unit": row.get("timing_unit"),
                "time_basis": row.get("time_basis"),
                "timestep_ps": row.get("timestep_ps"),
                "first_after_timestep": row.get("first_after_timestep"),
                "last_after_timestep": row.get("last_after_timestep"),
                "first_time_ps": row.get("first_time_ps"),
                "last_time_ps": row.get("last_time_ps"),
                "forward_tp": row.get("forward_tp"),
                "reverse_tp": row.get("reverse_tp"),
                "net_tp": row.get("net_tp"),
                "share_pct": row.get("ratio_pct"),
                "event_count": row.get("event_count"),
                "event_frequency_per_ps": row.get("event_frequency_per_ps"),
                "observation_time_ps": row.get("observation_time_ps"),
                "k_app": row.get("k_app"),
                "k_app_unit": row.get("k_app_unit"),
                "k_app_ci95_low": row.get("k_app_ci95_low"),
                "k_app_ci95_high": row.get("k_app_ci95_high"),
                "kinetic_exposure": row.get("kinetic_exposure"),
                "kinetic_exposure_unit": row.get("kinetic_exposure_unit"),
                "kinetic_model": row.get("kinetic_model"),
                "kinetics_status": row.get("kinetics_status"),
                "kinetics_reason": row.get("kinetics_reason"),
                "kinetics_reason_message": row.get(
                    "kinetics_reason_message"
                ),
                "reverse_event_count": row.get("reverse_event_count"),
                "reverse_event_frequency_per_ps": row.get(
                    "reverse_event_frequency_per_ps"
                ),
                "reverse_kinetics_reason": row.get(
                    "reverse_kinetics_reason"
                ),
                "reverse_kinetics_reason_message": row.get(
                    "reverse_kinetics_reason_message"
                ),
                "reverse_k_app": row.get("reverse_k_app"),
                "reverse_k_app_unit": row.get("reverse_k_app_unit"),
                "reverse_k_app_ci95_low": row.get("reverse_k_app_ci95_low"),
                "reverse_k_app_ci95_high": row.get("reverse_k_app_ci95_high"),
            }
            for row in rows
        ]
        filename_stem = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            focus_formula or "species",
        ).strip("._-") or "species"
        download = {
            "content": svc.rows_to_csv(export_rows),
            "filename": f"{filename_stem}-{role}-channels.csv",
            "type": "text/csv;charset=utf-8",
        }
        if role == "production":
            return download, no_update
        return no_update, download

    @app.callback(
        Output("rxn-production-grid", "rowData", allow_duplicate=True),
        Output("rxn-production-grid", "columnDefs", allow_duplicate=True),
        Output("rxn-consumption-grid", "rowData", allow_duplicate=True),
        Output("rxn-consumption-grid", "columnDefs", allow_duplicate=True),
        Output("rxn-channel-alert", "children", allow_duplicate=True),
        Output("rxn-production-grid", "selectedRows", allow_duplicate=True),
        Output("rxn-consumption-grid", "selectedRows", allow_duplicate=True),
        Output("rxn-production-grid", "cellClicked", allow_duplicate=True),
        Output("rxn-consumption-grid", "cellClicked", allow_duplicate=True),
        Output("rxn-channel-selection-store", "data", allow_duplicate=True),
        Output("rxn-channel-history-store", "data", allow_duplicate=True),
        Output("species-focus", "data", allow_duplicate=True),
        Output("rxn-query-card", "style", allow_duplicate=True),
        Output("rxn-results-card", "style", allow_duplicate=True),
        Output("rxn-channel-view", "style", allow_duplicate=True),
        Output("rxn-channel-focus-store", "data", allow_duplicate=True),
        Input(
            {
                "type": "rxn-structure-species",
                "scope": ALL,
                "side": ALL,
                "index": ALL,
                "smiles": ALL,
                "formula": ALL,
            },
            "n_clicks",
        ),
        State("rxn-top", "value"),
        State("app-store", "data"),
        State("rxn-channel-history-store", "data"),
        State("rxn-production-grid", "rowData"),
        State("rxn-production-grid", "columnDefs"),
        State("rxn-production-grid", "selectedRows"),
        State("rxn-consumption-grid", "rowData"),
        State("rxn-consumption-grid", "columnDefs"),
        State("rxn-consumption-grid", "selectedRows"),
        State("rxn-channel-selection-store", "data"),
        State("rxn-channel-alert", "children"),
        State("rxn-channel-focus-store", "data"),
        State("species-focus", "data"),
        prevent_initial_call=True,
    )
    def _focus_reaction_structure_species(
        _clicks,
        top,
        store,
        history,
        production_data,
        production_columns,
        production_selected_rows,
        consumption_data,
        consumption_columns,
        consumption_selected_rows,
        channel_selection,
        channel_alert,
        channel_focus,
        species_focus,
    ):
        click_values = _clicks if isinstance(_clicks, (list, tuple)) else [_clicks]
        if not any(
            isinstance(value, (int, float)) and value > 0
            for value in click_values
        ):
            raise PreventUpdate
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            raise PreventUpdate
        selected_smiles = str(triggered.get("smiles") or "").strip()
        if not selected_smiles:
            raise PreventUpdate
        selected_formula = str(triggered.get("formula") or "").strip()
        if selected_formula == "?":
            selected_formula = ""
        store = store if isinstance(store, dict) else {}
        focus = {
            key: value
            for key, value in current_species_focus(species_focus, store).items()
            if key != "context"
        }
        history = list(history) if isinstance(history, list) else []
        if triggered.get("scope") == "channel":
            previous_species = _channel_focus_species(channel_focus, store, species_focus)
            previous_target = (
                str(svc.candidate_formula(previous_species) or "")
                or focus["selected_formula"] or previous_species
            )
            history.append(
                {
                    "kind": "channel",
                    "label": (
                        f"返回 {previous_target} 的通道"
                        if previous_target
                        else "返回上一物种通道"
                    ),
                    "focus": focus,
                    "production_data": production_data or [],
                    "production_columns": production_columns or [],
                    "production_selected_rows": production_selected_rows or [],
                    "consumption_data": consumption_data or [],
                    "consumption_columns": consumption_columns or [],
                    "consumption_selected_rows": consumption_selected_rows or [],
                    "selection": channel_selection,
                    "alert": channel_alert or "",
                    **({"channel_focus": channel_focus} if channel_focus else {}),
                }
            )
        else:
            history.append(
                {
                    "kind": "reaction-search",
                    "label": "返回反应式检索",
                    "focus": focus,
                }
            )
        updated_focus = species_focus_for(
            store, selected_smiles, selected_formula, "reaction_structure"
        )
        columns = _direct_channel_columns()
        try:
            result = svc.collect_species_channels(
                store.get("artifacts", {}) or {},
                selected_smiles,
                top=max(1, int(top or 50)),
                include_kinetics=False,
            )
            production_rows = result.get("production_rows") or []
            consumption_rows = result.get("consumption_rows") or []
            message = str((result.get("timing") or {}).get("message") or "")
        except svc.ServiceError as exc:
            production_rows = []
            consumption_rows = []
            message = str(exc.message)
        return (
            production_rows,
            columns,
            consumption_rows,
            columns,
            message,
            [],
            [],
            None,
            None,
            None,
            history,
            updated_focus,
            {"display": "none"},
            {"display": "none"},
            {"display": "block"},
            {"species": selected_smiles, "context": context_key(store)},
        )

    @app.callback(
        Output("rxn-channel-selection-store", "data"),
        Output(
            "rxn-production-grid",
            "selectedRows",
            allow_duplicate=True,
        ),
        Output(
            "rxn-consumption-grid",
            "selectedRows",
            allow_duplicate=True,
        ),
        Input("rxn-production-grid", "cellClicked"),
        Input("rxn-consumption-grid", "cellClicked"),
        Input("rxn-production-grid", "selectedRows"),
        Input("rxn-consumption-grid", "selectedRows"),
        State("rxn-production-grid", "rowData"),
        State("rxn-consumption-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _select_species_channel(
        production_active, consumption_active, production_selected, consumption_selected,
        production_rows, consumption_rows,
    ):
        triggered_props = _triggered_property_ids(ctx)
        lanes = {
            "production": (production_active, production_selected, production_rows, "生成"),
            "consumption": (consumption_active, consumption_selected, consumption_rows, "消耗"),
        }
        for lane, (clicked, selected, rows, label) in lanes.items():
            if f"rxn-{lane}-grid.cellClicked" in triggered_props:
                row = next((dict(item) for item in rows or []
                            if ui.row_identity(item) == str((clicked or {}).get("rowId"))), None)
            elif f"rxn-{lane}-grid.selectedRows" in triggered_props:
                row = _selected_table_row(selected, rows)
            else:
                continue
            if row is None:
                continue
            selection = {"lane": lane, "row": {**row, "role_label": row.get("role_label") or label}}
            return (selection, [row], []) if lane == "production" else (selection, [], [row])
        for lane, (_, selected, rows, label) in lanes.items():
            row = _selected_table_row(selected, rows)
            if row:
                return {"lane": lane, "row": {**row, "role_label": row.get("role_label") or label}}, no_update, no_update
        return None, no_update, no_update

    @app.callback(
        Output("rxn-channel-detail", "children"),
        Output("rxn-channel-detail", "className"),
        Output("rxn-channel-choice", "children"),
        Output("rxn-channel-to-event-btn", "disabled"),
        Input("rxn-channel-selection-store", "data"),
        Input("rxn-channel-show-h", "value"),
    )
    def _render_selected_species_channel(selection, show_h):
        row = (selection or {}).get("row") or {}
        if not row:
            return (
                "在上方表格中选择一条通道，查看完整结构反应式。",
                "rs-channel-detail rs-channel-detail-empty",
                "选择一条生成或消耗通道。",
                True,
            )
        detail = svc.build_channel_structure_detail(row, show_h=bool(show_h))
        children = (
            _reaction_structure_detail_children(
                detail,
                action_scope="channel",
            )
            if detail.get("ok")
            else "所选反应缺少可解析的 SMILES。"
        )
        role_label = str(row.get("role_label") or "通道")
        reaction = str(
            row.get("reaction_formulas")
            or row.get("reaction_smiles")
            or ""
        )
        return (
            children,
            "rs-channel-detail",
            f"已选{role_label}通道：{reaction}",
            False,
        )

    @app.callback(
        Output("rxn-structure-detail", "children"),
        Input("rxn-grid", "selectedRows"),
        Input("rxn-structure-show-h", "value"),
        State("rxn-grid", "rowData"),
    )
    def _show_reaction_structure(selected_rows, show_h, rows):
        row = _selected_table_row(selected_rows, rows)
        if not row:
            return "选择一条公式反应后显示完整结构反应式。"
        detail = svc.build_channel_structure_detail(
            row,
            show_h=bool(show_h),
        )
        if not detail.get("ok"):
            return "所选反应缺少可解析的 SMILES。"
        return _reaction_structure_detail_children(
            detail,
            action_scope="search",
        )
