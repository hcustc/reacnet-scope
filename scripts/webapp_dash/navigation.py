"""Shared page and top-navigation configuration for the Dash workbench."""

from __future__ import annotations

from typing import Final


PAGE_IDS: Final[tuple[str, ...]] = (
    "species",
    "reactions",
    "evolution",
    "events",
    "species-fate",
    "trajectory",
    "candidate-paths",
    "pathway",
    "element-distribution",
    "data-management",
    "batch-compare",
)

PAGE_LABELS: Final[dict[str, str]] = {
    "species": "物种检索",
    "reactions": "反应式检索",
    "evolution": "时间演化",
    "events": "反应事件",
    "species-fate": "物种命运分析",
    "trajectory": "轨迹查看",
    "candidate-paths": "候选路径发现",
    "pathway": "路径验证",
    "element-distribution": "元素分布演化",
    "data-management": "数据集",
    "batch-compare": "批量对比",
}

PAGE_DESCRIPTIONS: Final[dict[str, str]] = {
    "species": "按分子式、SMILES 或精确质量定位物种，并继续查看结构与反应通道。",
    "reactions": "检索反应式、比较净通量，并把可信通道交给路径或事件工作流。",
    "evolution": "绘制单个或多组物种的时间演化曲线，比较生成与消耗趋势。",
    "events": "从反应通道定位 RNG 事件，建立可复核的轨迹证据入口。",
    "species-fate": "从目标 Species 的形成事件出发，追踪锚点后代直至用户定义终点或证据删失。",
    "trajectory": "检查局部反应轨迹、关键帧和原子环境，并导出外部分析脚本。",
    "candidate-paths": "从一个或多个起始 Species 出发，在观测有向反应网络上发现有界候选路径。",
    "pathway": "输入明确的 Reaction Type 序列，用时间、分子实例和原子 ID 核查完整事件链。",
    "element-distribution": "按数据集中发现的元素分组和筛选物种，追踪分布随时间的变化。",
    "data-management": "选择当前数据集、查看可用分析功能，并按需维护派生索引。",
    "batch-compare": "跨多个数据集比较反应检出、通量与条件差异。",
}

PAGE_SECTIONS: Final[dict[str, str]] = {
    "species": "检索与趋势",
    "reactions": "检索与趋势",
    "evolution": "检索与趋势",
    "events": "事件证据",
    "species-fate": "事件证据",
    "trajectory": "事件证据",
    "candidate-paths": "事件证据",
    "pathway": "事件证据",
    "element-distribution": "检索与趋势",
    "data-management": "数据集",
    "batch-compare": "数据工作区",
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
    "species-fate": "/assets/icons/events.svg",
    "trajectory": "/assets/icons/trajectory.svg",
    "candidate-paths": "/assets/icons/pathway.svg",
    "pathway": "/assets/icons/pathway.svg",
    "element-distribution": "/assets/icons/element-distribution.svg",
    "data-management": "/assets/icons/data-management.svg",
    "batch-compare": "/assets/icons/batch-compare.svg",
}

PAGE_CLASS_NAMES: Final[dict[str, str]] = {
    "candidate-paths": "rs-page rs-pathway-page",
    "pathway": "rs-page rs-pathway-page",
    "element-distribution": "rs-page rs-element-distribution-minimal",
    "data-management": "rs-page rs-data-page",
}

NAV_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "检索与趋势",
        (
            "species",
            "reactions",
            "evolution",
            "element-distribution",
        ),
    ),
    (
        "事件证据",
        (
            "events",
            "species-fate",
            "trajectory",
            "candidate-paths",
            "pathway",
        ),
    ),
)

TOP_NAV_PAGE_IDS: Final[tuple[str, ...]] = tuple(
    page_id
    for _group_label, page_ids in NAV_GROUPS
    for page_id in page_ids
)

DEFAULT_PAGE: Final[str] = "species"

# A fresh session has no Current Dataset, so it starts where that prerequisite
# can be satisfied.  Successful loads stay on the dataset overview.
START_PAGE: Final[str] = "data-management"
