from __future__ import annotations

import json
import math
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType

import pytest

from rng_tools.pathways import (
    CandidatePath,
    CandidatePathResult,
    PathwayStep,
    find_candidate_paths,
    score_path,
    score_step,
)
from rng_tools.network import Reaction, ReactionNetwork


def test_evidence_linked_step_score_uses_v1_weights() -> None:
    score, status = score_step(
        net_share=0.50,
        directionality=0.80,
        event_coverage=0.75,
        time_coverage=0.10,
    )

    assert score == pytest.approx(
        0.40 * 0.50 + 0.25 * 0.80 + 0.20 * 0.75 + 0.15 * 0.10
    )
    assert status == "evidence_linked"


def test_network_only_score_renormalizes_available_terms() -> None:
    score, status = score_step(
        net_share=0.50,
        directionality=0.80,
        event_coverage=None,
        time_coverage=None,
    )

    assert score == pytest.approx((0.40 * 0.50 + 0.25 * 0.80) / 0.65)
    assert status == "network_only"


def test_path_score_combines_geometric_mean_and_weakest_step() -> None:
    assert score_path([0.81, 0.49]) == pytest.approx(
        0.70 * math.sqrt(0.81 * 0.49) + 0.30 * 0.49
    )


def test_pathway_serialization_retains_stoichiometry_and_raw_metrics() -> None:
    step = PathwayStep(
        reaction_key="A+A->B",
        traversal_direction="downstream",
        focal_input="A",
        focal_output="B",
        reactants=("A", "A"),
        products=("B",),
        forward_tp=11,
        reverse_tp=2,
        net_tp=9,
        net_share=0.123456789,
        directionality=9 / 11,
        event_coverage=0.75,
        time_coverage=0.125,
        event_total=4,
        matched_event_total=3,
        distinct_intervals=2,
        evidence_status="evidence_linked",
        source_references=("events.sqlite3", "run.reactionabcd"),
        score=0.456789123,
    )
    path = CandidatePath(
        rank=1,
        species=("A", "B"),
        steps=(step,),
        score=0.456789123,
    )
    result = CandidatePathResult(
        paths=(path,),
        query={"max_paths": 20, "score_version": "candidate-path/v1"},
        source_signatures={"reactionabcd": {"mtime_ns": 123}},
        evidence_status="evidence_linked",
        reason="ok",
        truncated=False,
        expansions=1,
    )

    payload = result.as_dict()

    serialized_step = payload["paths"][0]["steps"][0]
    assert serialized_step["reactants"] == ["A", "A"]
    assert serialized_step["net_share"] == 0.123456789
    assert serialized_step["source_references"] == [
        "events.sqlite3",
        "run.reactionabcd",
    ]
    assert serialized_step["score_version"] == "candidate-path/v1"
    assert payload["query"]["score_version"] == "candidate-path/v1"
    assert payload["source_signatures"] == {"reactionabcd": {"mtime_ns": 123}}


@pytest.mark.parametrize("metric", [-0.01, 1.01])
def test_score_step_rejects_metrics_outside_unit_interval(metric: float) -> None:
    with pytest.raises(ValueError, match="net_share"):
        score_step(
            net_share=metric,
            directionality=0.5,
            event_coverage=None,
            time_coverage=None,
        )


def test_score_step_rejects_partially_available_evidence() -> None:
    with pytest.raises(ValueError, match="both"):
        score_step(
            net_share=0.5,
            directionality=0.5,
            event_coverage=0.5,
            time_coverage=None,
        )


def test_score_path_rejects_empty_or_invalid_scores() -> None:
    with pytest.raises(ValueError, match="empty"):
        score_path([])
    with pytest.raises(ValueError, match="step_scores"):
        score_path([1.1])


