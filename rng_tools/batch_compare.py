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

from rng_tools.network import Reaction, ReactionNetwork


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
        return "_".join(parts) if parts else _fallback_group_name(self.name)


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
    mean_net_tp: float = 0.0
    std_net_tp: float = 0.0
    ci_95_lower: float = 0.0
    ci_95_upper: float = 0.0


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------

_CONDITION_FIELD_PATTERNS: Dict[str, str] = {
    "temperature": r"(?:^|[_-])T(?:EMP)?[=_-]?(\d+(?:\.\d+)?)K?(?=$|[_-])",
    "o2_ratio": r"(?:^|[_-])O2[=_-]?(\d+(?:\.\d+)?)(?=$|[_-])",
    "pressure": r"(?:^|[_-])P(?:RESSURE)?[=_-]?(\d+(?:\.\d+)?)(?:ATM)?(?=$|[_-])",
    "replicate": r"(?:^|[_-])(?:REP(?:LICATE)?|RUN|SEED)[=_-]?(\d+)(?=$|[_-])",
}

_REPLICATE_SUFFIX_RE = re.compile(
    r"(?i)(?:[_-](?:rep(?:licate)?|run|seed)[=_-]?\d+)$"
)


def _parse_condition_name(dirname: str) -> Dict[str, Any]:
    """Try to extract temperature, O₂, pressure, replicate from a
    directory name.

    Returns a dict with keys that were successfully parsed.
    """
    name = os.path.basename(str(dirname).rstrip(os.sep))
    result: Dict[str, Any] = {}
    for field_name, pattern in _CONDITION_FIELD_PATTERNS.items():
        match = re.search(pattern, name, re.IGNORECASE)
        if match is None:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError, IndexError):
            continue
        if value == int(value):
            value = int(value)
        result[field_name] = value
    return result


def _fallback_group_name(condition_name: str) -> str:
    """Group generic ``case_repN`` directories without losing parent context."""
    clean_name = str(condition_name or "").replace(os.sep, "/").rstrip("/")
    parent, _, leaf = clean_name.rpartition("/")
    grouped_leaf = _REPLICATE_SUFFIX_RE.sub("", leaf).rstrip("_-") or leaf
    return f"{parent}/{grouped_leaf}" if parent else grouped_leaf


def _split_top_level_terms(side: str) -> Tuple[str, ...]:
    """Split a reaction side without treating charge signs as separators."""
    terms: List[str] = []
    current: List[str] = []
    bracket_depth = 0
    for character in str(side or ""):
        if character == "[":
            bracket_depth += 1
        elif character == "]" and bracket_depth:
            bracket_depth -= 1
        if character == "+" and bracket_depth == 0:
            term = "".join(current).strip()
            if term:
                terms.append(term)
            current = []
            continue
        current.append(character)
    term = "".join(current).strip()
    if term:
        terms.append(term)
    return tuple(terms)


def _canonical_reaction_key(reaction_text: str) -> str:
    text = str(reaction_text or "").strip()
    if "->" not in text:
        return text
    left, right = text.split("->", 1)
    reactants = _split_top_level_terms(left)
    products = _split_top_level_terms(right)
    return f"{'+'.join(sorted(reactants))}->{'+'.join(sorted(products))}"


def _reverse_reaction_key(reaction_key: str) -> str:
    if "->" not in reaction_key:
        return ""
    left, right = reaction_key.split("->", 1)
    return f"{right}->{left}"


_T_CRITICAL_95 = (
    12.706,
    4.303,
    3.182,
    2.776,
    2.571,
    2.447,
    2.365,
    2.306,
    2.262,
    2.228,
    2.201,
    2.179,
    2.160,
    2.145,
    2.131,
    2.120,
    2.110,
    2.101,
    2.093,
    2.086,
    2.080,
    2.074,
    2.069,
    2.064,
    2.060,
    2.056,
    2.052,
    2.048,
    2.045,
    2.042,
)


