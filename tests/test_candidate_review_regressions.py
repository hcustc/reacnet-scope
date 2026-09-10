"""Regressions for PR #34: rankers must not rewrite routes or exceed budgets."""

from copy import deepcopy
import heapq

import pytest

from reacnet_scope.candidate_paths import (
    discover_network_candidate_routes,
    rank_candidate_paths,
)
from reacnet_scope.network import Reaction, ReactionNetwork


def _multi_anchor_network():
    return ReactionNetwork([
        Reaction(("[H]", "[O]"), ("[H][O]",), 10),
        Reaction(("[H][O]", "[H]"), ("[H][O][H]",), 8),
    ])


def test_ranking_preserves_each_declared_anchor_and_signature():
    network = _multi_anchor_network()
    report = discover_network_candidate_routes(
        network, ["[H]", "[O]"], maximum_path_length=2,
    )
    original = deepcopy(report)
    expected = {path["signature_id"]: path["species"] for path in report["paths"]}
    assert set(expected.values()) == {
        ("[H]", "[H][O]", "[H][O][H]"),
        ("[O]", "[H][O]", "[H][O][H]"),
    }
    for starts in (["[H]", "[O]"], ["[O]", "[H]"], ["[O]"]):
        ranked = rank_candidate_paths(network, report, starts)
        assert len(ranked["paths"]) == len(starts)
        for path in ranked["paths"]:
            assert path["species"] == expected[path["signature_id"]]
            assert path["start_species"] == path["species"][0]
            assert path["start_species"] in starts
            assert tuple(step["focal_output"] for step in path["steps"]) == path["species"][1:]
    assert report == original


def test_default_frontier_budget_bounds_high_fanout(monkeypatch):
    network = ReactionNetwork([
        Reaction(("A",), (f"P{index:05d}",), 1) for index in range(10_000)
    ])
    push = heapq.heappush
    metrics = {"pushes": 0, "peak": 0}

    def measured_push(queue, item):
        push(queue, item)
        metrics["pushes"] += 1
        metrics["peak"] = max(metrics["peak"], len(queue))

    monkeypatch.setattr(heapq, "heappush", measured_push)
    result = discover_network_candidate_routes(
        network, ["A"], minimum_path_length=1, maximum_path_length=1,
        max_expansions=1, max_paths=1,
    )
    assert metrics["peak"] <= 5_000
    assert metrics["pushes"] <= 10_000
    assert result["summary"]["traversal_truncated"] is True
    assert "max_frontier_states" in result["summary"]["truncation_reasons"]


@pytest.mark.parametrize("metric", ["temporal", "continuity", "energy"])
def test_unavailable_positive_weights_are_a_parameter_error(metric):
    network = _multi_anchor_network()
    report = discover_network_candidate_routes(
        network, ["[H]", "[O]"], maximum_path_length=2,
    )
    weights = dict.fromkeys(["frequency", "structure", "temporal", "continuity", "energy"], 0)
    weights[metric] = 1
    with pytest.raises(ValueError, match="available.*weight|weight.*available"):
        rank_candidate_paths(network, report, ["[H]", "[O]"], score_weights=weights)


@pytest.mark.parametrize("chain", [None, "ABC", ["A", "B"], ["A", "X", "D"], ["A", "D", "D"]])
def test_network_ranker_rejects_incomplete_or_invalid_declared_chains(chain):
    network = ReactionNetwork([
        Reaction(("A",), ("B", "C"), 2),
        Reaction(("B", "C"), ("D",), 1),
    ])
    report = discover_network_candidate_routes(network, ["A"], maximum_path_length=2)
    report["paths"][0]["species"] = chain
    with pytest.raises(ValueError, match="species chain|Species"):
        rank_candidate_paths(network, report, ["A"])


