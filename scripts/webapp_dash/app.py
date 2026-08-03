"""Dash WebUI V1 entry point for ReacNet Scope.

Runs in parallel with the legacy WebUI at ``scripts.webapp.server``.

Usage::

    uv run reacnet-scope-web-dash --host 127.0.0.1 --port 8060
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import dash
import dash_bootstrap_components as dbc
import dash_cytoscape as cyto
from dash import dash_table, dcc, html
from flask import Response, jsonify, request

# Ensure project root is importable when run via ``python -m`` or directly.
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from scripts.webapp_dash import callbacks as cb  # noqa: E402
from scripts.webapp_dash import services as svc  # noqa: E402
from scripts.webapp_dash.navigation import (  # noqa: E402
    DEFAULT_PAGE,
    NAV_GROUPS,
    PAGE_DESCRIPTIONS,
    PAGE_ICONS,
    PAGE_LABELS,
    PAGE_SECTIONS,
)


_PROCESS_STARTED_AT = time.time()


def _background_callback_manager() -> Any:
    """Create a small shared result cache for long-running UI operations."""
    import diskcache

    user_id = getattr(os, "getuid", lambda: "default")()
    task_cache = Path(tempfile.gettempdir()) / (
        f"reacnet-scope-dash-background-{user_id}"
    )
    cache = diskcache.Cache(str(task_cache), size_limit=64 * 1024**2)
    return dash.DiskcacheManager(cache, expire=3600)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _top_nav_group(label: str, page_ids: tuple[str, ...]) -> html.Div:
    return html.Div(
        [
            html.Span(label, className="rs-top-nav-label"),
            *[
                html.Button(
                    [
                        html.Span(
                            PAGE_ICONS[page_id],
                            className="rs-nav-icon",
                            **{"aria-hidden": "true"},
                        ),
                        html.Span(PAGE_LABELS[page_id], className="rs-nav-text"),
                    ],
                    id=f"nav-{page_id}",
                    type="button",
                    title=PAGE_LABELS[page_id],
                    n_clicks=0,
                    className=(
                        "rs-top-nav-item active"
                        if page_id == DEFAULT_PAGE
                        else "rs-top-nav-item"
                    ),
                )
                for page_id in page_ids
            ],
        ],
        className="rs-top-nav-group",
        **{"aria-label": label},
    )


def _topbar() -> dbc.Container:
    return dbc.Container(
        [
            html.Div(
                "管理数据",
                className="rs-topbar-page-context",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(
                                className="rs-dataset-indicator",
                                **{"aria-hidden": "true"},
                            ),
                            html.Div(
                                [
                                    html.Span("当前数据集", className="rs-meta-label"),
                                    html.Div(
                                        [
                                            html.Span(id="topbar-rungroup", children="未选择", className="rs-meta-value"),
                                            html.Span(id="topbar-status", className="rs-badge rs-bad", children="未加载数据"),
                                        ],
                                        className="rs-meta-value-row",
                                    ),
                                ],
                                className="rs-meta-copy",
                            ),
                        ],
                        className="rs-meta-primary",
                    ),
                    html.Div(
                        [
                            html.Span(id="topbar-folder", children="未选择"),
                        ],
                        className="visually-hidden",
                    ),
                ],
                className="rs-meta",
            ),
            html.Div(
                [
                    html.Span(
                        id="topbar-index-status",
                        className="rs-index-global-state",
                    ),
                    dbc.Button(
                        "刷新状态",
                        id="data-prep-refresh-btn",
                        color="secondary",
                        size="sm",
                        outline=True,
                        className="rs-topbar-refresh-btn",
                    ),
                    dbc.Button(
                        "切换数据集",
                        id="data-pick-btn",
                        color="secondary",
                        size="sm",
                        outline=True,
                        className="rs-topbar-dataset-switch",
                    ),
                    dbc.Button(
                        "管理数据",
                        id="open-data-modal",
                        color="secondary",
                        size="sm",
                        outline=True,
                        className="rs-data-button rs-topbar-data-entry",
                    ),
                ],
                className="rs-top-actions ms-auto",
            ),
        ],
        className="rs-topbar",
        fluid=True,
    )


def _sidebar() -> html.Aside:
    return html.Aside(
        [
            html.Div(
                [
                    html.Span(
                        "RS",
                        className="rs-brand-mark",
                        **{"aria-hidden": "true"},
                    ),
                    html.Div(
                        [
                            html.Span("ReacNet Scope", className="rs-brand"),
                            html.Span(
                                "反应分析工作台",
                                className="rs-brand-subtitle",
                            ),
                        ],
                        className="rs-brand-copy",
                    ),
                ],
                className="rs-brand-lockup rs-sidebar-brand",
            ),
            html.Div(
                [
                    html.Nav(
                        [
                            _top_nav_group(label, page_ids)
                            for label, page_ids in NAV_GROUPS
                        ],
                        className="rs-top-nav",
                        **{"aria-label": "分析功能"},
                    ),
                ],
                className="rs-nav-scroll",
            ),
            html.Div(
                [
                    html.Div("数据工作区", className="rs-top-nav-label"),
                    html.Button(
                        [
                            html.Span(
                                PAGE_ICONS["data-management"],
                                className="rs-nav-icon",
                                **{"aria-hidden": "true"},
                            ),
                            html.Span(
                                PAGE_LABELS["data-management"],
                                className="rs-nav-text",
                            ),
                        ],
                        id="nav-data-management",
                        type="button",
                        title=PAGE_LABELS["data-management"],
                        n_clicks=0,
                        className="rs-top-nav-item rs-nav-utility",
                    ),
                    html.Button(
                        [
                            html.Span(
                                PAGE_ICONS["batch-compare"],
                                className="rs-nav-icon",
                                **{"aria-hidden": "true"},
                            ),
                            html.Span(
                                PAGE_LABELS["batch-compare"],
                                className="rs-nav-text",
                            ),
                        ],
                        id="data-open-batch-compare-btn",
                        type="button",
                        title=PAGE_LABELS["batch-compare"],
                        n_clicks=0,
                        className="rs-top-nav-item rs-nav-utility",
                    ),
                    html.Div(
                        [
                            html.Span(className="rs-nav-footer-dot"),
                            html.Span("所有计算均在当前服务器执行"),
                        ],
                        className="rs-nav-footer",
                    ),
                ],
                className="rs-nav-bottom",
            ),
        ],
        className="rs-nav",
    )


def _global_operation_progress() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Span("正在读取或处理数据", className="rs-global-progress-label"),
                    html.Span("请稍候", className="rs-global-progress-hint"),
                ],
                className="rs-global-progress-copy",
            ),
            html.Div(
                html.Span(className="rs-global-progress-value"),
                className="rs-global-progress-track",
                role="progressbar",
                **{
                    "aria-label": "正在读取或处理数据",
                    "aria-valuemin": "0",
                    "aria-valuemax": "100",
                },
            ),
        ],
        id="global-operation-progress",
        className="rs-global-operation-progress",
        role="status",
        **{"aria-live": "polite"},
    )


def _page_header() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("分析工作台"),
                            html.Span("/", className="rs-page-eyebrow-separator"),
                            html.Span(
                                PAGE_SECTIONS[DEFAULT_PAGE],
                                id="page-eyebrow-section",
                            ),
                        ],
                        className="rs-page-eyebrow",
                    ),
                    html.H1(PAGE_LABELS[DEFAULT_PAGE], id="page-title"),
                    html.P(
                        PAGE_DESCRIPTIONS[DEFAULT_PAGE],
                        id="page-description",
                    ),
                ],
                className="rs-page-heading",
            ),
            html.Div("需要导入数据", id="page-data-status", className="rs-page-status is-blocked"),
        ],
        className="rs-page-header",
        id="page-header",
    )


def _empty_chart_figure(title: str, hint: str) -> dict[str, Any]:
    """Return an intentional empty state instead of Plotly's default axes."""
    return {
        "data": [],
        "layout": {
            "autosize": True,
            "paper_bgcolor": "#ffffff",
            "plot_bgcolor": "#ffffff",
            "margin": {"l": 24, "r": 24, "t": 24, "b": 24},
            "xaxis": {"visible": False, "fixedrange": True, "range": [0, 1]},
            "yaxis": {"visible": False, "fixedrange": True, "range": [0, 1]},
            "annotations": [
                {
                    "x": 0.5,
                    "y": 0.53,
                    "xref": "paper",
                    "yref": "paper",
                    "showarrow": False,
                    "align": "center",
                    "text": (
                        f"<b>{title}</b><br>"
                        f"<span style='font-size:12px;color:#718096'>{hint}</span>"
                    ),
                    "font": {"family": "Inter, sans-serif", "size": 15, "color": "#25324b"},
                }
            ],
        },
    }


def _detail_panel() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div([html.H6("选中物种详情"), html.Span("结构与网络统计", className="rs-detail-kicker")]),
                    html.Div(
                        [
                            dbc.Button(
                                "检索生成/消耗通道",
                                id="species-to-channels-btn",
                                color="primary",
                                size="sm",
                                outline=True,
                                disabled=True,
                            ),
                            dbc.Button(
                                "查看时间演化",
                                id="species-to-evolution-btn",
                                color="primary",
                                size="sm",
                                outline=True,
                                disabled=True,
                            ),
                            dbc.Button("作为路径起点", id="species-to-pathway-btn", color="primary", size="sm", outline=True, disabled=True),
                            dbc.Button("经反应通道定位事件", id="species-to-event-btn", color="secondary", size="sm", outline=True, disabled=True),
                        ],
                        className="rs-detail-actions",
                    ),
                ],
                className="rs-detail-header",
            ),
            html.Div(
                id="detail-empty",
                className="rs-empty",
                children="从检索结果中选择物种以查看结构和轨迹",
            ),
            html.Div(id="detail-body", style={"display": "none"}, children=[]),
        ],
        className="rs-detail",
        id="detail-panel",
        style={"display": "none"},
    )


def _grid(
    grid_id: str,
    *,
    row_selectable: str = "single",
    page_size: int | None = None,
) -> dash_table.DataTable:
    pagination = (
        {"page_action": "native", "page_current": 0, "page_size": page_size}
        if page_size is not None
        else {"page_action": "none"}
    )
    return dash_table.DataTable(
        id=grid_id,
        columns=[],
        data=[],
        selected_rows=[],
        row_selectable=row_selectable,
        sort_action="native",
        filter_action="none",
        **pagination,
        css=[],
        style_table={"maxHeight": "560px", "overflowY": "auto", "overflowX": "auto"},
        style_cell={
            "fontSize": 12,
            "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "padding": "6px 8px",
            "textAlign": "left",
            "minWidth": "90px",
            "maxWidth": "360px",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        },
        style_header={
            "backgroundColor": "#f8fafc",
            "fontWeight": 600,
            "borderBottom": "1px solid #d9dee7",
        },
        style_data={"borderBottom": "1px solid #eef1f5"},
        style_data_conditional=[
            {
                "if": {"state": "selected"},
                "backgroundColor": "#eef2ff",
                "borderLeft": "3px solid #3b82f6",
            },
            {"if": {"row_index": "odd"}, "backgroundColor": "#fbfcfe"},
        ],
        tooltip_data=[],
        tooltip_duration=None,
    )


