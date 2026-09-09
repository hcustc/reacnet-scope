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

import csv
import io
import json
import math
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
from pathlib import Path
from collections import Counter
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from reacnet_scope.network import ReactionNetwork, count_atoms_fast, formula_from_counts  # noqa: E402
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
)
from reacnet_scope.event_package import (  # noqa: E402
    build_event_package,
    event_trajectory_text,
)
from reacnet_scope.event_paths import (  # noqa: E402
    EventPathAnalysisError,
    EventPathSource,
    verify_event_path,
)
from reacnet_scope.candidate_paths import (  # noqa: E402
    discover_network_candidate_routes,
    load_energy_evidence_csv,
    rank_candidate_paths,
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
    load_linked_trajectory,
    load_type_element_map,
    load_coordinate_length_unit,
    load_timestep_ps,
    normalize_type_element_map,
    read_lammps_frame_block,
    recentered_positions,
    save_coordinate_length_unit,
    save_linked_trajectory,
    save_type_element_map,
    save_timestep_ps,
    select_local_environment,
)
from reacnet_scope.kinetics import (  # noqa: E402
    KineticsInputError,
    estimate_mass_action_rate_aligned,
)
from reacnet_scope.queries import (  # noqa: E402
    STORE,
    build_dataset_status_payload,
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
    reaction_smiles_to_svg,
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


def _reaction_min_tp(artifacts: Mapping[str, Any]) -> int:
    """Return the session-level reaction throughput threshold."""
    try:
        return max(1, int(artifacts.get("_min_tp") or 1))
    except (TypeError, ValueError):
        return 1


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


def verify_event_path_for_dash(
    artifacts: Mapping[str, Any],
    *,
    current_replicate: str = "current",
    additional_sources: str = "",
    reaction_sequence: str | Iterable[str],
    max_interval_gap: int | None = None,
    max_timestep_gap: int | None = None,
    max_occurrence_details: int = 1_000,
) -> dict[str, Any]:
    """Verify one explicit Reaction Type sequence for the current dataset."""
    sources = _event_path_sources_for_dash(
        artifacts,
        current_replicate=current_replicate,
        additional_sources=additional_sources,
    )
    try:
        reaction_keys = (
            str(reaction_sequence or "").splitlines()
            if isinstance(reaction_sequence, str)
            else list(reaction_sequence)
        )
        return verify_event_path(
            sources,
            reaction_keys,
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
            f"无效的路径验证参数: {exc}",
            reason="bad_event_path_query",
        ) from exc


def discover_candidate_paths_for_dash(
    artifacts: Mapping[str, Any],
    start_species: str | Iterable[str],
    *,
    current_replicate: str = "current",
    additional_sources: str = "",
    minimum_path_length: int = 2,
    maximum_path_length: int = 4,
    max_interval_gap: int | None = None,
    max_timestep_gap: int | None = None,
    max_expansions: int = 5_000,
    max_paths: int = 20,
    minimum_occurrences: int = 1,
    energy_csv: str = "",
) -> dict[str, Any]:
    """Discover bounded network Candidates and attach indexed Step Evidence."""

    if isinstance(start_species, str):
        starts = [
            line.strip()
            for line in start_species.replace(";", "\n").splitlines()
            if line.strip()
        ]
    else:
        starts = [str(value).strip() for value in start_species if str(value).strip()]
    reaction_file = str(artifacts.get("reaction") or "").strip()
    if not reaction_file or not Path(reaction_file).is_file():
        raise ServiceError(
            "缺少 .reactionabcd，无法计算候选路径的反应频次",
            reason="missing_reaction_network",
        )
    sources = _event_path_sources_for_dash(
        artifacts,
        current_replicate=current_replicate,
        additional_sources=additional_sources,
    )
    energy_path = (
        str(validate_browse_path(str(energy_csv)))
        if str(energy_csv or "").strip()
        else ""
    )
    try:
        network = STORE.get(reaction_file, _reaction_min_tp(artifacts))
        evidence_report = discover_network_candidate_routes(
            network,
            starts,
            minimum_path_length=int(minimum_path_length),
            maximum_path_length=int(maximum_path_length),
            max_expansions=int(max_expansions),
            max_paths=int(max_paths),
            minimum_occurrences=int(minimum_occurrences),
        )
        candidate_keys = sorted(
            {
                str(key)
                for path in evidence_report.get("paths") or ()
                for key in path.get("reaction_keys") or ()
            }
        )
        summaries_by_replicate: dict[str, dict[str, dict[str, Any]]] = {}
        source_documents: list[dict[str, Any]] = []
        for source in sources:
            summaries = EVENT_EVIDENCE_STORE.reaction_summary(
                source.reactionevent_file,
                source.molecules_file,
                candidate_keys,
            )
            summaries_by_replicate[source.replicate] = summaries
            source_documents.append(
                {
                    "replicate": source.replicate,
                    "reactionevent_file": source.reactionevent_file,
                    "molecules_file": source.molecules_file,
                    "queried_reaction_type_count": len(candidate_keys),
                }
            )

        evidence_paths: list[dict[str, Any]] = []
        for raw_path in evidence_report.get("paths") or ():
            path = dict(raw_path)
            reaction_keys = tuple(
                str(value) for value in path.get("reaction_keys") or ()
            )
            step_evidence: list[dict[str, Any]] = []
            for reaction_key_text in reaction_keys:
                by_replicate = {
                    replicate: dict(summaries.get(reaction_key_text) or {})
                    for replicate, summaries in summaries_by_replicate.items()
                }
                total_events = sum(
                    int(summary.get("total_events") or 0)
                    for summary in by_replicate.values()
                )
                step_evidence.append(
                    {
                        "reaction_key": reaction_key_text,
                        "total_events": total_events,
                        "matched_events": sum(
                            int(summary.get("matched_events") or 0)
                            for summary in by_replicate.values()
                        ),
                        "distinct_intervals": sum(
                            int(summary.get("distinct_intervals") or 0)
                            for summary in by_replicate.values()
                        ),
                        "replicate_count": sum(
                            int(summary.get("total_events") or 0) > 0
                            for summary in by_replicate.values()
                        ),
                    }
                )
            if not step_evidence or any(
                int(step["total_events"]) < int(minimum_occurrences)
                for step in step_evidence
            ):
                continue
            replicate_support = sum(
                all(
                    int(
                        summaries_by_replicate[replicate]
                        .get(reaction_key_text, {})
                        .get("total_events")
                        or 0
                    )
                    > 0
                    for reaction_key_text in reaction_keys
                )
                for replicate in summaries_by_replicate
            )
            minimum_step_events = min(
                int(step["total_events"]) for step in step_evidence
            )
            path.update(
                occurrence_count=minimum_step_events,
                minimum_step_occurrence_count=minimum_step_events,
                replicate_support_count=replicate_support,
                replicate_reproduction_rate=(
                    replicate_support / len(summaries_by_replicate)
                    if summaries_by_replicate
                    else 0.0
                ),
                step_evidence=step_evidence,
                support_is_lower_bound=bool(
                    (evidence_report.get("summary") or {}).get(
                        "traversal_truncated"
                    )
                ),
            )
            evidence_paths.append(path)
        evidence_report["paths"] = evidence_paths
        evidence_report["sources"] = source_documents
        evidence_report["summary"].update(
            {
                "replicate_count": len(sources),
                "candidate_route_count": len(evidence_paths),
                "step_evidence_only": True,
            }
        )
        energy = (
            load_energy_evidence_csv(energy_path)
            if energy_path
            else None
        )
        result = rank_candidate_paths(
            network,
            evidence_report,
            starts,
            max_paths=int(max_paths),
            minimum_occurrences=int(minimum_occurrences),
            energy_evidence=energy,
        )
        result["query"].update(
            {
                "minimum_path_length": int(minimum_path_length),
                "maximum_path_length": int(maximum_path_length),
                "max_interval_gap": max_interval_gap,
                "max_timestep_gap": max_timestep_gap,
                "max_expansions": int(max_expansions),
                "energy_csv": energy_path,
                "continuous_support_filters_deferred": bool(
                    max_interval_gap not in (None, "")
                    or max_timestep_gap not in (None, "")
                ),
            }
        )
        return result
    except (IndexNotReadyError, IndexStaleError) as exc:
        raise ServiceError(
            "事件索引尚未准备或已经过期；请在“管理数据”中建立事件索引",
            reason="event_index_not_ready",
        ) from exc
    except (IndexInvalidError, EventPathAnalysisError) as exc:
        raise ServiceError(
            f"候选路径事件证据不可用: {exc}",
            reason="invalid_candidate_path_evidence",
        ) from exc
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_candidate_path_source") from exc
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            f"无效的候选路径参数: {exc}",
            reason="bad_candidate_path_query",
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


def render_reaction_svg(
    reaction_smiles: str,
    *,
    width: int = 720,
    height: int = 220,
    show_h: bool = True,
) -> dict[str, Any]:
    """Render a complete reaction expression as one SVG preview."""
    text = str(reaction_smiles or "").strip()
    if not text:
        return {"ok": False, "svg": "", "message": "未选择反应式"}
    try:
        svg = reaction_smiles_to_svg(
            text,
            width=width,
            height=height,
            show_h=show_h,
        )
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


def _rate_display(value: Any, unit: str) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(numeric):
        return ""
    return f"{numeric:.4g} {unit}".strip()


def channel_timestep_ps(artifacts: Mapping[str, Any]) -> float | None:
    """Return the confirmed physical-time conversion used by channel rates."""
    for artifact in ("species", "trajectory"):
        source_file = str(artifacts.get(artifact) or "").strip()
        if source_file:
            timestep_ps = load_timestep_ps(source_file)
            if timestep_ps is not None:
                return timestep_ps
    return None


def confirm_channel_timestep_ps(
    artifacts: Mapping[str, Any],
    value: Any,
) -> float:
    """Persist a user-confirmed conversion for the current channel dataset."""
    species_file = str(artifacts.get("species") or "").strip()
    if not species_file or not Path(species_file).is_file():
        raise ServiceError(
            "当前数据集缺少 .species 文件，无法保存表观速率时间换算。",
            reason="missing_species_file",
        )
    try:
        timestep_ps = float(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            "请输入大于 0 的 timestep → ps 换算值。",
            reason="invalid_timestep",
        ) from exc
    if not math.isfinite(timestep_ps) or timestep_ps <= 0:
        raise ServiceError(
            "请输入大于 0 的 timestep → ps 换算值。",
            reason="invalid_timestep",
        )
    try:
        save_timestep_ps(species_file, timestep_ps)
    except (TrajectoryFrameError, OSError) as exc:
        raise ServiceError(
            f"保存 timestep → ps 换算失败：{exc}",
            reason="timestep_save_failed",
        ) from exc
    return timestep_ps


def _channel_dataset_source(artifacts: Mapping[str, Any]) -> str:
    for artifact in ("species", "reaction", "trajectory"):
        source = str(artifacts.get(artifact) or "").strip()
        if source:
            return source
    return ""


def channel_volume_evidence(
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe the current dataset's simulation-box volume evidence."""
    dataset_source = _channel_dataset_source(artifacts)
    trajectory_file = str(artifacts.get("trajectory") or "").strip()
    source = "dataset_artifact" if trajectory_file else ""
    linked_trajectory = ""
    if dataset_source:
        try:
            linked_trajectory = str(
                load_linked_trajectory(dataset_source) or ""
            ).strip()
        except TrajectoryFrameError as exc:
            return {
                "ready": False,
                "trajectory": "",
                "source": "workspace_link",
                "coordinate_length_unit": None,
                "index_state": "invalid",
                "reason": "invalid_trajectory_link",
                "message": f"已保存的轨迹关联无效：{exc}",
            }
        if linked_trajectory and (
            not trajectory_file
            or Path(linked_trajectory).expanduser().resolve()
            == Path(trajectory_file).expanduser().resolve()
        ):
            trajectory_file = linked_trajectory
            source = "workspace_link"
    if not trajectory_file:
        return {
            "ready": False,
            "trajectory": "",
            "source": "",
            "coordinate_length_unit": None,
            "index_state": "missing",
            "reason": "missing_trajectory",
            "message": "缺少用于双分子表观 k 的模拟盒轨迹；请关联 .lammpstrj。",
        }
    trajectory_path = Path(trajectory_file).expanduser().resolve()
    if not trajectory_path.is_file():
        return {
            "ready": False,
            "trajectory": str(trajectory_path),
            "source": source,
            "coordinate_length_unit": None,
            "index_state": "missing_source",
            "reason": "linked_trajectory_missing",
            "message": f"已关联的轨迹文件不存在：{trajectory_path}",
        }
    try:
        coordinate_unit = load_coordinate_length_unit(str(trajectory_path))
    except TrajectoryFrameError as exc:
        return {
            "ready": False,
            "trajectory": str(trajectory_path),
            "source": source,
            "coordinate_length_unit": None,
            "index_state": "invalid",
            "reason": "invalid_length_unit_confirmation",
            "message": str(exc),
        }
    try:
        index_status = TRAJECTORY_INDEX_STORE.status(str(trajectory_path))
    except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
        index_status = {"state": "invalid", "message": str(exc)}
    index_state = str(index_status.get("state") or "missing")
    if coordinate_unit != "angstrom":
        reason = "unconfirmed_length_unit"
        message = "请确认已关联轨迹的坐标长度单位为 Å。"
    elif index_state != "ready":
        reason, message = {
            "stale": (
                "trajectory_index_stale",
                "轨迹已变化；请重建轨迹帧索引。",
            ),
            "invalid": (
                "trajectory_index_invalid",
                "轨迹帧索引无效；请重建索引。",
            ),
            "building": (
                "trajectory_index_building",
                "轨迹帧索引正在后台建立。",
            ),
        }.get(
            index_state,
            (
                "trajectory_index_not_ready",
                "轨迹已关联；请建立轨迹帧索引以读取逐帧模拟盒体积。",
            ),
        )
    else:
        reason = ""
        message = "模拟盒体积证据已就绪。"
    return {
        "ready": not reason,
        "trajectory": str(trajectory_path),
        "source": source,
        "coordinate_length_unit": coordinate_unit,
        "index_state": index_state,
        "reason": reason,
        "message": message,
        "index": index_status,
    }


def configure_channel_volume_source(
    artifacts: Mapping[str, Any],
    trajectory_file: Any,
    *,
    confirm_angstrom: bool = False,
) -> dict[str, Any]:
    """Associate a trajectory with the current dataset and report readiness."""
    dataset_source = _channel_dataset_source(artifacts)
    if not dataset_source or not Path(dataset_source).is_file():
        raise ServiceError(
            "当前数据集缺少可保存关联的源证据。",
            reason="missing_dataset_source",
        )
    try:
        trajectory_path = validate_browse_path(str(trajectory_file or ""))
    except Exception as exc:
        raise ServiceError(str(exc), reason="invalid_trajectory_path") from exc
    if trajectory_path.suffix.lower() != ".lammpstrj":
        raise ServiceError(
            "请选择 .lammpstrj 轨迹文件。",
            reason="invalid_trajectory_file",
        )
    if not trajectory_path.is_file():
        raise ServiceError(
            f"轨迹文件不存在：{trajectory_path}",
            reason="missing_trajectory_file",
        )
    try:
        save_linked_trajectory(dataset_source, str(trajectory_path))
        if confirm_angstrom:
            save_coordinate_length_unit(str(trajectory_path), "angstrom")
    except (TrajectoryFrameError, OSError) as exc:
        raise ServiceError(
            f"保存轨迹关联失败：{exc}",
            reason="trajectory_link_save_failed",
        ) from exc
    return channel_volume_evidence(artifacts)


def _channel_reaction_key(row: Mapping[str, Any], *, reverse: bool = False) -> str:
    reactants = tuple(str(value) for value in row.get("reactant_smiles") or [])
    products = tuple(str(value) for value in row.get("product_smiles") or [])
    if reverse:
        reactants, products = products, reactants
    return canonical_reaction_key(reactants, products)


def _unavailable_kinetics(
    rows: Iterable[dict[str, Any]],
    *,
    reason: str,
    message: str,
) -> dict[str, Any]:
    for row in rows:
        row.update(
            kinetics_status="unavailable",
            kinetics_reason=reason,
            kinetics_reason_message=message,
            reverse_kinetics_reason=reason,
            reverse_kinetics_reason_message=message,
            k_app=None,
            k_app_unit="",
            k_app_display="",
            event_frequency_per_ps=None,
        )
    return {
        "status": "unavailable",
        "reason": reason,
        "message": message,
        "model": "stoichiometric_mass_action",
    }


def _enrich_channel_kinetics(
    artifacts: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach physical event frequency and auditable apparent k estimates."""
    if not rows:
        return {"status": "not_applicable", "message": ""}
    species_file = str(artifacts.get("species") or "").strip()
    if not species_file or not Path(species_file).is_file():
        return _unavailable_kinetics(
            rows,
            reason="missing_species_abundance",
            message="表观速率不可用：缺少 .species 丰度证据。",
        )
    timestep_ps = channel_timestep_ps(artifacts)
    if timestep_ps is None:
        return _unavailable_kinetics(
            rows,
            reason="missing_physical_time",
            message="表观速率不可用：请先确认并保存 timestep → ps 换算。",
        )
    reactionevent_file, molecules_file = _event_artifact_paths(artifacts)
    if not reactionevent_file or not Path(reactionevent_file).is_file():
        return _unavailable_kinetics(
            rows,
            reason="missing_reaction_occurrences",
            message="表观速率不可用：缺少 Timeline 或 Reaction Occurrence 证据。",
        )

    try:
        composition = SPECIES_COMPOSITION_STORE.open_required(species_file)
        timesteps = SPECIES_COMPOSITION_STORE.timesteps(species_file)
        opened_events = EVENT_EVIDENCE_STORE.open_required(
            reactionevent_file,
            molecules_file,
        )
    except IndexNotReadyError as exc:
        return _unavailable_kinetics(
            rows,
            reason="index_not_ready",
            message=f"表观速率不可用：所需索引未就绪（{exc}）。",
        )
    if opened_events.get("time_basis") != "physical_timestep":
        return _unavailable_kinetics(
            rows,
            reason="event_time_basis",
            message="表观速率不可用：事件索引没有物理 source timestep。",
        )
    if len(timesteps) < 2:
        return _unavailable_kinetics(
            rows,
            reason="insufficient_timepoints",
            message="表观速率不可用：至少需要两个 Species Abundance 时间点。",
        )

    reaction_keys = {
        key
        for row in rows
        for key in (
            _channel_reaction_key(row),
            _channel_reaction_key(row, reverse=True),
        )
    }
    try:
        event_counts = EVENT_EVIDENCE_STORE.reaction_counts(
            reactionevent_file,
            molecules_file,
            reaction_keys,
            before_timestep=timesteps[0],
            after_timestep=timesteps[-1],
        )
    except (IndexNotReadyError, OSError, ValueError) as exc:
        return _unavailable_kinetics(
            rows,
            reason="event_count_unavailable",
            message=f"表观速率不可用：无法读取 Reaction Occurrence 计数（{exc}）。",
        )

    required_species = {
        str(species)
        for row in rows
        for side in ("reactant_smiles", "product_smiles")
        for species in row.get(side) or []
        if str(species)
    }
    try:
        count_series = SPECIES_COMPOSITION_STORE.species_count_matrix(
            species_file,
            timesteps,
            sorted(required_species),
        )
    except (IndexNotReadyError, ValueError, OSError) as exc:
        return _unavailable_kinetics(
            rows,
            reason="abundance_exposure_unavailable",
            message=f"表观速率不可用：无法读取反应物丰度暴露量（{exc}）。",
        )

    needs_volume = any(
        len(row.get(side) or []) == 2
        for row in rows
        for side in ("reactant_smiles", "product_smiles")
    )
    volumes: dict[int, float] | None = None
    volume_reason_code = ""
    volume_reason = ""
    if needs_volume:
        volume_evidence = channel_volume_evidence(artifacts)
        trajectory_file = str(volume_evidence.get("trajectory") or "")
        if not volume_evidence.get("ready"):
            volume_reason_code = str(
                volume_evidence.get("reason") or "volume_evidence_unavailable"
            )
            volume_reason = str(volume_evidence.get("message") or "")
        else:
            try:
                trajectory_index = TRAJECTORY_INDEX_STORE.open_required(
                    trajectory_file
                )
                volumes = trajectory_index.volumes_for(timesteps)
                if len(volumes) < len(timesteps) - 1:
                    volume_reason_code = "misaligned_simulation_box_volume"
                    volume_reason = "轨迹索引缺少与丰度时间点对齐的模拟盒体积"
            except (IndexNotReadyError, TrajectoryFrameError, OSError) as exc:
                volume_reason_code = "volume_evidence_unavailable"
                volume_reason = str(exc)

    estimated = 0
    unsupported = 0
    reason_counts: Counter[str] = Counter()
    observation_time_ps = (timesteps[-1] - timesteps[0]) * float(timestep_ps)
    for row in rows:
        forward_key = _channel_reaction_key(row)
        reverse_key = _channel_reaction_key(row, reverse=True)
        directions = (
            (
                "",
                row.get("reactant_smiles") or [],
                int(event_counts.get(forward_key, 0)),
            ),
            (
                "reverse_",
                row.get("product_smiles") or [],
                int(event_counts.get(reverse_key, 0)),
            ),
        )
        direction_statuses: list[str] = []
        for prefix, reactants, occurrence_count in directions:
            direction_reason = ""
            direction_reason_message = ""
            if len(reactants) == 2 and volume_reason:
                estimate = None
                direction_reason = volume_reason_code
                direction_reason_message = volume_reason
            else:
                try:
                    estimate = estimate_mass_action_rate_aligned(
                        event_count=occurrence_count,
                        timesteps=timesteps,
                        timestep_ps=float(timestep_ps),
                        reactants=reactants,
                        species_counts=count_series,
                        volumes_angstrom3=volumes,
                    )
                except KineticsInputError as exc:
                    estimate = None
                    direction_reason = (
                        "unsupported_reaction_order"
                        if len(reactants) not in {1, 2}
                        else "kinetics_input_error"
                    )
                    direction_reason_message = str(exc)
            if estimate is None:
                row[f"{prefix}event_count"] = occurrence_count
                row[f"{prefix}event_frequency_per_ps"] = (
                    occurrence_count / observation_time_ps
                )
                row[f"{prefix}k_app"] = None
                row[f"{prefix}k_app_unit"] = ""
                row[f"{prefix}k_app_display"] = ""
                row[f"{prefix}kinetics_reason"] = direction_reason
                row[f"{prefix}kinetics_reason_message"] = (
                    direction_reason_message
                )
                row["observation_time_ps"] = observation_time_ps
                direction_statuses.append("unavailable")
                continue
            if estimate.get("status") != "estimated":
                direction_reason = "zero_reactant_exposure"
                direction_reason_message = "观察窗内反应物暴露量为零"
            unit = str(estimate["k_app_unit"])
            row[f"{prefix}event_count"] = occurrence_count
            row[f"{prefix}event_frequency_per_ps"] = estimate[
                "event_frequency_per_ps"
            ]
            row[f"{prefix}k_app"] = estimate["k_app"]
            row[f"{prefix}k_app_unit"] = unit
            row[f"{prefix}k_app_display"] = _rate_display(
                estimate["k_app"], unit
            )
            row[f"{prefix}k_app_ci95_low"] = estimate["ci95_low"]
            row[f"{prefix}k_app_ci95_high"] = estimate["ci95_high"]
            row[f"{prefix}kinetic_exposure"] = estimate["exposure"]
            row[f"{prefix}kinetic_exposure_unit"] = estimate[
                "exposure_unit"
            ]
            row[f"{prefix}kinetics_reason"] = direction_reason
            row[f"{prefix}kinetics_reason_message"] = (
                direction_reason_message
            )
            row["observation_time_ps"] = estimate["observation_time_ps"]
            row["kinetic_model"] = estimate["model"]
            direction_statuses.append(
                "estimated" if not direction_reason else "unavailable"
            )
        row["kinetics_status"] = (
            "estimated"
            if all(value == "estimated" for value in direction_statuses)
            else "partial"
            if any(value == "estimated" for value in direction_statuses)
            else "unavailable"
        )
        if row.get("k_app") is not None:
            estimated += 1
        else:
            unsupported += 1
            reason_counts[str(row.get("kinetics_reason") or "unknown")] += 1

    message = (
        "表观 k 已按 Reaction Occurrence、左端点丰度暴露量和化学计量质量作用假设计算；"
        "它不是无模型的本征速率常数。"
    )
    reason_messages = {
        "missing_trajectory": (
            "{count} 条双分子通道缺少关联的 .lammpstrj，未给出 k。"
        ),
        "linked_trajectory_missing": (
            "{count} 条双分子通道关联的 .lammpstrj 已不存在，未给出 k。"
        ),
        "unconfirmed_length_unit": (
            "{count} 条双分子通道尚未确认轨迹长度单位为 Å，未给出 k。"
        ),
        "trajectory_index_not_ready": (
            "{count} 条双分子通道的轨迹索引未就绪，未给出 k。"
        ),
        "trajectory_index_stale": (
            "{count} 条双分子通道的轨迹索引已过期，未给出 k。"
        ),
        "trajectory_index_invalid": (
            "{count} 条双分子通道的轨迹索引无效，未给出 k。"
        ),
        "trajectory_index_building": (
            "{count} 条双分子通道正在等待轨迹索引建立，暂未给出 k。"
        ),
        "misaligned_simulation_box_volume": (
            "{count} 条双分子通道缺少与丰度时间点对齐的模拟盒体积，未给出 k。"
        ),
        "unsupported_reaction_order": (
            "{count} 条高阶通道超出当前一阶/二阶模型，未给出 k。"
        ),
        "zero_reactant_exposure": (
            "{count} 条通道在观察窗内反应物暴露量为零，未给出 k。"
        ),
    }
    for reason, count in reason_counts.items():
        template = reason_messages.get(
            reason,
            "{count} 条通道因表观速率输入不完整未给出 k。",
        )
        message += " " + template.format(count=count)
    return {
        "status": "estimated" if estimated else "unavailable",
        "estimated_rows": estimated,
        "unavailable_rows": unsupported,
        "reason_counts": dict(reason_counts),
        "message": message,
        "model": "stoichiometric_mass_action",
        "timestep_ps": float(timestep_ps),
        "timepoint_count": int(composition["timepoints"]),
    }


def collect_species_channels(
    artifacts: dict[str, str],
    smiles: str,
    *,
    top: int = 20,
    include_kinetics: bool = True,
) -> dict[str, Any]:
    """Split one target species' direct channels into two lanes.

    Callers that need an interactive first paint can defer the comparatively
    expensive abundance/exposure calculation and request it explicitly later.
    """
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

    production_rows = decorate(production, "produce")
    consumption_rows = decorate(consumption, "consume")
    if include_kinetics:
        kinetics = _enrich_channel_kinetics(
            artifacts,
            [*production_rows, *consumption_rows],
        )
    else:
        kinetics = _unavailable_kinetics(
            [*production_rows, *consumption_rows],
            reason="deferred",
            message=(
                "直接反应通道已加载；表观速率未阻塞本次查询。"
                "如需速率，请使用“保存并重新计算”。"
            ),
        )
        kinetics["status"] = "deferred"
    return {
        "ok": True,
        "smiles": smiles,
        "production_rows": production_rows,
        "consumption_rows": consumption_rows,
        "kinetics": kinetics,
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
        "kinetics": {
            key: selected.get(key)
            for key in (
                "kinetics_status",
                "kinetics_reason",
                "kinetics_reason_message",
                "event_count",
                "event_frequency_per_ps",
                "observation_time_ps",
                "k_app",
                "k_app_unit",
                "k_app_display",
                "k_app_ci95_low",
                "k_app_ci95_high",
                "kinetic_exposure",
                "kinetic_exposure_unit",
                "kinetic_model",
                "reverse_event_count",
                "reverse_event_frequency_per_ps",
                "reverse_kinetics_reason",
                "reverse_kinetics_reason_message",
                "reverse_k_app",
                "reverse_k_app_unit",
                "reverse_k_app_display",
                "reverse_k_app_ci95_low",
                "reverse_k_app_ci95_high",
            )
            if key in selected
        },
    }
