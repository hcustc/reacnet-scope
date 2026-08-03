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
from reacnet_scope.analysis_services import (
    _bond_atom_ids,
    _bond_values,
    _reaction_min_tp,
)
from reacnet_scope.workspace_services import _event_artifact_paths


# ---------------------------------------------------------------------------
# Time evolution
# ---------------------------------------------------------------------------


def build_species_evolution(
    artifacts: dict[str, str],
    targets: list[str],
    *,
    species_file: str = "",
    species_files: str = "",
    x_axis: str = "step",
    timestep_ps: float | None = None,
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
    selected_axis = x_axis if x_axis in {"step", "ps", "ns"} else "step"
    try:
        source_specs = parse_species_file_specs([multi_source_text]) if multi_source_text else []
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_species_sources") from exc
    source_paths = (
        [str(Path(spec["path"]).expanduser().resolve()) for spec in source_specs]
        if source_specs
        else [str(Path(species_path).expanduser().resolve())]
    )
    explicit_conversion: float | None = None
    if timestep_ps is not None:
        try:
            explicit_conversion = float(timestep_ps)
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                "timestep 到 ps 的换算必须是正数",
                reason="invalid_timestep",
            ) from exc
        if explicit_conversion <= 0:
            raise ServiceError(
                "timestep 到 ps 的换算必须是正数",
                reason="invalid_timestep",
            )
        if len(source_paths) > 1:
            raise ServiceError(
                "多数据集比较不能批量套用一个 timestep 换算；请分别确认并保存每个数据集的换算",
                reason="ambiguous_time_conversion",
            )
    conversions: dict[str, float] = {}
    try:
        for source_path in source_paths:
            conversion = explicit_conversion
            if conversion is not None:
                save_timestep_ps(source_path, conversion)
            elif selected_axis != "step":
                conversion = load_timestep_ps(source_path)
            if conversion is not None:
                conversions[source_path] = conversion
    except TrajectoryFrameError as exc:
        raise ServiceError(str(exc), reason="invalid_timestep") from exc
    if selected_axis != "step" and len(conversions) != len(source_paths):
        raise ServiceError(
            "显示物理时间前，必须为每个数据集分别确认并保存 timestep 到 ps 的换算",
            reason="unconfirmed_time_conversion",
        )
    params = {
        "target": ["\n".join(target_list)],
        "reac": [reac_path or ""],
        "min_tp": [str(_reaction_min_tp(artifacts))],
        "species_file": [species_path],
        "species_files": [multi_source_text],
        "x_axis": [selected_axis],
        "timestep_ps": [
            str(conversions[source_paths[0]])
            if not source_specs and selected_axis != "step"
            else ""
        ],
        "timestep_ps_by_source": [json.dumps(conversions, sort_keys=True)],
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
    except (IndexNotReadyError, IndexBuildInProgressError, IndexStaleError, IndexInvalidError) as exc:
        raise ServiceError(
            f"Species Abundance Index 未就绪: {exc}",
            reason="species_index_not_ready",
        ) from exc
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"构建时间演化数据失败: {exc}") from exc


def evolution_to_csv(payload: dict[str, Any]) -> str:
    """Serialize unprocessed indexed abundances with both source coordinates."""
    import csv
    import io

    x_values = payload.get("raw_x_values") or payload.get("x_values") or []
    curves = payload.get("curves") or []
    x_name = payload.get("x_name") or "x"
    if int((payload.get("meta") or {}).get("n_input_species_files", 1)) > 1:
        buf = io.StringIO()
        writer = csv.writer(buf)
        header = [
            "system",
            "replicate",
            "series_name",
            "analyzed_frame",
            "source_timestep",
        ]
        if x_name not in {"timestep", "source_timestep"}:
            header.append(x_name)
        header.append("raw_count")
        writer.writerow(header)
        for curve in curves:
            raw_values = curve.get("raw_values") or curve.get("values") or []
            curve_x = curve.get("raw_x_values") or x_values
            timesteps = curve.get("source_timesteps") or []
            frames = curve.get("analyzed_frames") or []
            for index, raw_value in enumerate(raw_values):
                source_timestep = (
                    timesteps[index] if index < len(timesteps) else None
                )
                if source_timestep is None:
                    continue
                row = [
                    curve.get("system") or "",
                    curve.get("replicate") or "",
                    curve.get("base_series_name")
                    or curve.get("name")
                    or curve.get("query")
                    or "",
                    frames[index] if index < len(frames) else "",
                    source_timestep,
                ]
                if x_name not in {"timestep", "source_timestep"}:
                    row.append(curve_x[index] if index < len(curve_x) else "")
                row.append(raw_value)
                writer.writerow(row)
        return buf.getvalue()
    coordinates = next(
        (
            curve
            for curve in curves
            if len(curve.get("source_timesteps") or []) == len(x_values)
        ),
        {},
    )
    source_timesteps = coordinates.get("source_timesteps") or [""] * len(x_values)
    analyzed_frames = coordinates.get("analyzed_frames") or list(range(len(x_values)))
    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["analyzed_frame", "source_timestep"]
    if x_name not in {"timestep", "source_timestep"}:
        header.append(x_name)
    header.extend(
        c.get("name") or c.get("query") or f"curve_{i}"
        for i, c in enumerate(curves)
    )
    writer.writerow(header)
    for i, x in enumerate(x_values):
        row = [analyzed_frames[i], source_timesteps[i]]
        if x_name not in {"timestep", "source_timestep"}:
            row.append(x)
        for c in curves:
            vals = c.get("raw_values") or c.get("values") or []
            row.append(vals[i] if i < len(vals) else "")
        writer.writerow(row)
    return buf.getvalue()