def _sample_standard_deviation(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _confidence_interval_95(values: List[float]) -> Tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    mean = sum(values) / len(values)
    if len(values) < 2:
        return (mean, mean)
    standard_error = _sample_standard_deviation(values) / math.sqrt(len(values))
    degrees_of_freedom = len(values) - 1
    if degrees_of_freedom <= len(_T_CRITICAL_95):
        critical = _T_CRITICAL_95[degrees_of_freedom - 1]
    else:
        # Cornish-Fisher expansion for the two-sided 95% Student-t quantile.
        # This avoids a SciPy runtime dependency while remaining continuous
        # with the exact small-sample table above.
        z = 1.959963984540054
        df = float(degrees_of_freedom)
        critical = (
            z
            + (z**3 + z) / (4 * df)
            + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
        )
    margin = critical * standard_error
    return (mean - margin, mean + margin)


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
        self.scan_warnings: List[str] = []

    def add_condition(
        self,
        name: str,
        network: ReactionNetwork,
        **meta: Any,
    ) -> None:
        """Register a condition with its loaded reaction network."""
        condition_name = str(name or "").strip()
        if not condition_name:
            raise ValueError("condition name cannot be empty")

        # Replacing a condition must also remove its old reverse-index entries.
        if condition_name in self._conditions:
            for reaction_key in list(self._reaction_index):
                condition_names = self._reaction_index[reaction_key]
                condition_names.discard(condition_name)
                if not condition_names:
                    del self._reaction_index[reaction_key]

        self._conditions[condition_name] = network
        self._condition_meta[condition_name] = meta
        self._reaction_cache[condition_name] = {}

        # Index reactions
        for rxn in network.reactions:
            self._reaction_index[rxn.key].add(condition_name)
            self._reaction_cache[condition_name][rxn.key] = rxn

    def scan_directory_tree(
        self,
        root_dir: str,
        *,
        progress_callback: Any = None,
        recursive: bool = True,
        max_depth: int = 4,
        max_conditions: int = 500,
    ) -> List[SimulationCondition]:
        """Recursively scan `root_dir` for directories containing
        ``.reactionabcd`` files.

        Each such directory becomes a :class:`SimulationCondition`.
        """
        conditions: List[SimulationCondition] = []
        self.scan_warnings = []
        root = os.path.abspath(root_dir)

        if not os.path.isdir(root):
            return conditions

        ignored_directories = {".git", ".cache", "__pycache__", "node_modules"}

        def record_walk_error(exc: OSError) -> None:
            self.scan_warnings.append(
                f"无法读取目录 {getattr(exc, 'filename', '') or root}: {exc}"
            )

        visited = 0
        for entry_path, subdirectories, filenames in os.walk(
            root,
            topdown=True,
            onerror=record_walk_error,
            followlinks=False,
        ):
            visited += 1
            relative = os.path.relpath(entry_path, root)
            depth = 0 if relative == "." else len(relative.split(os.sep))
            subdirectories[:] = sorted(
                directory
                for directory in subdirectories
                if directory not in ignored_directories and not directory.startswith(".")
            )
            if not recursive or depth >= max(0, int(max_depth)):
                subdirectories[:] = []

            candidates = sorted(
                filename
                for filename in filenames
                if filename.endswith(".reactionabcd")
            )
            if not candidates:
                continue

            reaction_path = os.path.join(entry_path, candidates[0])
            if len(candidates) > 1:
                self.scan_warnings.append(
                    f"{entry_path} 含有多个 .reactionabcd 文件，使用 {candidates[0]}"
                )

            if relative == ".":
                entry = os.path.basename(root.rstrip(os.sep)) or root
            else:
                entry = relative.replace(os.sep, "/")
            parsed = _parse_condition_name(os.path.basename(entry_path))
            cond = SimulationCondition(
                name=entry,
                folder=entry_path,
                temperature=parsed.get("temperature"),
                o2_ratio=parsed.get("o2_ratio"),
                pressure=parsed.get("pressure"),
                replicate=int(parsed.get("replicate", 1)),
                artifacts={"reaction": reaction_path},
            )
            conditions.append(cond)

            # A simulation directory is a leaf for this scanner. This avoids
            # walking generated caches nested below an already discovered run.
            subdirectories[:] = []

            if progress_callback:
                progress_callback(
                    {
                        "progress": min(len(conditions) / max(max_conditions, 1), 0.99),
                        "phase": "scanning",
                        "message": f"已检查 {visited} 个目录",
                        "found": len(conditions),
                    }
                )

            if len(conditions) >= max(1, int(max_conditions)):
                self.scan_warnings.append(
                    f"扫描结果已达到上限 {max_conditions}，其余目录未继续处理"
                )
                break

        if progress_callback:
            progress_callback(
                {
                    "progress": 1.0,
                    "phase": "complete",
                    "message": f"扫描完成，共发现 {len(conditions)} 个条件",
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
        """Compare one directed, exact-SMILES reaction across conditions.

        Formula-only matching is intentionally not used here: molecular
        formulae cannot distinguish structural isomers and set-based formula
        matching also loses stoichiometric multiplicity.  Exact RNG reaction
        keys keep the comparison scientifically auditable.
        """
        reaction_key = _canonical_reaction_key(reaction_smiles)
        reverse_key = _reverse_reaction_key(reaction_key)
        result = ReactionComparison(
            reaction_smiles=reaction_key,
            reaction_formulas="",
        )
        detected_count = 0
        total_conditions = len(self._conditions)
        for name in self._conditions:
            cache = self._reaction_cache.get(name, {})
            forward_reaction = cache.get(reaction_key)
            reverse_reaction = cache.get(reverse_key)
            forward_tp = float(forward_reaction.tp if forward_reaction else 0)
            reverse_tp = float(reverse_reaction.tp if reverse_reaction else 0)
            net_tp = forward_tp - reverse_tp

            if forward_reaction is not None:
                result.reactions[name] = forward_reaction
                if not result.reaction_formulas:
                    result.reaction_formulas = (
                        " + ".join(forward_reaction.reactant_formulas)
                        + " -> "
                        + " + ".join(forward_reaction.product_formulas)
                    )

            result.tp_by_condition[name] = forward_tp
            result.net_tp_by_condition[name] = net_tp
            result.forward_tp_by_condition[name] = forward_tp
            result.reverse_tp_by_condition[name] = reverse_tp

            if forward_tp > 0:
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
        if not 0.0 <= float(min_detection_rate) <= 1.0:
            raise ValueError("min_detection_rate must be between 0 and 1")
        if int(top_n) < 1:
            raise ValueError("top_n must be at least 1")

        # The index contains exact directed SMILES keys only.
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

            all_reactions.append((reaction_key, detection_rate, total_tp))

        all_reactions.sort(key=lambda x: (-x[1], -x[2]))
        all_reactions = all_reactions[: int(top_n)]

        results: List[ReactionComparison] = []
        for rxn_key, _, _ in all_reactions:
            comparison = self.compare_reaction(rxn_key)
            if comparison.detection_rate >= float(min_detection_rate):
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
        net_values: List[float] = []
        reverse_values: List[float] = []
        replicate_rows: List[Dict[str, Any]] = []
        detected = 0
        for cond in condition_group.conditions:
            tp = float(comparison.tp_by_condition.get(cond.name, 0) or 0)
            net_tp = float(
                comparison.net_tp_by_condition.get(cond.name, 0) or 0
            )
            reverse_tp = float(
                comparison.reverse_tp_by_condition.get(cond.name, 0) or 0
            )
            tp_values.append(tp)
            net_values.append(net_tp)
            reverse_values.append(reverse_tp)
            if tp > 0:
                detected += 1
            replicate_rows.append(
                {
                    "name": str(cond.artifacts.get("display_name") or cond.name),
                    "replicate": cond.replicate,
                    "tp": round(tp, 3),
                    "reverse_tp": round(reverse_tp, 3),
                    "net_tp": round(net_tp, 3),
                    "detected": tp > 0,
                }
            )

        n = len(tp_values)
        if n == 0:
            return {}

        mean = sum(tp_values) / n
        std = _sample_standard_deviation(tp_values)
        mean_net = sum(net_values) / n
        std_net = _sample_standard_deviation(net_values)
        mean_reverse = sum(reverse_values) / n
        ci_lower, ci_upper = _confidence_interval_95(tp_values)

        return {
            "group_name": condition_group.group_name,
            "n_replicates": n,
            "mean_tp": round(mean, 2),
            "std_tp": round(std, 2),
            "min_tp": round(min(tp_values), 2),
            "max_tp": round(max(tp_values), 2),
            "detected_count": detected,
            "detection_rate": round(detected / n, 3),
            "mean_reverse_tp": round(mean_reverse, 2),
            "mean_net_tp": round(mean_net, 2),
            "std_net_tp": round(std_net, 2),
            "ci_95_lower": round(ci_lower, 2),
            "ci_95_upper": round(ci_upper, 2),
            "replicates": replicate_rows,
        }


# ---------------------------------------------------------------------------
# Convenience: reaction_key to display string
# ---------------------------------------------------------------------------


def reaction_key_to_display(rxn_key: str) -> str:
    """Convert a reaction key like ``"A+B->C+D"`` to a display string."""
    return rxn_key.replace("+", " + ").replace("->", " -> ")