def test_ranker_preserves_both_declared_carried_branches():
    network = ReactionNetwork([
        Reaction(("[C]",), ("[C][O]", "[O]"), 2),
        Reaction(("[C][O]", "[O]"), ("[C]=O",), 1),
    ])
    report = discover_network_candidate_routes(network, ["[C]"], maximum_path_length=2)
    ranked = rank_candidate_paths(network, report, ["[C]"])
    assert {path["signature_id"]: path["species"] for path in ranked["paths"]} == {
        path["signature_id"]: path["species"] for path in report["paths"]
    }
    assert {path["species"][1] for path in ranked["paths"]} == {"[C][O]", "[O]"}


@pytest.mark.parametrize("budget", ["max_frontier_states", "max_generated_states"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, 1_000_001])
def test_invalid_search_state_budgets_are_rejected(budget, value):
    with pytest.raises(ValueError, match=budget):
        discover_network_candidate_routes(_multi_anchor_network(), ["[H]"], **{budget: value})


def test_generation_limit_and_exact_budget_boundary_are_distinct():
    network = ReactionNetwork([
        Reaction(("A",), ("B",), 3), Reaction(("B",), ("C",), 2),
    ])
    complete = discover_network_candidate_routes(
        network, ["A"], minimum_path_length=1, maximum_path_length=1,
        max_frontier_states=1, max_generated_states=2,
    )
    incomplete = discover_network_candidate_routes(
        network, ["A"], minimum_path_length=1, maximum_path_length=2,
        max_frontier_states=1, max_generated_states=2,
    )
    assert complete["summary"]["statistics_complete"] is True
    assert complete["summary"]["truncation_reasons"] == []
    assert incomplete["summary"]["truncation_reasons"] == ["max_generated_states"]
    assert incomplete["summary"]["generated_states"] == 2
    assert incomplete["summary"]["peak_frontier_states"] == 1
    assert [path["species"] for path in incomplete["paths"]] == [("A", "B")]


def test_frontier_selection_is_streamed_bounded_and_input_order_independent(monkeypatch):
    reactions = [Reaction(("A",), (f"P{index:03d}",), index + 1) for index in range(100)]
    original_select = heapq.nsmallest
    selections = []

    def bounded_select(n, iterable, *, key):
        assert not isinstance(iterable, (list, tuple)), "children were fully materialized"
        assert n <= 4, "selection ignored remaining frontier slots"
        selected = original_select(n, iterable, key=key)
        selections.append(len(selected))
        return selected

    monkeypatch.setattr(heapq, "nsmallest", bounded_select)
    results = [discover_network_candidate_routes(
        ReactionNetwork(order), ["A"], minimum_path_length=1, maximum_path_length=1,
        max_frontier_states=3, max_generated_states=8,
    ) for order in (reactions, list(reversed(reactions)))]
    assert results[0] == results[1]
    assert selections == [4, 4]
    assert [path["species"][-1] for path in results[0]["paths"]] == ["P099", "P098", "P097"]
    assert results[0]["summary"]["truncation_reasons"] == ["max_frontier_states"]
    assert results[0]["summary"]["generated_states"] == 4
    assert results[0]["summary"]["peak_frontier_states"] == 3


def test_initial_anchors_also_obey_state_budgets():
    report = discover_network_candidate_routes(
        _multi_anchor_network(), ["[H]", "[O]"], minimum_path_length=1,
        max_frontier_states=1, max_generated_states=1,
    )
    assert report["summary"]["generated_states"] == 1
    assert report["summary"]["peak_frontier_states"] == 1
    assert report["summary"]["truncation_reasons"] == ["max_frontier_states", "max_generated_states"]
    assert report["summary"]["statistics_complete"] is False


def test_available_energy_only_weights_are_respected_without_fallback():
    network = _multi_anchor_network()
    report = discover_network_candidate_routes(network, ["[O]"], maximum_path_length=2)
    weights = {"frequency": 0, "structure": 0, "temporal": 0, "continuity": 0, "energy": 1}
    energy = {reaction.key: {"score": 0.25} for reaction in network.reactions}
    ranked = rank_candidate_paths(network, report, ["[O]"], energy_evidence=energy, score_weights=weights)
    assert ranked["score_weights"] == weights
    assert ranked["paths"][0]["score"] == pytest.approx(0.25)
