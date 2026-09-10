"""Discover and rank bounded candidate routes from prepared RNG evidence.

Search stays on the Reaction-Type/Species hypergraph.  Occurrence-level Event
Path construction is reserved for later validation of a selected Candidate.
"""

from __future__ import annotations

import csv
import hashlib
import heapq
import itertools
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping

from .network import Reaction, ReactionNetwork, count_atoms_fast
from .rng_events import canonical_reaction_key, reaction_key


CANDIDATE_PATH_SCHEMA_VERSION = "reacnet-scope/candidate-paths/v3"
SCORE_VERSION = "candidate-path/network-v1"
LEGACY_EVENT_PATH_SCORE_VERSION = "sampled-candidate-path/v2"
DEFAULT_SCORE_WEIGHTS = {
    "frequency": 0.35,
    "structure": 0.15,
    "temporal": 0.20,
    "continuity": 0.20,
    "energy": 0.10,
}


def discover_network_candidate_routes(
    network: ReactionNetwork,
    start_species: Iterable[str],
    *,
    minimum_path_length: int = 2,
    maximum_path_length: int = 4,
    max_expansions: int = 5_000,
    max_frontier_states: int = 5_000,
    max_generated_states: int = 10_000,
    max_paths: int = 20,
    minimum_occurrences: int = 1,
) -> dict[str, Any]:
    """Enumerate deterministic, bounded routes on the reaction hypergraph.

    This is deliberately a Reaction-Type/Species search.  It never reads
    Reaction Occurrences or constructs an occurrence graph; indexed Step
    Evidence is attached by the service after the bounded search identifies
    the small set of Reaction Types that needs drill-down.
    """

    starts = tuple(
        sorted({str(value).strip() for value in start_species if str(value).strip()})
    )
    if not starts:
        raise ValueError("at least one exact start Species is required")
    for name, value, lower, upper in (
        ("minimum_path_length", minimum_path_length, 1, 8),
        ("maximum_path_length", maximum_path_length, 1, 8),
        ("max_expansions", max_expansions, 1, 1_000_000),
        ("max_frontier_states", max_frontier_states, 1, 1_000_000),
        ("max_generated_states", max_generated_states, 1, 1_000_000),
        ("max_paths", max_paths, 1, 500),
        ("minimum_occurrences", minimum_occurrences, 1, None),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        if value < lower or (upper is not None and value > upper):
            suffix = f" and <= {upper}" if upper is not None else ""
            raise ValueError(f"{name} must be >= {lower}{suffix}")
    if minimum_path_length > maximum_path_length:
        raise ValueError("minimum_path_length must not exceed maximum_path_length")

    # Output headroom is separate from expansion, frontier and cumulative
    # generated-state budgets. The final ranker applies max_paths.
    candidate_limit = min(max_expansions, max(max_paths * 20, max_paths))
    sequence = itertools.count()
    queue: list[
        tuple[
            int,
            tuple[str, ...],
            tuple[str, ...],
            int,
            tuple[str, ...],
            tuple[Reaction, ...],
        ]
    ] = []
    generated_states = 0
    peak_frontier_states = 0
    child_candidates_examined = 0
    truncation_reasons: set[str] = set()
    for start in starts:
        if start not in network.species:
            continue
        if len(queue) >= max_frontier_states:
            truncation_reasons.add("max_frontier_states")
        if generated_states >= max_generated_states:
            truncation_reasons.add("max_generated_states")
        if truncation_reasons:
            break
        heapq.heappush(
            queue,
            (-2**63, (start,), (), next(sequence), (start,), ()),
        )
        generated_states += 1
        peak_frontier_states = max(peak_frontier_states, len(queue))

    routes: list[dict[str, Any]] = []
    seen_routes: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    expansions = 0
    while queue:
        if len(routes) >= candidate_limit:
            truncation_reasons.add("candidate_limit")
            break
        _priority, _species_key, _reaction_key, _order, species, reactions = (
            heapq.heappop(queue)
        )
        depth = len(reactions)
        if depth >= minimum_path_length:
            reaction_keys = tuple(reaction.key for reaction in reactions)
            identity = (species, reaction_keys)
            if identity not in seen_routes:
                seen_routes.add(identity)
                signature = hashlib.sha256(
                    "\x1f".join((*species, *reaction_keys)).encode("utf-8")
                ).hexdigest()[:20]
                routes.append(
                    {
                        "signature_id": signature,
                        "species": species,
                        "reaction_keys": reaction_keys,
                        "occurrence_count": min(
                            int(reaction.tp) for reaction in reactions
                        ),
                        "minimum_step_occurrence_count": min(
                            int(reaction.tp) for reaction in reactions
                        ),
                        "independent_atom_lineage_support_count": 0,
                        "replicate_support_count": 0,
                        "replicate_reproduction_rate": 0.0,
                        "interval_gap_by_edge": [],
                        "interval_span": {},
                        "support_is_lower_bound": False,
                    }
                )
        if depth >= maximum_path_length:
            continue
        if expansions >= max_expansions:
            truncation_reasons.add("max_expansions")
            break

        expansions += 1
        focal = species[-1]
        frontier_slots = max_frontier_states - len(queue)
        generation_slots = max_generated_states - generated_states
        slots = min(frontier_slots, generation_slots)

        def child_candidates():
            nonlocal child_candidates_examined
            for reaction in network.consume_idx.get(focal, ()):
                if int(reaction.tp) < minimum_occurrences:
                    continue
                # Stream products too: one hyperedge may have many products.
                for product in reaction.product_smiles:
                    if product in species:
                        continue
                    child_candidates_examined += 1
                    yield reaction, product

        # Retain at most the available slots plus one truncation witness, not
        # every outgoing branch. Match the frontier's ordering, independently
        # of adjacency input order. This bounds temporary memory, but still
        # scans adjacent reaction/product references in this compatibility
        # network; it is not a replacement for the production indexed search.
        parent_bottleneck = min((int(item.tp) for item in reactions), default=2**63)
        children = heapq.nsmallest(
            slots + 1,
            child_candidates(),
            key=lambda item: (
                -min(parent_bottleneck, int(item[0].tp)), item[1], item[0].key,
            ),
        )
        if len(children) > slots:
            if slots == frontier_slots:
                truncation_reasons.add("max_frontier_states")
            if slots == generation_slots:
                truncation_reasons.add("max_generated_states")
        for reaction, product in itertools.islice(children, slots):
            child_species = (*species, product)
            child_reactions = (*reactions, reaction)
            bottleneck = min(int(item.tp) for item in child_reactions)
            child_keys = tuple(item.key for item in child_reactions)
            heapq.heappush(
                queue,
                (
                    -bottleneck,
                    child_species,
                    child_keys,
                    next(sequence),
                    child_species,
                    child_reactions,
                ),
            )
            generated_states += 1
            peak_frontier_states = max(peak_frontier_states, len(queue))

    return {
        "summary": {
            "discovery_kind": "md_observed_reaction_hypergraph",
            "candidate_route_count": len(routes),
            "traversal_truncated": bool(truncation_reasons),
            "truncation_reasons": sorted(truncation_reasons),
            "expansions": expansions,
            "generated_states": generated_states,
            "peak_frontier_states": peak_frontier_states,
            "child_candidates_examined": child_candidates_examined,
            "budgets": {
                "max_expansions": max_expansions,
                "max_frontier_states": max_frontier_states,
                "max_generated_states": max_generated_states,
                "candidate_limit": candidate_limit,
            },
            "statistics_complete": not truncation_reasons,
        },
        "sources": [],
        "paths": routes,
    }


@dataclass(frozen=True)
class EnergyEvidence:
    """Optional normalized energy evidence for one exact Reaction Type."""

    score: float
    delta_energy: float | None = None
    barrier: float | None = None
    unit: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.score)) or not 0 <= float(self.score) <= 1:
            raise ValueError("energy score must be a finite number in [0, 1]")


