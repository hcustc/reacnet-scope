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
from reacnet_scope.workspace_services import (
    _event_artifact_paths,
    validate_browse_path,
)


# ---------------------------------------------------------------------------
# Species search (formula / SMILES / mass)
# ---------------------------------------------------------------------------


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
    action = "rebuild" if rebuild else "build"
    return f"reacnet-scope prepare {action} event {shlex.quote(source)}"


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

    reactionevent_path, molecules_path = _event_artifact_paths(artifacts)
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


# ---------------------------------------------------------------------------
# Concrete, atom-continuous event paths
# ---------------------------------------------------------------------------


def _event_path_source_from_prefix(
    replicate: str,
    prefix_text: str,
) -> EventPathSource:
    """Resolve one user-supplied RNG common prefix inside an allowed root."""
    label = str(replicate or "").strip()
    raw_prefix = Path(str(prefix_text or "").strip()).expanduser()
    if not label:
        raise ServiceError("重复实验标签不能为空", reason="bad_event_path_source")
    if not str(prefix_text or "").strip():
        raise ServiceError(
            f"重复实验 {label!r} 缺少公共文件前缀",
            reason="bad_event_path_source",
        )
    parent = validate_browse_path(str(raw_prefix.parent))
    prefix = str((parent / raw_prefix.name).resolve())
    timeline = f"{prefix}.timeline.h5"
    reactionevent = f"{prefix}.reactionevent.csv"
    molecules = f"{prefix}.molecules.csv"
    reaction = f"{prefix}.reactionabcd"
    if Path(timeline).is_file():
        reactionevent = timeline
        molecules = ""
    elif not Path(reactionevent).is_file():
        raise ServiceError(
            f"{label}: 找不到 {timeline} 或 {reactionevent}",
            reason="missing_event_path_source",
        )
    if not reactionevent.endswith(".timeline.h5") and not Path(molecules).is_file():
        raise ServiceError(
            f"{label}: 找不到 {molecules}",
            reason="missing_event_path_source",
        )
    return EventPathSource(
        replicate=label,
        reactionevent_file=reactionevent,
        molecules_file=molecules,
        reaction_file=reaction if Path(reaction).is_file() else "",
    )


