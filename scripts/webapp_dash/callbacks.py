"""Dash callback registration for ReacNet Scope WebUI V1.

All callbacks are registered in ``register_callbacks(app)``.  Each callback
delegates to ``reacnet_scope.services`` for data operations and never
re-implements analysis logic.
"""

from __future__ import annotations

from . import ui_components as ui
from .ui_state import species_query

import re
import time
import json
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
from scripts.webapp_dash.file_import import defer_query

from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope import services as svc
from scripts.webapp_dash.chart_presentation import empty_chart_figure
from scripts.webapp_dash.candidate_workbench import actual_event_view
from scripts.webapp_dash.navigation import (
    DEFAULT_PAGE,
    PAGE_WORKFLOWS,
    PAGE_CAPABILITY_REQUIREMENTS,
    PAGE_CLASS_NAMES,
    PAGE_DESCRIPTIONS,
    PAGE_IDS,
    PAGE_LABELS,
    PAGE_SECTIONS,
    PAGE_WORKSPACES,
    TOP_NAV_PAGE_IDS,
    WORKSPACE_PAGE_IDS,
    WORKSPACE_TASK_LABELS,
    WORKSPACE_TOOL_PAGES,
    resolve_page_id,
)
PAGE_DATA_REQUIREMENTS = {
    "species": ("reaction", "reactionabcd"),
    "reactions": ("reaction", "reactionabcd"),
    "evolution": ("species", ".species + Species Abundance Index"),
    "element-distribution": ("species", ".species"),
    "events": ("timeline", ".timeline.h5 或 .reactionevent.csv + .molecules.csv"),
    "trajectory": ("trajectory", "轨迹文件与帧索引"),
}

_CAPABILITY_LABELS = {
    "reaction_search": "反应检索",
    "species_abundance": "物种丰度",
    "event_search": "事件检索",
    "species_fate": "物种命运分析",
    "trajectory_evidence": "轨迹证据",
    "element_distribution": "元素分布",
}

_WORKSPACE_CAPABILITIES = {
    "species": ("reaction_search", "species_abundance", "element_distribution"),
    "reactions": ("reaction_search", "event_search"),
    "trajectory": ("event_search", "trajectory_evidence"),
}

_CAPABILITY_STATE_LABELS = {
    "ready": "可用",
    "needs-preparation": "需准备索引",
    "needs_preparation": "需准备索引",
    "preparing": "准备中",
    "stale": "需更新",
    "invalid": "不可用",
    "missing-source": "缺少源数据",
    "missing_source": "缺少源数据",
}

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


def _dft_participant_options(row: dict[str, Any], side: str) -> list[dict[str, Any]]:
    label = "反应物" if side == "reactant" else "产物"
    options = []
    for index, participant in enumerate(row.get(f"{side}_participants") or []):
        atom_ids = [int(value) for value in participant.get("atom_ids") or []]
        options.append(
            {
                "label": (
                    f"{label} {index + 1} · {participant.get('species') or '?'} · "
                    f"{len(atom_ids)} atoms · IDs {','.join(map(str, atom_ids))}"
                ),
                "value": index,
            }
        )
    return options


def _dft_output_stems(
    row: dict[str, Any],
    reactant_indices: list[int] | None,
    product_indices: list[int] | None,
    layout: str,
) -> list[str]:
    stems: list[str] = []
    for side, selected in (
        ("reactant", reactant_indices or []),
        ("product", product_indices or []),
    ):
        participants = row.get(f"{side}_participants") or []
        valid = [int(value) for value in selected if 0 <= int(value) < len(participants)]
        if not valid:
            continue
        if layout in {"combined", "both"}:
            stems.append("reactants" if side == "reactant" else "products")
        if layout in {"separate", "both"}:
            for index in valid:
                atom_ids = sorted(
                    int(value)
                    for value in (participants[index].get("atom_ids") or [])
                )
                if atom_ids:
                    stems.append(
                        f"{side}-{index + 1:02d}-atoms-{min(atom_ids)}-{max(atom_ids)}"
                    )
    return stems


def _dft_electronic_states_from_controls(
    charge_values: list[Any],
    charge_ids: list[dict[str, Any]],
    multiplicity_values: list[Any],
    multiplicity_ids: list[dict[str, Any]],
) -> dict[str, tuple[Any, Any]]:
    charges = {
        str(identifier.get("stem") or ""): value
        for identifier, value in zip(charge_ids or [], charge_values or [])
    }
    multiplicities = {
        str(identifier.get("stem") or ""): value
        for identifier, value in zip(
            multiplicity_ids or [], multiplicity_values or []
        )
    }
    states: dict[str, tuple[Any, Any]] = {}
    for stem in sorted(set(charges).union(multiplicities)):
        charge = charges.get(stem)
        multiplicity = multiplicities.get(stem)
        if charge in {None, ""} and multiplicity in {None, ""}:
            continue
        if charge in {None, ""} or multiplicity in {None, ""}:
            raise ValueError(f"{stem} 必须同时填写电荷和自旋多重度")
        states[stem] = (charge, multiplicity)
    return states


def _build_dft_bundle_from_controls(
    *,
    selected: dict[str, Any] | None,
    app_store: dict[str, Any] | None,
    reactant_indices: list[int] | None,
    product_indices: list[int] | None,
    layout: str,
    unit_confirmation: list[str] | None,
    isolated_cluster_confirmation: list[str] | None,
    charge_values: list[Any],
    charge_ids: list[dict[str, Any]],
    multiplicity_values: list[Any],
    multiplicity_ids: list[dict[str, Any]],
) -> Any:
    row = (selected or {}).get("row") or {}
    artifacts = (app_store or {}).get("artifacts") or {}
    trajectory = str(artifacts.get("trajectory") or "")
    confirmed_now = "angstrom" in (unit_confirmation or [])
    request = svc.DftGeometryRequest(
        include_reactants=bool(reactant_indices),
        include_products=bool(product_indices),
        reactant_indices=tuple(int(value) for value in (reactant_indices or [])),
        product_indices=tuple(int(value) for value in (product_indices or [])),
        layout=str(layout or "combined"),
        electronic_states=_dft_electronic_states_from_controls(
            charge_values,
            charge_ids,
            multiplicity_values,
            multiplicity_ids,
        ),
        source_length_unit="angstrom" if confirmed_now else None,
    )
    evaluation = svc.evaluate_reaction_readiness(
        artifacts,
        row,
        svc.ReactionReadinessRequest(
            geometry=request,
            isolated_cluster_confirmed=(
                "confirmed" in (isolated_cluster_confirmation or [])
            ),
        ),
        dataset_id=str((app_store or {}).get("dataset_id") or ""),
        source_revision=(app_store or {}).get("source_revision") or None,
        replicate=str((app_store or {}).get("label") or "current"),
    )
    if confirmed_now and evaluation.bundle is not None:
        svc.save_coordinate_length_unit(trajectory, "angstrom")
    return evaluation


