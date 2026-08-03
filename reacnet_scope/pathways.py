"""Immutable domain records and scoring for ranked candidate pathways.

The scores in this module are intentionally independent of graph libraries.
They rank candidate routes from network and optional event evidence; they do
not establish an atom-continuous reaction mechanism.
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
import heapq
import itertools
import math
from numbers import Real
from os import PathLike
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence

from reacnet_scope.network import Reaction, ReactionNetwork, count_atoms_fast


SCORE_VERSION = "candidate-path/v1"


class EvidenceProvider(Protocol):
    """Read-only source of batched event summaries for reaction keys."""

    @property
    def source_signatures(self) -> Mapping[str, Any]:
        """Describe the immutable evidence sources used for the summaries."""

    def reaction_summaries(
        self, reaction_keys: Sequence[str]
    ) -> Mapping[str, Mapping[str, Any]]:
        """Return evidence summaries keyed by reaction key."""


def _json_safe(value: Any) -> Any:
    """Convert immutable domain records into JSON-compatible primitives."""
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(_json_safe(key)): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Set):
        return sorted(
            (_json_safe(item) for item in value),
            key=lambda item: repr(item),
        )
    if isinstance(value, PathLike):
        return str(value)
    return value


def _validate_unit_metric(name: str, value: float) -> None:
    if (
        not isinstance(value, Real)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{name} must be a finite number in [0, 1]")


def score_step(
    *,
    net_share: float,
    directionality: float,
    event_coverage: float | None,
    time_coverage: float | None,
) -> tuple[float, Literal["evidence_linked", "network_only"]]:
    """Score one candidate step using the version-one weighting contract."""
    _validate_unit_metric("net_share", net_share)
    _validate_unit_metric("directionality", directionality)

    if (event_coverage is None) != (time_coverage is None):
        raise ValueError(
            "event evidence coverage and time coverage must both be supplied or both be None"
        )
    if event_coverage is None:
        return (
            (0.40 * net_share + 0.25 * directionality) / 0.65,
            "network_only",
        )

    _validate_unit_metric("event_coverage", event_coverage)
    _validate_unit_metric("time_coverage", time_coverage)
    return (
        0.40 * net_share
        + 0.25 * directionality
        + 0.20 * event_coverage
        + 0.15 * time_coverage,
        "evidence_linked",
    )


def score_path(step_scores: Iterable[float]) -> float:
    """Combine a path's geometric mean with its weakest step score."""
    scores = tuple(step_scores)
    if not scores:
        raise ValueError("step_scores must not be empty")
    for score in scores:
        _validate_unit_metric("step_scores", score)

    geometric_mean = math.prod(scores) ** (1 / len(scores))
    return 0.70 * geometric_mean + 0.30 * min(scores)


@dataclass(frozen=True)
class PathwayStep:
    reaction_key: str
    traversal_direction: Literal["downstream", "upstream"]
    focal_input: str
    focal_output: str
    reactants: tuple[str, ...]
    products: tuple[str, ...]
    forward_tp: int
    reverse_tp: int
    net_tp: int
    net_share: float
    directionality: float
    event_coverage: float | None
    time_coverage: float | None
    event_total: int | None
    matched_event_total: int | None
    distinct_intervals: int | None
    evidence_status: Literal["evidence_linked", "network_only"]
    source_references: tuple[str, ...]
    score: float
    score_version: str = field(default=SCORE_VERSION, init=False)

    def __post_init__(self) -> None:
        _, expected_evidence_status = score_step(
            net_share=self.net_share,
            directionality=self.directionality,
            event_coverage=self.event_coverage,
            time_coverage=self.time_coverage,
        )
        if self.evidence_status != expected_evidence_status:
            raise ValueError("evidence_status must match the supplied evidence coverage")
        _validate_event_counts(
            evidence_status=expected_evidence_status,
            event_total=self.event_total,
            matched_event_total=self.matched_event_total,
            distinct_intervals=self.distinct_intervals,
        )
        _validate_unit_metric("score", self.score)

    def as_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class CandidatePath:
    rank: int
    species: tuple[str, ...]
    steps: tuple[PathwayStep, ...]
    score: float
    evidence_status: Literal["evidence_linked", "network_only"] = field(init=False)

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("steps must not be empty")
        statuses = {step.evidence_status for step in self.steps}
        if len(statuses) != 1:
            raise ValueError("mixed step evidence statuses are not supported")
        object.__setattr__(self, "evidence_status", statuses.pop())

    def as_dict(self) -> dict[str, Any]:
        payload = _json_safe(self)
        payload["score_version"] = SCORE_VERSION
        return payload