@dataclass(frozen=True)
class CandidatePathStep:
    reaction_key: str
    focal_input: str
    focal_output: str
    reactants: tuple[str, ...]
    products: tuple[str, ...]
    forward_tp: int
    reverse_tp: int
    net_tp: int
    directionality: float
    structure_similarity: float
    energy_score: float | None
    delta_energy: float | None
    barrier: float | None
    energy_unit: str
    energy_source: str


@dataclass(frozen=True)
class RankedCandidatePath:
    rank: int
    signature_id: str
    start_species: str
    species: tuple[str, ...]
    reaction_keys: tuple[str, ...]
    steps: tuple[CandidatePathStep, ...]
    score: float
    frequency_score: float
    structure_score: float
    temporal_score: float
    continuity_score: float
    energy_score: float | None
    energy_coverage: float
    occurrence_count: int
    minimum_step_occurrence_count: int
    step_evidence: tuple[dict[str, Any], ...]
    independent_atom_lineage_support_count: int
    replicate_support_count: int
    replicate_reproduction_rate: float
    median_interval_span: float | None
    support_is_lower_bound: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_unit(name: str, value: Any) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return number


def _normalize_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    merged = dict(DEFAULT_SCORE_WEIGHTS)
    if weights is not None:
        unknown = set(weights).difference(merged)
        if unknown:
            raise ValueError(f"unknown score weights: {', '.join(sorted(unknown))}")
        merged.update({key: float(value) for key, value in weights.items()})
    if any(not math.isfinite(value) or value < 0 for value in merged.values()):
        raise ValueError("score weights must be finite and nonnegative")
    if not any(merged.values()):
        raise ValueError("at least one score weight must be positive")
    return merged


