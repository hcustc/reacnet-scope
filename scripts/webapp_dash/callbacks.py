"""Dash callback registration for ReacNet Scope WebUI V1.

All callbacks are registered in ``register_callbacks(app)``.  Each callback
delegates to ``scripts.webapp_dash.services`` for data operations and never
re-implements analysis logic.
"""

from __future__ import annotations

import re
import time
import base64
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from rng_tools.pathway_export import pathway_csv_text, pathway_document
from reacnet_scope.indexes import dataset_id_for_source
from scripts.webapp_dash import services as svc


PAGE_IDS = ["workflow", "species", "transitions", "reactions", "pathway", "intermediate", "evolution", "carbon", "events", "network", "literature", "batch-compare"]
PAGE_LABELS = {
    "workflow": "反应证据工作流",
    "species": "物种检索",
    "transitions": "转化关系",
    "reactions": "反应式检索",
    "pathway": "关键路径",
    "intermediate": "中间体筛选",
    "evolution": "时间演化",
    "carbon": "C/O/Cl 组成演化",
    "events": "事件证据",
    "network": "反应网络",
    "literature": "文献验证",
    "batch-compare": "批量对比",
}
PAGE_DESCRIPTIONS = {
    "workflow": "从实验目标物种出发，依次定位高频通道、代表性事件和局部轨迹证据。",
    "species": "按分子式、SMILES 或精确质量定位物种，并查看结构与通量。",
    "transitions": "围绕已选物种查看生成、消耗及净通量关系。",
    "reactions": "按反应物和产物组合检索反应，比较正反向通量。",
    "pathway": "从精确 SMILES 出发，搜索有界候选路径并关联事件证据。",
    "intermediate": "基于丰度、寿命与通量条件筛选关键中间体。",
    "evolution": "绘制目标物种随帧数或模拟时间变化的丰度曲线。",
    "carbon": "选择 O/Cl 条件，查看碳数随时间变化，再点击曲线查看代表物种。",
    "events": "检索 ReacNetGenerator 事件输出，并按参与原子查看局部轨迹。",
    "network": "在 reactionabcd 机制网络与 table 观察网络之间明确切换。",
    "literature": "将文献反应式与当前网络逐条比对并生成证据矩阵。",
    "batch-compare": "扫描多组模拟结果，对比反应通量与检出率。",
}
PAGE_DATA_REQUIREMENTS = {
    "species": ("reaction", "reactionabcd"),
    "transitions": ("reaction", "reactionabcd"),
    "reactions": ("reaction", "reactionabcd"),
    "pathway": ("reaction", "reactionabcd"),
    "intermediate": ("species", ".species"),
    "evolution": ("species", ".species"),
    "carbon": ("species", ".species"),
    "events": ("reactionevent", ".reactionevent.csv + .molecules.csv"),
    "network": ("table", ".lammpstrj.table"),
    "literature": ("reaction", "reactionabcd"),
}

NETWORK_STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "font-size": 8,
            "width": 28,
            "height": 28,
            "background-color": "#dbeafe",
            "border-color": "#93c5fd",
            "border-width": 1,
        },
    },
    {
        "selector": "node.reaction",
        "style": {
            "background-color": "#fde68a",
            "border-color": "#f59e0b",
            "shape": "rectangle",
            "width": 18,
            "height": 18,
            "font-size": 6,
        },
    },
    {
        "selector": "node[selected]",
        "style": {
            "border-width": 3,
            "border-color": "#2563eb",
        },
    },
    {
        "selector": "edge",
        "style": {
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.8,
            "line-color": "#9ca3af",
            "target-arrow-color": "#9ca3af",
            "width": 1,
        },
    },
    {
        "selector": ".is-path-highlight",
        "style": {
            "border-color": "#2563eb",
            "border-width": 4,
            "line-color": "#2563eb",
            "target-arrow-color": "#2563eb",
            "width": 3,
            "z-index": 10,
        },
    },
]


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
        "selected_smiles": "",
        "selected_formula": "",
    }


def initial_workflow_store() -> dict[str, Any]:
    """Session-local state for the focused four-step evidence workflow."""
    return {
        "dataset_key": "",
        "current_step": 1,
        "species": None,
        "channel": None,
        "event": None,
        "validations": [],
    }


def _network_dataset_id(store: dict[str, Any] | None) -> str:
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


def _network_status_alert(payload: dict[str, Any] | None) -> Any:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("meta") or {}
    elements = [
        item
        for item in payload.get("elements") or []
        if isinstance(item, dict)
    ]
    element_nodes = [
        item
        for item in elements
        if not (
            (item.get("data") or {}).get("source")
            or (item.get("data") or {}).get("target")
        )
    ]
    element_edges = [item for item in elements if item not in element_nodes]
    node_count = int(
        meta.get("node_count")
        if meta.get("node_count") is not None
        else len(element_nodes)
    )
    edge_count = int(
        meta.get("edge_count")
        if meta.get("edge_count") is not None
        else len(element_edges)
    )
    reaction_count = int(
        meta.get("reaction_count")
        if meta.get("reaction_count") is not None
        else sum(
            (item.get("data") or {}).get("kind") == "reaction"
            for item in element_nodes
        )
    )
    reason = str(meta.get("reason") or "ok")
    reason_labels = {
        "ok": "网络已构建",
        "species_absent": "锚点物种不在当前反应网络中",
        "no_positive_net_continuation": "锚点存在，但没有正净 TP 的延伸反应",
        "filtered_by_thresholds": "反应均被当前净 TP 阈值过滤",
        "filtered_by_evidence": "当前事件证据筛选没有匹配反应",
    }
    children: list[Any] = [
        html.Div(
            reason_labels.get(reason, f"网络状态：{reason}"),
            className="fw-semibold",
        ),
        html.Div(
            f"{node_count} 个节点 · {edge_count} 条边 · "
            f"{reaction_count} 个反应节点"
        ),
        html.Div(
            "证据层级：{} · 证据状态：{}".format(
                payload.get("evidence_level") or "未提供",
                payload.get("evidence_status") or "未提供",
            ),
            className="small text-muted",
        ),
    ]
    if meta.get("truncated"):
        children.append(
            html.Div("结果因节点上限而截断。", className="small fw-semibold")
        )
    command = str(payload.get("preparation_command") or "")
    if command:
        children.append(
            html.Div(
                [
                    html.Div("离线证据准备命令：", className="small"),
                    html.Div(
                        [
                            html.Code(command),
                            dcc.Clipboard(
                                content=command,
                                title="复制准备命令",
                                style={"marginLeft": "0.5rem"},
                            ),
                        ],
                        className="d-flex align-items-center",
                    ),
                ]
            )
        )
    color = (
        "warning"
        if reason != "ok" or bool(meta.get("truncated"))
        else "success"
    )
    return dbc.Alert(children, color=color, className="py-2 mb-0")


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
        "missing": ("未准备", "secondary"),
    }
    return labels.get(state, (state, "secondary"))


def _render_preparation_status(payload: dict[str, Any]) -> Any:
    rows: list[Any] = []
    entries = [
        ("基础分析", payload.get("basic") or {}),
        ("RNG 事件输出", payload.get("events") or {}),
        ("轨迹帧索引", payload.get("trajectory") or {}),
        ("C/O/Cl 组成索引", payload.get("composition") or {}),
    ]
    for label, item in entries:
        text, color = _preparation_state_text(item)
        detail = ""
        if label == "RNG 事件输出" and item.get("source_size"):
            detail = _format_bytes(item.get("source_size"))
        elif label == "轨迹帧索引" and item.get("trajectory_size"):
            detail = f"{_format_bytes(item.get('source_offset'))} / {_format_bytes(item.get('trajectory_size'))}"
        elif label == "C/O/Cl 组成索引" and item.get("species_size"):
            detail = f"{_format_bytes(item.get('source_offset'))} / {_format_bytes(item.get('species_size'))}"
        if item.get("state") == "ready":
            records = item.get("frames") if label == "轨迹帧索引" else item.get("timepoints") if label == "C/O/Cl 组成索引" else None
            if records is not None:
                detail = f"{int(records):,} 条记录 · {_format_bytes(item.get('index_size'))}"
        if item.get("message"):
            detail = str(item["message"])
        rows.append(
            html.Div(
                [
                    html.Span(label, className="text-muted"),
                    dbc.Badge(text, color=color, pill=True),
                    html.Span(detail, className="small text-muted ms-2"),
                ],
                className="d-flex align-items-center gap-2 py-1 flex-wrap",
            )
        )
    updated = payload.get("last_updated_epoch")
    updated_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated)) if updated else "-"
    rows.extend(
        [
            html.Div([html.Span("缓存目录", className="text-muted me-2"), html.Code(payload.get("cache_dir") or "未配置")], className="small mt-2 text-break"),
            html.Div(
                f"数据集 ID: {payload.get('dataset_id') or '-'} · 索引占用: {_format_bytes(payload.get('index_bytes'))} · 最后更新: {updated_text}",
                className="small text-muted mt-1",
            ),
        ]
    )
    return html.Div(rows)


