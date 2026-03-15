#!/usr/bin/env python3
"""Lightweight web frontend backend for ReacNetGenerator query workflows.

No external web framework required.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import threading
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
TOOL_ROOT = SCRIPTS_DIR.parent
PROJECT_ROOT = TOOL_ROOT.parent

if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from rng_tools.network import (  # noqa: E402
    Reaction,
    ReactionNetwork,
    export_initiation_csv,
    export_initiation_smiles_branches_csv,
    parse_reactionabcd,
    smiles_to_formula_fast,
)
from rng_tools.formula import formula_exact_mass, formula_nominal_mass  # noqa: E402

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
            TOOL_ROOT / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
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


def parse_species_selected(species_file: str, selected_smiles: list[str]) -> tuple[list[int], dict[str, list[int]]]:
    ts_re = re.compile(r"^Timestep\s+(\d+):(.*)$")
    selected = list(dict.fromkeys(selected_smiles))
    selected_set = set(selected)
    series: dict[str, list[int]] = {s: [] for s in selected}
    timesteps: list[int] = []

    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
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


def build_species_timeline_summary(species_file: str) -> SpeciesTimelineSummary:
    first_ts: int | None = None
    last_ts: int | None = None
    prev_ts: int | None = None
    step: int | None = None
    n_timesteps = 0

    start_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}
    max_counts: dict[str, int] = {}
    max_timestep: dict[str, int] = {}

    # Pass 1: start/end/max
    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
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

    if first_ts is None or last_ts is None:
        raise RuntimeError(f"no valid timestep rows found in species file: {species_file}")
    if step is None or step <= 0:
        step = 1

    # Pass 2: longest FWHM run (count >= 0.5 * max)
    half_threshold = {smi: cmax * 0.5 for smi, cmax in max_counts.items() if cmax > 0}
    active_start: dict[str, int] = {}
    active_last: dict[str, int] = {}
    longest_points: dict[str, int] = {}

    with open(species_file, encoding="utf-8") as fh:
        for line in fh:
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

    for smi in list(active_start.keys()):
        _finalize_run_points(longest_points, smi, active_start[smi], active_last[smi], step)

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

    def get(self, species_file: str) -> SpeciesTimelineSummary:
        path = os.path.abspath(species_file)
        if not os.path.exists(path):
            raise FileNotFoundError(f"species file not found: {path}")
        mtime = os.path.getmtime(path)
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] == mtime:
                return cached[1]
            summary = build_species_timeline_summary(path)
            self._cache[path] = (mtime, summary)
            return summary


SPECIES_STORE = SpeciesSummaryStore()


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
        file_path = (base / rel).resolve()
        if not str(file_path).startswith(str(base.resolve())):
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
            self._send_json(
                {
                    "ok": True,
                    "query": {"formula": formula, "reac": reac, "min_tp": min_tp, "top": top},
                    "meta": {"rows": len(rows), **formula_mass_fields(formula)},
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
            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "mass": target_mass,
                        "target_nominal_mass": target_nominal,
                        "mode": mode,
                        "tol": tol,
                        "reac": reac,
                        "min_tp": min_tp,
                        "top": top,
                    },
                    "meta": {"rows": len(rows)},
                    "rows": rows,
                }
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_next(self, params: dict[str, list[str]]) -> None:
        smiles = (params.get("smiles", [""])[0] or "").strip()
        if not smiles:
            self._send_json({"ok": False, "error": "missing smiles"}, status=400)
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
            if smiles not in net.species:
                self._send_json({"ok": True, "meta": {"rows": 0}, "rows": []})
                return
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
                        "smiles": smiles,
                        "role": role,
                        "reac": reac,
                        "min_tp": min_tp,
                        "top": top,
                        "net_positive_only": net_positive_only,
                    },
                    "meta": {
                        "formula": sp.formula,
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
        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        min_tp = int_param(params, "min_tp", 1)
        species_file = (params.get("species_file", [""])[0] or "").strip()
        if not species_file:
            species_file = derive_species_path(reac)
        if not os.path.exists(species_file):
            self._send_json({"ok": False, "error": f"species file not found: {species_file}"}, status=404)
            return

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

        try:
            summary = SPECIES_STORE.get(species_file)
            dt_ps = summary.timestep_step * timestep_ps
            rows: list[dict[str, Any]] = []

            for smi, cmax in summary.max_counts.items():
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

            rows.sort(key=lambda x: (x["score"], x["c_max"]), reverse=True)
            if top > 0:
                rows = rows[:top]
            for i, row in enumerate(rows, 1):
                row["rank"] = i

            if with_flux and rows:
                net = STORE.get(reac, min_tp)
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

            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "reac": reac,
                        "min_tp": min_tp,
                        "species_file": species_file,
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
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _api_plot(self, params: dict[str, list[str]]) -> None:
        raw_target_params = params.get("target", [])
        raw_targets = split_target_items(raw_target_params)
        if not raw_targets:
            self._send_json({"ok": False, "error": "missing target"}, status=400)
            return

        reac = (params.get("reac", [str(DEFAULT_REACTION_FILE)])[0] or "").strip()
        min_tp = int_param(params, "min_tp", 1)
        species_file = (params.get("species_file", [""])[0] or "").strip()
        if not species_file:
            species_file = derive_species_path(reac)
        if not os.path.exists(species_file):
            self._send_json({"ok": False, "error": f"species file not found: {species_file}"}, status=404)
            return

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
        smooth_window = max(1, int_param(params, "smooth_window", 1))
        downsample = int_param(params, "downsample", 1800)

        targets = [parse_target_item(x) for x in raw_targets]

        try:
            net = STORE.get(reac, min_tp)
            series_defs, mapping_rows, warnings = resolve_plot_series(
                net,
                targets,
                formula_mode=formula_mode,
                max_smiles_per_formula=max_smiles_per_formula,
            )
            if not series_defs:
                self._send_json(
                    {
                        "ok": True,
                        "query": {
                            "reac": reac,
                            "species_file": species_file,
                            "targets": raw_targets,
                        },
                        "meta": {"rows": 0, "warnings": warnings},
                        "mapping": mapping_rows,
                        "x_name": "time_ps",
                        "x_values": [],
                        "curves": [],
                    }
                )
                return

            if max_curves > 0 and len(series_defs) > max_curves:
                warnings.append(f"too many curves ({len(series_defs)}), truncated to {max_curves}")
                series_defs = series_defs[:max_curves]

            selected_smiles: list[str] = []
            for d in series_defs:
                selected_smiles.extend(d["members"])
            selected_smiles = list(dict.fromkeys(selected_smiles))

            timesteps, base_series = parse_species_selected(species_file, selected_smiles)
            if not timesteps:
                self._send_json(
                    {
                        "ok": True,
                        "query": {
                            "reac": reac,
                            "species_file": species_file,
                            "targets": raw_targets,
                        },
                        "meta": {"rows": 0, "warnings": warnings + ["no timestep rows parsed"]},
                        "mapping": mapping_rows,
                        "x_name": "time_ps",
                        "x_values": [],
                        "curves": [],
                    }
                )
                return

            # x-axis
            if x_axis == "step":
                x_vals = [float(ts) for ts in timesteps]
                x_name = "timestep"
            elif x_axis == "ns":
                x_vals = [ts * timestep_ps / 1000.0 for ts in timesteps]
                x_name = "time_ns"
            else:
                x_vals = [ts * timestep_ps for ts in timesteps]
                x_name = "time_ps"

            curves: list[dict[str, Any]] = []
            y_map: dict[str, list[float]] = {}
            for d in series_defs:
                vals = [0.0] * len(timesteps)
                for smi in d["members"]:
                    arr = base_series.get(smi, [])
                    if len(arr) != len(vals):
                        continue
                    for i, v in enumerate(arr):
                        vals[i] += float(v)

                if normalize == "initial":
                    v0 = vals[0] if vals else 0.0
                    vals = [v / v0 if v0 else 0.0 for v in vals]
                elif normalize == "max":
                    vmax = max(vals) if vals else 0.0
                    vals = [v / vmax if vmax else 0.0 for v in vals]

                vals = moving_average(vals, smooth_window)
                y_map[d["series_name"]] = vals
                curves.append(
                    {
                        "name": d["series_name"],
                        "query_type": d["query_type"],
                        "query": d["query"],
                        "formula": d["formula"],
                        "formula_exact_mass": d.get("formula_exact_mass"),
                        "formula_nominal_mass": d.get("formula_nominal_mass"),
                        "n_members": len(d["members"]),
                        "members": d["members"],
                        "values": vals,
                        "max_value": max(vals) if vals else 0.0,
                    }
                )

            # downsample for web payload/perf
            if downsample > 0:
                x_vals_ds, y_map_ds = downsample_series(x_vals, y_map, downsample)
                for c in curves:
                    c["values"] = y_map_ds.get(c["name"], [])
                x_vals = x_vals_ds

            self._send_json(
                {
                    "ok": True,
                    "query": {
                        "reac": reac,
                        "min_tp": min_tp,
                        "species_file": species_file,
                        "targets": raw_targets,
                        "formula_mode": formula_mode,
                        "max_smiles_per_formula": max_smiles_per_formula,
                        "x_axis": x_axis,
                        "timestep_ps": timestep_ps,
                        "normalize": normalize,
                        "smooth_window": smooth_window,
                        "downsample": downsample,
                    },
                    "meta": {
                        "n_timestep_full": len(timesteps),
                        "n_points_returned": len(x_vals),
                        "n_curves": len(curves),
                        "warnings": warnings,
                    },
                    "mapping": mapping_rows,
                    "x_name": x_name,
                    "x_values": x_vals,
                    "curves": curves,
                }
            )
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

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
            self._send_text(f"svg render failed: {e}", status=400)

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
            self._send_json({"ok": True, "status": "alive"})
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
        if path == "/api/plot":
            self._api_plot(params)
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
