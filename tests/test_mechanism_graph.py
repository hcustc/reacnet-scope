from __future__ import annotations

from collections.abc import Sequence

import pytest

import rng_tools.mechanism_graph as mechanism_graph
from rng_tools.mechanism_graph import build_mechanism_network
from rng_tools.network import Reaction, ReactionNetwork


def _reaction_nodes(payload: dict[str, object]) -> list[dict[str, object]]:
    return [
        node
        for node in payload["nodes"]  # type: ignore[index]
        if node["kind"] == "reaction"
    ]


def _species_smiles(payload: dict[str, object]) -> set[str]:
    return {
        node["smiles"]
        for node in payload["nodes"]  # type: ignore[index]
        if node["kind"] == "species"
    }


def test_mechanism_payload_is_bipartite_and_preserves_stoichiometry() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A", "A", "X"), ("B", "C"), 12),
            Reaction(("B", "C"), ("A", "A", "X"), 2),
        ]
    )

    payload = build_mechanism_network(network, anchor_smiles="A", max_depth=1)

    assert payload["schema_version"] == "reacnet-scope/mechanism-network/v1"
    assert payload["network_semantics"] == "mechanism"
    assert payload["evidence_level"] == "reaction_passage_counts"
    reaction = _reaction_nodes(payload)[0]
    assert reaction["reactants"] == ["A", "A", "X"]
    assert reaction["products"] == ["B", "C"]
    assert reaction["forward_tp"] == 12
    assert reaction["reverse_tp"] == 2
    assert reaction["net_tp"] == 10
    assert len(_reaction_nodes(payload)) == 1

    reactant_edges = [
        edge for edge in payload["edges"] if edge["role"] == "reactant"
    ]
    assert next(
        edge for edge in reactant_edges if edge["species_smiles"] == "A"
    )["coefficient"] == 2
    assert all(
        (
            edge["source"].startswith("species:")
            and edge["target"].startswith("reaction:")
        )
        if edge["role"] == "reactant"
        else (
            edge["source"].startswith("reaction:")
            and edge["target"].startswith("species:")
        )
        for edge in payload["edges"]
    )


def test_reverse_dominated_pair_is_oriented_in_positive_net_direction() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 3),
            Reaction(("B",), ("A",), 11),
        ]
    )

    payload = build_mechanism_network(network, anchor_smiles="A", max_depth=1)

    reaction = _reaction_nodes(payload)[0]
    assert reaction["reaction_key"] == "B->A"
    assert reaction["reactants"] == ["B"]
    assert reaction["products"] == ["A"]
    assert reaction["forward_tp"] == 11
    assert reaction["reverse_tp"] == 3
    assert reaction["net_tp"] == 8


@pytest.mark.parametrize(
    ("direction", "expected_keys"),
    [
        ("downstream", {"A->B"}),
        ("upstream", {"C->A"}),
        ("both", {"A->B", "C->A"}),
    ],
)
def test_direction_selects_expected_oriented_neighborhood(
    direction: str,
    expected_keys: set[str],
) -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("C",), ("A",), 8),
        ]
    )

    payload = build_mechanism_network(
        network,
        anchor_smiles="A",
        direction=direction,
        max_depth=1,
    )

    assert {
        node["reaction_key"] for node in _reaction_nodes(payload)
    } == expected_keys


def test_max_depth_counts_a_full_species_reaction_species_step() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B",), ("C",), 9),
            Reaction(("C",), ("D",), 8),
        ]
    )

    depth_one = build_mechanism_network(
        network,
        anchor_smiles="A",
        direction="downstream",
        max_depth=1,
    )
    depth_two = build_mechanism_network(
        network,
        anchor_smiles="A",
        direction="downstream",
        max_depth=2,
    )

    assert {
        node["reaction_key"] for node in _reaction_nodes(depth_one)
    } == {"A->B"}
    assert {
        node["reaction_key"] for node in _reaction_nodes(depth_two)
    } == {"A->B", "B->C"}


def test_max_nodes_skips_a_reaction_atomically_and_marks_truncated() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("A",), ("C",), 9),
        ]
    )

    payload = build_mechanism_network(
        network,
        anchor_smiles="A",
        max_depth=1,
        max_nodes=3,
    )

    assert {
        node["reaction_key"] for node in _reaction_nodes(payload)
    } == {"A->B"}
    assert _species_smiles(payload) == {"A", "B"}
    assert payload["meta"]["node_count"] == 3
    assert payload["meta"]["reaction_count"] == 1
    assert payload["meta"]["truncated"] is True
    assert not any(
        node.get("reaction_key") == "A->C"
        or node.get("smiles") == "C"
        for node in payload["nodes"]
    )


def test_ids_and_payload_order_are_stable_across_input_reaction_order() -> None:
    reactions = [
        Reaction(("A", "X", "A"), ("C", "B"), 12),
        Reaction(("B", "C"), ("A", "A", "X"), 2),
        Reaction(("B",), ("D",), 7),
    ]

    forward = build_mechanism_network(
        ReactionNetwork(reactions),
        anchor_smiles="A",
        max_depth=2,
    )
    backward = build_mechanism_network(
        ReactionNetwork(list(reversed(reactions))),
        anchor_smiles="A",
        max_depth=2,
    )

    assert forward == backward
    assert len({node["id"] for node in forward["nodes"]}) == len(forward["nodes"])
    assert len({edge["id"] for edge in forward["edges"]}) == len(forward["edges"])


