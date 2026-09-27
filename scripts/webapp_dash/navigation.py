"""Shared page, workflow and navigation configuration for the Dash workbench."""

from __future__ import annotations

from typing import Final


PAGE_IDS: Final[tuple[str, ...]] = (
    "species",
    "reactions",
    "evolution",
    "events",
    "trajectory",
    "element-distribution",
    "data-management",
    "reaction-compare",
)

PAGE_LABELS: Final[dict[str, str]] = {
    "species": "物种",
    "reactions": "反应",
    "evolution": "演化",
    "events": "事件",
    "trajectory": "事件详情",
    "element-distribution": "元素分布",
    "data-management": "RNG 数据",
    "reaction-compare": "反应对比",
}

PAGE_DESCRIPTIONS: Final[dict[str, str]] = {
    "species": "检索精确物种，从物种详情探索路线、直接反应与丰度趋势。",
    "reactions": "独立检索反应类型、查看具体事件并比较多个来源。",
    "evolution": "查看累计采样丰度、丰度趋势与元素分布。",
    "events": "独立查询 RNG 事件，打开共用的事件详情。",
    "trajectory": "核查事件键结构、完整轨迹和周围环境，导出可复核事件包或 DFT 初始几何。",
    "element-distribution": "按RNG 数据中发现的元素分组和筛选物种，追踪分布随时间的变化。",
    "data-management": "管理已导入的RNG 数据，添加来源、选择当前RNG 数据；在独立页签准备当前数据。",
    "reaction-compare": "比较多个来源的反应与条件统计。",
}

# Compact, font-independent marks keep navigation legible without another
# icon-font or network dependency.
# Offline SVG icons are served from the Dash assets folder.  Visible text
# labels stay adjacent to every icon, so these images are decorative and are
# hidden from assistive technology in the navigation controls.
PAGE_ICONS: Final[dict[str, str]] = {
    "species": "/assets/icons/species.svg",
    "reactions": "/assets/icons/reactions.svg",
    "evolution": "/assets/icons/evolution.svg",
    "events": "/assets/icons/events.svg",
    "trajectory": "/assets/icons/trajectory.svg",
    "element-distribution": "/assets/icons/element-distribution.svg",
    "data-management": "/assets/icons/data-management.svg",
    "reaction-compare": "/assets/icons/reactions.svg",
}

PAGE_CLASS_NAMES: Final[dict[str, str]] = {
    "element-distribution": "rs-page rs-element-distribution-minimal",
    "data-management": "rs-page rs-data-page",
}

# RNG Data is the fifth peer entry, rendered separately in the default sidebar.
NAV_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("分析入口", ("species", "reactions", "evolution", "events")),
)

WORKSPACE_PAGE_IDS: Final[tuple[str, ...]] = (
    "data-management",
    "species",
    "reactions",
    "evolution",
    "events",
)

WORKSPACE_TOOL_PAGES: Final[dict[str, tuple[str, ...]]] = {
    "data-management": ("data-management",),
    "species": ("species",),
    "reactions": ("reactions", "reaction-compare"),
    "evolution": ("evolution", "element-distribution"),
    "events": ("events", "trajectory"),
}

# Task buttons stay mounted so a workspace switch can expose the correct row in
# the same browser frame as the page itself.  Candidate discovery is a task
# inside the reactions page rather than a separately mounted page.
WORKSPACE_TASK_PAGES: Final[dict[str, tuple[str, ...]]] = {
    "species": ("species",),
    "reactions": ("reactions", "reaction-candidates", "reaction-related", "reaction-compare"),
    "evolution": ("evolution", "element-distribution"),
    "events": ("events",),
}

WORKSPACE_TASK_LABELS: Final[dict[str, str]] = {
    "data-management": "选择与准备RNG 数据",
    "species": "物种检索",
    "evolution": "丰度趋势",
    "element-distribution": "元素分布",
    "reactions": "反应检索",
    "events": "事件检索",
    "trajectory": "事件详情与导出",
    "reaction-compare": "反应对比",
    "reaction-related": "相关反应与反应检索",
}

PAGE_WORKSPACES: Final[dict[str, str]] = {
    page_id: workspace_id
    for workspace_id, page_ids in WORKSPACE_TOOL_PAGES.items()
    for page_id in page_ids
}

# Session storage from P0-P4 may still name a retired Dash page.  Keep only
# this redirect table—not the old page layouts or callbacks—so restoration
# lands in the workspace that owns the surviving evidence workflow.
LEGACY_PAGE_REDIRECTS: Final[dict[str, str]] = {
    "batch-compare": "evolution",
    "candidate-paths": "species",
    "pathway": "species",
    "species-fate": "events",
}

PAGE_SECTIONS: Final[dict[str, str]] = {
    page_id: PAGE_LABELS[workspace_id]
    for page_id, workspace_id in PAGE_WORKSPACES.items()
}

# Entry-level capabilities only. Individual operations (e.g. continuous MD
# support or DFT geometry) still check their own evidence in the core service.
PAGE_CAPABILITY_REQUIREMENTS: Final[dict[str, str]] = {
    "species": "species_abundance",
    "reactions": "reaction_search",
    "evolution": "species_abundance",
    "element-distribution": "element_distribution",
    "events": "event_search",
    "trajectory": "event_search",
}

GROUP_DESCRIPTIONS: Final[dict[str, str]] = {
    "分析入口": "从精确物种、反应、丰度观察或具体事件开始分析。",
}

TOP_NAV_PAGE_IDS: Final[tuple[str, ...]] = tuple(
    page_id
    for _group_label, page_ids in NAV_GROUPS
    for page_id in page_ids
)

DEFAULT_PAGE: Final[str] = "species"

# A fresh session has no Current Dataset, so it starts where that prerequisite
# can be satisfied.  Successful loads stay on the dataset overview.
START_PAGE: Final[str] = "data-management"


def resolve_page_id(value: object, *, default: str = DEFAULT_PAGE) -> str:
    """Resolve mounted pages and safely migrate retired session page IDs."""

    page_id = str(value or "")
    page_id = LEGACY_PAGE_REDIRECTS.get(page_id, page_id)
    return page_id if page_id in PAGE_IDS else default


def migrate_page_state(value: object) -> dict[str, object]:
    """Translate old session page meanings before interpreting a page ID."""

    state = dict(value) if isinstance(value, dict) else {}
    page = str(state.get("page") or START_PAGE)
    if state.get("version") != 2:
        # Before the five-entry UI, ``reactions`` meant Species research.
        # There is no safe way to infer which task was open after a refresh.
        if page in {"reactions", "candidate-paths", "pathway"}:
            page = "species"
        elif page == "species-fate":
            page = "events"
        elif page == "batch-compare":
            page = "evolution"
            state["compare_sources"] = True
        elif page == "trajectory":
            # Full-width restoration is allowed only after the persisted
            # bookmark has been checked against the published event index.
            page = "events"
    state["page"] = resolve_page_id(page)
    state["version"] = 2
    return state
