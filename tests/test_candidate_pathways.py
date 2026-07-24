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
        reason="no candidates",
        truncated=False,
        expansions=0,
    )

    payload = result.as_dict()

    assert payload["query"]["threshold"] == "0.12345678901234567890"
    assert payload["source_signatures"] == {"status": {"value": "1.2300"}}
    json.dumps(payload)


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
