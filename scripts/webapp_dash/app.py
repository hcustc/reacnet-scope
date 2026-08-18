"""Dash WebUI V1 entry point for ReacNet Scope.

This is the single supported Web application.

Usage::

    uv run reacnet-scope serve --host 127.0.0.1 --port 8060
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
from reacnet_scope import services as svc  # noqa: E402
from scripts.webapp_dash.navigation import (  # noqa: E402
    NAV_GROUPS,
    PAGE_DESCRIPTIONS,
    PAGE_ICONS,
    PAGE_LABELS,
    PAGE_SECTIONS,
    START_PAGE,
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
                        html.Img(
                            src=PAGE_ICONS[page_id],
                            className="rs-nav-icon",
                            alt="",
                            **{"aria-hidden": "true"},
                        ),
                        html.Span(PAGE_LABELS[page_id], className="rs-nav-text"),
                    ],
                    id=f"nav-{page_id}",
                    type="button",
                    title=PAGE_LABELS[page_id],
                    n_clicks=0,
                    **{
                        "aria-current": (
                            "page" if page_id == START_PAGE else "false"
                        )
                    },
                    className=(
                        "rs-top-nav-item active"
                        if page_id == START_PAGE
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
                PAGE_LABELS[START_PAGE],
                id="topbar-page-context",
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
                                            html.Span(
                                                id="topbar-status",
                                                className="rs-badge rs-bad",
                                                children="未加载数据",
                                                role="status",
                                                **{"aria-live": "polite"},
                                            ),
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
                        "刷新索引状态",
                        id="data-prep-refresh-btn",
                        color="secondary",
                        size="sm",
                        outline=True,
                        className="rs-topbar-refresh-btn",
                    ),
                    dbc.Button(
                        "选择数据集",
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
                            html.Img(
                                src=PAGE_ICONS["data-management"],
                                className="rs-nav-icon",
                                alt="",
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
                        **{"aria-current": "page"},
                        className="rs-top-nav-item rs-nav-utility active",
                    ),
                    html.Button(
                        [
                            html.Img(
                                src=PAGE_ICONS["batch-compare"],
                                className="rs-nav-icon",
                                alt="",
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
                        **{"aria-current": "false"},
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
                                PAGE_SECTIONS[START_PAGE],
                                id="page-eyebrow-section",
                            ),
                        ],
                        className="rs-page-eyebrow",
                    ),
                    html.H1(PAGE_LABELS[START_PAGE], id="page-title"),
                    html.P(
                        PAGE_DESCRIPTIONS[START_PAGE],
                        id="page-description",
                    ),
                ],
                className="rs-page-heading",
            ),
            html.Div(
                [
                    html.Div(
                        "需要选择数据集",
                        id="page-data-status",
                        className="rs-page-status is-blocked",
                        role="status",
                        **{"aria-live": "polite"},
                    ),
                    dbc.Button(
                        "前往数据管理",
                        id="page-capability-manage-btn",
                        color="secondary",
                        size="sm",
                        outline=True,
                    ),
                ],
                className="rs-page-capability-status",
            ),
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
                                "查看直接反应通道",
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
        tooltip_delay=300,
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
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            dbc.Button(
                                                "检索结果",
                                                id="species-stage-results-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                active=True,
                                            ),
                                            dbc.Button(
                                                "结构列表",
                                                id="species-stage-structures-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                            ),
                                            dbc.Button(
                                                "物种详情",
                                                id="species-stage-detail-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                            ),
                                        ],
                                        className="rs-species-stage-switch",
                                    ),
                                    dbc.Button(
                                        "← 返回检索结果",
                                        id="species-stage-back-btn",
                                        color="secondary",
                                        size="sm",
                                        outline=True,
                                        disabled=True,
                                        style={"display": "none"},
                                    ),
                                ],
                                className="rs-species-workspace-nav",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                id="species-alert",
                                                className="rs-result-summary mb-0",
                                            ),
                                            html.Span(
                                                "悬停分子式或 SMILES 可预览结构",
                                                className="rs-species-hover-hint",
                                            ),
                                        ],
                                        className="rs-species-result-meta",
                                    ),
                                    dcc.Loading(
                                        html.Div(
                                            _grid("species-grid", page_size=20),
                                            className=(
                                                "rs-grid-wrap "
                                                "rs-species-grid-wrap"
                                            ),
                                        ),
                                        type="circle",
                                    ),
                                ],
                                id="species-result-stage",
                            ),
                            html.Div(
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
                                                className=(
                                                    "rs-grid-wrap "
                                                    "rs-species-grid-wrap"
                                                ),
                                            ),
                                            type="circle",
                                        ),
                                        dcc.Download(
                                            id="species-structure-csv-download"
                                        ),
                                    ],
                                    id="species-structure-results",
                                    style={"display": "none"},
                                ),
                                id="species-structure-stage",
                                style={"display": "none"},
                            ),
                            html.Div(
                                _detail_panel(),
                                id="species-detail-stage",
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

    return html.Div([query_card, grid_card], className="rs-page", id="page-species")


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
                    dbc.Button(
                        "← 返回物种检索",
                        id="rxn-channel-back-btn",
                        title="返回物种检索",
                        color="secondary",
                        size="sm",
                        outline=True,
                    ),
                ],
                className="rs-channel-view-header rs-channel-view-actions",
            ),
            html.Div(id="rxn-channel-alert", className="rs-flow-alert"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.H3("生成通道"),
                                            html.Span("目标物种位于产物侧"),
                                        ],
                                        className="rs-lane-heading",
                                    ),
                                    html.Div(
                                        [
                                            dbc.Button(
                                                "导出 CSV",
                                                id="rxn-production-csv-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                            ),
                                            dcc.Download(
                                                id="rxn-production-csv-download"
                                            ),
                                        ],
                                        className="rs-lane-actions",
                                    ),
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
                                    html.Div(
                                        [
                                            html.H3("消耗通道"),
                                            html.Span("目标物种位于反应物侧"),
                                        ],
                                        className="rs-lane-heading",
                                    ),
                                    html.Div(
                                        [
                                            dbc.Button(
                                                "导出 CSV",
                                                id="rxn-consumption-csv-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                            ),
                                            dcc.Download(
                                                id="rxn-consumption-csv-download"
                                            ),
                                        ],
                                        className="rs-lane-actions",
                                    ),
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
            dcc.Store(id="rxn-channel-history-store", data=[]),
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


def _evolution_page() -> html.Div:
    query_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    dbc.Label("手动目标物种/分子式", className="mb-0"),
                                    dcc.Textarea(
                                        id="evolution-targets",
                                        value="",
                                        placeholder="可选；每行一个目标，支持 label::query",
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
                                        value="step",
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
                    html.Div(
                        [
                            html.Div(
                                [
                                    dbc.Label("从已导入文件选择物种", className="mb-0"),
                                    dcc.Dropdown(
                                        id="evolution-species-picker",
                                        options=[],
                                        value=[],
                                        multi=True,
                                        searchable=True,
                                        placeholder="先读取物种目录，再选择一个或多个分子式",
                                        optionHeight=42,
                                    ),
                                ],
                                className="rs-form-field rs-form-field-grow",
                            ),
                            html.Div(
                                [
                                    dbc.Button(
                                        "读取物种目录",
                                        id="evolution-load-species-btn",
                                        color="secondary",
                                        size="sm",
                                        outline=True,
                                    ),
                                    html.Div(
                                        id="evolution-catalog-alert",
                                        className="rs-inline-status",
                                        **{"aria-live": "polite"},
                                    ),
                                ],
                                className="rs-form-actions rs-evolution-catalog-actions",
                            ),
                        ],
                        className="rs-query-row rs-evolution-picker-row",
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
                                        dbc.Label("timestep → ps（确认后保存）", className="mb-0"),
                                        dcc.Input(id="evolution-timestep", value=None, type="number", min=0, placeholder="留空使用当前数据集已保存的换算", style={"width": 120}),
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


def _element_distribution_page() -> html.Div:
    settings_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("数据集", html_for="element-distribution-dataset-name"),
                                dcc.Input(
                                    id="element-distribution-dataset-name",
                                    value="未选择",
                                    disabled=True,
                                    className="rs-element-distribution-input",
                                ),
                            ],
                            className="rs-element-distribution-field rs-element-distribution-dataset",
                        ),
                        html.Div(
                            [
                                dbc.Label(
                                    "timestep → ps（确认后保存）",
                                    html_for="element-distribution-timestep",
                                ),
                                dcc.Input(
                                    id="element-distribution-timestep",
                                    value=None,
                                    type="number",
                                    min=1e-12,
                                    step="any",
                                    placeholder="留空使用当前数据集已保存的换算；未保存时显示 timestep",
                                ),
                            ],
                            className="rs-element-distribution-field",
                        ),
                        html.Div(
                            [
                                dbc.Label(
                                    "分组元素",
                                    html_for="element-distribution-group-element",
                                ),
                                dcc.Dropdown(
                                    id="element-distribution-group-element",
                                    options=[{"label": "C", "value": "C"}],
                                    value="C",
                                    clearable=False,
                                ),
                            ],
                            className="rs-element-distribution-field",
                        ),
                        html.Div(
                            [
                                dbc.Label(
                                    "最大原子数",
                                    html_for="element-distribution-max-count",
                                ),
                                dcc.Input(
                                    id="element-distribution-max-count",
                                    value=6,
                                    type="number",
                                    min=0,
                                    max=200,
                                ),
                            ],
                            className="rs-element-distribution-field",
                        ),
                        html.Div(
                            dbc.Checkbox(
                                id="element-distribution-include-zero",
                                value=False,
                                label="包含 0 原子分组（E0）",
                            ),
                            className="rs-element-distribution-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("参考物种 SMILES（可选）", html_for="element-distribution-reference-smiles"),
                                dcc.Input(
                                    id="element-distribution-reference-smiles",
                                    value="",
                                    placeholder="例如 [N][S]；留空则只显示元素分组",
                                    className="rs-element-distribution-input",
                                ),
                            ],
                            className="rs-element-distribution-field",
                        ),
                        dbc.Button("绘制", id="element-distribution-search-btn", color="primary", className="rs-element-distribution-draw"),
                    ],
                    className="rs-element-distribution-controls",
                ),
                html.Hr(),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span("筛选元素", className="rs-element-distribution-filter-label"),
                                dcc.Dropdown(
                                    id="element-distribution-filter-element",
                                    options=[],
                                    value=None,
                                    clearable=True,
                                ),
                            ],
                            className="rs-element-distribution-filter",
                        ),
                        html.Div(
                            [
                                html.Span("筛选条件", className="rs-element-distribution-filter-label"),
                                dbc.RadioItems(
                                    id="element-distribution-filter-mode",
                                    options=[
                                        {"label": "全部", "value": "all"},
                                        {"label": "存在", "value": "present"},
                                        {"label": "不存在", "value": "absent"},
                                        {"label": "原子数范围", "value": "range"},
                                    ],
                                    value="all",
                                    inline=True,
                                    className="rs-element-distribution-radio",
                                ),
                            ],
                            className="rs-element-distribution-filter",
                        ),
                        html.Div(
                            [
                                html.Span("最小 / 最大", className="rs-element-distribution-filter-label"),
                                dcc.Input(
                                    id="element-distribution-filter-min",
                                    type="number",
                                    min=0,
                                    placeholder="最小",
                                ),
                                dcc.Input(
                                    id="element-distribution-filter-max",
                                    type="number",
                                    min=0,
                                    placeholder="最大",
                                ),
                            ],
                            className="rs-element-distribution-filter",
                        ),
                    ],
                    className="rs-element-distribution-filter-row",
                ),
                html.Div(
                    [
                        html.Div(id="element-distribution-index-status", className="rs-index-status"),
                        dbc.Progress(
                            id="element-distribution-index-progress",
                            value=0,
                            max=100,
                            striped=True,
                            animated=True,
                            className="rs-index-progress",
                        ),
                    ],
                    className="rs-index-block",
                ),
                html.Div(id="element-distribution-progress", className="rs-analysis-progress"),
                dcc.Interval(
                    id="element-distribution-index-refresh",
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
                html.Div(id="element-distribution-alert"),
                html.Div(id="element-distribution-highlights", className="rs-stat-row"),
                dcc.Loading(
                    dcc.Graph(
                        id="element-distribution-composition-trend",
                        figure=_empty_chart_figure(
                            "尚无组成趋势",
                            "选择数据来源与元素范围，然后开始绘制。",
                        ),
                        config={"displaylogo": False, "responsive": True},
                        className="rs-element-distribution-chart",
                    ),
                    type="circle",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div(
                                    "点击主图中的参考物种或元素计数曲线，查看该时间点的代表物种。",
                                    id="element-distribution-composition-table-title",
                                    className="rs-composition-detail-title",
                                ),
                                html.Div(id="element-distribution-drilldown-progress", className="rs-analysis-progress"),
                            ],
                            className="rs-element-distribution-table-heading",
                        ),
                        _grid("element-distribution-composition-table", row_selectable="single"),
                        dbc.Checkbox(
                            id="element-distribution-structure-show-h",
                            value=True,
                            label="显示 H",
                            className="rs-structure-h-toggle",
                        ),
                        html.Div(
                            id="element-distribution-structure-detail",
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
        className="rs-page rs-element-distribution-minimal",
        id="page-element-distribution",
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
                    "直接查询已准备的事件索引；支持原生 .timeline.h5，"
                    "也兼容 .reactionevent.csv + .molecules.csv。",
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


def _molecule_lineage_card() -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("分子实例证据", className="rs-step-kicker"),
                                html.H6(
                                    "追踪此分子谱系",
                                    className="rs-card-title mb-0",
                                ),
                            ],
                            className="rs-step-heading",
                        ),
                        html.Div(
                            [
                                dbc.Button(
                                    "下载 JSON",
                                    id="molecule-lineage-json-btn",
                                    color="secondary",
                                    size="sm",
                                    outline=True,
                                    disabled=True,
                                ),
                                dbc.Button(
                                    "下载审计 CSV",
                                    id="molecule-lineage-csv-btn",
                                    color="secondary",
                                    size="sm",
                                    outline=True,
                                    disabled=True,
                                ),
                                dcc.Download(id="molecule-lineage-json-download"),
                                dcc.Download(id="molecule-lineage-csv-download"),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.P(
                    "以所选事件中的具体分子（SMILES + Atom IDs + 帧）为起点；"
                    "默认只用非氢原子延伸谱系，所有结论均可回查到 RNG event_id。",
                    className="rs-step-note",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("起点分子", html_for="molecule-lineage-participant"),
                                dcc.Dropdown(
                                    id="molecule-lineage-participant",
                                    options=[],
                                    value=None,
                                    clearable=False,
                                    placeholder="选择反应物或产物实例",
                                ),
                            ],
                            className="rs-lineage-field rs-lineage-participant-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("锚点规则", html_for="molecule-lineage-anchor-mode"),
                                dcc.Dropdown(
                                    id="molecule-lineage-anchor-mode",
                                    options=[
                                        {"label": "全部非氢原子", "value": "non_hydrogen"},
                                        {"label": "全部原子", "value": "all"},
                                        {"label": "指定元素", "value": "elements"},
                                        {"label": "指定 Atom IDs", "value": "atom_ids"},
                                    ],
                                    value="non_hydrogen",
                                    clearable=False,
                                ),
                            ],
                            className="rs-lineage-field",
                        ),
                        html.Div(
                            [
                                dbc.Label(
                                    "自定义锚点",
                                    html_for="molecule-lineage-anchor-value",
                                ),
                                dbc.Input(
                                    id="molecule-lineage-anchor-value",
                                    value="",
                                    placeholder="当前规则无需填写",
                                    disabled=True,
                                ),
                            ],
                            className="rs-lineage-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("向前 / 向后持久变化数"),
                                html.Div(
                                    [
                                        dbc.Input(
                                            id="molecule-lineage-depth-backward",
                                            type="number",
                                            value=3,
                                            min=0,
                                            max=20,
                                            step=1,
                                        ),
                                        dbc.Input(
                                            id="molecule-lineage-depth-forward",
                                            type="number",
                                            value=3,
                                            min=0,
                                            max=20,
                                            step=1,
                                        ),
                                    ],
                                    className="rs-lineage-paired-input",
                                ),
                            ],
                            className="rs-lineage-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("分子节点上限"),
                                dbc.Input(
                                    id="molecule-lineage-node-limit",
                                    type="number",
                                    value=100,
                                    min=1,
                                    max=10000,
                                    step=1,
                                ),
                            ],
                            className="rs-lineage-field",
                        ),
                        html.Div(
                            [
                                dbc.Label("快速回穿窗口（分析帧）"),
                                dbc.Input(
                                    id="molecule-lineage-recross-window",
                                    type="number",
                                    value=5,
                                    min=0,
                                    max=10000,
                                    step=1,
                                ),
                            ],
                            className="rs-lineage-field",
                        ),
                        dbc.Button(
                            "构建谱系",
                            id="molecule-lineage-run-btn",
                            color="primary",
                            disabled=True,
                            className="rs-lineage-run",
                        ),
                    ],
                    className="rs-lineage-controls",
                ),
                html.Div(id="molecule-lineage-alert", className="rs-result-summary"),
                html.Div(
                    [
                        html.Div(id="molecule-lineage-summary", className="rs-stat-row"),
                        html.Div(
                            id="molecule-lineage-truncation",
                            className="rs-step-note",
                        ),
                        html.Div(
                            [
                                dbc.Label("视图", className="mb-0"),
                                dbc.RadioItems(
                                    id="molecule-lineage-view",
                                    options=[
                                        {"label": "持久变化（折叠回穿）", "value": "persistent"},
                                        {"label": "原始事件", "value": "raw"},
                                    ],
                                    value="persistent",
                                    inline=True,
                                ),
                            ],
                            className="rs-lineage-view-toggle",
                        ),
                        cyto.Cytoscape(
                            id="molecule-lineage-cytoscape",
                            layout={
                                "name": "breadthfirst",
                                "directed": True,
                                "padding": 32,
                                "spacingFactor": 1.25,
                            },
                            elements=[],
                            style={"width": "100%", "height": "460px"},
                            className="rs-cytoscape rs-molecule-lineage-cytoscape",
                            stylesheet=[
                                {
                                    "selector": "node.molecule",
                                    "style": {
                                        "label": "data(label)",
                                        "shape": "round-rectangle",
                                        "width": 150,
                                        "height": 54,
                                        "background-color": "#e0f2fe",
                                        "border-color": "#0284c7",
                                        "border-width": 2,
                                        "font-size": 9,
                                        "text-wrap": "wrap",
                                    },
                                },
                                {
                                    "selector": "node.root",
                                    "style": {
                                        "background-color": "#dcfce7",
                                        "border-color": "#15803d",
                                        "border-width": 4,
                                    },
                                },
                                {
                                    "selector": "node.context",
                                    "style": {"opacity": 0.52},
                                },
                                {
                                    "selector": "node.event",
                                    "style": {
                                        "label": "data(label)",
                                        "shape": "diamond",
                                        "width": 78,
                                        "height": 78,
                                        "background-color": "#f1f5f9",
                                        "border-color": "#475569",
                                        "border-width": 2,
                                        "font-size": 8,
                                        "text-wrap": "wrap",
                                    },
                                },
                                {
                                    "selector": "node.recrossing",
                                    "style": {
                                        "label": "data(label)",
                                        "shape": "hexagon",
                                        "width": 120,
                                        "height": 62,
                                        "background-color": "#ffedd5",
                                        "border-color": "#ea580c",
                                        "border-width": 2,
                                        "font-size": 9,
                                        "text-wrap": "wrap",
                                    },
                                },
                                {
                                    "selector": "edge",
                                    "style": {
                                        "curve-style": "bezier",
                                        "target-arrow-shape": "triangle",
                                        "line-color": "#94a3b8",
                                        "target-arrow-color": "#94a3b8",
                                        "width": 1.5,
                                    },
                                },
                                {
                                    "selector": "edge.active",
                                    "style": {
                                        "line-color": "#0f766e",
                                        "target-arrow-color": "#0f766e",
                                        "width": 3,
                                    },
                                },
                                {
                                    "selector": "edge.persistence",
                                    "style": {"line-style": "dashed"},
                                },
                                {
                                    "selector": "edge.recrossing",
                                    "style": {
                                        "line-color": "#ea580c",
                                        "target-arrow-color": "#ea580c",
                                    },
                                },
                            ],
                        ),
                        html.P(
                            "点击事件菱形或下表行，会直接在上方局部轨迹查看器中载入该事件。",
                            className="rs-step-note mt-2",
                        ),
                        html.Div(
                            _grid(
                                "molecule-lineage-event-grid",
                                page_size=20,
                                row_selectable="single",
                            ),
                            className="rs-grid-wrap",
                        ),
                    ],
                    id="molecule-lineage-results",
                    style={"display": "none"},
                ),
            ],
            className="p-3",
        ),
        className="rs-card rs-molecule-lineage-card",
        id="molecule-lineage-card",
        style={"display": "none"},
    )


def _dft_geometry_card() -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Div("派生计算结构", className="rs-step-kicker"),
                                html.H6("DFT 初始几何", className="rs-card-title mb-0"),
                            ],
                            className="rs-step-heading",
                        ),
                        html.Span(
                            "不是过渡态或完整量化作业",
                            className="rs-type-map-caption",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    "只对具有精确 Molecular Evidence 的 matched 事件开放。",
                    id="event-dft-alert",
                    className="rs-step-note",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("反应物 · before_timestep"),
                                dcc.Checklist(
                                    id="event-dft-reactants",
                                    options=[],
                                    value=[],
                                    className="rs-dft-participants",
                                ),
                            ],
                            className="rs-dft-side",
                        ),
                        html.Div(
                            [
                                dbc.Label("产物 · after_timestep"),
                                dcc.Checklist(
                                    id="event-dft-products",
                                    options=[],
                                    value=[],
                                    className="rs-dft-participants",
                                ),
                            ],
                            className="rs-dft-side",
                        ),
                    ],
                    className="rs-dft-side-grid",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("输出方式", className="mb-1"),
                                dcc.RadioItems(
                                    id="event-dft-layout",
                                    options=[
                                        {"label": "合并复合物", "value": "combined"},
                                        {"label": "每个分子", "value": "separate"},
                                        {"label": "两者", "value": "both"},
                                    ],
                                    value="combined",
                                    inline=True,
                                    className="rs-compact-radio",
                                ),
                            ],
                        ),
                        dcc.Checklist(
                            id="event-dft-unit-confirmation",
                            options=[
                                {
                                    "label": "我确认源轨迹坐标单位为 Å",
                                    "value": "angstrom",
                                }
                            ],
                            value=[],
                            className="rs-dft-unit-confirmation",
                        ),
                    ],
                    className="rs-dft-options",
                ),
                html.Details(
                    [
                        html.Summary("可选：电荷与自旋多重度"),
                        html.Div(
                            "留空表示 unspecified；软件不会自动推断。",
                            className="rs-step-note",
                        ),
                        html.Div(id="event-dft-electronic-states"),
                    ],
                    className="rs-dft-electronic-details",
                ),
                html.Div(
                    [
                        dbc.Button(
                            "预检并生成预览",
                            id="event-dft-preview-btn",
                            color="success",
                            size="sm",
                        ),
                        dbc.Button(
                            "下载 DFT 几何 ZIP",
                            id="event-dft-download-btn",
                            color="primary",
                            size="sm",
                            disabled=True,
                        ),
                        dcc.Download(id="event-dft-download"),
                    ],
                    className="d-flex gap-2 flex-wrap mt-2",
                ),
                html.Div(id="event-dft-validation", className="mt-2"),
                html.Div(
                    [
                        html.Div(id="event-dft-summary", className="rs-stat-row"),
                        html.Div(
                            [
                                dcc.Dropdown(
                                    id="event-dft-preview-file",
                                    options=[],
                                    value=None,
                                    clearable=False,
                                ),
                                dcc.Clipboard(
                                    id="event-dft-copy",
                                    target_id="event-dft-preview-text",
                                    title="复制 XYZ",
                                ),
                            ],
                            className="rs-dft-preview-toolbar",
                        ),
                        html.Pre(
                            id="event-dft-preview-text",
                            className="rs-dft-preview-text",
                        ),
                    ],
                    id="event-dft-preview-panel",
                    style={"display": "none"},
                ),
            ],
            className="p-2",
        ),
        className="rs-card rs-dft-card",
        id="event-dft-card",
        style={"display": "none"},
    )


def _trajectory_page() -> html.Div:
    ovito_capability = svc.ovito_launch_capability()
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
                                                dbc.Button(
                                                    "下载键变距离 CSV",
                                                    id="event-distances-csv-btn",
                                                    color="secondary",
                                                    size="sm",
                                                    outline=True,
                                                ),
                                                dbc.Button("下载子轨迹", id="event-trajectory-btn", color="secondary", size="sm", outline=True),
                                                dbc.Button(
                                                    "在 OVITO 中打开",
                                                    id="event-ovito-open-btn",
                                                    color="secondary",
                                                    size="sm",
                                                    outline=True,
                                                    disabled=not bool(
                                                        ovito_capability.get("available")
                                                    ),
                                                    title=(
                                                        "远程模式仅支持下载"
                                                        if ovito_capability.get("mode") == "remote"
                                                        else "设置 REACNET_SCOPE_OVITO_EXECUTABLE 可指定 OVITO"
                                                    ),
                                                ),
                                                dbc.Button("下载 OVITO 脚本", id="event-ovito-btn", color="secondary", size="sm", outline=True),
                                                dbc.Button("下载 VMD 脚本", id="event-vmd-btn", color="secondary", size="sm", outline=True),
                                                dcc.Download(id="event-package-download"),
                                                dcc.Download(id="event-frames-csv-download"),
                                                dcc.Download(id="event-distances-csv-download"),
                                                dcc.Download(id="event-trajectory-download"),
                                                dcc.Download(id="event-ovito-download"),
                                                dcc.Download(id="event-vmd-download"),
                                                html.Div(
                                                    id="event-ovito-launch-status",
                                                    className="rs-inline-status",
                                                ),
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
        [source_card, viewer_card, _dft_geometry_card(), _molecule_lineage_card()],
        className="rs-page",
        id="page-trajectory",
    )


def _species_fate_page() -> html.Div:
    """Four-step query surface for bounded descendant evidence traversal."""

    endpoint_table = dash_table.DataTable(
        id="fate-endpoints-table",
        columns=[
            {"name": "Endpoint category", "id": "category", "editable": True},
            {
                "name": "Exact Species",
                "id": "species",
                "presentation": "dropdown",
                "editable": True,
            },
        ],
        data=[{"category": "", "species": ""}],
        editable=True,
        row_deletable=True,
        dropdown={"species": {"options": []}},
        style_table={"overflowX": "auto"},
    )
    return html.Div(
        [
            html.Div(
                [
                    html.Div([html.Span("1"), html.Strong("Dataset & Target")], className="rs-event-path-step is-active"),
                    html.Div([html.Span("2"), html.Strong("Fate Definition")], className="rs-event-path-step"),
                    html.Div([html.Span("3"), html.Strong("Observation & Limits")], className="rs-event-path-step"),
                    html.Div([html.Span("4"), html.Strong("Run & Results")], className="rs-event-path-step"),
                ],
                className="rs-event-path-stepper",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(dbc.CardBody([
                            html.H5("1. Dataset & Target"),
                            html.P("分析仅作用于当前 Dataset/Replicate。", className="rs-step-note"),
                            dbc.Label("Target Species"),
                            dcc.Dropdown(id="fate-target-species", options=[], placeholder="从 continuity catalog 选择"),
                        ]), className="rs-card h-100"),
                        md=6,
                    ),
                    dbc.Col(
                        dbc.Card(dbc.CardBody([
                            html.H5("2. Fate Definition"),
                            html.P("类别必须互斥；每行选择一个精确 Species，同名类别可占多行。", className="rs-step-note"),
                            endpoint_table,
                            dbc.Button("添加终点 Species", id="fate-add-endpoint-btn", color="secondary", outline=True, size="sm", className="mt-2"),
                        ]), className="rs-card h-100"),
                        md=6,
                    ),
                ],
                className="g-3",
            ),
            dbc.Card(
                dbc.CardBody([
                    html.H5("3. Observation & Limits"),
                    dbc.Row([
                        dbc.Col([dbc.Label("Anchor policy"), dcc.Dropdown(
                            id="fate-anchor-mode",
                            value="heavy_atoms",
                            clearable=False,
                            options=[
                                {"label": "全部非氢原子（默认）", "value": "heavy_atoms"},
                                {"label": "全部原子", "value": "all_atoms"},
                                {"label": "指定元素", "value": "elements"},
                                {"label": "指定 Atom IDs", "value": "atom_ids"},
                            ],
                        )], md=3),
                        dbc.Col([dbc.Label("Atom ID→Element"), dbc.Textarea(id="fate-atom-elements", placeholder="每行：12=C", rows=2)], md=3),
                        dbc.Col([dbc.Label("Anchor elements / Atom IDs"), dbc.Input(id="fate-anchor-values", placeholder="C,O 或 12,18")], md=3),
                        dbc.Col([dbc.Label("Minimum follow-up (frames)"), dbc.Input(id="fate-min-followup", type="number", min=0, step=1, value=0)], md=3),
                    ], className="g-2"),
                    dbc.Row([
                        dbc.Col([dbc.Label("Formation start"), dbc.Input(id="fate-formation-start", type="number", min=0, step=1, value=0)], md=2),
                        dbc.Col([dbc.Label("Formation end"), dbc.Input(id="fate-formation-end", type="number", min=0, step=1, placeholder="数据末端")], md=2),
                        dbc.Col([dbc.Label("Follow-up end"), dbc.Input(id="fate-followup-end", type="number", min=0, step=1, placeholder="数据末端")], md=2),
                        dbc.Col([dbc.Label("Events / episode"), dbc.Input(id="fate-max-events", type="number", min=1, step=1, value=10000)], md=2),
                        dbc.Col([dbc.Label("Active branches"), dbc.Input(id="fate-max-branches", type="number", min=1, step=1, value=1000)], md=2),
                        dbc.Col([dbc.Label("Retained details"), dbc.Input(id="fate-detail-limit", type="number", min=0, step=1, value=1000)], md=2),
                        dbc.Col([dbc.Label("Global episode limit"), dbc.Input(id="fate-global-episode-limit", type="number", min=1, step=1, placeholder="不限制")], md=2),
                    ], className="g-2 mt-1"),
                ]),
                className="rs-card mt-3",
            ),
            dbc.Card(
                dbc.CardBody([
                    html.Div([
                        html.Div([html.H5("4. Run & Results", className="mb-1"), html.P("统计始终基于完整扫描；episode 级资源边界转为明确删失。", className="rs-step-note mb-0")]),
                        dbc.Button("运行 Species Fate Analysis", id="fate-run-btn", color="primary"),
                    ], className="d-flex justify-content-between align-items-center gap-3"),
                    dbc.Alert(id="fate-error", color="danger", is_open=False, className="mt-3"),
                    dcc.Loading(html.Div(id="fate-summary", className="mt-3"), type="circle"),
                    dcc.Tabs(id="fate-results-tabs", value="signatures", children=[
                        dcc.Tab(label="Fate Signatures", value="signatures", children=[html.Div(id="fate-signatures", className="pt-3")]),
                        dcc.Tab(label="Endpoint Marginals", value="marginals", children=[html.Div(id="fate-marginals", className="pt-3")]),
                        dcc.Tab(label="Pathways", value="pathways", children=[html.Div(id="fate-pathways", className="pt-3")]),
                        dcc.Tab(label="Time Distributions", value="times", children=[html.Div(id="fate-times", className="pt-3")]),
                        dcc.Tab(label="Episodes / Raw Evidence", value="episodes", children=[html.Div(id="fate-episodes", className="pt-3")]),
                    ], className="mt-3"),
                    html.Div([
                        dbc.Button("下载 fate-result.json", id="fate-download-json-btn", color="secondary", outline=True),
                        dbc.Button("下载 fate-tables.zip", id="fate-download-tables-btn", color="secondary", outline=True),
                    ], className="d-flex gap-2 mt-3"),
                    dcc.Download(id="fate-download-json"),
                    dcc.Download(id="fate-download-tables"),
                ]),
                className="rs-card mt-3",
            ),
        ],
        className="rs-page",
        id="page-species-fate",
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
                [html.Span("2"), html.Div([html.Strong("输入路径"), html.Small("完整 Reaction Type 序列")])],
                id="event-path-progress-2",
                className="rs-event-path-step",
            ),
            html.Div(
                [html.Span("3"), html.Div([html.Strong("确认运行"), html.Small("检查参数")])],
                id="event-path-progress-3",
                className="rs-event-path-step",
            ),
            html.Div(
                [html.Span("4"), html.Div([html.Strong("查看证据"), html.Small("结论与事件链")])],
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
                            "公共前缀后应存在 .timeline.h5，或 .reactionevent.csv"
                            " 和 .molecules.csv；系统会在进入下一步前检查。",
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
                    "每行输入一个完整 Reaction Type，按预期发生顺序排列。"
                    "系统不会补全、改写或排名路径。",
                    className="rs-step-note",
                ),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("Reaction Type 序列（2–8 步）"),
                                dcc.Textarea(
                                    id="event-path-reaction-sequence",
                                    value="",
                                    placeholder=(
                                        "A + B -> C\n"
                                        "C -> D\n"
                                        "D + E -> F"
                                    ),
                                    className="rs-event-path-sources",
                                ),
                            ],
                            className="rs-pathway-field",
                        ),
                        html.Div(
                            id="event-path-sequence-preview",
                            children="尚未输入路径。",
                            className="rs-event-path-preview",
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
                            "验证这条路径",
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
                                html.H5("路径验证结果", className="mb-0"),
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
                                    "导出路径表 CSV",
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
        [signatures, audit],
        id="event-path-results",
        className="rs-event-path-results",
        style={"display": "none"},
    )
    return html.Div(
        [controls, results],
        className="rs-event-path-panel",
    )


def _pathway_page() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Span("事件证据", className="rs-step-kicker"),
                    html.H3("路径验证", className="mb-1"),
                    html.P(
                        "这里不自动猜测或排名反应路径。请给出要核查的完整 "
                        "Reaction Type 序列，系统只判断轨迹中是否存在满足严格时间、"
                        "同一分子实例和原子 ID 连续性的完整事件链。结果分为“有证据”、"
                        "“未观察到”和“证据不足”。",
                        className="rs-step-note mb-0",
                    ),
                ],
                className="rs-page-heading",
            ),
            _event_path_analysis_panel(),
        ],
        id="page-pathway",
        className="rs-page rs-pathway-page",
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
) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(label, className="rs-index-name"),
                ]
            ),
            html.Div(
                "正在检查…",
                id=f"data-prep-{kind}-status",
                className="rs-index-status-cell",
            ),
            dbc.Button(
                "准备索引",
                id=f"data-prep-{kind}-btn",
                color="secondary",
                size="sm",
                outline=True,
                className="rs-index-action",
            ),
        ],
        className="rs-index-readiness-row",
    )


def _data_cache_management_card() -> Any:
    command_rows = (
        (
            "事件索引",
            "data-prep-event-command",
            "data-prep-event-copy",
            "复制事件索引 CLI 命令",
        ),
        (
            "轨迹帧索引",
            "data-prep-trajectory-command",
            "data-prep-trajectory-copy",
            "复制轨迹索引 CLI 命令",
        ),
        (
            "元素分布索引",
            "data-prep-composition-command",
            "data-prep-composition-copy",
            "复制元素分布索引 CLI 命令",
        ),
    )
    return html.Section(
        [
            dcc.Store(id="data-prep-cancel-result", storage_type="memory"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                "当前数据集的分析索引",
                                className="rs-data-section-title",
                            ),
                            html.Div(
                                "索引只加速或启用分析，不修改原始数据。",
                                className="rs-card-subtitle",
                            ),
                        ]
                    ),
                    html.Div(
                        "空闲时停止自动刷新",
                        id="data-prep-refresh-label",
                        className="rs-index-refresh-label",
                    ),
                ],
                className="rs-index-management-header",
            ),
            html.Div(id="data-prep-status-alert"),
            html.Div(
                [
                    _data_index_readiness_row("event", "事件检索"),
                    _data_index_readiness_row("trajectory", "轨迹证据"),
                    _data_index_readiness_row(
                        "composition",
                        "物种丰度 / 元素分布",
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
            html.Div(
                id="data-prep-action-alert",
                className="rs-index-action-alert",
            ),
            html.Details(
                [
                    html.Summary(
                        [
                            html.Span("高级维护"),
                            html.Small("任务记录 · Workspace · CLI · 清理索引"),
                        ]
                    ),
                    html.Div(
                        [
                            html.Div(
                                id="data-preparation-tasks",
                                className="rs-preparation-task-list",
                                **{"aria-live": "polite"},
                            ),
                            html.Section(
                                [
                                    html.H4("Workspace 与诊断"),
                                    html.Div(
                                        id="data-prep-cache-meta",
                                        className="rs-cache-meta",
                                    ),
                                    *[
                                        html.Div(
                                            [
                                                html.Div(
                                                    label,
                                                    className="rs-command-label",
                                                ),
                                                html.Div(
                                                    [
                                                        html.Code(
                                                            id=command_id,
                                                            className="small flex-grow-1",
                                                        ),
                                                        dcc.Clipboard(
                                                            id=copy_id,
                                                            title=copy_title,
                                                        ),
                                                    ],
                                                    className="rs-command-line",
                                                ),
                                            ],
                                            className="rs-maintenance-command",
                                        )
                                        for label, command_id, copy_id, copy_title in command_rows
                                    ],
                                ],
                                className="rs-maintenance-group",
                            ),
                            html.Section(
                                [
                                    html.H4("清理派生索引"),
                                    html.Div(
                                        id="data-prep-clear-alert",
                                        className="rs-index-clear-alert",
                                        **{
                                            "aria-live": "polite",
                                            "role": "status",
                                        },
                                    ),
                                    html.Div(
                                        "清理后对应分析将不可用，直到重新建立；不会删除 RNG 原始输出。",
                                        className="rs-danger-copy",
                                    ),
                                    html.Div(
                                        "仅已有、失效或正在构建的索引可操作；任务运行中时，点击会给出安全停止步骤。",
                                        id="data-clear-help",
                                        className="rs-danger-help",
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
                                                title="清理事件检索的派生索引",
                                            ),
                                            dbc.Button(
                                                "清理轨迹索引",
                                                id="data-clear-trajectory-btn",
                                                color="danger",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                                title="清理轨迹证据的派生索引",
                                            ),
                                            dbc.Button(
                                                "清理组成索引",
                                                id="data-clear-composition-btn",
                                                color="danger",
                                                size="sm",
                                                outline=True,
                                                disabled=True,
                                                title="清理元素分布的派生索引",
                                            ),
                                        ],
                                        className="rs-danger-actions",
                                    ),
                                ],
                                className="rs-maintenance-group rs-danger-body",
                            ),
                        ],
                        className="rs-index-advanced-body",
                    ),
                ],
                id="data-index-advanced",
                className="rs-index-advanced",
            ),
        ],
        id="data-cache-management",
        className="rs-data-cache-workspace rs-index-management",
    )


def _data_management_page() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        id="data-load-feedback",
                        **{"aria-live": "polite"},
                    ),
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
                                                id="data-artifacts",
                                                className="rs-data-artifacts",
                                            ),
                                            html.Div(
                                                [
                                                    dbc.Button(
                                                        "选择数据集",
                                                        id="data-empty-pick-btn",
                                                        color="primary",
                                                        className="rs-empty-dataset-action",
                                                    ),
                                                    dbc.Button(
                                                        "刷新状态",
                                                        id="data-current-refresh-btn",
                                                        color="secondary",
                                                        outline=True,
                                                        className="rs-current-refresh-action",
                                                    ),
                                                    dbc.Button(
                                                        "更换数据集",
                                                        id="data-change-pick-btn",
                                                        color="primary",
                                                        className="rs-change-dataset-action",
                                                    ),
                                                ],
                                                className="rs-data-summary-actions",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        id="data-prep-basic-status",
                                                    ),
                                                    html.Div(
                                                        id="data-recent-datasets",
                                                    ),
                                                ],
                                                hidden=True,
                                            ),
                                        ],
                                        className="rs-data-summary-main",
                                    ),
                                ],
                                className="rs-data-summary-panel",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        id="data-next-action",
                                        className="rs-data-next-action",
                                    ),
                                    dbc.Button(
                                        "开始物种检索",
                                        id="data-open-species-btn",
                                        color="primary",
                                        className="rs-data-primary-analysis-action",
                                    ),
                                ],
                                className="rs-data-next-step-panel",
                                **{"aria-label": "下一步"},
                            ),
                            _data_cache_management_card(),
                        ],
                        id="data-overview-view",
                        className="rs-data-view",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                "数据工作区",
                                                className="rs-data-section-kicker",
                                            ),
                                            html.H2(
                                                "选择一个数据集",
                                                id="data-browser-title",
                                                className="rs-browser-title",
                                                tabIndex=-1,
                                            ),
                                            html.P(
                                                "从最近使用中选择，或输入路径浏览运行 ReacNet Scope 的计算机。",
                                                className="rs-browser-intro",
                                            ),
                                        ]
                                    ),
                                    dbc.Button(
                                        "返回索引管理",
                                        id="data-browser-index-btn",
                                        color="secondary",
                                        size="sm",
                                        outline=True,
                                    ),
                                ],
                                className="rs-browser-heading",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            dbc.Button(
                                                "上一级",
                                                id="dir-browser-back-btn",
                                                color="secondary",
                                                outline=True,
                                                disabled=True,
                                                className="rs-browser-back-button",
                                            ),
                                            dbc.Label(
                                                "当前路径",
                                                html_for="dir-browser-path-input",
                                                className="visually-hidden",
                                            ),
                                            dbc.Input(
                                                id="dir-browser-path-input",
                                                placeholder="输入数据文件夹或完整数据集路径",
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
                                        "也可粘贴数据集公共前缀；路径属于运行 ReacNet Scope 的计算机。",
                                        id="dir-browser-path-help",
                                        className="rs-browser-path-help",
                                    ),
                                ],
                                id="dir-browser-expert-path",
                                className="rs-browser-location-bar",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.H3(
                                                "最近使用",
                                                className="rs-browser-section-title",
                                            ),
                                            html.Span(
                                                "选择后可直接加载",
                                                className="rs-browser-item-count",
                                            ),
                                        ],
                                        className="rs-browser-section-heading",
                                    ),
                                    html.Div(
                                        id="dir-browser-recent-datasets",
                                        className="rs-browser-recent",
                                    ),
                                ],
                                id="dir-browser-recent-section",
                                className="rs-browser-recent-section",
                            ),
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            dbc.Label(
                                                "筛选数据集和文件夹",
                                                html_for="dir-browser-filter-input",
                                                className="visually-hidden",
                                            ),
                                            dbc.Input(
                                                id="dir-browser-filter-input",
                                                type="search",
                                                debounce=True,
                                                placeholder="筛选当前目录中的数据集或文件夹",
                                            ),
                                            dbc.Button(
                                                "清除",
                                                id="dir-browser-filter-clear-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                            ),
                                        ],
                                        id="dir-browser-filter-row",
                                        className="rs-browser-filter-row",
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
                                            html.Span(
                                                "请先选择一个候选数据集；只有点击“加载并使用”才会切换当前数据集。",
                                                id="data-apply-reason",
                                                className="rs-browser-submit-reason",
                                                **{"aria-live": "polite"},
                                            ),
                                            dbc.Button(
                                                "返回",
                                                id="dir-browser-cancel-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                            ),
                                            dbc.Button(
                                                "加载并使用",
                                                id="data-apply-btn",
                                                color="primary",
                                                disabled=True,
                                            ),
                                        ],
                                        className="rs-browser-submit-row",
                                    ),
                                ],
                                className="rs-browser-workspace",
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
        className="rs-page rs-data-page active",
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


_QUERY_CONTROL_PREFIXES = (
    "species-",
    "rxn-",
    "evolution-",
    "element-distribution-",
    "event-",
    "pathway-",
)
_PERSISTABLE_QUERY_CONTROLS = (
    dcc.Input,
    dcc.Textarea,
    dcc.Dropdown,
    dcc.RadioItems,
    dcc.Checklist,
    dcc.Slider,
    dbc.Checkbox,
)
_DATASET_BOUND_CONTROL_IDS = {
    "event-extract-id",
    "event-frame-slider",
    "event-path-additional-sources",
    "event-path-current-replicate",
    "event-path-occurrence-selector",
    "evolution-species-file",
    "evolution-species-files",
    "evolution-species-picker",
    "evolution-timestep",
    "element-distribution-timestep",
    "batch-condition-selector",
}


def _enable_query_session_persistence(component: Any) -> None:
    """Keep query inputs/preferences tab-local across a browser reload."""

    component_id = getattr(component, "id", None)
    if (
        isinstance(component_id, str)
        and component_id.startswith(_QUERY_CONTROL_PREFIXES)
        and component_id not in _DATASET_BOUND_CONTROL_IDS
        and isinstance(component, _PERSISTABLE_QUERY_CONTROLS)
    ):
        component.persistence = True
        component.persistence_type = "session"
    children = getattr(component, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None:
            _enable_query_session_persistence(child)


def build_layout() -> html.Div:
    """Build the full application layout."""
    layout = html.Div(
        [
            _topbar(),
            html.Div(
                [
                    _sidebar(),
                    html.Div(
                        [
                            _page_header(),
                            _species_page(),
                            _reactions_page(),
                            _pathway_page(),
                            _evolution_page(),
                            _element_distribution_page(),
                            _events_page(),
                            _species_fate_page(),
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
            html.Div(
                id="global-dataset-notice",
                className="rs-global-dataset-notice",
                role="status",
                **{"aria-live": "polite", "aria-atomic": "true"},
            ),
            dcc.Interval(
                id="global-dataset-notice-timeout",
                interval=8000,
                n_intervals=0,
                disabled=True,
            ),
            html.Div(id="dataset-focus-sink", hidden=True),
            dcc.Store(id="dir-browser-path", storage_type="memory", data=""),
            dcc.Store(id="dataset-browser-candidate", storage_type="memory", data=None),
            dcc.Store(id="recent-datasets", storage_type="local", data=[]),
            dcc.Store(
                id="app-store",
                storage_type="memory",
                data={**cb.initial_store(), "context_state": "restoring"},
            ),
            dcc.Store(
                id="dataset-session-store",
                storage_type="session",
                data=cb.initial_store(),
            ),
            dcc.Store(id="page-store", storage_type="session", data={"page": START_PAGE}),
            dcc.Store(id="dataset-switch-transaction", storage_type="memory", data={}),
            dcc.Store(id="dataset-switch-navigation", storage_type="memory", data={}),
            dcc.Store(id="dataset-context-commit", storage_type="memory", data={}),
            dcc.Store(id="dataset-focus-request", storage_type="memory", data={}),
            dcc.Store(id="dataset-restore-result", storage_type="memory", data={}),
            dcc.Store(id="preparation-task-snapshot", storage_type="local", data=[]),
            html.Div(
                [
                    dcc.Store(
                        id={"type": "dataset-bound-operation", "name": name},
                        storage_type="memory",
                        data=False,
                    )
                    for name in (
                        "species",
                        "species-structures",
                        "species-detail",
                        "reactions",
                        "evolution",
                        "element-distribution",
                        "events",
                        "species-fate",
                        "trajectory",
                        "molecule-lineage",
                        "path-verification",
                        "pathways",
                    )
                ],
                hidden=True,
            ),
            dcc.Interval(
                id="dataset-session-restore",
                interval=100,
                n_intervals=0,
                max_intervals=1,
            ),
            dcc.Store(id="species-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(
                id="species-workspace-stage",
                storage_type="memory",
                data="results",
            ),
            dcc.Store(id="rxn-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="evolution-payload-store", storage_type="memory", data=None),
            dcc.Store(id="element-distribution-payload-store", storage_type="memory", data=None),
            dcc.Store(id="event-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="fate-result-store", storage_type="memory", data=None),
            dcc.Store(id="data-clear-kind-store", storage_type="memory", data={}),
            dcc.Interval(id="data-prep-refresh", interval=2000, n_intervals=0, disabled=True),
            dcc.Interval(
                id="preparation-task-refresh",
                interval=2000,
                n_intervals=0,
                disabled=True,
            ),
            dcc.Store(id="event-selected-store", storage_type="memory", data=None),
            dcc.Store(id="event-viewer-store", storage_type="memory", data=None),
            dcc.Store(id="event-dft-store", storage_type="memory", data=None),
            dcc.Store(id="molecule-lineage-store", storage_type="memory", data=None),
            dcc.Store(
                id="molecule-lineage-drilldown-store",
                storage_type="memory",
                data=None,
            ),
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
    _enable_query_session_persistence(layout)
    return layout


def create_app() -> dash.Dash:
    """Create and configure the Dash application instance."""
    app = dash.Dash(
        __name__,
        # All production assets are vendored under ``assets/`` so the
        # workbench remains usable on offline analysis hosts.
        external_stylesheets=[],
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
        workspace_text = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
        workspace_path = (
            Path(workspace_text).expanduser() if workspace_text else None
        )
        workspace_ready = bool(
            not workspace_path
            or (
                workspace_path.exists()
                and workspace_path.is_dir()
                and os.access(workspace_path, os.W_OK)
            )
        )
        try:
            app_version = version("reacnet-scope")
        except PackageNotFoundError:
            app_version = "development"
        warnings: list[str] = []
        if not workspace_ready:
            warnings.append("configured Dataset Workspace root is not writable")
        return jsonify(
            {
                "ok": True,
                "service": "reacnet-scope",
                "version": app_version,
                "uptime_seconds": round(time.time() - _PROCESS_STARTED_AT, 3),
                "workspace_mode": (
                    "configured-root" if workspace_path else "dataset-sidecar"
                ),
                "workspace_root": str(workspace_path) if workspace_path else "",
                "workspace_ready": workspace_ready,
                "allowed_roots": [str(path) for path in svc.ALLOWED_ROOTS],
                "warnings": warnings,
            }
        )
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_server(*, host: str, port: int, debug: bool = False) -> int:
    """Run the supported Dash product surface."""
    app = create_app()
    print(f"[ReacNet-Scope-Dash] http://{host}:{port}")
    print("[ReacNet-Scope-Dash] Press Ctrl+C to stop")
    try:
        app.run(host=host, port=port, debug=debug)
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ReacNet Scope Dash WebUI V1")
    ap.add_argument("--host", default="127.0.0.1", help="bind host")
    ap.add_argument("--port", type=int, default=8060, help="bind port")
    ap.add_argument("--debug", action="store_true", help="enable Dash debug mode")
    args = ap.parse_args()
    return run_server(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