@dataclass(frozen=True)
class CandidatePathResult:
    paths: tuple[CandidatePath, ...]
    query: Mapping[str, Any]
    source_signatures: Mapping[str, Any]
    evidence_status: Literal["evidence_linked", "network_only"]
    reason: str
    truncated: bool
    expansions: int

    def __post_init__(self) -> None:
        if self.evidence_status not in {"evidence_linked", "network_only"}:
            raise ValueError(
                "evidence_status must be 'evidence_linked' or 'network_only'"
            )
        path_statuses = {path.evidence_status for path in self.paths}
        if path_statuses and path_statuses != {self.evidence_status}:
            raise ValueError(
                "path evidence statuses must match the query evidence status"
            )

    def as_dict(self) -> dict[str, Any]:
        payload = _json_safe(self)
        payload["paths"] = [path.as_dict() for path in self.paths]
        payload["score_version"] = SCORE_VERSION
        return payload


def _validate_event_counts(
    *,
    evidence_status: Literal["evidence_linked", "network_only"],
    event_total: int | None,
    matched_event_total: int | None,
    distinct_intervals: int | None,
) -> None:
    counts = (event_total, matched_event_total, distinct_intervals)
    if evidence_status == "network_only":
        if any(count is not None for count in counts):
            raise ValueError("network-only steps must not contain event counts")
        return
    if any(count is None for count in counts):
        raise ValueError("evidence-linked steps require all event counts")
    if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
        raise ValueError("event counts must be nonnegative integers")
    if matched_event_total > event_total:
        raise ValueError("matched event count must not exceed total event count")


@dataclass(frozen=True)
class _SearchState:
    species: tuple[str, ...]
    steps: tuple[PathwayStep, ...]
    step_scores: tuple[float, ...]
    score: float | None


