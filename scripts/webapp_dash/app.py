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

PAGE_IDS = ["species", "transitions", "reactions", "intermediate", "evolution", "carbon", "events", "network", "literature", "batch-compare"]
PAGE_LABELS = {
    "species": "物种检索",
    "transitions": "转化关系",
    "reactions": "反应式检索",
    "intermediate": "中间体筛选",
    "evolution": "时间演化",
    "carbon": "碳数演化",
    "events": "事件证据",
    "network": "观察网络",
    "literature": "文献验证",
    "batch-compare": "批量对比",
}
PAGE_DESCRIPTIONS = {
    "species": "按分子式、SMILES 或精确质量定位物种，并查看结构与通量。",
    "transitions": "围绕已选物种查看生成、消耗及净通量关系。",
    "reactions": "按反应物和产物组合检索反应，比较正反向通量。",
    "intermediate": "基于丰度、寿命与通量条件筛选关键中间体。",
    "evolution": "绘制目标物种随帧数或模拟时间变化的丰度曲线。",
    "carbon": "聚合不同碳数区间，观察体系碳骨架的演化过程。",
    "events": "定位物种和反应事件，并抽取对应的轨迹证据。",
    "network": "从观测表构建可交互的全局物种-反应网络。",
    "literature": "将文献反应式与当前网络逐条比对并生成证据矩阵。",
    "batch-compare": "扫描多组模拟结果，对比反应通量与检出率。",
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
                    html.Span("RS", className="rs-brand-mark", **{"aria-hidden": "true"}),
                    html.Div(
                        [
                            html.Span("ReacNet Scope", className="rs-brand"),
                            html.Span("反应网络分析工作台", className="rs-brand-subtitle"),
                        ],
                        className="rs-brand-copy",
                    ),
                ],
                className="rs-brand-lockup",
            ),
            html.Div(
                [
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
                        className="rs-meta-primary",
                    ),
                    html.Div(
                        [html.Span("目录", className="rs-meta-label"), html.Span(id="topbar-folder", children="未选择")],
                        className="rs-meta-path",
                    ),
                ],
                className="rs-meta",
            ),
            dbc.Button(
                "管理数据",
                id="open-data-modal",
                color="secondary",
                size="sm",
                outline=True,
                className="ms-auto",
            ),
        ],
        className="rs-topbar",
        fluid=True,
    )


def _nav() -> html.Div:
    groups = [
        ("检索与筛选", ["species", "transitions", "reactions", "intermediate"]),
        ("动力学分析", ["evolution", "carbon", "events", "network"]),
        ("验证与对比", ["literature", "batch-compare"]),
    ]
    children: list[Any] = []
    for group_label, page_ids in groups:
        children.append(html.Div(group_label, className="rs-nav-group-label"))
        children.extend(
            html.Button(
                [html.Span(PAGE_LABELS[pid])],
                id=f"nav-{pid}",
                className=f"rs-nav-item{(' active' if pid == DEFAULT_PAGE else '')}",
                n_clicks=0,
                title=PAGE_DESCRIPTIONS[pid],
            )
            for pid in page_ids
        )
    children.append(
        html.Div(
            [
                html.Span("10", className="rs-nav-count"),
                html.Span("个分析工具"),
            ],
            className="rs-nav-footer",
        )
    )
    return html.Nav(children, className="rs-nav", **{"aria-label": "分析功能"})


def _page_header() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("分析工作台", className="rs-page-eyebrow"),
                    html.H1(PAGE_LABELS[DEFAULT_PAGE], id="page-title"),
                    html.P(PAGE_DESCRIPTIONS[DEFAULT_PAGE], id="page-description"),
                ]
            ),
            html.Div("需要导入数据", id="page-data-status", className="rs-page-status is-blocked"),
        ],
        className="rs-page-header",
    )


def _detail_panel() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div([html.H6("选中物种详情"), html.Span("结构与网络统计", className="rs-detail-kicker")]),
                    dbc.Button("定位该物种事件", id="species-to-event-btn", color="secondary", size="sm", outline=True, disabled=True),
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


