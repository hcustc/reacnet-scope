"""Dataset switch transaction, validation, commit and restore callbacks."""

from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
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
from reacnet_scope import services as svc
from scripts.webapp_dash.chart_presentation import empty_chart_figure
from scripts.webapp_dash.navigation import (
    PAGE_LABELS,
)


def initial_store() -> dict[str, Any]:
    return {
        "folder": "",
        "base": "",
        "dataset_id": "",
        "source_revision": {},
        "context_state": "none",
        "label": "未选择",
        "capabilities": {},
        "analysis_capabilities": {},
        "readiness": {},
        "artifacts": {},
        "selected_smiles": "",
        "selected_formula": "",
        "selected_species_source": "",
        "inputs_pending": False,
    }


def _dataset_bound_resets() -> tuple[tuple[Output, Any], ...]:
    """Pair every dataset-bound UI value with its canonical empty state."""

    def reset(component_id: str, prop: str, value: Any) -> tuple[Output, Any]:
        return Output(component_id, prop, allow_duplicate=True), value

    return (
        reset("species-grid-store", "data", {"rows": []}),
        reset("cp-request", "data", None),
        reset("cp-raw", "data", None),
        reset("cp-report", "data", None),
        reset("cp-event-page", "data", None),
        reset("species-workspace-stage", "data", "results"),
        reset("species-detail-handoff", "data", None),
        reset("rxn-grid-store", "data", {"rows": []}),
        reset("reaction-event-request", "data", None),
        reset("reaction-event-response", "data", None),
        reset("reaction-event-store", "data", None),
        reset("reaction-event-grid", "rowData", []),
        reset("rxn-timing-distribution-store", "data", None),
        reset("rxn-timing-page-store", "data", None),
        reset("rxn-timing-graph", "figure", {}),
        reset("rxn-timing-event-grid", "rowData", []),
        reset("rxn-timing-card", "style", {"display": "none"}),
        reset("evolution-payload-store", "data", None),
        reset("evolution-focus-store", "data", None),
        reset("evolution-browse-mode", "value", "species"),
        reset("evolution-ranking", "active_item", None),
        reset("evolution-start-timestep", "value", None),
        reset("evolution-end-timestep", "value", None),
        reset("evolution-filter-result", "data", None),
        reset("evolution-filter-grid", "rowData", []),
        reset("evolution-filter-grid", "selectedRows", []),
        *(reset(f"{prefix}-{field}", "value", [] if field in {"required", "excluded"} else None)
          for prefix in ("evolution-filter", "evolution-group-filter")
          for field in ("required", "excluded", "element", "min", "max")),
        reset("evolution-group-payload-store", "data", None),
        reset("evolution-rank-store", "data", None),
        reset("evolution-rank-grid", "rowData", []),
        reset("evolution-rank-grid", "selectedRows", []),
        reset("evolution-group-members-grid", "rowData", []),
        reset("evolution-group-members-grid", "selectedRows", []),
        reset("evolution-timestep", "value", None),
        reset("element-distribution-payload-store", "data", None),
        reset("element-distribution-timestep", "value", None),
        reset("event-grid-store", "data", {"rows": []}),
        reset("event-selected-store", "data", None),
        reset("event-detail-response", "data", None),
        reset("event-detail-state", "data", None),
        reset("event-detail-dismissed", "data", None),
        reset("event-bookmark-validation-store", "data", None),
        reset("event-viewer-store", "data", None),
        reset("event-viewer-response", "data", None),
        reset("event-dft-store", "data", None),
        reset("molecule-lineage-store", "data", None),
        reset("molecule-lineage-response", "data", None),
        reset("molecule-lineage-drilldown-store", "data", None),
        reset("species-grid", "rowData", []),
        reset("species-grid", "selectedRows", []),
        reset("species-grid", "cellClicked", None),
        reset("species-grid-previews", "data", []),
        reset("species-structure-grid", "rowData", []),
        reset("species-structure-grid", "selectedRows", []),
        reset("species-structure-grid", "cellClicked", None),
        reset("species-structure-grid-previews", "data", []),
        reset("rxn-grid", "rowData", []),
        reset("rxn-grid", "selectedRows", []),
        reset("rxn-grid", "cellClicked", None),
        reset("rxn-production-grid", "rowData", []),
        reset("rxn-production-grid", "columnDefs", []),
        reset("rxn-production-grid", "selectedRows", []),
        reset("rxn-production-grid", "cellClicked", None),
        reset("rxn-consumption-grid", "rowData", []),
        reset("rxn-consumption-grid", "columnDefs", []),
        reset("rxn-consumption-grid", "selectedRows", []),
        reset("rxn-consumption-grid", "cellClicked", None),
        reset("rxn-channel-selection-store", "data", None),
        reset("rxn-channel-alert", "children", ""),
        reset("rxn-channel-timestep-ps", "value", None),
        reset("rxn-channel-timestep-status", "children", ""),
        reset("rxn-channel-trajectory-path", "value", ""),
        reset("rxn-channel-coordinate-unit-confirm", "value", False),
        reset("rxn-channel-volume-status", "children", ""),
        reset("rxn-channel-volume-progress", "children", ""),
        reset("rxn-channel-volume-refresh", "disabled", True),
        reset("rxn-channel-view", "style", {"display": "none"}),
        reset("rxn-channel-history-store", "data", []),
        reset("event-grid", "rowData", []),
        reset("event-grid", "selectedRows", []),
        reset("event-grid", "cellClicked", None),
        reset("batch-matrix-grid", "selectedRows", []),
        reset("molecule-lineage-event-grid", "rowData", []),
        reset("molecule-lineage-event-grid", "selectedRows", []),
        reset("molecule-lineage-cytoscape", "elements", []),
        reset("evolution-graph", "figure", empty_chart_figure(
            "选择对象，开始查看时间演化", "从左侧选择物种或元素分组。"
        )),
        reset("element-distribution-composition-trend", "figure", go.Figure()),
        reset("element-distribution-composition-table", "rowData", []),
        reset("event-extract-id", "value", ""),
        reset("event-frame-slider", "min", 0),
        reset("event-frame-slider", "max", 0),
        reset("event-frame-slider", "value", 0),
        reset("event-frame-slider", "marks", {}),
        reset("evolution-species-file", "value", ""),
        reset("evolution-species-files", "value", ""),
        reset("evolution-species-picker", "options", []),
        reset("evolution-species-picker", "value", []),
        reset("evolution-catalog-alert", "children", ""),
        reset("import-pending-evolution", "data", None),
        reset("import-pending-elements", "data", None),
        reset("import-pending-events", "data", None),
    )


