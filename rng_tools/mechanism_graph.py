"""Stable bounded payloads for chemistry-facing mechanism networks.

The payload is deliberately bipartite: species nodes connect to explicit
reaction nodes.  Passage counts describe ReacNetGenerator observations, not
kinetic rates or atom-transfer fluxes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import heapq
import io
import json
import math
from numbers import Integral, Real
from typing import Any, Literal

import networkx as nx

from rng_tools.network import Reaction, ReactionNetwork, smiles_to_formula_fast
from rng_tools.pathways import EvidenceProvider


SCHEMA_VERSION = "reacnet-scope/mechanism-network/v1"
_SERIALIZATION_FORMATS = ("cytoscape-json", "graphml", "gexf")
_GEXF_METADATA_FORMAT = "reacnet-scope/gexf-metadata/v1"
_MECHANISM_METADATA_FIELDS = (
    "schema_version",
    "network_semantics",
    "evidence_level",
    "anchor_smiles",
)
_GRAPHML_SEMANTIC_KEY_ATTRIBUTE = "semantic_key"


@dataclass(frozen=True)
class _OrientedReaction:
    reaction_key: str
    reactants: tuple[str, ...]
    products: tuple[str, ...]
    forward_tp: int
    reverse_tp: int
    net_tp: int


def canonical_mechanism_reaction_key(
    reactants: Iterable[str],
    products: Iterable[str],
) -> str:
    """Return the builder's multiplicity-preserving canonical reaction key."""
    return (
        "+".join(sorted(reactants))
        + "->"
        + "+".join(sorted(products))
    )


def stable_mechanism_id(kind: str, value: str) -> str:
    """Return the version-one stable identity for one semantic value."""
    digest = sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{digest}"


def stable_mechanism_edge_id(
    role: str,
    reaction_key: str,
    species_smiles: str,
) -> str:
    """Return the stable identity of one reaction/species semantic edge."""
    edge_seed = "\0".join((role, reaction_key, species_smiles))
    return stable_mechanism_id("edge", edge_seed)


def to_networkx_mechanism_graph(
    payload: Mapping[str, Any],
) -> nx.MultiDiGraph:
    """Project a mechanism payload into a stable bipartite multigraph."""
    validated = validate_mechanism_payload(payload)
    return _to_networkx_validated_mechanism_graph(validated)


def mechanism_graph_metrics(graph: nx.DiGraph) -> dict[str, Any]:
    """Return deterministic metrics for one bounded mechanism graph.

    Reachability is reported from the graph's stable anchor species ID.
    ``reachable_species_ids`` is the sorted union of upstream and downstream
    reachable species and deliberately excludes the separately reported
    anchor.  Degree centrality is NetworkX's unrounded value.  For a
    MultiDiGraph, parallel edges are counted and can therefore produce values
    above one.

    The caller remains responsible for constructing a bounded graph.  This
    function uses only linear graph traversals and does not mutate ``graph``.
    """
    metadata = _validate_mechanism_graph_contract(graph)

    components = list(nx.weakly_connected_components(graph))
    component_sizes = sorted(
        (len(component) for component in components),
        reverse=True,
    )
    centrality = nx.degree_centrality(graph)

    anchor_id: str | None = None
    downstream_node_ids: list[str] = []
    upstream_node_ids: list[str] = []
    downstream_species_ids: list[str] = []
    upstream_species_ids: list[str] = []
    anchor_smiles = metadata["anchor_smiles"]
    candidate_anchor_id = stable_mechanism_id("species", anchor_smiles)
    if candidate_anchor_id in graph:
        anchor_id = candidate_anchor_id
        downstream = nx.descendants(graph, anchor_id)
        upstream = nx.ancestors(graph, anchor_id)
        downstream_node_ids = sorted(downstream)
        upstream_node_ids = sorted(upstream)
        downstream_species_ids = sorted(
            node_id
            for node_id in downstream
            if graph.nodes[node_id]["kind"] == "species"
        )
        upstream_species_ids = sorted(
            node_id
            for node_id in upstream
            if graph.nodes[node_id]["kind"] == "species"
        )

    return {
        "weak_component_count": len(components),
        "weak_component_sizes": component_sizes,
        "anchor_id": anchor_id,
        "downstream_node_ids": downstream_node_ids,
        "upstream_node_ids": upstream_node_ids,
        "downstream_species_ids": downstream_species_ids,
        "upstream_species_ids": upstream_species_ids,
        "reachable_species_ids": sorted(
            set(downstream_species_ids) | set(upstream_species_ids)
        ),
        "degree_centrality": {
            node_id: centrality[node_id]
            for node_id in sorted(centrality)
        },
    }