def _reaction_from_key(key: str, by_key: Mapping[str, Reaction]) -> Reaction:
    existing = by_key.get(key)
    if existing is not None:
        return existing
    left, separator, right = str(key).partition("->")
    if not separator:
        raise ValueError(f"invalid Reaction Type in event path: {key!r}")
    reactants, products = reaction_key(left, right)
    if not reactants or not products:
        raise ValueError(f"invalid Reaction Type in event path: {key!r}")
    return Reaction(reactants, products, 0)


def _composition_similarity(left: str, right: str) -> float:
    left_counts = count_atoms_fast(left)
    right_counts = count_atoms_fast(right)
    elements = set(left_counts).union(right_counts)
    if not elements:
        return 1.0 if left == right else 0.0
    overlap = sum(min(left_counts.get(key, 0), right_counts.get(key, 0)) for key in elements)
    union = sum(max(left_counts.get(key, 0), right_counts.get(key, 0)) for key in elements)
    return overlap / union if union else 0.0


def structure_similarity(left: str, right: str) -> float:
    """Return Morgan-Tanimoto similarity, with a composition fallback."""

    try:
        from rdkit import Chem
        from rdkit.Chem import rdFingerprintGenerator

        left_molecule = Chem.MolFromSmiles(left)
        right_molecule = Chem.MolFromSmiles(right)
        if left_molecule is not None and right_molecule is not None:
            generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
            left_fp = generator.GetFingerprint(left_molecule)
            right_fp = generator.GetFingerprint(right_molecule)
            from rdkit.DataStructs import TanimotoSimilarity

            return float(TanimotoSimilarity(left_fp, right_fp))
    except Exception:
        pass
    return _composition_similarity(left, right)


def _choose_focal_output(
    focal_input: str,
    reaction: Reaction,
    next_reaction: Reaction | None,
) -> str:
    products = sorted(set(reaction.product_smiles))
    if next_reaction is not None:
        bridge = sorted(set(products).intersection(next_reaction.reactant_smiles))
        if bridge:
            products = bridge
    if not products:
        return ""
    return min(
        products,
        key=lambda value: (-structure_similarity(focal_input, value), value),
    )