@pytest.mark.parametrize(
    ("event_coverage", "time_coverage", "evidence_status"),
    [
        (0.75, None, "evidence_linked"),
        (None, None, "evidence_linked"),
        (0.75, 0.125, "network_only"),
    ],
)
def test_pathway_step_rejects_inconsistent_evidence_state(
    event_coverage: float | None,
    time_coverage: float | None,
    evidence_status: str,
) -> None:
    with pytest.raises(ValueError, match="evidence"):
        PathwayStep(
            reaction_key="A->B",
            traversal_direction="downstream",
            focal_input="A",
            focal_output="B",
            reactants=("A",),
            products=("B",),
            forward_tp=1,
            reverse_tp=0,
            net_tp=1,
            net_share=1.0,
            directionality=1.0,
            event_coverage=event_coverage,
            time_coverage=time_coverage,
            event_total=None,
            matched_event_total=None,
            distinct_intervals=None,
            evidence_status=evidence_status,  # type: ignore[arg-type]
            source_references=(),
            score=1.0,
        )


def test_pathway_result_serializes_nested_sets_and_paths_deterministically() -> None:
    result = CandidatePathResult(
        paths=(),
        query={
            "nested": MappingProxyType(
                {"reaction_keys": {"B->C", "A->B"}, "event_file": Path("events.sqlite3")}
            )
        },
        source_signatures={"paths": {Path("source")}},
        evidence_status="network_only",
        reason="no candidates",
        truncated=False,
        expansions=0,
    )

    payload = result.as_dict()

    assert payload["query"]["nested"] == {
        "reaction_keys": ["A->B", "B->C"],
        "event_file": "events.sqlite3",
    }
    assert payload["source_signatures"] == {"paths": ["source"]}
    assert json.dumps(payload, sort_keys=True) == json.dumps(result.as_dict(), sort_keys=True)


def test_pathway_step_score_version_is_not_caller_overridable() -> None:
    with pytest.raises(TypeError, match="score_version"):
        PathwayStep(
            reaction_key="A->B",
            traversal_direction="downstream",
            focal_input="A",
            focal_output="B",
            reactants=("A",),
            products=("B",),
            forward_tp=1,
            reverse_tp=0,
            net_tp=1,
            net_share=1.0,
            directionality=1.0,
            event_coverage=None,
            time_coverage=None,
            event_total=None,
            matched_event_total=None,
            distinct_intervals=None,
            evidence_status="network_only",
            source_references=(),
            score=1.0,
            score_version="caller-version",
        )


def test_candidate_path_derives_evidence_status_from_its_steps() -> None:
    step = _step(evidence_status="evidence_linked")

    path = CandidatePath(
        rank=1,
        species=("A", "B"),
        steps=(step,),
        score=step.score,
    )

    assert path.evidence_status == "evidence_linked"
    assert path.as_dict()["evidence_status"] == "evidence_linked"


def test_candidate_path_does_not_accept_a_caller_supplied_evidence_status() -> None:
    with pytest.raises(TypeError, match="evidence_status"):
        CandidatePath(
            rank=1,
            species=("A", "B"),
            steps=(_step(evidence_status="network_only"),),
            score=1.0,
            evidence_status="evidence_linked",  # type: ignore[call-arg]
        )


def test_candidate_path_rejects_mixed_step_evidence_statuses() -> None:
    with pytest.raises(ValueError, match="mixed"):
        CandidatePath(
            rank=1,
            species=("A", "B", "C"),
            steps=(
                _step(evidence_status="network_only"),
                _step(evidence_status="evidence_linked"),
            ),
            score=1.0,
        )


def test_candidate_path_rejects_an_empty_step_sequence() -> None:
    with pytest.raises(ValueError, match="steps"):
        CandidatePath(
            rank=1,
            species=("A",),
            steps=(),
            score=1.0,
        )


@pytest.mark.parametrize(
    ("event_total", "matched_event_total", "distinct_intervals"),
    [
        (None, 1, 1),
        (1, None, 1),
        (1, 1, None),
        (-1, 0, 0),
        (1, -1, 0),
        (1, 0, -1),
        (1, 2, 1),
    ],
)
def test_evidence_linked_step_rejects_incomplete_or_invalid_counts(
    event_total: int | None,
    matched_event_total: int | None,
    distinct_intervals: int | None,
) -> None:
    with pytest.raises(ValueError, match="event"):
        _step(
            evidence_status="evidence_linked",
            event_total=event_total,
            matched_event_total=matched_event_total,
            distinct_intervals=distinct_intervals,
        )


