"""Adapter layer that wraps existing analysis functions for the Dash UI.

This module never reimplements analysis logic.  It only:

* normalizes Dash-side inputs into the ``dict[str, list[str]]`` param shape
  that ``scripts.webapp.server`` payload builders expect,
* converts the returned payloads into compact structures suitable for AG
  Grid, Plotly and Cytoscape, and
* normalizes exceptions into structured error dictionaries so callbacks can
  surface concrete reasons via ``dbc.Alert`` instead of crashing the page.
"""

from __future__ import annotations

import base64
import csv
import io
import os
import re
import shlex
import sqlite3
import sys
import time
import traceback
from functools import lru_cache
from bisect import bisect_left, bisect_right
from pathlib import Path
from collections import Counter
from typing import Any, Iterable, Mapping
from urllib.parse import quote

# Ensure the project tool root is importable when this package is loaded
# directly (e.g. via ``uv run reacnet-scope-web-dash``).
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from rng_tools.network import ReactionNetwork, count_atoms_fast, formula_from_counts, parse_reactionabcd  # noqa: E402
from rng_tools.pathways import find_candidate_paths  # noqa: E402
from rng_tools.reaction import canonical_smiles  # noqa: E402
from reacnet_scope.indexes import (  # noqa: E402
    IndexBuildInProgressError,
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    clear_index,
    resolve_dataset_paths,
    ROUTE_INDEX_STORE,
    TRAJECTORY_INDEX_STORE,
)
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE  # noqa: E402
from reacnet_scope.event_index import (  # noqa: E402
    EVENT_EVIDENCE_STORE,
    EventIndexEvidenceProvider,
)
from reacnet_scope.rng_events import (  # noqa: E402
    canonical_reaction_key,
    reaction_key,
)
from reacnet_scope.datasets import (  # noqa: E402
    ARTIFACT_SUFFIXES,
    discover_dataset_candidates,
)
from scripts.webapp.server import (  # noqa: E402
    ReactionSourceChangedError,
    STORE,
    build_dataset_status_payload,
    build_carbon_plot_payload,
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
    parse_lammpstrj_frame_block,
    _group_reaction_hits_by_time,
    _prepare_reaction_query,
)