def find_candidate_paths(
    network: ReactionNetwork,
    start_smiles: str,
    *,
    direction: Literal["downstream", "upstream"] = "downstream",
    max_depth: int = 3,
    max_branches: int = 5,
    max_paths: int = 20,
    max_expansions: int = 5000,
    min_net_tp: int = 1,
    min_directionality: float = 0.05,
    target_max_carbon: int | None = None,
    evidence_provider: EvidenceProvider | None = None,
) -> CandidatePathResult:
    """Enumerate bounded, loopless candidate routes without implying proof.

    Search is best-first. Raw partial scores order the queue but are never used
    as completion bounds. Exact top-N completion is proved only from a queued
    state's best possible score after filling every remaining step with 1.0.
    """
    _validate_query(
        direction=direction,
        max_depth=max_depth,
        max_branches=max_branches,
        max_paths=max_paths,
        max_expansions=max_expansions,
        min_net_tp=min_net_tp,
        min_directionality=min_directionality,
        target_max_carbon=target_max_carbon,
    )
    query = {
        "start_smiles": start_smiles,
        "direction": direction,
        "max_depth": max_depth,
        "max_branches": max_branches,
        "max_paths": max_paths,
        "max_expansions": max_expansions,
        "min_net_tp": min_net_tp,
        "min_directionality": min_directionality,
        "score_version": SCORE_VERSION,
        "interpretation": "candidate route, not mechanistic proof",
    }
    if target_max_carbon is not None:
        query["target_max_carbon"] = target_max_carbon

    evidence_status: Literal["evidence_linked", "network_only"] = (
        "evidence_linked"
        if evidence_provider is not None
        else "network_only"
    )
    evidence_summaries, source_signatures = _prefetch_evidence(
        network,
        evidence_provider,
    )
    if start_smiles not in network.species:
        return CandidatePathResult(
            paths=(),
            query=query,
            source_signatures=source_signatures,
            evidence_status=evidence_status,
            reason="species_absent",
            truncated=False,
            expansions=0,
        )
    if _matches_carbon_target(start_smiles, target_max_carbon):
        return CandidatePathResult(
            paths=(),
            query=query,
            source_signatures=source_signatures,
            evidence_status=evidence_status,
            reason="target_already_reached",
            truncated=False,
            expansions=0,
        )

    sequence = itertools.count()
    root = _SearchState(
        species=(start_smiles,),
        steps=(),
        step_scores=(),
        score=None,
    )
    queue: list[
        tuple[
            float,
            tuple[str, ...],
            tuple[str, ...],
            int,
            _SearchState,
        ]
    ] = [
        (
            -1.0,
            root.species,
            (),
            next(sequence),
            root,
        )
    ]
    completed: list[_SearchState] = []
    expansions = 0
    root_had_positive = False
    root_had_fresh_continuation = False
    root_had_threshold_match = False
    stopped_at_expansion_cap = False

    while queue:
        if _can_stop_with_exact_top_n(
            completed=completed,
            queue=queue,
            max_paths=max_paths,
            max_depth=max_depth,
        ):
            break

        priority = heapq.heappop(queue)
        state = priority[-1]
        if state.steps and _matches_carbon_target(
            state.species[-1], target_max_carbon
        ):
            completed.append(state)
            continue
        if len(state.steps) >= max_depth:
            if target_max_carbon is None:
                completed.append(state)
            continue

        if expansions >= max_expansions:
            heapq.heappush(queue, priority)
            stopped_at_expansion_cap = True
            break

        # Every popped nonterminal state consumes an expansion, even when all
        # of its candidate branches are later pruned.
        expansions += 1
        (
            next_steps,
            had_positive,
            had_fresh_continuation,
            had_threshold_match,
        ) = _candidate_steps(
            network=network,
            focal_species=state.species[-1],
            visited_species=frozenset(state.species),
            direction=direction,
            min_net_tp=min_net_tp,
            min_directionality=min_directionality,
            evidence_summaries=evidence_summaries,
            evidence_available=evidence_provider is not None,
        )
        if not state.steps:
            root_had_positive = had_positive
            root_had_fresh_continuation = had_fresh_continuation
            root_had_threshold_match = had_threshold_match
        if not next_steps:
            if state.steps and target_max_carbon is None:
                completed.append(state)
            continue

        children: list[_SearchState] = []
        for step in next_steps:
            child_step_scores = (*state.step_scores, step.score)
            child = _SearchState(
                species=(*state.species, step.focal_output),
                steps=(*state.steps, step),
                step_scores=child_step_scores,
                score=score_path(child_step_scores),
            )
            children.append(child)

        if target_max_carbon is None:
            children.sort(key=_state_sort_key)
        else:
            # A goal-directed fragmentation search must not lose a direct
            # C1-Cn product merely because high-frequency isomerizations fill
            # the per-expansion branch cap. Scores still order the retained
            # goal paths; this key only decides which branches are explored.
            children.sort(
                key=lambda child: _target_branch_sort_key(
                    child, target_max_carbon
                )
            )
        for child in children[:max_branches]:
            heapq.heappush(
                queue,
                (
                    -_required_state_score(child),
                    child.species,
                    tuple(step.reaction_key for step in child.steps),
                    next(sequence),
                    child,
                ),
            )

    truncated = stopped_at_expansion_cap
    result_states = completed
    if truncated and target_max_carbon is None:
        result_states = [
            *completed,
            *(item[-1] for item in queue if item[-1].steps),
        ]
    ordered_states = sorted(result_states, key=_state_sort_key)[:max_paths]
    paths = tuple(
        CandidatePath(
            rank=rank,
            species=state.species,
            steps=state.steps,
            score=_required_state_score(state),
        )
        for rank, state in enumerate(ordered_states, start=1)
    )

    if paths:
        reason = "ok"
    elif target_max_carbon is not None:
        reason = "target_not_reached"
    elif not root_had_positive or not root_had_fresh_continuation:
        reason = "no_positive_net_continuation"
    elif not root_had_threshold_match:
        reason = "filtered_by_thresholds"
    else:
        reason = "no_positive_net_continuation"
    return CandidatePathResult(
        paths=paths,
        query=query,
        source_signatures=source_signatures,
        evidence_status=evidence_status,
        reason=reason,
        truncated=truncated,
        expansions=expansions,
    )


