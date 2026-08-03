"""Shared query models for ReacNetGenerator analysis workflows."""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
from bisect import bisect_left, bisect_right
from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from reacnet_scope.network import (  # noqa: E402
    Reaction,
    ReactionNetwork,
    export_initiation_csv,
    export_initiation_smiles_branches_csv,
    parse_reactionabcd,
    smiles_to_formula_fast,
)
from reacnet_scope.dir_browser import validate_browse_path  # noqa: E402
from reacnet_scope.datasets import (  # noqa: E402
    ARTIFACT_SUFFIXES,
    choose_dataset_candidate,
    discover_dataset_candidates,
)
from reacnet_scope.formula import (  # noqa: E402
    formula_exact_mass,
    formula_isotopic_masses,
    formula_nominal_mass,
)
from reacnet_scope.reaction import canonical_smiles  # noqa: E402
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE  # noqa: E402
from reacnet_scope.indexes import (  # noqa: E402
    TRAJECTORY_INDEX_STORE as PREPARED_TRAJECTORY_INDEX_STORE,
    TrajectoryFrameIndex as PreparedTrajectoryFrameIndex,
    TrajectoryIndexStore as PreparedTrajectoryIndexStore,
    resolve_dataset_paths,
    trajectory_index_path as prepared_trajectory_index_path,
)


try:
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D
except Exception:  # pragma: no cover
    Chem = None
    rdDepictor = None
    rdMolDraw2D = None


def detect_default_reaction_file() -> Path:
    env_path = os.getenv("RNG_REACTION_FILE", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            PROJECT_ROOT / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
            Path.cwd() / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
        ]
    )
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


DEFAULT_REACTION_FILE = detect_default_reaction_file()

FORMULA_RE = re.compile(r"^([A-Z][a-z]?\d*)+$")


def split_terms(expr: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    for character in str(expr or "").strip():
        if character == "[":
            bracket_depth += 1
        elif character == "]" and bracket_depth:
            bracket_depth -= 1
        if character in "+,;" and bracket_depth == 0:
            term = "".join(current).strip()
            if term:
                parts.append(term)
            current = []
            continue
        current.append(character)
    term = "".join(current).strip()
    if term:
        parts.append(term)
    return parts


def _round_or_none(v: float | None, ndigits: int = 6) -> float | None:
    if v is None:
        return None
    return round(v, ndigits)


@lru_cache(maxsize=50000)
def exact_mass_cached(formula: str) -> float | None:
    return formula_exact_mass(formula)


@lru_cache(maxsize=50000)
def nominal_mass_cached(formula: str) -> int | None:
    return formula_nominal_mass(formula)


@lru_cache(maxsize=50000)
def isotopic_masses_cached(formula: str) -> tuple[tuple[float, int], ...] | None:
    return formula_isotopic_masses(formula)


def closest_isotopic_mass(
    formula: str,
    target: float,
    mode: str,
) -> tuple[float, int] | None:
    """Return the chlorine isotopologue closest to a mass-search target."""
    masses = isotopic_masses_cached(formula)
    if not masses:
        return None
    comparison_index = 0 if mode == "exact" else 1
    return min(
        masses,
        key=lambda item: (
            abs(float(item[comparison_index]) - target),
            float(item[comparison_index]),
        ),
    )


@lru_cache(maxsize=200000)
def smiles_formula_cached(smiles: str) -> str:
    try:
        return str(smiles_to_formula_fast(smiles) or "")
    except Exception:
        return ""


def formula_mass_fields(formula: str) -> dict[str, Any]:
    return {
        "exact_mass": _round_or_none(exact_mass_cached(formula)),
        "nominal_mass": nominal_mass_cached(formula),
    }


def reaction_mass_fields(rxn: Reaction) -> dict[str, Any]:
    react_exact = 0.0
    prod_exact = 0.0
    react_nom = 0
    prod_nom = 0

    for f in rxn.reactant_formulas:
        m = exact_mass_cached(f)
        n = nominal_mass_cached(f)
        if m is None:
            react_exact = math.nan
        elif not math.isnan(react_exact):
            react_exact += m
        if n is None:
            react_nom = -10**9
        elif react_nom > -10**9:
            react_nom += n

    for f in rxn.product_formulas:
        m = exact_mass_cached(f)
        n = nominal_mass_cached(f)
        if m is None:
            prod_exact = math.nan
        elif not math.isnan(prod_exact):
            prod_exact += m
        if n is None:
            prod_nom = -10**9
        elif prod_nom > -10**9:
            prod_nom += n

    react_exact_out: float | None = None if math.isnan(react_exact) else round(react_exact, 6)
    prod_exact_out: float | None = None if math.isnan(prod_exact) else round(prod_exact, 6)
    react_nom_out: int | None = None if react_nom <= -10**9 else react_nom
    prod_nom_out: int | None = None if prod_nom <= -10**9 else prod_nom

    delta_exact: float | None = None
    if react_exact_out is not None and prod_exact_out is not None:
        delta_exact = round(prod_exact_out - react_exact_out, 6)
    delta_nom: int | None = None
    if react_nom_out is not None and prod_nom_out is not None:
        delta_nom = prod_nom_out - react_nom_out

    return {
        "reactant_exact_mass": react_exact_out,
        "product_exact_mass": prod_exact_out,
        "delta_exact_mass": delta_exact,
        "reactant_nominal_mass": react_nom_out,
        "product_nominal_mass": prod_nom_out,
        "delta_nominal_mass": delta_nom,
    }


def bool_param(params: dict[str, list[str]], key: str, default: bool = False) -> bool:
    vals = params.get(key)
    if not vals:
        return default
    v = vals[0].strip().lower()
    return v in {"1", "true", "yes", "on"}


def int_param(params: dict[str, list[str]], key: str, default: int) -> int:
    vals = params.get(key)
    if not vals:
        return default
    try:
        return int(vals[0])
    except ValueError:
        return default


def float_param(params: dict[str, list[str]], key: str, default: float) -> float:
    vals = params.get(key)
    if not vals:
        return default
    try:
        return float(vals[0])
    except ValueError:
        return default


def looks_like_formula(text: str) -> bool:
    return bool(FORMULA_RE.fullmatch(text.strip()))


def split_target_items(raw_items: list[str]) -> list[str]:
    out: list[str] = []
    for raw in raw_items:
        parts = [x.strip() for x in re.split(r"\s*,\s*|\s*;\s*|\s*\n+\s*", raw.strip()) if x.strip()]
        out.extend(parts)
    return out


def split_multiline_items(raw_items: list[str]) -> list[str]:
    out: list[str] = []
    for raw in raw_items:
        parts = [x.strip() for x in re.split(r"\s*;\s*|\s*\n+\s*", raw.strip()) if x.strip()]
        out.extend(parts)
    return out


def _auto_system_label_from_species_path(path_text: str, index: int) -> str:
    name = Path(path_text).name.strip()
    if name.lower().endswith(".species"):
        name = name[: -len(".species")]
    if not name:
        name = f"run_{index}"
    return name


def parse_species_file_specs(raw_items: list[str]) -> list[dict[str, Any]]:
    entries = split_multiline_items(raw_items)
    specs: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, 1):
        label_text = ""
        path_text = entry
        if "::" in entry:
            label_text, path_text = entry.split("::", 1)
            label_text = label_text.strip()
            path_text = path_text.strip()
        if not path_text:
            raise ValueError(f"Invalid species file entry #{index}: missing path.")

        source_type = "species"
        resolved_path = path_text
        if path_text.lower().endswith(".reactionabcd"):
            source_type = "reactionabcd"
            resolved_path = path_text[: -len(".reactionabcd")] + ".species"

        system = None
        replicate = None
        if label_text:
            if "@" in label_text:
                left, right = label_text.split("@", 1)
                system = left.strip() or None
                replicate = right.strip() or None
            else:
                system = label_text
        if not system:
            system = _auto_system_label_from_species_path(resolved_path, index)

        specs.append(
            {
                "index": index,
                "entry": entry,
                "path": resolved_path,
                "input_path": path_text,
                "source_type": source_type,
                "system": system,
                "replicate": replicate,
            }
        )
    return specs


def load_tidy_table(path_text: str) -> pd.DataFrame:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"tidy table not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


def resolve_time_align_group_cols(
    source: pd.DataFrame,
    system_col: str | None,
    replicate_col: str | None,
) -> list[str]:
    if replicate_col and replicate_col in source.columns:
        cols = [col for col in (system_col, replicate_col) if col and col in source.columns]
        if cols:
            return cols
        return [replicate_col]
    if system_col and system_col in source.columns:
        return [system_col]
    return []


