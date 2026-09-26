"""Compact top navbar combining navigation and dataset context (Grafana-style)."""

from dash import html
import dash_bootstrap_components as dbc

from .navigation import (
    NAV_GROUPS,
    PAGE_ICONS,
    PAGE_DESCRIPTIONS,
    PAGE_LABELS,
    PAGE_SECTIONS,
    START_PAGE,
)
from . import dataset_library


def build_navbar() -> dbc.Navbar:
    """
    Build a compact horizontal navbar combining:
    - Logo and brand
    - Five peer entries (RNG 数据, 物种, 反应, 演化, 事件)
    - Current dataset indicator and selector
    - Data management link

    Uses Bootstrap Navbar components for responsive layout.
    """

    # Build main navigation items from NAV_GROUPS
    nav_items = [dbc.NavItem(html.Button(
        [html.Img(src=PAGE_ICONS["data-management"], className="rs-nav-icon-compact", alt=""),
         html.Span(PAGE_LABELS["data-management"])],
        id="nav-data-management", type="button", n_clicks=0,
        title=PAGE_LABELS["data-management"],
        className="rs-top-nav-item active",
        **{"aria-current": "page",
           "data-page-description": PAGE_DESCRIPTIONS["data-management"],
           "data-page-section": PAGE_SECTIONS["data-management"]},
    ))]
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
                        **{"aria-current": "false",
                           "data-page-description": PAGE_DESCRIPTIONS[page_id],
                           "data-page-section": PAGE_SECTIONS[page_id]},
                    )
                )
            )

    return dbc.Navbar(
        dbc.Container(
            [
                # Brand/Logo
                html.Div(
                    [
                        html.Span("RS", className="rs-brand-mark-compact"),
                        html.Span("ReacNet Scope", className="rs-brand-compact"),
                    ],
                    className="rs-brand-lockup",
                ),

                # Main navigation
                dbc.Nav(
                    nav_items,
                    className="rs-nav-main-compact",
                    navbar=True,
                ),

                # Dataset context
                dataset_library.current_dataset_menu(),

                # Right-side actions
                dataset_library.toolbar_actions(),
                html.Div(
                    [
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