def _event_frame_figure(viewer: dict[str, Any], frame_index: int, scope: str, *, compact: bool = False):
    """Render one local trajectory frame as a grouped Plotly 3D scene."""
    import plotly.graph_objects as go

    frames = viewer.get("frames") or []
    if not frames:
        return go.Figure()
    safe_index = max(0, min(int(frame_index or 0), len(frames) - 1))
    frame = frames[safe_index]
    atoms = list(frame.get("atoms") or [])
    core_ids = {int(value) for value in ((viewer.get("atom_groups") or {}).get("core") or [])}
    if scope == "core":
        core_atoms = [atom for atom in atoms if int(atom.get("id") or -1) in core_ids]
        atoms = core_atoms or atoms

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
        fig.add_trace(go.Scatter3d(x=[a.get("x"), b.get("x")], y=[a.get("y"), b.get("y")], z=[a.get("z"), b.get("z")], mode="lines", name=label, line={"color": color, "width": 4 if not compact else 2}, hoverinfo="skip", showlegend=not compact))
    symbols = [atom.get("element") or f"T{atom.get('type') or '?'}" for atom in atoms]
    colors = ["#1d4ed8" if int(atom.get("id") or -1) in core_ids else "#64748b" for atom in atoms]
    fig.add_trace(
        go.Scatter3d(
            x=[atom.get("x") for atom in atoms], y=[atom.get("y") for atom in atoms], z=[atom.get("z") for atom in atoms],
            mode="markers", name="反应核" if scope == "core" else "参与原子",
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
        details.append("原子关联不确定")
    return html.Div(
        [
            html.Span("已选", className="rs-selection-label"),
            html.Span(" · ".join(details), className="rs-selection-main"),
            html.Span(str(row.get("reaction_smiles") or row.get("matched_smiles_at_anchor") or ""), className="rs-selection-query"),
        ],
        className="rs-selection-line",
    )


def _transition_selection_summary(row: dict[str, Any]) -> Any:
    """Render the selected transition before it is handed to event evidence."""
    return html.Div(
        [
            html.Span("已选反应", className="rs-selection-label"),
            html.Span(str(row.get("reaction_formulas") or ""), className="rs-selection-main"),
            html.Span(str(row.get("reaction_smiles") or ""), className="rs-selection-query"),
        ],
        className="rs-selection-line",
    )


def register_callbacks(app: Any) -> None:
    # ── Navigation ──────────────────────────────────────────────────

    @app.callback(
        Output("page-workflow", "className"),
        Output("page-species", "className"),
        Output("page-transitions", "className"),
        Output("page-reactions", "className"),
        Output("page-pathway", "className"),
        Output("page-intermediate", "className"),
        Output("page-evolution", "className"),
        Output("page-carbon", "className"),
        Output("page-events", "className"),
        Output("page-network", "className"),
        Output("page-literature", "className"),
        Output("page-batch-compare", "className"),
        Output("nav-workflow", "className"),
        Output("nav-species", "className"),
        Output("nav-transitions", "className"),
        Output("nav-reactions", "className"),
        Output("nav-pathway", "className"),
        Output("nav-intermediate", "className"),
        Output("nav-evolution", "className"),
        Output("nav-carbon", "className"),
        Output("nav-events", "className"),
        Output("nav-network", "className"),
        Output("nav-literature", "className"),
        Output("nav-batch-compare", "className"),
        Output("page-store", "data"),
        Output("page-title", "children"),
        Output("page-description", "children"),
        Output("page-header", "style"),
        Output("app-body", "className"),
        Input("nav-workflow", "n_clicks"),
        Input("nav-species", "n_clicks"),
        Input("nav-transitions", "n_clicks"),
        Input("nav-reactions", "n_clicks"),
        Input("nav-pathway", "n_clicks"),
        Input("nav-intermediate", "n_clicks"),
        Input("nav-evolution", "n_clicks"),
        Input("nav-carbon", "n_clicks"),
        Input("nav-events", "n_clicks"),
        Input("nav-network", "n_clicks"),
        Input("nav-literature", "n_clicks"),
        Input("nav-batch-compare", "n_clicks"),
        Input("species-to-event-btn", "n_clicks"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("transitions-to-event-btn", "n_clicks"),
        Input("species-to-pathway-btn", "n_clicks"),
        Input("rxn-to-pathway-btn", "n_clicks"),
        Input("pathway-open-events-btn", "n_clicks"),
        Input("pathway-highlight-network-btn", "n_clicks"),
        Input("network-open-events-btn", "n_clicks"),
        State("page-store", "data"),
    )
    def _navigate(*_args):
        triggered_id = ctx.triggered_id
        stored_page = (_args[-1] or {}).get("page") if _args else None
        if triggered_id in {
            "species-to-event-btn",
            "rxn-to-event-btn",
            "transitions-to-event-btn",
            "pathway-open-events-btn",
            "network-open-events-btn",
        }:
            page_id = "events"
        elif triggered_id in {"species-to-pathway-btn", "rxn-to-pathway-btn"}:
            page_id = "pathway"
        elif triggered_id == "pathway-highlight-network-btn":
            page_id = "network"
        else:
            page_id = triggered_id.removeprefix("nav-") if triggered_id else stored_page
        if page_id not in PAGE_IDS:
            page_id = "workflow"
        page_classes = {
            pid: "rs-page active" if pid == page_id else "rs-page"
            for pid in PAGE_IDS
        }
        nav_classes = {
            pid: f"rs-nav-item{' active' if pid == page_id else ''}"
            for pid in PAGE_IDS
        }
        nav_classes["workflow"] = f"rs-workflow-home{' active' if page_id == 'workflow' else ''}"
        shell_class = "rs-body rs-workflow-shell"
        return (
            tuple(page_classes[pid] for pid in PAGE_IDS)
            + tuple(nav_classes[pid] for pid in PAGE_IDS)
            + ({"page": page_id}, PAGE_LABELS[page_id], PAGE_DESCRIPTIONS[page_id], {"display": "none"} if page_id == "workflow" else {}, shell_class)
        )

    @app.callback(
        Output("page-data-status", "children"),
        Output("page-data-status", "className"),
        Input("page-store", "data"),
        Input("app-store", "data"),
        Input("network-semantics", "value"),
    )
    def _update_page_data_status(page_store, app_store, network_semantics):
        page_id = (page_store or {}).get("page") or "species"
        if page_id == "batch-compare":
            return "独立目录分析", "rs-page-status is-independent"
        if page_id == "events":
            event_ready = bool((((app_store or {}).get("readiness") or {}).get("event_search") or {}).get("ready"))
            return (
                ("RNG 事件输出已就绪", "rs-page-status is-ready")
                if event_ready
                else ("需要 reactionevent.csv + molecules.csv", "rs-page-status is-blocked")
            )
        if page_id == "network" and network_semantics == "mechanism":
            artifact_key, artifact_label = "reaction", "reactionabcd"
        else:
            artifact_key, artifact_label = PAGE_DATA_REQUIREMENTS.get(
                page_id,
                ("", ""),
            )
        artifacts = (app_store or {}).get("artifacts") or {}
        if artifact_key and artifacts.get(artifact_key):
            return f"{artifact_label} 已就绪", "rs-page-status is-ready"
        return f"需要 {artifact_label or '数据文件'}", "rs-page-status is-blocked"

    @app.callback(
        Output("transitions-search-btn", "disabled"),
        Output("rxn-search-btn", "disabled"),
        Output("pathway-search-btn", "disabled"),
        Output("inter-search-btn", "disabled"),
        Output("evolution-search-btn", "disabled"),
        Output("carbon-search-btn", "disabled"),
        Output("event-rxn-btn", "disabled"),
        Output("event-extract-btn", "disabled"),
        Output("network-search-btn", "disabled"),
        Output("literature-verify-btn", "disabled"),
        Input("app-store", "data"),
        Input("network-semantics", "value"),
    )
    def _update_data_dependent_actions(app_store, network_semantics):
        artifacts = (app_store or {}).get("artifacts") or {}
        readiness = (app_store or {}).get("readiness") or {}
        no_reaction = not bool(artifacts.get("reaction"))
        no_species = not bool(artifacts.get("species"))
        no_reaction_events = not bool((readiness.get("event_search") or {}).get("ready"))
        no_trajectory = not bool((readiness.get("trajectory_evidence") or {}).get("ready"))
        no_table = not bool(artifacts.get("table"))
        return (
            no_reaction,
            no_reaction,
            no_reaction,
            no_species,
            no_species,
            no_species,
            no_reaction_events,
            no_trajectory,
            no_reaction
            if network_semantics == "mechanism"
            else no_table,
            no_reaction,
        )

    # ── Data modal open / close ─────────────────────────────────────

    @app.callback(
        Output("data-modal", "is_open"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("data-close-btn", "n_clicks"),
        State("data-modal", "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_data_modal(topbar_open, species_open, close_btn, is_open):
        triggered = ctx.triggered_id
        if triggered in ("open-data-modal", "species-open-data-modal"):
            return True
        if triggered == "data-close-btn":
            return False
        return is_open

    @app.callback(
        Output("data-overview-view", "className"),
        Output("data-browser-view", "className"),
        Input("open-data-modal", "n_clicks"),
        Input("species-open-data-modal", "n_clicks"),
        Input("data-pick-btn", "n_clicks"),
        Input("dir-browser-cancel-btn", "n_clicks"),
        Input({"type": "dir-browser-recent-entry", "folder": ALL, "base": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _switch_data_modal_view(
        _topbar_open,
        _species_open,
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
        State("app-store", "data"),
    )
    def _show_candidate_status(candidate, app_store):
        selected = candidate if isinstance(candidate, dict) else {}
        folder = str(selected.get("folder") or "").strip()
        base = str(selected.get("base") or "").strip()
        if not folder or not base:
            loaded = app_store or {}
            current_label = str(loaded.get("label") or "未选择")
            return (
                html.Span(f"当前已加载：{current_label}；请选择要加载的数据集。", className="text-muted"),
                "尚未选择待加载数据集。",
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
            html.Div([html.Span("待加载数据集：", className="text-muted"), html.Strong(display_label)]),
            f"已验证 — {display_label}，就绪 {ready}/7",
            artifact_html,
            False,
        )

    @app.callback(
        Output("data-prep-status", "children"),
        Output("data-rng-event-command", "children"),
        Output("data-prep-trajectory-command", "children"),
        Output("data-prep-composition-command", "children"),
        Output("data-rng-event-copy", "content"),
        Output("data-prep-trajectory-copy", "content"),
        Output("data-prep-composition-copy", "content"),
        Output("data-clear-trajectory-btn", "disabled"),
        Output("data-prep-refresh", "disabled"),
        Input("data-modal", "is_open"),
        Input("data-prep-refresh-btn", "n_clicks"),
        Input("data-prep-refresh", "n_intervals"),
        Input("dataset-browser-candidate", "data"),
        State("app-store", "data"),
    )
    def _refresh_preparation_status(is_open, _refresh_clicks, _tick, candidate, app_store):
        if not is_open:
            return "", "", "", "", "", "", "", True, True
        try:
            target = _validated_dataset_target(candidate, app_store=app_store)
            payload = svc.dataset_preparation_status(
                target["folder"],
                base=target["base"],
            )
        except svc.ServiceError as exc:
            return str(exc.message), "", "", "", "", "", "", True, False
        except Exception as exc:
            return f"读取准备状态失败: {exc}", "", "", "", "", "", "", True, False

        trajectory = payload.get("trajectory") or {}
        trajectory_disabled = str(trajectory.get("state") or "missing") in {"missing", "building"}
        return (
            _render_preparation_status(payload),
            payload.get("rng_event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
            payload.get("rng_event_command") or "",
            payload.get("trajectory_command") or "",
            payload.get("composition_command") or "",
            trajectory_disabled,
            False,
        )

    @app.callback(
        Output("data-clear-confirm-modal", "is_open"),
        Output("data-clear-confirm-text", "children"),
        Output("data-clear-kind-store", "data"),
        Output("data-prep-clear-alert", "children"),
        Input("data-clear-trajectory-btn", "n_clicks"),
        Input("data-clear-cancel-btn", "n_clicks"),
        State("dataset-browser-candidate", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _confirm_index_clear(trajectory_clicks, cancel_clicks, candidate, app_store):
        if ctx.triggered_id == "data-clear-cancel-btn":
            return False, no_update, {}, None
        kind = "trajectory"
        try:
            target = _validated_dataset_target(candidate, app_store=app_store)
            payload = svc.dataset_preparation_status(
                target["folder"],
                base=target["base"],
            )
        except Exception as exc:
            return False, no_update, {}, dbc.Alert(f"无法读取索引状态: {exc}", color="danger", className="py-2")
        item = payload.get(kind) or {}
        if str(item.get("state") or "") == "building":
            return (
                False,
                no_update,
                {},
                dbc.Alert("索引正在由离线准备程序构建；请先停止该程序后再清理。", color="warning", className="py-2"),
            )
        size = _format_bytes(item.get("index_size"))
        label = "轨迹帧"
        message = html.Div(
            [
                html.P(f"将清理当前数据集的 {label} 索引，预计释放 {size}。"),
                html.P("不会删除 .route、轨迹或任何 ReacNetGenerator 输出文件。", className="text-muted mb-0"),
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

    # ── Directory browser (internal data-modal view) ──────────────────

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
        Output("data-modal", "is_open"),
        Output("recent-datasets", "data"),
        Output("dataset-browser-candidate", "data", allow_duplicate=True),
        Output("data-load-feedback", "children"),
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
                True,
                recent_records,
                None,
                dbc.Alert("请选择一个可用的数据集后再加载。", color="warning", className="py-2"),
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
                True,
                recent_records,
                None,
                dbc.Alert(
                    f"所选数据集不可用，未切换当前数据：{exc}",
                    color="danger",
                    className="py-2",
                ),
            )
        artifacts = svc.artifacts_from_status(status)
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
            False,
            recent,
            no_update,
            None,
        )

    # ── Focused four-step evidence workflow ─────────────────────────

    def _workflow_columns(items: list[tuple[str, str, int | None]]) -> list[dict[str, Any]]:
        return [
            {"name": label, "id": field, **({"presentation": "markdown"} if field == "structure" else {}), **({"type": "numeric"} if field not in {"structure", "smiles", "formula", "reaction_formulas", "recommendation", "association_status", "structure_source"} else {})}
            for field, label, _width in items
        ]

    @app.callback(
        Output("workflow-species-grid", "data"),
        Output("workflow-species-grid", "columns"),
        Output("workflow-species-alert", "children"),
        Output("workflow-species-grid", "selected_rows"),
        Input("workflow-species-search", "n_clicks"),
        Input("app-store", "data"),
        State("workflow-species-query", "value"),
        State("workflow-species-kind", "value"),
        State("workflow-mass-tolerance", "value"),
        State("workflow-mass-mode", "value"),
    )
    def _search_workflow_catalog(_clicks, app_store, query, kind, tolerance, mass_mode):
        artifacts = (app_store or {}).get("artifacts") or {}
        if not artifacts.get("species"):
            return [], _workflow_columns([]), "选择数据集后，可从 .species 建立目标物种目录。", []
        try:
            payload = svc.search_species_catalog(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=float(0.5 if tolerance is None else tolerance),
                mass_mode=mass_mode or "exact",
            )
        except svc.ServiceError as exc:
            return [], _workflow_columns([]), exc.message, []
        rows = payload.get("rows") or []
        columns = _workflow_columns(
            [
                ("structure", "结构", 90), ("formula", "分子式", 100), ("smiles", "SMILES", 240), ("exact_mass", "精确质量", 92),
                ("nominal_mass", "标称质量", 82), ("total_count", "累计丰度", 96),
                ("structure_source", "结构证据", 88),
            ]
        )
        meta = payload.get("meta") or {}
        moname_state = ".moname 已补充结构实例" if meta.get("moname_available") else ".moname 未提供，使用 SMILES 结构"
        return rows, columns, f"目录含 {meta.get('catalog_size', 0)} 个物种；{moname_state}。", []

    @app.callback(
        Output("workflow-store", "data"),
        Input("app-store", "data"),
        Input("workflow-species-search", "n_clicks"),
        Input("workflow-species-grid", "selected_rows"),
        Input("workflow-species-confirm", "n_clicks"),
        Input("workflow-production-grid", "selected_rows"),
        Input("workflow-consumption-grid", "selected_rows"),
        Input("workflow-channel-confirm", "n_clicks"),
        Input("workflow-event-grid", "selected_rows"),
        Input("workflow-event-confirm", "n_clicks"),
        Input("workflow-validation-save", "n_clicks"),
        Input("workflow-step-1", "n_clicks"),
        Input("workflow-step-2", "n_clicks"),
        Input("workflow-step-3", "n_clicks"),
        Input("workflow-step-4", "n_clicks"),
        State("workflow-store", "data"),
        State("workflow-species-grid", "data"),
        State("workflow-production-grid", "data"),
        State("workflow-consumption-grid", "data"),
        State("workflow-event-grid", "data"),
        State("workflow-validation-outcome", "value"),
        State("workflow-validation-note", "value"),
        prevent_initial_call=False,
    )
    def _advance_workflow(
        app_store, _species_search, species_selected, species_confirm, production_selected, consumption_selected, channel_confirm,
        event_selected, event_confirm, validation_save, _step1, _step2, _step3, _step4,
        workflow, species_rows, production_rows, consumption_rows, event_rows, outcome, note,
    ):
        state = dict(workflow or initial_workflow_store())
        app_store = app_store or {}
        dataset_key = str(app_store.get("base") or app_store.get("label") or "")
        triggered = ctx.triggered_id
        if triggered == "app-store" and dataset_key != state.get("dataset_key", ""):
            return {**initial_workflow_store(), "dataset_key": dataset_key}
        if not dataset_key:
            return initial_workflow_store()
        state["dataset_key"] = dataset_key

        def chosen(rows: list[int] | None, data: list[dict[str, Any]] | None) -> dict[str, Any] | None:
            if not rows or not data:
                return None
            index = int(rows[0])
            return dict(data[index]) if 0 <= index < len(data) else None

        if triggered == "workflow-species-search":
            state.update(
                {
                    "species": None,
                    "channel": None,
                    "event": None,
                    "current_step": 1,
                    "validation_message": "",
                }
            )
        elif triggered == "workflow-species-grid":
            row = chosen(species_selected, species_rows)
            if row:
                state.update({"species": row, "channel": None, "event": None, "current_step": 1})
        elif triggered == "workflow-species-confirm" and state.get("species"):
            state.update({"channel": None, "event": None, "current_step": 2})
        elif triggered in {"workflow-production-grid", "workflow-consumption-grid"}:
            row = chosen(production_selected if triggered == "workflow-production-grid" else consumption_selected, production_rows if triggered == "workflow-production-grid" else consumption_rows)
            if row:
                state.update({"channel": row, "event": None, "current_step": 2})
        elif triggered == "workflow-channel-confirm" and state.get("channel"):
            state.update({"event": None, "current_step": 3})
        elif triggered == "workflow-event-grid":
            row = chosen(event_selected, event_rows)
            if row:
                state.update({"event": row, "current_step": 3})
        elif triggered == "workflow-event-confirm" and (state.get("event") or {}).get("validation_ready"):
            state["current_step"] = 4
        elif triggered == "workflow-validation-save" and state.get("species") and state.get("channel") and state.get("event"):
            state["validations"] = svc.upsert_validation_record(
                state.get("validations"), dataset_id=dataset_key, species=state["species"], channel=state["channel"],
                event=state["event"], outcome=outcome or "insufficient", note=note or "",
                recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            state["validation_message"] = f"已记录 {len(state['validations'])} 条会话内验证结果。"
        elif triggered and str(triggered).startswith("workflow-step-"):
            requested = int(str(triggered).rsplit("-", 1)[-1])
            maximum = 1 + int(bool(state.get("species"))) + int(bool(state.get("channel"))) + int(bool(state.get("event") and (state.get("event") or {}).get("validation_ready")))
            state["current_step"] = min(requested, maximum)
        return state

    @app.callback(
        Output("workflow-production-grid", "data"),
        Output("workflow-production-grid", "columns"),
        Output("workflow-consumption-grid", "data"),
        Output("workflow-consumption-grid", "columns"),
        Output("workflow-channels-alert", "children"),
        Input("workflow-store", "data"),
        Input("app-store", "data"),
    )
    def _load_workflow_channels(workflow, app_store):
        species = (workflow or {}).get("species") or {}
        if not species or int((workflow or {}).get("current_step") or 1) < 2:
            return [], _workflow_columns([]), [], _workflow_columns([]), "先选定一个目标物种。"
        try:
            payload = svc.collect_species_channels((app_store or {}).get("artifacts") or {}, str(species.get("smiles") or ""))
        except svc.ServiceError as exc:
            return [], _workflow_columns([]), [], _workflow_columns([]), exc.message
        columns = _workflow_columns([("reaction_formulas", "反应式", 240), ("forward_tp", "频次", 72), ("reverse_tp", "逆向", 72), ("net_tp", "净频次", 76), ("ratio_pct", "占比%", 68)])
        return payload.get("production_rows") or [], columns, payload.get("consumption_rows") or [], columns, "按正向频次排序；净频次保留用于判断可逆性。"

    @app.callback(
        Output("workflow-event-grid", "data"),
        Output("workflow-event-grid", "columns"),
        Output("workflow-events-alert", "children"),
        Input("workflow-store", "data"),
        Input("app-store", "data"),
    )
    def _load_workflow_events(workflow, app_store):
        channel = (workflow or {}).get("channel") or {}
        if not channel or int((workflow or {}).get("current_step") or 1) < 3:
            return [], _workflow_columns([]), "先选定一条生成或消耗通道。"
        try:
            payload = svc.rank_representative_events((app_store or {}).get("artifacts") or {}, str(channel.get("reaction_smiles") or ""))
        except svc.ServiceError as exc:
            return [], _workflow_columns([]), exc.message
        columns = _workflow_columns([("recommendation", "推荐", 88), ("before_timestep", "反应前", 86), ("after_timestep", "反应后", 86), ("atom_count", "原子数", 70), ("association_status", "原子关联", 94), ("broken_bonds", "断键", 140), ("formed_bonds", "成键", 140)])
        meta = payload.get("meta") or {}
        message = f"推荐 {meta.get('recommended_count', 0)} 条事件。"
        if not meta.get("trajectory_index_ready"):
            message += " " + str(meta.get("trajectory_index_message") or "需要准备轨迹索引。")
        return payload.get("rows") or [], columns, message

    @app.callback(
        Output("workflow-panel-1", "style"), Output("workflow-panel-2", "style"), Output("workflow-panel-3", "style"), Output("workflow-panel-4", "style"),
        Output("workflow-step-1", "className"), Output("workflow-step-2", "className"), Output("workflow-step-3", "className"), Output("workflow-step-4", "className"),
        Output("workflow-summary", "children"), Output("workflow-species-choice", "children"), Output("workflow-channel-choice", "children"), Output("workflow-event-choice", "children"),
        Output("workflow-species-confirm", "disabled"), Output("workflow-channel-confirm", "disabled"), Output("workflow-event-confirm", "disabled"), Output("workflow-validation-status", "children"),
        Input("workflow-store", "data"),
    )
    def _render_workflow_state(workflow):
        state = workflow or initial_workflow_store()
        current = max(1, min(4, int(state.get("current_step") or 1)))
        species, channel, event = state.get("species") or {}, state.get("channel") or {}, state.get("event") or {}
        panels = tuple({"display": "block"} if current == step else {"display": "none"} for step in range(1, 5))
        step_classes = tuple("rs-flow-step is-current" if current == step else ("rs-flow-step is-complete" if step < current else "rs-flow-step") for step in range(1, 5))
        summary_items: list[Any] = []
        if species:
            structure: Any = None
            smiles = str(species.get("smiles") or "")
            if smiles:
                try:
                    render_result = svc.render_species_svg(smiles, width=96, height=54)
                    if render_result.get("ok") and render_result.get("svg"):
                        encoded_svg = base64.b64encode(str(render_result["svg"]).encode("utf-8")).decode("ascii")
                        structure = html.Img(src=f"data:image/svg+xml;base64,{encoded_svg}", className="rs-summary-structure", alt=f"{species.get('formula') or 'selected'} structure")
                except Exception:
                    structure = None
            summary_items.append(html.Div([structure, html.Div([html.Span("实验物种", className="rs-summary-label"), html.Strong(f"{species.get('formula') or '?'} · {smiles}"), html.Span(str(species.get('structure_source') or 'SMILES'), className="rs-summary-source")])], className="rs-summary-item"))
        if channel:
            summary_items.append(html.Div([html.Span("选定通道", className="rs-summary-label"), html.Strong(str(channel.get("reaction_formulas") or channel.get("reaction_smiles") or ""))], className="rs-summary-item"))
        if event:
            summary_items.append(html.Div([html.Span("选定事件", className="rs-summary-label"), html.Strong(f"{event.get('event_id') or ''} · {event.get('before_timestep')} → {event.get('after_timestep')}")], className="rs-summary-item"))
        summary = summary_items or [html.Span("从第 1 步开始：导入数据集后检索实验目标物种。", className="rs-summary-empty")]
        species_choice = f"已选：{species.get('formula') or ''} · {species.get('smiles') or ''}" if species else "在表格中选择一个物种。"
        channel_choice = f"已选 {channel.get('role_label') or ''}通道：{channel.get('reaction_formulas') or ''}" if channel else "从生成或消耗通道中选择一条反应。"
        event_choice = f"已选 {event.get('recommendation') or ''} 事件：{event.get('event_id') or ''}" if event else "选择一条可验证的代表性事件。"
        return (*panels, *step_classes, summary, species_choice, channel_choice, event_choice, not bool(species), not bool(channel), not bool(event and event.get("validation_ready")), state.get("validation_message") or "")

    @app.callback(
        Output("workflow-viewer-store", "data"), Output("workflow-event-evidence", "children"), Output("workflow-bond-evidence", "children"),
        Output("workflow-frame-slider", "min"), Output("workflow-frame-slider", "max"), Output("workflow-frame-slider", "value"), Output("workflow-frame-slider", "marks"), Output("workflow-storyboard", "children"), Output("workflow-viewer-alert", "children"),
        Input("workflow-store", "data"), Input("app-store", "data"),
    )
    def _load_workflow_viewer(workflow, app_store):
        state = workflow or {}
        event = state.get("event") or {}
        if int(state.get("current_step") or 1) < 4 or not event:
            return None, [], [], 0, 0, 0, {}, [], "确认一条可验证事件后提取局部轨迹。"
        try:
            viewer = svc.build_rng_event_visualization((app_store or {}).get("artifacts") or {}, event)
        except svc.ServiceError as exc:
            return None, [], [], 0, 0, 0, {}, [], exc.message
        frames = viewer.get("frames") or []
        marks = {index: str(frame.get("frame")) for index, frame in enumerate(frames)}
        anchor = event.get("anchor_frame")
        anchor_index = next((index for index, frame in enumerate(frames) if frame.get("frame") == anchor), 0)
        bonds = viewer.get("bond_evidence") or {}
        event_evidence = [html.H3("事件来源与参与原子"), html.P("reactionevent.csv + molecules.csv"), html.Div(f"事件 ID：{event.get('event_id') or '-'}"), html.Div(f"Timestep：{event.get('before_timestep')} → {event.get('after_timestep')}"), html.Div(f"参与原子：{event.get('atom_ids') or '-'}"), html.Div(f"关联状态：{event.get('association_status') or '-'}")]
        bond_evidence = [html.H3("键变化证据"), html.Div([html.Strong("反应前键"), html.Code("; ".join(bonds.get("reactant") or []) or "—")]), html.Div([html.Strong("反应后键"), html.Code("; ".join(bonds.get("product") or []) or "—")]), html.Div([html.Strong("断裂键"), html.Code("; ".join(bonds.get("broken") or []) or "—")], className="rs-bond-broken"), html.Div([html.Strong("形成键"), html.Code("; ".join(bonds.get("formed") or []) or "—")], className="rs-bond-formed")]
        storyboard = []
        for number in viewer.get("storyboard_frames") or []:
            index = next((idx for idx, frame in enumerate(frames) if frame.get("frame") == number), None)
            if index is not None:
                label = (viewer.get("storyboard_labels") or {}).get(str(number), f"Frame {number}")
                storyboard.append(html.Div([html.Div(label, className="rs-storyboard-label"), dcc.Graph(figure=_event_frame_figure(viewer, index, "context", compact=True), config={"displayModeBar": False})], className="rs-storyboard-item"))
        return viewer, event_evidence, bond_evidence, 0, max(0, len(frames) - 1), anchor_index, marks, storyboard, "局部轨迹已提取；中间帧中的彩色键线仅表示事件键变化指示。"

    @app.callback(Output("workflow-trajectory-3d", "figure"), Output("workflow-frame-label", "children"), Input("workflow-frame-slider", "value"), Input("workflow-view-scope", "value"), Input("workflow-viewer-store", "data"))
    def _render_workflow_frame(frame_index, scope, viewer):
        if not viewer or not (viewer.get("frames") or []):
            import plotly.graph_objects as go
            return go.Figure(), ""
        frames = viewer.get("frames") or []
        safe = max(0, min(int(frame_index or 0), len(frames) - 1))
        frame = frames[safe]
        note = "（键变化指示）" if frame.get("bond_state") == "intermediate" else ""
        return _event_frame_figure(viewer, safe, scope or "context"), f"Frame {frame.get('frame')} {note}"

    @app.callback(Output("workflow-validation-download", "data"), Input("workflow-validation-export", "n_clicks"), State("workflow-store", "data"), prevent_initial_call=True)
    def _export_workflow_validations(n_clicks, workflow):
        if n_clicks is None:
            raise PreventUpdate
        rows = (workflow or {}).get("validations") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "local_trajectory_validations.csv", "type": "text/csv"}

    # ── Species search ──────────────────────────────────────────────

    @app.callback(
        Output("species-grid", "data"),
        Output("species-grid", "columns"),
        Output("species-alert", "children"),
        Output("species-grid-store", "data"),
        Output("species-grid", "selected_rows"),
        Input("species-search-btn", "n_clicks"),
        State("species-query", "value"),
        State("species-query-kind", "value"),
        State("species-mass-tol", "value"),
        State("species-mass-mode", "value"),
        State("species-top", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_species(n_clicks, query, kind, mass_tol, mass_mode, top, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        if not artifacts.get("reaction"):
            return [], _species_columns(), '请先在「管理数据」中导入包含 reactionabcd 的数据目录。', {"rows": []}, []
        try:
            result = svc.search_species(
                artifacts,
                query or "",
                kind=kind or "auto",
                mass_tolerance=float(mass_tol or 0.5),
                mass_mode=mass_mode or "exact",
                top=int(top or 0),
            )
        except svc.ServiceError as exc:
            return [], _species_columns(), str(exc.message), {"rows": []}, []

        rows = result.get("rows") or []
        message = f"找到 {len(rows)} 条匹配物种" if rows else "未找到匹配物种；可以放宽质量容差或切换查询类型。"
        return (
            rows,
            _species_columns(result.get("query_kind")),
            message,
            {
                "rows": rows,
                "query_kind": result.get("query_kind"),
                "searched": True,
                "message": message,
            },
            [],
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
                html.Div("开始分析", className="rs-empty-eyebrow"),
                html.H5("导入反应网络数据", className="rs-empty-title"),
                html.P(
                    "选择包含 reactionabcd 文件的数据目录后，即可按分子式、SMILES 或质量数检索物种。",
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
            text = "输入查询内容并执行检索。支持分子式、SMILES 和质量数。"
            title = "等待查询"
        empty = [
            html.Div(title, className="rs-empty-title"),
            html.P(text, className="rs-empty-copy"),
        ]
        return empty, {"display": "flex"}, {"display": "none"}, {"display": "none"}, False, True, {}

    # ── Species detail panel ────────────────────────────────────────

    @app.callback(
        Output("detail-panel", "style"),
        Output("detail-body", "style"),
        Output("detail-body", "children"),
        Output("detail-empty", "style"),
        Output("species-to-event-btn", "disabled"),
        Output("species-to-pathway-btn", "disabled"),
        Output("app-store", "data", allow_duplicate=True),
        Output("transitions-smiles", "value"),
        Output("evolution-targets", "value"),
        Output("network-smiles", "value"),
        Input("species-grid", "selected_rows"),
        State("species-grid", "data"),
        State("app-store", "data"),
        State("species-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_species_detail(selected_rows, table_rows, store, grid_store):
        store = store or {}
        if not selected_rows or len(selected_rows) == 0:
            return (
                {"display": "none"},
                {"display": "none"},
                [],
                {"display": "block"},
                True,
                True,
                no_update,
                no_update,
                no_update,
                no_update,
            )
        table_rows = table_rows or (grid_store or {}).get("rows") or []
        selected_indices = [
            int(index)
            for index in selected_rows
            if isinstance(index, int) and 0 <= int(index) < len(table_rows)
        ]
        if not selected_indices:
            raise PreventUpdate
        row_idx = selected_indices[0]
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
                no_update,
                no_update,
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
        evolution_targets = "\n".join(
            dict.fromkeys(
                str((table_rows[index] or {}).get("smiles") or "").strip()
                for index in selected_indices
                if str((table_rows[index] or {}).get("smiles") or "").strip()
            )
        )

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

        updated_store = {**store, "selected_smiles": smiles, "selected_formula": formula}
        return (
            {"display": "block"},
            {"display": "grid"},
            children,
            {"display": "none"},
            False,
            False,
            updated_store,
            smiles,
            evolution_targets,
            smiles,
        )

    @app.callback(
        Output("pathway-start-smiles", "value"),
        Input("species-to-pathway-btn", "n_clicks"),
        Input("rxn-to-pathway-btn", "n_clicks"),
        State("species-grid", "selected_rows"),
        State("species-grid", "data"),
        State("rxn-grid", "selected_rows"),
        State("rxn-grid", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _send_selection_to_pathway(
        species_clicks,
        reaction_clicks,
        species_selected,
        species_rows,
        reaction_selected,
        reaction_rows,
        store,
    ):
        if ctx.triggered_id == "species-to-pathway-btn":
            if species_clicks is None:
                raise PreventUpdate
            rows = species_rows or []
            if species_selected:
                index = int(species_selected[0])
                if 0 <= index < len(rows):
                    smiles = str((rows[index] or {}).get("smiles") or "")
                    if smiles:
                        return smiles
            smiles = str((store or {}).get("selected_smiles") or "")
            if smiles:
                return smiles
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
            return str(reactants[0])
        reaction_text = str(row.get("reaction_smiles") or "")
        first_side, separator, _second_side = reaction_text.partition(" -> ")
        if separator and first_side:
            return first_side.split(" + ", 1)[0]
        raise PreventUpdate

    # ── Transitions ─────────────────────────────────────────────────

    @app.callback(
        Output("transitions-grid", "data"),
        Output("transitions-grid", "columns"),
        Output("transitions-alert", "children"),
        Output("transitions-grid-store", "data"),
        Input("transitions-search-btn", "n_clicks"),
        State("transitions-smiles", "value"),
        State("transitions-direction", "value"),
        State("transitions-top", "value"),
        State("transitions-net-positive", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _search_transitions(n_clicks, smiles, direction, top, net_positive, store):
        if n_clicks is None:
            raise PreventUpdate
        store = store or {}
        artifacts = store.get("artifacts", {}) or {}
        smi = (smiles or store.get("selected_smiles") or "").strip()
        if not smi:
            return [], _transitions_columns(), "请先在物种检索中选择一个物种。", {"rows": []}
        try:
            result = svc.collect_transitions(
                artifacts,
                smi,
                direction=direction or "both",
                top=int(top or 0),
                net_positive_only=bool(net_positive),
            )
        except svc.ServiceError as exc:
            return [], _transitions_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _transitions_columns(), None, {"rows": rows}

    @app.callback(
        Output("transitions-selection-card", "style"),
        Output("transitions-selected-summary", "children"),
        Output("transitions-to-event-btn", "disabled"),
        Input("transitions-grid", "selected_rows"),
        State("transitions-grid", "data"),
    )
    def _show_selected_transition(selected_rows, rows):
        if not selected_rows:
            return {"display": "none"}, [], True
        rows = rows or []
        index = int(selected_rows[0])
        if index < 0 or index >= len(rows):
            return {"display": "none"}, [], True
        row = rows[index] or {}
        if not row.get("reaction_smiles"):
            return {"display": "none"}, [], True
        return {"display": "flex"}, _transition_selection_summary(row), False

    # ── Reaction formula search ─────────────────────────────────────

    @app.callback(
        Output("rxn-grid", "data"),
        Output("rxn-grid", "columns"),
        Output("rxn-alert", "children"),
        Output("rxn-grid-store", "data"),
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
    def _search_reactions(n_clicks, reactants, products, mode, top, with_share, share_metric, share_abs, share_positive, store):
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
            return [], _reaction_columns(), str(exc.message), {"rows": []}
        rows = result.get("rows") or []
        return rows, _reaction_columns(with_share=bool(with_share)), None, {"rows": rows, "meta": result.get("meta", {})}

    @app.callback(
        Output("event-reaction-text", "value"),
        Input("rxn-to-event-btn", "n_clicks"),
        Input("transitions-to-event-btn", "n_clicks"),
        State("rxn-grid", "selected_rows"),
        State("rxn-grid", "data"),
        State("transitions-grid", "selected_rows"),
        State("transitions-grid", "data"),
        prevent_initial_call=True,
    )
    def _send_reaction_to_event(rxn_clicks, transition_clicks, rxn_selected_rows, rxn_rows, transition_selected_rows, transition_rows):
        if ctx.triggered_id == "transitions-to-event-btn":
            selected_rows, rows, n_clicks = transition_selected_rows, transition_rows, transition_clicks
        else:
            selected_rows, rows, n_clicks = rxn_selected_rows, rxn_rows, rxn_clicks
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
        keys = ["smiles", "formula", "exact_mass", "nominal_mass", "tp_as_reactant", "tp_as_product", "total_throughput", "n_consume_rxns", "n_produce_rxns"]
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return {"content": buf.getvalue(), "filename": "species_search.csv", "type": "text/csv"}

    # ── CSV export: transitions ─────────────────────────────────────

    @app.callback(
        Output("transitions-csv-download", "data"),
        Input("transitions-csv-btn", "n_clicks"),
        State("transitions-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_transitions_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        grid_store = grid_store or {}
        rows = grid_store.get("rows") or []
        if not rows:
            raise PreventUpdate
        import csv
        import io

        buf = io.StringIO()
        keys = ["role", "reaction_smiles", "reaction_formulas", "forward_tp", "reverse_tp", "net_tp", "ratio_pct", "tp"]
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return {"content": buf.getvalue(), "filename": "transitions.csv", "type": "text/csv"}

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
        return fig, None, payload

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

    # ── Carbon-number evolution ────────────────────────────────────

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
        Output("carbon-dataset-name", "value"),
        Output("carbon-index-status", "children"),
        Output("carbon-index-status", "className"),
        Output("carbon-index-progress", "value"),
        Input("app-store", "data"),
        Input("carbon-index-refresh", "n_intervals"),
    )
    def _refresh_carbon_index_status(store, _n_intervals):
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
        return label, text, class_name, percent

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
        meta = payload.get("meta") or {}
        message = meta.get("message") or f"从 RNG 输出中找到 {len(rows)} 条事件"
        workflow = {
            "rows": rows,
            "meta": meta,
            "kind": "rng_event",
            "config": config,
        }
        return rows, _event_columns(rows), message, workflow

    @app.callback(
        Output("event-grid", "selected_rows"),
        Output("event-selected-store", "data", allow_duplicate=True),
        Output("event-selection-card", "style", allow_duplicate=True),
        Output("event-viewer-store", "data", allow_duplicate=True),
        Output("event-viewer-card", "style", allow_duplicate=True),
        Input("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_event_workspace(_workflow):
        """A new RNG event query invalidates the former selection and viewer."""
        return [], None, {"display": "none"}, None, {"display": "none"}

    @app.callback(
        Output("event-selected-store", "data"),
        Output("event-extract-id", "value"),
        Output("event-selected-summary", "children"),
        Output("event-selection-card", "style"),
        Input("event-grid", "selected_rows"),
        State("event-grid", "data"),
        State("event-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _select_event(selected_rows, table_rows, grid_store):
        if not selected_rows:
            raise PreventUpdate
        table_rows = table_rows or []
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate
        workflow = grid_store or {}
        kind = workflow.get("kind") or ""
        if kind != "rng_event":
            raise PreventUpdate
        selected = {"row": table_rows[row_idx] or {}, "kind": kind, "config": workflow.get("config") or {}}
        event_id = str(selected["row"].get("event_id") or "")
        return selected, event_id, _event_selection_summary(selected), {"display": "block"}

    @app.callback(
        Output("event-viewer-store", "data"),
        Output("event-viewer-card", "style"),
        Output("event-viewer-summary", "children"),
        Output("event-viewer-paths", "children"),
        Output("event-frame-slider", "min"),
        Output("event-frame-slider", "max"),
        Output("event-frame-slider", "value"),
        Output("event-frame-slider", "marks"),
        Output("event-storyboard", "children"),
        Output("event-alert", "children", allow_duplicate=True),
        Input("event-extract-btn", "n_clicks"),
        State("event-selected-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _extract_selected_event(n_clicks, selected, store):
        if n_clicks is None:
            raise PreventUpdate
        selected = selected or {}
        row = selected.get("row") or {}
        config = selected.get("config") or {}
        kind = selected.get("kind") or ""
        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            if kind == "rng_event":
                viewer = svc.build_rng_event_visualization(
                    artifacts,
                    row,
                    before_frames=int(config.get("before_frames") or 3),
                    after_frames=int(config.get("after_frames") or 3),
                )
            else:
                raise svc.ServiceError("请先从定位结果中选择一个事件", reason="missing_selection")
        except (svc.ServiceError, TypeError, ValueError) as exc:
            message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
            return None, {"display": "none"}, [], [], 0, 0, 0, {}, [], message

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
        paths = viewer.get("paths") or {}
        summary = html.Div(
            [
                html.Span(f"{len(frames)} 帧", className="rs-stat-chip"),
                html.Span(f"反应核 {len((viewer.get('atom_groups') or {}).get('core') or [])} 原子", className="rs-stat-chip"),
                html.Span(f"局部上下文 {len((viewer.get('atom_groups') or {}).get('context') or [])} 原子", className="rs-stat-chip"),
                html.Span(str(meta.get("verification_status") or meta.get("status") or "已提取"), className="rs-stat-chip"),
            ],
            className="rs-stat-row",
        )
        path_items = [f"轨迹: {paths.get('trajectory') or '-'}"]
        if paths.get("type_map"):
            path_items.append(f"类型映射: {paths['type_map']}")
        return viewer, {"display": "block"}, summary, " · ".join(path_items), 0, len(frames) - 1, anchor_index, marks, storyboard, "局部轨迹已提取，可在下方逐帧核查反应上下文。"

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

    # ── Candidate pathways ─────────────────────────────────────────

    @app.callback(
        Output("pathway-grid", "data"),
        Output("pathway-grid", "columns"),
        Output("pathway-cytoscape", "elements"),
        Output("pathway-alert", "children"),
        Output("pathway-store", "data"),
        Output("pathway-context-store", "data"),
        Output("pathway-grid", "selected_rows"),
        Input("pathway-search-btn", "n_clicks"),
        State("pathway-start-smiles", "value"),
        State("pathway-direction", "value"),
        State("pathway-max-depth", "value"),
        State("pathway-max-branches", "value"),
        State("pathway-max-paths", "value"),
        State("pathway-min-net-tp", "value"),
        State("pathway-min-directionality", "value"),
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
        store,
    ):
        if n_clicks is None:
            raise PreventUpdate
        start = str(start_smiles or "")
        artifacts = (store or {}).get("artifacts") or {}
        try:
            payload = svc.find_pathways(
                artifacts,
                start,
                direction=direction if direction is not None else "downstream",
                max_depth=int(3 if max_depth is None else max_depth),
                max_branches=int(5 if max_branches is None else max_branches),
                max_paths=int(20 if max_paths is None else max_paths),
                min_net_tp=int(1 if min_net_tp is None else min_net_tp),
                min_directionality=float(
                    0.05
                    if min_directionality is None
                    else min_directionality
                ),
            )
        except (TypeError, ValueError) as exc:
            return [], _pathway_columns(), [], str(exc), None, None, []
        except svc.ServiceError as exc:
            return [], _pathway_columns(), [], str(exc.message), None, None, []

        rows = _pathway_rows(payload)
        elements = svc.build_pathway_elements(payload)
        reason_messages = {
            "species_absent": "起始物种不在当前反应网络中。",
            "no_positive_net_continuation": "该物种没有正净通量的可继续反应。",
            "filtered_by_thresholds": "候选路径均被当前净通量或方向性阈值过滤。",
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
        pathway_context = {
            "schema_version": "reacnet-scope/pathway-context/v1",
            "dataset_id": _network_dataset_id(store),
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
        )

    @app.callback(
        Output("pathway-selected-path", "data"),
        Output("pathway-selected-step", "data"),
        Output("pathway-selection-summary", "children"),
        Output("pathway-open-events-btn", "disabled"),
        Output("pathway-highlight-network-btn", "disabled"),
        Output("pathway-highlight-store", "data"),
        Input("pathway-store", "data"),
        Input("pathway-grid", "selected_rows"),
        Input("pathway-cytoscape", "tapNodeData"),
        State("pathway-grid", "data"),
    )
    def _select_pathway(payload, selected_rows, node_data, grid_rows):
        payload = payload or {}
        paths = payload.get("paths") or []
        if ctx.triggered_id == "pathway-store" or not paths:
            return None, None, "选择一条路径或一个反应节点。", True, True, None

        selected_path = None
        selected_step = None
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
                        reactants = [
                            str(value)
                            for value in candidate.get("reactants") or []
                        ]
                        products = [
                            str(value)
                            for value in candidate.get("products") or []
                        ]
                        selected_step = {
                            **candidate,
                            "path_rank": rank,
                            "step_index": step_index,
                            "reaction_text": (
                                f"{' + '.join(reactants)} -> "
                                f"{' + '.join(products)}"
                            ),
                        }
        if selected_path is None:
            return None, None, "选择一条有效路径或反应节点。", True, True, None
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
            summary = (
                f"路径 {path_handoff['path_rank']} · 第 "
                f"{selected_step['step_index']} 步 · "
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
            False,
            None,
        )

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
        Output("pathway-highlight-store", "data", allow_duplicate=True),
        Output("network-anchor-smiles", "value", allow_duplicate=True),
        Output("network-semantics", "value", allow_duplicate=True),
        Input("pathway-highlight-network-btn", "n_clicks"),
        State("pathway-selected-path", "data"),
        State("pathway-context-store", "data"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _send_pathway_to_network(
        n_clicks,
        selected_path,
        pathway_context,
        app_store,
    ):
        if n_clicks is None or not selected_path:
            raise PreventUpdate
        context = (
            pathway_context
            if isinstance(pathway_context, dict)
            else {}
        )
        source_dataset_id = str(context.get("dataset_id") or "")
        current_dataset_id = _network_dataset_id(app_store)
        if (
            context.get("schema_version")
            != "reacnet-scope/pathway-context/v1"
            or not source_dataset_id
            or source_dataset_id != current_dataset_id
        ):
            return None, "", no_update
        species_ids = list(selected_path.get("species_ids") or [])
        if not species_ids:
            return None, "", no_update
        handoff = {
            "schema_version": "reacnet-scope/pathway-highlight/v1",
            "source": "pathway",
            "pending": True,
            "dataset_id": source_dataset_id,
            "source_signatures": dict(
                context.get("source_signatures") or {}
            ),
            "path_rank": selected_path.get("path_rank"),
            "species_ids": species_ids,
            "reaction_keys": list(selected_path.get("reaction_keys") or []),
        }
        return handoff, str(species_ids[0]), "mechanism"

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

    # ── Dual-semantics network workspace ────────────────────────────

    @app.callback(
        Output("network-observation-controls", "style"),
        Output("network-mechanism-controls", "style"),
        Output("network-json-btn", "disabled"),
        Output("network-graphml-btn", "disabled"),
        Output("network-gexf-btn", "disabled"),
        Output("network-node-csv-btn", "disabled"),
        Output("network-edge-csv-btn", "disabled"),
        Input("network-semantics", "value"),
        Input("network-store", "data"),
        Input("app-store", "data"),
    )
    def _switch_network_semantics(semantics, payload, app_store):
        mechanism = semantics == "mechanism"
        export_ready = (
            mechanism
            and isinstance(payload, dict)
            and payload.get("network_semantics") == "mechanism"
            and str(payload.get("dataset_id") or "")
            == _network_dataset_id(app_store)
        )
        return (
            {"display": "none"} if mechanism else {"display": "flex"},
            {"display": "flex"} if mechanism else {"display": "none"},
            not export_ready,
            not export_ready,
            not export_ready,
            not export_ready,
            not export_ready,
        )

    @app.callback(
        Output("network-anchor-smiles", "value"),
        Output("network-semantics", "value"),
        Input("app-store", "data"),
    )
    def _network_anchor_from_dataset(app_store):
        selected = str((app_store or {}).get("selected_smiles") or "")
        return selected, ("mechanism" if selected else no_update)

    @app.callback(
        Output("pathway-store", "data", allow_duplicate=True),
        Output("pathway-context-store", "data", allow_duplicate=True),
        Output("pathway-selected-path", "data", allow_duplicate=True),
        Output("pathway-selected-step", "data", allow_duplicate=True),
        Output("pathway-highlight-store", "data", allow_duplicate=True),
        Output("pathway-grid", "selected_rows", allow_duplicate=True),
        Output("pathway-grid", "data", allow_duplicate=True),
        Output("pathway-cytoscape", "elements", allow_duplicate=True),
        Output("pathway-cytoscape", "tapNodeData", allow_duplicate=True),
        Output("pathway-selection-summary", "children", allow_duplicate=True),
        Output("pathway-open-events-btn", "disabled", allow_duplicate=True),
        Output("pathway-highlight-network-btn", "disabled", allow_duplicate=True),
        Output("pathway-alert", "children", allow_duplicate=True),
        Input("app-store", "data"),
        Input("network-semantics", "value"),
        State("pathway-context-store", "data"),
        State("pathway-highlight-store", "data"),
        prevent_initial_call=True,
    )
    def _reset_cross_context_pathway_state(
        app_store,
        network_semantics,
        pathway_context,
        pathway_handoff,
    ):
        if ctx.triggered_id == "app-store":
            current_dataset_id = _network_dataset_id(app_store)
            source_dataset_id = str(
                (pathway_context or {}).get("dataset_id") or ""
            )
            if (
                source_dataset_id
                and source_dataset_id == current_dataset_id
            ):
                raise PreventUpdate
            return (
                None,
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
                True,
                "",
            )
        if ctx.triggered_id == "network-semantics":
            handoff = (
                pathway_handoff
                if isinstance(pathway_handoff, dict)
                else {}
            )
            preserve_pending_handoff = (
                network_semantics == "mechanism"
                and handoff.get("pending") is True
                and str(handoff.get("dataset_id") or "")
                == _network_dataset_id(app_store)
            )
            return (
                no_update,
                no_update,
                None,
                None,
                no_update if preserve_pending_handoff else None,
                [],
                no_update,
                no_update,
                None,
                "选择一条路径或一个反应节点。",
                True,
                True,
                no_update,
            )
        raise PreventUpdate

    @app.callback(
        Output("network-alert", "children"),
        Output("network-raw-store", "data"),
        Output("network-store", "data"),
        Output("network-cytoscape", "tapNodeData"),
        Output("network-context-store", "data"),
        Input("network-search-btn", "n_clicks"),
        Input("network-semantics", "value"),
        Input("network-evidence-filter", "value"),
        Input("app-store", "data"),
        State("network-min-count", "value"),
        State("network-max-species", "value"),
        State("network-top-edges", "value"),
        State("network-anchor-smiles", "value"),
        State("network-direction", "value"),
        State("network-depth", "value"),
        State("network-min-net-tp", "value"),
        State("network-max-nodes", "value"),
        State("network-layout", "value"),
        State("network-raw-store", "data"),
        State("network-context-store", "data"),
        State("pathway-highlight-store", "data"),
        prevent_initial_call=True,
    )
    def _update_network_state(
        n_clicks,
        semantics,
        evidence_filter,
        store,
        min_count,
        max_species,
        top_edges,
        anchor_smiles,
        direction,
        max_depth,
        min_net_tp,
        max_nodes,
        layout_name,
        raw_payload,
        previous_context,
        pathway_handoff,
    ):
        store = store or {}
        semantics = (
            "mechanism" if semantics == "mechanism" else "event_transfer"
        )
        dataset_id = _network_dataset_id(store)
        current_context = {
            "dataset_id": dataset_id,
            "network_semantics": semantics,
        }
        previous_context = (
            previous_context
            if isinstance(previous_context, dict)
            else {}
        )
        if ctx.triggered_id in {"network-semantics", "app-store"}:
            if previous_context == current_context:
                raise PreventUpdate
            return "", None, None, None, current_context

        if ctx.triggered_id == "network-evidence-filter":
            if (
                not isinstance(raw_payload, dict)
                or raw_payload.get("network_semantics") != semantics
                or str(raw_payload.get("dataset_id") or "") != dataset_id
            ):
                return "", no_update, None, None, current_context
            try:
                displayed = svc.project_network_evidence(
                    raw_payload,
                    str(evidence_filter or "all"),
                )
            except (TypeError, ValueError) as exc:
                return (
                    dbc.Alert(str(exc), color="danger", className="py-2"),
                    no_update,
                    None,
                    None,
                    current_context,
                )
            return (
                _network_status_alert(displayed),
                no_update,
                displayed,
                None,
                current_context,
            )

        if ctx.triggered_id != "network-search-btn" or n_clicks is None:
            raise PreventUpdate
        artifacts = store.get("artifacts", {}) or {}
        try:
            if semantics == "mechanism":
                anchor_smiles = str(anchor_smiles or "").strip()
                if not anchor_smiles:
                    return (
                        dbc.Alert(
                            "请输入锚点物种的精确 SMILES；这与“锚点不在网络中”的空结果不同。",
                            color="warning",
                            className="py-2",
                        ),
                        None,
                        None,
                        None,
                        current_context,
                    )
                result = svc.build_mechanism_elements(
                    artifacts,
                    anchor_smiles=anchor_smiles,
                    direction=(
                        "both"
                        if direction is None
                        else str(direction)
                    ),
                    max_depth=int(2 if max_depth is None else max_depth),
                    min_net_tp=int(
                        1 if min_net_tp is None else min_net_tp
                    ),
                    max_nodes=int(200 if max_nodes is None else max_nodes),
                )
            else:
                result = svc.build_observation_elements(
                    artifacts,
                    min_count=int(1 if min_count is None else min_count),
                    max_species=int(
                        60 if max_species is None else max_species
                    ),
                    top_edges=int(40 if top_edges is None else top_edges),
                )
        except (TypeError, ValueError) as exc:
            return (
                dbc.Alert(str(exc), color="danger", className="py-2"),
                None,
                None,
                None,
                current_context,
            )
        except svc.ServiceError as exc:
            message = (
                "请输入锚点物种的精确 SMILES。"
                if exc.reason == "bad_mechanism_query"
                and not str(anchor_smiles or "").strip()
                else str(exc.message)
            )
            return (
                dbc.Alert(message, color="danger", className="py-2"),
                None,
                None,
                None,
                current_context,
            )

        raw = {
            **result,
            "dataset_id": dataset_id or "dataset",
            "_ui_layout": str(layout_name or "concentric"),
        }
        handoff = (
            pathway_handoff
            if isinstance(pathway_handoff, dict)
            else {}
        )
        handoff_dataset = str(handoff.get("dataset_id") or "")
        species_ids = [
            str(value) for value in handoff.get("species_ids") or []
        ]
        if (
            semantics == "mechanism"
            and species_ids
            and anchor_smiles in species_ids
            and handoff_dataset
            and handoff_dataset == dataset_id
        ):
            raw["_ui_pathway_highlight"] = {
                "path_rank": handoff.get("path_rank"),
                "species_ids": species_ids,
                "reaction_keys": [
                    str(value)
                    for value in handoff.get("reaction_keys") or []
                ],
            }
        try:
            displayed = svc.project_network_evidence(
                raw,
                str(evidence_filter or "all"),
            )
        except (TypeError, ValueError) as exc:
            return (
                dbc.Alert(str(exc), color="danger", className="py-2"),
                None,
                None,
                None,
                current_context,
            )
        return (
            _network_status_alert(displayed),
            raw,
            displayed,
            None,
            current_context,
        )

    @app.callback(
        Output("network-cytoscape", "elements"),
        Output("network-cytoscape", "layout"),
        Output("network-cytoscape", "stylesheet"),
        Output("network-semantics-badge", "children"),
        Input("network-store", "data"),
        Input("network-layout", "value"),
    )
    def _render_network(payload, layout_name):
        payload = payload or {}
        elements = [
            {
                **item,
                "data": dict(item.get("data") or {}),
                "classes": str(item.get("classes") or ""),
            }
            for item in payload.get("elements") or []
            if isinstance(item, dict)
        ]
        handoff = (
            payload.get("_ui_pathway_highlight")
            if isinstance(payload.get("_ui_pathway_highlight"), dict)
            else {}
        )
        species_ids = {
            str(value) for value in handoff.get("species_ids") or []
        }
        reaction_keys = {
            str(value) for value in handoff.get("reaction_keys") or []
        }
        for item in elements:
            data = item["data"]
            matched = (
                str(data.get("smiles") or "") in species_ids
                or str(data.get("reaction_key") or "") in reaction_keys
            )
            if matched:
                classes = {
                    value
                    for value in str(item.get("classes") or "").split()
                    if value
                }
                classes.add("is-path-highlight")
                item["classes"] = " ".join(sorted(classes))

        selected_layout = (
            layout_name
            if layout_name
            in {"concentric", "cose", "grid", "circle", "breadthfirst"}
            else "concentric"
        )
        semantics = str(payload.get("network_semantics") or "")
        if semantics == "mechanism":
            badge = "mechanism · reaction passage counts"
        elif semantics == "event_transfer":
            badge = "event_transfer · aggregate observation"
        else:
            badge = "尚未构建网络"
        return elements, {"name": selected_layout}, NETWORK_STYLESHEET, badge

    @app.callback(
        Output("network-detail-panel", "children"),
        Output("network-open-events-btn", "disabled"),
        Input("network-cytoscape", "tapNodeData"),
        Input("network-store", "data"),
    )
    def _network_node_detail(node_data, payload):
        if (
            ctx.triggered_id == "network-store"
            or not isinstance(node_data, dict)
        ):
            return "选择一个物种或反应节点查看详情。", True
        node_id = str(node_data.get("id") or "")
        canonical = next(
            (
                node
                for node in (payload or {}).get("nodes") or []
                if str(node.get("id") or "") == node_id
            ),
            node_data,
        )
        if canonical.get("kind") == "reaction":
            reactants = [
                str(value) for value in canonical.get("reactants") or []
            ]
            products = [
                str(value) for value in canonical.get("products") or []
            ]
            reaction_text = (
                f"{' + '.join(reactants)} → {' + '.join(products)}"
            )
            metrics = [
                ("正向 TP", canonical.get("forward_tp")),
                ("反向 TP", canonical.get("reverse_tp")),
                ("净 TP", canonical.get("net_tp")),
                ("事件总数", canonical.get("event_total")),
                ("匹配事件", canonical.get("matched_event_total")),
                ("事件覆盖率", canonical.get("event_coverage")),
                ("证据状态", canonical.get("evidence_status")),
            ]
            return (
                html.Div(
                    [
                        html.Div(reaction_text, className="rs-network-detail-title"),
                        html.Dl(
                            [
                                item
                                for label, value in metrics
                                for item in (
                                    html.Dt(label),
                                    html.Dd("—" if value is None else str(value)),
                                )
                            ]
                        ),
                    ]
                ),
                not bool(reactants or products),
            )
        return (
            html.Div(
                [
                    html.Div(
                        str(canonical.get("formula") or canonical.get("label") or ""),
                        className="rs-network-detail-title",
                    ),
                    html.Code(str(canonical.get("smiles") or "")),
                ]
            ),
            True,
        )

    @app.callback(
        Output("event-reaction-text", "value", allow_duplicate=True),
        Input("network-open-events-btn", "n_clicks"),
        State("network-cytoscape", "tapNodeData"),
        prevent_initial_call=True,
    )
    def _send_network_reaction_to_events(n_clicks, node_data):
        if n_clicks is None or not isinstance(node_data, dict):
            raise PreventUpdate
        reactants = [str(value) for value in node_data.get("reactants") or []]
        products = [str(value) for value in node_data.get("products") or []]
        if not reactants and not products:
            raise PreventUpdate
        return f"{' + '.join(reactants)} -> {' + '.join(products)}"

    def _network_export_name(payload, suffix):
        dataset_id = str((payload or {}).get("dataset_id") or "dataset")
        semantics = str(
            (payload or {}).get("network_semantics") or "network"
        )
        schema = str(
            (payload or {}).get("schema_version")
            or "reacnet-scope/mechanism-network/v1"
        )
        safe = lambda value: re.sub(  # noqa: E731
            r"[^\w.-]+",
            "-",
            value,
            flags=re.UNICODE,
        ).strip("-._") or "unknown"
        return (
            f"{safe(dataset_id)}_{safe(semantics)}_"
            f"{safe(schema.replace('/', '-'))}{suffix}"
        )

    def _network_export(payload, format_name, suffix):
        if (
            not isinstance(payload, dict)
            or payload.get("network_semantics") != "mechanism"
        ):
            return no_update
        try:
            exported = svc.export_mechanism_graph(payload, format_name)
        except (TypeError, ValueError):
            return no_update
        filename = _network_export_name(payload, suffix)
        if format_name == "cytoscape-json":
            return dcc.send_string(
                json.dumps(
                    exported,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                filename,
            )
        if format_name in {"graphml", "gexf"}:
            return dcc.send_bytes(exported, filename)
        return dcc.send_string(str(exported), filename)

    @app.callback(
        Output("network-json-download", "data"),
        Input("network-json-btn", "n_clicks"),
        State("network-store", "data"),
        prevent_initial_call=True,
    )
    def _download_network_json(n_clicks, payload):
        if n_clicks is None:
            raise PreventUpdate
        return _network_export(payload, "cytoscape-json", ".json")

    @app.callback(
        Output("network-graphml-download", "data"),
        Input("network-graphml-btn", "n_clicks"),
        State("network-store", "data"),
        prevent_initial_call=True,
    )
    def _download_network_graphml(n_clicks, payload):
        if n_clicks is None:
            raise PreventUpdate
        return _network_export(payload, "graphml", ".graphml")

    @app.callback(
        Output("network-gexf-download", "data"),
        Input("network-gexf-btn", "n_clicks"),
        State("network-store", "data"),
        prevent_initial_call=True,
    )
    def _download_network_gexf(n_clicks, payload):
        if n_clicks is None:
            raise PreventUpdate
        return _network_export(payload, "gexf", ".gexf")

    @app.callback(
        Output("network-node-csv-download", "data"),
        Input("network-node-csv-btn", "n_clicks"),
        State("network-store", "data"),
        prevent_initial_call=True,
    )
    def _download_network_node_csv(n_clicks, payload):
        if n_clicks is None:
            raise PreventUpdate
        return _network_export(payload, "node-csv", "_nodes.csv")

    @app.callback(
        Output("network-edge-csv-download", "data"),
        Input("network-edge-csv-btn", "n_clicks"),
        State("network-store", "data"),
        prevent_initial_call=True,
    )
    def _download_network_edge_csv(n_clicks, payload):
        if n_clicks is None:
            raise PreventUpdate
        return _network_export(payload, "edge-csv", "_edges.csv")

    @app.callback(
        Output("network-cytoscape", "generateImage"),
        Input("network-png-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def _export_network_png(n_clicks):
        if n_clicks is None:
            raise PreventUpdate
        return {"type": "png", "action": "download", "filename": "observation_network"}

    # ── Literature mechanism verification ────────────────────────────

    @app.callback(
        Output("literature-grid", "data"),
        Output("literature-grid", "columns"),
        Output("literature-alert", "children"),
        Output("literature-grid-store", "data"),
        Output("literature-summary", "children"),
        Output("literature-summary-card", "style"),
        Input("literature-verify-btn", "n_clicks"),
        State("literature-reactions-input", "value"),
        State("literature-verify-mode", "value"),
        State("app-store", "data"),
        prevent_initial_call=True,
    )
    def _verify_literature_mechanism(n_clicks, reaction_text, verify_mode, store):
        if n_clicks is None:
            raise PreventUpdate
        if not reaction_text or not str(reaction_text).strip():
            from dash import no_update
            return no_update, no_update, "请输入至少一个反应式", no_update, no_update, no_update

        from rng_tools.mechanism_verify import parse_literature_reaction_text
        reaction_lines = parse_literature_reaction_text(str(reaction_text))
        if not reaction_lines:
            from dash import no_update
            return no_update, no_update, "未能解析任何有效反应式", no_update, no_update, no_update

        artifacts = (store or {}).get("artifacts", {}) or {}
        try:
            payload = svc.verify_literature_mechanism(
                artifacts,
                reaction_lines,
                verify_mode=verify_mode or "species",
            )
        except svc.ServiceError as exc:
            return [], _literature_columns(), str(exc.message), {"rows": []}, None, {"display": "none"}

        rows = payload.get("rows") or []
        summary = payload.get("summary") or {}
        from dash import html

        summary_children = html.Div(
            [
                html.Div(
                    [
                        html.Span(f"共 {summary.get('total_reactions', 0)} 个反应", className="me-3"),
                        html.Span(f"检出 {summary.get('detected', 0)} 个", className="me-3 rs-evidence-detected", style={"padding": "2px 8px", "borderRadius": 4}),
                        html.Span(f"净通量 {summary.get('has_net_flux', 0)} 个", className="me-3 rs-evidence-net-flux", style={"padding": "2px 8px", "borderRadius": 4}),
                        html.Span(f"未检出 {summary.get('not_detected', 0)} 个", className="rs-evidence-not-detected", style={"padding": "2px 8px", "borderRadius": 4}),
                    ],
                    className="rs-stat-row",
                ),
                html.Div(
                    f"检出率: {summary.get('detection_rate', 0) * 100:.1f}%",
                    className="small text-muted mt-2",
                ),
            ]
        )
        meta = payload.get("meta") or {}
        message = meta.get("message") or None
        return rows, _literature_columns(), message, {"rows": rows, "summary": summary}, summary_children, {"display": "block"}

    @app.callback(
        Output("literature-csv-download", "data"),
        Input("literature-csv-btn", "n_clicks"),
        State("literature-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _export_literature_csv(n_clicks, grid_store):
        if n_clicks is None:
            raise PreventUpdate
        rows = (grid_store or {}).get("rows") or []
        if not rows:
            raise PreventUpdate
        return {"content": svc.rows_to_csv(rows), "filename": "literature_evidence_matrix.csv", "type": "text/csv"}

    # ── Batch comparison ────────────────────────────────────────────

    @app.callback(
        Output("batch-condition-selector", "options"),
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
            return [], None, f"扫描失败: {exc.message}"
        groups = payload.get("groups") or []
        options = [
            {
                "label": f"{g['group_name']} ({g['n_replicates']} 个重复)",
                "value": g["group_name"],
            }
            for g in groups
        ]
        status = f"扫描完成: {payload.get('total_conditions', 0)} 个条件, {payload.get('total_groups', 0)} 个条件组"
        return options, payload, status

    @app.callback(
        Output("batch-matrix-grid", "data"),
        Output("batch-matrix-grid", "columns"),
        Output("batch-alert", "children"),
        Output("batch-matrix-grid-store", "data"),
        Input("batch-compare-btn", "n_clicks"),
        State("batch-condition-selector", "value"),
        State("batch-conditions-store", "data"),
        State("batch-min-detection", "value"),
        State("batch-top-n", "value"),
        prevent_initial_call=True,
    )
    def _run_batch_comparison(n_clicks, selected_groups, conditions_payload, min_detection, top_n):
        if n_clicks is None:
            raise PreventUpdate
        if not selected_groups:
            return [], [], "请选择至少一个条件组", {"rows": []}

        all_conditions = (conditions_payload or {}).get("conditions") or []
        groups_dict = (conditions_payload or {}).get("groups") or []

        # Find folders for selected groups
        selected_folders = []
        selected_names = []
        for grp in groups_dict:
            if grp["group_name"] in selected_groups:
                for cname in grp.get("conditions", []):
                    for c in all_conditions:
                        if c["name"] == cname:
                            selected_folders.append(c["folder"])
                            selected_names.append(cname)
                            break

        if not selected_folders:
            return [], [], "未找到选中条件的目录", {"rows": []}

        try:
            payload = svc.run_batch_comparison(
                selected_folders,
                selected_names,
                min_detection_rate=float(min_detection or 0),
                top_n=int(top_n or 50),
            )
        except svc.ServiceError as exc:
            return [], _batch_comparison_columns([]), str(exc.message), {"rows": []}

        rows = payload.get("rows") or []
        columns = _columns_from_rows(rows, []) if rows else _batch_comparison_columns(payload.get("condition_names") or [])
        message = (payload.get("meta") or {}).get("message") or None
        return rows, columns, message, {"rows": rows, "condition_names": payload.get("condition_names", [])}

    @app.callback(
        Output("batch-reaction-chart", "figure"),
        Output("batch-reaction-stats", "children"),
        Output("batch-detail-card", "style"),
        Input("batch-matrix-grid", "selected_rows"),
        State("batch-matrix-grid", "data"),
        State("batch-matrix-grid-store", "data"),
        prevent_initial_call=True,
    )
    def _show_reaction_detail(selected_rows, table_rows, grid_store):
        if not selected_rows:
            raise PreventUpdate
        table_rows = table_rows or []
        row_idx = int(selected_rows[0])
        if row_idx < 0 or row_idx >= len(table_rows):
            raise PreventUpdate

        row = table_rows[row_idx] or {}
        rxn_smiles = str(row.get("reaction_smiles", ""))

        condition_names = (grid_store or {}).get("condition_names") or []
        tp_values = []
        for cn in condition_names:
            tp = float(row.get(f"tp_{cn}", 0) or 0)
            tp_values.append((cn, tp))

        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=[t[0] for t in tp_values],
                y=[t[1] for t in tp_values],
                text=[str(int(t[1])) for t in tp_values],
                textposition="auto",
            )
        )
        fig.update_layout(
            title=f"反应通量对比 — {rxn_smiles[:80]}",
            xaxis_title="条件",
            yaxis_title="TP (Total Passages)",
            height=300,
            margin={"l": 50, "r": 20, "t": 40, "b": 80},
        )

        stats = html.Div(
            [
                html.Div(f"反应式: {rxn_smiles}", className="mb-2"),
                html.Div(f"检出率: {row.get('detection_rate', '-')}", className="small text-muted"),
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
        return {"content": svc.rows_to_csv(rows), "filename": "batch_comparison.csv", "type": "text/csv"}

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
    columns = [
        {"field": "formula", "headerName": "分子式", "width": 110},
        {"field": "smiles", "headerName": "SMILES", "flex": 2, "minWidth": 200},
        {"field": "exact_mass", "headerName": "精确质量", "width": 110, "type": "numericColumn"},
        {"field": "nominal_mass", "headerName": "标称质量", "width": 95, "type": "numericColumn"},
    ]
    if query_kind == "mass":
        columns.extend([
            {"field": "mass_error", "headerName": "质量误差", "width": 100, "type": "numericColumn"},
            {"field": "ppm_error", "headerName": "误差 ppm", "width": 95, "type": "numericColumn"},
        ])
    columns.extend([
        {"field": "tp_as_reactant", "headerName": "TP(反应物)", "width": 105, "type": "numericColumn"},
        {"field": "tp_as_product", "headerName": "TP(产物)", "width": 100, "type": "numericColumn"},
        {"field": "total_throughput", "headerName": "总通量", "width": 100, "type": "numericColumn"},
        {"field": "n_consume_rxns", "headerName": "消耗反应", "width": 95, "type": "numericColumn"},
        {"field": "n_produce_rxns", "headerName": "生成反应", "width": 95, "type": "numericColumn"},
    ])
    return _dt_columns(columns)


def _transitions_columns():
    return _dt_columns([
        {"field": "role", "headerName": "方向", "width": 75},
        {"field": "reaction_formulas", "headerName": "反应式", "flex": 2, "minWidth": 220},
        {"field": "forward_tp", "headerName": "TP(正向)", "width": 100, "type": "numericColumn"},
        {"field": "reverse_tp", "headerName": "TP(反向)", "width": 100, "type": "numericColumn"},
        {"field": "net_tp", "headerName": "净 TP", "width": 90, "type": "numericColumn"},
        {"field": "ratio_pct", "headerName": "占比%", "width": 85, "type": "numericColumn"},
        {"field": "tp", "headerName": "总 TP", "width": 90, "type": "numericColumn"},
    ])


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


def _event_columns(rows=None):
    preferred = [
        "event_class",
        "event_index",
        "event_id",
        "timestep_index",
        "before_timestep",
        "after_timestep",
        "reactant",
        "product",
        "atom_count",
        "atom_ids",
        "association_status",
        "reactant_bonds",
        "product_bonds",
        "anchor_frame",
        "reaction_smiles",
    ]
    return _columns_from_rows(rows or [], preferred)


def _pathway_columns():
    return _dt_columns(
        [
            {"field": "rank", "headerName": "#", "type": "numericColumn"},
            {"field": "formula_chain", "headerName": "分子式路径"},
            {"field": "smiles_chain", "headerName": "SMILES 路径"},
            {"field": "path_score", "headerName": "路径分数", "type": "numericColumn"},
            {"field": "weakest_step_score", "headerName": "最弱步分数", "type": "numericColumn"},
            {"field": "depth", "headerName": "深度", "type": "numericColumn"},
            {"field": "evidence_badge", "headerName": "证据"},
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
                "path_score": path.get("score"),
                "weakest_step_score": min(step_scores) if step_scores else None,
                "depth": len(steps),
                "evidence_badge": (
                    "事件已关联"
                    if path.get("evidence_status") == "evidence_linked"
                    else "仅网络"
                ),
            }
        )
    return rows


def _literature_columns():
    return _dt_columns([
        {"field": "index", "headerName": "#", "width": 50},
        {"field": "reaction_text", "headerName": "文献反应式", "flex": 2, "minWidth": 200},
        {"field": "evidence_label", "headerName": "证据等级", "width": 130},
        {"field": "detected", "headerName": "是否检测到", "width": 100},
        {"field": "forward_tp", "headerName": "正向次数", "width": 90},
        {"field": "net_tp", "headerName": "净次数", "width": 90},
        {"field": "is_transient", "headerName": "瞬时过程", "width": 90},
        {"field": "atom_confirmed_count", "headerName": "原子确认数", "width": 100},
        {"field": "notes", "headerName": "备注", "flex": 1, "minWidth": 150},
    ])


def _batch_comparison_columns(condition_names=None):
    condition_names = condition_names or []
    base = [
        {"field": "index", "headerName": "#", "width": 50},
        {"field": "reaction_smiles", "headerName": "反应式 (SMILES)", "flex": 2, "minWidth": 200},
        {"field": "detection_rate", "headerName": "检出率", "width": 80},
    ]
    for cn in condition_names:
        base.append({"field": f"tp_{cn}", "headerName": f"{cn} (TP)", "width": 100})
    return _dt_columns(base)


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
    from dash import html

    labels = {
        "reaction": "Reaction",
        "species": "Species",
        "trajectory": "Trajectory",
        "route": "Route",
        "table": "Table",
    }
    chips: list[Any] = []
    for key, label in labels.items():
        path = artifacts.get(key)
        if path:
            chips.append(html.Span(f"{label}: {path}", style={"display": "block", "fontSize": 12, "color": "#4b5563", "wordBreak": "break-all"}))
        else:
            chips.append(html.Span(f"{label}: 缺失", style={"display": "block", "fontSize": 12, "color": "#9ca3af"}))
    return html.Div(chips, style={"lineHeight": 1.7})


def _carbon_highlights(summary: dict[str, Any], meta: dict[str, Any]) -> Any:
    from dash import html

    base = summary.get("base") if isinstance(summary.get("base"), dict) else summary
    items = [
        ("Rows", meta.get("n_plot_rows")),
        ("Systems", meta.get("n_systems")),
        ("Regions", meta.get("n_regions")),
        ("Plot", meta.get("plot_mode")),
        ("Parent", f"C{base.get('parent_carbon_number')}" if base.get("parent_carbon_number") else None),
        ("Max C", base.get("max_carbon_number_observed")),
        ("Large peak", base.get("large_hydrocarbon_peak_time")),
    ]
    chips = [
        html.Span([html.Strong(label), html.Span(_fmt_num(value))], className="rs-stat-chip")
        for label, value in items
        if value not in (None, "")
    ]
    return chips


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


def html_dl(items: dict[str, str]) -> Any:
    from dash import html

    children: list[Any] = []
    for key, value in items.items():
        children.extend([html.Dt(key), html.Dd(value)])
    return html.Dl(children)


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
