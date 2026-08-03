"""Shared application services for CLI and Dash workflows.

This module never reimplements analysis logic.  It only:

* normalizes interface inputs into the ``dict[str, list[str]]`` param shape
  that the core query builders expect,
* converts the returned payloads into compact structures suitable for AG
  Grid, Plotly and Cytoscape, and
* normalizes exceptions into structured error dictionaries so callbacks can
  surface concrete reasons via ``dbc.Alert`` instead of crashing the page.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from functools import lru_cache
from bisect import bisect_left, bisect_right
from pathlib import Path
from collections import Counter
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from reacnet_scope.network import ReactionNetwork, count_atoms_fast, formula_from_counts, parse_reactionabcd  # noqa: E402
from reacnet_scope.pathways import find_candidate_paths  # noqa: E402
from reacnet_scope.reaction import canonical_smiles  # noqa: E402
from reacnet_scope.indexes import (  # noqa: E402
    IndexBuildInProgressError,
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    clear_index,
    inspect_workspace_storage,
    resolve_dataset_paths,
    TRAJECTORY_INDEX_STORE,
)
from reacnet_scope.composition import (  # noqa: E402
    SPECIES_COMPOSITION_STORE,
    build_element_distribution_model,
    matches_element_filters,
)
from reacnet_scope import prepare as preparation  # noqa: E402
from reacnet_scope import dir_browser as _dir_browser  # noqa: E402
from reacnet_scope.event_index import (  # noqa: E402
    EVENT_EVIDENCE_STORE,
    EventIndexEvidenceProvider,
)
from reacnet_scope.event_package import (  # noqa: E402
    build_event_package,
    event_trajectory_text,
)
from reacnet_scope.event_paths import (  # noqa: E402
    EventPathAnalysisError,
    EventPathSource,
    analyze_event_paths,
)
from reacnet_scope.rng_events import (  # noqa: E402
    canonical_reaction_key,
    reaction_key,
)
from reacnet_scope.datasets import (  # noqa: E402
    ARTIFACT_SUFFIXES,
    discover_dataset_candidates,
)
from reacnet_scope.trajectory import (  # noqa: E402
    TrajectoryDependencyError,
    TrajectoryFrameError,
    dataset_settings_path,
    load_type_element_map,
    load_timestep_ps,
    normalize_type_element_map,
    read_lammps_frame_block,
    recentered_positions,
    save_type_element_map,
    save_timestep_ps,
    select_local_environment,
)
from reacnet_scope.queries import (  # noqa: E402
    ReactionSourceChangedError,
    STORE,
    build_dataset_status_payload,
    build_intermediate_candidates_payload,
    build_species_plot_payload,
    collect_species_totals,
    collect_next_reactions,
    closest_isotopic_mass,
    derive_species_path,
    formula_mass_fields,
    looks_like_formula,
    match_formula_reaction,
    net_flux,
    load_reaction_network_snapshot,
    reaction_source_signature,
    reaction_formula_str,
    reaction_mass_fields,
    reaction_smiles_str,
    resolve_start_smiles,
    smiles_formula_cached,
    smiles_to_svg,
    split_terms,
    parse_type_element_map_specs,
    parse_species_file_specs,
)


from reacnet_scope.service_types import ServiceError


# ---------------------------------------------------------------------------
# Dataset / folder management
# ---------------------------------------------------------------------------


def scan_dataset(folder: str, *, base: str = "") -> dict[str, Any]:
    """Scan a data folder and return the dataset status payload.

    The adapter accepts plain strings and returns the shared core status model.
    """
    folder_text = (folder or "").strip()
    if not folder_text:
        raise ServiceError("请先选择或输入数据目录", reason="missing_folder")
    folder_path = Path(folder_text).expanduser()
    if not folder_path.exists():
        raise ServiceError(f"数据目录不存在: {folder_path}", reason="missing_folder")
    if not folder_path.is_dir():
        raise ServiceError(f"路径不是目录: {folder_path}", reason="missing_folder")
    try:
        payload = build_dataset_status_payload(
            {
                "dataset_dir": [folder_text],
                "dataset_base": [base or ""],
            }
        )
        dataset = payload.get("dataset", {}) or {}
        artifacts = dataset.get("artifacts", {}) or {}
        timeline = str(
            (artifacts.get("timeline") or {}).get("path") or ""
        )
        reactionevent = str(
            (artifacts.get("reactionevent") or {}).get("path") or ""
        )
        molecules = str((artifacts.get("molecules") or {}).get("path") or "")
        event_primary = timeline if timeline and Path(timeline).is_file() else reactionevent
        if event_primary:
            try:
                event_status = EVENT_EVIDENCE_STORE.status(
                    event_primary,
                    (
                        molecules
                        if event_primary == reactionevent
                        and molecules
                        and Path(molecules).is_file()
                        else ""
                    ),
                    metadata_only=True,
                )
            except RuntimeError as exc:
                event_status = {"state": "invalid", "message": str(exc)}
            state = str(event_status.get("state") or "missing")
            if state == "missing":
                state = "needs_preparation"
            elif state == "missing_source":
                state = "missing"
            event_status = {
                **event_status,
                "state": state,
                "ready": state == "ready",
            }
            if state in {
                "needs_preparation",
                "stale",
                "invalid",
                "building",
            }:
                action = "rebuild" if state in {"stale", "invalid"} else "build"
                event_status["preparation_command"] = (
                    f"reacnet-scope prepare {action} event "
                    f"{shlex.quote(event_primary)}"
                )
            readiness = dataset.setdefault("readiness", {})
            readiness["event_search"] = event_status
        return payload
    except Exception as exc:
        raise ServiceError(f"扫描数据目录失败: {exc}") from exc


# ---------------------------------------------------------------------------
# Directory browser for remote server file system navigation
# ---------------------------------------------------------------------------
# Core logic lives in ``reacnet_scope.dir_browser`` (zero Dash dependency)
# so that CI can import and test it without the full web stack.
# This module re-exports thin adapters that translate
# ``reacnet_scope.dir_browser.DirBrowserError`` into ``ServiceError``.

from reacnet_scope.dir_browser import (  # noqa: E402
    ALLOWED_ROOTS,
    DirBrowserError,
    list_directory as _core_list_directory,
    validate_browse_path as _core_validate_browse_path,
)


def validate_browse_path(path_str: str) -> Path:
    """Normalise *path_str* and verify it lies inside an allowed root."""
    try:
        return _core_validate_browse_path(path_str)
    except DirBrowserError as exc:
        raise ServiceError(exc.message, reason=exc.reason) from exc


def list_directory(path_str: str) -> dict[str, Any]:
    """Enumerate subdirectories in *path_str* for the directory browser."""
    try:
        return _core_list_directory(path_str)
    except DirBrowserError as exc:
        raise ServiceError(exc.message, reason=exc.reason) from exc


def _breadcrumbs_within_allowed_root(current: Path) -> list[dict[str, str]]:
    """Return breadcrumbs starting at the most-specific permitted root."""
    containing = [
        root.resolve()
        for root in _dir_browser.ALLOWED_ROOTS
        if current.is_relative_to(root.resolve())
    ]
    if not containing:
        raise ServiceError("路径超出允许范围", reason="path_out_of_bounds")
    root = max(containing, key=lambda item: len(item.parts))
    crumbs = [{"label": root.name or str(root), "path": str(root)}]
    cursor = root
    for part in current.relative_to(root).parts:
        cursor = cursor / part
        crumbs.append({"label": part, "path": str(cursor)})
    return crumbs


def _candidate_index_states(candidate: dict[str, Any]) -> dict[str, str]:
    """Return prepared-index states without scanning a dataset or its manifest."""
    artifact_paths = dict(candidate.get("artifact_paths") or {})

    def status_for(
        store: Any,
        *kinds: str,
        metadata_only: bool = False,
    ) -> dict[str, Any]:
        paths = [str(artifact_paths.get(kind) or "") for kind in kinds]
        if not all(path and Path(path).is_file() for path in paths):
            return {"state": "missing"}
        try:
            if metadata_only:
                return store.status(*paths, metadata_only=True)
            return store.status(*paths)
        except FileNotFoundError:
            return {"state": "missing"}
        except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            return {"state": "invalid", "message": str(exc)}

    timeline_path = str(artifact_paths.get("timeline") or "")
    event_path = timeline_path or str(artifact_paths.get("reactionevent") or "")
    molecule_path = str(artifact_paths.get("molecules") or "")
    if event_path and Path(event_path).is_file():
        try:
            event_status = EVENT_EVIDENCE_STORE.status(
                event_path,
                (
                    molecule_path
                    if not timeline_path
                    and molecule_path
                    and Path(molecule_path).is_file()
                    else ""
                ),
                metadata_only=True,
            )
        except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            event_status = {"state": "invalid", "message": str(exc)}
    else:
        event_status = {"state": "missing"}
    return {
        "event": str(
            event_status.get("state") or "missing"
        ),
        "trajectory": str(
            status_for(
                TRAJECTORY_INDEX_STORE,
                "trajectory",
                metadata_only=True,
            ).get("state")
            or "missing"
        ),
        "composition": str(
            status_for(
                SPECIES_COMPOSITION_STORE,
                "species",
                metadata_only=True,
            ).get("state")
            or "missing"
        ),
    }


def browse_dataset_location(path: str) -> dict[str, Any]:
    """Build a read-only directory and dataset-discovery browser snapshot."""
    current = validate_browse_path(path)
    try:
        listing = _core_list_directory(str(current))
        candidates = discover_dataset_candidates(current)
    except DirBrowserError as exc:
        raise ServiceError(exc.message, reason=exc.reason) from exc
    except OSError as exc:
        raise ServiceError(f"读取数据集目录失败: {exc}", reason="read_error") from exc

    datasets: list[dict[str, Any]] = []
    for candidate in candidates:
        datasets.append(
            {
                **candidate,
                "auto_selected": len(candidates) == 1,
                "completeness": f"{candidate['score']}/{len(ARTIFACT_SUFFIXES)}",
                "index_states": _candidate_index_states(candidate),
            }
        )
    return {
        **listing,
        "breadcrumbs": _breadcrumbs_within_allowed_root(current),
        "datasets": datasets,
    }


def resolve_dataset_input(path: str) -> dict[str, str]:
    """Normalise a selected directory or manually entered dataset prefix."""
    raw = Path(str(path or "").strip()).expanduser()
    if raw.is_dir():
        folder = validate_browse_path(str(raw))
        return {"folder": str(folder), "preferred_base": ""}
    folder = validate_browse_path(str(raw.parent))
    if not folder.is_dir():
        raise ServiceError("数据集父目录不存在", reason="missing_folder")
    return {
        "folder": str(folder),
        "preferred_base": str(raw.resolve()),
    }


def normalise_recent_datasets(
    records: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Validate, deduplicate, and cap persisted recent-dataset records."""
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(records, (list, tuple)):
        return []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        folder = str(raw.get("folder") or "").strip()
        base = str(raw.get("base") or "").strip()
        if not folder or not base:
            continue
        try:
            loaded_at = int(raw.get("loaded_at") or 0)
        except (TypeError, ValueError):
            continue
        key = (os.path.abspath(folder), os.path.abspath(base))
        deduped[key] = {
            "folder": key[0],
            "base": key[1],
            "label": str(raw.get("label") or Path(base).name),
            "loaded_at": loaded_at,
        }
    return sorted(deduped.values(), key=lambda item: -item["loaded_at"])[:10]


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def artifacts_from_status(status: dict[str, Any]) -> dict[str, str]:
    """Return a compact ``{kind: path}`` mapping from a dataset status payload."""
    dataset = status.get("dataset", {}) if status else {}
    artifacts = dataset.get("artifacts", {}) or {}
    out: dict[str, str] = {}
    for key in (
        "reaction",
        "species",
        "trajectory",
        "timeline",
        "reactionevent",
        "molecules",
    ):
        item = artifacts.get(key, {}) or {}
        path_text = item.get("path") or ""
        if path_text:
            out[key] = path_text
    return out


