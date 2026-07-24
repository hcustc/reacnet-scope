from __future__ import annotations

from collections.abc import Sequence
import copy
from decimal import Decimal
import hashlib
import io
import json

import networkx as nx
import pytest

import rng_tools.mechanism_graph as mechanism_graph
from rng_tools.mechanism_graph import (
    build_mechanism_network,
    canonical_mechanism_reaction_key,
    decode_gexf_mechanism_metadata,
    mechanism_graph_metrics,
    serialize_mechanism_graph,
    stable_mechanism_edge_id,
    stable_mechanism_id,
    to_networkx_mechanism_graph,
    validate_mechanism_payload,
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


def test_missing_evidence_row_is_linked_with_zero_metrics() -> None:
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
    assert missing["event_total"] == 0
    assert missing["matched_event_total"] == 0
    assert missing["event_coverage"] == 0.0
    assert missing["evidence_status"] == "evidence_linked"
    assert payload["evidence_level"] == "event_evidence_linked"


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


@pytest.mark.parametrize(
    ("evidence_level", "status"),
    [
        ("event_evidence_linked", "network_only"),
        ("reaction_passage_counts", "evidence_linked"),
    ],
)
def test_validator_rejects_payload_reaction_evidence_contradictions(
    mechanism_payload: dict[str, object],
    evidence_level: str,
    status: str,
) -> None:
    payload = copy.deepcopy(mechanism_payload)
    payload["evidence_level"] = evidence_level
    reaction = _reaction_nodes(payload)[0]
    reaction["evidence_status"] = status
    if status == "network_only":
        reaction["event_total"] = None
        reaction["matched_event_total"] = None
        reaction["event_coverage"] = None
    else:
        reaction["event_total"] = 2
        reaction["matched_event_total"] = 1
        reaction["event_coverage"] = 0.5

    with pytest.raises(ValueError, match="evidence_level"):
        validate_mechanism_payload(payload)


def test_validator_rejects_species_with_reaction_evidence_fields(
    mechanism_payload: dict[str, object],
) -> None:
    payload = copy.deepcopy(mechanism_payload)
    species = next(
        node for node in payload["nodes"]  # type: ignore[index]
        if node["kind"] == "species"
    )
    species["evidence_status"] = "network_only"

    with pytest.raises(ValueError, match="species.*evidence_status"):
        validate_mechanism_payload(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "wrong_direction",
        "wrong_role",
        "wrong_reaction",
        "split_duplicate",
    ],
)
def test_validator_requires_exact_reaction_side_edge_counters(
    mechanism_payload: dict[str, object],
    mutation: str,
) -> None:
    payload = copy.deepcopy(mechanism_payload)
    reaction = _reaction_nodes(payload)[0]
    reaction_id = reaction["id"]
    reactant_edge = next(
        edge for edge in payload["edges"]  # type: ignore[index]
        if edge["role"] == "reactant"
    )
    if mutation == "missing":
        payload["edges"].remove(reactant_edge)  # type: ignore[union-attr]
    elif mutation == "extra":
        extra = copy.deepcopy(reactant_edge)
        extra.update(
            id="edge:extra",
            species_smiles="B",
            source=next(
                node["id"] for node in payload["nodes"]  # type: ignore[index]
                if node.get("smiles") == "B"
            ),
            coefficient=1,
        )
        payload["edges"].append(extra)  # type: ignore[union-attr]
    elif mutation == "wrong_direction":
        reactant_edge["source"], reactant_edge["target"] = (
            reactant_edge["target"],
            reactant_edge["source"],
        )
    elif mutation == "wrong_role":
        reactant_edge["role"] = "product"
    elif mutation == "wrong_reaction":
        other = copy.deepcopy(reaction)
        other["id"] = "reaction:other"
        payload["nodes"].append(other)  # type: ignore[union-attr]
        reactant_edge["target"] = "reaction:other"
    else:
        # Repeated A is represented by one edge with coefficient 2, never
        # multiple semantic edges that split the same stoichiometric count.
        duplicate = copy.deepcopy(reactant_edge)
        duplicate["id"] = "edge:split"
        duplicate["coefficient"] = 1
        reactant_edge["coefficient"] = 1
        payload["edges"].append(duplicate)  # type: ignore[union-attr]

    with pytest.raises(ValueError):
        validate_mechanism_payload(payload)


def test_validator_allows_catalyst_on_both_semantic_roles() -> None:
    payload = build_mechanism_network(
        ReactionNetwork(
            [Reaction(("A", "X"), ("B", "X"), 7)]
        ),
        anchor_smiles="A",
        max_depth=1,
    )

    validated = validate_mechanism_payload(payload)
    catalyst_edges = [
        edge for edge in validated["edges"]
        if edge["species_smiles"] == "X"
    ]

    assert {
        (edge["role"], edge["coefficient"])
        for edge in catalyst_edges
    } == {("reactant", 1), ("product", 1)}


