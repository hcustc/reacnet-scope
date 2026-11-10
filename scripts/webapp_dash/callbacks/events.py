"""Event evidence search, selection, detail and trajectory viewer callbacks."""

from __future__ import annotations

import time
from typing import Any
import dash_bootstrap_components as dbc
from dash import (
    ALL,
    ClientsideFunction,
    Input,
    Output,
    State,
    ctx,
    dcc,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from scripts.webapp_dash.file_import import defer_query
from reacnet_scope import services as svc
from scripts.webapp_dash.candidate_workbench import actual_event_view, context_key, event_participants_view
from scripts.webapp_dash.navigation import (
    migrate_page_state,
)

from .common import (
    _empty_plotly_figure,
    _event_columns,
    _event_table_rows,
)

def _int_with_default(value: Any, default: int) -> int:
    """Coerce an optional integer while preserving zero as an explicit value."""

    return int(default if value is None or value == "" else value)

_ELEMENT_SYMBOLS = (
    "H",
    "C",
    "N",
    "O",
    "S",
    "P",
    "F",
    "Cl",
    "Br",
    "I",
    "Si",
    "He",
    "Li",
    "Be",
    "B",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Kr",
    "Rb",
    "Sr",
    "Y",
    "Zr",
    "Nb",
    "Mo",
    "Tc",
    "Ru",
    "Rh",
    "Pd",
    "Ag",
    "Cd",
    "In",
    "Sn",
    "Sb",
    "Te",
    "Xe",
    "Cs",
    "Ba",
    "La",
    "Ce",
    "Pr",
    "Nd",
    "Pm",
    "Sm",
    "Eu",
    "Gd",
    "Tb",
    "Dy",
    "Ho",
    "Er",
    "Tm",
    "Yb",
    "Lu",
    "Hf",
    "Ta",
    "W",
    "Re",
    "Os",
    "Ir",
    "Pt",
    "Au",
    "Hg",
    "Tl",
    "Pb",
    "Bi",
    "Po",
    "At",
    "Rn",
    "Fr",
    "Ra",
    "Ac",
    "Th",
    "Pa",
    "U",
    "Np",
    "Pu",
    "Am",
    "Cm",
    "Bk",
    "Cf",
    "Es",
    "Fm",
    "Md",
    "No",
    "Lr",
    "Rf",
    "Db",
    "Sg",
    "Bh",
    "Hs",
    "Mt",
    "Ds",
    "Rg",
    "Cn",
    "Nh",
    "Fl",
    "Mc",
    "Lv",
    "Ts",
    "Og",
)
_ELEMENT_OPTIONS = [
    {"label": symbol, "value": symbol} for symbol in _ELEMENT_SYMBOLS
]


def _atom_type_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def _event_viewer_type_rows(
    viewer: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Summarize types at the displayed anchor frame for the map editor."""
    if not viewer:
        return []
    frames = viewer.get("frames") or []
    if not frames:
        return []
    anchor = (viewer.get("meta") or {}).get("anchor_frame")
    frame = next(
        (
            item
            for item in frames
            if anchor is not None
            and int(item.get("frame")) == int(anchor)
        ),
        frames[0],
    )
    counts: dict[str, int] = {}
    for atom in frame.get("atoms") or []:
        atom_type = str(atom.get("type") or "").strip()
        if atom_type:
            counts[atom_type] = counts.get(atom_type, 0) + 1
    current = {
        str(atom_type): str(element)
        for atom_type, element in (
            (viewer.get("meta") or {}).get("type_element_map") or {}
        ).items()
        if str(atom_type).strip()
    }
    return [
        {
            "atom_type": atom_type,
            "count": counts.get(atom_type, 0),
            "element": current.get(atom_type),
        }
        for atom_type in sorted(
            set(counts).union(current),
            key=_atom_type_sort_key,
        )
    ]


def _event_type_map_from_controls(
    values: list[Any] | None,
    component_ids: list[Any] | None,
) -> dict[str, str]:
    """Collect the dynamic per-type dropdowns into a validated map payload."""
    mapping: dict[str, str] = {}
    for component_id, value in zip(component_ids or [], values or []):
        if not isinstance(component_id, dict):
            continue
        atom_type = str(component_id.get("atom_type") or "").strip()
        element = str(value or "").strip()
        if atom_type and element:
            mapping[atom_type] = element
    return dict(sorted(mapping.items(), key=lambda item: _atom_type_sort_key(item[0])))


def _event_frame_figure(viewer: dict[str, Any], frame_index: int, scope: str, *, compact: bool = False):
    """Render the compatibility Plotly view using PBC-centered coordinates."""
    import plotly.graph_objects as go

    frames = viewer.get("frames") or []
    if not frames:
        return go.Figure()
    safe_index = max(0, min(int(frame_index or 0), len(frames) - 1))
    frame = frames[safe_index]
    atoms = list(frame.get("atoms") or [])
    groups = viewer.get("atom_groups") or {}
    core_ids = {int(value) for value in (groups.get("core") or [])}
    participant_values = groups.get("participants")
    if participant_values is None:
        participant_values = [*(groups.get("reactant") or []), *(groups.get("product") or [])]
    participant_ids = {
        int(value)
        for value in participant_values
    }
    if scope == "core":
        atoms = [atom for atom in atoms if atom.get("id") is not None and int(atom["id"]) in core_ids]
    elif scope == "participants":
        atoms = [atom for atom in atoms if atom.get("id") is not None and int(atom["id"]) in participant_ids]
    if not atoms:
        return _empty_plotly_figure("当前显示范围没有可渲染原子")

    def coordinate(atom: dict[str, Any], axis: str) -> Any:
        return atom.get(f"display_{axis}", atom.get(axis))

    fig = go.Figure()
    atoms_by_id = {int(atom.get("id")): atom for atom in atoms if atom.get("id") is not None}
    evidence = viewer.get("bond_evidence") or {}
    broken = set(evidence.get("broken") or [])
    formed = set(evidence.get("formed") or [])
    shown_bonds = list(frame.get("bonds") or [])
    guides = [] if frame.get("bond_state") != "intermediate" else [*broken, *formed]
    for bond in [*shown_bonds, *guides]:
        parts = str(bond).split("-")
        if len(parts) < 2:
            continue
        try:
            left, right = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if left not in atoms_by_id or right not in atoms_by_id:
            continue
        if bond in broken:
            color, label = "#dc2626", "断裂键"
        elif bond in formed:
            color, label = "#16a34a", "形成键"
        else:
            color, label = "#64748b", "保持键"
        a, b = atoms_by_id[left], atoms_by_id[right]
        fig.add_trace(go.Scatter3d(x=[coordinate(a, "x"), coordinate(b, "x")], y=[coordinate(a, "y"), coordinate(b, "y")], z=[coordinate(a, "z"), coordinate(b, "z")], mode="lines", name=label, line={"color": color, "width": 4 if not compact else 2}, hoverinfo="skip", showlegend=not compact))
    symbols = [atom.get("label") or atom.get("element") or f"T{atom.get('type') or '?'}" for atom in atoms]
    colors = [
        "#1d4ed8"
        if int(atom.get("id") or -1) in core_ids
        else (
            "#7c3aed"
            if int(atom.get("id") or -1) in participant_ids
            else "#94a3b8"
        )
        for atom in atoms
    ]
    fig.add_trace(
        go.Scatter3d(
            x=[coordinate(atom, "x") for atom in atoms], y=[coordinate(atom, "y") for atom in atoms], z=[coordinate(atom, "z") for atom in atoms],
            mode="markers", name="反应核" if scope == "core" else ("参与原子" if scope == "participants" else "局部上下文"),
            marker={"size": 5 if compact else 7, "color": colors, "opacity": 0.94, "line": {"color": "#ffffff", "width": 0.6}},
            text=[f"Atom {atom.get('id')} · {symbol}" for atom, symbol in zip(atoms, symbols)],
            hovertemplate="%{text}<br>x=%{x:.3f}, y=%{y:.3f}, z=%{z:.3f}<extra></extra>",
        )
    )

    title = f"Frame {frame.get('frame')}"
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "font": {"size": 13 if compact else 15}, "x": 0.01, "xanchor": "left"},
        height=220 if compact else 460,
        margin={"l": 0, "r": 0, "t": 30 if compact else 36, "b": 0},
        showlegend=not compact,
        legend={"orientation": "h", "y": -0.04, "x": 0},
        scene={
            "aspectmode": "data",
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
            "zaxis": {"visible": False},
            "bgcolor": "#fbfcfe",
            "camera": {"eye": {"x": 1.45, "y": 1.45, "z": 1.05}},
        },
    )
    return fig


def _event_selection_summary(selected: dict[str, Any]) -> Any:
    row = selected.get("row") or {}
    details = ["RNG 事件", f"{row.get('before_timestep', '-')} → {row.get('after_timestep', '-')}"]
    if row.get("event_id"):
        details.append(str(row["event_id"]))
    if row.get("association_status") != "matched":
        details.extend(("原子关联不确定", "轨迹不可用"))
    return html.Div(
        [
            html.Span("已选", className="rs-selection-label"),
            html.Span(" · ".join(details), className="rs-selection-main"),
            html.Span(str(row.get("reaction_smiles") or row.get("matched_smiles_at_anchor") or ""), className="rs-selection-query"),
        ],
        className="rs-selection-line",
    )


def register(app: Any) -> None:
    """Register events callbacks."""

    # ── Event evidence ──────────────────────────────────────────────

    @app.callback(
        Output("event-grid", "rowData", allow_duplicate=True),
        Output("event-grid", "columnDefs", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Output("import-pending-events", "data"),
        Input("event-rxn-btn", "n_clicks"),
        Input("import-auto-result", "data"),
        State("event-reaction-text", "value"),
        State("event-rxn-before", "value"),
        State("event-rxn-after", "value"),
        State("event-rxn-max", "value"),
        State("app-store", "data"),
        State("import-pending-events", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "events"}, "data"),
                True,
                False,
            ),
        ],
    )
    @defer_query("event", 4)
    def _locate_reaction_events(
        rxn_clicks,
        reaction_text,
        before,
        after,
        max_events,
        store,
    ):
        if rxn_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        config = {
            "reaction_text": reaction_text or "",
            "before_frames": _int_with_default(before, 3),
            "after_frames": _int_with_default(after, 3),
            "max_events": int(max_events or 100),
        }
        try:
            payload = svc.locate_rng_events(
                artifacts,
                config["reaction_text"],
                max_events=config["max_events"],
            )
        except svc.ServiceError as exc:
            empty = {"rows": [], "kind": "rng_event", "config": config}
            return [], _event_columns(), str(exc.message), empty
        rows = payload.get("rows") or []
        table_rows = _event_table_rows(rows)
        meta = payload.get("meta") or {}
        message = meta.get("message") or f"从 RNG 输出中找到 {len(rows)} 条事件"
        workflow = {
            "rows": rows,
            "meta": meta,
            "kind": "rng_event",
            "config": config,
        }
        return table_rows, _event_columns(table_rows), message, workflow

    @app.callback(
        Output("event-grid", "selectedRows"),
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-selection-card", "style", allow_duplicate=True),
        Output("event-viewer-store", "data", allow_duplicate=True),
        Output("event-viewer-card", "style", allow_duplicate=True),
        Output("trajectory-alert", "children", allow_duplicate=True),
        Input("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_event_workspace(_workflow):
        """A new RNG event query invalidates the former selection and viewer."""
        return (
            [],
            None,
            {"display": "none"},
            None,
            {"display": "none"},
            "请从“反应事件”页选择一条 RNG 事件；已有查看结果已清除。",
        )

    @app.callback(
        Output("event-selected-store", "data"),
        Output("event-extract-id", "value"),
        Output("event-selected-summary", "children"),
        Output("event-selection-card", "style"),
        Output("event-extract-btn", "disabled"),
        Output("event-extract-btn", "children"),
        Output("event-bookmark-store", "data"),
        Output("event-bookmark-status", "children"),
        Output("molecule-lineage-store", "data", allow_duplicate=True),
        Output("molecule-lineage-results", "style", allow_duplicate=True),
        Input("event-grid", "selectedRows"),
        State("event-grid-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _select_event(selected_row_ids, grid_store, app_store):
        if not selected_row_ids:
            raise PreventUpdate
        workflow = grid_store or {}
        kind = workflow.get("kind") or ""
        if kind != "rng_event":
            raise PreventUpdate
        event_id = str((selected_row_ids[0] or {}).get("id") or "")
        row = next(
            (
                item
                for item in (workflow.get("rows") or [])
                if str(item.get("event_id") or "") == event_id
            ),
            None,
        )
        if row is None:
            raise PreventUpdate
        selected = {
            "row": row,
            "kind": kind,
            "config": workflow.get("config") or {},
            "selection_token": str(time.time_ns()),
        }
        event_id = str(selected["row"].get("event_id") or "")
        association_ready = (
            row.get("association_status") == "matched"
            and bool(row.get("atom_id_list"))
        )
        trajectory_ready = bool(
            ((app_store or {}).get("artifacts") or {}).get("trajectory")
        )
        if not association_ready:
            extract_label = "该事件无法定位原子"
        elif not trajectory_ready:
            extract_label = "缺少轨迹文件"
        else:
            extract_label = "打开轨迹查看"
        config = selected.get("config") or {}
        try:
            bookmark = svc.create_event_bookmark(
                app_store or {},
                row,
                before_frames=_int_with_default(config.get("before_frames"), 3),
                after_frames=_int_with_default(config.get("after_frames"), 3),
            )
            bookmark_status = (
                "已保存稳定事件书签；再次使用时会核验RNG 数据身份、来源修订和事件 ID。"
            )
        except svc.ServiceError as exc:
            bookmark = None
            bookmark_status = str(exc.message)
        return (
            selected,
            event_id,
            _event_selection_summary(selected),
            {"display": "block"},
            not (association_ready and trajectory_ready),
            extract_label,
            bookmark,
            bookmark_status,
            None,
            {"display": "none"},
        )

    @app.callback(
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-bookmark-store", "data", allow_duplicate=True),
        Input("rxn-timing-event-grid", "selectedRows"),
        State("rxn-timing-page-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _select_reaction_timing_event(selected_rows, page, dataset):
        if not selected_rows or not page or page.get("dataset_id") != (dataset or {}).get("dataset_id"):
            raise PreventUpdate
        event_id = str((selected_rows[0] or {}).get("id") or "")
        row = next((item for item in page.get("rows") or []
                    if str(item.get("event_id") or "") == event_id), None)
        if not row:
            raise PreventUpdate
        selected = {"row": row, "kind": "rng_event",
                    "config": {"reaction_text": page.get("reaction_key") or "",
                               "before_frames": 3, "after_frames": 3},
                    "origin": {"kind": "reaction_timing", "reaction_key": page.get("reaction_key"),
                               "offset": page.get("offset")},
                    "selection_token": str(time.time_ns())}
        try:
            bookmark = svc.create_event_bookmark(dataset or {}, row)
        except svc.ServiceError:
            bookmark = None
        return selected, bookmark

    @app.callback(
        Output("event-detail-response", "data"),
        Input("event-selected-store", "data"),
        State("app-store", "data"),
        State("cp-report", "data"),
    )
    def _read_selected_event_detail(selected, dataset, report=None):
        row = (selected or {}).get("row") or {}
        event_id = str(row.get("event_id") or "")
        if not event_id:
            return None
        store = dataset or {}
        revision = store.get("source_revision") or {}
        fingerprint = str(revision.get("fingerprint") or "") if isinstance(revision, dict) else ""
        key = {"event_id": event_id, "dataset_id": str(store.get("dataset_id") or ""),
               "fingerprint": fingerprint}
        origin = (selected or {}).get("origin") or {}
        try:
            bookmark = svc.create_event_bookmark(store, row)
            restored = svc.restore_event_bookmark(
                store.get("artifacts") or {}, bookmark,
                dataset_id=key["dataset_id"], source_revision=revision,
            )
            row = restored["selection"]["row"]
            if origin.get("kind") == "candidate_step":
                if (not report or report.get("context") != context_key(store)
                        or origin.get("query_request_id") != report.get("query_request_id")):
                    return None
                evidence = svc.candidate_step_events(store.get("artifacts") or {}, report,
                    origin.get("signature_id"), origin.get("step_index"), origin.get("offset", 0))
                row = next((r for r in evidence.get("rows", []) if r.get("event_id") == event_id), None)
                if row is None:
                    return None
        except svc.ServiceError as exc:
            return {"key": key, "error": exc.message, "origin": origin}
        return {"key": key, "row": row, "origin": origin}

    app.clientside_callback(
        """function(selected) {
            return [null, {display: 'none'}, null, null, {display: 'none'}, null];
        }""",
        Output("event-viewer-store", "data", allow_duplicate=True),
        Output("event-viewer-card", "style", allow_duplicate=True),
        Output("event-dft-store", "data", allow_duplicate=True),
        Output("molecule-lineage-store", "data", allow_duplicate=True),
        Output("molecule-lineage-results", "style", allow_duplicate=True),
        Output("molecule-lineage-drilldown-store", "data", allow_duplicate=True),
        Input("event-selected-store", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """function(viewer, selected, drilldown) {
            const expected = [selected, drilldown].map(item =>
                String((((item || {}).row || {}).event_id) || ''));
            if (!viewer || !expected.includes(String(viewer.event_id || ''))) {
                return {display: 'none'};
            }
            return window.dash_clientside.no_update;
        }""",
        Output("event-viewer-card", "style", allow_duplicate=True),
        Input("event-viewer-store", "data"),
        Input("event-selected-store", "data"),
        Input("molecule-lineage-drilldown-store", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """function(response, selected, dataset, report, signature, step) {
            const row = (selected || {}).row || {};
            const revision = (dataset || {}).source_revision || {};
            const key = (response || {}).key || {};
            if (!row.event_id || key.event_id !== row.event_id ||
                key.dataset_id !== ((dataset || {}).dataset_id || '') ||
                key.fingerprint !== (revision.fingerprint || '')) return null;
            const origin = (selected || {}).origin || {};
            if (origin.kind === 'candidate_step') {
                const received = (response || {}).origin || {};
                if (origin.query_request_id !== (report || {}).query_request_id ||
                    origin.signature_id !== signature || origin.step_index !== step ||
                    received.query_request_id !== origin.query_request_id ||
                    received.signature_id !== signature || received.step_index !== step) return null;
            }
            return response;
        }""",
        Output("event-detail-state", "data"),
        Input("event-detail-response", "data"),
        Input("event-selected-store", "data"),
        Input("app-store", "data"),
        Input("cp-report", "data"), Input("cp-focus", "value"), Input("cp-step", "value"),
    )

    @app.callback(
        Output("event-detail-dismissed", "data"),
        Input("event-detail-close-btn", "n_clicks"),
        State("event-detail-state", "data"),
        prevent_initial_call=True,
    )
    def _close_event_detail(clicks, detail):
        if not clicks or not detail:
            raise PreventUpdate
        return detail.get("key")

    @app.callback(
        Output("event-detail-dismissed", "data", allow_duplicate=True),
        Input("event-selected-store", "data"),
        prevent_initial_call=True,
    )
    def _reopen_detail_for_selection(_selected):
        return None

    @app.callback(
        Output("event-detail-panel", "style"),
        Output("event-detail-body", "children"),
        Output("event-detail-expand-btn", "disabled"),
        Input("event-detail-state", "data"),
        Input("event-detail-dismissed", "data"),
        Input("page-store", "data"),
        Input("app-store", "data"),
    )
    def _render_event_detail(detail, dismissed, page, dataset):
        page_id = migrate_page_state(page)["page"]
        origin_kind = str(((detail or {}).get("origin") or {}).get("kind") or "")
        origin_page = "reactions" if origin_kind in {
            "candidate_step", "channel", "reaction_search", "reaction_timing",
        } else "events"
        store = dataset or {}
        revision = store.get("source_revision") or {}
        fingerprint = str(revision.get("fingerprint") or "") if isinstance(revision, dict) else ""
        key = (detail or {}).get("key") or {}
        if (not detail or page_id != origin_page or detail.get("key") == dismissed
                or key.get("dataset_id") != str(store.get("dataset_id") or "")
                or key.get("fingerprint") != fingerprint):
            return {"display": "none"}, [], True
        row = detail.get("row") or {}
        if detail.get("error"):
            return {}, dbc.Alert(f"事件证据不可用：{detail['error']}", color="warning"), True
        association_ready = row.get("association_status") == "matched" and bool(row.get("atom_id_list"))
        coordinate_ready = bool(((dataset or {}).get("artifacts") or {}).get("trajectory"))
        content = [
            html.Div([
                html.Span(f"事件 {row.get('event_id') or '-'}", className="rs-stat-chip"),
                html.Span(f"Transition {row.get('timestep_index', '-')}", className="rs-stat-chip"),
                html.Span(str(row.get("association_status") or "状态未知"), className="rs-stat-chip"),
            ], className="rs-stat-row"),
            html.Div(f"来源：{(dataset or {}).get('label') or '当前 RNG 数据'}"),
            actual_event_view(row),
            event_participants_view(row, context_key(store)),
        ]
        if not association_ready:
            content.append(dbc.Alert("事件保留反应统计与可用元数据，但参与分子尚未解析，不能定位轨迹。", color="warning"))
        elif not coordinate_ready:
            content.append(dbc.Alert("当前来源没有轨迹坐标；前后键结构仍可核查。", color="info"))
        return {}, content, not association_ready

    @app.callback(
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-participant-handoff", "data"),
        Input({"type": "event-track-participant", "event": ALL, "side": ALL,
               "index": ALL, "context": ALL}, "n_clicks"),
        State("event-detail-state", "data"), State("event-selected-store", "data"),
        State("app-store", "data"), State("cp-report", "data"), prevent_initial_call=True,
    )
    def _track_event_participant(clicks, detail, selected, dataset, report=None):
        trigger = ctx.triggered_id
        if (not any(clicks or []) or not isinstance(trigger, dict)
                or trigger.get("context") != context_key(dataset)
                or trigger.get("event") != ((detail or {}).get("key") or {}).get("event_id")
                or trigger.get("event") != (((selected or {}).get("row") or {}).get("event_id"))):
            raise PreventUpdate
        # Resolve participants again by stable event identity and current revision.
        current = _read_selected_event_detail(selected, dataset, report)
        if not current or not current.get("row"):
            raise PreventUpdate
        row = current["row"]
        side, index = trigger.get("side"), trigger.get("index")
        participants = row.get(f"{side}_participants") or []
        if (row.get("association_status") != "matched" or side not in {"reactant", "product"}
                or not isinstance(index, int) or not 0 <= index < len(participants)
                or not participants[index].get("atom_ids")):
            raise PreventUpdate
        origin = dict((selected or {}).get("origin") or {}, action="track-participant", participant=f"{side}:{index}")
        return dict(selected, row=row, origin=origin), {"token": str(time.time_ns()), "event_id": row["event_id"]}

    @app.callback(
        Output("trajectory-selection-summary", "children"),
        Output("trajectory-selection-structure", "children"),
        Output("trajectory-open-selected-btn", "disabled"),
        Output("trajectory-open-selected-btn", "children"),
        Output("trajectory-back-events-btn", "children"),
        Output("lx-card", "open"),
        Input("event-selected-store", "data"),
        Input("app-store", "data"),
        Input("page-store", "data"),
    )
    def _prepare_selected_instance_workspace(selected, app_store, page_store):
        row = (selected or {}).get("row") or {}
        origin = (selected or {}).get("origin") or {}
        return_label = (
            f"← {(page_store or {}).get('return_label')}"
            if (page_store or {}).get("return_label")
            else "← 返回候选路线"
            if (page_store or {}).get("candidate_direct_return")
            else "← 选择反应实例"
        )
        if not row:
            return (
                "尚未选择具体反应实例。",
                "",
                True,
                "载入当前实例的局部轨迹",
                return_label,
                False,
            )
        event_id = str(row.get("event_id") or "")
        association_ready = (
            row.get("association_status") == "matched"
            and bool(row.get("atom_id_list"))
        )
        trajectory_ready = bool(
            ((app_store or {}).get("artifacts") or {}).get("trajectory")
        )
        origin_text = (
            f"候选路线步骤 {int(origin.get('step_index', 0)) + 1}"
            if origin.get("kind") == "candidate_step"
            else "反应时间分布"
            if origin.get("kind") == "reaction_timing"
            else "事件检索"
        )
        summary = html.Div(
            [
                html.Span(f"实例 {event_id or '-'}", className="rs-stat-chip"),
                html.Span(origin_text, className="rs-stat-chip"),
                html.Span(
                    f"Transition {row.get('timestep_index', '-')}",
                    className="rs-stat-chip",
                ),
                html.Span(
                    str(row.get("association_status") or "状态未知"),
                    className="rs-stat-chip",
                ),
            ],
            className="rs-stat-row",
        )
        structure_detail = (
            html.Div(
                [
                    html.H6("该实例的 RNG 前后键结构"),
                    actual_event_view(row),
                ]
            )
            if origin.get("kind") == "candidate_step"
            and row.get("candidate_evidence")
            else ""
        )
        if not association_ready:
            return (
                summary,
                structure_detail,
                True,
                "该实例无法定位具体分子",
                return_label,
                False,
            )
        if not trajectory_ready:
            return (
                summary,
                structure_detail,
                True,
                "缺少轨迹坐标",
                return_label,
                origin.get("action") == "track-participant",
            )
        return (
            summary,
            structure_detail,
            False,
            "载入当前实例的局部轨迹",
            return_label,
            origin.get("action") == "track-participant",
        )

    @app.callback(
        Output("event-bookmark-validation-store", "data"),
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-extract-id", "value", allow_duplicate=True),
        Output("event-selected-summary", "children", allow_duplicate=True),
        Output("event-selection-card", "style", allow_duplicate=True),
        Output("event-extract-btn", "disabled", allow_duplicate=True),
        Output("event-extract-btn", "children", allow_duplicate=True),
        Output("event-bookmark-status", "children", allow_duplicate=True),
        Output("event-reaction-text", "value", allow_duplicate=True),
        Output("event-rxn-before", "value", allow_duplicate=True),
        Output("event-rxn-after", "value", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Input("app-store", "data"),
        Input("event-bookmark-store", "data"),
        Input("page-store", "data"),
        State("event-selected-store", "data"),
        prevent_initial_call=True,
    )
    def _restore_event_bookmark(app_store, bookmark, page_store, selected):
        page = str((page_store or {}).get("page") or "")
        if page not in {"events", "trajectory"} or not bookmark:
            raise PreventUpdate
        current_event_id = str(
            (((selected or {}).get("row") or {}).get("event_id")) or ""
        )
        bookmark_event_id = str(bookmark.get("event_id") or "")
        if current_event_id and current_event_id == bookmark_event_id:
            raise PreventUpdate

        store = app_store or {}
        try:
            restored = svc.restore_event_bookmark(
                store.get("artifacts") or {},
                bookmark,
                dataset_id=str(store.get("dataset_id") or ""),
                source_revision=store.get("source_revision") or {},
            )
        except svc.ServiceError as exc:
            validation = {
                "state": "rejected",
                "event_id": bookmark_event_id,
                "reason": str(exc.reason),
                "message": str(exc.message),
            }
            return (
                validation,
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                f"事件书签未恢复：{exc.message}",
                no_update,
                no_update,
                no_update,
                str(exc.message),
            )

        restored_selection = restored["selection"]
        restored_selection["selection_token"] = str(time.time_ns())
        row = restored_selection["row"]
        config = restored_selection["config"]
        association_ready = (
            row.get("association_status") == "matched"
            and bool(row.get("atom_id_list"))
        )
        trajectory_ready = bool((store.get("artifacts") or {}).get("trajectory"))
        if not association_ready:
            extract_label = "该事件无法定位原子"
        elif not trajectory_ready:
            extract_label = "缺少轨迹文件"
        else:
            extract_label = "打开轨迹查看"
        validation = {
            "state": "restored",
            "event_id": str(row.get("event_id") or ""),
            "dataset_id": str(store.get("dataset_id") or ""),
            "source_revision_fingerprint": str(
                (store.get("source_revision") or {}).get("fingerprint") or ""
            ),
        }
        return (
            validation,
            restored_selection,
            str(row.get("event_id") or ""),
            _event_selection_summary(restored_selection),
            {"display": "block"},
            not (association_ready and trajectory_ready),
            extract_label,
            restored["message"],
            str(config.get("reaction_text") or ""),
            int(config.get("before_frames", 3)),
            int(config.get("after_frames", 3)),
            restored["message"],
        )

    @app.callback(
        Output("event-type-map-editor", "children"),
        Input("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _render_event_type_map_editor(viewer):
        if not viewer:
            return html.Div(
                "打开一条事件轨迹后自动检测 Type。",
                className="rs-type-map-empty",
            )
        rows = _event_viewer_type_rows(viewer)
        meta = viewer.get("meta") or {}
        notices = []
        if meta.get("native_element_column"):
            notices.append(
                html.Div(
                    "轨迹自带 element 列：原始元素优先，下方映射仅作缺失值回退。",
                    className="rs-type-map-native-note",
                )
            )
        if not rows:
            return [
                *notices,
                html.Div(
                    "当前局部轨迹没有可映射的 type 列。",
                    className="rs-type-map-empty",
                ),
            ]
        cards = []
        for row in rows:
            atom_type = str(row["atom_type"])
            count = int(row["count"])
            cards.append(
                html.Div(
                    [
                        html.Div(
                            [
                                html.Code(
                                    f"Type {atom_type}",
                                    className="rs-type-map-type",
                                ),
                                html.Span(
                                    f"{count} 原子"
                                    if count
                                    else "当前窗口未出现",
                                    className="rs-type-map-count",
                                ),
                            ],
                            className="rs-type-map-card-heading",
                        ),
                        dcc.Dropdown(
                            id={
                                "type": "event-type-element-select",
                                "atom_type": atom_type,
                            },
                            options=_ELEMENT_OPTIONS,
                            value=row.get("element") or None,
                            clearable=True,
                            searchable=True,
                            placeholder=f"选择 Type {atom_type} 的元素",
                            className="rs-type-map-select",
                        ),
                    ],
                    className="rs-type-map-card",
                )
            )
        return [
            *notices,
            html.Div(cards, className="rs-type-map-grid"),
        ]

    @app.callback(
        Output("event-type-map-status", "children"),
        Output("event-type-map-status", "className"),
        Output("event-type-map-clear-btn", "disabled"),
        Input("event-viewer-store", "data"),
        Input(
            {
                "type": "event-type-element-select",
                "atom_type": ALL,
            },
            "value",
        ),
        State(
            {
                "type": "event-type-element-select",
                "atom_type": ALL,
            },
            "id",
        ),
    )
    def _summarize_event_type_map(viewer, values, component_ids):
        if not viewer:
            return "尚未检测", "rs-type-map-status", True
        rows = _event_viewer_type_rows(viewer)
        if not rows:
            native = bool((viewer.get("meta") or {}).get("native_element_column"))
            return (
                "轨迹已提供 element 列" if native else "未检测到 Type",
                "rs-type-map-status is-native" if native else "rs-type-map-status",
                True,
            )
        row_types = {str(row["atom_type"]) for row in rows}
        control_types = {
            str(component_id.get("atom_type") or "")
            for component_id in (component_ids or [])
            if isinstance(component_id, dict)
        }
        if control_types == row_types:
            mapping = _event_type_map_from_controls(values, component_ids)
        else:
            mapping = {
                str(row["atom_type"]): str(row["element"])
                for row in rows
                if row.get("element")
            }
        detected_count = sum(1 for row in rows if int(row["count"]) > 0)
        mapped_count = sum(
            1 for row in rows if str(row["atom_type"]) in mapping
        )
        message = (
            f"当前窗口 {detected_count} 种 Type · "
            f"已映射 {mapped_count}/{len(rows)}"
        )
        hidden_count = len(rows) - detected_count
        if hidden_count:
            message += f" · {hidden_count} 项已保存映射未在当前窗口出现"
        class_name = "rs-type-map-status"
        if mapped_count == len(rows):
            class_name += " is-complete"
        elif mapped_count:
            class_name += " is-partial"
        saved_mapping = (viewer.get("meta") or {}).get("type_element_map") or {}
        return message, class_name, not bool(mapping or saved_mapping)

    @app.callback(
        Output("event-viewer-response", "data"),
        Input("event-extract-btn", "n_clicks"),
        Input("trajectory-open-selected-btn", "n_clicks"),
        Input("trajectory-refresh-btn", "n_clicks"),
        Input("event-type-map-clear-btn", "n_clicks"),
        Input("molecule-lineage-drilldown-store", "data"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        State(
            {
                "type": "event-type-element-select",
                "atom_type": ALL,
            },
            "value",
        ),
        State(
            {
                "type": "event-type-element-select",
                "atom_type": ALL,
            },
            "id",
        ),
        State("event-environment-radius", "value"),
        prevent_initial_call=True,
        running=[
            (
                Output(
                    {"type": "dataset-bound-operation", "name": "trajectory"},
                    "data",
                ),
                True,
                False,
            ),
        ],
    )
    def _extract_selected_event(
        _open_clicks,
        _open_selected_clicks,
        _refresh_clicks,
        _clear_clicks,
        lineage_drilldown,
        selected,
        store,
        type_element_values,
        type_element_ids,
        environment_radius,
    ):
        if ctx.triggered_id not in {
            "event-extract-btn",
            "trajectory-open-selected-btn",
            "trajectory-refresh-btn",
            "event-type-map-clear-btn",
            "molecule-lineage-drilldown-store",
        }:
            raise PreventUpdate
        if ctx.triggered_id == "molecule-lineage-drilldown-store" and not lineage_drilldown:
            raise PreventUpdate
        original_selected = selected or {}
        store = store or {}
        revision = store.get("source_revision") or {}
        response_key = {
            "trigger": ctx.triggered_id,
            "selected_event_id": str(((original_selected.get("row") or {}).get("event_id")) or ""),
            "selected_token": original_selected.get("selection_token"),
            "drilldown_event_id": str((((lineage_drilldown or {}).get("row") or {}).get("event_id")) or ""),
            "drilldown_token": (lineage_drilldown or {}).get("selection_token"),
            "dataset_id": str(store.get("dataset_id") or ""),
            "fingerprint": str(revision.get("fingerprint") or "") if isinstance(revision, dict) else "",
            "clicks": [int(value or 0) for value in
                       (_open_clicks, _open_selected_clicks, _refresh_clicks, _clear_clicks)],
        }
        if ctx.triggered_id == "molecule-lineage-drilldown-store":
            selected = lineage_drilldown or {}
        selected = selected or {}
        row = selected.get("row") or {}
        config = selected.get("config") or {}
        kind = selected.get("kind") or ""
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            if kind == "rng_event":
                if ctx.triggered_id in {
                    "event-extract-btn",
                    "trajectory-open-selected-btn",
                }:
                    parsed_type_map = None
                elif ctx.triggered_id == "event-type-map-clear-btn":
                    parsed_type_map = {}
                else:
                    parsed_type_map = _event_type_map_from_controls(
                        type_element_values,
                        type_element_ids,
                    )
                viewer = svc.build_rng_event_visualization(
                    artifacts,
                    row,
                    before_frames=_int_with_default(config.get("before_frames"), 3),
                    after_frames=_int_with_default(config.get("after_frames"), 3),
                    environment_radius=float(
                        4.0
                        if environment_radius is None
                        else environment_radius
                    ),
                    atom_type_map=parsed_type_map,
                )
            else:
                raise svc.ServiceError("请先从定位结果中选择一个事件", reason="missing_selection")
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return {"key": response_key, "values": [
                None, {"display": "none"}, [], [], "", "", 0, 0, 0, {}, [], message,
            ]}

        frames = viewer.get("frames") or []
        anchor = row.get("anchor_frame")
        anchor_index = next((idx for idx, item in enumerate(frames) if int(item.get("frame")) == int(anchor)), 0) if anchor is not None else 0
        marks = {idx: str(item.get("frame")) for idx, item in enumerate(frames)}
        storyboard = []
        for frame_number in viewer.get("storyboard_frames") or []:
            idx = next((i for i, item in enumerate(frames) if int(item.get("frame")) == int(frame_number)), None)
            if idx is None:
                continue
            label = (viewer.get("storyboard_labels") or {}).get(str(frame_number), f"Frame {frame_number}")
            storyboard.append(
                html.Div(
                    [html.Div(label, className="rs-storyboard-label"), dcc.Graph(figure=_event_frame_figure(viewer, idx, "context", compact=True), config={"displayModeBar": False})],
                    className="rs-storyboard-item",
                )
            )
        meta = viewer.get("meta") or {}
        environment = meta.get("environment") or {}
        paths = viewer.get("paths") or {}
        environment_label = (
            f"环境 {int(environment.get('selected_environment_count') or 0)} 原子"
        )
        if environment.get("truncated"):
            environment_label += (
                f" / {int(environment.get('raw_environment_count') or 0)}（已截断）"
            )
        summary = html.Div(
            [
                html.Span(
                    f"事件 {str(row.get('event_id') or '-')}",
                    className="rs-stat-chip",
                ),
                html.Span(
                    f"{str(row.get('reactant') or '?')} → {str(row.get('product') or '?')}",
                    className="rs-stat-chip",
                ),
                html.Span(f"{len(frames)} 帧", className="rs-stat-chip"),
                html.Span(f"反应核 {len((viewer.get('atom_groups') or {}).get('core') or [])} 原子", className="rs-stat-chip"),
                html.Span(f"局部上下文 {len((viewer.get('atom_groups') or {}).get('context') or [])} 原子", className="rs-stat-chip"),
                html.Span(environment_label, className="rs-stat-chip"),
                html.Span(str(meta.get("verification_status") or meta.get("status") or "已提取"), className="rs-stat-chip"),
            ],
            className="rs-stat-row",
        )
        path_items = [f"轨迹: {paths.get('trajectory') or '-'}"]
        if paths.get("type_map"):
            path_items.append(f"类型映射: {paths['type_map']}")
        atom_ids_text = " ".join(
            str(value) for value in svc.event_viewer_atom_ids(viewer)
        )
        ovito_expression = svc.event_viewer_ovito_expression(viewer)
        return {"key": response_key, "values": [
            viewer, {"display": "block"}, summary, " · ".join(path_items),
            atom_ids_text, ovito_expression, 0, len(frames) - 1,
            anchor_index, marks, storyboard,
            "局部轨迹已按 PBC 重定位；3Dmol.js 用于快速查看，原始坐标可下载到 OVITO 复核。",
        ]}

    app.clientside_callback(
        """function(response, selected, drilldown, dataset, openClicks,
                    selectedClicks, refreshClicks, clearClicks) {
            const none = window.dash_clientside.no_update;
            const discard = () => Array(12).fill(none);
            if (!response || !Array.isArray(response.values) || response.values.length !== 12) {
                return discard();
            }
            const key = response.key || {};
            const selectedId = String((((selected || {}).row || {}).event_id) || '');
            const drilldownId = String((((drilldown || {}).row || {}).event_id) || '');
            const revision = (dataset || {}).source_revision || {};
            const clicks = [openClicks, selectedClicks, refreshClicks, clearClicks]
                .map(value => Number(value || 0));
            if (key.selected_event_id !== selectedId ||
                key.selected_token !== ((selected || {}).selection_token ?? null) ||
                key.drilldown_event_id !== drilldownId ||
                key.drilldown_token !== ((drilldown || {}).selection_token ?? null) ||
                key.dataset_id !== String((dataset || {}).dataset_id || '') ||
                key.fingerprint !== String(revision.fingerprint || '') ||
                JSON.stringify(key.clicks) !== JSON.stringify(clicks)) {
                return discard();
            }
            return response.values;
        }""",
        Output("event-viewer-store", "data"),
        Output("event-viewer-card", "style"),
        Output("event-viewer-summary", "children"),
        Output("event-viewer-paths", "children"),
        Output("event-atom-ids-text", "children"),
        Output("event-ovito-expression-text", "children"),
        Output("event-frame-slider", "min"),
        Output("event-frame-slider", "max"),
        Output("event-frame-slider", "value"),
        Output("event-frame-slider", "marks"),
        Output("event-storyboard", "children"),
        Output("trajectory-alert", "children"),
        Input("event-viewer-response", "data"),
        Input("event-selected-store", "data"),
        Input("molecule-lineage-drilldown-store", "data"),
        Input("app-store", "data"),
        Input("event-extract-btn", "n_clicks"),
        Input("trajectory-open-selected-btn", "n_clicks"),
        Input("trajectory-refresh-btn", "n_clicks"),
        Input("event-type-map-clear-btn", "n_clicks"),
    )

    app.clientside_callback(
        ClientsideFunction(
            namespace="reacnetScope",
            function_name="renderEventTrajectory",
        ),
        Output("event-3dmol-status", "children"),
        Input("event-frame-slider", "value"),
        Input("event-view-scope", "value"),
        Input("event-viewer-store", "data"),
        Input("event-core-label-toggle", "value"),
    )

    @app.callback(
        Output("event-trajectory-3d", "figure"),
        Output("event-frame-label", "children"),
        Input("event-frame-slider", "value"),
        Input("event-view-scope", "value"),
        Input("event-viewer-store", "data"),
    )
    def _render_event_frame(frame_index, scope, viewer):
        if not viewer or not (viewer.get("frames") or []):
            from plotly.graph_objects import Figure

            return Figure(), ""
        frames = viewer.get("frames") or []
        safe_index = max(0, min(int(frame_index or 0), len(frames) - 1))
        frame = frames[safe_index]
        return _event_frame_figure(viewer, safe_index, scope or "context"), f"Frame {frame.get('frame')} · {len(frame.get('atoms') or [])} atoms"