@pytest.mark.parametrize(
    ("event_total", "matched_event_total", "distinct_intervals"),
    [(0, None, None), (None, 0, None), (None, None, 0)],
)
def test_network_only_step_rejects_any_event_counts(
    event_total: int | None,
    matched_event_total: int | None,
    distinct_intervals: int | None,
) -> None:
    with pytest.raises(ValueError, match="event"):
        _step(
            evidence_status="network_only",
            event_total=event_total,
            matched_event_total=matched_event_total,
            distinct_intervals=distinct_intervals,
        )


class _SerializationState(Enum):
    LINKED = {"value": Decimal("1.2300")}


def test_pathway_result_serializes_decimals_and_enums_without_rounding() -> None:
    result = CandidatePathResult(
        paths=(),
        query={"threshold": Decimal("0.12345678901234567890")},
        source_signatures={"status": _SerializationState.LINKED},
        evidence_status="network_only",
        reason="no candidates",
        truncated=False,
        expansions=0,
    )

    payload = result.as_dict()

    assert payload["query"]["threshold"] == "0.12345678901234567890"
    assert payload["source_signatures"] == {"status": {"value": "1.2300"}}
    json.dumps(payload)


def test_hyperedge_branches_retain_all_stoichiometric_terms() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A", "X"), ("B", "C"), 20),
            Reaction(("B",), ("D",), 10),
            Reaction(("C",), ("E",), 8),
        ]
    )

    result = find_candidate_paths(net, "A", max_depth=2, max_paths=10)

    first_steps = [path.steps[0] for path in result.paths]
    assert {step.focal_output for step in first_steps} >= {"B", "C"}
    assert all(step.reactants == ("A", "X") for step in first_steps)
    assert all(step.products == ("B", "C") for step in first_steps)


def test_upstream_traversal_is_symmetric_and_retains_recorded_orientation() -> None:
    net = ReactionNetwork([Reaction(("A", "X"), ("B", "C"), 20)])

    result = find_candidate_paths(
        net,
        "C",
        direction="upstream",
        max_depth=1,
        max_paths=10,
    )

    assert [path.species for path in result.paths] == [
        ("C", "A"),
        ("C", "X"),
    ]
    for path in result.paths:
        step = path.steps[0]
        assert step.traversal_direction == "upstream"
        assert step.reactants == ("A", "X")
        assert step.products == ("B", "C")
        assert step.focal_input == "C"


def test_candidate_paths_never_revisit_a_focal_species() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B", "X"), ("A",), 7),
        ]
    )

    result = find_candidate_paths(net, "A", max_depth=3)

    assert [path.species for path in result.paths] == [("A", "B")]
    assert all(len(path.species) == len(set(path.species)) for path in result.paths)


def test_reverse_dominated_and_below_directionality_branches_are_filtered() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 4),
            Reaction(("B",), ("A",), 9),
            Reaction(("A",), ("C",), 100),
            Reaction(("C",), ("A",), 96),
            Reaction(("A",), ("D",), 10),
        ]
    )

    result = find_candidate_paths(net, "A", max_depth=1)

    assert [path.species for path in result.paths] == [("A", "D")]


def test_max_branches_is_applied_after_deterministic_ordering() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("Z",), 10),
            Reaction(("A",), ("B",), 10),
            Reaction(("A",), ("C",), 20),
        ]
    )

    result = find_candidate_paths(net, "A", max_depth=1, max_branches=2)

    assert [path.species for path in result.paths] == [
        ("A", "C"),
        ("A", "B"),
    ]