def _validate_query(
    *,
    direction: str,
    max_depth: int,
    max_branches: int,
    max_paths: int,
    max_expansions: int,
    min_net_tp: int,
    min_directionality: float,
    target_max_carbon: int | None,
) -> None:
    if direction not in {"downstream", "upstream"}:
        raise ValueError("direction must be 'downstream' or 'upstream'")
    _validate_int_bound("max_depth", max_depth, 1, 12)
    _validate_int_bound("max_branches", max_branches, 1, 100)
    _validate_int_bound("max_paths", max_paths, 1, 500)
    _validate_int_bound("max_expansions", max_expansions, 1, 1_000_000)
    _validate_int_bound("min_net_tp", min_net_tp, 1, None)
    _validate_unit_metric("min_directionality", min_directionality)
    if target_max_carbon is not None:
        _validate_int_bound("target_max_carbon", target_max_carbon, 1, 100)


def _validate_int_bound(
    name: str,
    value: int,
    minimum: int,
    maximum: int | None,
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be >= {minimum}{upper}")


def _prefetch_evidence(
    network: ReactionNetwork,
    evidence_provider: EvidenceProvider | None,
) -> tuple[Mapping[str, Mapping[str, Any]], Mapping[str, Any]]:
    if evidence_provider is None:
        return {}, {}
    reaction_keys = tuple(sorted(reaction.key for reaction in network.reactions))
    summaries = evidence_provider.reaction_summaries(reaction_keys)
    signatures: Any = getattr(evidence_provider, "source_signatures", {})
    if callable(signatures):
        signatures = signatures()
    if not isinstance(signatures, Mapping):
        raise ValueError("evidence provider source_signatures must be a mapping")
    return summaries, signatures


def _can_stop_with_exact_top_n(
    *,
    completed: Sequence[_SearchState],
    queue: Sequence[
        tuple[
            float,
            tuple[str, ...],
            tuple[str, ...],
            int,
            _SearchState,
        ]
    ],
    max_paths: int,
    max_depth: int,
) -> bool:
    if len(completed) < max_paths or not queue:
        return False
    retained = sorted(completed, key=_state_sort_key)[:max_paths]
    worst_retained_score = _required_state_score(retained[-1])
    best_queued_bound = max(
        _state_completion_upper_bound(item[-1], max_depth)
        for item in queue
    )
    return best_queued_bound < worst_retained_score


def _state_completion_upper_bound(
    state: _SearchState,
    max_depth: int,
) -> float:
    remaining_depth = max_depth - len(state.step_scores)
    if not state.step_scores:
        return 1.0
    return score_path((*state.step_scores, *((1.0,) * remaining_depth)))


def _candidate_steps(
    *,
    network: ReactionNetwork,
    focal_species: str,
    visited_species: frozenset[str],
    direction: Literal["downstream", "upstream"],
    min_net_tp: int,
    min_directionality: float,
    evidence_summaries: Mapping[str, Mapping[str, Any]],
    evidence_available: bool,
) -> tuple[list[PathwayStep], bool, bool, bool]:
    """Build eligible steps and report each candidate-filtering stage.

    The booleans distinguish a positive-net reaction, a positive-net branch
    to an unvisited species, and a fresh branch surviving query thresholds.
    Net shares use the raw positive branch population before any of those
    branch-level filters, including repeated stoichiometric terms.
    """
    if direction == "downstream":
        indexed_reactions = network.consume_idx.get(focal_species, ())
    else:
        indexed_reactions = network.produce_idx.get(focal_species, ())

    reactions_by_key: dict[str, Reaction] = {}
    for reaction in indexed_reactions:
        reactions_by_key.setdefault(reaction.key, reaction)

    raw_positive_candidates: list[
        tuple[Reaction, str, int, int, int, float]
    ] = []
    had_positive = False
    for reaction_key in sorted(reactions_by_key):
        reaction = reactions_by_key[reaction_key]
        forward_tp, reverse_tp, signed_net, _ = network.net_flux(reaction)
        if signed_net <= 0:
            continue
        had_positive = True
        directionality = signed_net / forward_tp if forward_tp > 0 else 0.0
        outputs = (
            reaction.product_smiles
            if direction == "downstream"
            else reaction.reactant_smiles
        )
        for focal_output in sorted(outputs):
            raw_positive_candidates.append(
                (
                    reaction,
                    focal_output,
                    forward_tp,
                    reverse_tp,
                    signed_net,
                    directionality,
                )
            )

    fresh_candidates = [
        candidate
        for candidate in raw_positive_candidates
        if candidate[1] not in visited_species
    ]
    threshold_candidates = [
        candidate
        for candidate in fresh_candidates
        if candidate[4] >= min_net_tp
        and candidate[5] >= min_directionality
    ]
    if not threshold_candidates:
        return [], had_positive, bool(fresh_candidates), False

    net_total = sum(candidate[4] for candidate in raw_positive_candidates)
    steps = [
        _build_step(
            reaction=reaction,
            direction=direction,
            focal_input=focal_species,
            focal_output=focal_output,
            forward_tp=forward_tp,
            reverse_tp=reverse_tp,
            net_tp=net_tp,
            net_share=net_tp / net_total,
            directionality=directionality,
            evidence_summary=evidence_summaries.get(reaction.key),
            evidence_available=evidence_available,
        )
        for (
            reaction,
            focal_output,
            forward_tp,
            reverse_tp,
            net_tp,
            directionality,
        ) in threshold_candidates
    ]
    return steps, had_positive, True, True


def _build_step(
    *,
    reaction: Reaction,
    direction: Literal["downstream", "upstream"],
    focal_input: str,
    focal_output: str,
    forward_tp: int,
    reverse_tp: int,
    net_tp: int,
    net_share: float,
    directionality: float,
    evidence_summary: Mapping[str, Any] | None,
    evidence_available: bool,
) -> PathwayStep:
    if evidence_available:
        summary = evidence_summary or {}
        event_total = _summary_count(summary, "total_events", "event_total")
        matched_event_total = _summary_count(
            summary,
            "matched_events",
            "matched_event_total",
        )
        distinct_intervals = _summary_count(summary, "distinct_intervals")
        available_intervals = _summary_count(summary, "available_intervals")
        event_coverage = (
            matched_event_total / event_total if event_total else 0.0
        )
        time_coverage = (
            distinct_intervals / available_intervals
            if available_intervals
            else 0.0
        )
        source_references = tuple(
            str(reference)
            for reference in summary.get("source_references", ())
        )
    else:
        event_total = None
        matched_event_total = None
        distinct_intervals = None
        event_coverage = None
        time_coverage = None
        source_references = ()

    step_score, evidence_status = score_step(
        net_share=net_share,
        directionality=directionality,
        event_coverage=event_coverage,
        time_coverage=time_coverage,
    )
    return PathwayStep(
        reaction_key=reaction.key,
        traversal_direction=direction,
        focal_input=focal_input,
        focal_output=focal_output,
        reactants=reaction.reactant_smiles,
        products=reaction.product_smiles,
        forward_tp=forward_tp,
        reverse_tp=reverse_tp,
        net_tp=net_tp,
        net_share=net_share,
        directionality=directionality,
        event_coverage=event_coverage,
        time_coverage=time_coverage,
        event_total=event_total,
        matched_event_total=matched_event_total,
        distinct_intervals=distinct_intervals,
        evidence_status=evidence_status,
        source_references=source_references,
        score=step_score,
    )


def _summary_count(
    summary: Mapping[str, Any],
    key: str,
    fallback_key: str | None = None,
) -> int:
    value = summary.get(key)
    if value is None and fallback_key is not None:
        value = summary.get(fallback_key)
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"evidence summary {key} must be a nonnegative integer")
    return value


def _required_state_score(state: _SearchState) -> float:
    if state.score is None:
        raise ValueError("a candidate path must contain at least one step")
    return state.score


def _state_sort_key(
    state: _SearchState,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    return (
        -_required_state_score(state),
        state.species,
        tuple(step.reaction_key for step in state.steps),
    )


def _carbon_count(smiles: str) -> int:
    """Return the explicit carbon count used by RNG-style SMILES."""
    return int(count_atoms_fast(smiles).get("C", 0))


def _matches_carbon_target(
    smiles: str,
    target_max_carbon: int | None,
) -> bool:
    if target_max_carbon is None:
        return False
    carbon_count = _carbon_count(smiles)
    return 0 < carbon_count <= target_max_carbon


def _target_branch_sort_key(
    state: _SearchState,
    target_max_carbon: int,
) -> tuple[
    int,
    int,
    float,
    tuple[str, ...],
    tuple[str, ...],
]:
    carbon_count = _carbon_count(state.species[-1])
    return (
        0 if _matches_carbon_target(
            state.species[-1], target_max_carbon
        ) else 1,
        carbon_count if carbon_count > 0 else 10**9,
        -_required_state_score(state),
        state.species,
        tuple(step.reaction_key for step in state.steps),
    )
