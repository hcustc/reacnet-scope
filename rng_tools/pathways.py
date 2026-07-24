"""Immutable domain records and scoring for ranked candidate pathways.

The scores in this module are intentionally independent of graph libraries.
They rank candidate routes from network and optional event evidence; they do
not establish an atom-continuous reaction mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
import math
from numbers import Real
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence


SCORE_VERSION = "candidate-path/v1"


class EvidenceProvider(Protocol):
    """Read-only source of batched event summaries for reaction keys."""

    def reaction_summaries(
        self, reaction_keys: Sequence[str]
    ) -> Mapping[str, Mapping[str, Any]]:
        """Return evidence summaries keyed by reaction key."""


def _json_safe(value: Any) -> Any:
    """Convert immutable domain records into JSON-compatible primitives."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _validate_unit_metric(name: str, value: float) -> None:
    if not isinstance(value, Real) or not math.isfinite(value) or not 0 <= value <= 1:
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
        raise ValueError("event_coverage and time_coverage must both be supplied or both be None")
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
    score_version: str = SCORE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class CandidatePath:
    rank: int
    species: tuple[str, ...]
    steps: tuple[PathwayStep, ...]
    score: float
    evidence_status: str

    def as_dict(self) -> dict[str, Any]:
        payload = _json_safe(self)
        payload["score_version"] = SCORE_VERSION
        return payload


@dataclass(frozen=True)
class CandidatePathResult:
    paths: tuple[CandidatePath, ...]
    query: Mapping[str, Any]
    source_signatures: Mapping[str, Any]
    reason: str
    truncated: bool
    expansions: int

    def as_dict(self) -> dict[str, Any]:
        payload = _json_safe(self)
        payload["paths"] = [path.as_dict() for path in self.paths]
        payload["score_version"] = SCORE_VERSION
        payload["evidence_status"] = _result_evidence_status(self.paths)
        return payload


def _result_evidence_status(paths: Sequence[CandidatePath]) -> str:
    statuses = {path.evidence_status for path in paths}
    if not statuses:
        return "network_only"
    if len(statuses) == 1:
        return next(iter(statuses))
    return "mixed"