def _declared_species_chain(
    path: Mapping[str, Any], reactions: tuple[Reaction, ...],
) -> tuple[str, ...]:
    """Validate a network Candidate without inferring or repairing its identity."""
    values = path.get("species")
    if not isinstance(values, (list, tuple)) or len(values) != len(reactions) + 1:
        raise ValueError("network Candidate requires an explicit anchor/carried species chain")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Candidate species chain requires exact Species strings")
    chain = tuple(values)
    if len(set(chain)) != len(chain):
        raise ValueError("ordinary Candidate species chain cannot revisit a Species")
    if any(
        chain[index] not in reaction.reactant_smiles
        or chain[index + 1] not in reaction.product_smiles
        for index, reaction in enumerate(reactions)
    ):
        raise ValueError("Candidate species chain must join the directed reaction sides")
    return chain


def _legacy_event_path_species_chain(
    path: Mapping[str, Any], reactions: tuple[Reaction, ...], starts: tuple[str, ...],
) -> tuple[str, ...]:
    """Explicit compatibility boundary for reports without network discovery semantics."""
    matched_starts = sorted(set(starts).intersection(reactions[0].reactant_smiles))
    if not matched_starts:
        return ()
    chain = [matched_starts[0]]
    reported = tuple(str(value) for value in path.get("species") or ())
    for index, reaction in enumerate(reactions):
        next_reaction = reactions[index + 1] if index + 1 < len(reactions) else None
        output = (
            reported[index + 1]
            if len(reported) == len(reactions) + 1 and reported[0] == chain[0]
            else _choose_focal_output(chain[-1], reaction, next_reaction)
        )
        if not output:
            return ()
        chain.append(output)
    return tuple(chain)


def _median_edge_gap(path: Mapping[str, Any]) -> float | None:
    medians = [
        float(row["median"])
        for row in path.get("interval_gap_by_edge") or ()
        if row.get("median") is not None
    ]
    return fmean(medians) if medians else None


def _energy_record(value: EnergyEvidence | Mapping[str, Any]) -> EnergyEvidence:
    if isinstance(value, EnergyEvidence):
        return value
    return EnergyEvidence(
        score=_bounded_unit("energy score", value.get("score")),
        delta_energy=(
            None if value.get("delta_energy") in (None, "") else float(value["delta_energy"])
        ),
        barrier=None if value.get("barrier") in (None, "") else float(value["barrier"]),
        unit=str(value.get("unit") or ""),
        source=str(value.get("source") or ""),
    )


def load_energy_evidence_csv(path: str | Path) -> dict[str, EnergyEvidence]:
    """Load optional user-normalized energy evidence keyed by Reaction Type.

    Required columns are ``reaction_key`` and ``score``. Optional columns are
    ``delta_energy``, ``barrier``, ``unit``, and ``source``. The normalized
    score is explicit because unrelated simulations and energy definitions
    must not be silently put onto a common scale.
    """

    source_path = Path(path).expanduser()
    records: dict[str, EnergyEvidence] = {}
    with source_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"reaction_key", "score"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("energy CSV requires reaction_key and score columns")
        for line_number, row in enumerate(reader, 2):
            raw_key = str(row.get("reaction_key") or "").strip()
            left, separator, right = raw_key.partition("->")
            if not separator:
                raise ValueError(f"energy CSV line {line_number} has an invalid reaction_key")
            key = canonical_reaction_key(*reaction_key(left, right))
            if key in records:
                raise ValueError(f"energy CSV contains duplicate Reaction Type {key!r}")
            enriched = dict(row)
            enriched["source"] = str(row.get("source") or source_path.resolve())
            try:
                records[key] = _energy_record(enriched)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid energy CSV line {line_number}: {exc}") from exc
    return records