def _species_page() -> html.Div:
    query_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("类型", className="rs-grid-label"),
                                    dcc.RadioItems(
                                        id="species-query-kind",
                                        value="auto",
                                        options=[
                                            {"label": "自动", "value": "auto"},
                                            {"label": "分子式", "value": "formula"},
                                            {"label": "SMILES", "value": "smiles"},
                                            {"label": "质量数", "value": "mass"},
                                        ],
                                        inline=True,
                                        className="rs-segmented",
                                        labelStyle={
                                            "display": "inline-flex",
                                            "alignItems": "center",
                                            "justifyContent": "center",
                                            "padding": "5px 14px",
                                            "fontSize": "13px",
                                            "border": "1px solid #d1d5db",
                                            "cursor": "pointer",
                                        },
                                    ),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Label("查询内容", className="rs-grid-label"),
                                    dcc.Input(
                                        id="species-query",
                                        value="",
                                        placeholder="例如 H2O / [H][O] / 17.00274",
                                        debounce=True,
                                        type="text",
                                        style={"width": "100%"},
                                    ),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Label("质量容差 (Da)", className="rs-grid-label"),
                                    dcc.Input(
                                        id="species-mass-tol",
                                        value="0.5",
                                        type="number",
                                        style={"width": "100%"},
                                    ),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Label("\u00A0", className="rs-grid-label"),
                                    html.Div(
                                        [
                                            dbc.Button("查询", id="species-search-btn", color="primary", size="sm"),
                                            dbc.Button(
                                                "导出全部 CSV",
                                                id="species-csv-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                className="ms-1",
                                            ),
                                        ],
                                        className="d-flex",
                                    ),
                                ],
                            ),
                            dcc.Download(id="species-csv-download"),
                        ],
                        className="rs-query-grid",
                    ),
                ],
                className="p-2",
            )
        ],
        className="rs-card",
        id="species-query-card",
    )

    grid_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Span(className="rs-empty-node rs-empty-node-a"),
                                    html.Span(className="rs-empty-bond"),
                                    html.Span(className="rs-empty-node rs-empty-node-b"),
                                ],
                                className="rs-empty-visual",
                                **{"aria-hidden": "true"},
                            ),
                            html.Div(
                                [
                                    html.H5("尚未导入反应数据", className="rs-empty-title"),
                                    html.P(
                                        "选择 reactionabcd 数据后即可检索。",
                                        className="rs-empty-copy",
                                    ),
                                ],
                                id="species-empty-copy",
                            ),
                            dbc.Button("管理数据", id="species-open-data-modal", color="primary", size="sm"),
                        ],
                        id="species-empty-state",
                        className="rs-empty-state",
                    ),
                    html.Div(
                        [
                            html.Div(id="species-alert", className="rs-result-summary"),
                            dcc.Loading(
                                html.Div(
                                    _grid("species-grid", page_size=20),
                                    className="rs-grid-wrap",
                                ),
                                type="circle",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H6(
                                                        "分子式对应结构",
                                                        id="species-structure-title",
                                                        className="mb-0",
                                                    ),
                                                    html.Div(
                                                        id="species-structure-alert",
                                                        className="rs-result-summary mb-0",
                                                    ),
                                                ]
                                            ),
                                            dbc.Button(
                                                "导出全部结构 CSV",
                                                id="species-structure-csv-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                            ),
                                        ],
                                        className="rs-result-toolbar",
                                    ),
                                    dcc.Loading(
                                        html.Div(
                                            _grid(
                                                "species-structure-grid",
                                                page_size=50,
                                            ),
                                            className="rs-grid-wrap",
                                        ),
                                        type="circle",
                                    ),
                                    dcc.Download(
                                        id="species-structure-csv-download"
                                    ),
                                ],
                                id="species-structure-results",
                                className="mt-3 pt-2 border-top",
                                style={"display": "none"},
                            ),
                        ],
                        id="species-results",
                        style={"display": "none"},
                    ),
                ],
                className="p-2",
            )
        ],
        className="rs-card",
    )

    return html.Div([query_card, grid_card, _detail_panel()], className="rs-page active", id="page-species")


def _reactions_page() -> html.Div:
    query_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        dbc.Label("反应物分子式", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="rxn-reactants", value="", placeholder="例如 C6H4 + H", className="rs-grow"),
                        dbc.Label("产物分子式", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="rxn-products", value="", placeholder="例如 C6H5", className="rs-grow"),
                        dbc.Label("匹配", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="rxn-mode",
                            options=[{"label": "精确", "value": "exact"}, {"label": "包含", "value": "contains"}],
                            value="exact",
                            clearable=False,
                            style={"width": 110},
                        ),
                        dbc.Label("Top", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="rxn-top", value="50", type="number", style={"width": 76}),
                        dbc.Button("查询", id="rxn-search-btn", color="primary", size="sm"),
                        dbc.Button("从所选反应起点找路径", id="rxn-to-pathway-btn", color="primary", size="sm", outline=True),
                        dbc.Button("送入事件证据", id="rxn-to-event-btn", color="secondary", size="sm", outline=True),
                        dbc.Button("导出 CSV", id="rxn-csv-btn", color="secondary", size="sm", outline=True),
                        dcc.Download(id="rxn-csv-download"),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    [
                        dbc.Checkbox(id="rxn-with-share", value=False, className="me-1"),
                        dbc.Label("计算 Top 占比", html_for="rxn-with-share", className="mb-0"),
                        dcc.Dropdown(
                            id="rxn-share-metric",
                            options=[
                                {"label": "tp", "value": "tp"},
                                {"label": "reverse_tp", "value": "reverse_tp"},
                                {"label": "net_tp", "value": "net_tp"},
                            ],
                            value="net_tp",
                            clearable=False,
                            style={"width": 150},
                        ),
                        dbc.Checkbox(id="rxn-share-abs", value=False, className="me-1"),
                        dbc.Label("绝对值", html_for="rxn-share-abs", className="mb-0"),
                        dbc.Checkbox(id="rxn-share-positive", value=False, className="me-1"),
                        dbc.Label("仅正值", html_for="rxn-share-positive", className="mb-0"),
                    ],
                    className="rs-subquery-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
        id="rxn-query-card",
    )
    grid_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Span("Rx", className="rs-inline-empty-icon"),
                        html.Div(
                            [
                                html.Strong("等待反应式查询"),
                                html.Span("输入反应物或产物后，结果与结构证据会显示在这里。"),
                            ]
                        ),
                    ],
                    id="rxn-initial-state",
                    className="rs-workspace-empty",
                ),
                html.Div(
                    [
                        html.Div(id="rxn-alert", className="rs-result-summary"),
                        dcc.Loading(html.Div(_grid("rxn-grid"), className="rs-grid-wrap"), type="circle"),
                        dbc.Checkbox(
                            id="rxn-structure-show-h",
                            value=True,
                            label="显示 H",
                            className="rs-structure-h-toggle",
                        ),
                        html.Div(
                            id="rxn-structure-detail",
                            className="rs-channel-detail rs-channel-detail-empty",
                        ),
                    ],
                    id="rxn-results-content",
                    style={"display": "none"},
                ),
            ],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
        id="rxn-results-card",
    )
    channel_view = html.Section(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H2("查看高频生成 / 消耗通道"),
                            html.P(
                                "选择一条通道，可继续定位对应的 RNG 事件。",
                            ),
                        ],
                        className="rs-channel-heading mb-0",
                    ),
                    dbc.Button(
                        "← 返回物种检索",
                        id="rxn-channel-back-btn",
                        color="secondary",
                        size="sm",
                        outline=True,
                    ),
                ],
                className="rs-channel-view-header",
            ),
            html.Div(id="rxn-channel-alert", className="rs-flow-alert"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("生成通道"),
                                    html.Span("目标物种位于产物侧"),
                                ],
                                className="rs-lane-title",
                            ),
                            _channel_grid("rxn-production-grid"),
                        ],
                        className="rs-channel-lane",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("消耗通道"),
                                    html.Span("目标物种位于反应物侧"),
                                ],
                                className="rs-lane-title",
                            ),
                            _channel_grid("rxn-consumption-grid"),
                        ],
                        className="rs-channel-lane",
                    ),
                ],
                className="rs-channel-lanes",
            ),
            dbc.Checkbox(
                id="rxn-channel-show-h",
                value=True,
                label="显示 H",
                className="rs-structure-h-toggle",
            ),
            html.Div(
                "在上方表格中选择一条通道，查看完整结构反应式。",
                id="rxn-channel-detail",
                className="rs-channel-detail rs-channel-detail-empty",
            ),
            html.Div(
                [
                    html.Div(
                        "选择一条生成或消耗通道。",
                        id="rxn-channel-choice",
                        className="rs-flow-choice",
                    ),
                    dbc.Button(
                        "定位所选通道事件 →",
                        id="rxn-channel-to-event-btn",
                        color="primary",
                        size="sm",
                        disabled=True,
                    ),
                ],
                className="rs-flow-handoff",
            ),
            dcc.Store(id="rxn-channel-selection-store", data=None),
        ],
        id="rxn-channel-view",
        className="rs-species-channel-view",
        style={"display": "none"},
    )
    return html.Div(
        [query_card, grid_card, channel_view],
        className="rs-page",
        id="page-reactions",
    )