class ServiceError(Exception):
    """Raised with a user-facing message when an adapter call cannot proceed."""

    def __init__(self, message: str, *, reason: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


def _error_dict(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ServiceError):
        return {"ok": False, "reason": exc.reason, "message": exc.message}
    return {
        "ok": False,
        "reason": "error",
        "message": str(exc) or exc.__class__.__name__,
        "traceback": traceback.format_exc(limit=4),
    }


# ---------------------------------------------------------------------------
# Dataset / folder management
# ---------------------------------------------------------------------------


def scan_dataset(folder: str, *, base: str = "") -> dict[str, Any]:
    """Scan a data folder and return the dataset status payload.

    Mirrors the legacy ``GET /api/dataset_status`` flow but accepts plain
    strings instead of multi-value query params.
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
        reactionevent = str(
            (artifacts.get("reactionevent") or {}).get("path") or ""
        )
        molecules = str((artifacts.get("molecules") or {}).get("path") or "")
        if reactionevent:
            try:
                event_status = EVENT_EVIDENCE_STORE.status(
                    reactionevent,
                    molecules if molecules and Path(molecules).is_file() else "",
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
                prepare_option = (
                    "--rebuild event"
                    if state in {"stale", "invalid"}
                    else "--event-only"
                )
                event_status["preparation_command"] = (
                    "reacnet-scope-prepare "
                    f"{shlex.quote(str(Path(reactionevent).parent))} "
                    f"{prepare_option}"
                )
            readiness = dataset.setdefault("readiness", {})
            readiness["event_search"] = event_status
        return payload
    except Exception as exc:
        raise ServiceError(f"扫描数据目录失败: {exc}") from exc


# ---------------------------------------------------------------------------
# Directory browser for remote server file system navigation
# ---------------------------------------------------------------------------
# Core logic lives in ``rng_tools.dir_browser`` (zero Dash dependency)
# so that CI can import and test it without the full web stack.
# This module re-exports thin adapters that translate
# ``rng_tools.dir_browser.DirBrowserError`` into ``ServiceError``.

from rng_tools.dir_browser import (  # noqa: E402
    ALLOWED_ROOTS,
    DirBrowserError,
    get_allowed_roots as _get_allowed_roots,
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
        for root in ALLOWED_ROOTS
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

    event_path = str(artifact_paths.get("reactionevent") or "")
    molecule_path = str(artifact_paths.get("molecules") or "")
    if event_path and Path(event_path).is_file():
        try:
            event_status = EVENT_EVIDENCE_STORE.status(
                event_path,
                (
                    molecule_path
                    if molecule_path and Path(molecule_path).is_file()
                    else ""
                ),
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
    for key in ("reaction", "species", "moname", "trajectory", "route", "reactionevent", "molecules"):
        item = artifacts.get(key, {}) or {}
        path_text = item.get("path") or ""
        if path_text:
            out[key] = path_text
    return out


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
    species_source = artifacts.get("species", "")
    if species_source and Path(species_source).is_file():
        try:
            composition = SPECIES_COMPOSITION_STORE.status(species_source)
        except RuntimeError as exc:
            composition = {"state": "invalid", "message": str(exc)}
    else:
        composition = {"state": "missing"}
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
    cache_dir = str(paths.cache_dir) if paths else ""
    for item in (events, trajectory, composition):
        if item.get("cache_dir"):
            cache_dir = str(item["cache_dir"])
            break
    if not cache_dir and manifest.get("path"):
        cache_dir = str(Path(str(manifest["path"])).parent)
    trajectory_source = artifacts.get("trajectory", "")
    command_prefix = ""
    if selected_base:
        command_prefix = (
            f"uv run reacnet-scope-prepare {shlex.quote(str(Path(selected_base).parent))} "
            f"--base {shlex.quote(Path(selected_base).name)}"
        )
    return {
        "dataset_id": dataset_id,
        "base": selected_base,
        "manifest_path": str(manifest.get("path") or ""),
        "manifest_found": bool(manifest.get("found")),
        "cache_dir": cache_dir,
        "index_bytes": index_bytes,
        "last_updated_epoch": max(timestamps) if timestamps else None,
        "basic": dict(readiness.get("basic_analysis") or {"state": "missing"}),
        "events": events,
        "trajectory": trajectory,
        "composition": composition,
        "rng_event_command": "--reaction-event --show-molecule-time",
        "event_command": (
            (
                f"{command_prefix} --rebuild event"
                if events.get("state") in {"stale", "invalid"}
                else f"{command_prefix} --event-only"
            )
            if artifacts.get("reactionevent")
            else ""
        ),
        "trajectory_command": f"{command_prefix} --trajectory-only" if trajectory_source else "",
        "composition_command": f"{command_prefix} --composition-only" if species_source else "",
    }


def clear_dataset_index(folder: str, *, base: str = "", kind: str) -> dict[str, Any]:
    """Safely clear one index through the shared preparation-layer API."""
    status = scan_dataset(folder, base=base)
    artifacts = artifacts_from_status(status)
    normalized_kind = str(kind or "").strip().lower()
    source = artifacts.get("route", "") if normalized_kind == "route" else artifacts.get("trajectory", "")
    if normalized_kind not in {"route", "trajectory"}:
        raise ServiceError("无效索引类型", reason="invalid_index_kind")
    if not source or not Path(source).is_file():
        raise ServiceError("当前数据集缺少对应源文件", reason="missing_source")
    try:
        return clear_index(source, kind=normalized_kind)
    except IndexBuildInProgressError as exc:
        raise ServiceError("索引正在由离线准备程序构建；请先停止该程序后再清理。", reason="index_building") from exc
    except Exception as exc:
        raise ServiceError(f"清理索引失败: {exc}", reason="clear_failed") from exc


def candidates_from_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = status.get("dataset", {}) if status else {}
    return list(dataset.get("candidates", []) or [])


# ---------------------------------------------------------------------------
# Species search (formula / SMILES / mass)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _load_moname_catalog(path_text: str, size: int, mtime_ns: int) -> dict[str, dict[str, Any]]:
    """Return compact, optional structure evidence grouped by exact SMILES.

    ReacNetGenerator's historical ``.moname`` format is deliberately treated
    as supplementary evidence: the species catalogue is always sourced from
    ``.species`` and this parser never attempts to infer a molecular identity
    from atom IDs alone.
    """
    del size, mtime_ns
    out: dict[str, dict[str, Any]] = {}
    with open(path_text, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            parts = raw.strip().split(None, 2)
            if not parts:
                continue
            smiles = parts[0]
            record = out.setdefault(
                smiles,
                {"moname_occurrences": 0, "moname_atom_count": 0, "moname_bond_count": 0},
            )
            record["moname_occurrences"] += 1
            if len(parts) > 1:
                atom_ids = [item for item in re.split(r"[;,]+", parts[1]) if item.strip()]
                record["moname_atom_count"] = max(int(record["moname_atom_count"]), len(atom_ids))
            if len(parts) > 2:
                bonds = []
                for item in (value.strip() for value in parts[2].split(";")):
                    if not item:
                        continue
                    comma_fields = [value.strip() for value in item.split(",")]
                    if len(comma_fields) == 3 and all(comma_fields):
                        bonds.append(item)
                        continue
                    # Retain compatibility with older generated fixtures/files.
                    hyphen_fields = [value.strip() for value in item.split("-")]
                    if len(hyphen_fields) == 3 and all(hyphen_fields):
                        bonds.append(item)
                record["moname_bond_count"] = max(int(record["moname_bond_count"]), len(bonds))
    return out


def _moname_catalog(path_text: str) -> dict[str, dict[str, Any]]:
    path = Path(path_text)
    if not path.is_file():
        return {}
    stat = path.stat()
    return _load_moname_catalog(str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))


def _file_signature(path_text: str) -> tuple[str, int, int]:
    """Return a cache-safe signature for an optional artifact file."""
    path = Path(path_text).expanduser()
    if not path.is_file():
        return "", 0, 0
    stat = path.stat()
    return str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns)


def _pathway_formula(smiles: str) -> str:
    return formula_from_counts(count_atoms_fast(smiles))


def _pathway_source_snapshot(
    path_text: str,
) -> dict[str, Any]:
    return reaction_source_signature(
        str(Path(path_text).expanduser().resolve())
    )


def _pathway_assert_source_current(
    path_text: str,
    expected: Mapping[str, Any],
) -> None:
    try:
        actual = _pathway_source_snapshot(path_text)
    except OSError as exc:
        raise ServiceError(
            "reactionabcd 文件在路径查询期间发生变化，请重试",
            reason="reaction_source_stale",
        ) from exc
    except ReactionSourceChangedError as exc:
        raise ServiceError(
            "reactionabcd 文件在路径查询期间发生变化，请重试",
            reason="reaction_source_stale",
        ) from exc
    if actual.get("sha256") != expected.get("sha256"):
        raise ServiceError(
            "reactionabcd 文件在路径查询期间发生变化，请重试",
            reason="reaction_source_stale",
        )


def _load_reaction_network_snapshot(
    reaction_path: str,
    min_tp: int,
) -> tuple[ReactionNetwork, dict[str, Any]]:
    """Load a network and its exact content signature as one snapshot."""
    get_with_signature = getattr(STORE, "get_with_signature", None)
    if callable(get_with_signature):
        network, signature = get_with_signature(reaction_path, min_tp)
        if not isinstance(signature, Mapping):
            raise RuntimeError("reaction source signature is invalid")
        return network, dict(signature)

    # A historical ``get`` result has no verifiable content provenance.  Parse
    # a fresh captured byte snapshot instead of pairing an opaque cached object
    # with the digest of whatever happens to be at the path now.
    return load_reaction_network_snapshot(reaction_path, min_tp)


def _reaction_min_tp(artifacts: Mapping[str, Any]) -> int:
    """Return the session-level reaction throughput threshold."""
    try:
        return max(1, int(artifacts.get("_min_tp") or 1))
    except (TypeError, ValueError):
        return 1


def _pathway_preparation_command(
    reaction_path: str,
    reactionevent_path: str,
    *,
    rebuild: bool,
) -> str:
    source = reactionevent_path or reaction_path
    option = "--rebuild event" if rebuild else "--event-only"
    return (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(source).parent))} "
        f"{option}"
    )


_PATHWAY_QUERY_KEYS = {
    "direction",
    "max_depth",
    "max_branches",
    "max_paths",
    "max_expansions",
    "min_net_tp",
    "min_directionality",
    "target_max_carbon",
    "evidence_mode",
}


def find_pathways(
    artifacts: dict[str, str],
    start_smiles: str,
    **limits: Any,
) -> dict[str, Any]:
    """Find candidate paths, linking a ready SQLite event index if present."""
    query_limits = dict(limits)
    evidence_mode = str(
        query_limits.pop("evidence_mode", "auto") or "auto"
    )
    if evidence_mode not in {"auto", "network_only"}:
        raise ServiceError(
            "evidence_mode 必须是 auto 或 network_only",
            reason="bad_pathway_query",
        )
    reaction_path = (artifacts.get("reaction") or "").strip()
    if (
        not reaction_path.lower().endswith(".reactionabcd")
        or not Path(reaction_path).is_file()
    ):
        raise ServiceError(
            "需要 .reactionabcd 文件",
            reason="missing_reac",
        )

    unknown_limits = sorted(set(limits) - _PATHWAY_QUERY_KEYS)
    if unknown_limits:
        raise ServiceError(
            f"无效的路径查询参数: {', '.join(unknown_limits)}",
            reason="bad_pathway_query",
        )

    try:
        network, reaction_signature = _load_reaction_network_snapshot(
            reaction_path,
            _reaction_min_tp(artifacts),
        )
        _pathway_assert_source_current(reaction_path, reaction_signature)
    except FileNotFoundError as exc:
        raise ServiceError(
            "需要 .reactionabcd 文件",
            reason="missing_reac",
        ) from exc
    except ReactionSourceChangedError as exc:
        raise ServiceError(
            "reactionabcd 文件在路径查询期间发生变化，请重试",
            reason="reaction_source_stale",
        ) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ServiceError(
            f"无法加载反应网络: {exc}",
            reason="bad_reac",
        ) from exc

    reactionevent_path = (artifacts.get("reactionevent") or "").strip()
    molecules_path = (artifacts.get("molecules") or "").strip()
    evidence_provider: EventIndexEvidenceProvider | None = None
    rebuild_event_index = False
    if (
        evidence_mode == "auto"
        and reactionevent_path
        and Path(reactionevent_path).is_file()
    ):
        molecules_path = (
            molecules_path
            if molecules_path and Path(molecules_path).is_file()
            else ""
        )
        try:
            opened = EVENT_EVIDENCE_STORE.open_required(
                reactionevent_path,
                molecules_path,
            )
            evidence_provider = EventIndexEvidenceProvider(
                reactionevent_path,
                molecules_path,
                store=EVENT_EVIDENCE_STORE,
                opened=opened,
            )
        except (IndexStaleError, IndexInvalidError):
            rebuild_event_index = True
        except IndexNotReadyError:
            rebuild_event_index = False

    try:
        try:
            result = find_candidate_paths(
                network,
                start_smiles,
                evidence_provider=evidence_provider,
                **query_limits,
            )
            if evidence_provider is not None:
                evidence_provider.assert_current()
        except IndexNotReadyError:
            evidence_provider = None
            rebuild_event_index = True
            result = find_candidate_paths(
                network,
                start_smiles,
                evidence_provider=None,
                **query_limits,
            )
    except (TypeError, ValueError) as exc:
        message = str(exc)
        if isinstance(exc, TypeError) or any(
            name in message for name in _PATHWAY_QUERY_KEYS
        ):
            raise ServiceError(
                f"无效的路径查询参数: {message}",
                reason="bad_pathway_query",
            ) from exc
        raise

    _pathway_assert_source_current(reaction_path, reaction_signature)
    payload = result.as_dict()
    payload.setdefault("query", {})["evidence_mode"] = evidence_mode
    payload["search_stage"] = (
        "network_shortlist"
        if evidence_mode == "network_only"
        else "evidence_ranked"
    )
    payload["evidence_deferred"] = evidence_mode == "network_only"
    target_max_carbon = payload.get("query", {}).get("target_max_carbon")
    max_depth = int(payload.get("query", {}).get("max_depth") or 0)
    for path in payload["paths"]:
        path["formulas"] = [
            _pathway_formula(smiles)
            for smiles in path["species"]
        ]
        for step in path["steps"]:
            step["focal_input_formula"] = _pathway_formula(
                step["focal_input"]
            )
            step["focal_output_formula"] = _pathway_formula(
                step["focal_output"]
            )
            step["reactant_formulas"] = [
                _pathway_formula(smiles)
                for smiles in step["reactants"]
            ]
            step["product_formulas"] = [
                _pathway_formula(smiles)
                for smiles in step["products"]
            ]
        _annotate_pathway_endpoints(
            path,
            direction=str(
                payload.get("query", {}).get("direction") or "downstream"
            ),
            target_max_carbon=(
                int(target_max_carbon)
                if target_max_carbon is not None
                else None
            ),
            max_depth=max_depth,
            search_truncated=bool(payload.get("truncated")),
        )

    payload["source_signatures"] = {
        "reactionabcd": reaction_signature,
        **dict(payload["source_signatures"]),
    }
    if evidence_provider is None and evidence_mode == "auto":
        payload["preparation_command"] = _pathway_preparation_command(
            reaction_path,
            reactionevent_path,
            rebuild=rebuild_event_index,
        )
    return payload


def _pathway_species_summary(smiles: str) -> dict[str, Any]:
    carbon_count = int(count_atoms_fast(smiles).get("C", 0))
    return {
        "smiles": smiles,
        "formula": _pathway_formula(smiles),
        "carbon_count": carbon_count,
        "is_small_carbon_fragment": 0 < carbon_count <= 4,
        "structure_url": (
            "/api/structure.svg?"
            f"smiles={quote(smiles, safe='')}&width=150&height=104&show_h=1"
        ),
    }


def _annotate_pathway_endpoints(
    path: dict[str, Any],
    *,
    direction: str,
    target_max_carbon: int | None,
    max_depth: int,
    search_truncated: bool,
) -> None:
    """Expose full terminal hyperedge products and route-ending semantics."""
    steps = path.get("steps") or []
    species = [str(value) for value in path.get("species") or []]
    terminal_smiles = species[-1] if species else ""
    path["terminal_species"] = (
        _pathway_species_summary(terminal_smiles)
        if terminal_smiles
        else None
    )
    terminal_carbon = int(
        (path.get("terminal_species") or {}).get("carbon_count") or 0
    )
    if (
        target_max_carbon is not None
        and 0 < terminal_carbon <= target_max_carbon
    ):
        ending = "small_molecule_goal"
    elif search_truncated and len(steps) < max_depth:
        ending = "search_truncated"
    elif len(steps) >= max_depth:
        ending = "depth_limit"
    else:
        ending = "no_positive_continuation"
    path["termination_reason"] = ending

    last_step = steps[-1] if steps else {}
    terminal_side = (
        last_step.get("reactants")
        if direction == "upstream"
        else last_step.get("products")
    ) or []
    path["terminal_products"] = [
        _pathway_species_summary(str(smiles))
        for smiles in terminal_side
    ]

    fragments: list[dict[str, Any]] = []
    seen: set[str] = set()
    fragmentation_steps: list[int] = []
    for step_index, step in enumerate(steps, start=1):
        focal_input = str(step.get("focal_input") or "")
        input_carbon = int(count_atoms_fast(focal_input).get("C", 0))
        output_side = (
            step.get("reactants")
            if direction == "upstream"
            else step.get("products")
        ) or []
        step_fragmented = False
        for raw_smiles in output_side:
            summary = _pathway_species_summary(str(raw_smiles))
            carbon_count = int(summary["carbon_count"])
            if (
                summary["is_small_carbon_fragment"]
                and input_carbon > carbon_count
            ):
                step_fragmented = True
                if summary["smiles"] not in seen:
                    seen.add(summary["smiles"])
                    fragments.append(summary)
        if step_fragmented:
            fragmentation_steps.append(step_index)
    path["small_fragments"] = fragments
    path["fragmentation_step_indices"] = fragmentation_steps
    path["has_fragmentation"] = bool(fragmentation_steps)


def _pathway_species_node_id(smiles: str) -> str:
    encoded = base64.urlsafe_b64encode(smiles.encode("utf-8")).decode("ascii")
    return f"species:{encoded}"


def build_pathway_elements(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build a lossless bipartite Cytoscape payload from serialized paths.

    Species identity is the exact SMILES string.  Reaction nodes remain
    path-local because the same reaction may occur at different ranks/steps.
    Repeated reactants/products intentionally produce repeated edges.
    """
    species_classes: dict[str, set[str]] = {}
    species_order: list[str] = []
    reaction_nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for path in payload.get("paths") or []:
        rank = int(path.get("rank") or 0)
        rank_class = f"path-rank-{rank}"
        for step_index, step in enumerate(path.get("steps") or [], start=1):
            reaction_key = str(step.get("reaction_key") or "")
            encoded_key = base64.urlsafe_b64encode(
                reaction_key.encode("utf-8")
            ).decode("ascii")
            reaction_id = (
                f"reaction:{encoded_key}:path-{rank}-step-{step_index}"
            )
            reactants = [str(value) for value in step.get("reactants") or []]
            products = [str(value) for value in step.get("products") or []]
            reaction_text = (
                f"{' + '.join(reactants)} -> {' + '.join(products)}"
            )
            classes = {"reaction", rank_class}
            network_only = step.get("evidence_status") == "network_only"
            if network_only:
                classes.add("network-only")
            reaction_nodes.append(
                {
                    "data": {
                        "id": reaction_id,
                        "node_kind": "reaction",
                        "label": f"R{rank}.{step_index}",
                        "path_rank": rank,
                        "step_index": step_index,
                        "reaction_key": reaction_key,
                        "reaction_text": reaction_text,
                        "score": step.get("score"),
                        "evidence_status": step.get("evidence_status"),
                    },
                    "classes": " ".join(sorted(classes)),
                }
            )
            for side, members in (("reactant", reactants), ("product", products)):
                for occurrence, smiles in enumerate(members, start=1):
                    if smiles not in species_classes:
                        species_classes[smiles] = {"species"}
                        species_order.append(smiles)
                    species_classes[smiles].add(rank_class)
                    species_id = _pathway_species_node_id(smiles)
                    edge_id = (
                        f"edge:{rank}:{step_index}:{side}:{occurrence}:"
                        f"{encoded_key}"
                    )
                    if side == "reactant":
                        source, target = species_id, reaction_id
                    else:
                        source, target = reaction_id, species_id
                    edges.append(
                        {
                            "data": {
                                "id": edge_id,
                                "source": source,
                                "target": target,
                                "path_rank": rank,
                                "step_index": step_index,
                                "side": side,
                                "occurrence": occurrence,
                            },
                            "classes": " ".join(
                                [
                                    rank_class,
                                    side,
                                    *(["network-only"] if network_only else []),
                                ]
                            ),
                        }
                    )
        for item in path.get("terminal_products") or []:
            smiles = str(item.get("smiles") or "")
            if smiles and smiles in species_classes:
                species_classes[smiles].add("terminal-product")
                if item.get("is_small_carbon_fragment"):
                    species_classes[smiles].add("small-fragment")
    species_nodes = [
        {
            "data": {
                "id": _pathway_species_node_id(smiles),
                "node_kind": "species",
                "label": _pathway_formula(smiles) or smiles,
                "formula": _pathway_formula(smiles),
                "smiles": smiles,
            },
            "classes": " ".join(sorted(species_classes[smiles])),
        }
        for smiles in species_order
    ]
    return [*species_nodes, *reaction_nodes, *edges]


def _species_catalog_entry(
    smiles: str,
    total_count: int,
    evidence: dict[str, Any] | None = None,
    *,
    catalog_source: str = ".species",
) -> dict[str, Any]:
    """Build one catalogue row, calculating chemistry only for that SMILES."""
    formula = smiles_formula_cached(smiles) or "?"
    mass_fields = formula_mass_fields(formula) if formula != "?" else {}
    evidence = evidence or {}
    return {
        "smiles": smiles,
        "formula": formula,
        "exact_mass": mass_fields.get("exact_mass"),
        "nominal_mass": mass_fields.get("nominal_mass"),
        "total_count": int(total_count),
        "catalog_source": catalog_source,
        "structure_source": (
            ".moname"
            if evidence
            else (".reactionabcd" if catalog_source == ".reactionabcd" else "SMILES")
        ),
        "moname_available": bool(evidence),
        "moname_occurrences": int(evidence.get("moname_occurrences") or 0),
        "moname_atom_count": int(evidence.get("moname_atom_count") or 0),
        "moname_bond_count": int(evidence.get("moname_bond_count") or 0),
    }


@lru_cache(maxsize=100_000)
def _canonical_smiles_cached(smiles: str) -> str:
    return canonical_smiles(smiles) or ""


def _reaction_catalog_matches(
    artifacts: Mapping[str, Any],
    query: str,
    *,
    existing: set[str],
) -> list[dict[str, Any]]:
    """Recover transient reaction-network species absent from snapshots."""
    reaction_path = str(artifacts.get("reaction") or "").strip()
    if not reaction_path or not Path(reaction_path).is_file():
        return []
    query_canonical = _canonical_smiles_cached(query)
    if not query_canonical:
        return []
    try:
        network = STORE.get(reaction_path, _reaction_min_tp(artifacts))
    except Exception:
        return []
    matches: list[dict[str, Any]] = []
    for smiles in network.species:
        if smiles in existing:
            continue
        if (
            smiles == query
            or _canonical_smiles_cached(smiles) == query_canonical
        ):
            matches.append(
                _species_catalog_entry(
                    smiles,
                    0,
                    catalog_source=".reactionabcd",
                )
            )
    return matches


@lru_cache(maxsize=8)
def _load_species_search_catalog(
    species_path_text: str,
    species_size: int,
    species_mtime_ns: int,
    moname_path_text: str,
    moname_size: int,
    moname_mtime_ns: int,
) -> tuple[dict[str, Any], ...]:
    """Materialize formula/mass metadata once per input-file revision.

    A 10k+ species catalogue must only pay the RDKit formula/mass cost once;
    later searches filter this cached metadata and the cache expires if either
    source file changes.
    """
    del species_size, species_mtime_ns, moname_size, moname_mtime_ns
    totals = collect_species_totals(species_path_text)
    moname = _moname_catalog(moname_path_text) if moname_path_text else {}
    catalog: list[dict[str, Any]] = []
    for smiles, total_count in totals.items():
        catalog.append(_species_catalog_entry(smiles, int(total_count), moname.get(smiles)))
    return tuple(catalog)


def _structure_markdown(smiles: str) -> str:
    """Return a same-origin SVG preview URL accepted by Dash DataTable."""
    if not smiles:
        return ""
    return f"![{smiles}](/api/structure.svg?smiles={quote(smiles, safe='')})"


def _aggregate_mass_rows_by_formula(
    rows: Iterable[dict[str, Any]],
    *,
    activity_field: str,
    summed_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Collapse mass-search structures into one result per molecular formula.

    The most active structure remains in ``smiles`` as the representative so
    existing detail/pathway actions can still operate on a concrete species.
    Formula-level activity fields are summed across every matching structure.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        formula = str(row.get("formula") or "")
        if formula:
            grouped.setdefault(formula, []).append(row)

    aggregated: list[dict[str, Any]] = []
    for formula, members in grouped.items():
        representative = min(
            members,
            key=lambda row: (
                -int(row.get(activity_field) or 0),
                str(row.get("smiles") or ""),
            ),
        )
        result = dict(representative)
        result["formula"] = formula
        result["structure_count"] = len(members)
        result["representative_activity"] = int(
            representative.get(activity_field) or 0
        )
        for field in summed_fields:
            result[field] = sum(int(row.get(field) or 0) for row in members)
        aggregated.append(result)
    return aggregated


def search_species_catalog(
    artifacts: dict[str, str],
    query: str = "",
    *,
    kind: str = "auto",
    mass_tolerance: float = 0.5,
) -> dict[str, Any]:
    """Search the ``.species``-derived target-species catalogue.

    The snapshot catalogue remains authoritative for abundance.  An optional
    reaction network is consulted only when an exact/canonical SMILES is
    absent from every snapshot, so transient channel species remain selectable
    with an explicit zero snapshot abundance and source label.  Mass searches
    return one aggregate row per matching molecular formula.  Mass matching
    always uses exact mass and all matching rows are returned.
    """
    species_path = (artifacts.get("species") or "").strip()
    if not species_path or not Path(species_path).is_file():
        raise ServiceError("缺少 .species 数据文件，无法建立物种目录", reason="missing_species_file")
    text = (query or "").strip()
    effective_kind = kind if kind and kind != "auto" else (detect_query_kind(text) if text else "all")
    if effective_kind not in {"all", "formula", "smiles", "mass"}:
        raise ServiceError(f"未知查询类型: {effective_kind}", reason="bad_kind")
    target_mass: float | None = None
    query_canonical = (
        _canonical_smiles_cached(text)
        if effective_kind == "smiles" and text
        else ""
    )
    if effective_kind == "mass":
        try:
            target_mass = float(text)
        except ValueError as exc:
            raise ServiceError(f"无效的质量数: {text}", reason="bad_mass") from exc

    species_signature = _file_signature(species_path)
    moname_signature = _file_signature((artifacts.get("moname") or "").strip())
    network_fallback = False
    canonical_match = False
    if effective_kind in {"all", "smiles"}:
        totals = collect_species_totals(species_signature[0])
        fast_items = [
            (smiles, total_count)
            for smiles, total_count in totals.items()
            if effective_kind == "all" or text.lower() in smiles.lower()
        ]
        if effective_kind == "smiles" and text and not fast_items:
            if query_canonical:
                fast_items = [
                    (smiles, total_count)
                    for smiles, total_count in totals.items()
                    if _canonical_smiles_cached(smiles) == query_canonical
                ]
                canonical_match = bool(fast_items)
        matching_count = len(fast_items)
        fast_items.sort(key=lambda item: (-int(item[1]), item[0]))
        catalog = tuple(
            _species_catalog_entry(smiles, int(total_count))
            for smiles, total_count in fast_items
        )
        if effective_kind == "smiles" and text and not catalog:
            network_rows = _reaction_catalog_matches(
                artifacts,
                text,
                existing={str(item["smiles"]) for item in catalog},
            )
            if network_rows:
                catalog = (*catalog, *network_rows)
                matching_count += len(network_rows)
                network_fallback = True
        catalog_size = len(totals)
        full_catalog_cached = False
    else:
        catalog = _load_species_search_catalog(*species_signature, *moname_signature)
        catalog_size = len(catalog)
        full_catalog_cached = True
        matching_count = None
    rows: list[dict[str, Any]] = []
    tolerance = max(0.0, float(mass_tolerance or 0.0))
    for entry in catalog:
        smiles = str(entry["smiles"])
        formula = str(entry["formula"])
        exact = entry.get("exact_mass")
        nominal = entry.get("nominal_mass")
        mass_error: float | None = None
        if effective_kind == "formula" and formula != text:
            continue
        if (
            effective_kind == "smiles"
            and text.lower() not in smiles.lower()
            and (
                not query_canonical
                or _canonical_smiles_cached(smiles) != query_canonical
            )
        ):
            continue
        if effective_kind == "mass":
            if exact is None or nominal is None or target_mass is None:
                continue
            matched_mass = closest_isotopic_mass(formula, target_mass, "exact")
            if matched_mass is None:
                continue
            exact, nominal = matched_mass
            mass_error = float(exact) - target_mass
            if abs(mass_error) > tolerance:
                continue
        rows.append(
            {
                **entry,
                "exact_mass": round(float(exact), 6) if exact is not None else None,
                "nominal_mass": int(nominal) if nominal is not None else None,
                "mass_error": round(mass_error, 6) if mass_error is not None else None,
                "ppm_error": round(mass_error / target_mass * 1e6, 3) if mass_error is not None and target_mass else None,
            }
        )
    if effective_kind == "mass":
        rows = _aggregate_mass_rows_by_formula(
            rows,
            activity_field="total_count",
            summed_fields=("total_count",),
        )
        rows.sort(key=lambda row: (abs(float(row.get("mass_error") or 0.0)), -int(row["total_count"]), row["formula"]))
    else:
        rows.sort(key=lambda row: (-int(row["total_count"]), row["formula"], row["smiles"]))
    for row in rows:
        row["structure"] = _structure_markdown(str(row["smiles"]))
    return {
        "ok": True,
        "query": {
            "text": text,
            "kind": effective_kind,
            "mass_tolerance": tolerance,
        },
        "rows": rows,
        "n_rows": matching_count if matching_count is not None else len(rows),
        "meta": {
            "species_file": species_signature[0],
            "moname_file": moname_signature[0],
            "moname_available": bool(moname_signature[0]),
            "catalog_size": catalog_size,
            "catalog_cache": "memory" if full_catalog_cached else "fast-path",
            "canonical_match": canonical_match,
            "reaction_network_fallback": network_fallback,
        },
    }


def detect_query_kind(query: str) -> str:
    """Auto-detect the query kind: ``mass`` / ``formula`` / ``smiles``."""
    text = (query or "").strip()
    if not text:
        return "smiles"
    # numeric (mass) — allow optional decimal and sign
    try:
        float(text)
        return "mass"
    except ValueError:
        pass
    if looks_like_formula(text):
        return "formula"
    return "smiles"


def search_species(
    artifacts: dict[str, str],
    query: str,
    *,
    kind: str = "auto",
    mass_tolerance: float = 0.5,
) -> dict[str, Any]:
    """Search species by formula / SMILES / mass using the existing network.

    Returns ``{ok, rows, query_kind}`` where each row carries the fields
    needed by AG Grid and the right-hand detail panel.  Formula and SMILES
    searches return concrete structures; mass searches aggregate all matching
    structures into one row per molecular formula.  Mass matching always uses
    exact mass, and every matching result is returned for client-side paging.
    """
    reac_path = (artifacts.get("reaction") or "").strip()
    if not reac_path:
        raise ServiceError("缺少 reactionabcd 数据文件", reason="missing_reaction")
    if not os.path.exists(reac_path):
        raise ServiceError(f"reactionabcd 文件不存在: {reac_path}", reason="missing_reaction")

    effective_kind = kind if kind and kind != "auto" else detect_query_kind(query)
    text = (query or "").strip()
    if not text:
        raise ServiceError("请输入查询内容", reason="missing_query")

    try:
        net = STORE.get(reac_path, _reaction_min_tp(artifacts))
    except FileNotFoundError as exc:
        raise ServiceError(f"reactionabcd 文件不存在: {reac_path}", reason="missing_reaction") from exc
    except Exception as exc:
        raise ServiceError(f"加载反应网络失败: {exc}") from exc

    rows: list[dict[str, Any]] = []
    if effective_kind == "formula":
        rows = _rows_for_formula(net, text)
    elif effective_kind == "smiles":
        rows = _rows_for_smiles(net, text)
    elif effective_kind == "mass":
        rows = _rows_for_mass(net, text, mass_tolerance)
    else:
        raise ServiceError(f"未知查询类型: {effective_kind}", reason="bad_kind")

    matching_count = len(rows)
    return {
        "ok": True,
        "query_kind": effective_kind,
        "query": text,
        "rows": rows,
        "n_rows": matching_count,
        "n_visible_rows": len(rows),
    }


def _species_row(net: ReactionNetwork, smi: str) -> dict[str, Any]:
    info = net.species.get(smi)
    formula = info.formula if info else (smiles_formula_cached(smi) or "?")
    mass_fields = formula_mass_fields(formula) if formula and formula != "?" else {}
    return {
        "smiles": smi,
        "formula": formula,
        "exact_mass": mass_fields.get("exact_mass"),
        "nominal_mass": mass_fields.get("nominal_mass"),
        "tp_as_reactant": int(info.tp_as_reactant) if info else 0,
        "tp_as_product": int(info.tp_as_product) if info else 0,
        "total_throughput": int(info.total_throughput) if info else 0,
        "n_consume_rxns": int(info.n_consume_rxns) if info else 0,
        "n_produce_rxns": int(info.n_produce_rxns) if info else 0,
        "net_production": int(info.net_production) if info else 0,
    }


def _rows_for_formula(net: ReactionNetwork, formula: str) -> list[dict[str, Any]]:
    smiles_set = net.smiles_by_formula(formula)
    if not smiles_set:
        return []
    rows = [_species_row(net, smi) for smi in smiles_set]
    rows.sort(key=lambda r: (-(r["total_throughput"]), r["smiles"]))
    return rows


def _rows_for_smiles(net: ReactionNetwork, query: str) -> list[dict[str, Any]]:
    resolved = resolve_start_smiles(net, query)
    if not resolved:
        return []
    return [_species_row(net, resolved)]


def _rows_for_mass(
    net: ReactionNetwork,
    query: str,
    tolerance: float,
) -> list[dict[str, Any]]:
    try:
        target = float(query)
    except ValueError as exc:
        raise ServiceError(f"无效的质量数: {query}", reason="bad_mass") from exc
    tol = max(0.0, float(tolerance))
    rows: list[dict[str, Any]] = []
    for smi, info in net.species.items():
        formula = info.formula
        if not formula:
            continue
        fields = formula_mass_fields(formula)
        exact = fields.get("exact_mass")
        nominal = fields.get("nominal_mass")
        if exact is None or nominal is None:
            continue
        matched_mass = closest_isotopic_mass(formula, target, "exact")
        if matched_mass is None:
            continue
        exact, nominal = matched_mass
        error = float(exact) - target
        if abs(error) > tol:
            continue
        row = _species_row(net, smi)
        row["exact_mass"] = round(float(exact), 6)
        row["nominal_mass"] = int(nominal)
        row["mass_error"] = round(error, 6)
        row["ppm_error"] = round(error / target * 1e6, 3) if target else None
        rows.append(row)
    rows = _aggregate_mass_rows_by_formula(
        rows,
        activity_field="total_throughput",
        summed_fields=(
            "tp_as_reactant",
            "tp_as_product",
            "total_throughput",
            "net_production",
        ),
    )
    rows.sort(key=lambda r: (abs(float(r.get("mass_error") or 0.0)), -(r["total_throughput"]), r["formula"]))
    return rows


# ---------------------------------------------------------------------------
# Species detail
# ---------------------------------------------------------------------------


def species_detail(artifacts: dict[str, str], smiles: str) -> dict[str, Any]:
    """Build the right-hand detail payload for a selected species."""
    smi = (smiles or "").strip()
    if not smi:
        raise ServiceError("未选择物种", reason="missing_species")
    reac_path = (artifacts.get("reaction") or "").strip()
    formula = smiles_formula_cached(smi) or "?"
    mass_fields = formula_mass_fields(formula) if formula and formula != "?" else {}
    tp_reactant = tp_product = 0
    n_consume = n_produce = 0
    if reac_path and os.path.exists(reac_path):
        try:
            net = STORE.get(reac_path, _reaction_min_tp(artifacts))
            info = net.species.get(smi)
            if info:
                tp_reactant = int(info.tp_as_reactant)
                tp_product = int(info.tp_as_product)
                n_consume = int(info.n_consume_rxns)
                n_produce = int(info.n_produce_rxns)
        except Exception:
            # Detail panel is best-effort; the network may already be loaded
            # elsewhere and the search page will have surfaced any real error.
            pass
    return {
        "ok": True,
        "smiles": smi,
        "formula": formula,
        "exact_mass": mass_fields.get("exact_mass"),
        "nominal_mass": mass_fields.get("nominal_mass"),
        "tp_as_reactant": tp_reactant,
        "tp_as_product": tp_product,
        "total_throughput": tp_reactant + tp_product,
        "n_consume_rxns": n_consume,
        "n_produce_rxns": n_produce,
    }


def render_species_svg(
    smiles: str,
    *,
    width: int = 280,
    height: int = 200,
    show_h: bool = True,
) -> dict[str, Any]:
    """Render a 2D structure SVG using the existing RDKit helper."""
    smi = (smiles or "").strip()
    if not smi:
        return {"ok": False, "svg": "", "message": "未选择物种"}
    try:
        svg = smiles_to_svg(smi, width=width, height=height, show_h=show_h)
        return {"ok": True, "svg": svg, "message": ""}
    except Exception as exc:
        return {"ok": False, "svg": "", "message": str(exc) or "RDKit 渲染失败"}


def _collect_reaction_channels(
    artifacts: dict[str, str],
    smiles: str,
    *,
    direction: str = "both",
    top: int = 30,
) -> dict[str, Any]:
    """Return reaction-channel rows for one selected species."""
    smi = (smiles or "").strip()
    if not smi:
        raise ServiceError("请先在物种检索中选择一个物种", reason="missing_species")
    reac_path = (artifacts.get("reaction") or "").strip()
    if not reac_path or not os.path.exists(reac_path):
        raise ServiceError("缺少 reactionabcd 数据文件", reason="missing_reaction")
    role = direction if direction in {"consume", "produce", "both"} else "both"
    try:
        net = STORE.get(reac_path, _reaction_min_tp(artifacts))
    except Exception as exc:
        raise ServiceError(f"加载反应网络失败: {exc}") from exc
    if smi not in net.species:
        raise ServiceError(f"当前网络中不存在该物种: {smi}", reason="species_not_found")
    try:
        matched = collect_next_reactions(net, smi, role)
    except Exception as exc:
        raise ServiceError(f"查询反应通道失败: {exc}") from exc
    rows = [_transition_row(m) for m in matched]
    if int(top or 0) > 0:
        rows = rows[: int(top)]
    return {
        "ok": True,
        "smiles": smi,
        "direction": role,
        "rows": rows,
        "n_rows": len(rows),
    }


def collect_species_channels(
    artifacts: dict[str, str],
    smiles: str,
    *,
    top: int = 20,
) -> dict[str, Any]:
    """Split one target species' high-frequency pathways into two lanes."""
    production = _collect_reaction_channels(
        artifacts,
        smiles,
        direction="produce",
        top=0,
    ).get("rows") or []
    consumption = _collect_reaction_channels(
        artifacts,
        smiles,
        direction="consume",
        top=0,
    ).get("rows") or []

    def decorate(rows: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
        prepared = []
        for row in rows:
            out = dict(row)
            out["role_label"] = "生成" if role == "produce" else "消耗"
            prepared.append(out)
        prepared.sort(key=lambda row: (-int(row.get("forward_tp") or 0), -abs(int(row.get("net_tp") or 0)), str(row.get("reaction_smiles") or "")))
        for rank, row in enumerate(prepared, 1):
            row["rank"] = rank
        return prepared[: max(1, int(top or 20))]

    return {
        "ok": True,
        "smiles": smiles,
        "production_rows": decorate(production, "produce"),
        "consumption_rows": decorate(consumption, "consume"),
    }


def _bond_key(value: str) -> str:
    parts = [part for part in str(value or "").strip().split("-") if part]
    if len(parts) < 2:
        return ""
    try:
        left, right = sorted((int(parts[0]), int(parts[1])))
        suffix = "-".join(parts[2:])
        return f"{left}-{right}" + (f"-{suffix}" if suffix else "")
    except ValueError:
        return "-".join(parts)


def _bond_values(value: Any) -> list[str]:
    return [item for item in (_bond_key(raw) for raw in str(value or "").split(";")) if item]


def _bond_atom_ids(bonds: list[str]) -> list[int]:
    ids: set[int] = set()
    for bond in bonds:
        parts = bond.split("-")
        if len(parts) < 2:
            continue
        try:
            ids.update((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return sorted(ids)


def rank_representative_events(
    artifacts: dict[str, str],
    reaction_text: str,
    *,
    max_events: int = 100,
) -> dict[str, Any]:
    """Return auditable representative-event recommendations.

    The recommendation is intentionally tiered rather than a hidden numeric
    score: researchers retain the final choice while seeing why an event is
    ready (or not ready) for local trajectory validation.
    """
    payload = locate_rng_events(artifacts, reaction_text, max_events=max_events)
    trajectory_path = (artifacts.get("trajectory") or "").strip()
    indexed_frames: set[int] | None = None
    index_message = ""
    if trajectory_path and Path(trajectory_path).is_file():
        try:
            indexed_frames = set(TRAJECTORY_INDEX_STORE.open_required(trajectory_path).frames)
        except IndexNotReadyError as exc:
            index_message = str(exc)
    else:
        index_message = "缺少原始轨迹文件"

    ranked: list[dict[str, Any]] = []
    for raw in payload.get("rows") or []:
        row = dict(raw)
        reactant_bonds = _bond_values(row.get("reactant_bonds"))
        product_bonds = _bond_values(row.get("product_bonds"))
        broken = sorted(set(reactant_bonds).difference(product_bonds))
        formed = sorted(set(product_bonds).difference(reactant_bonds))
        changed_atoms = _bond_atom_ids([*broken, *formed])
        before = int(row.get("before_timestep") or 0)
        after = int(row.get("after_timestep") or 0)
        association_ok = row.get("association_status") == "matched" and bool(row.get("atom_id_list"))
        trajectory_ok = indexed_frames is not None and before in indexed_frames and after in indexed_frames
        if association_ok and trajectory_ok and (broken or formed):
            tier, reason, priority = "recommended", "原子、键变化和轨迹索引均可核查", 0
        elif association_ok and trajectory_ok:
            tier, reason, priority = "reviewable", "可查看局部轨迹，但没有可区分的键变化", 1
        elif not association_ok:
            tier, reason, priority = "unavailable", "molecules 时间线未能唯一关联参与原子", 2
        else:
            tier, reason, priority = "unavailable", index_message or "轨迹索引未覆盖反应前后帧", 2
        row.update(
            {
                "recommendation": tier,
                "recommendation_reason": reason,
                "trajectory_ready": trajectory_ok,
                "broken_bonds": ";".join(broken),
                "formed_bonds": ";".join(formed),
                "changed_atom_ids": changed_atoms,
                "validation_ready": tier in {"recommended", "reviewable"},
                "_priority": priority,
            }
        )
        ranked.append(row)
    ranked.sort(key=lambda row: (int(row["_priority"]), int(row.get("timestep_index") or 0), str(row.get("event_id") or "")))
    for rank, row in enumerate(ranked, 1):
        row["recommendation_rank"] = rank
        row.pop("_priority", None)
    meta = dict(payload.get("meta") or {})
    meta.update(
        {
            "trajectory_index_ready": indexed_frames is not None,
            "trajectory_index_message": index_message,
            "recommended_count": sum(row["recommendation"] == "recommended" for row in ranked),
        }
    )
    return {"ok": True, "rows": ranked, "meta": meta}


def _continuous_sides(
    row: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    reactants = [
        str(item.get("species") or "").strip()
        for item in (row.get("reactant_participants") or [])
        if str(item.get("species") or "").strip()
    ]
    products = [
        str(item.get("species") or "").strip()
        for item in (row.get("product_participants") or [])
        if str(item.get("species") or "").strip()
    ]
    if reactants or products:
        return reactants, products
    raw_reactants = row.get("reactant_smiles") or []
    raw_products = row.get("product_smiles") or []
    if isinstance(raw_reactants, str):
        raw_reactants = [raw_reactants]
    if isinstance(raw_products, str):
        raw_products = [raw_products]
    explicit_reactants = [
        str(value).strip()
        for value in raw_reactants
        if str(value).strip()
    ]
    explicit_products = [
        str(value).strip()
        for value in raw_products
        if str(value).strip()
    ]
    if explicit_reactants or explicit_products:
        return explicit_reactants, explicit_products
    reaction_text = str(row.get("reaction_smiles") or "")
    if "->" not in reaction_text:
        return [], []
    left, right = reaction_text.split("->", 1)
    parsed_left, parsed_right = reaction_key(left, right)
    return list(parsed_left), list(parsed_right)


def _event_prepare_command(source: str, *, rebuild: bool = False) -> str:
    option = "--rebuild event" if rebuild else "--event-only"
    return (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(source).parent))} {option}"
    )


def _continuous_channel_candidates(
    artifacts: dict[str, str],
    anchor: Mapping[str, Any],
    direction: str,
    bridge: str,
    limit: int,
) -> list[dict[str, Any]]:
    role = "produce" if direction == "backward" else "consume"
    payload = _collect_reaction_channels(
        artifacts,
        bridge,
        direction=role,
        top=0,
    )
    anchor_sides = _continuous_sides(anchor)
    anchor_key = canonical_reaction_key(*anchor_sides)
    candidates: list[dict[str, Any]] = []
    for raw in payload.get("rows") or []:
        row = dict(raw)
        sides = _continuous_sides(row)
        if canonical_reaction_key(*sides) == anchor_key:
            continue
        connector = sides[1] if direction == "backward" else sides[0]
        if bridge not in connector:
            continue
        row.update(
            candidate_rank=0,
            direction=direction,
            intermediate_smiles=bridge,
            evidence_level="network_only",
            time_basis="none",
            can_assert_order=False,
            interval_gap=None,
        )
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            -int(row.get("forward_tp") or row.get("tp") or 0),
            -abs(int(row.get("net_tp") or 0)),
            str(row.get("reaction_smiles") or ""),
        )
    )
    for rank, row in enumerate(candidates[:limit], 1):
        row["candidate_rank"] = rank
    return candidates[:limit]


def _route_occurrences(
    route_file: str,
    row: Mapping[str, Any],
    *,
    max_hits: int = 5_000,
) -> list[dict[str, int]]:
    reaction_text = str(row.get("reaction_smiles") or "").strip()
    if not reaction_text:
        return []
    query = _prepare_reaction_query(reaction_text)
    hits = ROUTE_INDEX_STORE.query_reaction_hits(
        route_file,
        query,
        max_hits=max(1, int(max_hits)),
    ).get("hits") or []
    forward_hits = [
        hit
        for hit in hits
        if str(hit.get("direction") or "") == "reactant_to_product"
    ]
    groups = _group_reaction_hits_by_time(
        forward_hits or list(hits),
        merge_gap=1,
    )
    return [
        {
            "start_frame": min(int(hit["start_frame"]) for hit in group),
            "end_frame": max(int(hit["end_frame"]) for hit in group),
        }
        for group in groups
        if group
    ]


def _nearest_species_timestep(index_path: str, frame: int) -> int | None:
    connection = sqlite3.connect(
        f"file:{os.path.abspath(index_path)}?mode=ro",
        uri=True,
    )
    try:
        row = connection.execute(
            """
            SELECT timestep FROM timepoints
            ORDER BY ABS(timestep-?),timestep LIMIT 1
            """,
            (int(frame),),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0]) if row is not None else None