@pytest.mark.parametrize(
    "mutation",
    [
        "species_id",
        "reaction_key",
        "reaction_sides",
        "reaction_id",
        "edge_id",
    ],
)
def test_validator_rejects_tampered_stable_identities(
    mechanism_payload: dict[str, object],
    mutation: str,
) -> None:
    payload = copy.deepcopy(mechanism_payload)
    reaction = _reaction_nodes(payload)[0]
    species = next(
        node for node in payload["nodes"]  # type: ignore[index]
        if node["kind"] == "species"
    )
    edge = payload["edges"][0]  # type: ignore[index]

    if mutation == "species_id":
        old_id = species["id"]
        species["id"] = "species:" + "0" * 20
        for candidate in payload["edges"]:  # type: ignore[index]
            if candidate["source"] == old_id:
                candidate["source"] = species["id"]
            if candidate["target"] == old_id:
                candidate["target"] = species["id"]
    elif mutation == "reaction_key":
        reaction["reaction_key"] = "X->Y"
        for candidate in payload["edges"]:  # type: ignore[index]
            candidate["reaction_key"] = "X->Y"
    elif mutation == "reaction_sides":
        reaction["reactants"] = ["X", "X", "Y"]
        reaction["products"] = ["Z", "Ω"]
    elif mutation == "reaction_id":
        old_id = reaction["id"]
        reaction["id"] = "reaction:" + "0" * 20
        for candidate in payload["edges"]:  # type: ignore[index]
            if candidate["source"] == old_id:
                candidate["source"] = reaction["id"]
            if candidate["target"] == old_id:
                candidate["target"] = reaction["id"]
    else:
        edge["id"] = "edge:" + "0" * 20

    with pytest.raises(ValueError, match="stable|canonical"):
        validate_mechanism_payload(payload)


def test_validator_rejects_fully_relabelled_payload_with_old_ids(
    mechanism_payload: dict[str, object],
) -> None:
    payload = copy.deepcopy(mechanism_payload)
    replacements = {"A": "X", "B": "Y", "C": "Ω", "X": "催化剂"}
    for node in payload["nodes"]:  # type: ignore[index]
        if node["kind"] == "species":
            node["smiles"] = replacements[node["smiles"]]
        else:
            node["reactants"] = [
                replacements[item] for item in node["reactants"]
            ]
            node["products"] = [
                replacements[item] for item in node["products"]
            ]
            node["reaction_key"] = (
                "+".join(sorted(node["reactants"]))
                + "->"
                + "+".join(sorted(node["products"]))
            )
    for edge in payload["edges"]:  # type: ignore[index]
        edge["species_smiles"] = replacements[edge["species_smiles"]]
        edge["reaction_key"] = _reaction_nodes(payload)[0]["reaction_key"]

    with pytest.raises(ValueError, match="stable"):
        validate_mechanism_payload(payload)


def test_validator_accepts_unicode_repeated_stoichiometry_and_catalyst() -> None:
    payload = build_mechanism_network(
        ReactionNetwork(
            [
                Reaction(
                    ("α", "α", "催化剂"),
                    ("β", "催化剂"),
                    9,
                )
            ]
        ),
        anchor_smiles="α",
        max_depth=1,
    )

    assert validate_mechanism_payload(payload) == payload


def test_public_identity_helpers_lock_version_one_hash_contract() -> None:
    smiles = "催化剂"
    reaction_key = "A+A+催化剂->B+催化剂"
    assert canonical_mechanism_reaction_key(
        ("催化剂", "A", "A"),
        ("催化剂", "B"),
    ) == reaction_key
    assert stable_mechanism_id("species", smiles) == (
        "species:"
        + hashlib.sha256(smiles.encode("utf-8")).hexdigest()[:20]
    )
    edge_seed = "\0".join(("reactant", reaction_key, smiles))
    assert stable_mechanism_edge_id(
        "reactant",
        reaction_key,
        smiles,
    ) == (
        "edge:"
        + hashlib.sha256(edge_seed.encode("utf-8")).hexdigest()[:20]
    )


def _metric_node(
    graph: nx.MultiDiGraph,
    kind: str,
    value: str,
) -> str:
    node_id = stable_mechanism_id(kind, value)
    attributes = {"id": node_id, "kind": kind}
    attributes["smiles" if kind == "species" else "reaction_key"] = value
    graph.add_node(node_id, **attributes)
    return node_id


def _metric_graph(anchor_smiles: str = "A") -> nx.MultiDiGraph:
    return nx.MultiDiGraph(
        network_semantics="mechanism",
        anchor_smiles=anchor_smiles,
    )