def _intermediate_page() -> html.Div:
    query_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        dbc.Label("类别", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="inter-kind",
                            options=[
                                {"label": "intermediate", "value": "intermediate"},
                                {"label": "product", "value": "product"},
                                {"label": "reactant", "value": "reactant"},
                                {"label": "all", "value": "all"},
                            ],
                            value="intermediate",
                            clearable=False,
                            style={"width": 140},
                        ),
                        dbc.Label("Top", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-top", value="120", type="number", style={"width": 80}),
                        dbc.Label("丰度阈值", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-abundance", value="5", type="number", style={"width": 86}),
                        dbc.Label("StartRatioMax", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-start-ratio", value="0.1", type="number", style={"width": 86}),
                        dbc.Label("DecayAlpha", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-decay-alpha", value="0.8", type="number", style={"width": 86}),
                        dbc.Button("筛选", id="inter-search-btn", color="primary", size="sm"),
                        dbc.Button("导出 CSV", id="inter-csv-btn", color="secondary", size="sm", outline=True),
                        dcc.Download(id="inter-csv-download"),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    [
                        dbc.Label("FWHMMin(ps)", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-fwhm", value="0.5", type="number", style={"width": 92}),
                        dbc.Label("Timestep(ps)", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-timestep", value="0.0001", type="number", style={"width": 100}),
                        dbc.Checkbox(id="inter-require-fwhm", value=True, className="me-1"),
                        dbc.Label("RequireFWHM", html_for="inter-require-fwhm", className="mb-0"),
                        dbc.Checkbox(id="inter-with-flux", value=True, className="me-1"),
                        dbc.Label("WithFlux", html_for="inter-with-flux", className="mb-0"),
                        dbc.Label("FluxTop", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="inter-flux-top", value="10", type="number", style={"width": 76}),
                    ],
                    className="rs-subquery-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    grid_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(id="inter-alert"),
                html.Div(id="inter-progress", className="rs-analysis-progress"),
                dcc.Loading(html.Div(_grid("inter-grid"), className="rs-grid-wrap"), type="circle"),
                html.Div(
                    [
                        html.Div(id="inter-selected-summary"),
                        html.Div(
                            [
                                dbc.Button(
                                    "作为路径起点",
                                    id="inter-to-pathway-btn",
                                    color="primary",
                                    size="sm",
                                    outline=True,
                                    disabled=True,
                                ),
                                dbc.Button(
                                    "查看时间演化",
                                    id="inter-to-evolution-btn",
                                    color="secondary",
                                    size="sm",
                                    outline=True,
                                    disabled=True,
                                ),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    id="inter-selection-card",
                    className="rs-selection-actions",
                    style={"display": "none"},
                ),
                dbc.Checkbox(
                    id="inter-structure-show-h",
                    value=True,
                    label="显示 H",
                    className="rs-structure-h-toggle",
                ),
                html.Div(
                    id="inter-structure-detail",
                    className="rs-channel-detail rs-channel-detail-empty",
                ),
            ],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    return html.Div([query_card, grid_card], className="rs-page", id="page-intermediate")


def _evolution_page() -> html.Div:
    query_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    dbc.Label("目标物种/分子式", className="mb-0"),
                                    dcc.Textarea(
                                        id="evolution-targets",
                                        value="",
                                        placeholder="从物种检索自动继承；每行一个目标，支持 label::query",
                                        className="rs-multiline-input",
                                        style={"minHeight": 66, "height": 66},
                                    ),
                                ],
                                className="rs-form-field rs-form-field-grow",
                            ),
                            html.Div(
                                [
                                    dbc.Label("X 轴", className="mb-0"),
                                    dcc.Dropdown(
                                        id="evolution-xaxis",
                                        options=[
                                            {"label": "步数", "value": "step"},
                                            {"label": "ps", "value": "ps"},
                                            {"label": "ns", "value": "ns"},
                                        ],
                                        value="ps",
                                        clearable=False,
                                    ),
                                ],
                                className="rs-form-field rs-form-field-xaxis",
                            ),
                            html.Div(
                                [
                                    dbc.Label("平滑", className="mb-0"),
                                    dcc.Input(
                                        id="evolution-smooth",
                                        value="1",
                                        type="number",
                                    ),
                                ],
                                className="rs-form-field rs-form-field-smooth",
                            ),
                            html.Div(
                                [
                                    dbc.Button("绘制", id="evolution-search-btn", color="primary", size="sm"),
                                    dbc.Button("导出 CSV", id="evolution-csv-btn", color="secondary", size="sm", outline=True),
                                ],
                                className="rs-form-actions",
                            ),
                            dcc.Download(id="evolution-csv-download"),
                        ],
                        className="rs-query-row rs-evolution-primary",
                    ),
                    dbc.Accordion(
                        [
                            dbc.AccordionItem(
                                html.Div(
                                    [
                                        dbc.Label("单个 Species 文件", className="mb-0"),
                                        dcc.Input(id="evolution-species-file", placeholder="留空使用当前数据集", className="rs-grow"),
                                        dbc.Label("多文件列表", className="mb-0"),
                                        dcc.Textarea(
                                            id="evolution-species-files",
                                            placeholder="2500K@seed1::/path/run1.species\n3000K@seed1::/path/run2.reactionabcd",
                                            className="rs-grow",
                                            style={"minHeight": 58},
                                        ),
                                    ],
                                    className="rs-query-row",
                                ),
                                title="数据源",
                            ),
                            dbc.AccordionItem(
                                html.Div(
                                    [
                                        dbc.Label("公式模式", className="mb-0"),
                                        dcc.Dropdown(
                                            id="evolution-formula-mode",
                                            options=[
                                                {"label": "合并同分子式", "value": "sum"},
                                                {"label": "拆分 SMILES", "value": "split"},
                                                {"label": "同时显示", "value": "both"},
                                            ],
                                            value="sum",
                                            clearable=False,
                                            style={"width": 150},
                                        ),
                                        dbc.Label("每式 SMILES 上限", className="mb-0"),
                                        dcc.Input(id="evolution-max-smiles", value="0", type="number", min=0, style={"width": 88}),
                                        dbc.Label("归一化", className="mb-0"),
                                        dcc.Dropdown(
                                            id="evolution-normalize",
                                            options=[{"label": "无", "value": "none"}, {"label": "初始值", "value": "initial"}, {"label": "最大值", "value": "max"}],
                                            value="none",
                                            clearable=False,
                                            style={"width": 110},
                                        ),
                                        dbc.Label("时间对齐", className="mb-0"),
                                        dcc.Dropdown(
                                            id="evolution-time-align",
                                            options=[{"label": "原始时间", "value": "raw"}, {"label": "截断交集", "value": "truncate"}, {"label": "相对起点", "value": "relative"}],
                                            value="raw",
                                            clearable=False,
                                            style={"width": 130},
                                        ),
                                        dbc.Label("Timestep(ps)", className="mb-0"),
                                        dcc.Input(id="evolution-timestep", value="0.0001", type="number", min=0, style={"width": 110}),
                                        dbc.Label("下采样", className="mb-0"),
                                        dcc.Input(id="evolution-downsample", value="1800", type="number", min=0, style={"width": 88}),
                                        dbc.Label("最大曲线", className="mb-0"),
                                        dcc.Input(id="evolution-max-curves", value="30", type="number", min=1, style={"width": 80}),
                                        dbc.Label("曲线筛选", className="mb-0"),
                                        dcc.Input(id="evolution-curve-filter", placeholder="按名称筛选", style={"width": 150}),
                                    ],
                                    className="rs-query-row",
                                ),
                                title="曲线与对比设置",
                            ),
                        ],
                        start_collapsed=True,
                        className="rs-advanced",
                    ),
                ],
                className="p-2",
            )
        ],
        className="rs-card",
    )

    chart_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(id="evolution-alert"),
                    html.Div(id="evolution-progress", className="rs-analysis-progress"),
                    dcc.Loading(
                        html.Div(
                            dcc.Graph(
                                id="evolution-graph",
                                figure=_empty_chart_figure(
                                    "尚无演化曲线",
                                    "输入目标物种或分子式，然后点击“绘制”。",
                                ),
                                config={"displaylogo": False, "responsive": True},
                                className="rs-chart",
                                style={"height": "100%"},
                            ),
                            className="rs-grid-wrap",
                        ),
                        type="circle",
                    ),
                    dbc.Checkbox(
                        id="evolution-structure-show-h",
                        value=True,
                        label="显示 H",
                        className="rs-structure-h-toggle",
                    ),
                    html.Div(
                        id="evolution-structure-detail",
                        className="rs-channel-detail rs-channel-detail-empty",
                    ),
                ],
                className="p-2 rs-flex-fill",
            )
        ],
        className="rs-card rs-flex-fill",
    )

    return html.Div([query_card, chart_card], className="rs-page", id="page-evolution")


def _carbon_page() -> html.Div:
    settings_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("数据集", html_for="carbon-dataset-name"),
                                dcc.Input(
                                    id="carbon-dataset-name",
                                    value="未选择",
                                    disabled=True,
                                    className="rs-carbon-input",
                                ),
                            ],
                            className="rs-carbon-field rs-carbon-dataset",
                        ),
                        html.Div(
                            [
                                dbc.Label("Timestep (ps)", html_for="carbon-timestep"),
                                dcc.Input(
                                    id="carbon-timestep",
                                    value=0.0001,
                                    type="number",
                                    min=1e-12,
                                    step="any",
                                ),
                            ],
                            className="rs-carbon-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("最大碳数", html_for="carbon-max-c"),
                                dcc.Input(id="carbon-max-c", value=6, type="number", min=1, max=30),
                            ],
                            className="rs-carbon-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("参考物种 SMILES（可选）", html_for="carbon-reference-smiles"),
                                dcc.Input(
                                    id="carbon-reference-smiles",
                                    value="",
                                    placeholder="例如 [C][C]；留空则只显示 C1…Cn",
                                    className="rs-carbon-input",
                                ),
                            ],
                            className="rs-carbon-field",
                        ),
                        dbc.Button("绘制", id="carbon-search-btn", color="primary", className="rs-carbon-draw"),
                    ],
                    className="rs-carbon-controls",
                ),
                html.Hr(),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span("氯状态", className="rs-carbon-filter-label"),
                                dbc.RadioItems(
                                    id="carbon-chlorine-state",
                                    options=[
                                        {"label": "全部", "value": "all"},
                                        {"label": "含氯", "value": "chlorinated"},
                                        {"label": "不含氯", "value": "unchlorinated"},
                                    ],
                                    value="all",
                                    inline=True,
                                    className="rs-carbon-radio",
                                ),
                            ],
                            className="rs-carbon-filter",
                        ),
                        html.Div(
                            [
                                html.Span("氧状态", className="rs-carbon-filter-label"),
                                dbc.RadioItems(
                                    id="carbon-oxygen-state",
                                    options=[
                                        {"label": "全部", "value": "all"},
                                        {"label": "含氧", "value": "oxygenated"},
                                        {"label": "不含氧", "value": "unoxygenated"},
                                    ],
                                    value="all",
                                    inline=True,
                                    className="rs-carbon-radio",
                                ),
                            ],
                            className="rs-carbon-filter",
                        ),
                    ],
                    className="rs-carbon-filter-row",
                ),
                dbc.Accordion(
                    [
                        dbc.AccordionItem(
                            [
                                html.Div(
                                    [
                                        html.Div([dbc.Label("Tidy CSV / Excel"), dcc.Input(id="carbon-advanced-data", placeholder="可选 tidy 数据文件")], className="rs-carbon-field"),
                                        html.Div([dbc.Label("单个 Species"), dcc.Input(id="carbon-advanced-species-file", placeholder="留空使用当前数据集")], className="rs-carbon-field"),
                                        html.Div([dbc.Label("多文件列表"), dcc.Textarea(id="carbon-advanced-species-files", placeholder="system::/path/run.species", style={"minHeight": 60})], className="rs-carbon-field rs-carbon-advanced-wide"),
                                        html.Div([dbc.Label("X 轴"), dcc.Dropdown(id="carbon-advanced-xaxis", options=[{"label": "step", "value": "step"}, {"label": "ps", "value": "ps"}, {"label": "ns", "value": "ns"}], value="ps", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("模式"), dcc.Dropdown(id="carbon-advanced-mode", options=[{"label": "精确碳数", "value": "exact"}, {"label": "分箱", "value": "binned"}, {"label": "Top K", "value": "topk"}], value="exact", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("时间对齐"), dcc.Dropdown(id="carbon-advanced-time-align", options=[{"label": "原始", "value": "raw"}, {"label": "截断交集", "value": "truncate"}, {"label": "相对起点", "value": "relative"}], value="raw", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("Top K"), dcc.Input(id="carbon-advanced-top-k", value=12, type="number", min=1)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("精确曲线上限"), dcc.Input(id="carbon-advanced-max-exact", value=24, type="number", min=1)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("分箱"), dcc.Input(id="carbon-advanced-bins", placeholder="1-4;5-15;16-30;31+")], className="rs-carbon-field"),
                                        html.Div([dbc.Label("显示区间"), dcc.Input(id="carbon-advanced-display-ranges", placeholder="C1;C2;C24;C30+")], className="rs-carbon-field"),
                                        html.Div([dbc.Label("合并区间"), dcc.Input(id="carbon-advanced-merge-ranges", placeholder="Small:1-4;Growth:30+")], className="rs-carbon-field rs-carbon-advanced-wide"),
                                        html.Div([dbc.Label("母体碳数"), dcc.Input(id="carbon-advanced-parent", type="number", min=0)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("小分子高亮"), dcc.Input(id="carbon-advanced-small", value="1-4")], className="rs-carbon-field"),
                                        html.Div([dbc.Label("大分子阈值"), dcc.Input(id="carbon-advanced-large", value=30, type="number", min=1)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("平滑"), dcc.Dropdown(id="carbon-advanced-smoothing", options=[{"label": "无", "value": "none"}, {"label": "Rolling", "value": "rolling"}, {"label": "Savitzky–Golay", "value": "savgol"}], value="none", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("平滑窗口"), dcc.Input(id="carbon-advanced-window", value=5, type="number", min=1)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("多项式阶数"), dcc.Input(id="carbon-advanced-polyorder", value=2, type="number", min=1)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("布局"), dcc.Dropdown(id="carbon-advanced-layout", options=[{"label": "单图", "value": "single"}, {"label": "子图", "value": "subplots"}], value="single", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("子图区间"), dcc.Textarea(id="carbon-advanced-regions", placeholder="panel1:1-4; panel2:5-15", style={"minHeight": 60})], className="rs-carbon-field rs-carbon-advanced-wide"),
                                        html.Div([dbc.Label("体系模式"), dcc.Dropdown(id="carbon-advanced-system-mode", options=[{"label": "自动", "value": ""}, {"label": "Overlay", "value": "overlay"}, {"label": "Facet", "value": "facet"}], value="", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("主题"), dcc.Dropdown(id="carbon-advanced-theme", options=[{"label": "浅色", "value": "light"}, {"label": "深色", "value": "dark"}], value="light", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("图例"), dcc.Dropdown(id="carbon-advanced-legend", options=[{"label": "紧凑", "value": "compact"}, {"label": "详细", "value": "detailed"}], value="compact", clearable=False)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("图宽"), dcc.Input(id="carbon-advanced-width", value=11.5, type="number", min=4)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("图高"), dcc.Input(id="carbon-advanced-height", value=8.0, type="number", min=4)], className="rs-carbon-field"),
                                        html.Div([dbc.Label("公式列表上限"), dcc.Input(id="carbon-advanced-max-formulas", value=30, type="number", min=5)], className="rs-carbon-field"),
                                    ],
                                    className="rs-carbon-advanced-grid",
                                ),
                                html.Div(
                                    [
                                        dbc.Button("绘制高级 Carbon Plot", id="carbon-advanced-search-btn", color="primary"),
                                        dbc.Button("导出 CSV", id="carbon-advanced-csv-btn", color="secondary", outline=True),
                                        dbc.Button("导出 SVG", id="carbon-advanced-svg-btn", color="secondary", outline=True),
                                        dcc.Download(id="carbon-advanced-csv-download"),
                                        dcc.Download(id="carbon-advanced-svg-download"),
                                    ],
                                    className="d-flex gap-2 flex-wrap mt-3",
                                ),
                                html.Div(id="carbon-advanced-progress", className="rs-analysis-progress"),
                            ],
                            title="高级 Carbon Plot（多体系 / 分箱 / 平滑 / 子图）",
                        )
                    ],
                    start_collapsed=True,
                    className="rs-advanced mt-3",
                ),
                html.Div(
                    [
                        html.Div(id="carbon-index-status", className="rs-index-status"),
                        dbc.Progress(
                            id="carbon-index-progress",
                            value=0,
                            max=100,
                            striped=True,
                            animated=True,
                            className="rs-index-progress",
                        ),
                    ],
                    className="rs-index-block",
                ),
                html.Div(id="carbon-progress", className="rs-analysis-progress"),
                dcc.Interval(
                    id="carbon-index-refresh",
                    interval=2000,
                    n_intervals=0,
                    disabled=True,
                ),
            ],
            className="p-3",
        ),
        className="rs-card",
    )
    result_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(id="carbon-alert"),
                html.Div(id="carbon-highlights", className="rs-stat-row"),
                html.Div(id="carbon-advanced-alert"),
                html.Div(id="carbon-advanced-viewer", className="rs-carbon-advanced-viewer"),
                dcc.Loading(
                    dcc.Graph(
                        id="carbon-composition-trend",
                        figure=_empty_chart_figure(
                            "尚无组成趋势",
                            "选择数据来源与元素范围，然后开始绘制。",
                        ),
                        config={"displaylogo": False, "responsive": True},
                        className="rs-carbon-chart",
                    ),
                    type="circle",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div(
                                    "点击主图中的参考物种或碳数曲线，查看该时间点的代表物种。",
                                    id="carbon-composition-table-title",
                                    className="rs-composition-detail-title",
                                ),
                                html.Div(id="carbon-drilldown-progress", className="rs-analysis-progress"),
                            ],
                            className="rs-carbon-table-heading",
                        ),
                        _grid("carbon-composition-table", row_selectable="single"),
                        dbc.Checkbox(
                            id="carbon-structure-show-h",
                            value=True,
                            label="显示 H",
                            className="rs-structure-h-toggle",
                        ),
                        html.Div(
                            id="carbon-structure-detail",
                            className="rs-channel-detail rs-channel-detail-empty",
                        ),
                    ],
                    className="rs-composition-detail",
                ),
            ],
            className="p-3 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    return html.Div(
        [settings_card, result_card],
        className="rs-page rs-carbon-minimal",
        id="page-carbon",
    )