def _validate_mechanism_graph_contract(
    graph: nx.DiGraph,
) -> dict[str, Any]:
    if not isinstance(graph, nx.DiGraph):
        raise ValueError("mechanism graph must be a directed NetworkX graph")

    metadata = dict(graph.graph)
    if (
        "network_semantics" not in metadata
        or "anchor_smiles" not in metadata
    ) and "name" in metadata:
        for name, value in decode_gexf_mechanism_metadata(graph).items():
            metadata.setdefault(name, value)

    if metadata.get("network_semantics") != "mechanism":
        raise ValueError(
            "mechanism graph network_semantics must be 'mechanism'"
        )
    anchor_smiles = metadata.get("anchor_smiles")
    if not isinstance(anchor_smiles, str) or not anchor_smiles:
        raise ValueError(
            "mechanism graph anchor_smiles must be a non-empty string"
        )

    for node_id, attributes in graph.nodes(data=True):
        if not isinstance(node_id, str) or not node_id:
            raise ValueError(
                "mechanism graph node IDs must be non-empty strings"
            )
        attribute_id = attributes.get("id")
        if attribute_id is not None and attribute_id != node_id:
            raise ValueError(
                f"mechanism graph node {node_id!r} id must match its key"
            )
        kind = attributes.get("kind")
        if kind == "species":
            identity_name = "smiles"
        elif kind == "reaction":
            identity_name = "reaction_key"
        else:
            raise ValueError(
                f"mechanism graph node {node_id!r} kind must be "
                "'species' or 'reaction'"
            )
        identity_value = attributes.get(identity_name)
        if not isinstance(identity_value, str) or not identity_value:
            raise ValueError(
                f"mechanism graph node {node_id!r} {identity_name} "
                "must be a non-empty string"
            )
        expected_id = stable_mechanism_id(kind, identity_value)
        if node_id != expected_id:
            raise ValueError(
                f"mechanism graph node {node_id!r} does not match "
                f"stable identity {expected_id!r}"
            )

    for source, target, attributes in _mechanism_graph_edges(graph):
        if source == target:
            raise ValueError(
                f"mechanism graph self-loop at {source!r} is invalid"
            )
        source_kind = graph.nodes[source]["kind"]
        target_kind = graph.nodes[target]["kind"]
        role = attributes.get("role")
        if role == "reactant":
            valid_endpoints = (
                source_kind == "species" and target_kind == "reaction"
            )
        elif role == "product":
            valid_endpoints = (
                source_kind == "reaction" and target_kind == "species"
            )
        else:
            raise ValueError(
                "mechanism graph edge role must be 'reactant' or 'product'"
            )
        if not valid_endpoints:
            raise ValueError(
                f"mechanism graph {role} edge {source!r}->{target!r} "
                "violates bipartite direction"
            )
    return metadata


def _mechanism_graph_edges(
    graph: nx.DiGraph,
) -> Iterable[tuple[str, str, Mapping[str, Any]]]:
    if graph.is_multigraph():
        for source, target, _key, attributes in graph.edges(
            keys=True,
            data=True,
        ):
            yield source, target, attributes
        return
    yield from graph.edges(data=True)