def _grid(grid_id: str, *, row_selectable: str = "single") -> dash_table.DataTable:
    return dash_table.DataTable(
        id=grid_id,
        columns=[],
        data=[],
        selected_rows=[],
        row_selectable=row_selectable,
        sort_action="native",
        filter_action="none",
        page_action="none",
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
                                    html.Label("质量容差", className="rs-grid-label"),
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
                                    html.Label("质量模式", className="rs-grid-label"),
                                    dcc.Dropdown(
                                        id="species-mass-mode",
                                        options=[{"label": "精确质量", "value": "exact"}, {"label": "名义质量", "value": "nominal"}],
                                        value="exact",
                                        clearable=False,
                                    ),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Label("结果上限", className="rs-grid-label"),
                                    dcc.Input(id="species-top", value="50", type="number", min=1, style={"width": "100%"}),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Label("\u00A0", className="rs-grid-label"),
                                    html.Div(
                                        [
                                            dbc.Button("查询", id="species-search-btn", color="primary", size="sm"),
                                            dbc.Button(
                                                "导出 CSV",
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
                                    html.Div("开始分析", className="rs-empty-eyebrow"),
                                    html.H5("导入反应网络数据", className="rs-empty-title"),
                                    html.P(
                                        "选择包含 reactionabcd 文件的数据目录后，即可按分子式、SMILES 或质量数检索物种。",
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
                                    _grid("species-grid", row_selectable="multi"),
                                    className="rs-grid-wrap",
                                ),
                                type="circle",
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
                            dbc.Label("Top", className="mb-0", style={"fontSize": 12}),
                            dcc.Input(id="transitions-top", value="30", type="number", min=1, style={"width": 72}),
                            dbc.Checkbox(id="transitions-net-positive", value=False, className="me-1"),
                            dbc.Label("仅正净通量", html_for="transitions-net-positive", className="mb-0"),
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
                    html.Div(
                        [
                            html.Div(id="transitions-selected-summary"),
                            dbc.Button(
                                "定位反应事件",
                                id="transitions-to-event-btn",
                                color="secondary",
                                size="sm",
                                outline=True,
                                disabled=True,
                            ),
                        ],
                        id="transitions-selection-card",
                        className="rs-selection-actions",
                        style={"display": "none"},
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
                            dcc.Textarea(
                                id="evolution-targets",
                                value="",
                                placeholder="从物种检索自动继承；每行一个目标，支持 label::query",
                                className="rs-grow rs-multiline-input",
                                style={"minHeight": 66, "height": 66},
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
                dbc.Accordion(
                    [
                        dbc.AccordionItem(
                            html.Div(
                                [
                                    dbc.Label("Tidy CSV / Excel", className="mb-0"),
                                    dcc.Input(id="carbon-data-path", placeholder="留空则由 Species 文件构建", className="rs-grow"),
                                    dbc.Label("单个 Species 文件", className="mb-0"),
                                    dcc.Input(id="carbon-species-file", placeholder="留空使用当前数据集", className="rs-grow"),
                                    dbc.Label("多文件列表", className="mb-0"),
                                    dcc.Textarea(
                                        id="carbon-species-files",
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
                                    dbc.Label("主题", className="mb-0"),
                                    dcc.Dropdown(id="carbon-theme", options=[{"label": "浅色", "value": "light"}, {"label": "深色", "value": "dark"}], value="light", clearable=False, style={"width": 100}),
                                    dbc.Label("配色", className="mb-0"),
                                    dcc.Dropdown(id="carbon-palette", options=[{"label": "Viridis", "value": "viridis"}, {"label": "Plasma", "value": "plasma"}, {"label": "Tab20", "value": "tab20"}], value="viridis", clearable=False, style={"width": 110}),
                                    dbc.Label("时间对齐", className="mb-0"),
                                    dcc.Dropdown(id="carbon-time-align", options=[{"label": "原始时间", "value": "raw"}, {"label": "截断交集", "value": "truncate"}, {"label": "相对起点", "value": "relative"}], value="raw", clearable=False, style={"width": 130}),
                                    dbc.Label("系统显示", className="mb-0"),
                                    dcc.Dropdown(id="carbon-system-mode", options=[{"label": "自动", "value": ""}, {"label": "叠加", "value": "overlay"}, {"label": "分面", "value": "facet"}], value="", clearable=False, style={"width": 100}),
                                    dbc.Label("图例", className="mb-0"),
                                    dcc.Dropdown(id="carbon-legend-mode", options=[{"label": "紧凑", "value": "compact"}, {"label": "详细", "value": "detailed"}], value="compact", clearable=False, style={"width": 100}),
                                    dbc.Label("SavGol 阶数", className="mb-0"),
                                    dcc.Input(id="carbon-smooth-polyorder", value="2", type="number", min=1, style={"width": 70}),
                                    dbc.Label("子图区域", className="mb-0"),
                                    dcc.Input(id="carbon-layout-regions", placeholder="panel1:1-4; panel2:5-15", className="rs-grow"),
                                    dbc.Label("宽 / 高", className="mb-0"),
                                    dcc.Input(id="carbon-fig-width", value="11.5", type="number", min=4, style={"width": 72}),
                                    dcc.Input(id="carbon-fig-height", value="8", type="number", min=4, style={"width": 72}),
                                    dbc.Label("公式清单", className="mb-0"),
                                    dcc.Input(id="carbon-max-formula", value="30", type="number", min=5, style={"width": 72}),
                                    dbc.Checkbox(id="carbon-show-uncertainty", value=True, className="me-1"),
                                    dbc.Label("显示不确定性", html_for="carbon-show-uncertainty", className="mb-0"),
                                ],
                                className="rs-query-row",
                            ),
                            title="比较与渲染设置",
                        ),
                    ],
                    start_collapsed=True,
                    className="rs-advanced",
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
    workflow_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div("Step 1", className="rs-step-kicker"),
                        html.H6("定位可核查的事件", className="rs-card-title mb-0"),
                    ],
                    className="rs-step-heading",
                ),
                dbc.Tabs(
                    [
                        dbc.Tab(
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            dbc.Label("反应式", className="mb-0", style={"fontSize": 12}),
                                            dcc.Input(id="event-reaction-text", value="", placeholder="A + B -> C + D", className="rs-grow"),
                                            dbc.Label("展示前 / 后帧", className="mb-0", style={"fontSize": 12}),
                                            dcc.Input(id="event-rxn-before", value="5", type="number", min=0, style={"width": 72}),
                                            dcc.Input(id="event-rxn-after", value="5", type="number", min=0, style={"width": 72}),
                                            dbc.Label("候选上限", className="mb-0", style={"fontSize": 12}),
                                            dcc.Input(id="event-rxn-max", value="12", type="number", min=1, style={"width": 72}),
                                            dbc.Button("定位反应事件", id="event-rxn-btn", color="primary", size="sm"),
                                        ],
                                        className="rs-query-row",
                                    ),
                                    html.P("默认入口：使用 .route 定位原子级反应过程，再用轨迹窗口核查。", className="rs-step-note"),
                                ],
                                className="pt-2",
                            ),
                            label="按反应定位",
                            tab_id="event-reaction-tab",
                        ),
                        dbc.Tab(
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            dbc.Label("目标", className="mb-0", style={"fontSize": 12}),
                                            dcc.Input(id="event-species-target", value="", placeholder="SMILES 或分子式", className="rs-grow"),
                                            dbc.Label("匹配", className="mb-0", style={"fontSize": 12}),
                                            dcc.Dropdown(
                                                id="event-match-mode",
                                                options=[
                                                    {"label": "自动", "value": "auto"},
                                                    {"label": "SMILES", "value": "smiles"},
                                                    {"label": "分子式", "value": "formula"},
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
                                            dbc.Label("窗口前 / 后帧", className="mb-0", style={"fontSize": 12}),
                                            dcc.Input(id="event-before", value="3", type="number", min=0, style={"width": 72}),
                                            dcc.Input(id="event-after", value="3", type="number", min=0, style={"width": 72}),
                                            dbc.Label("事件上限", className="mb-0", style={"fontSize": 12}),
                                            dcc.Input(id="event-max", value="12", type="number", min=1, style={"width": 72}),
                                            dbc.Button("定位物种事件", id="event-species-btn", color="primary", size="sm"),
                                        ],
                                        className="rs-query-row",
                                    ),
                                    html.P("用于追踪特定物种的出现、消失和丰度变化，也可进入同一局部轨迹查看器。", className="rs-step-note"),
                                ],
                                className="pt-2",
                            ),
                            label="按物种定位",
                            tab_id="event-species-tab",
                        ),
                    ],
                    active_tab="event-reaction-tab",
                    className="rs-event-tabs",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    source_card = dbc.Card(
        dbc.CardBody(
            dbc.Accordion(
                [
                    dbc.AccordionItem(
                        html.Div(
                            [
                                dbc.Label("Species 覆盖", className="mb-0"),
                                dcc.Input(id="event-species-file", placeholder="留空使用当前数据集", className="rs-grow"),
                                dbc.Label("Trajectory 覆盖", className="mb-0"),
                                dcc.Input(id="event-trajectory-file", placeholder=".lammpstrj 路径（可选）", className="rs-grow"),
                                dbc.Label("Route 覆盖", className="mb-0"),
                                dcc.Input(id="event-route-file", placeholder=".route 路径（可选）", className="rs-grow"),
                                dbc.Label("类型-元素映射", className="mb-0"),
                                dcc.Input(id="event-type-element-map", placeholder="例如 1:C,2:H,3:O", style={"width": 180}),
                                dbc.Checkbox(id="event-include-route", value=True, className="me-1"),
                                dbc.Label("保留 Route 追踪", html_for="event-include-route", className="mb-0"),
                                dbc.Label("原子范围", className="mb-0"),
                                dcc.Dropdown(
                                    id="event-atom-scope",
                                    options=[{"label": "事件相关原子", "value": "event"}, {"label": "全部原子", "value": "all"}],
                                    value="event",
                                    clearable=False,
                                    style={"width": 130},
                                ),
                            ],
                            className="rs-query-row",
                        ),
                        title="数据源与轨迹设置（可选）",
                    )
                ],
                start_collapsed=True,
                className="rs-advanced",
            ),
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
                                dbc.Button("去重分析", id="event-dedup-btn", color="warning", size="sm", outline=True),
                                dbc.Button("导出 CSV", id="event-csv-btn", color="secondary", size="sm", outline=True),
                                dcc.Download(id="event-csv-download"),
                            ],
                            className="d-flex gap-2",
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(id="event-alert"),
                dcc.Loading(html.Div(_grid("event-grid"), className="rs-grid-wrap"), type="circle"),
            ],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    selection_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [html.Div("Step 2", className="rs-step-kicker"), html.H6("提取局部反应上下文", className="rs-card-title mb-0")],
                            className="rs-step-heading",
                        ),
                        dbc.Button("提取并可视化", id="event-extract-btn", color="success", size="sm"),
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
    viewer_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [html.Div("Step 3", className="rs-step-kicker"), html.H6("局部轨迹与关键帧", className="rs-card-title mb-0")],
                            className="rs-step-heading",
                        ),
                        html.Div(id="event-viewer-paths", className="rs-viewer-paths"),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(id="event-viewer-summary", className="rs-event-selected-summary"),
                html.Div(
                    [
                        dbc.Label("显示范围", className="mb-0", style={"fontSize": 12}),
                        dcc.RadioItems(
                            id="event-view-scope",
                            options=[{"label": "完整上下文", "value": "context"}, {"label": "仅反应核", "value": "core"}],
                            value="context",
                            inline=True,
                            className="rs-compact-radio",
                        ),
                        html.Span(id="event-frame-label", className="rs-frame-label"),
                    ],
                    className="rs-query-row rs-viewer-controls",
                ),
                dcc.Slider(id="event-frame-slider", min=0, max=0, value=0, step=1, marks={}, className="mb-3"),
                dcc.Loading(dcc.Graph(id="event-trajectory-3d", className="rs-event-3d"), type="circle"),
                html.Div([html.Div("关键帧故事板", className="rs-storyboard-title"), html.Div(id="event-storyboard", className="rs-storyboard")]),
            ],
            className="p-2",
        ),
        className="rs-card",
        id="event-viewer-card",
        style={"display": "none"},
    )
    return html.Div([workflow_card, source_card, grid_card, selection_card, viewer_card], className="rs-page", id="page-events")


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


