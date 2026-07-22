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
import sys
import traceback
from pathlib import Path
from collections import Counter
from typing import Any

# Ensure the project tool root is importable when this package is loaded
# directly (e.g. via ``uv run reacnet-scope-web-dash``).
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from rng_tools.network import ReactionNetwork, parse_reactionabcd  # noqa: E402
from scripts.webapp.server import (  # noqa: E402
    STORE,
    build_dataset_status_payload,
    build_carbon_plot_payload,
    build_intermediate_candidates_payload,
    build_reaction_event_extract_payload,
    build_reaction_event_locate_payload,
    build_species_plot_payload,
    build_structure_context_payload,
    build_transition_table_payload,
    collect_next_reactions,
    derive_species_path,
    formula_mass_fields,
    looks_like_formula,
    match_formula_reaction,
    net_flux,
    pick_folder_with_system,
    reaction_formula_str,
    reaction_mass_fields,
    reaction_smiles_str,
    resolve_start_smiles,
    smiles_formula_cached,
    smiles_to_svg,
    split_terms,
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


def pick_folder_macos(initial_dir: str = "") -> dict[str, Any]:
    """Open the native macOS folder picker.  Returns ``{ok, path, canceled}``."""
    try:
        return pick_folder_with_system(initial_dir)
    except RuntimeError as exc:
        raise ServiceError(str(exc), reason="picker_unavailable") from exc


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def artifacts_from_status(status: dict[str, Any]) -> dict[str, str]:
    """Return a compact ``{kind: path}`` mapping from a dataset status payload."""
    dataset = status.get("dataset", {}) if status else {}
    artifacts = dataset.get("artifacts", {}) or {}
    out: dict[str, str] = {}
    for key in ("reaction", "species", "trajectory", "route", "table"):
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


def candidates_from_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = status.get("dataset", {}) if status else {}
    return list(dataset.get("candidates", []) or [])


# ---------------------------------------------------------------------------
# Species search (formula / SMILES / mass)
# ---------------------------------------------------------------------------


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
    return rows_to_csv(payload.get("plot_data") or [])


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


def locate_species_events(
    artifacts: dict[str, str],
    target: str,
    *,
    species_file: str = "",
    trajectory_file: str = "",
    route_file: str = "",
    match_mode: str = "auto",
    event_mode: str = "appear",
    before_frames: int = 3,
    after_frames: int = 3,
    max_events: int = 12,
    include_route_trace: bool = True,
    trajectory_atom_scope: str = "event",
    type_element_map: str = "",
) -> dict[str, Any]:
    """Mirror legacy structure-context event location without inline viewer."""
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (species_file or artifacts.get("species") or "").strip()
    trajectory_path = (trajectory_file or artifacts.get("trajectory") or "").strip()
    route_path = (route_file or artifacts.get("route") or "").strip()
    if not target.strip():
        raise ServiceError("请输入目标物种或分子式", reason="missing_target")
    params = {
        "reac": [reac_path or ""],
        "species_file": [species_path],
        "trajectory_file": [trajectory_path],
        "route_file": [route_path],
        "target": [target.strip()],
        "match_mode": [match_mode if match_mode in {"auto", "smiles", "formula"} else "auto"],
        "event_mode": [event_mode if event_mode in {"appear", "disappear", "production", "consumption", "peak", "nonzero"} else "appear"],
        "before_frames": [str(max(0, int(before_frames)))],
        "after_frames": [str(max(0, int(after_frames)))],
        "max_events": [str(max(1, int(max_events)))],
        "include_route_trace": ["1" if include_route_trace else "0"],
        "include_trajectory": ["0"],
        "inline_viewer": ["0"],
        "trajectory_atom_scope": [trajectory_atom_scope if trajectory_atom_scope in {"all", "event"} else "event"],
        "type_element_map": [(type_element_map or "").strip()],
    }
    try:
        return build_structure_context_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"定位物种事件失败: {exc}") from exc