def _event_artifact_paths(
    artifacts: Mapping[str, str],
) -> tuple[str, str]:
    """Apply native-timeline precedence to one compact artifact mapping."""

    timeline = str(artifacts.get("timeline") or "").strip()
    if timeline and Path(timeline).is_file():
        return timeline, ""
    reactionevent = str(artifacts.get("reactionevent") or "").strip()
    molecules = str(artifacts.get("molecules") or "").strip()
    return (
        reactionevent,
        molecules if molecules and Path(molecules).is_file() else "",
    )


def dataset_label(status: dict[str, Any]) -> str:
    dataset = status.get("dataset", {}) if status else {}
    return str(dataset.get("label") or "未选择数据集")


def dataset_ready_count(status: dict[str, Any]) -> int:
    dataset = status.get("dataset", {}) if status else {}
    return int(dataset.get("ready_count") or 0)


def dataset_capabilities(status: dict[str, Any]) -> dict[str, bool]:
    dataset = status.get("dataset", {}) if status else {}
    caps = dataset.get("capabilities", {}) or {}
    return {key: bool(caps.get(key)) for key in ("species", "intermediate", "reaction", "events", "evolution", "transition")}


def dataset_readiness(status: dict[str, Any]) -> dict[str, Any]:
    dataset = status.get("dataset", {}) if status else {}
    return dict(dataset.get("readiness", {}) or {})