def _events_page() -> html.Div:
    query_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("Step 1", className="rs-step-kicker"),
                                html.H6(
                                    "定位可核查的事件",
                                    className="rs-card-title mb-0",
                                ),
                            ]
                        ),
                        dbc.Button(
                            "返回",
                            id="event-back-btn",
                            color="secondary",
                            size="sm",
                            outline=True,
                            style={"display": "none"},
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    [
                        dbc.Label("反应式", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-reaction-text", value="", placeholder="A + B -> C + D", className="rs-grow"),
                        dbc.Label("轨迹前 / 后帧", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-rxn-before", value="3", type="number", min=0, style={"width": 72}),
                        dcc.Input(id="event-rxn-after", value="3", type="number", min=0, style={"width": 72}),
                        dbc.Label("结果上限", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-rxn-max", value="100", type="number", min=1, style={"width": 82}),
                        dbc.Button("查询 RNG 事件", id="event-rxn-btn", color="primary", size="sm"),
                    ],
                    className="rs-query-row mt-2",
                ),
                html.P(
                    "直接查询已准备的 .reactionevent.csv 索引；.molecules.csv 可选，仅补充物理 timestep、参与原子与键。",
                    className="rs-step-note",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    grid_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("Step 1 结果", className="rs-step-kicker"),
                                html.H6("选择一个事件进入轨迹核查", className="rs-card-title mb-0"),
                            ],
                            className="rs-step-heading",
                        ),
                        html.Div(
                            [
                                dbc.Button("导出 CSV", id="event-csv-btn", color="secondary", size="sm", outline=True),
                                dcc.Download(id="event-csv-download"),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    "请输入完整反应式；从物种开始时，请先选择一条生成或消耗通道。",
                    id="event-alert",
                ),
                dcc.Loading(
                    html.Div(
                        _grid("event-grid", page_size=25),
                        className="rs-grid-wrap",
                    ),
                    type="circle",
                ),
            ],
            className="p-2",
        ),
        className="rs-card rs-event-results-card",
        id="event-results-card",
    )
    selection_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [html.Div("Step 2", className="rs-step-kicker"), html.H6("在独立轨迹页核查", className="rs-card-title mb-0")],
                            className="rs-step-heading",
                        ),
                        dbc.Button(
                            "打开轨迹查看",
                            id="event-extract-btn",
                            color="success",
                            size="sm",
                            disabled=True,
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(id="event-selected-summary", className="rs-event-selected-summary"),
                dcc.Input(id="event-extract-id", value="", type="text", readOnly=True, style={"display": "none"}),
            ],
            className="p-2",
        ),
        className="rs-card",
        id="event-selection-card",
        style={"display": "none"},
    )
    return html.Div(
        [query_card, grid_card, selection_card],
        className="rs-page",
        id="page-events",
    )


def _trajectory_page() -> html.Div:
    source_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("轨迹工作区", className="rs-step-kicker"),
                                html.H6(
                                    "局部反应轨迹",
                                    className="rs-card-title mb-0",
                                ),
                            ],
                            className="rs-step-heading",
                        ),
                        dbc.Button(
                            "← 选择反应事件",
                            id="trajectory-back-events-btn",
                            color="secondary",
                            size="sm",
                            outline=True,
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    "请从“反应事件”页选择一条 RNG 事件；已有查看结果会保留在本页。",
                    id="trajectory-alert",
                    className="rs-step-note",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    viewer_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [html.Div("当前事件", className="rs-step-kicker"), html.H6("局部轨迹与关键帧", className="rs-card-title mb-0")],
                            className="rs-step-heading",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Details(
                    [
                        html.Summary(
                            [
                                html.Span(
                                    [
                                        html.Span(
                                            "数据、映射与导出设置",
                                            className="rs-trajectory-tools-title",
                                        ),
                                        html.Span(
                                            "事件详情 · Atom IDs · OVITO · Type → Element · 文件下载",
                                            className="rs-trajectory-tools-note",
                                        ),
                                    ],
                                    className="rs-trajectory-tools-copy",
                                ),
                            ],
                            className="rs-trajectory-tools-summary",
                        ),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Div(
                                                    "数据来源",
                                                    className="rs-step-kicker",
                                                ),
                                                html.Div(
                                                    id="event-viewer-paths",
                                                    className="rs-viewer-paths",
                                                ),
                                            ],
                                            className="rs-trajectory-source",
                                        ),
                                        dbc.Button(
                                            "应用设置并重新提取",
                                            id="trajectory-refresh-btn",
                                            color="success",
                                            size="sm",
                                        ),
                                    ],
                                    className="rs-trajectory-tools-toolbar",
                                ),
                                html.Div(
                                    id="event-viewer-summary",
                                    className="rs-event-selected-summary",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Span("Atom IDs", className="rs-selection-label"),
                                                html.Code(id="event-atom-ids-text"),
                                                dcc.Clipboard(
                                                    id="event-atom-ids-copy",
                                                    target_id="event-atom-ids-text",
                                                    title="复制 Atom IDs",
                                                ),
                                            ],
                                            className="rs-event-tool-line",
                                        ),
                                        html.Div(
                                            [
                                                html.Span("OVITO", className="rs-selection-label"),
                                                html.Code(id="event-ovito-expression-text"),
                                                dcc.Clipboard(
                                                    id="event-ovito-expression-copy",
                                                    target_id="event-ovito-expression-text",
                                                    title="复制 OVITO Expression Selection",
                                                ),
                                            ],
                                            className="rs-event-tool-line",
                                        ),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        html.Div(
                                                            [
                                                                html.Div(
                                                                    [
                                                                        html.Span(
                                                                            "Type → Element",
                                                                            className="rs-selection-label",
                                                                        ),
                                                                        html.Span(
                                                                            "按轨迹中检测到的 Type 逐项设置",
                                                                            className="rs-type-map-caption",
                                                                        ),
                                                                    ],
                                                                    className="rs-type-map-heading",
                                                                ),
                                                                html.Div(
                                                                    id="event-type-map-status",
                                                                    className="rs-type-map-status",
                                                                    **{"role": "status"},
                                                                ),
                                                            ],
                                                            className="rs-type-map-toolbar",
                                                        ),
                                                        html.Div(
                                                            id="event-type-map-editor",
                                                            children=html.Div(
                                                                "打开一条事件轨迹后自动检测 Type。",
                                                                className="rs-type-map-empty",
                                                            ),
                                                        ),
                                                    ],
                                                    className="rs-type-map-field",
                                                ),
                                                html.Div(
                                                    [
                                                        dbc.Label(
                                                            "环境半径 (Å)",
                                                            html_for="event-environment-radius",
                                                            className="rs-selection-label mb-0",
                                                        ),
                                                        dbc.Input(
                                                            id="event-environment-radius",
                                                            type="number",
                                                            value=4.0,
                                                            min=0,
                                                            max=20,
                                                            step=0.5,
                                                            size="sm",
                                                        ),
                                                        html.Span(
                                                            "用于选取反应原子周围的局部环境",
                                                            className="rs-type-map-caption",
                                                        ),
                                                    ],
                                                    className="rs-environment-radius-field",
                                                ),
                                            ],
                                            className="rs-event-settings-panel",
                                        ),
                                        html.Div(
                                            [
                                                html.Div(
                                                    "选择后点击“应用设置并重新提取”保存到当前数据集；未映射的 Type 保持 T1、T2 等标记。",
                                                    className="rs-event-tool-help",
                                                ),
                                                dbc.Button(
                                                    "清空已保存映射",
                                                    id="event-type-map-clear-btn",
                                                    color="danger",
                                                    size="sm",
                                                    outline=True,
                                                    disabled=True,
                                                ),
                                            ],
                                            className="rs-type-map-footer",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Button(
                                                    "下载事件包 ZIP",
                                                    id="event-package-btn",
                                                    color="primary",
                                                    size="sm",
                                                ),
                                                dbc.Button("下载帧 CSV", id="event-frames-csv-btn", color="secondary", size="sm", outline=True),
                                                dbc.Button("下载子轨迹", id="event-trajectory-btn", color="secondary", size="sm", outline=True),
                                                dbc.Button("下载 OVITO 脚本", id="event-ovito-btn", color="secondary", size="sm", outline=True),
                                                dbc.Button("下载 VMD 脚本", id="event-vmd-btn", color="secondary", size="sm", outline=True),
                                                dcc.Download(id="event-package-download"),
                                                dcc.Download(id="event-frames-csv-download"),
                                                dcc.Download(id="event-trajectory-download"),
                                                dcc.Download(id="event-ovito-download"),
                                                dcc.Download(id="event-vmd-download"),
                                            ],
                                            className="d-flex gap-2 flex-wrap",
                                        ),
                                    ],
                                    className="rs-event-research-tools",
                                ),
                            ],
                            className="rs-trajectory-tools-body",
                        ),
                    ],
                    className="rs-trajectory-tools",
                ),
                html.Div(
                    [
                        dbc.Label("显示范围", className="mb-0", style={"fontSize": 12}),
                        dcc.RadioItems(
                            id="event-view-scope",
                            options=[
                                {"label": "完整上下文", "value": "context"},
                                {"label": "参与原子", "value": "participants"},
                                {"label": "仅反应核", "value": "core"},
                            ],
                            value="participants",
                            inline=True,
                            className="rs-compact-radio",
                        ),
                        dcc.Checklist(
                            id="event-core-label-toggle",
                            options=[
                                {
                                    "label": "固定显示反应核编号",
                                    "value": "core_labels",
                                }
                            ],
                            value=[],
                            inline=True,
                            className="rs-compact-radio rs-viewer-label-toggle",
                        ),
                        html.Span(id="event-frame-label", className="rs-frame-label"),
                    ],
                    className="rs-query-row rs-viewer-controls",
                ),
                dcc.Slider(id="event-frame-slider", min=0, max=0, value=0, step=1, marks={}, className="mb-3"),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div(
                                    id="event-trajectory-3dmol",
                                    className="rs-event-3dmol",
                                    **{
                                        "role": "img",
                                        "aria-label": "3D 分子事件轨迹查看器",
                                    },
                                ),
                                html.Div(
                                    [
                                        html.Span(className="rs-bond-key rs-bond-formed"),
                                        html.Span("形成键"),
                                        html.Span(className="rs-bond-key rs-bond-broken"),
                                        html.Span("断裂键"),
                                        html.Span(className="rs-atom-key rs-atom-core"),
                                        html.Span("反应核光环"),
                                    ],
                                    className="rs-event-3dmol-legend",
                                ),
                            ],
                            className="rs-event-3dmol-shell",
                        ),
                        html.Aside(
                            [
                                html.Div(
                                    [
                                        html.Div("原子详情", className="rs-atom-inspector-title"),
                                        html.Div(
                                            "悬停预览 · 点击固定",
                                            className="rs-atom-inspector-hint",
                                        ),
                                    ],
                                    className="rs-atom-inspector-header",
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            "反应核原子",
                                            className="rs-atom-inspector-section-title",
                                        ),
                                        html.Div(
                                            id="event-core-atom-list",
                                            className="rs-core-atom-list",
                                        ),
                                    ],
                                    className="rs-atom-inspector-core",
                                ),
                                html.Div(
                                    "将鼠标移到原子上查看信息，点击后可固定详情。",
                                    id="event-atom-inspector-body",
                                    className="rs-atom-inspector-body rs-atom-inspector-empty",
                                ),
                            ],
                            id="event-atom-inspector",
                            className="rs-atom-inspector",
                            **{"aria-live": "polite"},
                        ),
                    ],
                    className="rs-event-viewer-workspace",
                ),
                html.Div(id="event-3dmol-status", className="rs-event-viewer-status"),
                html.Details(
                    [
                        html.Summary("兼容 Plotly 视图"),
                        dcc.Loading(
                            dcc.Graph(
                                id="event-trajectory-3d",
                                className="rs-event-3d",
                            ),
                            type="circle",
                        ),
                    ],
                    className="rs-event-fallback",
                ),
                html.Div([html.Div("关键帧故事板", className="rs-storyboard-title"), html.Div(id="event-storyboard", className="rs-storyboard")]),
            ],
            className="p-2 rs-trajectory-card-body",
        ),
        className="rs-card rs-trajectory-card",
        id="event-viewer-card",
        style={"display": "none"},
    )
    return html.Div(
        [source_card, viewer_card],
        className="rs-page",
        id="page-trajectory",
    )