def align_time_axis_for_comparison(
    source: pd.DataFrame,
    *,
    time_col: str,
    system_col: str | None,
    replicate_col: str | None,
    mode: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if mode not in {"raw", "truncate", "relative"}:
        raise ValueError("time_align must be one of {'raw', 'truncate', 'relative'}.")
    if time_col not in source.columns:
        raise ValueError(f"time column {time_col!r} not found in input data.")

    aligned = source.copy()
    aligned[time_col] = pd.to_numeric(aligned[time_col], errors="raise")
    if aligned.empty:
        raise ValueError("No rows available for time alignment.")

    group_cols = resolve_time_align_group_cols(
        source=aligned,
        system_col=system_col,
        replicate_col=replicate_col,
    )
    before_min = float(aligned[time_col].min())
    before_max = float(aligned[time_col].max())
    meta: dict[str, Any] = {
        "time_align": mode,
        "group_by": group_cols,
        "time_min_before": before_min,
        "time_max_before": before_max,
    }
    if mode == "raw":
        meta["time_min_after"] = before_min
        meta["time_max_after"] = before_max
        return aligned, meta

    if mode == "truncate":
        if group_cols:
            max_by_group = aligned.groupby(group_cols, dropna=False)[time_col].max()
            common_end = float(max_by_group.min())
        else:
            common_end = before_max
        aligned = aligned[aligned[time_col] <= common_end].copy()
        if aligned.empty:
            raise ValueError("time_align='truncate' removed all rows.")
        meta["common_end_time"] = common_end
        meta["time_min_after"] = float(aligned[time_col].min())
        meta["time_max_after"] = float(aligned[time_col].max())
        return aligned, meta

    if group_cols:
        min_by_group = aligned.groupby(group_cols, dropna=False)[time_col].transform("min")
        max_by_group = aligned.groupby(group_cols, dropna=False)[time_col].transform("max")
    else:
        min_value = float(aligned[time_col].min())
        max_value = float(aligned[time_col].max())
        min_by_group = pd.Series(min_value, index=aligned.index)
        max_by_group = pd.Series(max_value, index=aligned.index)

    span = max_by_group - min_by_group
    normalized = aligned[time_col] - min_by_group
    nonzero = span > 0
    normalized.loc[nonzero] = normalized.loc[nonzero] / span.loc[nonzero]
    normalized.loc[~nonzero] = 0.0
    aligned["__time_original"] = aligned[time_col]
    aligned[time_col] = normalized.astype(float)
    meta["time_min_after"] = float(aligned[time_col].min())
    meta["time_max_after"] = float(aligned[time_col].max())
    return aligned, meta


def parse_target_item(item: str) -> tuple[str, str, str]:
    """Return (qtype, query, label), qtype in {'formula', 'smiles'}."""
    label = ""
    query = item.strip()
    if "::" in query:
        label, query = query.split("::", 1)
        label = label.strip()
        query = query.strip()

    low = query.lower()
    if low.startswith("formula:"):
        query = query[len("formula:") :].strip()
        qtype = "formula"
    elif low.startswith("f:"):
        query = query[2:].strip()
        qtype = "formula"
    elif low.startswith("smiles:"):
        query = query[len("smiles:") :].strip()
        qtype = "smiles"
    elif low.startswith("smi:"):
        query = query[len("smi:") :].strip()
        qtype = "smiles"
    elif low.startswith("s:"):
        query = query[2:].strip()
        qtype = "smiles"
    else:
        qtype = "formula" if looks_like_formula(query) else "smiles"

    if not label:
        label = query
    return qtype, query, label


def derive_species_path(reac_path: str) -> str:
    if reac_path.endswith(".reactionabcd"):
        return reac_path[: -len(".reactionabcd")] + ".species"
    return reac_path + ".species"


def derive_trajectory_path(source_path: str) -> str:
    path = (source_path or "").strip()
    if not path:
        return path
    candidates: list[str] = []
    low = path.lower()
    if low.endswith(".lammpstrj"):
        candidates.append(path)
    elif low.endswith(".species"):
        base = path[: -len(".species")]
        candidates.append(base)
        if not base.lower().endswith(".lammpstrj"):
            candidates.append(base + ".lammpstrj")
    elif low.endswith(".reactionabcd"):
        base = path[: -len(".reactionabcd")]
        candidates.append(base)
        if not base.lower().endswith(".lammpstrj"):
            candidates.append(base + ".lammpstrj")
    else:
        candidates.append(path)
        candidates.append(path + ".lammpstrj")

    deduped: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        key = cand.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)

    # Only accept existing lammpstrj files as a resolved trajectory.
    for candidate in deduped:
        if candidate.lower().endswith(".lammpstrj") and os.path.exists(candidate):
            return candidate
    return deduped[0] if deduped else path


def _dataset_base_path(path_text: str) -> str:
    """Return the shared RNG output stem for a known artifact path."""

    path = (path_text or "").strip()
    if not path:
        return ""
    for suffix, kind in ARTIFACT_SUFFIXES:
        if path.lower().endswith(suffix):
            return path if kind == "trajectory" else path[: -len(suffix)]
    for suffix in (".json", ".html", ".svg"):
        if path.lower().endswith(suffix):
            return path[: -len(suffix)]
    if path.lower().endswith(".lammpstrj"):
        return path
    return path


def _dataset_file_descriptor(path_text: str, *, source: str) -> dict[str, Any]:
    path = (path_text or "").strip()
    exists = bool(path) and os.path.isfile(path)
    return {
        "path": path,
        "source": source,
        "exists": exists,
        "size_bytes": os.path.getsize(path) if exists else None,
    }


def _scan_rng_dataset_directory(
    directory_text: str,
    *,
    preferred_base: str = "",
) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    """Find the most complete ReacNetGenerator output set in one directory."""

    candidates = discover_dataset_candidates(directory_text)
    if not candidates:
        return "", {}, []
    preferred = (preferred_base or "").strip()
    chosen = choose_dataset_candidate(candidates, preferred)
    for item in candidates:
        item["selected"] = bool(chosen and str(item["base"]) == str(chosen["base"]))
    if not chosen:
        return "", {}, candidates[:12]
    selected = dict(chosen["artifact_paths"])
    visible_candidates = candidates[:12]
    if chosen not in visible_candidates:
        visible_candidates = [chosen, *visible_candidates[:11]]
    return str(chosen["base"]), selected, visible_candidates


