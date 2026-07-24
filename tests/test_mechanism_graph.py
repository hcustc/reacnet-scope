from __future__ import annotations

from collections.abc import Sequence
import copy
from decimal import Decimal
import io
import json

import networkx as nx
import pytest

import rng_tools.mechanism_graph as mechanism_graph
from rng_tools.mechanism_graph import (
    build_mechanism_network,
    decode_gexf_mechanism_metadata,
    serialize_mechanism_graph,
    to_networkx_mechanism_graph,
)
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


@pytest.fixture
def mechanism_payload() -> dict[str, object]:
    return build_mechanism_network(
        ReactionNetwork(
            [
                Reaction(("A", "A", "X"), ("B", "C"), 12),
                Reaction(("B", "C"), ("A", "A", "X"), 2),
            ]
        ),
        anchor_smiles="A",
        max_depth=1,
    )


def test_networkx_projection_retains_bipartite_roles_and_stable_ids(
    mechanism_payload: dict[str, object],
) -> None:
    graph = to_networkx_mechanism_graph(mechanism_payload)

    assert isinstance(graph, nx.MultiDiGraph)
    assert nx.is_weakly_connected(graph)
    assert graph.graph == {
        "schema_version": "reacnet-scope/mechanism-network/v1",
        "network_semantics": "mechanism",
        "evidence_level": "reaction_passage_counts",
        "anchor_smiles": "A",
    }
    payload_node_ids = {
        node["id"] for node in mechanism_payload["nodes"]  # type: ignore[index]
    }
    payload_edge_ids = {
        edge["id"] for edge in mechanism_payload["edges"]  # type: ignore[index]
    }
    assert set(graph) == payload_node_ids
    assert {
        data["id"] for *_, data in graph.edges(data=True)
    } == payload_edge_ids

    reaction_id = next(
        node
        for node, data in graph.nodes(data=True)
        if data["kind"] == "reaction"
    )
    assert {
        data["role"]
        for *_, data in graph.in_edges(reaction_id, data=True)
    } == {"reactant"}
    assert {
        data["role"]
        for *_, data in graph.out_edges(reaction_id, data=True)
    } == {"product"}
    for source, target, key, data in graph.edges(keys=True, data=True):
        species_id = source if data["role"] == "reactant" else target
        assert key == f"{data['role']}:{reaction_id}:{species_id}"


def test_cytoscape_serialization_preserves_schema_and_element_ids(
    mechanism_payload: dict[str, object],
) -> None:
    graph = to_networkx_mechanism_graph(mechanism_payload)
    before = copy.deepcopy(graph)

    document = serialize_mechanism_graph(graph)

    assert isinstance(document, dict)
    assert document["data"]["schema_version"] == mechanism_payload["schema_version"]
    assert document["data"]["network_semantics"] == "mechanism"
    node_ids = {
        element["data"]["id"] for element in document["elements"]["nodes"]
    }
    edge_ids = {
        element["data"]["id"] for element in document["elements"]["edges"]
    }
    assert node_ids == set(graph)
    assert edge_ids == {
        data["id"] for *_, data in graph.edges(data=True)
    }
    assert nx.utils.graphs_equal(graph, before)