def _event_path_analysis_panel() -> html.Div:
    stepper = html.Div(
        [
            html.Div(
                [html.Span("1"), html.Div([html.Strong("确认数据"), html.Small("当前数据集与重复")])],
                id="event-path-progress-1",
                className="rs-event-path-step is-active",
            ),
            html.Div(
                [html.Span("2"), html.Div([html.Strong("定义路径"), html.Small("节点数与起点")])],
                id="event-path-progress-2",
                className="rs-event-path-step",
            ),
            html.Div(
                [html.Span("3"), html.Div([html.Strong("确认运行"), html.Small("检查参数")])],
                id="event-path-progress-3",
                className="rs-event-path-step",
            ),
            html.Div(
                [html.Span("4"), html.Div([html.Strong("查看结果"), html.Small("筛选与审计")])],
                id="event-path-progress-4",
                className="rs-event-path-step",
            ),
        ],
        className="rs-event-path-stepper",
    )

    step_one = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span("步骤 1", className="rs-step-kicker"),
                                html.H5("确认用于轨迹证据验证的数据", className="mb-1"),
                                html.P(
                                    "当前数据集会自动加入，不需要填写文件路径。"
                                    "只有计算跨重复复现率时才需要添加其他重复。",
                                    className="rs-step-note mb-0",
                                ),
                            ]
                        ),
                        html.Span(
                            "正在检查当前数据…",
                            id="event-path-index-status",
                            className="rs-page-status is-independent",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    id="event-path-current-source-summary",
                    className="rs-event-path-current-source",
                ),
                dcc.Input(
                    id="event-path-current-replicate",
                    value="current",
                    readOnly=True,
                    style={"display": "none"},
                ),
                dbc.RadioItems(
                    id="event-path-source-mode",
                    options=[
                        {
                            "label": "只分析当前数据集（先用这个）",
                            "value": "current",
                        },
                        {
                            "label": "加入其他重复，计算跨重复复现率",
                            "value": "multiple",
                        },
                    ],
                    value="current",
                    className="rs-event-path-source-mode",
                ),
                html.Div(
                    [
                        dbc.Label("每行填写一个：重复标签=RNG 公共前缀"),
                        dcc.Textarea(
                            id="event-path-additional-sources",
                            value="",
                            placeholder=(
                                "rep2=/data/case/rep2/run.lammpstrj\n"
                                "rep3=/data/case/rep3/run.lammpstrj"
                            ),
                            className="rs-event-path-sources",
                        ),
                        html.P(
                            "公共前缀后应存在 .reactionevent.csv 和 .molecules.csv；"
                            "系统会在进入下一步前检查。",
                            className="rs-step-note mt-1 mb-0",
                        ),
                    ],
                    id="event-path-additional-source-panel",
                    style={"display": "none"},
                    className="rs-event-path-additional-panel",
                ),
                html.Div(
                    [
                        dbc.Button(
                            "确认数据，下一步",
                            id="event-path-step1-next",
                            color="primary",
                        )
                    ],
                    className="rs-event-path-wizard-actions",
                ),
            ],
            className="p-3",
        ),
        id="event-path-step-1",
        className="rs-card rs-event-path-wizard-card",
    )

    step_two = dbc.Card(
        dbc.CardBody(
            [
                html.Span("步骤 2", className="rs-step-kicker"),
                html.H5("定义要验证的实际事件链", className="mb-1"),
                html.P(
                    "默认寻找 event₁ → event₂ → event₃。第一次使用只需保留默认值。",
                    className="rs-step-note",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("路径包含几个事件节点？"),
                                dcc.Input(
                                    id="event-path-length",
                                    value=3,
                                    type="number",
                                    min=2,
                                    max=8,
                                ),
                            ],
                            className="rs-pathway-field",
                        ),
                        html.Div(
                            id="event-path-length-preview",
                            children="event₁ → event₂ → event₃",
                            className="rs-event-path-preview",
                        ),
                        html.Div(
                            [
                                dbc.Label("是否限定第一个事件消耗的物种？"),
                                dcc.Input(
                                    id="event-path-start-smiles",
                                    value="",
                                    placeholder="留空表示不限制；也可输入精确 SMILES",
                                    className="rs-pathway-start",
                                ),
                            ],
                            className="rs-pathway-field rs-event-path-start-field",
                        ),
                    ],
                    className="rs-event-path-definition",
                ),
                html.Details(
                    [
                        html.Summary("高级限制（第一次使用无需修改）"),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        dbc.Label("相邻事件最大区间差"),
                                        dcc.Input(
                                            id="event-path-max-interval-gap",
                                            value=None,
                                            type="number",
                                            min=0,
                                            placeholder="不限",
                                        ),
                                    ],
                                    className="rs-pathway-field",
                                ),
                                html.Div(
                                    [
                                        dbc.Label("最大空闲 timestep"),
                                        dcc.Input(
                                            id="event-path-max-timestep-gap",
                                            value=None,
                                            type="number",
                                            min=0,
                                            placeholder="不限",
                                        ),
                                    ],
                                    className="rs-pathway-field",
                                ),
                                html.Div(
                                    [
                                        dbc.Label("保留多少条具体路径供下钻"),
                                        dcc.Input(
                                            id="event-path-max-details",
                                            value=1000,
                                            type="number",
                                            min=0,
                                            max=10000,
                                        ),
                                    ],
                                    className="rs-pathway-field",
                                ),
                            ],
                            className="rs-event-path-advanced-grid",
                        ),
                    ],
                    className="rs-event-path-advanced",
                ),
                html.Div(
                    [
                        dbc.Button(
                            "返回数据选择",
                            id="event-path-step2-back",
                            color="secondary",
                            outline=True,
                        ),
                        dbc.Button(
                            "下一步：确认参数",
                            id="event-path-step2-next",
                            color="primary",
                        ),
                    ],
                    className="rs-event-path-wizard-actions",
                ),
            ],
            className="p-3",
        ),
        id="event-path-step-2",
        className="rs-card rs-event-path-wizard-card",
        style={"display": "none"},
    )

    step_three = dbc.Card(
        dbc.CardBody(
            [
                html.Span("步骤 3", className="rs-step-kicker"),
                html.H5("确认后开始分析", className="mb-1"),
                html.P(
                    "下面是即将执行的分析。确认无误后点击开始；运行完成会自动进入结果页。",
                    className="rs-step-note",
                ),
                html.Div(
                    id="event-path-review-summary",
                    className="rs-event-path-review",
                ),
                html.Div(
                    id="event-path-alert",
                    className="rs-result-summary mt-2",
                ),
                html.Div(
                    [
                        dbc.Button(
                            "返回修改路径",
                            id="event-path-step3-back",
                            color="secondary",
                            outline=True,
                        ),
                        dbc.Button(
                            "查找实际发生路径",
                            id="event-path-run-btn",
                            color="primary",
                        ),
                    ],
                    className="rs-event-path-wizard-actions",
                ),
            ],
            className="p-3",
        ),
        id="event-path-step-3",
        className="rs-card rs-event-path-wizard-card",
        style={"display": "none"},
    )

    controls = html.Div(
        [
            stepper,
            html.Div(
                id="event-path-wizard-feedback",
                className="rs-result-summary",
            ),
            step_one,
            step_two,
            step_three,
        ],
        className="rs-event-path-wizard",
    )

    signatures = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span("步骤 4", className="rs-step-kicker"),
                                html.H5("轨迹中实际发生的路径", className="mb-0"),
                            ]
                        ),
                        html.Div(
                            [
                                dbc.Button(
                                    "修改参数",
                                    id="event-path-step4-edit",
                                    color="secondary",
                                    size="sm",
                                    outline=True,
                                ),
                                dbc.Button(
                                    "下载 JSON",
                                    id="event-path-json-btn",
                                    color="secondary",
                                    size="sm",
                                    outline=True,
                                ),
                                dbc.Button(
                                    "下载 CSV",
                                    id="event-path-csv-btn",
                                    color="secondary",
                                    size="sm",
                                    outline=True,
                                ),
                                dcc.Download(id="event-path-json-download"),
                                dcc.Download(id="event-path-csv-download"),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(id="event-path-summary", className="rs-event-path-metrics"),
                html.Div(
                    id="event-path-summary-explanation",
                    className="rs-pathway-reading-guide",
                ),
                html.Div(
                    [
                        dbc.Checklist(
                            id="event-path-filter-flags",
                            options=[
                                {"label": "隐藏纯 H/H₂ 循环", "value": "hide_pure_h"},
                                {"label": "隐藏首尾相同的往返路径", "value": "hide_return"},
                            ],
                            value=["hide_pure_h"],
                            inline=True,
                            switch=True,
                        ),
                        html.Div(
                            [
                                dbc.Label("最小复现率"),
                                dcc.Input(
                                    id="event-path-min-reproduction",
                                    value=0,
                                    type="number",
                                    min=0,
                                    max=1,
                                    step=0.05,
                                ),
                            ],
                            className="rs-event-path-filter-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("最小原子谱系支持"),
                                dcc.Input(
                                    id="event-path-min-lineages",
                                    value=1,
                                    type="number",
                                    min=0,
                                ),
                            ],
                            className="rs-event-path-filter-field",
                        ),
                        html.Span(
                            "尚未运行分析。",
                            id="event-path-filter-summary",
                            className="rs-step-note",
                        ),
                    ],
                    className="rs-event-path-filters",
                ),
                dcc.Loading(
                    html.Div(
                        _grid("event-path-signature-grid", page_size=25),
                        className="rs-grid-wrap",
                    ),
                    type="circle",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )

    comparison = dbc.Card(
        dbc.CardBody(
            [
                html.H6("网络拼出的路线，有多少真的发生？", className="rs-card-title"),
                html.P(
                    "这里比较的是相同反应序列的路径签名，不是反应转化率："
                    "“有整链实证”要求同一分子实例中的原子按时间连续通过所有事件；"
                    "“无整链实证”表示各步虽然能在聚合网络中拼接，却没有这样的完整事件链。",
                    className="rs-step-note",
                ),
                dcc.Graph(
                    id="event-path-comparison-chart",
                    figure={"data": [], "layout": {"height": 300}},
                    config={"displayModeBar": False},
                    className="rs-event-path-comparison-chart",
                ),
                html.Div(
                    _grid("event-path-comparison-grid", page_size=12),
                    className="rs-grid-wrap",
                ),
                html.Div(
                    [
                        dbc.Label("查看差异路径样本"),
                        dcc.Dropdown(
                            id="event-path-comparison-class",
                            options=[
                                {"label": "网络候选且有整链实证", "value": "confirmed"},
                                {"label": "网络候选但无整链实证", "value": "aggregate_only"},
                                {"label": "事件中发生但网络表未收录", "value": "actual_only"},
                            ],
                            value="confirmed",
                            clearable=False,
                        ),
                    ],
                    className="rs-event-path-comparison-selector",
                ),
                html.Div(
                    _grid("event-path-comparison-signature-grid", page_size=15),
                    className="rs-grid-wrap",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )

    audit = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H6("具体事件路径审计", className="rs-card-title mb-1"),
                                html.Div(
                                    "从上方选择路径签名。",
                                    id="event-path-selected-summary",
                                    className="rs-step-note",
                                ),
                            ]
                        ),
                        dcc.Dropdown(
                            id="event-path-occurrence-selector",
                            options=[],
                            value=None,
                            placeholder="选择一次具体发生",
                            className="rs-event-path-occurrence-selector",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    id="event-path-occurrence-summary",
                    className="rs-result-summary",
                ),
                html.H6("路径签名时间间隔统计", className="rs-card-title mt-3"),
                html.Div(
                    _grid("event-path-time-grid"),
                    className="rs-grid-wrap",
                ),
                html.H6("所选具体发生的事件—分子实例图", className="rs-card-title mt-3"),
                cyto.Cytoscape(
                    id="event-path-cytoscape",
                    layout={"name": "breadthfirst", "directed": True, "padding": 32},
                    elements=[],
                    style={"width": "100%", "height": "330px"},
                    className="rs-cytoscape rs-event-path-cytoscape",
                    stylesheet=[
                        {
                            "selector": "node.concrete-event",
                            "style": {
                                "label": "data(label)",
                                "shape": "round-rectangle",
                                "width": 150,
                                "height": 50,
                                "background-color": "#dbeafe",
                                "border-color": "#2563eb",
                                "border-width": 2,
                                "font-size": 10,
                                "text-wrap": "wrap",
                            },
                        },
                        {
                            "selector": "edge.molecule-instance-edge",
                            "style": {
                                "label": "data(label)",
                                "curve-style": "bezier",
                                "target-arrow-shape": "triangle",
                                "line-color": "#0f766e",
                                "target-arrow-color": "#0f766e",
                                "width": 3,
                                "font-size": 9,
                                "text-background-color": "#ffffff",
                                "text-background-opacity": 0.9,
                            },
                        },
                    ],
                ),
                html.H6("事件节点", className="rs-card-title mt-3"),
                html.Div(_grid("event-path-event-grid"), className="rs-grid-wrap"),
                html.H6("分子实例连接与连续原子", className="rs-card-title mt-3"),
                html.Div(_grid("event-path-edge-grid"), className="rs-grid-wrap"),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    results = html.Div(
        [signatures, comparison, audit],
        id="event-path-results",
        className="rs-event-path-results",
        style={"display": "none"},
    )
    return html.Div(
        [controls, results],
        className="rs-event-path-panel",
    )


def _pathway_page() -> html.Div:
    concept_guide = html.Section(
        [
            html.Div(
                [
                    html.Span("先看懂两个问题", className="rs-step-kicker"),
                    html.H5("聚合网络是“路网”，真实事件路径是“行车记录”", className="mb-1"),
                    html.P(
                        "它们不是两份互相竞争的答案，而是候选发现与证据验证两个层级。"
                        "两者也可单独使用：功能 2 会独立枚举实际事件链，并自动做整体签名对照。",
                        className="rs-step-note mb-0",
                    ),
                ],
                className="rs-pathway-concept-heading",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("1 · 找可能路线", className="rs-pathway-concept-number"),
                            html.H6("聚合反应网络", className="mb-1"),
                            html.Strong("回答：网络上可以怎样走？"),
                            html.P(
                                "把整段轨迹里出现过的反应类型和累计频次汇总；"
                                "只要前一步产物与后一步反应物同名，就可拼成候选路线。",
                            ),
                            html.Small("能快速找候选；不能证明同一批原子按这个顺序走完。"),
                        ],
                        id="pathway-concept-aggregate",
                        className="rs-pathway-concept-card is-aggregate",
                    ),
                    html.Div("→", className="rs-pathway-concept-arrow", **{"aria-hidden": "true"}),
                    html.Div(
                        [
                            html.Span("2 · 查实际证据", className="rs-pathway-concept-number"),
                            html.H6("具体事件与原子谱系", className="mb-1"),
                            html.Strong("回答：轨迹里真的这样走过吗？"),
                            html.P(
                                "连接严格按时间先后发生的具体 RNG 事件；相邻事件还必须共享"
                                "同一分子实例和连续原子 ID。",
                            ),
                            html.Small("能报告实际次数、时间间隔、原子谱系支持和跨重复复现率。"),
                        ],
                        id="pathway-concept-actual",
                        className="rs-pathway-concept-card is-actual",
                    ),
                ],
                className="rs-pathway-concept-flow",
            ),
            html.Div(
                [
                    html.Strong("同一个例子："),
                    html.Span("网络中有 A → B，也有 B → C，所以 A → B → C 是候选。"),
                    html.Span(
                        "只有当某个具体事件产生 B，之后另一个具体事件消费同一个 B，"
                        "而且至少一个原子贯穿两步时，它才是实际发生路径。"
                    ),
                ],
                className="rs-pathway-example",
            ),
        ],
        id="pathway-concept-guide",
        className="rs-pathway-concept-guide",
    )
    controls = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Span("功能 1 · 候选发现", className="rs-step-kicker"),
                        html.H5("从聚合网络搜索可能路线", className="mb-1"),
                        html.P(
                            "输入起始物种，寻找由已汇总反应类型能够拼接出的路线。"
                            "若要判断整条路线是否真实发生，请切换功能 2；功能 2 不会把"
                            "本表选中行直接当作已验证，而是从具体事件中独立寻找整链证据。",
                            className="rs-step-note mb-2",
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("起始物种（精确 SMILES）"),
                                dcc.Input(
                                    id="pathway-start-smiles",
                                    value="",
                                    placeholder="例如 [CH3]",
                                    className="rs-pathway-start",
                                ),
                            ],
                            className="rs-pathway-field rs-pathway-start-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("方向"),
                                dcc.Dropdown(
                                    id="pathway-direction",
                                    options=[
                                        {"label": "下游（消耗）", "value": "downstream"},
                                        {"label": "上游（生成）", "value": "upstream"},
                                    ],
                                    value="downstream",
                                    clearable=False,
                                ),
                            ],
                            className="rs-pathway-field",
                        ),
                        html.Div([dbc.Label("最大深度"), dcc.Input(id="pathway-max-depth", value=4, type="number", min=1)], className="rs-pathway-field"),
                        html.Div([dbc.Label("每步分支"), dcc.Input(id="pathway-max-branches", value=4, type="number", min=1)], className="rs-pathway-field"),
                        html.Div([dbc.Label("路径上限"), dcc.Input(id="pathway-max-paths", value=10, type="number", min=1)], className="rs-pathway-field"),
                        html.Div([dbc.Label("最小净 TP"), dcc.Input(id="pathway-min-net-tp", value=1, type="number", min=1)], className="rs-pathway-field"),
                        html.Div([dbc.Label("最小方向性"), dcc.Input(id="pathway-min-directionality", value=0.05, type="number", min=0, max=1, step=0.01)], className="rs-pathway-field"),
                        html.Div(
                            [
                                dbc.Label("搜索目标"),
                                dcc.Dropdown(
                                    id="pathway-goal",
                                    options=[
                                        {"label": "按通量排名路径", "value": "ranked"},
                                        {"label": "追踪至小分子碎片（快速）", "value": "small_fragments"},
                                    ],
                                    value="small_fragments",
                                    clearable=False,
                                ),
                            ],
                            className="rs-pathway-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("小分子最大碳数"),
                                dcc.Input(
                                    id="pathway-target-max-carbon",
                                    value=4,
                                    type="number",
                                    min=1,
                                    max=100,
                                ),
                            ],
                            className="rs-pathway-field",
                        ),
                        dbc.Button("搜索网络可能路线", id="pathway-search-btn", color="primary", className="rs-pathway-search"),
                    ],
                    className="rs-pathway-controls",
                ),
                html.P(
                    "小分子快速模式只做 reactionabcd 核心粗筛，不读取事件、Route "
                    "或时间连续性。表中的每一行都是“网络可以这样拼”，不代表同一批原子"
                    "真的连续走完；整链证据请到“② 验证实际发生”查看。",
                    className="rs-step-note mt-2 mb-0",
                ),
            ],
            className="p-3",
        ),
        className="rs-card",
    )
    results = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(id="pathway-alert", className="rs-result-summary"),
                        html.Div(
                            [
                                dbc.Button("下载 JSON", id="pathway-json-btn", color="secondary", size="sm", outline=True),
                                dbc.Button("下载 CSV", id="pathway-csv-btn", color="secondary", size="sm", outline=True),
                                dcc.Download(id="pathway-json-download"),
                                dcc.Download(id="pathway-csv-download"),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                dcc.Loading(
                    html.Div(
                        _grid("pathway-grid"),
                        className="rs-grid-wrap",
                    ),
                    type="circle",
                ),
                html.Div(
                    [
                        html.H6(
                            "单个反应步骤的事件证据（不是整条路线）",
                            className="rs-card-title mb-1",
                        ),
                        html.P(
                            "这里一次只检查所选路线中的一个反应步骤。单步存在并不能证明"
                            "前后步骤由同一原子谱系连续完成。系统会先查询 RNG 事件索引；"
                            "缺少 RNG 事件时再查询已准备的 Route 索引。"
                            "Route 命中只是近似发生帧，不等于完整反应事件。",
                            className="rs-step-note mb-2",
                        ),
                        html.Div(
                            "选择一条路径开始验证。",
                            id="pathway-evidence-alert",
                            className="rs-result-summary",
                        ),
                        dcc.Loading(
                            html.Div(
                                _grid("pathway-evidence-grid"),
                                className="rs-grid-wrap",
                            ),
                            type="circle",
                        ),
                    ],
                    className="rs-pathway-evidence-panel",
                ),
                html.Div(
                    id="pathway-terminal-summary",
                    className="rs-pathway-terminal-summary",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    graph = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H6("网络可能路线图（尚未验证整链）", className="rs-card-title mb-0"),
                                html.Div(id="pathway-selection-summary", className="rs-step-note"),
                            ]
                        ),
                        html.Div(
                            [
                                dbc.Button("查看该单步事件", id="pathway-open-events-btn", color="secondary", size="sm", outline=True, disabled=True),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                cyto.Cytoscape(
                    id="pathway-cytoscape",
                    layout={"name": "breadthfirst", "directed": True, "padding": 20},
                    elements=[],
                    style={"width": "100%", "height": "460px"},
                    className="rs-cytoscape rs-pathway-cytoscape",
                    stylesheet=[
                        {"selector": "node.species", "style": {"label": "data(label)", "background-color": "#dbeafe", "border-color": "#60a5fa", "border-width": 1, "font-size": 8}},
                        {"selector": "node.terminal-product", "style": {"border-color": "#0f766e", "border-width": 3}},
                        {"selector": "node.small-fragment", "style": {"background-color": "#bbf7d0", "border-color": "#16a34a", "border-width": 4}},
                        {"selector": "node.reaction", "style": {"label": "data(label)", "shape": "diamond", "width": 24, "height": 24, "background-color": "#fbbf24", "font-size": 8}},
                        {"selector": "node.network-only", "style": {"background-color": "#d1d5db", "border-style": "dashed"}},
                        {"selector": "edge", "style": {"curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "#94a3b8", "target-arrow-color": "#94a3b8", "width": 1}},
                        {"selector": ".is-selected-path", "style": {"line-color": "#2563eb", "target-arrow-color": "#2563eb", "border-color": "#2563eb", "border-width": 3, "width": 3}},
                    ],
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    tabs = dbc.Tabs(
        [
            dbc.Tab(
                [controls, results, graph],
                label="① 搜索可能路线",
                tab_id="aggregate-pathways",
            ),
            dbc.Tab(
                _event_path_analysis_panel(),
                label="② 验证实际发生",
                tab_id="concrete-event-paths",
            ),
        ],
        id="pathway-analysis-tabs",
        active_tab="aggregate-pathways",
        className="rs-pathway-tabs",
    )
    return html.Div(
        [concept_guide, tabs],
        className="rs-page rs-pathway-page",
        id="page-pathway",
    )


def _batch_compare_page() -> html.Div:
    condition_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.H6("对比数据选择", className="rs-card-title mb-0"),
                        html.Span(
                            "可组合使用数据管理中的数据集与目录扫描结果",
                            className="rs-card-subtitle",
                        ),
                    ],
                    className="rs-batch-card-heading",
                ),
                html.Div(
                    [
                        dbc.Label("已管理数据集", className="mb-0 rs-batch-field-label"),
                        html.Div(
                            dcc.Dropdown(
                                id="batch-managed-selector",
                                multi=True,
                                options=[],
                                value=[],
                                placeholder="选择当前或最近加载的数据集",
                            ),
                            className="rs-grow rs-batch-multi-select",
                        ),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    id="batch-managed-status",
                    className="small text-muted rs-batch-source-status",
                ),
                html.Div(
                    [
                        html.Span("或扫描多条件目录", className="rs-batch-divider-label"),
                    ],
                    className="rs-batch-divider",
                ),
                html.Div(
                    [
                        dbc.Label("数据根目录", className="mb-0 rs-batch-field-label"),
                        dcc.Input(
                            id="batch-root-dir",
                            placeholder="选择包含多组模拟结果的目录",
                            className="rs-grow",
                            debounce=True,
                        ),
                        dbc.Button(
                            "当前目录上级",
                            id="batch-use-current-parent-btn",
                            color="secondary",
                            size="sm",
                            outline=True,
                        ),
                        dbc.Button("扫描", id="batch-scan-btn", color="primary", size="sm"),
                    ],
                    className="rs-query-row",
                ),
                html.Div(id="batch-conditions-status", className="small text-muted rs-batch-source-status"),
                html.Div(
                    [
                        dbc.Label("扫描条件组", className="mb-0 rs-batch-field-label"),
                        html.Div(
                            dcc.Dropdown(
                                id="batch-condition-selector",
                                multi=True,
                                placeholder="选择要对比的条件组",
                                options=[],
                            ),
                            className="rs-grow rs-batch-multi-select",
                        ),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    [
                        dbc.Label("最小检出率", className="mb-0 rs-batch-field-label"),
                        dcc.Input(id="batch-min-detection", value="0.0", type="number", min=0, max=1, step=0.1, style={"width": 80}),
                        dbc.Label("Top N", className="mb-0 rs-batch-field-label"),
                        dcc.Input(id="batch-top-n", value="50", type="number", min=1, max=500, style={"width": 80}),
                        dbc.Button("对比", id="batch-compare-btn", color="primary", size="sm", disabled=True),
                        dbc.Button("导出 CSV", id="batch-csv-btn", color="secondary", size="sm", outline=True, disabled=True),
                        dcc.Download(id="batch-csv-download"),
                    ],
                    className="rs-query-row rs-batch-action-row",
                ),
                html.Div(id="batch-selection-summary", className="small text-muted"),
            ],
            className="p-3",
        ),
        className="rs-card rs-batch-controls-card",
    )
    matrix_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    id="batch-alert",
                    children=html.Div(
                        [
                            html.Div("选择条件组开始对比", className="rs-batch-empty-title"),
                            html.Div(
                                "可直接选择已管理数据集，也可扫描目录并选择自动识别的重复实验组。",
                                className="rs-batch-empty-hint",
                            ),
                        ],
                        className="rs-batch-empty-state",
                    ),
                ),
                dcc.Loading(
                    html.Div(
                        _grid("batch-matrix-grid", page_size=50),
                        id="batch-grid-container",
                        className="rs-grid-wrap rs-batch-grid-wrap",
                        style={"display": "none"},
                    ),
                    type="circle",
                ),
            ],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill rs-batch-results-card",
    )
    detail_card = dbc.Card(
        dbc.CardBody(
            [
                html.H6("反应详情", className="rs-card-title"),
                dcc.Loading(
                    [
                        dcc.Graph(id="batch-reaction-chart", className="rs-chart"),
                        html.Div(id="batch-reaction-stats"),
                    ],
                    type="circle",
                ),
            ],
            className="p-3",
        ),
        className="rs-card rs-batch-detail-card",
        id="batch-detail-card",
        style={"display": "none"},
    )
    return html.Div([condition_card, matrix_card, detail_card], className="rs-page", id="page-batch-compare")