def _species_validates_occurrence(
    species_file: str,
    species_index: Mapping[str, Any],
    row: Mapping[str, Any],
    occurrence: Mapping[str, int],
    snapshot_cache: dict[int, dict[str, int]],
) -> bool:
    before = _nearest_species_timestep(
        str(species_index["index_path"]),
        int(occurrence["start_frame"]),
    )
    after = _nearest_species_timestep(
        str(species_index["index_path"]),
        int(occurrence["end_frame"]),
    )
    if before is None or after is None or before == after:
        return False

    def counts(timestep: int) -> dict[str, int]:
        cached = snapshot_cache.get(timestep)
        if cached is None:
            snapshot = SPECIES_COMPOSITION_STORE.snapshot(
                species_file, timestep
            )
            cached = {
                str(item["smiles"]): int(item["count"])
                for item in snapshot.get("records") or []
            }
            snapshot_cache[timestep] = cached
        return cached

    before_counts = counts(before)
    after_counts = counts(after)
    reactants, products = _continuous_sides(row)
    expected = Counter(products)
    expected.subtract(reactants)
    tested = False
    for species, expected_delta in expected.items():
        if expected_delta == 0:
            continue
        tested = True
        observed = after_counts.get(species, 0) - before_counts.get(
            species, 0
        )
        if expected_delta > 0 and observed <= 0:
            return False
        if expected_delta < 0 and observed >= 0:
            return False
    return tested