def test_paths_sort_by_score_species_and_reaction_keys() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A", "Y"), ("B",), 10),
            Reaction(("A", "X"), ("B",), 10),
            Reaction(("A",), ("C",), 20),
        ]
    )

    result = find_candidate_paths(net, "A", max_depth=1)

    assert [path.score for path in result.paths] == sorted(
        (path.score for path in result.paths),
        reverse=True,
    )
    tied = [path for path in result.paths if path.species == ("A", "B")]
    assert [path.steps[0].reaction_key for path in tied] == [
        "A+X->B",
        "A+Y->B",
    ]


def test_max_expansions_returns_deterministic_partial_results() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("A",), ("C",), 10),
            Reaction(("B",), ("D",), 10),
            Reaction(("C",), ("E",), 10),
        ]
    )

    first = find_candidate_paths(net, "A", max_depth=3, max_expansions=1)
    second = find_candidate_paths(net, "A", max_depth=3, max_expansions=1)

    assert first == second
    assert [path.species for path in first.paths] == [("A", "B"), ("A", "C")]
    assert first.expansions == 1
    assert first.truncated is True


def test_absent_start_has_specific_reason() -> None:
    net = ReactionNetwork([Reaction(("A",), ("B",), 10)])

    result = find_candidate_paths(net, "missing")

    assert result.paths == ()
    assert result.reason == "species_absent"
    assert result.expansions == 0


def test_present_start_without_positive_net_continuation_has_specific_reason() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 5),
            Reaction(("B",), ("A",), 10),
        ]
    )

    result = find_candidate_paths(net, "A")

    assert result.paths == ()
    assert result.reason == "no_positive_net_continuation"


def test_threshold_removal_has_specific_reason() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B",), ("A",), 9),
        ]
    )

    result = find_candidate_paths(net, "A", min_directionality=0.2)

    assert result.paths == ()
    assert result.reason == "filtered_by_thresholds"


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("max_depth", 0),
        ("max_depth", 13),
        ("max_branches", 0),
        ("max_branches", 101),
        ("max_paths", 0),
        ("max_paths", 501),
        ("max_expansions", 0),
        ("max_expansions", 1_000_001),
        ("min_net_tp", 0),
        ("min_directionality", -0.01),
        ("min_directionality", 1.01),
    ],
)
def test_candidate_path_query_rejects_out_of_bounds_limits(
    keyword: str,
    value: int | float,
) -> None:
    net = ReactionNetwork([Reaction(("A",), ("B",), 10)])

    with pytest.raises(ValueError, match=keyword):
        find_candidate_paths(net, "A", **{keyword: value})


def test_candidate_path_query_rejects_an_unknown_direction() -> None:
    net = ReactionNetwork([Reaction(("A",), ("B",), 10)])

    with pytest.raises(ValueError, match="direction"):
        find_candidate_paths(net, "A", direction="sideways")


def test_result_query_records_all_limits_and_candidate_disclaimer() -> None:
    net = ReactionNetwork([Reaction(("A",), ("B",), 10)])

    result = find_candidate_paths(net, "A")
    payload = result.as_dict()

    assert payload["query"] == {
        "start_smiles": "A",
        "direction": "downstream",
        "max_depth": 3,
        "max_branches": 5,
        "max_paths": 20,
        "max_expansions": 5000,
        "min_net_tp": 1,
        "min_directionality": 0.05,
        "score_version": "candidate-path/v1",
        "interpretation": "candidate route, not mechanistic proof",
    }
    assert payload["source_signatures"] == {}
    assert payload["evidence_status"] == "network_only"
    assert payload["score_version"] == "candidate-path/v1"


class _RecordingEvidenceProvider:
    source_signatures = {"event_index": {"signature": "fixture"}}

    def __init__(self, summaries: dict[str, dict[str, object]]) -> None:
        self.summaries = summaries
        self.calls: list[tuple[str, ...]] = []

    def reaction_summaries(
        self,
        reaction_keys: tuple[str, ...],
    ) -> dict[str, dict[str, object]]:
        self.calls.append(reaction_keys)
        return self.summaries


