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

import os
import re
import shlex
import sys
import time
import traceback
from functools import lru_cache
from bisect import bisect_left
from pathlib import Path
from collections import Counter
from typing import Any
from urllib.parse import quote

# Ensure the project tool root is importable when this package is loaded
# directly (e.g. via ``uv run reacnet-scope-web-dash``).
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from rng_tools.network import ReactionNetwork, count_atoms_fast, formula_from_counts, parse_reactionabcd  # noqa: E402
from reacnet_scope.indexes import (  # noqa: E402
    IndexBuildInProgressError,
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    clear_index,
    resolve_dataset_paths,
    TRAJECTORY_INDEX_STORE,
)
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE  # noqa: E402
from reacnet_scope.rng_events import RngEventDataError, query_rng_events  # noqa: E402
from scripts.webapp.server import (  # noqa: E402
    STORE,
    build_dataset_status_payload,
    build_carbon_plot_payload,
    build_intermediate_candidates_payload,
    build_species_plot_payload,
    build_transition_table_payload,
    collect_species_totals,
    collect_next_reactions,
    derive_species_path,
    formula_mass_fields,
    looks_like_formula,
    match_formula_reaction,
    net_flux,
    reaction_formula_str,
    reaction_mass_fields,
    reaction_smiles_str,
    resolve_start_smiles,
    smiles_formula_cached,
    smiles_to_svg,
    split_terms,
    parse_lammpstrj_frame_block,
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
        return build_dataset_status_payload(
            {
                "dataset_dir": [folder_text],
                "dataset_base": [base or ""],
            }
        )
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


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def artifacts_from_status(status: dict[str, Any]) -> dict[str, str]:
    """Return a compact ``{kind: path}`` mapping from a dataset status payload."""
    dataset = status.get("dataset", {}) if status else {}
    artifacts = dataset.get("artifacts", {}) or {}
    out: dict[str, str] = {}
    for key in ("reaction", "species", "moname", "trajectory", "route", "table", "reactionevent", "molecules"):
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
    if species_source:
        try:
            composition = SPECIES_COMPOSITION_STORE.status(species_source)
        except RuntimeError as exc:
            composition = {"state": "invalid", "message": str(exc)}
    else:
        composition = {"state": "missing"}
    index_bytes = int(trajectory.get("index_size", 0) or 0) + int(composition.get("index_size", 0) or 0)
    timestamps = [
        value
        for value in (
            trajectory.get("updated_at_epoch"),
            composition.get("updated_at_epoch"),
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


def _species_catalog_entry(smiles: str, total_count: int, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "structure_source": ".moname" if evidence else "SMILES",
        "moname_available": bool(evidence),
        "moname_occurrences": int(evidence.get("moname_occurrences") or 0),
        "moname_atom_count": int(evidence.get("moname_atom_count") or 0),
        "moname_bond_count": int(evidence.get("moname_bond_count") or 0),
    }


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


def search_species_catalog(
    artifacts: dict[str, str],
    query: str = "",
    *,
    kind: str = "auto",
    mass_tolerance: float = 0.5,
    mass_mode: str = "exact",
    top: int = 50,
) -> dict[str, Any]:
    """Search the ``.species``-derived target-species catalogue.

    Unlike the legacy species search, this endpoint does not require a
    reaction network.  It is the first stage of the focused experimental
    species workflow; reaction channels are joined only in the next stage.
    """
    species_path = (artifacts.get("species") or "").strip()
    if not species_path or not Path(species_path).is_file():
        raise ServiceError("缺少 .species 数据文件，无法建立物种目录", reason="missing_species_file")
    text = (query or "").strip()
    effective_kind = kind if kind and kind != "auto" else (detect_query_kind(text) if text else "all")
    if effective_kind not in {"all", "formula", "smiles", "mass"}:
        raise ServiceError(f"未知查询类型: {effective_kind}", reason="bad_kind")
    target_mass: float | None = None
    if effective_kind == "mass":
        try:
            target_mass = float(text)
        except ValueError as exc:
            raise ServiceError(f"无效的质量数: {text}", reason="bad_mass") from exc

    species_signature = _file_signature(species_path)
    moname_signature = _file_signature((artifacts.get("moname") or "").strip())
    # The landing table is deliberately a fast path: it needs only the most
    # abundant rows, not formula/mass derivation for every species in a large
    # trajectory.  Formula and mass searches build the reusable full index.
    limit = max(1, int(top or 30))
    if effective_kind in {"all", "smiles"}:
        totals = collect_species_totals(species_signature[0])
        fast_items = [
            (smiles, total_count)
            for smiles, total_count in totals.items()
            if effective_kind == "all" or text.lower() in smiles.lower()
        ]
        matching_count = len(fast_items)
        fast_items.sort(key=lambda item: (-int(item[1]), item[0]))
        catalog = tuple(_species_catalog_entry(smiles, int(total_count)) for smiles, total_count in fast_items[:limit])
        catalog_size = len(totals)
        full_catalog_cached = False
    else:
        catalog = _load_species_search_catalog(*species_signature, *moname_signature)
        catalog_size = len(catalog)
        full_catalog_cached = True
        matching_count = None
    rows: list[dict[str, Any]] = []
    tolerance = max(0.0, float(mass_tolerance or 0.0))
    normalized_mode = mass_mode if mass_mode in {"exact", "nominal"} else "exact"
    for entry in catalog:
        smiles = str(entry["smiles"])
        formula = str(entry["formula"])
        exact = entry.get("exact_mass")
        nominal = entry.get("nominal_mass")
        mass_error: float | None = None
        if effective_kind == "formula" and formula != text:
            continue
        if effective_kind == "smiles" and text.lower() not in smiles.lower():
            continue
        if effective_kind == "mass":
            if exact is None or nominal is None or target_mass is None:
                continue
            comparison = float(exact) if normalized_mode == "exact" else float(nominal)
            target = target_mass if normalized_mode == "exact" else float(round(target_mass))
            mass_error = comparison - target
            if abs(mass_error) > tolerance:
                continue
        rows.append(
            {
                **entry,
                "mass_error": round(mass_error, 6) if mass_error is not None else None,
                "ppm_error": round(mass_error / target_mass * 1e6, 3) if mass_error is not None and target_mass else None,
            }
        )
    if effective_kind == "mass":
        rows.sort(key=lambda row: (abs(float(row.get("mass_error") or 0.0)), -int(row["total_count"]), row["smiles"]))
    else:
        rows.sort(key=lambda row: (-int(row["total_count"]), row["formula"], row["smiles"]))
    visible_rows = rows[:limit]
    for row in visible_rows:
        row["structure"] = _structure_markdown(str(row["smiles"]))
    return {
        "ok": True,
        "query": {"text": text, "kind": effective_kind, "mass_mode": normalized_mode, "mass_tolerance": tolerance},
        "rows": visible_rows,
        "n_rows": matching_count if matching_count is not None else len(rows),
        "meta": {
            "species_file": species_signature[0],
            "moname_file": moname_signature[0],
            "moname_available": bool(moname_signature[0]),
            "catalog_size": catalog_size,
            "catalog_cache": "memory" if full_catalog_cached else "fast-path",
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
    mass_mode: str = "exact",
    top: int = 0,
) -> dict[str, Any]:
    """Search species by formula / SMILES / mass using the existing network.

    Returns ``{ok, rows, query_kind}`` where each row carries the fields
    needed by AG Grid and the right-hand detail panel.
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
        net = STORE.get(reac_path, 1)
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
        rows = _rows_for_mass(net, text, mass_tolerance, mode=mass_mode)
    else:
        raise ServiceError(f"未知查询类型: {effective_kind}", reason="bad_kind")

    if int(top or 0) > 0:
        rows = rows[: int(top)]
    return {
        "ok": True,
        "query_kind": effective_kind,
        "query": text,
        "rows": rows,
        "n_rows": len(rows),
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


def _rows_for_mass(net: ReactionNetwork, query: str, tolerance: float, *, mode: str = "exact") -> list[dict[str, Any]]:
    try:
        target = float(query)
    except ValueError as exc:
        raise ServiceError(f"无效的质量数: {query}", reason="bad_mass") from exc
    tol = max(0.0, float(tolerance))
    mass_mode = mode if mode in {"nominal", "exact"} else "exact"
    target_nominal = int(round(target))
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
        error = float(exact) - target if mass_mode == "exact" else float(nominal) - target_nominal
        if abs(error) > tol:
            continue
        row = _species_row(net, smi)
        row["mass_error"] = round(error, 6)
        row["ppm_error"] = round(error / target * 1e6, 3) if mass_mode == "exact" and target else None
        rows.append(row)
    rows.sort(key=lambda r: (abs(float(r.get("mass_error") or 0.0)), -(r["total_throughput"]), r["smiles"]))
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
            net = STORE.get(reac_path, 1)
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


def render_species_svg(smiles: str, *, width: int = 280, height: int = 200) -> dict[str, Any]:
    """Render a 2D structure SVG using the existing RDKit helper."""
    smi = (smiles or "").strip()
    if not smi:
        return {"ok": False, "svg": "", "message": "未选择物种"}
    try:
        svg = smiles_to_svg(smi, width=width, height=height, show_h=True)
        return {"ok": True, "svg": svg, "message": ""}
    except Exception as exc:
        return {"ok": False, "svg": "", "message": str(exc) or "RDKit 渲染失败"}


# ---------------------------------------------------------------------------
# Transition relations (next-step reactions)
# ---------------------------------------------------------------------------


def collect_transitions(
    artifacts: dict[str, str],
    smiles: str,
    *,
    direction: str = "both",
    top: int = 30,
    net_positive_only: bool = False,
) -> dict[str, Any]:
    """Wrap ``collect_next_reactions`` for the transition relations page."""
    smi = (smiles or "").strip()
    if not smi:
        raise ServiceError("请先在物种检索中选择一个物种", reason="missing_species")
    reac_path = (artifacts.get("reaction") or "").strip()
    if not reac_path or not os.path.exists(reac_path):
        raise ServiceError("缺少 reactionabcd 数据文件", reason="missing_reaction")
    role = direction if direction in {"consume", "produce", "both"} else "both"
    try:
        net = STORE.get(reac_path, 1)
    except Exception as exc:
        raise ServiceError(f"加载反应网络失败: {exc}") from exc
    if smi not in net.species:
        raise ServiceError(f"当前网络中不存在该物种: {smi}", reason="species_not_found")
    try:
        matched = collect_next_reactions(net, smi, role)
    except Exception as exc:
        raise ServiceError(f"查询转化关系失败: {exc}") from exc
    rows = [_transition_row(m) for m in matched]
    if net_positive_only:
        rows = [row for row in rows if int(row.get("net_tp") or 0) > 0]
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
    production = collect_transitions(artifacts, smiles, direction="produce", top=0).get("rows") or []
    consumption = collect_transitions(artifacts, smiles, direction="consume", top=0).get("rows") or []

    def decorate(rows: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
        prepared = []
        for row in rows:
            out = dict(row)
            out["workflow_role"] = role
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
        net = STORE.get(reac_path, 1)
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
        "forward_tp": int(matched.forward_tp),
        "reverse_tp": int(matched.reverse_tp),
        "net_tp": int(matched.net_tp),
        "ratio_pct": round(float(matched.ratio_pct), 4),
        "tp": int(rxn.tp),
    }


# ---------------------------------------------------------------------------
# Transition table payload (species transition matrix)
# ---------------------------------------------------------------------------


def build_transition_table(
    artifacts: dict[str, str],
    *,
    min_count: int = 1,
    max_species: int = 60,
    top_edges: int = 40,
) -> dict[str, Any]:
    """Wrap ``build_transition_table_payload`` for the observation network page."""
    table_path = (artifacts.get("table") or "").strip()
    if not table_path:
        raise ServiceError("缺少 .lammpstrj.table 数据文件", reason="missing_table")
    if not os.path.exists(table_path):
        raise ServiceError(f"table 文件不存在: {table_path}", reason="missing_table")
    params = {
        "table": [table_path],
        "min_count": [str(max(0, int(min_count)))],
        "max_species": [str(max(0, int(max_species)))],
        "top_edges": [str(max(1, min(500, int(top_edges))))],
    }
    try:
        return build_transition_table_payload(params)
    except Exception as exc:
        raise ServiceError(f"构建转化矩阵失败: {exc}") from exc


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
    if not molecules_file or not Path(molecules_file).is_file():
        raise ServiceError(
            "缺少 .molecules.csv；请在 ReacNetGenerator 中启用 --show-molecule-time",
            reason="missing_molecules",
        )
    try:
        return query_rng_events(
            reactionevent_file,
            molecules_file,
            reaction_text,
            max_events=max_events,
        )
    except (OSError, ValueError, RngEventDataError) as exc:
        raise ServiceError(str(exc), reason="rng_event_data_error") from exc


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


def upsert_validation_record(
    validations: list[dict[str, Any]] | None,
    *,
    dataset_id: str,
    species: dict[str, Any],
    channel: dict[str, Any],
    event: dict[str, Any],
    outcome: str,
    note: str = "",
    recorded_at: str = "",
) -> list[dict[str, Any]]:
    """Upsert one user validation in the session-local evidence ledger."""
    normalized = outcome if outcome in {"support", "insufficient", "exclude"} else "insufficient"
    row = {
        "dataset_id": dataset_id,
        "species_formula": species.get("formula") or "",
        "species_smiles": species.get("smiles") or "",
        "channel_role": channel.get("role_label") or channel.get("workflow_role") or "",
        "reaction_formulas": channel.get("reaction_formulas") or "",
        "reaction_smiles": channel.get("reaction_smiles") or "",
        "forward_tp": channel.get("forward_tp"),
        "reverse_tp": channel.get("reverse_tp"),
        "net_tp": channel.get("net_tp"),
        "event_id": event.get("event_id") or "",
        "before_timestep": event.get("before_timestep"),
        "after_timestep": event.get("after_timestep"),
        "atom_ids": event.get("atom_ids") or "",
        "association_status": event.get("association_status") or "",
        "validation_outcome": normalized,
        "note": (note or "").strip(),
        "recorded_at": recorded_at,
    }
    items = [dict(item) for item in (validations or [])]
    key = (str(dataset_id), str(row["event_id"]))
    for index, item in enumerate(items):
        if (str(item.get("dataset_id") or ""), str(item.get("event_id") or "")) == key:
            items[index] = row
            return items
    items.append(row)
    return items


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
# Observation network (Cytoscape)
# ---------------------------------------------------------------------------


def build_observation_elements(
    artifacts: dict[str, str],
    *,
    min_count: int = 1,
    max_species: int = 60,
    top_edges: int = 40,
) -> dict[str, Any]:
    """Build Cytoscape elements from the existing transition table payload."""
    payload = build_transition_table(
        artifacts,
        min_count=min_count,
        max_species=max_species,
        top_edges=top_edges,
    )
    network = payload.get("network") or {}
    species_nodes = network.get("species") or []
    reaction_nodes = network.get("reactions") or []
    edges = network.get("edges") or []

    elements: list[dict[str, Any]] = []
    for node in species_nodes:
        elements.append(
            {
                "data": {
                    "id": node["id"],
                    "label": node.get("formula") or node.get("smiles") or node["id"],
                    "kind": "species",
                    "smiles": node.get("smiles", ""),
                    "formula": node.get("formula", ""),
                    "rank": int(node.get("rank") or 0),
                    "incoming": int(node.get("incoming") or 0),
                    "outgoing": int(node.get("outgoing") or 0),
                    "total": int(node.get("total") or 0),
                },
                "classes": "species",
            }
        )
    for node in reaction_nodes:
        elements.append(
            {
                "data": {
                    "id": node["id"],
                    "label": node.get("label") or node["id"],
                    "kind": "reaction",
                    "reaction_type": node.get("reaction_type", ""),
                    "event_count": int(node.get("event_count") or 0),
                    "net_event_count": int(node.get("net_event_count") or 0),
                    "ordinal": int(node.get("ordinal") or 0),
                },
                "classes": "reaction",
            }
        )
    for edge in edges:
        elements.append(
            {
                "data": {
                    "id": edge["id"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "kind": edge.get("kind", ""),
                    "event_count": int(edge.get("event_count") or 0),
                },
            }
        )
    return {
        "ok": True,
        "elements": elements,
        "species_smiles": [n.get("smiles", "") for n in species_nodes],
        "meta": payload.get("meta", {}),
        "network": network,
    }


# ---------------------------------------------------------------------------
# Literature mechanism verification
# ---------------------------------------------------------------------------


def verify_literature_mechanism(
    artifacts: dict[str, str],
    reaction_texts: list[str],
    *,
    verify_mode: str = "species",
) -> dict[str, Any]:
    """Verify a list of literature reactions against simulation data.

    Parameters
    ----------
    artifacts:
        Dataset artifact paths dict.
    reaction_texts:
        List of reaction strings (e.g. ``"A + B -> C + D"``).
    verify_mode:
        ``"species"`` for formula-level only, ``"atom"`` for atom-level
        (requires ``.route`` file).
    """
    from rng_tools.mechanism_verify import MechanismVerifier

    reac_path = (artifacts.get("reaction") or "").strip()
    if not reac_path or not os.path.isfile(reac_path):
        raise ServiceError("需要 .reactionabcd 文件", reason="missing_reac")

    # Load or reuse the ReactionNetwork
    network = STORE.get(reac_path, 1)

    verifier = MechanismVerifier(network)

    try:
        mechanism_reactions = verifier.parse_literature_reactions(reaction_texts)
    except Exception as exc:
        raise ServiceError(f"无法解析文献反应式: {exc}", reason="parse_error")

    if not mechanism_reactions:
        raise ServiceError("未能解析任何有效反应式", reason="no_reactions")

    matrix = verifier.build_matrix(mechanism_reactions)
    rows = verifier.matrix_to_rows(matrix)
    summary = verifier.summary_to_dict(matrix)

    return {
        "ok": True,
        "rows": rows,
        "summary": summary,
        "meta": {
            "status": "ok",
            "message": (
                f"验证完成: {summary['detected']}/{summary['total_reactions']} "
                f"检测到, {summary['has_net_flux']} 存在净通量"
            ),
            "verify_mode": verify_mode,
        },
    }


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
    "list_directory",
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
    "search_species_catalog",
    "search_species",
    "species_detail",
    "render_species_svg",
    "collect_transitions",
    "collect_species_channels",
    "search_reactions_by_formula",
    "build_transition_table",
    "build_species_evolution",
    "evolution_to_csv",
    "build_carbon_evolution",
    "build_elemental_composition_evolution",
    "composition_index_status",
    "build_carbon_species_drilldown",
    "carbon_plot_to_csv",
    "build_intermediate_candidates",
    "locate_rng_events",
    "rank_representative_events",
    "build_rng_event_visualization",
    "upsert_validation_record",
    "rows_to_csv",
    "build_observation_elements",
    "verify_literature_mechanism",
    "scan_batch_conditions",
    "run_batch_comparison",
]
