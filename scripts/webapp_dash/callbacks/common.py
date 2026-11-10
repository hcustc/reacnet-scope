"""Shared helpers for the Dash callback modules."""

from __future__ import annotations

from .. import ui_components as ui
from pathlib import Path
from typing import Any
from urllib.parse import quote
from dash import (
    ctx,
    html,
)
from reacnet_scope import services as svc
_CAPABILITY_LABELS = {
    "reaction_search": "反应检索",
    "species_abundance": "物种丰度",
    "event_search": "事件检索",
    "species_fate": "物种命运分析",
    "trajectory_evidence": "轨迹证据",
    "element_distribution": "元素分布",
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


def _format_bytes(value: Any) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _capability_state_class(state: Any) -> str:
    return str(state or "invalid").replace("_", "-")


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
        return html.Div(
            [html.Button(
                children,
                id={
                    "type": "rxn-structure-species",
                    "scope": action_scope,
                    "side": side,
                    "index": int(item.get("index") or 0),
                    "smiles": smiles,
                    "formula": formula,
                },
                n_clicks=0, type="button", title=action_label,
                className="rs-channel-species-card rs-channel-species-action",
                **{"aria-label": action_label},
            ),
             html.Button("查看物种详情", id={
                 "type": "species-open-detail", "scope": action_scope,
                 "side": side, "index": int(item.get("index") or 0),
                 "smiles": smiles, "formula": formula,
             }, n_clicks=0, type="button", className="rs-open-species-detail")],
            className="rs-channel-species-actions",
        )
    return html.Div(children, className="rs-channel-species-card")


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


def _evolution_comparison_active(page: dict | None) -> bool:
    state = page or {}
    return state.get("page") == "batch-compare" or (
        state.get("page") == "evolution" and bool(state.get("compare_sources"))
    )

# ── Directory browser helpers ───────────────────────────────────────

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
        display = {field: raw.get(field) for field in fields}
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


def _dt_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return ui.columns(columns)


# ── Helpers ─────────────────────────────────────────────────────────


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


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
