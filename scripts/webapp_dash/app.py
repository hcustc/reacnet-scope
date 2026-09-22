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
from dash import dcc, html
from flask import Response, jsonify, request

# Ensure project root is importable when run via ``python -m`` or directly.
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from scripts.webapp_dash.chart_presentation import empty_chart_figure as _empty_chart_figure
from scripts.webapp_dash import candidate_workbench
from scripts.webapp_dash import lineage_explorer
from scripts.webapp_dash import file_import
from scripts.webapp_dash import callbacks as cb  # noqa: E402
from scripts.webapp_dash import ui_components as ui
from scripts.webapp_dash import ui_state
from scripts.webapp_dash import dataset_library
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
                                    html.Span("当前RNG 数据", className="rs-meta-label"),
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
            dataset_library.selector(),
            html.Div(
                [
                    html.Span(
                        id="topbar-index-status",
                        className="rs-index-global-state",
                    ),
                    dbc.Button(
                        "选择RNG 数据",
                        id="data-pick-btn",
                        color="secondary",
                        size="sm",
                        outline=True,
                        className="rs-topbar-dataset-switch",
                    ),
                    dbc.DropdownMenu(
                        [dbc.DropdownMenuItem("RNG 数据与准备任务", id="open-data-modal"),
                         dbc.DropdownMenuItem("刷新索引状态", id="data-prep-refresh-btn")],
                        label="数据与任务", color="secondary", size="sm", toggle_style={"background": "white", "color": "#445166"},
                        align_end=True, className="rs-data-menu",
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
                    html.Button(id="data-open-batch-compare-btn", hidden=True, n_clicks=0),
                    html.Div(
                        [
                            html.Span(className="rs-nav-footer-dot"),
                            html.Span("数据与计算均在软件运行的机器上"),
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
                        "需要选择RNG 数据",
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


def _workspace_task_navigation() -> html.Nav:
    """Host the task entries for whichever of the five workspaces is active."""

    return html.Nav(
        id="workspace-task-nav",
        className="rs-workspace-task-nav",
        **{"aria-label": "当前工作区工具"},
    )



def _detail_panel() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div([html.H6("选中物种详情"), html.Span("结构与网络统计", className="rs-detail-kicker")]),
                    dbc.Button("关闭详情", id="species-detail-close", size="sm", color="secondary", outline=True, title="关闭物种详情并返回结果"),
                    html.Div(
                        [
                            dbc.Button(
                                "查看时间演化",
                                id="species-to-evolution-btn",
                                color="primary",
                                size="sm",
                                outline=True,
                                disabled=True,
                            ),
                            dbc.Button("经反应通道定位事件", id="species-to-event-btn", color="secondary", size="sm", outline=True, disabled=True),
                            dbc.Button("从该物种探索", id="cp-from-species", size="sm", outline=True, disabled=True),
                            dbc.Button("寻找生成路线", id="cp-to-species", size="sm", outline=True, disabled=True),
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


def _grid(grid_id: str, *, row_selectable: str = "single", page_size: int | None = None) -> html.Div:
    return ui.result_grid(grid_id, selection=row_selectable, page_size=page_size,
                          sortable=grid_id != "rxn-timing-event-grid")


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
                                    html.Label("查询内容", htmlFor="species-query", className="rs-grid-label"),
                                    dcc.Input(
                                        id="species-query",
                                        value="",
                                        placeholder="例如 H2O / [H][O] / 17.00274",
                                        debounce=False,
                                        type="text",
                                        style={"width": "100%"},
                                    ),
                                ],
                            ),
                            html.Div(
                                [
                                    html.Label("质量容差 (Da)", htmlFor="species-mass-tol", className="rs-grid-label"),
                                    dcc.Input(
                                        id="species-mass-tol",
                                        value=0.5,
                                        min=0,
                                        type="number",
                                        style={"width": "100%"},
                                    ),
                                ],
                                id="species-mass-field", style={"display": "none"},
                            ),
                            html.Div(
                                [
                                    html.Label("\u00A0", className="rs-grid-label"),
                                    html.Div(
                                        [
                                            dbc.Button("查询", id="species-search-btn", color="primary", size="sm"),

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
                            dbc.Button("选择RNG 数据", id="species-open-data-modal", color="primary", size="sm"),
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
                                    html.Div(
                                        [
                                            dbc.Button(
                                                "查看所选物种的反应通道与时间",
                                                id="species-to-channels-btn",
                                                color="primary",
                                                size="sm",
                                                disabled=True,
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
                                        className="d-flex gap-2",
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
                                            dbc.Button(
                                                "导出全部 CSV",
                                                id="species-csv-btn",
                                                color="secondary",
                                                size="sm",
                                                outline=True,
                                                className="ms-1",
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
                                        html.P(
                                            "时间属于具体的精确 Reaction Type。选择一个结构后，"
                                            "点击“查看所选物种的反应通道与时间”，再选择一条生成或消耗通道。",
                                            id="species-structure-timing-hint",
                                            className="rs-step-note mt-2 mb-0",
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

    return html.Div([query_card,
                     html.Div(id="species-query-feedback", role="status", **{"aria-live": "polite"}),
                     grid_card], className="rs-page", id="page-species")


def _reactions_page() -> html.Div:
    query_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        dbc.Label("反应物分子式", html_for="rxn-reactants", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="rxn-reactants", value="", placeholder="例如 C6H4 + H", className="rs-grow"),
                        dbc.Label("产物分子式", html_for="rxn-products", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="rxn-products", value="", placeholder="例如 C6H5", className="rs-grow"),
                        dbc.Label("匹配", html_for="rxn-mode", className="mb-0", style={"fontSize": 12}),
                        dcc.Dropdown(
                            id="rxn-mode",
                            options=[{"label": "精确", "value": "exact"}, {"label": "包含", "value": "contains"}],
                            value="exact",
                            clearable=False,
                            style={"width": 110},
                        ),
                        dbc.Label("Top", html_for="rxn-top", className="mb-0", style={"fontSize": 12}),
                        dcc.Input(id="rxn-top", value="50", type="number", style={"width": 76}),
                        dbc.Button("查询", id="rxn-search-btn", color="primary", size="sm"),
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
                        ui.result_toolbar(html.Div(id="rxn-alert"), [
                            dbc.Button("送入事件证据", id="rxn-to-event-btn", color="secondary", size="sm", outline=True),
                            dbc.Button("导出 CSV", id="rxn-csv-btn", color="secondary", size="sm", outline=True),
                            dcc.Download(id="rxn-csv-download"),
                        ]),
                        dcc.Loading(html.Div(_grid("rxn-grid"), className="rs-grid-wrap"), type="circle"),
                        html.P("时间取事件后帧；反应发生于前后采样帧之间。首末跨度不是分子寿命，也不表示期间持续发生反应。", className="rs-step-note"),
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
            html.Details(
                [
                    html.Summary(
                        [
                            html.Strong("物理时间换算"),
                            html.Span("可选设置 · 不计算表观速率"),
                        ]
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Strong("确认时间单位"),
                                    html.Span(
                                        "填写 source timestep 每增加 1 对应的物理时间；"
                                        "例如 0.25 fs 填 0.00025 ps。",
                                    ),
                                ],
                                className="rs-kinetics-setup-copy",
                            ),
                            html.Div(
                                [
                                    dbc.Label(
                                        "timestep → ps",
                                        html_for="rxn-channel-timestep-ps",
                                        className="mb-0",
                                    ),
                                    dcc.Input(
                                        id="rxn-channel-timestep-ps",
                                        type="number",
                                        min=1e-12,
                                        step="any",
                                        value=None,
                                        placeholder="例如 0.00025",
                                    ),
                                    dbc.Button(
                                        "保存并刷新时间",
                                        id="rxn-channel-timestep-save-btn",
                                        color="primary",
                                        size="sm",
                                    ),
                                ],
                                className="rs-kinetics-setup-actions",
                            ),
                            html.Div(
                                id="rxn-channel-timestep-status",
                                className="rs-kinetics-setup-status",
                                role="status",
                                **{"aria-live": "polite"},
                            ),
                            html.Div(
                                id="rxn-channel-timestep-progress",
                                className="rs-kinetics-progress",
                                role="status",
                                **{"aria-live": "polite"},
                            ),
                        ],
                        className="rs-kinetics-setup",
                    ),
                ],
                id="rxn-channel-time-settings",
                className="rs-channel-settings",
            ),
            html.Div(
                [
                    dcc.Input(
                        id="rxn-channel-trajectory-path",
                        type="text",
                        value="",
                    ),
                    dbc.Checkbox(
                        id="rxn-channel-coordinate-unit-confirm",
                        value=False,
                    ),
                    dbc.Button(
                        "兼容保留",
                        id="rxn-channel-volume-save-btn",
                    ),
                    html.Div(id="rxn-channel-volume-status"),
                    html.Div(id="rxn-channel-volume-progress"),
                    dcc.Interval(
                        id="rxn-channel-volume-refresh",
                        interval=1000,
                        n_intervals=0,
                        disabled=True,
                    ),
                ],
                id="rxn-channel-rate-compatibility",
                hidden=True,
            ),
            html.Div(id="rxn-channel-alert", className="rs-flow-alert"),
            html.P("时间取事件后帧；首末跨度不是分子寿命，也不表示期间持续发生反应。", className="rs-step-note"),
            html.Div([
                html.Div([
                    html.H2("直接反应通道"),
                    html.P("选择一行查看完整结构，再定位具体事件。同分子式可能对应不同结构。"),
                ]),
                html.Div([
                    dcc.RadioItems(
                        id="rxn-channel-layout", options=[
                            {"label": "纵向浏览", "value": "stacked"},
                            {"label": "并排对照", "value": "compare"}],
                        value="stacked", inline=True,
                        persistence=True, persistence_type="session",
                        className="rs-channel-layout-picker",
                    ),
                    html.Div(
                        dbc.Checkbox(
                            id="rxn-channel-show-rates",
                            value=False,
                            label="显示速率列",
                        ),
                        hidden=True,
                    ),
                ], className="rs-channel-display-options"),
            ], className="rs-channel-toolbar"),
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
                id="rxn-channel-lanes",
                className="rs-channel-lanes is-stacked",
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
    timing_card = dbc.Card(
        dbc.CardBody([
            html.H6("反应发生时间分布", className="rs-card-title"),
            html.Div(id="rxn-timing-status", role="status"),
            html.Div([
                dbc.Label("时间窗起点"),
                dcc.Input(id="rxn-timing-start", type="number", debounce=True, style={"width": 120}),
                dbc.Label("终点"),
                dcc.Input(id="rxn-timing-end", type="number", debounce=True, style={"width": 120}),
                dbc.Label("分箱宽度"),
                dcc.Input(id="rxn-timing-width", type="number", min=0, debounce=True, style={"width": 120}),
                dbc.Button("应用", id="rxn-timing-apply-btn", size="sm"),
            ], className="rs-query-row"),
            dcc.Graph(id="rxn-timing-graph", config={"displayModeBar": False}),
            html.Div(id="rxn-timing-bin-status"),
            html.Div([
                dbc.Button("上一页", id="rxn-timing-prev-btn", size="sm", outline=True),
                dbc.Button("下一页", id="rxn-timing-next-btn", size="sm", outline=True),
                dbc.Button("在事件页核查这些事件", id="rxn-timing-open-events-btn", size="sm", color="primary"),
                dbc.Button("导出本页 CSV", id="rxn-timing-csv-btn", size="sm", outline=True),
                dcc.Download(id="rxn-timing-csv-download"),
            ], className="rs-query-row"),
            html.Div(_grid("rxn-timing-event-grid", page_size=25), className="rs-grid-wrap"),
            dcc.Store(id="rxn-timing-distribution-store"),
            dcc.Store(id="rxn-timing-page-store"),
        ]), className="rs-card", id="rxn-timing-card", style={"display": "none"},
    )
    return html.Div(
        [dcc.Tabs(id="reaction-task-tabs", value="direct", style={"display": "none"}, children=[
            dcc.Tab(label="直接反应", value="direct"),
            dcc.Tab(label="候选路径", value="candidates"),
        ]),
         html.Div([query_card, html.Div(id="rxn-query-feedback", role="status"),
                   grid_card, channel_view, timing_card], id="cp-direct-panel"),
         html.Div(candidate_workbench.layout(), id="cp-path-panel", style={"display": "none"})],
        className="rs-page",
        id="page-reactions",
    )


def _evolution_page() -> html.Div:
    def field(label: str, control: Any, hint: str = "") -> html.Div:
        return html.Div(
            [dbc.Label(label, html_for=control.id), control,
             *([html.Small(hint, className="rs-evolution-hint")] if hint else [])],
            className="rs-evolution-field",
        )

    query_card = dbc.Card(
        dbc.CardBody([
            html.Div([html.Span("01", className="rs-evolution-step"),
                      html.H2("选择物种")], className="rs-evolution-section-title"),
            field("目标物种或分子式", dcc.Textarea(
                id="evolution-targets", value="",
                placeholder="每行一个 SMILES 或分子式\n例如：CO2",
                className="rs-multiline-input",
            ), "支持多个目标；自定义名称可写为 名称::查询。"),
            html.Div("或从RNG 数据中选择", className="rs-evolution-divider"),
            dbc.Button("读取物种目录", id="evolution-load-species-btn",
                       color="secondary", size="sm", outline=True),
            html.Div(id="evolution-catalog-alert", className="rs-inline-status",
                     role="status", **{"aria-live": "polite"}),
            field("物种目录", dcc.Dropdown(
                id="evolution-species-picker", options=[], value=[], multi=True,
                searchable=True, placeholder="读取目录后搜索分子式", optionHeight=42,
            )),
            html.Div([html.Span("02", className="rs-evolution-step"),
                      html.H2("绘图设置")], className="rs-evolution-section-title"),
            html.Div([
                field("横轴", dcc.Dropdown(
                    id="evolution-xaxis", options=[
                        {"label": "步数", "value": "step"},
                        {"label": "时间 / ps", "value": "ps"},
                        {"label": "时间 / ns", "value": "ns"}],
                    value="step", clearable=False,
                )),
                field("平滑窗口", dcc.Input(id="evolution-smooth", value="1",
                      type="number", min=1, step=1), "1 表示不平滑"),
            ], className="rs-evolution-settings-grid"),
            html.Div([
                html.Div([
                    html.Strong("比较多个RNG 数据", className="rs-evolution-compare-title"),
                    html.Span(
                        "逐来源确认精确物种、模型迭代与模拟条件；选择来源不会切换当前RNG 数据。",
                        className="rs-evolution-hint",
                    ),
                ], className="rs-evolution-compare-copy"),
                dbc.Button(
                    "打开多来源对比",
                    id="evolution-open-compare-btn",
                    color="secondary",
                    size="sm",
                    outline=True,
                ),
            ], className="rs-evolution-compare-cta"),
            dbc.Accordion([
                dbc.AccordionItem([
                    html.P(
                        "默认使用当前RNG 数据；这里可临时叠加一个或多个已准备的 Species 文件。",
                        className="rs-evolution-hint",
                    ),
                    field("单个 Species 文件", dcc.Input(
                        id="evolution-species-file", placeholder="留空使用当前RNG 数据")),
                    field("多文件列表", dcc.Textarea(
                        id="evolution-species-files",
                        placeholder="2500K@seed1::/path/run1.species\n3000K@seed1::/path/run2.species",
                    )),
                ], title="兼容：临时多文件叠加"),
                dbc.AccordionItem([
                    field("分子式显示方式", dcc.Dropdown(
                        id="evolution-formula-mode", options=[
                            {"label": "合并同分子式", "value": "sum"},
                            {"label": "拆分 SMILES", "value": "split"},
                            {"label": "同时显示", "value": "both"}],
                        value="sum", clearable=False,
                    )),
                    field("每式 SMILES 上限", dcc.Input(id="evolution-max-smiles", value="0", type="number", min=0)),
                    field("归一化", dcc.Dropdown(id="evolution-normalize", options=[
                        {"label": "无", "value": "none"}, {"label": "初始值", "value": "initial"},
                        {"label": "最大值", "value": "max"}], value="none", clearable=False)),
                    field("时间对齐", dcc.Dropdown(id="evolution-time-align", options=[
                        {"label": "原始时间", "value": "raw"}, {"label": "截断交集", "value": "truncate"},
                        {"label": "相对起点", "value": "relative"}], value="raw", clearable=False)),
                    field("timestep → ps（确认后保存）", dcc.Input(
                        id="evolution-timestep", value=None, type="number", min=0,
                        placeholder="使用RNG 数据已保存的换算")),
                    field("下采样", dcc.Input(id="evolution-downsample", value="1800", type="number", min=0)),
                    field("最大曲线数", dcc.Input(id="evolution-max-curves", value="30", type="number", min=1)),
                    field("曲线筛选", dcc.Input(id="evolution-curve-filter", placeholder="按名称筛选")),
                ], title="曲线处理设置"),
            ], start_collapsed=True, className="rs-evolution-advanced"),
            dbc.Button("绘制演化曲线", id="evolution-search-btn", color="primary",
                       className="rs-evolution-submit"),
            html.Small("手动输入与目录选择的目标会合并绘制。", className="rs-evolution-hint"),
        ]), className="rs-card rs-evolution-controls",
    )
    chart_card = dbc.Card(dbc.CardBody([
        html.Div([
            html.Div([html.H2("丰度随时间变化"),
                      html.P("点击曲线数据点查看对应物种结构。")]),
            dbc.Button("导出 CSV", id="evolution-csv-btn", color="secondary",
                       size="sm", outline=True, disabled=True),
            dcc.Download(id="evolution-csv-download"),
        ], className="rs-evolution-chart-heading"),
        html.Div(id="evolution-alert", role="alert"),
        html.Div(id="evolution-progress", className="rs-analysis-progress", role="status"),
        dcc.Loading(dcc.Graph(
            id="evolution-graph",
            figure=_empty_chart_figure("选择物种，开始查看时间演化", "在设置区添加目标，然后点击绘制。"),
            config={"displaylogo": False, "responsive": True},
            className="rs-evolution-chart",
        ), type="circle"),
        dbc.Checkbox(id="evolution-structure-show-h", value=True, label="显示 H",
                     className="rs-structure-h-toggle"),
        html.Div(id="evolution-structure-detail", className="rs-channel-detail rs-channel-detail-empty"),
    ]), className="rs-card rs-evolution-results")
    return html.Div([query_card, chart_card], className="rs-page", id="page-evolution")


def _element_distribution_page() -> html.Div:
    settings_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                dbc.Label("RNG 数据", html_for="element-distribution-dataset-name"),
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
                                    placeholder="留空使用当前RNG 数据已保存的换算；未保存时显示 timestep",
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
                html.Div(
                    id="event-bookmark-status",
                    className="rs-step-note mt-1",
                ),
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
                                    "分子分支追踪",
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
                    "默认只用非氢原子延伸变化历史，所有结论均可回查到 RNG event_id。",
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
                            "开始分支追踪",
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
                                html.Div(
                                    [
                                        html.H6(
                                            "继续追踪分支",
                                            className="rs-card-title mb-1",
                                        ),
                                        html.P(
                                            "只有因本段预算停止的分支可以继续；证据边界和连续性断点不会被越过。",
                                            className="rs-step-note mb-2",
                                        ),
                                    ]
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                dbc.Label("选择停止分支"),
                                                dcc.Dropdown(
                                                    id="molecule-lineage-branch",
                                                    options=[],
                                                    value=None,
                                                    clearable=False,
                                                    placeholder="当前没有可继续的分支",
                                                ),
                                            ],
                                            className="rs-lineage-field rs-lineage-branch-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("本段持久变化预算"),
                                                dbc.Input(
                                                    id="molecule-lineage-continue-depth",
                                                    type="number",
                                                    value=3,
                                                    min=1,
                                                    max=20,
                                                    step=1,
                                                ),
                                            ],
                                            className="rs-lineage-field",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("本段分子节点预算"),
                                                dbc.Input(
                                                    id="molecule-lineage-continue-node-limit",
                                                    type="number",
                                                    value=100,
                                                    min=1,
                                                    max=10000,
                                                    step=1,
                                                ),
                                            ],
                                            className="rs-lineage-field",
                                        ),
                                        dbc.Button(
                                            "继续追踪所选分支",
                                            id="molecule-lineage-continue-btn",
                                            color="primary",
                                            outline=True,
                                            disabled=True,
                                            className="rs-lineage-run",
                                        ),
                                    ],
                                    className="rs-lineage-continue-controls",
                                ),
                                html.Div(
                                    id="molecule-lineage-branch-summary",
                                    className="rs-step-note mt-2",
                                ),
                            ],
                            className="rs-lineage-continuation",
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
                            "点击事件菱形、分子节点或下表行，会直接在上方局部轨迹查看器中载入对应事件。",
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
                                html.H6(
                                    "DFT 初始几何 · 量化交接",
                                    className="rs-card-title mb-0",
                                ),
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
                        dcc.Checklist(
                            id="event-dft-isolated-cluster-confirmation",
                            options=[
                                {
                                    "label": "我确认本次交接按非周期孤立簇处理",
                                    "value": "confirmed",
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
                            "下载量化交接 ZIP",
                            id="event-dft-download-btn",
                            color="primary",
                            size="sm",
                            disabled=True,
                        ),
                        dcc.Download(id="event-dft-download"),
                    ],
                    className="d-flex gap-2 flex-wrap mt-2",
                ),
                html.Details(
                    [
                        html.Summary("量化交接准备检查"),
                        html.Div(id="event-dft-validation", className="mt-2"),
                        dcc.Checklist(
                            id="event-dft-review-confirmation",
                            options=[
                                {
                                    "label": "我已逐项复核 review_required 警告",
                                    "value": "acknowledged",
                                }
                            ],
                            value=[],
                            className="rs-dft-unit-confirmation mt-2",
                        ),
                    ],
                    open=True,
                    className="rs-dft-electronic-details mt-2",
                ),
                html.Details(
                    [
                        html.Summary("动力学适用性"),
                        html.Div(
                            [
                                html.Strong("insufficient_evidence"),
                                html.Div(
                                    "量化交接包 ready 只表示可交给外部 TS 流程；"
                                    "不表示已验证基元步骤，也不表示可直接计算速率或适用气相 TST/RRKM。",
                                    className="rs-step-note mt-1",
                                ),
                            ],
                            className="mt-2",
                        ),
                    ],
                    className="rs-dft-electronic-details mt-2",
                ),
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
                                html.Div("具体反应实例", className="rs-step-kicker"),
                                html.H6(
                                    "前后结构与局部轨迹",
                                    className="rs-card-title mb-0",
                                ),
                            ],
                            className="rs-step-heading",
                        ),
                        dbc.Button(
                            "← 选择反应实例",
                            id="trajectory-back-events-btn",
                            color="secondary",
                            size="sm",
                            outline=True,
                        ),
                    ],
                    className="rs-result-toolbar",
                ),
                html.Div(
                    "请先选择一个具体反应实例。坐标可用时可以载入局部轨迹；分子变化追踪另行检查事件与分子证据。",
                    id="trajectory-alert",
                    className="rs-step-note",
                ),
                html.Div(id="trajectory-selection-summary", className="rs-event-selected-summary"),
                html.Div(
                    id="trajectory-selection-structure",
                    className="rs-candidate-instance-detail",
                ),
                dbc.Button(
                    "载入当前实例的局部轨迹",
                    id="trajectory-open-selected-btn",
                    color="success",
                    size="sm",
                    disabled=True,
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
                                                    "选择后点击“应用设置并重新提取”保存到当前RNG 数据；未映射的 Type 保持 T1、T2 等标记。",
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
        [source_card, viewer_card, _dft_geometry_card(), lineage_explorer.layout(),
         html.Details([html.Summary('兼容：事件端点变化追踪'), _molecule_lineage_card()])],
        className="rs-page",
        id="page-trajectory",
    )










def _batch_compare_page() -> html.Div:
    species_card = dbc.Card(
        dbc.CardBody([
            html.Div([
                html.Div([
                    html.Span("01", className="rs-compare-step"),
                    html.Div([
                        html.H6("选择数据来源", className="rs-card-title mb-0"),
                        html.Span(
                            "选择已导入的RNG 数据加入对比；至少需要两个来源。",
                            className="rs-card-subtitle",
                        ),
                    ]),
                ], className="rs-compare-step-heading"),
                html.Div(id="species-compare-entry-note", className="small"),
            ], className="rs-compare-heading-row"),
            html.Div([
                dcc.Dropdown(
                    id="species-compare-managed",
                    multi=True,
                    options=[],
                    value=[],
                    placeholder="选择已导入的RNG 数据（可多选）",
                    className="rs-grow",
                ),
            ], className="rs-query-row rs-compare-source-picker"),
            html.Div([
                html.Span("02", className="rs-compare-step"),
                html.Div([
                    html.H6("逐来源确认精确 Species", className="rs-card-title mb-0"),
                    html.Span(
                        "相同分子式或显示名称不会自动视为同一结构。",
                        className="rs-card-subtitle",
                    ),
                ]),
            ], className="rs-compare-step-heading"),
            html.Div(id="species-compare-sources"),
            html.Details([
                html.Summary("未在列表中？手工添加 Species 文件"),
                html.Div([
                    dcc.Input(id="species-compare-path", placeholder="输入 .species 文件路径", className="rs-grow"),
                    dcc.Input(id="species-compare-new-label", placeholder="来源名称（可选）"),
                    dbc.Button("添加来源", id="species-compare-add-path", size="sm", color="secondary", outline=True),
                ], className="rs-query-row"),
            ], className="rs-compare-manual-source"),
            html.Div([
                html.Span("03", className="rs-compare-step"),
                html.Div([
                    html.H6("检查时间口径并运行", className="rs-card-title mb-0"),
                    html.Span(
                        "原始 timestep 保持独立；物理时间要求每个来源都已确认换算。",
                        className="rs-card-subtitle",
                    ),
                ]),
            ], className="rs-compare-step-heading"),
            html.Div([
                dbc.Label("时间轴"),
                dcc.Dropdown(id="species-compare-axis", options=[{"label": "原始 timestep", "value": "step"}, {"label": "ps（各来源需已确认换算）", "value": "ps"}, {"label": "ns（各来源需已确认换算）", "value": "ns"}], value="step", clearable=False, style={"width": 240}),
                dbc.Button("比较丰度", id="species-compare-run", color="primary", size="sm", disabled=True),
                dbc.Button("导出曲线、汇总和查询条件", id="species-compare-export", color="secondary", size="sm", outline=True, disabled=True),
                dcc.Download(id="species-compare-download"),
            ], className="rs-query-row rs-compare-actions"),
            html.Div(id="species-compare-readiness", className="rs-compare-readiness", role="status", **{"aria-live": "polite"}),
            html.Div(id="species-compare-alert", className="small"),
            dcc.Loading(dcc.Graph(
                id="species-compare-graph",
                figure=_empty_chart_figure(
                    "添加来源并确认精确 Species",
                    "准备完成后，比较按钮会自动启用。",
                ),
            ), type="circle"),
            _grid("species-compare-summary", page_size=25),
        ], className="p-3"), className="rs-card rs-batch-controls-card"
    )
    condition_card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.H6("对比数据选择", className="rs-card-title mb-0"),
                        html.Span(
                            "可组合使用数据管理中的RNG 数据与目录扫描结果",
                            className="rs-card-subtitle",
                        ),
                    ],
                    className="rs-batch-card-heading",
                ),
                html.Div(
                    [
                        dbc.Label("已管理RNG 数据", className="mb-0 rs-batch-field-label"),
                        html.Div(
                            dcc.Dropdown(
                                id="batch-managed-selector",
                                multi=True,
                                options=[],
                                value=[],
                                placeholder="选择已导入的RNG 数据（可多选）",
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
                        dbc.Label(
                            "数据根目录（每行一个，可导入多个文件夹）",
                            className="mb-0 rs-batch-field-label",
                            style={"whiteSpace": "normal"},
                        ),
                        dcc.Textarea(
                            id="batch-root-dir",
                            placeholder="每行输入一个目录；可一次扫描多个文件夹",
                            className="rs-grow",
                            rows=2,
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
                ui.result_grid("batch-scan-review",
                    definitions=[
                        {"name": "来源", "id": "name", "editable": False},
                        {"name": "模型迭代", "id": "model_iteration", "editable": True},
                        {"name": "Simulation Condition", "id": "simulation_condition", "editable": True},
                        {"name": "Replicate", "id": "replicate", "type": "numeric", "editable": True},
                        {"name": "元数据状态", "id": "metadata_status", "editable": False},
                        {"name": "反应来源", "id": "reaction_file", "editable": False},
                        {"name": "建议分组", "id": "source_group", "editable": False},
                        {"name": "目录", "id": "folder", "editable": False},
                    ],
                    selection=None,
                    hidden=tuple(["source_group", "folder"])),
                dcc.Checklist(
                    id="batch-confirm-inferred-metadata",
                    options=[{
                        "label": "我已检查并确认上表的条件与 Replicate；目录名只提供建议",
                        "value": "confirmed",
                    }],
                    value=[],
                    className="small",
                ),
                html.Div(
                    [
                        dbc.Label("选择待确认分组建议", className="mb-0 rs-batch-field-label"),
                        html.Div(
                            dcc.Dropdown(
                                id="batch-condition-selector",
                                multi=True,
                                placeholder="先检查上表，再选择要采用的分组建议",
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
                        dbc.Button("导出结果与来源", id="batch-csv-btn", color="secondary", size="sm", outline=True, disabled=True),
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
                                "可直接选择已管理RNG 数据，也可扫描目录、检查并确认条件/Replicate 建议。",
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
    return html.Div([
        html.Div(html.Div(species_card, id="compare-species-panel"),
                 className="rs-page", id="page-batch-compare"),
        html.Div(html.Div([condition_card, matrix_card, detail_card], id="compare-reactions-panel"),
                 className="rs-page", id="page-reaction-compare"),
    ])


def _channel_grid(grid_id: str) -> html.Div:
    return ui.result_grid(grid_id, height=350)


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
                                "当前RNG 数据的分析索引",
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
                    html.Div([
                        dcc.RadioItems(id="library-view", options=[
                            {"label": "已导入RNG 数据", "value": "library"},
                            {"label": "当前数据与准备任务", "value": "tasks"}],
                            value="library", inline=True, className="rs-library-tabs"),
                        dataset_library.management_panel(),
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
                                                                "当前RNG 数据",
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
                                                        "选择RNG 数据",
                                                        id="data-empty-pick-btn",
                                                        color="primary",
                                                        className="rs-empty-dataset-action",
                                                    ),
                                                    dbc.Button(
                                                        "更新状态",
                                                        id="data-current-refresh-btn",
                                                        color="secondary",
                                                        outline=True,
                                                        className="rs-current-refresh-action",
                                                        style={"display": "none"},
                                                    ),
                                                    dbc.Button(
                                                        "更换RNG 数据",
                                                        id="data-change-pick-btn",
                                                        color="secondary",
                                                        outline=True,
                                                        className="rs-change-dataset-action",
                                                    ),
                                                    dbc.Button(
                                                        "打开分析功能",
                                                        id="data-open-species-btn",
                                                        style={"display": "none"},
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
                            html.Div(
                                id="data-next-action",
                                className="rs-data-next-action d-none",
                            ),
                            html.Div(id="data-overview-actions", className="rs-data-overview-actions"),
                            _data_cache_management_card(),
                        ],
                        id="library-tasks-panel",
                        style={"display": "none"},
                    )], id="data-overview-view", className="rs-data-view"),
                    file_import.layout(),
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
                            _workspace_task_navigation(),
                            html.Div(id="page-workflow-guide"),
                            file_import.preparation_layout(),
                            _species_page(),
                            _reactions_page(),
                            _evolution_page(),
                            _element_distribution_page(),
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
            dcc.Store(id="dataset-switch-request", storage_type="memory", data={}),
            dcc.Store(id="dataset-switch-validation", storage_type="memory", data={}),
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
                        "reaction-timing-chart",
                        "reaction-timing-page",
                        "evolution",
                        "element-distribution",
                        "events",
                        "trajectory",
                        "molecule-lineage",
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
            dataset_library.stores(),
            dcc.Store(id="species-detail-focus"),
            dcc.Store(id="species-search-btn-response"),
            dcc.Store(id="rxn-search-btn-response"),
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
            dcc.Store(id="data-clear-kind-store", storage_type="memory", data={}),
            dcc.Interval(id="data-prep-refresh", interval=2000, n_intervals=0, disabled=True),
            dcc.Interval(
                id="preparation-task-refresh",
                interval=2000,
                n_intervals=0,
                disabled=True,
            ),
            dcc.Store(id="event-selected-store", storage_type="memory", data=None),
            dcc.Store(id="event-bookmark-store", storage_type="session", data=None),
            dcc.Store(
                id="event-bookmark-validation-store",
                storage_type="memory",
                data=None,
            ),
            dcc.Store(id="event-viewer-store", storage_type="memory", data=None),
            dcc.Store(id="event-dft-store", storage_type="memory", data=None),
            dcc.Store(id="molecule-lineage-store", storage_type="memory", data=None),
            dcc.Store(
                id="molecule-lineage-drilldown-store",
                storage_type="memory",
                data=None,
            ),
            dcc.Store(id="batch-managed-store", storage_type="memory", data={"datasets": []}),
            dcc.Store(id="species-compare-sources-store", storage_type="session", data=[]),
            dcc.Store(id="species-compare-catalog-store", storage_type="memory", data={}),
            dcc.Store(id="species-compare-result-store", storage_type="memory", data=None),
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
        external_stylesheets=["/assets/bootstrap-local.css"],
        assets_ignore=r"bootstrap-local\.css$",
        suppress_callback_exceptions=True,
        title="ReacNet Scope (Dash)",
        assets_folder=str(Path(__file__).parent / "assets"),
        background_callback_manager=_background_callback_manager(),
    )
    app.layout = build_layout()
    ui.register_grid_callbacks(app)
    ui_state.register_callbacks(app)
    cb.register_callbacks(app)
    candidate_workbench.register_callbacks(app)
    lineage_explorer.register_callbacks(app)
    file_import.register_callbacks(app)
    dataset_library.register_callbacks(app)

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

    @app.server.get("/api/reaction.svg")
    def _reaction_svg():
        reaction_smiles = (request.args.get("reaction_smiles") or "").strip()
        if not reaction_smiles or len(reaction_smiles) > 8192:
            return Response(
                "invalid reaction SMILES",
                status=400,
                mimetype="text/plain",
            )
        try:
            width = max(320, min(1200, int(request.args.get("width") or 720)))
            height = max(120, min(400, int(request.args.get("height") or 220)))
        except (TypeError, ValueError):
            return Response("invalid dimensions", status=400, mimetype="text/plain")
        show_h = str(request.args.get("show_h") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        result = svc.render_reaction_svg(
            reaction_smiles,
            width=width,
            height=height,
            show_h=show_h,
        )
        if not result.get("ok") or not result.get("svg"):
            return Response("reaction unavailable", status=422, mimetype="text/plain")
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