def dataset_preparation_status(folder: str, *, base: str = "") -> dict[str, Any]:
    """Return the read-only preparation view for one selected dataset."""
    configured_workspace_root = os.environ.get(
        "REACNET_SCOPE_CACHE_DIR", ""
    ).strip()
    status = scan_dataset(folder, base=base)
    dataset = status.get("dataset", {}) or {}
    artifacts = artifacts_from_status(status)
    readiness = dataset_readiness(status)
    selected_base = str(dataset.get("selected_base") or dataset.get("base") or "")
    try:
        paths = (
            resolve_dataset_paths(Path(selected_base).parent, Path(selected_base).name)
            if selected_base
            else None
        )
    except RuntimeError:
        paths = None
    dataset_id = paths.dataset_id if paths else ""
    manifest = dataset.get("manifest", {}) or {}
    events = dict(readiness.get("event_search") or {"state": "missing"})
    trajectory = dict(readiness.get("trajectory_evidence") or {"state": "missing"})
    event_primary, _event_molecules = _event_artifact_paths(artifacts)
    events["source_available"] = bool(event_primary)
    trajectory["source_available"] = bool(artifacts.get("trajectory"))
    species_source = artifacts.get("species", "")
    if species_source and Path(species_source).is_file():
        try:
            composition = SPECIES_COMPOSITION_STORE.status(species_source)
        except RuntimeError as exc:
            composition = {"state": "invalid", "message": str(exc)}
    else:
        composition = {"state": "missing"}
    composition["source_available"] = bool(species_source)
    if selected_base:
        for capability, item in (
            ("event", events),
            ("trajectory", trajectory),
            ("composition", composition),
        ):
            task = preparation.preparation_task_status(
                str(Path(selected_base).parent),
                base=Path(selected_base).name,
                capability=capability,
            )
            if not task:
                continue
            item["task"] = task
            task_state = str(task.get("state") or "")
            if task_state in {"running", "cancel_requested"}:
                item["state"] = "building"
            elif task_state == "interrupted":
                item["task_state"] = "interrupted"
    index_bytes = (
        int(events.get("index_size", 0) or 0)
        + int(trajectory.get("index_size", 0) or 0)
        + int(composition.get("index_size", 0) or 0)
    )
    timestamps = [
        value
        for value in (
            trajectory.get("updated_at_epoch"),
            composition.get("updated_at_epoch"),
            events.get("updated_at_epoch"),
        )
        if value is not None
    ]
    workspace_path = str(paths.workspace_dir) if paths else ""
    for item in (events, trajectory, composition):
        if item.get("workspace_path"):
            workspace_path = str(item["workspace_path"])
            break
    if not workspace_path and manifest.get("path"):
        workspace_path = str(Path(str(manifest["path"])).parent)
    workspace_target = (
        paths.workspace_dir
        if paths
        else Path(configured_workspace_root).expanduser()
        if configured_workspace_root
        else None
    )
    workspace_writable = bool(
        workspace_target
        and inspect_workspace_storage(workspace_target).writable
    )
    trajectory_source = artifacts.get("trajectory", "")
    def preparation_command(
        capability: str,
        item: Mapping[str, Any],
        *,
        available: bool,
    ) -> str:
        if not selected_base or not available:
            return ""
        action = (
            "rebuild"
            if str(item.get("state") or "") in {"ready", "stale", "invalid"}
            else "build"
        )
        return (
            f"reacnet-scope prepare {action} {capability} "
            f"{shlex.quote(str(Path(selected_base).parent))} "
            f"--base {shlex.quote(Path(selected_base).name)}"
        )
    return {
        "dataset_id": dataset_id,
        "base": selected_base,
        "manifest_path": str(manifest.get("path") or ""),
        "manifest_found": bool(manifest.get("found")),
        "workspace_path": workspace_path,
        "workspace_resolved": bool(paths),
        "workspace_writable": workspace_writable,
        "index_bytes": index_bytes,
        "last_updated_epoch": max(timestamps) if timestamps else None,
        "basic": dict(readiness.get("basic_analysis") or {"state": "missing"}),
        "events": events,
        "trajectory": trajectory,
        "composition": composition,
        "rng_event_command": "--reaction-event --show-molecule-time",
        "event_command": preparation_command(
            "event",
            events,
            available=bool(event_primary),
        ),
        "trajectory_command": preparation_command(
            "trajectory",
            trajectory,
            available=bool(trajectory_source),
        ),
        "composition_command": preparation_command(
            "element-distribution",
            composition,
            available=bool(species_source),
        ),
    }