def _route_continuous_candidates(
    artifacts: dict[str, str],
    anchor: Mapping[str, Any],
    direction: str,
    bridge: str,
    limit: int,
    *,
    max_route_hits: int = 5_000,
    candidate_pool: int | None = None,
    validate_species: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    route_file = str(artifacts.get("route") or "").strip()
    anchor_occurrences = _route_occurrences(
        route_file,
        anchor,
        max_hits=max_route_hits,
    )
    if not anchor_occurrences:
        return [], False
    pool_size = (
        max(limit * 5, 50)
        if candidate_pool is None
        else max(limit, int(candidate_pool))
    )
    network_rows = _continuous_channel_candidates(
        artifacts, anchor, direction, bridge, pool_size
    )
    species_file = str(artifacts.get("species") or "").strip()
    species_index: dict[str, Any] | None = None
    if (
        validate_species
        and species_file
        and Path(species_file).is_file()
    ):
        try:
            species_index = SPECIES_COMPOSITION_STORE.open_required(
                species_file
            )
        except (
            IndexBuildInProgressError,
            IndexInvalidError,
            IndexNotReadyError,
            IndexStaleError,
        ):
            species_index = None
    snapshot_cache: dict[int, dict[str, int]] = {}
    paired: list[dict[str, Any]] = []
    anchor_by_start = sorted(
        anchor_occurrences,
        key=lambda item: (
            int(item["start_frame"]),
            int(item["end_frame"]),
        ),
    )
    anchor_starts = [
        int(item["start_frame"]) for item in anchor_by_start
    ]
    anchor_by_end = sorted(
        anchor_occurrences,
        key=lambda item: (
            int(item["end_frame"]),
            int(item["start_frame"]),
        ),
    )
    anchor_ends = [int(item["end_frame"]) for item in anchor_by_end]
    for raw in network_rows:
        candidate_occurrences = _route_occurrences(
            route_file,
            raw,
            max_hits=max_route_hits,
        )
        for occurrence in candidate_occurrences:
            if direction == "backward":
                position = bisect_left(
                    anchor_starts, int(occurrence["end_frame"])
                )
                if position >= len(anchor_by_start):
                    continue
                anchor_occurrence = anchor_by_start[position]
                gap = (
                    int(anchor_occurrence["start_frame"])
                    - int(occurrence["end_frame"])
                )
            else:
                position = (
                    bisect_right(
                        anchor_ends, int(occurrence["start_frame"])
                    )
                    - 1
                )
                if position < 0:
                    continue
                anchor_occurrence = anchor_by_end[position]
                gap = (
                    int(occurrence["start_frame"])
                    - int(anchor_occurrence["end_frame"])
                )
            species_validated = False
            if species_index is not None:
                species_validated = (
                    _species_validates_occurrence(
                        species_file,
                        species_index,
                        anchor,
                        anchor_occurrence,
                        snapshot_cache,
                    )
                    and _species_validates_occurrence(
                        species_file,
                        species_index,
                        raw,
                        occurrence,
                        snapshot_cache,
                    )
                )
                if not species_validated:
                    continue
            paired.append(
                {
                    **raw,
                    "anchor_start_frame": int(
                        anchor_occurrence["start_frame"]
                    ),
                    "anchor_end_frame": int(
                        anchor_occurrence["end_frame"]
                    ),
                    "candidate_start_frame": int(
                        occurrence["start_frame"]
                    ),
                    "candidate_end_frame": int(
                        occurrence["end_frame"]
                    ),
                    "interval_gap": gap,
                    "evidence_level": (
                        "route_species"
                        if species_index is not None
                        else "route"
                    ),
                    "time_basis": "route_frame",
                    "can_assert_order": True,
                    "species_validated": species_validated,
                }
            )
    paired.sort(
        key=lambda row: (
            int(row["interval_gap"]),
            -int(row.get("forward_tp") or row.get("tp") or 0),
            str(row.get("reaction_smiles") or ""),
            int(row["candidate_start_frame"]),
        )
    )
    for rank, row in enumerate(paired[:limit], 1):
        row["candidate_rank"] = rank
    return paired[:limit], species_index is not None


def find_continuous_reactions(
    artifacts: dict[str, str],
    anchor: dict[str, Any],
    direction: str = "backward",
    intermediate_smiles: str = "",
    limit: int = 20,
    core_only: bool = False,
) -> dict[str, Any]:
    """Use the strongest prepared chronology available for one two-step link."""
    direction_text = str(direction or "backward")
    if direction_text not in {"backward", "forward"}:
        raise ServiceError("方向必须是前溯或后溯", reason="bad_direction")
    bridge = str(intermediate_smiles or "").strip()
    if not bridge:
        raise ServiceError("请选择连接中间体", reason="missing_intermediate")
    safe_limit = max(1, min(int(limit), 200))
    if core_only:
        safe_limit = min(safe_limit, 10)
    anchor_reactants, anchor_products = _continuous_sides(anchor)
    selected_side = (
        anchor_reactants
        if direction_text == "backward"
        else anchor_products
    )
    if bridge not in selected_side:
        raise ServiceError(
            "所选中间体不在当前反应的连接侧",
            reason="invalid_intermediate",
        )
    preparation_hints: list[str] = []
    reaction_file = str(artifacts.get("reaction") or "").strip()
    reactionevent_file = (artifacts.get("reactionevent") or "").strip()
    molecules_file = (artifacts.get("molecules") or "").strip()
    event_id = str(anchor.get("event_id") or "")
    if reactionevent_file and Path(reactionevent_file).is_file():
        event_molecules = (
            molecules_file
            if molecules_file and Path(molecules_file).is_file()
            else ""
        )
        if event_id:
            try:
                payload = EVENT_EVIDENCE_STORE.query_adjacent_events(
                    reactionevent_file,
                    event_molecules,
                    event_id,
                    intermediate_smiles=bridge,
                    direction=direction_text,
                    limit=safe_limit,
                    include_total=not core_only,
                )
                payload.update(
                    ok=True,
                    candidates=list(payload.get("rows") or []),
                    data_sources=[
                        reactionevent_file,
                        *([event_molecules] if event_molecules else []),
                    ],
                    preparation_hint="\n".join(preparation_hints),
                    preparation_hints=preparation_hints,
                    meta={
                        "message": (
                            f"找到 {len(payload.get('rows') or [])} 个 "
                            "RNG 事件区间候选"
                        ),
                        "semantics": (
                            "按 RNG authored Timestep_Index 确定事件区间"
                            "先后；不要求位于相邻帧"
                        ),
                        "search_stage": (
                            "core_shortlist" if core_only else "validated"
                        ),
                        "budgets": (
                            {
                                "candidate_limit": safe_limit,
                                "count_total": False,
                            }
                            if core_only
                            else {}
                        ),
                    },
                )
                return payload
            except (IndexInvalidError, IndexStaleError):
                preparation_hints.append(
                    _event_prepare_command(
                        reactionevent_file, rebuild=True
                    )
                )
            except IndexNotReadyError:
                preparation_hints.append(
                    _event_prepare_command(reactionevent_file)
                )
            except (OSError, TypeError, ValueError) as exc:
                raise ServiceError(
                    str(exc), reason="continuous_event_query_error"
                ) from exc
        else:
            preparation_hints.append(
                "选择一个 RNG 代表事件后可提升为事件区间证据"
            )

    route_file = str(artifacts.get("route") or "").strip()
    if route_file and Path(route_file).is_file():
        try:
            ROUTE_INDEX_STORE.open_required(route_file)
        except (IndexInvalidError, IndexStaleError):
            preparation_hints.append(
                "reacnet-scope-prepare "
                f"{shlex.quote(str(Path(route_file).parent))} "
                "--rebuild route"
            )
        except IndexNotReadyError:
            preparation_hints.append(
                "reacnet-scope-prepare "
                f"{shlex.quote(str(Path(route_file).parent))} "
                "--route-only"
            )
        else:
            try:
                rows, species_validated = _route_continuous_candidates(
                    artifacts,
                    anchor,
                    direction_text,
                    bridge,
                    safe_limit,
                    max_route_hits=(200 if core_only else 5_000),
                    candidate_pool=(
                        max(safe_limit * 2, 10)
                        if core_only
                        else None
                    ),
                    validate_species=not core_only,
                )
            except (OSError, TypeError, ValueError) as exc:
                raise ServiceError(
                    f"Route 候选查询失败: {exc}",
                    reason="continuous_route_query_error",
                ) from exc
            if rows:
                evidence_level = (
                    "route_species" if species_validated else "route"
                )
                species_file = str(
                    artifacts.get("species") or ""
                ).strip()
                if (
                    species_file
                    and Path(species_file).is_file()
                    and not species_validated
                ):
                    preparation_hints.append(
                        "reacnet-scope-prepare "
                        f"{shlex.quote(str(Path(species_file).parent))} "
                        "--composition-only"
                    )
                return {
                    "ok": True,
                    "anchor": dict(anchor),
                    "direction": direction_text,
                    "intermediate_smiles": bridge,
                    "rows": rows,
                    "candidates": rows,
                    "total": len(rows),
                    "limit": safe_limit,
                    "evidence_level": evidence_level,
                    "time_basis": "route_frame",
                    "can_assert_order": True,
                    "association_available": False,
                    "data_sources": [
                        reaction_file,
                        route_file,
                        *(
                            [
                                str(
                                    artifacts.get("species") or ""
                                ).strip()
                            ]
                            if species_validated
                            else []
                        ),
                    ],
                    "preparation_hint": "\n".join(preparation_hints),
                    "preparation_hints": preparation_hints,
                    "meta": {
                        "message": (
                            "Route + species 时间候选"
                            if species_validated
                            else "Route 原子转移时间候选"
                        ),
                        "semantics": (
                            "Route 仅提供近似发生帧，不把单原子变化"
                            "视为已确认的完整反应事件"
                        ),
                        "search_stage": (
                            "core_shortlist" if core_only else "validated"
                        ),
                        "budgets": (
                            {
                                "candidate_limit": safe_limit,
                                "network_candidate_pool": max(
                                    safe_limit * 2, 10
                                ),
                                "route_hits_per_reaction": 200,
                                "species_validation": False,
                            }
                            if core_only
                            else {}
                        ),
                    },
                }

    if not reaction_file or not Path(reaction_file).is_file():
        raise ServiceError(
            "缺少可用于共享 SMILES 连接的 .reactionabcd",
            reason="missing_reaction_network",
        )
    rows = _continuous_channel_candidates(
        artifacts, anchor, direction_text, bridge, safe_limit
    )
    return {
        "ok": True,
        "anchor": dict(anchor),
        "direction": direction_text,
        "intermediate_smiles": bridge,
        "rows": rows,
        "candidates": rows,
        "total": len(rows),
        "limit": safe_limit,
        "evidence_level": "network_only",
        "time_basis": "none",
        "can_assert_order": False,
        "association_available": False,
        "data_sources": [reaction_file],
        "preparation_hint": "\n".join(preparation_hints),
        "preparation_hints": preparation_hints,
        "meta": {
            "message": f"找到 {len(rows)} 个聚合网络候选",
            "semantics": (
                "仅按精确共享 SMILES 连接；当前数据不能判断两个"
                "反应是否实际连续发生"
            ),
            "search_stage": (
                "core_shortlist" if core_only else "network_only"
            ),
            "budgets": (
                {
                    "candidate_limit": safe_limit,
                    "event_scan": False,
                    "route_scan": False,
                }
                if core_only
                else {}
            ),
        },
    }


def compose_continuous_reaction_pair(
    anchor_event: Mapping[str, Any],
    candidate_event: Mapping[str, Any],
    *,
    direction: str,
    intermediate_smiles: str,
) -> dict[str, Any]:
    """Compose two reactions while cancelling exactly one chosen bridge."""
    direction_text = str(direction or "backward")
    if direction_text not in {"backward", "forward"}:
        raise ValueError("direction must be backward or forward")
    bridge = str(intermediate_smiles or "").strip()
    if not bridge:
        raise ValueError("intermediate_smiles is required")
    chronological = (
        [dict(candidate_event), dict(anchor_event)]
        if direction_text == "backward"
        else [dict(anchor_event), dict(candidate_event)]
    )
    first_reactants, first_products = _continuous_sides(chronological[0])
    second_reactants, second_products = _continuous_sides(
        chronological[1]
    )
    if bridge not in first_products or bridge not in second_reactants:
        raise ValueError(
            "selected intermediate does not connect the two reactions"
        )
    first_products.remove(bridge)
    second_reactants.remove(bridge)
    reactant_smiles = [*first_reactants, *second_reactants]
    product_smiles = [*first_products, *second_products]
    reactant_formulas = [
        smiles_formula_cached(smiles) or "?"
        for smiles in reactant_smiles
    ]
    product_formulas = [
        smiles_formula_cached(smiles) or "?"
        for smiles in product_smiles
    ]
    return {
        "reaction_smiles": (
            " + ".join(reactant_smiles)
            + " -> "
            + " + ".join(product_smiles)
        ),
        "reaction_formulas": (
            " + ".join(reactant_formulas)
            + " -> "
            + " + ".join(product_formulas)
        ),
        "reactant_smiles": reactant_smiles,
        "product_smiles": product_smiles,
        "reactant_formulas": reactant_formulas,
        "product_formulas": product_formulas,
        "event_ids": [
            str(row.get("event_id") or "")
            for row in chronological
            if row.get("event_id")
        ],
        "event_count": len(chronological),
        "cancelled_intermediate": bridge,
        "semantics": "candidate_composed_net_change",
    }


def search_reactions_by_formula(
    artifacts: dict[str, str],
    reactants_text: str,
    products_text: str,
    *,
    mode: str = "exact",
    top: int = 50,
    with_share: bool = False,
    share_metric: str = "net_tp",
    share_abs_metric: bool = False,
    share_positive_only: bool = False,
) -> dict[str, Any]:
    """Mirror legacy ``/api/rxn_formula`` for Dash."""
    reactants = split_terms((reactants_text or "").strip())
    products = split_terms((products_text or "").strip())
    if not reactants and not products:
        raise ServiceError("请输入反应物和/或产物分子式", reason="missing_query")

    reac_path = (artifacts.get("reaction") or "").strip()
    if not reac_path or not os.path.exists(reac_path):
        raise ServiceError("缺少 reactionabcd 数据文件", reason="missing_reaction")

    effective_mode = mode if mode in {"exact", "contains"} else "exact"
    metric = share_metric if share_metric in {"tp", "reverse_tp", "net_tp"} else "net_tp"
    try:
        net = STORE.get(reac_path, _reaction_min_tp(artifacts))
    except Exception as exc:
        raise ServiceError(f"加载反应网络失败: {exc}") from exc

    tp_map = {r.key: r.tp for r in net.reactions}
    need_r = Counter(reactants)
    need_p = Counter(products)
    rows: list[dict[str, Any]] = []
    for rxn in net.reactions:
        if not match_formula_reaction(rxn, need_r, need_p, effective_mode):
            continue
        fwd, rev, nt = net_flux(rxn, tp_map)
        rows.append(
            {
                "tp": fwd,
                "reverse_tp": rev,
                "net_tp": nt,
                "reactant_formulas": " + ".join(rxn.reactant_formulas),
                "product_formulas": " + ".join(rxn.product_formulas),
                "reaction_formulas": reaction_formula_str(rxn),
                "reaction_smiles": reaction_smiles_str(rxn),
                "reactant_smiles": list(rxn.reactant_smiles),
                "product_smiles": list(rxn.product_smiles),
                **reaction_mass_fields(rxn),
            }
        )

    share_total_metric: float | None = None
    share_top_sum: float | None = None
    limit = int(top or 0)
    if with_share:
        scored_rows: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            value = float(row.get(metric, 0.0))
            if share_abs_metric:
                value = abs(value)
            if share_positive_only and value <= 0:
                continue
            scored_rows.append((value, row))
        scored_rows.sort(key=lambda item: item[0], reverse=True)
        share_total_metric = sum(value for value, _ in scored_rows)
        if limit > 0:
            scored_rows = scored_rows[:limit]
        share_top_sum = sum(value for value, _ in scored_rows)
        rows_out: list[dict[str, Any]] = []
        cumulative = 0.0
        for idx, (value, row) in enumerate(scored_rows, 1):
            pct = (value / share_total_metric * 100.0) if share_total_metric else 0.0
            cumulative += pct
            out = dict(row)
            out["rank"] = idx
            out["metric_value"] = value
            out["share_pct"] = round(pct, 3)
            out["cumulative_pct"] = round(cumulative, 3)
            rows_out.append(out)
        rows = rows_out
    else:
        rows.sort(key=lambda row: (row["tp"], abs(row["net_tp"])), reverse=True)
        if limit > 0:
            rows = rows[:limit]
        for idx, row in enumerate(rows, 1):
            row["rank"] = idx

    return {
        "ok": True,
        "query": {
            "reactants": reactants,
            "products": products,
            "mode": effective_mode,
            "top": limit,
            "with_share": with_share,
            "share_metric": metric,
            "share_abs_metric": share_abs_metric,
            "share_positive_only": share_positive_only,
        },
        "meta": {
            "rows": len(rows),
            "share_metric_total": share_total_metric,
            "share_metric_top_sum": share_top_sum,
        },
        "rows": rows,
    }


def _transition_row(matched: Any) -> dict[str, Any]:
    rxn = matched.reaction
    return {
        "role": matched.role,
        "reaction_smiles": " + ".join(rxn.reactant_smiles) + " -> " + " + ".join(rxn.product_smiles),
        "reaction_formulas": " + ".join(rxn.reactant_formulas) + " -> " + " + ".join(rxn.product_formulas),
        # Keep the ordered, uncollapsed sides in the row payload.  The
        # focused workflow uses these only after a channel is selected, and
        # repeated entries represent real stoichiometric occurrences.
        "reactant_smiles": list(rxn.reactant_smiles),
        "product_smiles": list(rxn.product_smiles),
        "reactant_formulas": list(rxn.reactant_formulas),
        "product_formulas": list(rxn.product_formulas),
        "forward_tp": int(matched.forward_tp),
        "reverse_tp": int(matched.reverse_tp),
        "net_tp": int(matched.net_tp),
        "ratio_pct": round(float(matched.ratio_pct), 4),
        "tp": int(rxn.tp),
    }


def _reaction_text_sides(reaction_text: str) -> tuple[list[str], list[str]]:
    """Recover ordered reaction sides from the UI's spaced reaction text."""
    text = str(reaction_text or "").strip()
    for arrow in (" -> ", " → ", "->", "→"):
        if arrow not in text:
            continue
        left, right = text.split(arrow, 1)

        def terms(side: str) -> list[str]:
            # ``_transition_row`` deliberately emits a spaced separator so a
            # charge marker such as ``[NH4+]`` is never mistaken for a term.
            return [part.strip() for part in side.split(" + ") if part.strip()]

        return terms(left), terms(right)
    return [], []


def build_species_structure_items(
    smiles_values: Iterable[Any],
    *,
    formula_values: Iterable[Any] | None = None,
    show_h: bool = True,
    max_items: int = 0,
) -> list[dict[str, Any]]:
    """Return ordered structure cards without eagerly rendering any SVG."""
    smiles_list = [
        str(value).strip()
        for value in smiles_values
        if str(value).strip()
    ]
    if max_items > 0:
        smiles_list = smiles_list[: int(max_items)]
    provided = (
        [str(value).strip() for value in formula_values]
        if formula_values is not None
        else []
    )
    totals = Counter(smiles_list)
    seen: Counter[str] = Counter()
    result: list[dict[str, Any]] = []
    for index, smiles in enumerate(smiles_list):
        seen[smiles] += 1
        formula = provided[index] if index < len(provided) and provided[index] else (
            smiles_formula_cached(smiles) or "?"
        )
        result.append(
            {
                "index": index,
                "smiles": smiles,
                "formula": formula,
                "occurrence": seen[smiles],
                "occurrence_total": totals[smiles],
                "structure_url": (
                    f"/api/structure.svg?smiles={quote(smiles, safe='')}"
                    "&width=180&height=116"
                    f"&show_h={1 if show_h else 0}"
                ),
            }
        )
    return result


def build_channel_structure_detail(
    channel: Mapping[str, Any] | None,
    *,
    show_h: bool = True,
) -> dict[str, Any]:
    """Build one selected channel's ordered, stoichiometry-preserving detail.

    No SVG is rendered here.  The returned same-origin image URLs are added
    only for this selected channel, so the browser never requests structures
    for every row in the channel tables.
    """
    selected = dict(channel or {})
    reactant_smiles = [
        str(value).strip()
        for value in (selected.get("reactant_smiles") or [])
        if str(value).strip()
    ]
    product_smiles = [
        str(value).strip()
        for value in (selected.get("product_smiles") or [])
        if str(value).strip()
    ]
    if not reactant_smiles or not product_smiles:
        parsed_reactants, parsed_products = _reaction_text_sides(
            str(selected.get("reaction_smiles") or "")
        )
        reactant_smiles = reactant_smiles or parsed_reactants
        product_smiles = product_smiles or parsed_products

    reactants = build_species_structure_items(
        reactant_smiles,
        formula_values=selected.get("reactant_formulas"),
        show_h=show_h,
    )
    products = build_species_structure_items(
        product_smiles,
        formula_values=selected.get("product_formulas"),
        show_h=show_h,
    )
    reaction_smiles = str(selected.get("reaction_smiles") or "").strip() or (
        " + ".join(item["smiles"] for item in reactants)
        + " -> "
        + " + ".join(item["smiles"] for item in products)
    )
    reaction_formulas = str(selected.get("reaction_formulas") or "").strip() or (
        " + ".join(item["formula"] for item in reactants)
        + " -> "
        + " + ".join(item["formula"] for item in products)
    )
    return {
        "ok": bool(reactants or products),
        "reaction_smiles": reaction_smiles,
        "reaction_formulas": reaction_formulas,
        "reactants": reactants,
        "products": products,
    }


# ---------------------------------------------------------------------------
# Time evolution
# ---------------------------------------------------------------------------


def build_species_evolution(
    artifacts: dict[str, str],
    targets: list[str],
    *,
    species_file: str = "",
    species_files: str = "",
    x_axis: str = "ps",
    timestep_ps: float = 0.0001,
    normalize: str = "none",
    smooth_window: int = 1,
    downsample: int = 1800,
    max_curves: int = 30,
    formula_mode: str = "sum",
    max_smiles_per_formula: int = 0,
    time_align: str = "raw",
) -> dict[str, Any]:
    """Wrap ``build_species_plot_payload`` for the time evolution page.

    Returns the payload as-is; the callback converts ``x_values`` and
    ``curves`` into Plotly traces without re-smoothing or re-sampling.
    """
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (species_file or artifacts.get("species") or "").strip()
    if not species_path and reac_path:
        species_path = derive_species_path(reac_path)
    multi_source_text = (species_files or "").strip()
    if not multi_source_text and (not species_path or not os.path.exists(species_path)):
        raise ServiceError("缺少 .species 数据文件", reason="missing_species_file")
    target_list = [t.strip() for t in (targets or []) if t and t.strip()]
    if not target_list:
        raise ServiceError("请至少选择一个目标物种或分子式", reason="missing_target")
    params = {
        "target": ["\n".join(target_list)],
        "reac": [reac_path or ""],
        "min_tp": [str(_reaction_min_tp(artifacts))],
        "species_file": [species_path],
        "species_files": [multi_source_text],
        "x_axis": [x_axis if x_axis in {"step", "ps", "ns"} else "ps"],
        "timestep_ps": [str(timestep_ps)],
        "normalize": [normalize if normalize in {"none", "initial", "max"} else "none"],
        "smooth_window": [str(max(1, int(smooth_window)))],
        "downsample": [str(max(0, int(downsample)))],
        "max_curves": [str(max(1, int(max_curves)))],
        "formula_mode": [formula_mode if formula_mode in {"sum", "split", "both"} else "sum"],
        "max_smiles_per_formula": [str(max(0, int(max_smiles_per_formula)))],
        "time_align": [time_align if time_align in {"raw", "truncate", "relative"} else "raw"],
    }
    try:
        return build_species_plot_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"构建时间演化数据失败: {exc}") from exc