def test_truncated_hash_collision_is_rejected_instead_of_merging_species(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConstantDigest:
        def hexdigest(self) -> str:
            return "0" * 64

    monkeypatch.setattr(mechanism_graph, "sha256", lambda _value: ConstantDigest())

    with pytest.raises(ValueError, match="stable node ID collision"):
        build_mechanism_network(
            ReactionNetwork([Reaction(("A",), ("B",), 10)]),
            anchor_smiles="A",
        )


def test_anchor_node_is_returned_when_species_has_no_positive_net_reaction() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 5),
            Reaction(("B",), ("A",), 5),
        ]
    )

    payload = build_mechanism_network(network, anchor_smiles="A")

    assert _species_smiles(payload) == {"A"}
    assert payload["edges"] == []
    assert payload["meta"]["reason"] == "no_positive_net_continuation"
    assert payload["meta"]["truncated"] is False


def test_missing_anchor_returns_empty_species_absent_payload() -> None:
    payload = build_mechanism_network(
        ReactionNetwork([Reaction(("A",), ("B",), 10)]),
        anchor_smiles="missing",
    )

    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["meta"] == {
        "node_count": 0,
        "edge_count": 0,
        "reaction_count": 0,
        "truncated": False,
        "reason": "species_absent",
    }


def test_threshold_only_empty_result_has_distinct_reason() -> None:
    payload = build_mechanism_network(
        ReactionNetwork([Reaction(("A",), ("B",), 3)]),
        anchor_smiles="A",
        min_net_tp=4,
    )

    assert _species_smiles(payload) == {"A"}
    assert payload["meta"]["reason"] == "filtered_by_thresholds"


class _RecordingEvidenceProvider:
    source_signatures = {"event_index": {"path": "fixture.sqlite3"}}

    def __init__(self, summaries: dict[str, dict[str, object]]) -> None:
        self.summaries = summaries
        self.calls: list[tuple[str, ...]] = []

    def reaction_summaries(
        self,
        reaction_keys: Sequence[str],
    ) -> dict[str, dict[str, object]]:
        self.calls.append(tuple(reaction_keys))
        return self.summaries


def test_ready_evidence_provider_is_called_once_with_selected_sorted_keys() -> None:
    provider = _RecordingEvidenceProvider(
        {
            "A->B": {"total_events": 4, "matched_events": 3},
            "A->C": {"event_total": 0, "matched_event_total": 0},
        }
    )
    network = ReactionNetwork(
        [
            Reaction(("A",), ("C",), 8),
            Reaction(("A",), ("B",), 10),
            Reaction(("X",), ("Y",), 100),
        ]
    )

    payload = build_mechanism_network(
        network,
        anchor_smiles="A",
        max_depth=1,
        evidence_provider=provider,
    )

    assert provider.calls == [("A->B", "A->C")]
    by_key = {
        node["reaction_key"]: node for node in _reaction_nodes(payload)
    }
    assert by_key["A->B"]["event_total"] == 4
    assert by_key["A->B"]["matched_event_total"] == 3
    assert by_key["A->B"]["event_coverage"] == pytest.approx(0.75)
    assert by_key["A->B"]["evidence_status"] == "evidence_linked"
    assert by_key["A->C"]["event_coverage"] == 0.0
    assert payload["evidence_level"] == "event_evidence_linked"
    assert payload["source_signatures"] == provider.source_signatures


def test_missing_evidence_row_is_network_only_with_none_metrics() -> None:
    provider = _RecordingEvidenceProvider(
        {"A->B": {"total_events": 2, "matched_events": 2}}
    )
    payload = build_mechanism_network(
        ReactionNetwork(
            [
                Reaction(("A",), ("B",), 10),
                Reaction(("A",), ("C",), 8),
            ]
        ),
        anchor_smiles="A",
        max_depth=1,
        evidence_provider=provider,
    )

    missing = next(
        node for node in _reaction_nodes(payload)
        if node["reaction_key"] == "A->C"
    )
    assert missing["event_total"] is None
    assert missing["matched_event_total"] is None
    assert missing["event_coverage"] is None
    assert missing["evidence_status"] == "network_only"


def test_unavailable_evidence_does_not_perform_any_lookup() -> None:
    payload = build_mechanism_network(
        ReactionNetwork([Reaction(("A",), ("B",), 10)]),
        anchor_smiles="A",
        evidence_provider=None,
    )

    reaction = _reaction_nodes(payload)[0]
    assert reaction["event_total"] is None
    assert reaction["matched_event_total"] is None
    assert reaction["event_coverage"] is None
    assert reaction["evidence_status"] == "network_only"
    assert payload["source_signatures"] == {}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"anchor_smiles": ""}, "anchor_smiles"),
        ({"anchor_smiles": "   "}, "anchor_smiles"),
        ({"anchor_smiles": 3}, "anchor_smiles"),
        ({"anchor_smiles": "A", "direction": "sideways"}, "direction"),
        ({"anchor_smiles": "A", "direction": []}, "direction"),
        ({"anchor_smiles": "A", "max_depth": True}, "max_depth"),
        ({"anchor_smiles": "A", "max_depth": 0}, "max_depth"),
        ({"anchor_smiles": "A", "min_net_tp": False}, "min_net_tp"),
        ({"anchor_smiles": "A", "min_net_tp": 0}, "min_net_tp"),
        ({"anchor_smiles": "A", "max_nodes": True}, "max_nodes"),
        ({"anchor_smiles": "A", "max_nodes": 0}, "max_nodes"),
    ],
)
def test_query_validation_is_strict(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_mechanism_network(
            ReactionNetwork([Reaction(("A",), ("B",), 10)]),
            **kwargs,
        )
