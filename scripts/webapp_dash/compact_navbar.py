"""Compact top navbar combining navigation and dataset context (Grafana-style)."""

from dash import html
import dash_bootstrap_components as dbc

from .navigation import (
    NAV_GROUPS,
    PAGE_ICONS,
    PAGE_LABELS,
    START_PAGE,
)
from . import dataset_library


def build_navbar() -> dbc.Navbar:
    """
    Build a compact horizontal navbar combining:
    - Logo and brand
    - Main navigation items (物种发现, 反应路径, 证据核查, 趋势对比)
    - Current dataset indicator and selector
    - Data management link

    Uses Bootstrap Navbar components for responsive layout.
    """

    # Build main navigation items from NAV_GROUPS
    nav_items = []
    for _group_label, page_ids in NAV_GROUPS:
        for page_id in page_ids:
            nav_items.append(
                dbc.NavItem(
                    html.Button(
                        [
                            html.Img(
                                src=PAGE_ICONS[page_id],
                                className="rs-nav-icon-compact",
                                alt="",
                            ),
                            html.Span(PAGE_LABELS[page_id]),
                        ],
                        id=f"nav-{page_id}",
                        type="button",
                        n_clicks=0,
                        title=PAGE_LABELS[page_id],
                        className="rs-top-nav-item",
                        **{"aria-current": "false"},
                    )
                )
            )

    # Dataset context section
    dataset_context = html.Div(
        [
            html.Span(
                className="rs-dataset-indicator-compact",
                **{"aria-hidden": "true"},
            ),
            html.Div(
                [
                    html.Span(id="topbar-rungroup", children="未选择", className="rs-dataset-name"),
                    html.Span(
                        id="topbar-status",
                        className="rs-badge rs-bad",
                        children="未加载",
                        role="status",
                        **{"aria-live": "polite"},
                    ),
                ],
                className="rs-dataset-info",
            ),
        ],
        className="rs-dataset-context-compact",
    )

    # Right-side actions
    nav_right = dbc.Nav(
        [
            dbc.NavItem(dataset_library.selector()),
            html.Span(id="topbar-index-status", className="rs-index-global-state"),
            dbc.NavItem(
                dbc.Button(
                    "选择数据",
                    id="data-pick-btn",
                    color="secondary",
                    size="sm",
                    outline=True,
                    className="ms-2",
                )
            ),
            dbc.NavItem(
                dbc.DropdownMenu(
                    [
                        dbc.DropdownMenuItem("RNG 数据与准备任务", id="open-data-modal"),
                        dbc.DropdownMenuItem("刷新索引状态", id="data-prep-refresh-btn"),
                    ],
                    label="数据与任务",
                    color="secondary",
                    size="sm",
                    toggle_style={"background": "white", "color": "#445166"},
                    align_end=True,
                    className="rs-data-menu ms-2",
                )
            ),
        ],
        className="rs-navbar-actions ms-auto",
        navbar=True,
    )

    return dbc.Navbar(
        dbc.Container(
            [
                # Brand/Logo
                html.Button(
                    [
                        html.Span("RS", className="rs-brand-mark-compact"),
                        html.Span("ReacNet Scope", className="rs-brand-compact"),
                    ],
                    type="button",
                    n_clicks=0,
                    title="RNG 数据",
                    className="rs-top-nav-item rs-nav-utility active",
                    id="nav-data-management",
                    **{"aria-current": "page", "aria-label": "RNG 数据"},
                ),

                # Main navigation
                dbc.Nav(
                    nav_items,
                    className="rs-nav-main-compact",
                    navbar=True,
                ),

                # Dataset context
                dataset_context,

                # Right-side actions
                nav_right,
                html.Div(
                    [
                        html.Span(id="topbar-folder", children="未选择"),
                        html.Span(id="topbar-page-context", children=PAGE_LABELS[START_PAGE]),
                        html.Button(id="data-open-batch-compare-btn", n_clicks=0),
                    ],
                    hidden=True,
                ),
            ],
            fluid=True,
            className="rs-navbar-container",
        ),
        color="white",
        dark=False,
        className="rs-navbar-compact",
        sticky="top",
    )