def locate_reaction_events(
    artifacts: dict[str, str],
    reaction_text: str,
    *,
    species_file: str = "",
    trajectory_file: str = "",
    route_file: str = "",
    type_element_map: str = "",
    before_frames: int = 5,
    after_frames: int = 5,
    max_events: int = 12,
    defer_trajectory_verification: bool = True,
) -> dict[str, Any]:
    """Mirror legacy ``/api/reaction_event_locate``."""
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (species_file or artifacts.get("species") or "").strip()
    trajectory_path = (trajectory_file or artifacts.get("trajectory") or "").strip()
    route_path = (route_file or artifacts.get("route") or "").strip()
    if not reaction_text.strip():
        raise ServiceError("请输入反应式", reason="missing_reaction_query")
    params = {
        "reac": [reac_path or ""],
        "species_file": [species_path],
        "trajectory_file": [trajectory_path],
        "route_file": [route_path],
        "reaction_smiles": [reaction_text.strip()],
        "before_frames": [str(max(0, int(before_frames)))],
        "after_frames": [str(max(0, int(after_frames)))],
        "max_events": [str(max(1, int(max_events)))],
        "defer_trajectory_verification": ["1" if defer_trajectory_verification else "0"],
        "type_element_map": [(type_element_map or "").strip()],
    }
    try:
        return build_reaction_event_locate_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"定位反应事件失败: {exc}") from exc


def extract_reaction_event(
    artifacts: dict[str, str],
    reaction_text: str,
    event_id: str,
    *,
    species_file: str = "",
    trajectory_file: str = "",
    route_file: str = "",
    type_element_map: str = "",
    before_frames: int = 5,
    after_frames: int = 5,
    max_events: int = 200,
    inline_viewer: bool = False,
) -> dict[str, Any]:
    """Mirror legacy ``/api/reaction_event_extract`` for a selected event_id."""
    reac_path = (artifacts.get("reaction") or "").strip()
    species_path = (species_file or artifacts.get("species") or "").strip()
    trajectory_path = (trajectory_file or artifacts.get("trajectory") or "").strip()
    route_path = (route_file or artifacts.get("route") or "").strip()
    if not reaction_text.strip():
        raise ServiceError("请输入反应式", reason="missing_reaction_query")
    if not event_id.strip():
        raise ServiceError("请输入或选择 event_id", reason="missing_event_id")
    params = {
        "reac": [reac_path or ""],
        "species_file": [species_path],
        "trajectory_file": [trajectory_path],
        "route_file": [route_path],
        "reaction_smiles": [reaction_text.strip()],
        "event_id": [event_id.strip()],
        "before_frames": [str(max(0, int(before_frames)))],
        "after_frames": [str(max(0, int(after_frames)))],
        "max_events": [str(max(1, int(max_events)))],
        "type_element_map": [(type_element_map or "").strip()],
        "inline_viewer": ["1" if inline_viewer else "0"],
    }
    try:
        return build_reaction_event_extract_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"抽取反应事件轨迹失败: {exc}") from exc