def _dataset_bound_reset_outputs() -> tuple[Output, ...]:
    return tuple(output for output, _value in _dataset_bound_resets())


def _dataset_bound_reset_values() -> tuple[Any, ...]:
    return tuple(value for _output, value in _dataset_bound_resets())


def register(app: Any) -> None:
    """Register dataset switch callbacks."""

    # ── Directory browser (internal data-management view) ────────────


    @app.callback(
        Output("dataset-switch-transaction", "data"),
        Output("dataset-switch-request", "data"),
        inputs=(
            Input("data-apply-btn", "n_clicks"),
            Input("library-use", "n_clicks"),
            Input({"type": "library-use-entry", "base": ALL}, "n_clicks"),
            Input("data-browser-index-btn", "n_clicks"),
            Input("dir-browser-cancel-btn", "n_clicks"),
            State("dataset-browser-candidate", "data"),
            State("page-store", "data"),
            Input("species-compare-switch-request", "data"),
            State("library-select", "value"),
            State("dataset-library", "data"),
            State("dataset-switch-transaction", "data"),
            State({"type": "dataset-bound-operation", "name": ALL}, "data"),
        ),
        prevent_initial_call=True,
    )
    def _reduce_dataset_switch(
        _apply_clicks,
        _library_clicks,
        _entry_clicks,
        _index_clicks,
        _cancel_clicks,
        candidate,
        page_store,
        compare_switch,
        library_selected,
        library_records,
        transaction,
        bound_operations,
    ):
        """Keep one authoritative switch request for this browser tab."""
        triggered = ctx.triggered_id
        # A click can arrive in the same Dash update as page hydration.
        # Process the explicit action rather than dropping it as a page change.
        for action in ("dir-browser-cancel-btn", "library-use", "data-apply-btn"):
            if any(item["prop_id"] == action + ".n_clicks" and item.get("value") for item in ctx.triggered):
                triggered = action
                break
        if triggered != "dir-browser-cancel-btn":
            for item in ctx.triggered:
                prop_id = str(item.get("prop_id") or "")
                if not prop_id.endswith(".n_clicks") or not item.get("value"):
                    continue
                try:
                    clicked_id = json.loads(prop_id.rsplit(".", 1)[0])
                except (TypeError, ValueError):
                    continue
                if isinstance(clicked_id, dict) and clicked_id.get("type") == "library-use-entry":
                    library_selected = clicked_id.get("base")
                    triggered = "library-use"
                    break
        current = transaction if isinstance(transaction, dict) else {}
        selected = candidate if isinstance(candidate, dict) else {}

        if triggered == "species-compare-switch-request":
            handoff = compare_switch if isinstance(compare_switch, dict) else {}
            if not handoff.get("candidate") or current.get("state") == "validating":
                raise PreventUpdate
            if any(bool(value) for value in bound_operations or []):
                return ({"state": "failed", "reason": "analysis_in_progress",
                         "message": "当前分析仍在完成，请等待后重试；RNG 数据未切换。"}, no_update)
            selected = handoff["candidate"]
            if not selected.get("dataset_id") or not selected.get("base"):
                return ({"state": "failed", "reason": "missing_dataset_identity",
                         "message": "所选来源尚未登记为 RNG 数据；请先导入。"}, no_update)
            request = svc.begin_dataset_switch(
                selected,
                origin={"page": "species", "library": True,
                        "target_smiles": handoff["target_smiles"],
                        "expected_source_revision": handoff.get("expected_source_revision"),
                        "origin_frame": handoff.get("origin_frame")},
            )
            return request, request

        if triggered == "library-use":
            selected = next((entry for entry in svc.normalise_dataset_library(library_records)
                             if entry["base"] == library_selected), {})
            # Ensure dataset_id is included for identity verification
            if not selected.get("dataset_id"):
                return (
                    {
                        "state": "failed",
                        "candidate": selected,
                        "reason": "missing_dataset_identity",
                        "message": "RNG 数据身份缺失，请重新导入。",
                    },
                    no_update,
                )

        if isinstance(triggered, str) and triggered in {"data-apply-btn", "library-use"}:
            if current.get("state") == "validating":
                raise PreventUpdate
            if any(bool(value) for value in bound_operations or []):
                return (
                    {
                        "state": "failed",
                        "candidate": selected,
                        "reason": "analysis_in_progress",
                        "message": (
                            "当前分析仍在完成，暂不能切换RNG 数据。"
                            "等待该分析结束后重试；"
                            "当前RNG 数据和所选RNG 数据均已保留。"
                        ),
                    },
                    no_update,
                )
            if not selected.get("folder") or not selected.get("base"):
                return (
                    {
                        "state": "failed",
                        "candidate": selected,
                        "reason": "missing_candidate",
                        "message": (
                            "请先选择要加载的RNG 数据。当前RNG 数据未改变。"
                        ),
                    },
                    no_update,
                )
            request = svc.begin_dataset_switch(
                selected,
                origin=({"page": (page_store or {}).get("page"), "library": True}
                        if triggered == "library-use" else dict((page_store or {}).get("dataset_return") or {})),
            )
            return request, request

        if isinstance(triggered, str) and triggered in {"data-browser-index-btn", "dir-browser-cancel-btn"}:
            superseded = svc.supersede_dataset_switch(
                current,
                reason=(
                    "returned_to_index_management"
                    if triggered == "data-browser-index-btn"
                    else "cancelled"
                ),
            )
            return (
                superseded
                or {
                    "state": "superseded",
                    "reason": (
                        "returned_to_index_management"
                        if triggered == "data-browser-index-btn"
                        else "cancelled"
                    ),
                },
                no_update,
            )

        raise PreventUpdate

    # Preview/page updates must not delay or overwrite an explicit switch.
    # Cancellation is local and immediate; late validation is rejected by the
    # existing server resolver using the authoritative transaction state.
    preview_switch_transaction = """function(candidate, page, transaction) {
            const none = window.dash_clientside.no_update;
            const current = transaction || {};
            const origin = current.origin || {};
            const changed = window.dash_clientside.callback_context.triggered
                .map(item => item.prop_id);
            const supersede = reason =>
                ['validating', 'candidate-selected', 'failed'].includes(current.state)
                ? {...current, state: 'superseded', reason} : none;
            if (changed.includes('page-store.data')) {
                if ((page || {}).page === 'data-management' ||
                    (origin.library && origin.page === (page || {}).page)) return none;
                return supersede('left_workspace');
            }
            if (origin.library || current.state === 'succeeded') return none;
            if (current.state === 'validating') return supersede('candidate_changed');
            return candidate ? {state: 'candidate-selected', candidate} : {state: 'idle'};
        }"""
    app.clientside_callback(
        preview_switch_transaction,
        Output("dataset-switch-transaction", "data", allow_duplicate=True),
        Input("dataset-browser-candidate", "data"), State("page-store", "data"),
        State("dataset-switch-transaction", "data"), prevent_initial_call=True,
    )
    app.clientside_callback(
        "function(page, candidate, transaction) { return (" +
        preview_switch_transaction + ")(candidate, page, transaction); }",
        Output("dataset-switch-transaction", "data", allow_duplicate=True),
        Input("page-store", "data"), State("dataset-browser-candidate", "data"),
        State("dataset-switch-transaction", "data"), prevent_initial_call=True,
    )

    @app.callback(
        Output("dataset-switch-validation", "data"),
        Input("dataset-switch-request", "data"),
        prevent_initial_call=True,
    )
    def _validate_dataset_switch(switch_request):
        request = switch_request if isinstance(switch_request, dict) else {}
        if request.get("state") != "validating":
            raise PreventUpdate
        request_id = str(request.get("request_id") or "")
        candidate = request.get("candidate") or {}
        try:
            validation = (
                svc.validate_file_collection_candidate(candidate)
                if candidate.get("collection_id")
                else svc.validate_dataset_candidate(
                    str(candidate.get("folder") or ""),
                    str(candidate.get("base") or ""),
                )
            )
            validation = {
                **validation,
                "label": str(
                    candidate.get("label")
                    or Path(str(candidate.get("folder") or "")).name
                    or validation.get("label")
                    or "未命名RNG 数据"
                ),
            }
        except svc.ServiceError as exc:
            return {
                "request_id": request_id,
                "ok": False,
                "reason": str(exc.reason or "validation_failed"),
                "message": (
                    f"{exc.message} 当前RNG 数据未改变；"
                    "请修正数据来源后重试。"
                ),
                "completed_ns": time.time_ns(),
            }
        except Exception:
            return {
                "request_id": request_id,
                "ok": False,
                "reason": "validation_failed",
                "message": (
                    "暂时无法加载所选RNG 数据，当前RNG 数据未改变；"
                    "请重试或重新选择。"
                ),
                "completed_ns": time.time_ns(),
            }
        return {
            "request_id": request_id,
            "ok": True,
            "validation": validation,
            "completed_ns": time.time_ns(),
        }

    @app.callback(
        Output("dataset-switch-transaction", "data", allow_duplicate=True),
        Input("dataset-switch-validation", "data"),
        State("dataset-switch-transaction", "data"),
        State({"type": "dataset-bound-operation", "name": ALL}, "data"),
        prevent_initial_call=True,
    )
    def _resolve_dataset_switch_validation(
        validation_result,
        transaction,
        bound_operations,
    ):
        """Resolve validation outside the callback that initiated the request."""
        current = transaction if isinstance(transaction, dict) else {}
        if any(bool(value) for value in bound_operations or []):
            return {
                **current,
                "state": "failed",
                "reason": "analysis_in_progress",
                "message": (
                    "RNG 数据检查完成时仍有当前RNG 数据的分析在运行，"
                    "因此结果未提交。"
                    "当前RNG 数据和所选RNG 数据均已保留；等待分析结束后重试。"
                ),
            }
        resolved = svc.resolve_dataset_switch(current, validation_result)
        if resolved == current:
            raise PreventUpdate
        if resolved.get("state") == "succeeded":
            try:
                svc.commit_file_collection(resolved.get("validation") or {})
            except svc.ServiceError as exc:
                return {**resolved, "state": "failed", "reason": exc.reason,
                        "message": f"{exc.message} 当前RNG 数据未改变。"}
        return resolved

    @app.callback(
        Output("data-load-feedback", "children", allow_duplicate=True),
        Input("dataset-switch-transaction", "data"),
        prevent_initial_call=True,
    )
    def _render_dataset_switch(transaction):
        state = str((transaction or {}).get("state") or "idle")
        if ((transaction or {}).get("origin") or {}).get("library"):
            return ""
        if state == "validating":
            return dbc.Alert(
                "正在检查RNG 数据与最新源文件；当前RNG 数据仍然有效。",
                color="info",
                className="py-2",
            )
        if state == "failed":
            return html.Div(dbc.Alert(
                str((transaction or {}).get("message") or "加载失败，请重试。"),
                color="danger", className="py-2",
            ), role="alert", tabIndex=-1)
        if state == "succeeded":
            return no_update
        if state == "candidate-selected":
            return ""
        return ""



    @app.callback(
        Output({"type": "dir-browser-recent-entry", "index": ALL}, "disabled"),
        Input("dataset-switch-transaction", "data"),
        State({"type": "dir-browser-recent-entry", "index": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _lock_dynamic_dataset_choices(transaction, recent_ids):
        validating = (transaction or {}).get("state") == "validating"
        return [validating for _item in recent_ids or []]

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Output("recent-datasets", "data", allow_duplicate=True),
        Output("dataset-browser-candidate", "data", allow_duplicate=True),
        Output("data-load-feedback", "children", allow_duplicate=True),
        Output("dataset-switch-navigation", "data"),
        Output("dataset-context-commit", "data"),
        Output("global-dataset-notice", "children"),
        Output("data-overview-view", "className", allow_duplicate=True),
        Output("data-browser-view", "className", allow_duplicate=True),
        Output("topbar-folder", "children"),
        Output("topbar-rungroup", "children"),
        Output("topbar-status", "children"),
        Output("topbar-status", "className"),
        *_dataset_bound_reset_outputs(),
        Input("dataset-switch-transaction", "data"),
        State("app-store", "data"),
        State("recent-datasets", "data"),
        prevent_initial_call=True,
    )
    def _commit_dataset_switch(transaction, current_store, recent_records):
        request = transaction if isinstance(transaction, dict) else {}
        if request.get("state") != "succeeded":
            raise PreventUpdate
        validation = request.get("validation") or {}
        navigation = {
            "request_id": str(request.get("request_id") or ""),
            "page": (
                "species" if (validation.get("artifacts") or {}).get("reaction")
                else "element-distribution" if (validation.get("artifacts") or {}).get("species")
                else "events"
            ) if validation.get("collection_id") else "data-management",
        }
        origin_page = str((request.get("origin") or {}).get("page") or "")
        origin_is_library = bool((request.get("origin") or {}).get("library"))
        # Return to origin page only if switching directly from library list (not from modal/browser)
        # This ensures library list switches return to the analysis page, while modal switches show overview
        if origin_is_library and origin_page in PAGE_LABELS and origin_page != "data-management":
            navigation["page"] = origin_page
        label = str(validation.get("label") or "未命名RNG 数据")
        if svc.is_same_dataset_revision(current_store, validation):
            message = f"{label} 已是当前使用的RNG 数据。"
            return (
                no_update,
                no_update,
                recent_records,
                request.get("candidate"),
                ("" if origin_is_library else dbc.Alert(
                    message,
                    color="info",
                    className="py-2 rs-data-inline-feedback",
                    duration=8000,
                )),
                navigation,
                {},
                no_update,
                "rs-data-view",
                "rs-data-view d-none",
                no_update,
                no_update,
                no_update,
                no_update,
                *((no_update,) * len(_dataset_bound_reset_outputs())),
            )

        new_store = svc.current_dataset_from_validation(validation)
        recent = svc.normalise_recent_datasets(
            [
                {
                    "folder": new_store["folder"],
                    "base": new_store["base"],
                    "label": label,
                    "loaded_at": int(time.time()),
                },
                *(recent_records if isinstance(recent_records, list) else []),
            ]
        )
        message = (
            f"当前RNG 数据已切换为：{label}。旧结果与选择已清除；"
            "查询条件已保留，但尚未在新RNG 数据运行。"
        )
        marker = {
            "request_id": str(request.get("request_id") or ""),
            "dataset_id": str(new_store.get("dataset_id") or ""),
            "source_revision": dict(new_store.get("source_revision") or {}),
        }
        return (
            new_store,
            new_store,
            recent,
            None,
            ("" if origin_is_library else dbc.Alert(
                message,
                color="success",
                className="py-2 rs-data-inline-feedback",
                duration=8000,
            )),
            navigation,
            marker,
            no_update,
            "rs-data-view",
            "rs-data-view d-none",
            label,
            label,
            "分析功能按项显示",
            "rs-badge",
            *_dataset_bound_reset_values(),
        )

    app.clientside_callback(
        """
        function(request) {
            if (!request || !request.target || !request.token) {
                return window.dash_clientside.no_update;
            }
            let attempts = 0;
            function focusWhenVisible() {
                const target = document.getElementById(request.target);
                const expectedText = request.expected_text || "";
                const textMatches = !expectedText ||
                    (target && target.textContent.trim() === expectedText);
                if (target && target.getClientRects().length > 0 && textMatches) {
                    if (!target.hasAttribute("tabindex")) {
                        target.setAttribute("tabindex", "-1");
                    }
                    target.focus();
                    return;
                }
                attempts += 1;
                if (attempts < 30) {
                    window.requestAnimationFrame(focusWhenVisible);
                }
            }
            window.requestAnimationFrame(focusWhenVisible);
            return request.token;
        }
        """,
        Output("dataset-focus-sink", "children"),
        Input("dataset-focus-request", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Output("dataset-restore-result", "data"),
        Output("dataset-context-commit", "data", allow_duplicate=True),
        Output("global-dataset-notice", "children", allow_duplicate=True),
        *_dataset_bound_reset_outputs(),
        Input("dataset-session-restore", "n_intervals"),
        State("dataset-session-store", "data"),
        prevent_initial_call=True,
    )
    def _restore_current_dataset(_tick, session_store):
        current = session_store if isinstance(session_store, dict) else {}
        if not current.get("dataset_id"):
            empty = initial_store()
            if current == empty:
                # Fresh tabs have no prior dataset-bound results. Rewriting
                # every empty store creates a second wave of Dash callbacks
                # just as the user can first click an imported dataset.
                return (
                    empty,
                    no_update,
                    {"state": "none"},
                    no_update,
                    no_update,
                    *((no_update,) * len(_dataset_bound_reset_outputs())),
                )
            return (
                empty,
                empty,
                {"state": "none"},
                {},
                no_update,
                *_dataset_bound_reset_values(),
            )
        result = svc.revalidate_current_dataset(current)
        state = str(result.get("state") or "unavailable")
        if state == "active":
            return (
                result.get("context"),
                result.get("context"),
                result,
                {},
                no_update,
                *((no_update,) * len(_dataset_bound_reset_outputs())),
            )
        if state == "revision-changed":
            marker = {
                "request_id": f"restore-{time.time_ns()}",
                "reason": "restore-revision-changed",
            }
            return (
                result.get("context"),
                result.get("context"),
                result,
                marker,
                dbc.Alert(
                    "当前RNG 数据的源文件已变化。旧证据已停止作为当前结果；"
                    "查询条件已保留，请更新当前RNG 数据状态。",
                    color="warning",
                    className="mb-0",
                ),
                *_dataset_bound_reset_values(),
            )
        marker = {
            "request_id": f"restore-{time.time_ns()}",
            "reason": "restore-unavailable",
        }
        return (
            initial_store(),
            initial_store(),
            result,
            marker,
            dbc.Alert(
                "无法恢复当前RNG 数据，已清除失效上下文；查询输入和最近记录均已保留。",
                color="danger",
                className="mb-0",
            ),
            *_dataset_bound_reset_values(),
        )

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Output("dataset-context-commit", "data", allow_duplicate=True),
        Output("global-dataset-notice", "children", allow_duplicate=True),
        *_dataset_bound_reset_outputs(),
        Input("data-current-refresh-btn", "n_clicks"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _refresh_current_dataset(n_clicks, app_store):
        if n_clicks is None:
            raise PreventUpdate
        current = app_store if isinstance(app_store, dict) else {}
        if not current.get("dataset_id"):
            raise PreventUpdate
        was_revision_changed = current.get("context_state") == "revision-changed"
        result = svc.revalidate_current_dataset(
            current,
            adopt_revision=was_revision_changed,
        )
        state = str(result.get("state") or "unavailable")
        if state == "active":
            marker = (
                {
                    "request_id": f"refresh-{time.time_ns()}",
                    "reason": "revision-adopted",
                }
                if was_revision_changed
                else {}
            )
            message = (
                "已采用当前RNG 数据的最新源修订；查询条件已保留但尚未重新运行。"
                if was_revision_changed
                else "当前RNG 数据的身份和源修订仍然有效。"
            )
            return (
                result.get("context"),
                result.get("context"),
                marker,
                dbc.Alert(message, color="success", className="mb-0"),
                *(
                    _dataset_bound_reset_values()
                    if was_revision_changed
                    else (no_update,) * len(_dataset_bound_reset_outputs())
                ),
            )
        if state == "revision-changed":
            return (
                result.get("context"),
                result.get("context"),
                {
                    "request_id": f"refresh-{time.time_ns()}",
                    "reason": "revision-changed",
                },
                dbc.Alert(
                    "检测到源修订变化。旧证据已停止作为当前结果；"
                    "请选择“更新当前RNG 数据状态”采用新修订。",
                    color="warning",
                    className="mb-0",
                ),
                *_dataset_bound_reset_values(),
            )
        return (
            initial_store(),
            initial_store(),
            {
                "request_id": f"refresh-{time.time_ns()}",
                "reason": "current-unavailable",
            },
            dbc.Alert(
                "当前RNG 数据已不可用，已清除失效上下文；查询输入和最近记录均已保留。",
                color="danger",
                className="mb-0",
            ),
            *_dataset_bound_reset_values(),
        )
