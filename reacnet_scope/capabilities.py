"""Canonical, user-visible Analysis Capability evidence.

File discovery and index stores expose implementation-oriented facts.  This
module translates those facts into the small capability vocabulary used by
the CLI, Dataset Candidate browser, Current Dataset context, and Dash task
management UI.  A dataset deliberately has no aggregate readiness state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CAPABILITY_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "reaction_search",
        "label": "反应检索",
        "source_kinds": ("reaction",),
        "preparation_kind": "",
        "ready_reason": "已发现 Reaction Evidence，可直接检索反应网络。",
        "missing_reason": "缺少 .reactionabcd Reaction Evidence。",
    },
    {
        "key": "species_abundance",
        "label": "物种丰度",
        "source_kinds": ("species",),
        "preparation_kind": "",
        "ready_reason": "已发现 Species Abundance Evidence，可直接查询丰度与演化。",
        "missing_reason": "缺少 .species Species Abundance Evidence。",
    },
    {
        "key": "event_search",
        "label": "事件检索",
        "source_kinds": ("timeline", "reactionevent", "molecules"),
        "preparation_kind": "event",
        "ready_reason": "事件索引与当前源修订一致，可检索 Reaction Occurrence。",
        "missing_reason": "需要 .timeline.h5，或兼容的 .reactionevent.csv 与 .molecules.csv。",
    },
    {
        "key": "trajectory_evidence",
        "label": "轨迹证据",
        "source_kinds": ("trajectory",),
        "preparation_kind": "trajectory",
        "ready_reason": "轨迹帧索引与当前源修订一致，可定位 Analyzed Frame。",
        "missing_reason": "缺少轨迹源文件。",
    },
    {
        "key": "element_distribution",
        "label": "元素分布",
        "source_kinds": ("species",),
        "preparation_kind": "composition",
        "ready_reason": "元素分布索引与当前源修订一致，可分析分布演化。",
        "missing_reason": "缺少 .species Species Abundance Evidence。",
    },
)

CAPABILITY_BY_KEY = {
    str(item["key"]): item for item in CAPABILITY_DEFINITIONS
}

CAPABILITY_STATES = frozenset(
    {
        "ready",
        "needs-preparation",
        "preparing",
        "missing-source",
        "stale",
        "invalid",
    }
)


def normalize_capability_state(value: Any) -> str:
    """Normalize store/task state spelling to the public capability vocabulary."""

    state = str(value or "").strip().lower().replace("_", "-")
    state = {
        "": "needs-preparation",
        "missing": "needs-preparation",
        "building": "preparing",
        "running": "preparing",
        "cancel-requested": "preparing",
        "completed": "ready",
    }.get(state, state)
    return state if state in CAPABILITY_STATES else "invalid"


def _source_available(key: str, artifacts: Mapping[str, Any]) -> bool:
    if key == "event_search":
        return bool(
            artifacts.get("timeline")
            or (artifacts.get("reactionevent") and artifacts.get("molecules"))
        )
    definition = CAPABILITY_BY_KEY[key]
    return all(bool(artifacts.get(kind)) for kind in definition["source_kinds"])


def _reason_for_state(
    definition: Mapping[str, Any],
    state: str,
    status: Mapping[str, Any],
    task: Mapping[str, Any],
) -> str:
    if state == "ready":
        return str(definition["ready_reason"])
    if state == "missing-source":
        return str(definition["missing_reason"])
    if state == "needs-preparation":
        return "源证据可用；请在数据管理中显式准备对应索引。"
    if state == "preparing":
        phase = str(task.get("phase") or status.get("phase") or "正在准备索引")
        return f"Preparation Task 正在运行：{phase}。"
    if state == "stale":
        return "已发布索引属于较早的源修订；请在数据管理中续建或重建。"
    message = str(task.get("message") or status.get("message") or "").strip()
    return message or "索引证据无法验证；请在数据管理中检查并重建。"


def analysis_capability_evidence(
    artifacts: Mapping[str, Any] | None,
    *,
    index_statuses: Mapping[str, Mapping[str, Any] | str] | None = None,
    tasks: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return independent capability states and one-line explanations.

    ``artifacts`` is the compact ``{kind: path}`` mapping. ``index_statuses``
    uses preparation kinds (``event``, ``trajectory``, ``composition``).
    Direct-source capabilities intentionally ignore unrelated missing indexes.
    """

    artifact_map = dict(artifacts or {})
    statuses = dict(index_statuses or {})
    task_map = dict(tasks or {})
    evidence: dict[str, dict[str, Any]] = {}
    for definition in CAPABILITY_DEFINITIONS:
        key = str(definition["key"])
        preparation_kind = str(definition["preparation_kind"])
        raw_status = statuses.get(preparation_kind, {}) if preparation_kind else {}
        status = (
            dict(raw_status)
            if isinstance(raw_status, Mapping)
            else {"state": str(raw_status or "")}
        )
        task = dict(task_map.get(preparation_kind) or {}) if preparation_kind else {}
        if not _source_available(key, artifact_map):
            state = "missing-source"
        elif not preparation_kind:
            state = "ready"
        elif (
            str(task.get("state") or "") in {"running", "cancel_requested"}
            and task.get("matches_current_revision") is not False
        ):
            state = "preparing"
        else:
            state = normalize_capability_state(status.get("state"))
        evidence[key] = {
            "key": key,
            "label": str(definition["label"]),
            "state": state,
            "reason": _reason_for_state(definition, state, status, task),
            "source_kinds": list(definition["source_kinds"]),
            "preparation_kind": preparation_kind,
        }
    return evidence


def capability_states(
    evidence: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Return the compact state-only compatibility view."""

    return {
        key: normalize_capability_state(item.get("state"))
        for key, item in dict(evidence or {}).items()
    }


__all__ = [
    "CAPABILITY_BY_KEY",
    "CAPABILITY_DEFINITIONS",
    "CAPABILITY_STATES",
    "analysis_capability_evidence",
    "capability_states",
    "normalize_capability_state",
]