def evolution_to_csv(payload: dict[str, Any]) -> str:
    """Serialize an evolution payload to a CSV string (x + one column per curve)."""
    import csv
    import io

    x_values = payload.get("x_values") or []
    curves = payload.get("curves") or []
    x_name = payload.get("x_name") or "x"
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = [x_name] + [c.get("name") or c.get("query") or f"curve_{i}" for i, c in enumerate(curves)]
    writer.writerow(header)
    for i, x in enumerate(x_values):
        row = [x]
        for c in curves:
            vals = c.get("values") or []
            row.append(vals[i] if i < len(vals) else "")
        writer.writerow(row)
    return buf.getvalue()


def build_carbon_evolution(
    artifacts: dict[str, str],
    *,
    data_path: str = "",
    species_file: str = "",
    species_files: str = "",
    x_axis: str = "ps",
    timestep_ps: float = 0.0001,
    mode: str = "exact",
    top_k: int = 12,
    max_exact_lines: int = 24,
    display_ranges: str = "",
    merge_ranges: str = "",
    carbon_bins: str = "",
    parent_carbon_number: int | None = None,
    highlight_small: str = "1-4",
    highlight_large: int = 30,
    smoothing: str = "none",
    smooth_window: int = 5,
    smooth_polyorder: int = 2,
    layout: str = "single",
    layout_regions: str = "",
    theme: str = "light",
    palette: str = "viridis",
    time_align: str = "raw",
    system_mode: str = "",
    legend_mode: str = "compact",
    fig_width: float = 11.5,
    fig_height: float = 8.0,
    max_formula_list: int = 30,
    show_uncertainty: bool = True,
) -> dict[str, Any]:
    """Mirror the legacy Carbon-Number Evolution payload."""
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (species_file or artifacts.get("species") or "").strip()
    if not species_path and reac_path:
        species_path = derive_species_path(reac_path)
    multi_source_text = (species_files or "").strip()
    if not (data_path or multi_source_text) and (not species_path or not os.path.exists(species_path)):
        raise ServiceError("缺少 .species 数据文件", reason="missing_species_file")
    params = {
        "data": [(data_path or "").strip()],
        "reac": [reac_path or ""],
        "species_file": [species_path],
        "species_files": [multi_source_text],
        "x_axis": [x_axis if x_axis in {"step", "ps", "ns"} else "ps"],
        "timestep_ps": [str(timestep_ps)],
        "mode": [mode if mode in {"exact", "binned", "topk"} else "exact"],
        "top_k": [str(max(1, int(top_k)))],
        "max_exact_lines": [str(max(1, int(max_exact_lines)))],
        "display_ranges": [display_ranges or ""],
        "merge_ranges": [merge_ranges or ""],
        "carbon_bins": [carbon_bins or ""],
        "parent_carbon_number": [str(parent_carbon_number or 0)],
        "highlight_small": [highlight_small or "1-4"],
        "highlight_large": [str(max(1, int(highlight_large)))],
        "smoothing": [smoothing if smoothing in {"none", "rolling", "savgol"} else "none"],
        "smooth_window": [str(max(1, int(smooth_window)))],
        "smooth_polyorder": [str(max(1, int(smooth_polyorder)))],
        "layout": [layout if layout in {"single", "subplots"} else "single"],
        "layout_regions": [layout_regions or ""],
        "theme": [theme if theme in {"light", "dark"} else "light"],
        "palette": [palette or "viridis"],
        "time_align": [time_align if time_align in {"raw", "truncate", "relative"} else "raw"],
        "system_mode": [system_mode if system_mode in {"facet", "overlay"} else ""],
        "legend_mode": [legend_mode if legend_mode in {"compact", "detailed"} else "compact"],
        "fig_width": [str(max(4.0, float(fig_width)))],
        "fig_height": [str(max(4.0, float(fig_height)))],
        "max_formula_list": [str(max(5, int(max_formula_list)))],
        "show_uncertainty": ["1" if show_uncertainty else "0"],
        "max_points": ["1200"],
    }
    try:
        return build_carbon_plot_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"构建 Carbon 演化图失败: {exc}") from exc