@pytest.mark.parametrize(
    ("network", "start_smiles", "limits", "reason"),
    [
        (
            ReactionNetwork([Reaction(("A",), ("B",), 10)]),
            "missing",
            {},
            "species_absent",
        ),
        (
            ReactionNetwork(
                [
                    Reaction(("A",), ("B",), 5),
                    Reaction(("B",), ("A",), 10),
                ]
            ),
            "A",
            {},
            "no_positive_net_continuation",
        ),
        (
            ReactionNetwork(
                [
                    Reaction(("A",), ("B",), 10),
                    Reaction(("B",), ("A",), 9),
                ]
            ),
            "A",
            {"min_directionality": 0.2},
            "filtered_by_thresholds",
        ),
    ],
)
def test_ready_evidence_provider_defines_query_status_for_empty_results(
    network: ReactionNetwork,
    start_smiles: str,
    limits: dict[str, object],
    reason: str,
) -> None:
    provider = _RecordingEvidenceProvider({})

    result = find_candidate_paths(
        network,
        start_smiles,
        evidence_provider=provider,
        **limits,
    )
    payload = result.as_dict()

    assert payload["paths"] == []
    assert payload["reason"] == reason
    assert payload["evidence_status"] == "evidence_linked"
    assert payload["source_signatures"] == provider.source_signatures
    assert provider.calls == [
        tuple(sorted(reaction.key for reaction in network.reactions))
    ]


def test_unavailable_evidence_defines_network_only_status_for_empty_result() -> None:
    result = find_candidate_paths(
        ReactionNetwork([Reaction(("A",), ("B",), 10)]),
        "missing",
    )

    payload = result.as_dict()
    assert payload["paths"] == []
    assert payload["evidence_status"] == "network_only"
    assert payload["source_signatures"] == {}


def test_evidence_is_prefetched_once_and_applied_without_expansion_io() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B",), ("C",), 8),
        ]
    )
    provider = _RecordingEvidenceProvider(
        {
            "A->B": {
                "total_events": 4,
                "matched_events": 3,
                "distinct_intervals": 2,
                "available_intervals": 10,
                "source_references": ("events.sqlite3",),
            },
            "B->C": {
                "total_events": 2,
                "matched_events": 2,
                "distinct_intervals": 1,
                "available_intervals": 10,
                "source_references": ("events.sqlite3",),
            },
        }
    )

    result = find_candidate_paths(
        net,
        "A",
        max_depth=2,
        evidence_provider=provider,
    )

    assert provider.calls == [("A->B", "B->C")]
    assert result.source_signatures == provider.source_signatures
    assert result.paths[0].evidence_status == "evidence_linked"
    assert result.paths[0].steps[0].event_coverage == pytest.approx(3 / 4)
    assert result.paths[0].steps[0].time_coverage == pytest.approx(2 / 10)


def test_search_does_not_treat_partial_score_as_an_upper_bound() -> None:
    reactions = [
        Reaction(("A",), ("C",), 10),
        Reaction(("A",), ("B",), 10),
        Reaction(("C",), ("X",), 10),
        Reaction(("X",), ("C",), 4),
        Reaction(("C",), ("Y",), 10),
        Reaction(("Y",), ("C",), 4),
        Reaction(("B",), ("Z",), 10),
    ]
    provider = _RecordingEvidenceProvider(
        {
            reaction.key: {
                "total_events": 1,
                "matched_events": int(reaction.key in {"A->C", "B->Z"}),
                "distinct_intervals": int(reaction.key in {"A->C", "B->Z"}),
                "available_intervals": 1,
            }
            for reaction in reactions
        }
    )

    result = find_candidate_paths(
        ReactionNetwork(reactions),
        "A",
        max_depth=2,
        max_paths=1,
        max_branches=5,
        evidence_provider=provider,
    )

    assert result.paths[0].species == ("A", "B", "Z")