def _literature_page() -> html.Div:
    input_card = dbc.Card(
        dbc.CardBody(
            [
                html.H6("文献反应式验证", className="rs-card-title"),
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("文献反应式", className="mb-0", style={"fontSize": 12}),
                                dcc.Textarea(
                                    id="literature-reactions-input",
                                    placeholder="每行一个反应式，例如:\nC6H5ClO -> C6H4O + Cl\nC6H4O -> C5H4 + CO",
                                    rows=6,
                                    style={"width": "100%", "fontFamily": "monospace", "fontSize": 12},
                                ),
                            ],
                            className="rs-grow",
                        ),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    [
                        dbc.Label("验证模式", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="literature-verify-mode",
                            options=[
                                {"label": "物种级别", "value": "species"},
                            ],
                            value="species",
                            clearable=False,
                            style={"width": 150},
                        ),
                        dbc.Button("验证", id="literature-verify-btn", color="primary", size="sm"),
                        dbc.Button("导出 CSV", id="literature-csv-btn", color="secondary", size="sm", outline=True),
                        dcc.Download(id="literature-csv-download"),
                    ],
                    className="rs-query-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    matrix_card = dbc.Card(
        dbc.CardBody(
            [html.Div(id="literature-alert"), dcc.Loading(html.Div(_grid("literature-grid"), className="rs-grid-wrap"), type="circle")],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
    )
    summary_card = dbc.Card(
        dbc.CardBody(
            [
                html.H6("验证摘要", className="rs-card-title"),
                html.Div(id="literature-summary"),
            ],
            className="p-2",
        ),
        className="rs-card",
        id="literature-summary-card",
        style={"display": "none"},
    )
    return html.Div([input_card, matrix_card, summary_card], className="rs-page", id="page-literature")


def _batch_compare_page() -> html.Div:
    condition_card = dbc.Card(
        dbc.CardBody(
            [
                html.H6("条件组选择", className="rs-card-title"),
                html.Div(
                    [
                        dbc.Label("数据根目录", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="batch-root-dir", placeholder="扫描多条件模拟目录", className="rs-grow"),
                        dbc.Button("扫描", id="batch-scan-btn", color="primary", size="sm"),
                    ],
                    className="rs-query-row",
                ),
                html.Div(id="batch-conditions-status", className="small text-muted mb-2"),
                html.Div(
                    [
                        dbc.Label("条件组", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="batch-condition-selector",
                            multi=True,
                            placeholder="选择要对比的条件组",
                            options=[],
                            className="rs-grow",
                        ),
                    ],
                    className="rs-query-row",
                ),
                html.Div(
                    [
                        dbc.Label("最小检出率", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="batch-min-detection", value="0.0", type="number", min=0, max=1, step=0.1, style={"width": 80}),
                        dbc.Label("Top N", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="batch-top-n", value="50", type="number", min=1, max=500, style={"width": 80}),
                        dbc.Button("对比", id="batch-compare-btn", color="primary", size="sm"),
                        dbc.Button("导出 CSV", id="batch-csv-btn", color="secondary", size="sm", outline=True),
                        dcc.Download(id="batch-csv-download"),
                    ],
                    className="rs-query-row",
                ),
            ],
            className="p-2",
        ),
        className="rs-card",
    )
    matrix_card = dbc.Card(
        dbc.CardBody(
            [html.Div(id="batch-alert"), dcc.Loading(html.Div(_grid("batch-matrix-grid"), className="rs-grid-wrap"), type="circle")],
            className="p-2 rs-flex-fill",
        ),
        className="rs-card rs-flex-fill",
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
            className="p-2",
        ),
        className="rs-card",
        id="batch-detail-card",
        style={"display": "none"},
    )
    return html.Div([condition_card, matrix_card, detail_card], className="rs-page", id="page-batch-compare")


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
                            _page_header(),
                            _species_page(),
                            _transitions_page(),
                            _reactions_page(),
                            _intermediate_page(),
                            _evolution_page(),
                            _carbon_page(),
                            _events_page(),
                            _network_page(),
                            _literature_page(),
                            _batch_compare_page(),
                        ],
                        className="rs-main",
                    ),
                ],
                className="rs-body",
                id="app-body",
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
            dcc.Store(id="event-selected-store", storage_type="memory", data=None),
            dcc.Store(id="event-viewer-store", storage_type="memory", data=None),
            dcc.Store(id="network-store", storage_type="memory", data=None),
            dcc.Store(id="literature-grid-store", storage_type="memory", data={"rows": []}),
            dcc.Store(id="batch-conditions-store", storage_type="memory", data=None),
            dcc.Store(id="batch-matrix-grid-store", storage_type="memory", data={"rows": []}),
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