def intermediate_candidates_to_csv(payload: Mapping[str, Any] | None) -> str:
    """Export candidate rows with the exact rule and parameter audit trail."""
    document = dict(payload or {})
    query_json = json.dumps(
        document.get("query") or {},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    rows = [
        {
            "schema_version": document.get("schema_version") or "",
            "rule_version": document.get("rule_version") or "",
            "scoring_version": document.get("scoring_version") or "",
            "query_parameters_json": query_json,
            **dict(row),
        }
        for row in document.get("rows") or []
    ]
    return rows_to_csv(rows)


def build_elemental_composition_evolution(
    artifacts: dict[str, str],
    *,
    species_file: str = "",
    reference_smiles: str = "",
    x_axis: str = "step",
    timestep_ps: float | None = None,
    group_element: str = "C",
    max_group_count: int = 6,
    element_filters: dict[str, dict[str, Any]] | None = None,
    include_zero: bool = False,
    max_points: int = 600,
) -> dict[str, Any]:
    """Build one generic Element Distribution Evolution query."""
    started = time.perf_counter()
    selected_axis = x_axis if x_axis in {"step", "ps", "ns"} else "step"
    conversion: float | None = None
    if selected_axis in {"ps", "ns"}:
        try:
            conversion = float(timestep_ps)
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                "显示物理时间前必须确认并保存 timestep 到 ps 的换算",
                reason="unconfirmed_time_conversion",
            ) from exc
        if conversion <= 0:
            raise ServiceError(
                "Timestep 换算必须是正数（ps）",
                reason="invalid_timestep",
            )
    species_path = (species_file or artifacts.get("species") or "").strip()
    if not species_path or not Path(species_path).is_file():
        raise ServiceError("缺少 .species 数据文件", reason="missing_species_file")
    selected_element = str(group_element or "").strip()
    if not re.fullmatch(r"[A-Z][a-z]?", selected_element):
        raise ServiceError("请选择有效的分组元素", reason="invalid_group_element")
    selected_max = max(0, int(max_group_count))
    filters = dict(element_filters or {})
    try:
        model = build_element_distribution_model(
            species_files={"current": species_path},
            max_points=max_points,
            group_element=selected_element,
            max_group_count=selected_max,
            element_filters=filters,
            include_zero=bool(include_zero),
        )
        source_meta = (model.get("sources") or [{}])[0].get("meta") or {}
        indexed = {
            "rows": model.get("raw_rows") or [],
            "timesteps": sorted(
                {int(row["timestep"]) for row in model.get("raw_rows") or []}
            ),
            "meta": source_meta,
        }
    except (
        IndexNotReadyError,
        IndexStaleError,
        IndexBuildInProgressError,
        IndexInvalidError,
        RuntimeError,
    ) as exc:
        raise ServiceError(str(exc), reason="composition_index_not_ready") from exc

    def convert_time(timestep: int) -> int | float:
        if selected_axis == "step":
            return int(timestep)
        value = float(timestep) * float(conversion)
        return value / 1000.0 if selected_axis == "ns" else value

    rows = list(indexed.get("rows") or [])
    if not rows:
        raise ServiceError("元素筛选范围内没有物种分布数据", reason="empty_composition")
    sampled_timesteps = [int(value) for value in indexed.get("timesteps") or []]
    first_timestep = sampled_timesteps[0]
    last_timestep = sampled_timesteps[-1]
    reference_smiles = str(reference_smiles or "").strip()
    reference_atoms = count_atoms_fast(reference_smiles) if reference_smiles else {}
    reference_record = {
        "smiles": reference_smiles,
        "formula": formula_from_counts(reference_atoms) if reference_smiles else "",
        "elements": {
            str(element): int(value)
            for element, value in reference_atoms.items()
            if int(value) > 0
        },
    }
    reference_counts = (
        SPECIES_COMPOSITION_STORE.species_count_series(
            species_path,
            sampled_timesteps,
            reference_smiles,
        )
        if reference_smiles
        else {}
    )

    reference_group_count = int(
        (reference_record["elements"] or {}).get(selected_element, 0)
    )
    distribution_totals: Counter[tuple[int, int]] = Counter()
    for row in rows:
        distribution_totals[
            (int(row["timestep"]), int(row["group_count"]))
        ] += int(row["count"])
    reference_allowed = bool(reference_smiles) and matches_element_filters(
        reference_record["elements"],
        filters,
    )
    observed_groups = sorted({int(row["group_count"]) for row in rows})
    distribution_rows: list[dict[str, Any]] = []
    for timestep in sampled_timesteps:
        reference_count = int(reference_counts.get(timestep, 0)) if reference_allowed else 0
        for group_count in observed_groups:
            distribution_rows.append(
                {
                    "timestep": timestep,
                    "x": convert_time(timestep),
                    "series": f"{selected_element}{group_count}",
                    "count": int(
                        distribution_totals[(timestep, group_count)]
                    ),
                }
            )
        if reference_smiles:
            distribution_rows.append(
                {
                    "timestep": timestep,
                    "x": convert_time(timestep),
                    "series": "参考物种",
                    "count": reference_count,
                }
            )
        if reference_smiles and reference_group_count <= selected_max:
            distribution_rows.append(
                {
                    "timestep": timestep,
                    "x": convert_time(timestep),
                    "series": f"{selected_element}{reference_group_count} 其他物种",
                    "count": max(
                        0,
                        int(
                            distribution_totals[
                                (timestep, reference_group_count)
                            ]
                        )
                        - reference_count,
                    ),
                }
            )
    meta = dict(indexed.get("meta") or {})
    meta["analysis_seconds"] = round(time.perf_counter() - started, 4)
    return {
        "ok": True,
        "view": "element-distribution",
        "x_name": {"step": "Timestep", "ps": "Time (ps)", "ns": "Time (ns)"}[selected_axis],
        "distribution_rows": distribution_rows,
        "csv_rows": list(distribution_rows),
        "filters": filters,
        "summary": {
            "reference_group": (
                f"{selected_element}{reference_group_count}"
                if reference_smiles
                else ""
            ),
            "reference_smiles": reference_smiles,
            "reference_formula": str(reference_record["formula"]),
            "reference_group_count": reference_group_count,
            "group_element": selected_element,
            "max_group_count": selected_max,
            "include_zero": bool(include_zero),
            "first_timestep": first_timestep,
            "last_timestep": last_timestep,
            "x_axis": selected_axis,
            "timestep_ps": conversion,
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


def build_element_distribution_species_drilldown(
    payload: dict[str, Any],
    *,
    series: str,
    timestep: int,
    limit: int = 100,
) -> dict[str, Any]:
    """Resolve one selected element-count curve to exact Species summaries."""
    meta = payload.get("meta") or {}
    summary = payload.get("summary") or {}
    filters = payload.get("filters") or {}
    species_path = str(meta.get("species_file") or "")
    if not species_path or not Path(species_path).is_file():
        raise ServiceError("组成索引缺少 .species 来源", reason="missing_species_file")
    reference_smiles = str(summary.get("reference_smiles") or "")
    group_element = str(summary.get("group_element") or "C")
    reference_group_count = int(summary.get("reference_group_count") or 0)
    only_smiles = ""
    exclude_smiles = ""
    if series == "参考物种" and reference_smiles:
        group_count = reference_group_count
        only_smiles = reference_smiles
    elif (
        series == f"{group_element}{reference_group_count} 其他物种"
        and reference_smiles
    ):
        group_count = reference_group_count
        exclude_smiles = reference_smiles
    else:
        matched = re.fullmatch(
            rf"{re.escape(group_element)}(\d+)",
            str(series or ""),
        )
        if not matched:
            raise ServiceError(
                "无法识别所选元素分组",
                reason="invalid_element_distribution_series",
            )
        group_count = int(matched.group(1))
    try:
        result = SPECIES_COMPOSITION_STORE.query_species_summary(
            species_path,
            group_element=group_element,
            group_count=group_count,
            current_timestep=int(timestep),
            element_filters={
                str(element): dict(rule or {})
                for element, rule in filters.items()
            },
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
    selected_axis = str(summary.get("x_axis") or "step")
    timestep_ps = summary.get("timestep_ps")

    def convert(value: int) -> int | float:
        if selected_axis == "step" or timestep_ps is None:
            return int(value)
        physical = float(value) * float(timestep_ps)
        return physical / 1000.0 if selected_axis == "ns" else physical

    rows = [
        {
            **row,
            "peak_time": convert(int(row["peak_timestep"])),
        }
        for row in result.get("rows") or []
    ]
    return {
        "series": series,
        "group_element": group_element,
        "group_count": group_count,
        "timestep": int(timestep),
        "current_time": convert(int(timestep)),
        "x_axis": selected_axis,
        "x_unit": {"step": "timestep", "ps": "ps", "ns": "ns"}.get(
            selected_axis, "timestep"
        ),
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
    product_ratio_min: float = 0.95,
    reactant_start_ratio_min: float = 0.9,
    fwhm_min_frames: float = 1.0,
    timestep_ps: float | None = None,
    require_fwhm: bool = True,
    with_flux: bool = True,
    flux_top: int = 10,
) -> dict[str, Any]:
    """Build the intermediate-candidate query model for Dash."""
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (artifacts.get("species") or "").strip()
    if not species_path and reac_path:
        species_path = derive_species_path(reac_path)
    if not species_path or not os.path.exists(species_path):
        raise ServiceError("缺少 .species 数据文件", reason="missing_species_file")
    requested_with_flux = bool(with_flux)
    flux_available = bool(reac_path and os.path.exists(reac_path))
    effective_with_flux = requested_with_flux and flux_available
    conversion: float | None
    try:
        if timestep_ps is None:
            conversion = load_timestep_ps(species_path)
        else:
            conversion = float(timestep_ps)
            save_timestep_ps(species_path, conversion)
    except (TrajectoryFrameError, TypeError, ValueError) as exc:
        raise ServiceError(str(exc), reason="invalid_timestep") from exc
    params = {
        "reac": [reac_path or ""],
        "min_tp": [str(_reaction_min_tp(artifacts))],
        "species_file": [species_path],
        "kind": [kind if kind in {"intermediate", "product", "reactant", "all"} else "intermediate"],
        "top": [str(max(1, int(top)))],
        "abundance_threshold": [str(float(abundance_threshold))],
        "start_ratio_max": [str(float(start_ratio_max))],
        "decay_alpha": [str(float(decay_alpha))],
        "product_ratio_min": [str(float(product_ratio_min))],
        "reactant_start_ratio_min": [str(float(reactant_start_ratio_min))],
        "fwhm_min_frames": [str(float(fwhm_min_frames))],
        "timestep_ps": [str(conversion) if conversion is not None else ""],
        "require_fwhm": ["1" if require_fwhm else "0"],
        "with_flux": ["1" if effective_with_flux else "0"],
        "flux_top": [str(max(0, int(flux_top)))],
    }
    try:
        result = build_intermediate_candidates_payload(params)
        result.setdefault("query", {})["with_flux_requested"] = requested_with_flux
        result.setdefault("meta", {})["flux_enrichment"] = {
            "requested": requested_with_flux,
            "available": flux_available,
            "applied": effective_with_flux,
            "reason": (
                ""
                if flux_available or not requested_with_flux
                else "reaction_network_missing"
            ),
        }
        return result
    except (IndexNotReadyError, IndexBuildInProgressError, IndexStaleError, IndexInvalidError) as exc:
        raise ServiceError(
            f"Species Abundance Index 未就绪: {exc}",
            reason="species_index_not_ready",
        ) from exc
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
    reactionevent_file, molecules_file = _event_artifact_paths(artifacts)
    if not reactionevent_file or not Path(reactionevent_file).is_file():
        raise ServiceError(
            "缺少 .timeline.h5 或 .reactionevent.csv 事件源",
            reason="missing_reactionevent",
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
            f"{exc}; 运行 reacnet-scope prepare rebuild event "
            f"{shlex.quote(reactionevent_file)}",
            reason="event_index_stale",
        ) from exc
    except IndexInvalidError as exc:
        raise ServiceError(
            f"{exc}; 运行 reacnet-scope prepare rebuild event "
            f"{shlex.quote(reactionevent_file)}",
            reason="event_index_invalid",
        ) from exc
    except IndexNotReadyError as exc:
        raise ServiceError(
            f"{exc}; 运行 reacnet-scope prepare build event "
            f"{shlex.quote(reactionevent_file)}",
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
        "source_kind": (
            "native_hdf5"
            if reactionevent_file.endswith(".timeline.h5")
            else "legacy_csv"
        ),
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
    """Validate one shortlisted pathway step against native event evidence."""

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
    event_file, _event_molecules = _event_artifact_paths(artifacts)
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

    reasons: list[str] = []
    if event_absent:
        reasons.append("数据集没有 .timeline.h5 或 .reactionevent.csv")
    else:
        reasons.append("RNG 事件索引中没有精确匹配事件")
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


def parse_event_type_element_map(text: str) -> dict[str, str]:
    """Parse the compact mapping accepted by the event-viewer controls."""
    try:
        return normalize_type_element_map(parse_type_element_map_specs(text))
    except (TrajectoryDependencyError, TrajectoryFrameError, ValueError) as exc:
        raise ServiceError(str(exc), reason="invalid_type_element_map") from exc


def build_rng_event_visualization(
    artifacts: dict[str, str],
    event_row: dict[str, Any],
    *,
    before_frames: int = 3,
    after_frames: int = 3,
    environment_radius: float = 4.0,
    max_environment_atoms: int = 500,
    atom_type_map: Mapping[Any, Any] | None = None,
    persist_type_map: bool = True,
) -> dict[str, Any]:
    """Build an ASE/PBC-aware local view from indexed trajectory frames."""
    trajectory_file = (artifacts.get("trajectory") or "").strip()
    if not trajectory_file or not Path(trajectory_file).is_file():
        raise ServiceError("缺少原始轨迹文件", reason="missing_trajectory")
    atom_ids = sorted(
        {int(value) for value in (event_row.get("atom_id_list") or [])}
    )
    if not atom_ids:
        raise ServiceError(
            "该复杂事件无法由 Molecular Evidence 唯一关联原子",
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
        choices = [
            idx for idx in (pos - 1, pos) if 0 <= idx < len(available)
        ]
        return min(choices, key=lambda idx: abs(available[idx] - value))

    left = nearest_index(before_timestep)
    right = nearest_index(after_timestep)
    if left > right:
        left, right = right, left
    start = max(0, left - max(0, int(before_frames)))
    stop = min(len(available), right + max(0, int(after_frames)) + 1)
    selected_frames = available[start:stop]
    offsets = index.offsets_for(selected_frames)
    if not offsets:
        raise ServiceError(
            "轨迹索引没有返回所需帧的字节范围",
            reason="missing_frame_offsets",
        )

    reactant_bonds = _bond_values(event_row.get("reactant_bonds"))
    product_bonds = _bond_values(event_row.get("product_bonds"))
    broken_bonds = sorted(set(reactant_bonds).difference(product_bonds))
    formed_bonds = sorted(set(product_bonds).difference(reactant_bonds))
    core_atom_ids = sorted(
        _bond_atom_ids([*broken_bonds, *formed_bonds]) or atom_ids
    )

    try:
        if atom_type_map is not None:
            resolved_type_map = normalize_type_element_map(atom_type_map)
            if persist_type_map:
                type_map_path = save_type_element_map(
                    trajectory_file,
                    resolved_type_map,
                )
            else:
                type_map_path = dataset_settings_path(trajectory_file)
                if not type_map_path.is_file():
                    type_map_path = None
        else:
            resolved_type_map = load_type_element_map(trajectory_file)
            type_map_path = dataset_settings_path(trajectory_file)
            if not type_map_path.is_file():
                type_map_path = None

        parsed_frames: dict[int, dict[str, Any]] = {}
        with open(trajectory_file, "rb") as source:
            for frame in selected_frames:
                byte_range = offsets.get(frame)
                if byte_range is None:
                    continue
                source.seek(int(byte_range[0]))
                block = source.read(
                    int(byte_range[1]) - int(byte_range[0])
                )
                parsed_frames[int(frame)] = read_lammps_frame_block(
                    block,
                    type_element_map=resolved_type_map,
                )

        requested_anchor = event_row.get("anchor_frame")
        anchor_value = (
            int(requested_anchor)
            if requested_anchor is not None
            else available[right]
        )
        anchor_frame = min(
            parsed_frames,
            key=lambda frame: abs(int(frame) - anchor_value),
        )
        environment = select_local_environment(
            parsed_frames[anchor_frame],
            atom_ids,
            radius=float(environment_radius),
            max_environment_atoms=int(max_environment_atoms),
        )
        environment["max_environment_atoms"] = int(max_environment_atoms)
    except TrajectoryDependencyError as exc:
        raise ServiceError(str(exc), reason="missing_ase") from exc
    except (TrajectoryFrameError, OSError, UnicodeError, ValueError) as exc:
        raise ServiceError(
            f"局部轨迹解析失败: {exc}",
            reason="trajectory_frame_error",
        ) from exc

    environment_ids = list(environment["environment_ids"])
    context_ids = sorted({*atom_ids, *environment_ids})
    core_set = set(core_atom_ids)
    participant_set = set(atom_ids)
    environment_set = set(environment_ids)
    frames: list[dict[str, Any]] = []
    try:
        for frame in selected_frames:
            parsed = parsed_frames.get(int(frame))
            if not parsed:
                continue
            display_positions = recentered_positions(
                parsed,
                context_ids,
                core_atom_ids,
            )
            atoms = []
            for atom_id in context_ids:
                atom = (parsed.get("atoms") or {}).get(atom_id)
                if atom is None or atom_id not in display_positions:
                    continue
                display_x, display_y, display_z = display_positions[atom_id]
                if atom_id in core_set:
                    group = "core"
                elif atom_id in participant_set:
                    group = "participant"
                elif atom_id in environment_set:
                    group = "environment"
                else:  # pragma: no cover - context is built from these groups
                    group = "context"
                atoms.append(
                    {
                        "id": int(atom_id),
                        "x": round(float(atom.get("x", 0.0)), 7),
                        "y": round(float(atom.get("y", 0.0)), 7),
                        "z": round(float(atom.get("z", 0.0)), 7),
                        "display_x": round(display_x, 7),
                        "display_y": round(display_y, 7),
                        "display_z": round(display_z, 7),
                        "element": str(atom.get("element") or ""),
                        "label": str(atom.get("label") or ""),
                        "type": str(atom.get("type") or ""),
                        "group": group,
                    }
                )
            if not atoms:
                continue
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
                    "box_header": parsed.get("box_header") or "",
                    "box_lines": parsed.get("box_lines") or [],
                    "cell": parsed.get("cell") or [],
                    "cell_origin": parsed.get("cell_origin") or [],
                    "pbc": parsed.get("pbc") or [],
                    "atoms": atoms,
                    "bonds": frame_bonds,
                    "bond_state": bond_state,
                }
            )
    except (TrajectoryDependencyError, TrajectoryFrameError) as exc:
        raise ServiceError(
            f"局部轨迹重定位失败: {exc}",
            reason="trajectory_geometry_error",
        ) from exc
    if not frames:
        raise ServiceError(
            "选中事件的参与原子未出现在轨迹窗口中",
            reason="no_coordinates",
        )

    storyboard = list(
        dict.fromkeys(
            [
                frames[0]["frame"],
                available[left],
                available[right],
                frames[-1]["frame"],
            ]
        )
    )
    labels = {
        str(available[left]): "反应前",
        str(available[right]): "反应后",
    }
    source_signatures: dict[str, dict[str, Any]] = {}

    def add_signature(label: str, path_text: str) -> None:
        path = Path(str(path_text or ""))
        if not path.is_file():
            return
        stat = path.stat()
        source_signatures[label] = {
            "path": str(path.resolve()),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    add_signature("trajectory", trajectory_file)
    add_signature("trajectory_index", str(index.index_path))
    for kind in ("timeline", "reactionevent", "molecules"):
        add_signature(kind, str(artifacts.get(kind) or ""))
    event_index = resolve_dataset_paths(trajectory_file).event_index
    add_signature("event_index", str(event_index))

    event_details = {
        key: event_row.get(key)
        for key in (
            "event_id",
            "source_row",
            "timestep_index",
            "before_timestep",
            "after_timestep",
            "anchor_frame",
            "reactant",
            "product",
            "reaction_smiles",
            "reactant_participants",
            "product_participants",
            "association_status",
        )
        if event_row.get(key) is not None
    }
    return {
        "event_id": str(event_row.get("event_id") or ""),
        "event": event_details,
        "frames": frames,
        "atom_groups": {
            "core": core_atom_ids,
            "participants": atom_ids,
            "reactant": atom_ids,
            "product": atom_ids,
            "environment": environment_ids,
            "context": context_ids,
        },
        "bond_evidence": {
            "reactant": reactant_bonds,
            "product": product_bonds,
            "broken": broken_bonds,
            "formed": formed_bonds,
        },
        "storyboard_frames": storyboard,
        "storyboard_labels": {
            str(frame): labels.get(str(frame), f"Frame {frame}")
            for frame in storyboard
        },
        "meta": {
            "status": "rng_event",
            "verification_status": str(
                event_row.get("association_status") or "matched"
            ),
            "reaction_smiles": str(event_row.get("reaction_smiles") or ""),
            "coordinate_treatment": "ASE minimum-image, reaction-core centered",
            "anchor_frame": anchor_frame,
            "extraction": {
                "before_frames": int(before_frames),
                "after_frames": int(after_frames),
                "environment_radius": float(environment_radius),
                "max_environment_atoms": int(max_environment_atoms),
            },
            "environment": environment,
            "type_element_map": resolved_type_map,
            "native_element_column": "element"
            in {
                str(value).strip().lower()
                for value in (
                    parsed_frames[anchor_frame].get("atom_columns") or []
                )
            },
        },
        "paths": {
            "trajectory": trajectory_file,
            "vmd": "",
            "type_map": str(type_map_path) if type_map_path else "",
        },
        "source_signatures": source_signatures,
    }


def event_viewer_frames_csv(viewer: Mapping[str, Any] | None) -> str:
    """Serialize the currently extracted event window for audit/download."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "frame",
            "atom_id",
            "type",
            "element",
            "x",
            "y",
            "z",
            "group",
            "bond_state",
            "label",
            "display_x",
            "display_y",
            "display_z",
        ]
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
                    atom.get("label"),
                    atom.get("display_x"),
                    atom.get("display_y"),
                    atom.get("display_z"),
                ]
            )
    return output.getvalue()


def event_viewer_trajectory_text(viewer: Mapping[str, Any] | None) -> str:
    """Serialize original event coordinates as an OVITO-ready LAMMPS dump."""
    if not viewer:
        return ""
    return event_trajectory_text(viewer, scope="environment")


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


def ovito_launch_capability(*, configured_path: str = "") -> dict[str, Any]:
    """Detect an explicitly configured or common local OVITO installation."""
    mode = str(
        os.environ.get("REACNET_SCOPE_DEPLOYMENT_MODE", "local")
    ).strip().lower()
    if mode not in {"local", "desktop"}:
        return {
            "mode": "remote",
            "available": False,
            "reason": "remote_launch_disabled",
        }
    configured = str(
        configured_path
        or os.environ.get("REACNET_SCOPE_OVITO_EXECUTABLE", "")
    ).strip()
    candidates: list[tuple[str, str]] = []
    if configured:
        candidates.append((configured, "configured"))
    if sys.platform == "darwin":
        candidates.extend(
            (
                (
                    "/Applications/OVITO.app/Contents/MacOS/ovito",
                    "detected",
                ),
                (
                    str(
                        Path.home()
                        / "Applications/OVITO.app/Contents/MacOS/ovito"
                    ),
                    "detected",
                ),
            )
        )
    elif os.name == "nt":
        for root_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(root_name, "").strip()
            if root:
                candidates.extend(
                    (
                        (str(Path(root) / "OVITO Basic/ovito.exe"), "detected"),
                        (str(Path(root) / "OVITO Pro/ovito.exe"), "detected"),
                    )
                )
    for command in ("ovito", "ovito.exe"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append((resolved, "path"))
    for candidate, source in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return {
                "mode": "local",
                "available": True,
                "path": str(path.resolve()),
                "source": source,
                "platform": sys.platform,
            }
    return {
        "mode": "local",
        "available": False,
        "reason": "ovito_not_found",
        "configured_path": configured,
        "platform": sys.platform,
    }


def launch_event_in_ovito(
    viewer: Mapping[str, Any] | None,
    *,
    configured_path: str = "",
) -> dict[str, Any]:
    """Export the current event subset and launch it after a user click."""
    if not viewer:
        raise ServiceError("没有可打开的事件轨迹。", reason="missing_event")
    capability = ovito_launch_capability(configured_path=configured_path)
    if capability.get("mode") != "local":
        raise ServiceError(
            "远程部署只提供下载，不能启动服务器桌面上的 OVITO。",
            reason="remote_launch_disabled",
        )
    if not capability.get("available"):
        raise ServiceError(
            "未检测到 OVITO；可设置 REACNET_SCOPE_OVITO_EXECUTABLE 后重试。",
            reason="ovito_not_found",
        )
    event_id = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        str(viewer.get("event_id") or "event"),
    ).strip(".-") or "event"
    export_dir = Path(tempfile.mkdtemp(prefix="reacnet-scope-ovito-"))
    trajectory_path = export_dir / f"{event_id}_subset.lammpstrj"
    trajectory_path.write_text(
        event_viewer_trajectory_text(viewer), encoding="utf-8"
    )
    try:
        process = subprocess.Popen(
            [str(capability["path"]), str(trajectory_path)],
            close_fds=os.name != "nt",
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise ServiceError(
            f"无法启动 OVITO: {exc}", reason="ovito_launch_failed"
        ) from exc
    return {
        "ok": True,
        "pid": int(process.pid),
        "executable": str(capability["path"]),
        "trajectory": str(trajectory_path),
        "detection_source": str(capability.get("source") or ""),
    }


def event_viewer_ovito_script(
    viewer: Mapping[str, Any] | None,
    *,
    trajectory_name: str = "event_subset.lammpstrj",
) -> str:
    """Build a portable OVITO Python helper for the downloaded subset."""
    expression = event_viewer_ovito_expression(viewer)
    event_id = str((viewer or {}).get("event_id") or "event")
    return (
        "# Generated by ReacNet Scope. Run with OVITO's ovitos interpreter:\n"
        f"# ovitos {event_id}_view_ovito.py {trajectory_name}\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "from ovito.io import import_file\n"
        "from ovito.modifiers import ExpressionSelectionModifier\n\n"
        f"default_trajectory = {trajectory_name!r}\n"
        "trajectory = (\n"
        "    Path(sys.argv[1]).expanduser().resolve()\n"
        "    if len(sys.argv) > 1\n"
        "    else Path(__file__).with_name(default_trajectory)\n"
        ")\n"
        "pipeline = import_file(str(trajectory), sort_particles=True)\n"
        f"selection_expression = {expression!r}\n"
        "if selection_expression:\n"
        "    pipeline.modifiers.append(\n"
        "        ExpressionSelectionModifier(expression=selection_expression)\n"
        "    )\n"
        "pipeline.add_to_scene()\n"
        "data = pipeline.compute(0)\n"
        "print(f'Loaded {pipeline.num_frames} frame(s) from {trajectory}')\n"
        "print(f'Selected event atoms: {data.attributes.get(\"ExpressionSelection.count\", 0)}')\n"
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


def batch_comparison_to_csv(payload: Mapping[str, Any] | None) -> str:
    """Export displayed batch columns with user-facing headers for Excel."""
    safe_payload = payload if isinstance(payload, Mapping) else {}
    rows = safe_payload.get("rows") or []
    columns = safe_payload.get("columns") or []
    if not isinstance(rows, list) or not rows:
        raise ServiceError("没有可导出的批量对比结果", reason="no_export_rows")
    field_headers = [
        (
            str(column.get("field") or ""),
            str(column.get("headerName") or column.get("field") or ""),
        )
        for column in columns
        if isinstance(column, Mapping) and str(column.get("field") or "")
    ]
    if not field_headers:
        raise ServiceError("批量对比结果缺少列定义", reason="no_export_columns")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for _, header in field_headers])
    for row in rows:
        safe_row = row if isinstance(row, Mapping) else {}
        writer.writerow([safe_row.get(field, "") for field, _ in field_headers])
    # UTF-8 BOM keeps Chinese headers readable in common spreadsheet tools.
    return "\ufeff" + buffer.getvalue()