def _channel_grid(grid_id: str) -> dash_table.DataTable:
    """Dense, single-selection table used for reaction channels."""
    return dash_table.DataTable(
        id=grid_id,
        columns=[],
        data=[],
        selected_rows=[],
        row_selectable="single",
        page_action="none",
        sort_action="native",
        markdown_options={"link_target": "_blank"},
        style_table={"maxHeight": "420px", "overflowY": "auto", "overflowX": "auto"},
        style_cell={"fontSize": 12, "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", "padding": "8px 9px", "textAlign": "left", "minWidth": "76px", "maxWidth": "340px", "overflow": "hidden", "textOverflow": "ellipsis"},
        style_header={"backgroundColor": "#f8fafc", "fontWeight": 650, "borderBottom": "1px solid #d9dee7"},
        style_data={"borderBottom": "1px solid #edf0f4"},
        style_data_conditional=[
            {"if": {"state": "selected"}, "backgroundColor": "#eef3ff", "borderLeft": "3px solid #1d4ed8"},
            {"if": {"row_index": "odd"}, "backgroundColor": "#fbfcfe"},
            {"if": {"column_id": "structure"}, "width": "90px", "minWidth": "90px", "maxWidth": "90px", "padding": "1px 5px", "textAlign": "center"},
        ],
    )


