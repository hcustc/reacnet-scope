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
    "batch-compare",
    "reaction-compare",
)

PAGE_LABELS: Final[dict[str, str]] = {
    "species": "物种与趋势",
    "reactions": "反应与事件",
    "evolution": "时间演化",
    "events": "反应事件",
    "trajectory": "结构与轨迹",
    "element-distribution": "元素分布演化",
    "data-management": "RNG 数据",
    "batch-compare": "物种多来源对比",
    "reaction-compare": "反应多来源对比",
}

PAGE_DESCRIPTIONS: Final[dict[str, str]] = {
    "species": "检索精确物种与结构，并从同一工作区进入丰度趋势和元素分布。",
    "reactions": "查看直接生成/消耗通道、反应式和具体事件，不把计数解释为速率或机理。",
    "evolution": "绘制单个或多组物种的时间演化曲线，比较生成与消耗趋势。",
    "events": "从反应通道定位 RNG 事件，建立可复核的轨迹证据入口。",
    "trajectory": "核查具体反应实例的前后结构、局部轨迹与几何导出，并按需追踪参与分子的变化。",
    "element-distribution": "按RNG 数据中发现的元素分组和筛选物种，追踪分布随时间的变化。",
    "data-management": "管理已导入的RNG 数据，添加来源、选择当前RNG 数据；在独立页签准备当前数据。",
    "reaction-compare": "比较多个来源的反应与条件统计，保持当前RNG 数据不变。",
    "batch-compare": "选择多个来源，逐来源确认精确物种后比较丰度趋势；保持当前RNG 数据不变。",
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
    "batch-compare": "/assets/icons/batch-compare.svg",
    "reaction-compare": "/assets/icons/reactions.svg",
}

PAGE_CLASS_NAMES: Final[dict[str, str]] = {
    "element-distribution": "rs-page rs-element-distribution-minimal",
    "data-management": "rs-page rs-data-page",
}

# The product surface is deliberately small.  Only pages owned by the five
# supported workspaces are mounted in Dash.
NAV_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("分析工作区", ("species", "reactions", "trajectory")),
)

WORKSPACE_PAGE_IDS: Final[tuple[str, ...]] = (
    "data-management",
    "species",
    "reactions",
    "trajectory",
)

WORKSPACE_TOOL_PAGES: Final[dict[str, tuple[str, ...]]] = {
    "data-management": ("data-management",),
    "species": ("species", "evolution", "element-distribution", "batch-compare"),
    "reactions": ("reactions", "events", "reaction-compare"),
    "trajectory": ("trajectory",),
}

WORKSPACE_TASK_LABELS: Final[dict[str, str]] = {
    "data-management": "选择与准备RNG 数据",
    "species": "物种检索",
    "evolution": "时间演化",
    "element-distribution": "元素分布",
    "reactions": "直接通道与反应式",
    "events": "具体事件",
    "trajectory": "反应结构、轨迹与变化追踪",
    "batch-compare": "多来源对比",
    "reaction-compare": "多来源对比",
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
    "candidate-paths": "reactions",
    "pathway": "reactions",
    "species-fate": "trajectory",
}

PAGE_SECTIONS: Final[dict[str, str]] = {
    page_id: PAGE_LABELS[workspace_id]
    for page_id, workspace_id in PAGE_WORKSPACES.items()
}

# Entry-level capabilities only. Individual operations (e.g. continuous MD
# support or DFT geometry) still check their own evidence in the core service.
PAGE_CAPABILITY_REQUIREMENTS: Final[dict[str, str]] = {
    "species": "reaction_search",
    "reactions": "reaction_search",
    "evolution": "species_abundance",
    "element-distribution": "element_distribution",
    "events": "event_search",
    "trajectory": "event_search",
}

GROUP_DESCRIPTIONS: Final[dict[str, str]] = {
    "分析工作区": "从物种、反应和具体事件逐步下钻到轨迹与原子证据。",
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