def build_dataset_status_payload(params: dict[str, list[str]]) -> dict[str, Any]:
    """Resolve a compact, shared view of a ReacNetGenerator output set."""

    explicit = {
        "reaction": (params.get("reac", [""])[0] or "").strip(),
        "species": (params.get("species_file", [""])[0] or "").strip(),
        "trajectory": (params.get("trajectory_file", [""])[0] or "").strip(),
        "timeline": (params.get("timeline_file", [""])[0] or "").strip(),
        "reactionevent": (params.get("reactionevent_file", [""])[0] or "").strip(),
        "molecules": (params.get("molecules_file", [""])[0] or "").strip(),
    }
    explicit = {
        key: str(validate_browse_path(path)) if path else ""
        for key, path in explicit.items()
    }
    folder = (params.get("dataset_dir", [""])[0] or "").strip()
    folder_base = ""
    folder_files: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    if folder:
        folder = str(validate_browse_path(folder))
        preferred_base = (params.get("dataset_base", [""])[0] or "").strip()
        folder_base, folder_files, candidates = _scan_rng_dataset_directory(folder, preferred_base=preferred_base)
    seed = next((value for value in explicit.values() if value), folder_base)
    base = _dataset_base_path(seed)
    inferred = {
        "reaction": f"{base}.reactionabcd" if base else "",
        "species": f"{base}.species" if base else "",
        "trajectory": base,
        "timeline": f"{base}.timeline.h5" if base else "",
        "reactionevent": f"{base}.reactionevent.csv" if base else "",
        "molecules": f"{base}.molecules.csv" if base else "",
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for key in (
        "reaction",
        "species",
        "trajectory",
        "timeline",
        "reactionevent",
        "molecules",
    ):
        selected = explicit[key] or folder_files.get(key, "") or inferred[key]
        source = "explicit" if explicit[key] else ("folder" if folder_files.get(key) else "derived")
        artifacts[key] = _dataset_file_descriptor(selected, source=source)

    capabilities = {
        "species": artifacts["reaction"]["exists"],
        "intermediate": artifacts["species"]["exists"],
        "reaction": artifacts["reaction"]["exists"],
        "events": bool(
            artifacts["timeline"]["exists"]
            or artifacts["reactionevent"]["exists"]
        ),
        "evolution": artifacts["species"]["exists"],
    }
    manifest_payload: dict[str, Any] = {}
    manifest_path = ""
    configured_cache = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
    if base and configured_cache:
        candidate = resolve_dataset_paths(
            Path(base).parent,
            Path(base).name,
            persist_identity=False,
        ).manifest
        manifest_path = str(candidate)
        if candidate.is_file():
            try:
                manifest_payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                manifest_payload = {}

    trajectory_index_status: dict[str, Any] = {"state": "missing"}
    if artifacts["trajectory"]["exists"]:
        try:
            trajectory_index_status = TRAJECTORY_INDEX_STORE.status(artifacts["trajectory"]["path"])
        except Exception as exc:
            trajectory_index_status = {"state": "invalid", "message": str(exc)}

    reactionevent_exists = bool(artifacts["reactionevent"]["exists"])
    molecules_exists = bool(artifacts["molecules"]["exists"])
    timeline_exists = bool(artifacts["timeline"]["exists"])
    rng_event_status = {
        "ready": bool(timeline_exists or reactionevent_exists),
        "state": (
            "ready"
            if timeline_exists or reactionevent_exists
            else "missing"
        ),
        "timeline_exists": timeline_exists,
        "reactionevent_exists": reactionevent_exists,
        "molecules_exists": molecules_exists,
    }

    readiness = {
        "basic_analysis": {
            "ready": bool(artifacts["reaction"]["exists"] and artifacts["species"]["exists"]),
            "state": "ready" if artifacts["reaction"]["exists"] and artifacts["species"]["exists"] else "missing",
        },
        "event_search": {
            **rng_event_status,
        },
        "trajectory_evidence": {
            "ready": trajectory_index_status.get("state") == "ready",
            **trajectory_index_status,
        },
    }
    return {
        "ok": True,
        "dataset": {
            "base": base,
            "label": Path(base).name if base else "未选择数据集",
            "folder": folder,
            "selected_base": folder_base or base,
            "candidates": candidates,
            "artifacts": artifacts,
            "capabilities": capabilities,
            "ready_count": sum(1 for item in artifacts.values() if item["exists"]),
            "readiness": readiness,
            "manifest": {
                "path": manifest_path,
                "found": bool(manifest_payload),
                "dataset_id": str(manifest_payload.get("dataset_id", "")),
            },
        },
    }


def resolve_start_smiles(net: ReactionNetwork, start_query: str) -> str | None:
    q = start_query.strip()
    if not q:
        return None
    if q in net.species:
        return q
    candidates = net.smiles_by_formula(q)
    if not candidates:
        return None
    return max(candidates, key=lambda s: net.species[s].total_throughput)


def moving_average(vals: list[float], window: int) -> list[float]:
    if window <= 1 or len(vals) <= 2:
        return list(vals)
    out: list[float] = []
    half = window // 2
    for i in range(len(vals)):
        lo = max(0, i - half)
        hi = min(len(vals), i + half + 1)
        seg = vals[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def downsample_series(x_vals: list[float], y_map: dict[str, list[float]], max_points: int) -> tuple[list[float], dict[str, list[float]]]:
    n = len(x_vals)
    if max_points <= 0 or n <= max_points:
        return x_vals, y_map

    step = max(1, math.ceil(n / max_points))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)

    x2 = [x_vals[i] for i in idx]
    y2: dict[str, list[float]] = {}
    for k, arr in y_map.items():
        y2[k] = [arr[i] for i in idx]
    return x2, y2


def collect_species_totals(
    species_file: str,
    *,
    progress_callback: Any = None,
) -> dict[str, int]:
    if progress_callback is not None:
        progress_callback(
            {
                "progress": 0.1,
                "message": "Reading prepared Species catalog",
            }
        )
    totals = SPECIES_COMPOSITION_STORE.species_totals(species_file)
    if progress_callback is not None:
        progress_callback(
            {
                "progress": 1.0,
                "message": "Loaded prepared Species catalog",
                "timesteps": None,
            }
        )
    return totals


def resolve_plot_series_from_species_totals(
    targets: list[tuple[str, str, str]],
    *,
    species_totals: dict[str, int],
    formula_mode: str,
    max_smiles_per_formula: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    series_defs: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    if not species_totals:
        warnings.append("no species tokens found in species source")
        return series_defs, mapping_rows, warnings

    formula_by_smiles: dict[str, str] = {}
    formula_to_smiles: dict[str, list[str]] = {}
    for smi, total in species_totals.items():
        try:
            formula = smiles_to_formula_fast(smi)
        except Exception:
            continue
        formula_by_smiles[smi] = formula
        formula_to_smiles.setdefault(formula, []).append(smi)

    for formula, smiles_list in formula_to_smiles.items():
        smiles_list.sort(key=lambda smi: (-species_totals.get(smi, 0), smi))
        formula_to_smiles[formula] = smiles_list

    for qtype, query, label in targets:
        if qtype == "smiles":
            if query not in species_totals:
                warnings.append(f"SMILES not found in species source: {query}")
                continue
            formula = formula_by_smiles.get(query, smiles_to_formula_fast(query))
            mass_fields = formula_mass_fields(formula)
            series_defs.append(
                {
                    "series_name": label,
                    "query_type": "smiles",
                    "query": query,
                    "formula": formula,
                    "formula_exact_mass": mass_fields["exact_mass"],
                    "formula_nominal_mass": mass_fields["nominal_mass"],
                    "members": [query],
                }
            )
            mapping_rows.append(
                {
                    "series_name": label,
                    "query_type": "smiles",
                    "query": query,
                    "formula": formula,
                    "smiles": query,
                    "formula_exact_mass": mass_fields["exact_mass"],
                    "formula_nominal_mass": mass_fields["nominal_mass"],
                    "exact_mass": mass_fields["exact_mass"],
                    "nominal_mass": mass_fields["nominal_mass"],
                    "tp_total": species_totals.get(query, 0),
                }
            )
            continue

        smiles_list = list(formula_to_smiles.get(query, []))
        if not smiles_list:
            warnings.append(f"Formula not found in species source: {query}")
            continue

        if max_smiles_per_formula > 0 and len(smiles_list) > max_smiles_per_formula:
            warnings.append(
                f"{query}: {len(smiles_list)} species members found, truncated to {max_smiles_per_formula}"
            )
            smiles_list = smiles_list[:max_smiles_per_formula]

        formula_mass = formula_mass_fields(query)

        if formula_mode in {"sum", "both"}:
            series_defs.append(
                {
                    "series_name": label,
                    "query_type": "formula_sum",
                    "query": query,
                    "formula": query,
                    "formula_exact_mass": formula_mass["exact_mass"],
                    "formula_nominal_mass": formula_mass["nominal_mass"],
                    "members": smiles_list,
                }
            )
            for smi in smiles_list:
                smi_formula = formula_by_smiles.get(smi, query)
                smi_mass = formula_mass_fields(smi_formula)
                mapping_rows.append(
                    {
                        "series_name": label,
                        "query_type": "formula_sum",
                        "query": query,
                        "formula": query,
                        "smiles": smi,
                        "formula_exact_mass": formula_mass["exact_mass"],
                        "formula_nominal_mass": formula_mass["nominal_mass"],
                        "exact_mass": smi_mass["exact_mass"],
                        "nominal_mass": smi_mass["nominal_mass"],
                        "tp_total": species_totals.get(smi, 0),
                    }
                )

        if formula_mode in {"split", "both"}:
            for idx, smi in enumerate(smiles_list, 1):
                sname = f"{label}[{idx}]"
                smi_formula = formula_by_smiles.get(smi, query)
                smi_mass = formula_mass_fields(smi_formula)
                series_defs.append(
                    {
                        "series_name": sname,
                        "query_type": "formula_member",
                        "query": query,
                        "formula": query,
                        "formula_exact_mass": formula_mass["exact_mass"],
                        "formula_nominal_mass": formula_mass["nominal_mass"],
                        "members": [smi],
                    }
                )
                mapping_rows.append(
                    {
                        "series_name": sname,
                        "query_type": "formula_member",
                        "query": query,
                        "formula": query,
                        "smiles": smi,
                        "formula_exact_mass": formula_mass["exact_mass"],
                        "formula_nominal_mass": formula_mass["nominal_mass"],
                        "exact_mass": smi_mass["exact_mass"],
                        "nominal_mass": smi_mass["nominal_mass"],
                        "tp_total": species_totals.get(smi, 0),
                    }
                )

    return series_defs, mapping_rows, warnings


def resolve_plot_series(
    net: ReactionNetwork,
    targets: list[tuple[str, str, str]],
    *,
    formula_mode: str,
    max_smiles_per_formula: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    series_defs: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    for qtype, query, label in targets:
        if qtype == "smiles":
            if query not in net.species:
                warnings.append(f"SMILES not in network: {query}")
                continue
            sp = net.species[query]
            mass_fields = formula_mass_fields(sp.formula)
            series_defs.append(
                {
                    "series_name": label,
                    "query_type": "smiles",
                    "query": query,
                    "formula": sp.formula,
                    "formula_exact_mass": mass_fields["exact_mass"],
                    "formula_nominal_mass": mass_fields["nominal_mass"],
                    "members": [query],
                }
            )
            mapping_rows.append(
                {
                    "series_name": label,
                    "query_type": "smiles",
                    "query": query,
                    "formula": sp.formula,
                    "smiles": query,
                    "exact_mass": mass_fields["exact_mass"],
                    "nominal_mass": mass_fields["nominal_mass"],
                    "tp_total": sp.total_throughput,
                }
            )
            continue

        smiles_list = list(net.smiles_by_formula(query))
        if not smiles_list:
            warnings.append(f"Formula has no SMILES in network: {query}")
            continue
        formula_mass = formula_mass_fields(query)
        smiles_list.sort(
            key=lambda s: net.species[s].total_throughput if s in net.species else 0,
            reverse=True,
        )
        if max_smiles_per_formula > 0:
            smiles_list = smiles_list[:max_smiles_per_formula]

        if formula_mode in {"sum", "both"}:
            series_defs.append(
                {
                    "series_name": label,
                    "query_type": "formula_sum",
                    "query": query,
                    "formula": query,
                    "formula_exact_mass": formula_mass["exact_mass"],
                    "formula_nominal_mass": formula_mass["nominal_mass"],
                    "members": list(smiles_list),
                }
            )
            for smi in smiles_list:
                sp = net.species.get(smi)
                smi_mass = formula_mass_fields(sp.formula if sp else query)
                mapping_rows.append(
                    {
                        "series_name": label,
                        "query_type": "formula_sum",
                        "query": query,
                        "formula": query,
                        "smiles": smi,
                        "formula_exact_mass": formula_mass["exact_mass"],
                        "formula_nominal_mass": formula_mass["nominal_mass"],
                        "exact_mass": smi_mass["exact_mass"],
                        "nominal_mass": smi_mass["nominal_mass"],
                        "tp_total": sp.total_throughput if sp else 0,
                    }
                )

        if formula_mode in {"split", "both"}:
            for i, smi in enumerate(smiles_list, 1):
                sp = net.species.get(smi)
                sname = f"{label}[{i}]"
                smi_mass = formula_mass_fields(sp.formula if sp else query)
                series_defs.append(
                    {
                        "series_name": sname,
                        "query_type": "formula_member",
                        "query": query,
                        "formula": query,
                        "formula_exact_mass": formula_mass["exact_mass"],
                        "formula_nominal_mass": formula_mass["nominal_mass"],
                        "members": [smi],
                    }
                )
                mapping_rows.append(
                    {
                        "series_name": sname,
                        "query_type": "formula_member",
                        "query": query,
                        "formula": query,
                        "smiles": smi,
                        "formula_exact_mass": formula_mass["exact_mass"],
                        "formula_nominal_mass": formula_mass["nominal_mass"],
                        "exact_mass": smi_mass["exact_mass"],
                        "nominal_mass": smi_mass["nominal_mass"],
                        "tp_total": sp.total_throughput if sp else 0,
                    }
                )

    return series_defs, mapping_rows, warnings


def parse_species_selected(
    species_file: str,
    selected_smiles: list[str],
    progress_callback: Any = None,
) -> tuple[list[int], dict[str, list[int]]]:
    selected = list(dict.fromkeys(selected_smiles))
    timesteps = SPECIES_COMPOSITION_STORE.timesteps(species_file)
    series = {
        smiles: [
            int(value)
            for value in SPECIES_COMPOSITION_STORE.species_count_series(
                species_file, timesteps, smiles
            ).values()
        ]
        for smiles in selected
    }
    if progress_callback is not None:
        progress_callback(
            {
                "progress": 1.0,
                "message": "Loaded prepared Species abundance series",
                "timesteps": len(timesteps),
                "frame": timesteps[-1] if timesteps else None,
            }
        )
    return timesteps, series



def collect_trajectory_timestep_index(
    trajectory_file: str,
    *,
    progress_callback: Any = None,
) -> list[int]:
    index = TRAJECTORY_INDEX_STORE.open_required(trajectory_file)
    return list(index.frames)


def collect_trajectory_frames_by_ranges(
    trajectory_file: str,
    specs: list[tuple[int, int]],
    *,
    progress_callback: Any = None,
    progress_start: float = 0.02,
    progress_span: float = 0.40,
) -> list[int]:
    if not specs:
        return []
    file_size = max(os.path.getsize(trajectory_file), 1)
    bytes_read = 0
    last_emit = 0.0
    max_end = max(hi for _lo, hi in specs)
    frames: list[int] = []

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "reading_trajectory",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    emit(progress_start, f"Scanning trajectory ranges up to frame {max_end}")
    with open(trajectory_file, "rb") as fh:
        while True:
            line = fh.readline()
            if not line:
                break
            bytes_read += len(line)
            if not line.startswith(b"ITEM: TIMESTEP"):
                now = time.monotonic()
                frac = bytes_read / file_size
                if frac >= 0.99 or (now - last_emit) >= 1.0:
                    emit(
                        progress_start + progress_span * min(frac, 1.0),
                        f"Scanning trajectory file: {frac * 100:.1f}%",
                        n_selected_frames=len(frames),
                    )
                    last_emit = now
                continue
            timestep_line = fh.readline()
            if not timestep_line:
                break
            bytes_read += len(timestep_line)
            frame: int | None = None
            try:
                frame = int(timestep_line.strip().split()[0])
            except Exception:
                frame = None
            if frame is None:
                continue
            if frame > max_end:
                break
            if any(lo <= frame <= hi for lo, hi in specs):
                frames.append(frame)
            now = time.monotonic()
            frac = bytes_read / file_size
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    progress_start + progress_span * min(frac, 1.0),
                    f"Scanning trajectory file: {frac * 100:.1f}%",
                    frame=frame,
                    n_selected_frames=len(frames),
                )
                last_emit = now

    emit(
        progress_start + progress_span,
        f"Trajectory range scan ready: {len(frames)} frames",
        n_selected_frames=len(frames),
    )
    return frames


def parse_frame_range_specs(text: str) -> list[tuple[int, int]]:
    specs: list[tuple[int, int]] = []
    raw = (text or "").strip()
    if not raw:
        return specs
    for token in re.split(r"[\s,;]+", raw):
        item = token.strip()
        if not item:
            continue
        m = re.match(r"^(-?\d+)\s*[-:~]\s*(-?\d+)$", item)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2))
            specs.append((min(lo, hi), max(lo, hi)))
            continue
        if re.match(r"^-?\d+$", item):
            value = int(item)
            specs.append((value, value))
            continue
        raise ValueError(f"invalid frame range token: {item}")
    return specs