def _additional_event_path_sources(source_text: str) -> list[EventPathSource]:
    sources: list[EventPathSource] = []
    for line_number, raw_line in enumerate(str(source_text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ServiceError(
                f"附加重复第 {line_number} 行应为 label=/path/to/common-prefix",
                reason="bad_event_path_source",
            )
        replicate, prefix = line.split("=", 1)
        sources.append(_event_path_source_from_prefix(replicate, prefix))
    return sources


def _event_path_sources_for_dash(
    artifacts: Mapping[str, Any],
    *,
    current_replicate: str = "current",
    additional_sources: str = "",
) -> list[EventPathSource]:
    label = str(current_replicate or "current").strip() or "current"
    reactionevent, molecules = _event_artifact_paths(artifacts)
    reaction = str(artifacts.get("reaction") or "").strip()
    if not reactionevent or not Path(reactionevent).is_file():
        raise ServiceError(
            "当前数据集缺少 .timeline.h5 或 .reactionevent.csv",
            reason="missing_event_path_source",
        )
    if (
        not reactionevent.endswith(".timeline.h5")
        and (not molecules or not Path(molecules).is_file())
    ):
        raise ServiceError(
            "当前数据集缺少 .molecules.csv，无法证明原子连续性",
            reason="missing_event_path_source",
        )
    sources = [
        EventPathSource(
            replicate=label,
            reactionevent_file=reactionevent,
            molecules_file=molecules,
            reaction_file=(reaction if reaction and Path(reaction).is_file() else ""),
        ),
        *_additional_event_path_sources(additional_sources),
    ]
    labels = [source.replicate for source in sources]
    if len(set(labels)) != len(labels):
        raise ServiceError(
            "当前数据集与附加重复的标签必须唯一",
            reason="bad_event_path_source",
        )
    return sources


def validate_event_path_sources_for_dash(
    artifacts: Mapping[str, Any],
    *,
    current_replicate: str = "current",
    additional_sources: str = "",
) -> dict[str, Any]:
    """Validate source files and prepared indexes before leaving wizard step 1."""
    sources = _event_path_sources_for_dash(
        artifacts,
        current_replicate=current_replicate,
        additional_sources=additional_sources,
    )
    documents: list[dict[str, Any]] = []
    for source in sources:
        try:
            status = EVENT_EVIDENCE_STORE.status(
                source.reactionevent_file,
                source.molecules_file,
                metadata_only=True,
            )
        except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            raise ServiceError(
                f"{source.replicate}: 无法读取事件索引状态: {exc}",
                reason="invalid_event_path_evidence",
            ) from exc
        state = str(status.get("state") or "missing")
        if state != "ready":
            raise ServiceError(
                f"{source.replicate}: 事件索引状态为 {state}；"
                "请先在“管理数据”中建立或重建事件索引",
                reason="event_index_not_ready",
            )
        if not status.get("association_available"):
            raise ServiceError(
                f"{source.replicate}: 事件索引没有分子实例与原子 ID 关联",
                reason="invalid_event_path_evidence",
            )
        documents.append(
            {
                "replicate": source.replicate,
                "reactionevent_file": source.reactionevent_file,
                "molecules_file": source.molecules_file,
                "reaction_file": source.reaction_file,
                "event_count": int(status.get("event_count") or 0),
                "available_intervals": int(status.get("available_intervals") or 0),
                "time_basis": str(status.get("time_basis") or ""),
                "state": state,
            }
        )
    return {
        "replicate_count": len(documents),
        "sources": documents,
        "total_event_count": sum(item["event_count"] for item in documents),
    }


def analyze_event_paths_for_dash(
    artifacts: Mapping[str, Any],
    *,
    current_replicate: str = "current",
    additional_sources: str = "",
    path_length: int = 3,
    start_smiles: str = "",
    max_interval_gap: int | None = None,
    max_timestep_gap: int | None = None,
    max_occurrence_details: int = 1_000,
) -> dict[str, Any]:
    """Run the strict event-path engine for the current Dash dataset."""
    sources = _event_path_sources_for_dash(
        artifacts,
        current_replicate=current_replicate,
        additional_sources=additional_sources,
    )
    try:
        return analyze_event_paths(
            sources,
            path_length=int(path_length),
            start_smiles=str(start_smiles or "").strip(),
            max_interval_gap=(
                None if max_interval_gap in (None, "") else int(max_interval_gap)
            ),
            max_timestep_gap=(
                None if max_timestep_gap in (None, "") else int(max_timestep_gap)
            ),
            max_occurrence_details=int(max_occurrence_details),
        )
    except (IndexNotReadyError, IndexStaleError) as exc:
        raise ServiceError(
            "事件索引尚未准备或已经过期；请在“管理数据”中建立事件索引",
            reason="event_index_not_ready",
        ) from exc
    except (IndexInvalidError, EventPathAnalysisError) as exc:
        raise ServiceError(
            f"事件路径证据不可用: {exc}",
            reason="invalid_event_path_evidence",
        ) from exc
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_event_path_source") from exc
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            f"无效的事件路径参数: {exc}",
            reason="bad_event_path_query",
        ) from exc


def _compact_event_path_text(value: str, *, limit: int = 260) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


_PURE_H_EVENT_KEYS = {
    "[H]+[H]->[H][H]",
    "[H][H]->[H]+[H]",
}


def event_path_signature_rows(
    report: Mapping[str, Any],
    *,
    hide_pure_h: bool = False,
    hide_return_cycles: bool = False,
    min_reproduction_rate: float = 0.0,
    min_lineage_support: int = 0,
) -> list[dict[str, Any]]:
    """Create compact, filterable rows without losing report provenance."""
    rows: list[dict[str, Any]] = []
    for path in report.get("paths") or []:
        keys = [str(value) for value in path.get("reaction_keys") or []]
        pure_h = bool(keys) and set(keys).issubset(_PURE_H_EVENT_KEYS)
        return_cycle = len(keys) >= 3 and keys[0] == keys[-1]
        rate = float(path.get("replicate_reproduction_rate") or 0.0)
        lineages = int(path.get("independent_atom_lineage_support_count") or 0)
        if hide_pure_h and pure_h:
            continue
        if hide_return_cycles and return_cycle:
            continue
        if rate < float(min_reproduction_rate or 0.0):
            continue
        if lineages < int(min_lineage_support or 0):
            continue
        span = path.get("anchor_timestep_span") or {}
        rows.append(
            {
                "rank": len(rows) + 1,
                "signature_id": str(path.get("signature_id") or ""),
                "reaction_path": _compact_event_path_text(" | ".join(keys)),
                "occurrences": int(path.get("occurrence_count") or 0),
                "atom_lineages": lineages,
                "lineage_sets": int(
                    path.get("independent_lineage_set_support_count") or 0
                ),
                "replicate_support": int(path.get("replicate_support_count") or 0),
                "reproduction_rate": rate,
                "median_timestep_span": span.get("median"),
                "pure_h_cycle": pure_h,
                "return_cycle": return_cycle,
                "support_is_lower_bound": bool(path.get("support_is_lower_bound")),
            }
        )
    return rows


def event_path_comparison_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in (report.get("comparison") or {}).get("per_replicate") or []:
        rows.append(
            {
                "replicate": str(item.get("replicate") or ""),
                "aggregate_reachable": int(
                    item.get("aggregate_reachable_path_count") or 0
                ),
                "actual": int(item.get("actual_path_signature_count") or 0),
                "confirmed": int(item.get("confirmed_actual_path_count") or 0),
                "aggregate_only": item.get("aggregate_only_path_count"),
                "actual_only": int(item.get("actual_only_path_count") or 0),
                "realization_rate": item.get("realization_rate"),
                "complete": bool(item.get("comparison_complete")),
            }
        )
    return rows


def event_path_comparison_signature_rows(
    report: Mapping[str, Any],
    classification: str,
) -> list[dict[str, Any]]:
    safe_class = str(classification or "confirmed")
    if safe_class not in {"confirmed", "aggregate_only", "actual_only"}:
        safe_class = "confirmed"
    rows: list[dict[str, Any]] = []
    for item in (report.get("comparison") or {}).get("per_replicate") or []:
        replicate = str(item.get("replicate") or "")
        for signature in item.get(safe_class) or []:
            keys = [str(value) for value in signature.get("reaction_keys") or []]
            rows.append(
                {
                    "replicate": replicate,
                    "classification": safe_class,
                    "signature_id": str(signature.get("signature_id") or ""),
                    "reaction_path": _compact_event_path_text(" | ".join(keys)),
                }
            )
    return rows


def event_path_occurrences_for_signature(
    report: Mapping[str, Any],
    signature_id: str,
) -> list[dict[str, Any]]:
    selected = next(
        (
            item
            for item in report.get("paths") or []
            if str(item.get("signature_id") or "") == str(signature_id or "")
        ),
        None,
    )
    if selected is None:
        return []
    keys = [str(value) for value in selected.get("reaction_keys") or []]
    return [
        dict(item)
        for item in report.get("occurrences") or []
        if [str(value) for value in item.get("reaction_keys") or []] == keys
    ]


def event_path_signature_time_rows(
    report: Mapping[str, Any],
    signature_id: str,
) -> list[dict[str, Any]]:
    selected = next(
        (
            item
            for item in report.get("paths") or []
            if str(item.get("signature_id") or "") == str(signature_id or "")
        ),
        None,
    )
    if selected is None:
        return []
    interval = list(selected.get("interval_gap_by_edge") or [])
    idle = list(selected.get("idle_timestep_gap_by_edge") or [])
    anchor = list(selected.get("anchor_timestep_gap_by_edge") or [])
    row_count = max(len(interval), len(idle), len(anchor))
    rows: list[dict[str, Any]] = []
    for index in range(row_count):
        interval_stats = interval[index] if index < len(interval) else {}
        idle_stats = idle[index] if index < len(idle) else {}
        anchor_stats = anchor[index] if index < len(anchor) else {}
        rows.append(
            {
                "edge": index + 1,
                "samples": interval_stats.get("count"),
                "interval_min": interval_stats.get("min"),
                "interval_median": interval_stats.get("median"),
                "interval_mean": interval_stats.get("mean"),
                "interval_max": interval_stats.get("max"),
                "idle_min": idle_stats.get("min"),
                "idle_median": idle_stats.get("median"),
                "idle_mean": idle_stats.get("mean"),
                "idle_max": idle_stats.get("max"),
                "anchor_min": anchor_stats.get("min"),
                "anchor_median": anchor_stats.get("median"),
                "anchor_mean": anchor_stats.get("mean"),
                "anchor_max": anchor_stats.get("max"),
            }
        )
    return rows


def event_path_occurrence_rows(
    occurrence: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    event_rows: list[dict[str, Any]] = []
    for index, event in enumerate(occurrence.get("events") or [], start=1):
        event_rows.append(
            {
                "step": index,
                "event_id": str(event.get("event_id") or ""),
                "interval": event.get("timestep_index"),
                "before_timestep": event.get("before_timestep"),
                "after_timestep": event.get("after_timestep"),
                "reaction": _compact_event_path_text(
                    str(event.get("reaction_smiles") or ""), limit=360
                ),
                "participating_atoms": ";".join(
                    str(value) for value in event.get("atom_ids") or []
                ),
            }
        )
    edge_rows: list[dict[str, Any]] = []
    for index, edge in enumerate(occurrence.get("edges") or [], start=1):
        molecule_labels = []
        for molecule in edge.get("molecule_instances") or []:
            atoms = [int(value) for value in molecule.get("atom_ids") or []]
            molecule_labels.append(
                f"{molecule.get('species') or '?'} @ {{{','.join(map(str, atoms))}}}"
            )
        edge_rows.append(
            {
                "edge": index,
                "from_event_id": str(edge.get("from_event_id") or ""),
                "to_event_id": str(edge.get("to_event_id") or ""),
                "molecule_instances": _compact_event_path_text(
                    " + ".join(molecule_labels), limit=360
                ),
                "carrier_atom_ids": ";".join(
                    str(value) for value in edge.get("carrier_atom_ids") or []
                ),
                "interval_gap": edge.get("interval_gap"),
                "idle_timestep_gap": edge.get("idle_timestep_gap"),
                "anchor_timestep_gap": edge.get("anchor_timestep_gap"),
            }
        )
    return event_rows, edge_rows


def build_event_path_occurrence_elements(
    occurrence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build a concrete event-node graph for one audited occurrence."""
    elements: list[dict[str, Any]] = []
    for index, event in enumerate(occurrence.get("events") or [], start=1):
        event_id = str(event.get("event_id") or f"event-{index}")
        elements.append(
            {
                "data": {
                    "id": event_id,
                    "node_kind": "concrete_event",
                    "label": f"E{index} · interval {event.get('timestep_index')}",
                    "event_id": event_id,
                    "step": index,
                    "reaction": str(event.get("reaction_smiles") or ""),
                    "before_timestep": event.get("before_timestep"),
                    "after_timestep": event.get("after_timestep"),
                },
                "classes": "concrete-event",
            }
        )
    for index, edge in enumerate(occurrence.get("edges") or [], start=1):
        molecules = edge.get("molecule_instances") or []
        species = " + ".join(str(item.get("species") or "?") for item in molecules)
        carrier_atoms = [int(value) for value in edge.get("carrier_atom_ids") or []]
        elements.append(
            {
                "data": {
                    "id": f"event-path-edge-{index}",
                    "source": str(edge.get("from_event_id") or ""),
                    "target": str(edge.get("to_event_id") or ""),
                    "label": _compact_event_path_text(
                        f"{species} · {len(carrier_atoms)} atoms", limit=72
                    ),
                    "molecule_instances": molecules,
                    "carrier_atom_ids": carrier_atoms,
                    "interval_gap": edge.get("interval_gap"),
                    "idle_timestep_gap": edge.get("idle_timestep_gap"),
                },
                "classes": "molecule-instance-edge",
            }
        )
    return elements


def _species_catalog_entry(
    smiles: str,
    total_count: int,
    *,
    catalog_source: str = ".species",
) -> dict[str, Any]:
    """Build one catalogue row, calculating chemistry only for that SMILES."""
    formula = smiles_formula_cached(smiles) or "?"
    mass_fields = formula_mass_fields(formula) if formula != "?" else {}
    return {
        "smiles": smiles,
        "formula": formula,
        "exact_mass": mass_fields.get("exact_mass"),
        "nominal_mass": mass_fields.get("nominal_mass"),
        "total_count": int(total_count),
        "catalog_source": catalog_source,
        "structure_source": (
            ".reactionabcd" if catalog_source == ".reactionabcd" else "SMILES"
        ),
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
) -> tuple[dict[str, Any], ...]:
    """Materialize formula/mass metadata once per input-file revision.

    A 10k+ species catalogue must only pay the RDKit formula/mass cost once;
    later searches filter this cached metadata and the cache expires when the
    Species Abundance Evidence changes.
    """
    del species_size, species_mtime_ns
    totals = collect_species_totals(species_path_text)
    catalog: list[dict[str, Any]] = []
    for smiles, total_count in totals.items():
        catalog.append(_species_catalog_entry(smiles, int(total_count)))
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
        catalog = _load_species_search_catalog(*species_signature)
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
    # Imported lazily to keep the analysis and evidence workflows acyclic.
    from reacnet_scope.evidence_services import locate_rng_events

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
    action = "rebuild" if rebuild else "build"
    return f"reacnet-scope prepare {action} event {shlex.quote(source)}"


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
    reactionevent_file, molecules_file = _event_artifact_paths(artifacts)
    event_id = str(anchor.get("event_id") or "")
    if reactionevent_file and Path(reactionevent_file).is_file():
        event_molecules = molecules_file
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
    """Build the formula-reaction query model for Dash."""
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
