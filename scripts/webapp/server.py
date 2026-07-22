#!/usr/bin/env python3
"""Lightweight web frontend backend for ReacNetGenerator query workflows.

No external web framework required.
"""

from __future__ import annotations

import argparse
import hashlib
import csv
import html
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from bisect import bisect_left, bisect_right
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
TOOL_ROOT = SCRIPTS_DIR.parent
PROJECT_ROOT = TOOL_ROOT.parent

if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))


def _prebootstrap_local_site_packages() -> None:
    venv_lib = TOOL_ROOT / ".venv" / "lib"
    candidates: list[Path] = []
    preferred = venv_lib / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if preferred.exists():
        candidates.append(preferred)
    if venv_lib.exists():
        for site_dir in sorted(venv_lib.glob("python*/site-packages")):
            if site_dir.exists() and site_dir not in candidates:
                candidates.append(site_dir)
    for site_dir in candidates:
        site_text = str(site_dir)
        if site_text not in sys.path:
            sys.path.insert(0, site_text)


_prebootstrap_local_site_packages()

from rng_tools.network import (  # noqa: E402
    Reaction,
    ReactionNetwork,
    export_initiation_csv,
    export_initiation_smiles_branches_csv,
    parse_reactionabcd,
    smiles_to_formula_fast,
)
from rng_tools.io import load_transition_table  # noqa: E402
from rng_tools.observation_network import build_observation_network  # noqa: E402
from rng_tools.formula import formula_exact_mass, formula_nominal_mass  # noqa: E402
from rng_tools.reaction import canonical_smiles  # noqa: E402
from rng_tools.carbon_plot import (  # noqa: E402
    parse_carbon_range_specs,
    parse_formula_to_atom_counts,
    plot_carbon_number_evolution,
    species_file_to_tidy_table,
)


ROUTE_TRANSITION_INDEX_SCHEMA_VERSION = 1
TRAJECTORY_FRAME_INDEX_SCHEMA_VERSION = 1


def _bootstrap_local_site_packages() -> None:
    candidates: list[Path] = []
    venv_lib = TOOL_ROOT / ".venv" / "lib"
    preferred = venv_lib / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if preferred.exists():
        candidates.append(preferred)
    if venv_lib.exists():
        for site_dir in sorted(venv_lib.glob("python*/site-packages")):
            if site_dir not in candidates and site_dir.exists():
                candidates.append(site_dir)
    for site_dir in candidates:
        site_text = str(site_dir)
        if site_text not in sys.path:
            sys.path.insert(0, site_text)