def prepare_dataset_workspace(
    folder: str,
    *,
    base: str,
    kind: str,
) -> dict[str, Any]:
    """Build or rebuild one derived index in its Dataset Workspace."""
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"event", "trajectory", "composition"}:
        raise ServiceError("无效准备能力", reason="invalid_preparation_kind")

    folder_path = validate_browse_path(folder)
    if not folder_path.is_dir():
        raise ServiceError("数据集目录不存在", reason="missing_folder")
    base_path = validate_browse_path(base)
    candidate_bases = {
        str(Path(item.get("base") or "").resolve())
        for item in discover_dataset_candidates(folder_path)
    }
    if str(base_path) not in candidate_bases:
        raise ServiceError(
            "所选数据集已不存在，请重新选择。",
            reason="invalid_dataset_candidate",
        )

    before = dataset_preparation_status(
        str(folder_path),
        base=str(base_path),
    )
    if not before.get("workspace_writable"):
        raise ServiceError(
            "Dataset Workspace 不可写；请检查数据集目录或管理员配置的集中位置。",
            reason="workspace_not_writable",
        )
    item_key = {
        "event": "events",
        "trajectory": "trajectory",
        "composition": "composition",
    }[normalized_kind]
    item = before.get(item_key) or {}
    if not item.get("source_available"):
        labels = {
            "event": ".timeline.h5 或 .reactionevent.csv",
            "trajectory": "轨迹",
            "composition": ".species",
        }
        raise ServiceError(
            f"当前数据集缺少 {labels[normalized_kind]} 源文件。",
            reason="missing_source",
        )

    previous_state = str(item.get("state") or "missing")
    rebuild = previous_state in {"ready", "stale", "invalid"}
    capability = (
        "element-distribution"
        if normalized_kind == "composition"
        else normalized_kind
    )
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            exit_code = preparation.run_preparation(
                action="rebuild" if rebuild else "build",
                capability=capability,
                case=str(folder_path),
                base=base_path.name,
            )
    except IndexBuildInProgressError as exc:
        raise ServiceError(
            "同类索引正在由另一个 Preparation Task 构建。",
            reason="index_building",
        ) from exc
    except SystemExit as exc:
        raise ServiceError(
            f"准备参数无效（退出码 {exc.code}）。",
            reason="preparation_failed",
        ) from exc
    except Exception as exc:
        raise ServiceError(
            f"Preparation Task 失败: {exc}",
            reason="preparation_failed",
        ) from exc
    if int(exit_code or 0) == 130:
        after = dataset_preparation_status(
            str(folder_path),
            base=str(base_path),
        )
        return {
            "ok": True,
            "kind": normalized_kind,
            "canceled": True,
            "status": after.get(item_key) or {},
            "dataset_id": after.get("dataset_id") or "",
            "log_tail": [
                line
                for line in output.getvalue().splitlines()
                if line.strip()
            ][-8:],
        }
    if int(exit_code or 0) != 0:
        raise ServiceError(
            f"Preparation Task 失败（退出码 {exit_code}）。",
            reason="preparation_failed",
        )

    after = dataset_preparation_status(
        str(folder_path),
        base=str(base_path),
    )
    result = after.get(item_key) or {}
    if str(result.get("state") or "") != "ready":
        task = preparation.preparation_task_status(
            str(folder_path),
            base=base_path.name,
            capability=normalized_kind,
        )
        if str(task.get("state") or "") in {"running", "cancel_requested"}:
            return {
                "ok": True,
                "kind": normalized_kind,
                "existing_task": task,
                "status": result,
                "dataset_id": after.get("dataset_id") or "",
                "log_tail": [
                    line
                    for line in output.getvalue().splitlines()
                    if line.strip()
                ][-8:],
            }
        raise ServiceError(
            "Preparation Task 已结束，但索引尚未达到就绪状态；请查看状态详情。",
            reason="preparation_incomplete",
        )
    log_lines = [line for line in output.getvalue().splitlines() if line.strip()]
    return {
        "ok": True,
        "kind": normalized_kind,
        "rebuilt": rebuild,
        "previous_state": previous_state,
        "status": result,
        "dataset_id": after.get("dataset_id") or "",
        "log_tail": log_lines[-8:],
    }


