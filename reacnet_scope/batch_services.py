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
    artifacts_from_status,
    scan_dataset,
    validate_browse_path,
)


# ---------------------------------------------------------------------------
# Batch comparison
# ---------------------------------------------------------------------------


def scan_batch_conditions(root_dir: str) -> dict[str, Any]:
    """Scan a directory tree for simulation conditions."""
    from reacnet_scope.batch_compare import BatchComparator

    if not root_dir.strip():
        raise ServiceError("请提供数据根目录", reason="missing_dir")

    try:
        root_path = validate_browse_path(root_dir)
    except ServiceError as exc:
        raise ServiceError(exc.message, reason=exc.reason) from exc
    if not root_path.is_dir():
        raise ServiceError(f"目录不存在: {root_path}", reason="bad_dir")
    root = str(root_path)

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
            "reaction_file": str(c.artifacts.get("reaction") or ""),
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
        "warnings": list(comparator.scan_warnings),
        "meta": {
            "status": "ok",
            "message": f"扫描完成: {len(conditions)} 个条件, {len(groups)} 个条件组",
            "warnings": list(comparator.scan_warnings),
        },
    }

def _validate_batch_limits(
    min_detection_rate: float,
    top_n: int,
) -> tuple[float, int]:
    try:
        detection_rate = float(min_detection_rate)
    except (TypeError, ValueError) as exc:
        raise ServiceError("最小检出率必须是 0 到 1 之间的数值", reason="bad_detection_rate") from exc
    try:
        row_number = float(top_n)
    except (TypeError, ValueError) as exc:
        raise ServiceError("Top N 必须是整数", reason="bad_top_n") from exc
    if not row_number.is_integer():
        raise ServiceError("Top N 必须是整数", reason="bad_top_n")
    row_limit = int(row_number)
    if not 0.0 <= detection_rate <= 1.0:
        raise ServiceError("最小检出率必须在 0 到 1 之间", reason="bad_detection_rate")
    if not 1 <= row_limit <= 500:
        raise ServiceError("Top N 必须在 1 到 500 之间", reason="bad_top_n")
    return detection_rate, row_limit


def _resolve_batch_reaction_source(source: Mapping[str, Any]) -> dict[str, str]:
    """Resolve one client-supplied source to one validated reaction file."""
    folder_text = str(source.get("folder") or "").strip()
    if not folder_text:
        raise ServiceError("条件缺少数据目录", reason="missing_condition_folder")
    folder = validate_browse_path(folder_text)
    if not folder.is_dir():
        raise ServiceError(f"条件目录不存在: {folder}", reason="missing_condition_folder")

    base = str(source.get("base") or "").strip()
    reaction_file = str(source.get("reaction_file") or "").strip()
    if base:
        status = scan_dataset(str(folder), base=base)
        selected_base = str((status.get("dataset") or {}).get("selected_base") or "")
        if selected_base != base:
            raise ServiceError("所选数据集已不存在，请在数据管理中重新加载", reason="stale_dataset")
        reaction_file = str(artifacts_from_status(status).get("reaction") or "")

    if reaction_file:
        reaction_path = Path(reaction_file).expanduser().resolve()
        if not reaction_path.is_relative_to(folder.resolve()):
            raise ServiceError("反应文件不属于所选数据目录", reason="reaction_out_of_bounds")
        if reaction_path.suffix != ".reactionabcd" or not reaction_path.is_file():
            raise ServiceError(f"反应文件不可用: {reaction_path}", reason="missing_reaction_file")
    else:
        candidates = sorted(folder.glob("*.reactionabcd"))
        if not candidates:
            raise ServiceError(f"目录中没有 .reactionabcd: {folder}", reason="missing_reaction_file")
        if len(candidates) > 1:
            names = "、".join(path.name for path in candidates[:3])
            raise ServiceError(
                f"目录中存在多个 .reactionabcd（{names}），请从数据管理选择明确的数据集",
                reason="ambiguous_reaction_file",
            )
        reaction_path = candidates[0].resolve()

    return {
        "folder": str(folder.resolve()),
        "base": base,
        "reaction_file": str(reaction_path),
        "name": str(source.get("name") or source.get("label") or folder.name),
        "label": str(source.get("label") or source.get("name") or folder.name),
    }