def carbon_plot_to_csv(payload: dict[str, Any]) -> str:
    """Serialize Carbon plot_data rows to CSV."""
    return rows_to_csv(payload.get("csv_rows") or payload.get("plot_data") or [])


def build_elemental_composition_evolution(
    artifacts: dict[str, str],
    *,
    species_file: str = "",
    reference_smiles: str = "",
    x_axis: str = "ps",
    timestep_ps: float = 0.0001,
    max_carbon: int = 6,
    chlorine_state: str = "all",
    oxygen_state: str = "all",
    max_points: int = 600,
) -> dict[str, Any]:
    """Build the minimal filtered carbon-skeleton trajectory."""
    started = time.perf_counter()
    try:
        timestep_ps = float(timestep_ps)
    except (TypeError, ValueError) as exc:
        raise ServiceError("Timestep 必须是正数（ps）", reason="invalid_timestep") from exc
    if timestep_ps <= 0:
        raise ServiceError("Timestep 必须是正数（ps）", reason="invalid_timestep")
    species_path = (species_file or artifacts.get("species") or "").strip()
    if not species_path or not Path(species_path).is_file():
        raise ServiceError("缺少 .species 数据文件", reason="missing_species_file")
    chlorine_state = chlorine_state if chlorine_state in {"all", "chlorinated", "unchlorinated"} else "all"
    oxygen_state = oxygen_state if oxygen_state in {"all", "oxygenated", "unoxygenated"} else "all"
    try:
        indexed = SPECIES_COMPOSITION_STORE.query(
            species_path,
            max_points=max_points,
            max_carbon=max(0, int(max_carbon)),
            max_oxygen=None,
            chlorine_mode="exact",
        )
    except (
        IndexNotReadyError,
        IndexStaleError,
        IndexBuildInProgressError,
        IndexInvalidError,
        RuntimeError,
    ) as exc:
        raise ServiceError(str(exc), reason="composition_index_not_ready") from exc

    def convert_time(timestep: int) -> int | float:
        if x_axis == "step":
            return int(timestep)
        value = float(timestep) * float(timestep_ps)
        return value / 1000.0 if x_axis == "ns" else value

    rows = list(indexed.get("rows") or [])
    if not rows:
        raise ServiceError("碳数过滤范围内没有物种组成数据", reason="empty_composition")
    sampled_timesteps = [int(value) for value in indexed.get("timesteps") or []]
    first_timestep = sampled_timesteps[0]
    last_timestep = sampled_timesteps[-1]
    reference_smiles = str(reference_smiles or "").strip()
    reference_atoms = count_atoms_fast(reference_smiles) if reference_smiles else {}
    reference_record = {
        "smiles": reference_smiles,
        "formula": formula_from_counts(reference_atoms) if reference_smiles else "",
        "carbon": int(reference_atoms.get("C", 0)),
        "oxygen": int(reference_atoms.get("O", 0)),
        "chlorine": int(reference_atoms.get("Cl", 0)),
    }
    if reference_smiles and int(reference_record["carbon"]) <= 0:
        raise ServiceError(
            "参考物种必须是包含碳原子的有效 SMILES",
            reason="invalid_reference_species",
        )
    reference_counts = (
        SPECIES_COMPOSITION_STORE.species_count_series(
            species_path,
            sampled_timesteps,
            reference_smiles,
        )
        if reference_smiles
        else {}
    )

    def matches_filter(oxygen: int, chlorine: int) -> bool:
        chlorine_ok = (
            chlorine_state == "all"
            or (chlorine_state == "chlorinated" and chlorine > 0)
            or (chlorine_state == "unchlorinated" and chlorine == 0)
        )
        oxygen_ok = (
            oxygen_state == "all"
            or (oxygen_state == "oxygenated" and oxygen > 0)
            or (oxygen_state == "unoxygenated" and oxygen == 0)
        )
        return chlorine_ok and oxygen_ok

    reference_c = int(reference_record["carbon"])
    carbon_totals: Counter[tuple[int, int]] = Counter()
    for row in rows:
        if matches_filter(int(row["oxygen"]), int(row["chlorine"])):
            carbon_totals[(int(row["timestep"]), int(row["carbon"]))] += int(row["count"])
    reference_allowed = bool(reference_smiles) and matches_filter(
        int(reference_record["oxygen"]),
        int(reference_record["chlorine"]),
    )
    carbon_skeleton_rows: list[dict[str, Any]] = []
    for timestep in sampled_timesteps:
        reference_count = int(reference_counts.get(timestep, 0)) if reference_allowed else 0
        for carbon in range(1, int(max_carbon) + 1):
            carbon_skeleton_rows.append(
                {
                    "timestep": timestep,
                    "x": convert_time(timestep),
                    "series": f"C{carbon}",
                    "count": int(carbon_totals[(timestep, carbon)]),
                }
            )
        if reference_smiles:
            carbon_skeleton_rows.append(
                {
                    "timestep": timestep,
                    "x": convert_time(timestep),
                    "series": "参考物种",
                    "count": reference_count,
                }
            )
        if reference_smiles and reference_c <= int(max_carbon):
            carbon_skeleton_rows.append(
                {
                    "timestep": timestep,
                    "x": convert_time(timestep),
                    "series": f"C{reference_c} 其他物种",
                    "count": max(
                        0,
                        int(carbon_totals[(timestep, reference_c)]) - reference_count,
                    ),
                }
            )
    meta = dict(indexed.get("meta") or {})
    meta["analysis_seconds"] = round(time.perf_counter() - started, 4)
    return {
        "ok": True,
        "view": "composition",
        "x_name": {"step": "Timestep", "ps": "Time (ps)", "ns": "Time (ns)"}.get(x_axis, "Time (ps)"),
        "carbon_skeleton_rows": carbon_skeleton_rows,
        "csv_rows": list(carbon_skeleton_rows),
        "filters": {
            "chlorine_state": chlorine_state,
            "oxygen_state": oxygen_state,
        },
        "summary": {
            "reference_group": (
                f"C{reference_record['carbon']}O{reference_record['oxygen']}Cl{reference_record['chlorine']}"
                if reference_smiles
                else ""
            ),
            "reference_smiles": reference_smiles,
            "reference_formula": str(reference_record["formula"]),
            "reference_carbon": reference_c,
            "first_timestep": first_timestep,
            "last_timestep": last_timestep,
            "timestep_ps": float(timestep_ps),
        },
        "meta": meta,
    }