def cancel_dataset_preparation(
    folder: str,
    *,
    base: str,
    kind: str = "all",
) -> dict[str, Any]:
    """Request checkpoint-aware cancellation through the persisted task."""
    normalized = str(kind or "all").strip().lower()
    if normalized not in {"event", "trajectory", "composition", "all"}:
        raise ServiceError("无效准备能力", reason="invalid_preparation_kind")
    folder_path = validate_browse_path(folder)
    base_path = validate_browse_path(base)
    candidate_bases = {
        str(Path(item.get("base") or "").resolve())
        for item in discover_dataset_candidates(folder_path)
    }
    if str(base_path) not in candidate_bases:
        raise ServiceError(
            "所选数据集已不存在，请重新选择。",
            reason="invalid_dataset_candidate",
        )
    canceled = preparation.request_cancellation(
        str(folder_path),
        base=base_path.name,
        capability=normalized,
    )
    return {
        "ok": True,
        "kind": normalized,
        "cancellation_requested": bool(canceled),
        "message": (
            "已请求取消 Preparation Task；最近检查点会保留。"
            if canceled
            else "当前没有活动的 Preparation Task。"
        ),
    }


def clear_dataset_index(folder: str, *, base: str = "", kind: str) -> dict[str, Any]:
    """Safely clear one index through the shared preparation-layer API."""
    status = scan_dataset(folder, base=base)
    artifacts = artifacts_from_status(status)
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"trajectory", "event", "composition"}:
        raise ServiceError("无效索引类型", reason="invalid_index_kind")

    source_key = {
        "trajectory": "trajectory",
        "event": "reactionevent",
        "composition": "species",
    }[normalized_kind]
    if normalized_kind == "event":
        source, event_molecules = _event_artifact_paths(artifacts)
    else:
        source = artifacts.get(source_key, "")
        event_molecules = ""
    if not source or not Path(source).is_file():
        raise ServiceError("当前数据集缺少对应源文件", reason="missing_source")
    try:
        if normalized_kind == "trajectory":
            return clear_index(source, kind=normalized_kind)
        if normalized_kind == "event":
            return EVENT_EVIDENCE_STORE.clear(
                source,
                event_molecules,
            )

        before = SPECIES_COMPOSITION_STORE.status(
            source,
            metadata_only=True,
        )
        removed = SPECIES_COMPOSITION_STORE.clear(source)
        return {
            "kind": "composition",
            "index_path": str(before.get("index_path") or ""),
            "removed": removed,
            "released_bytes": int(before.get("index_size", 0) or 0),
        }
    except IndexBuildInProgressError as exc:
        raise ServiceError("索引正在由离线准备程序构建；请先停止该程序后再清理。", reason="index_building") from exc
    except Exception as exc:
        raise ServiceError(f"清理索引失败: {exc}", reason="clear_failed") from exc


def candidates_from_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = status.get("dataset", {}) if status else {}
    return list(dataset.get("candidates", []) or [])
