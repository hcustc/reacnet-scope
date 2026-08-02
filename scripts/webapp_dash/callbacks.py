"""Dash callback registration for ReacNet Scope WebUI V1.

All callbacks are registered in ``register_callbacks(app)``.  Each callback
delegates to ``scripts.webapp_dash.services`` for data operations and never
re-implements analysis logic.
"""

from __future__ import annotations

import re
import time
import json
from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
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

from rng_tools.pathway_export import pathway_csv_text, pathway_document
from reacnet_scope.indexes import dataset_id_for_source
from scripts.webapp_dash import services as svc
from scripts.webapp_dash.navigation import (
    DEFAULT_PAGE,
    PAGE_CLASS_NAMES,
    PAGE_DESCRIPTIONS,
    PAGE_IDS,
    PAGE_LABELS,
    PAGE_SECTIONS,
    TOP_NAV_PAGE_IDS,
)
PAGE_DATA_REQUIREMENTS = {
    "species": ("reaction", "reactionabcd"),
    "reactions": ("reaction", "reactionabcd"),
    "pathway": ("reaction", "reactionabcd"),
    "intermediate": ("species", ".species"),
    "evolution": ("species", ".species"),
    "carbon": ("species", ".species"),
    "events": ("reactionevent", ".reactionevent.csv + .molecules.csv"),
    "trajectory": ("trajectory", "轨迹文件与帧索引"),
}

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


def initial_store() -> dict[str, Any]:
    return {
        "folder": "",
        "base": "",
        "dataset_id": "",
        "label": "未选择",
        "ready_count": 0,
        "capabilities": {},
        "readiness": {},
        "artifacts": {},
        "discovered_artifacts": {},
        "artifact_overrides": {},
        "min_tp": 1,
        "selected_smiles": "",
        "selected_formula": "",
        "selected_species_source": "",
    }


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


def _dataset_id_from_selection(folder: str, base: str) -> str:
    """Return the cache-compatible ID for one fully qualified dataset base."""
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


def _pathway_reset_trigger_kind(
    callback_context: Any,
    app_store: dict[str, Any] | None,
    pathway_context: dict[str, Any] | None,
) -> str | None:
    """Choose the strongest pathway reset required by one Dash update."""
    property_ids = _triggered_property_ids(callback_context)
    triggered_components = {
        property_id.split(".", 1)[0]
        for property_id in property_ids
    }
    app_store_triggered = (
        "app-store.data" in property_ids
        or "app-store" in triggered_components
    )
    if app_store_triggered:
        current_dataset_id = _current_dataset_id(app_store)
        source_dataset_id = str(
            (pathway_context or {}).get("dataset_id") or ""
        )
        if (
            not current_dataset_id
            or not source_dataset_id
            or current_dataset_id != source_dataset_id
        ):
            return "dataset"
    return None


_EVENT_GROUP_STYLE = {
    "core": ("反应核", "#dc2626"),
    "reactant": ("反应物原子", "#2563eb"),
    "product": ("产物原子", "#16a34a"),
    "shared": ("前后共有原子", "#7c3aed"),
    "context": ("局部上下文", "#94a3b8"),
}


def _format_bytes(value: Any) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _preparation_state_text(item: dict[str, Any]) -> tuple[str, str]:
    state = str(item.get("state") or "missing")
    labels = {
        "ready": ("就绪", "success"),
        "building": (f"构建中 {float(item.get('progress', 0.0) or 0.0) * 100:.0f}%", "warning"),
        "stale": ("已失效", "warning"),
        "invalid": ("无效", "danger"),
        "needs_preparation": ("待构建", "secondary"),
        "missing_source": ("缺少源文件", "secondary"),
        "missing": ("未准备", "secondary"),
    }
    return labels.get(state, (state, "secondary"))


def _preparation_item_detail(label: str, item: dict[str, Any]) -> str:
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
    elif label == "C/O/Cl 组成索引" and item.get("species_size"):
        detail = (
            f"{_format_bytes(item.get('source_offset'))} / "
            f"{_format_bytes(item.get('species_size'))}"
        )
    if item.get("state") == "ready":
        records = (
            item.get("frames")
            if label == "轨迹帧索引"
            else item.get("timepoints")
            if label == "C/O/Cl 组成索引"
            else None
        )
        if records is not None:
            detail = (
                f"{int(records):,} 条记录 · "
                f"{_format_bytes(item.get('index_size'))}"
            )
    if item.get("message"):
        detail = str(item["message"])
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
                html.Div("分析索引已全部就绪", className="rs-next-action-title"),
                html.Div(
                    "可直接进入分析工作流；仅在源文件变化后重新构建。",
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
            "建立 C/O/Cl 组成索引",
            "启用“组成演化”分析和代表物种下钻。",
        ),
    }[recommended_kind]
    return html.Div(
        [
            html.Div("建议下一步", className="rs-next-action-kicker"),
            html.Div(title, className="rs-next-action-title"),
            html.Div(copy, className="rs-next-action-copy"),
            html.Div("在下方索引表中执行 ↓", className="rs-next-action-direction"),
        ]
    )


def _render_preparation_status(payload: dict[str, Any]) -> dict[str, Any]:
    alert: Any = ""
    if payload.get("cache_configured") is False:
        alert = dbc.Alert(
            [
                "启动 Dash 前请设置 ",
                html.Code("REACNET_SCOPE_CACHE_DIR=/真实/可写目录"),
                "；不要直接使用文档中的 /path/to/... 示例路径。",
            ],
            color="warning",
            className="py-2 mb-0",
        )
    elif payload.get("cache_writable") is False:
        alert = dbc.Alert(
            [
                "缓存根目录不可写；请检查启动环境中的 ",
                html.Code("REACNET_SCOPE_CACHE_DIR"),
                " 后重启 Dash。",
            ],
            color="danger",
            className="py-2 mb-0",
        )
    updated = payload.get("last_updated_epoch")
    updated_text = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
        if updated
        else "-"
    )
    cache_dir = str(payload.get("cache_dir") or "未配置")
    meta = html.Details(
        [
            html.Summary(
                f"缓存详情 · 索引占用 {_format_bytes(payload.get('index_bytes'))} · 最后更新 {updated_text}"
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("数据集 ID"),
                            html.Code(payload.get("dataset_id") or "-"),
                        ],
                        className="rs-cache-meta-row",
                    ),
                    html.Div(
                        [
                            html.Span("缓存目录"),
                            html.Code(cache_dir, className="rs-cache-path"),
                            dcc.Clipboard(
                                content=cache_dir,
                                title="复制缓存目录",
                            ),
                        ],
                        className="rs-cache-meta-row",
                    ),
                ],
                className="rs-cache-meta-details",
            )
        ],
        className="rs-cache-meta-details-shell",
    )
    recommended_kind = _recommended_preparation_kind(payload)
    items = {
        "basic": ("基础分析文件", payload.get("basic") or {}),
        "event": ("事件索引", payload.get("events") or {}),
        "trajectory": ("轨迹帧索引", payload.get("trajectory") or {}),
        "composition": ("C/O/Cl 组成索引", payload.get("composition") or {}),
    }
    ready_count = sum(
        1 for _label, item in items.values() if item.get("state") == "ready"
    )
    building_kind = next(
        (
            kind
            for kind, (_label, item) in items.items()
            if item.get("state") == "building"
        ),
        None,
    )
    global_status = (
        f"正在准备{items[building_kind][0]}"
        if building_kind
        else f"{ready_count} / {len(items)} 项就绪"
    )
    return {
        "basic": _render_preparation_item(*items["basic"]),
        "event": _render_preparation_item(*items["event"]),
        "trajectory": _render_preparation_item(*items["trajectory"]),
        "composition": _render_preparation_item(*items["composition"]),
        "meta": meta,
        "alert": alert,
        "next_action": _render_next_preparation_action(recommended_kind),
        "global_status": global_status,
        "global_class": (
            "rs-index-global-state is-ready"
            if ready_count == len(items)
            else "rs-index-global-state is-partial"
        ),
        "refresh_label": f"状态自动刷新 · {updated_text}",
        "recommended_kind": recommended_kind,
    }


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
    participant_ids = {
        int(value)
        for value in (
            groups.get("participants")
            or groups.get("reactant")
            or groups.get("product")
            or []
        )
    }
    if scope == "core":
        core_atoms = [atom for atom in atoms if int(atom.get("id") or -1) in core_ids]
        atoms = core_atoms or atoms
    elif scope == "participants":
        participant_atoms = [
            atom
            for atom in atoms
            if int(atom.get("id") or -1) in participant_ids
        ]
        atoms = participant_atoms or atoms

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


def _structure_species_card(item: dict[str, Any], side_label: str = "物种") -> Any:
    duplicate_label = ""
    if int(item.get("occurrence_total") or 0) > 1:
        duplicate_label = (
            f"（第 {item.get('occurrence')} / "
            f"{item.get('occurrence_total')} 项）"
        )
    return html.Div(
        [
            html.Img(
                src=item.get("structure_url"),
                alt=f"{side_label} {item.get('formula') or '?'} 结构{duplicate_label}",
            ),
            html.Strong(
                str(item.get("formula") or "?"),
                className="rs-channel-species-formula",
            ),
            html.Code(
                str(item.get("smiles") or ""),
                className="rs-channel-species-smiles",
            ),
            html.Span(duplicate_label, className="rs-channel-stoich-label")
            if duplicate_label
            else None,
        ],
        className="rs-channel-species-card",
    )


def _structure_reaction_side(items: list[dict[str, Any]], side_label: str) -> Any:
    children: list[Any] = []
    for index, item in enumerate(items):
        if index:
            children.append(html.Span("+", className="rs-channel-operator"))
        children.append(_structure_species_card(item, side_label))
    return html.Div(children, className="rs-channel-reaction-side")


def _reaction_structure_detail_children(
    detail: dict[str, Any],
    *,
    title: str = "完整结构反应式",
    role_label: str = "",
) -> list[Any]:
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
            ],
            className="rs-channel-detail-header",
        ),
        html.Div(
            [
                _structure_reaction_side(
                    detail.get("reactants") or [],
                    "反应物",
                ),
                html.Span(
                    "→",
                    className="rs-channel-arrow",
                    **{"aria-label": "生成"},
                ),
                _structure_reaction_side(
                    detail.get("products") or [],
                    "产物",
                ),
            ],
            className="rs-channel-structure-reaction",
        ),
    ]


def _species_structure_detail_children(
    items: list[dict[str, Any]],
    *,
    title: str,
    note: str = "",
) -> list[Any]:
    return [
        html.Div(
            [
                html.Span(title, className="rs-channel-detail-kicker"),
                html.Span(note, className="rs-channel-role") if note else None,
            ],
            className="rs-channel-detail-header rs-structure-detail-heading",
        ),
        html.Div(
            [_structure_species_card(item) for item in items],
            className="rs-channel-reaction-side rs-species-structure-list",
        ),
    ]