def composition_index_status(artifacts: dict[str, str]) -> dict[str, Any]:
    """Return a UI-safe status snapshot for the current composition index."""
    species_path = str((artifacts or {}).get("species") or "").strip()
    if not species_path or not Path(species_path).is_file():
        return {
            "state": "missing_source",
            "progress": 0.0,
            "timepoints": 0,
            "unique_species": 0,
        }
    try:
        return SPECIES_COMPOSITION_STORE.status(species_path)
    except (OSError, RuntimeError, IndexInvalidError, IndexNotReadyError, IndexStaleError) as exc:
        return {
            "state": "invalid",
            "progress": 0.0,
            "timepoints": 0,
            "unique_species": 0,
            "message": str(exc),
        }


def build_carbon_species_drilldown(
    payload: dict[str, Any],
    *,
    series: str,
    timestep: int,
    limit: int = 100,
) -> dict[str, Any]:
    """Resolve one clicked carbon curve to exact species peak statistics."""
    meta = payload.get("meta") or {}
    summary = payload.get("summary") or {}
    filters = payload.get("filters") or {}
    species_path = str(meta.get("species_file") or "")
    if not species_path or not Path(species_path).is_file():
        raise ServiceError("组成索引缺少 .species 来源", reason="missing_species_file")
    reference_smiles = str(summary.get("reference_smiles") or "")
    reference_carbon = int(summary.get("reference_carbon") or 0)
    only_smiles = ""
    exclude_smiles = ""
    if series == "参考物种" and reference_smiles:
        carbon = reference_carbon
        only_smiles = reference_smiles
    elif series == f"C{reference_carbon} 其他物种" and reference_smiles:
        carbon = reference_carbon
        exclude_smiles = reference_smiles
    else:
        matched = re.fullmatch(r"C(\d+)", str(series or ""))
        if not matched:
            raise ServiceError("无法识别所选碳数组", reason="invalid_carbon_series")
        carbon = int(matched.group(1))
    try:
        result = SPECIES_COMPOSITION_STORE.query_species_summary(
            species_path,
            carbon=carbon,
            current_timestep=int(timestep),
            chlorine_state=str(filters.get("chlorine_state") or "all"),
            oxygen_state=str(filters.get("oxygen_state") or "all"),
            only_smiles=only_smiles,
            exclude_smiles=exclude_smiles,
            limit=limit,
        )
    except (
        IndexNotReadyError,
        IndexStaleError,
        IndexBuildInProgressError,
        IndexInvalidError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise ServiceError(str(exc), reason="composition_index_not_ready") from exc
    timestep_ps = float(summary.get("timestep_ps") or 0.0001)
    rows = [
        {
            **row,
            "peak_time": float(row["peak_timestep"]) * timestep_ps,
        }
        for row in result.get("rows") or []
    ]
    return {
        "series": series,
        "carbon": carbon,
        "timestep": int(timestep),
        "current_time": float(timestep) * timestep_ps,
        "rows": rows,
        "query_seconds": result.get("query_seconds"),
    }


def build_intermediate_candidates(
    artifacts: dict[str, str],
    *,
    kind: str = "intermediate",
    top: int = 120,
    abundance_threshold: float = 5.0,
    start_ratio_max: float = 0.1,
    decay_alpha: float = 0.8,
    fwhm_min_ps: float = 0.5,
    timestep_ps: float = 0.0001,
    require_fwhm: bool = True,
    with_flux: bool = True,
    flux_top: int = 10,
) -> dict[str, Any]:
    """Mirror legacy ``/api/intermediate_candidates``."""
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (artifacts.get("species") or "").strip()
    if not species_path and reac_path:
        species_path = derive_species_path(reac_path)
    if not species_path or not os.path.exists(species_path):
        raise ServiceError("缺少 .species 数据文件", reason="missing_species_file")
    if with_flux and (not reac_path or not os.path.exists(reac_path)):
        raise ServiceError("WithFlux 需要 reactionabcd 数据文件", reason="missing_reaction")
    params = {
        "reac": [reac_path or ""],
        "min_tp": [str(_reaction_min_tp(artifacts))],
        "species_file": [species_path],
        "kind": [kind if kind in {"intermediate", "product", "reactant", "all"} else "intermediate"],
        "top": [str(max(1, int(top)))],
        "abundance_threshold": [str(float(abundance_threshold))],
        "start_ratio_max": [str(float(start_ratio_max))],
        "decay_alpha": [str(float(decay_alpha))],
        "fwhm_min_ps": [str(float(fwhm_min_ps))],
        "timestep_ps": [str(float(timestep_ps))],
        "require_fwhm": ["1" if require_fwhm else "0"],
        "with_flux": ["1" if with_flux else "0"],
        "flux_top": [str(max(0, int(flux_top)))],
    }
    try:
        return build_intermediate_candidates_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"筛选中间体候选失败: {exc}") from exc


def locate_rng_events(
    artifacts: dict[str, str],
    reaction_text: str,
    *,
    max_events: int = 100,
) -> dict[str, Any]:
    """Query RNG-authored event records without reading Route or trajectory."""
    reactionevent_file = (artifacts.get("reactionevent") or "").strip()
    molecules_file = (artifacts.get("molecules") or "").strip()
    if not reactionevent_file or not Path(reactionevent_file).is_file():
        raise ServiceError(
            "缺少 .reactionevent.csv；请在 ReacNetGenerator 中启用 --reaction-event",
            reason="missing_reactionevent",
        )
    molecules_file = (
        molecules_file
        if molecules_file and Path(molecules_file).is_file()
        else ""
    )
    if "->" not in str(reaction_text or ""):
        raise ServiceError(
            "请输入完整反应式，例如 A + B -> C + D",
            reason="bad_reaction_query",
        )
    query_left, query_right = str(reaction_text).split("->", 1)
    normalized = reaction_key(query_left, query_right)
    normalized_key = canonical_reaction_key(*normalized)
    try:
        payload = EVENT_EVIDENCE_STORE.query_events(
            reactionevent_file,
            molecules_file,
            normalized_key,
            limit=max_events,
        )
    except IndexStaleError as exc:
        raise ServiceError(
            f"{exc}; 运行 reacnet-scope-prepare "
            f"{shlex.quote(str(Path(reactionevent_file).parent))} "
            "--rebuild event",
            reason="event_index_stale",
        ) from exc
    except IndexInvalidError as exc:
        raise ServiceError(
            f"{exc}; 运行 reacnet-scope-prepare "
            f"{shlex.quote(str(Path(reactionevent_file).parent))} "
            "--rebuild event",
            reason="event_index_invalid",
        ) from exc
    except IndexNotReadyError as exc:
        raise ServiceError(
            f"{exc}; 运行 reacnet-scope-prepare "
            f"{shlex.quote(str(Path(reactionevent_file).parent))} "
            "--event-only",
            reason="event_index_not_ready",
        ) from exc
    except (OSError, ValueError) as exc:
        raise ServiceError(str(exc), reason="rng_event_data_error") from exc

    rows = payload.get("rows") or []
    matched = sum(
        row.get("association_status") == "matched" for row in rows
    )
    payload["meta"] = {
        "status": "ok",
        "message": (
            f"从 RNG 事件索引中找到 {payload.get('total', 0)} 条记录"
        ),
        "matched_atoms": matched,
        "unresolved_atoms": len(rows) - matched,
        "reactionevent_file": os.path.abspath(reactionevent_file),
        "molecules_file": (
            os.path.abspath(molecules_file) if molecules_file else ""
        ),
        "evidence_status": (
            "atom_evidence_linked"
            if payload.get("association_available")
            else "reactionevent_only"
        ),
        "time_basis": payload.get("time_basis"),
    }
    return payload


def validate_pathway_step_occurrences(
    artifacts: dict[str, str],
    step: Mapping[str, Any],
    *,
    max_occurrences: int = 20,
) -> dict[str, Any]:
    """Validate one shortlisted pathway step against prepared time indexes.

    RNG-authored events are authoritative for an exact reaction occurrence.
    A prepared Route index is a bounded fallback and is deliberately labelled
    as approximate atom-transfer timing rather than a complete reaction event.
    Raw event or Route sources are never scanned by this online query.
    """

    reactants = [
        str(value).strip()
        for value in (step.get("reactants") or [])
        if str(value).strip()
    ]
    products = [
        str(value).strip()
        for value in (step.get("products") or [])
        if str(value).strip()
    ]
    reaction_text = str(step.get("reaction_text") or "").strip()
    if not reaction_text and reactants and products:
        reaction_text = f"{' + '.join(reactants)} -> {' + '.join(products)}"
    if "->" not in reaction_text:
        raise ServiceError(
            "所选路径步骤缺少完整反应式",
            reason="bad_pathway_step",
        )

    limit = max(1, min(int(max_occurrences), 50))
    preparation_hints: list[str] = []
    checked_sources: list[str] = []
    event_file = str(artifacts.get("reactionevent") or "").strip()
    molecules_file = str(artifacts.get("molecules") or "").strip()
    event_absent = not event_file or not Path(event_file).is_file()

    if not event_absent:
        checked_sources.append(event_file)
        try:
            event_payload = locate_rng_events(
                artifacts,
                reaction_text,
                max_events=limit,
            )
        except ServiceError as exc:
            if exc.reason in {
                "event_index_not_ready",
                "event_index_stale",
                "event_index_invalid",
            }:
                preparation_hints.append(exc.message)
            else:
                raise
        else:
            event_rows = list(event_payload.get("rows") or [])
            if event_rows:
                for rank, row in enumerate(event_rows, 1):
                    row["occurrence_rank"] = rank
                    row["evidence_source"] = "RNG 事件"
                time_basis = str(
                    (event_payload.get("meta") or {}).get("time_basis")
                    or event_payload.get("time_basis")
                    or "timestep_index"
                )
                return {
                    "ok": True,
                    "reaction_text": reaction_text,
                    "rows": event_rows,
                    "evidence_level": "rng_event",
                    "time_basis": time_basis,
                    "can_assert_occurrence": True,
                    "checked_sources": checked_sources,
                    "preparation_hints": preparation_hints,
                    "message": (
                        f"找到 {len(event_rows)} 条精确匹配的 RNG 反应事件；"
                        "可选择事件后进入局部轨迹核查。"
                    ),
                }

    route_file = str(artifacts.get("route") or "").strip()
    route_absent = not route_file or not Path(route_file).is_file()
    if not route_absent:
        checked_sources.append(route_file)
        try:
            occurrences = _route_occurrences(
                route_file,
                {"reaction_smiles": reaction_text},
                max_hits=min(200, limit * 20),
            )[:limit]
        except (IndexInvalidError, IndexStaleError):
            preparation_hints.append(
                "reacnet-scope-prepare "
                f"{shlex.quote(str(Path(route_file).parent))} --rebuild route"
            )
            occurrences = []
        except (IndexNotReadyError, IndexBuildInProgressError):
            preparation_hints.append(
                "reacnet-scope-prepare "
                f"{shlex.quote(str(Path(route_file).parent))} --route-only"
            )
            occurrences = []
        except RuntimeError as exc:
            preparation_hints.append(str(exc))
            occurrences = []
        except (OSError, TypeError, ValueError) as exc:
            raise ServiceError(
                f"Route 时间候选查询失败: {exc}",
                reason="pathway_route_query_error",
            ) from exc
        if occurrences:
            route_rows = [
                {
                    "occurrence_rank": rank,
                    "evidence_source": "Route 候选",
                    "start_frame": int(item["start_frame"]),
                    "end_frame": int(item["end_frame"]),
                    "frame_span": (
                        int(item["end_frame"]) - int(item["start_frame"])
                    ),
                    "reaction_smiles": reaction_text,
                }
                for rank, item in enumerate(occurrences, 1)
            ]
            return {
                "ok": True,
                "reaction_text": reaction_text,
                "rows": route_rows,
                "evidence_level": "route",
                "time_basis": "route_frame",
                "can_assert_occurrence": False,
                "checked_sources": checked_sources,
                "preparation_hints": preparation_hints,
                "message": (
                    f"找到 {len(route_rows)} 个 Route 原子转移时间候选；"
                    "它们只能定位近似帧，不能单独证明完整反应事件。"
                ),
            }

    reasons: list[str] = []
    if event_absent:
        reasons.append("数据集没有 .reactionevent.csv")
    else:
        reasons.append("RNG 事件索引中没有精确匹配事件")
    if route_absent:
        reasons.append("数据集没有 .route")
    elif preparation_hints:
        reasons.append("Route 索引尚未就绪")
    else:
        reasons.append("Route 索引中没有匹配的时间候选")
    message = "；".join(reasons) + "。"
    if preparation_hints:
        message += " 请先执行：" + "；".join(
            dict.fromkeys(preparation_hints)
        )
    return {
        "ok": True,
        "reaction_text": reaction_text,
        "rows": [],
        "evidence_level": "network_only",
        "time_basis": "none",
        "can_assert_occurrence": False,
        "checked_sources": checked_sources,
        "preparation_hints": preparation_hints,
        "message": message,
    }