def test_mechanism_graph_metrics_reports_sorted_components_and_reachability(
) -> None:
    graph = _metric_graph()
    species_a = _metric_node(graph, "species", "A")
    species_b = _metric_node(graph, "species", "B")
    species_c = _metric_node(graph, "species", "C")
    reaction_ab = _metric_node(graph, "reaction", "A->B")
    reaction_ca = _metric_node(graph, "reaction", "C->A")
    species_x = _metric_node(graph, "species", "X")
    species_y = _metric_node(graph, "species", "Y")
    reaction_xy = _metric_node(graph, "reaction", "X->Y")
    graph.add_edge(species_c, reaction_ca, role="reactant")
    graph.add_edge(reaction_ca, species_a, role="product")
    graph.add_edge(species_a, reaction_ab, role="reactant")
    graph.add_edge(reaction_ab, species_b, role="product")
    graph.add_edge(species_x, reaction_xy, role="reactant")
    graph.add_edge(reaction_xy, species_y, role="product")
    before = copy.deepcopy(graph)

    metrics = mechanism_graph_metrics(graph)

    assert metrics == {
        "weak_component_count": 2,
        "weak_component_sizes": [5, 3],
        "anchor_id": species_a,
        "downstream_node_ids": sorted([reaction_ab, species_b]),
        "upstream_node_ids": sorted([reaction_ca, species_c]),
        "downstream_species_ids": [species_b],
        "upstream_species_ids": [species_c],
        # Reachable species is the union of upstream and downstream species;
        # the separately reported anchor is deliberately not included.
        "reachable_species_ids": sorted([species_b, species_c]),
        "degree_centrality": {
            node_id: nx.degree_centrality(graph)[node_id]
            for node_id in sorted(graph)
        },
    }
    assert list(metrics["degree_centrality"]) == sorted(graph)
    json.dumps(metrics, allow_nan=False)
    assert nx.utils.graphs_equal(graph, before)


def test_mechanism_graph_metrics_is_deterministic_across_insertion_order(
) -> None:
    first = _metric_graph()
    second = _metric_graph()
    first_ids = {
        (kind, value): _metric_node(first, kind, value)
        for kind, value in (
            ("species", "A"),
            ("reaction", "A->B"),
            ("species", "B"),
        )
    }
    second_ids = {
        (kind, value): _metric_node(second, kind, value)
        for kind, value in reversed(
            (
                ("species", "A"),
                ("reaction", "A->B"),
                ("species", "B"),
            )
        )
    }
    for graph, ids in ((first, first_ids), (second, second_ids)):
        graph.add_edge(
            ids[("species", "A")],
            ids[("reaction", "A->B")],
            role="reactant",
        )
        graph.add_edge(
            ids[("reaction", "A->B")],
            ids[("species", "B")],
            role="product",
        )

    assert mechanism_graph_metrics(first) == mechanism_graph_metrics(second)


def test_mechanism_graph_metrics_empty_and_missing_anchor_are_nonfatal(
) -> None:
    empty = _metric_graph()
    assert mechanism_graph_metrics(empty) == {
        "weak_component_count": 0,
        "weak_component_sizes": [],
        "anchor_id": None,
        "downstream_node_ids": [],
        "upstream_node_ids": [],
        "downstream_species_ids": [],
        "upstream_species_ids": [],
        "reachable_species_ids": [],
        "degree_centrality": {},
    }

    disconnected = _metric_graph(anchor_smiles="missing")
    species_a = _metric_node(disconnected, "species", "A")
    assert mechanism_graph_metrics(disconnected) == {
        "weak_component_count": 1,
        "weak_component_sizes": [1],
        "anchor_id": None,
        "downstream_node_ids": [],
        "upstream_node_ids": [],
        "downstream_species_ids": [],
        "upstream_species_ids": [],
        "reachable_species_ids": [],
        "degree_centrality": {species_a: 1.0},
    }


def test_mechanism_graph_metrics_preserves_parallel_edge_centrality() -> None:
    graph = _metric_graph()
    species_a = _metric_node(graph, "species", "A")
    reaction_ab = _metric_node(graph, "reaction", "A->B")
    graph.add_edge(species_a, reaction_ab, key="first", role="reactant")
    graph.add_edge(species_a, reaction_ab, key="second", role="reactant")

    metrics = mechanism_graph_metrics(graph)

    # NetworkX counts parallel edges in MultiDiGraph degree centrality, so the
    # unrounded value can exceed one and must not be clamped.
    assert metrics["degree_centrality"] == {
        reaction_ab: 2.0,
        species_a: 2.0,
    }


@pytest.mark.parametrize(
    ("graph", "message"),
    [
        (nx.DiGraph(network_semantics="mechanism"), "MultiDiGraph"),
        (nx.MultiDiGraph(network_semantics="event_transfer"), "mechanism"),
    ],
)
def test_mechanism_graph_metrics_rejects_invalid_graph_contract(
    graph: nx.Graph,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        mechanism_graph_metrics(graph)  # type: ignore[arg-type]


def test_mechanism_graph_metrics_rejects_self_loops_and_bad_node_identity(
) -> None:
    self_loop = _metric_graph()
    species_a = _metric_node(self_loop, "species", "A")
    self_loop.add_edge(species_a, species_a, role="reactant")
    with pytest.raises(ValueError, match="self-loop"):
        mechanism_graph_metrics(self_loop)

    bad_identity = _metric_graph()
    bad_identity.add_node(
        "species:not-stable",
        id="species:not-stable",
        kind="species",
        smiles="A",
    )
    with pytest.raises(ValueError, match="stable"):
        mechanism_graph_metrics(bad_identity)