def _data_index_readiness_row(
    kind: str,
    label: str,
    filename: str,
    purpose: str,
) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(label, className="rs-index-name"),
                    html.Div(filename, className="rs-index-filename"),
                ]
            ),
            html.Div(purpose, className="rs-index-purpose"),
            html.Div(
                "正在检查…",
                id=f"data-prep-{kind}-status",
                className="rs-index-status-cell",
            ),
            dbc.Button(
                "创建/重建",
                id=f"data-prep-{kind}-btn",
                color="secondary",
                size="sm",
                outline=True,
                className="rs-index-action",
            ),
        ],
        className="rs-index-readiness-row",
    )


def _data_cache_management_card() -> html.Div:
    return html.Div(
        [
            html.Section(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        "索引就绪状态",
                                        className="rs-data-section-title",
                                    ),
                                    html.Div(
                                        "每种索引保留一个直接操作；索引只写入缓存目录。",
                                        className="rs-card-subtitle",
                                    ),
                                ]
                            ),
                            html.Div(
                                "状态自动刷新",
                                id="data-prep-refresh-label",
                                className="rs-index-refresh-label",
                            ),
                        ],
                        className="rs-index-section-heading",
                    ),
                    html.Div(id="data-prep-status-alert"),
                    html.Div(
                        [
                            html.Div("索引"),
                            html.Div("用途"),
                            html.Div("状态与规模"),
                            html.Div("操作"),
                        ],
                        className="rs-index-readiness-header",
                        **{"aria-hidden": "true"},
                    ),
                    html.Div(
                        [
                            _data_index_readiness_row(
                                "event",
                                "事件索引",
                                "events.sqlite3",
                                "反应事件检索、路径证据与事件跳转",
                            ),
                            _data_index_readiness_row(
                                "trajectory",
                                "轨迹帧索引",
                                "trajectory.sqlite3",
                                "按时间步定位帧并提取局部反应轨迹",
                            ),
                            _data_index_readiness_row(
                                "composition",
                                "C/O/Cl 组成索引",
                                "composition.sqlite3",
                                "组成演化、时间采样与代表物种下钻",
                            ),
                        ],
                        id="data-prep-status",
                    ),
                    html.Div(
                        [
                            html.Div(
                                id="data-prep-action-progress",
                                className="rs-index-task-progress",
                            ),
                            dbc.Button(
                                "取消后台任务",
                                id="data-prep-cancel-btn",
                                color="secondary",
                                size="sm",
                                outline=True,
                                disabled=True,
                            ),
                        ],
                        className="rs-index-background-task",
                    ),
                    html.Div(id="data-prep-action-alert", className="rs-index-action-alert"),
                ],
                className="rs-data-section rs-index-readiness-section",
            ),
            html.Div(id="data-prep-clear-alert", className="rs-index-clear-alert"),
            html.Details(
                [
                    html.Summary(
                        [
                            html.Span("危险操作：清理索引缓存"),
                            html.Small("不会删除 RNG 原始输出"),
                        ]
                    ),
                    html.Div(
                        [
                            html.Div(
                                "清理后对应分析将不可用，直到重新建立索引；每次操作均需二次确认。",
                                className="rs-danger-copy",
                            ),
                            html.Div(
                                [
                                    dbc.Button(
                                        "清理事件索引",
                                        id="data-clear-event-btn",
                                        color="danger",
                                        size="sm",
                                        outline=True,
                                        disabled=True,
                                    ),
                                    dbc.Button(
                                        "清理轨迹索引",
                                        id="data-clear-trajectory-btn",
                                        color="danger",
                                        size="sm",
                                        outline=True,
                                        disabled=True,
                                    ),
                                    dbc.Button(
                                        "清理组成索引",
                                        id="data-clear-composition-btn",
                                        color="danger",
                                        size="sm",
                                        outline=True,
                                        disabled=True,
                                    ),
                                ],
                                className="rs-danger-actions",
                            ),
                        ],
                        className="rs-danger-body",
                    ),
                ],
                className="rs-data-collapsible rs-cache-danger-zone",
            ),
        ],
        id="data-cache-management",
        className="rs-data-cache-workspace",
    )