def validate_mechanism_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and copy one complete mechanism-network payload.

    This is the semantic firewall shared by every graph and tabular export.
    It deliberately validates plain records without routing them through
    NetworkX, so CSV serialization cannot silently reshape client-held data.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("mechanism payload must be a mapping")
    required = (
        "schema_version",
        "network_semantics",
        "evidence_level",
        "anchor_smiles",
        "nodes",
        "edges",
    )
    for name in required:
        if name not in payload:
            raise ValueError(f"mechanism payload is missing {name}")
    if payload["network_semantics"] != "mechanism":
        raise ValueError(
            "mechanism payload network_semantics must be 'mechanism'"
        )
    for name in ("schema_version", "evidence_level", "anchor_smiles"):
        if not isinstance(payload[name], str):
            raise ValueError(f"mechanism payload {name} must be a string")

    raw_nodes = payload["nodes"]
    raw_edges = payload["edges"]
    if not _is_record_sequence(raw_nodes):
        raise ValueError("mechanism payload nodes must be a sequence")
    if not _is_record_sequence(raw_edges):
        raise ValueError("mechanism payload edges must be a sequence")

    nodes: list[dict[str, Any]] = []
    node_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            raise ValueError(f"mechanism payload node {index} must be a mapping")
        node = dict(raw_node)
        node_id = _required_nonempty_string(node, "id", f"node {index}")
        kind = _required_nonempty_string(node, "kind", f"node {node_id}")
        if kind not in {"species", "reaction"}:
            raise ValueError(
                f"mechanism payload node {node_id} has invalid kind {kind!r}"
            )
        if node_id in node_by_id:
            raise ValueError(f"mechanism payload has duplicate node ID {node_id}")
        node_by_id[node_id] = node
        nodes.append(node)

    edges: list[dict[str, Any]] = []
    semantic_edge_keys: set[tuple[str, str, str]] = set()
    edge_ids: set[str] = set()
    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, Mapping):
            raise ValueError(f"mechanism payload edge {index} must be a mapping")
        edge = dict(raw_edge)
        edge_id = _required_nonempty_string(edge, "id", f"edge {index}")
        if edge_id in edge_ids:
            raise ValueError(f"mechanism payload has duplicate edge ID {edge_id}")
        edge_ids.add(edge_id)
        source = _required_nonempty_string(edge, "source", f"edge {edge_id}")
        target = _required_nonempty_string(edge, "target", f"edge {edge_id}")
        role = _required_nonempty_string(edge, "role", f"edge {edge_id}")
        if source not in node_by_id:
            raise ValueError(
                f"mechanism payload edge {edge_id} references unknown source "
                f"{source}"
            )
        if target not in node_by_id:
            raise ValueError(
                f"mechanism payload edge {edge_id} references unknown target "
                f"{target}"
            )
        if role == "reactant":
            species_id, reaction_id = source, target
        elif role == "product":
            reaction_id, species_id = source, target
        else:
            raise ValueError(
                f"mechanism payload edge {edge_id} has invalid role {role!r}"
            )
        if node_by_id[species_id]["kind"] != "species":
            raise ValueError(
                f"mechanism payload edge {edge_id} {role} species endpoint "
                "must have kind 'species'"
            )
        if node_by_id[reaction_id]["kind"] != "reaction":
            raise ValueError(
                f"mechanism payload edge {edge_id} {role} reaction endpoint "
                "must have kind 'reaction'"
            )
        semantic_key = (role, reaction_id, species_id)
        if semantic_key in semantic_edge_keys:
            raise ValueError(
                "mechanism payload has duplicate semantic edge key "
                f"{role}:{reaction_id}:{species_id}"
            )
        semantic_edge_keys.add(semantic_key)
        edges.append(edge)

    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            "mechanism payload schema_version must be "
            f"{SCHEMA_VERSION!r}"
        )
    if payload["evidence_level"] not in {
        "reaction_passage_counts",
        "event_evidence_linked",
    }:
        raise ValueError(
            "mechanism payload evidence_level is unsupported"
        )
    if not payload["anchor_smiles"]:
        raise ValueError(
            "mechanism payload anchor_smiles must be a non-empty string"
        )
    expected_evidence_status = (
        "evidence_linked"
        if payload["evidence_level"] == "event_evidence_linked"
        else "network_only"
    )
    expected_semantic_edges: dict[
        tuple[str, str, str],
        int,
    ] = {}
    species_ids_by_smiles: dict[str, str] = {}
    reaction_ids_by_key: dict[str, str] = {}

    for node in nodes:
        node_id = node["id"]
        kind = node["kind"]
        _required_nonempty_string(node, "label", f"node {node_id}")
        if kind == "species":
            smiles = _required_nonempty_string(
                node,
                "smiles",
                f"node {node_id}",
            )
            expected_node_id = stable_mechanism_id("species", smiles)
            if node_id != expected_node_id:
                raise ValueError(
                    f"mechanism payload species node {node_id} does not "
                    f"match stable identity {expected_node_id}"
                )
            _required_nullable_string(node, "formula", f"node {node_id}")
            existing_species_id = species_ids_by_smiles.get(smiles)
            if existing_species_id is not None:
                raise ValueError(
                    "mechanism payload has duplicate species identity "
                    f"{smiles!r} in {existing_species_id} and {node_id}"
                )
            species_ids_by_smiles[smiles] = node_id
            for reaction_field in (
                "reaction_key",
                "reactants",
                "products",
                "forward_tp",
                "reverse_tp",
                "net_tp",
                "event_total",
                "matched_event_total",
                "event_coverage",
                "evidence_status",
            ):
                if reaction_field in node:
                    raise ValueError(
                        f"mechanism payload species node {node_id} must not "
                        f"define {reaction_field}"
                    )
            continue
        _required_nullable_string(node, "formula", f"node {node_id}")
        reaction_key = _required_nonempty_string(
            node,
            "reaction_key",
            f"node {node_id}",
        )
        reactants = _required_string_sequence(
            node,
            "reactants",
            f"node {node_id}",
        )
        products = _required_string_sequence(
            node,
            "products",
            f"node {node_id}",
        )
        if not reactants or not products:
            raise ValueError(
                f"mechanism payload node {node_id} reaction sides "
                "must not be empty"
            )
        canonical_key = canonical_mechanism_reaction_key(
            reactants,
            products,
        )
        if reaction_key != canonical_key:
            raise ValueError(
                f"mechanism payload node {node_id} reaction_key is not "
                f"canonical for its reaction sides"
            )
        expected_node_id = stable_mechanism_id("reaction", reaction_key)
        if node_id != expected_node_id:
            raise ValueError(
                f"mechanism payload reaction node {node_id} does not match "
                f"stable identity {expected_node_id}"
            )
        existing_reaction_id = reaction_ids_by_key.get(reaction_key)
        if existing_reaction_id is not None:
            raise ValueError(
                "mechanism payload has duplicate reaction identity "
                f"{reaction_key!r} in {existing_reaction_id} and {node_id}"
            )
        reaction_ids_by_key[reaction_key] = node_id
        forward_tp = _required_nonnegative_int(
            node,
            "forward_tp",
            f"node {node_id}",
        )
        reverse_tp = _required_nonnegative_int(
            node,
            "reverse_tp",
            f"node {node_id}",
        )
        net_tp = _required_nonnegative_int(
            node,
            "net_tp",
            f"node {node_id}",
        )
        if net_tp != forward_tp - reverse_tp or net_tp < 1:
            raise ValueError(
                f"mechanism payload node {node_id} net_tp is inconsistent"
            )
        evidence_status = _required_nonempty_string(
            node,
            "evidence_status",
            f"node {node_id}",
        )
        if evidence_status not in {"network_only", "evidence_linked"}:
            raise ValueError(
                f"mechanism payload node {node_id} has invalid "
                "evidence_status"
            )
        if evidence_status != expected_evidence_status:
            raise ValueError(
                f"mechanism payload evidence_level contradicts node "
                f"{node_id} evidence_status"
            )
        _validate_reaction_evidence(node, node_id, evidence_status)
        for role, side in (
            ("reactant", reactants),
            ("product", products),
        ):
            for species_smiles, coefficient in Counter(side).items():
                expected_semantic_edges[
                    (role, node_id, species_smiles)
                ] = coefficient

    actual_semantic_edges: dict[tuple[str, str, str], int] = {}
    for edge in edges:
        edge_id = edge["id"]
        species_smiles = _required_nonempty_string(
            edge,
            "species_smiles",
            f"edge {edge_id}",
        )
        reaction_key = _required_nonempty_string(
            edge,
            "reaction_key",
            f"edge {edge_id}",
        )
        coefficient = _required_positive_int(
            edge,
            "coefficient",
            f"edge {edge_id}",
        )
        role = edge["role"]
        species_id = edge["source"] if role == "reactant" else edge["target"]
        reaction_id = edge["target"] if role == "reactant" else edge["source"]
        if node_by_id[species_id]["smiles"] != species_smiles:
            raise ValueError(
                f"mechanism payload edge {edge_id} species_smiles "
                "does not match its species node"
            )
        reaction_node = node_by_id[reaction_id]
        if reaction_node["reaction_key"] != reaction_key:
            raise ValueError(
                f"mechanism payload edge {edge_id} reaction_key "
                "does not match its reaction node"
            )
        expected_edge_id = stable_mechanism_edge_id(
            role,
            reaction_key,
            species_smiles,
        )
        if edge_id != expected_edge_id:
            raise ValueError(
                f"mechanism payload edge {edge_id} does not match stable "
                f"identity {expected_edge_id}"
            )
        expected_coefficient = Counter(
            reaction_node[
                "reactants" if role == "reactant" else "products"
            ]
        )[species_smiles]
        if coefficient != expected_coefficient:
            raise ValueError(
                f"mechanism payload edge {edge_id} coefficient "
                "does not match its reaction side"
            )
        semantic_edge = (role, reaction_id, species_smiles)
        if semantic_edge in actual_semantic_edges:
            raise ValueError(
                "mechanism payload has duplicate semantic reaction/species "
                f"edge {semantic_edge!r}"
            )
        actual_semantic_edges[semantic_edge] = coefficient

    if actual_semantic_edges != expected_semantic_edges:
        missing = sorted(
            set(expected_semantic_edges) - set(actual_semantic_edges)
        )
        extra = sorted(
            set(actual_semantic_edges) - set(expected_semantic_edges)
        )
        raise ValueError(
            "mechanism payload semantic edges do not exactly match reaction "
            f"sides (missing={missing!r}, extra={extra!r})"
        )

    validated = dict(payload)
    validated["nodes"] = nodes
    validated["edges"] = edges
    return validated