def _selected_table_row(
    selected_rows: list[int] | None,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not selected_rows or not rows:
        return None
    index = int(selected_rows[0])
    return dict(rows[index]) if 0 <= index < len(rows) else None


def register_callbacks(app: Any) -> None:
    # ── Navigation ──────────────────────────────────────────────────

    @app.callback(
        Output("page-species", "className"),
        Output("page-reactions", "className"),
        Output("page-evolution", "className"),
        Output("page-events", "className"),
        Output("page-trajectory", "className"),
        Output("page-intermediate", "className"),
        Output("page-pathway", "className"),
        Output("page-carbon", "className"),
        Output("page-data-management", "className"),
        Output("page-batch-compare", "className"),
        Output("nav-species", "className"),
        Output("nav-reactions", "className"),
        Output("nav-evolution", "className"),
        Output("nav-events", "className"),
        Output("nav-trajectory", "className"),
        Output("nav-intermediate", "className"),
        Output("nav-pathway", "className"),
        Output("nav-carbon", "className"),
        Output("nav-data-management", "className"),
        Output("data-open-batch-compare-btn", "className"),
        Output("page-store", "data"),
        Output("page-title", "children"),
        Output("page-eyebrow-section", "children"),
        Output("page-description", "children"),
        Output("page-header", "style"),
        Output("app-body", "className"),
        Input("nav-species", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        Input("nav-evolution", "n_clicks"),
        Input("nav-events", "n_clicks"),
        Input("nav-trajectory", "n_clicks"),
        Input("nav-intermediate", "n_clicks"),
        Input("nav-pathway", "n_clicks"),
        Input("nav-carbon", "n_clicks"),
        Input("nav-data-management", "n_clicks"),
        Input("data-open-batch-compare-btn", "n_clicks"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("species-to-channels-btn", "n_clicks"),
        Input("species-to-evolution-btn", "n_clicks"),
        Input("rxn-channel-back-btn", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("rxn-channel-to-event-btn", "n_clicks"),
        Input("event-back-btn", "n_clicks"),
        Input("species-to-pathway-btn", "n_clicks"),
        Input("rxn-to-pathway-btn", "n_clicks"),
        Input("inter-to-pathway-btn", "n_clicks"),
        Input("inter-to-evolution-btn", "n_clicks"),
        Input("pathway-open-events-btn", "n_clicks"),
        Input("event-extract-btn", "n_clicks"),
        Input("trajectory-back-events-btn", "n_clicks"),
        State("page-store", "data"),
    )
    def _navigate(*_args):
        triggered_id = ctx.triggered_id
        stored_state = (_args[-1] or {}) if _args else {}
        stored_page = stored_state.get("page")
        if triggered_id in {
            "rxn-to-event-btn",
            "rxn-channel-to-event-btn",
            "pathway-open-events-btn",
        }:
            page_id = "events"
        elif triggered_id == "event-back-btn":
            page_id = stored_state.get("return_page") or DEFAULT_PAGE
        elif triggered_id == "event-extract-btn":
            page_id = "trajectory"
        elif triggered_id == "trajectory-back-events-btn":
            page_id = "events"
        elif triggered_id in {
            "species-to-pathway-btn",
            "rxn-to-pathway-btn",
            "inter-to-pathway-btn",
        }:
            page_id = "pathway"
        elif triggered_id in {
            "species-to-channels-btn",
            "species-to-event-btn",
        }:
            page_id = "reactions"
        elif triggered_id == "rxn-channel-back-btn":
            page_id = "species"
        elif triggered_id == "data-open-batch-compare-btn":
            page_id = "batch-compare"
        elif triggered_id in {
            "nav-data-management",
            "open-data-modal",
            "species-open-data-modal",
        }:
            page_id = "data-management"
        elif triggered_id in {
            "species-to-evolution-btn",
            "inter-to-evolution-btn",
        }:
            page_id = "evolution"
        else:
            page_id = triggered_id.removeprefix("nav-") if triggered_id else stored_page
        if page_id not in PAGE_IDS:
            page_id = DEFAULT_PAGE
        page_classes = {
            pid: (
                f"{PAGE_CLASS_NAMES.get(pid, 'rs-page')} active"
                if pid == page_id
                else PAGE_CLASS_NAMES.get(pid, "rs-page")
            )
            for pid in PAGE_IDS
        }
        nav_classes = {
            pid: f"rs-top-nav-item{' active' if pid == page_id else ''}"
            for pid in TOP_NAV_PAGE_IDS
        }
        page_state = {"page": page_id}
        return_context = {
            "rxn-to-event-btn": ("reactions", "返回反应式检索"),
            "rxn-channel-to-event-btn": ("reactions", "返回反应通道"),
            "pathway-open-events-btn": ("pathway", "返回候选路径"),
        }.get(triggered_id)
        if page_id == "events" and return_context:
            page_state.update(
                return_page=return_context[0],
                return_label=return_context[1],
            )
        elif triggered_id in {
            "event-extract-btn",
            "trajectory-back-events-btn",
        }:
            for key in ("return_page", "return_label"):
                if stored_state.get(key):
                    page_state[key] = stored_state[key]
        return (
            tuple(page_classes[pid] for pid in PAGE_IDS)
            + tuple(nav_classes[pid] for pid in TOP_NAV_PAGE_IDS)
            + (
                (
                    "rs-top-nav-item rs-nav-utility active"
                    if page_id == "data-management"
                    else "rs-top-nav-item rs-nav-utility"
                ),
                (
                    "rs-top-nav-item rs-nav-utility active"
                    if page_id == "batch-compare"
                    else "rs-top-nav-item rs-nav-utility"
                ),
                page_state,
                PAGE_LABELS[page_id],
                PAGE_SECTIONS[page_id],
                PAGE_DESCRIPTIONS[page_id],
                {},
                "rs-body rs-tool-shell",
            )
        )

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
        Input("pathway-analysis-tabs", "active_tab"),
    )
    def _update_page_data_status(page_store, app_store, pathway_tab):
        page_id = (page_store or {}).get("page") or "species"
        if page_id == "data-management":
            label = str((app_store or {}).get("label") or "").strip()
            if label:
                return f"当前数据集：{label}", "rs-page-status is-ready"
            return "尚未加载数据集", "rs-page-status is-independent"
        if page_id == "batch-compare":
            reaction_ready = bool(((app_store or {}).get("artifacts") or {}).get("reaction"))
            if reaction_ready:
                return "当前数据集可加入对比", "rs-page-status is-ready"
            return "可扫描目录或从数据管理加载", "rs-page-status is-independent"
        if page_id == "events":
            artifacts = (app_store or {}).get("artifacts") or {}
            event_ready = bool(
                artifacts.get("reactionevent") and artifacts.get("molecules")
            )
            return (
                ("RNG 事件输出已就绪", "rs-page-status is-ready")
                if event_ready
                else ("需要 reactionevent.csv + molecules.csv", "rs-page-status is-blocked")
            )
        if page_id == "pathway" and pathway_tab == "concrete-event-paths":
            artifacts = (app_store or {}).get("artifacts") or {}
            event_ready = bool(
                artifacts.get("reactionevent") and artifacts.get("molecules")
            )
            return (
                ("事件轨迹证据已就绪", "rs-page-status is-ready")
                if event_ready
                else (
                    "需要 reactionevent.csv + molecules.csv",
                    "rs-page-status is-blocked",
                )
            )
        if page_id == "trajectory":
            readiness = (app_store or {}).get("readiness") or {}
            trajectory_ready = bool(
                (readiness.get("trajectory_evidence") or {}).get("ready")
            )
            return (
                ("轨迹帧索引已就绪", "rs-page-status is-ready")
                if trajectory_ready
                else ("需要轨迹文件与帧索引", "rs-page-status is-blocked")
            )
        artifact_key, artifact_label = PAGE_DATA_REQUIREMENTS.get(
            page_id,
            ("", ""),
        )
        artifacts = (app_store or {}).get("artifacts") or {}
        if artifact_key and artifacts.get(artifact_key):
            if page_id == "pathway":
                return "聚合反应网络已就绪", "rs-page-status is-ready"
            return f"{artifact_label} 已就绪", "rs-page-status is-ready"
        return f"需要 {artifact_label or '数据文件'}", "rs-page-status is-blocked"

    @app.callback(
        Output("rxn-search-btn", "disabled"),
        Output("pathway-search-btn", "disabled"),
        Output("inter-search-btn", "disabled"),
        Output("evolution-search-btn", "disabled"),
        Output("carbon-search-btn", "disabled"),
        Output("event-rxn-btn", "disabled"),
        Output("trajectory-refresh-btn", "disabled"),
        Input("app-store", "data"),
    )
    def _update_data_dependent_actions(app_store):
        artifacts = (app_store or {}).get("artifacts") or {}
        no_reaction = not bool(artifacts.get("reaction"))
        no_species = not bool(artifacts.get("species"))
        no_reaction_events = not bool(
            artifacts.get("reactionevent") and artifacts.get("molecules")
        )
        no_trajectory = not bool(artifacts.get("trajectory"))
        return (
            no_reaction,
            no_reaction,
            no_species,
            no_species,
            no_species,
            no_reaction_events,
            no_trajectory,
        )

    @app.callback(
        Output("data-overview-view", "className"),
        Output("data-browser-view", "className"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("nav-data-management", "n_clicks"),
        Input("data-pick-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "folder": ALL, "base": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _switch_data_management_view(
        _topbar_open,
        _species_open,
        _sidebar_open,
        _pick_clicks,
        _return_clicks,
        _recent_clicks,
    ):
        triggered = ctx.triggered_id
        if triggered == "data-pick-btn" or _pattern_trigger_type(triggered) == "dir-browser-recent-entry":
            return "rs-data-view d-none", "rs-data-view"
        return "rs-data-view", "rs-data-view d-none"

    @app.callback(
        Output("data-recent-datasets", "children"),
        Input("recent-datasets", "data"),
    )
    def _show_recent_datasets(recent_records):
        return _render_recent_datasets(recent_records)

    @app.callback(
        Output("data-candidate-summary", "children"),
        Output("data-scan-status", "children"),
        Output("data-artifacts", "children"),
        Output("data-apply-btn", "disabled"),
        Input("dataset-browser-candidate", "data"),
        Input("app-store", "data"),
    )
    def _show_candidate_status(candidate, app_store):
        selected = candidate if isinstance(candidate, dict) else {}
        folder = str(selected.get("folder") or "").strip()
        base = str(selected.get("base") or "").strip()
        if not folder or not base:
            loaded = app_store or {}
            current_label = str(loaded.get("label") or "").strip()
            ready = int(loaded.get("ready_count") or 0)
            if current_label:
                summary = html.Div(
                    [
                        html.Span(className="rs-current-dataset-dot"),
                        html.Strong(current_label),
                    ],
                    className="rs-current-dataset-name",
                )
                status_text = f"当前已加载 · {ready}/7 个数据文件就绪"
            else:
                summary = html.Strong("尚未加载数据集")
                status_text = "请切换到一个可用的数据集后继续。"
            return (
                summary,
                status_text,
                _render_artifacts(loaded.get("artifacts") or {}),
                True,
            )
        try:
            target = _validated_dataset_target(selected)
            folder = target["folder"]
            base = target["base"]
            status = svc.scan_dataset(folder, base=base)
        except svc.ServiceError as exc:
            return (
                dbc.Alert(f"所选数据集不可用：{exc.message}", color="danger", className="py-2"),
                f"读取失败: {exc.message}",
                _render_artifacts({}),
                True,
            )
        except Exception as exc:
            return (
                dbc.Alert(f"所选数据集不可用：{exc}", color="danger", className="py-2"),
                f"读取失败: {exc}",
                _render_artifacts({}),
                True,
            )
        dataset = status.get("dataset") or {}
        selected_base = str(dataset.get("selected_base") or "")
        if selected_base != base:
            return (
                dbc.Alert("所选数据集已不存在，请重新选择。", color="danger", className="py-2"),
                "所选数据集已不存在。",
                _render_artifacts({}),
                True,
            )
        artifact_html = _render_artifacts(svc.artifacts_from_status(status))
        ready = svc.dataset_ready_count(status)
        display_label = target["label"] or svc.dataset_label(status)
        return (
            html.Div(
                [
                    html.Span(className="rs-current-dataset-dot is-pending"),
                    html.Strong(display_label),
                ],
                className="rs-current-dataset-name",
            ),
            f"待加载数据集已验证 · {ready}/7 个数据文件就绪",
            artifact_html,
            False,
        )

    @app.callback(
        Output("data-global-min-tp", "value"),
        Output("data-override-reaction", "value"),
        Output("data-override-species", "value"),
        Output("data-override-moname", "value"),
        Output("data-override-trajectory", "value"),
        Output("data-override-route", "value"),
        Output("data-override-reactionevent", "value"),
        Output("data-override-molecules", "value"),
        Input("page-store", "data"),
        Input("app-store", "data"),
    )
    def _populate_data_overrides(page_store, app_store):
        if (page_store or {}).get("page") != "data-management":
            raise PreventUpdate
        store = app_store if isinstance(app_store, dict) else {}
        overrides = (
            store.get("artifact_overrides")
            if isinstance(store.get("artifact_overrides"), dict)
            else {}
        )
        keys = (
            "reaction",
            "species",
            "moname",
            "trajectory",
            "route",
            "reactionevent",
            "molecules",
        )
        return (
            int(store.get("min_tp") or 1),
            *(str(overrides.get(key) or "") for key in keys),
        )

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("data-overrides-feedback", "children"),
        Input("data-overrides-apply-btn", "n_clicks"),
        Input("data-overrides-reset-btn", "n_clicks"),
        State("data-global-min-tp", "value"),
        State("data-override-reaction", "value"),
        State("data-override-species", "value"),
        State("data-override-moname", "value"),
        State("data-override-trajectory", "value"),
        State("data-override-route", "value"),
        State("data-override-reactionevent", "value"),
        State("data-override-molecules", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _apply_data_overrides(
        apply_clicks,
        reset_clicks,
        min_tp,
        reaction,
        species,
        moname,
        trajectory,
        route,
        reactionevent,
        molecules,
        app_store,
    ):
        del apply_clicks, reset_clicks
        store = dict(app_store or {})
        discovered = (
            dict(store.get("discovered_artifacts") or {})
            if isinstance(store.get("discovered_artifacts"), dict)
            else {
                key: value
                for key, value in (store.get("artifacts") or {}).items()
                if not str(key).startswith("_")
            }
        )
        threshold = max(1, int(min_tp or 1))
        if ctx.triggered_id == "data-overrides-reset-btn":
            merged = {**discovered, "_min_tp": threshold}
            return (
                {
                    **store,
                    "artifacts": merged,
                    "artifact_overrides": {},
                    "discovered_artifacts": discovered,
                    "min_tp": threshold,
                },
                dbc.Alert(
                    f"已恢复自动检测文件；全局最小 TP = {threshold}。",
                    color="success",
                    className="py-2 mb-0",
                ),
            )

        raw_values = {
            "reaction": reaction,
            "species": species,
            "moname": moname,
            "trajectory": trajectory,
            "route": route,
            "reactionevent": reactionevent,
            "molecules": molecules,
        }
        overrides: dict[str, str] = {}
        try:
            for key, raw in raw_values.items():
                text = str(raw or "").strip()
                if not text:
                    continue
                path = svc.validate_browse_path(text)
                if not path.is_file():
                    raise svc.ServiceError(
                        f"{key} 不是可读文件: {path}",
                        reason="not_file",
                    )
                overrides[key] = str(path)
        except (TypeError, ValueError, svc.ServiceError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return no_update, dbc.Alert(
                f"未应用覆盖：{message}",
                color="danger",
                className="py-2 mb-0",
            )

        merged = {**discovered, **overrides, "_min_tp": threshold}
        return (
            {
                **store,
                "artifacts": merged,
                "artifact_overrides": overrides,
                "discovered_artifacts": discovered,
                "min_tp": threshold,
            },
            dbc.Alert(
                f"已应用 {len(overrides)} 个文件覆盖；全局最小 TP = {threshold}。",
                color="success",
                className="py-2 mb-0",
            ),
        )

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
        Output("data-rng-event-command", "children"),
        Output("data-prep-event-command", "children"),
        Output("data-prep-trajectory-command", "children"),
        Output("data-prep-composition-command", "children"),
        Output("data-rng-event-copy", "content"),
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
        Input("page-store", "data"),
        Input("data-prep-refresh-btn", "n_clicks"),
        Input("data-prep-refresh", "n_intervals"),
        Input("dataset-browser-candidate", "data"),
        State("app-store", "data"),
    )
    def _refresh_preparation_status(page_store, _refresh_clicks, _tick, candidate, app_store):
        if (page_store or {}).get("page") != "data-management":
            return (
                "", "", "", "", "", "", "", "",
                "rs-index-global-state", "状态自动刷新", "", "", "", "",
                "", "", "", "", True, True, True, True,
                "rs-index-action", "rs-index-action", "rs-index-action",
            )
        try:
            target = _validated_dataset_target(candidate, app_store=app_store)
            payload = svc.dataset_preparation_status(
                target["folder"],
                base=target["base"],
            )
        except svc.ServiceError as exc:
            error = str(exc.message)
            return (
                "", "", "", "", "", error, "", "状态不可用",
                "rs-index-global-state is-partial", "状态读取失败", "", "", "", "",
                "", "", "", "", True, True, True, False,
                "rs-index-action", "rs-index-action", "rs-index-action",
            )
        except Exception as exc:
            error = f"读取准备状态失败: {exc}"
            return (
                "", "", "", "", "", error, "", "状态不可用",
                "rs-index-global-state is-partial", "状态读取失败", "", "", "", "",
                "", "", "", "", True, True, True, False,
                "rs-index-action", "rs-index-action", "rs-index-action",
            )

        events = payload.get("events") or {}
        trajectory = payload.get("trajectory") or {}
        composition = payload.get("composition") or {}

        def clear_disabled(item: dict[str, Any]) -> bool:
            return str(item.get("state") or "missing") not in {
                "ready",
                "stale",
                "invalid",
            }

        rendered = _render_preparation_status(payload)
        recommended_kind = rendered["recommended_kind"]

        def action_class(kind: str) -> str:
            return (
                "rs-index-action is-recommended"
                if kind == recommended_kind
                else "rs-index-action"
            )

        return (
            rendered["basic"],
            rendered["event"],
            rendered["trajectory"],
            rendered["composition"],
            rendered["meta"],
            rendered["alert"],
            rendered["next_action"],
            rendered["global_status"],
            rendered["global_class"],
            rendered["refresh_label"],
            payload.get("rng_event_command") or "",
            payload.get("event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
            payload.get("rng_event_command") or "",
            payload.get("event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
            clear_disabled(events),
            clear_disabled(trajectory),
            clear_disabled(composition),
            False,
            action_class("event"),
            action_class("trajectory"),
            action_class("composition"),
        )

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
        cancel=Input("data-prep-cancel-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _prepare_dataset_cache(
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
            "composition": "组成索引",
        }
        try:
            target = _validated_dataset_target(
                candidate,
                app_store=app_store,
            )
            set_progress(
                dbc.Alert(
                    f"已启动 {labels[kind]}后台任务；可在上方查看检查点进度。",
                    color="info",
                    className="py-2 mb-0",
                )
            )
            result = svc.prepare_dataset_cache(
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
                f"缓存任务失败: {exc}",
                color="danger",
                className="py-2 mb-0",
            )

        status = result.get("status") or {}
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
                dbc.Alert("索引正在由离线准备程序构建；请先停止该程序后再清理。", color="warning", className="py-2"),
            )
        size = _format_bytes(item.get("index_size"))
        label = {
            "event": "事件",
            "trajectory": "轨迹帧",
            "composition": "组成",
        }[kind]
        message = html.Div(
            [
                html.P(f"将清理当前数据集的 {label} 索引，预计释放 {size}。"),
                html.P(
                    "只删除缓存目录中的派生文件，不会删除轨迹、.species、事件 CSV 或任何 ReacNetGenerator 输出文件。",
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

    # ── Directory browser (internal data-management view) ────────────

    @app.callback(
        Output("dir-browser-path-input", "value"),
        Output("dir-browser-back-btn", "disabled"),
        Output("dir-browser-current", "children"),
        Output("dir-browser-body", "children"),
        Output("dir-browser-path", "data"),
        Output("dataset-browser-candidate", "data"),
        Output("data-apply-btn", "disabled", allow_duplicate=True),
        Output("data-folder-input", "value", allow_duplicate=True),
        Output("data-rungroup", "value", allow_duplicate=True),
        Input("data-pick-btn", "n_clicks"),
        Input("data-folder-input", "value"),
        Input("dir-browser-path-input", "value"),
        Input("dir-browser-go-btn", "n_clicks"),
        Input({"type": "dir-browser-entry", "path": ALL}, "n_clicks"),
        Input("dir-browser-back-btn", "n_clicks"),
        Input({"type": "dir-browser-dataset", "base": ALL}, "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "folder": ALL, "base": ALL}, "n_clicks"),
        State("dir-browser-path", "data"),
        State("data-folder-input", "value"),
        State("dataset-browser-candidate", "data"),
        State("recent-datasets", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _handle_dir_browser(
        pick_clicks,
        manual_dataset_input,
        path_input,
        go_clicks,
        _entry_clicks,
        back_clicks,
        _dataset_clicks,
        _recent_clicks,
        current_path,
        folder_input,
        candidate,
        recent_records,
        app_store,
    ):
        """Consolidated state machine for the directory browser view.

        Browser state stays separate from the applied dataset. Directory
        navigation or manual input can choose a candidate; the separate atomic
        apply callback is the only writer to ``app-store``.
        """
        triggered_id = ctx.triggered_id
        if triggered_id is None:
            raise PreventUpdate

        # --- OPEN -----------------------------------------------------
        if triggered_id == "data-pick-btn":
            start_path = _resolve_initial_browse_path(
                folder_input,
                candidate=candidate,
                app_store=app_store,
            )
            return _build_dir_browser_response(start_path, recent_records)

        # --- ADVANCED MANUAL INPUT -----------------------------------
        if triggered_id == "data-folder-input":
            raw = str(manual_dataset_input or "").strip()
            if not raw:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    None,
                    True,
                    no_update,
                    no_update,
                )
            try:
                resolved = svc.resolve_dataset_input(raw)
                snapshot = svc.browse_dataset_location(resolved["folder"])
            except svc.ServiceError:
                return (
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    None,
                    True,
                    no_update,
                    no_update,
                )
            preferred_base = str(resolved.get("preferred_base") or "")
            datasets = snapshot.get("datasets") or []
            actual = (
                _candidate_for_base(snapshot, preferred_base)
                if preferred_base
                else (datasets[0] if len(datasets) == 1 else None)
            )
            compact = _compact_browser_candidate(actual) if actual else None
            return (
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                compact,
                compact is None,
                no_update,
                no_update,
            )

        # --- PATH INPUT / GO -----------------------------------------
        if triggered_id == "dir-browser-path-input" or triggered_id == "dir-browser-go-btn":
            target = (path_input or "").strip()
            if not target:
                return _build_dir_browser_response(
                    current_path
                    or _resolve_initial_browse_path(
                        folder_input,
                        candidate=candidate,
                        app_store=app_store,
                    ),
                    recent_records,
                    error="请输入服务器目录后再前往。",
                )
            return _build_dir_browser_response(target, recent_records)

        # --- NAVIGATE TO SUBDIR ---------------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-entry":
            if not _triggered_click_value():
                raise PreventUpdate
            return _build_dir_browser_response(triggered_id["path"], recent_records)

        # --- GO UP ----------------------------------------------------
        if triggered_id == "dir-browser-back-btn":
            stored = (current_path or "").strip()
            if not stored:
                raise PreventUpdate
            try:
                cur = svc.validate_browse_path(stored)
                parent = str(cur.parent)
                svc.validate_browse_path(parent)
                return _build_dir_browser_response(parent, recent_records)
            except svc.ServiceError:
                return _build_dir_browser_response(
                    stored,
                    recent_records,
                    error="已在允许的根目录边界，无法继续返回上一级。",
                )

        # --- SELECT DATASET CARD -------------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-dataset":
            if not _triggered_click_value():
                raise PreventUpdate
            return _select_browser_candidate(
                current_path,
                triggered_id.get("base", ""),
                recent_records,
            )

        # --- SELECT RECENT DATASET -----------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-recent-entry":
            if not _triggered_click_value():
                raise PreventUpdate
            return _select_browser_candidate(
                triggered_id.get("folder", ""),
                triggered_id.get("base", ""),
                recent_records,
            )

        raise PreventUpdate

    @app.callback(
        Output("app-store", "data"),
        Output("topbar-folder", "children"),
        Output("topbar-rungroup", "children"),
        Output("topbar-status", "children"),
        Output("topbar-status", "className"),
        Output("recent-datasets", "data"),
        Output("dataset-browser-candidate", "data", allow_duplicate=True),
        Output("data-load-feedback", "children"),
        Output("data-overview-view", "className", allow_duplicate=True),
        Output("data-browser-view", "className", allow_duplicate=True),
        Input("data-apply-btn", "n_clicks"),
        State("dataset-browser-candidate", "data"),
        State("app-store", "data"),
        State("recent-datasets", "data"),
        prevent_initial_call=True,
    )
    def _apply_data_folder(n_clicks, candidate, store, recent_records):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        selected = candidate if isinstance(candidate, dict) else {}
        folder = str(selected.get("folder") or "").strip()
        base = str(selected.get("base") or "").strip()
        if not folder or not base:
            return (
                store,
                no_update,
                no_update,
                no_update,
                no_update,
                recent_records,
                None,
                dbc.Alert("请选择一个可用的数据集后再加载。", color="warning", className="py-2"),
                no_update,
                no_update,
            )
        try:
            target = _validated_dataset_target(selected)
            folder = target["folder"]
            base = target["base"]
            status = svc.scan_dataset(folder, base=base)
            dataset = status.get("dataset", {}) or {}
            selected_base_new = str(dataset.get("selected_base") or "")
            if selected_base_new != base:
                raise svc.ServiceError("所选数据集已不存在，请重新选择。")
        except Exception as exc:
            return (
                store,
                no_update,
                no_update,
                no_update,
                no_update,
                recent_records,
                None,
                dbc.Alert(
                    f"所选数据集不可用，未切换当前数据：{exc}",
                    color="danger",
                    className="py-2",
                ),
                no_update,
                no_update,
            )
        artifacts = svc.artifacts_from_status(status)
        min_tp = max(1, int(store.get("min_tp") or 1))
        artifacts["_min_tp"] = min_tp
        capabilities = svc.dataset_capabilities(status)
        readiness = svc.dataset_readiness(status)
        ready = svc.dataset_ready_count(status)
        label = svc.dataset_label(status)
        new_store = {
            **initial_store(),
            "folder": folder,
            "base": selected_base_new,
            "dataset_id": _dataset_id_from_selection(
                folder,
                selected_base_new,
            ),
            "label": label,
            "ready_count": ready,
            "capabilities": capabilities,
            "readiness": readiness,
            "artifacts": artifacts,
            "discovered_artifacts": {
                key: value
                for key, value in artifacts.items()
                if not str(key).startswith("_")
            },
            "artifact_overrides": {},
            "min_tp": min_tp,
        }
        status_class = "rs-badge" if ready >= 3 else ("rs-badge rs-bad" if ready <= 1 else "rs-badge")
        recent = svc.normalise_recent_datasets(
            [
                {
                    "folder": folder,
                    "base": selected_base_new,
                    "label": label,
                    "loaded_at": int(time.time()),
                },
                *(recent_records if isinstance(recent_records, list) else []),
            ]
        )
        return (
            new_store,
            folder,
            label,
            "基础 {} · 事件 {} · 轨迹 {}".format(
                "就绪" if (readiness.get("basic_analysis") or {}).get("ready") else "未就绪",
                "就绪" if (readiness.get("event_search") or {}).get("ready") else "未就绪",
                "就绪" if (readiness.get("trajectory_evidence") or {}).get("ready") else "未就绪",
            ),
            status_class,
            recent,
            None,
            dbc.Alert(
                f"已加载数据集：{label}",
                color="success",
                className="py-2",
            ),
            "rs-data-view",
            "rs-data-view d-none",
        )

    def _channel_columns(items: list[tuple[str, str, int | None]]) -> list[dict[str, Any]]:
        return [
            {"name": label, "id": field, **({"presentation": "markdown"} if field == "structure" else {}), **({"type": "numeric"} if field not in {"structure", "smiles", "formula", "reaction_formulas", "recommendation", "association_status", "structure_source"} else {})}
            for field, label, _width in items
        ]

    # ── Species search ──────────────────────────────────────────────

    @app.callback(
        Output("species-grid", "data"),
        Output("species-grid", "columns"),
        Output("species-alert", "children"),
        Output("species-grid-store", "data"),
        Output("species-grid", "selected_rows"),
        Output("species-grid", "page_size"),
        Output("species-grid", "page_current"),
        Output("species-csv-btn", "children"),
        Input("species-search-btn", "n_clicks"),
        State("species-query", "value"),
        State("species-query-kind", "value"),
        State("species-mass-tol", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_species(n_clicks, query, kind, mass_tol, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        if not artifacts.get("reaction"):
            return (
                [],
                _species_columns(),
                '请先在「管理数据」中导入包含 reactionabcd 的数据目录。',
                {"rows": []},
                [],
                50,
                0,
                "导出全部 CSV",
            )
        try:
            result = svc.search_species(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=float(mass_tol or 0.5),
            )
        except svc.ServiceError as exc:
            return (
                [],
                _species_columns(),
                str(exc.message),
                {"rows": []},
                [],
                50,
                0,
                "导出全部 CSV",
            )

        rows = result.get("rows") or []
        query_kind = str(result.get("query_kind") or "")
        matching_count = int(result.get("n_rows") or len(rows))
        page_size = 20 if query_kind == "mass" else 50
        if rows:
            unit = "个匹配分子式" if query_kind == "mass" else "条匹配物种"
            message = f"找到 {matching_count} {unit}；每页显示 {page_size} 条"
            if query_kind == "mass":
                message += "；选择分子式可查看原始结构结果"
        else:
            message = "未找到匹配物种；可以放宽质量容差或切换查询类型。"
        return (
            rows,
            _species_columns(query_kind),
            message,
            {
                "rows": rows,
                "query_kind": query_kind,
                "n_rows": matching_count,
                "n_visible_rows": len(rows),
                "searched": True,
                "message": message,
            },
            [],
            page_size,
            0,
            (
                "导出全部分子式 CSV"
                if query_kind == "mass"
                else "导出全部结构 CSV"
            ),
        )

    @app.callback(
        Output("species-empty-copy", "children"),
        Output("species-empty-state", "style"),
        Output("species-results", "style"),
        Output("species-open-data-modal", "style"),
        Output("species-search-btn", "disabled"),
        Output("species-csv-btn", "disabled"),
        Output("species-query-card", "style"),
        Input("app-store", "data"),
        Input("species-grid-store", "data"),
    )
    def _update_species_state(store, grid_store):
        store = store or {}
        grid_store = grid_store or {}
        has_reaction_data = bool((store.get("artifacts") or {}).get("reaction"))
        rows = grid_store.get("rows") or []
        searched = bool(grid_store.get("searched"))

        if not has_reaction_data:
            empty = [
                html.H5("尚未导入反应数据", className="rs-empty-title"),
                html.P(
                    "选择 reactionabcd 数据后即可检索。",
                    className="rs-empty-copy",
                ),
            ]
            return empty, {"display": "flex"}, {"display": "none"}, {}, True, True, {"display": "none"}

        if rows:
            return [], {"display": "none"}, {"display": "block"}, {"display": "none"}, False, False, {}

        if searched:
            text = grid_store.get("message") or "未找到匹配物种；可以放宽质量容差或切换查询类型。"
            title = "没有匹配结果"
        else:
            text = "输入分子式、SMILES 或质量后查询。"
            title = "等待查询"
        empty = [
            html.Div(title, className="rs-empty-title"),
            html.P(text, className="rs-empty-copy"),
        ]
        return empty, {"display": "flex"}, {"display": "none"}, {"display": "none"}, False, True, {}

    @app.callback(
        Output("species-structure-results", "style"),
        Output("species-structure-title", "children"),
        Output("species-structure-alert", "children"),
        Output("species-structure-grid", "data"),
        Output("species-structure-grid", "columns"),
        Output("species-structure-grid", "selected_rows"),
        Output("species-structure-grid", "page_current"),
        Output("species-structure-csv-btn", "disabled"),
        Input("species-grid", "selected_rows"),
        State("species-grid", "data"),
        State("species-grid-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _load_mass_formula_structures(
        selected_rows,
        formula_rows,
        grid_store,
        store,
    ):
        if str((grid_store or {}).get("query_kind") or "") != "mass":
            return (
                {"display": "none"},
                "分子式对应结构",
                "",
                [],
                _species_columns(),
                [],
                0,
                True,
            )
        row = _selected_table_row(selected_rows, formula_rows)
        formula = str((row or {}).get("formula") or "").strip()
        if not formula:
            return (
                {"display": "none"},
                "分子式对应结构",
                "选择一个候选分子式以查看原始结构结果。",
                [],
                _species_columns(),
                [],
                0,
                True,
            )
        artifacts = ((store or {}).get("artifacts") or {})
        try:
            result = svc.search_species(
                artifacts,
                formula,
                kind="formula",
            )
        except svc.ServiceError as exc:
            return (
                {"display": "block"},
                f"{formula} 的结构结果",
                str(exc.message),
                [],
                _species_columns(),
                [],
                0,
                True,
            )
        rows = result.get("rows") or []
        total = int(result.get("n_rows") or len(rows))
        message = f"共 {total} 个结构；每页显示 50 条"
        return (
            {"display": "block"},
            f"{formula} 的结构结果",
            message,
            rows,
            _species_columns(),
            [],
            0,
            not bool(rows),
        )

    # ── Species detail panel ────────────────────────────────────────

    @app.callback(
        Output("detail-panel", "style"),
        Output("detail-body", "style"),
        Output("detail-body", "children"),
        Output("detail-empty", "style"),
        Output("species-to-channels-btn", "disabled"),
        Output("species-to-evolution-btn", "disabled"),
        Output("species-to-event-btn", "disabled"),
        Output("species-to-pathway-btn", "disabled"),
        Output("app-store", "data", allow_duplicate=True),
        Output("evolution-targets", "value"),
        Input("species-grid", "selected_rows"),
        Input("species-structure-grid", "selected_rows"),
        State("species-grid", "data"),
        State("species-structure-grid", "data"),
        State("app-store", "data"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_species_detail(
        formula_selected_rows,
        structure_selected_rows,
        formula_rows,
        structure_rows,
        store,
        grid_store,
    ):
        store = store or {}
        is_mass_search = str((grid_store or {}).get("query_kind") or "") == "mass"
        if is_mass_search:
            selected_rows = structure_selected_rows
            table_rows = structure_rows or []
            selected_source = "mass_structure"
            if ctx.triggered_id == "species-grid":
                selected_rows = []
        else:
            selected_rows = formula_selected_rows
            table_rows = formula_rows or (grid_store or {}).get("rows") or []
            selected_source = "species_grid"
        if not selected_rows or len(selected_rows) == 0:
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                True,
                True,
                no_update,
                no_update,
            )
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate
        row = table_rows[row_idx]
        smiles = (row.get("smiles") or "").strip()
        if not smiles:
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                True,
                True,
                no_update,
                no_update,
            )
        artifacts = store.get("artifacts", {}) or {}
        try:
            detail = svc.species_detail(artifacts, smiles)
        except svc.ServiceError:
            detail = {"ok": True, "smiles": smiles, "formula": row.get("formula") or "?"}

        svg_result = svc.render_species_svg(smiles)

        formula = detail.get("formula") or "?"
        smiles_value = detail.get("smiles") or smiles
        evolution_target = smiles

        info_panel = html.Div(
            [
                html.Div(
                    [
                        html.Span(formula, className="rs-detail-formula"),
                        html.Code(smiles_value, className="rs-detail-smiles"),
                    ],
                    className="rs-detail-identity",
                ),
                html.Dl(
                    [
                        html.Dt("精确质量"),
                        html.Dd(_fmt_num(detail.get("exact_mass"))),
                        html.Dt("标称质量"),
                        html.Dd(_fmt_num(detail.get("nominal_mass"))),
                        html.Dt("反应物通量"),
                        html.Dd(_fmt_num(detail.get("tp_as_reactant"))),
                        html.Dt("产物通量"),
                        html.Dd(_fmt_num(detail.get("tp_as_product"))),
                        html.Dt("总通量"),
                        html.Dd(_fmt_num(detail.get("total_throughput"))),
                        html.Dt("消耗反应数"),
                        html.Dd(_fmt_num(detail.get("n_consume_rxns"))),
                        html.Dt("生成反应数"),
                        html.Dd(_fmt_num(detail.get("n_produce_rxns"))),
                    ]
                ),
            ],
            className="rs-detail-stats",
        )

        if svg_result.get("ok") and svg_result.get("svg"):
            svg_raw = svg_result["svg"]
            svg_wrapped = _wrap_svg_doc(svg_raw)
            structure_panel = html.Div(
                html.Iframe(
                    srcDoc=svg_wrapped,
                    style={"border": "none", "width": "100%", "height": "100%"},
                ),
                className="rs-svg-wrap",
            )
        elif svg_result.get("message"):
            structure_panel = html.Div(svg_result["message"], className="rs-svg-wrap rs-empty")
        else:
            structure_panel = html.Div("暂无可用结构图", className="rs-svg-wrap rs-empty")

        children = [structure_panel, info_panel]

        updated_store = {
            **store,
            "selected_smiles": smiles,
            "selected_formula": formula,
            "selected_species_source": selected_source,
        }
        return (
            {"display": "block"},
            {"display": "grid"},
            children,
            {"display": "none"},
            False,
            False,
            False,
            False,
            updated_store,
            evolution_target,
        )

    @app.callback(
        Output("pathway-start-smiles", "value"),
        Output("pathway-goal", "value"),
        Output("pathway-target-max-carbon", "value"),
        Input("species-to-pathway-btn", "n_clicks"),
        Input("rxn-to-pathway-btn", "n_clicks"),
        Input("inter-to-pathway-btn", "n_clicks"),
        State("species-grid", "selected_rows"),
        State("species-grid", "data"),
        State("rxn-grid", "selected_rows"),
        State("rxn-grid", "data"),
        State("inter-grid", "selected_rows"),
        State("inter-grid", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _send_selection_to_pathway(
        species_clicks,
        reaction_clicks,
        intermediate_clicks,
        species_selected,
        species_rows,
        reaction_selected,
        reaction_rows,
        intermediate_selected,
        intermediate_rows,
        store,
    ):
        if ctx.triggered_id == "species-to-pathway-btn":
            if species_clicks is None:
                raise PreventUpdate
            if (
                str((store or {}).get("selected_species_source") or "")
                == "mass_structure"
            ):
                smiles = str((store or {}).get("selected_smiles") or "")
                if smiles:
                    return smiles, no_update, no_update
            rows = species_rows or []
            if species_selected:
                index = int(species_selected[0])
                if 0 <= index < len(rows):
                    smiles = str((rows[index] or {}).get("smiles") or "")
                    if smiles:
                        return smiles, no_update, no_update
            smiles = str((store or {}).get("selected_smiles") or "")
            if smiles:
                return smiles, no_update, no_update
            raise PreventUpdate
        if ctx.triggered_id == "inter-to-pathway-btn":
            if intermediate_clicks is None or not intermediate_selected:
                raise PreventUpdate
            rows = intermediate_rows or []
            index = int(intermediate_selected[0])
            if 0 <= index < len(rows):
                smiles = str((rows[index] or {}).get("smiles") or "").strip()
                if smiles:
                    return smiles, no_update, no_update
            raise PreventUpdate
        if reaction_clicks is None or not reaction_selected:
            raise PreventUpdate
        rows = reaction_rows or []
        index = int(reaction_selected[0])
        if index < 0 or index >= len(rows):
            raise PreventUpdate
        row = rows[index] or {}
        reactants = row.get("reactant_smiles") or []
        if reactants:
            return str(reactants[0]), no_update, no_update
        reaction_text = str(row.get("reaction_smiles") or "")
        first_side, separator, _second_side = reaction_text.partition(" -> ")
        if separator and first_side:
            return first_side.split(" + ", 1)[0], no_update, no_update
        raise PreventUpdate

    # ── Reaction formula search ─────────────────────────────────────

    @app.callback(
        Output("rxn-grid", "data"),
        Output("rxn-grid", "columns"),
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
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            result = svc.search_reactions_by_formula(
                artifacts,
                reactants or "",
                products or "",
                mode=mode or "exact",
                top=int(top or 50),
                with_share=bool(with_share),
                share_metric=share_metric or "net_tp",
                share_abs_metric=bool(share_abs),
                share_positive_only=bool(share_positive),
            )
        except svc.ServiceError as exc:
            return (
                [],
                _reaction_columns(),
                str(exc.message),
                {"rows": []},
                {"display": "none"},
                {"display": "block"},
            )
        rows = result.get("rows") or []
        return (
            rows,
            _reaction_columns(with_share=bool(with_share)),
            None,
            {"rows": rows, "meta": result.get("meta", {})},
            {"display": "none"},
            {"display": "block"},
        )

    @app.callback(
        Output("rxn-query-card", "style"),
        Output("rxn-results-card", "style"),
        Output("rxn-channel-view", "style"),
        Input("species-to-channels-btn", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        Input("rxn-channel-back-btn", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        prevent_initial_call=True,
    )
    def _toggle_reaction_view(
        _channel_clicks,
        _event_clicks,
        _back_clicks,
        _nav_clicks,
    ):
        if ctx.triggered_id in {
            "species-to-channels-btn",
            "species-to-event-btn",
        }:
            return {"display": "none"}, {"display": "none"}, {"display": "block"}
        return {}, {}, {"display": "none"}

    @app.callback(
        Output("rxn-production-grid", "data"),
        Output("rxn-production-grid", "columns"),
        Output("rxn-consumption-grid", "data"),
        Output("rxn-consumption-grid", "columns"),
        Output("rxn-channel-alert", "children"),
        Output("rxn-production-grid", "selected_rows"),
        Output("rxn-consumption-grid", "selected_rows"),
        Input("species-to-channels-btn", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        State("rxn-top", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _load_selected_species_channels(
        _channel_clicks,
        _event_clicks,
        top,
        store,
    ):
        if ctx.triggered_id not in {
            "species-to-channels-btn",
            "species-to-event-btn",
        }:
            raise PreventUpdate
        store = store or {}
        selected_smiles = str(store.get("selected_smiles") or "").strip()
        selected_formula = str(store.get("selected_formula") or "").strip()
        columns = _channel_columns(
            [
                ("reaction_formulas", "反应式", 240),
                ("forward_tp", "频次", 72),
                ("reverse_tp", "逆向", 72),
                ("net_tp", "净频次", 76),
                ("ratio_pct", "占比%", 68),
            ]
        )
        try:
            result = svc.collect_species_channels(
                store.get("artifacts", {}) or {},
                selected_smiles,
                top=max(1, int(top or 50)),
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
            )
        production_rows = result.get("production_rows") or []
        consumption_rows = result.get("consumption_rows") or []
        target = selected_formula or selected_smiles
        message = (
            f"{target}：生成通道 {len(production_rows)} 条，"
            f"消耗通道 {len(consumption_rows)} 条；"
            "按正向频次排序，净频次用于判断可逆性。"
        )
        return (
            production_rows,
            columns,
            consumption_rows,
            columns,
            message,
            [],
            [],
        )

    @app.callback(
        Output("rxn-channel-selection-store", "data"),
        Input("rxn-production-grid", "selected_rows"),
        Input("rxn-consumption-grid", "selected_rows"),
        State("rxn-production-grid", "data"),
        State("rxn-consumption-grid", "data"),
        prevent_initial_call=True,
    )
    def _select_species_channel(
        production_selected,
        consumption_selected,
        production_rows,
        consumption_rows,
    ):
        choices = (
            (
                "production",
                "生成",
                production_selected,
                production_rows or [],
            ),
            (
                "consumption",
                "消耗",
                consumption_selected,
                consumption_rows or [],
            ),
        )
        preferred_lane = (
            "production"
            if ctx.triggered_id == "rxn-production-grid"
            else "consumption"
        )
        ordered = sorted(choices, key=lambda item: item[0] != preferred_lane)
        for lane, role_label, selected, rows in ordered:
            if not selected:
                continue
            index = int(selected[0])
            if 0 <= index < len(rows):
                row = dict(rows[index] or {})
                row["role_label"] = row.get("role_label") or role_label
                return {"lane": lane, "row": row}
        return None

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
            _reaction_structure_detail_children(detail)
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
        Input("rxn-grid", "selected_rows"),
        Input("rxn-structure-show-h", "value"),
        State("rxn-grid", "data"),
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
        return _reaction_structure_detail_children(detail)

    @app.callback(
        Output("event-reaction-text", "value"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("rxn-channel-to-event-btn", "n_clicks"),
        State("rxn-grid", "selected_rows"),
        State("rxn-grid", "data"),
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
        index = int(selected_rows[0])
        if index < 0 or index >= len(rows):
            raise PreventUpdate
        return str((rows[index] or {}).get("reaction_smiles") or "")

    # ── Intermediate candidates ─────────────────────────────────────

    @app.callback(
        Output("inter-grid", "data"),
        Output("inter-grid", "columns"),
        Output("inter-alert", "children"),
        Output("inter-grid-store", "data"),
        Input("inter-search-btn", "n_clicks"),
        State("inter-kind", "value"),
        State("inter-top", "value"),
        State("inter-abundance", "value"),
        State("inter-start-ratio", "value"),
        State("inter-decay-alpha", "value"),
        State("inter-fwhm", "value"),
        State("inter-timestep", "value"),
        State("inter-require-fwhm", "value"),
        State("inter-with-flux", "value"),
        State("inter-flux-top", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output("inter-progress", "children"),
                "正在读取物种时间序列、计算寿命与通量候选…",
                "",
            ),
            (
                Output("inter-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _search_intermediates(
        n_clicks,
        kind,
        top,
        abundance,
        start_ratio,
        decay_alpha,
        fwhm,
        timestep,
        require_fwhm,
        with_flux,
        flux_top,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            result = svc.build_intermediate_candidates(
                artifacts,
                kind=kind or "intermediate",
                top=int(top or 120),
                abundance_threshold=float(abundance or 5.0),
                start_ratio_max=float(start_ratio or 0.1),
                decay_alpha=float(decay_alpha or 0.8),
                fwhm_min_ps=float(fwhm or 0.5),
                timestep_ps=float(timestep or 0.0001),
                require_fwhm=bool(require_fwhm),
                with_flux=bool(with_flux),
                flux_top=int(flux_top or 10),
            )
        except svc.ServiceError as exc:
            return [], _intermediate_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _intermediate_columns(rows), None, {"rows": rows, "meta": result.get("meta", {})}

    @app.callback(
        Output("inter-structure-detail", "children"),
        Output("inter-selected-summary", "children"),
        Output("inter-selection-card", "style"),
        Output("inter-to-pathway-btn", "disabled"),
        Output("inter-to-evolution-btn", "disabled"),
        Input("inter-grid", "selected_rows"),
        Input("inter-structure-show-h", "value"),
        State("inter-grid", "data"),
    )
    def _show_intermediate_structure(selected_rows, show_h, rows):
        row = _selected_table_row(selected_rows, rows)
        smiles = str((row or {}).get("smiles") or "").strip()
        if not smiles:
            return (
                "选择一个中间体后显示其结构、分子式与 SMILES。",
                "",
                {"display": "none"},
                True,
                True,
            )
        items = svc.build_species_structure_items(
            [smiles],
            formula_values=[(row or {}).get("formula") or ""],
            show_h=bool(show_h),
        )
        formula = str((row or {}).get("formula") or "")
        return (
            _species_structure_detail_children(
                items,
                title="中间体结构",
            ),
            f"已选：{formula or '未知分子式'} · {smiles}",
            {"display": "flex"},
            False,
            False,
        )

    @app.callback(
        Output("evolution-targets", "value", allow_duplicate=True),
        Input("inter-to-evolution-btn", "n_clicks"),
        State("inter-grid", "selected_rows"),
        State("inter-grid", "data"),
        prevent_initial_call=True,
    )
    def _send_intermediate_to_evolution(n_clicks, selected_rows, rows):
        if n_clicks is None:
            raise PreventUpdate
        row = _selected_table_row(selected_rows, rows)
        smiles = str((row or {}).get("smiles") or "").strip()
        if not smiles:
            raise PreventUpdate
        formula = str((row or {}).get("formula") or "").strip()
        return f"{formula}::{smiles}" if formula else smiles

    # ── CSV export: species ─────────────────────────────────────────

    @app.callback(
        Output("species-csv-download", "data"),
        Input("species-csv-btn", "n_clicks"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_species_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        grid_store = grid_store or {}
        rows = grid_store.get("rows") or []
        if not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        if grid_store.get("query_kind") == "mass":
            keys = [
                "formula",
                "exact_mass",
                "nominal_mass",
                "mass_error",
                "ppm_error",
                "structure_count",
                "smiles",
                "tp_as_reactant",
                "tp_as_product",
                "total_throughput",
            ]
            filename = "mass_formula_search.csv"
        else:
            keys = ["smiles", "formula", "exact_mass", "nominal_mass", "tp_as_reactant", "tp_as_product", "total_throughput", "n_consume_rxns", "n_produce_rxns"]
            filename = "species_search.csv"
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return {"content": buf.getvalue(), "filename": filename, "type": "text/csv"}

    @app.callback(
        Output("species-structure-csv-download", "data"),
        Input("species-structure-csv-btn", "n_clicks"),
        State("species-structure-grid", "data"),
        prevent_initial_call=True,
    )
    def _export_species_structure_csv(n_clicks, rows):
        if n_clicks is None or not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        keys = [
            "smiles",
            "formula",
            "exact_mass",
            "nominal_mass",
            "tp_as_reactant",
            "tp_as_product",
            "total_throughput",
            "n_consume_rxns",
            "n_produce_rxns",
        ]
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        formula = str((rows[0] or {}).get("formula") or "formula")
        return {
            "content": buf.getvalue(),
            "filename": f"{formula}_structures.csv",
            "type": "text/csv",
        }

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

    @app.callback(
        Output("inter-csv-download", "data"),
        Input("inter-csv-btn", "n_clicks"),
        State("inter-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_intermediate_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "intermediate_candidates.csv", "type": "text/csv"}

    # ── Evolution ───────────────────────────────────────────────────

    @app.callback(
        Output("evolution-graph", "figure"),
        Output("evolution-alert", "children"),
        Output("evolution-payload-store", "data"),
        Input("evolution-search-btn", "n_clicks"),
        State("evolution-targets", "value"),
        State("evolution-xaxis", "value"),
        State("evolution-smooth", "value"),
        State("evolution-species-file", "value"),
        State("evolution-species-files", "value"),
        State("evolution-formula-mode", "value"),
        State("evolution-max-smiles", "value"),
        State("evolution-normalize", "value"),
        State("evolution-time-align", "value"),
        State("evolution-timestep", "value"),
        State("evolution-downsample", "value"),
        State("evolution-max-curves", "value"),
        State("evolution-curve-filter", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output("evolution-progress", "children"),
                "正在读取时间序列、对齐多体系并生成曲线…",
                "",
            ),
            (
                Output("evolution-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _build_evolution(
        n_clicks,
        targets_text,
        x_axis,
        smooth,
        species_file,
        species_files,
        formula_mode,
        max_smiles,
        normalize,
        time_align,
        timestep,
        downsample,
        max_curves,
        curve_filter,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        targets = [t.strip() for t in re.split(r"[,;\n]+", targets_text or "") if t.strip()]
        if not targets:
            targets_text_default = store.get("selected_formula") or store.get("selected_smiles") or ""
            targets = [targets_text_default] if targets_text_default else []
        if not targets:
            from plotly.graph_objects import Figure

            return Figure(), "请先输入目标物种或分子式（或用物种检索中选择的物种）。", None
        try:
            payload = svc.build_species_evolution(
                artifacts,
                targets,
                species_file=species_file or "",
                species_files=species_files or "",
                x_axis=x_axis or "ps",
                timestep_ps=float(timestep or 0.0001),
                normalize=normalize or "none",
                smooth_window=int(smooth or 1),
                downsample=int(downsample or 0),
                max_curves=int(max_curves or 30),
                formula_mode=formula_mode or "sum",
                max_smiles_per_formula=int(max_smiles or 0),
                time_align=time_align or "raw",
            )
        except svc.ServiceError as exc:
            from plotly.graph_objects import Figure

            return Figure(), str(exc.message), None

        curves = payload.get("curves") or []
        curve_filter_text = (curve_filter or "").strip().casefold()
        if curve_filter_text:
            curves = [
                curve
                for curve in curves
                if curve_filter_text in str(curve.get("name") or curve.get("query") or "").casefold()
            ]
        x_values = payload.get("x_values") or []
        x_name = payload.get("x_name") or "x"

        import plotly.graph_objects as go

        fig = go.Figure()
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
        for i, curve in enumerate(curves):
            vals = curve.get("values") or []
            if len(vals) != len(x_values):
                continue
            name = curve.get("name") or curve.get("query") or f"curve_{i}"
            color = colors[i % len(colors)]
            fig.add_trace(go.Scatter(x=x_values, y=vals, mode="lines", name=name, line={"color": color, "width": 1.6}))
        fig.update_layout(
            xaxis_title=x_name,
            yaxis_title="相对丰度" if normalize in {"initial", "max"} else "丰度",
            template="plotly_white",
            margin={"l": 48, "r": 16, "t": 12, "b": 38},
            font={"size": 11},
            legend={"orientation": "h", "yanchor": "top", "y": -0.12, "xanchor": "left", "x": 0},
            hovermode="x unified",
        )
        visible_names = [
            str(curve.get("name") or curve.get("query") or "")
            for curve in curves
        ]
        return fig, None, {**payload, "visible_curve_names": visible_names}

    # ── CSV export: evolution ───────────────────────────────────────

    @app.callback(
        Output("evolution-csv-download", "data"),
        Input("evolution-csv-btn", "n_clicks"),
        State("evolution-payload-store", "data"),
        prevent_initial_call=True,
    )
    def _export_evolution_csv(n_clicks, payload):
        if n_clicks is None or not payload:
            raise PreventUpdate
        csv_text = svc.evolution_to_csv(payload)
        return {"content": csv_text, "filename": "evolution.csv", "type": "text/csv"}

    @app.callback(
        Output("evolution-structure-detail", "children"),
        Input("evolution-graph", "clickData"),
        Input("evolution-structure-show-h", "value"),
        State("evolution-payload-store", "data"),
    )
    def _show_evolution_curve_structures(click_data, show_h, payload):
        points = (click_data or {}).get("points") or []
        if not points or not payload:
            return "点击一条演化曲线，查看其成员物种结构。"
        curve_number = int(points[0].get("curveNumber") or 0)
        visible_names = payload.get("visible_curve_names") or []
        if curve_number < 0 or curve_number >= len(visible_names):
            return "无法定位所选曲线。"
        selected_name = str(visible_names[curve_number])
        curve = next(
            (
                item
                for item in (payload.get("curves") or [])
                if str(item.get("name") or item.get("query") or "") == selected_name
            ),
            None,
        )
        members = list((curve or {}).get("members") or [])
        if not members:
            return "所选曲线没有可显示的 SMILES 成员。"
        items = svc.build_species_structure_items(
            members,
            show_h=bool(show_h),
            max_items=24,
        )
        note = (
            f"显示前 24 / {len(members)} 个成员"
            if len(members) > 24
            else f"{len(members)} 个成员"
        )
        return _species_structure_detail_children(
            items,
            title=selected_name,
            note=note,
        )

    # ── Carbon-number evolution ────────────────────────────────────

    @app.callback(
        Output("carbon-advanced-alert", "children"),
        Output("carbon-advanced-viewer", "children"),
        Output("carbon-advanced-store", "data"),
        Input("carbon-advanced-search-btn", "n_clicks"),
        State("carbon-advanced-data", "value"),
        State("carbon-advanced-species-file", "value"),
        State("carbon-advanced-species-files", "value"),
        State("carbon-advanced-xaxis", "value"),
        State("carbon-advanced-mode", "value"),
        State("carbon-advanced-time-align", "value"),
        State("carbon-advanced-top-k", "value"),
        State("carbon-advanced-max-exact", "value"),
        State("carbon-advanced-bins", "value"),
        State("carbon-advanced-display-ranges", "value"),
        State("carbon-advanced-merge-ranges", "value"),
        State("carbon-advanced-parent", "value"),
        State("carbon-advanced-small", "value"),
        State("carbon-advanced-large", "value"),
        State("carbon-advanced-smoothing", "value"),
        State("carbon-advanced-window", "value"),
        State("carbon-advanced-polyorder", "value"),
        State("carbon-advanced-layout", "value"),
        State("carbon-advanced-regions", "value"),
        State("carbon-advanced-system-mode", "value"),
        State("carbon-advanced-theme", "value"),
        State("carbon-advanced-legend", "value"),
        State("carbon-advanced-width", "value"),
        State("carbon-advanced-height", "value"),
        State("carbon-advanced-max-formulas", "value"),
        State("carbon-timestep", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output("carbon-advanced-progress", "children"),
                "正在构建高级 Carbon Plot…",
                "",
            ),
            (
                Output("carbon-advanced-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _build_advanced_carbon(
        n_clicks,
        data_path,
        species_file,
        species_files,
        x_axis,
        mode,
        time_align,
        top_k,
        max_exact,
        carbon_bins,
        display_ranges,
        merge_ranges,
        parent,
        highlight_small,
        highlight_large,
        smoothing,
        smooth_window,
        smooth_polyorder,
        layout,
        layout_regions,
        system_mode,
        theme,
        legend_mode,
        fig_width,
        fig_height,
        max_formula_list,
        timestep,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        try:
            payload = svc.build_carbon_evolution(
                (store or {}).get("artifacts") or {},
                data_path=str(data_path or "").strip(),
                species_file=str(species_file or "").strip(),
                species_files=str(species_files or "").strip(),
                x_axis=x_axis or "ps",
                timestep_ps=float(0.0001 if timestep is None else timestep),
                mode=mode or "exact",
                top_k=int(top_k or 12),
                max_exact_lines=int(max_exact or 24),
                display_ranges=str(display_ranges or "").strip(),
                merge_ranges=str(merge_ranges or "").strip(),
                carbon_bins=str(carbon_bins or "").strip(),
                parent_carbon_number=int(parent) if parent not in (None, "") else None,
                highlight_small=str(highlight_small or "1-4"),
                highlight_large=int(highlight_large or 30),
                smoothing=smoothing or "none",
                smooth_window=int(smooth_window or 5),
                smooth_polyorder=int(smooth_polyorder or 2),
                layout=layout or "single",
                layout_regions=str(layout_regions or "").strip(),
                theme=theme or "light",
                time_align=time_align or "raw",
                system_mode=system_mode or "",
                legend_mode=legend_mode or "compact",
                fig_width=float(fig_width or 11.5),
                fig_height=float(fig_height or 8.0),
                max_formula_list=int(max_formula_list or 30),
            )
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return dbc.Alert(message, color="warning"), [], None
        svg = str(payload.get("svg") or "")
        if not svg:
            return dbc.Alert("高级 Carbon Plot 没有生成可显示的 SVG。", color="warning"), [], payload
        meta = payload.get("meta") or {}
        summary = payload.get("summary") or {}
        viewer = [
            html.Div(
                [
                    html.Span(
                        f"{int(meta.get('rows') or len(payload.get('plot_data') or []))} 数据行",
                        className="rs-stat-chip",
                    ),
                    html.Span(
                        f"{int(meta.get('n_systems') or 1)} 体系",
                        className="rs-stat-chip",
                    ),
                    html.Span(
                        str(summary.get("message") or payload.get("mode") or "carbon"),
                        className="rs-stat-chip",
                    ),
                ],
                className="rs-stat-row",
            ),
            html.Iframe(
                srcDoc=_wrap_svg_doc(svg),
                className="rs-carbon-advanced-frame",
                title="高级 Carbon Plot",
            ),
        ]
        return None, viewer, payload

    @app.callback(
        Output("carbon-advanced-csv-download", "data"),
        Input("carbon-advanced-csv-btn", "n_clicks"),
        State("carbon-advanced-store", "data"),
        prevent_initial_call=True,
    )
    def _export_advanced_carbon_csv(n_clicks, payload):
        if n_clicks is None or not payload:
            raise PreventUpdate
        return {
            "content": svc.carbon_plot_to_csv(payload),
            "filename": "carbon_plot.csv",
            "type": "text/csv",
        }

    @app.callback(
        Output("carbon-advanced-svg-download", "data"),
        Input("carbon-advanced-svg-btn", "n_clicks"),
        State("carbon-advanced-store", "data"),
        prevent_initial_call=True,
    )
    def _export_advanced_carbon_svg(n_clicks, payload):
        if n_clicks is None or not payload or not payload.get("svg"):
            raise PreventUpdate
        return {
            "content": str(payload["svg"]),
            "filename": "carbon_plot.svg",
            "type": "image/svg+xml",
        }

    @app.callback(
        Output("carbon-alert", "children"),
        Output("carbon-highlights", "children"),
        Output("carbon-payload-store", "data"),
        Output("carbon-composition-trend", "figure"),
        Input("carbon-search-btn", "n_clicks"),
        State("carbon-max-c", "value"),
        State("carbon-chlorine-state", "value"),
        State("carbon-oxygen-state", "value"),
        State("carbon-reference-smiles", "value"),
        State("carbon-timestep", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output("carbon-progress", "children"),
                "正在读取组成索引并应用 O/Cl 筛选…",
                "",
            ),
            (
                Output("carbon-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _build_carbon(
        n_clicks,
        max_c,
        chlorine_state,
        oxygen_state,
        reference_smiles,
        timestep,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.build_elemental_composition_evolution(
                artifacts,
                x_axis="ps",
                timestep_ps=float(0.0001 if timestep is None else timestep),
                max_carbon=int(max_c if max_c is not None else 6),
                chlorine_state=chlorine_state or "all",
                oxygen_state=oxygen_state or "all",
                reference_smiles=str(reference_smiles or "").strip(),
            )
        except svc.ServiceError as exc:
            empty = _empty_plotly_figure(str(exc.message))
            return dbc.Alert(str(exc.message), color="warning"), [], None, empty
        return None, _composition_highlights(payload), payload, _composition_trend_figure(payload)

    @app.callback(
        Output("carbon-composition-table", "columns"),
        Output("carbon-composition-table", "data"),
        Output("carbon-composition-table-title", "children"),
        Input("carbon-composition-trend", "clickData"),
        Input("carbon-payload-store", "data"),
        running=[
            (
                Output("carbon-drilldown-progress", "children"),
                "正在读取所选碳数组的当前值与全程峰值…",
                "",
            ),
            (
                Output("carbon-drilldown-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _render_composition_detail(click_data, payload):
        if not payload:
            return [], [], "绘制后，点击主图中的参考物种或碳数曲线查看代表物种。"
        points = (click_data or {}).get("points") or []
        if not points:
            return [], [], "点击主图中的参考物种或碳数曲线，查看该时间点的代表物种。"
        point = points[0]
        custom = point.get("customdata") or []
        try:
            timestep = int(custom[0])
            series = str(custom[1])
            detail = svc.build_carbon_species_drilldown(
                payload,
                series=series,
                timestep=timestep,
            )
        except (IndexError, TypeError, ValueError, svc.ServiceError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return [], [], f"无法读取所选碳数组：{message}"
        columns = [
            {"name": "分子式", "id": "formula"},
            {"name": "SMILES", "id": "smiles"},
            {"name": "当前数量", "id": "current_count", "type": "numeric"},
            {"name": "峰值数量", "id": "peak_count", "type": "numeric"},
            {"name": "峰值时间 (ps)", "id": "peak_time", "type": "numeric"},
        ]
        title = (
            f"{detail['series']} · 当前 {detail['current_time']:.6g} ps"
            f" · {len(detail['rows'])} 个代表物种"
            f" · 查询 {float(detail.get('query_seconds') or 0):.4f} s"
        )
        return columns, detail["rows"], title

    @app.callback(
        Output("carbon-structure-detail", "children"),
        Input("carbon-composition-table", "selected_rows"),
        Input("carbon-structure-show-h", "value"),
        State("carbon-composition-table", "data"),
    )
    def _show_carbon_species_structure(selected_rows, show_h, rows):
        row = _selected_table_row(selected_rows, rows)
        smiles = str((row or {}).get("smiles") or "").strip()
        if not smiles:
            return "选择一个代表物种后显示结构、分子式与 SMILES。"
        items = svc.build_species_structure_items(
            [smiles],
            formula_values=[(row or {}).get("formula") or ""],
            show_h=bool(show_h),
        )
        return _species_structure_detail_children(
            items,
            title="代表物种结构",
        )

    @app.callback(
        Output("carbon-dataset-name", "value"),
        Output("carbon-index-status", "children"),
        Output("carbon-index-status", "className"),
        Output("carbon-index-progress", "value"),
        Output("carbon-index-refresh", "disabled"),
        Input("app-store", "data"),
        Input("page-store", "data"),
        Input("carbon-index-refresh", "n_intervals"),
    )
    def _refresh_carbon_index_status(store, page_store, _n_intervals):
        if str((page_store or {}).get("page") or "") != "carbon":
            return no_update, no_update, no_update, no_update, True
        store = store or {}
        label = str(store.get("label") or store.get("folder") or "未选择")
        status = svc.composition_index_status(store.get("artifacts") or {})
        state = str(status.get("state") or "missing")
        percent = int(round(float(status.get("progress") or 0.0) * 100))
        if state == "ready":
            text = (
                f"组成索引已就绪 · {int(status.get('timepoints') or 0)} 个时间点"
                f" · {int(status.get('unique_species') or 0)} 个物种"
            )
            percent = 100
            class_name = "rs-index-status is-ready"
        elif state == "building":
            text = f"正在建立组成索引 · {percent}%"
            class_name = "rs-index-status is-building"
        elif state == "missing_source":
            text = "请先在“管理数据”中选择包含 .species 的数据集"
            class_name = "rs-index-status is-warning"
        elif state in {"stale", "invalid"}:
            if "REACNET_SCOPE_CACHE_DIR" in str(status.get("message") or ""):
                text = "请先设置 REACNET_SCOPE_CACHE_DIR，再建立 composition 索引"
            else:
                text = "组成索引需要重建：运行 reacnet-scope-prepare <目录> --composition-only"
            class_name = "rs-index-status is-warning"
        else:
            text = "组成索引尚未建立：运行 reacnet-scope-prepare <目录> --composition-only"
            class_name = "rs-index-status is-warning"
        return label, text, class_name, percent, state != "building"

    # ── Event evidence ──────────────────────────────────────────────

    @app.callback(
        Output("event-grid", "data", allow_duplicate=True),
        Output("event-grid", "columns", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Input("event-rxn-btn", "n_clicks"),
        State("event-reaction-text", "value"),
        State("event-rxn-before", "value"),
        State("event-rxn-after", "value"),
        State("event-rxn-max", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
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
            "before_frames": int(before or 3),
            "after_frames": int(after or 3),
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
        Output("event-grid", "selected_rows"),
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
        Input("event-grid", "selected_row_ids"),
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
        event_id = str(selected_row_ids[0] or "")
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
        }
        event_id = str(selected["row"].get("event_id") or "")
        association_ready = (
            row.get("association_status") == "matched"
            and bool(row.get("atom_id_list"))
        )
        trajectory_ready = bool(
            ((app_store or {}).get("artifacts") or {}).get("trajectory")
        )
        return (
            selected,
            event_id,
            _event_selection_summary(selected),
            {"display": "block"},
            not (association_ready and trajectory_ready),
            (
                "打开轨迹查看"
                if association_ready
                else "该事件无法定位原子"
            ),
        )

    @app.callback(
        Output("event-type-map-editor", "children"),
        Input("event-viewer-store", "data"),
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
        Input("event-extract-btn", "n_clicks"),
        Input("trajectory-refresh-btn", "n_clicks"),
        Input("event-type-map-clear-btn", "n_clicks"),
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
    )
    def _extract_selected_event(
        _open_clicks,
        _refresh_clicks,
        _clear_clicks,
        selected,
        store,
        type_element_values,
        type_element_ids,
        environment_radius,
    ):
        if ctx.triggered_id not in {
            "event-extract-btn",
            "trajectory-refresh-btn",
            "event-type-map-clear-btn",
        }:
            raise PreventUpdate
        selected = selected or {}
        row = selected.get("row") or {}
        config = selected.get("config") or {}
        kind = selected.get("kind") or ""
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            if kind == "rng_event":
                if ctx.triggered_id == "event-extract-btn":
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
                    before_frames=int(config.get("before_frames") or 3),
                    after_frames=int(config.get("after_frames") or 3),
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
            return None, {"display": "none"}, [], [], "", "", 0, 0, 0, {}, [], message

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
        return viewer, {"display": "block"}, summary, " · ".join(path_items), atom_ids_text, ovito_expression, 0, len(frames) - 1, anchor_index, marks, storyboard, "局部轨迹已按 PBC 重定位；3Dmol.js 用于快速查看，原始坐标可下载到 OVITO 复核。"

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

    @app.callback(
        Output("event-csv-download", "data"),
        Input("event-csv-btn", "n_clicks"),
        State("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_event_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "event_evidence.csv", "type": "text/csv"}

    @app.callback(
        Output("event-frames-csv-download", "data"),
        Input("event-frames-csv-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_frames(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        return {
            "content": svc.event_viewer_frames_csv(viewer),
            "filename": f"{event_id}_frames.csv",
            "type": "text/csv",
        }

    @app.callback(
        Output("event-package-download", "data"),
        Input("event-package-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        State("event-view-scope", "value"),
        prevent_initial_call=True,
    )
    def _download_event_package(n_clicks, viewer, scope):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        package_scope = (
            "environment"
            if scope == "context"
            else (scope or "participants")
        )
        return dcc.send_bytes(
            svc.build_event_package(viewer, scope=package_scope),
            f"{event_id}_evidence.zip",
            type="application/zip",
        )

    @app.callback(
        Output("event-trajectory-download", "data"),
        Input("event-trajectory-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_trajectory(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        return {
            "content": svc.event_viewer_trajectory_text(viewer),
            "filename": f"{event_id}_subset.lammpstrj",
            "type": "text/plain",
        }

    @app.callback(
        Output("event-ovito-download", "data"),
        Input("event-ovito-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_ovito_script(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        trajectory_name = f"{event_id}_subset.lammpstrj"
        return {
            "content": svc.event_viewer_ovito_script(
                viewer,
                trajectory_name=trajectory_name,
            ),
            "filename": f"{event_id}_view_ovito.py",
            "type": "text/x-python",
        }

    @app.callback(
        Output("event-vmd-download", "data"),
        Input("event-vmd-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_vmd(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        trajectory_name = f"{event_id}_subset.lammpstrj"
        return {
            "content": svc.event_viewer_vmd_script(
                viewer,
                trajectory_name=trajectory_name,
            ),
            "filename": f"{event_id}_view.tcl",
            "type": "text/plain",
        }

    # ── Candidate pathways ─────────────────────────────────────────

    @app.callback(
        Output("event-path-current-replicate", "value"),
        Output("event-path-current-source-summary", "children"),
        Output("event-path-index-status", "children"),
        Output("event-path-index-status", "className"),
        Input("app-store", "data"),
    )
    def _event_path_current_source(app_store):
        store = app_store or {}
        artifacts = store.get("artifacts") or {}
        base = str(store.get("base") or "").strip()
        label = Path(base).name if base else str(store.get("label") or "current")
        if label.endswith(".lammpstrj"):
            label = label[: -len(".lammpstrj")]
        label = label or "current"
        reactionevent = str(artifacts.get("reactionevent") or "")
        molecules = str(artifacts.get("molecules") or "")
        source_rows = [
            html.Div(
                [html.Span("当前重复"), html.Strong(label)],
                className="rs-event-path-source-row",
            ),
            html.Div(
                [
                    html.Span("事件文件"),
                    html.Code(Path(reactionevent).name if reactionevent else "未检测到"),
                ],
                className="rs-event-path-source-row",
            ),
            html.Div(
                [
                    html.Span("分子实例文件"),
                    html.Code(Path(molecules).name if molecules else "未检测到"),
                ],
                className="rs-event-path-source-row",
            ),
        ]
        if not reactionevent or not molecules:
            return (
                label,
                source_rows,
                "缺少事件或分子实例文件",
                "rs-page-status is-blocked",
            )
        try:
            validation = svc.validate_event_path_sources_for_dash(
                artifacts,
                current_replicate=label,
            )
        except svc.ServiceError as exc:
            return (
                label,
                source_rows,
                exc.message,
                "rs-page-status is-blocked",
            )
        return (
            label,
            source_rows,
            (
                f"事件索引已就绪 · "
                f"{int(validation.get('total_event_count') or 0):,} 个事件"
            ),
            "rs-page-status is-ready",
        )

    @app.callback(
        Output("event-path-additional-source-panel", "style"),
        Input("event-path-source-mode", "value"),
    )
    def _event_path_additional_source_visibility(source_mode):
        return {} if source_mode == "multiple" else {"display": "none"}

    @app.callback(
        Output("event-path-length-preview", "children"),
        Input("event-path-length", "value"),
    )
    def _event_path_length_preview(path_length):
        try:
            length = max(2, min(8, int(path_length)))
        except (TypeError, ValueError):
            return "请输入 2–8 之间的事件节点数"
        return " → ".join(f"event{index}" for index in range(1, length + 1))

    @app.callback(
        Output("event-path-review-summary", "children"),
        Input("event-path-current-replicate", "value"),
        Input("event-path-source-mode", "value"),
        Input("event-path-additional-sources", "value"),
        Input("event-path-length", "value"),
        Input("event-path-start-smiles", "value"),
        Input("event-path-max-interval-gap", "value"),
        Input("event-path-max-timestep-gap", "value"),
        Input("event-path-max-details", "value"),
    )
    def _event_path_review_summary(
        current_replicate,
        source_mode,
        additional_sources,
        path_length,
        start_smiles,
        max_interval_gap,
        max_timestep_gap,
        max_details,
    ):
        extra_lines = [
            line.strip()
            for line in str(additional_sources or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        replicate_count = 1 + (len(extra_lines) if source_mode == "multiple" else 0)
        return [
            html.Div(
                [html.Span("数据"), html.Strong(f"{replicate_count} 个重复")],
                className="rs-event-path-review-row",
            ),
            html.Div(
                [
                    html.Span("当前重复"),
                    html.Strong(str(current_replicate or "current")),
                ],
                className="rs-event-path-review-row",
            ),
            html.Div(
                [
                    html.Span("路径定义"),
                    html.Strong(
                        " → ".join(
                            f"event{index}"
                            for index in range(
                                1,
                                max(2, min(8, int(path_length or 3))) + 1,
                            )
                        )
                    ),
                ],
                className="rs-event-path-review-row",
            ),
            html.Div(
                [
                    html.Span("首事件物种"),
                    html.Strong(str(start_smiles or "不限制")),
                ],
                className="rs-event-path-review-row",
            ),
            html.Div(
                [
                    html.Span("时间限制"),
                    html.Strong(
                        f"区间差 ≤ {max_interval_gap if max_interval_gap not in (None, '') else '不限'}；"
                        f"空闲 timestep ≤ {max_timestep_gap if max_timestep_gap not in (None, '') else '不限'}"
                    ),
                ],
                className="rs-event-path-review-row",
            ),
            html.Div(
                [
                    html.Span("下钻明细"),
                    html.Strong(f"最多 {int(max_details or 0):,} 次具体发生"),
                ],
                className="rs-event-path-review-row",
            ),
        ]

    @app.callback(
        Output("event-path-wizard-step", "data"),
        Output("event-path-wizard-feedback", "children"),
        Input("event-path-step1-next", "n_clicks"),
        Input("event-path-step2-back", "n_clicks"),
        Input("event-path-step2-next", "n_clicks"),
        Input("event-path-step3-back", "n_clicks"),
        Input("event-path-step4-edit", "n_clicks"),
        Input("event-path-store", "data"),
        Input("app-store", "data"),
        State("event-path-wizard-step", "data"),
        State("event-path-current-replicate", "value"),
        State("event-path-source-mode", "value"),
        State("event-path-additional-sources", "value"),
        State("event-path-length", "value"),
        prevent_initial_call=True,
    )
    def _navigate_event_path_wizard(
        _step1_next,
        _step2_back,
        _step2_next,
        _step3_back,
        _step4_edit,
        report,
        app_store,
        current_step,
        current_replicate,
        source_mode,
        additional_sources,
        path_length,
    ):
        triggered = ctx.triggered_id
        step = int(current_step or 1)
        if triggered == "app-store":
            return 1, ""
        if triggered == "event-path-store":
            if report:
                return 4, ""
            raise PreventUpdate
        if triggered == "event-path-step2-back":
            return 1, ""
        if triggered in {"event-path-step3-back", "event-path-step4-edit"}:
            return 2, ""
        if triggered == "event-path-step2-next":
            try:
                length = int(path_length)
            except (TypeError, ValueError):
                return step, dbc.Alert("事件节点数必须是 2–8 的整数。", color="danger")
            if not 2 <= length <= 8:
                return step, dbc.Alert("事件节点数必须在 2–8 之间。", color="danger")
            return 3, ""
        if triggered == "event-path-step1-next":
            artifacts = (app_store or {}).get("artifacts") or {}
            extras = (
                str(additional_sources or "")
                if source_mode == "multiple"
                else ""
            )
            if source_mode == "multiple" and not extras.strip():
                return 1, dbc.Alert(
                    "已选择跨重复分析，请至少填写一个附加重复；"
                    "否则选择“只分析当前数据集”。",
                    color="warning",
                )
            try:
                validation = svc.validate_event_path_sources_for_dash(
                    artifacts,
                    current_replicate=str(current_replicate or "current"),
                    additional_sources=extras,
                )
            except svc.ServiceError as exc:
                return 1, dbc.Alert(exc.message, color="danger")
            return 2, dbc.Alert(
                f"数据检查通过：{int(validation.get('replicate_count') or 0)} 个重复、"
                f"{int(validation.get('total_event_count') or 0):,} 个事件。",
                color="success",
            )
        raise PreventUpdate

    @app.callback(
        Output("event-path-step-1", "style"),
        Output("event-path-step-2", "style"),
        Output("event-path-step-3", "style"),
        Output("event-path-results", "style"),
        Output("event-path-progress-1", "className"),
        Output("event-path-progress-2", "className"),
        Output("event-path-progress-3", "className"),
        Output("event-path-progress-4", "className"),
        Input("event-path-wizard-step", "data"),
    )
    def _render_event_path_wizard(step_value):
        step = max(1, min(4, int(step_value or 1)))
        panel_styles = [
            {} if step == index else {"display": "none"}
            for index in (1, 2, 3)
        ]
        result_style = {} if step == 4 else {"display": "none"}

        def step_class(index):
            suffix = " is-active" if index == step else (" is-complete" if index < step else "")
            return f"rs-event-path-step{suffix}"

        return (
            *panel_styles,
            result_style,
            *(step_class(index) for index in (1, 2, 3, 4)),
        )

    @app.callback(
        Output("event-path-run-btn", "disabled"),
        Input("app-store", "data"),
    )
    def _event_path_run_disabled(app_store):
        artifacts = (app_store or {}).get("artifacts") or {}
        return not bool(artifacts.get("reactionevent") and artifacts.get("molecules"))

    @app.callback(
        Output("event-path-store", "data"),
        Output("event-path-context-store", "data"),
        Output("event-path-alert", "children"),
        Output("event-path-summary", "children"),
        Output("event-path-summary-explanation", "children"),
        Output("event-path-comparison-chart", "figure"),
        Output("event-path-comparison-grid", "data"),
        Output("event-path-comparison-grid", "columns"),
        Input("event-path-run-btn", "n_clicks"),
        State("event-path-current-replicate", "value"),
        State("event-path-source-mode", "value"),
        State("event-path-additional-sources", "value"),
        State("event-path-length", "value"),
        State("event-path-start-smiles", "value"),
        State("event-path-max-interval-gap", "value"),
        State("event-path-max-timestep-gap", "value"),
        State("event-path-max-details", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _run_event_path_analysis(
        n_clicks,
        current_replicate,
        source_mode,
        additional_sources,
        path_length,
        start_smiles,
        max_interval_gap,
        max_timestep_gap,
        max_details,
        app_store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (app_store or {}).get("artifacts") or {}
        try:
            report = svc.analyze_event_paths_for_dash(
                artifacts,
                current_replicate=str(current_replicate or "current"),
                additional_sources=(
                    str(additional_sources or "")
                    if source_mode == "multiple"
                    else ""
                ),
                path_length=int(3 if path_length is None else path_length),
                start_smiles=str(start_smiles or ""),
                max_interval_gap=max_interval_gap,
                max_timestep_gap=max_timestep_gap,
                max_occurrence_details=int(1000 if max_details is None else max_details),
            )
        except svc.ServiceError as exc:
            return (
                None,
                None,
                dbc.Alert(exc.message, color="danger", className="py-2 mb-0"),
                [],
                [],
                _event_path_comparison_figure({}),
                [],
                _event_path_comparison_columns(),
            )
        summary = report.get("summary") or {}
        comparison = report.get("comparison") or {}
        complete = bool(summary.get("statistics_complete"))
        message = (
            f"找到 {int(summary.get('actual_path_occurrence_count') or 0):,} 次"
            f"实际路径、{int(summary.get('actual_path_signature_count') or 0):,} 种签名；"
            f"来自 {int(summary.get('replicate_count') or 0)} 个重复。"
        )
        if report.get("occurrence_details_truncated"):
            message += " 具体发生明细只保留到界面设定上限，汇总统计仍完整。"
        if not complete:
            message += " 实际路径搜索达到展开上限，支持数为下界。"
        if not comparison.get("comparison_available"):
            message += " 未找到 reactionabcd，未执行聚合网络对照。"
        return (
            report,
            {
                "schema_version": "reacnet-scope/event-path-context/v1",
                "dataset_id": _current_dataset_id(app_store),
            },
            dbc.Alert(
                message,
                color="success" if complete else "warning",
                className="py-2 mb-0",
            ),
            _event_path_summary_cards(report),
            _event_path_summary_explanation(report),
            _event_path_comparison_figure(report),
            svc.event_path_comparison_rows(report),
            _event_path_comparison_columns(),
        )

    @app.callback(
        Output("event-path-signature-grid", "data"),
        Output("event-path-signature-grid", "columns"),
        Output("event-path-filter-summary", "children"),
        Output("event-path-signature-grid", "selected_rows"),
        Input("event-path-store", "data"),
        Input("event-path-filter-flags", "value"),
        Input("event-path-min-reproduction", "value"),
        Input("event-path-min-lineages", "value"),
    )
    def _filter_event_path_signatures(
        report,
        flags,
        min_reproduction,
        min_lineages,
    ):
        if not report:
            return [], _event_path_signature_columns(), "尚未运行分析。", []
        values = set(flags or [])
        rows = svc.event_path_signature_rows(
            report,
            hide_pure_h="hide_pure_h" in values,
            hide_return_cycles="hide_return" in values,
            min_reproduction_rate=float(min_reproduction or 0.0),
            min_lineage_support=int(min_lineages or 0),
        )
        total = len(report.get("paths") or [])
        return (
            rows,
            _event_path_signature_columns(),
            f"显示 {len(rows):,} / {total:,} 种实际路径签名。",
            [],
        )

    @app.callback(
        Output("event-path-occurrence-selector", "options"),
        Output("event-path-occurrence-selector", "value"),
        Output("event-path-selected-summary", "children"),
        Output("event-path-time-grid", "data"),
        Output("event-path-time-grid", "columns"),
        Input("event-path-signature-grid", "selected_rows"),
        State("event-path-signature-grid", "data"),
        State("event-path-store", "data"),
    )
    def _select_event_path_signature(selected_rows, rows, report):
        selected = _selected_table_row(selected_rows, rows)
        if not selected or not report:
            return [], None, "从上方选择路径签名。", [], _event_path_time_columns()
        signature_id = str(selected.get("signature_id") or "")
        occurrences = svc.event_path_occurrences_for_signature(report, signature_id)
        options = [
            {
                "label": (
                    f"{item.get('replicate')} · {item.get('path_id')} · "
                    f"{int(item.get('lineage_atom_support_count') or 0)} 个连续原子"
                ),
                "value": str(item.get("path_id") or ""),
            }
            for item in occurrences
        ]
        detail_note = (
            f"保留了 {len(occurrences)} 次具体发生可供审计。"
            if occurrences
            else "该签名的具体发生明细未保留；提高“审计明细上限”后重跑。"
        )
        return (
            options,
            options[0]["value"] if options else None,
            f"{signature_id} · {detail_note}",
            svc.event_path_signature_time_rows(report, signature_id),
            _event_path_time_columns(),
        )

    @app.callback(
        Output("event-path-cytoscape", "elements"),
        Output("event-path-event-grid", "data"),
        Output("event-path-event-grid", "columns"),
        Output("event-path-edge-grid", "data"),
        Output("event-path-edge-grid", "columns"),
        Output("event-path-occurrence-summary", "children"),
        Input("event-path-occurrence-selector", "value"),
        State("event-path-store", "data"),
    )
    def _render_event_path_occurrence(path_id, report):
        occurrence = next(
            (
                item
                for item in (report or {}).get("occurrences") or []
                if str(item.get("path_id") or "") == str(path_id or "")
            ),
            None,
        )
        if occurrence is None:
            return (
                [],
                [],
                _event_path_event_columns(),
                [],
                _event_path_edge_columns(),
                "选择一次具体发生以查看严格事件链。",
            )
        event_rows, edge_rows = svc.event_path_occurrence_rows(occurrence)
        lineage = [int(value) for value in occurrence.get("lineage_atom_ids") or []]
        return (
            svc.build_event_path_occurrence_elements(occurrence),
            event_rows,
            _event_path_event_columns(),
            edge_rows,
            _event_path_edge_columns(),
            (
                f"{occurrence.get('replicate')} · {occurrence.get('path_id')} · "
                f"事件 {' → '.join(str(value) for value in occurrence.get('event_ids') or [])} · "
                f"全路径连续原子：{';'.join(map(str, lineage)) or '—'}"
            ),
        )

    @app.callback(
        Output("event-path-comparison-signature-grid", "data"),
        Output("event-path-comparison-signature-grid", "columns"),
        Input("event-path-store", "data"),
        Input("event-path-comparison-class", "value"),
    )
    def _event_path_comparison_signatures(report, classification):
        return (
            svc.event_path_comparison_signature_rows(report or {}, classification),
            _event_path_comparison_signature_columns(),
        )

    @app.callback(
        Output("event-path-json-download", "data"),
        Input("event-path-json-btn", "n_clicks"),
        State("event-path-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_path_json(n_clicks, report):
        if n_clicks is None or not report:
            raise PreventUpdate
        return dcc.send_string(
            json.dumps(report, ensure_ascii=False, indent=2),
            "event-paths.json",
        )

    @app.callback(
        Output("event-path-csv-download", "data"),
        Input("event-path-csv-btn", "n_clicks"),
        State("event-path-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_path_csv(n_clicks, report):
        if n_clicks is None or not report:
            raise PreventUpdate
        rows = svc.event_path_signature_rows(report)
        if not rows:
            raise PreventUpdate
        return {
            "content": svc.rows_to_csv(rows),
            "filename": "event-path-signatures.csv",
            "type": "text/csv",
        }

    @app.callback(
        Output("event-path-store", "data", allow_duplicate=True),
        Output("event-path-context-store", "data", allow_duplicate=True),
        Input("app-store", "data"),
        State("event-path-context-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_event_paths_on_dataset_change(app_store, event_path_context):
        if not event_path_context:
            raise PreventUpdate
        if str(event_path_context.get("dataset_id") or "") == _current_dataset_id(
            app_store
        ):
            raise PreventUpdate
        return None, None

    @app.callback(
        Output("pathway-grid", "data"),
        Output("pathway-grid", "columns"),
        Output("pathway-cytoscape", "elements"),
        Output("pathway-alert", "children"),
        Output("pathway-store", "data"),
        Output("pathway-context-store", "data"),
        Output("pathway-grid", "selected_rows"),
        Output("pathway-terminal-summary", "children"),
        Input("pathway-search-btn", "n_clicks"),
        State("pathway-start-smiles", "value"),
        State("pathway-direction", "value"),
        State("pathway-max-depth", "value"),
        State("pathway-max-branches", "value"),
        State("pathway-max-paths", "value"),
        State("pathway-min-net-tp", "value"),
        State("pathway-min-directionality", "value"),
        State("pathway-goal", "value"),
        State("pathway-target-max-carbon", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_pathways(
        n_clicks,
        start_smiles,
        direction,
        max_depth,
        max_branches,
        max_paths,
        min_net_tp,
        min_directionality,
        goal,
        target_max_carbon,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        start = str(start_smiles or "")
        artifacts = (store or {}).get("artifacts") or {}
        try:
            limits = {
                "direction": (
                    direction if direction is not None else "downstream"
                ),
                "max_depth": int(3 if max_depth is None else max_depth),
                "max_branches": int(
                    5 if max_branches is None else max_branches
                ),
                "max_paths": int(20 if max_paths is None else max_paths),
                "min_net_tp": int(1 if min_net_tp is None else min_net_tp),
                "min_directionality": float(
                    0.05
                    if min_directionality is None
                    else min_directionality
                ),
            }
            if goal == "small_fragments":
                limits["target_max_carbon"] = int(
                    4
                    if target_max_carbon is None
                    else target_max_carbon
                )
                limits["max_expansions"] = 300
                limits["evidence_mode"] = "network_only"
            payload = svc.find_pathways(artifacts, start, **limits)
        except (TypeError, ValueError) as exc:
            return (
                [],
                _pathway_columns(),
                [],
                str(exc),
                None,
                None,
                [],
                [],
            )
        except svc.ServiceError as exc:
            return (
                [],
                _pathway_columns(),
                [],
                str(exc.message),
                None,
                None,
                [],
                [],
            )

        rows = _pathway_rows(payload)
        elements = svc.build_pathway_elements(payload)
        reason_messages = {
            "species_absent": "起始物种不在当前反应网络中。",
            "no_positive_net_continuation": "该物种没有正净通量的可继续反应。",
            "filtered_by_thresholds": "候选路径均被当前净通量或方向性阈值过滤。",
            "target_not_reached": "在当前深度、分支和阈值内尚未到达目标碳数的小分子。",
            "target_already_reached": "起始物种本身已满足目标碳数；请选择更大的母体物种。",
        }
        if rows:
            message = f"找到 {len(rows)} 条候选路径；已展开 {int(payload.get('expansions') or 0)} 个状态。"
        else:
            message = reason_messages.get(
                str(payload.get("reason") or ""),
                "未找到候选路径。",
            )
        if payload.get("truncated"):
            message += (
                f" 搜索达到展开上限，结果已截断（expansions="
                f"{int(payload.get('expansions') or 0)}）。"
            )
        if payload.get("search_stage") == "network_shortlist":
            message += (
                " 当前为快速网络粗筛：未读取事件、Route 或 species 时间索引；"
                "请在选定具体反应后再做时间验证。"
            )
        pathway_context = {
            "schema_version": "reacnet-scope/pathway-context/v1",
            "dataset_id": _current_dataset_id(store),
            "source_signatures": dict(
                payload.get("source_signatures") or {}
            ),
        }
        return (
            rows,
            _pathway_columns(),
            elements,
            message,
            payload,
            pathway_context,
            [],
            _pathway_terminal_cards(payload),
        )

    @app.callback(
        Output("pathway-selected-path", "data"),
        Output("pathway-selected-step", "data"),
        Output("pathway-selection-summary", "children"),
        Output("pathway-open-events-btn", "disabled"),
        Input("pathway-store", "data"),
        Input("pathway-grid", "selected_rows"),
        Input("pathway-cytoscape", "tapNodeData"),
        State("pathway-grid", "data"),
    )
    def _select_pathway(payload, selected_rows, node_data, grid_rows):
        payload = payload or {}
        paths = payload.get("paths") or []
        if ctx.triggered_id == "pathway-store" or not paths:
            return None, None, "选择一条路径或一个反应节点。", True

        selected_path = None
        selected_step = None

        def step_handoff(path, step_index):
            steps = path.get("steps") or []
            if step_index < 1 or step_index > len(steps):
                return None
            candidate = steps[step_index - 1]
            reactants = [
                str(value) for value in candidate.get("reactants") or []
            ]
            products = [
                str(value) for value in candidate.get("products") or []
            ]
            return {
                **candidate,
                "path_rank": int(path.get("rank") or 0),
                "step_index": step_index,
                "reaction_text": (
                    f"{' + '.join(reactants)} -> {' + '.join(products)}"
                ),
            }

        if ctx.triggered_id == "pathway-grid":
            rows = grid_rows or []
            if selected_rows:
                index = int(selected_rows[0])
                if 0 <= index < len(rows):
                    rank = int((rows[index] or {}).get("rank") or 0)
                    selected_path = next(
                        (
                            path
                            for path in paths
                            if int(path.get("rank") or 0) == rank
                        ),
                        None,
                    )
                    if (
                        selected_path is not None
                        and len(selected_path.get("steps") or []) == 1
                    ):
                        selected_step = step_handoff(selected_path, 1)
        elif (
            ctx.triggered_id == "pathway-cytoscape"
            and isinstance(node_data, dict)
            and node_data.get("node_kind") == "reaction"
        ):
            rank = int(node_data.get("path_rank") or 0)
            step_index = int(node_data.get("step_index") or 0)
            reaction_key = str(node_data.get("reaction_key") or "")
            selected_path = next(
                (
                    path
                    for path in paths
                    if int(path.get("rank") or 0) == rank
                ),
                None,
            )
            if selected_path is not None:
                steps = selected_path.get("steps") or []
                if 1 <= step_index <= len(steps):
                    candidate = steps[step_index - 1]
                    if str(candidate.get("reaction_key") or "") == reaction_key:
                        selected_step = step_handoff(
                            selected_path,
                            step_index,
                        )
        if selected_path is None:
            return None, None, "选择一条有效路径或反应节点。", True
        path_handoff = {
            "path_rank": int(selected_path.get("rank") or 0),
            "species_ids": [
                str(value) for value in selected_path.get("species") or []
            ],
            "reaction_keys": [
                str(step.get("reaction_key") or "")
                for step in selected_path.get("steps") or []
            ],
        }
        if selected_step is not None:
            automatic = (
                " · 已自动选中唯一反应步骤"
                if len(selected_path.get("steps") or []) == 1
                and ctx.triggered_id == "pathway-grid"
                else ""
            )
            summary = (
                f"路径 {path_handoff['path_rank']} · 第 "
                f"{selected_step['step_index']} 步{automatic} · "
                f"{selected_step['reaction_text']}"
            )
        else:
            summary = (
                f"已选路径 {path_handoff['path_rank']}；"
                "点击黄色反应节点可查看事件证据。"
            )
        return (
            path_handoff,
            selected_step,
            summary,
            selected_step is None,
        )

    @app.callback(
        Output("pathway-evidence-grid", "data"),
        Output("pathway-evidence-grid", "columns"),
        Output("pathway-evidence-alert", "children"),
        Input("pathway-selected-path", "data"),
        Input("pathway-selected-step", "data"),
        Input("app-store", "data"),
    )
    def _validate_selected_pathway_step(
        selected_path,
        selected_step,
        app_store,
    ):
        if not selected_path:
            return [], [], "选择一条路径开始验证。"
        step_count = len(selected_path.get("reaction_keys") or [])
        if not selected_step:
            return (
                [],
                [],
                (
                    f"所选路径包含 {step_count} 步；请点击下方超图中的"
                    "黄色反应节点，逐步验证其时间事件。整条网络路径"
                    "本身不代表时间连续事件链。"
                ),
            )
        try:
            result = svc.validate_pathway_step_occurrences(
                (app_store or {}).get("artifacts") or {},
                selected_step,
                max_occurrences=20,
            )
        except svc.ServiceError as exc:
            return [], [], exc.message
        rows = list(result.get("rows") or [])
        evidence_level = str(result.get("evidence_level") or "network_only")
        if evidence_level == "rng_event":
            columns = _event_columns(rows)
        elif evidence_level == "route":
            columns = _columns_from_rows(
                rows,
                [
                    "occurrence_rank",
                    "evidence_source",
                    "start_frame",
                    "end_frame",
                    "frame_span",
                    "reaction_smiles",
                ],
            )
        else:
            columns = []
        prefix = (
            "该候选路径只有 1 步，不存在两步连续性；"
            if step_count == 1
            else f"当前验证第 {int(selected_step.get('step_index') or 0)} 步；"
        )
        return rows, columns, prefix + str(result.get("message") or "")

    @app.callback(
        Output("event-reaction-text", "value", allow_duplicate=True),
        Input("pathway-open-events-btn", "n_clicks"),
        State("pathway-selected-step", "data"),
        prevent_initial_call=True,
    )
    def _send_pathway_step_to_events(n_clicks, selected_step):
        if n_clicks is None or not selected_step:
            raise PreventUpdate
        reaction_text = str(selected_step.get("reaction_text") or "")
        if not reaction_text:
            raise PreventUpdate
        return reaction_text

    @app.callback(
        Output("pathway-json-download", "data"),
        Input("pathway-json-btn", "n_clicks"),
        State("pathway-store", "data"),
        prevent_initial_call=True,
    )
    def _download_pathway_json(n_clicks, payload):
        if n_clicks is None or not payload:
            raise PreventUpdate
        return dcc.send_string(
            json.dumps(
                pathway_document(payload),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "candidate_pathways.json",
        )

    @app.callback(
        Output("pathway-csv-download", "data"),
        Input("pathway-csv-btn", "n_clicks"),
        State("pathway-store", "data"),
        prevent_initial_call=True,
    )
    def _download_pathway_csv(n_clicks, payload):
        if n_clicks is None or not payload:
            raise PreventUpdate
        return dcc.send_string(
            pathway_csv_text(payload),
            "candidate_pathways.csv",
        )

    @app.callback(
        Output("pathway-store", "data", allow_duplicate=True),
        Output("pathway-context-store", "data", allow_duplicate=True),
        Output("pathway-selected-path", "data", allow_duplicate=True),
        Output("pathway-selected-step", "data", allow_duplicate=True),
        Output("pathway-grid", "selected_rows", allow_duplicate=True),
        Output("pathway-grid", "data", allow_duplicate=True),
        Output("pathway-cytoscape", "elements", allow_duplicate=True),
        Output("pathway-cytoscape", "tapNodeData", allow_duplicate=True),
        Output("pathway-selection-summary", "children", allow_duplicate=True),
        Output("pathway-open-events-btn", "disabled", allow_duplicate=True),
        Output("pathway-alert", "children", allow_duplicate=True),
        Output("pathway-terminal-summary", "children", allow_duplicate=True),
        Input("app-store", "data"),
        State("pathway-context-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_cross_context_pathway_state(
        app_store,
        pathway_context,
    ):
        reset_kind = _pathway_reset_trigger_kind(
            ctx,
            app_store,
            pathway_context,
        )
        if reset_kind == "dataset":
            return (
                None,
                None,
                None,
                None,
                [],
                [],
                [],
                None,
                "选择一条路径或一个反应节点。",
                True,
                "",
                [],
            )
        raise PreventUpdate

    # ── Batch comparison ────────────────────────────────────────────

    @app.callback(
        Output("batch-managed-selector", "options"),
        Output("batch-managed-store", "data"),
        Output("batch-managed-status", "children"),
        Input("app-store", "data"),
        Input("recent-datasets", "data"),
    )
    def _refresh_batch_managed_datasets(app_store, recent_records):
        catalog = _batch_managed_dataset_catalog(app_store, recent_records)
        options = catalog["options"]
        enabled_count = sum(not bool(option.get("disabled")) for option in options)
        if enabled_count:
            status = f"可选择 {enabled_count} 个已管理数据集；每个数据集作为一个独立条件组。"
        else:
            status = "暂无含 reactionabcd 的当前数据集；可前往管理数据加载，或使用下方目录扫描。"
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
        if triggered == "page-store" and (page_store or {}).get("page") != "batch-compare":
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
            )
        except Exception as exc:
            return [], [], None, dbc.Alert(
                f"扫描失败：{exc}",
                color="danger",
                className="py-1 px-2 mb-0",
            )
        groups = payload.get("groups") or []
        options = [
            {
                "label": f"{g['group_name']} ({g['n_replicates']} 个重复)",
                "value": g["group_name"],
            }
            for g in groups
        ]
        warnings = payload.get("warnings") or []
        status_text = (
            f"扫描完成：{payload.get('total_conditions', 0)} 个条件，"
            f"{payload.get('total_groups', 0)} 个条件组；已默认选择全部条件组。"
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
        return options, [option["value"] for option in options], payload, status

    @app.callback(
        Output("batch-selection-summary", "children"),
        Output("batch-compare-btn", "disabled"),
        Input("batch-managed-selector", "value"),
        Input("batch-condition-selector", "value"),
        State("batch-managed-store", "data"),
        State("batch-conditions-store", "data"),
    )
    def _summarize_batch_selection(
        managed_selected,
        scanned_selected,
        managed_payload,
        scanned_payload,
    ):
        try:
            requests = _build_batch_group_requests(
                managed_selected,
                managed_payload,
                scanned_selected,
                scanned_payload,
            )
        except svc.ServiceError as exc:
            return f"选择不可用：{exc.message}", True
        group_count = len(requests)
        replicate_count = sum(len(item.get("conditions") or []) for item in requests)
        if not group_count:
            return "请选择已管理数据集，或扫描并选择条件组。", True
        comparison_hint = "建议至少选择两个条件组。" if group_count < 2 else "可以开始对比。"
        return (
            f"已选择 {group_count} 个条件组、{replicate_count} 个重复实验；{comparison_hint}",
            False,
        )

    @app.callback(
        Output("batch-matrix-grid", "data"),
        Output("batch-matrix-grid", "columns"),
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
    ):
        empty_store = {"rows": [], "columns": [], "details": {}, "groups": []}
        if ctx.triggered_id != "batch-compare-btn":
            has_selection = bool(managed_selected or scanned_selected)
            return (
                [],
                [],
                _batch_empty_state(
                    "对比条件已变化" if has_selection else "选择条件组开始对比",
                    (
                        "确认选择后点击“对比”生成新的统计结果。"
                        if has_selection
                        else "可直接选择已管理数据集，也可扫描目录并选择自动识别的重复实验组。"
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
        }
        return (
            rows,
            columns,
            dbc.Alert(message, color="success", className="py-2 rs-batch-summary-alert"),
            store,
            {},
            False,
            {"display": "none"},
        )

    @app.callback(
        Output("batch-reaction-chart", "figure"),
        Output("batch-reaction-stats", "children"),
        Output("batch-detail-card", "style"),
        Input("batch-matrix-grid", "selected_row_ids"),
        State("batch-matrix-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_reaction_detail(selected_row_ids, grid_store):
        if not selected_row_ids:
            return go.Figure(), None, {"display": "none"}
        reaction_id = str(selected_row_ids[0])
        detail = ((grid_store or {}).get("details") or {}).get(reaction_id)
        if not isinstance(detail, dict):
            return go.Figure(), None, {"display": "none"}
        groups = detail.get("groups") or []
        group_names = [str(item.get("group_name") or "条件组") for item in groups]
        mean_values = [float(item.get("mean_tp") or 0) for item in groups]
        std_values = [float(item.get("std_tp") or 0) for item in groups]

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                name="组平均 TP",
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
                    name="单次重复",
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
            ci_lower = group.get("ci_95_lower", "-")
            ci_upper = group.get("ci_95_upper", "-")
            stat_cards.append(
                html.Div(
                    [
                        html.Div(str(group.get("group_name") or "条件组"), className="rs-batch-stat-title"),
                        html.Div(
                            f"检出 {group.get('detected_count', 0)}/{group.get('n_replicates', 0)} · "
                            f"检出率 {group.get('detection_rate', 0):.3f}",
                            className="rs-batch-stat-line",
                        ),
                        html.Div(
                            f"平均 TP {group.get('mean_tp', 0):.2f} ± {group.get('std_tp', 0):.2f}",
                            className="rs-batch-stat-line",
                        ),
                        html.Div(
                            f"平均净 TP {group.get('mean_net_tp', 0):.2f} · 95% CI [{ci_lower}, {ci_upper}]",
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
            content = svc.batch_comparison_to_csv(grid_store)
        except svc.ServiceError:
            raise PreventUpdate
        return {
            "content": content,
            "filename": "batch_comparison.csv",
            "type": "text/csv;charset=utf-8",
        }

# ── Directory browser helpers ───────────────────────────────────────


def _resolve_initial_browse_path(
    folder_input: str | None,
    *,
    candidate: dict[str, Any] | None = None,
    app_store: dict[str, Any] | None = None,
) -> str:
    """Determine the starting path for the directory browser.

    Prefer a selected candidate, then an explicit manual path, then the
    currently applied dataset.  This keeps reopening the picker independent
    of the optional manual-input control while still honoring a freshly typed
    path.  Invalid or unavailable values fall back to the first allowed root.
    """
    from pathlib import Path

    possible_inputs: list[str] = []
    selected = candidate if isinstance(candidate, dict) else {}
    for key in ("folder", "base"):
        value = str(selected.get(key) or "").strip()
        if value:
            possible_inputs.append(value)
    manual = str(folder_input or "").strip()
    if manual:
        possible_inputs.append(manual)
    applied = app_store if isinstance(app_store, dict) else {}
    for key in ("folder", "base"):
        value = str(applied.get(key) or "").strip()
        if value:
            possible_inputs.append(value)

    for possible in possible_inputs:
        try:
            resolved = svc.resolve_dataset_input(possible)
            return str(resolved["folder"])
        except svc.ServiceError:
            continue
    # A deployment may configure roots that exclude the service account's
    # home directory.  Start at the first permitted root in that case so the
    # browser opens successfully instead of immediately showing an error.
    for root in svc.ALLOWED_ROOTS:
        if root.is_dir():
            return str(root)
    return str(Path.home())


def _triggered_click_value() -> bool:
    """Ignore Dash's synthetic pattern-input reset events."""
    return bool((ctx.triggered or [{}])[0].get("value"))


def _pattern_trigger_type(triggered_id: Any) -> str:
    """Read Dash's pattern ID from either a dict or AttributeDict."""
    getter = getattr(triggered_id, "get", None)
    return str(getter("type") or "") if callable(getter) else ""


def _compact_browser_candidate(candidate: dict[str, Any]) -> dict[str, str]:
    """Keep browser selection state independent from index/status payloads."""
    return {
        "folder": str(candidate.get("folder") or ""),
        "base": str(candidate.get("base") or ""),
        "label": str(candidate.get("label") or ""),
    }


def _candidate_for_base(snapshot: dict[str, Any], base: str) -> dict[str, Any] | None:
    """Return an exact discovered candidate without trusting client state."""
    target = str(base or "")
    return next(
        (item for item in snapshot.get("datasets") or [] if item.get("base") == target),
        None,
    )


def _validated_dataset_target(
    candidate: dict[str, Any] | None,
    *,
    app_store: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve one client-side selection through the bounded browser snapshot.

    A partially populated candidate must fail as a unit instead of borrowing
    its missing field from the applied store.  The store is only a fallback
    when no candidate selection exists.
    """
    proposed = candidate if isinstance(candidate, dict) else {}
    has_candidate = any(
        str(proposed.get(key) or "").strip()
        for key in ("folder", "base")
    )
    selected = proposed if has_candidate else (
        app_store if isinstance(app_store, dict) else {}
    )
    folder = str(selected.get("folder") or "").strip()
    base = str(selected.get("base") or "").strip()
    if not folder or not base:
        raise svc.ServiceError(
            "请选择一个可用的数据集。",
            reason="missing_dataset",
        )
    snapshot = svc.browse_dataset_location(folder)
    actual = _candidate_for_base(snapshot, base)
    if actual is None:
        raise svc.ServiceError(
            "所选数据集已不存在，请重新选择。",
            reason="invalid_dataset_candidate",
        )
    return _compact_browser_candidate(actual)


def _build_dir_browser_snapshot_response(
    data: dict[str, Any],
    candidate: dict[str, str] | None,
    *,
    error: str = "",
) -> tuple:
    """Render one already-validated directory snapshot without rereading it."""
    return (
        data["current_path"],
        not bool(data.get("can_go_up")),
        _render_browser_current(data, candidate, error=error),
        _render_dir_browser_body(data, error=error),
        data["current_path"],
        candidate,
        candidate is None,
        no_update,
        no_update,
    )


def _build_dir_browser_error_response(path: str, message: str) -> tuple:
    """Render a browser error without retrying the failed directory read."""
    attempted = str(path or "")
    return (
        attempted,
        True,
        _render_browser_current(None, None, error=message, path=attempted),
        _render_dir_browser_error(message),
        no_update,
        None,
        True,
        no_update,
        no_update,
    )


def _build_dir_browser_response(
    path_str: str,
    recent_records: list[dict[str, Any]] | None,
    error: str = "",
) -> tuple:
    """Build a complete browser snapshot response without applying a dataset."""
    try:
        resolved = svc.resolve_dataset_input(path_str)
        data = svc.browse_dataset_location(resolved["folder"])
    except svc.ServiceError as exc:
        return _build_dir_browser_error_response(
            path_str,
            str(exc.message),
        )
    datasets = data.get("datasets") or []
    preferred_base = str(resolved.get("preferred_base") or "")
    actual = (
        _candidate_for_base(data, preferred_base)
        if preferred_base
        else (datasets[0] if len(datasets) == 1 else None)
    )
    candidate = _compact_browser_candidate(actual) if actual else None
    if preferred_base and actual is None:
        error = "当前目录未发现指定的数据集前缀。"
    return _build_dir_browser_snapshot_response(
        data,
        candidate,
        error=error,
    )


def _select_browser_candidate(
    folder: str,
    base: str,
    recent_records: list[dict[str, Any]] | None,
) -> tuple:
    """Read a directory once then set its explicitly selected candidate."""
    try:
        snapshot = svc.browse_dataset_location(folder)
    except svc.ServiceError as exc:
        return _build_dir_browser_error_response(
            folder,
            str(exc.message),
        )
    candidate = _candidate_for_base(snapshot, base)
    if candidate is None:
        return _build_dir_browser_snapshot_response(
            snapshot,
            None,
            error="该数据集已不存在，请从当前目录重新选择。",
        )
    compact = _compact_browser_candidate(candidate)
    return _build_dir_browser_snapshot_response(snapshot, compact)


def _render_browser_current(
    data: dict[str, Any] | None,
    candidate: dict[str, str] | None,
    *,
    error: str = "",
    path: str = "",
) -> Any:
    """Render compact current-directory status and candidate rows."""
    snapshot = data or {}
    current_path = str(snapshot.get("current_path") or path or "")
    datasets = snapshot.get("datasets") or []
    selected_base = str((candidate or {}).get("base") or "")
    if not datasets:
        candidates: Any = html.Div(
            "当前目录未发现 ReacNetGenerator 数据集，可继续进入子目录。",
            className="rs-browser-empty-line",
        )
    else:
        candidates = html.Div(
            [
            dbc.Button(
                [
                    html.Span(
                        "●" if item["base"] == selected_base else "○",
                        className="rs-browser-radio",
                    ),
                    html.Strong(item["label"], className="rs-browser-candidate-name"),
                    html.Span(
                        f"文件完整度 {item['completeness']}",
                        className="rs-browser-candidate-meta",
                    ),
                ],
                id={"type": "dir-browser-dataset", "base": item["base"]},
                color="light",
                size="sm",
                className=(
                    "rs-browser-candidate-row is-selected"
                    if item["base"] == selected_base
                    else "rs-browser-candidate-row"
                ),
            )
            for item in datasets
            ],
            className="rs-browser-candidate-list",
        )
    alert = html.Div(error, className="text-warning small") if error else None
    return html.Div(
        [
            html.Div(
                [
                    html.Span("当前目录", className="text-muted"),
                    html.Code(current_path, className="rs-browser-current-path"),
                ],
                className="rs-browser-current-line",
            ),
            alert,
            candidates,
        ]
    )


def _render_recent_datasets(records: list[dict[str, Any]] | None) -> Any:
    """Render valid recent records without trusting browser-local storage."""
    from pathlib import Path

    entries: list[Any] = []
    for record in svc.normalise_recent_datasets(records or []):
        folder = str(record.get("folder") or "")
        base = str(record.get("base") or "")
        try:
            available = bool(folder and base and svc.validate_browse_path(folder).is_dir())
        except svc.ServiceError:
            available = False
        label = str(record.get("label") or Path(base).name or folder)
        if available:
            entries.append(
                dbc.Button(
                    label,
                    id={"type": "dir-browser-recent-entry", "folder": folder, "base": base},
                    color="link",
                    size="sm",
                    className="rs-browser-recent-entry",
                )
            )
        else:
            entries.append(html.Span(f"{label}（不可用）", className="rs-browser-recent-unavailable"))
    if not entries:
        return None
    return html.Section([html.H6("最近加载"), html.Div(entries, className="rs-browser-recent-list")])


def _render_dir_browser_error(message: str) -> Any:
    """Render a recoverable error inside the directory-list section."""
    return html.Div(
        [html.Span("⚠ "), html.Span(message)],
        className="text-danger small py-2",
    )


def _render_dir_browser_body(data: dict[str, Any], error: str = "") -> Any:
    """Render only the subdirectory section for a browser snapshot."""
    subdirs: list[dict[str, Any]] = data.get("subdirs", [])
    if not subdirs:
        directory_list: Any = html.Div("当前目录没有子文件夹", className="rs-browser-empty-line")
    else:
        directory_list = html.Div(
            [
                dbc.Button(
                    [
                        html.Span("📁", className="rs-browser-folder-icon"),
                        html.Span(item.get("name", ""), className="rs-browser-folder-name"),
                        html.Span("›", className="rs-browser-chevron"),
                    ],
                    id={"type": "dir-browser-entry", "path": item["path"]},
                    color="light",
                    size="sm",
                    disabled=not bool(item.get("accessible", True)),
                    className="rs-browser-directory-entry",
                )
                for item in subdirs
            ],
            className="rs-browser-directory-list",
        )
    return directory_list


# ── Shared column factories ─────────────────────────────────────────


def _species_columns(query_kind: str = ""):
    if query_kind == "mass":
        return _dt_columns([
            {"field": "formula", "headerName": "候选分子式", "width": 120},
            {"field": "exact_mass", "headerName": "匹配精确质量", "width": 120, "type": "numericColumn"},
            {"field": "nominal_mass", "headerName": "匹配标称质量", "width": 110, "type": "numericColumn"},
            {"field": "mass_error", "headerName": "质量误差", "width": 100, "type": "numericColumn"},
            {"field": "ppm_error", "headerName": "误差 ppm", "width": 95, "type": "numericColumn"},
            {"field": "structure_count", "headerName": "结构数", "width": 85, "type": "numericColumn"},
            {"field": "smiles", "headerName": "代表 SMILES", "minWidth": 220},
            {"field": "tp_as_reactant", "headerName": "TP(反应物汇总)", "width": 125, "type": "numericColumn"},
            {"field": "tp_as_product", "headerName": "TP(产物汇总)", "width": 120, "type": "numericColumn"},
            {"field": "total_throughput", "headerName": "总通量汇总", "width": 110, "type": "numericColumn"},
        ])

    columns = [
        {"field": "formula", "headerName": "分子式", "width": 110},
        {"field": "smiles", "headerName": "SMILES", "flex": 2, "minWidth": 200},
        {"field": "exact_mass", "headerName": "精确质量", "width": 110, "type": "numericColumn"},
        {"field": "nominal_mass", "headerName": "标称质量", "width": 95, "type": "numericColumn"},
    ]
    columns.extend([
        {"field": "tp_as_reactant", "headerName": "TP(反应物)", "width": 105, "type": "numericColumn"},
        {"field": "tp_as_product", "headerName": "TP(产物)", "width": 100, "type": "numericColumn"},
        {"field": "total_throughput", "headerName": "总通量", "width": 100, "type": "numericColumn"},
        {"field": "n_consume_rxns", "headerName": "消耗反应", "width": 95, "type": "numericColumn"},
        {"field": "n_produce_rxns", "headerName": "生成反应", "width": 95, "type": "numericColumn"},
    ])
    return _dt_columns(columns)


def _reaction_columns(*, with_share: bool = False):
    cols = [
        {"field": "rank", "headerName": "#", "width": 70, "type": "numericColumn"},
        {"field": "reaction_formulas", "headerName": "反应式", "flex": 2, "minWidth": 240},
        {"field": "reaction_smiles", "headerName": "Reaction SMILES", "flex": 2, "minWidth": 260},
        {"field": "tp", "headerName": "TP", "width": 85, "type": "numericColumn"},
        {"field": "reverse_tp", "headerName": "Reverse", "width": 95, "type": "numericColumn"},
        {"field": "net_tp", "headerName": "Net", "width": 85, "type": "numericColumn"},
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


def _intermediate_columns(rows=None):
    preferred = [
        "rank",
        "class",
        "formula",
        "smiles",
        "score",
        "c_start",
        "c_max",
        "c_end",
        "start_ratio",
        "end_ratio",
        "peak_timestep",
        "peak_time_ps",
        "fwhm_ps",
        "tp_consume",
        "tp_produce",
        "net_production",
        "top_sources",
        "top_sinks",
    ]
    return _columns_from_rows(rows or [], preferred)


_EVENT_TABLE_COLUMNS = [
    {"field": "event_index", "headerName": "事件序号", "type": "numericColumn"},
    {"field": "event_id", "headerName": "事件 ID"},
    {"field": "timestep_index", "headerName": "事件区间", "type": "numericColumn"},
    {"field": "before_timestep", "headerName": "反应前 timestep", "type": "numericColumn"},
    {"field": "after_timestep", "headerName": "反应后 timestep", "type": "numericColumn"},
    {"field": "reactant", "headerName": "反应物"},
    {"field": "product", "headerName": "产物"},
    {"field": "atom_count", "headerName": "原子数", "type": "numericColumn"},
    {"field": "atom_ids", "headerName": "参与原子"},
    {"field": "association_status", "headerName": "原子关联"},
    {"field": "reactant_bonds", "headerName": "反应前键"},
    {"field": "product_bonds", "headerName": "反应后键"},
    {"field": "anchor_frame", "headerName": "锚点帧", "type": "numericColumn"},
]


def _event_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep browser table values scalar while retaining raw rows in the Store."""
    fields = [str(column["field"]) for column in _EVENT_TABLE_COLUMNS]
    table_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        event_id = str(raw.get("event_id") or f"event-{index + 1}")
        display = {
            field: raw.get(field) if raw.get(field) is not None else ""
            for field in fields
        }
        display["id"] = event_id
        table_rows.append(display)
    return table_rows


def _event_columns(rows=None):
    available = {
        key
        for row in (rows or [])
        for key in row
    }
    columns = [
        column
        for column in _EVENT_TABLE_COLUMNS
        if not available or column["field"] in available
    ]
    return _dt_columns(columns)


def _event_path_signature_columns():
    return _dt_columns(
        [
            {"field": "rank", "headerName": "#", "type": "numericColumn"},
            {"field": "signature_id", "headerName": "路径签名 ID"},
            {"field": "reaction_path", "headerName": "实际反应事件序列"},
            {"field": "occurrences", "headerName": "发生次数", "type": "numericColumn"},
            {"field": "atom_lineages", "headerName": "原子谱系支持", "type": "numericColumn"},
            {"field": "lineage_sets", "headerName": "谱系集合支持", "type": "numericColumn"},
            {"field": "replicate_support", "headerName": "重复支持数", "type": "numericColumn"},
            {"field": "reproduction_rate", "headerName": "跨重复复现率", "type": "numericColumn"},
            {"field": "median_timestep_span", "headerName": "中位 timestep 跨度", "type": "numericColumn"},
            {"field": "pure_h_cycle", "headerName": "纯氢循环"},
            {"field": "return_cycle", "headerName": "首尾往返"},
            {"field": "support_is_lower_bound", "headerName": "支持数为下界"},
        ]
    )


def _event_path_comparison_columns():
    return _dt_columns(
        [
            {"field": "replicate", "headerName": "重复"},
            {"field": "aggregate_reachable", "headerName": "网络可拼接候选", "type": "numericColumn"},
            {"field": "actual", "headerName": "轨迹实际路径", "type": "numericColumn"},
            {"field": "confirmed", "headerName": "候选且有整链实证", "type": "numericColumn"},
            {"field": "aggregate_only", "headerName": "候选但无整链实证", "type": "numericColumn"},
            {"field": "actual_only", "headerName": "实际发生但网络未收录", "type": "numericColumn"},
            {"field": "realization_rate", "headerName": "候选实证比例", "type": "numericColumn"},
            {"field": "complete", "headerName": "比较完整"},
        ]
    )


def _event_path_comparison_signature_columns():
    return _dt_columns(
        [
            {"field": "replicate", "headerName": "重复"},
            {"field": "classification", "headerName": "证据分类"},
            {"field": "signature_id", "headerName": "路径签名 ID"},
            {"field": "reaction_path", "headerName": "反应事件序列"},
        ]
    )


def _event_path_event_columns():
    return _dt_columns(
        [
            {"field": "step", "headerName": "节点", "type": "numericColumn"},
            {"field": "event_id", "headerName": "事件 ID"},
            {"field": "interval", "headerName": "事件区间", "type": "numericColumn"},
            {"field": "before_timestep", "headerName": "反应前 timestep", "type": "numericColumn"},
            {"field": "after_timestep", "headerName": "反应后 timestep", "type": "numericColumn"},
            {"field": "reaction", "headerName": "具体反应"},
            {"field": "participating_atoms", "headerName": "参与原子 ID"},
        ]
    )


def _event_path_time_columns():
    return _dt_columns(
        [
            {"field": "edge", "headerName": "相邻边", "type": "numericColumn"},
            {"field": "samples", "headerName": "样本数", "type": "numericColumn"},
            {"field": "interval_min", "headerName": "区间差 min", "type": "numericColumn"},
            {"field": "interval_median", "headerName": "区间差 median", "type": "numericColumn"},
            {"field": "interval_mean", "headerName": "区间差 mean", "type": "numericColumn"},
            {"field": "interval_max", "headerName": "区间差 max", "type": "numericColumn"},
            {"field": "idle_min", "headerName": "空闲 timestep min", "type": "numericColumn"},
            {"field": "idle_median", "headerName": "空闲 timestep median", "type": "numericColumn"},
            {"field": "idle_mean", "headerName": "空闲 timestep mean", "type": "numericColumn"},
            {"field": "idle_max", "headerName": "空闲 timestep max", "type": "numericColumn"},
            {"field": "anchor_min", "headerName": "锚点差 min", "type": "numericColumn"},
            {"field": "anchor_median", "headerName": "锚点差 median", "type": "numericColumn"},
            {"field": "anchor_mean", "headerName": "锚点差 mean", "type": "numericColumn"},
            {"field": "anchor_max", "headerName": "锚点差 max", "type": "numericColumn"},
        ]
    )


def _event_path_edge_columns():
    return _dt_columns(
        [
            {"field": "edge", "headerName": "边", "type": "numericColumn"},
            {"field": "from_event_id", "headerName": "前一事件"},
            {"field": "to_event_id", "headerName": "后一事件"},
            {"field": "molecule_instances", "headerName": "精确分子实例（物种 @ 原子集）"},
            {"field": "carrier_atom_ids", "headerName": "携带原子 ID"},
            {"field": "interval_gap", "headerName": "区间差", "type": "numericColumn"},
            {"field": "idle_timestep_gap", "headerName": "空闲 timestep", "type": "numericColumn"},
            {"field": "anchor_timestep_gap", "headerName": "锚点 timestep 差", "type": "numericColumn"},
        ]
    )


def _event_path_summary_cards(report: dict[str, Any]) -> None:
    return None


def _event_path_summary_explanation(report: dict[str, Any]) -> list[Any]:
    """Turn event-path counters into one plain-language reading sentence."""
    summary = report.get("summary") or {}
    comparison = report.get("comparison") or {}
    event_nodes = sum(
        int(item.get("event_node_count") or 0)
        for item in report.get("sources") or []
    )
    occurrences = int(summary.get("actual_path_occurrence_count") or 0)
    signatures = int(summary.get("actual_path_signature_count") or 0)
    lineages = int(
        summary.get("independent_atom_lineage_support_count") or 0
    )
    parts: list[Any] = [
        html.Strong("本次结果这样读："),
        html.Span(
            f"{event_nodes:,} 个具体事件是可连接的原始节点；其中找到了 "
            f"{occurrences:,} 次符合条件的实际事件链。"
        ),
        html.Span(
            f"这些具体发生按反应序列合并为 {signatures:,} 种路径签名，"
            f"由 {lineages:,} 个去重原子谱系支持。"
        ),
    ]
    realization = comparison.get("realization_rate")
    if realization is not None:
        parts.append(
            html.Span(
                f"网络候选实证比例 {float(realization):.2%} 表示："
                "聚合网络能拼出的同长度路径签名中，有这么多比例找到了整链实证；"
                "它不是产率、转化率或事件占比。"
            )
        )
    else:
        parts.append(html.Span("当前没有可用的聚合网络对照，因此不计算候选实证比例。"))
    return parts


def _event_path_comparison_figure(report: dict[str, Any]) -> go.Figure:
    rows = svc.event_path_comparison_rows(report or {})
    figure = go.Figure()
    if not rows:
        figure.add_annotation(
            text="运行真实事件路径分析后显示聚合/实际对照。",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"color": "#64748b", "size": 13},
        )
    else:
        replicate_labels = [str(row["replicate"]) for row in rows]
        series = [
            ("网络可拼接候选", "aggregate_reachable", "#94a3b8"),
            ("候选但无整链实证", "aggregate_only", "#cbd5e1"),
            ("候选且有整链实证", "confirmed", "#16a34a"),
            ("实际发生但网络未收录", "actual_only", "#f59e0b"),
        ]
        for name, field, color in series:
            figure.add_bar(
                name=name,
                x=replicate_labels,
                y=[int(row.get(field) or 0) for row in rows],
                marker_color=color,
                hovertemplate=f"{name}: %{{y:,}}<extra>%{{x}}</extra>",
            )
    figure.update_layout(
        height=300,
        margin={"l": 48, "r": 20, "t": 20, "b": 48},
        barmode="group",
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        legend={"orientation": "h", "y": 1.12, "x": 0},
        xaxis={"title": "重复实验", "gridcolor": "#eef2f7"},
        yaxis={"title": "路径签名数", "gridcolor": "#eef2f7", "rangemode": "tozero"},
    )
    return figure


def _pathway_columns():
    return _dt_columns(
        [
            {"field": "rank", "headerName": "#", "type": "numericColumn"},
            {"field": "formula_chain", "headerName": "分子式路径"},
            {"field": "smiles_chain", "headerName": "SMILES 路径"},
            {"field": "terminal_products", "headerName": "末步全部物种"},
            {"field": "small_fragments", "headerName": "已见小分子碎片"},
            {"field": "termination_label", "headerName": "终点状态"},
            {"field": "path_score", "headerName": "路径分数", "type": "numericColumn"},
            {"field": "weakest_step_score", "headerName": "最弱步分数", "type": "numericColumn"},
            {"field": "depth", "headerName": "深度", "type": "numericColumn"},
            {"field": "evidence_badge", "headerName": "当前证据层级"},
        ]
    )


def _pathway_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in payload.get("paths") or []:
        steps = path.get("steps") or []
        step_scores = [
            float(step.get("score"))
            for step in steps
            if step.get("score") is not None
        ]
        rows.append(
            {
                "rank": int(path.get("rank") or 0),
                "formula_chain": " → ".join(
                    str(value) for value in path.get("formulas") or []
                ),
                "smiles_chain": " → ".join(
                    str(value) for value in path.get("species") or []
                ),
                "terminal_products": " + ".join(
                    str(item.get("formula") or item.get("smiles") or "")
                    for item in path.get("terminal_products") or []
                ),
                "small_fragments": "、".join(
                    str(item.get("formula") or item.get("smiles") or "")
                    for item in path.get("small_fragments") or []
                ) or "—",
                "termination_label": {
                    "small_molecule_goal": "已到达小分子",
                    "no_positive_continuation": "无正净后继",
                    "depth_limit": "达到深度上限",
                    "search_truncated": "搜索被截断",
                }.get(
                    str(path.get("termination_reason") or ""),
                    "未标注",
                ),
                "path_score": path.get("score"),
                "weakest_step_score": min(step_scores) if step_scores else None,
                "depth": len(steps),
                "evidence_badge": (
                    "各步可查事件（未证整链）"
                    if path.get("evidence_status") == "evidence_linked"
                    else "网络候选（未证整链）"
                ),
            }
        )
    return rows


def _pathway_terminal_cards(payload: dict[str, Any]) -> list[Any]:
    paths = payload.get("paths") or []
    if not paths:
        return []
    labels = {
        "small_molecule_goal": "已到达小分子目标",
        "no_positive_continuation": "无正净通量后继",
        "depth_limit": "仅到达当前深度上限",
        "search_truncated": "搜索上限内的部分结果",
    }
    cards: list[Any] = [
        html.Div(
            [
                html.H6("路线终点与末步全部物种", className="mb-1"),
                html.P(
                    "这里同时显示焦点终点和末步反应的全部物种；"
                    "“达到深度上限”不等于真实终产物。",
                    className="rs-step-note mb-0",
                ),
            ],
            className="rs-pathway-terminal-heading",
        )
    ]
    for path in paths[:20]:
        products = path.get("terminal_products") or []
        product_cards = []
        for item in products:
            classes = "rs-pathway-terminal-species"
            if item.get("is_small_carbon_fragment"):
                classes += " is-small-fragment"
            product_cards.append(
                html.Div(
                    [
                        html.Img(
                            src=str(item.get("structure_url") or ""),
                            alt=str(item.get("formula") or "终点物种"),
                        ),
                        html.Strong(str(item.get("formula") or "?")),
                        html.Code(str(item.get("smiles") or "")),
                        html.Span(
                            (
                                f"C{int(item.get('carbon_count') or 0)}"
                                if int(item.get("carbon_count") or 0) > 0
                                else "无碳物种"
                            )
                        ),
                    ],
                    className=classes,
                )
            )
        cards.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Strong(f"路径 {int(path.get('rank') or 0)}"),
                            html.Span(
                                labels.get(
                                    str(path.get("termination_reason") or ""),
                                    "终点未标注",
                                ),
                                className="rs-pathway-terminal-badge",
                            ),
                        ],
                        className="rs-pathway-terminal-meta",
                    ),
                    html.Div(
                        product_cards
                        or [html.Span("末步物种不可用。")],
                        className="rs-pathway-terminal-products",
                    ),
                ],
                className="rs-pathway-terminal-route",
            )
        )
    return cards


def _batch_empty_state(
    title: str = "选择条件组开始对比",
    hint: str = "可直接选择已管理数据集，也可扫描目录并选择自动识别的重复实验组。",
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
            }
        )
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
                "reaction_ready": candidate.get("reaction_ready"),
            }
        )
    options = []
    for dataset in datasets:
        prefix = "当前 · " if dataset["current"] else "最近 · "
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
                "已管理数据集选择已过期，请重新选择",
                reason="stale_managed_selection",
            )
        label = str(dataset.get("label") or Path(str(dataset.get("base") or "")).name)
        requests.append(
            {
                "group_name": unique_group_name(label),
                "conditions": [
                    {
                        "name": label,
                        "label": label,
                        "folder": dataset.get("folder"),
                        "base": dataset.get("base"),
                        "replicate": 1,
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
    for selected_group in scanned_selected or []:
        group = scanned_groups.get(str(selected_group))
        if group is None:
            raise svc.ServiceError(
                "扫描条件组选择已过期，请重新扫描",
                reason="stale_scanned_selection",
            )
        conditions = []
        for condition_name in group.get("conditions") or []:
            condition = scanned_conditions.get(str(condition_name))
            if condition is None:
                raise svc.ServiceError(
                    f"条件组 {selected_group} 的扫描结果不完整，请重新扫描",
                    reason="incomplete_scanned_group",
                )
            conditions.append(
                {
                    "name": condition.get("name"),
                    "label": condition.get("name"),
                    "folder": condition.get("folder"),
                    "reaction_file": condition.get("reaction_file"),
                    "temperature": condition.get("temperature"),
                    "o2_ratio": condition.get("o2_ratio"),
                    "pressure": condition.get("pressure"),
                    "replicate": condition.get("replicate"),
                }
            )
        requests.append(
            {
                "group_name": unique_group_name(str(group.get("group_name") or selected_group)),
                "temperature": group.get("temperature"),
                "o2_ratio": group.get("o2_ratio"),
                "pressure": group.get("pressure"),
                "conditions": conditions,
            }
        )
    return requests


def _columns_from_rows(rows: list[dict[str, Any]], preferred: list[str]):
    seen = set()
    fields: list[str] = []
    if rows:
        all_keys = {key for row in rows for key in row.keys()}
        for key in preferred:
            if key in all_keys and key not in seen:
                seen.add(key)
                fields.append(key)
        for key in sorted(all_keys):
            if key not in seen:
                seen.add(key)
                fields.append(key)
    else:
        fields = list(preferred[:8])

    cols = []
    for field in fields:
        is_num = field.endswith("_count") or field.endswith("_tp") or field in {
            "rank",
            "event_index",
            "candidate_index",
            "anchor_frame",
            "window_start",
            "window_end",
            "n_window_frames",
            "count_at_frame",
            "delta_from_prev",
            "score",
            "c_start",
            "c_max",
            "c_end",
            "start_ratio",
            "end_ratio",
            "peak_timestep",
            "peak_time_ps",
            "fwhm_ps",
            "net_production",
        }
        cols.append(
            {
                "field": field,
                "headerName": field,
                "minWidth": 120 if field not in {"smiles", "reaction_smiles", "top_sources", "top_sinks"} else 220,
                "flex": 2 if field in {"smiles", "reaction_smiles", "top_sources", "top_sinks", "route_context_atom_ids"} else 1,
                **({"type": "numericColumn"} if is_num else {}),
            }
        )
    return _dt_columns(cols)


def _dt_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for col in columns:
        field = str(col.get("field") or col.get("id") or "")
        if not field:
            continue
        dtype = "numeric" if col.get("type") == "numericColumn" else "text"
        out.append({"id": field, "name": str(col.get("headerName") or col.get("name") or field), "type": dtype})
    return out


# ── Helpers ─────────────────────────────────────────────────────────


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _render_artifacts(artifacts: dict[str, str]) -> Any:
    labels = {
        "reaction": "Reaction",
        "species": "Species",
        "trajectory": "Trajectory",
        "route": "Route",
    }
    rows: list[Any] = []
    ready = 0
    for key, label in labels.items():
        path = artifacts.get(key)
        if path:
            ready += 1
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
        else:
            rows.append(
                html.Div(
                    [
                        html.Span(label, className="rs-artifact-label"),
                        html.Span("缺失", className="rs-artifact-name is-missing"),
                    ],
                    className="rs-artifact-row",
                )
            )
    return html.Details(
        [
            html.Summary(f"基础文件与路径 · {ready}/{len(labels)} 已找到"),
            html.Div(rows, className="rs-artifact-list"),
        ],
        className="rs-artifact-details",
    )


def _empty_plotly_figure(message: str) -> Any:
    import plotly.graph_objects as go

    figure = go.Figure()
    if message:
        figure.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
    figure.update_layout(template="plotly_white", margin={"l": 30, "r": 20, "t": 45, "b": 30})
    return figure


def _composition_trend_figure(payload: dict[str, Any]) -> Any:
    import plotly.graph_objects as go

    x_name = str(payload.get("x_name") or "Time")
    rows = payload.get("carbon_skeleton_rows") or []
    if not rows:
        return _empty_plotly_figure("没有可显示的碳骨架数据")
    styles = {
        "参考物种": {"color": "#111827", "dash": "solid", "width": 3.2},
        "C1": {"color": "#2563eb", "dash": "solid", "width": 2.2},
        "C2": {"color": "#0f766e", "dash": "solid", "width": 2.2},
        "C3": {"color": "#7c3aed", "dash": "solid", "width": 2.2},
        "C4": {"color": "#ca8a04", "dash": "solid", "width": 2.2},
        "C5": {"color": "#dc2626", "dash": "solid", "width": 2.2},
    }
    names = list(dict.fromkeys(str(row["series"]) for row in rows))
    figure = go.Figure()
    for index, name in enumerate(names):
        series = sorted((row for row in rows if str(row["series"]) == name), key=lambda row: float(row["x"]))
        style = styles.get(name)
        if style is None and name.endswith(" 其他物种"):
            style = {"color": "#64748b", "dash": "dash", "width": 2.5}
        if style is None:
            style = {"color": "#667085", "dash": "dot", "width": 1.8}
        figure.add_trace(
            go.Scatter(
                x=[row["x"] for row in series],
                y=[row["count"] for row in series],
                mode="lines",
                name=name,
                line=style,
                customdata=[[int(row["timestep"]), name] for row in series],
                hovertemplate=(
                    f"{name}<br>{x_name}: %{{x}}<br>物种数量: %{{y}}"
                    "<br><b>点击查看代表物种</b><extra></extra>"
                ),
            )
        )
    figure.update_yaxes(title_text="物种数量", rangemode="tozero", gridcolor="#e6ebf2")
    figure.update_xaxes(title_text=x_name, gridcolor="#eef2f6", zeroline=False)
    figure.update_layout(
        title={
            "text": "碳数分布随时间变化<br><sup>点击任一曲线，查看该时间点的代表物种</sup>",
            "x": 0.01,
        },
        template="plotly_white",
        height=520,
        autosize=True,
        margin={"l": 58, "r": 34, "t": 72, "b": 52},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        hovermode="closest",
        clickmode="event+select",
        uirevision="carbon-minimal",
    )
    return figure


def _composition_highlights(payload: dict[str, Any]) -> Any:
    from dash import html

    meta = payload.get("meta") or {}
    summary = payload.get("summary") or {}
    filters = payload.get("filters") or {}
    chlorine_labels = {
        "all": "全部",
        "chlorinated": "含氯",
        "unchlorinated": "不含氯",
    }
    oxygen_labels = {
        "all": "全部",
        "oxygenated": "含氧",
        "unoxygenated": "不含氧",
    }
    items = [
        ("索引时间点", meta.get("source_timepoints")),
        ("绘图采样点", meta.get("sampled_timepoints")),
        ("索引查询", f"{meta.get('query_seconds')} s" if meta.get("query_seconds") is not None else None),
        ("总耗时", f"{meta.get('analysis_seconds')} s" if meta.get("analysis_seconds") is not None else None),
        ("氯状态", chlorine_labels.get(str(filters.get("chlorine_state") or "all"))),
        ("氧状态", oxygen_labels.get(str(filters.get("oxygen_state") or "all"))),
        ("Timestep", f"{summary.get('timestep_ps')} ps" if summary.get("timestep_ps") is not None else None),
        ("参考物种", summary.get("reference_formula") or summary.get("reference_smiles")),
    ]
    return [
        html.Span([html.Strong(label), html.Span(_fmt_num(value))], className="rs-stat-chip")
        for label, value in items
        if value not in (None, "")
    ]


def _wrap_svg_doc(svg: str) -> str:
    """Wrap an SVG string in a full HTML document with reset CSS and viewBox fix."""
    import re

    # Ensure the SVG has a viewBox attribute
    if "viewBox" not in svg:
        w_match = re.search(r'width=["\']?(\d+)', svg)
        h_match = re.search(r'height=["\']?(\d+)', svg)
        if w_match and h_match:
            w, h = w_match.group(1), h_match.group(1)
            svg = svg.replace("<svg", f'<svg viewBox="0 0 {w} {h}"', 1)

    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\"><style>\n"
        "html,body{margin:0;padding:0;overflow:hidden;width:100%;height:100%}\n"
        "svg{max-width:100%;max-height:100%;display:block;margin:0 auto}\n"
        "</style></head><body>\n"
        + svg +
        "\n</body></html>"
    )