def _data_management_page() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(id="data-load-feedback"),
                    html.Div(
                        [
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                "当前数据集",
                                                                className="rs-data-section-kicker",
                                                            ),
                                                            html.Div(
                                                                id="data-candidate-summary",
                                                                className="rs-data-candidate-summary",
                                                            ),
                                                        ],
                                                        className="rs-data-summary-title-group",
                                                    ),
                                                    html.Div(
                                                        id="data-scan-status",
                                                        className="rs-data-scan-status",
                                                    ),
                                                ],
                                                className="rs-data-summary-heading",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Span(
                                                                "基础分析文件",
                                                                className="rs-summary-metric-label",
                                                            ),
                                                            html.Div(
                                                                "正在检查…",
                                                                id="data-prep-basic-status",
                                                                className="rs-summary-metric-value",
                                                            ),
                                                        ],
                                                        className="rs-summary-metric",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Span(
                                                                "工作区",
                                                                className="rs-summary-metric-label",
                                                            ),
                                                            html.Span(
                                                                "当前服务器",
                                                                className="rs-summary-metric-value",
                                                            ),
                                                        ],
                                                        className="rs-summary-metric",
                                                    ),
                                                ],
                                                className="rs-data-summary-metrics",
                                            ),
                                            html.Div(
                                                id="data-artifacts",
                                                className="rs-data-artifacts",
                                            ),
                                            html.Div(
                                                [
                                                    html.Span(
                                                        "最近加载",
                                                        className="rs-recent-label",
                                                    ),
                                                    html.Div(
                                                        id="data-recent-datasets",
                                                        className="rs-browser-recent",
                                                    ),
                                                ],
                                                className="rs-data-recent-row",
                                            ),
                                        ],
                                        className="rs-data-summary-main",
                                    ),
                                    html.Aside(
                                        html.Div(
                                            "正在确定建议操作…",
                                            id="data-next-action",
                                        ),
                                        className="rs-data-next-action",
                                    ),
                                ],
                                className="rs-data-summary-panel",
                            ),
                            _data_cache_management_card(),
                        ],
                        id="data-overview-view",
                        className="rs-data-view",
                    ),
                    html.Div(
                        [
                            dbc.Button(
                                "↑ 上一级",
                                id="dir-browser-back-btn",
                                color="secondary",
                                size="sm",
                                outline=True,
                                disabled=True,
                                className="mb-2",
                            ),
                            dbc.InputGroup(
                                [
                                    dbc.Input(
                                        id="dir-browser-path-input",
                                        debounce=True,
                                        placeholder="输入服务器目录或数据集公共前缀",
                                    ),
                                    dbc.Button(
                                        "前往",
                                        id="dir-browser-go-btn",
                                        color="secondary",
                                    ),
                                ],
                                className="rs-browser-path-control",
                            ),
                            html.Div(
                                id="dir-browser-current",
                                className="rs-browser-current",
                            ),
                            html.Div(
                                id="dir-browser-body",
                                children=html.Div(
                                    "正在加载…",
                                    className="small text-muted",
                                ),
                                className="rs-browser-directory-list",
                            ),
                            html.Div(
                                [
                                    dbc.Button(
                                        "返回数据管理",
                                        id="dir-browser-cancel-btn",
                                        color="secondary",
                                        size="sm",
                                        outline=True,
                                    ),
                                    dbc.Button(
                                        "加载数据集",
                                        id="data-apply-btn",
                                        color="success",
                                        size="sm",
                                        disabled=True,
                                    ),
                                ],
                                className="d-flex justify-content-between mt-3",
                            ),
                        ],
                        id="data-browser-view",
                        className="rs-data-view d-none",
                    ),
                    dbc.Input(
                        id="data-folder-input",
                        debounce=True,
                        style={"display": "none"},
                    ),
                    # base remains an internal compatibility value for legacy
                    # callbacks while browser candidates own selection.
                    dcc.Dropdown(
                        id="data-rungroup",
                        options=[],
                        style={"display": "none"},
                    ),
                ],
                className="rs-data-page-body",
            ),
        ],
        id="page-data-management",
        className="rs-page rs-data-page",
    )


def _index_clear_confirm_modal() -> dbc.Modal:
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("确认清理索引")),
            dbc.ModalBody(id="data-clear-confirm-text"),
            dbc.ModalFooter(
                [
                    dbc.Button("取消", id="data-clear-cancel-btn", color="secondary", size="sm", outline=True),
                    dbc.Button("确认清理", id="data-clear-confirm-btn", color="danger", size="sm"),
                ]
            ),
        ],
        id="data-clear-confirm-modal",
        is_open=False,
        backdrop="static",
    )


def build_layout() -> html.Div:
    """Build the full application layout."""
    return html.Div(
        [
            _topbar(),
            _global_operation_progress(),
            html.Div(
                [
                    _sidebar(),
                    html.Div(
                        [
                            _page_header(),
                            _species_page(),
                            _reactions_page(),
                            _pathway_page(),
                            _intermediate_page(),
                            _evolution_page(),
                            _carbon_page(),
                            _events_page(),
                            _trajectory_page(),
                            _data_management_page(),
                            _batch_compare_page(),
                        ],
                        className="rs-main",
                    ),
                ],
                className="rs-body rs-tool-shell",
                id="app-body",
            ),
            _index_clear_confirm_modal(),
            dcc.Store(id="dir-browser-path", storage_type="memory", data=""),
            dcc.Store(id="dataset-browser-candidate", storage_type="memory", data=None),
            dcc.Store(id="recent-datasets", storage_type="local", data=[]),
            dcc.Store(id="app-store", storage_type="session", data=cb.initial_store()),
            dcc.Store(id="page-store", storage_type="session", data={"page": DEFAULT_PAGE}),
            dcc.Store(id="species-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="rxn-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="inter-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="evolution-payload-store", storage_type="memory", data=None),
            dcc.Store(id="carbon-payload-store", storage_type="memory", data=None),
            dcc.Store(id="carbon-advanced-store", storage_type="memory", data=None),
            dcc.Store(id="event-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="data-clear-kind-store", storage_type="memory", data={}),
            dcc.Interval(id="data-prep-refresh", interval=2000, n_intervals=0, disabled=True),
            dcc.Store(id="event-selected-store", storage_type="memory", data=None),
            dcc.Store(id="event-viewer-store", storage_type="memory", data=None),
            dcc.Store(id="pathway-store", storage_type="memory", data=None),
            dcc.Store(id="pathway-context-store", storage_type="memory", data=None),
            dcc.Store(id="pathway-selected-step", storage_type="memory", data=None),
            dcc.Store(id="pathway-selected-path", storage_type="memory", data=None),
            dcc.Store(id="event-path-store", storage_type="memory", data=None),
            dcc.Store(id="event-path-context-store", storage_type="memory", data=None),
            dcc.Store(id="event-path-wizard-step", storage_type="memory", data=1),
            dcc.Store(id="batch-managed-store", storage_type="memory", data={"datasets": []}),
            dcc.Store(id="batch-conditions-store", storage_type="memory", data=None),
            dcc.Store(
                id="batch-matrix-grid-store",
                storage_type="memory",
                data={"rows": [], "columns": [], "details": {}, "groups": []},
            ),
        ],
        className="rs-root",
    )


def create_app() -> dash.Dash:
    """Create and configure the Dash application instance."""
    app = dash.Dash(
        __name__,
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
        title="ReacNet Scope (Dash)",
        assets_folder=str(Path(__file__).parent / "assets"),
        background_callback_manager=_background_callback_manager(),
    )
    app.layout = build_layout()
    cb.register_callbacks(app)

    @app.server.get("/api/structure.svg")
    def _structure_svg():
        smiles = (request.args.get("smiles") or "").strip()
        if not smiles or len(smiles) > 4096:
            return Response("invalid SMILES", status=400, mimetype="text/plain")
        try:
            width = max(80, min(360, int(request.args.get("width") or 112)))
            height = max(48, min(240, int(request.args.get("height") or 58)))
        except (TypeError, ValueError):
            return Response("invalid dimensions", status=400, mimetype="text/plain")
        show_h = str(request.args.get("show_h") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        result = svc.render_species_svg(
            smiles,
            width=width,
            height=height,
            show_h=show_h,
        )
        if not result.get("ok") or not result.get("svg"):
            return Response("structure unavailable", status=422, mimetype="text/plain")
        return Response(
            str(result["svg"]),
            mimetype="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.server.get("/api/health")
    def _health():
        cache_text = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
        cache_path = Path(cache_text).expanduser() if cache_text else None
        cache_ready = bool(
            cache_path
            and cache_path.exists()
            and cache_path.is_dir()
            and os.access(cache_path, os.W_OK)
        )
        try:
            app_version = version("reacnet-scope")
        except PackageNotFoundError:
            app_version = "development"
        warnings: list[str] = []
        if not cache_text:
            warnings.append("REACNET_SCOPE_CACHE_DIR is not configured")
        elif not cache_ready:
            warnings.append("configured cache directory is not writable")
        return jsonify(
            {
                "ok": True,
                "service": "reacnet-scope-web-dash",
                "version": app_version,
                "uptime_seconds": round(time.time() - _PROCESS_STARTED_AT, 3),
                "cache_dir": str(cache_path) if cache_path else "",
                "cache_ready": cache_ready,
                "allowed_roots": [str(path) for path in svc.ALLOWED_ROOTS],
                "warnings": warnings,
            }
        )
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="ReacNet Scope Dash WebUI V1")
    ap.add_argument("--host", default="127.0.0.1", help="bind host")
    ap.add_argument("--port", type=int, default=8060, help="bind port")
    ap.add_argument("--debug", action="store_true", help="enable Dash debug mode")
    args = ap.parse_args()

    app = create_app()
    print(f"[ReacNet-Scope-Dash] http://{args.host}:{args.port}")
    print("[ReacNet-Scope-Dash] Press Ctrl+C to stop")
    try:
        app.run(host=args.host, port=args.port, debug=args.debug)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
