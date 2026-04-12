#!/usr/bin/env python3
"""Unified terminal query tool for ReacNetGenerator reaction datasets.

Common use cases:
1) Query all SMILES for a molecular formula
2) Query next-step reactions for a given SMILES (consumption/production)
3) Query reaction channels by formula equation (e.g. C6H4O2+C6H4->C12H8O2)
4) Compute TOP-N share from a CSV metric column
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = TOOL_ROOT.parent

if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from rng_tools.network import (  # noqa: E402
    Reaction,
    ReactionNetwork,
    parse_reactionabcd,
    smiles_to_formula_fast,
)
from rng_tools.carbon_plot import (  # noqa: E402
    parse_carbon_range_specs,
    plot_carbon_number_evolution,
    species_file_to_tidy_table,
)


def detect_default_reaction_file() -> Path:
    env_path = os.getenv("RNG_REACTION_FILE", "").strip()
    candidates: List[Path] = []
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

SEP_RE = re.compile(r"\s*\+\s*|\s*,\s*|\s*;\s*")


@dataclass
class MatchedReaction:
    role: str
    reaction: Reaction
    forward_tp: int
    reverse_tp: int
    net_tp: int
    ratio_pct: float


def split_terms(expr: str) -> List[str]:
    terms = [x.strip() for x in SEP_RE.split(expr.strip()) if x.strip()]
    return terms


def multiset_contains(have: Counter, need: Counter) -> bool:
    for key, val in need.items():
        if have.get(key, 0) < val:
            return False
    return True


def reaction_formula_str(rxn: Reaction) -> str:
    return " + ".join(rxn.reactant_formulas) + " -> " + " + ".join(rxn.product_formulas)


def reaction_smiles_str(rxn: Reaction) -> str:
    return " + ".join(rxn.reactant_smiles) + " -> " + " + ".join(rxn.product_smiles)


def build_network(reac_file: str, min_tp: int) -> ReactionNetwork:
    if not os.path.exists(reac_file):
        raise FileNotFoundError(f"reactionabcd not found: {reac_file}")
    reactions = parse_reactionabcd(reac_file, min_tp=min_tp)
    if not reactions:
        raise RuntimeError(f"No reactions loaded from: {reac_file}")
    return ReactionNetwork(reactions)


def reverse_key(rxn: Reaction) -> str:
    return "+".join(sorted(rxn.product_smiles)) + "->" + "+".join(sorted(rxn.reactant_smiles))


def net_flux(rxn: Reaction, tp_map: dict[str, int]) -> tuple[int, int, int]:
    fwd = rxn.tp
    rev = tp_map.get(reverse_key(rxn), 0)
    return fwd, rev, fwd - rev


def write_csv(path: str, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def cmd_species(args: argparse.Namespace) -> int:
    net = build_network(args.reac, args.min_tp)
    smiles_set = net.smiles_by_formula(args.formula)

    if not smiles_set:
        print(f"[INFO] No SMILES found for formula {args.formula}")
        return 0

    rows = []
    for smi in smiles_set:
        sp = net.species[smi]
        rows.append(
            {
                "smiles": smi,
                "formula": sp.formula,
                "tp_total": sp.total_throughput,
                "tp_consume": sp.tp_as_reactant,
                "tp_produce": sp.tp_as_product,
                "net_production": sp.net_production,
                "n_consume_rxns": sp.n_consume_rxns,
                "n_produce_rxns": sp.n_produce_rxns,
            }
        )
    rows.sort(key=lambda x: x["tp_total"], reverse=True)

    top = rows[: args.top] if args.top > 0 else rows
    total_tp = sum(x["tp_total"] for x in rows)

    print(f"# formula={args.formula}  n_smiles={len(rows)}  total_tp={total_tp}")
    print(
        "rank,tp_total,tp_consume,tp_produce,net_production,"
        "n_consume_rxns,n_produce_rxns,smiles"
    )
    for i, row in enumerate(top, 1):
        print(
            f"{i},{row['tp_total']},{row['tp_consume']},{row['tp_produce']},"
            f"{row['net_production']},{row['n_consume_rxns']},{row['n_produce_rxns']},"
            f"{row['smiles']}"
        )

    if args.out:
        out_rows = []
        for i, row in enumerate(top, 1):
            d = dict(row)
            d["rank"] = i
            d["share_in_formula_tp_pct"] = round(
                row["tp_total"] / total_tp * 100.0 if total_tp else 0.0, 3
            )
            out_rows.append(d)
        fieldnames = [
            "rank",
            "smiles",
            "formula",
            "tp_total",
            "tp_consume",
            "tp_produce",
            "net_production",
            "share_in_formula_tp_pct",
            "n_consume_rxns",
            "n_produce_rxns",
        ]
        write_csv(args.out, fieldnames, out_rows)
        print(f"[OK] wrote: {args.out}")
    return 0


def collect_next_reactions(net: ReactionNetwork, smi: str, role: str) -> List[MatchedReaction]:
    tp_map = {r.key: r.tp for r in net.reactions}
    rows: List[MatchedReaction] = []

    if role in {"consume", "both"}:
        total = net.total_consume_tp(smi)
        for rxn in net.consumption_of(smi):
            fwd, rev, net_tp = net_flux(rxn, tp_map)
            ratio = (rxn.tp / total * 100.0) if total else 0.0
            rows.append(MatchedReaction("consume", rxn, fwd, rev, net_tp, ratio))

    if role in {"produce", "both"}:
        total = net.total_produce_tp(smi)
        for rxn in net.production_of(smi):
            fwd, rev, net_tp = net_flux(rxn, tp_map)
            ratio = (rxn.tp / total * 100.0) if total else 0.0
            rows.append(MatchedReaction("produce", rxn, fwd, rev, net_tp, ratio))

    rows.sort(key=lambda x: (abs(x.net_tp), x.forward_tp), reverse=True)
    return rows


def cmd_next(args: argparse.Namespace) -> int:
    net = build_network(args.reac, args.min_tp)
    smi = args.smiles
    if smi not in net.species:
        print(f"[INFO] SMILES not found in reaction network: {smi}")
        return 0

    matched = collect_next_reactions(net, smi, args.role)
    if args.net_positive_only:
        matched = [x for x in matched if x.net_tp > 0]
    top = matched[: args.top] if args.top > 0 else matched

    sp = net.species[smi]
    print(
        f"# smiles={smi}\n"
        f"# formula={sp.formula}, tp_consume={sp.tp_as_reactant}, tp_produce={sp.tp_as_product}, "
        f"net={sp.net_production}\n"
        f"# matched={len(matched)} (show={len(top)})"
    )
    print(
        "rank,role,tp,reverse_tp,net_tp,ratio_pct,reaction_formulas,reaction_smiles"
    )
    out_rows = []
    for i, m in enumerate(top, 1):
        row = {
            "rank": i,
            "role": m.role,
            "tp": m.forward_tp,
            "reverse_tp": m.reverse_tp,
            "net_tp": m.net_tp,
            "ratio_pct": round(m.ratio_pct, 3),
            "reaction_formulas": reaction_formula_str(m.reaction),
            "reaction_smiles": reaction_smiles_str(m.reaction),
        }
        out_rows.append(row)
        print(
            f"{row['rank']},{row['role']},{row['tp']},{row['reverse_tp']},"
            f"{row['net_tp']},{row['ratio_pct']},{row['reaction_formulas']},"
            f"{row['reaction_smiles']}"
        )

    if args.out:
        write_csv(
            args.out,
            [
                "rank",
                "role",
                "tp",
                "reverse_tp",
                "net_tp",
                "ratio_pct",
                "reaction_formulas",
                "reaction_smiles",
            ],
            out_rows,
        )
        print(f"[OK] wrote: {args.out}")
    return 0


def match_formula_reaction(
    rxn: Reaction,
    need_r: Counter,
    need_p: Counter,
    mode: str,
) -> bool:
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


def cmd_rxn_formula(args: argparse.Namespace) -> int:
    reactants = split_terms(args.reactants)
    products = split_terms(args.products)
    if not reactants and not products:
        print("[ERROR] provide reactants and/or products.")
        return 2

    net = build_network(args.reac, args.min_tp)
    tp_map = {r.key: r.tp for r in net.reactions}
    need_r = Counter(reactants)
    need_p = Counter(products)

    matched = []
    for rxn in net.reactions:
        if not match_formula_reaction(rxn, need_r, need_p, args.mode):
            continue
        fwd, rev, net_tp = net_flux(rxn, tp_map)
        matched.append(
            {
                "tp": fwd,
                "reverse_tp": rev,
                "net_tp": net_tp,
                "reactant_formulas": " + ".join(rxn.reactant_formulas),
                "product_formulas": " + ".join(rxn.product_formulas),
                "reaction_formulas": reaction_formula_str(rxn),
                "reaction_smiles": reaction_smiles_str(rxn),
            }
        )

    matched.sort(key=lambda x: (x["tp"], abs(x["net_tp"])), reverse=True)
    top = matched[: args.top] if args.top > 0 else matched

    lhs = " + ".join(reactants) if reactants else "*"
    rhs = " + ".join(products) if products else "*"
    q = f"{lhs} -> {rhs}"
    print(f"# query={q}, mode={args.mode}, matches={len(matched)} (show={len(top)})")
    print("rank,tp,reverse_tp,net_tp,reaction_formulas,reaction_smiles")
    for i, row in enumerate(top, 1):
        print(
            f"{i},{row['tp']},{row['reverse_tp']},{row['net_tp']},"
            f"{row['reaction_formulas']},{row['reaction_smiles']}"
        )

    if args.out:
        out_rows = []
        for i, row in enumerate(top, 1):
            d = dict(row)
            d["rank"] = i
            out_rows.append(d)
        write_csv(
            args.out,
            [
                "rank",
                "tp",
                "reverse_tp",
                "net_tp",
                "reactant_formulas",
                "product_formulas",
                "reaction_formulas",
                "reaction_smiles",
            ],
            out_rows,
        )
        print(f"[OK] wrote: {args.out}")
    return 0


def parse_metric(value: str) -> float | None:
    s = value.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cmd_topshare(args: argparse.Namespace) -> int:
    if not os.path.exists(args.csv):
        print(f"[ERROR] file not found: {args.csv}")
        return 2

    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or args.metric not in reader.fieldnames:
            print(f"[ERROR] metric column not found: {args.metric}")
            print(f"       available: {', '.join(reader.fieldnames or [])}")
            return 2
        rows = []
        for row in reader:
            v = parse_metric(str(row.get(args.metric, "")))
            if v is None:
                continue
            if args.abs_metric:
                v = abs(v)
            if args.positive_only and v <= 0:
                continue
            rows.append((v, row))

    if not rows:
        print("[INFO] no valid rows after filtering.")
        return 0

    rows.sort(key=lambda x: x[0], reverse=True)
    top = rows[: args.top] if args.top > 0 else rows

    total = sum(v for v, _ in rows)
    top_sum = sum(v for v, _ in top)
    share = top_sum / total * 100.0 if total else 0.0

    print(
        f"# metric={args.metric}, rows={len(rows)}, top={len(top)}, "
        f"top_sum={top_sum:.6g}, total={total:.6g}, share={share:.3f}%"
    )
    print(f"rank,metric_value,share_pct,cumulative_pct")
    cum = 0.0
    out_rows = []
    for i, (v, row) in enumerate(top, 1):
        pct = v / total * 100.0 if total else 0.0
        cum += pct
        data = {
            "rank": i,
            "metric_value": v,
            "share_pct": round(pct, 3),
            "cumulative_pct": round(cum, 3),
        }
        out_rows.append(data)
        print(f"{i},{v:.6g},{data['share_pct']},{data['cumulative_pct']}")

    if args.out:
        write_csv(args.out, ["rank", "metric_value", "share_pct", "cumulative_pct"], out_rows)
        print(f"[OK] wrote: {args.out}")
    return 0


_FORMULA_RE = re.compile(r"^([A-Z][a-z]?\d*)+$")


def looks_like_formula(text: str) -> bool:
    return bool(_FORMULA_RE.fullmatch(text.strip()))


def split_target_args(raw_items: Sequence[str]) -> List[str]:
    out: List[str] = []
    for raw in raw_items:
        parts = [x.strip() for x in re.split(r"\s*,\s*|\s*;\s*", raw.strip()) if x.strip()]
        out.extend(parts)
    return out


def parse_target_item(item: str) -> tuple[str, str, str]:
    """Return (query_type, query, label). query_type in {'formula','smiles'}."""
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


def moving_average(vals: List[float], window: int) -> List[float]:
    if window <= 1 or len(vals) <= 2:
        return list(vals)
    out: List[float] = []
    half = window // 2
    for i in range(len(vals)):
        lo = max(0, i - half)
        hi = min(len(vals), i + half + 1)
        seg = vals[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def resolve_plot_series(
    net: ReactionNetwork,
    targets: Sequence[tuple[str, str, str]],
    *,
    formula_mode: str,
    max_smiles_per_formula: int,
) -> tuple[List[dict], List[dict]]:
    """Build plot series definitions and mapping rows."""
    series_defs: List[dict] = []
    mapping_rows: List[dict] = []

    for qtype, query, label in targets:
        if qtype == "smiles":
            if query not in net.species:
                print(f"[WARN] SMILES not in reaction network, skipped: {query}")
                continue
            sp = net.species[query]
            series_defs.append(
                {
                    "series_name": label,
                    "query_type": "smiles",
                    "query": query,
                    "formula": sp.formula,
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
                    "tp_total": sp.total_throughput,
                }
            )
            continue

        # formula
        smiles_list = list(net.smiles_by_formula(query))
        if not smiles_list:
            print(f"[WARN] Formula has no SMILES in network, skipped: {query}")
            continue
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
                    "members": list(smiles_list),
                }
            )

        if formula_mode in {"split", "both"}:
            for i, smi in enumerate(smiles_list, 1):
                sp = net.species.get(smi)
                series_defs.append(
                    {
                        "series_name": f"{label}[{i}]",
                        "query_type": "formula_member",
                        "query": query,
                        "formula": query,
                        "members": [smi],
                    }
                )
                mapping_rows.append(
                    {
                        "series_name": f"{label}[{i}]",
                        "query_type": "formula_member",
                        "query": query,
                        "formula": query,
                        "smiles": smi,
                        "tp_total": sp.total_throughput if sp else 0,
                    }
                )

        if formula_mode in {"sum", "both"}:
            for smi in smiles_list:
                sp = net.species.get(smi)
                mapping_rows.append(
                    {
                        "series_name": label,
                        "query_type": "formula_sum",
                        "query": query,
                        "formula": query,
                        "smiles": smi,
                        "tp_total": sp.total_throughput if sp else 0,
                    }
                )

    return series_defs, mapping_rows


def parse_species_selected(
    species_file: str,
    selected_smiles: Sequence[str],
) -> tuple[List[int], dict[str, List[int]]]:
    ts_re = re.compile(r"^Timestep\s+(\d+):(.*)$")
    selected = list(dict.fromkeys(selected_smiles))
    selected_set = set(selected)
    series: dict[str, List[int]] = {s: [] for s in selected}
    timesteps: List[int] = []

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


def cmd_plot(args: argparse.Namespace) -> int:
    species_file = args.species_file or derive_species_path(args.reac)
    if not os.path.exists(species_file):
        print(f"[ERROR] species file not found: {species_file}")
        return 2

    if not args.target:
        print("[ERROR] --target is required (support multiple).")
        return 2

    net = build_network(args.reac, args.min_tp)

    raw_items = split_target_args(args.target)
    targets = [parse_target_item(x) for x in raw_items]

    series_defs, mapping_rows = resolve_plot_series(
        net,
        targets,
        formula_mode=args.formula_mode,
        max_smiles_per_formula=args.max_smiles_per_formula,
    )
    if not series_defs:
        print("[INFO] no valid targets after resolving formulas/smiles.")
        return 0

    selected_smiles: List[str] = []
    for d in series_defs:
        selected_smiles.extend(d["members"])
    selected_smiles = list(dict.fromkeys(selected_smiles))

    timesteps, base_series = parse_species_selected(species_file, selected_smiles)
    if not timesteps:
        print(f"[INFO] no timestep rows parsed from: {species_file}")
        return 0

    if args.list_only:
        print("# target mapping")
        print("series_name,query_type,query,formula,smiles,tp_total")
        for row in mapping_rows:
            print(
                f"{row['series_name']},{row['query_type']},{row['query']},"
                f"{row['formula']},{row['smiles']},{row['tp_total']}"
            )
        if args.out_map:
            write_csv(
                args.out_map,
                ["series_name", "query_type", "query", "formula", "smiles", "tp_total"],
                mapping_rows,
            )
            print(f"[OK] wrote: {args.out_map}")
        return 0

    # build curve data
    curves: List[dict] = []
    for d in series_defs:
        vals = [0.0] * len(timesteps)
        for smi in d["members"]:
            sv = base_series.get(smi, [])
            if len(sv) != len(vals):
                continue
            for i, v in enumerate(sv):
                vals[i] += float(v)

        if args.normalize == "initial":
            v0 = vals[0] if vals else 0.0
            vals = [v / v0 if v0 else 0.0 for v in vals]
        elif args.normalize == "max":
            vmax = max(vals) if vals else 0.0
            vals = [v / vmax if vmax else 0.0 for v in vals]

        vals = moving_average(vals, args.smooth_window)
        curves.append(
            {
                "series_name": d["series_name"],
                "query_type": d["query_type"],
                "query": d["query"],
                "formula": d["formula"],
                "n_members": len(d["members"]),
                "members": d["members"],
                "values": vals,
            }
        )

    # x-axis
    if args.x_axis == "step":
        x_vals = [float(ts) for ts in timesteps]
        x_name = "timestep"
    elif args.x_axis == "ns":
        x_vals = [ts * args.timestep_ps / 1000.0 for ts in timesteps]
        x_name = "time_ns"
    else:
        x_vals = [ts * args.timestep_ps for ts in timesteps]
        x_name = "time_ps"

    # stdout summary
    print(
        f"# species_file={species_file}\n"
        f"# timesteps={len(timesteps)}, x_axis={args.x_axis}, curves={len(curves)}\n"
        f"# normalize={args.normalize}, smooth_window={args.smooth_window}"
    )
    for i, c in enumerate(curves, 1):
        vmax = max(c["values"]) if c["values"] else 0.0
        print(
            f"  {i:>2}. {c['series_name']}  ({c['query_type']})  "
            f"members={c['n_members']}  max={vmax:.6g}"
        )

    # write mapping
    if args.out_map:
        write_csv(
            args.out_map,
            ["series_name", "query_type", "query", "formula", "smiles", "tp_total"],
            mapping_rows,
        )
        print(f"[OK] wrote: {args.out_map}")

    # write curve csv
    if args.out_csv:
        rows = []
        for i in range(len(x_vals)):
            row = {x_name: x_vals[i], "timestep": timesteps[i]}
            for c in curves:
                row[c["series_name"]] = c["values"][i]
            rows.append(row)
        fieldnames = [x_name, "timestep"] + [c["series_name"] for c in curves]
        write_csv(args.out_csv, fieldnames, rows)
        print(f"[OK] wrote: {args.out_csv}")

    # draw plot
    if args.out_png:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(args.fig_width, args.fig_height), dpi=args.dpi)
        for c in curves:
            ax.plot(x_vals, c["values"], linewidth=1.8, label=c["series_name"])
        ax.set_xlabel(x_name)
        y_label = "count"
        if args.normalize in {"initial", "max"}:
            y_label = "normalized_count"
        ax.set_ylabel(y_label)
        title = args.title or "Species Time Series"
        ax.set_title(title)
        if not args.no_grid:
            ax.grid(True, alpha=0.25)
        ax.legend(loc="best", fontsize=9)
        fig.tight_layout()
        os.makedirs(os.path.dirname(args.out_png) or ".", exist_ok=True)
        fig.savefig(args.out_png)
        plt.close(fig)
        print(f"[OK] wrote: {args.out_png}")

    return 0


def cmd_carbon_plot(args: argparse.Namespace) -> int:
    data_path = (args.data or "").strip()
    species_file = (args.species_file or "").strip()
    if not data_path:
        species_file = species_file or derive_species_path(args.reac)
        if not os.path.exists(species_file):
            print(f"[ERROR] species file not found: {species_file}")
            return 2

    carbon_bins = parse_carbon_range_specs(args.carbon_bins) or None
    display_ranges = parse_carbon_range_specs(args.display_ranges) or None
    merge_ranges = parse_carbon_range_specs(args.merge_ranges) or None
    layout_regions = parse_carbon_range_specs(args.layout_regions) or None

    smoothing = None
    if args.smoothing == "rolling":
        smoothing = {"method": "rolling", "window": args.smooth_window}
    elif args.smoothing == "savgol":
        smoothing = {
            "method": "savgol",
            "window_length": args.smooth_window,
            "polyorder": args.smooth_polyorder,
        }

    system_col = args.system_col.strip() or None
    replicate_col = args.replicate_col.strip() or None
    parent_carbon_number = args.parent_carbon_number if args.parent_carbon_number > 0 else None
    system_mode = args.system_mode.strip() or None
    highlight_small = tuple(args.highlight_small)

    if data_path:
        source = data_path
        source_desc = data_path
    else:
        source = species_file_to_tidy_table(
            species_file=species_file,
            time_axis=args.x_axis,
            timestep_ps=args.timestep_ps,
            species_resolver=smiles_to_formula_fast,
            system=args.system_name.strip() or None,
            replicate=args.replicate_id.strip() or None,
        )
        if args.system_name.strip() and system_col is None:
            system_col = "system"
        if args.replicate_id.strip() and replicate_col is None:
            replicate_col = "replicate"
        source_desc = species_file

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, _, summary, plot_data = plot_carbon_number_evolution(
        data=source,
        time_col=args.time_col,
        species_col=args.species_col,
        count_col=args.count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        carbon_bins=carbon_bins,
        display_ranges=display_ranges,
        merge_ranges=merge_ranges,
        mode=args.mode,
        top_k=args.top_k,
        max_exact_lines=args.max_exact_lines,
        parent_carbon_number=parent_carbon_number,
        highlight_small=highlight_small,
        highlight_large=args.highlight_large,
        smoothing=smoothing,
        layout=args.layout,
        layout_regions=layout_regions,
        system_mode=system_mode,
        legend_mode=args.legend_mode,
        palette=args.palette,
        theme=args.theme,
        figsize=(args.fig_width, args.fig_height),
        return_summary=True,
        show_uncertainty=not args.no_uncertainty,
        output_path=None,
    )

    if args.out_csv:
        os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
        plot_data.to_csv(args.out_csv, index=False)
        print(f"[OK] wrote: {args.out_csv}")

    if args.out_summary:
        os.makedirs(os.path.dirname(args.out_summary) or ".", exist_ok=True)
        with open(args.out_summary, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print(f"[OK] wrote: {args.out_summary}")

    if args.out_fig:
        os.makedirs(os.path.dirname(args.out_fig) or ".", exist_ok=True)
        fig.savefig(args.out_fig, bbox_inches="tight", dpi=args.dpi)
        print(f"[OK] wrote: {args.out_fig}")

    print(f"# source={source_desc}")
    print(f"# plot_rows={len(plot_data)}, plot_mode={summary.get('plot_mode', args.mode)}")
    if "group_by" in summary:
        systems = ", ".join(sorted(summary.get("by_system", {}).keys()))
        print(f"# group_by={summary['group_by']}, systems={systems}")
        overall = summary.get("overall", {})
        print(
            f"# overall parent=C{overall.get('parent_carbon_number')} "
            f"peak={overall.get('parent_peak_count', 0):.6g} "
            f"final={overall.get('parent_final_count', 0):.6g} "
            f"decay_onset={overall.get('parent_decay_onset_time')}"
        )
    else:
        print(
            f"# parent=C{summary.get('parent_carbon_number')} "
            f"peak={summary.get('parent_peak_count', 0):.6g} "
            f"final={summary.get('parent_final_count', 0):.6g} "
            f"decay_onset={summary.get('parent_decay_onset_time')}"
        )
        print(
            f"# small_peak={summary.get('small_fragment_peak_time')} "
            f"large_peak={summary.get('large_hydrocarbon_peak_time')} "
            f"max_carbon={summary.get('max_carbon_number_observed')}"
        )

    plt.close(fig)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="ReacNetGenerator 常用检索终端工具 (formula/smiles/path/topshare/plot/carbon-plot)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_reac_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--reac",
            default=str(DEFAULT_REACTION_FILE),
            help=f"reactionabcd 文件路径 (default: {DEFAULT_REACTION_FILE})",
        )
        sp.add_argument("--min-tp", type=int, default=1, help="最低 tp 过滤阈值")

    sp_species = sub.add_parser("species", help="按分子式列出所有 SMILES")
    add_reac_flags(sp_species)
    sp_species.add_argument("--formula", required=True, help="目标分子式, 例如 C6H4")
    sp_species.add_argument("--top", type=int, default=50, help="输出前 N 条, <=0 表示全部")
    sp_species.add_argument("--out", default="", help="可选输出 CSV 路径")
    sp_species.set_defaults(func=cmd_species)

    sp_next = sub.add_parser("next", help="查询某个 SMILES 的下一步(消耗/生成)反应")
    add_reac_flags(sp_next)
    sp_next.add_argument("--smiles", required=True, help="目标 SMILES")
    sp_next.add_argument(
        "--role",
        choices=["consume", "produce", "both"],
        default="consume",
        help="检索角色",
    )
    sp_next.add_argument(
        "--net-positive-only",
        action="store_true",
        help="仅保留 net_tp > 0 的通道",
    )
    sp_next.add_argument("--top", type=int, default=30, help="输出前 N 条, <=0 表示全部")
    sp_next.add_argument("--out", default="", help="可选输出 CSV 路径")
    sp_next.set_defaults(func=cmd_next)

    sp_rf = sub.add_parser("rxn-formula", help="按公式级反应检索路径")
    add_reac_flags(sp_rf)
    sp_rf.add_argument(
        "--reactants",
        required=False,
        default="",
        help="可选: 反应物分子式列表, 用 + 或 , 分隔, 如 C6H4O2+C6H4",
    )
    sp_rf.add_argument(
        "--products",
        required=False,
        default="",
        help="可选: 产物分子式列表, 用 + 或 , 分隔, 如 C12H8O2",
    )
    sp_rf.add_argument(
        "--mode",
        choices=["exact", "contains"],
        default="exact",
        help="exact: 已提供的一侧需完全一致(缺省侧不限制); contains: 已提供的一侧只要求包含",
    )
    sp_rf.add_argument("--top", type=int, default=50, help="输出前 N 条, <=0 表示全部")
    sp_rf.add_argument("--out", default="", help="可选输出 CSV 路径")
    sp_rf.set_defaults(func=cmd_rxn_formula)

    sp_ts = sub.add_parser("topshare", help="计算 CSV 指标列的 TOP-N 占比")
    sp_ts.add_argument("--csv", required=True, help="输入 CSV 路径")
    sp_ts.add_argument("--metric", required=True, help="指标列名 (数值列)")
    sp_ts.add_argument("--top", type=int, default=10, help="TOP N")
    sp_ts.add_argument("--positive-only", action="store_true", help="仅统计 metric > 0")
    sp_ts.add_argument("--abs-metric", action="store_true", help="对 metric 取绝对值后统计")
    sp_ts.add_argument("--out", default="", help="可选输出 CSV 路径")
    sp_ts.set_defaults(func=cmd_topshare)

    sp_plot = sub.add_parser(
        "plot",
        help="绘制物种随时间变化曲线 (支持 formula/SMILES 混合输入与多曲线)",
    )
    add_reac_flags(sp_plot)
    sp_plot.add_argument(
        "--species-file",
        default="",
        help="species 文件路径, 默认由 --reac 自动推导(.reactionabcd -> .species)",
    )
    sp_plot.add_argument(
        "--target",
        action="append",
        default=[],
        help=(
            "目标物种, 可重复传入或用逗号分隔; 支持 formula/SMILES 混输, "
            "支持 label::query, 支持前缀 formula:/f: 与 smiles:/smi:"
        ),
    )
    sp_plot.add_argument(
        "--formula-mode",
        choices=["sum", "split", "both"],
        default="sum",
        help="formula 输入如何展开: sum(聚合), split(拆分每个SMILES), both",
    )
    sp_plot.add_argument(
        "--max-smiles-per-formula",
        type=int,
        default=0,
        help="限制每个 formula 展开的 SMILES 数, 0 表示不限制",
    )
    sp_plot.add_argument(
        "--x-axis",
        choices=["step", "ps", "ns"],
        default="ps",
        help="x 轴单位",
    )
    sp_plot.add_argument(
        "--timestep-ps",
        type=float,
        default=0.0001,
        help="LAMMPS 单步时间(ps), 用于 step->ps/ns 转换",
    )
    sp_plot.add_argument(
        "--normalize",
        choices=["none", "initial", "max"],
        default="none",
        help="曲线归一化方式",
    )
    sp_plot.add_argument(
        "--smooth-window",
        type=int,
        default=1,
        help="平滑窗口(移动平均), 1 表示不平滑",
    )
    sp_plot.add_argument("--title", default="", help="图标题")
    sp_plot.add_argument("--fig-width", type=float, default=10.5, help="图宽(英寸)")
    sp_plot.add_argument("--fig-height", type=float, default=6.0, help="图高(英寸)")
    sp_plot.add_argument("--dpi", type=int, default=180, help="输出 PNG 分辨率")
    sp_plot.add_argument("--no-grid", action="store_true", help="关闭网格")
    sp_plot.add_argument("--out-png", default="", help="输出 PNG 路径")
    sp_plot.add_argument("--out-csv", default="", help="输出曲线 CSV 路径(宽表)")
    sp_plot.add_argument("--out-map", default="", help="输出目标展开映射 CSV 路径")
    sp_plot.add_argument(
        "--list-only",
        action="store_true",
        help="仅输出目标展开映射(公式->SMILES), 不绘图",
    )
    sp_plot.set_defaults(func=cmd_plot)

    sp_carbon = sub.add_parser(
        "carbon-plot",
        help="按碳数聚合绘制分子数量时间演化图",
    )
    add_reac_flags(sp_carbon)
    sp_carbon.add_argument("--data", default="", help="tidy CSV/Excel 路径, 提供后优先于 --species-file")
    sp_carbon.add_argument(
        "--species-file",
        default="",
        help="RNG .species 文件路径, 默认由 --reac 自动推导",
    )
    sp_carbon.add_argument("--time-col", default="time", help="tidy 表时间列名")
    sp_carbon.add_argument("--species-col", default="species", help="tidy 表物种列名")
    sp_carbon.add_argument("--count-col", default="count", help="tidy 表计数列名")
    sp_carbon.add_argument("--system-col", default="", help="tidy 表体系列名")
    sp_carbon.add_argument("--replicate-col", default="", help="tidy 表重复列名")
    sp_carbon.add_argument("--system-name", default="", help="从 .species 读取时附加的 system 常量值")
    sp_carbon.add_argument("--replicate-id", default="", help="从 .species 读取时附加的 replicate 常量值")
    sp_carbon.add_argument(
        "--x-axis",
        choices=["step", "ps", "ns"],
        default="ps",
        help=".species 输入时的时间轴单位",
    )
    sp_carbon.add_argument(
        "--timestep-ps",
        type=float,
        default=0.0001,
        help="LAMMPS 单步时间(ps), 用于 .species 的 step->ps/ns 转换",
    )
    sp_carbon.add_argument("--mode", choices=["exact", "binned", "topk"], default="exact", help="绘图模式")
    sp_carbon.add_argument("--top-k", type=int, default=12, help="topk 模式保留的碳数数量")
    sp_carbon.add_argument("--max-exact-lines", type=int, default=24, help="exact 模式自动切换阈值")
    sp_carbon.add_argument(
        "--carbon-bins",
        default="",
        help="分箱定义, 如 '1-4;5-15;16-30;31+' 或 'Small:1-4;Growth:31+'",
    )
    sp_carbon.add_argument(
        "--display-ranges",
        default="",
        help="仅显示的碳数/区间, 如 'C1;C2;C24;C30+'",
    )
    sp_carbon.add_argument(
        "--merge-ranges",
        default="",
        help="合并成单曲线的碳数区间, 如 'Small:1-4;Parent:24;Growth:30+'",
    )
    sp_carbon.add_argument("--layout", choices=["single", "subplots"], default="single", help="布局")
    sp_carbon.add_argument(
        "--layout-regions",
        default="",
        help="子图区间定义, 格式同 --carbon-bins",
    )
    sp_carbon.add_argument(
        "--system-mode",
        choices=["facet", "overlay"],
        default="",
        help="多体系时使用 facet 或 overlay",
    )
    sp_carbon.add_argument("--parent-carbon-number", type=int, default=0, help="母体碳数, 0 表示自动推断")
    sp_carbon.add_argument(
        "--highlight-small",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        default=[1, 4],
        help="小碎片高亮区间, 例如 1 4",
    )
    sp_carbon.add_argument("--highlight-large", type=int, default=30, help="大分子增长阈值")
    sp_carbon.add_argument(
        "--smoothing",
        choices=["none", "rolling", "savgol"],
        default="none",
        help="平滑方式",
    )
    sp_carbon.add_argument("--smooth-window", type=int, default=5, help="rolling/savgol 窗口")
    sp_carbon.add_argument("--smooth-polyorder", type=int, default=2, help="savgol 多项式阶数")
    sp_carbon.add_argument("--legend-mode", choices=["detailed", "compact"], default="compact", help="图例模式")
    sp_carbon.add_argument("--theme", choices=["light", "dark"], default="light", help="主题")
    sp_carbon.add_argument("--palette", default="viridis", help="中间碳数区调色板")
    sp_carbon.add_argument("--fig-width", type=float, default=10.5, help="图宽(英寸)")
    sp_carbon.add_argument("--fig-height", type=float, default=6.2, help="图高(英寸)")
    sp_carbon.add_argument("--dpi", type=int, default=180, help="保留兼容性的 dpi 参数")
    sp_carbon.add_argument("--no-uncertainty", action="store_true", help="有 replicate 时关闭标准差阴影")
    sp_carbon.add_argument("--out-fig", default="", help="输出图像路径, 支持 PNG/SVG/PDF")
    sp_carbon.add_argument("--out-csv", default="", help="输出 plot_data CSV")
    sp_carbon.add_argument("--out-summary", default="", help="输出 summary JSON")
    sp_carbon.set_defaults(func=cmd_carbon_plot)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
