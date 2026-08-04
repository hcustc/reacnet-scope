"""Transactional Current Dataset validation and state transitions.

The functions in this module are JSON-shaped application services.  They do
not know about Dash components, and they never prepare derived indexes.  A
caller may therefore run validation away from the UI thread and only commit a
result after matching its request identity against the tab-local transaction.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from reacnet_scope.datasets import discover_dataset_candidates
from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope.service_types import ServiceError
from reacnet_scope.workspace_services import (
    artifacts_from_status,
    dataset_capabilities,
    dataset_label,
    dataset_readiness,
    scan_dataset,
    validate_browse_path,
)


DEFAULT_VALIDATION_TIMEOUT_SECONDS = 30


def _candidate_at(folder: str, base: str) -> dict[str, Any]:
    root = validate_browse_path(folder)
    expected = str(Path(base).expanduser().resolve(strict=False))
    try:
        candidates = discover_dataset_candidates(root)
    except OSError as exc:
        raise ServiceError(
            "无法读取 Dataset Candidate；Current Dataset 未改变。",
            reason="candidate_unavailable",
        ) from exc
    candidate = next(
        (
            item
            for item in candidates
            if str(Path(str(item.get("base") or "")).resolve(strict=False))
            == expected
        ),
        None,
    )
    if candidate is None:
        raise ServiceError(
            "所选 Dataset Candidate 已不存在；Current Dataset 未改变。",
            reason="candidate_missing",
        )
    return candidate


def capture_dataset_revision(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Capture bounded file metadata that identifies one source revision."""

    descriptors: list[dict[str, Any]] = []
    for kind, path_text in sorted(
        dict(candidate.get("artifact_paths") or {}).items()
    ):
        path = Path(str(path_text or ""))
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError) as exc:
            raise ServiceError(
                "Dataset Candidate 的源文件在验证期间发生变化；请重试。",
                reason="source_revision_changed",
            ) from exc
        if not path.is_file():
            raise ServiceError(
                "Dataset Candidate 的源文件不再可用；请重新选择。",
                reason="source_revision_changed",
            )
        descriptors.append(
            {
                "kind": str(kind),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )
    if not descriptors:
        raise ServiceError(
            "Dataset Candidate 没有可验证的源文件。",
            reason="candidate_missing",
        )
    encoded = json.dumps(
        descriptors,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "artifacts": descriptors,
    }


def validate_dataset_candidate(folder: str, base: str) -> dict[str, Any]:
    """Validate identity and the latest source revision as one read-only unit."""

    before_candidate = _candidate_at(folder, base)
    before_revision = capture_dataset_revision(before_candidate)
    resolved_folder = str(before_candidate["folder"])
    resolved_base = str(before_candidate["base"])
    before_identity = dataset_id_for_source(resolved_base)

    status = scan_dataset(resolved_folder, base=resolved_base)
    selected_base = str((status.get("dataset") or {}).get("selected_base") or "")
    if selected_base != resolved_base:
        raise ServiceError(
            "Dataset Candidate 在验证期间已被替换；请重新选择。",
            reason="candidate_changed",
        )

    after_candidate = _candidate_at(resolved_folder, resolved_base)
    after_revision = capture_dataset_revision(after_candidate)
    after_identity = dataset_id_for_source(resolved_base)
    if before_revision != after_revision or before_identity != after_identity:
        raise ServiceError(
            "Dataset Candidate 的源修订在验证期间发生变化；请重试。",
            reason="source_revision_changed",
        )

    return {
        "folder": resolved_folder,
        "base": resolved_base,
        "label": str(after_candidate.get("label") or dataset_label(status)),
        "dataset_id": after_identity,
        "source_revision": after_revision,
        "artifacts": artifacts_from_status(status),
        "capabilities": dataset_capabilities(status),
        "readiness": dataset_readiness(status),
    }


def inspect_dataset_candidate(folder: str, base: str) -> dict[str, Any]:
    """Inspect bounded identity/revision metadata without validating or scanning."""

    candidate = _candidate_at(folder, base)
    resolved_base = str(candidate["base"])
    return {
        "folder": str(candidate["folder"]),
        "base": resolved_base,
        "label": str(candidate.get("label") or Path(resolved_base).name),
        "dataset_id": dataset_id_for_source(resolved_base),
        "source_revision": capture_dataset_revision(candidate),
    }


def begin_dataset_switch(
    candidate: Mapping[str, Any],
    *,
    origin: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    started_ns: int | None = None,
    timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Create the sole active validation request for one browser tab."""

    started = int(started_ns if started_ns is not None else time.time_ns())
    timeout = max(1, int(timeout_seconds)) * 1_000_000_000
    return {
        "state": "validating",
        "request_id": str(request_id or uuid.uuid4().hex),
        "candidate": {
            "folder": str(candidate.get("folder") or ""),
            "base": str(candidate.get("base") or ""),
            "label": str(candidate.get("label") or ""),
        },
        "origin": dict(origin or {}),
        "started_ns": started,
        "deadline_ns": started + timeout,
    }


def supersede_dataset_switch(
    transaction: Mapping[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    """Make an active request permanently unable to commit."""

    current = dict(transaction or {})
    if current.get("state") not in {"validating", "candidate-selected", "failed"}:
        return current
    return {
        **current,
        "state": "superseded",
        "reason": str(reason or "superseded"),
    }


def resolve_dataset_switch(
    transaction: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
    *,
    completed_ns: int | None = None,
) -> dict[str, Any]:
    """Accept a validation result only for the live, unexpired request."""

    current = dict(transaction or {})
    value = dict(result or {})
    if current.get("state") != "validating":
        return current
    if not value.get("request_id") or value.get("request_id") != current.get(
        "request_id"
    ):
        return current
    completed = int(
        completed_ns if completed_ns is not None else value.get("completed_ns") or time.time_ns()
    )
    if completed > int(current.get("deadline_ns") or 0):
        return {
            **current,
            "state": "failed",
            "reason": "validation_timeout",
            "message": (
                "验证结果已超时，未提交。Current Dataset 和 Dataset Candidate 均已保留；"
                "请重试验证。"
            ),
        }
    if not value.get("ok"):
        return {
            **current,
            "state": "failed",
            "reason": str(value.get("reason") or "validation_failed"),
            "message": str(
                value.get("message")
                or "验证失败，Current Dataset 和 Dataset Candidate 均已保留；请重试。"
            ),
        }
    validation = value.get("validation")
    if not isinstance(validation, Mapping):
        return {
            **current,
            "state": "failed",
            "reason": "invalid_validation_result",
            "message": "验证没有返回可提交的上下文；Current Dataset 已保留，请重试。",
        }
    return {
        **current,
        "state": "succeeded",
        "completed_ns": completed,
        "validation": dict(validation),
    }


def is_same_dataset_revision(
    current: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
) -> bool:
    """Return whether a validated candidate is an explicit no-op."""

    old = current or {}
    new = candidate or {}
    old_revision = old.get("source_revision") or {}
    new_revision = new.get("source_revision") or {}
    return bool(old.get("dataset_id")) and (
        str(old.get("dataset_id")) == str(new.get("dataset_id"))
        and str(old_revision.get("fingerprint") or "")
        == str(new_revision.get("fingerprint") or "")
        and bool(old_revision.get("fingerprint"))
    )


def current_dataset_from_validation(
    validation: Mapping[str, Any],
    *,
    inputs_pending: bool = True,
) -> dict[str, Any]:
    """Build the atomically replaceable Current Dataset context."""

    return {
        "folder": str(validation.get("folder") or ""),
        "base": str(validation.get("base") or ""),
        "dataset_id": str(validation.get("dataset_id") or ""),
        "label": str(validation.get("label") or "未选择"),
        "source_revision": dict(validation.get("source_revision") or {}),
        "context_state": "active",
        "capabilities": dict(validation.get("capabilities") or {}),
        "readiness": dict(validation.get("readiness") or {}),
        "artifacts": dict(validation.get("artifacts") or {}),
        "selected_smiles": "",
        "selected_formula": "",
        "selected_species_source": "",
        "inputs_pending": bool(inputs_pending),
    }


def _revision_changed_context(
    current: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    def descriptors(revision: Mapping[str, Any] | None) -> dict[str, tuple[int, int]]:
        return {
            str(item.get("kind") or ""): (
                int(item.get("size") or 0),
                int(item.get("mtime_ns") or 0),
            )
            for item in dict(revision or {}).get("artifacts") or []
            if item.get("kind")
        }

    previous = descriptors(current.get("source_revision") or {})
    detected = descriptors(validation.get("source_revision") or {})
    changed_kinds = {
        kind
        for kind in previous.keys() | detected.keys()
        if previous.get(kind) != detected.get(kind)
    }
    capability_sources = {
        "species": {"reaction"},
        "reaction": {"reaction"},
        "transition": {"reaction"},
        "intermediate": {"species"},
        "evolution": {"species"},
        "events": {"timeline", "reactionevent", "molecules"},
    }
    readiness_sources = {
        "basic_analysis": {"reaction", "species"},
        "event_search": {"timeline", "reactionevent", "molecules"},
        "trajectory_evidence": {"trajectory"},
    }
    capabilities = dict(current.get("capabilities") or {})
    for key, sources in capability_sources.items():
        if key in capabilities and sources & changed_kinds:
            capabilities[key] = False
    readiness = dict(current.get("readiness") or {})
    for key, sources in readiness_sources.items():
        if key in readiness and sources & changed_kinds:
            readiness[key] = {"ready": False, "state": "stale"}
    artifacts = {
        str(kind): str(path)
        for kind, path in dict(current.get("artifacts") or {}).items()
        if str(kind) not in changed_kinds
    }
    return {
        **dict(current),
        "context_state": "revision-changed",
        "detected_revision": dict(validation.get("source_revision") or {}),
        "invalidated_artifacts": sorted(changed_kinds),
        "artifacts": artifacts,
        "capabilities": capabilities,
        "readiness": readiness,
        "selected_smiles": "",
        "selected_formula": "",
        "selected_species_source": "",
        "inputs_pending": True,
    }


def revalidate_current_dataset(
    current: Mapping[str, Any] | None,
    *,
    adopt_revision: bool = False,
) -> dict[str, Any]:
    """Revalidate a restored/current context without silently switching identity."""

    existing = dict(current or {})
    if not existing.get("dataset_id"):
        return {"state": "none", "context": None}
    try:
        validation = validate_dataset_candidate(
            str(existing.get("folder") or ""),
            str(existing.get("base") or ""),
        )
    except (ServiceError, OSError, RuntimeError) as exc:
        return {
            "state": "unavailable",
            "context": None,
            "reason": str(getattr(exc, "reason", "unavailable") or "unavailable"),
            "message": str(getattr(exc, "message", exc)),
        }
    if str(validation.get("dataset_id") or "") != str(
        existing.get("dataset_id") or ""
    ):
        return {
            "state": "unavailable",
            "context": None,
            "reason": "dataset_identity_changed",
            "message": "Dataset Identity 无法重新验证；已清除 Current Dataset。",
        }
    if is_same_dataset_revision(existing, validation) or adopt_revision:
        context = current_dataset_from_validation(
            validation,
            inputs_pending=bool(existing.get("inputs_pending")),
        )
        return {"state": "active", "context": context}
    return {
        "state": "revision-changed",
        "context": _revision_changed_context(existing, validation),
    }


__all__ = [
    "DEFAULT_VALIDATION_TIMEOUT_SECONDS",
    "begin_dataset_switch",
    "capture_dataset_revision",
    "current_dataset_from_validation",
    "inspect_dataset_candidate",
    "is_same_dataset_revision",
    "resolve_dataset_switch",
    "revalidate_current_dataset",
    "supersede_dataset_switch",
    "validate_dataset_candidate",
]
