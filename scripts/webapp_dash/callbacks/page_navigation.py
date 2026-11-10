"""Page navigation, page restore and global dataset status callbacks."""

from __future__ import annotations

import time
import json
from typing import Any
from dash import (
    ALL,
    Input,
    Output,
    State,
    ctx,
    no_update,
)
from dash.exceptions import PreventUpdate
from reacnet_scope import services as svc
from scripts.webapp_dash.navigation import (
    DEFAULT_PAGE,
    PAGE_CAPABILITY_REQUIREMENTS,
    PAGE_CLASS_NAMES,
    PAGE_DESCRIPTIONS,
    PAGE_IDS,
    PAGE_LABELS,
    PAGE_SECTIONS,
    PAGE_WORKSPACES,
    TOP_NAV_PAGE_IDS,
    migrate_page_state,
    resolve_page_id,
)

from .common import (
    _CAPABILITY_LABELS,
    _CAPABILITY_STATE_LABELS,
    _capabilities_from_store,
    _capability_state_class,
    _evolution_comparison_active,
    _pattern_trigger_type,
)


def register(app: Any) -> None:
    """Register page navigation callbacks."""

    # ── Navigation ──────────────────────────────────────────────────

    @app.callback(
        *(Output(f"page-{page_id}", "className") for page_id in PAGE_IDS),
        *(
            Output(f"nav-{page_id}", "className")
            for page_id in TOP_NAV_PAGE_IDS
        ),
        Output("nav-data-management", "className"),
        Output("data-open-batch-compare-btn", "className"),
        *(
            Output(f"nav-{page_id}", "aria-current")
            for page_id in TOP_NAV_PAGE_IDS
        ),
        Output("nav-data-management", "aria-current"),
        Output("data-open-batch-compare-btn", "aria-current"),
        Output("page-store", "data"),
        Output("page-title", "children"),
        Output("page-eyebrow-section", "children"),
        Output("page-description", "children"),
        Output("topbar-page-context", "children"),
        Output("page-header", "className"),
        Output("app-body", "className"),
        Output("dataset-focus-request", "data"),
        *(Input(f"nav-{page_id}", "n_clicks") for page_id in TOP_NAV_PAGE_IDS),
        Input("nav-data-management", "n_clicks"),
        Input("data-open-batch-compare-btn", "n_clicks"),
        Input("data-open-species-btn", "n_clicks"),
        Input(
            {"type": "data-overview-open-page", "page": ALL},
            "n_clicks",
        ),
        Input(
            {"type": "workspace-open-page", "page": ALL},
            "n_clicks",
        ),
        Input("data-pick-btn", "n_clicks"),
        Input("library-add-more", "n_clicks"),
        Input("open-data-modal", "n_clicks"),
        Input("page-capability-manage-btn", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("species-result-compare-btn", "n_clicks"),
        Input("reaction-open-compare-btn", "n_clicks"),
        Input("reaction-compare-back-btn", "n_clicks"),
        Input("species-to-channels-btn", "n_clicks"),
        Input("species-to-evolution-btn", "n_clicks"),
        Input("species-direct-route-btn", "n_clicks"),
        Input("analysis-back-btn", "n_clicks"),
        Input("evolution-back-btn", "n_clicks"),
        Input("evolution-open-compare-btn", "n_clicks"),
        Input("evolution-close-compare-btn", "n_clicks"),
        Input("evolution-distribution-tab", "n_clicks"),
        Input("element-distribution-back-evolution-btn", "n_clicks"),
        Input("event-participant-handoff", "data"),
        Input("cp-prepare", "n_clicks"),
        Input("cp-from-species", "n_clicks"),
        Input("rxn-timing-open-events-btn", "n_clicks"),
        Input("event-back-btn", "n_clicks"),
        Input("event-extract-btn", "n_clicks"),
        Input("event-detail-expand-btn", "n_clicks"),
        Input("trajectory-back-events-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input("dataset-switch-navigation", "data"),
        State("page-store", "data"),
        prevent_initial_call=True,
    )
    def _navigate(*_args):
        triggered_id = ctx.triggered_id
        triggered = ctx.triggered[0] if ctx.triggered else {}
        # Dynamic overview buttons may hydrate in the same renderer batch as
        # a real click or a completed import. Their zero-click notification
        # must not swallow that navigation event.
        direct_clicks = [item for item in ctx.triggered
            if str(item.get("prop_id") or "").endswith(".n_clicks")
            and not str(item.get("prop_id") or "").startswith("{")
            and isinstance(item.get("value"), (int, float)) and item["value"] > 0]
        # A freshly mounted page can report its navigation click alongside the
        # next action. The action is the user's latest intent in that batch.
        direct_click = next((item for item in reversed(direct_clicks)
                             if not str(item["prop_id"]).startswith("nav-")),
                            direct_clicks[-1] if direct_clicks else None)
        switch_event = next((item for item in ctx.triggered
            if item.get("prop_id") == "dataset-switch-navigation.data" and item.get("value")), None)
        if direct_click or switch_event:
            triggered = direct_click or switch_event
            triggered_id = triggered["prop_id"].rsplit(".", 1)[0]
        if str(triggered.get("prop_id") or "").endswith(".n_clicks"):
            raw_clicks = triggered.get("value")
            if raw_clicks is None and isinstance(triggered_id, dict):
                triggered_type = triggered_id.get("type")
                for input_item in ctx.inputs_list:
                    items = input_item if isinstance(input_item, list) else [input_item]
                    for item in items:
                        item_id = item.get("id")
                        try:
                            pattern_id = json.loads(item_id) if isinstance(item_id, str) else item_id
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(pattern_id, dict):
                            continue
                        if pattern_id == triggered_id or (
                            pattern_id.get("type") == triggered_type
                            and any(isinstance(value, list) for value in pattern_id.values())
                        ):
                            raw_clicks = item.get("value")
                            break
                    if raw_clicks is not None:
                        break

            def has_positive_click(value: Any) -> bool:
                if isinstance(value, (list, tuple)):
                    return any(has_positive_click(item) for item in value)
                return isinstance(value, (int, float)) and value > 0

            if not has_positive_click(raw_clicks):
                # Pattern-matched controls can be inserted while the restored
                # layout hydrates.  Their zero-valued n_clicks notification is
                # not navigation and must not overwrite the session page.
                raise PreventUpdate
        triggered_string_id = (
            triggered_id if isinstance(triggered_id, str) else None
        )
        if triggered_string_id == "evolution-back-btn":
            triggered_string_id = "analysis-back-btn"
        if triggered_string_id == "event-participant-handoff" and not (
            isinstance(triggered.get("value"), dict) and triggered["value"].get("token")
        ):
            raise PreventUpdate
        stored_state = migrate_page_state(_args[-1] if _args else {})
        stored_page = resolve_page_id(stored_state.get("page"))
        switch_navigation = _args[-2] if len(_args) >= 2 else {}
        if triggered_string_id == "dataset-switch-navigation":
            page_id = str((switch_navigation or {}).get("page") or stored_page)
        elif _pattern_trigger_type(triggered_id) == "data-overview-open-page":
            page_id = str(triggered_id.get("page") or "data-management")
        elif _pattern_trigger_type(triggered_id) == "workspace-open-page":
            page_id = str(triggered_id.get("page") or DEFAULT_PAGE)
            if page_id in {"reaction-candidates", "reaction-related"}:
                page_id = "reactions"
        elif triggered_string_id == "dir-browser-cancel-btn":
            page_id = str(
                ((stored_state.get("dataset_return") or {}).get("page"))
                or "data-management"
            )
        elif triggered_string_id == "rxn-timing-open-events-btn":
            page_id = "events"
        elif triggered_string_id == "event-participant-handoff":
            page_id = "trajectory"
        elif triggered_string_id == "analysis-back-btn":
            page_id = "species"
        elif triggered_string_id == "evolution-distribution-tab":
            page_id = "element-distribution"
        elif triggered_string_id == "element-distribution-back-evolution-btn":
            page_id = "evolution"
        elif triggered_string_id == "reaction-open-compare-btn":
            page_id = "reaction-compare"
        elif triggered_string_id == "reaction-compare-back-btn":
            page_id = "reactions"
        elif triggered_string_id == "event-back-btn":
            page_id = stored_state.get("return_page") or DEFAULT_PAGE
        elif triggered_string_id in {"event-extract-btn", "event-detail-expand-btn"}:
            page_id = "trajectory"
        elif triggered_string_id == "trajectory-back-events-btn":
            page_id = stored_state.get("return_page") or (
                "reactions" if stored_state.get("candidate_direct_return") else "events"
            )
        elif triggered_string_id in {
            "species-to-channels-btn",
            "cp-from-species",
            "species-direct-route-btn",
        }:
            page_id = "reactions"
        elif triggered_string_id in {
            "data-open-batch-compare-btn",
            "evolution-open-compare-btn",
            "species-result-compare-btn",
        }:
            page_id = "evolution"
        elif triggered_string_id == "data-open-species-btn":
            page_id = "species"
        elif triggered_string_id in {
            "nav-data-management",
            "cp-prepare",
            "data-pick-btn",
            "library-add-more",
            "open-data-modal",
            "page-capability-manage-btn",
            "species-open-data-modal",
        }:
            page_id = "data-management"
        elif triggered_string_id in {"species-to-evolution-btn", "evolution-close-compare-btn"}:
            page_id = "evolution"
        else:
            page_id = (
                triggered_string_id.removeprefix("nav-")
                if triggered_string_id
                else stored_page
            )
        page_id = resolve_page_id(page_id)
        page_classes = {
            pid: (
                f"{PAGE_CLASS_NAMES.get(pid, 'rs-page')} active"
                if pid == page_id
                else PAGE_CLASS_NAMES.get(pid, "rs-page")
            )
            for pid in PAGE_IDS
        }
        page_state = {"page": page_id, "version": 2}
        if (stored_state.get("return_stack")
                and triggered_string_id != "dataset-switch-navigation"
                and not str(triggered_string_id or "").startswith("nav-")):
            page_state["return_stack"] = list(stored_state["return_stack"])[-4:]
        species_entry_actions = {
            "species-to-channels-btn", "cp-from-species",
            "species-direct-route-btn",
            "species-to-evolution-btn",
        }
        if triggered_string_id in species_entry_actions:
            page_state["entry"] = "species"
        elif triggered_string_id in {
            "event-participant-handoff", "event-extract-btn", "event-detail-expand-btn",
            "event-back-btn", "trajectory-back-events-btn",
            "rxn-timing-open-events-btn",
            "evolution-open-compare-btn", "evolution-close-compare-btn",
        } and stored_state.get("entry") == "species":
            page_state["entry"] = "species"
        active_workspace = str(page_state.get("entry") or PAGE_WORKSPACES.get(page_id, DEFAULT_PAGE))
        nav_classes = {
            pid: f"rs-top-nav-item{' active' if pid == active_workspace else ''}"
            for pid in TOP_NAV_PAGE_IDS
        }
        if page_id == "evolution" and triggered_string_id in {
            "data-open-batch-compare-btn", "evolution-open-compare-btn",
            "species-result-compare-btn",
        }:
            page_state["compare_sources"] = True
        if (
            triggered_string_id in {
                "data-pick-btn",
                "open-data-modal",
                "page-capability-manage-btn",
                "species-open-data-modal",
            }
            and stored_page in PAGE_IDS
            and stored_page != "data-management"
        ):
            page_state["dataset_return"] = {
                "page": stored_page,
                "trigger": str(triggered_id),
            }
        elif (
            page_id == "data-management"
            and triggered_string_id == "data-pick-btn"
            and stored_state.get("dataset_return")
        ):
            page_state["dataset_return"] = dict(stored_state["dataset_return"])
        return_context = {
            "rxn-timing-open-events-btn": ("reactions", "返回时间分布"),
        }.get(triggered_string_id)
        if page_id == "events" and return_context:
            page_state.update(
                return_page=return_context[0],
                return_label=return_context[1],
            )
        elif triggered_string_id == "event-participant-handoff":
            page_state.update(return_page=stored_page,
                              return_label=f"返回{PAGE_LABELS.get(stored_page, '事件列表')}")
        elif triggered_string_id in {"event-extract-btn", "event-detail-expand-btn"}:
            page_state["return_page"] = stored_page
            page_state["return_label"] = f"返回{PAGE_LABELS.get(stored_page, '事件列表')}"
        elif (
            triggered_string_id == "trajectory-back-events-btn"
            and page_id == "events"
        ):
            for key in ("return_page", "return_label"):
                if stored_state.get(key):
                    page_state[key] = stored_state[key]
        focus_request: Any = no_update
        if triggered_string_id in {"data-pick-btn", "library-add-more"}:
            focus_request = {
                "token": f"picker-{time.time_ns()}",
                "target": "data-browser-title",
            }
        elif triggered_string_id == "nav-data-management":
            focus_request = {"token": f"library-{time.time_ns()}", "target": "library-add-more"}
        elif triggered_string_id in {
            "open-data-modal",
            "page-capability-manage-btn",
            "species-open-data-modal",
        }:
            required_capability = PAGE_CAPABILITY_REQUIREMENTS.get(
                str(stored_page or ""),
                "",
            )
            preparation_target = {
                "event_search": "data-prep-event-btn",
                "species_fate": "data-prep-event-btn",
                "trajectory_evidence": "data-prep-trajectory-btn",
                "element_distribution": "data-prep-composition-btn",
                "species_abundance": "data-prep-composition-btn",
            }.get(required_capability, "data-prep-basic-status")
            focus_request = {
                "token": f"data-workspace-{time.time_ns()}",
                "target": (
                    preparation_target
                    if triggered_string_id == "page-capability-manage-btn"
                    else "library-management-panel"
                ),
            }
        elif triggered_string_id == "dir-browser-cancel-btn":
            focus_request = {
                "token": f"cancel-{time.time_ns()}",
                "target": str(
                    ((stored_state.get("dataset_return") or {}).get("trigger"))
                    or "library-management-panel"
                ),
            }
        elif triggered_string_id == "dataset-switch-navigation":
            focus_request = {
                "token": str((switch_navigation or {}).get("request_id") or time.time_ns()),
                "target": (
                    "page-title"
                    if page_id != "data-management"
                    else "library-management-panel"
                ),
                "expected_text": (
                    PAGE_LABELS[page_id] if page_id != "data-management" else ""
                ),
            }
        elif triggered_string_id == "event-participant-handoff":
            focus_request = {
                "token": f"candidate-lineage-{time.time_ns()}",
                "target": "lx-card",
            }
        return (
            tuple(page_classes[pid] for pid in PAGE_IDS)
            + tuple(nav_classes[pid] for pid in TOP_NAV_PAGE_IDS)
            + (
                (
                    "rs-top-nav-item active"
                    if active_workspace == "data-management"
                    else "rs-top-nav-item"
                ),
                (
                    "rs-top-nav-item rs-nav-utility active"
                    if page_id == "evolution"
                    else "rs-top-nav-item rs-nav-utility"
                ),
            )
            + tuple(
                "page" if pid == active_workspace else "false"
                for pid in TOP_NAV_PAGE_IDS
            )
            + (
                "page" if active_workspace == "data-management" else "false",
                "page" if page_id == "evolution" else "false",
            )
            + (
                page_state,
                PAGE_LABELS[active_workspace],
                PAGE_SECTIONS[active_workspace],
                PAGE_DESCRIPTIONS[active_workspace],
                PAGE_LABELS[active_workspace],
                (
                    "rs-page-header is-title-only"
                    if page_id == "reactions"
                    else "rs-page-header"
                ),
                "rs-body rs-tool-shell",
                focus_request,
            )
        )

    @app.callback(
        *(
            Output(
                f"page-{page_id}",
                "className",
                allow_duplicate=True,
            )
            for page_id in PAGE_IDS
        ),
        *(
            Output(
                f"nav-{page_id}",
                "className",
                allow_duplicate=True,
            )
            for page_id in TOP_NAV_PAGE_IDS
        ),
        Output(
            "nav-data-management",
            "className",
            allow_duplicate=True,
        ),
        Output(
            "data-open-batch-compare-btn",
            "className",
            allow_duplicate=True,
        ),
        *(
            Output(
                f"nav-{page_id}",
                "aria-current",
                allow_duplicate=True,
            )
            for page_id in TOP_NAV_PAGE_IDS
        ),
        Output(
            "nav-data-management",
            "aria-current",
            allow_duplicate=True,
        ),
        Output(
            "data-open-batch-compare-btn",
            "aria-current",
            allow_duplicate=True,
        ),
        Output("page-title", "children", allow_duplicate=True),
        Output("page-eyebrow-section", "children", allow_duplicate=True),
        Output("page-description", "children", allow_duplicate=True),
        Output("topbar-page-context", "children", allow_duplicate=True),
        Output("page-header", "className", allow_duplicate=True),
        Output("app-body", "className", allow_duplicate=True),
        Input("page-store", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def _sync_page_after_restore(page_store):
        """Keep visible page chrome aligned when session storage restores page-store."""
        state = page_store if isinstance(page_store, dict) else {}
        state = migrate_page_state(state)
        page_id = str(state["page"])
        active_workspace = str(state.get("entry") or PAGE_WORKSPACES.get(page_id, DEFAULT_PAGE))
        page_classes = tuple(
            (
                f"{PAGE_CLASS_NAMES.get(pid, 'rs-page')} active"
                if pid == page_id
                else PAGE_CLASS_NAMES.get(pid, "rs-page")
            )
            for pid in PAGE_IDS
        )
        nav_classes = tuple(
            f"rs-top-nav-item{' active' if pid == active_workspace else ''}"
            for pid in TOP_NAV_PAGE_IDS
        )
        return (
            *page_classes,
            *nav_classes,
            (
                "rs-top-nav-item active"
                if active_workspace == "data-management"
                else "rs-top-nav-item"
            ),
            (
                "rs-top-nav-item rs-nav-utility active"
                if page_id == "evolution"
                else "rs-top-nav-item rs-nav-utility"
            ),
            *(
                "page" if pid == active_workspace else "false"
                for pid in TOP_NAV_PAGE_IDS
            ),
            *(
                "page" if active_workspace == "data-management" else "false",
                "page" if page_id == "evolution" else "false",
            ),
            PAGE_LABELS[active_workspace],
            PAGE_SECTIONS[active_workspace],
            PAGE_DESCRIPTIONS[active_workspace],
            PAGE_LABELS[active_workspace],
            (
                "rs-page-header is-title-only"
                if page_id == "reactions"
                else "rs-page-header"
            ),
            "rs-body rs-tool-shell",
        )

    app.clientside_callback(
        """function(command) {
            if (!command || !command.page) return window.dash_clientside.no_update;
            window.requestAnimationFrame(() => {
                window.reacnetScopeNavigation?.activatePage(command.page, command.entry);
            });
            return command.page + ':' + (command.entry || '');
        }""",
        Output("navigation-sync-token", "data"),
        Input("navigation-command", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("analysis-back-bar", "style"),
        Input("page-store", "data"),
    )
    def _analysis_back_visibility(page_store):
        state = migrate_page_state(page_store)
        return {} if state.get("entry") == "species" and state["page"] != "species" else {"display": "none"}

    @app.callback(
        Output("page-store", "data", allow_duplicate=True),
        Input("dataset-session-restore", "n_intervals"),
        State("page-store", "data"),
        State("event-bookmark-store", "data"),
        State("app-store", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def _migrate_restored_page(_tick, page_store, bookmark, dataset):
        migrated = migrate_page_state(page_store)
        if (isinstance(page_store, dict) and page_store.get("version") != 2
                and page_store.get("page") == "trajectory" and bookmark):
            store = dataset or {}
            try:
                svc.restore_event_bookmark(
                    store.get("artifacts") or {}, bookmark,
                    dataset_id=str(store.get("dataset_id") or ""),
                    source_revision=store.get("source_revision") or {},
                )
            except svc.ServiceError:
                pass
            else:
                migrated["page"] = "trajectory"
                migrated.setdefault("return_page", "events")
                migrated.setdefault("return_label", "返回事件")
        return migrated if migrated != (page_store or {}) else no_update

    @app.callback(
        Output("event-back-btn", "children"),
        Output("event-back-btn", "style"),
        Input("page-store", "data"),
    )
    def _render_event_back_button(page_store):
        state = page_store or {}
        if state.get("page") != "events" or not state.get("return_page"):
            return "返回", {"display": "none"}
        return f"← {state.get('return_label') or '返回'}", {}

    @app.callback(
        Output("page-data-status", "children"),
        Output("page-data-status", "className"),
        Input("page-store", "data"),
        Input("app-store", "data"),
    )
    def _update_page_data_status(page_store, app_store):
        page_id = resolve_page_id((page_store or {}).get("page"))
        inputs_pending = bool((app_store or {}).get("inputs_pending"))

        def ready_status(message: str) -> tuple[str, str]:
            if inputs_pending:
                message = f"{message} · 请重新查询"
            return message, "rs-page-status is-ready"

        if page_id == "data-management":
            label = str((app_store or {}).get("label") or "").strip()
            if (app_store or {}).get("context_state") == "revision-changed":
                return (
                    f"当前RNG 数据：{label} · 源修订已变化",
                    "rs-page-status is-blocked",
                )
            if label:
                return ready_status(f"当前RNG 数据：{label}")
            return "尚未加载RNG 数据", "rs-page-status is-independent"
        if page_id == "reaction-compare" or _evolution_comparison_active(page_store):
            return "可直接选择多个来源进行对比", "rs-page-status is-independent"
        capability_key = PAGE_CAPABILITY_REQUIREMENTS.get(page_id, "")
        if not capability_key:
            return "此页面不依赖当前RNG 数据", "rs-page-status is-independent"
        label = _CAPABILITY_LABELS[capability_key]
        if not (app_store or {}).get("dataset_id"):
            return (
                (
                    "单来源物种检索需选择当前RNG 数据"
                    if page_id == "species"
                    else f"{label}：需要先选择当前RNG 数据"
                ),
                "rs-page-status is-blocked",
            )
        evidence = dict(
            _capabilities_from_store(app_store).get(capability_key) or {}
        )
        state = _capability_state_class(evidence.get("state"))
        reason = str(evidence.get("reason") or "状态原因暂不可用。")
        state_label = _CAPABILITY_STATE_LABELS.get(state, state)
        if state == "ready":
            return ready_status("当前查询可用")
        return (
            f"{label}：{state_label} · {reason}",
            "rs-page-status is-blocked",
        )

    @app.callback(
        Output("rxn-search-btn", "disabled"),
        Output("evolution-search-btn", "disabled"),
        Output("element-distribution-search-btn", "disabled"),
        Output("event-rxn-btn", "disabled"),
        Output("trajectory-refresh-btn", "disabled"),
        Input("app-store", "data"),
        Input({"type": "dataset-bound-operation", "name": "reactions"}, "data"),
    )
    def _update_data_dependent_actions(app_store, reactions_running):
        capabilities = _capabilities_from_store(app_store)

        def blocked(key: str) -> bool:
            return str((capabilities.get(key) or {}).get("state") or "") != "ready"

        no_reaction = blocked("reaction_search")
        no_species = blocked("species_abundance")
        no_reaction_events = blocked("event_search")
        no_trajectory = blocked("trajectory_evidence")
        return (
            no_reaction or bool(reactions_running),
            no_species,
            blocked("element_distribution"),
            no_reaction_events,
            no_trajectory,
        )

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Input("species-search-btn", "n_clicks"),
        Input("rxn-search-btn", "n_clicks"),
        Input("evolution-search-btn", "n_clicks"),
        Input("element-distribution-search-btn", "n_clicks"),
        Input("event-rxn-btn", "n_clicks"),
        Input("trajectory-refresh-btn", "n_clicks"),
        State("app-store", "data"),
        State("dataset-session-store", "data"),
        prevent_initial_call=True,
    )
    def _mark_preserved_inputs_executed(*args):
        current = args[-2] if len(args) >= 2 else {}
        session = args[-1] if args else {}
        if not isinstance(current, dict) or not current.get("inputs_pending"):
            raise PreventUpdate
        executed = {**current, "inputs_pending": False}
        persisted = (
            {**session, "inputs_pending": False}
            if isinstance(session, dict) and session.get("dataset_id") == current.get("dataset_id")
            else executed
        )
        return executed, persisted

    @app.callback(
        Output("dataset-focus-request", "data", allow_duplicate=True),
        Input("dataset-switch-transaction", "data"),
        prevent_initial_call=True,
    )
    def _focus_dataset_validation_failure(transaction):
        if (transaction or {}).get("state") != "failed":
            raise PreventUpdate
        candidate = (transaction or {}).get("candidate") or {}
        origin = (transaction or {}).get("origin") or {}
        target = (
            json.dumps(
                {"base": candidate["base"], "type": "library-switch-feedback"},
                sort_keys=True,
                separators=(",", ":"),
            )
            if origin.get("library") and candidate.get("base")
            else "data-load-feedback"
        )
        return {
            "token": f"validation-failed-{time.time_ns()}",
            "target": target,
        }

    @app.callback(
        Output("global-dataset-notice-timeout", "disabled"),
        Input("global-dataset-notice", "children"),
        prevent_initial_call=True,
    )
    def _arm_global_dataset_notice_timeout(children):
        if not children:
            raise PreventUpdate
        return False

    @app.callback(
        Output("global-dataset-notice", "children", allow_duplicate=True),
        Output(
            "global-dataset-notice-timeout",
            "disabled",
            allow_duplicate=True,
        ),
        Input("global-dataset-notice-timeout", "n_intervals"),
        prevent_initial_call=True,
    )
    def _expire_global_dataset_notice(n_intervals):
        if not n_intervals:
            raise PreventUpdate
        return "", True

    @app.callback(
        Output("topbar-folder", "children", allow_duplicate=True),
        Output("topbar-rungroup", "children", allow_duplicate=True),
        Output("topbar-status", "children", allow_duplicate=True),
        Output("topbar-status", "className", allow_duplicate=True),
        Input("app-store", "data"),
        prevent_initial_call=True,
    )
    def _render_current_dataset_topbar(app_store):
        current = app_store if isinstance(app_store, dict) else {}
        label = str(current.get("label") or "未选择")
        if not current.get("dataset_id"):
            return "未选择", "未选择", "未选择数据", "rs-badge rs-bad"
        if current.get("context_state") == "revision-changed":
            affected = "、".join(current.get("invalidated_artifacts") or [])
            status = (
                f"源修订已变化 · {affected} 待采用 · 其余能力仍可用"
                if affected
                else "源修订已变化 · 请采用新修订"
            )
            return label, label, status, "rs-badge rs-bad"
        return (
            label,
            label,
            "分析功能按项显示",
            "rs-badge",
        )