def extract_species_event(
    artifacts: dict[str, str],
    target: str,
    anchor_frame: int,
    *,
    species_file: str = "",
    trajectory_file: str = "",
    route_file: str = "",
    match_mode: str = "auto",
    event_mode: str = "appear",
    before_frames: int = 3,
    after_frames: int = 3,
    include_route_trace: bool = True,
    trajectory_atom_scope: str = "event",
    type_element_map: str = "",
) -> dict[str, Any]:
    """Extract a visualizable local trajectory for a selected species event."""
    target_text = (target or "").strip()
    if not target_text:
        raise ServiceError("缺少物种事件目标", reason="missing_target")
    try:
        frame_value = int(anchor_frame)
    except (TypeError, ValueError) as exc:
        raise ServiceError("选中事件缺少有效锚定帧", reason="missing_anchor_frame") from exc

    params = {
        "reac": [(artifacts.get("reaction") or "").strip()],
        "species_file": [(species_file or artifacts.get("species") or "").strip()],
        "trajectory_file": [(trajectory_file or artifacts.get("trajectory") or "").strip()],
        "route_file": [(route_file or artifacts.get("route") or "").strip()],
        "target": [target_text],
        "match_mode": [match_mode if match_mode in {"auto", "smiles", "formula"} else "auto"],
        "event_mode": [event_mode if event_mode in {"appear", "disappear", "production", "consumption", "peak", "nonzero"} else "appear"],
        "anchor_frame": [str(frame_value)],
        "before_frames": [str(max(0, int(before_frames)))],
        "after_frames": [str(max(0, int(after_frames)))],
        "max_events": ["1"],
        "include_route_trace": ["1" if include_route_trace else "0"],
        "include_trajectory": ["1"],
        "inline_viewer": ["1"],
        "trajectory_atom_scope": [trajectory_atom_scope if trajectory_atom_scope in {"all", "event"} else "event"],
        "type_element_map": [(type_element_map or "").strip()],
    }
    try:
        return build_structure_context_payload(params)
    except FileNotFoundError as exc:
        raise ServiceError(str(exc), reason="missing_file") from exc
    except ValueError as exc:
        raise ServiceError(str(exc), reason="bad_request") from exc
    except Exception as exc:
        raise ServiceError(f"抽取物种事件轨迹失败: {exc}") from exc


