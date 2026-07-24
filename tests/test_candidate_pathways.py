from __future__ import annotations

import math

import pytest

from rng_tools.pathways import (
    CandidatePath,
    CandidatePathResult,
    PathwayStep,
    score_path,
    score_step,
)


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
        evidence_status="evidence_linked",
    )
    result = CandidatePathResult(
        paths=(path,),
        query={"max_paths": 20, "score_version": "candidate-path/v1"},
        source_signatures={"reactionabcd": {"mtime_ns": 123}},
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
