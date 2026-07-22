"""Multi-condition batch comparison for reactive MD simulations.

Scans directory trees for multiple simulation conditions (varying
temperature, O₂ ratio, pressure, replicate number), loads reaction
networks for each, and computes cross-condition comparison statistics.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from rng_tools.network import Reaction, ReactionNetwork, parse_reactionabcd


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SimulationCondition:
    """Metadata for one simulation directory."""

    name: str
    folder: str
    temperature: Optional[float] = None
    o2_ratio: Optional[float] = None
    pressure: Optional[float] = None
    replicate: int = 1
    artifacts: Dict[str, str] = field(default_factory=dict)

    @property
    def group_key(self) -> str:
        """Build a grouping key from temperature and O₂ ratio."""
        parts: List[str] = []
        if self.temperature is not None:
            parts.append(f"T{self.temperature:.0f}K")
        if self.o2_ratio is not None:
            parts.append(f"O2={self.o2_ratio}")
        if self.pressure is not None:
            parts.append(f"P={self.pressure}")
        return "_".join(parts) if parts else self.name


@dataclass
class ConditionGroup:
    """A group of replicate simulations sharing identical conditions."""

    group_name: str
    temperature: Optional[float] = None
    o2_ratio: Optional[float] = None
    pressure: Optional[float] = None
    conditions: List[SimulationCondition] = field(default_factory=list)

    @property
    def n_replicates(self) -> int:
        return len(self.conditions)


@dataclass
class ReactionComparison:
    """Cross-condition comparison for a single reaction."""

    reaction_smiles: str
    reaction_formulas: str
    reactions: Dict[str, Reaction] = field(default_factory=dict)
    tp_by_condition: Dict[str, float] = field(default_factory=dict)
    net_tp_by_condition: Dict[str, float] = field(default_factory=dict)
    forward_tp_by_condition: Dict[str, float] = field(default_factory=dict)
    reverse_tp_by_condition: Dict[str, float] = field(default_factory=dict)
    detection_rate: float = 0.0

    @property
    def condition_names(self) -> List[str]:
        return sorted(self.tp_by_condition.keys())


@dataclass
class ReplicateStatistic:
    """Statistics across replicates within one condition group."""

    group_name: str
    mean_tp: float = 0.0
    std_tp: float = 0.0
    min_tp: float = 0.0
    max_tp: float = 0.0
    n_replicates: int = 0
    detected_count: int = 0
    detection_rate: float = 0.0


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------

# Patterns for extracting condition metadata from directory names
_CONDITION_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    # T300K_O2-0.1_rep1
    (
        r"T(\d+\.?\d*)K?[_-]O2[=_-](\d+\.?\d*)[_-]rep(\d+)",
        ("temperature", "o2_ratio", "replicate"),
    ),
    # T300_O2-0.1_rep1
    (
        r"T(\d+\.?\d*)[_-]O2[=_-](\d+\.?\d*)[_-]rep(\d+)",
        ("temperature", "o2_ratio", "replicate"),
    ),
    # T300K_P-1atm_O2-0.5_rep2
    (
        r"T(\d+\.?\d*)K?[_-]P[=_-](\d+\.?\d*)[_-]O2[=_-](\d+\.?\d*)[_-]rep(\d+)",
        ("temperature", "pressure", "o2_ratio", "replicate"),
    ),
    # Generic: any dir with rep at the end
    (
        r".*[_-]rep(\d+)$",
        ("replicate",),
    ),
]


def _parse_condition_name(dirname: str) -> Dict[str, Any]:
    """Try to extract temperature, O₂, pressure, replicate from a
    directory name.

    Returns a dict with keys that were successfully parsed.
    """
    for pattern, fields in _CONDITION_PATTERNS:
        m = re.match(pattern, dirname, re.IGNORECASE)
        if m:
            result: Dict[str, Any] = {}
            for i, field in enumerate(fields):
                try:
                    val = float(m.group(i + 1))
                    if val == int(val):
                        val = int(val)
                    result[field] = val
                except (ValueError, IndexError):
                    pass
            return result
    return {}


# ---------------------------------------------------------------------------
# BatchComparator
# ---------------------------------------------------------------------------


class BatchComparator:
    """Compare reactions across multiple simulation conditions.

    Usage::

        comparator = BatchComparator()
        comparator.add_condition("T300K_O2-0.1",
                                 network_t300, temperature=300, o2_ratio=0.1)
        comparator.add_condition("T400K_O2-0.1",
                                 network_t400, temperature=400, o2_ratio=0.1)
        results = comparator.compare_all_common(top_n=100)
    """

    def __init__(self) -> None:
        self._conditions: Dict[str, ReactionNetwork] = {}
        self._condition_meta: Dict[str, Dict[str, Any]] = {}
        # Reverse index: reaction_key -> list of condition_names
        self._reaction_index: Dict[str, Set[str]] = defaultdict(set)
        # Cache: condition_name -> {reaction_key -> Reaction}
        self._reaction_cache: Dict[str, Dict[str, Reaction]] = {}

    def add_condition(
        self,
        name: str,
        network: ReactionNetwork,
        **meta: Any,
    ) -> None:
        """Register a condition with its loaded reaction network."""
        self._conditions[name] = network
        self._condition_meta[name] = meta
        self._reaction_cache[name] = {}

        # Index reactions
        for rxn in network.reactions:
            self._reaction_index[rxn.key].add(name)
            self._reaction_cache[name][rxn.key] = rxn

            # Also index by formula key
            if hasattr(rxn, 'formula_key'):
                self._reaction_index[rxn.formula_key].add(name)

    def scan_directory_tree(
        self,
        root_dir: str,
        *,
        progress_callback: Any = None,
    ) -> List[SimulationCondition]:
        """Recursively scan `root_dir` for directories containing
        ``.reactionabcd`` files.

        Each such directory becomes a :class:`SimulationCondition`.
        """
        conditions: List[SimulationCondition] = []
        root = os.path.abspath(root_dir)

        if not os.path.isdir(root):
            return conditions

        entries = sorted(os.listdir(root))
        total = len(entries)

        for i, entry in enumerate(entries):
            entry_path = os.path.join(root, entry)
            if not os.path.isdir(entry_path):
                continue

            reac_file = os.path.join(entry_path, f"{entry}.reactionabcd")
            # Also check for bare name
            if not os.path.isfile(reac_file):
                # Try finding any .reactionabcd
                candidates = [
                    f for f in os.listdir(entry_path)
                    if f.endswith(".reactionabcd")
                ]
                if candidates:
                    reac_file = os.path.join(entry_path, candidates[0])
                else:
                    continue

            parsed = _parse_condition_name(entry)
            cond = SimulationCondition(
                name=entry,
                folder=entry_path,
                temperature=parsed.get("temperature"),
                o2_ratio=parsed.get("o2_ratio"),
                pressure=parsed.get("pressure"),
                replicate=int(parsed.get("replicate", 1)),
            )
            conditions.append(cond)

            if progress_callback:
                progress_callback(
                    {
                        "progress": (i + 1) / max(total, 1),
                        "phase": "scanning",
                        "message": f"Scanned {i + 1}/{total} entries",
                        "found": len(conditions),
                    }
                )

        return conditions

    def auto_group_conditions(
        self,
        conditions: List[SimulationCondition],
    ) -> List[ConditionGroup]:
        """Group conditions by temperature and O₂ ratio."""
        groups: Dict[str, ConditionGroup] = {}

        for cond in conditions:
            key = cond.group_key
            if key not in groups:
                groups[key] = ConditionGroup(
                    group_name=key,
                    temperature=cond.temperature,
                    o2_ratio=cond.o2_ratio,
                    pressure=cond.pressure,
                )
            groups[key].conditions.append(cond)

        return sorted(groups.values(), key=lambda g: g.group_name)

    def compare_reaction(
        self,
        reaction_smiles: str,
    ) -> ReactionComparison:
        """Compare a single reaction across all registered conditions.

        Uses both exact SMILES matching and formula-level matching.
        """
        from rng_tools.network import smiles_to_formula_fast

        result = ReactionComparison(
            reaction_smiles=reaction_smiles,
            reaction_formulas="",
        )

        # Parse the target reaction into formulas
        try:
            arrow = "->"
            if arrow in reaction_smiles:
                left, right = reaction_smiles.split(arrow, 1)
                target_reactant_f = frozenset(
                    smiles_to_formula_fast(s.strip()) for s in left.split("+") if s.strip()
                )
                target_product_f = frozenset(
                    smiles_to_formula_fast(s.strip()) for s in right.split("+") if s.strip()
                )
            else:
                target_reactant_f = frozenset()
                target_product_f = frozenset()
        except Exception:
            target_reactant_f = frozenset()
            target_product_f = frozenset()

        detected_count = 0
        total_conditions = len(self._conditions)

        for name, network in self._conditions.items():
            tp = 0.0
            net_tp = 0.0
            forward_tp = 0.0
            reverse_tp = 0.0

            # Try formula-level matching first (more robust)
            for rxn in network.reactions:
                r_f = frozenset(rxn.reactant_formulas)
                p_f = frozenset(rxn.product_formulas)

                # Forward match
                if target_reactant_f and target_product_f:
                    if r_f == target_reactant_f and p_f == target_product_f:
                        tp = rxn.tp
                        forward_tp = rxn.tp
                        # Try to find reverse
                        for r2 in network.reactions:
                            r2_f = frozenset(r2.reactant_formulas)
                            p2_f = frozenset(r2.product_formulas)
                            if r2_f == target_product_f and p2_f == target_reactant_f:
                                reverse_tp = r2.tp
                                break
                        net_tp = forward_tp - reverse_tp
                        if not result.reaction_formulas:
                            result.reaction_formulas = (
                                " + ".join(rxn.reactant_formulas) +
                                " -> " +
                                " + ".join(rxn.product_formulas)
                            )
                        break

            # Fallback: exact SMILES string match
            if tp == 0:
                for rxn in network.reactions:
                    rxn_str = (
                        " + ".join(rxn.reactant_smiles) +
                        " -> " +
                        " + ".join(rxn.product_smiles)
                    )
                    if rxn_str == reaction_smiles:
                        tp = rxn.tp
                        forward_tp = rxn.tp
                        rev_smiles = (
                            " + ".join(rxn.product_smiles) +
                            " -> " +
                            " + ".join(rxn.reactant_smiles)
                        )
                        for r2 in network.reactions:
                            r2_str = (
                                " + ".join(r2.reactant_smiles) +
                                " -> " +
                                " + ".join(r2.product_smiles)
                            )
                            if r2_str == rev_smiles:
                                reverse_tp = r2.tp
                                break
                        net_tp = forward_tp - reverse_tp
                        if not result.reaction_formulas:
                            result.reaction_formulas = (
                                " + ".join(rxn.reactant_formulas) +
                                " -> " +
                                " + ".join(rxn.product_formulas)
                            )
                        break

            result.tp_by_condition[name] = tp
            result.net_tp_by_condition[name] = net_tp
            result.forward_tp_by_condition[name] = forward_tp
            result.reverse_tp_by_condition[name] = reverse_tp

            if tp > 0 or net_tp != 0:
                detected_count += 1

        result.detection_rate = (
            detected_count / max(total_conditions, 1)
        )
        return result

    def compare_all_common(
        self,
        *,
        min_detection_rate: float = 0.0,
        top_n: int = 100,
    ) -> List[ReactionComparison]:
        """Find all reactions appearing in at least one condition.

        Results are sorted by detection rate (descending), then total
        tp (descending).
        """
        # Collect all unique reaction SMILES across all conditions
        seen: Set[str] = set()
        all_reactions: List[Tuple[str, float, float]] = []

        for reaction_key, condition_set in self._reaction_index.items():
            detection_rate = len(condition_set) / max(len(self._conditions), 1)
            if detection_rate < min_detection_rate:
                continue

            # Get total tp across conditions
            total_tp = 0.0
            for name in condition_set:
                cache = self._reaction_cache.get(name, {})
                rxn = cache.get(reaction_key)
                if rxn:
                    total_tp += rxn.tp

            if reaction_key not in seen:
                seen.add(reaction_key)
                all_reactions.append((reaction_key, detection_rate, total_tp))

        all_reactions.sort(key=lambda x: (-x[1], -x[2]))
        all_reactions = all_reactions[:top_n]

        results: List[ReactionComparison] = []
        for rxn_key, _, _ in all_reactions:
            comparison = self.compare_reaction(rxn_key)
            results.append(comparison)

        return results

    def build_comparison_matrix(
        self,
        reactions: List[ReactionComparison],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Build a flat table of reaction × condition.

        Returns
        -------
        (rows, condition_names)
            ``rows`` — list of dicts with columns for each condition.
            ``condition_names`` — sorted list of condition names (the
            per-condition column headers).
        """
        condition_names = sorted(self._conditions.keys())
        rows: List[Dict[str, Any]] = []

        for i, comp in enumerate(reactions):
            row: Dict[str, Any] = {
                "index": i + 1,
                "reaction_smiles": comp.reaction_smiles,
                "reaction_formulas": comp.reaction_formulas,
                "detection_rate": round(comp.detection_rate, 3),
            }
            for cname in condition_names:
                tp = float(comp.tp_by_condition.get(cname, 0) or 0)
                net = float(comp.net_tp_by_condition.get(cname, 0) or 0)
                row[f"tp_{cname}"] = int(tp)
                row[f"net_{cname}"] = int(net)

            rows.append(row)

        return rows, condition_names

    def statistical_summary(
        self,
        comparison: ReactionComparison,
        condition_group: Optional[ConditionGroup] = None,
    ) -> Dict[str, Any]:
        """Compute replicate statistics for a reaction across one
        condition group.
        """
        if condition_group is None:
            return {}

        tp_values: List[float] = []
        detected = 0
        for cond in condition_group.conditions:
            tp = float(comparison.tp_by_condition.get(cond.name, 0) or 0)
            tp_values.append(tp)
            if tp > 0:
                detected += 1

        n = len(tp_values)
        if n == 0:
            return {}

        mean = sum(tp_values) / n
        variance = sum((x - mean) ** 2 for x in tp_values) / max(n - 1, 1)
        std = math.sqrt(variance)

        # 95% CI using t-distribution approximation (normal for n >= 30)
        if n >= 2:
            se = std / math.sqrt(n)
            ci = 1.96 * se  # normal approximation
        else:
            ci = 0.0

        return {
            "group_name": condition_group.group_name,
            "n_replicates": n,
            "mean_tp": round(mean, 2),
            "std_tp": round(std, 2),
            "min_tp": round(min(tp_values), 2),
            "max_tp": round(max(tp_values), 2),
            "detected_count": detected,
            "detection_rate": round(detected / n, 3),
            "ci_95_lower": round(mean - ci, 2),
            "ci_95_upper": round(mean + ci, 2),
        }


# ---------------------------------------------------------------------------
# Convenience: reaction_key to display string
# ---------------------------------------------------------------------------


def reaction_key_to_display(rxn_key: str) -> str:
    """Convert a reaction key like ``"A+B->C+D"`` to a display string."""
    return rxn_key.replace("+", " + ").replace("->", " -> ")