def rank_candidate_paths(
    network: ReactionNetwork,
    event_path_report: Mapping[str, Any],
    start_species: Iterable[str],
    *,
    max_paths: int = 20,
    minimum_occurrences: int = 1,
    energy_evidence: Mapping[str, EnergyEvidence | Mapping[str, Any]] | None = None,
    score_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Rank declared network routes; retain explicit legacy Event Path compatibility."""

    if not isinstance(max_paths, int) or isinstance(max_paths, bool) or not 1 <= max_paths <= 500:
        raise ValueError("max_paths must be an integer in [1, 500]")
    if (
        not isinstance(minimum_occurrences, int)
        or isinstance(minimum_occurrences, bool)
        or minimum_occurrences < 1
    ):
        raise ValueError("minimum_occurrences must be a positive integer")
    starts = tuple(sorted({str(value).strip() for value in start_species if str(value).strip()}))
    if not starts:
        raise ValueError("at least one exact start Species is required")
    report_summary = dict(event_path_report.get("summary") or {})
    network_discovery = (
        report_summary.get("discovery_kind")
        == "md_observed_reaction_hypergraph"
    )
    weights = _normalize_weights(score_weights)
    energy_by_key = {
        str(key): _energy_record(value)
        for key, value in (energy_evidence or {}).items()
    }
    available_metrics = {"frequency", "structure"}
    if not network_discovery:
        available_metrics.update(("temporal", "continuity"))
    if energy_by_key:
        available_metrics.add("energy")
    denominator = sum(weights[name] for name in sorted(available_metrics))
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("available score metrics must have a finite positive total weight")
    reaction_by_key = {reaction.key: reaction for reaction in network.reactions}
    raw_paths = [
        dict(path)
        for path in event_path_report.get("paths") or ()
        if int(path.get("occurrence_count") or 0) >= minimum_occurrences
    ]
    max_occurrences = max((int(path.get("occurrence_count") or 0) for path in raw_paths), default=1)
    max_lineages = max(
        1,
        max(
            (
                int(path.get("independent_atom_lineage_support_count") or 0)
                for path in raw_paths
            ),
            default=0,
        ),
    )
    max_reaction_frequency = max(
        (
            math.log1p(max(0, _reaction_from_key(key, reaction_by_key).tp))
            for path in raw_paths
            for key in path.get("reaction_keys") or ()
        ),
        default=1.0,
    ) or 1.0

    candidates: list[RankedCandidatePath] = []
    for path in raw_paths:
        keys = tuple(str(key) for key in path.get("reaction_keys") or ())
        reactions = tuple(_reaction_from_key(key, reaction_by_key) for key in keys)
        if not reactions:
            continue
        declared_chain = (
            _declared_species_chain(path, reactions)
            if network_discovery
            else _legacy_event_path_species_chain(path, reactions, starts)
        )
        if not declared_chain or declared_chain[0] not in starts:
            continue
        start = declared_chain[0]
        focal = start
        species_chain = [start]
        steps: list[CandidatePathStep] = []
        step_frequency_scores: list[float] = []
        step_structure_scores: list[float] = []
        step_energy_scores: list[float] = []
        for index, (key, reaction) in enumerate(zip(keys, reactions)):
            output = declared_chain[index + 1]
            forward, reverse, net, _reversible = network.net_flux(reaction)
            similarity = structure_similarity(focal, output)
            energy = energy_by_key.get(key)
            step_frequency_scores.append(math.log1p(max(0, forward)) / max_reaction_frequency)
            step_structure_scores.append(similarity)
            if energy is not None:
                step_energy_scores.append(float(energy.score))
            steps.append(
                CandidatePathStep(
                    reaction_key=key,
                    focal_input=focal,
                    focal_output=output,
                    reactants=tuple(reaction.reactant_smiles),
                    products=tuple(reaction.product_smiles),
                    forward_tp=forward,
                    reverse_tp=reverse,
                    net_tp=net,
                    directionality=max(0, net) / forward if forward > 0 else 0.0,
                    structure_similarity=similarity,
                    energy_score=None if energy is None else float(energy.score),
                    delta_energy=None if energy is None else energy.delta_energy,
                    barrier=None if energy is None else energy.barrier,
                    energy_unit="" if energy is None else energy.unit,
                    energy_source="" if energy is None else energy.source,
                )
            )
            species_chain.append(output)
            focal = output
        if not steps:
            continue

        occurrence_count = int(path.get("occurrence_count") or 0)
        lineage_count = int(path.get("independent_atom_lineage_support_count") or 0)
        occurrence_score = math.log1p(occurrence_count) / math.log1p(max_occurrences)
        reaction_frequency_score = fmean(step_frequency_scores)
        frequency_score = 0.6 * occurrence_score + 0.4 * reaction_frequency_score
        structure_score = fmean(step_structure_scores)
        gap = _median_edge_gap(path)
        temporal_score = (
            0.0
            if network_discovery
            else (1.0 / max(1.0, gap) if gap is not None else 1.0)
        )
        reproduction_rate = _bounded_unit(
            "replicate reproduction rate",
            path.get("replicate_reproduction_rate") or 0.0,
        )
        lineage_score = math.log1p(lineage_count) / math.log1p(max_lineages)
        continuity_score = (
            0.0
            if network_discovery
            else 0.6 * reproduction_rate + 0.4 * lineage_score
        )
        energy_coverage = len(step_energy_scores) / len(steps)
        energy_score = (
            (fmean(step_energy_scores) * energy_coverage if step_energy_scores else 0.0)
            if energy_by_key
            else None
        )
        metric_values = {
            "frequency": frequency_score,
            "structure": structure_score,
            "temporal": None if network_discovery else temporal_score,
            "continuity": None if network_discovery else continuity_score,
            "energy": energy_score,
        }
        score = sum(
            weights[name] * float(value)
            for name, value in metric_values.items()
            if value is not None
        ) / denominator
        span = path.get("interval_span") or {}
        candidates.append(
            RankedCandidatePath(
                rank=0,
                signature_id=str(path.get("signature_id") or ""),
                start_species=start,
                species=tuple(species_chain),
                reaction_keys=keys,
                steps=tuple(steps),
                score=score,
                frequency_score=frequency_score,
                structure_score=structure_score,
                temporal_score=temporal_score,
                continuity_score=continuity_score,
                energy_score=energy_score,
                energy_coverage=energy_coverage,
                occurrence_count=occurrence_count,
                minimum_step_occurrence_count=int(
                    path.get("minimum_step_occurrence_count")
                    or occurrence_count
                ),
                step_evidence=tuple(
                    dict(value) for value in path.get("step_evidence") or ()
                ),
                independent_atom_lineage_support_count=lineage_count,
                replicate_support_count=int(path.get("replicate_support_count") or 0),
                replicate_reproduction_rate=reproduction_rate,
                median_interval_span=(None if span.get("median") is None else float(span["median"])),
                support_is_lower_bound=bool(path.get("support_is_lower_bound")),
            )
        )

    candidates.sort(
        key=lambda path: (
            -path.score,
            -path.occurrence_count,
            path.species,
            path.reaction_keys,
        )
    )
    ranked = [
        replace(path, rank=rank)
        for rank, path in enumerate(candidates[:max_paths], 1)
    ]
    return {
        "schema_version": CANDIDATE_PATH_SCHEMA_VERSION,
        "score_version": (
            SCORE_VERSION
            if network_discovery
            else LEGACY_EVENT_PATH_SCORE_VERSION
        ),
        "semantics": (
            "bounded candidate routes supported step-by-step by observed directed "
            "Reaction Types; not one continuous Event Path or mechanistic proof"
            if network_discovery
            else
            "ranked candidate routes observed in concrete RNG events; not proof of "
            "causality, uniqueness, transition states, or a complete mechanism"
        ),
        "query": {
            "start_species": list(starts),
            "max_paths": max_paths,
            "minimum_occurrences": minimum_occurrences,
        },
        "score_weights": weights,
        "energy_status": "provided" if energy_by_key else "not_provided",
        "paths": [path.as_dict() for path in ranked],
        "path_count": len(ranked),
        "candidate_count_before_limit": len(candidates),
        "truncated": bool(report_summary.get("traversal_truncated")) or len(candidates) > max_paths,
        "evidence_summary": report_summary,
        "sources": list(event_path_report.get("sources") or ()),
    }


__all__ = [
    "CANDIDATE_PATH_SCHEMA_VERSION",
    "SCORE_VERSION",
    "LEGACY_EVENT_PATH_SCORE_VERSION",
    "DEFAULT_SCORE_WEIGHTS",
    "EnergyEvidence",
    "discover_network_candidate_routes",
    "CandidatePathStep",
    "RankedCandidatePath",
    "load_energy_evidence_csv",
    "rank_candidate_paths",
    "structure_similarity",
]