def run_grouped_batch_comparison(
    group_requests: list[dict[str, Any]],
    *,
    min_detection_rate: float = 0.0,
    top_n: int = 50,
) -> dict[str, Any]:
    """Compare exact reactions and aggregate replicate statistics by group.

    The operation is all-or-nothing: every selected replicate must resolve and
    parse successfully, otherwise a concrete error is returned instead of a
    silently incomplete comparison.
    """
    from reacnet_scope.batch_compare import (
        BatchComparator,
        ConditionGroup,
        SimulationCondition,
        reaction_key_to_display,
    )

    detection_rate, row_limit = _validate_batch_limits(
        min_detection_rate,
        top_n,
    )
    if not isinstance(group_requests, list) or not group_requests:
        raise ServiceError("请至少选择一个条件组或已管理数据集", reason="no_conditions")

    comparator = BatchComparator()
    loaded_groups: list[ConditionGroup] = []
    seen_group_names: set[str] = set()
    seen_reaction_files: dict[str, str] = {}
    load_errors: list[str] = []

    for group_index, raw_group in enumerate(group_requests, start=1):
        if not isinstance(raw_group, Mapping):
            load_errors.append(f"第 {group_index} 个条件组格式无效")
            continue
        group_name = str(raw_group.get("group_name") or "").strip()
        if not group_name:
            load_errors.append(f"第 {group_index} 个条件组缺少名称")
            continue
        if group_name in seen_group_names:
            load_errors.append(f"条件组名称重复: {group_name}")
            continue
        seen_group_names.add(group_name)
        raw_conditions = raw_group.get("conditions") or []
        if not isinstance(raw_conditions, list) or not raw_conditions:
            load_errors.append(f"条件组 {group_name} 没有可用的重复实验")
            continue

        condition_group = ConditionGroup(
            group_name=group_name,
            temperature=raw_group.get("temperature"),
            o2_ratio=raw_group.get("o2_ratio"),
            pressure=raw_group.get("pressure"),
        )
        for condition_index, raw_source in enumerate(raw_conditions, start=1):
            source_mapping = raw_source if isinstance(raw_source, Mapping) else {}
            source_label = str(
                source_mapping.get("label")
                or source_mapping.get("name")
                or f"重复 {condition_index}"
            )
            try:
                if not isinstance(raw_source, Mapping):
                    raise ServiceError("数据源格式无效", reason="bad_condition_source")
                source = _resolve_batch_reaction_source(raw_source)
                previous_group = seen_reaction_files.get(source["reaction_file"])
                if previous_group:
                    raise ServiceError(
                        f"与条件组 {previous_group} 使用了同一反应文件",
                        reason="duplicate_reaction_file",
                    )
                reactions = parse_reactionabcd(source["reaction_file"], min_tp=1)
                if not reactions:
                    raise ServiceError("反应文件没有可比较记录", reason="empty_reaction_file")
                network = ReactionNetwork(reactions)
                replicate = int(raw_source.get("replicate") or condition_index)
                if replicate < 1:
                    raise ValueError("重复编号必须大于 0")
            except ServiceError as exc:
                load_errors.append(f"{group_name} / {source_label}: {exc.message}")
                continue
            except Exception as exc:
                load_errors.append(f"{group_name} / {source_label}: 解析失败（{exc}）")
                continue

            seen_reaction_files[source["reaction_file"]] = group_name
            internal_name = f"group_{group_index}:replicate_{condition_index}"
            comparator.add_condition(
                internal_name,
                network,
                group_name=group_name,
                source=source,
            )
            condition_group.conditions.append(
                SimulationCondition(
                    name=internal_name,
                    folder=source["folder"],
                    temperature=raw_source.get("temperature"),
                    o2_ratio=raw_source.get("o2_ratio"),
                    pressure=raw_source.get("pressure"),
                    replicate=replicate,
                    artifacts={
                        "reaction": source["reaction_file"],
                        "display_name": source["name"],
                    },
                )
            )
        if condition_group.conditions:
            loaded_groups.append(condition_group)

    if load_errors:
        preview = "；".join(load_errors[:8])
        if len(load_errors) > 8:
            preview += f"；另有 {len(load_errors) - 8} 项错误"
        raise ServiceError(f"批量对比未执行：{preview}", reason="condition_load_failed")
    if not loaded_groups:
        raise ServiceError("未能加载任何条件组", reason="no_networks")

    comparisons = comparator.compare_all_common(
        min_detection_rate=detection_rate,
        top_n=row_limit,
    )
    if not comparisons:
        raise ServiceError("未找到符合检出率条件的反应", reason="no_results")

    group_meta = [
        {
            "id": f"group_{index}",
            "name": group.group_name,
            "n_replicates": group.n_replicates,
            "temperature": group.temperature,
            "o2_ratio": group.o2_ratio,
            "pressure": group.pressure,
        }
        for index, group in enumerate(loaded_groups, start=1)
    ]
    columns: list[dict[str, Any]] = [
        {"field": "index", "headerName": "#", "type": "numericColumn"},
        {"field": "reaction_smiles", "headerName": "反应式 (SMILES)"},
        {"field": "reaction_formulas", "headerName": "反应式 (分子式)"},
        {"field": "detection_rate", "headerName": "总体检出率", "type": "numericColumn"},
        {"field": "total_tp", "headerName": "总 TP", "type": "numericColumn"},
        {"field": "total_net_tp", "headerName": "总净 TP", "type": "numericColumn"},
    ]
    for group in group_meta:
        prefix = group["id"]
        label = group["name"]
        columns.extend(
            [
                {"field": f"{prefix}_detection_rate", "headerName": f"{label} · 检出率", "type": "numericColumn"},
                {"field": f"{prefix}_mean_tp", "headerName": f"{label} · 平均 TP", "type": "numericColumn"},
                {"field": f"{prefix}_std_tp", "headerName": f"{label} · TP 标准差", "type": "numericColumn"},
                {"field": f"{prefix}_mean_net_tp", "headerName": f"{label} · 平均净 TP", "type": "numericColumn"},
            ]
        )

    rows: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    for index, comparison in enumerate(comparisons, start=1):
        reaction_id = f"reaction_{index}"
        group_statistics: list[dict[str, Any]] = []
        row: dict[str, Any] = {
            "id": reaction_id,
            "index": index,
            "reaction_smiles": reaction_key_to_display(comparison.reaction_smiles),
            "reaction_formulas": comparison.reaction_formulas,
            "detection_rate": round(comparison.detection_rate, 3),
            "total_tp": int(sum(comparison.tp_by_condition.values())),
            "total_net_tp": int(sum(comparison.net_tp_by_condition.values())),
        }
        for group_index, condition_group in enumerate(loaded_groups, start=1):
            stats = comparator.statistical_summary(comparison, condition_group)
            group_id = f"group_{group_index}"
            stats["id"] = group_id
            group_statistics.append(stats)
            row[f"{group_id}_detection_rate"] = stats["detection_rate"]
            row[f"{group_id}_mean_tp"] = stats["mean_tp"]
            row[f"{group_id}_std_tp"] = stats["std_tp"]
            row[f"{group_id}_mean_net_tp"] = stats["mean_net_tp"]
        rows.append(row)
        details[reaction_id] = {
            "id": reaction_id,
            "reaction_smiles": row["reaction_smiles"],
            "reaction_formulas": row["reaction_formulas"],
            "detection_rate": row["detection_rate"],
            "total_tp": row["total_tp"],
            "total_net_tp": row["total_net_tp"],
            "groups": group_statistics,
        }

    return {
        "ok": True,
        "rows": rows,
        "columns": columns,
        "groups": group_meta,
        "details": details,
        "meta": {
            "status": "ok",
            "message": (
                f"对比完成：{len(rows)} 个反应，{len(loaded_groups)} 个条件组，"
                f"{sum(group.n_replicates for group in loaded_groups)} 个重复实验"
            ),
            "n_reactions": len(rows),
            "n_groups": len(loaded_groups),
            "n_conditions": sum(group.n_replicates for group in loaded_groups),
        },
    }