def _to_networkx_validated_mechanism_graph(
    payload: Mapping[str, Any],
) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.graph.update(
        schema_version=payload["schema_version"],
        network_semantics="mechanism",
        evidence_level=payload["evidence_level"],
        anchor_smiles=payload["anchor_smiles"],
    )
    for node in payload["nodes"]:
        graph.add_node(node["id"], **node)
    for edge in payload["edges"]:
        role = edge["role"]
        reaction_id = edge["target"] if role == "reactant" else edge["source"]
        species_id = edge["source"] if role == "reactant" else edge["target"]
        key = f"{role}:{reaction_id}:{species_id}"
        graph.add_edge(edge["source"], edge["target"], key=key, **edge)
    return graph


def serialize_mechanism_graph(
    graph: nx.MultiDiGraph,
    *,
    format: str = "cytoscape-json",
) -> dict[str, Any] | bytes:
    """Serialize a mechanism graph without mutating it or writing files."""
    if not isinstance(graph, nx.MultiDiGraph):
        raise ValueError("mechanism graph must be a networkx.MultiDiGraph")
    if format not in _SERIALIZATION_FORMATS:
        supported = ", ".join(_SERIALIZATION_FORMATS)
        raise ValueError(
            f"unknown mechanism graph format {format!r}; "
            f"valid formats: {supported}"
        )
    if format == "cytoscape-json":
        document = nx.cytoscape_data(graph)
        graph_data = document.get("data", {})
        if isinstance(graph_data, list):
            document["data"] = dict(graph_data)
        elif isinstance(graph_data, Mapping):
            document["data"] = dict(graph_data)
        else:
            raise ValueError("NetworkX returned invalid Cytoscape graph data")
        return document

    export_graph = _xml_safe_graph_copy(graph)
    buffer = io.BytesIO()
    if format == "graphml":
        nx.write_graphml(
            export_graph,
            buffer,
            encoding="utf-8",
            edge_id_from_attribute="id",
        )
    else:
        # NetworkX's GEXF writer only preserves the graph ``name`` field.
        # Reserve it for a versioned JSON envelope so required ReacNet Scope
        # metadata remains machine-readable without adding sentinel elements.
        export_graph.graph["name"] = _gexf_metadata_envelope(graph)
        nx.write_gexf(export_graph, buffer, encoding="utf-8")
    return buffer.getvalue()