try:
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D
except Exception:  # pragma: no cover
    _bootstrap_local_site_packages()
    try:
        from rdkit import Chem
        from rdkit.Chem import rdDepictor
        from rdkit.Chem.Draw import rdMolDraw2D
    except Exception:
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
            TOOL_ROOT / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
            Path.cwd() / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
        ]
    )
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def detect_default_transition_table() -> Path:
    """Find the bundled RP3 transition matrix when no table is supplied."""

    env_path = os.getenv("RNG_TRANSITION_TABLE", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            TOOL_ROOT / "ref_data" / "rng_rp3_test" / "rp3.lammpstrj.table",
            PROJECT_ROOT / "reacnet-scope" / "ref_data" / "rng_rp3_test" / "rp3.lammpstrj.table",
            Path.cwd() / "ref_data" / "rng_rp3_test" / "rp3.lammpstrj.table",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_REACTION_FILE = detect_default_reaction_file()
DEFAULT_TRANSITION_TABLE = detect_default_transition_table()

FORMULA_RE = re.compile(r"^([A-Z][a-z]?\d*)+$")
SPECIES_TS_PREFIX_RE = re.compile(r"^Timestep\s+(\d+):")
ROUTE_LINE_RE = re.compile(r"^\s*Atom\s+(\d+)\s+\S+:\s*(.*)$")
ROUTE_STEP_RE = re.compile(r"(\d+)\s+(\S+)")

COVALENT_RADII: dict[str, float] = {
    "H": 0.31,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
    "Br": 1.20,
    "I": 1.39,
}


def split_terms(expr: str) -> list[str]:
    parts = re.split(r"\s*[+,;]\s*", expr.strip())
    return [x for x in parts if x]


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


def build_carbon_formula_index(
    source: pd.DataFrame,
    *,
    species_col: str,
    count_col: str,
    system_col: str | None,
    max_formula_list: int,
) -> list[dict[str, Any]]:
    if species_col not in source.columns:
        raise ValueError(f"species column {species_col!r} not found in source table.")
    if count_col not in source.columns:
        raise ValueError(f"count column {count_col!r} not found in source table.")
    if max_formula_list <= 0:
        return []

    working = source.copy()
    working[species_col] = working[species_col].astype(str).str.strip()
    working[count_col] = pd.to_numeric(working[count_col], errors="coerce").fillna(0.0)

    species_values = pd.Index(working[species_col].dropna().drop_duplicates())
    species_to_carbon: dict[str, int] = {}
    parse_errors: list[str] = []
    for species in species_values:
        label = str(species).strip()
        if not label:
            continue
        try:
            atom_counts = parse_formula_to_atom_counts(label)
            species_to_carbon[label] = int(atom_counts.get("C", 0))
        except Exception as exc:
            if len(parse_errors) < 5:
                parse_errors.append(f"{label!r}: {exc}")

    if parse_errors:
        examples = "; ".join(parse_errors)
        raise ValueError(
            "Failed to parse one or more species labels for carbon-formula index. "
            f"Examples: {examples}"
        )

    working["__carbon_number"] = working[species_col].map(species_to_carbon)
    working = working[working["__carbon_number"].notna()].copy()
    if working.empty:
        return []
    working["__carbon_number"] = working["__carbon_number"].astype(int)

    group_prefix: list[str] = []
    if system_col and system_col in working.columns:
        group_prefix = [system_col]

    grouped = (
        working.groupby(group_prefix + ["__carbon_number", species_col], dropna=False, as_index=False)[count_col]
        .sum()
    )
    records: list[dict[str, Any]] = []
    for keys, subset in grouped.groupby(group_prefix + ["__carbon_number"], dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        if group_prefix:
            system_value = keys[0]
            carbon_number = int(keys[1])
        else:
            system_value = None
            carbon_number = int(keys[0])

        ordered = subset.sort_values([count_col, species_col], ascending=[False, True]).reset_index(drop=True)
        species_rows = [
            {
                "species": str(row[species_col]),
                "total_count": float(row[count_col]),
            }
            for _, row in ordered.head(max_formula_list).iterrows()
        ]
        record = {
            "system": None if pd.isna(system_value) else str(system_value),
            "carbon_number": carbon_number,
            "label": f"C{carbon_number}",
            "n_formulae": int(len(ordered)),
            "truncated": bool(len(ordered) > max_formula_list),
            "formulae": species_rows,
        }
        records.append(record)

    records.sort(
        key=lambda item: (
            "" if item.get("system") is None else str(item.get("system")),
            int(item.get("carbon_number", 0)),
        )
    )
    return records


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


def derive_route_path(source_path: str) -> str:
    path = (source_path or "").strip()
    if not path:
        return path
    candidates: list[str] = []
    low = path.lower()
    if low.endswith(".route"):
        candidates.append(path)
    elif low.endswith(".lammpstrj"):
        candidates.append(path + ".route")
    elif low.endswith(".species"):
        base = path[: -len(".species")]
        candidates.append(base + ".route")
    elif low.endswith(".reactionabcd"):
        base = path[: -len(".reactionabcd")]
        candidates.append(base + ".route")
    else:
        candidates.append(path + ".route")
        candidates.append(path)

    deduped: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        key = cand.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)

    for candidate in deduped:
        if candidate.lower().endswith(".route") and os.path.exists(candidate):
            return candidate
    return deduped[0] if deduped else path


def _dataset_base_path(path_text: str) -> str:
    """Return the shared RNG output stem for a known artifact path."""

    path = (path_text or "").strip()
    if not path:
        return ""
    for suffix in (".reactionabcd", ".species", ".route", ".table", ".json", ".html", ".svg"):
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

    directory = Path(directory_text).expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"dataset folder not found: {directory}")
    preferred = (preferred_base or "").strip()
    preferred_resolved = str(Path(preferred).expanduser().resolve()) if preferred else ""

    groups: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in directory.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        kind = ""
        if name.endswith(".reactionabcd"):
            kind = "reaction"
        elif name.endswith(".species"):
            kind = "species"
        elif name.endswith(".route"):
            kind = "route"
        elif name.endswith(".table"):
            kind = "table"
        elif name.endswith(".lammpstrj"):
            kind = "trajectory"
        if kind:
            groups[_dataset_base_path(str(path))][kind] = path

    candidates: list[dict[str, Any]] = []
    for base, files in groups.items():
        candidates.append(
            {
                "base": base,
                "label": Path(base).name,
                "kinds": sorted(files),
                "score": len(files),
                "mtime": max((item.stat().st_mtime for item in files.values()), default=0.0),
            }
        )
    candidates.sort(key=lambda item: (-int(item["score"]), -float(item["mtime"]), str(item["label"])))
    if not candidates:
        return "", {}, []
    chosen = next(
        (
            item
            for item in candidates
            if preferred and str(item["base"]) in {preferred, preferred_resolved}
        ),
        candidates[0],
    )
    for item in candidates:
        item["selected"] = str(item["base"]) == str(chosen["base"])
    selected = {key: str(value) for key, value in groups[str(chosen["base"])].items()}
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
        "route": (params.get("route_file", [""])[0] or "").strip(),
        "table": (params.get("table_file", [""])[0] or "").strip(),
    }
    folder = (params.get("dataset_dir", [""])[0] or "").strip()
    folder_base = ""
    folder_files: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    if folder:
        preferred_base = (params.get("dataset_base", [""])[0] or "").strip()
        folder_base, folder_files, candidates = _scan_rng_dataset_directory(folder, preferred_base=preferred_base)
    seed = next((value for value in explicit.values() if value), folder_base)
    base = _dataset_base_path(seed)
    inferred = {
        "reaction": f"{base}.reactionabcd" if base else "",
        "species": f"{base}.species" if base else "",
        "trajectory": base,
        "route": f"{base}.route" if base else "",
        "table": f"{base}.table" if base else "",
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for key in ("reaction", "species", "trajectory", "route", "table"):
        selected = explicit[key] or folder_files.get(key, "") or inferred[key]
        source = "explicit" if explicit[key] else ("folder" if folder_files.get(key) else "derived")
        artifacts[key] = _dataset_file_descriptor(selected, source=source)

    capabilities = {
        "species": artifacts["reaction"]["exists"],
        "intermediate": artifacts["species"]["exists"],
        "reaction": artifacts["reaction"]["exists"],
        "events": artifacts["species"]["exists"],
        "evolution": artifacts["species"]["exists"],
        "transition": artifacts["table"]["exists"],
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


_SPECIES_TOTALS_CACHE_LOCK = threading.Lock()
_SPECIES_TOTALS_CACHE: OrderedDict[tuple[str, int, int], dict[str, int]] = OrderedDict()
_SPECIES_TOTALS_CACHE_MAX_ENTRIES = 8


def collect_species_totals(
    species_file: str,
    *,
    progress_callback: Any = None,
) -> dict[str, int]:
    path = os.path.abspath(species_file)
    stat = os.stat(path)
    cache_key = (path, stat.st_mtime_ns, stat.st_size)
    with _SPECIES_TOTALS_CACHE_LOCK:
        cached = _SPECIES_TOTALS_CACHE.get(cache_key)
        if cached is not None:
            _SPECIES_TOTALS_CACHE.move_to_end(cache_key)
            if progress_callback is not None:
                progress_callback(
                    {
                        "progress": 1.0,
                        "message": f"Using cached species catalog: {os.path.basename(path)}",
                        "timesteps": None,
                    }
                )
            return cached

    totals: dict[str, int] = {}
    file_size = max(stat.st_size, 1)
    bytes_read = 0
    last_emit = 0.0
    timesteps = 0

    if progress_callback is not None:
        progress_callback(
            {
                "progress": 0.0,
                "message": f"Scanning species catalog: {os.path.basename(path)}",
                "timesteps": 0,
            }
        )

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            timesteps += 1
            for smi, cnt in pairs:
                totals[smi] = totals.get(smi, 0) + int(cnt)

            frac = bytes_read / file_size
            now = time.monotonic()
            if progress_callback is not None and (frac >= 0.99 or (now - last_emit) >= 1.0):
                progress_callback(
                    {
                        "progress": max(0.0, min(float(frac), 1.0)),
                        "message": f"Scanning species catalog: {frac * 100:.1f}%",
                        "timesteps": timesteps,
                        "frame": ts,
                    }
                )
                last_emit = now

    with _SPECIES_TOTALS_CACHE_LOCK:
        _SPECIES_TOTALS_CACHE[cache_key] = totals
        _SPECIES_TOTALS_CACHE.move_to_end(cache_key)
        while len(_SPECIES_TOTALS_CACHE) > _SPECIES_TOTALS_CACHE_MAX_ENTRIES:
            _SPECIES_TOTALS_CACHE.popitem(last=False)
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
    ts_re = re.compile(r"^Timestep\s+(\d+):(.*)$")
    selected = list(dict.fromkeys(selected_smiles))
    selected_set = set(selected)
    series: dict[str, list[int]] = {s: [] for s in selected}
    timesteps: list[int] = []
    file_size = max(os.path.getsize(species_file), 1)
    bytes_read = 0
    last_emit = 0.0

    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            m = ts_re.match(line.strip())
            if not m:
                continue
            ts = int(m.group(1))
            timesteps.append(ts)
            for s in selected:
                series[s].append(0)

            tokens = m.group(2).strip().split()
            i = 0
            while i < len(tokens) - 1:
                smi = tokens[i]
                try:
                    cnt = int(tokens[i + 1])
                except ValueError:
                    i += 1
                    continue
                if smi in selected_set:
                    series[smi][-1] += cnt
                i += 2

            frac = bytes_read / file_size
            now = time.monotonic()
            if progress_callback is not None and (frac >= 0.99 or (now - last_emit) >= 1.0):
                progress_callback(
                    {
                        "progress": max(0.0, min(float(frac), 1.0)),
                        "timesteps": len(timesteps),
                        "frame": ts,
                    }
                )
                last_emit = now

    return timesteps, series


def parse_species_timestep_line(line: str) -> tuple[int, list[tuple[str, int]]] | None:
    m = re.match(r"^Timestep\s+(\d+):(.*)$", line.strip())
    if not m:
        return None
    ts = int(m.group(1))
    tokens = m.group(2).strip().split()
    pairs: list[tuple[str, int]] = []
    i = 0
    while i < len(tokens) - 1:
        smi = tokens[i]
        try:
            cnt = int(tokens[i + 1])
        except ValueError:
            i += 1
            continue
        pairs.append((smi, cnt))
        i += 2
    return ts, pairs


def parse_species_timestep_only(line: str) -> int | None:
    m = SPECIES_TS_PREFIX_RE.match(line.strip())
    if not m:
        return None
    return int(m.group(1))


def collect_species_timestep_index(
    species_file: str,
    *,
    progress_callback: Any = None,
) -> list[int]:
    index = SPECIES_FRAME_INDEX_STORE.get(
        species_file,
        progress_callback=progress_callback,
        progress_start=0.02,
        progress_span=0.40,
    )
    return list(index.frames)


def collect_trajectory_timestep_index(
    trajectory_file: str,
    *,
    progress_callback: Any = None,
) -> list[int]:
    index = TRAJECTORY_INDEX_STORE.get(
        trajectory_file,
        progress_callback=progress_callback,
        progress_start=0.02,
        progress_span=0.40,
    )
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


def collect_species_occurrence_stats(
    species_file: str,
    selected_smiles: list[str],
) -> dict[str, dict[str, Any]]:
    selected = [item for item in dict.fromkeys(selected_smiles or []) if item]
    if not selected:
        return {}
    selected_set = set(selected)
    stats: dict[str, dict[str, Any]] = {
        smi: {
            "first_frame": None,
            "last_frame": None,
            "peak_frame": None,
            "peak_count": 0,
            "n_nonzero_frames": 0,
            "active_windows_raw": [],
            "_open_start": None,
            "_last_nonzero": None,
        }
        for smi in selected
    }

    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            counts = {smi: 0 for smi in selected}
            for smi, cnt in pairs:
                if smi in selected_set:
                    counts[smi] += int(cnt)

            for smi in selected:
                item = stats[smi]
                count = counts.get(smi, 0)
                if count > 0:
                    if item["first_frame"] is None:
                        item["first_frame"] = ts
                    item["last_frame"] = ts
                    item["n_nonzero_frames"] += 1
                    if count > int(item["peak_count"]):
                        item["peak_count"] = count
                        item["peak_frame"] = ts
                    if item["_open_start"] is None:
                        item["_open_start"] = ts
                    item["_last_nonzero"] = ts
                elif item["_open_start"] is not None:
                    item["active_windows_raw"].append((int(item["_open_start"]), int(item["_last_nonzero"])))
                    item["_open_start"] = None
                    item["_last_nonzero"] = None

    for smi, item in stats.items():
        if item["_open_start"] is not None:
            item["active_windows_raw"].append((int(item["_open_start"]), int(item["_last_nonzero"])))
        windows = list(item["active_windows_raw"])
        item["active_windows"] = format_frame_windows(windows)
        item["n_active_windows"] = len(windows)
        item.pop("active_windows_raw", None)
        item.pop("_open_start", None)
        item.pop("_last_nonzero", None)
    return stats


def resolve_context_match_mode(target: str, match_mode: str) -> str:
    mode = (match_mode or "auto").strip().lower()
    if mode not in {"auto", "smiles", "formula"}:
        mode = "auto"
    if mode == "auto":
        return "formula" if looks_like_formula(target) else "smiles"
    return mode


def build_context_matcher(target: str, match_mode: str) -> tuple[str, Any]:
    mode = resolve_context_match_mode(target, match_mode)
    query = target.strip()
    if mode == "smiles":
        return mode, lambda smi: smi == query

    formula_cache: dict[str, str] = {}

    def _matches_formula(smi: str) -> bool:
        cached = formula_cache.get(smi)
        if cached is None:
            try:
                cached = smiles_to_formula_fast(smi)
            except Exception:
                cached = ""
            formula_cache[smi] = cached
        return cached == query

    return mode, _matches_formula


def parse_reaction_smiles_query(text: str) -> tuple[list[str], list[str]]:
    raw = (text or "").strip()
    if not raw:
        return [], []
    if "->" not in raw:
        return [], []
    left, right = raw.split("->", 1)
    reactants = [item for item in split_terms(left) if item]
    products = [item for item in split_terms(right) if item]
    return reactants, products


def build_smiles_or_formula_matcher(items: list[str]) -> Any:
    exact_smiles: set[str] = set()
    formula_set: set[str] = set()
    for raw in items:
        token = (raw or "").strip()
        if not token:
            continue
        if looks_like_formula(token):
            formula_set.add(token)
        else:
            exact_smiles.add(token)

    def _match(smi: str) -> bool:
        if not smi:
            return False
        if smi in exact_smiles:
            return True
        if formula_set:
            return smiles_formula_cached(smi) in formula_set
        return False

    return _match


@lru_cache(maxsize=400000)
def _normalize_route_species_label(token: str) -> tuple[str, str]:
    raw = str(token or "").strip()
    if not raw:
        return "", ""
    canonical = canonical_smiles(raw) or ""
    formula = raw if looks_like_formula(raw) else ""
    if not formula:
        formula = smiles_formula_cached(canonical or raw) or ""
    return canonical, formula


def _prepare_reaction_query(reaction_text: str) -> dict[str, Any]:
    raw = str(reaction_text or "").strip()
    reactants_raw, products_raw = parse_reaction_smiles_query(raw)
    if not reactants_raw or not products_raw:
        raise ValueError("missing reaction expression: expected 'A + B -> C + D'")

    reactant_can: list[str] = []
    product_can: list[str] = []
    can_ok = True
    for token in reactants_raw:
        if looks_like_formula(token):
            can_ok = False
            break
        can = canonical_smiles(token)
        if not can:
            can_ok = False
            break
        reactant_can.append(can)
    if can_ok:
        for token in products_raw:
            if looks_like_formula(token):
                can_ok = False
                break
            can = canonical_smiles(token)
            if not can:
                can_ok = False
                break
            product_can.append(can)

    reactant_formula: list[str] = []
    product_formula: list[str] = []
    if can_ok:
        match_mode = "canonical_smiles"
        reactant_tokens = reactant_can
        product_tokens = product_can
    else:
        for token in reactants_raw:
            if looks_like_formula(token):
                reactant_formula.append(token)
                continue
            formula = smiles_formula_cached(token) or ""
            if not formula:
                raise ValueError(f"reaction token cannot be canonicalized or reduced to formula: {token}")
            reactant_formula.append(formula)
        for token in products_raw:
            if looks_like_formula(token):
                product_formula.append(token)
                continue
            formula = smiles_formula_cached(token) or ""
            if not formula:
                raise ValueError(f"reaction token cannot be canonicalized or reduced to formula: {token}")
            product_formula.append(formula)
        match_mode = "formula"
        reactant_tokens = reactant_formula
        product_tokens = product_formula

    reaction_signature = (
        f"{match_mode}|"
        f"{'+'.join(sorted(str(item) for item in reactant_tokens))}"
        f"->"
        f"{'+'.join(sorted(str(item) for item in product_tokens))}"
    )
    return {
        "raw": raw,
        "match_mode": match_mode,
        "reactants_raw": reactants_raw,
        "products_raw": products_raw,
        "reactant_tokens": tuple(sorted(str(item) for item in reactant_tokens)),
        "product_tokens": tuple(sorted(str(item) for item in product_tokens)),
        "reactant_token_set": set(str(item) for item in reactant_tokens),
        "product_token_set": set(str(item) for item in product_tokens),
        "reaction_signature": reaction_signature,
    }


def _reaction_query_token_order(reaction_query: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in [*reaction_query["reactant_tokens"], *reaction_query["product_tokens"]]:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _format_token_counter_summary(
    counter: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> str:
    if not ordered_tokens:
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    for token in ordered_tokens:
        if token in seen:
            continue
        seen.add(token)
        parts.append(f"{token}({int(counter.get(token, 0))})")
    extras = sorted(
        token
        for token, value in counter.items()
        if token not in seen and int(value) != 0
    )
    for token in extras:
        parts.append(f"{token}({int(counter.get(token, 0))})")
    return "; ".join(parts)


def _format_token_delta_summary(
    before_counter: dict[str, int] | Counter[str],
    after_counter: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for token in ordered_tokens:
        if token in seen:
            continue
        seen.add(token)
        before_value = int(before_counter.get(token, 0))
        after_value = int(after_counter.get(token, 0))
        delta = after_value - before_value
        if delta == 0:
            continue
        parts.append(f"{token}: {before_value}->{after_value} ({delta:+d})")
    if not parts:
        return "no net species change"
    return "; ".join(parts)


def _reaction_query_expected_delta(reaction_query: dict[str, Any]) -> Counter[str]:
    delta: Counter[str] = Counter()
    for token in reaction_query["reactant_tokens"]:
        delta[str(token)] -= 1
    for token in reaction_query["product_tokens"]:
        delta[str(token)] += 1
    return delta


def _observed_counter_delta(
    before_counter: dict[str, int] | Counter[str],
    after_counter: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> Counter[str]:
    delta: Counter[str] = Counter()
    for token in ordered_tokens:
        before_value = int(before_counter.get(token, 0))
        after_value = int(after_counter.get(token, 0))
        diff = after_value - before_value
        if diff:
            delta[str(token)] = int(diff)
    return delta


def _format_expected_delta_summary(
    expected_delta: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for token in ordered_tokens:
        if token in seen:
            continue
        seen.add(token)
        diff = int(expected_delta.get(token, 0))
        if diff:
            parts.append(f"{token}: {diff:+d}")
    if not parts:
        return "no expected query-token change"
    return "; ".join(parts)


def _counters_match_exact(
    expected_delta: dict[str, int] | Counter[str],
    observed_delta: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> bool:
    token_union = set(ordered_tokens) | set(expected_delta.keys()) | set(observed_delta.keys())
    return all(int(expected_delta.get(token, 0)) == int(observed_delta.get(token, 0)) for token in token_union)


def _delta_scalar_multiple(
    expected_delta: dict[str, int] | Counter[str],
    observed_delta: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> int | None:
    token_union = set(ordered_tokens) | set(expected_delta.keys()) | set(observed_delta.keys())
    factor: int | None = None
    for token in token_union:
        expected_value = int(expected_delta.get(token, 0))
        observed_value = int(observed_delta.get(token, 0))
        if expected_value == 0:
            if observed_value != 0:
                return None
            continue
        if observed_value == 0:
            return None
        if observed_value % expected_value != 0:
            return None
        current_factor = observed_value // expected_value
        if current_factor <= 0:
            return None
        if factor is None:
            factor = current_factor
            continue
        if current_factor != factor:
            return None
    return factor


def _has_reverse_query_delta(
    expected_delta: dict[str, int] | Counter[str],
    observed_delta: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> bool:
    for token in ordered_tokens:
        expected_value = int(expected_delta.get(token, 0))
        observed_value = int(observed_delta.get(token, 0))
        if expected_value == 0 or observed_value == 0:
            continue
        if expected_value * observed_value < 0:
            return True
    return False


def _is_partial_same_direction(
    expected_delta: dict[str, int] | Counter[str],
    observed_delta: dict[str, int] | Counter[str],
    ordered_tokens: list[str],
) -> bool:
    saw_relevant = False
    for token in ordered_tokens:
        expected_value = int(expected_delta.get(token, 0))
        observed_value = int(observed_delta.get(token, 0))
        if observed_value == 0:
            continue
        if expected_value == 0:
            return False
        if expected_value * observed_value < 0:
            return False
        if abs(observed_value) > abs(expected_value):
            return False
        saw_relevant = True
    if not saw_relevant:
        return False
    return not _counters_match_exact(expected_delta, observed_delta, ordered_tokens)


def _collect_reaction_species_token_snapshots(
    species_file: str,
    *,
    requested_frames: list[int],
    query_tokens: list[str],
    match_mode: str,
    progress_callback: Any = None,
    progress_start: float = 0.0,
    progress_span: float = 1.0,
) -> dict[int, dict[str, int]]:
    wanted = sorted({int(frame) for frame in requested_frames if frame is not None})
    if not wanted:
        return {}
    token_set = {str(token or "").strip() for token in query_tokens if str(token or "").strip()}
    if not token_set:
        return {int(frame): {} for frame in wanted}

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "reading_species",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    file_size = max(os.path.getsize(species_file), 1)
    bytes_read = 0
    last_emit = 0.0
    wanted_set = set(wanted)
    snapshots: dict[int, dict[str, int]] = {}
    emit(progress_start, f"Scanning species snapshots for {len(wanted)} comparison frame(s)")
    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            if ts not in wanted_set:
                now = time.monotonic()
                frac = bytes_read / file_size
                if frac >= 0.99 or (now - last_emit) >= 1.0:
                    emit(
                        progress_start + progress_span * min(frac, 1.0),
                        f"Scanning species file: {frac * 100:.1f}%",
                        n_snapshots=len(snapshots),
                    )
                    last_emit = now
                continue
            counts: Counter[str] = Counter()
            for label, count in pairs:
                canonical, formula = _normalize_route_species_label(label)
                token = canonical if match_mode == "canonical_smiles" else formula
                if token and token in token_set:
                    counts[token] += int(count)
            snapshots[int(ts)] = {token: int(counts.get(token, 0)) for token in token_set}
            wanted_set.discard(ts)
            now = time.monotonic()
            frac = bytes_read / file_size
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    progress_start + progress_span * min(frac, 1.0),
                    f"Collecting species comparison frame {ts}",
                    frame=ts,
                    n_snapshots=len(snapshots),
                )
                last_emit = now
            if not wanted_set:
                break
    for frame in wanted:
        snapshots.setdefault(int(frame), {token: 0 for token in token_set})
    emit(
        progress_start + progress_span,
        f"Species comparison snapshots ready: {len(snapshots)} frame(s)",
        n_snapshots=len(snapshots),
    )
    return snapshots


def _classify_reaction_candidate_rows(
    candidate_rows: list[dict[str, Any]],
    *,
    reaction_query: dict[str, Any],
    species_snapshots: dict[int, dict[str, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted_rows: list[dict[str, Any]] = []
    candidate_process_rows: list[dict[str, Any]] = []
    discarded_rows: list[dict[str, Any]] = []
    ordered_tokens = _reaction_query_token_order(reaction_query)
    expected_delta = _reaction_query_expected_delta(reaction_query)
    expected_delta_summary = _format_expected_delta_summary(expected_delta, ordered_tokens)

    for candidate in candidate_rows:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("event_id") or "").strip()
        before_frame = int(candidate.get("comparison_before_frame", candidate.get("route_event_start_frame", 0)))
        after_frame = int(candidate.get("comparison_after_frame", candidate.get("anchor_frame", 0)))
        before_counter = Counter(species_snapshots.get(before_frame) or {})
        after_counter = Counter(species_snapshots.get(after_frame) or {})
        observed_delta = _observed_counter_delta(before_counter, after_counter, ordered_tokens)
        observed_delta_summary = _format_expected_delta_summary(observed_delta, ordered_tokens)
        from_summary = _format_token_counter_summary(before_counter, ordered_tokens)
        to_summary = _format_token_counter_summary(after_counter, ordered_tokens)
        net_summary = _format_token_delta_summary(before_counter, after_counter, ordered_tokens)

        row = dict(candidate)
        row["candidate_id"] = candidate_id
        row["comparison_before_frame"] = before_frame
        row["comparison_after_frame"] = after_frame
        row["from_multiset_summary"] = from_summary
        row["to_multiset_summary"] = to_summary
        row["net_reaction_summary"] = net_summary
        row["expected_delta_summary"] = expected_delta_summary
        row["observed_delta_summary"] = observed_delta_summary
        row["route_species_forward_score"] = int(
            sum(
                max(0, int(after_counter.get(token, 0)) - int(before_counter.get(token, 0)))
                if int(expected_delta.get(token, 0)) > 0
                else (
                    max(0, int(before_counter.get(token, 0)) - int(after_counter.get(token, 0)))
                    if int(expected_delta.get(token, 0)) < 0
                    else 0
                )
                for token in ordered_tokens
            )
        )
        row["route_species_reverse_score"] = int(
            sum(
                max(0, int(before_counter.get(token, 0)) - int(after_counter.get(token, 0)))
                if int(expected_delta.get(token, 0)) > 0
                else (
                    max(0, int(after_counter.get(token, 0)) - int(before_counter.get(token, 0)))
                    if int(expected_delta.get(token, 0)) < 0
                    else 0
                )
                for token in ordered_tokens
            )
        )

        route_confidence = round(float(row.get("route_confidence", row.get("confidence", 0.0)) or 0.0), 3)
        row["route_confidence"] = route_confidence
        row["confidence"] = route_confidence

        exact_match = _counters_match_exact(expected_delta, observed_delta, ordered_tokens)
        multiplicity = _delta_scalar_multiple(expected_delta, observed_delta, ordered_tokens)
        reverse_direction = _has_reverse_query_delta(expected_delta, observed_delta, ordered_tokens)
        partial_same_direction = _is_partial_same_direction(expected_delta, observed_delta, ordered_tokens)
        has_any_observed_change = any(int(observed_delta.get(token, 0)) != 0 for token in ordered_tokens)
        trajectory_sampling_status = str(row.get("trajectory_sampling_status", "sparse") or "sparse")
        context_mode = str(row.get("context_reconstruction_mode", row.get("route_context_atom_source", "")) or "")
        visualization_ready = bool(row.get("visualization_ready", False))
        context_ok = context_mode in {"same_molecule_union", "connected_component_union"}

        verification_status = "candidate_partial"
        failure_reason = ""
        event_quality = "candidate_partial"
        reaction_confidence = 0.58
        row_class = "candidate"

        if not has_any_observed_change:
            verification_status = "discarded_zero_net_change"
            failure_reason = "net_reaction_zero"
            event_quality = "discarded_zero_net_change"
            reaction_confidence = 0.0
            row_class = "discarded"
        elif reverse_direction or int(row.get("route_species_reverse_score", 0)) > 0:
            verification_status = "candidate_direction_mismatch"
            failure_reason = "direction_mismatch"
            event_quality = "discarded_direction_mismatch"
            reaction_confidence = 0.08
            row_class = "discarded"
        elif exact_match:
            reaction_confidence = 0.99
            if trajectory_sampling_status != "good":
                verification_status = "candidate_sampling_sparse"
                failure_reason = "sampling_sparse"
                event_quality = "candidate_sampling_sparse"
            elif not context_ok or not visualization_ready:
                verification_status = "candidate_context_fallback"
                failure_reason = "context_fallback"
                event_quality = "candidate_context_fallback"
            else:
                verification_status = "verified_exact"
                failure_reason = ""
                event_quality = "verified_exact"
                row_class = "verified"
        elif multiplicity and multiplicity != 1:
            verification_status = "candidate_multiplicity"
            failure_reason = "multiplicity"
            event_quality = "candidate_multiplicity"
            reaction_confidence = min(0.92, 0.72 + 0.04 * min(int(multiplicity), 4))
        elif partial_same_direction:
            verification_status = "candidate_partial"
            failure_reason = "partial"
            event_quality = "candidate_partial"
            reaction_confidence = 0.58
        else:
            verification_status = "discarded_unmatched"
            failure_reason = "no_query_token_change"
            event_quality = "discarded_no_query_token_change"
            reaction_confidence = 0.03
            row_class = "discarded"

        row["verification_status"] = verification_status
        row["failure_reason"] = failure_reason
        row["event_quality"] = event_quality
        row["reaction_confidence"] = round(reaction_confidence, 3)
        row["confidence"] = round(reaction_confidence, 3)
        row["selected_event_class"] = row_class
        row["visualization_ready"] = bool(verification_status == "verified_exact" and visualization_ready)
        row["step2_extractable"] = (
            row_class in {"verified", "candidate"}
            and bool(row.get("window_frames"))
            and bool(row.get("context_atom_ids"))
        )
        row["step2_visualizable"] = bool(row["visualization_ready"])
        row["event_resolution"] = (
            "verified_reaction"
            if row_class == "verified"
            else ("candidate_process" if row_class == "candidate" else "discarded_event")
        )
        row["event_resolution_label"] = (
            "严格反应事件"
            if row_class == "verified"
            else ("相关候选过程" if row_class == "candidate" else "已拒绝")
        )
        if row_class == "verified":
            row["event_resolution_reason"] = "前后净化学计量恰好等于 1 次查询反应，且真实轨迹采样与分子上下文恢复满足可视化要求"
        elif verification_status == "candidate_sampling_sparse":
            row["event_resolution_reason"] = "净变化与查询反应一致，但事件核附近缺少足够真实轨迹帧，不能作为主流程可视化事件"
        elif verification_status == "candidate_context_fallback":
            row["event_resolution_reason"] = "净变化与查询反应一致，但上下文原子恢复落入 fallback，不能作为严格反应事件"
        elif verification_status == "candidate_multiplicity":
            row["event_resolution_reason"] = "观察到的净变化是查询反应的整数倍，不是单次严格反应事件"
        elif verification_status == "candidate_partial":
            row["event_resolution_reason"] = "只观察到与查询反应同方向的部分净变化，可作为候选过程人工核查"
        elif failure_reason == "direction_mismatch":
            row["event_resolution_reason"] = "query tokens 的净变化方向与查询反应不一致，已拒绝"
        else:
            row["event_resolution_reason"] = "该候选没有形成可接受的查询反应净变化，已拒绝"

        if row_class == "verified":
            accepted_rows.append(row)
        elif row_class == "candidate":
            candidate_process_rows.append(row)
        else:
            discarded_rows.append(row)

    accepted_rows.sort(
        key=lambda row: (
            -float(row.get("reaction_confidence", 0.0) or 0.0),
            float(row.get("route_confidence", 0.0) or 0.0) * -1.0,
            int(row.get("route_event_start_frame", row.get("anchor_frame", 0))),
            int(row.get("anchor_frame", 0)),
        )
    )
    candidate_process_rows.sort(
        key=lambda row: (
            str(row.get("verification_status", "")),
            -float(row.get("reaction_confidence", 0.0) or 0.0),
            int(row.get("route_event_start_frame", row.get("anchor_frame", 0))),
            int(row.get("anchor_frame", 0)),
        )
    )
    discarded_rows.sort(
        key=lambda row: (
            str(row.get("verification_status", "")),
            int(row.get("route_event_start_frame", row.get("anchor_frame", 0))),
            int(row.get("anchor_frame", 0)),
        )
    )
    for idx, row in enumerate(accepted_rows, 1):
        row["event_index"] = idx
    for idx, row in enumerate(candidate_process_rows, 1):
        row["candidate_index"] = idx
    for idx, row in enumerate(discarded_rows, 1):
        row["candidate_index"] = idx
    return accepted_rows, candidate_process_rows, discarded_rows


def _event_ids_text(atom_ids: set[int] | list[int] | tuple[int, ...]) -> str:
    values = sorted({int(atom_id) for atom_id in atom_ids if atom_id is not None})
    return ",".join(str(item) for item in values)


def _infer_frame_step(frames: list[int]) -> int:
    if len(frames) < 2:
        return 1
    diffs = [int(frames[idx + 1]) - int(frames[idx]) for idx in range(len(frames) - 1)]
    positive = sorted(diff for diff in diffs if diff > 0)
    if not positive:
        return 1
    return positive[len(positive) // 2]


def _nearest_available_frame(frames: list[int], target_frame: int) -> int:
    if not frames:
        return int(target_frame)
    pos = bisect_left(frames, int(target_frame))
    if pos <= 0:
        return int(frames[0])
    if pos >= len(frames):
        return int(frames[-1])
    left = int(frames[pos - 1])
    right = int(frames[pos])
    return left if abs(left - int(target_frame)) <= abs(right - int(target_frame)) else right


def _next_available_frame(frames: list[int], target_frame: int) -> int:
    if not frames:
        return int(target_frame)
    pos = bisect_left(frames, int(target_frame))
    if pos >= len(frames):
        return int(frames[-1])
    return int(frames[pos])


def _previous_available_frame_strict(frames: list[int], target_frame: int) -> int:
    if not frames:
        return int(target_frame)
    pos = bisect_left(frames, int(target_frame))
    if pos <= 0:
        return int(frames[0])
    return int(frames[pos - 1])


def _next_available_frame_strict(frames: list[int], target_frame: int) -> int:
    if not frames:
        return int(target_frame)
    pos = bisect_right(frames, int(target_frame))
    if pos >= len(frames):
        return int(frames[-1])
    return int(frames[pos])


def _expand_event_window_frames(
    frames: list[int],
    *,
    route_event_start_frame: int,
    route_event_end_frame: int,
    before_frames: int,
    after_frames: int,
) -> list[int]:
    if not frames:
        return [int(route_event_start_frame), int(route_event_end_frame)]
    start_pos = bisect_left(frames, int(route_event_start_frame))
    if start_pos >= len(frames):
        start_pos = len(frames) - 1
    elif int(frames[start_pos]) > int(route_event_start_frame) and start_pos > 0:
        start_pos -= 1
    end_pos = bisect_left(frames, int(route_event_end_frame))
    if end_pos >= len(frames):
        end_pos = len(frames) - 1
    start_idx = max(0, int(start_pos) - max(0, int(before_frames)))
    end_idx = min(len(frames) - 1, int(end_pos) + max(0, int(after_frames)))
    return [int(frame) for frame in frames[start_idx : end_idx + 1]]


def _nearest_or_previous_available_frame(frames: list[int], target_frame: int) -> int:
    if not frames:
        return int(target_frame)
    pos = bisect_right(frames, int(target_frame))
    if pos <= 0:
        return int(frames[0])
    return int(frames[pos - 1])


def _select_trajectory_event_frames(
    trajectory_frames: list[int],
    *,
    route_event_start_frame: int,
    route_event_end_frame: int,
    before_padding: int,
    after_padding: int,
) -> dict[str, Any]:
    route_start = int(route_event_start_frame)
    route_end = int(route_event_end_frame)
    if not trajectory_frames:
        return {
            "trajectory_pre_frame": route_start,
            "trajectory_anchor_frame": route_end,
            "trajectory_post_frame": route_end,
            "trajectory_window_frames": [],
            "trajectory_window_start": route_start,
            "trajectory_window_end": route_end,
            "trajectory_sampling_status": "sparse",
            "trajectory_storyboard_ready": False,
        }

    start_insert = bisect_left(trajectory_frames, route_start)
    end_insert = bisect_right(trajectory_frames, route_end)
    midpoint = int(round((route_start + route_end) / 2.0))
    if start_insert < end_insert:
        in_window = trajectory_frames[start_insert:end_insert]
        anchor_frame = min(in_window, key=lambda frame: abs(int(frame) - midpoint))
    else:
        anchor_frame = _nearest_available_frame(trajectory_frames, midpoint)

    pre_frame = _previous_available_frame_strict(trajectory_frames, route_start)
    if pre_frame >= anchor_frame:
        pre_frame = _previous_available_frame_strict(trajectory_frames, anchor_frame)

    post_frame = _next_available_frame_strict(trajectory_frames, route_end)
    if post_frame <= anchor_frame:
        post_frame = _next_available_frame_strict(trajectory_frames, anchor_frame)

    try:
        pre_idx = trajectory_frames.index(pre_frame)
    except ValueError:
        pre_idx = max(0, bisect_left(trajectory_frames, pre_frame) - 1)
    try:
        anchor_idx = trajectory_frames.index(anchor_frame)
    except ValueError:
        anchor_idx = min(len(trajectory_frames) - 1, bisect_left(trajectory_frames, anchor_frame))
    try:
        post_idx = trajectory_frames.index(post_frame)
    except ValueError:
        post_idx = min(len(trajectory_frames) - 1, bisect_left(trajectory_frames, post_frame))

    window_start_idx = max(0, min(pre_idx, anchor_idx, post_idx) - max(0, int(before_padding)))
    window_end_idx = min(
        len(trajectory_frames) - 1,
        max(pre_idx, anchor_idx, post_idx) + max(0, int(after_padding)),
    )
    window_frames = [int(frame) for frame in trajectory_frames[window_start_idx : window_end_idx + 1]]
    distinct_kernel_frames = len({int(pre_frame), int(anchor_frame), int(post_frame)})
    storyboard_ready = len(window_frames) >= 5
    sampling_status = "good" if distinct_kernel_frames >= 3 and storyboard_ready else "sparse"
    return {
        "trajectory_pre_frame": int(pre_frame),
        "trajectory_anchor_frame": int(anchor_frame),
        "trajectory_post_frame": int(post_frame),
        "trajectory_window_frames": window_frames,
        "trajectory_window_start": int(window_frames[0]) if window_frames else int(pre_frame),
        "trajectory_window_end": int(window_frames[-1]) if window_frames else int(post_frame),
        "trajectory_sampling_status": sampling_status,
        "trajectory_storyboard_ready": storyboard_ready,
    }


def _distance_cluster_atom_ids(
    atoms_by_id: dict[int, dict[str, Any]],
    box: list[tuple[float, float]],
    atom_ids: set[int],
    *,
    cutoff: float = 6.0,
) -> list[set[int]]:
    groups, _mode = _candidate_groups_by_distance(atoms_by_id, atom_ids, box, cutoff=cutoff)
    normalized = [set(group) for group in groups if group]
    return normalized or [set(atom_ids)]


def _resolve_atom_element(
    atom: dict[str, Any],
    type_element_map: dict[str, str] | None = None,
) -> str:
    element = str(atom.get("element", "") or "").strip()
    if element:
        return element[0].upper() + element[1:].lower()
    atom_type = _normalize_atom_type_token(atom.get("type", ""))
    if type_element_map and atom_type:
        mapped = str(type_element_map.get(atom_type, "") or "").strip()
        if mapped:
            return mapped[0].upper() + mapped[1:].lower()
    return ""


def _bond_cutoff_sq(
    atom_a: dict[str, Any],
    atom_b: dict[str, Any],
    box: list[tuple[float, float]],
    *,
    type_element_map: dict[str, str] | None = None,
) -> float | None:
    element_a = _resolve_atom_element(atom_a, type_element_map)
    element_b = _resolve_atom_element(atom_b, type_element_map)
    radius_a = COVALENT_RADII.get(element_a)
    radius_b = COVALENT_RADII.get(element_b)
    if radius_a is None or radius_b is None:
        return None
    cutoff = 1.25 * (float(radius_a) + float(radius_b)) + 0.25
    cutoff = min(max(cutoff, 0.85), 2.35)
    return cutoff * cutoff


def _expand_connected_component_atom_ids(
    atoms_by_id: dict[int, dict[str, Any]],
    box: list[tuple[float, float]],
    seed_ids: set[int],
    *,
    type_element_map: dict[str, str] | None = None,
    max_passes: int = 12,
) -> tuple[set[int], str]:
    available_seed = {int(atom_id) for atom_id in seed_ids if atom_id in atoms_by_id}
    if not available_seed:
        return set(), "none"

    mol_ids: set[str] = set()
    for atom_id in available_seed:
        mol = str(atoms_by_id[atom_id].get("mol", "") or "").strip()
        if mol and mol not in {"0", "0.0"}:
            mol_ids.add(mol)
    if mol_ids:
        full_group = {
            int(atom_id)
            for atom_id, atom in atoms_by_id.items()
            if str(atom.get("mol", "") or "").strip() in mol_ids
        }
        return full_group or set(available_seed), "same_molecule"

    topology_ready = any(
        _resolve_atom_element(atom, type_element_map)
        for atom in atoms_by_id.values()
    )

    group = set(available_seed)
    frontier = set(available_seed)
    remaining = {int(atom_id) for atom_id in atoms_by_id.keys() if atom_id not in group}
    inferred_any = False
    for _ in range(max(1, int(max_passes))):
        if not frontier or not remaining:
            break
        new_frontier: set[int] = set()
        frontier_atoms = [atoms_by_id[atom_id] for atom_id in sorted(frontier) if atom_id in atoms_by_id]
        if not frontier_atoms:
            break
        for atom_id in list(remaining):
            atom = atoms_by_id.get(atom_id)
            if atom is None:
                remaining.discard(atom_id)
                continue
            for front_atom in frontier_atoms:
                cutoff_sq = _bond_cutoff_sq(atom, front_atom, box, type_element_map=type_element_map)
                if cutoff_sq is None:
                    continue
                if _distance_sq_pbc(atom, front_atom, box) <= cutoff_sq:
                    new_frontier.add(atom_id)
                    break
        if not new_frontier:
            break
        inferred_any = True
        group.update(new_frontier)
        frontier = new_frontier
        remaining.difference_update(new_frontier)

    if inferred_any:
        return group, "connected_component"
    if not topology_ready:
        return set(available_seed), "missing_topology"
    return set(available_seed), "core_only"


def _expand_reaction_context_atom_ids(
    atoms_by_id: dict[int, dict[str, Any]],
    box: list[tuple[float, float]],
    core_ids: set[int],
    *,
    type_element_map: dict[str, str] | None = None,
) -> tuple[set[int], str]:
    if not core_ids:
        return set(), "none"
    available_core = {atom_id for atom_id in core_ids if atom_id in atoms_by_id}
    if not available_core:
        return set(core_ids), "core_only"

    connected_ids, connected_source = _expand_connected_component_atom_ids(
        atoms_by_id,
        box,
        available_core,
        type_element_map=type_element_map,
    )
    if connected_ids and connected_source in {"same_molecule", "connected_component"}:
        return connected_ids, connected_source

    cutoff_sq = 2.35 * 2.35
    context_ids = set(available_core)
    for atom_id, atom in atoms_by_id.items():
        if atom_id in context_ids:
            continue
        if any(
            _distance_sq_pbc(atom, atoms_by_id[core_id], box) <= cutoff_sq
            for core_id in available_core
            if core_id in atoms_by_id
        ):
            context_ids.add(int(atom_id))
    return context_ids or set(available_core), "distance_shell_fallback"


def _expand_reaction_context_ids_for_frames(
    parsed_frames: dict[int, dict[str, Any]],
    *,
    focus_frames: list[int],
    core_atom_ids: set[int],
    type_element_map: dict[str, str] | None = None,
) -> tuple[set[int], str]:
    if not core_atom_ids:
        return set(), "none"
    union_ids: set[int] = set(core_atom_ids)
    sources: list[str] = []
    for frame in focus_frames:
        parsed = parsed_frames.get(int(frame)) or {}
        atoms_by_id = parsed.get("atoms") or {}
        if not atoms_by_id:
            continue
        context_ids, source = _expand_reaction_context_atom_ids(
            atoms_by_id,
            parsed.get("box", []),
            core_atom_ids,
            type_element_map=type_element_map,
        )
        if context_ids:
            union_ids.update(context_ids)
        sources.append(source)
    if not sources:
        return union_ids, "core_only"
    normalized = set(sources)
    if normalized == {"same_molecule"}:
        return union_ids, "same_molecule_union"
    if normalized.issubset({"same_molecule", "connected_component"}):
        return union_ids, "connected_component_union"
    if "distance_shell_fallback" in normalized:
        return union_ids, "distance_shell_fallback"
    return union_ids, sources[0]


def _build_reaction_event_id(reaction_signature: str, anchor_frame: int, core_ids: set[int]) -> str:
    payload = f"{reaction_signature}|{int(anchor_frame)}|{_event_ids_text(core_ids)}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"rxevt_{int(anchor_frame)}_{digest}"


def _sample_route_labels(hits: list[dict[str, Any]], key: str, limit: int = 4) -> str:
    labels = []
    seen: set[str] = set()
    for hit in hits:
        label = str(hit.get(key, "") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
        if len(labels) >= max(1, int(limit)):
            break
    return "; ".join(labels)


def species_by_frames_from_transitions(
    transitions: list[tuple[int, str]],
    frames: list[int],
) -> dict[int, str]:
    if not transitions or not frames:
        return {}
    out: dict[int, str] = {}
    ptr = 0
    current = ""
    n_trans = len(transitions)
    for frame in frames:
        while ptr < n_trans and transitions[ptr][0] <= frame:
            current = transitions[ptr][1]
            ptr += 1
        if current:
            out[int(frame)] = current
    return out


def summarize_route_atom_changes(
    route_file: str,
    *,
    selected_frames: list[int],
    anchor_frames: list[int],
    previous_frame_of_anchor: dict[int, int],
    target: str,
    match_mode: str,
    reaction_smiles: str,
    atom_sample_limit: int,
    progress_callback: Any = None,
    progress_start: float = 0.70,
    progress_span: float = 0.02,
) -> dict[str, Any]:
    frames = sorted({int(frame) for frame in selected_frames})
    if not frames:
        return {
            "frame_stats": {},
            "event_stats": {},
            "meta": {
                "route_file": route_file,
                "scanned_atoms": 0,
                "frames_tracked": 0,
                "reaction_smiles": reaction_smiles,
                "message": "no selected frames for route analysis",
            },
        }

    max_frame = max(frames + [int(v) for v in previous_frame_of_anchor.values()] + [0])
    all_interest_frames = sorted({*frames, *[int(v) for v in previous_frame_of_anchor.values()]})

    target_mode, target_matcher = build_context_matcher(target, match_mode) if target else ("smiles", lambda _s: False)
    reactants, products = parse_reaction_smiles_query(reaction_smiles)
    reactant_matcher = build_smiles_or_formula_matcher(reactants)
    product_matcher = build_smiles_or_formula_matcher(products)
    has_reaction = bool(reactants and products)

    sample_limit = max(1, min(int(atom_sample_limit), 200))

    def _mk_stats() -> dict[str, Any]:
        return {
            "target_count": 0,
            "reactant_count": 0,
            "product_count": 0,
            "target_atom_ids": [],
            "reactant_atom_ids": [],
            "product_atom_ids": [],
            "target_atom_ids_all": set(),
            "reactant_atom_ids_all": set(),
            "product_atom_ids_all": set(),
        }

    frame_stats: dict[int, dict[str, Any]] = {int(frame): _mk_stats() for frame in frames}
    event_stats: dict[int, dict[str, Any]] = {
        int(anchor): {
            "changed_target_count": 0,
            "changed_target_atom_ids": [],
            "reactant_to_product_count": 0,
            "reactant_to_product_atom_ids": [],
            "product_to_reactant_count": 0,
            "product_to_reactant_atom_ids": [],
            "changed_target_atom_ids_all": set(),
            "reactant_to_product_atom_ids_all": set(),
            "product_to_reactant_atom_ids_all": set(),
            "changed_target_first_frame": None,
            "changed_target_last_frame": None,
            "reactant_to_product_first_frame": None,
            "reactant_to_product_last_frame": None,
            "product_to_reactant_first_frame": None,
            "product_to_reactant_last_frame": None,
        }
        for anchor in anchor_frames
    }

    file_size = max(os.path.getsize(route_file), 1)
    bytes_read = 0
    last_emit = 0.0
    scanned_atoms = 0

    def append_sample(items: list[int], atom_id: int) -> None:
        if len(items) < sample_limit:
            items.append(atom_id)

    def append_sample_and_all(items: list[int], all_items: set[int], atom_id: int) -> None:
        all_items.add(atom_id)
        append_sample(items, atom_id)

    def update_frame_range(stats: dict[str, Any], prefix: str, frame: int) -> None:
        first_key = f"{prefix}_first_frame"
        last_key = f"{prefix}_last_frame"
        current_first = stats.get(first_key)
        current_last = stats.get(last_key)
        if current_first is None or int(frame) < int(current_first):
            stats[first_key] = int(frame)
        if current_last is None or int(frame) > int(current_last):
            stats[last_key] = int(frame)

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "reading_route",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    emit(progress_start, f"Scanning route file: {os.path.basename(route_file)}")
    with open(route_file, encoding="utf-8", errors="ignore") as fh:
        for raw_line in fh:
            bytes_read += len(raw_line)
            m = ROUTE_LINE_RE.match(raw_line.strip())
            if not m:
                continue
            scanned_atoms += 1
            atom_id = int(m.group(1))
            route_text = m.group(2)
            transitions: list[tuple[int, str]] = []
            for hit in ROUTE_STEP_RE.finditer(route_text):
                ts = int(hit.group(1))
                if ts > max_frame:
                    break
                transitions.append((ts, hit.group(2)))
            if not transitions:
                continue

            species_by_frame = species_by_frames_from_transitions(transitions, all_interest_frames)
            if not species_by_frame:
                continue

            for frame in frames:
                frame_smi = species_by_frame.get(frame, "")
                if not frame_smi:
                    continue
                fstats = frame_stats[frame]
                if target and target_matcher(frame_smi):
                    fstats["target_count"] += 1
                    append_sample_and_all(fstats["target_atom_ids"], fstats["target_atom_ids_all"], atom_id)
                if has_reaction and reactant_matcher(frame_smi):
                    fstats["reactant_count"] += 1
                    append_sample_and_all(fstats["reactant_atom_ids"], fstats["reactant_atom_ids_all"], atom_id)
                if has_reaction and product_matcher(frame_smi):
                    fstats["product_count"] += 1
                    append_sample_and_all(fstats["product_atom_ids"], fstats["product_atom_ids_all"], atom_id)

            for anchor in anchor_frames:
                prev_frame = int(previous_frame_of_anchor.get(anchor, anchor))
                prev_smi = species_by_frame.get(prev_frame, "")
                cur_smi = species_by_frame.get(anchor, "")
                estats = event_stats.get(anchor)
                if estats is None:
                    continue
                interval_transitions = [(ts, smi) for ts, smi in transitions if prev_frame < ts <= anchor]
                if target and (target_matcher(prev_smi) != target_matcher(cur_smi)):
                    estats["changed_target_count"] += 1
                    append_sample_and_all(
                        estats["changed_target_atom_ids"],
                        estats["changed_target_atom_ids_all"],
                        atom_id,
                    )
                    prev_flag = bool(target_matcher(prev_smi))
                    found_change = False
                    for ts, smi in interval_transitions:
                        new_flag = bool(target_matcher(smi))
                        if new_flag != prev_flag:
                            update_frame_range(estats, "changed_target", ts)
                            prev_flag = new_flag
                            found_change = True
                    if not found_change:
                        update_frame_range(estats, "changed_target", anchor)
                if has_reaction and reactant_matcher(prev_smi) and product_matcher(cur_smi):
                    estats["reactant_to_product_count"] += 1
                    append_sample_and_all(
                        estats["reactant_to_product_atom_ids"],
                        estats["reactant_to_product_atom_ids_all"],
                        atom_id,
                    )
                    prev_react = bool(reactant_matcher(prev_smi))
                    prev_prod = bool(product_matcher(prev_smi))
                    found_change = False
                    for ts, smi in interval_transitions:
                        new_react = bool(reactant_matcher(smi))
                        new_prod = bool(product_matcher(smi))
                        if prev_react and new_prod:
                            update_frame_range(estats, "reactant_to_product", ts)
                            found_change = True
                        prev_react = new_react
                        prev_prod = new_prod
                    if not found_change:
                        update_frame_range(estats, "reactant_to_product", anchor)
                if has_reaction and product_matcher(prev_smi) and reactant_matcher(cur_smi):
                    estats["product_to_reactant_count"] += 1
                    append_sample_and_all(
                        estats["product_to_reactant_atom_ids"],
                        estats["product_to_reactant_atom_ids_all"],
                        atom_id,
                    )
                    prev_react = bool(reactant_matcher(prev_smi))
                    prev_prod = bool(product_matcher(prev_smi))
                    found_change = False
                    for ts, smi in interval_transitions:
                        new_react = bool(reactant_matcher(smi))
                        new_prod = bool(product_matcher(smi))
                        if prev_prod and new_react:
                            update_frame_range(estats, "product_to_reactant", ts)
                            found_change = True
                        prev_react = new_react
                        prev_prod = new_prod
                    if not found_change:
                        update_frame_range(estats, "product_to_reactant", anchor)

            frac = bytes_read / file_size
            now = time.monotonic()
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    progress_start + progress_span * min(frac, 1.0),
                    f"Scanning route file: {frac * 100:.1f}%",
                    scanned_atoms=scanned_atoms,
                )
                last_emit = now

    for stats in frame_stats.values():
        stats["target_atom_ids_text"] = ",".join(str(x) for x in stats["target_atom_ids"])
        stats["reactant_atom_ids_text"] = ",".join(str(x) for x in stats["reactant_atom_ids"])
        stats["product_atom_ids_text"] = ",".join(str(x) for x in stats["product_atom_ids"])
    for stats in event_stats.values():
        stats["changed_target_atom_ids_text"] = ",".join(str(x) for x in stats["changed_target_atom_ids"])
        stats["reactant_to_product_atom_ids_text"] = ",".join(str(x) for x in stats["reactant_to_product_atom_ids"])
        stats["product_to_reactant_atom_ids_text"] = ",".join(str(x) for x in stats["product_to_reactant_atom_ids"])

    return {
        "frame_stats": frame_stats,
        "event_stats": event_stats,
        "meta": {
            "route_file": route_file,
            "scanned_atoms": scanned_atoms,
            "frames_tracked": len(frames),
            "target_match_mode": target_mode,
            "reaction_smiles": reaction_smiles,
            "reaction_reactants": reactants,
            "reaction_products": products,
            "sample_limit": sample_limit,
            "message": (
                "no atom-transition rows parsed from route file; verify .route path/format"
                if scanned_atoms <= 0
                else "route atom transitions parsed"
            ),
        },
    }


def locate_context_events(
    timeline: list[dict[str, Any]],
    event_mode: str,
    before_frames: int,
    after_frames: int,
    max_events: int,
) -> list[dict[str, Any]]:
    if not timeline:
        return []
    mode = (event_mode or "appear").strip().lower()
    counts = [int(item.get("count", 0)) for item in timeline]
    peak_count = max(counts) if counts else 0
    peak_idx = counts.index(peak_count) if counts else 0
    out: list[dict[str, Any]] = []
    n_items = len(timeline)

    for idx, item in enumerate(timeline):
        frame = int(item["frame"])
        count = counts[idx]
        prev_count = counts[idx - 1] if idx > 0 else 0
        next_count = counts[idx + 1] if idx + 1 < n_items else count
        delta = count - prev_count

        matched = False
        label = mode
        if mode == "appear":
            matched = count > 0 and prev_count == 0
        elif mode == "disappear":
            matched = prev_count > 0 and count == 0
        elif mode == "production":
            matched = delta > 0
        elif mode == "consumption":
            matched = delta < 0
        elif mode == "peak":
            matched = idx == peak_idx and peak_count > 0
        elif mode == "nonzero":
            matched = count > 0
        else:
            matched = count > 0 and prev_count == 0
            label = "appear"
        if not matched:
            continue

        lo = max(0, idx - max(0, before_frames))
        hi = min(n_items - 1, idx + max(0, after_frames))
        window_frames = [int(timeline[j]["frame"]) for j in range(lo, hi + 1)]
        out.append(
            {
                "event_index": len(out) + 1,
                "event_type": label,
                "anchor_index": idx,
                "anchor_frame": frame,
                "prev_count": prev_count,
                "count_at_frame": count,
                "next_count": next_count,
                "delta_from_prev": delta,
                "window_start": int(timeline[lo]["frame"]),
                "window_end": int(timeline[hi]["frame"]),
                "window_frames": window_frames,
            }
        )
        if len(out) >= max(1, max_events):
            break

    return out


def extract_target_timeline(
    species_file: str,
    target: str,
    match_mode: str,
    *,
    progress_callback: Any = None,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    mode, matcher = build_context_matcher(target, match_mode)
    file_size = max(os.path.getsize(species_file), 1)
    bytes_read = 0
    last_emit = 0.0
    timeline: list[dict[str, Any]] = []
    matched_totals: Counter[str] = Counter()

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "reading_species",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    emit(0.02, f"Scanning {os.path.basename(species_file)} for target frames")
    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            count = 0
            for smi, cnt in pairs:
                if matcher(smi):
                    count += cnt
                    if cnt > 0:
                        matched_totals[smi] += int(cnt)
            timeline.append({"frame": ts, "count": count})
            frac = bytes_read / file_size
            now = time.monotonic()
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    0.02 + 0.48 * min(frac, 1.0),
                    f"Scanning species file: {frac * 100:.1f}%",
                    frame=ts,
                    match_mode=mode,
                )
                last_emit = now
    return timeline, matched_totals


def collect_frame_match_details(
    species_file: str,
    target: str,
    match_mode: str,
    selected_frames: set[int],
    *,
    progress_callback: Any = None,
) -> dict[int, list[tuple[str, int]]]:
    mode, matcher = build_context_matcher(target, match_mode)
    if not selected_frames:
        return {}
    wanted = set(int(frame) for frame in selected_frames)
    max_wanted = max(wanted)
    file_size = max(os.path.getsize(species_file), 1)
    bytes_read = 0
    last_emit = 0.0
    details: dict[int, list[tuple[str, int]]] = {}

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "collecting_frames",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    emit(0.52, "Collecting matched structures inside selected windows")
    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            if ts > max_wanted and not wanted:
                break
            if ts not in wanted:
                if ts > max_wanted:
                    break
                continue
            hits = [(smi, int(cnt)) for smi, cnt in pairs if cnt > 0 and matcher(smi)]
            hits.sort(key=lambda item: (-item[1], item[0]))
            details[ts] = hits
            wanted.discard(ts)
            frac = bytes_read / file_size
            now = time.monotonic()
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    0.52 + 0.18 * min(frac, 1.0),
                    f"Collecting window-frame details: {frac * 100:.1f}%",
                    frame=ts,
                    match_mode=mode,
                )
                last_emit = now
            if not wanted:
                break
    return details


def extract_lammpstrj_subset(
    trajectory_file: str,
    selected_frames: list[int],
    *,
    frame_atom_filters: dict[int, set[int]] | None = None,
    type_element_map: dict[str, str] | None = None,
    progress_callback: Any = None,
    inline_text_limit: int = 2_000_000,
    inline_frame_limit: int = 120,
    preview_text_limit: int = 8_000_000,
    preview_frame_limit: int = 8,
    preview_first_frame_hard_limit: int = 32_000_000,
    output_filename: str = "context_subset.lammpstrj",
) -> dict[str, Any]:
    def emit(progress: float, phase: str, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": phase,
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    resolved_type_map = {str(key): value for key, value in (type_element_map or {}).items() if str(key).strip() and str(value).strip()}

    def transform_lammpstrj_block(block: bytes, atom_ids: set[int] | None = None) -> bytes:
        wanted_ids = set(atom_ids or set())
        text = block.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        if not lines:
            return block

        n_header_idx: int | None = None
        atoms_header_idx: int | None = None
        for idx, line in enumerate(lines):
            if line.startswith("ITEM: NUMBER OF ATOMS"):
                n_header_idx = idx
            if line.startswith("ITEM: ATOMS"):
                atoms_header_idx = idx
                break
        if n_header_idx is None or atoms_header_idx is None or (n_header_idx + 1) >= len(lines):
            return block

        atom_start = atoms_header_idx + 1
        atom_lines = lines[atom_start:]
        atom_header_tokens = lines[atoms_header_idx].split()
        atom_cols = [str(tok).strip() for tok in atom_header_tokens[2:]] if len(atom_header_tokens) > 2 else []
        id_col_idx = 0
        if atom_cols and "id" in atom_cols:
            id_col_idx = atom_cols.index("id")
        type_col_idx = atom_cols.index("type") if "type" in atom_cols else None
        has_element_col = "element" in atom_cols
        inject_element_col = bool(resolved_type_map) and not has_element_col and type_col_idx is not None
        rebuilt_atom_header = lines[atoms_header_idx]
        if inject_element_col:
            rebuilt_atom_header = f"ITEM: ATOMS {' '.join(atom_cols + ['element'])}"

        kept_atoms: list[str] = []
        for atom_line in atom_lines:
            cols = atom_line.split()
            if not cols:
                continue
            if id_col_idx >= len(cols):
                continue
            try:
                atom_id = int(float(cols[id_col_idx]))
            except Exception:
                continue
            if wanted_ids and atom_id not in wanted_ids:
                continue
            if inject_element_col:
                atom_type = _normalize_atom_type_token(cols[type_col_idx]) if type_col_idx is not None and type_col_idx < len(cols) else ""
                element = resolved_type_map.get(atom_type, "X")
                kept_atoms.append(" ".join([*cols, element]))
            else:
                kept_atoms.append(atom_line)

        rebuilt = (
            lines[: n_header_idx + 1]
            + [str(len(kept_atoms))]
            + lines[n_header_idx + 2 : atoms_header_idx]
            + [rebuilt_atom_header]
            + kept_atoms
        )
        return ("\n".join(rebuilt) + "\n").encode("utf-8")

    def copy_ranges(
        ranges: list[tuple[int, int, int]],
        missing: int,
        *,
        extract_mode: str,
        index_frames: int = 0,
        progress_start: float = 0.90,
        progress_span: float = 0.08,
    ) -> dict[str, Any]:
        if not ranges:
            return {
                "trajectory_text": "",
                "trajectory_saved_path": "",
                "matched_blocks": 0,
                "n_missing_frames": missing,
                "subset_bytes": 0,
                "extract_mode": extract_mode,
                "index_frames": index_frames,
            }
        total_copy_bytes = sum(end - start for _frame, start, end in ranges)
        matched_blocks = 0
        copied_bytes = 0
        processed_input_bytes = 0
        last_emit = 0.0
        output_path = context_tempfile_path(output_filename)
        preview_buffer = io.BytesIO()
        preview_blocks = 0
        preview_bytes = 0
        use_atom_filter = bool(frame_atom_filters)
        use_block_transform = use_atom_filter or bool(resolved_type_map)
        emit(progress_start, "extracting_trajectory", f"Extracting {len(ranges)} selected trajectory frames")
        with open(trajectory_file, "rb") as src, open(output_path, "wb") as dst:
            for frame, start, end in ranges:
                src.seek(start)
                remain = end - start
                block_size = max(0, end - start)
                frame_filter_ids = None
                if use_atom_filter:
                    frame_filter_ids = set(frame_atom_filters.get(int(frame), set()))
                include_preview = False
                if preview_blocks < max(1, preview_frame_limit):
                    if (preview_bytes + block_size) <= max(1, preview_text_limit):
                        include_preview = True
                    elif preview_blocks == 0 and block_size <= max(1, preview_first_frame_hard_limit):
                        include_preview = True
                if not use_block_transform and frame_filter_ids is None:
                    while remain > 0:
                        chunk = src.read(min(4 * 1024 * 1024, remain))
                        if not chunk:
                            break
                        dst.write(chunk)
                        if include_preview:
                            preview_buffer.write(chunk)
                        n_bytes = len(chunk)
                        copied_bytes += n_bytes
                        processed_input_bytes += n_bytes
                        remain -= n_bytes
                        now = time.monotonic()
                        if now - last_emit >= 0.8 or processed_input_bytes >= total_copy_bytes:
                            frac = processed_input_bytes / max(total_copy_bytes, 1)
                            emit(
                                progress_start + progress_span * min(frac, 1.0),
                                "extracting_trajectory",
                                f"Extracting trajectory subset: {frac * 100:.1f}%",
                                matched_blocks=matched_blocks,
                                frame=frame,
                            )
                            last_emit = now
                else:
                    block = src.read(remain)
                    processed_input_bytes += remain
                    remain = 0
                    transformed = transform_lammpstrj_block(block, frame_filter_ids)
                    dst.write(transformed)
                    copied_bytes += len(transformed)
                    if include_preview:
                        preview_buffer.write(transformed)
                    now = time.monotonic()
                    if now - last_emit >= 0.8 or processed_input_bytes >= total_copy_bytes:
                        frac = processed_input_bytes / max(total_copy_bytes, 1)
                        emit(
                            progress_start + progress_span * min(frac, 1.0),
                            "extracting_trajectory",
                            f"Extracting trajectory subset: {frac * 100:.1f}%",
                            matched_blocks=matched_blocks,
                            frame=frame,
                        )
                        last_emit = now
                matched_blocks += 1
                if include_preview:
                    preview_blocks += 1
                    preview_bytes += block_size if (not use_block_transform and frame_filter_ids is None) else len(transformed)
        trajectory_text = ""
        if matched_blocks <= max(1, inline_frame_limit) and copied_bytes <= max(1, inline_text_limit):
            trajectory_text = output_path.read_text(encoding="utf-8", errors="ignore")
        trajectory_preview_text = trajectory_text
        if not trajectory_preview_text:
            trajectory_preview_text = preview_buffer.getvalue().decode("utf-8", errors="ignore")
        return {
            "trajectory_text": trajectory_text,
            "trajectory_preview_text": trajectory_preview_text,
            "trajectory_saved_path": str(output_path),
            "matched_blocks": matched_blocks,
            "n_missing_frames": missing,
            "subset_bytes": copied_bytes,
            "extract_mode": extract_mode,
            "index_frames": index_frames,
            "preview_blocks": preview_blocks,
            "preview_bytes": preview_bytes,
        }

    def scan_selected_ranges_without_full_index(
        selected: list[int],
    ) -> tuple[list[tuple[int, int, int]], int, int]:
        selected_set = set(selected)
        max_selected = max(selected)
        file_size = max(os.path.getsize(trajectory_file), 1)
        bytes_read = 0
        last_emit = 0.0
        scanned_frames = 0
        ranges: list[tuple[int, int, int]] = []
        current_frame: int | None = None
        current_start: int | None = None
        emit(0.72, "scanning_trajectory", f"Scanning trajectory until frame {max_selected} (no global index)")
        with open(trajectory_file, "rb") as fh:
            while True:
                block_start = fh.tell()
                line = fh.readline()
                if not line:
                    break
                bytes_read += len(line)
                if not line.startswith(b"ITEM: TIMESTEP"):
                    now = time.monotonic()
                    frac = bytes_read / file_size
                    if frac >= 0.99 or (now - last_emit) >= 1.0:
                        emit(
                            0.72 + 0.16 * min(frac, 1.0),
                            "scanning_trajectory",
                            f"Scanning trajectory for selected frames: {frac * 100:.1f}%",
                            n_scanned_frames=scanned_frames,
                        )
                        last_emit = now
                    continue

                timestep_line = fh.readline()
                if not timestep_line:
                    break
                bytes_read += len(timestep_line)

                if current_frame is not None and current_start is not None and block_start > current_start:
                    if current_frame in selected_set:
                        ranges.append((current_frame, current_start, block_start))
                    if current_frame >= max_selected:
                        current_frame = None
                        current_start = None
                        break

                next_frame: int | None = None
                try:
                    next_frame = int(timestep_line.strip().split()[0])
                    scanned_frames += 1
                except Exception:
                    next_frame = None
                current_frame = next_frame
                current_start = block_start

                now = time.monotonic()
                frac = bytes_read / file_size
                if frac >= 0.99 or (now - last_emit) >= 1.0:
                    emit(
                        0.72 + 0.16 * min(frac, 1.0),
                        "scanning_trajectory",
                        f"Scanning trajectory for selected frames: {frac * 100:.1f}%",
                        n_scanned_frames=scanned_frames,
                        frame=current_frame,
                    )
                    last_emit = now

        if current_frame is not None and current_start is not None:
            end_pos = os.path.getsize(trajectory_file)
            if current_frame in selected_set and end_pos > current_start:
                ranges.append((current_frame, current_start, end_pos))

        found_frames = {frame for frame, _start, _end in ranges}
        missing = sum(1 for frame in selected if frame not in found_frames)
        emit(
            0.88,
            "scanning_trajectory",
            f"Selected-frame scan ready: {len(found_frames)}/{len(selected)} frames",
            n_scanned_frames=scanned_frames,
            n_missing_frames=missing,
        )
        return ranges, missing, scanned_frames

    selected = sorted({int(frame) for frame in selected_frames})
    if not selected:
        return {
            "trajectory_text": "",
            "trajectory_saved_path": "",
            "matched_blocks": 0,
            "n_missing_frames": 0,
            "subset_bytes": 0,
            "extract_mode": "none",
            "index_frames": 0,
        }

    cached_index = TRAJECTORY_INDEX_STORE.peek(trajectory_file)
    if cached_index is not None:
        emit(0.72, "cached_trajectory_index", f"Using cached trajectory index: {os.path.basename(trajectory_file)}")
        ranges: list[tuple[int, int, int]] = []
        missing = 0
        for frame in selected:
            block = cached_index.frame_offsets.get(frame)
            if block is None:
                missing += 1
                continue
            start, end = int(block[0]), int(block[1])
            if end <= start:
                missing += 1
                continue
            ranges.append((frame, start, end))
        return copy_ranges(
            ranges,
            missing,
            extract_mode="indexed_seek_copy",
            index_frames=len(cached_index.frames),
            progress_start=0.90,
            progress_span=0.08,
        )

    ranges, missing, scanned_frames = scan_selected_ranges_without_full_index(selected)
    return copy_ranges(
        ranges,
        missing,
        extract_mode="range_scan_copy",
        index_frames=scanned_frames,
        progress_start=0.90,
        progress_span=0.08,
    )


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

    cached_index = TRAJECTORY_INDEX_STORE.peek(trajectory_file)
    if cached_index is not None:
        blocks: dict[int, bytes] = {}
        emit(progress_start, f"Reading {len(requested)} anchor frame(s) from cached trajectory index")
        with open(trajectory_file, "rb") as fh:
            for idx, frame in enumerate(requested, 1):
                block = cached_index.frame_offsets.get(frame)
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
        return blocks

    file_size = max(os.path.getsize(trajectory_file), 1)
    bytes_read = 0
    last_emit = 0.0
    max_requested = max(requested)
    requested_set = set(requested)
    blocks: dict[int, bytes] = {}
    current_frame: int | None = None
    current_block: bytearray | None = None

    emit(progress_start, f"Scanning trajectory for {len(requested)} anchor frame(s)")
    with open(trajectory_file, "rb") as fh:
        while True:
            line = fh.readline()
            if not line:
                break
            bytes_read += len(line)
            if line.startswith(b"ITEM: TIMESTEP"):
                if current_frame in requested_set and current_block is not None:
                    blocks[int(current_frame)] = bytes(current_block)
                    if len(blocks) >= len(requested_set):
                        break
                timestep_line = fh.readline()
                if not timestep_line:
                    break
                bytes_read += len(timestep_line)
                try:
                    current_frame = int(timestep_line.strip().split()[0])
                except Exception:
                    current_frame = None
                current_block = None
                if current_frame is not None and current_frame in requested_set:
                    current_block = bytearray()
                    current_block.extend(line)
                    current_block.extend(timestep_line)
                if current_frame is not None and current_frame > max_requested and len(blocks) >= len(requested_set):
                    break
            elif current_frame in requested_set and current_block is not None:
                current_block.extend(line)
            now = time.monotonic()
            frac = bytes_read / file_size
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    progress_start + progress_span * min(frac, 1.0),
                    f"Scanning trajectory for anchor frames: {frac * 100:.1f}%",
                    n_found_frames=len(blocks),
                    frame=current_frame,
                )
                last_emit = now
            if current_frame is not None and current_frame > max_requested and len(blocks) >= len(requested_set):
                break
    if current_frame in requested_set and current_block is not None and current_frame not in blocks:
        blocks[int(current_frame)] = bytes(current_block)
    emit(
        progress_start + progress_span,
        f"Anchor-frame scan ready: {len(blocks)}/{len(requested)} frame(s)",
        n_found_frames=len(blocks),
    )
    return blocks


def parse_lammpstrj_frame_block(
    block: bytes,
    *,
    atom_ids: set[int] | None = None,
) -> dict[str, Any]:
    text = block.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if not lines:
        return {"frame": None, "box": [], "atoms": {}}

    frame: int | None = None
    n_atoms = 0
    box: list[tuple[float, float]] = []
    atoms: dict[int, dict[str, Any]] = {}
    idx = 0
    wanted = set(atom_ids or set())

    while idx < len(lines):
        line = str(lines[idx] or "").strip()
        if line == "ITEM: TIMESTEP":
            if idx + 1 < len(lines):
                try:
                    frame = int(str(lines[idx + 1] or "").strip())
                except Exception:
                    frame = None
            idx += 2
            continue
        if line.startswith("ITEM: NUMBER OF ATOMS"):
            if idx + 1 < len(lines):
                try:
                    n_atoms = int(str(lines[idx + 1] or "").strip())
                except Exception:
                    n_atoms = 0
            idx += 2
            continue
        if line.startswith("ITEM: BOX BOUNDS"):
            box = []
            for axis in range(3):
                if idx + 1 + axis >= len(lines):
                    break
                parts = [float(value) for value in str(lines[idx + 1 + axis] or "").strip().split()[:2]]
                if len(parts) >= 2:
                    box.append((parts[0], parts[1]))
            idx += 4
            continue
        if line.startswith("ITEM: ATOMS"):
            atom_cols = [str(tok).strip() for tok in line.split()[2:]]
            col_index = {name: pos for pos, name in enumerate(atom_cols)}
            id_col = col_index.get("id", 0)
            mol_col = col_index.get("mol")
            type_col = col_index.get("type")
            element_col = col_index.get("element")
            x_key = next((name for name in ("x", "xu", "xs") if name in col_index), "")
            y_key = next((name for name in ("y", "yu", "ys") if name in col_index), "")
            z_key = next((name for name in ("z", "zu", "zs") if name in col_index), "")
            use_scaled = x_key.endswith("s") and y_key.endswith("s") and z_key.endswith("s") and len(box) == 3
            atom_start = idx + 1
            atom_stop = min(len(lines), atom_start + max(n_atoms, 0))
            for atom_idx in range(atom_start, atom_stop):
                parts = str(lines[atom_idx] or "").strip().split()
                if not parts:
                    continue
                if id_col >= len(parts):
                    continue
                try:
                    atom_id = int(float(parts[id_col]))
                except Exception:
                    continue
                if wanted and atom_id not in wanted:
                    continue

                def read_num(key: str, fallback: float = float("nan")) -> float:
                    pos = col_index.get(key)
                    if pos is None or pos >= len(parts):
                        return fallback
                    try:
                        return float(parts[pos])
                    except Exception:
                        return fallback

                x = read_num(x_key) if x_key else float("nan")
                y = read_num(y_key) if y_key else float("nan")
                z = read_num(z_key) if z_key else float("nan")
                if use_scaled and len(box) == 3:
                    x = box[0][0] + x * (box[0][1] - box[0][0])
                    y = box[1][0] + y * (box[1][1] - box[1][0])
                    z = box[2][0] + z * (box[2][1] - box[2][0])
                if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                    continue
                atoms[atom_id] = {
                    "id": atom_id,
                    "x": x,
                    "y": y,
                    "z": z,
                    "mol": str(parts[mol_col]) if mol_col is not None and mol_col < len(parts) else "",
                    "type": str(parts[type_col]) if type_col is not None and type_col < len(parts) else "",
                    "element": str(parts[element_col]) if element_col is not None and element_col < len(parts) else "",
                }
            idx = atom_stop
            continue
        idx += 1
    return {"frame": frame, "box": box, "atoms": atoms}


def _minimum_image_delta(delta: float, length: float) -> float:
    if not math.isfinite(length) or length <= 0:
        return delta
    return delta - round(delta / length) * length


def _distance_sq_pbc(atom_a: dict[str, Any], atom_b: dict[str, Any], box: list[tuple[float, float]]) -> float:
    dx = float(atom_a["x"]) - float(atom_b["x"])
    dy = float(atom_a["y"]) - float(atom_b["y"])
    dz = float(atom_a["z"]) - float(atom_b["z"])
    if len(box) == 3:
        dx = _minimum_image_delta(dx, float(box[0][1]) - float(box[0][0]))
        dy = _minimum_image_delta(dy, float(box[1][1]) - float(box[1][0]))
        dz = _minimum_image_delta(dz, float(box[2][1]) - float(box[2][0]))
    return dx * dx + dy * dy + dz * dz


def _candidate_groups_by_mol(atoms_by_id: dict[int, dict[str, Any]], atom_ids: set[int]) -> tuple[list[set[int]], str]:
    groups: dict[str, set[int]] = {}
    for atom_id in atom_ids:
        atom = atoms_by_id.get(atom_id)
        if atom is None:
            continue
        mol = str(atom.get("mol", "") or "").strip()
        if not mol or mol in {"0", "0.0"}:
            continue
        groups.setdefault(mol, set()).add(atom_id)
    if len(groups) >= 2:
        return list(groups.values()), "mol"
    return [], ""


def _candidate_groups_by_distance(
    atoms_by_id: dict[int, dict[str, Any]],
    atom_ids: set[int],
    box: list[tuple[float, float]],
    *,
    cutoff: float = 2.25,
) -> tuple[list[set[int]], str]:
    available = sorted(atom_id for atom_id in atom_ids if atom_id in atoms_by_id)
    if not available:
        return [], "distance"
    cutoff_sq = float(cutoff) * float(cutoff)
    neighbors: dict[int, set[int]] = {atom_id: set() for atom_id in available}
    for idx, atom_id in enumerate(available):
        atom_a = atoms_by_id[atom_id]
        for other_id in available[idx + 1 :]:
            atom_b = atoms_by_id[other_id]
            if _distance_sq_pbc(atom_a, atom_b, box) <= cutoff_sq:
                neighbors[atom_id].add(other_id)
                neighbors[other_id].add(atom_id)
    groups: list[set[int]] = []
    visited: set[int] = set()
    for atom_id in available:
        if atom_id in visited:
            continue
        stack = [atom_id]
        group: set[int] = set()
        visited.add(atom_id)
        while stack:
            current = stack.pop()
            group.add(current)
            for neighbor in neighbors.get(current, set()):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                stack.append(neighbor)
        groups.append(group)
    return groups, "distance"


def _group_min_distance_sq(
    group_a: set[int],
    group_b: set[int],
    atoms_by_id: dict[int, dict[str, Any]],
    box: list[tuple[float, float]],
) -> float:
    best = float("inf")
    for atom_a_id in group_a:
        atom_a = atoms_by_id.get(atom_a_id)
        if atom_a is None:
            continue
        for atom_b_id in group_b:
            atom_b = atoms_by_id.get(atom_b_id)
            if atom_b is None:
                continue
            best = min(best, _distance_sq_pbc(atom_a, atom_b, box))
            if best <= 0.0:
                return 0.0
    return best


def select_main_context_atom_group(
    atoms_by_id: dict[int, dict[str, Any]],
    box: list[tuple[float, float]],
    *,
    candidate_ids: set[int],
    event_ids: set[int],
    target_ids: set[int],
    reactant_ids: set[int],
    product_ids: set[int],
    reaction_smiles: str,
) -> dict[str, Any]:
    available_ids = {atom_id for atom_id in candidate_ids if atom_id in atoms_by_id}
    if not available_ids:
        return {
            "selected_ids": set(),
            "group_mode": "none",
            "n_groups": 0,
            "n_selected_groups": 0,
            "source": "none",
        }

    groups, group_mode = _candidate_groups_by_mol(atoms_by_id, available_ids)
    if not groups:
        groups, group_mode = _candidate_groups_by_distance(atoms_by_id, available_ids, box)
    if not groups:
        groups = [set(available_ids)]
        group_mode = "fallback"

    scored: list[dict[str, Any]] = []
    for idx, ids in enumerate(groups):
        overlap_event = len(ids & event_ids)
        overlap_target = len(ids & target_ids)
        overlap_reactant = len(ids & reactant_ids)
        overlap_product = len(ids & product_ids)
        score = (
            overlap_event * 1000
            + overlap_target * 300
            + overlap_product * 220
            + overlap_reactant * 180
            + len(ids)
        )
        scored.append(
            {
                "index": idx,
                "ids": ids,
                "size": len(ids),
                "score": score,
                "overlap_event": overlap_event,
                "overlap_target": overlap_target,
                "overlap_reactant": overlap_reactant,
                "overlap_product": overlap_product,
            }
        )
    scored.sort(
        key=lambda item: (
            int(item["score"]),
            int(item["overlap_event"]),
            int(item["overlap_target"]),
            int(item["size"]),
        ),
        reverse=True,
    )
    primary = scored[0]
    selected_indexes: list[int]
    if reaction_smiles:
        initial = [item["index"] for item in scored if int(item["overlap_event"]) > 0]
        if initial:
            selected_indexes = initial[:3]
        else:
            selected_indexes = [int(primary["index"])]
        merge_cutoff_sq = 4.2 * 4.2
        changed = True
        while changed and len(selected_indexes) < min(len(scored), 3):
            changed = False
            for item in scored:
                idx = int(item["index"])
                if idx in selected_indexes or int(item["score"]) <= 0:
                    continue
                near_selected = any(
                    _group_min_distance_sq(groups[idx], groups[selected_idx], atoms_by_id, box) <= merge_cutoff_sq
                    for selected_idx in selected_indexes
                )
                if near_selected:
                    selected_indexes.append(idx)
                    changed = True
                    if len(selected_indexes) >= min(len(scored), 3):
                        break
    else:
        selected_indexes = [int(primary["index"])]

    selected_ids: set[int] = set()
    for idx in selected_indexes:
        selected_ids.update(groups[idx])
    source = "reaction_cluster" if reaction_smiles else "target_cluster"
    return {
        "selected_ids": selected_ids,
        "group_mode": group_mode,
        "n_groups": len(groups),
        "n_selected_groups": len(selected_indexes),
        "source": source,
    }


def _safe_name_fragment(text: str, fallback: str = "target") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip()).strip("._")
    return cleaned[:80] or fallback


def context_tempfile_path(filename: str) -> Path:
    root = Path(tempfile.gettempdir()) / "reacnet_scope_context"
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    token = uuid4().hex[:8]
    return root / f"{stamp}_{token}_{_safe_name_fragment(filename, 'context_subset.lammpstrj')}"


def route_index_cache_root() -> Path:
    root = Path(tempfile.gettempdir()) / "reacnet_scope_route_index"
    root.mkdir(parents=True, exist_ok=True)
    return root


def trajectory_index_cache_root(trajectory_file: str) -> Path:
    """Return the durable cache directory for a trajectory frame index.

    The default sits beside the simulation data so a large dataset does not
    consume the workstation system disk. ``REACNET_SCOPE_CACHE_DIR`` can point
    to a shared high-capacity cache volume when desired.
    """
    configured = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path(trajectory_file).resolve().parent / ".reacnet_scope_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def trajectory_frame_index_path(trajectory_file: str, *, mtime: float, size: int) -> Path:
    abs_path = os.path.abspath(trajectory_file)
    mtime_ns = int(round(float(mtime) * 1_000_000_000))
    digest = hashlib.sha1(
        f"trajectory-frame-index|v{TRAJECTORY_FRAME_INDEX_SCHEMA_VERSION}|{abs_path}|{mtime_ns}|{int(size)}".encode("utf-8")
    ).hexdigest()[:16]
    stem = _safe_name_fragment(Path(abs_path).stem, "trajectory")
    return trajectory_index_cache_root(abs_path) / f"{stem}.{digest}.trajectory-index.json"


def route_transition_index_path(route_file: str, *, mtime: float, size: int) -> Path:
    abs_path = os.path.abspath(route_file)
    mtime_ns = int(round(float(mtime) * 1_000_000_000))
    digest = hashlib.sha1(
        f"route-transition-index|v{ROUTE_TRANSITION_INDEX_SCHEMA_VERSION}|{abs_path}|{mtime_ns}|{int(size)}".encode("utf-8")
    ).hexdigest()[:16]
    stem = _safe_name_fragment(Path(abs_path).stem, "route")
    return route_index_cache_root() / f"{stem}.{digest}.sqlite3"


def write_context_trajectory_tempfile(text: str, filename: str) -> str:
    target = context_tempfile_path(filename)
    target.write_text(text, encoding="utf-8")
    return str(target)


def write_context_type_map_tempfile(mapping: dict[str, str], filename: str) -> str:
    target = context_tempfile_path(filename)
    if target.suffix:
        target = target.with_suffix(".txt")
    else:
        target = target.parent / f"{target.name}.txt"
    lines = ["# ReacNetScope type -> element map"]
    for key in sorted(mapping, key=lambda item: int(item) if str(item).isdigit() else item):
        lines.append(f"{key}: {mapping[key]}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


def inspect_lammpstrj_atom_columns(trajectory_file: str) -> dict[str, Any]:
    columns: list[str] = []
    with open(trajectory_file, "rb") as fh:
        for _ in range(512):
            line = fh.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text.startswith("ITEM: ATOMS"):
                columns = [str(tok).strip() for tok in text.split()[2:]]
                break
    return {
        "atom_columns": columns,
        "has_type": "type" in columns,
        "has_element": "element" in columns,
    }


def _normalize_atom_type_token(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = float(text)
    except Exception:
        return text
    if math.isfinite(number) and abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return text


def classify_context_event_row(row: dict[str, Any]) -> dict[str, Any]:
    def as_int(key: str) -> int:
        try:
            return max(0, int(row.get(key, 0) or 0))
        except Exception:
            return 0

    route_event_atoms = as_int("route_event_atom_count")
    changed_target_atoms = as_int("route_changed_target_atoms")
    reactant_to_product_atoms = as_int("route_reactant_to_product_atoms")
    product_to_reactant_atoms = as_int("route_product_to_reactant_atoms")
    anchor_target_atoms = as_int("route_target_atom_count")
    anchor_reactant_atoms = as_int("route_anchor_reactant_atom_count")
    anchor_product_atoms = as_int("route_anchor_product_atom_count")
    context_atoms = as_int("route_context_atom_count")
    has_event_window = row.get("route_event_start_frame") is not None or row.get("route_event_end_frame") is not None

    route_resolved = any(
        value > 0
        for value in (
            route_event_atoms,
            changed_target_atoms,
            reactant_to_product_atoms,
            product_to_reactant_atoms,
        )
    ) or has_event_window
    anchor_only = (
        not route_resolved
        and any(value > 0 for value in (anchor_target_atoms, anchor_reactant_atoms, anchor_product_atoms, context_atoms))
    )

    if route_resolved:
        resolution = "route_resolved"
        label = "可原子级可视化"
        reason = "route 已解析到事件变化原子，可直接下钻到 OVITO/VMD 观察事件前后轨迹"
        step2_extractable = True
        step2_visualizable = True
    elif anchor_only:
        resolution = "anchor_only"
        label = "仅锚点上下文"
        reason = "仅解析到锚点原子或上下文簇，适合看局部邻域，但不能视为已解析出真实生成/反应路径"
        step2_extractable = True
        step2_visualizable = False
    else:
        resolution = "species_only"
        label = "仅物种定位"
        reason = "这是 .species 时间序列上的物种事件；route 未解析到对应原子，不能直接重建反应动态"
        step2_extractable = False
        step2_visualizable = False

    row["event_resolution"] = resolution
    row["event_resolution_label"] = label
    row["event_resolution_reason"] = reason
    row["step2_extractable"] = bool(step2_extractable)
    row["step2_visualizable"] = bool(step2_visualizable)
    return row


def summarize_context_event_resolutions(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        key = str(row.get("event_resolution", "") or "unknown")
        counts[key] += 1
    return {key: int(value) for key, value in sorted(counts.items())}


def build_context_vmd_script(
    trajectory_path: str,
    *,
    focus_frame_index: int | None = None,
    selection_text: str = "all",
    event_atom_count: int = 0,
    source_note: str = "",
) -> str:
    safe_path = trajectory_path.replace("\\", "\\\\").replace('"', '\\"')
    safe_selection = (selection_text or "all").replace("\\", "\\\\").replace('"', '\\"')
    note_line = f"# {source_note}" if source_note else "# generated by ReacNetScope"
    frame_line = ""
    if focus_frame_index is not None and int(focus_frame_index) >= 0:
        frame_line = f"""
set __focus_frame {int(focus_frame_index)}
set __num_frames [molinfo top get numframes]
if {{ $__num_frames > 0 }} {{
  if {{ $__focus_frame >= $__num_frames }} {{
    set __focus_frame [expr {{ $__num_frames - 1 }}]
  }}
  animate goto $__focus_frame
}}
"""
    return f"""# ReacNetScope VMD helper
{note_line}
# event_atom_count={int(event_atom_count)}
display projection Orthographic
axes location Off
color Display Background white
mol new "{safe_path}" type lammpstrj waitfor all
mol delrep 0 top
mol representation DynamicBonds 1.9 0.15 12
mol color Type
mol selection "{safe_selection}"
mol material Opaque
mol addrep top
catch {{package require pbctools}}
catch {{pbc wrap -all -compound fragment -center com -centersel "all"}}
{frame_line}
display resetview
"""


def write_context_vmd_script_tempfile(text: str, filename: str) -> str:
    target = context_tempfile_path(filename)
    if target.suffix:
        target = target.with_suffix(".tcl")
    else:
        target = target.parent / f"{target.name}.tcl"
    target.write_text(text, encoding="utf-8")
    return str(target)


def open_path_with_system(path: str, mode: str = "default") -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"path not found: {target}")
    mode_key = (mode or "default").strip().lower()

    def _macos_app_arg(candidates: list[str]) -> str:
        fallback_name = ""
        for cand in candidates:
            cand_text = str(cand).strip()
            if not cand_text:
                continue
            if cand_text.endswith(".app"):
                p = Path(cand_text).expanduser()
                if p.exists():
                    return str(p)
            else:
                if not fallback_name:
                    fallback_name = cand_text
        return fallback_name

    if sys.platform == "darwin":
        if mode_key == "reveal":
            cmd = ["open", "-R", str(target)]
        elif mode_key == "ovito":
            ovito_candidates = (
                ["Ovito", "OVITO"]
                + [str(p) for p in sorted(Path("/Applications").glob("Ovito*.app"))]
                + [str(p) for p in sorted(Path("/Applications").glob("OVITO*.app"))]
            )
            cmd = ["open", "-a", _macos_app_arg(ovito_candidates), str(target)]
        elif mode_key == "vmd":
            vmd_candidates = ["VMD"] + [str(p) for p in sorted(Path("/Applications").glob("VMD*.app"))]
            app_arg = _macos_app_arg(vmd_candidates)
            if target.suffix.lower() == ".tcl":
                cmd = ["open", "-a", app_arg, "--args", "-e", str(target)]
            else:
                cmd = ["open", "-a", app_arg, str(target)]
        elif mode_key == "pymol":
            pymol_candidates = ["PyMOL"] + [str(p) for p in sorted(Path("/Applications").glob("PyMOL*.app"))]
            cmd = ["open", "-a", _macos_app_arg(pymol_candidates), str(target)]
        else:
            cmd = ["open", str(target)]
    else:
        if mode_key == "reveal":
            cmd = ["xdg-open", str(target.parent)]
        elif mode_key == "vmd":
            if target.suffix.lower() == ".tcl":
                cmd = ["vmd", "-e", str(target)]
            else:
                cmd = ["vmd", str(target)]
        else:
            cmd = ["xdg-open", str(target)]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or "failed to launch external viewer"
        raise RuntimeError(f"{mode_key} open failed: {detail}")
    return {
        "ok": True,
        "path": str(target),
        "mode": mode_key,
        "pid": None,
        "returncode": completed.returncode,
        "cmd": cmd,
    }


def _applescript_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def pick_folder_with_system(initial_dir: str = "") -> dict[str, Any]:
    """Open a native folder picker and return a local absolute folder path."""

    if sys.platform != "darwin":
        raise RuntimeError("native folder picker is currently supported on macOS only")

    script: list[str] = []
    initial = Path(initial_dir).expanduser() if initial_dir else None
    if initial and initial.is_dir():
        script.append(f"set defaultFolder to POSIX file {_applescript_string(str(initial.resolve()))}")
        script.append('set pickedFolder to choose folder with prompt "Select ReacNetGenerator output folder" default location defaultFolder')
    else:
        script.append('set pickedFolder to choose folder with prompt "Select ReacNetGenerator output folder"')
    script.append("POSIX path of pickedFolder")

    cmd: list[str] = ["osascript"]
    for line in script:
        cmd.extend(["-e", line])
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        if "-128" in message or "User canceled" in message:
            return {"ok": True, "path": "", "canceled": True}
        raise RuntimeError(message or f"folder picker failed with return code {completed.returncode}")

    path_text = (completed.stdout or "").strip()
    if not path_text:
        return {"ok": True, "path": "", "canceled": True}
    return {
        "ok": True,
        "path": str(Path(path_text).expanduser().resolve()),
        "canceled": False,
    }


def _resolve_reaction_query_text(params: dict[str, list[str]]) -> str:
    reaction_smiles = (params.get("reaction_smiles", [""])[0] or "").strip()
    reaction_formulas = (params.get("reaction_formulas", [""])[0] or "").strip()
    return reaction_smiles or reaction_formulas


def _resolve_reaction_context_files(
    params: dict[str, list[str]],
) -> dict[str, Any]:
    reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
    species_file_raw = (params.get("species_file", [""])[0] or "").strip()
    trajectory_file_raw = (params.get("trajectory_file", [""])[0] or "").strip()
    route_file_raw = (params.get("route_file", [""])[0] or "").strip()

    species_file = species_file_raw
    species_file_source_type = "species"
    if species_file.lower().endswith(".reactionabcd"):
        species_file = derive_species_path(species_file)
        species_file_source_type = "reactionabcd"
    if not species_file:
        if reac:
            species_file = derive_species_path(reac)
            species_file_source_type = "derived_from_reactionabcd"
    species_exists = bool(species_file) and os.path.exists(species_file)

    trajectory_file = trajectory_file_raw
    if not trajectory_file:
        if species_exists:
            trajectory_file = derive_trajectory_path(species_file)
        elif reac:
            trajectory_file = derive_trajectory_path(reac)
    trajectory_exists = bool(trajectory_file) and os.path.exists(trajectory_file)

    route_file = route_file_raw
    if not route_file:
        if trajectory_exists:
            route_file = derive_route_path(trajectory_file)
        elif species_exists:
            route_file = derive_route_path(species_file)
        elif reac:
            route_file = derive_route_path(reac)
    elif not route_file.lower().endswith(".route"):
        derived_route = derive_route_path(route_file)
        if os.path.exists(derived_route):
            route_file = derived_route
    route_exists = bool(route_file) and os.path.exists(route_file)

    return {
        "reac": reac,
        "species_file": species_file,
        "species_file_source_type": species_file_source_type,
        "species_exists": species_exists,
        "trajectory_file": trajectory_file,
        "trajectory_exists": trajectory_exists,
        "route_file": route_file,
        "route_exists": route_exists,
    }


def _scan_route_for_reaction_hits(
    route_file: str,
    reaction_query: dict[str, Any],
    *,
    progress_callback: Any = None,
    progress_start: float = 0.10,
    progress_span: float = 0.48,
) -> dict[str, Any]:
    return ROUTE_TRANSITION_INDEX_STORE.query_reaction_hits(
        route_file,
        reaction_query,
        progress_callback=progress_callback,
        progress_start=progress_start,
        progress_span=progress_span,
    )


def _group_reaction_hits_by_time(
    hits: list[dict[str, Any]],
    *,
    merge_gap: int,
) -> list[list[dict[str, Any]]]:
    if not hits:
        return []
    ordered = sorted(
        hits,
        key=lambda item: (
            int(item.get("start_frame", 0)),
            int(item.get("end_frame", 0)),
            int(item.get("atom_id", 0)),
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [ordered[0]]
    current_end = int(ordered[0].get("end_frame", ordered[0].get("start_frame", 0)))
    for hit in ordered[1:]:
        start_frame = int(hit.get("start_frame", hit.get("end_frame", 0)))
        end_frame = int(hit.get("end_frame", start_frame))
        if start_frame <= current_end + max(0, int(merge_gap)):
            current.append(hit)
            current_end = max(current_end, end_frame)
            continue
        groups.append(current)
        current = [hit]
        current_end = end_frame
    if current:
        groups.append(current)
    return groups


def _build_reaction_event_rows(
    *,
    reaction_query: dict[str, Any],
    time_groups: list[list[dict[str, Any]]],
    species_frames: list[int],
    trajectory_frames: list[int],
    before_frames: int,
    after_frames: int,
    trajectory_file: str,
    trajectory_exists: bool,
    type_element_map: dict[str, str] | None = None,
    progress_callback: Any = None,
    progress_start: float = 0.60,
    progress_span: float = 0.18,
) -> list[dict[str, Any]]:
    if not time_groups:
        return []

    def emit(progress: float, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": "building_events",
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    group_specs: list[dict[str, Any]] = []
    requested_focus_frames: set[int] = set()
    for group_index, group_hits in enumerate(time_groups, 1):
        route_start = min(int(hit["start_frame"]) for hit in group_hits)
        route_end = max(int(hit["end_frame"]) for hit in group_hits)
        compare_before_frame = _previous_available_frame_strict(species_frames, route_start)
        compare_after_frame = _next_available_frame_strict(species_frames, route_end)
        if compare_before_frame == compare_after_frame and species_frames:
            compare_after_frame = _next_available_frame(species_frames, route_end)
        trajectory_spec = _select_trajectory_event_frames(
            trajectory_frames if trajectory_exists else [],
            route_event_start_frame=route_start,
            route_event_end_frame=route_end,
            before_padding=before_frames,
            after_padding=after_frames,
        )
        group_specs.append(
            {
                "group_index": group_index,
                "group_hits": group_hits,
                "route_start": route_start,
                "route_end": route_end,
                "comparison_before_frame": compare_before_frame,
                "comparison_after_frame": compare_after_frame,
                "trajectory_spec": trajectory_spec,
            }
        )
        requested_focus_frames.update(
            {
                int(trajectory_spec["trajectory_pre_frame"]),
                int(trajectory_spec["trajectory_anchor_frame"]),
                int(trajectory_spec["trajectory_post_frame"]),
            }
        )

    frame_blocks: dict[int, bytes] = {}
    if trajectory_exists and requested_focus_frames:
        frame_blocks = read_trajectory_requested_frame_blocks(
            trajectory_file,
            sorted(requested_focus_frames),
            progress_callback=progress_callback,
            progress_start=progress_start,
            progress_span=progress_span * 0.65,
        )

    rows: list[dict[str, Any]] = []
    total_groups = max(1, len(time_groups))
    for spec in group_specs:
        group_index = int(spec["group_index"])
        group_hits = list(spec["group_hits"])
        route_start = int(spec["route_start"])
        route_end = int(spec["route_end"])
        compare_before_frame = int(spec["comparison_before_frame"])
        compare_after_frame = int(spec["comparison_after_frame"])
        trajectory_spec = dict(spec["trajectory_spec"])
        pre_reaction_frame = int(trajectory_spec["trajectory_pre_frame"])
        anchor_frame = int(trajectory_spec["trajectory_anchor_frame"])
        post_reaction_frame = int(trajectory_spec["trajectory_post_frame"])
        window_frames = [int(frame) for frame in trajectory_spec["trajectory_window_frames"]]
        core_candidate_ids = {int(hit["atom_id"]) for hit in group_hits}
        parsed_frames = {
            int(frame): (
                parse_lammpstrj_frame_block(frame_blocks.get(int(frame), b""))
                if frame_blocks.get(int(frame))
                else {"atoms": {}, "box": [], "frame": int(frame)}
            )
            for frame in {pre_reaction_frame, anchor_frame, post_reaction_frame}
        }
        parsed_anchor = parsed_frames.get(anchor_frame) or {"atoms": {}, "box": [], "frame": anchor_frame}
        cluster_sets = [set(core_candidate_ids)]
        if parsed_anchor.get("atoms"):
            cluster_sets = _distance_cluster_atom_ids(
                parsed_anchor["atoms"],
                parsed_anchor.get("box", []),
                core_candidate_ids,
                cutoff=6.0,
            )

        n_clusters = max(1, len(cluster_sets))
        for cluster_index, cluster_ids in enumerate(cluster_sets, 1):
            cluster_hits = [hit for hit in group_hits if int(hit["atom_id"]) in cluster_ids]
            if not cluster_hits:
                continue
            core_atom_ids = {int(hit["atom_id"]) for hit in cluster_hits}
            reactant_atom_ids = {
                int(hit["atom_id"])
                for hit in cluster_hits
                if str(hit.get("from_token", "")) in reaction_query["reactant_token_set"]
            }
            product_atom_ids = {
                int(hit["atom_id"])
                for hit in cluster_hits
                if str(hit.get("to_token", "")) in reaction_query["product_token_set"]
            }
            if parsed_frames:
                context_atom_ids, context_source = _expand_reaction_context_ids_for_frames(
                    parsed_frames,
                    focus_frames=[pre_reaction_frame, anchor_frame, post_reaction_frame],
                    core_atom_ids=core_atom_ids,
                    type_element_map=type_element_map,
                )
            else:
                context_atom_ids, context_source = set(core_atom_ids), "core_only"

            reactant_to_product_atom_ids = {
                int(hit["atom_id"])
                for hit in cluster_hits
                if str(hit.get("direction", "")) == "reactant_to_product"
            }
            product_to_reactant_atom_ids = {
                int(hit["atom_id"])
                for hit in cluster_hits
                if str(hit.get("direction", "")) == "product_to_reactant"
                }
            event_id = _build_reaction_event_id(
                reaction_query["reaction_signature"],
                anchor_frame,
                core_atom_ids,
            )
            candidate_id = event_id
            route_confidence = min(
                0.99,
                0.52
                + 0.03 * min(len(core_atom_ids), 10)
                + (0.10 if reactant_to_product_atom_ids else 0.0)
                + (0.10 if product_to_reactant_atom_ids else 0.0),
            )
            if n_clusters > 1:
                route_confidence = max(0.35, route_confidence - 0.05)
            anchor_atoms_ready = bool((parsed_frames.get(anchor_frame) or {}).get("atoms"))
            trajectory_sampling_status = str(trajectory_spec.get("trajectory_sampling_status", "sparse"))
            storyboard_ready = bool(trajectory_spec.get("trajectory_storyboard_ready"))
            if not anchor_atoms_ready:
                trajectory_sampling_status = "sparse"
                storyboard_ready = False
            context_mode = str(context_source or "core_only")
            visualization_ready = (
                trajectory_sampling_status == "good"
                and storyboard_ready
                and context_mode in {"same_molecule_union", "connected_component_union"}
            )
            event_resolution_label = "候选过程"
            event_resolution_reason = "事件候选已定位，但是否能作为严格反应事件仍需净变化与可视化条件校验"
            if visualization_ready:
                event_resolution_label = "候选过程(可视化就绪)"
                event_resolution_reason = "route 已解析到变化原子，轨迹采样与分子上下文恢复满足局部可视化要求"
            row = {
                "candidate_id": candidate_id,
                "event_id": event_id,
                "reaction_smiles": reaction_query["raw"],
                "reaction_match_mode": reaction_query["match_mode"],
                "event_type": "reaction",
                "anchor_frame": int(anchor_frame),
                "pre_reaction_frame": int(pre_reaction_frame),
                "post_reaction_frame": int(post_reaction_frame),
                "trajectory_pre_frame": int(pre_reaction_frame),
                "trajectory_anchor_frame": int(anchor_frame),
                "trajectory_post_frame": int(post_reaction_frame),
                "comparison_before_frame": int(compare_before_frame),
                "comparison_after_frame": int(compare_after_frame),
                "route_event_start_frame": int(route_start),
                "route_event_end_frame": int(route_end),
                "window_start": int(trajectory_spec["trajectory_window_start"]),
                "window_end": int(trajectory_spec["trajectory_window_end"]),
                "window_frames": [int(frame) for frame in window_frames],
                "trajectory_window_start": int(trajectory_spec["trajectory_window_start"]),
                "trajectory_window_end": int(trajectory_spec["trajectory_window_end"]),
                "trajectory_window_frames": [int(frame) for frame in window_frames],
                "reactant_atom_ids": sorted(reactant_atom_ids),
                "product_atom_ids": sorted(product_atom_ids),
                "core_atom_ids": sorted(core_atom_ids),
                "context_atom_ids": sorted(context_atom_ids),
                "core_atom_count": len(core_atom_ids),
                "context_atom_count": len(context_atom_ids),
                "route_confidence": round(route_confidence, 3),
                "reaction_confidence": 0.0,
                "confidence": 0.0,
                "failure_reason": "",
                "event_quality": "route_candidate",
                "verification_status": "candidate_route_only",
                "matched_smiles_at_anchor": _sample_route_labels(cluster_hits, "to_label"),
                "transition_from_samples": _sample_route_labels(cluster_hits, "from_label"),
                "transition_to_samples": _sample_route_labels(cluster_hits, "to_label"),
                "time_group_index": group_index,
                "spatial_cluster_index": cluster_index,
                "route_event_atom_count": len(core_atom_ids),
                "route_event_atom_ids": _event_ids_text(core_atom_ids),
                "route_context_atom_count": len(context_atom_ids),
                "route_context_atom_ids": _event_ids_text(context_atom_ids),
                "route_reactant_atom_count": len(reactant_atom_ids),
                "route_reactant_atom_ids": _event_ids_text(reactant_atom_ids),
                "route_product_atom_count": len(product_atom_ids),
                "route_product_atom_ids": _event_ids_text(product_atom_ids),
                "route_context_atom_source": context_source,
                "route_context_group_mode": "distance_cluster" if n_clusters > 1 else context_source,
                "route_context_group_count": n_clusters,
                "route_context_selected_group_count": 1,
                "route_reactant_to_product_atoms": len(reactant_to_product_atom_ids),
                "route_reactant_to_product_atom_ids": _event_ids_text(reactant_to_product_atom_ids),
                "route_product_to_reactant_atoms": len(product_to_reactant_atom_ids),
                "route_product_to_reactant_atom_ids": _event_ids_text(product_to_reactant_atom_ids),
                "route_target_atom_count": len(core_atom_ids),
                "route_target_atom_ids": _event_ids_text(core_atom_ids),
                "route_changed_target_atoms": len(core_atom_ids),
                "route_changed_target_atom_ids": _event_ids_text(core_atom_ids),
                "n_window_frames": len(window_frames),
                "trajectory_sampling_status": trajectory_sampling_status,
                "context_reconstruction_mode": context_mode,
                "visualization_ready": bool(visualization_ready),
                "step2_extractable": True,
                "step2_visualizable": bool(visualization_ready),
                "event_resolution": "reaction_candidate",
                "event_resolution_label": event_resolution_label,
                "event_resolution_reason": event_resolution_reason,
            }
            rows.append(row)

        emit(
            progress_start + progress_span * min(group_index / total_groups, 1.0),
            f"Building reaction-event candidates: {group_index}/{total_groups}",
            n_event_rows=len(rows),
            anchor_frame=anchor_frame,
        )

    rows.sort(
        key=lambda row: (
            int(row.get("route_event_start_frame", row.get("anchor_frame", 0))),
            int(row.get("anchor_frame", 0)),
            -int(row.get("core_atom_count", 0)),
        )
    )
    for idx, row in enumerate(rows, 1):
        row["event_index"] = idx
    return rows


def build_reaction_event_locate_payload(
    params: dict[str, list[str]],
    progress_callback: Any = None,
) -> dict[str, Any]:
    files = _resolve_reaction_context_files(params)
    reaction_text = _resolve_reaction_query_text(params)
    if not reaction_text:
        raise ValueError("missing reaction_smiles / reaction_formulas")
    if not files["species_exists"]:
        raise FileNotFoundError(f"species file not found: {files['species_file']}")
    if not files["route_exists"]:
        raise FileNotFoundError(f"route file not found: {files['route_file']}")

    before_frames = max(0, int_param(params, "before_frames", 5))
    after_frames = max(0, int_param(params, "after_frames", 5))
    max_events = max(1, min(int_param(params, "max_events", 12), 200))
    defer_trajectory_verification = bool_param(params, "defer_trajectory_verification", False)
    type_element_map = parse_type_element_map_specs((params.get("type_element_map", [""])[0] or "").strip())
    reaction_query = _prepare_reaction_query(reaction_text)

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

    report(0.01, "starting", "Starting reaction-event location")
    species_index = SPECIES_FRAME_INDEX_STORE.get(
        files["species_file"],
        progress_callback=progress_callback,
        progress_start=0.02,
        progress_span=0.08,
    )
    species_frames = [int(frame) for frame in species_index.frames]
    if not species_frames:
        raise RuntimeError(f"no timestep rows found in species file: {files['species_file']}")
    step = _infer_frame_step(species_frames)

    scan_result = _scan_route_for_reaction_hits(
        files["route_file"],
        reaction_query,
        progress_callback=progress_callback,
        progress_start=0.10,
        progress_span=0.48,
    )
    raw_hits = list(scan_result["hits"])
    if not raw_hits:
        report(1.0, "completed", "No route-matched reaction process was found")
        return {
            "ok": True,
            "query": {
                "reac": files["reac"],
                "species_file": files["species_file"],
                "trajectory_file": files["trajectory_file"],
                "route_file": files["route_file"],
                "reaction_smiles": reaction_query["raw"],
                "reaction_match_mode": reaction_query["match_mode"],
                "before_frames": before_frames,
                "after_frames": after_frames,
                "max_events": max_events,
            },
            "meta": {
                "status": "failed",
                "message": "route file did not resolve any atom-level transition that matches this candidate reaction",
                "failure_reason": "route_no_matching_transition",
                "reaction_smiles": reaction_query["raw"],
                "reaction_match_mode": reaction_query["match_mode"],
                "species_file": files["species_file"],
                "route_file": files["route_file"],
                "trajectory_file": files["trajectory_file"],
                "species_timestep_step": step,
                "matched_atom_transitions": 0,
                "route_index_path": str((scan_result.get("route_index", {}) or {}).get("index_path", "")),
                "route_index_transitions": int((scan_result.get("route_index", {}) or {}).get("indexed_transitions", 0) or 0),
                "route_index_state": str((scan_result.get("route_index", {}) or {}).get("index_state", "")),
                "rows": 0,
                "candidate_rows": 0,
                "discarded_rows": 0,
            },
            "rows": [],
            "candidate_rows": [],
            "discarded_rows": [],
        }

    trajectory_frames: list[int] = []
    trajectory_index_state = "missing"
    trajectory_index_path = ""
    if files["trajectory_exists"] and not defer_trajectory_verification:
        trajectory_index = TRAJECTORY_INDEX_STORE.get(
            files["trajectory_file"],
            progress_callback=progress_callback,
            progress_start=0.58,
            progress_span=0.10,
        )
        trajectory_frames = [int(frame) for frame in trajectory_index.frames]
        trajectory_index_state = "cached_or_built"
        trajectory_index_path = os.path.abspath(files["trajectory_file"])
    elif files["trajectory_exists"]:
        trajectory_index_state = "deferred_until_extraction"

    time_groups = _group_reaction_hits_by_time(raw_hits, merge_gap=max(1, step))
    candidate_rows = _build_reaction_event_rows(
        reaction_query=reaction_query,
        time_groups=time_groups,
        species_frames=species_frames,
        trajectory_frames=trajectory_frames,
        before_frames=before_frames,
        after_frames=after_frames,
        trajectory_file=str(files["trajectory_file"]),
        trajectory_exists=bool(files["trajectory_exists"]) and not defer_trajectory_verification,
        type_element_map=type_element_map,
        progress_callback=progress_callback,
        progress_start=0.68,
        progress_span=0.12,
    )
    comparison_frames = sorted(
        {
            int(row.get("comparison_before_frame", row.get("route_event_start_frame", 0)))
            for row in candidate_rows
        }
        | {
            int(row.get("comparison_after_frame", row.get("anchor_frame", 0)))
            for row in candidate_rows
        }
    )
    report(0.79, "reading_species", "Validating route candidates against species net changes")
    species_snapshots = SPECIES_TOKEN_SNAPSHOT_STORE.get(
        files["species_file"],
        requested_frames=comparison_frames,
        query_tokens=_reaction_query_token_order(reaction_query),
        match_mode=str(reaction_query["match_mode"]),
        progress_callback=progress_callback,
        progress_start=0.80,
        progress_span=0.12,
    )
    rows, candidate_process_rows, discarded_rows = _classify_reaction_candidate_rows(
        candidate_rows,
        reaction_query=reaction_query,
        species_snapshots=species_snapshots,
    )
    rows = rows[:max_events]
    candidate_process_rows = candidate_process_rows[: max(max_events * 3, max_events)]
    for idx, row in enumerate(rows, 1):
        row["event_index"] = idx
    for idx, row in enumerate(candidate_process_rows, 1):
        row["candidate_index"] = idx
    if discarded_rows:
        for idx, row in enumerate(discarded_rows, 1):
            row["candidate_index"] = idx

    status = "ok" if rows else ("candidate_only" if candidate_process_rows else "failed")
    if rows:
        message = f"Located {len(rows)} strict reaction event(s)"
    elif candidate_process_rows:
        message = f"No strict reaction event passed; kept {len(candidate_process_rows)} related candidate process(es)"
    elif discarded_rows:
        message = "Route candidates were found, but none passed the net-reaction consistency checks"
    else:
        message = "No reaction-event candidate survived route/species validation"
    if defer_trajectory_verification:
        message = f"{message}; trajectory verification is deferred until an event is selected"
    report(
        1.0,
        "completed",
        message,
        rows=len(rows),
        candidate_rows=len(candidate_process_rows),
        discarded_rows=len(discarded_rows),
    )
    return {
        "ok": True,
        "query": {
            "reac": files["reac"],
            "species_file": files["species_file"],
            "trajectory_file": files["trajectory_file"],
            "route_file": files["route_file"],
            "reaction_smiles": reaction_query["raw"],
            "reaction_match_mode": reaction_query["match_mode"],
            "before_frames": before_frames,
            "after_frames": after_frames,
            "max_events": max_events,
            "defer_trajectory_verification": defer_trajectory_verification,
            "type_element_map": ";".join(f"{key}:{value}" for key, value in sorted(type_element_map.items(), key=lambda item: int(item[0]))),
        },
        "meta": {
            "status": status,
            "message": message,
            "reaction_smiles": reaction_query["raw"],
            "reaction_match_mode": reaction_query["match_mode"],
            "species_file": files["species_file"],
            "route_file": files["route_file"],
            "trajectory_file": files["trajectory_file"],
            "species_timestep_step": step,
            "matched_atom_transitions": int(scan_result["matched_atom_transitions"]),
            "scanned_route_atoms": int(scan_result["scanned_atoms"]),
            "route_index_path": str((scan_result.get("route_index", {}) or {}).get("index_path", "")),
            "route_index_transitions": int((scan_result.get("route_index", {}) or {}).get("indexed_transitions", 0) or 0),
            "route_index_state": str((scan_result.get("route_index", {}) or {}).get("index_state", "")),
            "trajectory_index_state": trajectory_index_state,
            "trajectory_index_path": trajectory_index_path,
            "trajectory_index_frames": len(trajectory_frames),
            "trajectory_verification_deferred": defer_trajectory_verification,
            "temporal_groups": len(time_groups),
            "provisional_candidate_rows": len(candidate_rows),
            "candidate_rows": len(candidate_process_rows),
            "discarded_rows": len(discarded_rows),
            "rows": len(rows),
        },
        "rows": rows,
        "candidate_rows": candidate_process_rows,
        "discarded_rows": discarded_rows,
    }


def _build_storyboard_frames(
    frame_rows: list[dict[str, Any]],
    selected_event: dict[str, Any],
) -> tuple[list[int], list[dict[str, Any]]]:
    ordered_frames = [int(row["frame"]) for row in frame_rows if row.get("frame") is not None]
    if not ordered_frames:
        return [], []
    pre_reaction_frame = int(selected_event.get("pre_reaction_frame", ordered_frames[0]))
    anchor_frame = int(selected_event.get("anchor_frame", ordered_frames[min(len(ordered_frames) - 1, len(ordered_frames) // 2)]))
    post_reaction_frame = int(selected_event.get("post_reaction_frame", ordered_frames[-1]))

    def nearest(frame_value: int) -> int:
        return _nearest_available_frame(ordered_frames, frame_value)

    candidates = [
        ("pre_start", ordered_frames[0]),
        ("pre_reaction", nearest(pre_reaction_frame)),
        ("anchor", nearest(anchor_frame)),
        ("post_reaction", nearest(post_reaction_frame)),
        ("post_end", ordered_frames[-1]),
    ]
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for label, frame in candidates:
        if frame in seen:
            continue
        seen.add(frame)
        items.append({"label": label, "frame": int(frame)})
    return [int(item["frame"]) for item in items], items


def build_reaction_event_extract_payload(
    params: dict[str, list[str]],
    progress_callback: Any = None,
) -> dict[str, Any]:
    files = _resolve_reaction_context_files(params)
    if not files["trajectory_exists"]:
        raise FileNotFoundError(f"trajectory file not found: {files['trajectory_file']}")
    if not files["species_exists"]:
        raise FileNotFoundError(f"species file not found: {files['species_file']}")
    if not files["route_exists"]:
        raise FileNotFoundError(f"route file not found: {files['route_file']}")

    reaction_text = _resolve_reaction_query_text(params)
    if not reaction_text:
        raise ValueError("missing reaction_smiles / reaction_formulas")
    event_id = (params.get("event_id", [""])[0] or "").strip()
    if not event_id:
        raise ValueError("missing event_id")

    type_element_map = parse_type_element_map_specs((params.get("type_element_map", [""])[0] or "").strip())
    inline_viewer = bool_param(params, "inline_viewer", False)
    before_frames = max(0, int_param(params, "before_frames", 5))
    after_frames = max(0, int_param(params, "after_frames", 5))
    max_events = max(1, min(int_param(params, "max_events", 200), 200))

    locate_params = {
        "reac": [files["reac"]],
        "species_file": [files["species_file"]],
        "trajectory_file": [files["trajectory_file"]],
        "route_file": [files["route_file"]],
        "reaction_smiles": [reaction_text],
        "before_frames": [str(before_frames)],
        "after_frames": [str(after_frames)],
        "max_events": [str(max_events)],
        "type_element_map": [";".join(f"{key}:{value}" for key, value in sorted(type_element_map.items(), key=lambda item: int(item[0])))],
    }
    locate_payload = REACTION_EVENT_LOCATE_STORE.get(
        locate_params,
        progress_callback=progress_callback,
        progress_start=0.02,
        progress_span=0.56,
    )
    rows = list(locate_payload.get("rows") or [])
    candidate_rows = list(locate_payload.get("candidate_rows") or [])
    selected_event = next((row for row in rows if str(row.get("event_id", "")) == event_id), None)
    selected_event_class = "verified"
    if selected_event is None:
        selected_event = next((row for row in candidate_rows if str(row.get("event_id", "")) == event_id), None)
        selected_event_class = "candidate"
    if selected_event is None:
        raise ValueError(f"selected event_id not found in current locate result: {event_id}")

    selected_frames = [
        int(frame)
        for frame in (
            selected_event.get("trajectory_window_frames")
            or selected_event.get("window_frames")
            or []
        )
    ]
    if not selected_frames:
        raise RuntimeError("selected event does not contain window_frames")
    context_atom_ids = {int(atom_id) for atom_id in selected_event.get("context_atom_ids", [])}
    if not context_atom_ids:
        raise RuntimeError("selected event does not contain context_atom_ids")
    atom_groups = {
        "core_atom_ids": [int(atom_id) for atom_id in selected_event.get("core_atom_ids", [])],
        "context_atom_ids": [int(atom_id) for atom_id in selected_event.get("context_atom_ids", [])],
        "reactant_atom_ids": [int(atom_id) for atom_id in selected_event.get("reactant_atom_ids", [])],
        "product_atom_ids": [int(atom_id) for atom_id in selected_event.get("product_atom_ids", [])],
    }
    event_truth_summary = {
        "selected_event_class": selected_event_class,
        "verification_status": str(selected_event.get("verification_status", "") or ""),
        "expected_delta_summary": str(selected_event.get("expected_delta_summary", "") or ""),
        "observed_delta_summary": str(selected_event.get("observed_delta_summary", "") or ""),
        "trajectory_sampling_status": str(selected_event.get("trajectory_sampling_status", "") or ""),
        "context_reconstruction_mode": str(selected_event.get("context_reconstruction_mode", "") or ""),
    }

    trajectory_atom_info = inspect_lammpstrj_atom_columns(files["trajectory_file"])
    subset_result = extract_lammpstrj_subset(
        files["trajectory_file"],
        selected_frames,
        frame_atom_filters={int(frame): set(context_atom_ids) for frame in selected_frames},
        type_element_map=type_element_map,
        progress_callback=progress_callback,
        inline_text_limit=2_500_000 if inline_viewer else 0,
        preview_text_limit=10_000_000,
        output_filename=f"{selected_event['event_id']}_context_subset.lammpstrj",
    )
    anchor_frame = int(selected_event.get("anchor_frame", selected_frames[0]))
    focus_frame_index = selected_frames.index(anchor_frame) if anchor_frame in selected_frames else 0
    vmd_script = build_context_vmd_script(
        subset_result["trajectory_saved_path"],
        focus_frame_index=focus_frame_index,
        selection_text="all",
        event_atom_count=int(selected_event.get("core_atom_count", 0)),
        source_note=f"reaction-first event {selected_event['event_id']}",
    )
    vmd_script_path = write_context_vmd_script_tempfile(vmd_script, f"{selected_event['event_id']}_context_subset.tcl")
    type_map_saved_path = ""
    if type_element_map and not trajectory_atom_info.get("has_element"):
        type_map_saved_path = write_context_type_map_tempfile(type_element_map, f"{selected_event['event_id']}_type_map.txt")

    frame_rows: list[dict[str, Any]] = []
    storyboard_frames, snapshot_items = _build_storyboard_frames(
        [{"frame": int(frame)} for frame in selected_frames],
        selected_event,
    )
    storyboard_frame_set = set(storyboard_frames)
    pre_reaction_frame = int(selected_event.get("pre_reaction_frame", selected_frames[0]))
    post_reaction_frame = int(selected_event.get("post_reaction_frame", selected_frames[-1]))
    for frame in selected_frames:
        phase = ""
        if frame == storyboard_frames[0] if storyboard_frames else False:
            phase = "pre_start"
        elif frame == pre_reaction_frame:
            phase = "pre_reaction"
        elif frame == anchor_frame:
            phase = "anchor"
        elif frame == post_reaction_frame:
            phase = "post_reaction"
        elif storyboard_frames and frame == storyboard_frames[-1]:
            phase = "post_end"
        frame_rows.append(
            {
                "frame": int(frame),
                "event_refs": f"{selected_event['event_id']}@{anchor_frame}",
                "core_atom_count": int(selected_event.get("core_atom_count", 0)),
                "context_atom_count": int(selected_event.get("context_atom_count", 0)),
                "pre_reaction_frame": int(selected_event.get("pre_reaction_frame", anchor_frame)),
                "post_reaction_frame": int(selected_event.get("post_reaction_frame", anchor_frame)),
                "route_target_atom_count": int(selected_event.get("route_target_atom_count", selected_event.get("core_atom_count", 0))),
                "route_target_atom_ids": selected_event.get("route_target_atom_ids", selected_event.get("route_event_atom_ids", "")),
                "reaction_smiles": selected_event.get("reaction_smiles", ""),
                "storyboard_phase": phase,
                "is_storyboard_frame": int(frame) in storyboard_frame_set,
                "route_event_start_frame": int(selected_event.get("route_event_start_frame", anchor_frame)),
                "route_event_end_frame": int(selected_event.get("route_event_end_frame", anchor_frame)),
                "route_event_atom_ids": selected_event.get("route_event_atom_ids", ""),
                "route_context_atom_ids": selected_event.get("route_context_atom_ids", ""),
                "route_reactant_atom_count": int(selected_event.get("route_reactant_atom_count", len(selected_event.get("reactant_atom_ids", []) or []))),
                "route_reactant_atom_ids": selected_event.get("route_reactant_atom_ids", _event_ids_text(selected_event.get("reactant_atom_ids", []) or [])),
                "route_product_atom_count": int(selected_event.get("route_product_atom_count", len(selected_event.get("product_atom_ids", []) or []))),
                "route_product_atom_ids": selected_event.get("route_product_atom_ids", _event_ids_text(selected_event.get("product_atom_ids", []) or [])),
                "route_reactant_to_product_atom_ids": selected_event.get("route_reactant_to_product_atom_ids", ""),
                "route_product_to_reactant_atom_ids": selected_event.get("route_product_to_reactant_atom_ids", ""),
            }
        )

    return {
        "ok": True,
        "query": {
            "reac": files["reac"],
            "species_file": files["species_file"],
            "trajectory_file": files["trajectory_file"],
            "route_file": files["route_file"],
            "reaction_smiles": reaction_text,
            "event_id": event_id,
            "before_frames": before_frames,
            "after_frames": after_frames,
        },
        "meta": {
            "status": "ok",
            "message": "reaction-event context extraction completed",
            "reaction_smiles": reaction_text,
            "event_id": event_id,
            "selected_event_class": selected_event_class,
            "context_scope_mode": selected_event.get("route_context_atom_source", ""),
            "core_atom_count": len(atom_groups["core_atom_ids"]),
            "context_atom_count": len(atom_groups["context_atom_ids"]),
            "reactant_atom_count": len(atom_groups["reactant_atom_ids"]),
            "product_atom_count": len(atom_groups["product_atom_ids"]),
            "event_truth_summary": event_truth_summary,
            "trajectory_window_start": int(selected_event.get("trajectory_window_start", selected_frames[0])),
            "trajectory_window_end": int(selected_event.get("trajectory_window_end", selected_frames[-1])),
            "trajectory_saved_path": subset_result["trajectory_saved_path"],
            "vmd_script_saved_path": vmd_script_path,
            "type_map_saved_path": type_map_saved_path,
            "matched_blocks": subset_result.get("matched_blocks", 0),
            "subset_bytes": subset_result.get("subset_bytes", 0),
            "preview_blocks": subset_result.get("preview_blocks", 0),
            "trajectory_has_element_column": bool(trajectory_atom_info.get("has_element")),
        },
        "rows": [selected_event],
        "selected_event_class": selected_event_class,
        "selected_event": selected_event,
        "event_truth_summary": event_truth_summary,
        "atom_groups": atom_groups,
        "frame_rows": frame_rows,
        "trajectory_text": subset_result.get("trajectory_text", ""),
        "trajectory_preview_text": subset_result.get("trajectory_preview_text", ""),
        "trajectory_saved_path": subset_result["trajectory_saved_path"],
        "trajectory_window_start": int(selected_event.get("trajectory_window_start", selected_frames[0])),
        "trajectory_window_end": int(selected_event.get("trajectory_window_end", selected_frames[-1])),
        "vmd_script_saved_path": vmd_script_path,
        "type_map_saved_path": type_map_saved_path,
        "storyboard_frames": storyboard_frames,
        "snapshot_items": snapshot_items,
        "suggested_files": {
            "trajectory": Path(subset_result["trajectory_saved_path"]).name,
            "frames_csv": f"{selected_event['event_id']}_frames.csv",
        },
    }


def build_structure_context_payload(
    params: dict[str, list[str]],
    progress_callback: Any = None,
) -> dict[str, Any]:
    reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
    species_file_raw = (params.get("species_file", [""])[0] or "").strip()
    trajectory_file_raw = (params.get("trajectory_file", [""])[0] or "").strip()
    route_file_raw = (params.get("route_file", [""])[0] or "").strip()
    target = (params.get("target", [""])[0] or "").strip()
    reaction_smiles = (params.get("reaction_smiles", [""])[0] or "").strip()
    frame_ranges_raw = (params.get("frame_ranges", [""])[0] or "").strip()
    atom_ids_raw = (params.get("atom_ids", [""])[0] or "").strip()
    type_element_map_raw = (params.get("type_element_map", [""])[0] or "").strip()
    anchor_frame_raw = (params.get("anchor_frame", [""])[0] or "").strip()
    match_mode = (params.get("match_mode", ["auto"])[0] or "auto").strip().lower()
    event_mode = (params.get("event_mode", ["appear"])[0] or "appear").strip().lower()
    before_frames = max(0, int_param(params, "before_frames", 3))
    after_frames = max(0, int_param(params, "after_frames", 3))
    max_events = max(1, min(int_param(params, "max_events", 12), 200))
    include_trajectory = bool_param(params, "include_trajectory", True)
    include_route_trace = bool_param(params, "include_route_trace", False)
    inline_viewer = bool_param(params, "inline_viewer", False)
    route_atom_sample_limit = max(1, min(int_param(params, "route_atom_sample_limit", 80), 200))
    trajectory_atom_scope = (params.get("trajectory_atom_scope", ["all"])[0] or "all").strip().lower()
    if trajectory_atom_scope not in {"all", "event"}:
        trajectory_atom_scope = "all"
    frame_range_specs = parse_frame_range_specs(frame_ranges_raw)
    use_frame_ranges = bool(frame_range_specs)
    manual_atom_ids = parse_atom_id_specs(atom_ids_raw)
    use_manual_atom_ids = bool(manual_atom_ids)
    type_element_map = parse_type_element_map_specs(type_element_map_raw)
    anchor_frame_override: int | None = None
    if anchor_frame_raw:
        try:
            anchor_frame_override = int(anchor_frame_raw)
        except Exception as exc:
            raise ValueError(f"invalid anchor_frame: {anchor_frame_raw}") from exc

    if not target and not use_frame_ranges and anchor_frame_override is None:
        raise ValueError("missing target (SMILES / Formula)")

    species_file = species_file_raw
    species_file_source_type = "species"
    if species_file_raw.lower().endswith(".reactionabcd"):
        species_file = derive_species_path(species_file_raw)
        species_file_source_type = "reactionabcd"
    if not species_file:
        if reac:
            species_file = derive_species_path(reac)
            species_file_source_type = "derived_from_reactionabcd"
        else:
            species_file = ""
            species_file_source_type = "none"
    species_exists = bool(species_file) and os.path.exists(species_file)
    if not species_exists and not use_frame_ranges:
        raise FileNotFoundError(f"species file not found: {species_file}")

    trajectory_file = trajectory_file_raw
    if not trajectory_file:
        if species_exists:
            trajectory_file = derive_trajectory_path(species_file)
        elif reac:
            trajectory_file = derive_trajectory_path(reac)
    trajectory_exists = bool(trajectory_file) and os.path.exists(trajectory_file)
    trajectory_atom_info = inspect_lammpstrj_atom_columns(trajectory_file) if trajectory_exists else {
        "atom_columns": [],
        "has_type": False,
        "has_element": False,
    }

    route_file = route_file_raw
    if not route_file:
        if trajectory_exists:
            route_file = derive_route_path(trajectory_file)
        elif species_exists:
            route_file = derive_route_path(species_file)
        elif reac:
            route_file = derive_route_path(reac)
    elif not route_file.lower().endswith(".route"):
        derived_route = derive_route_path(route_file)
        if os.path.exists(derived_route):
            route_file = derived_route
    route_exists = bool(route_file) and os.path.exists(route_file)
    resolved_match_mode = resolve_context_match_mode(target, match_mode)

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

    report(0.01, "starting", "Starting structure-context extraction")
    timeline: list[dict[str, Any]] = []
    matched_totals: Counter[str] = Counter()
    frame_details: dict[int, list[tuple[str, int]]] = {}
    frame_to_count: dict[int, int] = {}
    event_rows: list[dict[str, Any]] = []
    frame_event_map: dict[int, list[str]] = {}
    selected_frames: list[int] = []
    preselected_range_frames: list[int] = []

    if target and species_exists and not use_frame_ranges:
        timeline, matched_totals = extract_target_timeline(
            species_file,
            target,
            resolved_match_mode,
            progress_callback=progress_callback,
        )
        if not timeline:
            raise RuntimeError(f"no valid timestep rows found in species file: {species_file}")
        frame_to_count = {int(item["frame"]): int(item["count"]) for item in timeline}
    elif species_exists:
        timeline = [{"frame": frame, "count": 0} for frame in collect_species_timestep_index(species_file, progress_callback=progress_callback)]
    elif trajectory_exists and use_frame_ranges:
        preselected_range_frames = collect_trajectory_frames_by_ranges(
            trajectory_file,
            frame_range_specs,
            progress_callback=progress_callback,
            progress_start=0.02,
            progress_span=0.40,
        )
        timeline = [{"frame": frame, "count": 0} for frame in preselected_range_frames]
    elif trajectory_exists:
        timeline = [{"frame": frame, "count": 0} for frame in collect_trajectory_timestep_index(trajectory_file, progress_callback=progress_callback)]
    else:
        raise FileNotFoundError("neither species file nor trajectory file could be resolved")

    if use_frame_ranges:
        if preselected_range_frames:
            selected_frames = sorted({int(frame) for frame in preselected_range_frames})
        else:
            available_frames = [int(item["frame"]) for item in timeline]
            selected_frames = expand_frames_by_ranges(available_frames, frame_range_specs)
        if target and species_exists and selected_frames:
            frame_details = collect_frame_match_details(
                species_file,
                target,
                resolved_match_mode,
                set(selected_frames),
                progress_callback=progress_callback,
            )
            frame_to_count = {
                int(frame): int(sum(max(int(cnt), 0) for _smi, cnt in hits))
                for frame, hits in frame_details.items()
            }
        for idx, (req_start, req_end) in enumerate(frame_range_specs, 1):
            hits = [frame for frame in selected_frames if req_start <= frame <= req_end]
            anchor = hits[0] if hits else None
            for frame in hits:
                frame_event_map.setdefault(int(frame), []).append(f"range@{req_start}-{req_end}")
            anchor_hits = frame_details.get(anchor, []) if anchor is not None else []
            event_rows.append(
                {
                    "range_index": idx,
                    "event_type": "frame_range",
                    "requested_start": req_start,
                    "requested_end": req_end,
                    "first_frame_found": hits[0] if hits else None,
                    "last_frame_found": hits[-1] if hits else None,
                    "n_window_frames": len(hits),
                    "anchor_frame": anchor,
                    "count_at_frame": frame_to_count.get(anchor, 0) if anchor is not None else None,
                    "matched_smiles_at_anchor": "; ".join(f"{smi}({cnt})" for smi, cnt in anchor_hits[:6]),
                }
            )
    else:
        if anchor_frame_override is not None:
            available_frames = [int(item["frame"]) for item in timeline]
            if not available_frames:
                raise RuntimeError("no timeline frames available for anchor extraction")
            if anchor_frame_override not in set(available_frames):
                raise ValueError(f"anchor_frame not found in timeline: {anchor_frame_override}")
            anchor_idx = available_frames.index(anchor_frame_override)
            lo = max(0, anchor_idx - max(0, before_frames))
            hi = min(len(available_frames) - 1, anchor_idx + max(0, after_frames))
            window_frames = available_frames[lo : hi + 1]
            anchor_count = int(timeline[anchor_idx]["count"])
            prev_count = int(timeline[anchor_idx - 1]["count"]) if anchor_idx > 0 else anchor_count
            next_count = int(timeline[anchor_idx + 1]["count"]) if anchor_idx < (len(available_frames) - 1) else anchor_count
            events = [
                {
                    "event_index": 1,
                    "event_type": f"anchor_{event_mode}",
                    "anchor_frame": int(anchor_frame_override),
                    "prev_count": prev_count,
                    "count_at_frame": anchor_count,
                    "next_count": next_count,
                    "delta_from_prev": anchor_count - prev_count,
                    "window_start": int(window_frames[0]) if window_frames else int(anchor_frame_override),
                    "window_end": int(window_frames[-1]) if window_frames else int(anchor_frame_override),
                    "window_frames": list(window_frames),
                }
            ]
        else:
            events = locate_context_events(
                timeline,
                event_mode=event_mode,
                before_frames=before_frames,
                after_frames=after_frames,
                max_events=max_events,
            )
        selected_frames = sorted({frame for event in events for frame in event["window_frames"]})
        frame_details = collect_frame_match_details(
            species_file,
            target,
            resolved_match_mode,
            set(selected_frames),
            progress_callback=progress_callback,
        )
        for event in events:
            anchor = int(event["anchor_frame"])
            for frame in event["window_frames"]:
                frame_event_map.setdefault(int(frame), []).append(f"{event['event_type']}@{anchor}")
            anchor_hits = frame_details.get(anchor, [])
            event_rows.append(
                {
                    "event_index": event["event_index"],
                    "event_type": event["event_type"],
                    "anchor_frame": anchor,
                    "prev_count": event["prev_count"],
                    "count_at_frame": event["count_at_frame"],
                    "next_count": event["next_count"],
                    "delta_from_prev": event["delta_from_prev"],
                    "window_start": event["window_start"],
                    "window_end": event["window_end"],
                    "n_window_frames": len(event["window_frames"]),
                    "matched_smiles_at_anchor": "; ".join(f"{smi}({cnt})" for smi, cnt in anchor_hits[:6]),
                }
            )

    frame_rows = [
        {
            "frame": frame,
            "target_count": frame_to_count.get(frame, 0) if target else None,
            "event_refs": "; ".join(frame_event_map.get(frame, [])),
            "matched_smiles": "; ".join(f"{smi}({cnt})" for smi, cnt in frame_details.get(frame, [])[:8]),
            "n_matched_smiles": len(frame_details.get(frame, [])),
        }
        for frame in selected_frames
    ]

    timeline_frames = [int(item["frame"]) for item in timeline]
    frame_index = {frame: idx for idx, frame in enumerate(timeline_frames)}
    anchor_frames: list[int] = []
    for row in event_rows:
        value = row.get("anchor_frame")
        if value is None:
            continue
        try:
            anchor_frame = int(value)
        except Exception:
            continue
        anchor_frames.append(anchor_frame)
    anchor_frames = sorted(set(anchor_frames))
    previous_frame_of_anchor: dict[int, int] = {}
    for anchor_frame in anchor_frames:
        idx = frame_index.get(anchor_frame, -1)
        if idx > 0:
            previous_frame_of_anchor[anchor_frame] = timeline_frames[idx - 1]
        else:
            previous_frame_of_anchor[anchor_frame] = anchor_frame

    route_note = ""
    frame_atom_filters: dict[int, set[int]] | None = None
    route_meta: dict[str, Any] = {
        "route_file": route_file,
        "route_found": route_exists,
        "route_enabled": include_route_trace,
        "route_bypassed_by_manual_atom_ids": use_manual_atom_ids,
        "route_target_match_mode": resolved_match_mode,
        "route_reaction_smiles": reaction_smiles,
        "route_atom_sample_limit": route_atom_sample_limit,
        "trajectory_atom_scope": trajectory_atom_scope,
        "manual_atom_ids_enabled": use_manual_atom_ids,
        "manual_atom_ids_count": len(manual_atom_ids),
        "manual_atom_ids_sample": ",".join(str(x) for x in sorted(manual_atom_ids)[:24]),
        "trajectory_atom_columns": trajectory_atom_info.get("atom_columns", []),
        "trajectory_has_element_column": bool(trajectory_atom_info.get("has_element", False)),
        "trajectory_has_type_column": bool(trajectory_atom_info.get("has_type", False)),
        "type_element_map": dict(type_element_map),
        "type_element_map_enabled": bool(type_element_map),
    }
    if use_manual_atom_ids:
        if not selected_frames:
            route_note = "manual atom-id filtering requested, but no selected frames were found"
        elif trajectory_atom_scope == "event":
            frame_atom_filters = {int(frame): set(manual_atom_ids) for frame in selected_frames}
            route_meta["trajectory_filtered_frames"] = len(frame_atom_filters)
            route_meta["trajectory_filtered_atom_total"] = int(
                sum(len(ids) for ids in frame_atom_filters.values())
            )
            route_note = "manual atom-id filtering enabled; route analysis bypassed"
        else:
            route_note = "manual atom ids were provided, but trajectory atom scope=all so full frames will be exported"
    elif include_route_trace:
        if not selected_frames:
            route_note = "route analysis skipped because no selected frames were found"
        elif not route_exists:
            route_note = "route file not found; skip atom-level event analysis"
        else:
            route_result = ROUTE_ANALYSIS_STORE.get(
                route_file,
                selected_frames=selected_frames,
                anchor_frames=anchor_frames,
                previous_frame_of_anchor=previous_frame_of_anchor,
                target=target,
                match_mode=resolved_match_mode,
                reaction_smiles=reaction_smiles,
                atom_sample_limit=route_atom_sample_limit,
                progress_callback=progress_callback,
                progress_start=0.70,
                progress_span=0.02,
            )
            route_meta.update(route_result.get("meta", {}))
            scanned_atoms = int(route_meta.get("scanned_atoms", 0) or 0)
            frame_stats = route_result.get("frame_stats", {}) or {}
            for row in frame_rows:
                frame = int(row.get("frame", 0))
                stats = frame_stats.get(frame, {})
                row["route_target_atom_count"] = int(stats.get("target_count", 0))
                row["route_target_atom_ids"] = str(stats.get("target_atom_ids_text", ""))
                if reaction_smiles:
                    row["route_reactant_atom_count"] = int(stats.get("reactant_count", 0))
                    row["route_product_atom_count"] = int(stats.get("product_count", 0))
                    row["route_reactant_atom_ids"] = str(stats.get("reactant_atom_ids_text", ""))
                    row["route_product_atom_ids"] = str(stats.get("product_atom_ids_text", ""))
            event_stats = route_result.get("event_stats", {}) or {}
            anchor_context_ids_by_anchor: dict[int, set[int]] = {}
            context_refine_inputs: list[dict[str, Any]] = []
            for row in event_rows:
                value = row.get("anchor_frame")
                if value is None:
                    continue
                try:
                    anchor = int(value)
                except Exception:
                    continue
                stats = event_stats.get(anchor, {})
                anchor_frame_stats = frame_stats.get(anchor, {}) or {}
                anchor_target_ids = set(anchor_frame_stats.get("target_atom_ids_all", set()) or set())
                anchor_reactant_ids = set(anchor_frame_stats.get("reactant_atom_ids_all", set()) or set())
                anchor_product_ids = set(anchor_frame_stats.get("product_atom_ids_all", set()) or set())
                row["route_target_atom_count"] = int(anchor_frame_stats.get("target_count", 0))
                row["route_target_atom_ids"] = str(anchor_frame_stats.get("target_atom_ids_text", ""))
                row["route_changed_target_atoms"] = int(stats.get("changed_target_count", 0))
                row["route_changed_target_atom_ids"] = str(stats.get("changed_target_atom_ids_text", ""))
                route_event_ids = set(stats.get("changed_target_atom_ids_all", set()) or set())
                route_event_start = stats.get("changed_target_first_frame")
                route_event_end = stats.get("changed_target_last_frame")
                if reaction_smiles:
                    row["route_reactant_to_product_atoms"] = int(stats.get("reactant_to_product_count", 0))
                    row["route_reactant_to_product_atom_ids"] = str(stats.get("reactant_to_product_atom_ids_text", ""))
                    row["route_product_to_reactant_atoms"] = int(stats.get("product_to_reactant_count", 0))
                    row["route_product_to_reactant_atom_ids"] = str(stats.get("product_to_reactant_atom_ids_text", ""))
                    if not route_event_ids:
                        route_event_ids.update(stats.get("reactant_to_product_atom_ids_all", set()) or set())
                        route_event_ids.update(stats.get("product_to_reactant_atom_ids_all", set()) or set())
                    candidate_starts = [
                        stats.get("reactant_to_product_first_frame"),
                        stats.get("product_to_reactant_first_frame"),
                    ]
                    candidate_ends = [
                        stats.get("reactant_to_product_last_frame"),
                        stats.get("product_to_reactant_last_frame"),
                    ]
                    if route_event_start is None:
                        route_event_start = min(
                            (int(v) for v in candidate_starts if v is not None),
                            default=None,
                        )
                    if route_event_end is None:
                        route_event_end = max(
                            (int(v) for v in candidate_ends if v is not None),
                            default=None,
                        )
                    row["route_anchor_reactant_atom_count"] = int(anchor_frame_stats.get("reactant_count", 0))
                    row["route_anchor_product_atom_count"] = int(anchor_frame_stats.get("product_count", 0))
                    row["route_anchor_reactant_atom_ids"] = str(anchor_frame_stats.get("reactant_atom_ids_text", ""))
                    row["route_anchor_product_atom_ids"] = str(anchor_frame_stats.get("product_atom_ids_text", ""))
                row["route_event_start_frame"] = int(route_event_start) if route_event_start is not None else None
                row["route_event_end_frame"] = int(route_event_end) if route_event_end is not None else None
                row["route_event_atom_count"] = len(route_event_ids)
                row["route_event_atom_ids"] = ",".join(str(x) for x in sorted(route_event_ids))
                route_context_ids: set[int] = set(route_event_ids)
                route_context_source = "event"
                if reaction_smiles:
                    route_context_ids.update(anchor_reactant_ids)
                    route_context_ids.update(anchor_product_ids)
                    if route_context_ids:
                        route_context_source = "reaction_anchor"
                else:
                    if anchor_target_ids:
                        route_context_ids.update(anchor_target_ids)
                        route_context_source = "target_anchor"
                if not route_context_ids and anchor_target_ids:
                    route_context_ids.update(anchor_target_ids)
                    route_context_source = "target_anchor_fallback"
                row["route_context_atom_raw_count"] = len(route_context_ids)
                row["route_context_atom_ids_raw"] = ",".join(str(x) for x in sorted(route_context_ids))
                row["route_context_atom_count"] = len(route_context_ids)
                row["route_context_atom_ids"] = row["route_context_atom_ids_raw"]
                row["route_context_atom_source"] = route_context_source
                anchor_context_ids_by_anchor[anchor] = route_context_ids
                context_refine_inputs.append(
                    {
                        "row": row,
                        "anchor": anchor,
                        "candidate_ids": set(route_context_ids),
                        "event_ids": set(route_event_ids),
                        "target_ids": set(anchor_target_ids),
                        "reactant_ids": set(anchor_reactant_ids),
                        "product_ids": set(anchor_product_ids),
                        "raw_source": route_context_source,
                    }
                )
            should_refine_context = bool(
                include_trajectory
                and trajectory_exists
                and trajectory_atom_scope == "event"
                and context_refine_inputs
            )
            if should_refine_context:
                refine_anchor_frames = sorted(
                    {
                        int(item["anchor"])
                        for item in context_refine_inputs
                        if item.get("candidate_ids")
                    }
                )
                anchor_blocks = read_trajectory_requested_frame_blocks(
                    trajectory_file,
                    refine_anchor_frames,
                    progress_callback=progress_callback,
                    progress_start=0.72,
                    progress_span=0.05,
                )
                refined_context_count = 0
                for item in context_refine_inputs:
                    candidate_ids = set(item.get("candidate_ids", set()) or set())
                    if not candidate_ids:
                        continue
                    anchor = int(item["anchor"])
                    block = anchor_blocks.get(anchor)
                    if not block:
                        continue
                    parsed_anchor = parse_lammpstrj_frame_block(block, atom_ids=candidate_ids)
                    atoms_by_id = parsed_anchor.get("atoms", {}) or {}
                    box = parsed_anchor.get("box", []) or []
                    cluster = select_main_context_atom_group(
                        atoms_by_id,
                        box,
                        candidate_ids=candidate_ids,
                        event_ids=set(item.get("event_ids", set()) or set()),
                        target_ids=set(item.get("target_ids", set()) or set()),
                        reactant_ids=set(item.get("reactant_ids", set()) or set()),
                        product_ids=set(item.get("product_ids", set()) or set()),
                        reaction_smiles=reaction_smiles,
                    )
                    selected_ids = set(cluster.get("selected_ids", set()) or set())
                    if not selected_ids:
                        continue
                    row = item["row"]
                    row["route_context_atom_count"] = len(selected_ids)
                    row["route_context_atom_ids"] = ",".join(str(x) for x in sorted(selected_ids))
                    raw_source = str(item.get("raw_source", "") or "context")
                    row["route_context_atom_source"] = f"{raw_source}->{cluster.get('source', 'cluster')}"
                    row["route_context_group_mode"] = str(cluster.get("group_mode", "unknown") or "unknown")
                    row["route_context_group_count"] = int(cluster.get("n_groups", 0) or 0)
                    row["route_context_selected_group_count"] = int(cluster.get("n_selected_groups", 0) or 0)
                    anchor_context_ids_by_anchor[anchor] = selected_ids
                    refined_context_count += 1
                route_meta["route_context_refined_events"] = refined_context_count
                route_meta["route_context_refined_anchor_frames"] = len(anchor_blocks)
            if trajectory_atom_scope == "event":
                frame_atom_filters = {}
                for row in frame_rows:
                    frame = int(row.get("frame", 0))
                    refs = str(row.get("event_refs", "") or "")
                    anchor_hits = [int(m.group(1)) for m in re.finditer(r"@(\d+)", refs)]
                    ids: set[int] = set()
                    for anchor in anchor_hits:
                        ids.update(anchor_context_ids_by_anchor.get(anchor, set()) or set())
                    if not ids:
                        fstats = frame_stats.get(frame, {}) or {}
                        ids.update(fstats.get("target_atom_ids_all", set()) or set())
                        if reaction_smiles and not ids:
                            ids.update(fstats.get("reactant_atom_ids_all", set()) or set())
                            ids.update(fstats.get("product_atom_ids_all", set()) or set())
                    frame_atom_filters[frame] = ids
                route_meta["trajectory_filtered_frames"] = len(frame_atom_filters)
                route_meta["trajectory_filtered_atom_total"] = int(
                    sum(len(ids) for ids in frame_atom_filters.values())
                )
            refined_context_count = int(route_meta.get("route_context_refined_events", 0) or 0)
            if scanned_atoms <= 0:
                route_note = "route trace parsed zero transitions; check Route file is a valid .route file"
            elif refined_context_count > 0:
                route_note = (
                    f"route atom-trace analysis completed; refined main visualization cluster for "
                    f"{refined_context_count} event(s)"
                )
            else:
                route_note = "route atom-trace analysis completed"
    else:
        route_note = "route atom-trace analysis disabled by user"
    route_meta["route_note"] = route_note

    safe_target = _safe_name_fragment(target or "manual_atoms")
    trajectory_text = ""
    trajectory_preview_text = ""
    trajectory_saved_path = ""
    matched_blocks = 0
    trajectory_missing_frames = 0
    trajectory_subset_bytes = 0
    trajectory_extract_mode = ""
    trajectory_index_frames = 0
    trajectory_preview_frames = 0
    trajectory_preview_bytes = 0
    trajectory_note = ""
    vmd_script_saved_path = ""
    type_map_saved_path = ""
    if include_trajectory:
        if not trajectory_exists:
            trajectory_note = "trajectory file not found; only frame table is returned"
        else:
            active_frame_filters = frame_atom_filters if trajectory_atom_scope == "event" else None
            extract_result = extract_lammpstrj_subset(
                trajectory_file,
                selected_frames,
                frame_atom_filters=active_frame_filters,
                type_element_map=type_element_map,
                progress_callback=progress_callback,
                inline_text_limit=2_000_000,
                inline_frame_limit=120,
                output_filename=f"{safe_target}_context_subset.lammpstrj",
            )
            trajectory_text = str(extract_result.get("trajectory_text", "") or "")
            trajectory_preview_text = str(extract_result.get("trajectory_preview_text", "") or "")
            trajectory_saved_path = str(extract_result.get("trajectory_saved_path", "") or "")
            matched_blocks = int(extract_result.get("matched_blocks", 0) or 0)
            trajectory_missing_frames = int(extract_result.get("n_missing_frames", 0) or 0)
            trajectory_subset_bytes = int(extract_result.get("subset_bytes", 0) or 0)
            trajectory_extract_mode = str(extract_result.get("extract_mode", "") or "")
            trajectory_index_frames = int(extract_result.get("index_frames", 0) or 0)
            trajectory_preview_frames = int(extract_result.get("preview_blocks", 0) or 0)
            trajectory_preview_bytes = int(extract_result.get("preview_bytes", 0) or 0)
            if trajectory_extract_mode == "range_scan_copy" and not trajectory_note:
                trajectory_note = "trajectory extracted via range scan (no full-file index build)"
            if trajectory_atom_scope == "event":
                if use_manual_atom_ids:
                    if not trajectory_note:
                        trajectory_note = "trajectory subset contains user-specified atom ids only"
                elif not include_route_trace:
                    trajectory_note = "event-atom extraction requires route trace; fallback to full-frame extraction"
                elif not frame_atom_filters:
                    trajectory_note = "event-atom extraction enabled, but no event atoms were resolved from route"
                elif not trajectory_note:
                    trajectory_note = "trajectory subset contains event-related atoms only"
            if type_element_map and not bool(trajectory_atom_info.get("has_element", False)):
                mapped = ",".join(f"{key}:{value}" for key, value in sorted(type_element_map.items(), key=lambda item: int(item[0])))
                extra_note = f"element column injected into extracted subset using type map ({mapped})"
                trajectory_note = f"{trajectory_note}; {extra_note}" if trajectory_note else extra_note
            elif not type_element_map and not bool(trajectory_atom_info.get("has_element", False)):
                extra_note = (
                    "raw lammpstrj has no element column; OVITO/VMD only see numeric atom types unless you provide "
                    "Type->Element Map (for example 1:H;2:C;3:O;4:Cl)"
                )
                trajectory_note = f"{trajectory_note}; {extra_note}" if trajectory_note else extra_note

            if matched_blocks <= 0:
                trajectory_note = "trajectory scanned, but no selected frame ids were found in this lammpstrj"
            elif not trajectory_text and trajectory_preview_text:
                trajectory_note = (
                    f"trajectory subset is large; showing preview ({trajectory_preview_frames} frame(s)); "
                    "full subset saved to a temp file"
                )
            elif not trajectory_text:
                trajectory_note = "trajectory subset saved to a temp file; inline viewer disabled due to size"
            if not inline_viewer:
                trajectory_text = ""
                trajectory_preview_text = ""
                base_note = "inline trajectory viewer disabled; use OVITO/PyMOL/VMD to open extracted subset"
                trajectory_note = f"{trajectory_note}; {base_note}" if trajectory_note else base_note
    else:
        trajectory_note = "trajectory export disabled by user"

    for row in event_rows:
        classify_context_event_row(row)

    if type_element_map:
        type_map_saved_path = write_context_type_map_tempfile(
            type_element_map,
            f"{safe_target}_type_element_map.txt",
        )

    if trajectory_saved_path:
        focus_frame = None
        if event_rows:
            first_anchor = event_rows[0].get("anchor_frame")
            if first_anchor is not None:
                try:
                    focus_frame = int(first_anchor)
                except Exception:
                    focus_frame = None
        if focus_frame is None and frame_rows:
            try:
                focus_frame = int(frame_rows[0].get("frame"))
            except Exception:
                focus_frame = None

        focus_frame_index = None
        if focus_frame is not None and frame_rows:
            for idx, row in enumerate(frame_rows):
                try:
                    if int(row.get("frame", -1)) == int(focus_frame):
                        focus_frame_index = idx
                        break
                except Exception:
                    continue

        event_atom_union: set[int] = set()
        if frame_atom_filters:
            for ids in frame_atom_filters.values():
                event_atom_union.update(ids or set())

        vmd_script_text = build_context_vmd_script(
            trajectory_saved_path,
            focus_frame_index=focus_frame_index,
            selection_text="all",
            event_atom_count=len(event_atom_union),
            source_note=(
                (
                    "selection=all on manually filtered atom-id subset; use DynamicBonds and pbc wrap for event-focused viewing"
                    if use_manual_atom_ids
                    else (
                        "selection=all on extracted subset; use DynamicBonds and pbc wrap for event-focused viewing"
                        if trajectory_atom_scope == "event"
                        else "selection=all on extracted trajectory; unrelated atoms may still be present"
                    )
                )
            ),
        )
        vmd_script_saved_path = write_context_vmd_script_tempfile(
            vmd_script_text,
            f"{safe_target}_context_view.tcl",
        )

    top_matches = [
        {"smiles": smi, "total_count": int(total)}
        for smi, total in matched_totals.most_common(12)
    ]
    target_count_peak = (
        max(frame_to_count.values(), default=0)
        if (use_frame_ranges and target)
        else max((item["count"] for item in timeline), default=0)
    )
    payload = {
        "ok": True,
        "query": {
            "reac": reac,
            "species_file": species_file,
            "species_file_input": species_file_raw,
            "species_file_source_type": species_file_source_type,
            "trajectory_file": trajectory_file,
            "trajectory_file_input": trajectory_file_raw,
            "route_file": route_file,
            "route_file_input": route_file_raw,
            "target": target,
            "reaction_smiles": reaction_smiles,
            "frame_ranges": frame_ranges_raw,
            "atom_ids": atom_ids_raw,
            "type_element_map": type_element_map_raw,
            "anchor_frame": anchor_frame_override,
            "match_mode": resolved_match_mode,
            "event_mode": "frame_range" if use_frame_ranges else event_mode,
            "before_frames": before_frames,
            "after_frames": after_frames,
            "max_events": max_events,
            "include_trajectory": include_trajectory,
            "include_route_trace": include_route_trace,
            "inline_viewer": inline_viewer,
            "route_atom_sample_limit": route_atom_sample_limit,
            "trajectory_atom_scope": trajectory_atom_scope,
            "manual_atom_ids_count": len(manual_atom_ids),
        },
        "meta": {
            "species_file": species_file,
            "species_file_found": species_exists,
            "trajectory_file": trajectory_file,
            "trajectory_found": trajectory_exists,
            "trajectory_atom_columns": trajectory_atom_info.get("atom_columns", []),
            "trajectory_has_element_column": bool(trajectory_atom_info.get("has_element", False)),
            "trajectory_has_type_column": bool(trajectory_atom_info.get("has_type", False)),
            "type_element_map": dict(type_element_map),
            "type_element_map_enabled": bool(type_element_map),
            "trajectory_included": bool(trajectory_text),
            "trajectory_preview_included": bool(trajectory_preview_text),
            "trajectory_preview_only": bool(trajectory_preview_text) and not bool(trajectory_text),
            "trajectory_saved_path": trajectory_saved_path,
            "vmd_script_saved_path": vmd_script_saved_path,
            "type_map_saved_path": type_map_saved_path,
            "trajectory_note": trajectory_note,
            "n_timeline_frames": len(timeline),
            "n_events": len(event_rows),
            "n_selected_frames": len(selected_frames),
            "n_trajectory_frames": matched_blocks,
            "n_missing_trajectory_frames": trajectory_missing_frames,
            "trajectory_subset_bytes": trajectory_subset_bytes,
            "trajectory_extract_mode": trajectory_extract_mode,
            "trajectory_index_frames": trajectory_index_frames,
            "trajectory_preview_frames": trajectory_preview_frames,
            "trajectory_preview_bytes": trajectory_preview_bytes,
            "inline_viewer": inline_viewer,
            "trajectory_atom_scope": trajectory_atom_scope,
            "manual_atom_ids_enabled": use_manual_atom_ids,
            "manual_atom_ids_count": len(manual_atom_ids),
            "route_file": route_file,
            "route_found": route_exists,
            "route_note": route_note,
            "route_meta": route_meta,
            "first_frame": int(timeline[0]["frame"]) if timeline else None,
            "last_frame": int(timeline[-1]["frame"]) if timeline else None,
            "target_count_peak": target_count_peak,
            "matched_smiles_top": top_matches,
            "event_resolution_counts": summarize_context_event_resolutions(event_rows),
            "n_route_resolved_events": sum(1 for row in event_rows if row.get("event_resolution") == "route_resolved"),
            "message": (
                "manual atom-id extraction completed"
                if use_manual_atom_ids
                else ("frame-range extraction completed" if use_frame_ranges else "context extraction completed")
            ),
            "limitation": (
                "trajectory extraction filters by user-supplied atom ids; chemistry is not inferred from .route in this mode"
                if (use_manual_atom_ids and trajectory_atom_scope == "event")
                else (
                    "manual atom ids were provided, but full frames were exported because trajectory atom scope=all"
                    if use_manual_atom_ids
                    else (
                        "trajectory extraction uses event-atom filtering from .route; atom-level event summary may still include multi-molecule contexts"
                        if (include_route_trace and trajectory_atom_scope == "event")
                        else (
                            "trajectory extraction is whole-frame slicing; atom-level event summary is inferred from .route and may include multiple molecules"
                            if include_route_trace
                            else "current extraction is whole-frame trajectory slicing; enable route analysis for atom-level event summary"
                        )
                    )
                )
            ),
        },
        "rows": event_rows,
        "frame_rows": frame_rows,
        "trajectory_text": trajectory_text,
        "trajectory_preview_text": trajectory_preview_text,
        "trajectory_saved_path": trajectory_saved_path,
        "vmd_script_saved_path": vmd_script_saved_path,
        "type_map_saved_path": type_map_saved_path,
        "suggested_files": {
            "events_csv": f"{safe_target}_context_events.csv",
            "frames_csv": f"{safe_target}_context_frames.csv",
            "trajectory": f"{safe_target}_context_subset.lammpstrj",
            "vmd_script": f"{safe_target}_context_view.tcl",
            "type_map": f"{safe_target}_type_element_map.txt" if type_map_saved_path else "",
        },
    }
    report(1.0, "completed", "Structure-context extraction completed", n_events=len(event_rows), n_frames=len(selected_frames))
    return payload


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


def _transition_species_summary(smiles: str, index: int, incoming: int, outgoing: int) -> dict[str, Any]:
    formula = smiles_formula_cached(smiles)
    return {
        "index": int(index),
        "smiles": smiles,
        "formula": formula or "?",
        "incoming": int(incoming),
        "outgoing": int(outgoing),
        "total": int(incoming + outgoing),
    }


def build_transition_table_payload(params: dict[str, list[str]]) -> dict[str, Any]:
    """Build a compact visualization payload for RNG transition matrices."""

    raw_path = (params.get("table", params.get("transition_table", [str(DEFAULT_TRANSITION_TABLE)]))[0] or "").strip()
    table_path = Path(raw_path).expanduser() if raw_path else DEFAULT_TRANSITION_TABLE
    if not table_path.exists() and not raw_path:
        table_path = DEFAULT_TRANSITION_TABLE
    parsed = load_transition_table(table_path)
    labels = list(parsed["labels"])
    matrix = parsed["matrix"]
    min_count = max(0, int_param(params, "min_count", 1))
    max_species = max(0, int_param(params, "max_species", 60))
    top_edges_limit = max(1, min(500, int_param(params, "top_edges", 40)))

    incoming = [sum(int(row[index]) for row in matrix) for index in range(len(labels))]
    outgoing = [sum(int(value) for value in matrix[index]) for index in range(len(labels))]
    ranking = sorted(
        range(len(labels)),
        key=lambda index: (-incoming[index] - outgoing[index], -incoming[index], labels[index]),
    )
    if max_species:
        selected = ranking[: min(max_species, len(ranking))]
    else:
        selected = ranking
    selected_set = set(selected)
    selected = [index for index in ranking if index in selected_set]
    # Keep the displayed matrix in rank order, which makes dominant species
    # visible at the upper-left instead of preserving arbitrary file order.
    submatrix = [[int(matrix[row][col]) for col in selected] for row in selected]
    species = [
        _transition_species_summary(labels[index], index, incoming[index], outgoing[index])
        for index in selected
    ]
    for rank, item in enumerate(species, 1):
        item["rank"] = rank

    edges: list[dict[str, Any]] = []
    for row_index in selected:
        for col_index in selected:
            count = int(matrix[row_index][col_index])
            if count < min_count:
                continue
            edges.append(
                {
                    "source_index": int(row_index),
                    "target_index": int(col_index),
                    "source": labels[row_index],
                    "target": labels[col_index],
                    "source_formula": smiles_formula_cached(labels[row_index]) or "?",
                    "target_formula": smiles_formula_cached(labels[col_index]) or "?",
                    "count": count,
                }
            )
    edges.sort(key=lambda edge: (-int(edge["count"]), edge["source"], edge["target"]))

    total_events = int(sum(sum(int(value) for value in row) for row in matrix))
    nonzero_events = int(sum(1 for row in matrix for value in row if int(value) >= min_count))
    observation_network = build_observation_network(
        parsed,
        table_path=table_path,
        min_count=min_count,
        max_species=max_species,
        top_edges=top_edges_limit,
        formula_resolver=smiles_formula_cached,
    )
    return {
        "ok": True,
        "mode": "transition_table",
        "query": {
            "table": str(table_path.resolve()),
            "min_count": min_count,
            "max_species": max_species,
            "top_edges": top_edges_limit,
        },
        "meta": {
            "n_species_total": len(labels),
            "n_species_displayed": len(selected),
            "n_edges_displayed": min(len(edges), top_edges_limit),
            "total_events": total_events,
            "nonzero_events": nonzero_events,
            "density": round(nonzero_events / max(1, len(labels) ** 2), 6),
        },
        "labels": [labels[index] for index in selected],
        "matrix": submatrix,
        "species": species,
        "edges": edges[:top_edges_limit],
        "network": observation_network,
    }


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
    x_axis = (params.get("x_axis", ["ps"])[0] or "ps").strip().lower()
    if x_axis not in {"step", "ps", "ns"}:
        x_axis = "ps"
    timestep_ps = float_param(params, "timestep_ps", 0.0001)
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
    report(0.08, "resolving_targets", "Scanning species source for target resolution")

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
                    f"[{file_idx}/{total_files}] {this_system}: {update.get('message', 'Scanning species catalog')}",
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
                str(update.get("message", "Scanning species catalog")),
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
            "x_name": "time_ps",
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
                    f"[{file_idx}/{total_files}] {this_system}: {update.get('message', 'Reading species file')}",
                    timesteps=update.get("timesteps"),
                    rows=update.get("rows"),
                    frame=update.get("frame"),
                    system=this_system,
                    replicate=this_replicate,
                )

            table = species_file_to_tidy_table(
                species_file=this_path,
                time_axis=x_axis,
                timestep_ps=timestep_ps,
                species_resolver=lambda species: species,
                system=this_system,
                replicate=this_replicate,
                time_col="time",
                species_col="species",
                count_col="count",
                progress_callback=species_progress,
            )
            table = table[table["species"].isin(selected_smiles)].copy()
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
                "x_name": "time_ps",
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

            for definition in series_defs:
                vals = [0.0] * len(x_vals)
                for smi in definition["members"]:
                    smi_map = counts_by_species.get(str(smi), {})
                    for idx, time_value in enumerate(x_vals):
                        vals[idx] += float(smi_map.get(float(time_value), 0.0))

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
            f"Reading species file: {os.path.basename(species_file)}",
            selected_smiles=len(selected_smiles),
        )
        timesteps, base_series = parse_species_selected(
            species_file,
            selected_smiles,
            progress_callback=lambda update: report(
                0.34 + 0.24 * float(update.get("progress", 0.0)),
                "reading_species",
                f"Reading species file: {float(update.get('progress', 0.0)) * 100:.1f}%",
                selected_smiles=len(selected_smiles),
                timesteps=update.get("timesteps"),
                frame=update.get("frame"),
            ),
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
                "x_name": "time_ps",
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
                    "max_value": max(vals) if vals else 0.0,
                }
            )

    if downsample > 0:
        report(0.84, "downsampling", "Downsampling web payload")
        x_vals_ds, y_map_ds = downsample_series(x_vals, y_map, downsample)
        for curve in curves:
            curve["values"] = y_map_ds.get(curve["name"], [])
            curve["max_value"] = max(curve["values"]) if curve["values"] else 0.0
        x_vals = x_vals_ds

    report(0.95, "serializing", "Preparing plot payload")
    return {
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
            "time_align": time_align,
            "time_align_meta": time_align_meta,
            "normalize": normalize,
            "smooth_window": smooth_window,
            "downsample": downsample,
        },
        "meta": {
            "n_timestep_full": len(x_vals),
            "n_points_returned": len(x_vals),
            "n_curves": len(curves),
            "n_input_species_files": len(species_file_specs) if species_file_specs else 1,
            "warnings": warnings,
        },
        "mapping": mapping_rows_out,
        "x_name": x_name,
        "x_values": x_vals,
        "curves": curves,
    }


def build_carbon_plot_payload(
    params: dict[str, list[str]],
    *,
    progress_callback: Any = None,
) -> dict[str, Any]:
    data_path = (params.get("data", [""])[0] or "").strip()
    reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
    species_file_raw = (params.get("species_file", [""])[0] or "").strip()
    species_file = species_file_raw
    species_file_source_type = "species"
    if species_file_raw.lower().endswith(".reactionabcd"):
        species_file = derive_species_path(species_file_raw)
        species_file_source_type = "reactionabcd"
    species_file_specs = parse_species_file_specs(params.get("species_files", []))
    source_mode = "multi_species_files" if species_file_specs else "single_species_file_or_table"
    source_notes: list[str] = []
    if species_file_specs and species_file:
        source_notes.append("species_files is set; species_file is ignored.")
    elif species_file_raw and species_file_source_type == "reactionabcd":
        source_notes.append("species_file uses .reactionabcd and is converted to paired .species path.")
    if not data_path:
        if species_file_specs:
            for spec in species_file_specs:
                candidate = Path(str(spec["path"])).expanduser().resolve()
                if not candidate.exists():
                    raise FileNotFoundError(f"species file not found: {candidate}")
        else:
            if not species_file:
                species_file = derive_species_path(reac)
            if not os.path.exists(species_file):
                raise FileNotFoundError(f"species file not found: {species_file}")

    time_col = (params.get("time_col", ["time"])[0] or "time").strip()
    species_col = (params.get("species_col", ["species"])[0] or "species").strip()
    count_col = (params.get("count_col", ["count"])[0] or "count").strip()
    system_col = (params.get("system_col", [""])[0] or "").strip() or None
    replicate_col = (params.get("replicate_col", [""])[0] or "").strip() or None
    system_name = (params.get("system_name", [""])[0] or "").strip() or None
    replicate_id = (params.get("replicate_id", [""])[0] or "").strip() or None
    time_align = (params.get("time_align", ["raw"])[0] or "raw").strip().lower()
    if time_align not in {"raw", "truncate", "relative"}:
        time_align = "raw"

    x_axis = (params.get("x_axis", ["ps"])[0] or "ps").strip().lower()
    if x_axis not in {"step", "ps", "ns"}:
        x_axis = "ps"
    timestep_ps = float_param(params, "timestep_ps", 0.0001)

    mode = (params.get("mode", ["exact"])[0] or "exact").strip().lower()
    if mode not in {"exact", "binned", "topk"}:
        mode = "exact"
    layout = (params.get("layout", ["single"])[0] or "single").strip().lower()
    if layout not in {"single", "subplots"}:
        layout = "single"
    system_mode = (params.get("system_mode", [""])[0] or "").strip().lower() or None
    if system_mode not in {None, "facet", "overlay"}:
        system_mode = None
    legend_mode = (params.get("legend_mode", ["compact"])[0] or "compact").strip().lower()
    if legend_mode not in {"compact", "detailed"}:
        legend_mode = "compact"
    theme = (params.get("theme", ["light"])[0] or "light").strip().lower()
    if theme not in {"light", "dark"}:
        theme = "light"
    palette = (params.get("palette", ["viridis"])[0] or "viridis").strip()

    top_k = int_param(params, "top_k", 12)
    max_exact_lines = int_param(params, "max_exact_lines", 24)
    highlight_large = int_param(params, "highlight_large", 30)
    parent_carbon_number = int_param(params, "parent_carbon_number", 0) or None
    show_uncertainty = bool_param(params, "show_uncertainty", True)
    fig_width = float_param(params, "fig_width", 11.5)
    fig_height = float_param(params, "fig_height", 6.5)
    dpi = int_param(params, "dpi", 180)
    max_points = max(200, int_param(params, "max_points", 1200))
    max_formula_list = max(5, int_param(params, "max_formula_list", 30))

    carbon_bins = parse_carbon_range_specs((params.get("carbon_bins", [""])[0] or "").strip()) or None
    display_ranges = parse_carbon_range_specs((params.get("display_ranges", [""])[0] or "").strip()) or None
    merge_ranges = parse_carbon_range_specs((params.get("merge_ranges", [""])[0] or "").strip()) or None
    layout_regions = parse_carbon_range_specs((params.get("layout_regions", [""])[0] or "").strip()) or None

    highlight_small_spec = parse_carbon_range_specs(
        (params.get("highlight_small", ["1-4"])[0] or "1-4").strip()
    )
    if highlight_small_spec:
        _, small_start, small_end = highlight_small_spec[0]
        highlight_small = (small_start or 1, small_end or 4)
    else:
        highlight_small = (1, 4)

    smoothing_method = (params.get("smoothing", ["none"])[0] or "none").strip().lower()
    if smoothing_method not in {"none", "rolling", "savgol"}:
        smoothing_method = "none"
    smoothing = None
    if smoothing_method == "rolling":
        smoothing = {"method": "rolling", "window": max(1, int_param(params, "smooth_window", 5))}
    elif smoothing_method == "savgol":
        smoothing = {
            "method": "savgol",
            "window_length": max(3, int_param(params, "smooth_window", 5)),
            "polyorder": max(1, int_param(params, "smooth_polyorder", 2)),
        }

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

    report(0.02, "preparing", "Preparing carbon plot request")

    time_align_meta: dict[str, Any] = {"time_align": time_align, "group_by": []}
    if data_path:
        source: Any = load_tidy_table(data_path)
        source_desc = data_path
        report(0.18, "loading_table", f"Loading tidy table: {os.path.basename(data_path)}")
        if system_col is None and "system" in source.columns:
            system_col = "system"
        if replicate_col is None and "replicate" in source.columns:
            replicate_col = "replicate"
    else:
        if species_file_specs:
            tables: list[pd.DataFrame] = []
            has_replicate = False
            total_files = len(species_file_specs)
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
                        0.05 + 0.70 * combined,
                        "reading_species",
                        f"[{file_idx}/{total_files}] {this_system}: {update.get('message', 'Reading species file')}",
                        timesteps=update.get("timesteps"),
                        rows=update.get("rows"),
                        frame=update.get("frame"),
                        system=this_system,
                        replicate=this_replicate,
                    )

                table = species_file_to_tidy_table(
                    species_file=this_path,
                    time_axis=x_axis,
                    timestep_ps=timestep_ps,
                    species_resolver=smiles_to_formula_fast,
                    system=this_system,
                    replicate=this_replicate,
                    progress_callback=species_progress,
                )
                tables.append(table)
            source = pd.concat(tables, ignore_index=True)
            if system_col is None:
                system_col = "system"
            if has_replicate and replicate_col is None:
                replicate_col = "replicate"
            source_desc = f"{total_files} species files"
        else:
            def species_progress(update: dict[str, Any]) -> None:
                fraction = float(update.get("progress", 0.0))
                report(
                    0.05 + 0.70 * fraction,
                    "reading_species",
                    str(update.get("message", "Reading species file")),
                    timesteps=update.get("timesteps"),
                    rows=update.get("rows"),
                    frame=update.get("frame"),
                )

            source = species_file_to_tidy_table(
                species_file=species_file,
                time_axis=x_axis,
                timestep_ps=timestep_ps,
                species_resolver=smiles_to_formula_fast,
                system=system_name,
                replicate=replicate_id,
                progress_callback=species_progress,
            )
            if system_name and system_col is None:
                system_col = "system"
            if replicate_id and replicate_col is None:
                replicate_col = "replicate"
            source_desc = species_file

    if isinstance(source, pd.DataFrame):
        source, time_align_meta = align_time_axis_for_comparison(
            source=source,
            time_col=time_col,
            system_col=system_col,
            replicate_col=replicate_col,
            mode=time_align,
        )
        carbon_formula_index = build_carbon_formula_index(
            source=source,
            species_col=species_col,
            count_col=count_col,
            system_col=system_col,
            max_formula_list=max_formula_list,
        )
    else:
        carbon_formula_index = []

    base_time_axis_label = {
        "step": "timestep",
        "ps": "time_ps",
        "ns": "time_ns",
    }.get(x_axis, time_col)
    time_axis_label = f"{base_time_axis_label}_relative" if time_align == "relative" else base_time_axis_label

    report(0.80, "aggregating", "Aggregating carbon-number trajectories")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, _, summary, plot_data = plot_carbon_number_evolution(
        data=source,
        time_col=time_col,
        species_col=species_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        carbon_bins=carbon_bins,
        display_ranges=display_ranges,
        merge_ranges=merge_ranges,
        mode=mode,
        top_k=top_k,
        max_exact_lines=max_exact_lines,
        parent_carbon_number=parent_carbon_number,
        highlight_small=highlight_small,
        highlight_large=highlight_large,
        smoothing=smoothing,
        layout=layout,
        layout_regions=layout_regions,
        system_mode=system_mode,
        legend_mode=legend_mode,
        palette=palette,
        theme=theme,
        figsize=(fig_width, fig_height),
        max_points_per_series=max_points,
        return_summary=True,
        show_uncertainty=show_uncertainty,
        output_path=None,
    )
    for axis in fig.axes:
        axis.set_xlabel(time_axis_label)

    report(0.92, "rendering_plot", "Rendering SVG figure")
    svg_io = io.StringIO()
    fig.savefig(svg_io, format="svg", bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    svg_text = svg_io.getvalue()

    report(0.97, "serializing", "Preparing plot payload")
    plot_rows = plot_data.where(plot_data.notna(), None).to_dict(orient="records")
    summary = downsample_summary_payload(summary, max_points)

    return {
        "ok": True,
        "query": {
            "reac": reac,
            "data": data_path,
            "species_file": species_file,
            "species_file_input": species_file_raw,
            "species_file_source_type": species_file_source_type,
            "species_files": [
                {
                    "path": str(spec["path"]),
                    "input_path": str(spec.get("input_path", spec["path"])),
                    "source_type": str(spec.get("source_type", "species")),
                    "system": str(spec["system"]),
                    "replicate": spec.get("replicate"),
                }
                for spec in species_file_specs
            ],
            "source_mode": source_mode,
            "source_notes": source_notes,
            "source": source_desc,
            "time_col": time_col,
            "time_axis_label": time_axis_label,
            "species_col": species_col,
            "count_col": count_col,
            "system_col": system_col,
            "replicate_col": replicate_col,
            "x_axis": x_axis,
            "timestep_ps": timestep_ps,
            "time_align": time_align,
            "time_align_meta": time_align_meta,
            "max_formula_list": max_formula_list,
            "mode": mode,
            "top_k": top_k,
            "max_exact_lines": max_exact_lines,
            "display_ranges": display_ranges,
            "merge_ranges": merge_ranges,
            "parent_carbon_number": parent_carbon_number,
            "highlight_small": list(highlight_small),
            "highlight_large": highlight_large,
            "layout": layout,
            "system_mode": system_mode,
            "legend_mode": legend_mode,
            "theme": theme,
            "palette": palette,
            "smoothing": smoothing,
            "max_points": max_points,
        },
        "meta": {
            "n_plot_rows": int(len(plot_data)),
            "n_systems": int(plot_data[system_col].nunique()) if system_col and system_col in plot_data.columns else 1,
            "n_regions": int(plot_data["plot_region"].nunique()) if "plot_region" in plot_data.columns else 1,
            "n_input_species_files": len(species_file_specs) if species_file_specs else 0,
            "n_formula_index_entries": len(carbon_formula_index),
            "plot_mode": summary.get("plot_mode", mode),
            "max_points_returned_per_series": max_points,
        },
        "summary": summary,
        "plot_data": plot_rows,
        "carbon_formula_index": carbon_formula_index,
        "svg": svg_text,
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
    fwhm_min_ps = float_param(params, "fwhm_min_ps", 0.5)
    timestep_ps = float_param(params, "timestep_ps", 0.0001)
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
    summary = SPECIES_STORE.get(
        species_file,
        progress_callback=lambda update: report(
            0.08 + 0.62 * float(update.get("progress", 0.0)),
            str(update.get("phase", "reading_species")),
            str(update.get("message", "Reading species file")),
            timesteps=update.get("timesteps"),
            frame=update.get("frame"),
        ),
    )
    dt_ps = summary.timestep_step * timestep_ps
    rows: list[dict[str, Any]] = []
    total_species = max(len(summary.max_counts), 1)
    scanned = 0

    report(0.72, "classifying", "Classifying candidate species")
    for smi, cmax in summary.max_counts.items():
        scanned += 1
        if cmax < abundance_threshold:
            continue
        cstart = summary.start_counts.get(smi, 0)
        cend = summary.end_counts.get(smi, 0)
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

        fwhm_points = summary.fwhm_longest_points.get(smi, 0)
        fwhm_ps = fwhm_points * dt_ps
        if cls == "intermediate" and require_fwhm and fwhm_ps < fwhm_min_ps:
            continue

        peak_ts = summary.max_timestep.get(smi, summary.first_timestep)
        peak_time_ps = (peak_ts - summary.first_timestep) * timestep_ps
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
                "peak_time_ps": round(peak_time_ps, 6),
                "fwhm_ps": round(fwhm_ps, 6),
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
            "fwhm_min_ps": fwhm_min_ps,
            "timestep_ps": timestep_ps,
            "require_fwhm": require_fwhm,
            "with_flux": with_flux,
            "flux_top": flux_top,
        },
        "meta": {
            "rows": len(rows),
            "n_timesteps": summary.n_timesteps,
            "first_timestep": summary.first_timestep,
            "last_timestep": summary.last_timestep,
            "timestep_step": summary.timestep_step,
            "dt_ps": dt_ps,
            "species_scanned": len(summary.max_counts),
        },
        "rows": rows,
    }


@dataclass
class SpeciesTimelineSummary:
    species_file: str
    n_timesteps: int
    first_timestep: int
    last_timestep: int
    timestep_step: int
    start_counts: dict[str, int]
    end_counts: dict[str, int]
    max_counts: dict[str, int]
    max_timestep: dict[str, int]
    fwhm_longest_points: dict[str, int]


def _finalize_run_points(
    longest: dict[str, int],
    smi: str,
    start_ts: int,
    last_ts: int,
    step: int,
) -> None:
    if step <= 0:
        points = 1
    else:
        points = int((last_ts - start_ts) // step) + 1
    if points > longest.get(smi, 0):
        longest[smi] = points


def build_species_timeline_summary(
    species_file: str,
    progress_callback: Any = None,
) -> SpeciesTimelineSummary:
    first_ts: int | None = None
    last_ts: int | None = None
    prev_ts: int | None = None
    step: int | None = None
    n_timesteps = 0

    start_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}
    max_counts: dict[str, int] = {}
    max_timestep: dict[str, int] = {}
    file_size = max(os.path.getsize(species_file), 1)
    bytes_read = 0
    last_emit = 0.0

    def emit(progress: float, phase: str, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": phase,
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    # Pass 1: start/end/max
    emit(0.01, "reading_species", f"Scanning {os.path.basename(species_file)} (pass 1/2)")
    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            n_timesteps += 1
            if first_ts is None:
                first_ts = ts
                start_counts = {smi: cnt for smi, cnt in pairs}
            elif prev_ts is not None and step is None and ts != prev_ts:
                step = ts - prev_ts

            end_counts = {smi: cnt for smi, cnt in pairs}
            last_ts = ts
            prev_ts = ts

            for smi, cnt in pairs:
                old = max_counts.get(smi, -1)
                if cnt > old:
                    max_counts[smi] = cnt
                    max_timestep[smi] = ts

            frac = bytes_read / file_size
            now = time.monotonic()
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    0.02 + 0.50 * min(frac, 1.0),
                    "reading_species",
                    f"Scanning species file (pass 1/2): {frac * 100:.1f}%",
                    timesteps=n_timesteps,
                    frame=ts,
                )
                last_emit = now

    if first_ts is None or last_ts is None:
        raise RuntimeError(f"no valid timestep rows found in species file: {species_file}")
    if step is None or step <= 0:
        step = 1

    # Pass 2: longest FWHM run (count >= 0.5 * max)
    half_threshold = {smi: cmax * 0.5 for smi, cmax in max_counts.items() if cmax > 0}
    active_start: dict[str, int] = {}
    active_last: dict[str, int] = {}
    longest_points: dict[str, int] = {}
    bytes_read = 0
    last_emit = 0.0

    emit(0.53, "reading_species", f"Scanning {os.path.basename(species_file)} (pass 2/2)")
    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
            bytes_read += len(line)
            parsed = parse_species_timestep_line(line)
            if parsed is None:
                continue
            ts, pairs = parsed
            current_above: set[str] = set()
            for smi, cnt in pairs:
                thr = half_threshold.get(smi)
                if thr is not None and cnt >= thr:
                    current_above.add(smi)

            for smi in list(active_start.keys()):
                if smi not in current_above:
                    _finalize_run_points(longest_points, smi, active_start[smi], active_last[smi], step)
                    active_start.pop(smi, None)
                    active_last.pop(smi, None)

            for smi in current_above:
                if smi not in active_start:
                    active_start[smi] = ts
                    active_last[smi] = ts
                else:
                    last_seen = active_last[smi]
                    if ts - last_seen == step:
                        active_last[smi] = ts
                    else:
                        _finalize_run_points(longest_points, smi, active_start[smi], active_last[smi], step)
                        active_start[smi] = ts
                        active_last[smi] = ts

            frac = bytes_read / file_size
            now = time.monotonic()
            if frac >= 0.99 or (now - last_emit) >= 1.0:
                emit(
                    0.53 + 0.43 * min(frac, 1.0),
                    "reading_species",
                    f"Scanning species file (pass 2/2): {frac * 100:.1f}%",
                    frame=ts,
                )
                last_emit = now

    for smi in list(active_start.keys()):
        _finalize_run_points(longest_points, smi, active_start[smi], active_last[smi], step)

    emit(1.0, "completed", f"Loaded timeline summary from {os.path.basename(species_file)}", timesteps=n_timesteps)

    return SpeciesTimelineSummary(
        species_file=os.path.abspath(species_file),
        n_timesteps=n_timesteps,
        first_timestep=first_ts,
        last_timestep=last_ts,
        timestep_step=step,
        start_counts=start_counts,
        end_counts=end_counts,
        max_counts=max_counts,
        max_timestep=max_timestep,
        fwhm_longest_points=longest_points,
    )


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


@dataclass
class SpeciesFrameIndex:
    species_file: str
    mtime: float
    size: int
    frames: list[int]


class NetworkStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, int], tuple[float, ReactionNetwork]] = {}

    def get(self, reac_file: str, min_tp: int) -> ReactionNetwork:
        path = os.path.abspath(reac_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"reaction file not found: {path}")
        key = (path, min_tp)
        mtime = os.path.getmtime(path)

        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == mtime:
                return cached[1]

            reactions = parse_reactionabcd(path, min_tp=min_tp)
            if not reactions:
                raise RuntimeError(f"no reactions loaded from: {path}")
            net = ReactionNetwork(reactions)
            self._cache[key] = (mtime, net)
            return net


STORE = NetworkStore()


class SpeciesSummaryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, SpeciesTimelineSummary]] = {}

    def get(self, species_file: str, progress_callback: Any = None) -> SpeciesTimelineSummary:
        path = os.path.abspath(species_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"species file not found: {path}")
        mtime = os.path.getmtime(path)
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] == mtime:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "progress": 1.0,
                            "phase": "cached",
                            "message": f"Using cached timeline summary: {os.path.basename(path)}",
                        }
                    )
                return cached[1]
            summary = build_species_timeline_summary(path, progress_callback=progress_callback)
            self._cache[path] = (mtime, summary)
            return summary


SPECIES_STORE = SpeciesSummaryStore()


class SpeciesFrameIndexStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, SpeciesFrameIndex] = {}

    def get(
        self,
        species_file: str,
        *,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> SpeciesFrameIndex:
        path = os.path.abspath(species_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"species file not found: {path}")
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached.mtime == mtime and cached.size == size:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "progress": max(0.0, min(progress_start + progress_span, 1.0)),
                            "phase": "cached_species_index",
                            "message": f"Using cached species timestep index: {os.path.basename(path)}",
                            "n_index_frames": len(cached.frames),
                        }
                    )
                return cached
        fresh = self._scan_index(
            path,
            mtime=mtime,
            size=size,
            progress_callback=progress_callback,
            progress_start=progress_start,
            progress_span=progress_span,
        )
        with self._lock:
            self._cache[path] = fresh
        return fresh

    def _scan_index(
        self,
        species_file: str,
        *,
        mtime: float,
        size: int,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> SpeciesFrameIndex:
        file_size = max(size, 1)
        bytes_read = 0
        last_emit = 0.0
        frames: list[int] = []

        def emit(progress: float, message: str, **extra: Any) -> None:
            if progress_callback is None:
                return
            payload = {
                "progress": max(0.0, min(float(progress), 1.0)),
                "phase": "indexing_species",
                "message": message,
            }
            payload.update(extra)
            progress_callback(payload)

        emit(progress_start, f"Indexing species timesteps: {os.path.basename(species_file)}")
        with open(species_file, encoding="utf-8") as fh:
            for line in fh:
                bytes_read += len(line)
                ts = parse_species_timestep_only(line)
                if ts is not None:
                    frames.append(ts)
                frac = bytes_read / file_size
                now = time.monotonic()
                if frac >= 0.99 or (now - last_emit) >= 1.0:
                    emit(
                        progress_start + progress_span * min(frac, 1.0),
                        f"Indexing species file: {frac * 100:.1f}%",
                        n_index_frames=len(frames),
                        frame=ts,
                    )
                    last_emit = now
        emit(
            progress_start + progress_span,
            f"Species timestep index ready: {len(frames)} frames",
            n_index_frames=len(frames),
        )
        return SpeciesFrameIndex(
            species_file=species_file,
            mtime=mtime,
            size=size,
            frames=frames,
        )


SPECIES_FRAME_INDEX_STORE = SpeciesFrameIndexStore()


class SpeciesTokenSnapshotStore:
    def __init__(self, max_entries: int = 64) -> None:
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[Any, ...], dict[int, dict[str, int]]] = OrderedDict()
        self._max_entries = max(8, int(max_entries))

    def get(
        self,
        species_file: str,
        *,
        requested_frames: list[int],
        query_tokens: list[str],
        match_mode: str,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> dict[int, dict[str, int]]:
        path = os.path.abspath(species_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"species file not found: {path}")
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
        query_key = (
            path,
            mtime,
            size,
            tuple(sorted({int(frame) for frame in requested_frames if frame is not None})),
            tuple(sorted({str(token or "").strip() for token in query_tokens if str(token or "").strip()})),
            str(match_mode or ""),
        )
        with self._lock:
            cached = self._cache.get(query_key)
            if cached is not None:
                self._cache.move_to_end(query_key)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "progress": max(0.0, min(progress_start + progress_span, 1.0)),
                            "phase": "cached_species_snapshots",
                            "message": f"Using cached species snapshots: {os.path.basename(path)}",
                            "n_snapshots": len(cached),
                        }
                    )
                return cached

        fresh = _collect_reaction_species_token_snapshots(
            path,
            requested_frames=requested_frames,
            query_tokens=query_tokens,
            match_mode=match_mode,
            progress_callback=progress_callback,
            progress_start=progress_start,
            progress_span=progress_span,
        )
        with self._lock:
            self._cache[query_key] = fresh
            self._cache.move_to_end(query_key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return fresh


SPECIES_TOKEN_SNAPSHOT_STORE = SpeciesTokenSnapshotStore()


class RouteTransitionIndexStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, float, int], dict[str, Any]] = {}
        self._build_locks: dict[tuple[str, float, int], threading.Lock] = {}

    def _route_signature(self, route_file: str) -> tuple[str, float, int]:
        path = os.path.abspath(route_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"route file not found: {path}")
        return path, os.path.getmtime(path), os.path.getsize(path)

    def _emit(
        self,
        progress_callback: Any,
        *,
        progress: float,
        phase: str,
        message: str,
        **extra: Any,
    ) -> None:
        if progress_callback is None:
            return
        payload = {
            "progress": max(0.0, min(float(progress), 1.0)),
            "phase": phase,
            "message": message,
        }
        payload.update(extra)
        progress_callback(payload)

    def _read_meta(self, index_path: Path) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        finally:
            conn.close()
        for key, value in rows:
            meta[str(key)] = value
        return meta

    def _store_meta_rows(
        self,
        conn: sqlite3.Connection,
        *,
        route_file: str,
        mtime: float,
        size: int,
        scanned_atoms: int,
        indexed_transitions: int,
    ) -> None:
        mtime_ns = int(round(float(mtime) * 1_000_000_000))
        rows = [
            ("schema_version", str(int(ROUTE_TRANSITION_INDEX_SCHEMA_VERSION))),
            ("route_file", os.path.abspath(route_file)),
            ("route_mtime_ns", str(mtime_ns)),
            ("route_size", str(int(size))),
            ("scanned_atoms", str(int(scanned_atoms))),
            ("indexed_transitions", str(int(indexed_transitions))),
            ("built_at_epoch", str(int(time.time()))),
        ]
        conn.executemany("INSERT INTO meta(key, value) VALUES(?, ?)", rows)

    def _connect_for_build(self, target: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(target))
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-20000")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE transitions(
                atom_id INTEGER NOT NULL,
                start_frame INTEGER NOT NULL,
                end_frame INTEGER NOT NULL,
                from_label TEXT NOT NULL,
                to_label TEXT NOT NULL,
                from_canonical TEXT NOT NULL,
                to_canonical TEXT NOT NULL,
                from_formula TEXT NOT NULL,
                to_formula TEXT NOT NULL
            )
            """
        )
        return conn

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX idx_transitions_atom ON transitions(atom_id)")
        conn.execute("CREATE INDEX idx_transitions_start_end ON transitions(start_frame, end_frame)")
        conn.execute("CREATE INDEX idx_transitions_canonical ON transitions(from_canonical, to_canonical)")
        conn.execute("CREATE INDEX idx_transitions_formula ON transitions(from_formula, to_formula)")

    def _build_index(
        self,
        *,
        route_file: str,
        mtime: float,
        size: int,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> dict[str, Any]:
        index_path = route_transition_index_path(route_file, mtime=mtime, size=size)
        tmp_path = index_path.with_suffix(index_path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

        file_size = max(int(size), 1)
        bytes_read = 0
        last_emit = 0.0
        scanned_atoms = 0
        indexed_transitions = 0
        batch: list[tuple[Any, ...]] = []
        batch_size = 5000

        self._emit(
            progress_callback,
            progress=progress_start,
            phase="indexing_route",
            message=f"Building route transition index: {os.path.basename(route_file)}",
        )
        conn = self._connect_for_build(tmp_path)
        try:
            with open(route_file, encoding="utf-8", errors="ignore") as fh:
                for raw_line in fh:
                    bytes_read += len(raw_line)
                    m = ROUTE_LINE_RE.match(raw_line.strip())
                    if not m:
                        continue
                    scanned_atoms += 1
                    atom_id = int(m.group(1))
                    route_text = m.group(2)
                    transitions: list[tuple[int, str]] = []
                    for hit in ROUTE_STEP_RE.finditer(route_text):
                        transitions.append((int(hit.group(1)), hit.group(2)))
                    if len(transitions) >= 2:
                        prev_ts, prev_label = transitions[0]
                        prev_can, prev_formula = _normalize_route_species_label(prev_label)
                        for ts, label in transitions[1:]:
                            if prev_label != label:
                                current_can, current_formula = _normalize_route_species_label(label)
                                batch.append(
                                    (
                                        atom_id,
                                        int(prev_ts),
                                        int(ts),
                                        str(prev_label),
                                        str(label),
                                        str(prev_can),
                                        str(current_can),
                                        str(prev_formula),
                                        str(current_formula),
                                    )
                                )
                                indexed_transitions += 1
                                prev_can, prev_formula = current_can, current_formula
                            else:
                                prev_can, prev_formula = _normalize_route_species_label(label)
                            prev_ts = ts
                            prev_label = label
                    if len(batch) >= batch_size:
                        conn.executemany(
                            """
                            INSERT INTO transitions(
                                atom_id, start_frame, end_frame,
                                from_label, to_label,
                                from_canonical, to_canonical,
                                from_formula, to_formula
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            batch,
                        )
                        batch.clear()
                    frac = bytes_read / file_size
                    now = time.monotonic()
                    if frac >= 0.99 or (now - last_emit) >= 1.0:
                        self._emit(
                            progress_callback,
                            progress=progress_start + progress_span * min(frac * 0.85, 0.85),
                            phase="indexing_route",
                            message=f"Building route index: {frac * 100:.1f}%",
                            scanned_atoms=scanned_atoms,
                            indexed_transitions=indexed_transitions,
                        )
                        last_emit = now
            if batch:
                conn.executemany(
                    """
                    INSERT INTO transitions(
                        atom_id, start_frame, end_frame,
                        from_label, to_label,
                        from_canonical, to_canonical,
                        from_formula, to_formula
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
            self._emit(
                progress_callback,
                progress=progress_start + progress_span * 0.88,
                phase="indexing_route",
                message="Finalizing route transition index",
                scanned_atoms=scanned_atoms,
                indexed_transitions=indexed_transitions,
            )
            self._create_indexes(conn)
            self._store_meta_rows(
                conn,
                route_file=route_file,
                mtime=mtime,
                size=size,
                scanned_atoms=scanned_atoms,
                indexed_transitions=indexed_transitions,
            )
            conn.commit()
        except Exception:
            conn.close()
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

        os.replace(tmp_path, index_path)
        meta = {
            "index_path": str(index_path),
            "route_file": os.path.abspath(route_file),
            "route_mtime": float(mtime),
            "route_size": int(size),
            "scanned_atoms": int(scanned_atoms),
            "indexed_transitions": int(indexed_transitions),
            "index_state": "built",
        }
        self._emit(
            progress_callback,
            progress=progress_start + progress_span,
            phase="indexing_route",
            message=f"Route index ready: {indexed_transitions} transitions",
            scanned_atoms=scanned_atoms,
            indexed_transitions=indexed_transitions,
            index_path=str(index_path),
        )
        return meta

    def get(
        self,
        route_file: str,
        *,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> dict[str, Any]:
        path, mtime, size = self._route_signature(route_file)
        key = (path, mtime, size)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and os.path.exists(cached["index_path"]):
                self._emit(
                    progress_callback,
                    progress=progress_start + progress_span,
                    phase="cached_route_index",
                    message=f"Using cached route transition index: {os.path.basename(path)}",
                    indexed_transitions=int(cached.get("indexed_transitions", 0) or 0),
                )
                hit = dict(cached)
                hit["index_state"] = "cached_memory"
                return hit
            build_lock = self._build_locks.setdefault(key, threading.Lock())

        with build_lock:
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None and os.path.exists(cached["index_path"]):
                    self._emit(
                        progress_callback,
                        progress=progress_start + progress_span,
                        phase="cached_route_index",
                        message=f"Using cached route transition index: {os.path.basename(path)}",
                        indexed_transitions=int(cached.get("indexed_transitions", 0) or 0),
                    )
                    hit = dict(cached)
                    hit["index_state"] = "cached_memory"
                    return hit

            index_path = route_transition_index_path(path, mtime=mtime, size=size)
            if index_path.exists():
                meta_rows = self._read_meta(index_path)
                meta = {
                    "index_path": str(index_path),
                    "route_file": path,
                    "route_mtime": float(mtime),
                    "route_size": int(size),
                    "scanned_atoms": int(meta_rows.get("scanned_atoms", 0) or 0),
                    "indexed_transitions": int(meta_rows.get("indexed_transitions", 0) or 0),
                    "index_state": "cached_disk",
                }
                with self._lock:
                    self._cache[key] = dict(meta)
                self._emit(
                    progress_callback,
                    progress=progress_start + progress_span,
                    phase="cached_route_index",
                    message=f"Using persisted route transition index: {os.path.basename(path)}",
                    indexed_transitions=int(meta.get("indexed_transitions", 0) or 0),
                )
                return meta

            fresh = self._build_index(
                route_file=path,
                mtime=mtime,
                size=size,
                progress_callback=progress_callback,
                progress_start=progress_start,
                progress_span=progress_span,
            )
            with self._lock:
                self._cache[key] = dict(fresh)
            return fresh

    def query_reaction_hits(
        self,
        route_file: str,
        reaction_query: dict[str, Any],
        *,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> dict[str, Any]:
        reactant_tokens = sorted(str(token) for token in reaction_query["reactant_token_set"])
        product_tokens = sorted(str(token) for token in reaction_query["product_token_set"])
        if not reactant_tokens or not product_tokens:
            return {
                "hits": [],
                "scanned_atoms": 0,
                "matched_atom_transitions": 0,
                "route_index": {},
            }

        build_span = progress_span * 0.82
        query_span = max(progress_span - build_span, 0.0)
        index_meta = self.get(
            route_file,
            progress_callback=progress_callback,
            progress_start=progress_start,
            progress_span=build_span,
        )

        index_path = str(index_meta["index_path"])
        match_mode = str(reaction_query["match_mode"] or "canonical_smiles")
        from_col = "from_canonical" if match_mode == "canonical_smiles" else "from_formula"
        to_col = "to_canonical" if match_mode == "canonical_smiles" else "to_formula"
        reactant_placeholders = ",".join("?" for _ in reactant_tokens)
        product_placeholders = ",".join("?" for _ in product_tokens)
        sql = (
            "SELECT atom_id, start_frame, end_frame, from_label, to_label, "
            f"{from_col} AS from_token, {to_col} AS to_token "
            "FROM transitions "
            "WHERE "
            f"(({from_col} IN ({reactant_placeholders}) AND {to_col} IN ({product_placeholders})) "
            f"OR ({from_col} IN ({product_placeholders}) AND {to_col} IN ({reactant_placeholders}))) "
            "ORDER BY start_frame, end_frame, atom_id"
        )
        params = [*reactant_tokens, *product_tokens, *product_tokens, *reactant_tokens]
        self._emit(
            progress_callback,
            progress=progress_start + build_span,
            phase="querying_route_index",
            message=f"Querying route transition index: {os.path.basename(route_file)}",
        )

        hits: list[dict[str, Any]] = []
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            cursor = conn.execute(sql, params)
            for atom_id, start_frame, end_frame, from_label, to_label, from_token, to_token in cursor:
                direction = ""
                if str(from_token) in reaction_query["reactant_token_set"] and str(to_token) in reaction_query["product_token_set"]:
                    direction = "reactant_to_product"
                elif str(from_token) in reaction_query["product_token_set"] and str(to_token) in reaction_query["reactant_token_set"]:
                    direction = "product_to_reactant"
                if not direction:
                    continue
                hits.append(
                    {
                        "atom_id": int(atom_id),
                        "start_frame": int(start_frame),
                        "end_frame": int(end_frame),
                        "anchor_frame": int(end_frame),
                        "from_label": str(from_label),
                        "to_label": str(to_label),
                        "from_token": str(from_token),
                        "to_token": str(to_token),
                        "direction": direction,
                    }
                )
        finally:
            conn.close()
        self._emit(
            progress_callback,
            progress=progress_start + progress_span,
            phase="querying_route_index",
            message=f"Matched {len(hits)} route transitions from indexed cache",
            matched_atom_transitions=len(hits),
        )
        return {
            "hits": hits,
            "scanned_atoms": int(index_meta.get("scanned_atoms", 0) or 0),
            "matched_atom_transitions": len(hits),
            "route_index": index_meta,
        }


ROUTE_TRANSITION_INDEX_STORE = RouteTransitionIndexStore()


class RouteAnalysisStore:
    def __init__(self, max_entries: int = 32) -> None:
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._max_entries = max(4, int(max_entries))

    def get(
        self,
        route_file: str,
        *,
        selected_frames: list[int],
        anchor_frames: list[int],
        previous_frame_of_anchor: dict[int, int],
        target: str,
        match_mode: str,
        reaction_smiles: str,
        atom_sample_limit: int,
        progress_callback: Any = None,
        progress_start: float = 0.70,
        progress_span: float = 0.02,
    ) -> dict[str, Any]:
        path = os.path.abspath(route_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"route file not found: {path}")
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
        query_key = (
            path,
            mtime,
            size,
            tuple(int(x) for x in selected_frames),
            tuple(int(x) for x in anchor_frames),
            tuple((int(k), int(v)) for k, v in sorted(previous_frame_of_anchor.items(), key=lambda item: int(item[0]))),
            str(target or ""),
            str(match_mode or ""),
            str(reaction_smiles or ""),
            int(atom_sample_limit),
        )
        with self._lock:
            cached = self._cache.get(query_key)
            if cached is not None:
                self._cache.move_to_end(query_key)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "progress": max(0.0, min(progress_start + progress_span, 1.0)),
                            "phase": "cached_route_analysis",
                            "message": f"Using cached route analysis: {os.path.basename(path)}",
                        }
                    )
                return cached

        fresh = summarize_route_atom_changes(
            path,
            selected_frames=selected_frames,
            anchor_frames=anchor_frames,
            previous_frame_of_anchor=previous_frame_of_anchor,
            target=target,
            match_mode=match_mode,
            reaction_smiles=reaction_smiles,
            atom_sample_limit=atom_sample_limit,
            progress_callback=progress_callback,
            progress_start=progress_start,
            progress_span=progress_span,
        )
        with self._lock:
            self._cache[query_key] = fresh
            self._cache.move_to_end(query_key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return fresh


ROUTE_ANALYSIS_STORE = RouteAnalysisStore()


class ReactionEventLocateStore:
    def __init__(self, max_entries: int = 32) -> None:
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._max_entries = max(4, int(max_entries))

    def _build_key(self, params: dict[str, list[str]]) -> tuple[Any, ...]:
        files = _resolve_reaction_context_files(params)
        reaction_text = _resolve_reaction_query_text(params)
        reaction_query = _prepare_reaction_query(reaction_text)
        before_frames = max(0, int_param(params, "before_frames", 5))
        after_frames = max(0, int_param(params, "after_frames", 5))
        max_events = max(1, min(int_param(params, "max_events", 12), 200))
        type_element_map = parse_type_element_map_specs((params.get("type_element_map", [""])[0] or "").strip())

        def file_sig(path_text: str) -> tuple[str, float, int]:
            path = os.path.abspath(path_text) if path_text else ""
            if not path or not os.path.exists(path):
                return path, 0.0, 0
            return path, os.path.getmtime(path), os.path.getsize(path)

        return (
            file_sig(files["species_file"]),
            file_sig(files["route_file"]),
            file_sig(files["trajectory_file"]),
            reaction_query["reaction_signature"],
            int(before_frames),
            int(after_frames),
            int(max_events),
            tuple(sorted(type_element_map.items(), key=lambda item: int(item[0]))),
        )

    def get(
        self,
        params: dict[str, list[str]],
        *,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> dict[str, Any]:
        query_key = self._build_key(params)
        with self._lock:
            cached = self._cache.get(query_key)
            if cached is not None:
                self._cache.move_to_end(query_key)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "progress": max(0.0, min(progress_start + progress_span, 1.0)),
                            "phase": "cached_reaction_event_locate",
                            "message": "Using cached reaction-event locate result",
                            "rows": len(cached.get("rows") or []),
                        }
                    )
                return cached

        scaled_callback = progress_callback
        if progress_callback is not None and (progress_start != 0.0 or progress_span != 1.0):
            def scaled_callback(payload: dict[str, Any]) -> None:
                forwarded = dict(payload or {})
                progress = float(forwarded.get("progress", 0.0) or 0.0)
                forwarded["progress"] = max(0.0, min(progress_start + progress_span * progress, 1.0))
                progress_callback(forwarded)

        fresh = build_reaction_event_locate_payload(
            params,
            progress_callback=scaled_callback,
        )
        with self._lock:
            self._cache[query_key] = fresh
            self._cache.move_to_end(query_key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return fresh


REACTION_EVENT_LOCATE_STORE = ReactionEventLocateStore()


class TrajectoryIndexStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, TrajectoryFrameIndex] = {}

    def peek(self, trajectory_file: str) -> TrajectoryFrameIndex | None:
        path = os.path.abspath(trajectory_file)
        if not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached.mtime == mtime and cached.size == size:
                return cached
        return None

    def get(
        self,
        trajectory_file: str,
        *,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> TrajectoryFrameIndex:
        path = os.path.abspath(trajectory_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"trajectory file not found: {path}")
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)

        with self._lock:
            cached = self._cache.get(path)
            if cached and cached.mtime == mtime and cached.size == size:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "progress": max(0.0, min(progress_start + progress_span, 1.0)),
                            "phase": "cached_trajectory_index",
                            "message": f"Using cached trajectory index: {os.path.basename(path)}",
                            "n_index_frames": len(cached.frames),
                        }
                    )
                return cached

        persisted = self._load_persisted(path, mtime=mtime, size=size)
        if persisted is not None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "progress": max(0.0, min(progress_start + progress_span, 1.0)),
                        "phase": "cached_trajectory_index_disk",
                        "message": f"Using persistent trajectory index: {os.path.basename(path)}",
                        "n_index_frames": len(persisted.frames),
                        "index_path": str(trajectory_frame_index_path(path, mtime=mtime, size=size)),
                    }
                )
            with self._lock:
                self._cache[path] = persisted
            return persisted

        fresh = self._scan_index(
            path,
            mtime=mtime,
            size=size,
            progress_callback=progress_callback,
            progress_start=progress_start,
            progress_span=progress_span,
        )
        self._persist(fresh)
        with self._lock:
            self._cache[path] = fresh
        return fresh

    def _load_persisted(self, trajectory_file: str, *, mtime: float, size: int) -> TrajectoryFrameIndex | None:
        index_path = trajectory_frame_index_path(trajectory_file, mtime=mtime, size=size)
        if not index_path.exists():
            return None
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            expected_mtime_ns = int(round(float(mtime) * 1_000_000_000))
            if (
                int(payload.get("schema_version", 0)) != TRAJECTORY_FRAME_INDEX_SCHEMA_VERSION
                or str(payload.get("trajectory_file", "")) != os.path.abspath(trajectory_file)
                or int(payload.get("trajectory_mtime_ns", -1)) != expected_mtime_ns
                or int(payload.get("trajectory_size", -1)) != int(size)
            ):
                return None
            offsets: dict[int, tuple[int, int]] = {}
            for item in payload.get("frame_offsets") or []:
                frame, start, end = int(item[0]), int(item[1]), int(item[2])
                if end > start:
                    offsets[frame] = (start, end)
            frames = [int(frame) for frame in payload.get("frames") or []]
            if not frames or not offsets:
                return None
            return TrajectoryFrameIndex(
                trajectory_file=os.path.abspath(trajectory_file),
                mtime=mtime,
                size=size,
                frames=frames,
                frame_offsets=offsets,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError, IndexError):
            return None

    def _persist(self, index: TrajectoryFrameIndex) -> None:
        index_path = trajectory_frame_index_path(index.trajectory_file, mtime=index.mtime, size=index.size)
        payload = {
            "schema_version": TRAJECTORY_FRAME_INDEX_SCHEMA_VERSION,
            "trajectory_file": os.path.abspath(index.trajectory_file),
            "trajectory_mtime_ns": int(round(float(index.mtime) * 1_000_000_000)),
            "trajectory_size": int(index.size),
            "frames": [int(frame) for frame in index.frames],
            "frame_offsets": [
                [int(frame), int(start), int(end)]
                for frame, (start, end) in sorted(index.frame_offsets.items())
            ],
        }
        tmp_path = index_path.with_suffix(index_path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp_path, index_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _scan_index(
        self,
        trajectory_file: str,
        *,
        mtime: float,
        size: int,
        progress_callback: Any = None,
        progress_start: float = 0.0,
        progress_span: float = 1.0,
    ) -> TrajectoryFrameIndex:
        file_size = max(size, 1)
        bytes_read = 0
        last_emit = 0.0
        frames: list[int] = []
        frame_offsets: dict[int, tuple[int, int]] = {}
        current_frame: int | None = None
        current_start: int | None = None

        def emit(progress: float, message: str, **extra: Any) -> None:
            if progress_callback is None:
                return
            payload = {
                "progress": max(0.0, min(float(progress), 1.0)),
                "phase": "indexing_trajectory",
                "message": message,
            }
            payload.update(extra)
            progress_callback(payload)

        emit(progress_start, f"Indexing trajectory frames: {os.path.basename(trajectory_file)}")
        with open(trajectory_file, "rb") as fh:
            while True:
                block_start = fh.tell()
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
                            f"Indexing trajectory: {frac * 100:.1f}%",
                            n_index_frames=len(frames),
                        )
                        last_emit = now
                    continue

                timestep_line = fh.readline()
                if not timestep_line:
                    break
                bytes_read += len(timestep_line)

                if current_frame is not None and current_start is not None and block_start > current_start:
                    frame_offsets[current_frame] = (current_start, block_start)

                try:
                    current_frame = int(timestep_line.strip().split()[0])
                    frames.append(current_frame)
                except Exception:
                    current_frame = None
                current_start = block_start

                now = time.monotonic()
                frac = bytes_read / file_size
                if frac >= 0.99 or (now - last_emit) >= 1.0:
                    emit(
                        progress_start + progress_span * min(frac, 1.0),
                        f"Indexing trajectory: {frac * 100:.1f}%",
                        n_index_frames=len(frames),
                        frame=current_frame,
                    )
                    last_emit = now

        if current_frame is not None and current_start is not None and size > current_start:
            frame_offsets[current_frame] = (current_start, size)

        emit(
            progress_start + progress_span,
            f"Trajectory index ready: {len(frames)} frames",
            n_index_frames=len(frames),
        )
        return TrajectoryFrameIndex(
            trajectory_file=trajectory_file,
            mtime=mtime,
            size=size,
            frames=frames,
            frame_offsets=frame_offsets,
        )


TRAJECTORY_INDEX_STORE = TrajectoryIndexStore()


class AsyncTaskStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def create(self, kind: str, query: dict[str, Any]) -> str:
        task_id = uuid4().hex
        now = time.time()
        task = {
            "task_id": task_id,
            "kind": kind,
            "status": "queued",
            "progress": 0.0,
            "phase": "queued",
            "message": "Queued",
            "error": None,
            "result": None,
            "query": query,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._prune_locked(now)
            self._tasks[task_id] = task
        return task_id

    def update(self, task_id: str, **fields: Any) -> None:
        now = time.time()
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.update(fields)
            task["updated_at"] = now

    def complete(self, task_id: str, result: dict[str, Any]) -> None:
        self.update(
            task_id,
            status="completed",
            progress=1.0,
            phase="completed",
            message="Completed",
            error=None,
            result=result,
        )

    def fail(self, task_id: str, error: str) -> None:
        self.update(
            task_id,
            status="error",
            phase="error",
            message=error,
            error=error,
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return dict(task)

    def _prune_locked(self, now: float) -> None:
        stale_ids = [
            task_id
            for task_id, task in self._tasks.items()
            if task.get("updated_at", now) < now - 3600
        ]
        for task_id in stale_ids:
            self._tasks.pop(task_id, None)
        if len(self._tasks) <= 64:
            return
        keep = sorted(
            self._tasks.items(),
            key=lambda item: item[1].get("updated_at", 0.0),
            reverse=True,
        )[:64]
        self._tasks = {task_id: task for task_id, task in keep}


TASKS = AsyncTaskStore()


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


class AppHandler(BaseHTTPRequestHandler):
    server_version = "RNGQueryWeb/0.1"

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str) -> None:
        if not path.exists() or not path.is_file():
            self._send_text("Not Found", status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel: str) -> None:
        rel = rel.lstrip("/")
        base = SCRIPT_DIR / "static"
        base_resolved = base.resolve()
        file_path = (base_resolved / rel).resolve()
        try:
            file_path.relative_to(base_resolved)
        except ValueError:
            self._send_text("Forbidden", status=403)
            return
        ctype = "text/plain; charset=utf-8"
        if file_path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif file_path.suffix == ".svg":
            ctype = "image/svg+xml"
        self._send_file(file_path, ctype)

    def _api_species(self, params: dict[str, list[str]]) -> None:
        formula = (params.get("formula", [""])[0] or "").strip()
        if not formula:
            self._send_json({"ok": False, "error": "missing formula"}, status=400)
            return
        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        species_file_raw = (params.get("species_file", [""])[0] or "").strip()
        top = int_param(params, "top", 50)
        min_tp = int_param(params, "min_tp", 1)

        try:
            net = STORE.get(reac, min_tp)
            smiles_set = net.smiles_by_formula(formula)
            rows = []
            for smi in smiles_set:
                sp = net.species[smi]
                rows.append(
                    {
                        "smiles": smi,
                        "formula": sp.formula,
                        **formula_mass_fields(sp.formula),
                        "tp_total": sp.total_throughput,
                        "tp_consume": sp.tp_as_reactant,
                        "tp_produce": sp.tp_as_product,
                        "net_production": sp.net_production,
                        "n_consume_rxns": sp.n_consume_rxns,
                        "n_produce_rxns": sp.n_produce_rxns,
                    }
                )
            rows.sort(key=lambda x: x["tp_total"], reverse=True)
            if top > 0:
                rows = rows[:top]
            species_file = species_file_raw or derive_species_path(reac)
            species_found = bool(species_file and os.path.exists(species_file))
            occurrence_stats: dict[str, dict[str, Any]] = {}
            if species_found and rows:
                occurrence_stats = collect_species_occurrence_stats(species_file, [str(row["smiles"]) for row in rows])
                for row in rows:
                    row.update(occurrence_stats.get(str(row["smiles"]), {}))
            self._send_json(
                {
                    "ok": True,
                    "query": {"formula": formula, "reac": reac, "species_file": species_file_raw, "min_tp": min_tp, "top": top},
                    "meta": {
                        "rows": len(rows),
                        "species_file": species_file,
                        "species_file_found": species_found,
                        "with_frame_ranges": bool(occurrence_stats),
                        **formula_mass_fields(formula),
                    },
                    "rows": rows,
                }
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_species_mass(self, params: dict[str, list[str]]) -> None:
        mass_raw = (params.get("mass", [""])[0] or "").strip()
        if not mass_raw:
            self._send_json({"ok": False, "error": "missing mass"}, status=400)
            return
        try:
            target_mass = float(mass_raw)
        except ValueError:
            self._send_json({"ok": False, "error": "invalid mass"}, status=400)
            return

        mode = (params.get("mode", ["nominal"])[0] or "nominal").strip().lower()
        if mode not in {"nominal", "exact"}:
            mode = "nominal"
        tol_default = 0.0 if mode == "nominal" else 0.5
        tol = abs(float_param(params, "tol", tol_default))
        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        species_file_raw = (params.get("species_file", [""])[0] or "").strip()
        top = int_param(params, "top", 100)
        min_tp = int_param(params, "min_tp", 1)

        target_nominal = int(round(target_mass))
        try:
            net = STORE.get(reac, min_tp)
            rows: list[dict[str, Any]] = []
            for smi, sp in net.species.items():
                f = sp.formula
                exact = exact_mass_cached(f)
                nominal = nominal_mass_cached(f)
                if exact is None or nominal is None:
                    continue
                if mode == "exact":
                    err = exact - target_mass
                    if abs(err) > tol:
                        continue
                    err_ppm = (err / target_mass * 1e6) if target_mass else None
                else:
                    err = float(nominal - target_nominal)
                    if abs(err) > tol:
                        continue
                    err_ppm = None
                rows.append(
                    {
                        "smiles": smi,
                        "formula": f,
                        "exact_mass": round(exact, 6),
                        "nominal_mass": nominal,
                        "mass_error": round(err, 6),
                        "ppm_error": _round_or_none(err_ppm, 3),
                        "tp_total": sp.total_throughput,
                        "tp_consume": sp.tp_as_reactant,
                        "tp_produce": sp.tp_as_product,
                        "net_production": sp.net_production,
                        "n_consume_rxns": sp.n_consume_rxns,
                        "n_produce_rxns": sp.n_produce_rxns,
                    }
                )

            rows.sort(key=lambda x: (abs(float(x["mass_error"])), -int(x["tp_total"])))
            if top > 0:
                rows = rows[:top]
            species_file = species_file_raw or derive_species_path(reac)
            species_found = bool(species_file and os.path.exists(species_file))
            occurrence_stats: dict[str, dict[str, Any]] = {}
            if species_found and rows:
                occurrence_stats = collect_species_occurrence_stats(species_file, [str(row["smiles"]) for row in rows])
                for row in rows:
                    row.update(occurrence_stats.get(str(row["smiles"]), {}))
            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "mass": target_mass,
                        "target_nominal_mass": target_nominal,
                        "mode": mode,
                        "tol": tol,
                        "reac": reac,
                        "species_file": species_file_raw,
                        "min_tp": min_tp,
                        "top": top,
                    },
                    "meta": {
                        "rows": len(rows),
                        "species_file": species_file,
                        "species_file_found": species_found,
                        "with_frame_ranges": bool(occurrence_stats),
                    },
                    "rows": rows,
                }
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_next(self, params: dict[str, list[str]]) -> None:
        start_query = (
            params.get("start", [""])[0]
            or params.get("query", [""])[0]
            or params.get("smiles", [""])[0]
            or ""
        ).strip()
        if not start_query:
            self._send_json({"ok": False, "error": "missing start query"}, status=400)
            return
        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        role = (params.get("role", ["consume"])[0] or "consume").strip().lower()
        if role not in {"consume", "produce", "both"}:
            role = "consume"
        top = int_param(params, "top", 30)
        min_tp = int_param(params, "min_tp", 1)
        net_positive_only = bool_param(params, "net_positive_only", False)

        try:
            net = STORE.get(reac, min_tp)
            resolved_smiles = resolve_start_smiles(net, start_query)
            formula_candidates = list(net.smiles_by_formula(start_query)) if looks_like_formula(start_query) else []
            query_type = "smiles" if start_query in net.species else ("formula" if looks_like_formula(start_query) else "smiles")
            if not resolved_smiles:
                self._send_json(
                    {
                        "ok": True,
                        "query": {
                            "start": start_query,
                            "query_type": query_type,
                            "resolved_smiles": None,
                            "role": role,
                            "reac": reac,
                            "min_tp": min_tp,
                            "top": top,
                            "net_positive_only": net_positive_only,
                        },
                        "meta": {
                            "rows": 0,
                            "formula_candidates": len(formula_candidates),
                            "message": "no matching species for start query",
                        },
                        "rows": [],
                    }
                )
                return
            smiles = resolved_smiles
            matched = collect_next_reactions(net, smiles, role)
            if net_positive_only:
                matched = [x for x in matched if x.net_tp > 0]
            if top > 0:
                matched = matched[:top]

            sp = net.species[smiles]
            rows = []
            for i, m in enumerate(matched, 1):
                rows.append(
                    {
                        "rank": i,
                        "role": m.role,
                        "tp": m.forward_tp,
                        "reverse_tp": m.reverse_tp,
                        "net_tp": m.net_tp,
                        "ratio_pct": round(m.ratio_pct, 3),
                        "reaction_formulas": reaction_formula_str(m.reaction),
                        "reaction_smiles": reaction_smiles_str(m.reaction),
                        **reaction_mass_fields(m.reaction),
                    }
                )

            sp_masses = formula_mass_fields(sp.formula)
            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "start": start_query,
                        "query_type": query_type,
                        "resolved_smiles": smiles,
                        "role": role,
                        "reac": reac,
                        "min_tp": min_tp,
                        "top": top,
                        "net_positive_only": net_positive_only,
                    },
                    "meta": {
                        "formula": sp.formula,
                        "formula_candidates": len(formula_candidates),
                        **sp_masses,
                        "tp_consume": sp.tp_as_reactant,
                        "tp_produce": sp.tp_as_product,
                        "net_production": sp.net_production,
                        "rows": len(rows),
                    },
                    "rows": rows,
                }
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_rxn_formula(self, params: dict[str, list[str]]) -> None:
        reactants = split_terms((params.get("reactants", [""])[0] or "").strip())
        products = split_terms((params.get("products", [""])[0] or "").strip())
        if not reactants and not products:
            self._send_json({"ok": False, "error": "missing query: provide reactants and/or products"}, status=400)
            return

        mode = (params.get("mode", ["exact"])[0] or "exact").strip().lower()
        if mode not in {"exact", "contains"}:
            mode = "exact"
        with_share = bool_param(params, "with_share", False)
        share_metric = (params.get("share_metric", ["net_tp"])[0] or "net_tp").strip()
        if share_metric not in {"tp", "reverse_tp", "net_tp"}:
            share_metric = "net_tp"
        share_abs_metric = bool_param(params, "share_abs_metric", False)
        share_positive_only = bool_param(params, "share_positive_only", False)

        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        top = int_param(params, "top", 50)
        min_tp = int_param(params, "min_tp", 1)

        try:
            net = STORE.get(reac, min_tp)
            tp_map = {r.key: r.tp for r in net.reactions}
            need_r = Counter(reactants)
            need_p = Counter(products)
            share_total_metric: float | None = None
            share_top_sum: float | None = None
            rows = []
            for rxn in net.reactions:
                if not match_formula_reaction(rxn, need_r, need_p, mode):
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
                        **reaction_mass_fields(rxn),
                    }
                )

            if with_share:
                scored_rows: list[tuple[float, dict[str, Any]]] = []
                for row in rows:
                    v = float(row.get(share_metric, 0.0))
                    if share_abs_metric:
                        v = abs(v)
                    if share_positive_only and v <= 0:
                        continue
                    scored_rows.append((v, row))

                scored_rows.sort(key=lambda x: x[0], reverse=True)
                total_metric = sum(v for v, _ in scored_rows)
                share_total_metric = total_metric
                if top > 0:
                    scored_rows = scored_rows[:top]
                share_top_sum = sum(v for v, _ in scored_rows)

                rows_out: list[dict[str, Any]] = []
                cum = 0.0
                for i, (v, row) in enumerate(scored_rows, 1):
                    pct = (v / total_metric * 100.0) if total_metric else 0.0
                    cum += pct
                    d = dict(row)
                    d["rank"] = i
                    d["metric_value"] = v
                    d["share_pct"] = round(pct, 3)
                    d["cumulative_pct"] = round(cum, 3)
                    rows_out.append(d)
                rows = rows_out
            else:
                rows.sort(key=lambda x: (x["tp"], abs(x["net_tp"])), reverse=True)
                if top > 0:
                    rows = rows[:top]
                for i, row in enumerate(rows, 1):
                    row["rank"] = i

            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "reactants": reactants,
                        "products": products,
                        "mode": mode,
                        "reac": reac,
                        "min_tp": min_tp,
                        "top": top,
                        "with_share": with_share,
                        "share_metric": share_metric,
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
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_generate_initiation_csv(self, params: dict[str, list[str]]) -> None:
        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        min_tp = int_param(params, "min_tp", 1)
        start = (params.get("start", [""])[0] or "").strip()
        out_csv = (params.get("out_csv", [""])[0] or "").strip()
        aggregate_by = (params.get("aggregate_by", ["formula"])[0] or "formula").strip().lower()
        if aggregate_by not in {"formula", "smiles"}:
            aggregate_by = "formula"
        include_formula_preserving = bool_param(params, "include_formula_preserving", False)
        initiation_min_loss = int_param(params, "min_net_start_loss", 1)
        top_n = int_param(params, "top_n", 0)
        export_branches = bool_param(params, "export_branches", True)
        out_branches = (params.get("out_branches_csv", [""])[0] or "").strip()

        if not start:
            self._send_json({"ok": False, "error": "missing start (SMILES or formula)"}, status=400)
            return
        if not out_csv:
            base = Path(reac).resolve().parent
            out_csv = str(base / "initiation_channels.csv")

        try:
            net = STORE.get(reac, min_tp)
            start_smiles = resolve_start_smiles(net, start)
            if not start_smiles:
                self._send_json({"ok": False, "error": f"start species not found: {start}"}, status=404)
                return

            channels, meta = net.extract_initiation_channels(
                start_smiles,
                aggregate_by=aggregate_by,
                include_formula_preserving=include_formula_preserving,
                min_net_start_loss=max(1, initiation_min_loss),
                top_n=top_n if top_n > 0 else 0,
            )

            export_initiation_csv(channels, out_csv)
            branches_path = ""
            if export_branches:
                if not out_branches:
                    p = Path(out_csv)
                    out_branches = str(p.with_name(f"{p.stem}_smiles_branches{p.suffix or '.csv'}"))
                export_initiation_smiles_branches_csv(channels, out_branches)
                branches_path = out_branches

            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "reac": reac,
                        "min_tp": min_tp,
                        "start": start,
                        "start_smiles_resolved": start_smiles,
                        "aggregate_by": aggregate_by,
                        "include_formula_preserving": include_formula_preserving,
                        "min_net_start_loss": max(1, initiation_min_loss),
                        "top_n": top_n,
                    },
                    "meta": {
                        "channels_exported": len(channels),
                        "start_formula": meta.get("start_formula", ""),
                        "positive_loss_total": meta.get("positive_loss_total", 0),
                        "species_net_loss": meta.get("species_net_loss", 0),
                    },
                    "outputs": {
                        "csv": out_csv,
                        "branches_csv": branches_path,
                    },
                }
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_intermediate_candidates(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_intermediate_candidates_payload(params))
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_intermediate_candidates_start(self, params: dict[str, list[str]]) -> None:
        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        species_file = (params.get("species_file", [""])[0] or "").strip()
        if not species_file:
            species_file = derive_species_path(reac)

        task_id = TASKS.create(
            "intermediate_candidates",
            {
                "reac": reac,
                "species_file": species_file,
                "kind": (params.get("kind", ["intermediate"])[0] or "intermediate").strip().lower(),
            },
        )
        params_copy = {key: list(values) for key, values in params.items()}

        def worker() -> None:
            TASKS.update(
                task_id,
                status="running",
                progress=0.01,
                phase="starting",
                message="Starting intermediate-candidate task",
            )
            try:
                result = build_intermediate_candidates_payload(
                    params_copy,
                    progress_callback=lambda payload: TASKS.update(
                        task_id,
                        status="running",
                        **payload,
                    ),
                )
                TASKS.complete(task_id, result)
            except Exception as exc:
                TASKS.fail(task_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        self._send_json({"ok": True, "task_id": task_id})

    def _api_structure_context(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_structure_context_payload(params))
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_structure_context_start(self, params: dict[str, list[str]]) -> None:
        task_id = TASKS.create(
            "structure_context",
            {
                "species_file": (params.get("species_file", [""])[0] or "").strip(),
                "trajectory_file": (params.get("trajectory_file", [""])[0] or "").strip(),
                "route_file": (params.get("route_file", [""])[0] or "").strip(),
                "target": (params.get("target", [""])[0] or "").strip(),
                "reaction_smiles": (params.get("reaction_smiles", [""])[0] or "").strip(),
            },
        )
        params_copy = {key: list(values) for key, values in params.items()}

        def worker() -> None:
            TASKS.update(
                task_id,
                status="running",
                progress=0.01,
                phase="starting",
                message="Starting structure-context task",
            )
            try:
                result = build_structure_context_payload(
                    params_copy,
                    progress_callback=lambda payload: TASKS.update(
                        task_id,
                        status="running",
                        **payload,
                    ),
                )
                TASKS.complete(task_id, result)
            except Exception as exc:
                TASKS.fail(task_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        self._send_json({"ok": True, "task_id": task_id})

    def _api_reaction_event_locate(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_reaction_event_locate_payload(params))
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_reaction_event_locate_start(self, params: dict[str, list[str]]) -> None:
        task_id = TASKS.create(
            "reaction_event_locate",
            {
                "species_file": (params.get("species_file", [""])[0] or "").strip(),
                "trajectory_file": (params.get("trajectory_file", [""])[0] or "").strip(),
                "route_file": (params.get("route_file", [""])[0] or "").strip(),
                "reaction_smiles": _resolve_reaction_query_text(params),
            },
        )
        params_copy = {key: list(values) for key, values in params.items()}

        def worker() -> None:
            TASKS.update(
                task_id,
                status="running",
                progress=0.01,
                phase="starting",
                message="Starting reaction-event locate task",
            )
            try:
                result = REACTION_EVENT_LOCATE_STORE.get(
                    params_copy,
                    progress_callback=lambda payload: TASKS.update(
                        task_id,
                        status="running",
                        **payload,
                    ),
                )
                TASKS.complete(task_id, result)
            except Exception as exc:
                TASKS.fail(task_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        self._send_json({"ok": True, "task_id": task_id})

    def _api_reaction_event_extract(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_reaction_event_extract_payload(params))
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_reaction_event_extract_start(self, params: dict[str, list[str]]) -> None:
        task_id = TASKS.create(
            "reaction_event_extract",
            {
                "species_file": (params.get("species_file", [""])[0] or "").strip(),
                "trajectory_file": (params.get("trajectory_file", [""])[0] or "").strip(),
                "route_file": (params.get("route_file", [""])[0] or "").strip(),
                "reaction_smiles": _resolve_reaction_query_text(params),
                "event_id": (params.get("event_id", [""])[0] or "").strip(),
            },
        )
        params_copy = {key: list(values) for key, values in params.items()}

        def worker() -> None:
            TASKS.update(
                task_id,
                status="running",
                progress=0.01,
                phase="starting",
                message="Starting reaction-event extract task",
            )
            try:
                result = build_reaction_event_extract_payload(
                    params_copy,
                    progress_callback=lambda payload: TASKS.update(
                        task_id,
                        status="running",
                        **payload,
                    ),
                )
                TASKS.complete(task_id, result)
            except Exception as exc:
                TASKS.fail(task_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        self._send_json({"ok": True, "task_id": task_id})

    def _api_open_path(self, params: dict[str, list[str]]) -> None:
        path = (params.get("path", [""])[0] or "").strip()
        mode = (params.get("mode", ["default"])[0] or "default").strip()
        if not path:
            self._send_json({"ok": False, "error": "missing path"}, status=400)
            return
        try:
            self._send_json(open_path_with_system(path, mode))
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_pick_folder(self, params: dict[str, list[str]]) -> None:
        initial_dir = (params.get("initial_dir", [""])[0] or "").strip()
        try:
            self._send_json(pick_folder_with_system(initial_dir))
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_plot(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_species_plot_payload(params))
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_transition_table(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_transition_table_payload(params))
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_dataset_status(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_dataset_status_payload(params))
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _resolve_evolution_mode(self, params: dict[str, list[str]]) -> str:
        for key in ("plot_kind", "evolution_mode", "mode"):
            raw = (params.get(key, [""])[0] or "").strip().lower()
            if not raw:
                continue
            if raw in {"species", "carbon"}:
                return raw
            # Compatibility: carbon requests may still send plotting mode as `mode`.
            if key == "mode" and raw in {"exact", "binned", "topk"}:
                return "carbon"
        return "species"

    def _api_evolution_plot(self, params: dict[str, list[str]]) -> None:
        mode = self._resolve_evolution_mode(params)
        try:
            builder = build_carbon_plot_payload if mode == "carbon" else build_species_plot_payload
            result = builder(params)
            if isinstance(result, dict):
                result.setdefault("mode", mode)
            self._send_json(result)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _api_evolution_plot_start(self, params: dict[str, list[str]]) -> None:
        mode = self._resolve_evolution_mode(params)

        task_id = TASKS.create(
            "evolution_plot",
            {
                "mode": mode,
                "data": (params.get("data", [""])[0] or "").strip(),
                "species_file": (params.get("species_file", [""])[0] or "").strip(),
                "target_count": len(split_target_items(params.get("target", []))),
            },
        )
        params_copy = {key: list(values) for key, values in params.items()}

        def worker() -> None:
            TASKS.update(
                task_id,
                status="running",
                mode=mode,
                progress=0.01,
                phase="starting",
                message=f"Starting {mode} plot task",
            )
            try:
                builder = build_carbon_plot_payload if mode == "carbon" else build_species_plot_payload
                result = builder(
                    params_copy,
                    progress_callback=lambda payload: TASKS.update(
                        task_id,
                        status="running",
                        mode=mode,
                        **payload,
                    ),
                )
                if isinstance(result, dict):
                    result.setdefault("mode", mode)
                TASKS.complete(task_id, result)
            except Exception as exc:
                TASKS.fail(task_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        self._send_json({"ok": True, "task_id": task_id, "mode": mode})

    def _api_carbon_plot(self, params: dict[str, list[str]]) -> None:
        try:
            self._send_json(build_carbon_plot_payload(params))
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_carbon_plot_start(self, params: dict[str, list[str]]) -> None:
        params_copy = {key: list(values) for key, values in params.items()}
        params_copy["mode"] = ["carbon"]
        self._api_evolution_plot_start(params_copy)

    def _api_task_status(self, params: dict[str, list[str]]) -> None:
        task_id = (params.get("task_id", [""])[0] or "").strip()
        if not task_id:
            self._send_json({"ok": False, "error": "missing task_id"}, status=400)
            return
        task = TASKS.get(task_id)
        if task is None:
            self._send_json({"ok": False, "error": f"task not found: {task_id}"}, status=404)
            return
        payload = {
            "ok": True,
            "task_id": task_id,
            "kind": task.get("kind"),
            "status": task.get("status"),
            "progress": task.get("progress", 0.0),
            "progress_pct": round(float(task.get("progress", 0.0)) * 100.0, 1),
            "phase": task.get("phase"),
            "message": task.get("message"),
            "error": task.get("error"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }
        for key, value in task.items():
            if key in payload or key in {"result", "query"}:
                continue
            payload[key] = value
        if task.get("status") == "completed" and task.get("result") is not None:
            payload["result"] = task.get("result")
        self._send_json(payload)

    def _api_smiles_svg(self, params: dict[str, list[str]]) -> None:
        smiles = (params.get("smiles", [""])[0] or "").strip()
        if not smiles:
            self._send_text("missing smiles", status=400)
            return

        width = int_param(params, "w", 360)
        height = int_param(params, "h", 240)
        show_h = bool_param(params, "show_h", True)
        width = max(120, min(width, 1200))
        height = max(100, min(height, 1000))

        try:
            svg = smiles_to_svg(smiles, width=width, height=height, show_h=show_h)
            self._send_text(svg, status=200, ctype="image/svg+xml; charset=utf-8")
        except Exception as e:
            fallback = render_error_svg(str(e), width=width, height=height)
            self._send_text(fallback, status=200, ctype="image/svg+xml; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            rel = unquote(path[len("/static/") :])
            self._serve_static(rel)
            return

        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "status": "alive",
                    "rdkit": {
                        "available": bool(Chem is not None and rdMolDraw2D is not None),
                    },
                }
            )
            return
        if path == "/api/species":
            self._api_species(params)
            return
        if path == "/api/species_mass":
            self._api_species_mass(params)
            return
        if path == "/api/next":
            self._api_next(params)
            return
        if path == "/api/rxn_formula":
            self._api_rxn_formula(params)
            return
        if path == "/api/generate_initiation_csv":
            self._api_generate_initiation_csv(params)
            return
        if path == "/api/intermediate_candidates":
            self._api_intermediate_candidates(params)
            return
        if path == "/api/intermediate_candidates_start":
            self._api_intermediate_candidates_start(params)
            return
        if path == "/api/structure_context":
            self._api_structure_context(params)
            return
        if path == "/api/structure_context_start":
            self._api_structure_context_start(params)
            return
        if path == "/api/reaction_event_locate":
            self._api_reaction_event_locate(params)
            return
        if path == "/api/reaction_event_locate_start":
            self._api_reaction_event_locate_start(params)
            return
        if path == "/api/reaction_event_extract":
            self._api_reaction_event_extract(params)
            return
        if path == "/api/reaction_event_extract_start":
            self._api_reaction_event_extract_start(params)
            return
        if path == "/api/open_path":
            self._api_open_path(params)
            return
        if path == "/api/pick_folder":
            self._api_pick_folder(params)
            return
        if path == "/api/plot":
            self._api_plot(params)
            return
        if path == "/api/transition_table":
            self._api_transition_table(params)
            return
        if path == "/api/dataset_status":
            self._api_dataset_status(params)
            return
        if path == "/api/evolution_plot":
            self._api_evolution_plot(params)
            return
        if path == "/api/evolution_plot_start":
            self._api_evolution_plot_start(params)
            return
        if path == "/api/carbon_plot_start":
            self._api_carbon_plot_start(params)
            return
        if path == "/api/task_status":
            self._api_task_status(params)
            return
        if path == "/api/carbon_plot":
            self._api_carbon_plot(params)
            return
        if path == "/api/smiles_svg":
            self._api_smiles_svg(params)
            return

        self._send_text("Not Found", status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep logs concise for terminal usage
        sys.stdout.write("[web] " + fmt % args + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="RNG Query Web backend")
    ap.add_argument("--host", default="127.0.0.1", help="bind host")
    ap.add_argument("--port", type=int, default=8765, help="bind port")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"[RNG-Query-Web] http://{args.host}:{args.port}")
    print("[RNG-Query-Web] Press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