def test_net_share_is_invariant_to_threshold_pruning() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("A",), ("C",), 10),
            Reaction(("C",), ("A",), 9),
        ]
    )

    unfiltered = find_candidate_paths(net, "A", max_depth=1, min_net_tp=1)
    filtered = find_candidate_paths(net, "A", max_depth=1, min_net_tp=2)

    unfiltered_b = next(
        path.steps[0] for path in unfiltered.paths if path.species == ("A", "B")
    )
    assert filtered.paths[0].steps[0].net_share == pytest.approx(
        unfiltered_b.net_share
    )
    assert filtered.paths[0].steps[0].net_share == pytest.approx(10 / 11)


def test_net_share_is_invariant_to_branch_cap_pruning() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("A",), ("C",), 5),
        ]
    )

    uncapped = find_candidate_paths(net, "A", max_depth=1, max_branches=2)
    capped = find_candidate_paths(net, "A", max_depth=1, max_branches=1)

    uncapped_b = next(
        path.steps[0] for path in uncapped.paths if path.species == ("A", "B")
    )
    assert capped.paths[0].steps[0].net_share == pytest.approx(
        uncapped_b.net_share
    )
    assert capped.paths[0].steps[0].net_share == pytest.approx(2 / 3)


def test_net_share_is_computed_before_visited_species_pruning() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 20),
            Reaction(("B", "X"), ("A",), 10),
            Reaction(("B",), ("C",), 5),
        ]
    )

    result = find_candidate_paths(net, "A", max_depth=2)

    assert result.paths[0].species == ("A", "B", "C")
    assert result.paths[0].steps[1].net_share == pytest.approx(1 / 3)


def test_repeated_output_terms_retain_search_multiplicity() -> None:
    net = ReactionNetwork([Reaction(("A",), ("B", "B"), 10)])

    result = find_candidate_paths(net, "A", max_depth=1, max_paths=10)

    assert [path.species for path in result.paths] == [
        ("A", "B"),
        ("A", "B"),
    ]
    assert [path.steps[0].net_share for path in result.paths] == pytest.approx(
        [0.5, 0.5]
    )
    assert all(path.steps[0].products == ("B", "B") for path in result.paths)


def test_small_carbon_goal_prioritizes_direct_fragment_before_branch_cap() -> None:
    parent = "[C][C][C][C][C][C]"
    five_carbon = "[C][C][C][C][C]"
    carbon_monoxide = "[C]=[O]"
    net = ReactionNetwork(
        [
            Reaction((parent,), (five_carbon, carbon_monoxide), 1),
            Reaction((parent,), ("[C]1[C][C][C][C][C]1",), 100),
        ]
    )

    result = find_candidate_paths(
        net,
        parent,
        max_depth=2,
        max_branches=1,
        target_max_carbon=4,
    )

    assert result.reason == "ok"
    assert result.query["target_max_carbon"] == 4
    assert [path.species for path in result.paths] == [
        (parent, carbon_monoxide)
    ]
    assert result.paths[0].steps[0].products == (
        five_carbon,
        carbon_monoxide,
    )


def test_small_carbon_goal_does_not_return_non_goal_depth_limited_paths() -> None:
    net = ReactionNetwork(
        [
            Reaction(
                ("[C][C][C][C][C][C]",),
                ("[C][C][C][C][C]",),
                10,
            )
        ]
    )

    result = find_candidate_paths(
        net,
        "[C][C][C][C][C][C]",
        max_depth=1,
        target_max_carbon=4,
    )

    assert result.paths == ()
    assert result.reason == "target_not_reached"


def test_expansion_cap_precedes_further_candidate_evidence_validation() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B",), ("C",), 10),
        ]
    )
    provider = _RecordingEvidenceProvider(
        {
            "A->B": {
                "total_events": 1,
                "matched_events": 1,
                "distinct_intervals": 1,
                "available_intervals": 1,
            },
            "B->C": {
                "total_events": True,
            },
        }
    )

    result = find_candidate_paths(
        net,
        "A",
        max_depth=3,
        max_expansions=1,
        evidence_provider=provider,
    )

    assert [path.species for path in result.paths] == [("A", "B")]
    assert result.expansions == 1
    assert result.truncated is True


