"""Dash callback registration for ReacNet Scope WebUI V1.

All callbacks are registered in ``register_callbacks(app)``.  Each callback
delegates to ``reacnet_scope.services`` for data operations and never
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

from reacnet_scope.pathway_export import pathway_csv_text, pathway_document
from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope import services as svc
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
    "element-distribution": ("species", ".species"),
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
        "source_revision": {},
        "context_state": "none",
        "label": "未选择",
        "capabilities": {},
        "readiness": {},
        "artifacts": {},
        "selected_smiles": "",
        "selected_formula": "",
        "selected_species_source": "",
        "inputs_pending": False,
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
            "建立元素分布索引",
            "启用元素分布演化分析和代表物种下钻。",
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
    if payload.get("workspace_resolved") is False:
        alert = dbc.Alert(
            "无法为当前数据集确定 Dataset Workspace；请检查数据集路径和访问权限。",
            color="warning",
            className="py-2 mb-0",
        )
    elif payload.get("workspace_writable") is False:
        alert = dbc.Alert(
            "Dataset Workspace 不可写；请检查数据集目录，或由管理员配置集中位置。",
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
                    html.Span("数据集 ID"),
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
    items = {
        "basic": ("基础分析文件", payload.get("basic") or {}),
        "event": ("事件索引", payload.get("events") or {}),
        "trajectory": ("轨迹帧索引", payload.get("trajectory") or {}),
        "composition": ("元素分布索引", payload.get("composition") or {}),
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
        "meta": workspace_meta,
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
        Output("page-element-distribution", "className"),
        Output("page-data-management", "className"),
        Output("page-batch-compare", "className"),
        Output("nav-species", "className"),
        Output("nav-reactions", "className"),
        Output("nav-evolution", "className"),
        Output("nav-events", "className"),
        Output("nav-trajectory", "className"),
        Output("nav-intermediate", "className"),
        Output("nav-pathway", "className"),
        Output("nav-element-distribution", "className"),
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
        Input("nav-element-distribution", "n_clicks"),
        Input("nav-data-management", "n_clicks"),
        Input("data-open-batch-compare-btn", "n_clicks"),
        Input("data-pick-btn", "n_clicks"),
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
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input("dataset-switch-navigation", "data"),
        State("page-store", "data"),
    )
    def _navigate(*_args):
        triggered_id = ctx.triggered_id
        stored_state = (_args[-1] or {}) if _args else {}
        stored_page = stored_state.get("page")
        switch_navigation = _args[-2] if len(_args) >= 2 else {}
        if triggered_id == "dataset-switch-navigation":
            page_id = str((switch_navigation or {}).get("page") or stored_page)
        elif triggered_id == "dir-browser-cancel-btn":
            page_id = str(
                ((stored_state.get("dataset_return") or {}).get("page"))
                or "data-management"
            )
        elif triggered_id in {
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
            "data-pick-btn",
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
        if (
            triggered_id in {
                "data-pick-btn",
                "open-data-modal",
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
            and triggered_id == "data-pick-btn"
            and stored_state.get("dataset_return")
        ):
            page_state["dataset_return"] = dict(stored_state["dataset_return"])
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
        inputs_pending = bool((app_store or {}).get("inputs_pending"))
        invalidated = set((app_store or {}).get("invalidated_artifacts") or [])

        def ready_status(message: str) -> tuple[str, str]:
            if inputs_pending:
                message = f"{message} · 保留的查询输入尚未在当前数据集运行"
            return message, "rs-page-status is-ready"

        def stale_status(label: str) -> tuple[str, str]:
            return (
                f"{label} 的源修订已变化；采用新修订后可继续",
                "rs-page-status is-blocked",
            )

        if page_id == "data-management":
            label = str((app_store or {}).get("label") or "").strip()
            if (app_store or {}).get("context_state") == "revision-changed":
                return (
                    f"当前数据集：{label} · 源修订已变化",
                    "rs-page-status is-blocked",
                )
            if label:
                return ready_status(f"当前数据集：{label}")
            return "尚未加载数据集", "rs-page-status is-independent"
        if page_id == "batch-compare":
            reaction_ready = bool(((app_store or {}).get("artifacts") or {}).get("reaction"))
            if reaction_ready:
                return "当前数据集可加入对比", "rs-page-status is-ready"
            return "可扫描目录或从数据管理加载", "rs-page-status is-independent"
        if page_id == "events":
            if invalidated & {"timeline", "reactionevent", "molecules"}:
                return stale_status("事件证据")
            artifacts = (app_store or {}).get("artifacts") or {}
            event_ready = bool(
                artifacts.get("reactionevent") and artifacts.get("molecules")
            )
            return (
                ready_status("RNG 事件输出已就绪")
                if event_ready
                else ("需要 reactionevent.csv + molecules.csv", "rs-page-status is-blocked")
            )
        if page_id == "pathway" and pathway_tab == "concrete-event-paths":
            if invalidated & {"timeline", "reactionevent", "molecules"}:
                return stale_status("事件路径证据")
            artifacts = (app_store or {}).get("artifacts") or {}
            event_ready = bool(
                artifacts.get("reactionevent") and artifacts.get("molecules")
            )
            return (
                ready_status("事件轨迹证据已就绪")
                if event_ready
                else (
                    "需要 reactionevent.csv + molecules.csv",
                    "rs-page-status is-blocked",
                )
            )
        if page_id == "trajectory":
            if "trajectory" in invalidated:
                return stale_status("轨迹证据")
            readiness = (app_store or {}).get("readiness") or {}
            trajectory_ready = bool(
                (readiness.get("trajectory_evidence") or {}).get("ready")
            )
            return (
                ready_status("轨迹帧索引已就绪")
                if trajectory_ready
                else ("需要轨迹文件与帧索引", "rs-page-status is-blocked")
            )
        artifact_key, artifact_label = PAGE_DATA_REQUIREMENTS.get(
            page_id,
            ("", ""),
        )
        artifacts = (app_store or {}).get("artifacts") or {}
        if artifact_key and artifact_key in invalidated:
            return stale_status(artifact_label or "当前能力")
        if artifact_key and artifacts.get(artifact_key):
            if page_id == "pathway":
                return ready_status("聚合反应网络已就绪")
            return ready_status(f"{artifact_label} 已就绪")
        return f"需要 {artifact_label or '数据文件'}", "rs-page-status is-blocked"

    @app.callback(
        Output("rxn-search-btn", "disabled"),
        Output("pathway-search-btn", "disabled"),
        Output("inter-search-btn", "disabled"),
        Output("evolution-search-btn", "disabled"),
        Output("element-distribution-search-btn", "disabled"),
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
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Input("species-search-btn", "n_clicks"),
        Input("rxn-search-btn", "n_clicks"),
        Input("inter-search-btn", "n_clicks"),
        Input("evolution-search-btn", "n_clicks"),
        Input("element-distribution-search-btn", "n_clicks"),
        Input("event-rxn-btn", "n_clicks"),
        Input("event-path-run-btn", "n_clicks"),
        Input("pathway-search-btn", "n_clicks"),
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
        return {
            "token": f"validation-failed-{time.time_ns()}",
            "target": "data-load-feedback",
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
        readiness = current.get("readiness") or {}
        return (
            label,
            label,
            "基础 {} · 事件 {} · 轨迹 {}".format(
                "就绪" if (readiness.get("basic_analysis") or {}).get("ready") else "未就绪",
                "就绪" if (readiness.get("event_search") or {}).get("ready") else "未就绪",
                "就绪" if (readiness.get("trajectory_evidence") or {}).get("ready") else "未就绪",
            ),
            "rs-badge",
        )

    @app.callback(
        Output("data-overview-view", "className"),
        Output("data-browser-view", "className"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("nav-data-management", "n_clicks"),
        Input("data-pick-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "index": ALL}, "n_clicks"),
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

    app.clientside_callback(
        """
        function(nClicks) {
            if (!nClicks) {
                return window.dash_clientside.no_update;
            }
            return "";
        }
        """,
        Output("dir-browser-filter-input", "value"),
        Input("dir-browser-filter-clear-btn", "n_clicks"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("data-candidate-summary", "children"),
        Output("data-scan-status", "children"),
        Output("data-artifacts", "children"),
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
            if current_label:
                summary = html.Div(
                    [
                        html.Span(className="rs-current-dataset-dot"),
                        html.Strong(current_label),
                    ],
                    className="rs-current-dataset-name",
                )
                status_text = "Current Dataset · Analysis Capability 状态按下方项目分别显示"
            else:
                summary = html.Strong("尚无 Current Dataset")
                status_text = "请选择 Dataset Candidate，并显式执行“使用此数据集”。"
            return (
                summary,
                status_text,
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
                "Dataset Candidate 验证失败；Current Dataset 未改变。",
                _render_artifacts({}),
            )
        except Exception:
            return (
                dbc.Alert(
                    "Dataset Candidate 暂时无法验证。Current Dataset 已保留；请重试或重新选择。",
                    color="danger",
                    className="py-2",
                ),
                "Dataset Candidate 验证失败；Current Dataset 未改变。",
                _render_artifacts({}),
            )
        dataset = status.get("dataset") or {}
        selected_base = str(dataset.get("selected_base") or "")
        if selected_base != base:
            return (
                dbc.Alert("所选数据集已不存在，请重新选择。", color="danger", className="py-2"),
                "所选数据集已不存在。",
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
            "Dataset Candidate 的有限元数据已检查；使用此数据集前不会改变 Current Dataset。",
            artifact_html,
        )

    @app.callback(
        Output("data-current-refresh-btn", "children"),
        Output("data-current-refresh-btn", "color"),
        Output("data-current-refresh-btn", "outline"),
        Output("data-current-refresh-btn", "disabled"),
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
                    "更新当前数据集状态"
                    if context_state == "revision-changed"
                    else "刷新状态"
                ),
                "secondary",
                True,
                True,
                "等待当前分析完成",
                True,
            )

        if validating:
            apply_label = "正在验证…"
            apply_disabled = True
        elif switch_state == "failed" and has_candidate:
            apply_label = "重试验证"
            apply_disabled = False
        elif same_revision or (
            switch_state == "succeeded" and not is_different_candidate
        ):
            apply_label = "当前正在使用"
            apply_disabled = True
        else:
            apply_label = "使用此数据集"
            apply_disabled = not has_candidate

        if context_state == "revision-changed":
            refresh_label = "更新当前数据集状态"
            if is_different_candidate:
                return (
                    refresh_label,
                    "secondary",
                    True,
                    validating,
                    apply_label,
                    apply_disabled,
                )
            return (
                refresh_label,
                "primary",
                False,
                validating,
                apply_label,
                True,
            )
        return (
            "刷新状态",
            "secondary",
            True,
            not bool(current.get("dataset_id")) or validating,
            apply_label,
            apply_disabled,
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
                "rs-index-global-state is-partial", "状态读取失败",
                "", "", "", "", "", "",
                True, True, True, False,
                "rs-index-action", "rs-index-action", "rs-index-action",
            )
        except Exception as exc:
            error = f"读取准备状态失败: {exc}"
            return (
                "", "", "", "", "", error, "", "状态不可用",
                "rs-index-global-state is-partial", "状态读取失败",
                "", "", "", "", "", "",
                True, True, True, False,
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
        status_alert = rendered["alert"]
        if isinstance(cancel_result, dict) and cancel_result:
            status_alert = dbc.Alert(
                str(cancel_result.get("message") or "取消请求已提交。"),
                color="info" if cancel_result.get("ok") else "danger",
                className="py-2 mb-0",
            )
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
            status_alert,
            rendered["next_action"],
            rendered["global_status"],
            rendered["global_class"],
            rendered["refresh_label"],
            payload.get("event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
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
                    f"已启动 {labels[kind]}后台任务；可在上方查看检查点进度。",
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
                f"Preparation Task 失败: {exc}",
                color="danger",
                className="py-2 mb-0",
            )

        status = result.get("status") or {}
        if result.get("existing_task"):
            return dbc.Alert(
                "同类 Preparation Task 已在运行；继续显示现有任务进度。",
                color="info",
                className="py-2 mb-0",
            )
        if result.get("canceled"):
            return dbc.Alert(
                "Preparation Task 已取消；最近检查点已保留。",
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
        State("dataset-browser-candidate", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _cancel_preparation_task(n_clicks, candidate, app_store):
        if not n_clicks:
            raise PreventUpdate
        try:
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
        Input("dir-browser-path-input", "n_submit"),
        Input("dir-browser-go-btn", "n_clicks"),
        Input({"type": "dir-browser-root", "index": ALL}, "n_clicks"),
        Input({"type": "dir-browser-breadcrumb", "index": ALL}, "n_clicks"),
        Input({"type": "dir-browser-entry", "name": ALL}, "n_clicks"),
        Input("dir-browser-back-btn", "n_clicks"),
        Input({"type": "dir-browser-dataset", "name": ALL}, "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "index": ALL}, "n_clicks"),
        Input("dir-browser-filter-input", "value"),
        State("dir-browser-path", "data"),
        State("dir-browser-path-input", "value"),
        State("data-folder-input", "value"),
        State("dataset-browser-candidate", "data"),
        State("recent-datasets", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _handle_dir_browser(
        pick_clicks,
        manual_dataset_input,
        path_submit,
        go_clicks,
        _root_clicks,
        _breadcrumb_clicks,
        _entry_clicks,
        back_clicks,
        _dataset_clicks,
        _recent_clicks,
        browser_filter,
        current_path,
        path_input,
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
            return _build_dir_browser_response(
                start_path,
                current_path=current_path,
                candidate=candidate,
                app_store=app_store,
                filter_text=browser_filter,
            )

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
        if (
            isinstance(triggered_id, str)
            and triggered_id in {"dir-browser-path-input", "dir-browser-go-btn"}
        ):
            target = (path_input or "").strip()
            if not target:
                return _recover_browser_error(
                    current_path,
                    candidate,
                    reason="empty_path",
                    filter_text=browser_filter,
                )
            return _build_dir_browser_response(
                target,
                current_path=current_path,
                candidate=candidate,
                app_store=app_store,
                filter_text=browser_filter,
            )

        # --- FILTER CURRENT LOCATION --------------------------------
        if triggered_id == "dir-browser-filter-input":
            if not str(current_path or "").strip():
                raise PreventUpdate
            return _refresh_browser_location(
                current_path,
                candidate,
                filter_text=browser_filter,
            )

        # --- NAVIGATE TO ALLOWED ROOT -------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-root":
            if not _triggered_click_value():
                raise PreventUpdate
            target = _allowed_root_for_index(triggered_id.get("index"))
            if target is None:
                return _recover_browser_error(
                    current_path,
                    candidate,
                    reason="no_roots",
                    filter_text=browser_filter,
                )
            return _build_dir_browser_response(
                target,
                current_path=current_path,
                candidate=candidate,
                app_store=app_store,
                filter_text=browser_filter,
            )

        # --- NAVIGATE BY RELATIVE BREADCRUMB ------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-breadcrumb":
            if not _triggered_click_value():
                raise PreventUpdate
            target = _breadcrumb_path_for_index(
                current_path,
                triggered_id.get("index"),
            )
            if target is None:
                return _recover_browser_error(
                    current_path,
                    candidate,
                    reason="not_found",
                    filter_text=browser_filter,
                )
            return _build_dir_browser_response(
                target,
                current_path=current_path,
                candidate=candidate,
                app_store=app_store,
                filter_text=browser_filter,
            )

        # --- NAVIGATE TO SUBDIR ---------------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-entry":
            if not _triggered_click_value():
                raise PreventUpdate
            target = _subdirectory_path_for_name(
                current_path,
                triggered_id.get("name"),
            )
            if target is None:
                return _recover_browser_error(
                    current_path,
                    candidate,
                    reason="not_found",
                    filter_text=browser_filter,
                )
            return _build_dir_browser_response(
                target,
                current_path=current_path,
                candidate=candidate,
                app_store=app_store,
                filter_text=browser_filter,
            )

        # --- GO UP ----------------------------------------------------
        if triggered_id == "dir-browser-back-btn":
            stored = (current_path or "").strip()
            if not stored:
                raise PreventUpdate
            try:
                cur = svc.validate_browse_path(stored)
                parent = str(cur.parent)
                svc.validate_browse_path(parent)
                return _build_dir_browser_response(
                    parent,
                    current_path=current_path,
                    candidate=candidate,
                    app_store=app_store,
                    filter_text=browser_filter,
                )
            except svc.ServiceError:
                return _recover_browser_error(
                    stored,
                    candidate,
                    reason="root_boundary",
                    filter_text=browser_filter,
                )

        # --- SELECT DATASET CARD -------------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-dataset":
            if not _triggered_click_value():
                raise PreventUpdate
            return _select_browser_candidate(
                current_path,
                triggered_id.get("name", ""),
                filter_text=browser_filter,
            )

        # --- SELECT RECENT DATASET -----------------------------------
        if _pattern_trigger_type(triggered_id) == "dir-browser-recent-entry":
            if not _triggered_click_value():
                raise PreventUpdate
            records = svc.normalise_recent_datasets(recent_records)
            try:
                record = records[int(triggered_id.get("index"))]
            except (IndexError, TypeError, ValueError):
                return _recover_browser_error(
                    current_path,
                    candidate,
                    reason="recent_missing",
                    filter_text=browser_filter,
                )
            return _select_browser_candidate(
                record.get("folder", ""),
                Path(str(record.get("base") or "")).name,
                filter_text=browser_filter,
                fallback_path=current_path,
                fallback_candidate=candidate,
            )

        raise PreventUpdate

    @app.callback(
        Output("dataset-switch-transaction", "data"),
        Input("data-apply-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input("dataset-browser-candidate", "data"),
        Input("page-store", "data"),
        Input("dataset-switch-validation", "data"),
        State("dataset-switch-transaction", "data"),
        State({"type": "dataset-bound-operation", "name": ALL}, "data"),
        prevent_initial_call=True,
    )
    def _reduce_dataset_switch(
        _apply_clicks,
        _cancel_clicks,
        candidate,
        page_store,
        validation_result,
        transaction,
        bound_operations,
    ):
        """Keep one request authoritative for this browser tab."""
        triggered = ctx.triggered_id
        current = transaction if isinstance(transaction, dict) else {}
        selected = candidate if isinstance(candidate, dict) else {}

        if triggered == "data-apply-btn":
            if current.get("state") == "validating":
                raise PreventUpdate
            if any(bool(value) for value in bound_operations or []):
                return {
                    "state": "failed",
                    "candidate": selected,
                    "reason": "analysis_in_progress",
                    "message": (
                        "当前分析仍在完成，暂不能切换 Current Dataset。"
                        "等待该分析结束后重试；现有 Current Dataset 和候选均已保留。"
                    ),
                }
            if not selected.get("folder") or not selected.get("base"):
                return {
                    "state": "failed",
                    "candidate": selected,
                    "reason": "missing_candidate",
                    "message": "请选择 Dataset Candidate 后再使用。Current Dataset 未改变。",
                }
            return svc.begin_dataset_switch(
                selected,
                origin=dict((page_store or {}).get("dataset_return") or {}),
            )

        if triggered == "dataset-switch-validation":
            if any(bool(value) for value in bound_operations or []):
                return {
                    **current,
                    "state": "failed",
                    "reason": "analysis_in_progress",
                    "message": (
                        "验证完成时仍有旧 Current Dataset 的分析在运行，因此结果未提交。"
                        "旧 Current Dataset 和候选均已保留；等待分析结束后重试。"
                    ),
                }
            resolved = svc.resolve_dataset_switch(current, validation_result)
            if resolved == current:
                raise PreventUpdate
            return resolved

        if triggered == "dir-browser-cancel-btn":
            superseded = svc.supersede_dataset_switch(
                current,
                reason="cancelled",
            )
            return superseded or {"state": "superseded", "reason": "cancelled"}

        if triggered == "page-store":
            if (page_store or {}).get("page") == "data-management":
                raise PreventUpdate
            superseded = svc.supersede_dataset_switch(current, reason="left_workspace")
            if superseded == current:
                raise PreventUpdate
            return superseded

        if triggered == "dataset-browser-candidate":
            if current.get("state") == "validating":
                return svc.supersede_dataset_switch(current, reason="candidate_changed")
            if selected:
                return {"state": "candidate-selected", "candidate": selected}
            return {"state": "idle"}
        raise PreventUpdate

    @app.callback(
        Output("dataset-switch-validation", "data"),
        Input("dataset-switch-transaction", "data"),
        background=True,
        prevent_initial_call=True,
    )
    def _validate_dataset_switch(transaction):
        request = transaction if isinstance(transaction, dict) else {}
        if request.get("state") != "validating":
            raise PreventUpdate
        request_id = str(request.get("request_id") or "")
        candidate = request.get("candidate") or {}
        try:
            validation = svc.validate_dataset_candidate(
                str(candidate.get("folder") or ""),
                str(candidate.get("base") or ""),
            )
        except svc.ServiceError as exc:
            return {
                "request_id": request_id,
                "ok": False,
                "reason": str(exc.reason or "validation_failed"),
                "message": (
                    f"{exc.message} Current Dataset 和 Dataset Candidate 均已保留；"
                    "请修正来源后重试验证。"
                ),
                "completed_ns": time.time_ns(),
            }
        except Exception:
            return {
                "request_id": request_id,
                "ok": False,
                "reason": "validation_failed",
                "message": (
                    "Dataset Candidate 暂时无法验证。Current Dataset 和 Dataset Candidate "
                    "均已保留；请重试。"
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
        Output("data-load-feedback", "children", allow_duplicate=True),
        Output("data-apply-btn", "disabled", allow_duplicate=True),
        Output("data-apply-btn", "children", allow_duplicate=True),
        Input("dataset-switch-transaction", "data"),
        prevent_initial_call=True,
    )
    def _render_dataset_switch(transaction):
        state = str((transaction or {}).get("state") or "idle")
        if state == "validating":
            return (
                dbc.Alert(
                    "正在验证 Dataset Identity 与最新源修订。旧 Current Dataset 仍然有效。",
                    color="info",
                    className="py-2",
                ),
                True,
                "正在验证…",
            )
        if state == "failed":
            return (
                dbc.Alert(
                    str((transaction or {}).get("message") or "验证失败，请重试。"),
                    color="danger",
                    className="py-2",
                    role="alert",
                    tabIndex=-1,
                ),
                False,
                "重试验证",
            )
        if state == "succeeded":
            return no_update, True, "当前正在使用"
        if state == "candidate-selected":
            return "", False, "使用此数据集"
        return "", True, "使用此数据集"

    @app.callback(
        Output("dir-browser-current", "style"),
        Output("dir-browser-body", "style"),
        Output("dir-browser-recent-datasets", "style"),
        Output("dir-browser-filter-row", "style"),
        Output("dir-browser-expert-path", "style"),
        Output("dir-browser-path-input", "disabled"),
        Output("dir-browser-go-btn", "disabled"),
        Output("dir-browser-filter-input", "disabled"),
        Output("dir-browser-filter-clear-btn", "disabled"),
        Output("dir-browser-back-btn", "disabled", allow_duplicate=True),
        Input("dataset-switch-transaction", "data"),
        State("dir-browser-path", "data"),
        prevent_initial_call=True,
    )
    def _lock_dataset_selection_controls(transaction, current_path):
        validating = (transaction or {}).get("state") == "validating"
        lock_style = {"pointerEvents": "none", "opacity": 0.62} if validating else {}
        can_go_up = False
        if current_path:
            try:
                can_go_up = bool(
                    svc.browse_dataset_location(str(current_path)).get("can_go_up")
                )
            except svc.ServiceError:
                can_go_up = False
        return (
            lock_style,
            lock_style,
            lock_style,
            lock_style,
            lock_style,
            validating,
            validating,
            validating,
            validating,
            validating or not can_go_up,
        )

    @app.callback(
        Output({"type": "dir-browser-dataset", "name": ALL}, "disabled"),
        Output({"type": "dir-browser-recent-entry", "index": ALL}, "disabled"),
        Input("dataset-switch-transaction", "data"),
        State({"type": "dir-browser-dataset", "name": ALL}, "id"),
        State({"type": "dir-browser-recent-entry", "index": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _lock_dynamic_dataset_choices(transaction, candidate_ids, recent_ids):
        validating = (transaction or {}).get("state") == "validating"
        return (
            [validating for _item in candidate_ids or []],
            [validating for _item in recent_ids or []],
        )

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
        Output("dataset-focus-request", "data"),
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
        origin = request.get("origin") or {}
        target_page = str(origin.get("page") or "data-management")
        navigation = {
            "request_id": str(request.get("request_id") or ""),
            "page": target_page,
        }
        label = str(validation.get("label") or "未命名数据集")
        if svc.is_same_dataset_revision(current_store, validation):
            message = f"{label} 已是相同源修订的 Current Dataset。"
            return (
                no_update,
                no_update,
                recent_records,
                request.get("candidate"),
                dbc.Alert(message, color="info", className="py-2"),
                navigation,
                {},
                dbc.Alert(message, color="info", className="mb-0"),
                "rs-data-view",
                "rs-data-view d-none",
                no_update,
                no_update,
                no_update,
                no_update,
                {
                    "token": str(request.get("request_id") or ""),
                    "target": "page-title" if target_page != "data-management" else "data-browser-title",
                },
            )

        new_store = svc.current_dataset_from_validation(validation)
        readiness = new_store.get("readiness") or {}
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
            f"当前数据集已切换为：{label}。旧结果与选择已清除；"
            "查询条件已保留，但尚未在新 Current Dataset 运行。"
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
            dbc.Alert(message, color="success", className="py-2"),
            navigation,
            marker,
            dbc.Alert(message, color="success", className="mb-0"),
            "rs-data-view",
            "rs-data-view d-none",
            label,
            label,
            "基础 {} · 事件 {} · 轨迹 {}".format(
                "就绪" if (readiness.get("basic_analysis") or {}).get("ready") else "未就绪",
                "就绪" if (readiness.get("event_search") or {}).get("ready") else "未就绪",
                "就绪" if (readiness.get("trajectory_evidence") or {}).get("ready") else "未就绪",
            ),
            "rs-badge",
            {
                "token": str(request.get("request_id") or ""),
                "target": "page-title" if target_page != "data-management" else "data-candidate-summary",
            },
        )

    @app.callback(
        Output("dataset-focus-request", "data", allow_duplicate=True),
        Input("data-pick-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        State("page-store", "data"),
        prevent_initial_call=True,
    )
    def _request_dataset_focus(_pick_clicks, _cancel_clicks, page_store):
        if ctx.triggered_id == "data-pick-btn":
            return {"token": f"picker-{time.time_ns()}", "target": "data-browser-title"}
        if ctx.triggered_id == "dir-browser-cancel-btn":
            return {
                "token": f"cancel-{time.time_ns()}",
                "target": str(
                    (((page_store or {}).get("dataset_return") or {}).get("trigger"))
                    or "data-candidate-summary"
                ),
            }
        raise PreventUpdate

    app.clientside_callback(
        """
        function(request) {
            if (!request || !request.target || !request.token) {
                return window.dash_clientside.no_update;
            }
            let attempts = 0;
            function focusWhenVisible() {
                const target = document.getElementById(request.target);
                if (target && target.getClientRects().length > 0) {
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
        Output("species-grid-store", "data", allow_duplicate=True),
        Output("rxn-grid-store", "data", allow_duplicate=True),
        Output("inter-grid-store", "data", allow_duplicate=True),
        Output("evolution-payload-store", "data", allow_duplicate=True),
        Output("element-distribution-payload-store", "data", allow_duplicate=True),
        Output("event-grid-store", "data", allow_duplicate=True),
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-viewer-store", "data", allow_duplicate=True),
        Output("pathway-store", "data", allow_duplicate=True),
        Output("pathway-context-store", "data", allow_duplicate=True),
        Output("pathway-selected-step", "data", allow_duplicate=True),
        Output("pathway-selected-path", "data", allow_duplicate=True),
        Output("event-path-store", "data", allow_duplicate=True),
        Output("event-path-context-store", "data", allow_duplicate=True),
        Output("event-path-wizard-step", "data", allow_duplicate=True),
        Output("species-grid", "data", allow_duplicate=True),
        Output("species-grid", "selected_rows", allow_duplicate=True),
        Output("species-structure-grid", "data", allow_duplicate=True),
        Output("species-structure-grid", "selected_rows", allow_duplicate=True),
        Output("rxn-grid", "data", allow_duplicate=True),
        Output("inter-grid", "data", allow_duplicate=True),
        Output("event-grid", "data", allow_duplicate=True),
        Output("event-grid", "selected_rows", allow_duplicate=True),
        Output("pathway-grid", "data", allow_duplicate=True),
        Output("pathway-grid", "selected_rows", allow_duplicate=True),
        Output("pathway-evidence-grid", "data", allow_duplicate=True),
        Output("event-path-comparison-grid", "data", allow_duplicate=True),
        Output("event-path-signature-grid", "data", allow_duplicate=True),
        Output("event-path-time-grid", "data", allow_duplicate=True),
        Output("event-path-event-grid", "data", allow_duplicate=True),
        Output("event-path-edge-grid", "data", allow_duplicate=True),
        Output("event-path-cytoscape", "elements", allow_duplicate=True),
        Output("evolution-graph", "figure", allow_duplicate=True),
        Output("element-distribution-composition-trend", "figure", allow_duplicate=True),
        Output("element-distribution-composition-table", "data", allow_duplicate=True),
        Input("dataset-context-commit", "data"),
        prevent_initial_call=True,
    )
    def _reset_dataset_bound_results(commit_marker):
        if not (commit_marker or {}).get("request_id"):
            raise PreventUpdate
        return (
            {"rows": []},
            {"rows": []},
            {"rows": []},
            None,
            None,
            {"rows": []},
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1,
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            go.Figure(),
            go.Figure(),
            [],
        )

    @app.callback(
        Output("event-extract-id", "value", allow_duplicate=True),
        Output("event-frame-slider", "min", allow_duplicate=True),
        Output("event-frame-slider", "max", allow_duplicate=True),
        Output("event-frame-slider", "value", allow_duplicate=True),
        Output("event-frame-slider", "marks", allow_duplicate=True),
        Output("event-path-additional-sources", "value", allow_duplicate=True),
        Output("event-path-current-replicate", "value", allow_duplicate=True),
        Output("event-path-occurrence-selector", "value", allow_duplicate=True),
        Output("evolution-species-file", "value", allow_duplicate=True),
        Output("evolution-species-files", "value", allow_duplicate=True),
        Input("dataset-context-commit", "data"),
        prevent_initial_call=True,
    )
    def _reset_dataset_bound_controls(commit_marker):
        if not (commit_marker or {}).get("request_id"):
            raise PreventUpdate
        return "", 0, 0, 0, {}, "", "", None, "", ""

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Output("dataset-restore-result", "data"),
        Output("dataset-context-commit", "data", allow_duplicate=True),
        Output("global-dataset-notice", "children", allow_duplicate=True),
        Input("dataset-session-restore", "n_intervals"),
        State("dataset-session-store", "data"),
        prevent_initial_call=True,
    )
    def _restore_current_dataset(_tick, session_store):
        current = session_store if isinstance(session_store, dict) else {}
        if not current.get("dataset_id"):
            empty = initial_store()
            return empty, empty, {"state": "none"}, {}, no_update
        result = svc.revalidate_current_dataset(current)
        state = str(result.get("state") or "unavailable")
        if state == "active":
            return result.get("context"), result.get("context"), result, {}, no_update
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
                    "Current Dataset 的源修订已变化。旧证据已停止作为当前结果；"
                    "查询条件已保留，请更新当前数据集状态。",
                    color="warning",
                    className="mb-0",
                ),
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
                "无法恢复 Current Dataset，已清除失效上下文；查询输入和最近记录均已保留。",
                color="danger",
                className="mb-0",
            ),
        )

    @app.callback(
        Output("app-store", "data", allow_duplicate=True),
        Output("dataset-session-store", "data", allow_duplicate=True),
        Output("dataset-context-commit", "data", allow_duplicate=True),
        Output("global-dataset-notice", "children", allow_duplicate=True),
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
                "已采用 Current Dataset 的最新源修订；查询条件已保留但尚未重新运行。"
                if was_revision_changed
                else "Current Dataset 的身份和源修订仍然有效。"
            )
            return (
                result.get("context"),
                result.get("context"),
                marker,
                dbc.Alert(message, color="success", className="mb-0"),
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
                    "请选择“更新当前数据集状态”采用新修订。",
                    color="warning",
                    className="mb-0",
                ),
            )
        return (
            initial_store(),
            initial_store(),
            {
                "request_id": f"refresh-{time.time_ns()}",
                "reason": "current-unavailable",
            },
            dbc.Alert(
                "Current Dataset 已不可用，已清除失效上下文；查询输入和最近记录均已保留。",
                color="danger",
                className="mb-0",
            ),
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
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "species"}, "data"),
                True,
                False,
            ),
        ],
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
        running=[
            (
                Output(
                    {
                        "type": "dataset-bound-operation",
                        "name": "species-structures",
                    },
                    "data",
                ),
                True,
                False,
            ),
        ],
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
        running=[
            (
                Output(
                    {
                        "type": "dataset-bound-operation",
                        "name": "species-detail",
                    },
                    "data",
                ),
                True,
                False,
            ),
        ],
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
        Output("inter-with-flux", "value"),
        Input("inter-search-btn", "n_clicks"),
        State("inter-kind", "value"),
        State("inter-top", "value"),
        State("inter-abundance", "value"),
        State("inter-start-ratio", "value"),
        State("inter-decay-alpha", "value"),
        State("inter-product-ratio", "value"),
        State("inter-reactant-ratio", "value"),
        State("inter-fwhm", "value"),
        State("inter-timestep", "value"),
        State("inter-require-fwhm", "value"),
        State("inter-with-flux", "value"),
        State("inter-flux-top", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "intermediate"}, "data"),
                True,
                False,
            ),
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
        product_ratio_min,
        reactant_start_ratio_min,
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
                product_ratio_min=float(
                    0.95 if product_ratio_min is None else product_ratio_min
                ),
                reactant_start_ratio_min=float(
                    0.9
                    if reactant_start_ratio_min is None
                    else reactant_start_ratio_min
                ),
                fwhm_min_frames=float(fwhm if fwhm is not None else 1.0),
                timestep_ps=float(timestep) if timestep is not None else None,
                require_fwhm=bool(require_fwhm),
                with_flux=bool(with_flux),
                flux_top=int(flux_top or 10),
            )
        except svc.ServiceError as exc:
            return (
                [],
                _intermediate_columns(),
                str(exc.message),
                {"rows": []},
                bool(with_flux),
            )
        rows = result.get("rows") or []
        flux_meta = (result.get("meta") or {}).get("flux_enrichment") or {}
        flux_applied = bool(flux_meta.get("applied"))
        downgrade = (
            "未找到 Reaction Network；已继续执行丰度筛选并关闭通量富集。"
            if flux_meta.get("requested") and not flux_meta.get("available")
            else None
        )
        return rows, _intermediate_columns(rows), downgrade, {
            "rows": rows,
            "meta": result.get("meta", {}),
            "query": result.get("query", {}),
            "schema_version": result.get("schema_version"),
            "rule_version": result.get("rule_version"),
            "scoring_version": result.get("scoring_version"),
        }, flux_applied

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
                "选择一个中间体候选后显示其结构、分子式与 SMILES。",
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
                title="中间体候选结构",
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
        return {
            "content": svc.intermediate_candidates_to_csv(grid_store),
            "filename": "intermediate_candidates.csv",
            "type": "text/csv",
        }

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
                Output({"type": "dataset-bound-operation", "name": "evolution"}, "data"),
                True,
                False,
            ),
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
                x_axis=x_axis or "step",
                timestep_ps=float(timestep) if timestep is not None else None,
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
        warnings = [
            str(item)
            for item in ((payload.get("meta") or {}).get("warnings") or [])
            if str(item).strip()
        ]
        return (
            fig,
            "；".join(warnings) if warnings else None,
            {**payload, "visible_curve_names": visible_names},
        )

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

    # ── Element Distribution Evolution ─────────────────────────────

    @app.callback(
        Output("element-distribution-alert", "children"),
        Output("element-distribution-highlights", "children"),
        Output("element-distribution-payload-store", "data"),
        Output("element-distribution-composition-trend", "figure"),
        Input("element-distribution-search-btn", "n_clicks"),
        State("element-distribution-group-element", "value"),
        State("element-distribution-max-count", "value"),
        State("element-distribution-include-zero", "value"),
        State("element-distribution-filter-element", "value"),
        State("element-distribution-filter-mode", "value"),
        State("element-distribution-filter-min", "value"),
        State("element-distribution-filter-max", "value"),
        State("element-distribution-reference-smiles", "value"),
        State("element-distribution-timestep", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "element-distribution"}, "data"),
                True,
                False,
            ),
            (
                Output("element-distribution-progress", "children"),
                "正在读取元素分布索引并应用筛选…",
                "",
            ),
            (
                Output("element-distribution-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _build_element_distribution(
        n_clicks,
        group_element,
        max_group_count,
        include_zero,
        filter_element,
        filter_mode,
        filter_min,
        filter_max,
        reference_smiles,
        timestep,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = (store or {}).get("artifacts", {}) or {}
        filters: dict[str, dict[str, Any]] = {}
        selected_filter = str(filter_element or "").strip()
        selected_mode = str(filter_mode or "all")
        if selected_filter and selected_mode != "all":
            rule: dict[str, Any] = {"mode": selected_mode}
            if selected_mode == "range":
                if filter_min is not None:
                    rule["min"] = int(filter_min)
                if filter_max is not None:
                    rule["max"] = int(filter_max)
            filters[selected_filter] = rule
        try:
            species_path = str(artifacts.get("species") or "").strip()
            confirmed_timestep = (
                float(timestep)
                if timestep is not None
                else svc.load_timestep_ps(species_path)
            )
            if timestep is not None and species_path:
                svc.save_timestep_ps(species_path, float(timestep))
            payload = svc.build_elemental_composition_evolution(
                artifacts,
                x_axis="ps" if confirmed_timestep is not None else "step",
                timestep_ps=confirmed_timestep,
                group_element=str(group_element or "C"),
                max_group_count=int(max_group_count if max_group_count is not None else 6),
                element_filters=filters,
                include_zero=bool(include_zero),
                reference_smiles=str(reference_smiles or "").strip(),
            )
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            empty = _empty_plotly_figure(message)
            return dbc.Alert(message, color="warning"), [], None, empty
        return None, _composition_highlights(payload), payload, _composition_trend_figure(payload)

    @app.callback(
        Output("element-distribution-composition-table", "columns"),
        Output("element-distribution-composition-table", "data"),
        Output("element-distribution-composition-table-title", "children"),
        Input("element-distribution-composition-trend", "clickData"),
        Input("element-distribution-payload-store", "data"),
        running=[
            (
                Output("element-distribution-drilldown-progress", "children"),
                "正在读取所选元素计数组的当前值与全程峰值…",
                "",
            ),
            (
                Output("element-distribution-drilldown-progress", "className"),
                "rs-analysis-progress is-running",
                "rs-analysis-progress",
            ),
        ],
    )
    def _render_composition_detail(click_data, payload):
        if not payload:
            return [], [], "绘制后，点击主图中的参考物种或元素计数曲线查看代表物种。"
        points = (click_data or {}).get("points") or []
        if not points:
            return [], [], "点击主图中的参考物种或元素计数曲线，查看该时间点的代表物种。"
        point = points[0]
        custom = point.get("customdata") or []
        try:
            timestep = int(custom[0])
            series = str(custom[1])
            detail = svc.build_element_distribution_species_drilldown(
                payload,
                series=series,
                timestep=timestep,
            )
        except (IndexError, TypeError, ValueError, svc.ServiceError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return [], [], f"无法读取所选元素计数组：{message}"
        unit = str(detail.get("x_unit") or "timestep")
        columns = [
            {"name": "分子式", "id": "formula"},
            {"name": "SMILES", "id": "smiles"},
            {"name": "当前数量", "id": "current_count", "type": "numeric"},
            {"name": "峰值数量", "id": "peak_count", "type": "numeric"},
            {"name": f"峰值位置 ({unit})", "id": "peak_time", "type": "numeric"},
        ]
        title = (
            f"{detail['series']} · 当前 {detail['current_time']:.6g} {unit}"
            f" · {len(detail['rows'])} 个代表物种"
            f" · 查询 {float(detail.get('query_seconds') or 0):.4f} s"
        )
        return columns, detail["rows"], title

    @app.callback(
        Output("element-distribution-structure-detail", "children"),
        Input("element-distribution-composition-table", "selected_rows"),
        Input("element-distribution-structure-show-h", "value"),
        State("element-distribution-composition-table", "data"),
    )
    def _show_element_distribution_species_structure(selected_rows, show_h, rows):
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
        Output("element-distribution-dataset-name", "value"),
        Output("element-distribution-index-status", "children"),
        Output("element-distribution-index-status", "className"),
        Output("element-distribution-index-progress", "value"),
        Output("element-distribution-index-refresh", "disabled"),
        Output("element-distribution-group-element", "options"),
        Output("element-distribution-group-element", "value"),
        Output("element-distribution-filter-element", "options"),
        Output("element-distribution-filter-element", "value"),
        Input("app-store", "data"),
        Input("page-store", "data"),
        Input("element-distribution-index-refresh", "n_intervals"),
        State("element-distribution-group-element", "value"),
        State("element-distribution-filter-element", "value"),
    )
    def _refresh_element_distribution_index_status(
        store,
        page_store,
        _n_intervals,
        current_group_element,
        current_filter_element,
    ):
        if str((page_store or {}).get("page") or "") != "element-distribution":
            return (
                no_update,
                no_update,
                no_update,
                no_update,
                True,
                no_update,
                no_update,
                no_update,
                no_update,
            )
        store = store or {}
        label = str(store.get("label") or store.get("folder") or "未选择")
        status = svc.composition_index_status(store.get("artifacts") or {})
        state = str(status.get("state") or "missing")
        percent = int(round(float(status.get("progress") or 0.0) * 100))
        if state == "ready":
            text = (
                f"元素分布索引已就绪 · {int(status.get('timepoints') or 0)} 个时间点"
                f" · {int(status.get('unique_species') or 0)} 个物种"
            )
            percent = 100
            class_name = "rs-index-status is-ready"
        elif state == "building":
            text = f"正在建立元素分布索引 · {percent}%"
            class_name = "rs-index-status is-building"
        elif state == "missing_source":
            text = "请先在“管理数据”中选择包含 .species 的数据集"
            class_name = "rs-index-status is-warning"
        elif state in {"stale", "invalid"}:
            text = "元素分布索引需要重建：运行 reacnet-scope prepare rebuild element-distribution <目录>"
            class_name = "rs-index-status is-warning"
        else:
            text = "元素分布索引尚未建立：运行 reacnet-scope prepare build element-distribution <目录>"
            class_name = "rs-index-status is-warning"
        elements = [str(value) for value in status.get("available_elements") or []]
        if not elements:
            elements = ["C"]
        options = [{"label": value, "value": value} for value in elements]
        group_value = (
            current_group_element
            if current_group_element in elements
            else ("C" if "C" in elements else elements[0])
        )
        filter_value = current_filter_element if current_filter_element in elements else None
        return (
            label,
            text,
            class_name,
            percent,
            state != "building",
            options,
            group_value,
            options,
            filter_value,
        )

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
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "events"}, "data"),
                True,
                False,
            ),
        ],
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
        Output("event-ovito-launch-status", "children"),
        Input("event-ovito-open-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _launch_event_ovito(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        try:
            launched = svc.launch_event_in_ovito(viewer)
        except svc.ServiceError as exc:
            return str(exc.message)
        return f"OVITO 已启动（PID {launched['pid']}）。"

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
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "event-paths"}, "data"),
                True,
                False,
            ),
        ],
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
        Output("pathway-max-depth", "value"),
        Output("pathway-max-branches", "value"),
        Output("pathway-max-paths", "value"),
        Output("pathway-max-expansions", "value"),
        Input("pathway-goal", "value"),
        prevent_initial_call=True,
    )
    def _apply_pathway_search_preset(goal):
        if goal == "small_fragments":
            return 4, 4, 10, 300
        return 3, 5, 20, 5000

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
        State("pathway-max-expansions", "value"),
        State("pathway-min-net-tp", "value"),
        State("pathway-min-directionality", "value"),
        State("pathway-goal", "value"),
        State("pathway-target-max-carbon", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output({"type": "dataset-bound-operation", "name": "pathways"}, "data"),
                True,
                False,
            ),
        ],
    )
    def _search_pathways(
        n_clicks,
        start_smiles,
        direction,
        max_depth,
        max_branches,
        max_paths,
        max_expansions,
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
                "max_expansions": int(
                    5000 if max_expansions is None else max_expansions
                ),
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
                " 当前为快速网络粗筛：未读取事件或 species 时间索引；"
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

_BROWSER_RENDER_LIMIT = 100

_CAPABILITY_LABELS = {
    "reaction_search": "反应检索",
    "species_abundance": "物种丰度",
    "event_search": "事件检索",
    "trajectory_evidence": "轨迹证据",
    "element_distribution": "元素分布",
}

_CAPABILITY_STATE_LABELS = {
    "ready": "就绪",
    "needs_preparation": "需准备",
    "preparing": "准备中",
    "stale": "已失效",
    "invalid": "无效",
    "missing_source": "缺少来源",
}



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
    possible_inputs: list[str] = []
    selected = candidate if isinstance(candidate, dict) else {}
    for key in ("base", "folder"):
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
            return str(resolved.get("preferred_base") or resolved["folder"])
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


def _candidate_for_name(snapshot: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Resolve a client-visible candidate name through a fresh snapshot."""
    target = str(name or "")
    return next(
        (
            item
            for item in snapshot.get("datasets") or []
            if str(item.get("label") or "") == target
        ),
        None,
    )


def _allowed_roots() -> list[Path]:
    """Return currently valid roots without exposing their absolute paths."""
    roots: list[Path] = []
    for configured in svc.ALLOWED_ROOTS:
        try:
            root = configured.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if root.is_dir():
            roots.append(root)
    return roots


def _allowed_root_for_index(raw_index: Any) -> str | None:
    try:
        return str(_allowed_roots()[int(raw_index)])
    except (IndexError, TypeError, ValueError):
        return None


def _breadcrumb_path_for_index(current_path: Any, raw_index: Any) -> str | None:
    try:
        snapshot = svc.browse_dataset_location(str(current_path or ""))
        crumb = (snapshot.get("breadcrumbs") or [])[int(raw_index)]
        return str(crumb.get("path") or "") or None
    except (IndexError, TypeError, ValueError, svc.ServiceError):
        return None


def _subdirectory_path_for_name(current_path: Any, name: Any) -> str | None:
    """Resolve an untrusted visible child name from the current snapshot."""
    try:
        snapshot = svc.browse_dataset_location(str(current_path or ""))
    except svc.ServiceError:
        return None
    target = str(name or "")
    item = next(
        (
            entry
            for entry in snapshot.get("subdirs") or []
            if str(entry.get("name") or "") == target
        ),
        None,
    )
    return str((item or {}).get("path") or "") or None


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
    filter_text: str = "",
    path_input_value: Any = None,
) -> tuple:
    """Render one already-validated directory snapshot without rereading it."""
    if path_input_value is None:
        path_input_value = data["current_path"]
    return (
        path_input_value,
        not bool(data.get("can_go_up")),
        _render_browser_current(
            data,
            candidate,
            error=error,
            filter_text=filter_text,
        ),
        _render_dir_browser_body(data, filter_text=filter_text),
        data["current_path"],
        candidate,
        candidate is None,
        no_update,
        no_update,
    )


def _build_dir_browser_error_response(message: str) -> tuple:
    """Render a browser error without retrying the failed directory read."""
    return (
        no_update,
        True,
        _render_browser_current(None, None, error=message),
        _render_dir_browser_error(message),
        no_update,
        no_update,
        True,
        no_update,
        no_update,
    )


def _browser_error_copy(reason: str) -> str:
    """Return actionable, path-safe feedback for one browser failure."""
    return {
        "empty_path": (
            "未输入服务器路径。浏览位置、Dataset Candidate 和 Current Dataset 均未改变；"
            "请输入允许根目录内的位置后按 Enter 或选择“前往”。"
        ),
        "path_out_of_bounds": (
            "该位置不在允许根目录内。原浏览位置和 Current Dataset 已保留；"
            "请选择下方允许根目录或修正专家路径。"
        ),
        "permission_denied": (
            "没有读取目标位置的权限。原浏览位置和 Current Dataset 已保留；"
            "请选择其他允许位置或联系管理员授权。"
        ),
        "not_found": (
            "目标位置已不存在。原浏览位置和 Current Dataset 已保留；"
            "请从仍可用的面包屑或允许根目录继续。"
        ),
        "not_directory": (
            "目标不是可浏览目录。原浏览位置和 Current Dataset 已保留；"
            "请输入目录或准确的 Dataset Candidate 公共前缀。"
        ),
        "recent_missing": (
            "最近记录已失效。原浏览位置和 Current Dataset 已保留；"
            "可移除该记录并从允许根目录重新查找。"
        ),
        "candidate_missing": (
            "Dataset Candidate 已消失。当前目录和 Current Dataset 已保留；"
            "请选择仍然存在的候选或继续浏览。"
        ),
        "root_boundary": (
            "已到达允许根目录边界。浏览位置和 Current Dataset 均未改变。"
        ),
        "no_roots": (
            "当前没有可用的允许根目录。Current Dataset 已保留；"
            "请联系管理员检查浏览根配置。"
        ),
        "read_error": (
            "目标位置暂时无法读取。原浏览位置和 Current Dataset 已保留；"
            "请重试或选择其他允许位置。"
        ),
    }.get(
        str(reason or ""),
        "无法打开目标位置。原浏览位置和 Current Dataset 已保留；请修正输入后重试。",
    )


def _candidate_in_snapshot(
    snapshot: dict[str, Any],
    candidate: dict[str, Any] | None,
) -> dict[str, str] | None:
    selected = candidate if isinstance(candidate, dict) else {}
    base = str(selected.get("base") or "")
    actual = _candidate_for_base(snapshot, base) if base else None
    return _compact_browser_candidate(actual) if actual else None


def _refresh_browser_location(
    current_path: Any,
    candidate: dict[str, Any] | None,
    *,
    filter_text: Any = "",
    error: str = "",
) -> tuple:
    try:
        snapshot = svc.browse_dataset_location(str(current_path or ""))
    except svc.ServiceError as exc:
        return _build_dir_browser_error_response(
            _browser_error_copy(str(exc.reason or "read_error"))
        )
    selected = _candidate_in_snapshot(snapshot, candidate)
    if candidate and selected is None and not error:
        error = _browser_error_copy("candidate_missing")
    return _build_dir_browser_snapshot_response(
        snapshot,
        selected,
        error=error,
        filter_text=str(filter_text or ""),
        path_input_value=no_update if error else None,
    )


def _recover_browser_error(
    current_path: Any,
    candidate: dict[str, Any] | None,
    *,
    reason: str,
    filter_text: Any = "",
) -> tuple:
    message = _browser_error_copy(reason)
    if str(current_path or "").strip():
        return _refresh_browser_location(
            current_path,
            candidate,
            filter_text=filter_text,
            error=message,
        )
    return _build_dir_browser_error_response(message)


def _build_dir_browser_response(
    path_str: str,
    *,
    current_path: Any = "",
    candidate: dict[str, Any] | None = None,
    app_store: dict[str, Any] | None = None,
    filter_text: Any = "",
) -> tuple:
    """Build a complete browser snapshot response without applying a dataset."""
    try:
        resolved = svc.resolve_dataset_input(path_str)
        data = svc.browse_dataset_location(resolved["folder"])
    except svc.ServiceError as exc:
        return _recover_browser_error(
            current_path,
            candidate,
            reason=str(exc.reason or "read_error"),
            filter_text=filter_text,
        )
    datasets = data.get("datasets") or []
    preferred_base = str(resolved.get("preferred_base") or "")
    actual = (
        _candidate_for_base(data, preferred_base)
        if preferred_base else None
    )
    if actual is None and not preferred_base and len(datasets) == 1:
        actual = datasets[0]
    if actual is None and not preferred_base:
        current = app_store if isinstance(app_store, dict) else {}
        actual = _candidate_for_base(data, str(current.get("base") or ""))
    candidate = _compact_browser_candidate(actual) if actual else None
    if preferred_base and actual is None:
        error = _browser_error_copy("candidate_missing")
    else:
        error = ""
    return _build_dir_browser_snapshot_response(
        data,
        candidate,
        error=error,
        filter_text=str(filter_text or ""),
    )


def _select_browser_candidate(
    folder: str,
    name: str,
    *,
    filter_text: Any = "",
    fallback_path: Any = "",
    fallback_candidate: dict[str, Any] | None = None,
) -> tuple:
    """Read a directory once then set its explicitly selected candidate."""
    try:
        snapshot = svc.browse_dataset_location(folder)
    except svc.ServiceError as exc:
        return _recover_browser_error(
            fallback_path or folder,
            fallback_candidate,
            reason=(
                "recent_missing"
                if fallback_path
                else str(exc.reason or "read_error")
            ),
            filter_text=filter_text,
        )
    candidate = _candidate_for_name(snapshot, name)
    if candidate is None:
        if fallback_path:
            return _recover_browser_error(
                fallback_path,
                fallback_candidate,
                reason="recent_missing",
                filter_text=filter_text,
            )
        return _build_dir_browser_snapshot_response(
            snapshot,
            None,
            error=_browser_error_copy("candidate_missing"),
            filter_text=str(filter_text or ""),
        )
    compact = _compact_browser_candidate(candidate)
    return _build_dir_browser_snapshot_response(
        snapshot,
        compact,
        filter_text=str(filter_text or ""),
    )


def _render_browser_current(
    data: dict[str, Any] | None,
    candidate: dict[str, str] | None,
    *,
    error: str = "",
    filter_text: str = "",
) -> Any:
    """Render allowed roots, relative breadcrumbs, and candidate radios."""
    snapshot = data or {}
    datasets = list(snapshot.get("datasets") or [])
    selected_base = str((candidate or {}).get("base") or "")
    matched, visible = _bounded_browser_items(
        datasets,
        filter_text,
        key="label",
    )
    if selected_base and not filter_text and not any(
        str(item.get("base") or "") == selected_base for item in visible
    ):
        selected = _candidate_for_base(snapshot, selected_base)
        if selected is not None:
            visible = [*visible[: _BROWSER_RENDER_LIMIT - 1], selected]
    visible_selection = any(
        str(item.get("base") or "") == selected_base for item in visible
    )

    if not datasets:
        candidate_content: Any = html.Div(
            "当前目录没有 Dataset Candidate；可以继续浏览子目录。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    elif not matched:
        candidate_content = html.Div(
            [
                html.Span("没有候选匹配当前筛选。"),
                html.Span(" 使用“清除筛选”恢复全部候选。"),
            ],
            className="rs-browser-empty-line is-filter-empty",
            **{"role": "status"},
        )
    else:
        candidate_content = html.Div(
            [
                _render_candidate_radio(
                    item,
                    selected=(str(item.get("base") or "") == selected_base),
                    tabbable=(
                        str(item.get("base") or "") == selected_base
                        or not visible_selection and position == 0
                    ),
                )
                for position, item in enumerate(visible)
            ],
            className="rs-browser-candidate-list",
            **{
                "role": "radiogroup",
                "aria-label": "Dataset Candidate",
            },
        )
    candidate_section = html.Section(
        [
            html.Div(
                [
                    html.H3(
                        "Dataset Candidate",
                        className="rs-browser-section-title",
                    ),
                    _render_item_count(
                        shown=len(visible),
                        matched=len(matched),
                        total=len(datasets),
                    ),
                ],
                className="rs-browser-section-heading",
            ),
            candidate_content,
            _render_selected_candidate_details(snapshot, selected_base),
        ],
        className="rs-browser-section rs-browser-candidates",
    )
    alert = (
        html.Div(
            error,
            className="rs-browser-region-alert",
            **{"role": "alert"},
        )
        if error
        else None
    )
    return html.Div(
        [
            _render_allowed_roots(),
            _render_breadcrumbs(snapshot.get("breadcrumbs") or []),
            alert,
            candidate_section,
        ]
    )


def _bounded_browser_items(
    items: list[dict[str, Any]],
    filter_text: Any,
    *,
    key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    query = str(filter_text or "").strip().casefold()
    matched = [
        item
        for item in items
        if not query or query in str(item.get(key) or "").casefold()
    ]
    return matched, matched[:_BROWSER_RENDER_LIMIT]


def _render_item_count(*, shown: int, matched: int, total: int) -> Any:
    if matched == total:
        text = f"显示 {shown} / 共 {total}"
    else:
        text = f"显示 {shown} / 匹配 {matched} / 共 {total}"
    return html.Span(text, className="rs-browser-item-count")


def _render_allowed_roots() -> Any:
    roots = _allowed_roots()
    if not roots:
        content: Any = html.Div(
            "没有可用的允许根目录。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    else:
        labels: dict[str, int] = {}
        buttons: list[Any] = []
        for index, root in enumerate(roots):
            base_label = root.name or "根目录"
            labels[base_label] = labels.get(base_label, 0) + 1
            label = (
                base_label
                if labels[base_label] == 1
                else f"{base_label} {labels[base_label]}"
            )
            buttons.append(
                html.Button(
                    label,
                    id={"type": "dir-browser-root", "index": index},
                    type="button",
                    className="rs-browser-root-button",
                )
            )
        content = html.Div(buttons, className="rs-browser-root-list")
    return html.Section(
        [
            html.H3("允许根目录", className="rs-browser-section-title"),
            content,
        ],
        className="rs-browser-section rs-browser-roots",
    )


def _render_breadcrumbs(crumbs: list[dict[str, Any]]) -> Any:
    buttons: list[Any] = []
    for index, crumb in enumerate(crumbs):
        buttons.append(
            html.Button(
                str(crumb.get("label") or "位置"),
                id={"type": "dir-browser-breadcrumb", "index": index},
                type="button",
                className="rs-browser-breadcrumb-button",
                **(
                    {"aria-current": "location"}
                    if index == len(crumbs) - 1
                    else {}
                ),
            )
        )
    return html.Nav(
        [
            html.H3("当前位置", className="rs-browser-section-title"),
            html.Div(buttons, className="rs-browser-breadcrumb-list"),
        ],
        className="rs-browser-section rs-browser-breadcrumbs",
        **{"aria-label": "当前目录的相对面包屑"},
    )


def _render_candidate_radio(
    item: dict[str, Any],
    *,
    selected: bool,
    tabbable: bool,
) -> Any:
    capability_states = dict(item.get("capability_states") or {})
    capability_badges = [
        html.Span(
            [
                html.Span(label, className="rs-browser-capability-name"),
                html.Span(
                    _CAPABILITY_STATE_LABELS.get(
                        str(capability_states.get(key) or "missing_source"),
                        "不可用",
                    ),
                    className=(
                        "rs-browser-capability-state is-"
                        + str(capability_states.get(key) or "missing_source").replace("_", "-")
                    ),
                ),
            ],
            className="rs-browser-capability",
        )
        for key, label in _CAPABILITY_LABELS.items()
    ]
    return html.Button(
        [
            html.Span(className="rs-browser-radio-indicator", **{"aria-hidden": "true"}),
            html.Span(
                [
                    html.Strong(
                        str(item.get("label") or "未命名候选"),
                        className="rs-browser-candidate-name",
                    ),
                    html.Span("当前目录", className="rs-browser-candidate-location"),
                    html.Span(
                        capability_badges,
                        className="rs-browser-capability-list",
                    ),
                ],
                className="rs-browser-candidate-content",
            ),
        ],
        id={
            "type": "dir-browser-dataset",
            "name": str(item.get("label") or ""),
        },
        type="button",
        role="radio",
        tabIndex=0 if tabbable else -1,
        className=(
            "rs-browser-candidate-row is-selected"
            if selected
            else "rs-browser-candidate-row"
        ),
        **{"aria-checked": "true" if selected else "false"},
    )


def _render_selected_candidate_details(
    snapshot: dict[str, Any],
    selected_base: str,
) -> Any:
    selected = _candidate_for_base(snapshot, selected_base) if selected_base else None
    if selected is None:
        return None
    artifact_names = sorted(
        Path(str(path)).name
        for path in (selected.get("artifact_paths") or {}).values()
    )
    return html.Details(
        [
            html.Summary("展开候选来源详情"),
            html.Div(
                [
                    html.Div("完整公共前缀", className="rs-browser-detail-label"),
                    html.Code(selected_base),
                    html.Div("发现的源工件", className="rs-browser-detail-label"),
                    html.Ul([html.Li(name) for name in artifact_names]),
                ],
                className="rs-browser-candidate-details-body",
            ),
        ],
        className="rs-browser-candidate-details",
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
        if not interactive:
            entries.append(
                html.Span(
                    label if available else f"{label}（不可用）",
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
                        label if available else f"{label}（不可用）",
                        id={"type": "dir-browser-recent-entry", "index": index},
                        type="button",
                        disabled=not available,
                        className=(
                            "rs-browser-recent-entry"
                            if available
                            else "rs-browser-recent-unavailable"
                        ),
                    ),
                    html.Button(
                        "移除",
                        id={"type": "dir-browser-recent-remove", "index": index},
                        type="button",
                        className="rs-browser-recent-remove",
                        **{"aria-label": f"从最近数据集中移除 {label}"},
                    ),
                ],
                className="rs-browser-recent-item",
            )
        )
    if not entries:
        return html.Div(
            "暂无最近数据集记录。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    return html.Div(entries, className="rs-browser-recent-list")


def _render_dir_browser_error(message: str) -> Any:
    """Render a recoverable error inside the directory-list section."""
    return html.Div(
        message,
        className="rs-browser-region-alert",
        **{"role": "alert"},
    )


def _render_dir_browser_body(
    data: dict[str, Any],
    *,
    filter_text: str = "",
) -> Any:
    """Render only the subdirectory section for a browser snapshot."""
    subdirs: list[dict[str, Any]] = list(data.get("subdirs") or [])
    matched, visible = _bounded_browser_items(
        subdirs,
        filter_text,
        key="name",
    )
    if not subdirs:
        directory_list: Any = html.Div(
            "当前目录没有子目录。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    elif not matched:
        directory_list = html.Div(
            "没有子目录匹配当前筛选；使用“清除筛选”恢复全部子目录。",
            className="rs-browser-empty-line is-filter-empty",
            **{"role": "status"},
        )
    else:
        directory_list = html.Div(
            [
                html.Button(
                    [
                        html.Span(item.get("name", ""), className="rs-browser-folder-name"),
                        html.Span(
                            "无读取权限" if not item.get("accessible", True) else "打开",
                            className="rs-browser-directory-action",
                        ),
                    ],
                    id={"type": "dir-browser-entry", "name": item["name"]},
                    type="button",
                    disabled=not bool(item.get("accessible", True)),
                    className="rs-browser-directory-entry",
                )
                for item in visible
            ],
            className="rs-browser-directory-list",
        )
    return html.Section(
        [
            html.Div(
                [
                    html.H3("子目录", className="rs-browser-section-title"),
                    _render_item_count(
                        shown=len(visible),
                        matched=len(matched),
                        total=len(subdirs),
                    ),
                ],
                className="rs-browser-section-heading",
            ),
            directory_list,
        ],
        className="rs-browser-section rs-browser-subdirectories",
    )


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
        "peak_analyzed_frame",
        "fwhm_frames",
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
            "peak_analyzed_frame",
            "fwhm_frames",
            "peak_time_ps",
            "fwhm_ps",
            "net_production",
        }
        cols.append(
            {
                "field": field,
                "headerName": field,
                "minWidth": 120 if field not in {"smiles", "reaction_smiles", "top_sources", "top_sinks"} else 220,
                "flex": 2 if field in {"smiles", "reaction_smiles", "top_sources", "top_sinks"} else 1,
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
    rows = payload.get("distribution_rows") or []
    if not rows:
        return _empty_plotly_figure("没有可显示的元素分布数据")
    styles = {
        "参考物种": {"color": "#111827", "dash": "solid", "width": 3.2},
    }
    names = list(dict.fromkeys(str(row["series"]) for row in rows))
    figure = go.Figure()
    for index, name in enumerate(names):
        series = sorted((row for row in rows if str(row["series"]) == name), key=lambda row: float(row["x"]))
        style = styles.get(name)
        if style is None and name.endswith(" 其他物种"):
            style = {"color": "#64748b", "dash": "dash", "width": 2.5}
        if style is None:
            style = {
                "color": f"hsl({(index * 47) % 360}, 58%, 43%)",
                "dash": "solid",
                "width": 2.0,
            }
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
            "text": "元素分布随时间变化<br><sup>点击任一曲线，查看该时间点的代表物种</sup>",
            "x": 0.01,
        },
        template="plotly_white",
        height=520,
        autosize=True,
        margin={"l": 58, "r": 34, "t": 72, "b": 52},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        hovermode="closest",
        clickmode="event+select",
        uirevision="element-distribution",
    )
    return figure


def _composition_highlights(payload: dict[str, Any]) -> Any:
    from dash import html

    meta = payload.get("meta") or {}
    summary = payload.get("summary") or {}
    filters = payload.get("filters") or {}
    filter_summary = ", ".join(
        f"{element}: {str((rule or {}).get('mode') or 'all')}"
        for element, rule in sorted(filters.items())
    )
    items = [
        ("索引时间点", meta.get("source_timepoints")),
        ("绘图采样点", meta.get("sampled_timepoints")),
        ("索引查询", f"{meta.get('query_seconds')} s" if meta.get("query_seconds") is not None else None),
        ("总耗时", f"{meta.get('analysis_seconds')} s" if meta.get("analysis_seconds") is not None else None),
        ("分组元素", summary.get("group_element")),
        ("最大原子数", summary.get("max_group_count")),
        ("筛选", filter_summary or "无"),
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