def run_batch_comparison(
    condition_folders: list[str],
    condition_names: list[str],
    *,
    min_detection_rate: float = 0.0,
    top_n: int = 50,
) -> dict[str, Any]:
    """Run a replicate-level comparison for compatibility with older callers."""
    from reacnet_scope.batch_compare import BatchComparator

    if not condition_folders:
        raise ServiceError("请选择至少一个条件组", reason="no_conditions")
    if len(condition_folders) != len(condition_names):
        raise ServiceError("条件名称与目录数量不匹配", reason="mismatch")
    detection_rate, row_limit = _validate_batch_limits(min_detection_rate, top_n)
    if len(set(condition_names)) != len(condition_names):
        raise ServiceError("条件名称不能重复", reason="duplicate_condition_name")

    comparator = BatchComparator()
    seen_files: set[str] = set()
    errors: list[str] = []
    for folder, name in zip(condition_folders, condition_names):
        try:
            source = _resolve_batch_reaction_source(
                {"folder": folder, "name": name}
            )
            if source["reaction_file"] in seen_files:
                raise ServiceError("重复选择了同一反应文件", reason="duplicate_reaction_file")
            reactions = parse_reactionabcd(source["reaction_file"], min_tp=1)
            if not reactions:
                raise ServiceError("反应文件没有可比较记录", reason="empty_reaction_file")
            network = ReactionNetwork(reactions)
        except ServiceError as exc:
            errors.append(f"{name}: {exc.message}")
            continue
        except Exception as exc:
            errors.append(f"{name}: 解析失败（{exc}）")
            continue
        seen_files.add(source["reaction_file"])
        comparator.add_condition(name, network)

    if errors:
        raise ServiceError(
            "批量对比未执行：" + "；".join(errors),
            reason="condition_load_failed",
        )

    results = comparator.compare_all_common(
        min_detection_rate=detection_rate,
        top_n=row_limit,
    )
    if not results:
        raise ServiceError("未找到符合条件的共同反应", reason="no_results")

    rows, cond_names = comparator.build_comparison_matrix(results)
    base_columns = [
        {"field": "index", "headerName": "#", "width": 50},
        {"field": "reaction_smiles", "headerName": "反应式", "flex": 2, "minWidth": 200},
        {"field": "reaction_formulas", "headerName": "反应式 (分子式)", "flex": 2, "minWidth": 180},
        {"field": "detection_rate", "headerName": "检出率", "width": 80, "type": "numericColumn"},
    ]
    cond_columns = [
        {"field": f"tp_{cn}", "headerName": f"{cn} (TP)", "width": 100, "type": "numericColumn"}
        for cn in cond_names
    ]
    net_columns = [
        {"field": f"net_{cn}", "headerName": f"{cn} (净 TP)", "width": 100, "type": "numericColumn"}
        for cn in cond_names
    ]
    return {
        "ok": True,
        "rows": rows,
        "columns": base_columns + cond_columns + net_columns,
        "condition_names": cond_names,
        "meta": {
            "status": "ok",
            "message": f"对比完成: {len(rows)} 个反应, {len(cond_names)} 个条件",
            "n_reactions": len(rows),
            "n_conditions": len(cond_names),
        },
    }
