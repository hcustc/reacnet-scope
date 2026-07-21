"""Dash WebUI V1 entry point for ReacNet Scope.

Runs in parallel with the legacy WebUI at ``scripts.webapp.server``.

Usage::

    uv run reacnet-scope-web-dash --host 127.0.0.1 --port 8060
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import dash
import dash_bootstrap_components as dbc
import dash_cytoscape as cyto
import plotly.graph_objects as go
from dash import Input, Output, State, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate

# Ensure project root is importable when run via ``python -m`` or directly.
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from scripts.webapp_dash import callbacks as cb  # noqa: E402
from scripts.webapp_dash import services as svc  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_IDS = ["species", "transitions", "reactions", "intermediate", "evolution", "carbon", "events", "network"]
PAGE_LABELS = {
    "species": "物种检索",
    "transitions": "转化关系",
    "reactions": "反应式检索",
    "intermediate": "中间体筛选",
    "evolution": "时间演化",
    "carbon": "碳数演化",
    "events": "事件证据",
    "network": "观察网络",
}
DEFAULT_PAGE = "species"


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _topbar() -> dbc.Container:
    return dbc.Container(
        [
            html.Div(
                [
                    html.Span("ReacNet Scope", className="rs-brand"),
                    html.Span("·", style={"color": "#9ca3af"}),
                    html.Span("Dash WebUI V1", style={"color": "#6b7280", "fontSize": 12}),
                ],
                className="d-flex align-items-center gap-2",
            ),
            html.Div(
                [
                    html.Div(
                        [html.Span("目录: "), html.Span(id="topbar-folder", children="未选择")],
                        className="rs-meta-item",
                    ),
                    html.Div(
                        [html.Span("运行组: "), html.Span(id="topbar-rungroup", children="未选择")],
                        className="rs-meta-item",
                    ),
                    html.Span(id="topbar-status", className="rs-badge rs-bad", children="未加载数据"),
                ],
                className="rs-meta",
            ),
            dbc.Button(
                "管理数据",
                id="open-data-modal",
                color="primary",
                size="sm",
                className="ms-auto",
            ),
        ],
        className="rs-topbar",
        fluid=True,
    )


def _nav() -> html.Div:
    items = [
        html.Button(
            [html.Span(label)],
            id=f"nav-{pid}",
            className=f"rs-nav-item{(' active' if pid == DEFAULT_PAGE else '')}",
            n_clicks=0,
        )
        for pid, label in PAGE_LABELS.items()
    ]
    return html.Div(items, className="rs-nav")


def _detail_panel() -> html.Div:
    return html.Div(
        [
            html.H6("当前选中物种"),
            html.Div(id="detail-empty", className="rs-empty", children="未选择物种"),
            html.Div(id="detail-body", style={"display": "none"}, children=[]),
        ],
        className="rs-detail",
        id="detail-panel",
    )


def _grid(grid_id: str) -> dash_table.DataTable:
    return dash_table.DataTable(
        id=grid_id,
        columns=[],
        data=[],
        selected_rows=[],
        row_selectable="single",
        sort_action="native",
        filter_action="native",
        page_action="none",
        style_table={"height": "100%", "overflow": "auto"},
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
            {"if": {"state": "selected"}, "backgroundColor": "#eef2ff", "border": "1px solid #c7d2fe"},
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
                            dbc.Label("类型", className="mb-0", style={"fontSize": 12}),
                            dcc.Dropdown(
                                id="species-query-kind",
                                options=[
                                    {"label": "自动", "value": "auto"},
                                    {"label": "分子式", "value": "formula"},
                                    {"label": "SMILES", "value": "smiles"},
                                    {"label": "质量数", "value": "mass"},
                                ],
                                value="auto",
                                clearable=False,
                                style={"width": 120},
                            ),
                            dbc.Label("查询内容", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(
                                id="species-query",
                                value="",
                                placeholder="例如 H2O / [H][O] / 17.00274",
                                className="rs-grow",
                                debounce=True,
                                type="text",
                            ),
                            dbc.Label("质量容差", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(
                                id="species-mass-tol",
                                value="0.5",
                                type="number",
                                style={"width": 90},
                            ),
                            dbc.Button("查询", id="species-search-btn", color="primary", size="sm"),
                            dbc.Button("导出 CSV", id="species-csv-btn", color="secondary", size="sm", outline=True),
                            dcc.Download(id="species-csv-download"),
                        ],
                        className="rs-query-row",
                    ),
                ],
                className="p-2",
            )
        ],
        className="rs-card",
    )

    grid_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(id="species-alert"),
                    dcc.Loading(
                        html.Div(
                            _grid("species-grid"),
                            className="rs-grid-wrap",
                        ),
                        type="circle",
                    ),
                ],
                className="p-2 rs-flex-fill",
            )
        ],
        className="rs-card rs-flex-fill",
    )

    return html.Div([query_card, grid_card], className="rs-page active", id="page-species")


def _transitions_page() -> html.Div:
    query_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(
                        [
                            dbc.Label("中心物种", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(
                                id="transitions-smiles",
                                value="",
                                placeholder="从物种检索页面自动继承",
                                className="rs-grow",
                                readOnly=True,
                            ),
                            dbc.Label("方向", className="mb-0", style={"fontSize": 12}),
                            dcc.Dropdown(
                                id="transitions-direction",
                                options=[
                                    {"label": "双向", "value": "both"},
                                    {"label": "上游 (消耗)", "value": "consume"},
                                    {"label": "下游 (生成)", "value": "produce"},
                                ],
                                value="both",
                                clearable=False,
                                style={"width": 160},
                            ),
                            dbc.Button("查询", id="transitions-search-btn", color="primary", size="sm"),
                            dbc.Button("导出 CSV", id="transitions-csv-btn", color="secondary", size="sm", outline=True),
                            dcc.Download(id="transitions-csv-download"),
                        ],
                        className="rs-query-row",
                    ),
                ],
                className="p-2",
            )
        ],
        className="rs-card",
    )

    grid_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(id="transitions-alert"),
                    dcc.Loading(
                        html.Div(
                            _grid("transitions-grid"),
                            className="rs-grid-wrap",
                        ),
                        type="circle",
                    ),
                ],
                className="p-2 rs-flex-fill",
            )
        ],
        className="rs-card rs-flex-fill",
    )

    return html.Div([query_card, grid_card], className="rs-page", id="page-transitions")


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
    )
    grid_card = dbc.Card(
        dbc.CardBody(
            [html.Div(id="rxn-alert"), dcc.Loading(html.Div(_grid("rxn-grid"), className="rs-grid-wrap"), type="circle")],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    return html.Div([query_card, grid_card], className="rs-page", id="page-reactions")


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
            [html.Div(id="inter-alert"), dcc.Loading(html.Div(_grid("inter-grid"), className="rs-grid-wrap"), type="circle")],
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
                            dbc.Label("目标物种/分子式", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(
                                id="evolution-targets",
                                value="",
                                placeholder="从物种检索自动继承，多个用逗号分隔",
                                className="rs-grow",
                            ),
                            dbc.Label("X 轴", className="mb-0", style={"fontSize": 12}),
                            dcc.Dropdown(
                                id="evolution-xaxis",
                                options=[
                                    {"label": "步数", "value": "step"},
                                    {"label": "ps", "value": "ps"},
                                    {"label": "ns", "value": "ns"},
                                ],
                                value="ps",
                                clearable=False,
                                style={"width": 100},
                            ),
                            dbc.Label("平滑", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(id="evolution-smooth", value="1", type="number", style={"width": 70}),
                            dbc.Button("绘制", id="evolution-search-btn", color="primary", size="sm"),
                            dbc.Button("导出 CSV", id="evolution-csv-btn", color="secondary", size="sm", outline=True),
                            dcc.Download(id="evolution-csv-download"),
                        ],
                        className="rs-query-row",
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
                    dcc.Loading(
                        html.Div(
                            dcc.Graph(id="evolution-graph", className="rs-chart", style={"height": "100%"}),
                            className="rs-grid-wrap",
                        ),
                        type="circle",
                    ),
                ],
                className="p-2 rs-flex-fill",
            )
        ],
        className="rs-card rs-flex-fill",
    )

    return html.Div([query_card, chart_card], className="rs-page", id="page-evolution")


def _carbon_page() -> html.Div:
    query_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        dbc.Label("X 轴", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="carbon-xaxis",
                            options=[
                                {"label": "步数", "value": "step"},
                                {"label": "ps", "value": "ps"},
                                {"label": "ns", "value": "ns"},
                            ],
                            value="ps",
                            clearable=False,
                            style={"width": 100},
                        ),
                        dbc.Label("Timestep(ps)", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-timestep", value="0.0001", type="number", style={"width": 105}),
                        dbc.Label("Mode", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="carbon-mode",
                            options=[
                                {"label": "exact", "value": "exact"},
                                {"label": "binned", "value": "binned"},
                                {"label": "topk", "value": "topk"},
                            ],
                            value="exact",
                            clearable=False,
                            style={"width": 110},
                        ),
                        dbc.Label("Top K", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-topk", value="12", type="number", style={"width": 76}),
                        dbc.Label("Max Exact", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-max-exact", value="24", type="number", style={"width": 86}),
                        dbc.Button("绘制", id="carbon-search-btn", color="primary", size="sm"),
                        dbc.Button("导出 CSV", id="carbon-csv-btn", color="secondary", size="sm", outline=True),
                        dbc.Button("导出 SVG", id="carbon-svg-btn", color="secondary", size="sm", outline=True),
                        dcc.Download(id="carbon-csv-download"),
                        dcc.Download(id="carbon-svg-download"),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    [
                        dbc.Label("Display Ranges", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-display-ranges", value="", placeholder="C1;C2;C24;C30+", className="rs-grow"),
                        dbc.Label("Merge Ranges", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-merge-ranges", value="", placeholder="Small:1-4;Growth:30+", className="rs-grow"),
                        dbc.Label("Bins", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-bins", value="", placeholder="1-4;5-15;16-30;31+", className="rs-grow"),
                    ],
                    className="rs-subquery-row",
                ),
                html.Div(
                    [
                        dbc.Label("Parent C", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-parent", value="", type="number", style={"width": 84}),
                        dbc.Label("Small Range", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-small", value="1-4", type="text", style={"width": 90}),
                        dbc.Label("Large", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-large", value="30", type="number", style={"width": 76}),
                        dbc.Label("Smoothing", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="carbon-smoothing",
                            options=[
                                {"label": "none", "value": "none"},
                                {"label": "rolling", "value": "rolling"},
                                {"label": "savgol", "value": "savgol"},
                            ],
                            value="none",
                            clearable=False,
                            style={"width": 120},
                        ),
                        dbc.Label("Window", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="carbon-smooth-window", value="5", type="number", style={"width": 76}),
                        dbc.Label("Layout", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="carbon-layout",
                            options=[{"label": "single", "value": "single"}, {"label": "subplots", "value": "subplots"}],
                            value="single",
                            clearable=False,
                            style={"width": 110},
                        ),
                    ],
                    className="rs-subquery-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    plot_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(id="carbon-alert"),
                html.Div(id="carbon-highlights", className="rs-stat-row"),
                dcc.Loading(
                    html.Div(
                        html.Iframe(id="carbon-svg-frame", srcDoc="", style={"border": "none", "width": "100%", "height": "100%"}),
                        className="rs-plot-frame",
                    ),
                    type="circle",
                ),
            ],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    return html.Div([query_card, plot_card], className="rs-page", id="page-carbon")


def _events_page() -> html.Div:
    species_card = dbc.Card(
        dbc.CardBody(
            [
                html.H6("物种事件", className="rs-card-title"),
                html.Div(
                    [
                        dbc.Label("目标", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-species-target", value="", placeholder="SMILES 或分子式", className="rs-grow"),
                        dbc.Label("匹配", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="event-match-mode",
                            options=[
                                {"label": "auto", "value": "auto"},
                                {"label": "SMILES", "value": "smiles"},
                                {"label": "Formula", "value": "formula"},
                            ],
                            value="auto",
                            clearable=False,
                            style={"width": 110},
                        ),
                        dbc.Label("事件", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="event-mode",
                            options=[
                                {"label": "出现", "value": "appear"},
                                {"label": "消失", "value": "disappear"},
                                {"label": "生成", "value": "production"},
                                {"label": "消耗", "value": "consumption"},
                                {"label": "峰值", "value": "peak"},
                                {"label": "非零", "value": "nonzero"},
                            ],
                            value="appear",
                            clearable=False,
                            style={"width": 110},
                        ),
                        dbc.Label("Before", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-before", value="3", type="number", style={"width": 76}),
                        dbc.Label("After", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-after", value="3", type="number", style={"width": 76}),
                        dbc.Label("Max", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-max", value="12", type="number", style={"width": 72}),
                        dbc.Button("定位", id="event-species-btn", color="primary", size="sm"),
                    ],
                    className="rs-query-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    reaction_card = dbc.Card(
        dbc.CardBody(
            [
                html.H6("反应事件", className="rs-card-title"),
                html.Div(
                    [
                        dbc.Label("反应式", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-reaction-text", value="", placeholder="A + B -> C + D", className="rs-grow"),
                        dbc.Label("Before", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-rxn-before", value="5", type="number", style={"width": 76}),
                        dbc.Label("After", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-rxn-after", value="5", type="number", style={"width": 76}),
                        dbc.Label("Max", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-rxn-max", value="12", type="number", style={"width": 72}),
                        dbc.Button("定位", id="event-rxn-btn", color="primary", size="sm"),
                        dbc.Label("event_id", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="event-extract-id", value="", placeholder="从结果表选择或粘贴", style={"width": 180}),
                        dbc.Button("抽取轨迹", id="event-extract-btn", color="success", size="sm"),
                        dbc.Button("导出 CSV", id="event-csv-btn", color="secondary", size="sm", outline=True),
                        dcc.Download(id="event-csv-download"),
                    ],
                    className="rs-query-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    grid_card = dbc.Card(
        dbc.CardBody(
            [html.Div(id="event-alert"), dcc.Loading(html.Div(_grid("event-grid"), className="rs-grid-wrap"), type="circle")],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    return html.Div([species_card, reaction_card, grid_card], className="rs-page", id="page-events")


def _network_page() -> html.Div:
    query_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(
                        [
                            dbc.Label("观察网络", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(
                                id="network-smiles",
                                value="",
                                placeholder="使用 .lammpstrj.table 构建全局观察网络",
                                className="rs-grow",
                                readOnly=True,
                            ),
                            dbc.Label("最小次数", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(id="network-min-count", value="1", type="number", style={"width": 76}),
                            dbc.Label("显示物种数", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(id="network-max-species", value="60", type="number", style={"width": 80}),
                            dbc.Label("边数", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(id="network-top-edges", value="40", type="number", style={"width": 70}),
                            dbc.Label("布局", className="mb-0", style={"fontSize": 12}),
                            dcc.Dropdown(
                                id="network-layout",
                                options=[
                                    {"label": "同心圆", "value": "concentric"},
                                    {"label": "力导向", "value": "cose"},
                                    {"label": "网格", "value": "grid"},
                                    {"label": "圆形", "value": "circle"},
                                    {"label": "树形", "value": "breadthfirst"},
                                ],
                                value="concentric",
                                clearable=False,
                                style={"width": 120},
                            ),
                            dbc.Button("构建", id="network-search-btn", color="primary", size="sm"),
                            dbc.Button("导出 PNG", id="network-png-btn", color="secondary", size="sm", outline=True),
                        ],
                        className="rs-query-row",
                    ),
                ],
                className="p-2",
            )
        ],
        className="rs-card",
    )

    cyto_card = dbc.Card(
        [
            dbc.CardBody(
                [
                    html.Div(id="network-alert"),
                    dcc.Loading(
                        html.Div(
                            cyto.Cytoscape(
                                id="network-cytoscape",
                                layout={"name": "concentric"},
                                elements=[],
                                style={"width": "100%", "height": "100%"},
                                className="rs-cytoscape",
                                stylesheet=[
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
                                ],
                            ),
                            className="rs-grid-wrap",
                        ),
                        type="circle",
                    ),
                ],
                className="p-2 rs-flex-fill",
            )
        ],
        className="rs-card rs-flex-fill",
    )

    return html.Div([query_card, cyto_card], className="rs-page", id="page-network")


def _data_modal() -> dbc.Modal:
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("管理数据")),
            dbc.ModalBody(
                [
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("数据目录"),
                                    dbc.Input(id="data-folder-input", placeholder="输入或选择数据目录"),
                                ],
                                width=True,
                            ),
                            dbc.Col(
                                dbc.Button("选择文件夹", id="data-pick-btn", color="secondary", size="sm"),
                                width="auto",
                                className="align-self-end",
                            ),
                        ],
                        className="g-2 mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("运行组 (base)"),
                                    dcc.Dropdown(
                                        id="data-rungroup",
                                        options=[],
                                        clearable=True,
                                        placeholder="留空使用默认",
                                    ),
                                ],
                                width=True,
                            ),
                        ],
                        className="g-2 mb-2",
                    ),
                    html.Div(id="data-scan-status"),
                    html.Hr(),
                    html.Div(id="data-artifacts", className="small text-muted"),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button("扫描", id="data-scan-btn", color="primary", size="sm", className="me-auto"),
                    dbc.Button("应用", id="data-apply-btn", color="success", size="sm"),
                    dbc.Button("关闭", id="data-close-btn", color="secondary", size="sm", outline=True),
                ]
            ),
        ],
        id="data-modal",
        is_open=False,
        size="lg",
        backdrop="static",
    )


def build_layout() -> html.Div:
    """Build the full application layout."""
    return html.Div(
        [
            _topbar(),
            html.Div(
                [
                    _nav(),
                    html.Div(
                        [
                            _species_page(),
                            _transitions_page(),
                            _reactions_page(),
                            _intermediate_page(),
                            _evolution_page(),
                            _carbon_page(),
                            _events_page(),
                            _network_page(),
                        ],
                        className="rs-main",
                    ),
                    _detail_panel(),
                ],
                className="rs-body",
            ),
            _data_modal(),
            dcc.Store(id="app-store", storage_type="session", data=cb.initial_store()),
            dcc.Store(id="page-store", storage_type="session", data={"page": DEFAULT_PAGE}),
            dcc.Store(id="species-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="transitions-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="rxn-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="inter-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="evolution-payload-store", storage_type="memory", data=None),
            dcc.Store(id="carbon-payload-store", storage_type="memory", data=None),
            dcc.Store(id="event-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="network-store", storage_type="memory", data=None),
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
    )
    app.layout = build_layout()
    cb.register_callbacks(app)
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