def decode_gexf_mechanism_metadata(
    graph: nx.Graph,
) -> dict[str, str]:
    """Decode metadata stored in ReacNet Scope's reserved GEXF ``name`` field.

    ``serialize_mechanism_graph(..., format="gexf")`` reserves the graph
    ``name`` attribute for a canonical JSON envelope identified by
    ``reacnet-scope/gexf-metadata/v1``.  This is necessary because NetworkX's
    GEXF writer does not round-trip arbitrary graph attributes.
    """
    raw_name = graph.graph.get("name")
    if not isinstance(raw_name, str):
        raise ValueError("GEXF graph is missing ReacNet Scope metadata name")
    try:
        envelope = json.loads(raw_name)
    except json.JSONDecodeError as error:
        raise ValueError(
            "GEXF graph name is not valid ReacNet Scope metadata JSON"
        ) from error
    if not isinstance(envelope, Mapping):
        raise ValueError("GEXF graph metadata envelope must be a mapping")
    if envelope.get("format") != _GEXF_METADATA_FORMAT:
        raise ValueError(
            "GEXF graph metadata envelope has unsupported format"
        )
    metadata = envelope.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("GEXF graph metadata envelope is missing metadata")
    decoded: dict[str, str] = {}
    for field in _MECHANISM_METADATA_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, str):
            raise ValueError(
                f"GEXF graph metadata {field} must be a string"
            )
        decoded[field] = value
    return decoded


