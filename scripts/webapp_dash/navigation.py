"""Shared page and top-navigation configuration for the Dash workbench."""

from __future__ import annotations

from typing import Final


PAGE_IDS: Final[tuple[str, ...]] = (
    "species",
    "reactions",
    "evolution",
    "events",
    "trajectory",
    "intermediate",
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
    "trajectory": "轨迹查看",
    "intermediate": "中间体候选",
    "pathway": "反应路径",
    "element-distribution": "元素分布演化",
    "data-management": "管理数据",
    "batch-compare": "批量对比",
}

PAGE_DESCRIPTIONS: Final[dict[str, str]] = {
    "species": "按分子式、SMILES 或精确质量定位物种，并继续查看结构与反应通道。",
    "reactions": "检索反应式、比较净通量，并把可信通道交给路径或事件工作流。",
    "evolution": "绘制单个或多组物种的时间演化曲线，比较生成与消耗趋势。",
    "events": "从反应通道定位 RNG 事件，建立可复核的轨迹证据入口。",
    "trajectory": "检查局部反应轨迹、关键帧和原子环境，并导出外部分析脚本。",
    "intermediate": "按寿命、丰度与通量筛选中间体候选，衔接后续路径分析。",
    "pathway": "先从聚合反应网络寻找可能路线，再用时间、分子实例和原子 ID 验证真实发生路径。",
    "element-distribution": "按数据集中发现的元素分组和筛选物种，追踪分布随时间的变化。",
    "data-management": "选择当前数据集、检查文件就绪状态，并准备 Dataset Workspace 派生索引。",
    "batch-compare": "跨多个数据集比较反应检出、通量与条件差异。",
}

PAGE_SECTIONS: Final[dict[str, str]] = {
    "species": "检索与趋势",
    "reactions": "检索与趋势",
    "evolution": "检索与趋势",
    "events": "事件证据",
    "trajectory": "事件证据",
    "intermediate": "自动分析",
    "pathway": "自动分析",
    "element-distribution": "自动分析",
    "data-management": "数据工作区",
    "batch-compare": "数据工作区",
}

# Compact, font-independent marks keep navigation legible without another
# icon-font or network dependency.
PAGE_ICONS: Final[dict[str, str]] = {
    "species": "Sp",
    "reactions": "Rx",
    "evolution": "Ev",
    "events": "Et",
    "trajectory": "Tr",
    "intermediate": "In",
    "pathway": "Pw",
    "element-distribution": "Ed",
    "data-management": "Dm",
    "batch-compare": "Cp",
}

PAGE_CLASS_NAMES: Final[dict[str, str]] = {
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
        ),
    ),
    (
        "事件证据",
        (
            "events",
            "trajectory",
        ),
    ),
    (
        "自动分析",
        (
            "intermediate",
            "pathway",
            "element-distribution",
        ),
    ),
)

TOP_NAV_PAGE_IDS: Final[tuple[str, ...]] = tuple(
    page_id
    for _group_label, page_ids in NAV_GROUPS
    for page_id in page_ids
)

DEFAULT_PAGE: Final[str] = "species"
