"""Stable bounded payloads for chemistry-facing mechanism networks.

The payload is deliberately bipartite: species nodes connect to explicit
reaction nodes.  Passage counts describe ReacNetGenerator observations, not
kinetic rates or atom-transfer fluxes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import heapq
from typing import Any, Literal

from rng_tools.network import Reaction, ReactionNetwork, smiles_to_formula_fast
from rng_tools.pathways import EvidenceProvider


SCHEMA_VERSION = "reacnet-scope/mechanism-network/v1"


@dataclass(frozen=True)
class _OrientedReaction:
    reaction_key: str
    reactants: tuple[str, ...]
    products: tuple[str, ...]
    forward_tp: int
    reverse_tp: int
    net_tp: int


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
    linked_evidence = False
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
            if reaction_key not in summaries:
                continue
            summary = summaries[reaction_key]
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
            linked_evidence = True

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
    return (
        "+".join(sorted(reaction.product_smiles))
        + "->"
        + "+".join(sorted(reaction.reactant_smiles))
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
                reaction_key=oriented.key,
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


def _stable_id(kind: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{digest}"


def _species_node(smiles: str) -> dict[str, Any]:
    formula = smiles_to_formula_fast(smiles)
    return {
        "id": _stable_id("species", smiles),
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
        "id": _stable_id("reaction", reaction.reaction_key),
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
            species_id = _stable_id("species", species_smiles)
            if role == "reactant":
                source, target = species_id, reaction_id
            else:
                source, target = reaction_id, species_id
            edge_seed = "\0".join(
                (role, reaction.reaction_key, species_smiles)
            )
            edges.append(
                {
                    "id": _stable_id("edge", edge_seed),
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