def _dft_request_fingerprint(**controls: Any) -> str:
    """Bind a preview to the exact browser request and dataset context."""
    context = dict(controls)
    store = context.pop("app_store") or {}
    context["dataset"] = {
        key: store.get(key)
        for key in ("dataset_id", "source_revision", "label", "artifacts")
    }
    context["selected"] = (context.get("selected") or {}).get("row") or {}
    encoded = json.dumps(context, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dft_review_token(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "review:" + hashlib.sha256(encoded).hexdigest()


def _dft_readiness_validation(report: dict[str, Any] | None) -> Any:
    value = report or {}
    qc_handoff = value.get("qc_handoff") or {}
    status = str(qc_handoff.get("status") or "blocked")
    colors = {
        "blocked": "danger",
        "needs_input": "info",
        "review_required": "warning",
        "ready": "success",
    }
    labels = {
        "blocked": "被证据或结构条件阻断",
        "needs_input": "需要补充或确认输入",
        "review_required": "需要人工复核后才能导出",
        "ready": "可交给外部 TS 流程",
    }
    checks = list(qc_handoff.get("checks") or [])
    groups = []
    for group_status in ("blocked", "needs_input", "review_required", "pass"):
        items = [item for item in checks if item.get("status") == group_status]
        if not items:
            continue
        groups.append(
            html.Div(
                [
                    html.Strong(f"{group_status} ({len(items)})"),
                    html.Ul(
                        [
                            html.Li(
                                [
                                    html.Code(str(item.get("id") or "check")),
                                    " · ",
                                    "；".join(
                                        str(value)
                                        for value in (
                                            (item.get("evidence") or {}).get(
                                                "message"
                                            ),
                                            item.get("remediation")
                                            or item.get("claim_limit"),
                                        )
                                        if value
                                    )
                                    or "检查通过",
                                ]
                            )
                            for item in items
                        ],
                        className="mb-1 mt-1",
                    ),
                ],
                className="mt-2",
            )
        )
    return dbc.Alert(
        [
            html.Div(
                [
                    html.Code(status),
                    " · ",
                    labels.get(status, "检查状态未知"),
                ]
            ),
            *groups,
            html.Div(
                "ready 不表示已验证过渡态、动力学模型或可直接计算速率。",
                className="rs-step-note mt-2",
            ),
        ],
        color=colors.get(status, "secondary"),
        className="py-2 mb-0",
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
        reset("rxn-grid-store", "data", {"rows": []}),
        reset("rxn-timing-distribution-store", "data", None),
        reset("rxn-timing-page-store", "data", None),
        reset("rxn-timing-graph", "figure", {}),
        reset("rxn-timing-event-grid", "rowData", []),
        reset("rxn-timing-card", "style", {"display": "none"}),
        reset("evolution-payload-store", "data", None),
        reset("evolution-timestep", "value", None),
        reset("element-distribution-payload-store", "data", None),
        reset("element-distribution-timestep", "value", None),
        reset("event-grid-store", "data", {"rows": []}),
        reset("event-selected-store", "data", None),
        reset("event-bookmark-validation-store", "data", None),
        reset("event-viewer-store", "data", None),
        reset("event-dft-store", "data", None),
        reset("molecule-lineage-store", "data", None),
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
            "选择物种，开始查看时间演化", "在设置区添加目标，然后点击绘制。"
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


def _capability_state_class(state: Any) -> str:
    return str(state or "invalid").replace("_", "-")


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


def _capabilities_from_store(store: dict[str, Any] | None) -> dict[str, Any]:
    current = store if isinstance(store, dict) else {}
    values = dict(current.get("analysis_capabilities") or {})
    if values:
        return values
    artifacts = dict(current.get("artifacts") or {})
    readiness = dict(current.get("readiness") or {})

    def direct(key: str, artifact: str, reason: str) -> None:
        ready = bool(artifacts.get(artifact))
        values[key] = {
            "state": "ready" if ready else "missing-source",
            "reason": reason if ready else f"缺少 {artifact} 源证据。",
        }

    direct("reaction_search", "reaction", "Reaction Evidence 可直接使用。")
    species_source_ready = bool(artifacts.get("species"))
    composition_state = str(
        (dict(readiness.get("composition") or {})).get("state")
        or "missing"
    )
    values["species_abundance"] = {
        "state": (
            "ready"
            if species_source_ready and composition_state == "ready"
            else "missing-source"
            if not species_source_ready
            else "needs-preparation"
        ),
        "reason": (
            "Species Abundance Index 与源修订一致，可直接查询时间演化。"
            if species_source_ready and composition_state == "ready"
            else "缺少 .species Species Abundance Evidence。"
            if not species_source_ready
            else "源证据可用；请在数据管理中显式准备 Species Abundance Index。"
        ),
    }
    for key, readiness_key, source in (
        ("event_search", "event_search", "事件源证据"),
        ("species_fate", "species_fate", "Molecular Continuity Substrate"),
        ("trajectory_evidence", "trajectory_evidence", "轨迹源文件"),
    ):
        item = dict(readiness.get(readiness_key) or {})
        state = str(item.get("state") or "missing").replace("_", "-")
        values[key] = {
            "state": (
                "ready"
                if item.get("ready")
                else "missing-source"
                if state == "missing"
                else "needs-preparation"
            ),
            "reason": (
                f"{source}与索引可用。"
                if item.get("ready")
                else f"{source}尚未形成可用能力。"
            ),
        }
    composition_ready = bool(artifacts.get("species"))
    values["element_distribution"] = {
        "state": "needs-preparation" if composition_ready else "missing-source",
        "reason": (
            "源证据可用；请在数据管理中显式准备元素分布索引。"
            if composition_ready
            else "缺少 .species Species Abundance Evidence。"
        ),
    }
    return values


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


def _species_identity_context_panel(detail: dict[str, Any]) -> Any:
    """Render explicit RNG identity/provenance without inferring missing settings."""

    context = detail.get("identity_context") or {}
    processing = context.get("processing") or {}
    fields = processing.get("fields") or {}
    molecular = context.get("molecular_evidence") or {}
    labels = (
        ("miso", "miso"),
        ("run_hmm", "HMM"),
        ("step_interval", "step interval"),
        ("timestep_ps", "timestep (ps)"),
        ("reacnetgenerator_version", "RNG 版本"),
        ("rng_source_branch", "RNG 分支"),
        ("rng_source_revision", "RNG 修订"),
    )

    def display_value(value: Any) -> str:
        if value is None or value == "":
            return "未知（未显式记录）"
        if isinstance(value, bool):
            return "是" if value else "否"
        return str(value)

    setting_rows: list[Any] = []
    for key, label in labels:
        field = fields.get(key) or {}
        source = str(field.get("source") or "")
        setting_rows.extend(
            (
                html.Dt(label),
                html.Dd(
                    [
                        html.Span(display_value(field.get("value"))),
                        html.Code(
                            source,
                            title=source,
                            className="rs-provenance-source",
                        )
                        if source
                        else None,
                    ]
                ),
            )
        )

    reaction_source = str(context.get("reaction_source") or "")
    molecular_source = str(molecular.get("source") or "")
    return html.Details(
        [
            html.Summary("身份、处理设置与 Molecular Evidence"),
            html.P(
                [
                    "精确 RNG Species：",
                    html.Code(
                        str(context.get("rng_species_label") or detail.get("smiles") or "")
                    ),
                    "。分子式仅用于检索与分组，不代替结构身份。",
                ],
                className="rs-step-note mt-2",
            ),
            html.Dl(
                [
                    html.Dt("Reaction Source"),
                    html.Dd(
                        html.Code(reaction_source, title=reaction_source)
                        if reaction_source
                        else "未知（未显式记录）"
                    ),
                    *setting_rows,
                    html.Dt("Molecular Evidence"),
                    html.Dd(
                        [
                            html.Span(
                                str(
                                    molecular.get("message")
                                    or "当前没有可说明的 Molecular Evidence。"
                                )
                            ),
                            html.Code(
                                molecular_source,
                                title=molecular_source,
                                className="rs-provenance-source",
                            )
                            if molecular_source
                            else None,
                        ]
                    ),
                ],
                className="mt-2",
            ),
        ],
        className="rs-species-provenance mt-2",
    )


def _structure_species_card(
    item: dict[str, Any],
    side_label: str = "物种",
    *,
    action_scope: str = "",
    side: str = "",
) -> Any:
    duplicate_label = ""
    if int(item.get("occurrence_total") or 0) > 1:
        duplicate_label = (
            f"（第 {item.get('occurrence')} / "
            f"{item.get('occurrence_total')} 项）"
        )
    formula = str(item.get("formula") or "?")
    smiles = str(item.get("smiles") or "")
    children = [
        html.Img(
            src=item.get("structure_url"),
            alt=f"{side_label} {formula} 结构{duplicate_label}",
        ),
        html.Strong(
            formula,
            className="rs-channel-species-formula",
        ),
        html.Code(
            smiles,
            className="rs-channel-species-smiles",
        ),
        html.Span(duplicate_label, className="rs-channel-stoich-label")
        if duplicate_label
        else None,
    ]
    if action_scope:
        action_label = f"查看 {formula} 的直接生成/消耗通道"
        return html.Button(
            children,
            id={
                "type": "rxn-structure-species",
                "scope": action_scope,
                "side": side,
                "index": int(item.get("index") or 0),
                "smiles": smiles,
                "formula": formula,
            },
            n_clicks=0,
            type="button",
            title=action_label,
            className="rs-channel-species-card rs-channel-species-action",
            **{"aria-label": action_label},
        )
    return html.Div(children, className="rs-channel-species-card")


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


def _selected_table_row(selected_rows, rows):
    return ui.selected_row(selected_rows, rows)


def register_callbacks(app: Any) -> None:
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
        Input("species-to-channels-btn", "n_clicks"),
        Input("species-to-evolution-btn", "n_clicks"),
        Input("evolution-open-compare-btn", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        Input("cp-open-events", "n_clicks"),
        Input("cp-track-instance", "n_clicks"),
        Input("cp-prepare", "n_clicks"),
        Input("cp-from-species", "n_clicks"),
        Input("cp-to-species", "n_clicks"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("rxn-channel-to-event-btn", "n_clicks"),
        Input("rxn-timing-open-events-btn", "n_clicks"),
        Input("event-back-btn", "n_clicks"),
        Input("event-extract-btn", "n_clicks"),
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
        direct_click = next((item for item in ctx.triggered
            if str(item.get("prop_id") or "").endswith(".n_clicks")
            and not str(item.get("prop_id") or "").startswith("{")
            and isinstance(item.get("value"), (int, float)) and item["value"] > 0), None)
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
        stored_state = (_args[-1] or {}) if _args else {}
        stored_page = resolve_page_id(stored_state.get("page"))
        switch_navigation = _args[-2] if len(_args) >= 2 else {}
        if triggered_string_id == "dataset-switch-navigation":
            page_id = str((switch_navigation or {}).get("page") or stored_page)
        elif _pattern_trigger_type(triggered_id) == "data-overview-open-page":
            page_id = str(triggered_id.get("page") or "data-management")
        elif _pattern_trigger_type(triggered_id) == "workspace-open-page":
            page_id = str(triggered_id.get("page") or DEFAULT_PAGE)
            if page_id == "reaction-candidates":
                page_id = "reactions"
        elif triggered_string_id == "dir-browser-cancel-btn":
            page_id = str(
                ((stored_state.get("dataset_return") or {}).get("page"))
                or "data-management"
            )
        elif triggered_string_id in {
            "rxn-to-event-btn",
            "rxn-channel-to-event-btn",
            "rxn-timing-open-events-btn",
        }:
            page_id = "events"
        elif triggered_string_id in {"cp-open-events", "cp-track-instance"}:
            page_id = "trajectory"
        elif triggered_string_id == "event-back-btn":
            page_id = stored_state.get("return_page") or DEFAULT_PAGE
        elif triggered_string_id == "event-extract-btn":
            page_id = "trajectory"
        elif triggered_string_id == "trajectory-back-events-btn":
            page_id = (
                "reactions"
                if stored_state.get("candidate_direct_return")
                else "events"
            )
        elif triggered_string_id in {
            "species-to-channels-btn",
            "species-to-event-btn",
            "cp-from-species",
            "cp-to-species",
        }:
            page_id = "reactions"
        elif triggered_string_id in {
            "data-open-batch-compare-btn",
            "evolution-open-compare-btn",
        }:
            page_id = "batch-compare"
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
        elif triggered_string_id == "species-to-evolution-btn":
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
        active_workspace = PAGE_WORKSPACES.get(page_id, DEFAULT_PAGE)
        nav_classes = {
            pid: f"rs-top-nav-item{' active' if pid == active_workspace else ''}"
            for pid in TOP_NAV_PAGE_IDS
        }
        page_state = {"page": page_id}
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
            "rxn-to-event-btn": ("reactions", "返回反应式检索"),
            "rxn-channel-to-event-btn": ("reactions", "返回反应通道"),
            "rxn-timing-open-events-btn": ("reactions", "返回时间分布"),
        }.get(triggered_string_id)
        if page_id == "events" and return_context:
            page_state.update(
                return_page=return_context[0],
                return_label=return_context[1],
            )
        elif triggered_string_id in {"cp-open-events", "cp-track-instance"}:
            page_state.update(
                return_page="reactions",
                return_label="返回候选路线",
                candidate_direct_return=True,
            )
        elif triggered_string_id == "event-extract-btn":
            for key in ("return_page", "return_label"):
                if stored_state.get(key):
                    page_state[key] = stored_state[key]
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
                    else "data-candidate-summary"
                ),
            }
        elif triggered_string_id == "dir-browser-cancel-btn":
            focus_request = {
                "token": f"cancel-{time.time_ns()}",
                "target": str(
                    ((stored_state.get("dataset_return") or {}).get("trigger"))
                    or "data-candidate-summary"
                ),
            }
        elif triggered_string_id == "dataset-switch-navigation":
            focus_request = {
                "token": str((switch_navigation or {}).get("request_id") or time.time_ns()),
                "target": (
                    "page-title"
                    if page_id != "data-management"
                    else "data-candidate-summary"
                ),
                "expected_text": (
                    PAGE_LABELS[page_id] if page_id != "data-management" else ""
                ),
            }
        elif triggered_string_id == "cp-track-instance":
            focus_request = {
                "token": f"candidate-lineage-{time.time_ns()}",
                "target": "lx-card",
            }
        return (
            tuple(page_classes[pid] for pid in PAGE_IDS)
            + tuple(nav_classes[pid] for pid in TOP_NAV_PAGE_IDS)
            + (
                (
                    "rs-top-nav-item rs-nav-utility active"
                    if active_workspace == "data-management"
                    else "rs-top-nav-item rs-nav-utility"
                ),
                (
                    "rs-top-nav-item rs-nav-utility active"
                    if active_workspace == "batch-compare"
                    else "rs-top-nav-item rs-nav-utility"
                ),
            )
            + tuple(
                "page" if pid == active_workspace else "false"
                for pid in TOP_NAV_PAGE_IDS
            )
            + (
                "page" if active_workspace == "data-management" else "false",
                "page" if active_workspace == "batch-compare" else "false",
            )
            + (
                page_state,
                PAGE_LABELS[page_id],
                PAGE_SECTIONS[page_id],
                PAGE_DESCRIPTIONS[page_id],
                PAGE_LABELS[page_id],
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
        page_id = str(state.get("page") or DEFAULT_PAGE)
        page_id = resolve_page_id(page_id)
        active_workspace = PAGE_WORKSPACES.get(page_id, DEFAULT_PAGE)
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
                "rs-top-nav-item rs-nav-utility active"
                if active_workspace == "data-management"
                else "rs-top-nav-item rs-nav-utility"
            ),
            (
                "rs-top-nav-item rs-nav-utility active"
                if active_workspace == "batch-compare"
                else "rs-top-nav-item rs-nav-utility"
            ),
            *(
                "page" if pid == active_workspace else "false"
                for pid in TOP_NAV_PAGE_IDS
            ),
            *(
                "page" if active_workspace == "data-management" else "false",
                "page" if active_workspace == "batch-compare" else "false",
            ),
            PAGE_LABELS[page_id],
            PAGE_SECTIONS[page_id],
            PAGE_DESCRIPTIONS[page_id],
            PAGE_LABELS[page_id],
            (
                "rs-page-header is-title-only"
                if page_id == "reactions"
                else "rs-page-header"
            ),
            "rs-body rs-tool-shell",
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
        if page_id in {"batch-compare", "reaction-compare"}:
            reaction_ready = bool(((app_store or {}).get("artifacts") or {}).get("reaction"))
            if reaction_ready:
                return "当前RNG 数据可加入对比", "rs-page-status is-ready"
            return "可扫描目录或从数据管理加载", "rs-page-status is-independent"
        capability_key = PAGE_CAPABILITY_REQUIREMENTS.get(page_id, "")
        if not capability_key:
            return "此页面不依赖当前RNG 数据", "rs-page-status is-independent"
        label = _CAPABILITY_LABELS[capability_key]
        if not (app_store or {}).get("dataset_id"):
            return (
                f"{label}：需要先选择当前RNG 数据",
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
        Output("data-pick-btn", "children"),
        Input("app-store", "data"),
        prevent_initial_call=True,
    )
    def _render_current_dataset_topbar(app_store):
        current = app_store if isinstance(app_store, dict) else {}
        label = str(current.get("label") or "未选择")
        pick_label = "添加RNG 数据"
        if not current.get("dataset_id"):
            return "未选择", "未选择", "未选择数据", "rs-badge rs-bad", pick_label
        if current.get("context_state") == "revision-changed":
            affected = "、".join(current.get("invalidated_artifacts") or [])
            status = (
                f"源修订已变化 · {affected} 待采用 · 其余能力仍可用"
                if affected
                else "源修订已变化 · 请采用新修订"
            )
            return label, label, status, "rs-badge rs-bad", pick_label
        return (
            label,
            label,
            "分析功能按项显示",
            "rs-badge",
            pick_label,
        )

    @app.callback(
        Output("data-overview-view", "className"),
        Output("data-browser-view", "className"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("nav-data-management", "n_clicks"),
        Input("data-pick-btn", "n_clicks"),
        Input("library-add-more", "n_clicks"),
        Input("data-empty-pick-btn", "n_clicks"),
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
        _empty_pick_clicks,
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
            "data-empty-pick-btn",
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
        Output("workspace-task-nav", "children"),
        Input("page-store", "data"),
        Input("reaction-task-tabs", "value"),
        State("workspace-task-nav", "children"),
    )
    def _render_workspace_task_navigation(page_store, reaction_task, previous):
        page_id = resolve_page_id((page_store or {}).get("page"))
        workspace_id = PAGE_WORKSPACES[page_id]
        page_ids = WORKSPACE_TOOL_PAGES.get(workspace_id, (workspace_id,))
        if len(page_ids) <= 1:
            return []
        if workspace_id == "reactions":
            page_ids = ("reactions", "reaction-candidates", "events", "reaction-compare")
            if page_id == "reactions" and reaction_task == "candidates":
                page_id = "reaction-candidates"
        previous_pages = [((child.get("props") or {}).get("id") or {}).get("page")
                          for child in previous or [] if isinstance(child, dict)]
        if previous_pages == list(page_ids):
            # Keep mounted buttons and their click counts stable. Replacing the
            # children during a task switch can erase the next click before
            # Dash samples its n_clicks. Active styles are updated separately.
            return no_update
        return [
            *[
                html.Button(
                    "候选路径" if tool_page == "reaction-candidates" else WORKSPACE_TASK_LABELS[tool_page],
                    id={"type": "workspace-open-page", "page": tool_page},
                    className="rs-task-tab nav-link" + (" active" if tool_page == page_id else ""),
                    n_clicks=0,
                    type="button",
                    **{"aria-current": "page" if tool_page == page_id else "false"},
                )
                for tool_page in page_ids
            ],
        ]

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
            capability_keys = _WORKSPACE_CAPABILITIES.get(page_id, ())
            workspace_evidence = [
                (key, dict(capabilities.get(key) or {}))
                for key in capability_keys
            ]
            if page_id == "data-management":
                state = "ready" if loaded else "no-dataset"
                state_label = "当前RNG 数据" if loaded else "先选择RNG 数据"
                reason = (
                    f"当前：{current.get('label') or current.get('base') or '已加载'}"
                    if loaded
                    else "添加 RNG 文件后开始分析。"
                )
            elif page_id == "batch-compare":
                state = "ready"
                state_label = "可独立使用"
                reason = ""
            else:
                states = [
                    _capability_state_class(evidence.get("state"))
                    for _key, evidence in workspace_evidence
                ]
                if not loaded:
                    state = "no-dataset"
                elif states and all(item == "ready" for item in states):
                    state = "ready"
                elif "needs-preparation" in states:
                    state = "needs-preparation"
                else:
                    state = next(
                        (item for item in states if item != "ready"),
                        "unknown",
                    )
                state_label = (
                    _CAPABILITY_STATE_LABELS.get(state, "状态待检查")
                    if loaded
                    else "先选择RNG 数据"
                )
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
                if loaded and (state != "ready" or page_id == "data-management")
                else None,
                dbc.Button(
                    "进入工作区" if page_id != "data-management" else "管理RNG 数据",
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
        Output("page-workflow-guide", "children"),
        Input("page-store", "data"),
    )
    def _render_workflow_guide(page_store):
        page = resolve_page_id((page_store or {}).get("page"))
        if page == "data-management":
            return []
        workflow = PAGE_WORKFLOWS.get(page)
        if workflow is None:
            return []
        question, required, next_step = workflow
        return html.Details([
            html.Summary("使用帮助"),
            html.Div([
                html.P(question),
                html.P([html.Strong("准备："), required]),
                html.P([html.Strong("后续："), next_step]),
            ], className="rs-workflow-guide-body"),
        ], className="rs-workflow-guide")

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
        Output("rxn-channel-timestep-ps", "value"),
        Input("app-store", "data"),
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
                        html.Strong("尚未加载RNG 数据"),
                        html.Span(
                            "选择一个 ReacNetGenerator RNG 数据后即可开始分析。",
                            className="rs-current-dataset-empty-copy",
                        ),
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
        Input("data-prep-cancel-result", "data"),
        State("preparation-task-snapshot", "data"),
        prevent_initial_call=True,
    )
    def _refresh_persisted_preparation_tasks(
        _tick,
        candidate,
        app_store,
        recent_records,
        _task_action_result,
        previous_tasks,
    ):
        targets = [
            candidate if isinstance(candidate, dict) else {},
            app_store if isinstance(app_store, dict) else {},
            *(recent_records if isinstance(recent_records, list) else []),
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
        Input("data-prep-event-btn", "n_clicks"),
        Input("data-prep-trajectory-btn", "n_clicks"),
        Input("data-prep-composition-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _toggle_preparation_task_refresh(
        task_snapshot,
        _auto_request,
        _event_clicks,
        _trajectory_clicks,
        _composition_clicks,
    ):
        if ctx.triggered_id in {
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

    # ── Directory browser (internal data-management view) ────────────


    @app.callback(
        Output("dataset-switch-transaction", "data"),
        Output("dataset-switch-request", "data"),
        Input("data-apply-btn", "n_clicks"),
        Input("library-use", "n_clicks"),
        Input("data-browser-index-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input("dataset-browser-candidate", "data"),
        Input("page-store", "data"),
        State("library-select", "value"),
        State("dataset-library", "data"),
        State("dataset-switch-transaction", "data"),
        State({"type": "dataset-bound-operation", "name": ALL}, "data"),
        prevent_initial_call=True,
    )
    def _reduce_dataset_switch(
        _apply_clicks,
        _library_clicks,
        _index_clicks,
        _cancel_clicks,
        candidate,
        page_store,
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
        current = transaction if isinstance(transaction, dict) else {}
        selected = candidate if isinstance(candidate, dict) else {}

        if triggered == "library-use":
            selected = next((entry for entry in svc.normalise_dataset_library(library_records)
                             if entry["base"] == library_selected), {})

        if triggered in {"data-apply-btn", "library-use"}:
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

        if triggered in {"data-browser-index-btn", "dir-browser-cancel-btn"}:
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

        if triggered == "page-store":
            if (page_store or {}).get("page") == "data-management":
                raise PreventUpdate
            superseded = svc.supersede_dataset_switch(current, reason="left_workspace")
            if superseded == current:
                raise PreventUpdate
            return superseded, no_update

        if triggered == "dataset-browser-candidate":
            if current.get("state") == "validating" and (current.get("origin") or {}).get("library"):
                raise PreventUpdate
            if current.get("state") == "validating":
                return (
                    svc.supersede_dataset_switch(current, reason="candidate_changed"),
                    no_update,
                )
            if selected:
                return {"state": "candidate-selected", "candidate": selected}, no_update
            return {"state": "idle"}, no_update
        raise PreventUpdate

    @app.callback(
        Output("dataset-switch-validation", "data"),
        Input("dataset-switch-request", "data"),
        background=True,
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
        if (validation.get("collection_id") or (request.get("origin") or {}).get("library")) and origin_page in PAGE_LABELS and origin_page not in {"data-management", "batch-compare"}:
            navigation["page"] = origin_page
        label = str(validation.get("label") or "未命名RNG 数据")
        if svc.is_same_dataset_revision(current_store, validation):
            message = f"{label} 已是当前使用的RNG 数据。"
            return (
                no_update,
                no_update,
                recent_records,
                request.get("candidate"),
                dbc.Alert(
                    message,
                    color="info",
                    className="py-2 rs-data-inline-feedback",
                    duration=8000,
                ),
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
            dbc.Alert(
                message,
                color="success",
                className="py-2 rs-data-inline-feedback",
                duration=8000,
            ),
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
        Output("dataset-switch-navigation", "data", allow_duplicate=True),
        Input("dataset-restore-result", "data"),
        State("page-store", "data"),
        prevent_initial_call=True,
    )
    def _route_unusable_restored_context(restore_result, page_store):
        """Never leave a restored tab on an analysis page without a dataset."""
        state = str((restore_result or {}).get("state") or "")
        current_page = str((page_store or {}).get("page") or "")
        if state not in {"none", "unavailable"}:
            raise PreventUpdate
        if current_page == "data-management":
            raise PreventUpdate
        return {
            "request_id": f"restore-route-{time.time_ns()}",
            "page": "data-management",
        }

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

    def _channel_columns(items: list[tuple[str, str, int | None]]) -> list[dict[str, Any]]:
        return ui.columns([
            {"name": label, "id": field, **({"presentation": "markdown"} if field == "structure" else {}), **({"type": "numeric"} if field not in {"structure", "smiles", "formula", "reaction_formulas", "recommendation", "association_status", "structure_source"} else {})}
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

    def _species_preview_tooltips(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build lazy same-origin structure previews for identity cells."""
        tooltips: list[dict[str, Any]] = []
        for row in rows:
            smiles = str(row.get("smiles") or "").strip()
            formula = str(row.get("formula") or "未知分子式").strip()
            if not smiles:
                tooltips.append({})
                continue
            preview_url = (
                "/api/structure.svg?"
                f"smiles={quote(smiles, safe='')}&width=280&height=190"
            )
            preview = {
                "value": (
                    f"**{formula}**\n\n"
                    f"![{formula}]({preview_url})\n\n"
                    f"`{smiles}`"
                ),
                "type": "markdown",
            }
            tooltips.append(
                {
                    "formula": preview,
                    "smiles": preview,
                }
            )
        return tooltips

    # ── Species search ──────────────────────────────────────────────

    def _reaction_preview_tooltips(
        rows: list[dict[str, Any]] | None,
        *,
        show_h: bool,
    ) -> list[dict[str, Any]]:
        """Build lazy full-reaction hover cards for reaction-expression cells."""
        tooltips: list[dict[str, Any]] = []
        for row in rows or []:
            reaction_smiles = str(row.get("reaction_smiles") or "").strip()
            if not reaction_smiles:
                tooltips.append({})
                continue
            reaction_formulas = str(
                row.get("reaction_formulas") or reaction_smiles
            ).strip()
            preview_url = (
                "/api/reaction.svg?"
                f"reaction_smiles={quote(reaction_smiles, safe='')}"
                "&width=720&height=220"
                f"&show_h={1 if show_h else 0}"
            )
            preview = {
                "value": (
                    "**完整结构反应式**\n\n"
                    f"![完整结构反应式]({preview_url})\n\n"
                    f"**分子式**  `{reaction_formulas}`\n\n"
                    f"**SMILES**  `{reaction_smiles}`"
                ),
                "type": "markdown",
            }
            tooltips.append(
                {
                    "reaction_formulas": preview,
                    "reaction_smiles": preview,
                }
            )
        return tooltips

    @app.callback(
        Output("rxn-grid-previews", "data"),
        Input("rxn-grid", "rowData"),
        Input("rxn-structure-show-h", "value"),
    )
    def _preview_searched_reactions(rows, show_h):
        return _reaction_preview_tooltips(rows, show_h=bool(show_h))

    @app.callback(
        Output("rxn-production-grid-previews", "data"),
        Output("rxn-consumption-grid-previews", "data"),
        Input("rxn-production-grid", "rowData"),
        Input("rxn-consumption-grid", "rowData"),
        Input("rxn-channel-show-h", "value"),
    )
    def _preview_species_channels(production_rows, consumption_rows, show_h):
        return (
            _reaction_preview_tooltips(production_rows, show_h=bool(show_h)),
            _reaction_preview_tooltips(consumption_rows, show_h=bool(show_h)),
        )

    @ui.guarded_query(app,
        Output("species-grid", "rowData"),
        Output("species-grid", "columnDefs"),
        Output("species-grid-previews", "data"),
        Output("species-alert", "children"),
        Output("species-grid-store", "data"),
        Output("species-grid", "selectedRows"),
        Output("species-grid-page-size", "data"),
        Output("species-grid", "paginationGoTo"),
        Output("species-csv-btn", "children"),
        Output(
            "species-structure-results",
            "style",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-title",
            "children",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-alert",
            "children",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "rowData",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "columnDefs",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid-previews",
            "data",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "selectedRows",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-grid",
            "paginationGoTo",
            allow_duplicate=True,
        ),
        Output(
            "species-structure-csv-btn",
            "disabled",
            allow_duplicate=True,
        ),
        Output("app-store", "data", allow_duplicate=True),
        Output("evolution-targets", "value", allow_duplicate=True),
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
        cleared_structure_results = (
            {"display": "none"},
            "分子式对应结构",
            "",
            [],
            _species_columns(),
            [],
            [],
            0,
            True,
        )
        store = store or {}
        cleared_store = {
            **store,
            "selected_smiles": "",
            "selected_formula": "",
            "selected_species_source": "",
            # This callback executes a fresh species query, so preserved
            # inputs are no longer pending.  The shared marker callback sets
            # the same value; keeping them identical avoids a last-writer
            # race between callbacks triggered by the same button.
            "inputs_pending": False,
        }
        artifacts = store.get("artifacts", {}) or {}
        if not artifacts.get("reaction"):
            return (
                [],
                _species_columns(),
                [],
                '请先在「RNG 数据」中选择包含 reactionabcd 的数据文件夹。',
                {"rows": [], "state": "blocked", "message": "请先选择可检索物种的RNG 数据。"},
                [],
                50,
                0,
                "导出全部 CSV",
                *cleared_structure_results,
                cleared_store,
                "",
            )
        try:
            submitted = species_query(query, kind, mass_tol)
            result = svc.search_species(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=submitted["mass_tolerance"],
            )
        except (svc.ServiceError, ValueError, TypeError) as exc:
            message = str(getattr(exc, "message", exc))
            return (
                [],
                _species_columns(),
                [],
                message,
                {"rows": [], "state": "error", "message": message},
                [],
                50,
                0,
                "导出全部 CSV",
                *cleared_structure_results,
                cleared_store,
                "",
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
            message = ("未找到匹配物种；可以放宽质量容差。" if query_kind == "mass"
                       else "未找到匹配物种；请检查分子式、SMILES 或查询类型。")
        return (
            rows,
            _species_columns(query_kind),
            _species_preview_tooltips(rows),
            message,
            {
                "rows": rows,
                "query_kind": query_kind,
                "n_rows": matching_count,
                "n_visible_rows": len(rows),
                "searched": True,
                "state": "ready" if rows else "empty",
                "query": submitted,
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
            *cleared_structure_results,
            cleared_store,
            "",
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
        Input({"type": "dataset-bound-operation", "name": "species"}, "data"),
    )
    def _update_species_state(store, grid_store, running):
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
            return [], {"display": "none"}, {"display": "block"}, {"display": "none"}, bool(running), False, {}

        if running:
            title, text = "正在查询", "请稍候，查询完成后更新结果。"
        elif grid_store.get("state") in {"error", "blocked"}:
            title = "查询失败" if grid_store["state"] == "error" else "暂时无法查询"
            text = grid_store.get("message", "请检查输入并重试。")
        elif searched:
            text = grid_store.get("message") or "未找到匹配物种；可以放宽质量容差或切换查询类型。"
            title = "没有匹配结果"
        else:
            text = "输入分子式、SMILES 或质量后查询。"
            title = "等待查询"
        empty = [
            html.Div(title, className="rs-empty-title"),
            html.P(text, className="rs-empty-copy"),
        ]
        return empty, {"display": "flex"}, {"display": "none"}, {"display": "none"}, bool(running), True, {}

    @app.callback(
        Output("species-structure-results", "style"),
        Output("species-structure-title", "children"),
        Output("species-structure-alert", "children"),
        Output("species-structure-grid", "rowData"),
        Output("species-structure-grid", "columnDefs"),
        Output("species-structure-grid-previews", "data"),
        Output("species-structure-grid", "selectedRows"),
        Output("species-structure-grid", "paginationGoTo"),
        Output("species-structure-csv-btn", "disabled"),
        Output("app-store", "data", allow_duplicate=True),
        Input("species-grid", "selectedRows"),
        State("species-grid", "rowData"),
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
                [],
                0,
                True,
                no_update,
            )
        row = _selected_table_row(selected_rows, formula_rows)
        formula = str((row or {}).get("formula") or "").strip()
        current_store = store if isinstance(store, dict) else {}
        cleared_store = {
            **current_store,
            "selected_smiles": "",
            "selected_formula": "",
            "selected_species_source": "",
        }
        if not formula:
            return (
                {"display": "none"},
                "分子式对应结构",
                "选择一个候选分子式以查看原始结构结果。",
                [],
                _species_columns(),
                [],
                [],
                0,
                True,
                cleared_store,
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
                [],
                0,
                True,
                cleared_store,
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
            _species_preview_tooltips(rows),
            [],
            0,
            not bool(rows),
            cleared_store,
        )

    @app.callback(
        Output("species-workspace-stage", "data"),
        Input("species-search-btn", "n_clicks"),
        Input("species-grid", "selectedRows"),
        Input("species-structure-grid", "selectedRows"),
        Input("species-stage-results-btn", "n_clicks"),
        Input("species-stage-structures-btn", "n_clicks"),
        Input("species-stage-detail-btn", "n_clicks"),
        Input("species-stage-back-btn", "n_clicks"),
        State("species-workspace-stage", "data"),
        State("species-grid-store", "data"),
        State("species-structure-grid", "rowData"),
        prevent_initial_call=True,
    )
    def _select_species_workspace_stage(
        _search_clicks,
        formula_selected_rows,
        structure_selected_rows,
        _results_clicks,
        _structures_clicks,
        _detail_clicks,
        _back_clicks,
        current_stage,
        grid_store,
        structure_rows,
    ):
        triggered_id = ctx.triggered_id
        query_kind = str((grid_store or {}).get("query_kind") or "")
        is_mass_search = query_kind == "mass"
        has_structures = is_mass_search and bool(structure_rows)
        has_detail = bool(
            structure_selected_rows
            if is_mass_search
            else formula_selected_rows
        )

        if triggered_id in {"species-search-btn", "species-stage-results-btn"}:
            return "results"
        if triggered_id == "species-grid":
            if not formula_selected_rows:
                return "results"
            return "structures" if is_mass_search else "detail"
        if triggered_id == "species-structure-grid":
            if structure_selected_rows:
                return "detail"
            return "structures" if has_structures else "results"
        if triggered_id == "species-stage-structures-btn":
            if has_structures:
                return "structures"
            raise PreventUpdate
        if triggered_id == "species-stage-detail-btn":
            if has_detail:
                return "detail"
            raise PreventUpdate
        if triggered_id == "species-stage-back-btn":
            if current_stage == "detail" and has_structures:
                return "structures"
            if current_stage in {"detail", "structures"}:
                return "results"
        raise PreventUpdate

    @app.callback(
        Output("species-result-stage", "style"),
        Output("species-structure-stage", "style"),
        Output("species-detail-stage", "style"),
        Output("species-stage-results-btn", "active"),
        Output("species-stage-structures-btn", "active"),
        Output("species-stage-detail-btn", "active"),
        Output("species-stage-results-btn", "children"),
        Output("species-stage-structures-btn", "children"),
        Output("species-stage-structures-btn", "disabled"),
        Output("species-stage-detail-btn", "disabled"),
        Output("species-stage-back-btn", "children"),
        Output("species-stage-back-btn", "disabled"),
        Output("species-stage-back-btn", "style"),
        Input("species-workspace-stage", "data"),
        Input("species-grid-store", "data"),
        Input("species-structure-grid", "rowData"),
        Input("species-grid", "selectedRows"),
        Input("species-structure-grid", "selectedRows"),
    )
    def _render_species_workspace_stage(
        requested_stage,
        grid_store,
        structure_rows,
        formula_selected_rows,
        structure_selected_rows,
    ):
        is_mass_search = (
            str((grid_store or {}).get("query_kind") or "") == "mass"
        )
        has_structures = is_mass_search and bool(structure_rows)
        has_detail = bool(
            structure_selected_rows
            if is_mass_search
            else formula_selected_rows
        )
        stage = str(requested_stage or "results")
        if stage == "structures" and not has_structures:
            stage = "results"
        elif stage == "detail" and not has_detail:
            stage = "structures" if has_structures else "results"

        result_count = int(
            (grid_store or {}).get("n_visible_rows")
            or len((grid_store or {}).get("rows") or [])
        )
        result_name = "候选分子式" if is_mass_search else "检索结果"
        result_label = (
            f"{result_name} · {result_count}" if result_count else result_name
        )
        structure_label = (
            f"结构列表 · {len(structure_rows or [])}"
            if has_structures
            else "结构列表"
        )
        back_label = (
            "← 返回结构列表"
            if stage == "detail" and has_structures
            else f"← 返回{result_name}"
        )
        back_visible = stage != "results"
        return (
            {} if not is_mass_search or stage == "results" else {"display": "none"},
            {} if is_mass_search and stage in {"structures", "detail"} else {"display": "none"},
            {} if stage == "detail" else {"display": "none"},
            stage == "results",
            stage == "structures",
            stage == "detail",
            result_label,
            structure_label,
            not has_structures,
            not has_detail,
            back_label,
            not back_visible,
            {} if back_visible else {"display": "none"},
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
        Output("app-store", "data", allow_duplicate=True),
        Output("evolution-targets", "value"),
        Input("species-grid", "selectedRows"),
        Input("species-structure-grid", "selectedRows"),
        State("species-grid", "rowData"),
        State("species-structure-grid", "rowData"),
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
            cleared_store = {
                **store,
                "selected_smiles": "",
                "selected_formula": "",
                "selected_species_source": "",
            }
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                True,
                cleared_store if store.get("selected_smiles") else no_update,
                "",
            )
        row = _selected_table_row(selected_rows, table_rows)
        if row is None:
            raise PreventUpdate
        smiles = (row.get("smiles") or "").strip()
        if not smiles:
            cleared_store = {
                **store,
                "selected_smiles": "",
                "selected_formula": "",
                "selected_species_source": "",
            }
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                True,
                cleared_store if store.get("selected_smiles") else no_update,
                "",
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
                _species_identity_context_panel(detail),
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
            updated_store,
            evolution_target,
        )

    # ── Reaction formula search ─────────────────────────────────────

    @ui.guarded_query(app,
        Output("rxn-grid", "rowData"),
        Output("rxn-grid", "columnDefs"),
        Output("rxn-grid", "selectedRows"),
        Output("rxn-grid", "cellClicked"),
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
        from .ui_state import reaction_query

        submitted = reaction_query(reactants, products, mode, top, with_share,
                                   share_metric, share_abs, share_positive)
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            result = svc.search_reactions_by_formula(
                artifacts,
                reactants or "",
                products or "",
                mode=mode or "exact",
                top=int(top if top is not None else 50),
                with_share=bool(with_share),
                share_metric=share_metric or "net_tp",
                share_abs_metric=bool(share_abs),
                share_positive_only=bool(share_positive),
            )
        except (svc.ServiceError, ValueError, TypeError) as exc:
            message = str(getattr(exc, "message", exc))
            return (
                [],
                _reaction_columns(),
                [],
                None,
                message,
                {"rows": [], "state": "error", "message": message, "query": submitted},
                {"display": "none"},
                {"display": "block"},
            )
        rows = result.get("rows") or []
        timing = (result.get("meta") or {}).get("timing") or {}
        timing_notice = (
            "事件索引未就绪，首次/末次时间不可用；请在管理数据中准备或重建事件索引。"
            if str(timing.get("status") or "").startswith("event_index_") or timing.get("status") == "missing_reactionevent"
            else "未确认 timestep → ps 换算；时间列保留原始坐标。"
            if timing.get("status") == "time_conversion_missing"
            else "当前事件源只有分析帧序号；时间列单位为 analyzed_frame。"
            if timing.get("status") == "analyzed_frame_only" else None
        )
        return (
            rows,
            _reaction_columns(with_share=bool(with_share)),
            [],
            None,
            f"找到 {len(rows)} 条反应" + (f" · {timing_notice}" if timing_notice else ""),
            {"rows": rows, "meta": result.get("meta", {}), "query": submitted,
             "state": "ready" if rows else "empty"},
            {"display": "none"},
            {"display": "block"},
        )

    @app.callback(
        Output("rxn-query-card", "style"),
        Output("rxn-results-card", "style"),
        Output("rxn-channel-view", "style"),
        Output("rxn-channel-history-store", "data"),
        Input("species-to-channels-btn", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        prevent_initial_call=True,
    )
    def _toggle_reaction_view(
        _channel_clicks,
        _event_clicks,
        _nav_clicks,
    ):
        if ctx.triggered_id in {
            "species-to-channels-btn",
            "species-to-event-btn",
        }:
            return (
                {"display": "none"},
                {"display": "none"},
                {"display": "block"},
                [{"kind": "species", "label": "返回物种检索"}],
            )
        return {}, {}, {"display": "none"}, []

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
    def _save_channel_timestep_ps(n_clicks, timestep_ps, top, store):
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
            selected_smiles = str(store.get("selected_smiles") or "").strip()
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
            selected_smiles = str(current.get("selected_smiles") or "").strip()
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
        prevent_initial_call=True,
    )
    def _download_species_channels(
        production_clicks,
        consumption_clicks,
        production_rows,
        consumption_rows,
        store,
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
        focus_formula = str(store.get("selected_formula") or "").strip()
        focus_smiles = str(store.get("selected_smiles") or "").strip()
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
        Output("app-store", "data", allow_duplicate=True),
        Output("rxn-query-card", "style", allow_duplicate=True),
        Output("rxn-results-card", "style", allow_duplicate=True),
        Output("rxn-channel-view", "style", allow_duplicate=True),
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
            "selected_smiles": store.get("selected_smiles") or "",
            "selected_formula": store.get("selected_formula") or "",
            "selected_species_source": (
                store.get("selected_species_source") or ""
            ),
        }
        history = list(history) if isinstance(history, list) else []
        if triggered.get("scope") == "channel":
            previous_target = focus["selected_formula"] or focus["selected_smiles"]
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
        updated_store = {
            **store,
            "selected_smiles": selected_smiles,
            "selected_formula": selected_formula,
            "selected_species_source": "reaction_structure",
        }
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
            updated_store,
            {"display": "none"},
            {"display": "none"},
            {"display": "block"},
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

    @app.callback(
        Output("event-reaction-text", "value"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("rxn-channel-to-event-btn", "n_clicks"),
        State("rxn-grid", "selectedRows"),
        State("rxn-grid", "rowData"),
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
        row = _selected_table_row(selected_rows, rows)
        if row is None:
            raise PreventUpdate
        return str(row.get("reaction_smiles") or "")

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
        State("species-structure-grid", "rowData"),
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

    # ── Evolution ───────────────────────────────────────────────────

    @app.callback(
        Output("evolution-species-picker", "options"),
        Output("evolution-species-picker", "value"),
        Output("evolution-catalog-alert", "children"),
        Input("evolution-load-species-btn", "n_clicks"),
        State("evolution-species-file", "value"),
        State("evolution-species-files", "value"),
        State("evolution-species-picker", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[
            (Output("evolution-load-species-btn", "disabled"), True, False),
        ],
    )
    def _load_evolution_species_catalog(
        n_clicks,
        species_file,
        species_files,
        selected_values,
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        artifacts = ((store or {}).get("artifacts") or {})
        try:
            catalog = svc.species_evolution_catalog(
                artifacts,
                species_file=species_file or "",
                species_files=species_files or "",
            )
        except svc.ServiceError as exc:
            return [], [], str(exc.message)

        options = catalog.get("options") or []
        valid_values = {str(option.get("value")) for option in options}
        kept_values = [
            str(value)
            for value in (selected_values or [])
            if str(value) in valid_values
        ]
        meta = catalog.get("meta") or {}
        message = (
            f"已从 {int(meta.get('n_sources') or 0)} 个文件读取 "
            f"{int(meta.get('n_formulas') or 0)} 种分子式"
        )
        warnings = [
            str(item)
            for item in (meta.get("warnings") or [])
            if str(item).strip()
        ]
        if warnings:
            message = f"{message}；{'；'.join(warnings)}"
        return options, kept_values, message

    @app.callback(
        Output("evolution-graph", "figure"),
        Output("evolution-alert", "children"),
        Output("evolution-payload-store", "data"),
        Output("import-pending-evolution", "data"),
        Input("evolution-search-btn", "n_clicks"),
        Input("import-auto-result", "data"),
        State("evolution-species-picker", "value"),
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
        State("import-pending-evolution", "data"),
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
    @defer_query("composition", 3)
    def _build_evolution(
        n_clicks,
        selected_species,
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
        picked_targets = [
            str(item).strip()
            for item in (selected_species or [])
            if str(item).strip()
        ]
        manual_targets = [
            t.strip()
            for t in re.split(r"[,;\n]+", targets_text or "")
            if t.strip()
        ]
        targets = list(dict.fromkeys([*picked_targets, *manual_targets]))
        if not targets:
            targets_text_default = store.get("selected_formula") or store.get("selected_smiles") or ""
            targets = [targets_text_default] if targets_text_default else []
        if not targets:
            return empty_chart_figure("尚未选择目标物种", "输入目标或从物种目录选择后再绘制。"), "请先输入目标物种或分子式（或用物种检索中选择的物种）。", None
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
            return empty_chart_figure("本次绘图未完成", "请查看上方提示，调整设置后重试。"), str(exc.message), None

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
        if not fig.data:
            fig = go.Figure(empty_chart_figure("没有可显示的曲线", "请检查目标物种、曲线筛选及数据范围。"))
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

    @app.callback(
        Output("evolution-csv-btn", "disabled"),
        Input("evolution-payload-store", "data"),
    )
    def _evolution_export_available(payload):
        return not bool(payload and payload.get("curves"))

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
        Output("evolution-graph", "clickData"),
        Output(
            "evolution-structure-detail",
            "children",
            allow_duplicate=True,
        ),
        Input("evolution-search-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_evolution_curve_selection(n_clicks):
        if n_clicks is None:
            raise PreventUpdate
        return None, "点击一条演化曲线，查看其成员物种结构。"

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
        Output("import-pending-elements", "data"),
        Input("element-distribution-search-btn", "n_clicks"),
        Input("import-auto-result", "data"),
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
        State("import-pending-elements", "data"),
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
    @defer_query("composition", 4)
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
        Output("element-distribution-composition-table", "selectedRows"),
        Output("element-distribution-composition-trend", "clickData"),
        Input("element-distribution-search-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _clear_element_distribution_selection(n_clicks):
        if n_clicks is None:
            raise PreventUpdate
        return [], None

    @app.callback(
        Output("element-distribution-composition-table", "columnDefs"),
        Output("element-distribution-composition-table", "rowData"),
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
        Input("element-distribution-composition-table", "selectedRows"),
        Input("element-distribution-structure-show-h", "value"),
        State("element-distribution-composition-table", "rowData"),
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
            text = "请先在“RNG 数据”中选择包含 .species 的RNG 数据"
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

    # ── Exact reaction timing ───────────────────────────────────────

    @app.callback(
        Output("rxn-timing-distribution-store", "data"),
        Output("rxn-timing-graph", "figure"),
        Output("rxn-timing-status", "children"),
        Output("rxn-timing-card", "style", allow_duplicate=True),
        Output("rxn-timing-start", "value"),
        Output("rxn-timing-end", "value"),
        Output("rxn-timing-width", "value"),
        Output("rxn-timing-graph", "clickData"),
        Input("rxn-grid", "selectedRows"),
        Input("rxn-channel-selection-store", "data"),
        Input("rxn-timing-apply-btn", "n_clicks"),
        State("rxn-grid", "rowData"),
        State("rxn-timing-distribution-store", "data"),
        State("rxn-timing-start", "value"),
        State("rxn-timing-end", "value"),
        State("rxn-timing-width", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[(
            Output({"type": "dataset-bound-operation", "name": "reaction-timing-chart"}, "data"),
            True, False,
        )],
    )
    def _show_reaction_timing(
        selected, channel_selection, _apply, rows, previous,
        start, end, width, store,
    ):
        trigger = ctx.triggered_id
        dataset_id = (store or {}).get("dataset_id")
        if trigger == "rxn-timing-apply-btn":
            if not previous or previous.get("dataset_id") != dataset_id:
                raise PreventUpdate
            row = previous["selection"]
            requested = {"start": start, "end": end, "width": width}
        elif trigger == "rxn-channel-selection-store":
            row = ((channel_selection or {}).get("row") or {})
            requested = {}
        else:
            row = _selected_table_row(selected, rows) or {}
            requested = {}
        if not row:
            return None, go.Figure(), "", {"display": "none"}, None, None, None, None
        reactants = list(row.get("reactant_smiles") or [])
        products = list(row.get("product_smiles") or [])
        try:
            result = svc.reaction_time_distribution(
                (store or {}).get("artifacts") or {}, reactants, products,
                **requested,
            )
        except svc.ServiceError as exc:
            return None, go.Figure(), exc.message, {"display": "block"}, start, end, width, None
        bins = result["bins"]
        fig = go.Figure()
        for direction, label, color in (
            ("forward", "正向", "#337ab7"),
            ("reverse", "严格逆向", "#d88442"),
        ):
            if direction == "reverse" and result["self_reverse"]:
                continue
            fig.add_bar(
                name=label,
                x=[(item["start"] + item["end"]) / 2 for item in bins],
                y=[item[direction] for item in bins],
                width=[item["end"] - item["start"] for item in bins],
                marker_color=color,
                customdata=[[item["index"], direction] for item in bins],
                hovertemplate="%{y} 次<extra>" + label + "</extra>",
            )
        fig.update_layout(
            barmode="group", xaxis_title=f"事件后帧时间 / {result['unit']}",
            yaxis_title="发生次数", xaxis_range=[result["start"], result["end"]],
            margin={"l": 50, "r": 15, "t": 15, "b": 45},
        )
        note = (
            f"时间窗 [{result['start']:g}, {result['end']:g}) {result['unit']}；"
            f"分箱宽度 {result['width']:g} {result['unit']}；"
            f"窗内正向 {result['forward_total']} 次、逆向 {result['reverse_total']} 次。"
            "反应发生于前后采样帧之间，后帧时间仅用于定位。"
        )
        if result["self_reverse"]:
            note += "此反应完全自反，只显示一组事件。"
        if result["unit"] != "ps":
            note += "未确认 timestep → ps 换算或仅有分析帧序号；当前不显示 ps。"
        return (
            {**result, "selection": {"reactant_smiles": reactants,
                                    "product_smiles": products,
                                    "reaction_smiles": row.get("reaction_smiles")},
             "dataset_id": dataset_id},
            fig, note, {"display": "block"},
            result["start"], result["end"], result["width"], None,
        )

    @app.callback(
        Output("rxn-timing-page-store", "data", allow_duplicate=True),
        Output("rxn-timing-event-grid", "rowData", allow_duplicate=True),
        Output("rxn-timing-event-grid", "columnDefs"),
        Output("rxn-timing-bin-status", "children"),
        Input("rxn-timing-graph", "clickData"),
        Input("rxn-timing-prev-btn", "n_clicks"),
        Input("rxn-timing-next-btn", "n_clicks"),
        Input("rxn-timing-distribution-store", "data"),
        State("rxn-timing-page-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
        running=[(
            Output({"type": "dataset-bound-operation", "name": "reaction-timing-page"}, "data"),
            True, False,
        )],
    )
    def _page_reaction_timing(click_data, _prev, _next, distribution, previous, store):
        empty = (None, [], _event_columns(), "点击时间箱查看该方向的全部事件，可逐页核查。")
        if ctx.triggered_id == "rxn-timing-distribution-store" or not distribution:
            return empty
        dataset_id = (store or {}).get("dataset_id")
        if distribution.get("dataset_id") != dataset_id:
            return empty
        if ctx.triggered_id == "rxn-timing-graph":
            point = ((click_data or {}).get("points") or [{}])[0]
            custom = point.get("customdata") or []
            if len(custom) != 2:
                return empty
            index, direction = int(custom[0]), str(custom[1])
            if not 0 <= index < len(distribution["bins"]) or direction not in {"forward", "reverse"}:
                return empty
            item = distribution["bins"][index]
            key = distribution["reaction_key"] if direction == "forward" else distribution["reverse_reaction_key"]
            offset = 0
        else:
            if not previous or previous.get("dataset_id") != dataset_id:
                return empty
            item = previous["bin"]
            direction = previous["direction"]
            key = previous["reaction_key"]
            offset = max(0, int(previous["offset"]) + (25 if ctx.triggered_id == "rxn-timing-next-btn" else -25))
            if offset >= int(previous["total"]):
                offset = int(previous["offset"])
        try:
            page = svc.reaction_time_events(
                (store or {}).get("artifacts") or {}, key,
                start_raw=item["start_raw"], end_raw=item["end_raw"],
                offset=offset, limit=25,
            )
        except svc.ServiceError as exc:
            return None, [], _event_columns(), exc.message
        for event in page["rows"]:
            event["selected_direction"] = direction
            event["bin_start_raw"] = item["start_raw"]
            event["bin_end_raw"] = item["end_raw"]
            event["bin_start"] = item["start"]
            event["bin_end"] = item["end"]
        text = (
            f"{direction} [{item['start']:g}, {item['end']:g}) {distribution['unit']}："
            f"{page['total']} 个事件；当前第 {offset + 1 if page['total'] else 0}–"
            f"{offset + len(page['rows'])} 个。"
        )
        return (
            {**page, "bin": item, "direction": direction,
             "reaction_key": key, "dataset_id": dataset_id,
             "reaction_smiles": distribution["selection"].get("reaction_smiles")},
            _event_table_rows(page["rows"]), _event_columns(), text,
        )

    @app.callback(
        Output("rxn-timing-prev-btn", "disabled"),
        Output("rxn-timing-next-btn", "disabled"),
        Output("rxn-timing-open-events-btn", "disabled"),
        Output("rxn-timing-csv-btn", "disabled"),
        Input("rxn-timing-page-store", "data"),
    )
    def _timing_page_actions(page):
        page = page or {}
        total = int(page.get("total") or 0)
        offset = int(page.get("offset") or 0)
        length = len(page.get("rows") or [])
        return offset == 0, offset + length >= total, length == 0, length == 0

    @app.callback(
        Output("event-grid-store", "data", allow_duplicate=True),
        Output("event-grid", "rowData", allow_duplicate=True),
        Output("event-grid", "columnDefs", allow_duplicate=True),
        Output("event-alert", "children", allow_duplicate=True),
        Output("event-reaction-text", "value", allow_duplicate=True),
        Input("rxn-timing-open-events-btn", "n_clicks"),
        State("rxn-timing-page-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _open_timing_events(_clicks, page, store):
        if not page or page.get("dataset_id") != (store or {}).get("dataset_id"):
            raise PreventUpdate
        rows = page.get("rows") or []
        return (
            {"rows": rows, "kind": "rng_event", "config": {"max_events": 25},
             "meta": {"message": "来自所选时间箱；可选择事件查看详情与轨迹。"}},
            _event_table_rows(rows), _event_columns(),
            "来自所选时间箱；请选择具体事件查看详情与轨迹。",
            page.get("reaction_smiles") or "",
        )

    @app.callback(
        Output("rxn-timing-csv-download", "data"),
        Input("rxn-timing-csv-btn", "n_clicks"),
        State("rxn-timing-page-store", "data"),
        prevent_initial_call=True,
    )
    def _download_timing_events(_clicks, page):
        if not page or not page.get("rows"):
            raise PreventUpdate
        return {"content": svc.rows_to_csv(page["rows"]),
                "filename": "reaction_time_events.csv", "type": "text/csv"}

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
            "← 返回候选路线"
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
                origin.get("action") == "cp-track-instance",
            )
        return (
            summary,
            structure_detail,
            False,
            "载入当前实例的局部轨迹",
            return_label,
            origin.get("action") == "cp-track-instance",
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

    @app.callback(
        Output("event-dft-card", "style"),
        Output("event-dft-reactants", "options"),
        Output("event-dft-reactants", "value"),
        Output("event-dft-products", "options"),
        Output("event-dft-products", "value"),
        Output("event-dft-unit-confirmation", "value"),
        Output("event-dft-isolated-cluster-confirmation", "value"),
        Output("event-dft-preview-btn", "disabled"),
        Output("event-dft-alert", "children"),
        Input("event-selected-store", "data"),
        Input("event-viewer-store", "data"),
        State("app-store", "data"),
    )
    def _prepare_dft_geometry(selected, viewer, app_store):
        row = (selected or {}).get("row") or {}
        viewer_event_id = str((viewer or {}).get("event_id") or "")
        event_id = str(row.get("event_id") or "")
        if (
            not row
            or row.get("association_status") != "matched"
            or not viewer
            or viewer_event_id != event_id
        ):
            return {"display": "none"}, [], [], [], [], [], [], True, (
                "只有具有精确 Molecular Evidence 的 matched 事件可以导出 DFT 几何。"
            )
        reactants = _dft_participant_options(row, "reactant")
        products = _dft_participant_options(row, "product")
        trajectory = str(
            (((app_store or {}).get("artifacts") or {}).get("trajectory") or "")
        )
        try:
            confirmed = svc.load_coordinate_length_unit(trajectory) == "angstrom"
        except (OSError, ValueError):
            confirmed = False
        message = (
            f"反应物使用 timestep {row.get('before_timestep')}；"
            f"产物使用 timestep {row.get('after_timestep')}。"
        )
        return (
            {"display": "block"},
            reactants,
            [option["value"] for option in reactants],
            products,
            [option["value"] for option in products],
            ["angstrom"] if confirmed else [],
            [],
            not bool(reactants or products),
            message,
        )

    @app.callback(
        Output("event-dft-electronic-states", "children"),
        Input("event-dft-reactants", "value"),
        Input("event-dft-products", "value"),
        Input("event-dft-layout", "value"),
        State("event-selected-store", "data"),
    )
    def _render_dft_electronic_states(
        reactant_indices,
        product_indices,
        layout,
        selected,
    ):
        row = (selected or {}).get("row") or {}
        stems = _dft_output_stems(
            row,
            reactant_indices,
            product_indices,
            str(layout or "combined"),
        )
        if not stems:
            return html.Div("选择至少一个 Molecule Instance。", className="rs-step-note")
        return [
            html.Div(
                [
                    html.Code(stem, className="rs-dft-state-label"),
                    dbc.Input(
                        id={"type": "event-dft-charge", "stem": stem},
                        type="number",
                        step=1,
                        placeholder="总电荷",
                        className="rs-dft-state-input",
                    ),
                    dbc.Input(
                        id={"type": "event-dft-multiplicity", "stem": stem},
                        type="number",
                        min=1,
                        step=1,
                        placeholder="多重度",
                        className="rs-dft-state-input",
                    ),
                ],
                className="rs-dft-state-row",
            )
            for stem in stems
        ]

    app.clientside_callback(
        """function(n, reactants, products, layout, unit, isolated, charges,
                    chargeIds, multiplicities, multiplicityIds, selected, store) {
            if (!n) return window.dash_clientside.no_update;
            const id = (window.crypto && window.crypto.randomUUID)
                ? window.crypto.randomUUID()
                : String(Date.now()) + '-' + String(Math.random());
            return {id, controls: {
                reactant_indices: reactants ?? [], product_indices: products ?? [],
                layout: layout ?? 'combined', unit_confirmation: unit ?? [],
                isolated_cluster_confirmation: isolated ?? [],
                charge_values: charges ?? [], charge_ids: chargeIds ?? [],
                multiplicity_values: multiplicities ?? [], multiplicity_ids: multiplicityIds ?? [],
                selected: selected ?? null, app_store: store ?? null
            }};
        }""",
        Output("event-dft-request", "data"),
        Input("event-dft-preview-btn", "n_clicks"),
        State("event-dft-reactants", "value"),
        State("event-dft-products", "value"),
        State("event-dft-layout", "value"),
        State("event-dft-unit-confirmation", "value"),
        State("event-dft-isolated-cluster-confirmation", "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "id"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "value"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "id"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("event-dft-response", "data"),
        Input("event-dft-request", "data"),
        prevent_initial_call=True,
        running=[
            (
                Output(
                    {"type": "dataset-bound-operation", "name": "dft-geometry"},
                    "data",
                ),
                True,
                False,
            )
        ],
    )
    def _preview_dft_geometry(request):
        if not request:
            raise PreventUpdate
        controls = request["controls"]
        request_id = request["id"]
        try:
            evaluation = _build_dft_bundle_from_controls(**controls)
        except (
            svc.DftGeometryError,
            svc.ServiceError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return {"request_id": request_id, "payload": None,
                    "validation": dbc.Alert(message, color="danger", className="py-2 mb-0"),
                    "summary": [], "options": [], "file": None,
                    "panel": {"display": "none"}, "disabled": True}
        report = evaluation.report
        status = str((report.get("qc_handoff") or {}).get("status") or "blocked")
        validation = _dft_readiness_validation(report)
        if evaluation.bundle is None:
            return {"request_id": request_id,
                    "payload": {"readiness_report": report,
                                "request_fingerprint": _dft_request_fingerprint(**controls)},
                    "validation": validation,
                    "summary": [html.Span(status, className="rs-stat-chip")],
                    "options": [], "file": None,
                    "panel": {"display": "none"}, "disabled": True}
        bundle = evaluation.bundle
        manifest = bundle.manifest
        warnings = [
            warning
            for geometry in manifest.get("geometries") or []
            for warning in geometry.get("warnings") or []
        ]
        geometry_meta = manifest.get("geometries") or []
        summary = [
            html.Span(status, className="rs-stat-chip"),
            html.Span(f"文件 {len(bundle.geometries)}", className="rs-stat-chip"),
            html.Span(
                f"最大 {max(int(item.get('atom_count') or 0) for item in geometry_meta)} 原子",
                className="rs-stat-chip",
            ),
            html.Span(f"警告 {len(warnings)}", className="rs-stat-chip"),
            html.Span("单位 Å", className="rs-stat-chip"),
        ]
        options = [
            {"label": name, "value": name} for name in sorted(bundle.geometries)
        ]
        return {"request_id": request_id,
                "payload": {**bundle.preview_payload(),
                            "request_fingerprint": _dft_request_fingerprint(**controls)},
                "validation": validation, "summary": summary,
                "options": options, "file": options[0]["value"],
                "panel": {"display": "block"}, "disabled": status != "ready"}

    app.clientside_callback(
        """function(response, request, reactants, products, layout, unit,
                    isolated, charges, chargeIds, multiplicities, multiplicityIds,
                    selected, store) {
            const empty = [null, [], [], [], null, {display: 'none'}, true, []];
            if (!response || !request || response.request_id !== request.id)
                return empty;
            const current = {
                reactant_indices: reactants ?? [], product_indices: products ?? [],
                layout: layout ?? 'combined', unit_confirmation: unit ?? [],
                isolated_cluster_confirmation: isolated ?? [],
                charge_values: charges ?? [], charge_ids: chargeIds ?? [],
                multiplicity_values: multiplicities ?? [], multiplicity_ids: multiplicityIds ?? [],
                selected: selected ?? null, app_store: store ?? null
            };
            const stable = x => x && typeof x === 'object'
                ? (Array.isArray(x) ? x.map(stable)
                    : Object.fromEntries(Object.keys(x).sort().map(k => [k, stable(x[k])])))
                : x;
            if (JSON.stringify(stable(current)) !== JSON.stringify(stable(request.controls)))
                return empty;
            return [response.payload, response.validation, response.summary,
                    response.options, response.file, response.panel,
                    response.disabled, []];
        }""",
        Output("event-dft-store", "data", allow_duplicate=True),
        Output("event-dft-validation", "children", allow_duplicate=True),
        Output("event-dft-summary", "children", allow_duplicate=True),
        Output("event-dft-preview-file", "options", allow_duplicate=True),
        Output("event-dft-preview-file", "value", allow_duplicate=True),
        Output("event-dft-preview-panel", "style", allow_duplicate=True),
        Output("event-dft-download-btn", "disabled", allow_duplicate=True),
        Output("event-dft-review-confirmation", "value", allow_duplicate=True),
        Input("event-dft-response", "data"),
        Input("event-dft-request", "data"),
        Input("event-dft-reactants", "value"),
        Input("event-dft-products", "value"),
        Input("event-dft-layout", "value"),
        Input("event-dft-unit-confirmation", "value"),
        Input("event-dft-isolated-cluster-confirmation", "value"),
        Input({"type": "event-dft-charge", "stem": ALL}, "value"),
        Input({"type": "event-dft-charge", "stem": ALL}, "id"),
        Input({"type": "event-dft-multiplicity", "stem": ALL}, "value"),
        Input({"type": "event-dft-multiplicity", "stem": ALL}, "id"),
        Input("event-selected-store", "data"),
        Input("app-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("event-dft-review-confirmation", "options"),
        Input("event-dft-store", "data"),
    )
    def _bind_dft_review_to_report(payload):
        report = (payload or {}).get("readiness_report") or {}
        if (report.get("qc_handoff") or {}).get("status") != "review_required":
            return []
        return [{"label": "我已逐项复核当前报告的 review_required 警告",
                 "value": _dft_review_token(payload)}]

    @app.callback(
        Output("event-dft-download-btn", "disabled", allow_duplicate=True),
        Input("event-dft-review-confirmation", "value"),
        State("event-dft-store", "data"),
        prevent_initial_call=True,
    )
    def _acknowledge_dft_review(confirmation, payload):
        report = (payload or {}).get("readiness_report") or {}
        status = str((report.get("qc_handoff") or {}).get("status") or "")
        if status == "ready":
            return False
        return not (
            status == "review_required"
            and _dft_review_token(payload) in (confirmation or [])
            and bool((payload or {}).get("geometries"))
        )

    @app.callback(
        Output("event-dft-preview-text", "children"),
        Input("event-dft-preview-file", "value"),
        Input("event-dft-store", "data"),
    )
    def _render_dft_preview(filename, payload):
        return str(((payload or {}).get("geometries") or {}).get(filename) or "")

    @app.callback(
        Output("event-dft-download", "data"),
        Output("event-dft-validation", "children", allow_duplicate=True),
        Output("event-dft-download-btn", "disabled", allow_duplicate=True),
        Input("event-dft-download-btn", "n_clicks"),
        State("event-dft-store", "data"),
        State("event-selected-store", "data"),
        State("event-dft-review-confirmation", "value"),
        State("app-store", "data"),
        State("event-dft-reactants", "value"),
        State("event-dft-products", "value"),
        State("event-dft-layout", "value"),
        State("event-dft-unit-confirmation", "value"),
        State("event-dft-isolated-cluster-confirmation", "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "value"),
        State({"type": "event-dft-charge", "stem": ALL}, "id"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "value"),
        State({"type": "event-dft-multiplicity", "stem": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _download_dft_geometry(
        n_clicks,
        payload,
        selected,
        review_confirmation,
        app_store,
        reactant_indices,
        product_indices,
        layout,
        unit_confirmation,
        isolated_cluster_confirmation,
        charge_values,
        charge_ids,
        multiplicity_values,
        multiplicity_ids,
    ):
        if n_clicks is None or not payload:
            raise PreventUpdate

        def require_new_preview(message: str):
            return no_update, dbc.Alert(message, color="warning", className="py-2 mb-0"), True

        report = payload.get("readiness_report") or {}
        status = str((report.get("qc_handoff") or {}).get("status") or "")
        if status not in {"ready", "review_required"}:
            return require_new_preview("交接条件尚未满足；请补全输入后重新预检。")
        if status == "review_required" and _dft_review_token(payload) not in (
            review_confirmation or []
        ):
            return require_new_preview("请重新复核并确认当前报告的全部警告，然后下载。")
        controls = dict(
            selected=selected, app_store=app_store,
            reactant_indices=reactant_indices, product_indices=product_indices,
            layout=layout, unit_confirmation=unit_confirmation,
            isolated_cluster_confirmation=isolated_cluster_confirmation,
            charge_values=charge_values, charge_ids=charge_ids,
            multiplicity_values=multiplicity_values, multiplicity_ids=multiplicity_ids,
        )
        if payload.get("request_fingerprint") != _dft_request_fingerprint(**controls):
            return require_new_preview("事件、数据集或几何参数已变化；请重新预检后下载。")
        try:
            evaluation = _build_dft_bundle_from_controls(**controls)
        except (svc.DftGeometryError, svc.ServiceError, OSError, RuntimeError, TypeError, ValueError):
            return require_new_preview("交接证据已变化或暂不可读取；请检查数据并重新预检。")
        bundle = evaluation.bundle
        expected = {key: value for key, value in payload.items() if key != "request_fingerprint"}
        if bundle is None or bundle.preview_payload() != expected:
            return require_new_preview("源数据、设置或检查结果已变化；请重新预检并复核当前报告。")
        event_id = str(((selected or {}).get("row") or {}).get("event_id") or "event")
        return (
            dcc.send_bytes(bundle.to_zip(), f"{event_id}_qc_handoff.zip", type="application/zip"),
            no_update,
            no_update,
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
        Output("molecule-lineage-store", "data"),
        Output("molecule-lineage-alert", "children"),
        Output("molecule-lineage-summary", "children"),
        Output("molecule-lineage-truncation", "children"),
        Output("molecule-lineage-results", "style"),
        Output("molecule-lineage-json-btn", "disabled"),
        Output("molecule-lineage-csv-btn", "disabled"),
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
        try:
            if triggered == "molecule-lineage-continue-btn":
                if not existing_report or not branch_id:
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
                return (
                    no_update,
                    dbc.Alert(message, color="danger", className="py-2 mb-0"),
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                )
            return (
                None,
                dbc.Alert(message, color="danger", className="py-2 mb-0"),
                [],
                "",
                {"display": "none"},
                True,
                True,
            )
        summary_chips, truncation_text = _molecule_lineage_result_presentation(
            report
        )
        message = (
            "所选分支已继续追踪，并按稳定事件和分子身份合并；既有片段保持不变。"
            if triggered == "molecule-lineage-continue-btn"
            else "分支追踪已完成。图用于总览；下方事件表和导出文件是审计依据。"
        )
        return (
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
    )
    def _render_molecule_lineage(report, view):
        if not report:
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
        }
        return selected

    @app.callback(
        Output("molecule-lineage-json-download", "data"),
        Input("molecule-lineage-json-btn", "n_clicks"),
        State("molecule-lineage-store", "data"),
        prevent_initial_call=True,
    )
    def _download_molecule_lineage_json(n_clicks, report):
        if n_clicks is None or not report:
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
        prevent_initial_call=True,
    )
    def _download_molecule_lineage_csv(n_clicks, report):
        if n_clicks is None or not report:
            raise PreventUpdate
        event_id = str((report.get("query") or {}).get("event_id") or "lineage")
        return {
            "content": svc.molecule_lineage_to_csv(report),
            "filename": f"molecule-lineage-{event_id}.csv",
            "type": "text/csv",
        }

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
        Output("event-distances-csv-download", "data"),
        Input("event-distances-csv-btn", "n_clicks"),
        State("event-viewer-store", "data"),
        prevent_initial_call=True,
    )
    def _download_event_changed_bond_distances(n_clicks, viewer):
        if n_clicks is None or not viewer:
            raise PreventUpdate
        event_id = str(viewer.get("event_id") or "event")
        return {
            "content": svc.event_viewer_changed_bond_distances_csv(viewer),
            "filename": f"{event_id}_changed_bond_distances.csv",
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

    # ── Batch comparison ────────────────────────────────────────────

    @app.callback(
        Output("species-compare-managed", "options"),
        Output("species-compare-managed", "value"),
        Input("batch-managed-store", "data"),
        State("species-compare-sources-store", "data"),
    )
    def _species_compare_managed_options(managed, sources):
        options = []
        managed_paths = {
            f"{item.get('base')}.species"
            for item in (managed or {}).get("datasets", [])
            if item.get("base")
        }
        selected_paths = {
            str(item.get("species_file") or "")
            for item in (sources or [])
            if item.get("source_origin") == "managed"
            or (
                not item.get("source_origin")
                and item.get("species_file") in managed_paths
            )
        }
        selected = []
        for item in (managed or {}).get("datasets", []):
            if not item.get("id"):
                continue
            species_file = f"{item.get('base')}.species"
            if species_file in selected_paths:
                selected.append(str(item["id"]))
            catalog = svc.species_compare_catalog(species_file)
            status = str(catalog.get("status") or "missing_source")
            status_label = {
                "ready": "丰度索引可用",
                "missing_index": "需准备丰度索引",
                "missing_source": "缺少 Species 来源",
            }.get(status, "状态未知")
            options.append(
                {
                    "label": (
                        f"{str(item.get('label') or item.get('base'))} · "
                        f"{status_label}"
                    ),
                    "value": str(item["id"]),
                }
            )
        return options, selected

    @app.callback(
        Output("species-compare-sources-store", "data"),
        Output("species-compare-entry-note", "children"),
        Input("species-compare-managed", "value"),
        Input("species-compare-add-path", "n_clicks"),
        Input("evolution-open-compare-btn", "n_clicks"),
        Input({"type": "species-compare-remove", "path": ALL}, "n_clicks"),
        State("batch-managed-store", "data"),
        State("species-compare-path", "value"),
        State("species-compare-new-label", "value"),
        State("species-compare-sources-store", "data"),
        State({"type": "species-compare-target", "path": ALL}, "value"),
        State({"type": "species-compare-target", "path": ALL}, "id"),
        State({"type": "species-compare-label", "path": ALL}, "value"),
        State({"type": "species-compare-label", "path": ALL}, "id"),
        State({"type": "species-compare-model-iteration", "path": ALL}, "value"),
        State({"type": "species-compare-model-iteration", "path": ALL}, "id"),
        State({"type": "species-compare-condition", "path": ALL}, "value"),
        State({"type": "species-compare-condition", "path": ALL}, "id"),
        State({"type": "species-compare-replicate", "path": ALL}, "value"),
        State({"type": "species-compare-replicate", "path": ALL}, "id"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _edit_species_compare_sources(
        managed_ids, _add_path, _handoff, _remove, managed, path_text,
        new_label, sources, targets, target_ids, labels, label_ids,
        model_iterations, model_iteration_ids, conditions, condition_ids,
        replicates, replicate_ids, app_store,
    ):
        sources = [dict(item) for item in (sources or [])]
        current = {item["species_file"]: item for item in sources}
        for value, item_id in zip(targets or [], target_ids or []):
            path = item_id["path"]
            if path in current:
                current[path]["target_smiles"] = str(value or "").strip()
        for value, item_id in zip(labels or [], label_ids or []):
            path = item_id["path"]
            if path in current:
                current[path]["label"] = str(value or "").strip()
        for field, values, item_ids in (
            ("model_iteration", model_iterations, model_iteration_ids),
            ("simulation_condition", conditions, condition_ids),
            ("replicate", replicates, replicate_ids),
        ):
            for value, item_id in zip(values or [], item_ids or []):
                path = item_id["path"]
                if path in current:
                    current[path][field] = str(value or "").strip()

        triggered = ctx.triggered_id
        if isinstance(triggered, dict) and triggered.get("type") == "species-compare-remove":
            if not any(int(clicks or 0) > 0 for clicks in (_remove or [])):
                raise PreventUpdate
            return [
                item
                for item in sources
                if item["species_file"] != triggered.get("path")
            ], no_update
        if triggered == "evolution-open-compare-btn":
            store = app_store or {}
            species_file = str(
                (store.get("artifacts") or {}).get("species") or ""
            ).strip()
            if not species_file:
                return sources, dbc.Alert(
                    "当前RNG 数据没有可交接的 Species 来源；请从已管理RNG 数据或手工路径添加。",
                    color="warning",
                    className="py-1 px-2 mb-0",
                )
            path = str(Path(species_file).expanduser().resolve())
            selected_smiles = str(store.get("selected_smiles") or "").strip()
            existing = next(
                (item for item in sources if item.get("species_file") == path),
                None,
            )
            if existing is None:
                sources.append(
                    {
                        "species_file": path,
                        "label": str(store.get("label") or Path(path).stem),
                        "target_smiles": selected_smiles,
                        "model_iteration": "",
                        "simulation_condition": "",
                        "replicate": "",
                        "dataset_id": str(store.get("dataset_id") or ""),
                        "source_revision": store.get("source_revision") or {},
                        "source_origin": "handoff",
                    }
                )
            elif selected_smiles and not str(
                existing.get("target_smiles") or ""
            ).strip():
                existing["target_smiles"] = selected_smiles
            message = (
                f"已加入当前RNG 数据，并交接精确 Species：{selected_smiles}。"
                if selected_smiles
                else "已加入当前RNG 数据；请为每个来源分别确认精确 Species。"
            )
            return sources, dbc.Alert(
                message,
                color="info",
                className="py-1 px-2 mb-0",
            )
        additions: list[dict[str, Any]] = []
        if triggered == "species-compare-managed":
            lookup = {str(item.get("id")): item for item in (managed or {}).get("datasets", [])}
            selected_ids = {str(dataset_id) for dataset_id in (managed_ids or [])}
            selected_paths = {
                str(Path(f"{lookup[dataset_id]['base']}.species").expanduser().resolve())
                for dataset_id in selected_ids
                if dataset_id in lookup
            }
            known_managed_paths = {
                str(Path(f"{item['base']}.species").expanduser().resolve())
                for item in lookup.values()
                if item.get("base")
            }
            sources = [
                item for item in sources
                if (
                    item.get("source_origin") != "managed"
                    and not (
                        not item.get("source_origin")
                        and item.get("species_file") in known_managed_paths
                    )
                )
                or item.get("species_file") in selected_paths
            ]
            current = {item["species_file"]: item for item in sources}
            for item in sources:
                if (
                    not item.get("source_origin")
                    and item.get("species_file") in selected_paths
                ):
                    item["source_origin"] = "managed"
            for dataset_id in managed_ids or []:
                dataset = lookup.get(str(dataset_id))
                if dataset:
                    additions.append(
                        {
                            "path": f"{dataset['base']}.species",
                            "label": str(dataset.get("label") or ""),
                            "dataset_id": str(dataset.get("dataset_id") or ""),
                            "source_revision": dataset.get("source_revision") or {},
                            "source_origin": "managed",
                        }
                    )
        elif triggered == "species-compare-add-path" and str(path_text or "").strip():
            additions.append(
                {
                    "path": str(path_text).strip(),
                    "label": str(new_label or "").strip(),
                }
            )
        else:
            raise PreventUpdate
        for addition in additions:
            path = str(addition.get("path") or "")
            label = str(addition.get("label") or "")
            resolved = str(Path(path).expanduser().resolve())
            if resolved not in current:
                item = {
                    "species_file": resolved,
                    "label": label or Path(resolved).stem,
                    "target_smiles": "",
                    "model_iteration": "",
                    "simulation_condition": "",
                    "replicate": "",
                    "dataset_id": str(addition.get("dataset_id") or ""),
                    "source_revision": addition.get("source_revision") or {},
                    "source_origin": str(addition.get("source_origin") or "manual"),
                }
                sources.append(item)
                current[resolved] = item
        return sources, no_update

    @app.callback(
        Output("species-compare-sources", "children"),
        Output("species-compare-catalog-store", "data"),
        Input("species-compare-sources-store", "data"),
    )
    def _render_species_compare_sources(sources):
        if not sources:
            return html.Div(
                [
                    html.Div("尚未添加来源", className="rs-batch-empty-title"),
                    html.Div(
                        "从已管理RNG 数据中选择，或展开高级入口添加 Species 文件。",
                        className="rs-batch-empty-hint",
                    ),
                ],
                className="rs-compare-source-empty",
            ), {}
        rows = []
        catalogs = {}
        for index, source in enumerate(sources, 1):
            path = source["species_file"]
            catalog = svc.species_compare_catalog(path)
            catalogs[path] = catalog
            label = str(source.get("label") or Path(path).stem)
            catalog_status = str(catalog.get("status") or "missing_source")
            badge_color = {
                "ready": "success",
                "missing_index": "warning",
                "missing_source": "danger",
            }.get(catalog_status, "secondary")
            badge_label = {
                "ready": "丰度索引可用",
                "missing_index": "需准备索引",
                "missing_source": "来源缺失",
            }.get(catalog_status, "状态未知")
            rows.append(html.Div([
                html.Div([
                    html.Div([
                        html.Span(f"来源 {index:02d}", className="rs-compare-source-kicker"),
                        html.Strong(label, className="rs-compare-source-name"),
                        dbc.Badge(badge_label, color=badge_color, pill=True),
                    ], className="rs-compare-source-title"),
                    (
                        html.Span("在上方取消选择", className="rs-compare-source-remove-hint")
                        if source.get("source_origin") == "managed"
                        else dbc.Button(
                            "移除",
                            id={"type": "species-compare-remove", "path": path},
                            size="sm",
                            color="danger",
                            outline=True,
                        )
                    ),
                ], className="rs-compare-source-header"),
                html.Div([
                    html.Div([
                        dbc.Label("该来源的精确 Species", className="rs-compare-field-label"),
                        dcc.Dropdown(
                            id={"type": "species-compare-target", "path": path},
                            options=catalog["options"],
                            value=source.get("target_smiles") or None,
                            placeholder="从丰度索引搜索并选择精确 SMILES",
                        ),
                    ], className="rs-compare-target-field"),
                ], className="rs-compare-source-main"),
                html.Div(str(catalog.get("message") or ""), className="rs-compare-source-status"),
                html.Details([
                    html.Summary("来源名称、分组信息与文件位置"),
                    html.Div([
                        html.Div([
                            dbc.Label("来源名称"),
                            dcc.Input(
                                id={"type": "species-compare-label", "path": path},
                                value=source.get("label") or "",
                                placeholder="来源名称",
                            ),
                        ], className="rs-evolution-field"),
                        html.Div([
                            dbc.Label("模型迭代"),
                            dcc.Input(
                                id={"type": "species-compare-model-iteration", "path": path},
                                value=source.get("model_iteration") or "",
                                placeholder="未知可留空",
                            ),
                        ], className="rs-evolution-field"),
                        html.Div([
                            dbc.Label("Simulation Condition"),
                            dcc.Input(
                                id={"type": "species-compare-condition", "path": path},
                                value=source.get("simulation_condition") or "",
                                placeholder="未知可留空",
                            ),
                        ], className="rs-evolution-field"),
                        html.Div([
                            dbc.Label("Replicate"),
                            dcc.Input(
                                id={"type": "species-compare-replicate", "path": path},
                                value=source.get("replicate") or "",
                                placeholder="未知可留空",
                            ),
                        ], className="rs-evolution-field"),
                    ], className="rs-compare-source-metadata"),
                    html.Div([
                        html.Span("Species 文件", className="rs-compare-path-label"),
                        html.Code(path),
                    ], className="rs-compare-source-path"),
                ], className="rs-compare-source-details"),
            ], className="rs-compare-source-card", key=path))
        return rows, catalogs

    @app.callback(
        Output("species-compare-readiness", "children"),
        Output("species-compare-run", "disabled"),
        Input("species-compare-sources-store", "data"),
        Input({"type": "species-compare-target", "path": ALL}, "value"),
        Input({"type": "species-compare-target", "path": ALL}, "id"),
        Input("species-compare-catalog-store", "data"),
        Input("species-compare-axis", "value"),
    )
    def _validate_species_compare_sources(sources, targets, target_ids, catalogs, axis):
        current = {
            str(item.get("species_file") or ""): dict(item)
            for item in (sources or [])
            if item.get("species_file")
        }
        for value, item_id in zip(targets or [], target_ids or []):
            path = str(item_id.get("path") or "")
            if path in current:
                current[path]["target_smiles"] = str(value or "").strip()
        if len(current) < 2:
            return dbc.Alert(
                f"已添加 {len(current)} 个来源；至少需要 2 个。",
                color="secondary",
                className="py-1 px-2 mb-0",
            ), True
        blockers = []
        ready = 0
        for source in current.values():
            label = str(source.get("label") or Path(source["species_file"]).stem)
            catalog = (catalogs or {}).get(source["species_file"])
            if not catalog:
                blockers.append(f"{label}：正在检查丰度索引")
                continue
            if catalog.get("status") != "ready":
                blockers.append(f"{label}：{catalog.get('message') or '丰度索引不可用'}")
                continue
            target = str(source.get("target_smiles") or "").strip()
            if not target:
                blockers.append(f"{label}：尚未确认精确 Species")
                continue
            catalog_targets = {
                str(option.get("value") or "")
                for option in catalog.get("options") or []
            }
            if target not in catalog_targets:
                blockers.append(f"{label}：索引中没有该精确 Species")
                continue
            ready += 1
        if blockers:
            return dbc.Alert(
                [
                    html.Div(f"{ready}/{len(current)} 个来源已就绪。"),
                    html.Ul([html.Li(item) for item in blockers], className="mb-0 ps-3"),
                ],
                color="warning",
                className="py-1 px-2 mb-0",
            ), True
        time_note = (
            "运行时将核验每个来源已保存的物理时间换算。"
            if axis in {"ps", "ns"}
            else "将按各来源独立的原始 timestep 展示。"
        )
        return dbc.Alert(
            f"{ready}/{len(current)} 个来源已就绪；{time_note}",
            color="success",
            className="py-1 px-2 mb-0",
        ), False

    @app.callback(
        Output("species-compare-graph", "figure"),
        Output("species-compare-summary", "rowData"),
        Output("species-compare-summary", "columnDefs"),
        Output("species-compare-alert", "children"),
        Output("species-compare-result-store", "data"),
        Output("species-compare-export", "disabled"),
        Input("species-compare-run", "n_clicks"),
        Input("species-compare-sources-store", "data"),
        Input({"type": "species-compare-target", "path": ALL}, "value"),
        Input({"type": "species-compare-target", "path": ALL}, "id"),
        Input({"type": "species-compare-label", "path": ALL}, "value"),
        Input({"type": "species-compare-label", "path": ALL}, "id"),
        Input({"type": "species-compare-model-iteration", "path": ALL}, "value"),
        Input({"type": "species-compare-model-iteration", "path": ALL}, "id"),
        Input({"type": "species-compare-condition", "path": ALL}, "value"),
        Input({"type": "species-compare-condition", "path": ALL}, "id"),
        Input({"type": "species-compare-replicate", "path": ALL}, "value"),
        Input({"type": "species-compare-replicate", "path": ALL}, "id"),
        Input("species-compare-axis", "value"),
        prevent_initial_call=True,
    )
    def _run_species_compare(
        _clicks, sources, targets, target_ids, labels, label_ids,
        model_iterations, model_iteration_ids, conditions, condition_ids,
        replicates, replicate_ids, axis,
    ):
        empty = (
            empty_chart_figure(
                "对比条件已变化",
                "检查来源和精确 Species 后，重新运行比较。",
            ),
            [], [], "来源或目标变化后，请重新比较。", None, True,
        )
        if ctx.triggered_id != "species-compare-run":
            return empty
        current = {item["species_file"]: dict(item) for item in (sources or [])}
        for value, item_id in zip(targets or [], target_ids or []):
            if item_id["path"] in current:
                current[item_id["path"]]["target_smiles"] = str(value or "").strip()
        for value, item_id in zip(labels or [], label_ids or []):
            if item_id["path"] in current:
                current[item_id["path"]]["label"] = str(value or "").strip()
        for field, values, item_ids in (
            ("model_iteration", model_iterations, model_iteration_ids),
            ("simulation_condition", conditions, condition_ids),
            ("replicate", replicates, replicate_ids),
        ):
            for value, item_id in zip(values or [], item_ids or []):
                if item_id["path"] in current:
                    current[item_id["path"]][field] = str(value or "").strip()
        requests = []
        for item in current.values():
            request = dict(item)
            request.pop("source_origin", None)
            requests.append(request)
        try:
            payload = svc.compare_species_sources(requests, x_axis=axis or "step")
        except svc.ServiceError as exc:
            return (
                empty_chart_figure("暂时无法比较", str(exc.message)),
                [], [], exc.message, None, True,
            )
        fig = go.Figure()
        for index, curve in enumerate(payload["curves"], 1):
            fig.add_trace(go.Scatter(x=curve["x_values"], y=curve["values"], mode="lines", name=f"{index}. {curve['label']}",
                                     customdata=[curve["target_smiles"]] * len(curve["values"]),
                                     hovertemplate="%{fullData.name}<br>%{customdata}<br>%{x}: %{y}<extra></extra>"))
        fig.update_layout(xaxis_title=payload["x_name"], yaxis_title="丰度", template="plotly_white", hovermode="x unified")
        columns = ui.columns([{"name": name, "id": field} for field, name in [
            ("label", "来源"),
            ("target_smiles", "精确 SMILES"),
            ("status_text", "状态"),
            ("time_basis_text", "时间口径"),
            ("initial", "初始值"),
            ("final", "末值"),
            ("peak", "峰值"),
            ("peak_time", "峰值时间"),
            ("message", "说明"),
        ]])
        comparability = payload.get("comparability") or {}
        alert = dbc.Alert(
            str(comparability.get("message") or "对比完成"),
            color=(
                "warning"
                if comparability.get("status") == "not_comparable"
                else "info"
            ),
            className="py-1 px-2 mb-0",
        )
        return fig, payload["summary"], columns, alert, payload, False

    @app.callback(
        Output("species-compare-download", "data"),
        Input("species-compare-export", "n_clicks"),
        State("species-compare-result-store", "data"),
        prevent_initial_call=True,
    )
    def _export_species_compare(_clicks, payload):
        if not payload:
            raise PreventUpdate
        return dcc.send_bytes(svc.species_comparison_zip(payload), "species_comparison.zip")

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

# ── Directory browser helpers ───────────────────────────────────────

_BROWSER_RENDER_LIMIT = 100

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
            "请选择一个可用的RNG 数据。",
            reason="missing_dataset",
        )
    if svc.is_collection_path(base):
        record = svc.read_collection(base, validate_sources=True)
        if record and Path(folder).resolve() == Path(base).parent:
            return {"folder": str(Path(base).parent), "base": base, "label": record["label"]}
        raise svc.ServiceError("所选文件集合已不存在，请重新添加。", reason="invalid_dataset_candidate")
    snapshot = svc.browse_dataset_location(folder)
    actual = _candidate_for_base(snapshot, base)
    if actual is None:
        raise svc.ServiceError(
            "所选RNG 数据已不存在，请重新选择。",
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
    app_store: dict[str, Any] | None = None,
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
            app_store=app_store,
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
    app_store: dict[str, Any] | None = None,
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
        app_store=app_store,
    )


def _recover_browser_error(
    current_path: Any,
    candidate: dict[str, Any] | None,
    *,
    reason: str,
    filter_text: Any = "",
    app_store: dict[str, Any] | None = None,
) -> tuple:
    message = _browser_error_copy(reason)
    if str(current_path or "").strip():
        return _refresh_browser_location(
            current_path,
            candidate,
            filter_text=filter_text,
            error=message,
            app_store=app_store,
        )
    return _build_dir_browser_error_response(message)


def _build_dir_browser_response(
    path_str: str,
    *,
    current_path: Any = "",
    candidate: dict[str, Any] | None = None,
    app_store: dict[str, Any] | None = None,
    filter_text: Any = "",
    select_dataset: bool = True,
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
    actual = None
    if select_dataset:
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
        app_store=app_store,
    )


def _select_dataset_folder_response(
    path_str: str,
    *,
    current_path: Any = "",
    candidate: dict[str, Any] | None = None,
    app_store: dict[str, Any] | None = None,
    filter_text: Any = "",
) -> tuple:
    """Inspect one user-visible folder and enter the confirmation step."""

    try:
        selected = svc.resolve_dataset_folder_candidate(path_str)
        snapshot = svc.browse_dataset_location(selected["folder"])
    except svc.ServiceError as exc:
        reason = str(exc.reason or "read_error")
        message = {
            "dataset_not_found": (
                "当前文件夹中没有识别到 ReacNetGenerator 数据；"
                "可以继续浏览其子文件夹。"
            ),
            "ambiguous_dataset_folder": (
                "当前文件夹包含多组数据；请为每组数据使用独立文件夹。"
            ),
        }.get(reason)
        if message:
            if str(current_path or "").strip():
                return _refresh_browser_location(
                    current_path,
                    candidate,
                    filter_text=filter_text,
                    error=message,
                    app_store=app_store,
                )
            return _build_dir_browser_error_response(message)
        return _recover_browser_error(
            current_path,
            candidate,
            reason=reason,
            filter_text=filter_text,
            app_store=app_store,
        )
    return _build_dir_browser_snapshot_response(
        snapshot,
        selected,
        filter_text=str(filter_text or ""),
        app_store=app_store,
    )


def _select_recent_dataset(
    record: dict[str, Any],
    *,
    filter_text: Any = "",
    fallback_path: Any = "",
    fallback_candidate: dict[str, Any] | None = None,
    app_store: dict[str, Any] | None = None,
) -> tuple:
    """Revalidate a recent record by its exact base while showing its folder label."""
    folder = str(record.get("folder") or "")
    base = str(record.get("base") or "")
    try:
        snapshot = svc.browse_dataset_location(folder)
    except svc.ServiceError:
        return _recover_browser_error(
            fallback_path or folder,
            fallback_candidate,
            reason="recent_missing",
            filter_text=filter_text,
            app_store=app_store,
        )
    actual = _candidate_for_base(snapshot, base)
    if actual is None:
        return _recover_browser_error(
            fallback_path or folder,
            fallback_candidate,
            reason="recent_missing",
            filter_text=filter_text,
            app_store=app_store,
        )
    compact = _compact_browser_candidate(actual)
    compact["label"] = str(record.get("label") or Path(folder).name)
    return _build_dir_browser_snapshot_response(
        snapshot,
        compact,
        filter_text=str(filter_text or ""),
        app_store=app_store,
    )


def _render_browser_current(
    data: dict[str, Any] | None,
    candidate: dict[str, str] | None,
    *,
    error: str = "",
    filter_text: str = "",
    app_store: dict[str, Any] | None = None,
) -> Any:
    """Render the current folder without exposing internal dataset prefixes."""
    snapshot = data or {}
    current_path = str(snapshot.get("current_path") or "")
    datasets = list(snapshot.get("datasets") or [])
    if len(datasets) == 1:
        status_copy = "已识别到 ReacNetGenerator 数据。"
        status_class = "rs-browser-folder-status is-ready"
    elif len(datasets) > 1:
        status_copy = "此文件夹包含多组数据，请分别放入独立文件夹。"
        status_class = "rs-browser-folder-status is-warning"
    else:
        status_copy = "当前文件夹未识别到数据，可以继续打开子文件夹。"
        status_class = "rs-browser-folder-status"
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
            html.Div(
                [
                    html.Span("当前位置", className="rs-browser-context-role"),
                    html.Code(current_path, className="rs-browser-context-value"),
                    html.Span(status_copy, className=status_class),
                ],
                className="rs-browser-context-strip",
                **{"aria-label": "当前文件夹"},
            ),
            _render_allowed_roots(),
            alert,
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


def _allowed_root_label(root: Path) -> str:
    """Return a friendly label while the full path keeps it unambiguous."""
    try:
        if root == Path.home().expanduser().resolve():
            return "主目录"
    except (OSError, RuntimeError):
        pass
    if len(root.parts) > 1 and root.parts[1] == "media":
        return "外接数据"
    if root == Path("/data"):
        return "共享数据"
    if root == Path("/mnt"):
        return "其他挂载"
    return root.name or "文件系统"


def _render_allowed_roots() -> Any:
    roots = _allowed_roots()
    if not roots:
        content: Any = html.Div(
            "没有可用的起始位置。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    else:
        buttons: list[Any] = []
        for index, root in enumerate(roots):
            label = _allowed_root_label(root)
            buttons.append(
                html.Button(
                    [
                        html.Span(label, className="rs-browser-root-name"),
                        html.Code(str(root), className="rs-browser-root-path"),
                    ],
                    id={"type": "dir-browser-root", "index": index},
                    type="button",
                    className="rs-browser-root-button",
                    title=str(root),
                    **{"aria-label": f"从{label} {root}开始浏览"},
                )
            )
        content = html.Div(buttons, className="rs-browser-root-list")
    return html.Details(
        [
            html.Summary("切换起始位置"),
            html.Div(
                [
                    html.P(
                        "选择一个起始位置继续浏览。",
                        className="rs-browser-root-help",
                    ),
                    content,
                ],
                className="rs-browser-root-menu",
            ),
        ],
        className="rs-browser-root-switcher",
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
            "当前目录没有文件夹。",
            className="rs-browser-empty-line",
            **{"role": "status"},
        )
    elif not matched:
        directory_list = html.Div(
            "没有文件夹匹配当前筛选；选择“清除”恢复全部内容。",
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
                    html.H3("文件夹", className="rs-browser-section-title"),
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
        {"field": "first_time", "headerName": "首次后帧时间", "width": 120, "type": "numericColumn"},
        {"field": "last_time", "headerName": "末次后帧时间", "width": 120, "type": "numericColumn"},
        {"field": "timing_unit", "headerName": "时间单位", "width": 115},
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


_EVENT_TABLE_COLUMNS = [
    {"field": "event_index", "headerName": "事件序号", "type": "numericColumn"},
    {"field": "event_id", "headerName": "事件 ID"},
    {"field": "timestep_index", "headerName": "事件区间", "type": "numericColumn"},
    {"field": "before_timestep", "headerName": "反应前原始坐标", "type": "numericColumn"},
    {"field": "after_timestep", "headerName": "反应后原始坐标", "type": "numericColumn"},
    {"field": "raw_time_unit", "headerName": "原始坐标单位"},
    {"field": "before_time_ps", "headerName": "反应前 / ps", "type": "numericColumn"},
    {"field": "after_time_ps", "headerName": "反应后 / ps", "type": "numericColumn"},
    {"field": "time_unit", "headerName": "时间单位"},
    {"field": "time_basis", "headerName": "原始时间依据"},
    {"field": "timestep_ps", "headerName": "timestep → ps", "type": "numericColumn"},
    {"field": "reaction_key", "headerName": "精确反应键"},
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
    return ui.columns(columns)


# ── Helpers ─────────────────────────────────────────────────────────


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


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