def test_expansion_cap_counts_loop_and_threshold_pruned_dead_ends_before_queued_evidence() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 20),
            Reaction(("A",), ("C",), 10),
            Reaction(("B", "X"), ("A",), 10),
            Reaction(("B",), ("E",), 1),
            Reaction(("C",), ("D",), 10),
        ]
    )
    provider = _RecordingEvidenceProvider(
        {
            "A->B": {
                "total_events": 1,
                "matched_events": 1,
                "distinct_intervals": 1,
                "available_intervals": 1,
            },
            "A->C": {
                "total_events": 1,
                "matched_events": 1,
                "distinct_intervals": 1,
                "available_intervals": 1,
            },
            "C->D": {"total_events": True},
        }
    )

    first = find_candidate_paths(
        net,
        "A",
        max_depth=3,
        max_expansions=2,
        min_net_tp=2,
        evidence_provider=provider,
    )
    second = find_candidate_paths(
        net,
        "A",
        max_depth=3,
        max_expansions=2,
        min_net_tp=2,
        evidence_provider=provider,
    )

    assert first == second
    assert [path.species for path in first.paths] == [("A", "B"), ("A", "C")]
    assert first.expansions == 2
    assert first.truncated is True


def test_safe_top_n_bound_stops_exact_search_before_expansion_cap() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 100),
            Reaction(("A",), ("C",), 1),
            Reaction(("B",), ("D",), 100),
            Reaction(("C",), ("E",), 1),
        ]
    )

    result = find_candidate_paths(
        net,
        "A",
        max_depth=2,
        max_paths=1,
        max_expansions=3,
    )

    assert [path.species for path in result.paths] == [("A", "B", "D")]
    assert result.expansions == 2
    assert result.truncated is False


def test_safe_top_n_bound_continues_on_equal_score_for_semantic_ties() -> None:
    net = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("A",), ("C",), 10),
            Reaction(("B",), ("D",), 10),
            Reaction(("C",), ("E",), 10),
        ]
    )

    result = find_candidate_paths(
        net,
        "A",
        max_depth=2,
        max_paths=1,
        max_expansions=3,
    )

    assert [path.species for path in result.paths] == [("A", "B", "D")]
    assert result.expansions == 3
    assert result.truncated is False


def test_positive_reaction_without_fresh_output_has_no_continuation_reason() -> None:
    net = ReactionNetwork([Reaction(("A", "X"), ("A",), 10)])

    result = find_candidate_paths(net, "A")

    assert result.paths == ()
    assert result.reason == "no_positive_net_continuation"


def test_candidate_path_query_rejects_boolean_directionality_bound() -> None:
    net = ReactionNetwork([Reaction(("A",), ("B",), 10)])

    with pytest.raises(ValueError, match="min_directionality"):
        find_candidate_paths(net, "A", min_directionality=True)


def _step(
    *,
    evidence_status: str,
    event_total: int | None | object = ...,
    matched_event_total: int | None | object = ...,
    distinct_intervals: int | None | object = ...,
) -> PathwayStep:
    evidence_linked = evidence_status == "evidence_linked"
    return PathwayStep(
        reaction_key="A->B",
        traversal_direction="downstream",
        focal_input="A",
        focal_output="B",
        reactants=("A",),
        products=("B",),
        forward_tp=1,
        reverse_tp=0,
        net_tp=1,
        net_share=1.0,
        directionality=1.0,
        event_coverage=1.0 if evidence_linked else None,
        time_coverage=1.0 if evidence_linked else None,
        event_total=(1 if evidence_linked else None) if event_total is ... else event_total,
        matched_event_total=(
            (1 if evidence_linked else None)
            if matched_event_total is ...
            else matched_event_total
        ),
        distinct_intervals=(
            (1 if evidence_linked else None)
            if distinct_intervals is ...
            else distinct_intervals
        ),
        evidence_status=evidence_status,  # type: ignore[arg-type]
        source_references=(),
        score=1.0,
    )