def build_rng_event_visualization(
    artifacts: dict[str, str],
    event_row: dict[str, Any],
    *,
    before_frames: int = 3,
    after_frames: int = 3,
) -> dict[str, Any]:
    """Read only indexed trajectory frames for one RNG-authored event."""
    trajectory_file = (artifacts.get("trajectory") or "").strip()
    if not trajectory_file or not Path(trajectory_file).is_file():
        raise ServiceError("缺少原始轨迹文件", reason="missing_trajectory")
    atom_ids = sorted({int(value) for value in (event_row.get("atom_id_list") or [])})
    if not atom_ids:
        raise ServiceError(
            "该复杂事件无法由 molecules 时间线唯一关联原子；不会回退扫描 Route",
            reason="unresolved_event_atoms",
        )
    try:
        index = TRAJECTORY_INDEX_STORE.open_required(trajectory_file)
    except IndexNotReadyError as exc:
        raise ServiceError(str(exc), reason="index_not_ready") from exc
    available = index.frames
    if not available:
        raise ServiceError("轨迹帧索引不包含任何帧", reason="empty_trajectory_index")

    before_timestep = int(event_row.get("before_timestep"))
    after_timestep = int(event_row.get("after_timestep"))

    def nearest_index(value: int) -> int:
        pos = bisect_left(available, value)
        choices = [idx for idx in (pos - 1, pos) if 0 <= idx < len(available)]
        return min(choices, key=lambda idx: abs(available[idx] - value))

    left = nearest_index(before_timestep)
    right = nearest_index(after_timestep)
    if left > right:
        left, right = right, left
    start = max(0, left - max(0, int(before_frames)))
    stop = min(len(available), right + max(0, int(after_frames)) + 1)
    selected_frames = available[start:stop]
    offsets = index.offsets_for(selected_frames)
    wanted = set(atom_ids)
    reactant_bonds = _bond_values(event_row.get("reactant_bonds"))
    product_bonds = _bond_values(event_row.get("product_bonds"))
    broken_bonds = sorted(set(reactant_bonds).difference(product_bonds))
    formed_bonds = sorted(set(product_bonds).difference(reactant_bonds))
    core_atom_ids = _bond_atom_ids([*broken_bonds, *formed_bonds]) or atom_ids
    frames: list[dict[str, Any]] = []
    with open(trajectory_file, "rb") as source:
        for frame in selected_frames:
            byte_range = offsets.get(frame)
            if byte_range is None:
                continue
            source.seek(int(byte_range[0]))
            parsed = parse_lammpstrj_frame_block(
                source.read(int(byte_range[1]) - int(byte_range[0])),
                atom_ids=wanted,
            )
            atoms = [
                {
                    "id": int(atom_id),
                    "x": round(float(atom.get("x", 0.0)), 7),
                    "y": round(float(atom.get("y", 0.0)), 7),
                    "z": round(float(atom.get("z", 0.0)), 7),
                    "element": str(atom.get("element") or ""),
                    "type": str(atom.get("type") or ""),
                    "group": "core",
                }
                for atom_id, atom in sorted((parsed.get("atoms") or {}).items())
            ]
            if atoms:
                if int(frame) <= before_timestep:
                    frame_bonds, bond_state = reactant_bonds, "before"
                elif int(frame) >= after_timestep:
                    frame_bonds, bond_state = product_bonds, "after"
                else:
                    # Coordinates are real for intermediate frames; RNG's
                    # molecule timeline does not expose instantaneous bond
                    # orders for those frames, so do not invent them.
                    frame_bonds, bond_state = [], "intermediate"
                frames.append(
                    {
                        "frame": int(frame),
                        "box": parsed.get("box") or [],
                        "atoms": atoms,
                        "bonds": frame_bonds,
                        "bond_state": bond_state,
                    }
                )
    if not frames:
        raise ServiceError("选中事件的参与原子未出现在轨迹窗口中", reason="no_coordinates")

    storyboard = list(dict.fromkeys([frames[0]["frame"], available[left], available[right], frames[-1]["frame"]]))
    labels = {
        str(available[left]): "反应前",
        str(available[right]): "反应后",
    }
    return {
        "event_id": str(event_row.get("event_id") or ""),
        "frames": frames,
        "atom_groups": {"core": core_atom_ids, "reactant": atom_ids, "product": atom_ids, "context": atom_ids},
        "bond_evidence": {
            "reactant": reactant_bonds,
            "product": product_bonds,
            "broken": broken_bonds,
            "formed": formed_bonds,
        },
        "storyboard_frames": storyboard,
        "storyboard_labels": {str(frame): labels.get(str(frame), f"Frame {frame}") for frame in storyboard},
        "meta": {
            "status": "rng_event",
            "verification_status": str(event_row.get("association_status") or "matched"),
            "reaction_smiles": str(event_row.get("reaction_smiles") or ""),
        },
        "paths": {"trajectory": trajectory_file, "vmd": "", "type_map": ""},
    }


def event_viewer_frames_csv(viewer: Mapping[str, Any] | None) -> str:
    """Serialize the currently extracted event window for audit/download."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["frame", "atom_id", "type", "element", "x", "y", "z", "group", "bond_state"]
    )
    for frame in (viewer or {}).get("frames") or []:
        for atom in frame.get("atoms") or []:
            writer.writerow(
                [
                    frame.get("frame"),
                    atom.get("id"),
                    atom.get("type"),
                    atom.get("element"),
                    atom.get("x"),
                    atom.get("y"),
                    atom.get("z"),
                    atom.get("group"),
                    frame.get("bond_state"),
                ]
            )
    return output.getvalue()


def event_viewer_trajectory_text(viewer: Mapping[str, Any] | None) -> str:
    """Serialize selected event atoms as a standalone LAMMPS trajectory."""
    chunks: list[str] = []
    for frame in (viewer or {}).get("frames") or []:
        atoms = frame.get("atoms") or []
        box = list(frame.get("box") or [])
        while len(box) < 3:
            box.append((0.0, 1.0))
        chunks.extend(
            [
                "ITEM: TIMESTEP",
                str(frame.get("frame") or 0),
                "ITEM: NUMBER OF ATOMS",
                str(len(atoms)),
                "ITEM: BOX BOUNDS pp pp pp",
                *[
                    f"{float(bounds[0]):.10g} {float(bounds[1]):.10g}"
                    for bounds in box[:3]
                ],
                "ITEM: ATOMS id type element x y z",
            ]
        )
        for atom in atoms:
            atom_type = str(atom.get("type") or "0")
            element = str(atom.get("element") or "X")
            chunks.append(
                "{} {} {} {:.10g} {:.10g} {:.10g}".format(
                    int(atom.get("id") or 0),
                    atom_type,
                    element,
                    float(atom.get("x") or 0.0),
                    float(atom.get("y") or 0.0),
                    float(atom.get("z") or 0.0),
                )
            )
    return "\n".join(chunks) + ("\n" if chunks else "")


def event_viewer_atom_ids(viewer: Mapping[str, Any] | None) -> list[int]:
    """Return the ordered visualization atom IDs for one extracted event."""
    groups = (viewer or {}).get("atom_groups") or {}
    values = groups.get("context") or groups.get("core") or []
    return sorted({int(value) for value in values})


def event_viewer_ovito_expression(viewer: Mapping[str, Any] | None) -> str:
    """Build an OVITO Expression Selection for original LAMMPS IDs."""
    return " || ".join(
        f"ParticleIdentifier == {atom_id}"
        for atom_id in event_viewer_atom_ids(viewer)
    )


def event_viewer_vmd_script(
    viewer: Mapping[str, Any] | None,
    *,
    trajectory_name: str = "event_subset.lammpstrj",
) -> str:
    """Build a portable VMD helper script for the downloaded event subset."""
    atom_ids = " ".join(str(value) for value in event_viewer_atom_ids(viewer))
    return (
        f'mol new "{trajectory_name}" type lammpstrj waitfor all\n'
        "mol delrep 0 top\n"
        "mol representation Licorice 0.18 12.0 12.0\n"
        "mol color Element\n"
        "mol selection all\n"
        "mol material Opaque\n"
        "mol addrep top\n"
        f'puts "Original LAMMPS atom IDs: {atom_ids}"\n'
        "animate goto 0\n"
        "display resetview\n"
    )


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize heterogeneous row dictionaries to CSV."""
    import csv
    import io

    safe_rows = list(rows or [])
    keys: list[str] = []
    seen: set[str] = set()
    for row in safe_rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                keys.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for row in safe_rows:
        writer.writerow(row)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Batch comparison
# ---------------------------------------------------------------------------


def scan_batch_conditions(root_dir: str) -> dict[str, Any]:
    """Scan a directory tree for simulation conditions."""
    from rng_tools.batch_compare import BatchComparator

    if not root_dir.strip():
        raise ServiceError("请提供数据根目录", reason="missing_dir")

    root = os.path.abspath(root_dir)
    if not os.path.isdir(root):
        raise ServiceError(f"目录不存在: {root}", reason="bad_dir")

    comparator = BatchComparator()
    conditions = comparator.scan_directory_tree(root)
    if not conditions:
        raise ServiceError(
            f"未在 {root} 下找到包含 .reactionabcd 的子目录",
            reason="no_conditions",
        )

    groups = comparator.auto_group_conditions(conditions)
    condition_rows = [
        {
            "index": i + 1,
            "name": c.name,
            "folder": c.folder,
            "temperature": c.temperature,
            "o2_ratio": c.o2_ratio,
            "pressure": c.pressure,
            "replicate": c.replicate,
            "group_key": c.group_key,
        }
        for i, c in enumerate(conditions)
    ]
    group_rows = [
        {
            "group_name": g.group_name,
            "temperature": g.temperature,
            "o2_ratio": g.o2_ratio,
            "pressure": g.pressure,
            "n_replicates": g.n_replicates,
            "conditions": [c.name for c in g.conditions],
        }
        for g in groups
    ]

    return {
        "ok": True,
        "conditions": condition_rows,
        "groups": group_rows,
        "total_conditions": len(conditions),
        "total_groups": len(groups),
        "meta": {
            "status": "ok",
            "message": f"扫描完成: {len(conditions)} 个条件, {len(groups)} 个条件组",
        },
    }


def run_batch_comparison(
    condition_folders: list[str],
    condition_names: list[str],
    *,
    min_detection_rate: float = 0.0,
    top_n: int = 50,
) -> dict[str, Any]:
    """Run cross-condition comparison for selected conditions."""
    from rng_tools.batch_compare import BatchComparator

    if not condition_folders:
        raise ServiceError("请选择至少一个条件组", reason="no_conditions")
    if len(condition_folders) != len(condition_names):
        raise ServiceError("条件名称与目录数量不匹配", reason="mismatch")

    comparator = BatchComparator()
    for folder, name in zip(condition_folders, condition_names):
        folder_path = os.path.abspath(folder)
        if not os.path.isdir(folder_path):
            continue
        candidates = [
            f for f in os.listdir(folder_path) if f.endswith(".reactionabcd")
        ]
        if not candidates:
            continue
        reac_path = os.path.join(folder_path, candidates[0])
        try:
            reactions = parse_reactionabcd(reac_path, min_tp=1)
        except Exception:
            continue
        comparator.add_condition(name, ReactionNetwork(reactions))

    if not comparator._conditions:
        raise ServiceError("未能加载任何条件的反应网络", reason="no_networks")

    results = comparator.compare_all_common(
        min_detection_rate=float(min_detection_rate),
        top_n=int(top_n),
    )
    if not results:
        raise ServiceError("未找到符合条件的共同反应", reason="no_results")

    rows, cond_names = comparator.build_comparison_matrix(results)
    base_columns = [
        {"field": "index", "headerName": "#", "width": 50},
        {"field": "reaction_smiles", "headerName": "反应式", "flex": 2, "minWidth": 200},
        {"field": "detection_rate", "headerName": "检出率", "width": 80},
    ]
    cond_columns = [
        {"field": f"tp_{cn}", "headerName": f"{cn} (tp)", "width": 100}
        for cn in cond_names
    ]
    return {
        "ok": True,
        "rows": rows,
        "columns": base_columns + cond_columns,
        "condition_names": cond_names,
        "meta": {
            "status": "ok",
            "message": f"对比完成: {len(rows)} 个反应, {len(cond_names)} 个条件",
            "n_reactions": len(rows),
            "n_conditions": len(cond_names),
        },
    }


__all__ = [
    "ALLOWED_ROOTS",
    "ServiceError",
    "browse_dataset_location",
    "list_directory",
    "normalise_recent_datasets",
    "resolve_dataset_input",
    "scan_dataset",
    "validate_browse_path",
    "artifacts_from_status",
    "dataset_label",
    "dataset_ready_count",
    "dataset_capabilities",
    "dataset_readiness",
    "dataset_preparation_status",
    "clear_dataset_index",
    "candidates_from_status",
    "detect_query_kind",
    "find_pathways",
    "search_species_catalog",
    "search_species",
    "species_detail",
    "render_species_svg",
    "collect_species_channels",
    "build_species_structure_items",
    "build_channel_structure_detail",
    "search_reactions_by_formula",
    "build_species_evolution",
    "evolution_to_csv",
    "build_carbon_evolution",
    "build_elemental_composition_evolution",
    "composition_index_status",
    "build_carbon_species_drilldown",
    "carbon_plot_to_csv",
    "build_intermediate_candidates",
    "locate_rng_events",
    "validate_pathway_step_occurrences",
    "rank_representative_events",
    "find_continuous_reactions",
    "compose_continuous_reaction_pair",
    "build_rng_event_visualization",
    "event_viewer_frames_csv",
    "event_viewer_trajectory_text",
    "event_viewer_atom_ids",
    "event_viewer_ovito_expression",
    "event_viewer_vmd_script",
    "rows_to_csv",
    "scan_batch_conditions",
    "run_batch_comparison",
]