def build_event_visualization(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert a small extracted LAMMPS trajectory into Dash-safe frame data.

    The legacy endpoint can return the local trajectory text for compact event
    windows.  Dash stores only parsed atoms and coordinates, never the raw
    trajectory string, to keep the browser-side workflow responsive.
    """
    from scripts.webapp.server import parse_lammpstrj_frame_block

    trajectory_text = str(payload.get("trajectory_text") or payload.get("trajectory_preview_text") or "").strip()
    if not trajectory_text:
        raise ServiceError(
            "未收到可内嵌的局部轨迹。请缩小事件窗口或降低上下文原子范围后重试。",
            reason="trajectory_preview_unavailable",
        )

    atom_groups = payload.get("atom_groups") or {}
    core_ids = {int(value) for value in atom_groups.get("core_atom_ids", []) if str(value).strip()}
    reactant_ids = {int(value) for value in atom_groups.get("reactant_atom_ids", []) if str(value).strip()}
    product_ids = {int(value) for value in atom_groups.get("product_atom_ids", []) if str(value).strip()}
    context_ids = {int(value) for value in atom_groups.get("context_atom_ids", []) if str(value).strip()}

    def atom_group(atom_id: int) -> str:
        if atom_id in core_ids:
            return "core"
        if atom_id in reactant_ids and atom_id in product_ids:
            return "shared"
        if atom_id in reactant_ids:
            return "reactant"
        if atom_id in product_ids:
            return "product"
        if atom_id in context_ids:
            return "context"
        return "context"

    frames: list[dict[str, Any]] = []
    for block in re.split(r"(?=ITEM: TIMESTEP)", trajectory_text):
        if not block.strip().startswith("ITEM: TIMESTEP"):
            continue
        parsed = parse_lammpstrj_frame_block(block.encode("utf-8"))
        frame_number = parsed.get("frame")
        if frame_number is None:
            continue
        atoms = []
        for atom_id, atom in sorted((parsed.get("atoms") or {}).items()):
            aid = int(atom_id)
            atoms.append(
                {
                    "id": aid,
                    "x": round(float(atom.get("x", 0.0)), 7),
                    "y": round(float(atom.get("y", 0.0)), 7),
                    "z": round(float(atom.get("z", 0.0)), 7),
                    "element": str(atom.get("element") or ""),
                    "type": str(atom.get("type") or ""),
                    "group": atom_group(aid),
                }
            )
        if atoms:
            frames.append({"frame": int(frame_number), "box": parsed.get("box") or [], "atoms": atoms})

    if not frames:
        raise ServiceError("局部轨迹中没有可显示的原子坐标", reason="no_coordinates")

    frames.sort(key=lambda item: int(item["frame"]))
    available_frames = [int(item["frame"]) for item in frames]
    requested_storyboard = [int(value) for value in (payload.get("storyboard_frames") or []) if int(value) in set(available_frames)]
    if not requested_storyboard:
        anchor = None
        selected_event = payload.get("selected_event") or {}
        for value in (selected_event.get("anchor_frame"), payload.get("anchor_frame")):
            try:
                anchor = int(value)
                break
            except (TypeError, ValueError):
                continue
        anchor_index = min(range(len(available_frames)), key=lambda idx: abs(available_frames[idx] - anchor)) if anchor is not None else len(available_frames) // 2
        requested_storyboard = [
            available_frames[0],
            available_frames[max(0, anchor_index - 1)],
            available_frames[anchor_index],
            available_frames[min(len(available_frames) - 1, anchor_index + 1)],
            available_frames[-1],
        ]
    storyboard_frames = list(dict.fromkeys(requested_storyboard))
    labels = {int(item.get("frame")): str(item.get("label") or "") for item in (payload.get("snapshot_items") or [])}
    return {
        "event_id": str((payload.get("meta") or {}).get("event_id") or (payload.get("selected_event") or {}).get("event_id") or ""),
        "frames": frames,
        "atom_groups": {
            "core": sorted(core_ids),
            "reactant": sorted(reactant_ids),
            "product": sorted(product_ids),
            "context": sorted(context_ids),
        },
        "storyboard_frames": storyboard_frames,
        "storyboard_labels": {str(frame): labels.get(frame) or f"Frame {frame}" for frame in storyboard_frames},
        "meta": payload.get("meta") or {},
        "paths": {
            "trajectory": payload.get("trajectory_saved_path") or "",
            "vmd": payload.get("vmd_script_saved_path") or "",
            "type_map": payload.get("type_map_saved_path") or "",
        },
    }


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
# Recrossing analysis
# ---------------------------------------------------------------------------


def analyze_event_recrossing(
    artifacts: dict[str, str],
    reaction_text: str,
    *,
    recrossing_threshold: int = 10,
    net_event_min_lifetime: int = 50,
) -> dict[str, Any]:
    """Run recrossing analysis on atom-level route transitions for a reaction.

    Queries the route transition index to get atom-level transitions,
    then runs :class:`~rng_tools.recrossing.RecrossingAnalyzer` to detect
    bond oscillations and produce deduplicated reaction events.
    """
    from rng_tools.recrossing import (
        RecrossingAnalyzer,
        convert_route_hits_to_transitions,
        dedup_events_to_rows,
    )
    from scripts.webapp.server import ROUTE_TRANSITION_INDEX_STORE, _prepare_reaction_query

    route_file = (artifacts.get("route") or "").strip()
    if not route_file or not os.path.isfile(route_file):
        raise ServiceError("需要 .route 文件才能进行去重分析", reason="missing_route")

    if not reaction_text.strip():
        raise ServiceError("请提供反应式", reason="missing_input")

    # Build a reaction query compatible with RouteTransitionIndexStore
    try:
        reaction_query = _prepare_reaction_query(reaction_text)
    except Exception as exc:
        raise ServiceError(f"无法解析反应式: {exc}", reason="parse_error")

    # Query the route index
    result = ROUTE_TRANSITION_INDEX_STORE.query_reaction_hits(
        route_file,
        reaction_query,
    )
    hits = result.get("hits", [])

    if not hits:
        raise ServiceError("在 route 文件中未找到该反应的原子转移记录", reason="no_hits")

    # Convert hits to AtomTransition objects
    transitions = convert_route_hits_to_transitions(hits)

    # Run recrossing analysis
    analyzer = RecrossingAnalyzer(
        recrossing_threshold_frames=int(recrossing_threshold),
        net_event_min_lifetime=int(net_event_min_lifetime),
    )

    # Build a reaction signature from the query
    reactant_str = " + ".join(sorted(reaction_query.get("reactant_token_set", [])))
    product_str = " + ".join(sorted(reaction_query.get("product_token_set", [])))
    reaction_signature = f"{reactant_str}->{product_str}"

    dedup_events, stats = analyzer.analyze(transitions, reaction_signature)

    rows = dedup_events_to_rows(dedup_events)

    return {
        "ok": True,
        "rows": rows,
        "events": [
            {
                "event_id": e.event_id,
                "reaction_signature": e.reaction_signature,
                "atom_ids": sorted(e.atom_ids),
                "atom_count": len(e.atom_ids),
                "start_frame": e.start_frame,
                "end_frame": e.end_frame,
                "lifetime": e.lifetime,
                "recrossing_count": e.recrossing_count,
                "is_net_event": e.is_net_event,
                "confidence": e.confidence,
            }
            for e in dedup_events
        ],
        "stats": stats,
        "meta": {
            "status": "ok",
            "message": (
                f"去重完成: {stats['total_deduplicated_events']} 个事件, "
                f"{stats['net_deduplicated_events']} 个净事件, "
                f"回穿率 {stats['recrossing_rate']}"
            ),
            "reaction_text": reaction_text,
            "reaction_signature": reaction_signature,
            "total_raw_transitions": len(transitions),
            "scanned_atoms": result.get("scanned_atoms", 0),
        },
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
    network = STORE.get(reac_path)

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
# Atom trajectory visualization
# ---------------------------------------------------------------------------


def build_atom_trajectory_visualization(
    artifacts: dict[str, str],
    event_id: str,
    reaction_text: str,
    *,
    atom_ids: list[int] | None = None,
    before_frames: int = 5,
    after_frames: int = 5,
) -> dict[str, Any]:
    """Build atom-level trajectory data for visualization.

    Extracts atom species timelines from the route file and trajectory
    data for the specified event.
    """
    from scripts.webapp.server import ROUTE_TRANSITION_INDEX_STORE, _prepare_reaction_query

    route_file = (artifacts.get("route") or "").strip()

    if not route_file or not os.path.isfile(route_file):
        raise ServiceError("需要 .route 文件", reason="missing_route")

    if not event_id.strip():
        raise ServiceError("请提供 event_id", reason="missing_event_id")

    # Parse event_id to extract anchor frame and atom IDs
    # event_id format: rxevt_{anchor_frame}_{hash}
    parts = event_id.split("_")
    if len(parts) < 2:
        raise ServiceError("无效的 event_id 格式", reason="bad_event_id")

    try:
        anchor_frame = int(parts[1])
    except (ValueError, IndexError):
        anchor_frame = 0

    # Query all transitions for the given atom_ids in the frame window
    start_window = max(0, anchor_frame - before_frames)
    end_window = anchor_frame + after_frames

    try:
        reaction_query = _prepare_reaction_query(reaction_text)
    except Exception:
        reaction_query = None

    # Query route hits
    if reaction_query:
        result = ROUTE_TRANSITION_INDEX_STORE.query_reaction_hits(
            route_file, reaction_query,
        )
        hits = result.get("hits", [])
    else:
        hits = []

    # Filter hits by atom_ids if specified
    if atom_ids:
        atom_set = set(atom_ids)
        hits = [h for h in hits if int(h.get("atom_id", 0)) in atom_set]

    # Filter by frame window
    window_hits = [
        h for h in hits
        if start_window <= int(h.get("start_frame", 0)) <= end_window
        or start_window <= int(h.get("end_frame", 0)) <= end_window
    ]

    # Build atom timeline
    atom_timelines: dict[int, list[dict[str, Any]]] = {}
    for h in window_hits:
        aid = int(h["atom_id"])
        if aid not in atom_timelines:
            atom_timelines[aid] = []
        atom_timelines[aid].append(
            {
                "frame": int(h["start_frame"]),
                "end_frame": int(h["end_frame"]),
                "from_label": str(h.get("from_label", "")),
                "to_label": str(h.get("to_label", "")),
                "direction": str(h.get("direction", "")),
            }
        )

    atoms = [
        {
            "atom_id": aid,
            "transitions": sorted(timeline, key=lambda x: x["frame"]),
            "n_transitions": len(timeline),
        }
        for aid, timeline in atom_timelines.items()
    ]

    return {
        "ok": True,
        "event_id": event_id,
        "anchor_frame": anchor_frame,
        "frames": sorted(set(
            int(h["start_frame"]) for h in window_hits
        ).union(int(h["end_frame"]) for h in window_hits)),
        "atoms": atoms,
        "n_atoms": len(atoms),
        "n_transitions": len(window_hits),
        "reaction_text": reaction_text,
        "meta": {
            "status": "ok",
            "message": f"提取了 {len(atoms)} 个原子的轨迹数据",
        },
    }


# ---------------------------------------------------------------------------
# Batch comparison
# ---------------------------------------------------------------------------


def scan_batch_conditions(
    root_dir: str,
) -> dict[str, Any]:
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
        raise ServiceError(f"未在 {root} 下找到包含 .reactionabcd 的子目录", reason="no_conditions")

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
    from rng_tools.batch_compare import BatchComparator, reaction_key_to_display

    if not condition_folders:
        raise ServiceError("请选择至少一个条件组", reason="no_conditions")

    if len(condition_folders) != len(condition_names):
        raise ServiceError("条件名称与目录数量不匹配", reason="mismatch")

    comparator = BatchComparator()

    for folder, name in zip(condition_folders, condition_names):
        reac_path = os.path.join(folder, f"{os.path.basename(folder)}.reactionabcd")
        if not os.path.isfile(reac_path):
            # Try to find any .reactionabcd file
            candidates = [
                f for f in os.listdir(folder)
                if f.endswith(".reactionabcd")
            ] if os.path.isdir(folder) else []
            if candidates:
                reac_path = os.path.join(folder, candidates[0])
            else:
                continue

        if not os.path.isfile(reac_path):
            continue

        try:
            reactions = parse_reactionabcd(reac_path, min_tp=1)
        except Exception:
            continue

        network = ReactionNetwork(reactions)
        comparator.add_condition(name, network)

    if not comparator._conditions:
        raise ServiceError("未能加载任何条件的反应网络", reason="no_networks")

    results = comparator.compare_all_common(
        min_detection_rate=float(min_detection_rate),
        top_n=int(top_n),
    )

    if not results:
        raise ServiceError("未找到符合条件的共同反应", reason="no_results")

    rows, cond_names = comparator.build_comparison_matrix(results)

    # Build columns definition for DataTable
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


# ---------------------------------------------------------------------------
# Public re-exports for tests
# ---------------------------------------------------------------------------


__all__ = [
    "ServiceError",
    "scan_dataset",
    "pick_folder_macos",
    "artifacts_from_status",
    "dataset_label",
    "dataset_ready_count",
    "dataset_capabilities",
    "candidates_from_status",
    "detect_query_kind",
    "search_species",
    "species_detail",
    "render_species_svg",
    "collect_transitions",
    "search_reactions_by_formula",
    "build_transition_table",
    "build_species_evolution",
    "evolution_to_csv",
    "build_carbon_evolution",
    "carbon_plot_to_csv",
    "build_intermediate_candidates",
    "locate_species_events",
    "locate_reaction_events",
    "extract_reaction_event",
    "extract_species_event",
    "build_event_visualization",
    "rows_to_csv",
    "build_observation_elements",
    "analyze_event_recrossing",
    "verify_literature_mechanism",
    "build_atom_trajectory_visualization",
    "scan_batch_conditions",
    "run_batch_comparison",
]