def _gexf_metadata_envelope(graph: nx.Graph) -> str:
    metadata: dict[str, str] = {}
    for field in _MECHANISM_METADATA_FIELDS:
        value = graph.graph.get(field)
        if not isinstance(value, str):
            raise ValueError(
                f"mechanism graph {field} must be a string for GEXF export"
            )
        metadata[field] = value
    return json.dumps(
        {"format": _GEXF_METADATA_FORMAT, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _is_record_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _required_nonempty_string(
    record: Mapping[str, Any],
    name: str,
    context: str,
) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"mechanism payload {context} {name} must be a non-empty string"
        )
    return value


def _required_nullable_string(
    record: Mapping[str, Any],
    name: str,
    context: str,
) -> str | None:
    if name not in record:
        raise ValueError(f"mechanism payload {context} is missing {name}")
    value = record[name]
    if value is not None and not isinstance(value, str):
        raise ValueError(
            f"mechanism payload {context} {name} must be a string or null"
        )
    return value


def _required_string_sequence(
    record: Mapping[str, Any],
    name: str,
    context: str,
) -> tuple[str, ...]:
    if name not in record:
        raise ValueError(f"mechanism payload {context} is missing {name}")
    value = record[name]
    if not _is_record_sequence(value) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(
            f"mechanism payload {context} {name} must be a sequence "
            "of non-empty strings"
        )
    return tuple(value)


def _required_nonnegative_int(
    record: Mapping[str, Any],
    name: str,
    context: str,
) -> int:
    if name not in record:
        raise ValueError(f"mechanism payload {context} is missing {name}")
    value = record[name]
    if not isinstance(value, Integral) or isinstance(value, bool) or value < 0:
        raise ValueError(
            f"mechanism payload {context} {name} must be a "
            "nonnegative integer"
        )
    return int(value)


def _required_positive_int(
    record: Mapping[str, Any],
    name: str,
    context: str,
) -> int:
    value = _required_nonnegative_int(record, name, context)
    if value < 1:
        raise ValueError(
            f"mechanism payload {context} {name} must be a positive integer"
        )
    return value


def _validate_reaction_evidence(
    node: Mapping[str, Any],
    node_id: str,
    evidence_status: str,
) -> None:
    context = f"node {node_id}"
    fields = ("event_total", "matched_event_total", "event_coverage")
    for name in fields:
        if name not in node:
            raise ValueError(f"mechanism payload {context} is missing {name}")
    if evidence_status == "network_only":
        if any(node[name] is not None for name in fields):
            raise ValueError(
                f"mechanism payload {context} network-only evidence "
                "fields must be null"
            )
        return

    event_total = _required_nonnegative_int(node, "event_total", context)
    matched_event_total = _required_nonnegative_int(
        node,
        "matched_event_total",
        context,
    )
    if matched_event_total > event_total:
        raise ValueError(
            f"mechanism payload {context} matched_event_total "
            "must not exceed event_total"
        )
    coverage = node["event_coverage"]
    if (
        not isinstance(coverage, Real)
        or isinstance(coverage, bool)
        or not math.isfinite(float(coverage))
        or not 0.0 <= float(coverage) <= 1.0
    ):
        raise ValueError(
            f"mechanism payload {context} event_coverage must be "
            "a finite value from 0 to 1"
        )
    expected = matched_event_total / event_total if event_total else 0.0
    if not math.isclose(float(coverage), expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(
            f"mechanism payload {context} event_coverage is inconsistent"
        )


def _xml_safe_graph_copy(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    copied = nx.MultiDiGraph()
    copied.graph.update(
        {key: _xml_attribute(value) for key, value in graph.graph.items()}
    )
    for node_id, attributes in graph.nodes(data=True):
        copied.add_node(
            node_id,
            **{
                key: _xml_attribute(value)
                for key, value in attributes.items()
            },
        )
    for source, target, key, attributes in graph.edges(
        keys=True,
        data=True,
    ):
        edge_attributes = {
            name: _xml_attribute(value)
            for name, value in attributes.items()
        }
        # GraphML consumes the payload ``id`` as the XML edge identifier.
        # Preserve the MultiDiGraph key separately for semantic round trips.
        edge_attributes[_GRAPHML_SEMANTIC_KEY_ATTRIBUTE] = str(key)
        copied.add_edge(
            source,
            target,
            key=key,
            **edge_attributes,
        )
    return copied


def _xml_attribute(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Decimal):
        return _decimal_json_safe(value)
    if isinstance(value, Real):
        return _real_json_safe(value)
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return json.dumps(
            _json_safe_attribute(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    return str(value)


def _json_safe_attribute(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Decimal):
        return _decimal_json_safe(value)
    if isinstance(value, Real):
        return _real_json_safe(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_attribute(item)
            for key, item in value.items()
        }
    if isinstance(value, (set, frozenset)):
        items = [_json_safe_attribute(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        )
    if isinstance(value, (list, tuple)):
        return [_json_safe_attribute(item) for item in value]
    return str(value)


def _real_json_safe(value: Real) -> float | str:
    converted = float(value)
    if math.isnan(converted):
        return "NaN"
    if math.isinf(converted):
        return "-Infinity" if converted < 0 else "Infinity"
    return converted


def _decimal_json_safe(value: Decimal) -> str:
    if value.is_nan():
        return "NaN"
    if value.is_infinite():
        return "-Infinity" if value.is_signed() else "Infinity"
    return str(value)


def build_mechanism_network(
    network: ReactionNetwork,
    *,
    anchor_smiles: str,
    direction: Literal["downstream", "upstream", "both"] = "both",
    max_depth: int = 2,
    min_net_tp: int = 1,
    max_nodes: int = 200,
    evidence_provider: EvidenceProvider | None = None,
) -> dict[str, Any]:
    """Build a deterministic, bounded bipartite mechanism neighborhood.

    ``max_depth`` is measured in chemical steps: a
    species → reaction → species traversal has depth one.  Reactions are
    normalized against their explicit reverse record and only the
    positive-net orientation is retained.
    """
    _validate_query(
        anchor_smiles=anchor_smiles,
        direction=direction,
        max_depth=max_depth,
        min_net_tp=min_net_tp,
        max_nodes=max_nodes,
    )
    query = {
        "direction": direction,
        "max_depth": max_depth,
        "min_net_tp": min_net_tp,
        "max_nodes": max_nodes,
    }

    if anchor_smiles not in network.species:
        return _payload(
            anchor_smiles=anchor_smiles,
            query=query,
            nodes=(),
            edges=(),
            source_signatures={},
            evidence_level="reaction_passage_counts",
            truncated=False,
            reason="species_absent",
        )

    oriented_reactions = _normalize_reversible_pairs(network)
    downstream, upstream = _build_adjacency(oriented_reactions)
    root_candidates = _reaction_candidates(
        anchor_smiles,
        direction=direction,
        downstream=downstream,
        upstream=upstream,
    )
    if not root_candidates:
        reason = "no_positive_net_continuation"
    elif not any(reaction.net_tp >= min_net_tp for reaction in root_candidates):
        reason = "filtered_by_thresholds"
    else:
        reason = "ok"

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    _insert_node(nodes, _species_node(anchor_smiles))

    frontier: list[tuple[int, str]] = [(0, anchor_smiles)]
    best_depth = {anchor_smiles: 0}
    processed: set[str] = set()
    truncated = False

    while frontier:
        depth, species_smiles = heapq.heappop(frontier)
        if best_depth.get(species_smiles) != depth or species_smiles in processed:
            continue
        processed.add(species_smiles)
        if depth >= max_depth:
            continue

        candidates = _reaction_candidates(
            species_smiles,
            direction=direction,
            downstream=downstream,
            upstream=upstream,
        )
        for reaction in candidates:
            if reaction.net_tp < min_net_tp:
                continue
            reaction_node = _reaction_node(reaction)
            species_nodes = [
                _species_node(smiles)
                for smiles in sorted(set(reaction.reactants + reaction.products))
            ]
            candidate_nodes = (reaction_node, *species_nodes)
            _validate_node_ids(nodes, candidate_nodes)
            required_nodes = [
                node
                for node in candidate_nodes
                if node["id"] not in nodes
            ]
            if len(nodes) + len(required_nodes) > max_nodes:
                truncated = True
                continue

            for node in required_nodes:
                _insert_node(nodes, node)
            for edge in _reaction_edges(reaction, reaction_node["id"]):
                _insert_edge(edges, edge)

            if depth + 1 >= max_depth:
                continue
            for continuation in _continuations(
                reaction,
                species_smiles=species_smiles,
                direction=direction,
            ):
                next_depth = depth + 1
                known_depth = best_depth.get(continuation)
                if known_depth is None or next_depth < known_depth:
                    best_depth[continuation] = next_depth
                    heapq.heappush(frontier, (next_depth, continuation))

    source_signatures: Mapping[str, Any] = {}
    linked_evidence = evidence_provider is not None
    if evidence_provider is not None:
        reaction_keys = tuple(
            sorted(
                node["reaction_key"]
                for node in nodes.values()
                if node["kind"] == "reaction"
            )
        )
        summaries = evidence_provider.reaction_summaries(reaction_keys)
        if not isinstance(summaries, Mapping):
            raise ValueError("evidence provider summaries must be a mapping")
        source_signatures = _provider_source_signatures(evidence_provider)
        for node in nodes.values():
            if node["kind"] != "reaction":
                continue
            reaction_key = node["reaction_key"]
            summary = summaries.get(reaction_key, {})
            if not isinstance(summary, Mapping):
                raise ValueError("evidence summary must be a mapping")
            event_total = _summary_count(summary, "total_events", "event_total")
            matched_event_total = _summary_count(
                summary,
                "matched_events",
                "matched_event_total",
            )
            if matched_event_total > event_total:
                raise ValueError(
                    "matched event count must not exceed total event count"
                )
            node.update(
                event_total=event_total,
                matched_event_total=matched_event_total,
                event_coverage=(
                    matched_event_total / event_total if event_total else 0.0
                ),
                evidence_status="evidence_linked",
            )

    ordered_nodes = tuple(
        sorted(nodes.values(), key=lambda node: (node["kind"], node["id"]))
    )
    ordered_edges = tuple(sorted(edges.values(), key=lambda edge: edge["id"]))
    return _payload(
        anchor_smiles=anchor_smiles,
        query=query,
        nodes=ordered_nodes,
        edges=ordered_edges,
        source_signatures=source_signatures,
        evidence_level=(
            "event_evidence_linked"
            if linked_evidence
            else "reaction_passage_counts"
        ),
        truncated=truncated,
        reason=reason,
    )


def _validate_query(
    *,
    anchor_smiles: object,
    direction: object,
    max_depth: object,
    min_net_tp: object,
    max_nodes: object,
) -> None:
    if not isinstance(anchor_smiles, str) or not anchor_smiles.strip():
        raise ValueError("anchor_smiles must be a non-empty string")
    if (
        not isinstance(direction, str)
        or direction not in {"downstream", "upstream", "both"}
    ):
        raise ValueError(
            "direction must be 'downstream', 'upstream', or 'both'"
        )
    _validate_positive_int("max_depth", max_depth)
    _validate_positive_int("min_net_tp", min_net_tp)
    _validate_positive_int("max_nodes", max_nodes)


def _validate_positive_int(name: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be >= 1")


def _reverse_key(reaction: Reaction) -> str:
    return canonical_mechanism_reaction_key(
        reaction.product_smiles,
        reaction.reactant_smiles,
    )


def _normalize_reversible_pairs(
    network: ReactionNetwork,
) -> tuple[_OrientedReaction, ...]:
    normalized: list[_OrientedReaction] = []
    seen_pairs: set[str] = set()
    for reaction in sorted(network.reactions, key=lambda item: item.key):
        reverse_key = _reverse_key(reaction)
        pair_key = min(reaction.key, reverse_key)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        reverse = network.find_reverse(reaction)
        reverse_tp = reverse.tp if reverse is not None else 0
        if reaction.tp > reverse_tp:
            oriented = reaction
            forward_tp = reaction.tp
            backward_tp = reverse_tp
        elif reverse is not None and reverse.tp > reaction.tp:
            oriented = reverse
            forward_tp = reverse.tp
            backward_tp = reaction.tp
        else:
            continue

        normalized.append(
            _OrientedReaction(
                reaction_key=canonical_mechanism_reaction_key(
                    oriented.reactant_smiles,
                    oriented.product_smiles,
                ),
                reactants=tuple(sorted(oriented.reactant_smiles)),
                products=tuple(sorted(oriented.product_smiles)),
                forward_tp=forward_tp,
                reverse_tp=backward_tp,
                net_tp=forward_tp - backward_tp,
            )
        )
    return tuple(
        sorted(normalized, key=lambda item: (-item.net_tp, item.reaction_key))
    )


def _build_adjacency(
    reactions: Sequence[_OrientedReaction],
) -> tuple[
    dict[str, tuple[_OrientedReaction, ...]],
    dict[str, tuple[_OrientedReaction, ...]],
]:
    downstream_lists: dict[str, list[_OrientedReaction]] = defaultdict(list)
    upstream_lists: dict[str, list[_OrientedReaction]] = defaultdict(list)
    for reaction in reactions:
        for smiles in set(reaction.reactants):
            downstream_lists[smiles].append(reaction)
        for smiles in set(reaction.products):
            upstream_lists[smiles].append(reaction)
    sort_key = lambda item: (-item.net_tp, item.reaction_key)
    downstream = {
        smiles: tuple(sorted(items, key=sort_key))
        for smiles, items in downstream_lists.items()
    }
    upstream = {
        smiles: tuple(sorted(items, key=sort_key))
        for smiles, items in upstream_lists.items()
    }
    return downstream, upstream


def _reaction_candidates(
    species_smiles: str,
    *,
    direction: str,
    downstream: Mapping[str, Sequence[_OrientedReaction]],
    upstream: Mapping[str, Sequence[_OrientedReaction]],
) -> tuple[_OrientedReaction, ...]:
    candidates: dict[str, _OrientedReaction] = {}
    if direction in {"downstream", "both"}:
        for reaction in downstream.get(species_smiles, ()):
            candidates[reaction.reaction_key] = reaction
    if direction in {"upstream", "both"}:
        for reaction in upstream.get(species_smiles, ()):
            candidates[reaction.reaction_key] = reaction
    return tuple(
        sorted(
            candidates.values(),
            key=lambda item: (-item.net_tp, item.reaction_key),
        )
    )


def _continuations(
    reaction: _OrientedReaction,
    *,
    species_smiles: str,
    direction: str,
) -> tuple[str, ...]:
    continuations: set[str] = set()
    if direction in {"downstream", "both"} and species_smiles in reaction.reactants:
        continuations.update(reaction.products)
    if direction in {"upstream", "both"} and species_smiles in reaction.products:
        continuations.update(reaction.reactants)
    return tuple(sorted(continuations))


def _species_node(smiles: str) -> dict[str, Any]:
    formula = smiles_to_formula_fast(smiles)
    return {
        "id": stable_mechanism_id("species", smiles),
        "kind": "species",
        "label": formula or smiles,
        "smiles": smiles,
        "formula": formula,
    }


def _reaction_node(reaction: _OrientedReaction) -> dict[str, Any]:
    reactant_formulas = [
        smiles_to_formula_fast(smiles) or smiles for smiles in reaction.reactants
    ]
    product_formulas = [
        smiles_to_formula_fast(smiles) or smiles for smiles in reaction.products
    ]
    return {
        "id": stable_mechanism_id("reaction", reaction.reaction_key),
        "kind": "reaction",
        "label": "+".join(reactant_formulas) + " → " + "+".join(product_formulas),
        "formula": "+".join(reactant_formulas)
        + "->"
        + "+".join(product_formulas),
        "reaction_key": reaction.reaction_key,
        "reactants": list(reaction.reactants),
        "products": list(reaction.products),
        "forward_tp": reaction.forward_tp,
        "reverse_tp": reaction.reverse_tp,
        "net_tp": reaction.net_tp,
        "event_total": None,
        "matched_event_total": None,
        "event_coverage": None,
        "evidence_status": "network_only",
    }


def _reaction_edges(
    reaction: _OrientedReaction,
    reaction_id: str,
) -> tuple[dict[str, Any], ...]:
    edges: list[dict[str, Any]] = []
    for role, side in (
        ("reactant", reaction.reactants),
        ("product", reaction.products),
    ):
        for species_smiles, coefficient in sorted(Counter(side).items()):
            species_id = stable_mechanism_id("species", species_smiles)
            if role == "reactant":
                source, target = species_id, reaction_id
            else:
                source, target = reaction_id, species_id
            edges.append(
                {
                    "id": stable_mechanism_edge_id(
                        role,
                        reaction.reaction_key,
                        species_smiles,
                    ),
                    "source": source,
                    "target": target,
                    "role": role,
                    "species_smiles": species_smiles,
                    "coefficient": coefficient,
                    "reaction_key": reaction.reaction_key,
                }
            )
    return tuple(sorted(edges, key=lambda edge: edge["id"]))


def _insert_node(
    nodes: dict[str, dict[str, Any]],
    node: dict[str, Any],
) -> None:
    existing = nodes.get(node["id"])
    if existing is not None and existing != node:
        raise ValueError(f"stable node ID collision: {node['id']}")
    nodes[node["id"]] = node


def _validate_node_ids(
    nodes: Mapping[str, Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> None:
    combined = dict(nodes)
    for node in candidates:
        existing = combined.get(node["id"])
        if existing is not None and existing != node:
            raise ValueError(f"stable node ID collision: {node['id']}")
        combined[node["id"]] = node


def _insert_edge(
    edges: dict[str, dict[str, Any]],
    edge: dict[str, Any],
) -> None:
    existing = edges.get(edge["id"])
    if existing is not None and existing != edge:
        raise ValueError(f"stable edge ID collision: {edge['id']}")
    edges[edge["id"]] = edge


def _provider_source_signatures(
    evidence_provider: EvidenceProvider,
) -> Mapping[str, Any]:
    signatures: Any = getattr(evidence_provider, "source_signatures", {})
    if callable(signatures):
        signatures = signatures()
    if not isinstance(signatures, Mapping):
        raise ValueError("evidence provider source_signatures must be a mapping")
    return dict(signatures)


def _summary_count(
    summary: Mapping[str, Any],
    key: str,
    fallback_key: str,
) -> int:
    value = summary.get(key)
    if value is None:
        value = summary.get(fallback_key)
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"evidence summary {key} must be a nonnegative integer")
    return value


def _payload(
    *,
    anchor_smiles: str,
    query: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    source_signatures: Mapping[str, Any],
    evidence_level: str,
    truncated: bool,
    reason: str,
) -> dict[str, Any]:
    node_list = [dict(node) for node in nodes]
    edge_list = [dict(edge) for edge in edges]
    return {
        "schema_version": SCHEMA_VERSION,
        "network_semantics": "mechanism",
        "evidence_level": evidence_level,
        "anchor_smiles": anchor_smiles,
        "query": dict(query),
        "source_signatures": dict(source_signatures),
        "nodes": node_list,
        "edges": edge_list,
        "meta": {
            "node_count": len(node_list),
            "edge_count": len(edge_list),
            "reaction_count": sum(
                node.get("kind") == "reaction" for node in node_list
            ),
            "truncated": truncated,
            "reason": reason,
        },
    }