def parse_atom_id_specs(text: str) -> set[int]:
    atom_ids: set[int] = set()
    raw = (text or "").strip()
    if not raw:
        return atom_ids
    normalized = (
        raw.replace("[", " ")
        .replace("]", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("，", " ")
        .replace("；", " ")
    )
    normalized = re.sub(r"(\d)\s*[-:~]\s*(\d)", r"\1-\2", normalized)
    for token in re.split(r"[\s,;]+", normalized):
        item = token.strip()
        if not item:
            continue
        m = re.match(r"^(\d+)\s*[-:~]\s*(\d+)$", item)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2))
            if lo <= 0 or hi <= 0:
                raise ValueError(f"invalid atom id token: {item}")
            atom_ids.update(range(min(lo, hi), max(lo, hi) + 1))
            continue
        if re.match(r"^\d+$", item):
            value = int(item)
            if value <= 0:
                raise ValueError(f"invalid atom id token: {item}")
            atom_ids.add(value)
            continue
        raise ValueError(f"invalid atom id token: {item}")
    return atom_ids


def parse_type_element_map_specs(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    raw = (text or "").strip()
    if not raw:
        return mapping
    normalized = raw.replace("；", ";").replace("，", ",").replace("：", ":")
    for token in re.split(r"[;,\n]+", normalized):
        item = token.strip()
        if not item:
            continue
        m = re.match(r"^([0-9]+)\s*[:=]\s*([A-Za-z][A-Za-z]?)$", item)
        if not m:
            raise ValueError(f"invalid type->element token: {item}")
        atom_type = str(int(m.group(1)))
        element = m.group(2)
        mapping[atom_type] = element[0].upper() + element[1:].lower()
    return mapping


def expand_frames_by_ranges(available_frames: list[int], specs: list[tuple[int, int]]) -> list[int]:
    if not available_frames or not specs:
        return []
    selected: list[int] = []
    for frame in available_frames:
        if any(lo <= frame <= hi for lo, hi in specs):
            selected.append(int(frame))
    return selected


def format_frame_windows(windows: list[tuple[int, int]], limit: int = 8) -> str:
    if not windows:
        return ""
    parts: list[str] = []
    for start, end in windows[:limit]:
        parts.append(str(start) if start == end else f"{start}-{end}")
    if len(windows) > limit:
        parts.append(f"...(+{len(windows) - limit})")
    return "; ".join(parts)



def read_trajectory_requested_frame_blocks(
    trajectory_file: str,
    requested_frames: list[int],
    *,
    progress_callback: Any = None,
    progress_start: float = 0.0,
    progress_span: float = 1.0,
) -> dict[int, bytes]:
    requested = sorted({int(frame) for frame in requested_frames if frame is not None})
    if not requested:
        return {}

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "reading_anchor_frames",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    required_index = TRAJECTORY_INDEX_STORE.open_required(trajectory_file)
    offsets = required_index.offsets_for(requested)
    blocks: dict[int, bytes] = {}
    emit(progress_start, f"Reading {len(requested)} anchor frame(s) from prepared trajectory index")
    with open(trajectory_file, "rb") as fh:
        for idx, frame in enumerate(requested, 1):
            block = offsets.get(frame)
            if block is None:
                continue
            start, end = int(block[0]), int(block[1])
            if end <= start:
                continue
            fh.seek(start)
            blocks[frame] = fh.read(end - start)
            emit(
                progress_start + progress_span * min(idx / max(len(requested), 1), 1.0),
                f"Reading anchor frame {frame} ({idx}/{len(requested)})",
                frame=frame,
            )
    emit(
        progress_start + progress_span,
        f"Anchor-frame read ready: {len(blocks)}/{len(requested)} frame(s)",
        n_found_frames=len(blocks),
    )
    return blocks


def downsample_xy_payload(payload: dict[str, Any], max_points: int) -> dict[str, Any]:
    time_values = payload.get("time") or []
    y_values = payload.get("value") or []
    count = min(len(time_values), len(y_values))
    if count <= max_points or max_points <= 1:
        return payload

    step = (count - 1) / float(max_points - 1)
    indices = [int(round(idx * step)) for idx in range(max_points)]
    indices[0] = 0
    indices[-1] = count - 1
    unique = sorted(set(indices))
    return {
        "time": [time_values[idx] for idx in unique],
        "value": [y_values[idx] for idx in unique],
    }


def downsample_summary_payload(obj: Any, max_points: int) -> Any:
    if isinstance(obj, dict):
        if set(obj.keys()) == {"time", "value"} or ("time" in obj and "value" in obj and len(obj) == 2):
            return downsample_xy_payload(obj, max_points)
        return {key: downsample_summary_payload(value, max_points) for key, value in obj.items()}
    if isinstance(obj, list):
        return [downsample_summary_payload(item, max_points) for item in obj]
    return obj


def build_species_plot_payload(
    params: dict[str, list[str]],
    *,
    progress_callback: Any = None,
) -> dict[str, Any]:
    raw_target_params = params.get("target", [])
    raw_targets = split_target_items(raw_target_params)
    if not raw_targets:
        raise ValueError("missing target")

    reac_input = (params.get("reac", [""])[0] or "").strip()
    min_tp = int_param(params, "min_tp", 1)
    species_file_raw = (params.get("species_file", [""])[0] or "").strip()
    species_file = species_file_raw
    species_file_source_type = "species"
    if species_file_raw.lower().endswith(".reactionabcd"):
        species_file = derive_species_path(species_file_raw)
        species_file_source_type = "reactionabcd"
    species_file_specs = parse_species_file_specs(params.get("species_files", []))
    source_hints: list[str] = []
    source_mode = "multi_species_files" if species_file_specs else "single_species_file"
    if species_file_specs and species_file:
        source_hints.append("species_files is set; species_file is ignored.")
    elif species_file_raw and species_file_source_type == "reactionabcd":
        source_hints.append("species_file uses .reactionabcd and is converted to paired .species path.")
    reac_for_derive = reac_input or str(DEFAULT_REACTION_FILE)
    if species_file_specs:
        for spec in species_file_specs:
            candidate = Path(str(spec["path"])).expanduser().resolve()
            if not candidate.exists():
                raise FileNotFoundError(f"species file not found: {candidate}")
    else:
        if not species_file:
            species_file = derive_species_path(reac_for_derive)
            source_hints.append("species_file is empty; derived from reactionabcd path.")
        if not os.path.exists(species_file):
            raise FileNotFoundError(f"species file not found: {species_file}")

    formula_mode = (params.get("formula_mode", ["sum"])[0] or "sum").strip().lower()
    if formula_mode not in {"sum", "split", "both"}:
        formula_mode = "sum"
    max_smiles_per_formula = int_param(params, "max_smiles_per_formula", 0)
    max_curves = int_param(params, "max_curves", 30)
    x_axis = (params.get("x_axis", ["step"])[0] or "step").strip().lower()
    if x_axis not in {"step", "ps", "ns"}:
        x_axis = "step"
    raw_timestep_ps = (params.get("timestep_ps", [""])[0] or "").strip()
    timestep_ps: float | None = None
    if raw_timestep_ps:
        try:
            timestep_ps = float(raw_timestep_ps)
        except ValueError as exc:
            raise ValueError("timestep_ps must be a positive number") from exc
        if timestep_ps <= 0:
            raise ValueError("timestep_ps must be a positive number")
    if x_axis != "step" and timestep_ps is None and not species_file_specs:
        raise ValueError(
            "physical time requires a confirmed timestep-to-ps conversion"
        )
    timestep_ps_by_source: dict[str, float] = {}
    raw_conversions = (params.get("timestep_ps_by_source", [""])[0] or "").strip()
    if raw_conversions:
        try:
            decoded_conversions = json.loads(raw_conversions)
        except json.JSONDecodeError as exc:
            raise ValueError("timestep_ps_by_source must be valid JSON") from exc
        if not isinstance(decoded_conversions, dict):
            raise ValueError("timestep_ps_by_source must be a JSON object")
        for raw_path, raw_value in decoded_conversions.items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "every source conversion must be a positive number"
                ) from exc
            if value <= 0:
                raise ValueError(
                    "every source conversion must be a positive number"
                )
            timestep_ps_by_source[str(Path(str(raw_path)).expanduser().resolve())] = value
    normalize = (params.get("normalize", ["none"])[0] or "none").strip().lower()
    if normalize not in {"none", "initial", "max"}:
        normalize = "none"
    time_align = (params.get("time_align", ["raw"])[0] or "raw").strip().lower()
    if time_align not in {"raw", "truncate", "relative"}:
        time_align = "raw"
    smooth_window = max(1, int_param(params, "smooth_window", 1))
    downsample = int_param(params, "downsample", 1800)

    def report(progress: float, phase: str, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": phase,
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    def species_specs_payload() -> list[dict[str, Any]]:
        return [
            {
                "path": str(spec["path"]),
                "input_path": str(spec.get("input_path", spec["path"])),
                "source_type": str(spec.get("source_type", "species")),
                "system": str(spec["system"]),
                "replicate": spec.get("replicate"),
            }
            for spec in species_file_specs
        ]

    report(0.02, "preparing", "Preparing species plot request")

    targets = [parse_target_item(x) for x in raw_targets]
    report(0.08, "resolving_targets", "Reading prepared Species catalog")

    species_totals: dict[str, int] = {}
    if species_file_specs:
        total_files = len(species_file_specs)
        for file_idx, spec in enumerate(species_file_specs, 1):
            this_path = str(spec["path"])
            this_system = str(spec["system"])
            totals_one = collect_species_totals(
                this_path,
                progress_callback=lambda update, file_idx=file_idx, total_files=total_files, this_system=this_system: report(
                    0.08 + 0.12 * (((file_idx - 1) + float(update.get("progress", 0.0))) / float(total_files)),
                    "resolving_targets",
                    f"[{file_idx}/{total_files}] {this_system}: {update.get('message', 'Reading prepared Species catalog')}",
                    timesteps=update.get("timesteps"),
                    frame=update.get("frame"),
                    system=this_system,
                ),
            )
            for smi, count in totals_one.items():
                species_totals[smi] = species_totals.get(smi, 0) + int(count)
    else:
        species_totals = collect_species_totals(
            species_file,
            progress_callback=lambda update: report(
                0.08 + 0.12 * float(update.get("progress", 0.0)),
                "resolving_targets",
                str(update.get("message", "Reading prepared Species catalog")),
                timesteps=update.get("timesteps"),
                frame=update.get("frame"),
            ),
        )

    series_defs, mapping_rows, warnings = resolve_plot_series_from_species_totals(
        targets,
        species_totals=species_totals,
        formula_mode=formula_mode,
        max_smiles_per_formula=max_smiles_per_formula,
    )
    warnings = source_hints + warnings
    report(0.22, "resolving_targets", f"Resolved {len(series_defs)} plot series")
    if not series_defs:
        return {
            "ok": True,
            "mode": "species",
            "query": {
                "reac": reac_input,
                "reac_effective": reac_for_derive,
                "species_file": species_file,
                "species_file_input": species_file_raw,
                "species_file_source_type": species_file_source_type,
                "species_files": species_specs_payload(),
                "source_mode": source_mode,
                "targets": raw_targets,
            },
            "meta": {"rows": 0, "warnings": warnings},
            "mapping": mapping_rows,
            "x_name": {"step": "timestep", "ps": "time_ps", "ns": "time_ns"}[x_axis],
            "x_values": [],
            "curves": [],
        }

    if max_curves > 0 and len(series_defs) > max_curves:
        warnings.append(f"too many curves ({len(series_defs)}), truncated to {max_curves}")
        series_defs = series_defs[:max_curves]

    selected_smiles: list[str] = []
    for definition in series_defs:
        selected_smiles.extend(definition["members"])
    selected_smiles = list(dict.fromkeys(selected_smiles))

    mapping_rows_out = [] if species_file_specs else list(mapping_rows)
    time_align_meta: dict[str, Any] = {"time_align": time_align, "group_by": []}
    curves: list[dict[str, Any]] = []
    y_map: dict[str, list[float]] = {}
    source_index_meta: list[dict[str, Any]] = []

    if species_file_specs:
        report(0.28, "reading_species", f"Reading {len(species_file_specs)} species files")
        tables: list[pd.DataFrame] = []
        total_files = len(species_file_specs)
        has_replicate = False
        for file_idx, spec in enumerate(species_file_specs, 1):
            this_path = str(spec["path"])
            this_system = str(spec["system"])
            this_replicate = spec.get("replicate")
            if this_replicate:
                has_replicate = True

            def species_progress(
                update: dict[str, Any],
                *,
                file_idx: int = file_idx,
                this_system: str = this_system,
                this_replicate: str | None = this_replicate,
            ) -> None:
                fraction = float(update.get("progress", 0.0))
                combined = ((file_idx - 1) + fraction) / float(total_files)
                report(
                    0.28 + 0.30 * combined,
                    "reading_species",
                    f"[{file_idx}/{total_files}] {this_system}: {update.get('message', 'Reading prepared Species index')}",
                    timesteps=update.get("timesteps"),
                    rows=update.get("rows"),
                    frame=update.get("frame"),
                    system=this_system,
                    replicate=this_replicate,
                )

            indexed_timesteps, indexed_series = parse_species_selected(
                this_path,
                selected_smiles,
                progress_callback=species_progress,
            )
            source_index_meta.append(
                SPECIES_COMPOSITION_STORE.open_required(this_path)
            )
            conversion = timestep_ps_by_source.get(
                str(Path(this_path).expanduser().resolve())
            )
            indexed_times = [
                (
                    float(timestep)
                    if x_axis == "step"
                    else float(timestep) * float(conversion)
                    / (1000.0 if x_axis == "ns" else 1.0)
                )
                for timestep in indexed_timesteps
            ]
            records = []
            for smiles in selected_smiles:
                values = indexed_series.get(smiles, [])
                records.extend(
                    {
                        "time": indexed_times[index],
                        "source_timestep": indexed_timesteps[index],
                        "analyzed_frame": index,
                        "species": smiles,
                        "count": int(values[index]),
                        "system": this_system,
                        "replicate": this_replicate,
                    }
                    for index in range(min(len(indexed_times), len(values)))
                )
            table = pd.DataFrame.from_records(records)
            tables.append(table)

        source = pd.concat(tables, ignore_index=True)
        if source.empty:
            return {
                "ok": True,
                "mode": "species",
                "query": {
                    "reac": reac_input,
                    "reac_effective": reac_for_derive,
                    "species_file": species_file,
                    "species_file_input": species_file_raw,
                    "species_file_source_type": species_file_source_type,
                    "species_files": species_specs_payload(),
                    "source_mode": source_mode,
                    "targets": raw_targets,
                },
                "meta": {"rows": 0, "warnings": warnings + ["no target species rows found in provided species files"]},
                "mapping": mapping_rows_out,
                "x_name": {"step": "timestep", "ps": "time_ps", "ns": "time_ns"}[x_axis],
                "x_values": [],
                "curves": [],
            }

        source, time_align_meta = align_time_axis_for_comparison(
            source=source,
            time_col="time",
            system_col="system",
            replicate_col="replicate" if has_replicate else None,
            mode=time_align,
        )
        x_vals = sorted({float(value) for value in source["time"].dropna().tolist()})
        base_time_name = {"step": "timestep", "ps": "time_ps", "ns": "time_ns"}.get(x_axis, "time")
        x_name = f"{base_time_name}_relative" if time_align == "relative" else base_time_name

        report(0.62, "building_curves", "Aggregating selected targets")
        group_cols = ["system"] + (["replicate"] if has_replicate else [])
        mapping_by_series: dict[str, list[dict[str, Any]]] = {}
        for row in mapping_rows:
            key = str(row.get("series_name", ""))
            mapping_by_series.setdefault(key, []).append(row)

        grouped = source.groupby(group_cols, dropna=False, sort=False)
        for group_values, subset in grouped:
            if not isinstance(group_values, tuple):
                group_values = (group_values,)
            system_value = group_values[0] if len(group_values) >= 1 else None
            replicate_value = group_values[1] if len(group_values) >= 2 else None
            system_text = "" if pd.isna(system_value) else str(system_value)
            replicate_text = "" if pd.isna(replicate_value) else str(replicate_value)
            group_label = system_text or "system"
            if replicate_text:
                group_label = f"{group_label}@{replicate_text}"

            counts_by_species: dict[str, dict[float, float]] = {}
            for smi, smi_df in subset.groupby("species", sort=False):
                timeline = (
                    smi_df.groupby("time", dropna=False)["count"]
                    .sum()
                    .astype(float)
                    .to_dict()
                )
                counts_by_species[str(smi)] = {float(k): float(v) for k, v in timeline.items()}
            source_timestep_by_time = {
                float(row["time"]): int(row["source_timestep"])
                for row in subset.to_dict(orient="records")
            }
            analyzed_frame_by_time = {
                float(row["time"]): int(row["analyzed_frame"])
                for row in subset.to_dict(orient="records")
            }

            for definition in series_defs:
                vals = [0.0] * len(x_vals)
                for smi in definition["members"]:
                    smi_map = counts_by_species.get(str(smi), {})
                    for idx, time_value in enumerate(x_vals):
                        vals[idx] += float(smi_map.get(float(time_value), 0.0))

                raw_vals = list(vals)

                if normalize == "initial":
                    v0 = vals[0] if vals else 0.0
                    vals = [value / v0 if v0 else 0.0 for value in vals]
                elif normalize == "max":
                    vmax = max(vals) if vals else 0.0
                    vals = [value / vmax if vmax else 0.0 for value in vals]
                vals = moving_average(vals, smooth_window)

                base_name = str(definition["series_name"])
                curve_name = f"{group_label} | {base_name}"
                y_map[curve_name] = vals
                curves.append(
                    {
                        "name": curve_name,
                        "base_series_name": base_name,
                        "system": system_text or None,
                        "replicate": replicate_text or None,
                        "query_type": definition["query_type"],
                        "query": definition["query"],
                        "formula": definition["formula"],
                        "formula_exact_mass": definition.get("formula_exact_mass"),
                        "formula_nominal_mass": definition.get("formula_nominal_mass"),
                        "n_members": len(definition["members"]),
                        "members": definition["members"],
                        "values": vals,
                        "raw_values": raw_vals,
                        "raw_x_values": list(x_vals),
                        "source_timesteps": [
                            source_timestep_by_time.get(float(value))
                            for value in x_vals
                        ],
                        "analyzed_frames": [
                            analyzed_frame_by_time.get(float(value))
                            for value in x_vals
                        ],
                        "max_value": max(vals) if vals else 0.0,
                    }
                )

                for map_row in mapping_by_series.get(base_name, []):
                    row_copy = dict(map_row)
                    row_copy["series_name"] = curve_name
                    row_copy["base_series_name"] = base_name
                    row_copy["system"] = system_text or None
                    row_copy["replicate"] = replicate_text or None
                    mapping_rows_out.append(row_copy)
    else:
        report(
            0.34,
            "reading_species",
            f"Reading prepared Species index: {os.path.basename(species_file)}",
            selected_smiles=len(selected_smiles),
        )
        timesteps, base_series = parse_species_selected(
            species_file,
            selected_smiles,
            progress_callback=lambda update: report(
                0.34 + 0.24 * float(update.get("progress", 0.0)),
                "reading_species",
                f"Reading prepared Species index: {float(update.get('progress', 0.0)) * 100:.1f}%",
                selected_smiles=len(selected_smiles),
                timesteps=update.get("timesteps"),
                frame=update.get("frame"),
            ),
        )
        source_index_meta.append(
            SPECIES_COMPOSITION_STORE.open_required(species_file)
        )
        if not timesteps:
            return {
                "ok": True,
                "mode": "species",
                "query": {
                    "reac": reac_input,
                    "reac_effective": reac_for_derive,
                    "species_file": species_file,
                    "species_file_input": species_file_raw,
                    "species_file_source_type": species_file_source_type,
                    "species_files": species_specs_payload(),
                    "source_mode": source_mode,
                    "targets": raw_targets,
                },
                "meta": {"rows": 0, "warnings": warnings + ["no timestep rows parsed"]},
                "mapping": mapping_rows_out,
                "x_name": {"step": "timestep", "ps": "time_ps", "ns": "time_ns"}[x_axis],
                "x_values": [],
                "curves": [],
            }

        if x_axis == "step":
            x_vals = [float(ts) for ts in timesteps]
            x_name = "timestep"
        elif x_axis == "ns":
            x_vals = [ts * timestep_ps / 1000.0 for ts in timesteps]
            x_name = "time_ns"
        else:
            x_vals = [ts * timestep_ps for ts in timesteps]
            x_name = "time_ps"

        report(0.62, "building_curves", "Aggregating selected targets")
        for definition in series_defs:
            vals = [0.0] * len(timesteps)
            for smi in definition["members"]:
                arr = base_series.get(smi, [])
                if len(arr) != len(vals):
                    continue
                for idx, value in enumerate(arr):
                    vals[idx] += float(value)

            raw_vals = list(vals)

            if normalize == "initial":
                v0 = vals[0] if vals else 0.0
                vals = [value / v0 if v0 else 0.0 for value in vals]
            elif normalize == "max":
                vmax = max(vals) if vals else 0.0
                vals = [value / vmax if vmax else 0.0 for value in vals]

            vals = moving_average(vals, smooth_window)
            y_map[definition["series_name"]] = vals
            curves.append(
                {
                    "name": definition["series_name"],
                    "query_type": definition["query_type"],
                    "query": definition["query"],
                    "formula": definition["formula"],
                    "formula_exact_mass": definition.get("formula_exact_mass"),
                    "formula_nominal_mass": definition.get("formula_nominal_mass"),
                    "n_members": len(definition["members"]),
                    "members": definition["members"],
                    "values": vals,
                    "raw_values": raw_vals,
                    "raw_x_values": list(x_vals),
                    "source_timesteps": list(timesteps),
                    "analyzed_frames": list(range(len(timesteps))),
                    "max_value": max(vals) if vals else 0.0,
                }
            )

    raw_x_values = list(x_vals)
    full_point_count = len(raw_x_values)
    if downsample > 0:
        report(0.84, "downsampling", "Downsampling web payload")
        x_vals_ds, y_map_ds = downsample_series(x_vals, y_map, downsample)
        for curve in curves:
            curve["values"] = y_map_ds.get(curve["name"], [])
            curve["max_value"] = max(curve["values"]) if curve["values"] else 0.0
        x_vals = x_vals_ds

    report(0.95, "serializing", "Preparing plot payload")
    return {
        "schema_version": 1,
        "ok": True,
        "mode": "species",
        "query": {
            "reac": reac_input,
            "reac_effective": reac_for_derive,
            "min_tp": min_tp,
            "species_file": species_file,
            "species_file_input": species_file_raw,
            "species_file_source_type": species_file_source_type,
            "species_files": species_specs_payload(),
            "source_mode": source_mode,
            "targets": raw_targets,
            "formula_mode": formula_mode,
            "max_smiles_per_formula": max_smiles_per_formula,
            "x_axis": x_axis,
            "timestep_ps": timestep_ps,
            "timestep_ps_by_source": timestep_ps_by_source,
            "time_align": time_align,
            "time_align_meta": time_align_meta,
            "normalize": normalize,
            "smooth_window": smooth_window,
            "downsample": downsample,
        },
        "meta": {
            "source_mode": "prepared_index",
            "indexes": source_index_meta,
            "n_timestep_full": full_point_count,
            "n_points_returned": len(x_vals),
            "n_curves": len(curves),
            "n_input_species_files": len(species_file_specs) if species_file_specs else 1,
            "warnings": warnings,
        },
        "mapping": mapping_rows_out,
        "x_name": x_name,
        "x_values": x_vals,
        "raw_x_values": raw_x_values,
        "curves": curves,
    }


def build_intermediate_candidates_payload(
    params: dict[str, list[str]],
    *,
    progress_callback: Any = None,
) -> dict[str, Any]:
    reac_input = (params.get("reac", [""])[0] or "").strip()
    min_tp = int_param(params, "min_tp", 1)
    species_file_raw = (params.get("species_file", [""])[0] or "").strip()
    species_file = species_file_raw
    species_file_source_type = "species"
    if species_file_raw.lower().endswith(".reactionabcd"):
        species_file = derive_species_path(species_file_raw)
        species_file_source_type = "reactionabcd"
    reac_effective = reac_input or str(DEFAULT_REACTION_FILE)
    if not species_file:
        species_file = derive_species_path(reac_effective)
    if not os.path.exists(species_file):
        raise FileNotFoundError(f"species file not found: {species_file}")

    kind = (params.get("kind", ["intermediate"])[0] or "intermediate").strip().lower()
    if kind not in {"intermediate", "product", "reactant", "all"}:
        kind = "intermediate"
    top = int_param(params, "top", 200)
    abundance_threshold = float_param(params, "abundance_threshold", 5.0)
    start_ratio_max = float_param(params, "start_ratio_max", 0.1)
    decay_alpha = float_param(params, "decay_alpha", 0.8)
    product_ratio_min = float_param(params, "product_ratio_min", 0.95)
    reactant_start_ratio_min = float_param(params, "reactant_start_ratio_min", 0.9)
    fwhm_min_frames = float_param(params, "fwhm_min_frames", 1.0)
    if fwhm_min_frames < 0:
        raise ValueError("fwhm_min_frames must be non-negative")
    raw_timestep_ps = (params.get("timestep_ps", [""])[0] or "").strip()
    timestep_ps: float | None = None
    if raw_timestep_ps:
        try:
            timestep_ps = float(raw_timestep_ps)
        except ValueError as exc:
            raise ValueError("timestep_ps must be a positive number") from exc
        if timestep_ps <= 0:
            raise ValueError("timestep_ps must be a positive number")
    require_fwhm = bool_param(params, "require_fwhm", True)
    with_flux = bool_param(params, "with_flux", True)
    flux_top = int_param(params, "flux_top", 10)

    def report(progress: float, phase: str, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": phase,
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    report(0.02, "preparing", "Preparing intermediate-candidate query")
    report(0.08, "reading_index", "Reading prepared Species abundance index")
    summary = SPECIES_COMPOSITION_STORE.timeline_summary(species_file)
    dt_ps = (
        int(summary["uniform_timestep_step"]) * timestep_ps
        if timestep_ps is not None
        and summary["uniform_timestep_step"] is not None
        else None
    )
    rows: list[dict[str, Any]] = []
    max_counts = dict(summary["max_counts"])
    total_species = max(len(max_counts), 1)
    scanned = 0

    report(0.72, "classifying", "Classifying candidate species")
    for smi, cmax in max_counts.items():
        scanned += 1
        if cmax < abundance_threshold:
            continue
        cstart = summary["start_counts"].get(smi, 0)
        cend = summary["end_counts"].get(smi, 0)
        start_ratio = (cstart / cmax) if cmax else 0.0
        end_ratio = (cend / cmax) if cmax else 0.0

        cls = "other"
        if start_ratio <= start_ratio_max:
            if end_ratio < decay_alpha:
                cls = "intermediate"
            elif end_ratio >= product_ratio_min:
                cls = "product"
        elif start_ratio >= reactant_start_ratio_min and cend < cstart:
            cls = "reactant"

        if kind != "all" and cls != kind:
            continue

        fwhm_points = summary["fwhm_longest_points"].get(smi, 0)
        if (
            cls == "intermediate"
            and require_fwhm
            and fwhm_points < fwhm_min_frames
        ):
            continue

        peak_ts = summary["max_timestep"].get(
            smi, summary["first_timestep"]
        )
        peak_frame = summary["max_analyzed_frame"].get(smi, 0)
        peak_time_ps = (
            peak_ts * timestep_ps
            if timestep_ps is not None
            else None
        )
        fwhm_ps = fwhm_points * dt_ps if dt_ps is not None else None
        score = cmax * max(0.0, 1.0 - end_ratio) if cls == "intermediate" else float(cmax)
        f = smiles_to_formula_fast(smi)

        rows.append(
            {
                "smiles": smi,
                "formula": f,
                **formula_mass_fields(f),
                "class": cls,
                "score": round(score, 6),
                "c_start": cstart,
                "c_max": cmax,
                "c_end": cend,
                "start_ratio": round(start_ratio, 6),
                "end_ratio": round(end_ratio, 6),
                "peak_timestep": peak_ts,
                "peak_analyzed_frame": int(peak_frame),
                "fwhm_frames": fwhm_points,
                "peak_time_ps": (
                    round(peak_time_ps, 6) if peak_time_ps is not None else None
                ),
                "fwhm_ps": round(fwhm_ps, 6) if fwhm_ps is not None else None,
            }
        )

        if scanned % 500 == 0:
            report(
                0.72 + 0.15 * min(scanned / total_species, 1.0),
                "classifying",
                f"Scanned {scanned}/{total_species} species",
                scanned=scanned,
                candidates=len(rows),
            )

    rows.sort(key=lambda x: (x["score"], x["c_max"]), reverse=True)
    if top > 0:
        rows = rows[:top]
    for i, row in enumerate(rows, 1):
        row["rank"] = i

    if with_flux and rows:
        report(0.90, "enriching_flux", "Loading reaction network for flux enrichment")
        reac_for_flux = reac_input or str(DEFAULT_REACTION_FILE)
        if not os.path.exists(reac_for_flux):
            raise FileNotFoundError(
                "with_flux requires a valid reactionabcd file. "
                "Please fill the top 'Reaction Network(.reactionabcd)' input."
            )
        net = STORE.get(reac_for_flux, min_tp)
        enrich_n = len(rows) if flux_top <= 0 else min(flux_top, len(rows))
        for row in rows[:enrich_n]:
            smi = str(row["smiles"])
            sp = net.species.get(smi)
            if sp is not None:
                row["tp_consume"] = sp.tp_as_reactant
                row["tp_produce"] = sp.tp_as_product
                row["net_production"] = sp.net_production
            src = net.production_of(smi, top_n=3)
            sink = net.consumption_of(smi, top_n=3)
            row["top_sources"] = " | ".join(
                f"{reaction_formula_str(r)} (tp={r.tp})" for r in src
            )
            row["top_sinks"] = " | ".join(
                f"{reaction_formula_str(r)} (tp={r.tp})" for r in sink
            )

    report(0.97, "serializing", "Preparing intermediate-candidate payload")
    return {
        "schema_version": "intermediate-candidate/v1",
        "rule_version": "intermediate-classification/v1",
        "scoring_version": "intermediate-score/v1",
        "ok": True,
        "query": {
            "reac": reac_input,
            "reac_effective": reac_effective,
            "min_tp": min_tp,
            "species_file": species_file,
            "species_file_input": species_file_raw,
            "species_file_source_type": species_file_source_type,
            "kind": kind,
            "top": top,
            "abundance_threshold": abundance_threshold,
            "start_ratio_max": start_ratio_max,
            "decay_alpha": decay_alpha,
            "product_ratio_min": product_ratio_min,
            "reactant_start_ratio_min": reactant_start_ratio_min,
            "fwhm_min_frames": fwhm_min_frames,
            "timestep_ps": timestep_ps,
            "require_fwhm": require_fwhm,
            "with_flux": with_flux,
            "flux_top": flux_top,
        },
        "meta": {
            "rows": len(rows),
            "n_timesteps": summary["n_timesteps"],
            "first_timestep": summary["first_timestep"],
            "last_timestep": summary["last_timestep"],
            "timestep_step": summary["timestep_step"],
            "dt_ps": dt_ps,
            "time_axis": "physical" if timestep_ps is not None else "analyzed_frame",
            "species_scanned": len(max_counts),
            "source_mode": "prepared_index",
            "index": summary["index"],
        },
        "rows": rows,
    }



@dataclass
class MatchedReaction:
    role: str
    reaction: Reaction
    forward_tp: int
    reverse_tp: int
    net_tp: int
    ratio_pct: float


@dataclass
class TrajectoryFrameIndex:
    trajectory_file: str
    mtime: float
    size: int
    frames: list[int]
    frame_offsets: dict[int, tuple[int, int]]


class ReactionSourceChangedError(RuntimeError):
    """Raised when a reaction source changes across one cache read."""


_REACTION_SNAPSHOT_MEMORY_LIMIT = 16 * 1024 * 1024
_REACTION_HASH_CHUNK_SIZE = 1024 * 1024


def _reaction_stat_identity(stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def _capture_reaction_source(
    path: str,
) -> tuple[tempfile.SpooledTemporaryFile[bytes], dict[str, Any]]:
    """Capture and hash one opened reaction source without rereading it.

    The spooled snapshot bounds memory for large ``reactionabcd`` files and is
    the exact byte sequence later parsed by ``NetworkStore``.
    """
    resolved = os.path.abspath(path)
    spool = tempfile.SpooledTemporaryFile(
        max_size=_REACTION_SNAPSHOT_MEMORY_LIMIT,
        mode="w+b",
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with open(resolved, "rb") as source:
            before = os.fstat(source.fileno())
            while True:
                chunk = source.read(_REACTION_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                spool.write(chunk)
                total += len(chunk)
            after = os.fstat(source.fileno())
        if _reaction_stat_identity(before) != _reaction_stat_identity(after):
            raise ReactionSourceChangedError(
                f"reaction file changed while loading: {resolved}"
            )
        spool.seek(0)
        return spool, {
            "path": resolved,
            "size": total,
            "mtime_ns": int(after.st_mtime_ns),
            "sha256": digest.hexdigest(),
        }
    except BaseException:
        spool.close()
        raise


def reaction_source_signature(path: str) -> dict[str, Any]:
    """Return a reproducible content-derived signature for a reaction file."""
    snapshot, signature = _capture_reaction_source(path)
    snapshot.close()
    return signature


def _parse_reaction_source_snapshot(
    snapshot: tempfile.SpooledTemporaryFile[bytes],
    *,
    path: str,
    min_tp: int,
) -> ReactionNetwork:
    """Parse an owned byte snapshot and always close its backing spool."""
    try:
        with io.TextIOWrapper(snapshot, encoding="utf-8") as text_snapshot:
            reactions = parse_reactionabcd(
                text_snapshot,
                min_tp=min_tp,
            )
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"reaction file is not valid UTF-8: {path}"
        ) from exc
    if not reactions:
        raise RuntimeError(f"no reactions loaded from: {path}")
    return ReactionNetwork(reactions)


def load_reaction_network_snapshot(
    reac_file: str,
    min_tp: int,
) -> tuple[ReactionNetwork, dict[str, Any]]:
    """Parse and sign exactly one captured ``reactionabcd`` byte snapshot.

    This uncached public loader is the safe compatibility boundary for
    callers whose injected store does not implement ``get_with_signature``.
    The returned digest can never describe bytes other than those parsed into
    the returned network.
    """
    path = os.path.abspath(reac_file)
    if not os.path.exists(path):
        raise FileNotFoundError(f"reaction file not found: {path}")
    snapshot, captured_signature = _capture_reaction_source(path)
    network = _parse_reaction_source_snapshot(
        snapshot,
        path=path,
        min_tp=min_tp,
    )
    current_signature = reaction_source_signature(path)
    if current_signature["sha256"] != captured_signature["sha256"]:
        raise ReactionSourceChangedError(
            f"reaction file changed while loading: {path}"
        )
    return network, current_signature


class NetworkStore:
    def __init__(self, max_entries: int = 8) -> None:
        self._lock = threading.Lock()
        self._cache: OrderedDict[
            tuple[str, int],
            tuple[str, ReactionNetwork],
        ] = OrderedDict()
        self._max_entries = max(2, int(max_entries))

    def get(self, reac_file: str, min_tp: int) -> ReactionNetwork:
        network, _signature = self.get_with_signature(reac_file, min_tp)
        return network

    def get_with_signature(
        self,
        reac_file: str,
        min_tp: int,
    ) -> tuple[ReactionNetwork, dict[str, Any]]:
        path = os.path.abspath(reac_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"reaction file not found: {path}")
        key = (path, min_tp)

        with self._lock:
            snapshot, signature = _capture_reaction_source(path)
            digest = str(signature["sha256"])
            cached = self._cache.get(key)
            if cached and cached[0] == digest:
                snapshot.close()
                self._cache.move_to_end(key)
                return cached[1], dict(signature)

            net = _parse_reaction_source_snapshot(
                snapshot,
                path=path,
                min_tp=min_tp,
            )
            current_signature = reaction_source_signature(path)
            if current_signature["sha256"] != digest:
                raise ReactionSourceChangedError(
                    f"reaction file changed while loading: {path}"
                )
            self._cache[key] = (digest, net)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            return net, current_signature


STORE = NetworkStore()





TrajectoryIndexStore = PreparedTrajectoryIndexStore
TrajectoryFrameIndex = PreparedTrajectoryFrameIndex
TRAJECTORY_INDEX_STORE = PREPARED_TRAJECTORY_INDEX_STORE

def trajectory_frame_index_path(
    trajectory_file: str,
    *,
    mtime: float | None = None,
    size: int | None = None,
) -> Path:
    del mtime, size
    return prepared_trajectory_index_path(trajectory_file)


def reverse_key(rxn: Reaction) -> str:
    return "+".join(sorted(rxn.product_smiles)) + "->" + "+".join(sorted(rxn.reactant_smiles))


def net_flux(rxn: Reaction, tp_map: dict[str, int]) -> tuple[int, int, int]:
    fwd = rxn.tp
    rev = tp_map.get(reverse_key(rxn), 0)
    return fwd, rev, fwd - rev


def reaction_formula_str(rxn: Reaction) -> str:
    return " + ".join(rxn.reactant_formulas) + " -> " + " + ".join(rxn.product_formulas)


def reaction_smiles_str(rxn: Reaction) -> str:
    return " + ".join(rxn.reactant_smiles) + " -> " + " + ".join(rxn.product_smiles)


def collect_next_reactions(net: ReactionNetwork, smi: str, role: str) -> list[MatchedReaction]:
    tp_map = {r.key: r.tp for r in net.reactions}
    rows: list[MatchedReaction] = []

    if role in {"consume", "both"}:
        total = net.total_consume_tp(smi)
        for rxn in net.consumption_of(smi):
            fwd, rev, nt = net_flux(rxn, tp_map)
            ratio = (rxn.tp / total * 100.0) if total else 0.0
            rows.append(MatchedReaction("consume", rxn, fwd, rev, nt, ratio))

    if role in {"produce", "both"}:
        total = net.total_produce_tp(smi)
        for rxn in net.production_of(smi):
            fwd, rev, nt = net_flux(rxn, tp_map)
            ratio = (rxn.tp / total * 100.0) if total else 0.0
            rows.append(MatchedReaction("produce", rxn, fwd, rev, nt, ratio))

    rows.sort(key=lambda x: (abs(x.net_tp), x.forward_tp), reverse=True)
    return rows


def multiset_contains(have: Counter[str], need: Counter[str]) -> bool:
    for k, v in need.items():
        if have.get(k, 0) < v:
            return False
    return True


def match_formula_reaction(rxn: Reaction, need_r: Counter[str], need_p: Counter[str], mode: str) -> bool:
    have_r = Counter(rxn.reactant_formulas)
    have_p = Counter(rxn.product_formulas)
    if mode == "exact":
        if need_r and have_r != need_r:
            return False
        if need_p and have_p != need_p:
            return False
        return True
    if need_r and not multiset_contains(have_r, need_r):
        return False
    if need_p and not multiset_contains(have_p, need_p):
        return False
    return True


def smiles_to_svg(smiles: str, width: int = 360, height: int = 240, show_h: bool = True) -> str:
    if Chem is None or rdMolDraw2D is None:
        raise RuntimeError("RDKit is not available")
    if show_h:
        parser = Chem.SmilesParserParams()
        parser.removeHs = False
        mol = Chem.MolFromSmiles(smiles, parser)
    else:
        mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        parser = Chem.SmilesParserParams()
        parser.removeHs = not show_h
        parser.sanitize = False
        mol = Chem.MolFromSmiles(smiles, parser)
        if mol is not None:
            mol.UpdatePropertyCache(strict=False)
    if mol is None:
        raise ValueError("invalid smiles")
    rdDepictor.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.addStereoAnnotation = False
    opts.clearBackground = False
    opts.padding = 0.08
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def render_error_svg(message: str, *, width: int, height: int) -> str:
    safe_msg = html.escape(str(message))
    tip = "Check environment: RDKit is required for structure rendering."
    safe_tip = html.escape(tip)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff9f3" stroke="#d9c7b1" />'
        '<text x="16" y="28" font-size="14" font-family="monospace" fill="#8a2d16">SMILES render failed</text>'
        f'<text x="16" y="52" font-size="12" font-family="monospace" fill="#3d3d3d">{safe_msg}</text>'
        f'<text x="16" y="{height - 18}" font-size="11" font-family="monospace" fill="#666">{safe_tip}</text>'
        "</svg>"
    )