@pytest.mark.parametrize(
    ("format_name", "reader"),
    [("graphml", nx.read_graphml), ("gexf", nx.read_gexf)],
)
def test_xml_serialization_round_trips_counts_and_canonicalizes_attributes(
    mechanism_payload: dict[str, object],
    format_name: str,
    reader: object,
) -> None:
    graph = to_networkx_mechanism_graph(mechanism_payload)
    reaction_id = next(
        node
        for node, data in graph.nodes(data=True)
        if data["kind"] == "reaction"
    )
    graph.graph["structured"] = {
        "tuple": ("x", None),
        "flags": [True, False],
    }
    graph.nodes[reaction_id]["nested"] = {"b": [2, None], "a": True}
    before = copy.deepcopy(graph)

    serialized = serialize_mechanism_graph(graph, format=format_name)

    assert isinstance(serialized, bytes)
    round_tripped = reader(io.BytesIO(serialized))  # type: ignore[operator]
    assert len(round_tripped.nodes) == len(graph.nodes)
    assert len(round_tripped.edges) == len(graph.edges)
    assert round_tripped.nodes[reaction_id]["reactants"] == json.dumps(
        ["A", "A", "X"],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert round_tripped.nodes[reaction_id]["products"] == json.dumps(
        ["B", "C"],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert round_tripped.nodes[reaction_id]["nested"] == (
        '{"a":1,"b":[2,""]}'
    )
    assert nx.utils.graphs_equal(graph, before)


def test_xml_serialization_coerces_none_booleans_and_nested_values() -> None:
    graph = nx.MultiDiGraph(
        schema_version="test/v1",
        network_semantics="mechanism",
        evidence_level="network_only",
        anchor_smiles="A",
        optional=None,
        visible=True,
    )
    graph.add_node(
        "species:A",
        id="species:A",
        kind="species",
        optional=None,
        visible=False,
        nested=("A", {"flag": True, "missing": None}),
    )

    xml = serialize_mechanism_graph(graph, format="graphml")
    restored = nx.read_graphml(io.BytesIO(xml))

    assert restored.graph["optional"] == ""
    assert restored.graph["visible"] == 1
    assert restored.nodes["species:A"]["optional"] == ""
    assert restored.nodes["species:A"]["visible"] == 0
    assert restored.nodes["species:A"]["nested"] == '["A",{"flag":1,"missing":""}]'


def test_graphml_round_trip_uses_payload_ids_and_retains_semantic_keys() -> None:
    graph = nx.MultiDiGraph(
        schema_version="test/v1",
        network_semantics="mechanism",
        evidence_level="network_only",
        anchor_smiles="A",
    )
    graph.add_edge(
        "species:A",
        "reaction:1",
        key="reactant:reaction:1:species:A:first",
        id="edge:first",
        role="reactant",
    )
    graph.add_edge(
        "species:A",
        "reaction:1",
        key="reactant:reaction:1:species:A:second",
        id="edge:second",
        role="reactant",
    )

    xml = serialize_mechanism_graph(graph, format="graphml")
    restored = nx.read_graphml(io.BytesIO(xml))

    assert isinstance(restored, nx.MultiDiGraph)
    assert set(restored["species:A"]["reaction:1"]) == {
        "edge:first",
        "edge:second",
    }
    assert {
        data["id"] for *_, data in restored.edges(data=True)
    } == {"edge:first", "edge:second"}
    assert {
        data["semantic_key"] for *_, data in restored.edges(data=True)
    } == {
        "reactant:reaction:1:species:A:first",
        "reactant:reaction:1:species:A:second",
    }


@pytest.mark.parametrize("empty", [False, True])
def test_gexf_round_trip_retains_machine_readable_mechanism_metadata(
    empty: bool,
) -> None:
    graph = nx.MultiDiGraph(
        schema_version="reacnet-scope/mechanism-network/v1",
        network_semantics="mechanism",
        evidence_level="event_linked",
        anchor_smiles="[CH3]",
    )
    if not empty:
        graph.add_edge(
            "species:A",
            "reaction:1",
            key="reactant:reaction:1:species:A",
            id="edge:1",
            role="reactant",
        )

    xml = serialize_mechanism_graph(graph, format="gexf")
    restored = nx.read_gexf(io.BytesIO(xml))

    assert len(restored.nodes) == len(graph.nodes)
    assert len(restored.edges) == len(graph.edges)
    assert decode_gexf_mechanism_metadata(restored) == {
        "schema_version": "reacnet-scope/mechanism-network/v1",
        "network_semantics": "mechanism",
        "evidence_level": "event_linked",
        "anchor_smiles": "[CH3]",
    }
    assert json.loads(restored.graph["name"])["format"] == (
        "reacnet-scope/gexf-metadata/v1"
    )


@pytest.mark.parametrize(
    ("format_name", "reader"),
    [("graphml", nx.read_graphml), ("gexf", nx.read_gexf)],
)
def test_xml_serialization_canonicalizes_nonfinite_numbers_recursively(
    format_name: str,
    reader: object,
) -> None:
    graph = nx.MultiDiGraph(
        schema_version="test/v1",
        network_semantics="mechanism",
        evidence_level="network_only",
        anchor_smiles="A",
    )
    graph.add_node(
        "species:A",
        id="species:A",
        kind="species",
        nan=float("nan"),
        positive_infinity=float("inf"),
        negative_infinity=float("-inf"),
        decimal_nan=Decimal("NaN"),
        decimal_positive_infinity=Decimal("Infinity"),
        decimal_negative_infinity=Decimal("-Infinity"),
        nested={
            "nan": float("nan"),
            "positive": Decimal("Infinity"),
            "negative": float("-inf"),
        },
    )
    before = copy.deepcopy(graph)

    first = serialize_mechanism_graph(graph, format=format_name)
    second = serialize_mechanism_graph(graph, format=format_name)
    restored = reader(io.BytesIO(first))  # type: ignore[operator]
    attributes = restored.nodes["species:A"]

    assert first == second
    assert attributes["nan"] == "NaN"
    assert attributes["positive_infinity"] == "Infinity"
    assert attributes["negative_infinity"] == "-Infinity"
    assert attributes["decimal_nan"] == "NaN"
    assert attributes["decimal_positive_infinity"] == "Infinity"
    assert attributes["decimal_negative_infinity"] == "-Infinity"
    assert attributes["nested"] == (
        '{"nan":"NaN","negative":"-Infinity","positive":"Infinity"}'
    )
    assert nx.utils.graphs_equal(graph, before)


def test_serializer_rejects_unknown_format_with_all_supported_names(
    mechanism_payload: dict[str, object],
) -> None:
    graph = to_networkx_mechanism_graph(mechanism_payload)

    with pytest.raises(ValueError) as error:
        serialize_mechanism_graph(graph, format="json")

    assert all(
        name in str(error.value)
        for name in ("cytoscape-json", "graphml", "gexf")
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "schema_version"),
        (
            {
                "schema_version": "v1",
                "network_semantics": "event_transfer",
                "evidence_level": "network_only",
                "anchor_smiles": "A",
                "nodes": [],
                "edges": [],
            },
            "network_semantics",
        ),
        (
            {
                "schema_version": "v1",
                "network_semantics": "mechanism",
                "evidence_level": "network_only",
                "anchor_smiles": "A",
                "nodes": [{"id": "species:A", "kind": "species"}],
                "edges": [
                    {
                        "id": "edge:missing",
                        "source": "species:A",
                        "target": "reaction:missing",
                        "role": "reactant",
                    }
                ],
            },
            "unknown target",
        ),
    ],
)
def test_networkx_projection_reports_clear_payload_validation_errors(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        to_networkx_mechanism_graph(payload)
