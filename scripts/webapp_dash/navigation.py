"""Shared page and top-navigation configuration for the Dash workbench."""

from __future__ import annotations

from typing import Final


PAGE_IDS: Final[tuple[str, ...]] = (
    "species",
    "reactions",
    "evolution",
    "events",
    "intermediate",
    "pathway",
    "carbon",
    "batch-compare",
)

PAGE_LABELS: Final[dict[str, str]] = {
    "species": "物种检索",
    "reactions": "反应式检索",
    "evolution": "时间演化",
    "events": "事件与轨迹",
    "intermediate": "中间体筛选",
    "pathway": "候选路径",
    "carbon": "组成演化",
    "batch-compare": "批量对比",
}

PAGE_CLASS_NAMES: Final[dict[str, str]] = {
    "pathway": "rs-page rs-pathway-page",
    "carbon": "rs-page rs-carbon-minimal",
}

NAV_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "通用工具",
        (
            "species",
            "reactions",
            "evolution",
            "events",
        ),
    ),
    (
        "自动分析",
        (
            "intermediate",
            "pathway",
            "carbon",
        ),
    ),
)

TOP_NAV_PAGE_IDS: Final[tuple[str, ...]] = tuple(
    page_id
    for _group_label, page_ids in NAV_GROUPS
    for page_id in page_ids
)

DEFAULT_PAGE: Final[str] = "species"
