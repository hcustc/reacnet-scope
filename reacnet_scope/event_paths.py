"""Time-ordered, atom-continuous paths through concrete RNG events.

The aggregate reaction network can connect reaction *types* through a shared
species name.  This module deliberately applies a stronger rule to concrete
events: a product molecule instance must be consumed by a strictly later
event, and at least one atom must remain continuous across every edge in the
path.  The implementation reads an already prepared event evidence index and
never reconstructs reactions from coordinates.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean, median
from typing import Any, Callable, Iterable, Sequence

from rng_tools.network import Reaction, ReactionNetwork, parse_reactionabcd

from .event_index import (
    EVENT_EVIDENCE_STORE,
    _EVENT_SELECT_COLUMNS,
    _event_payload_from_record,
)
from .indexes import IndexInvalidError, _readonly_connection
from .rng_events import reaction_key


EVENT_PATH_SCHEMA_VERSION = "event-path/v1"


class EventPathAnalysisError(RuntimeError):
    """Raised when indexed evidence cannot support atom-continuous paths."""


@dataclass(frozen=True)
class EventPathSource:
    """One repeat used in an event-path analysis.

    All supplied sources are interpreted as independent repeats of the same
    comparison group.  Atom IDs are therefore scoped by ``replicate`` when
    independent lineage support is counted.
    """

    replicate: str
    reactionevent_file: str
    molecules_file: str
    reaction_file: str = ""

    def __post_init__(self) -> None:
        replicate = str(self.replicate or "").strip()
        reactionevent_file = str(self.reactionevent_file or "").strip()
        molecules_file = str(self.molecules_file or "").strip()
        reaction_file = str(self.reaction_file or "").strip()
        if not replicate:
            raise ValueError("replicate is required")
        if not reactionevent_file:
            raise ValueError("reactionevent_file is required")
        if (
            not molecules_file
            and not reactionevent_file.lower().endswith(".timeline.h5")
        ):
            raise ValueError(
                "molecules_file is required for legacy CSV evidence"
            )
        object.__setattr__(self, "replicate", replicate)
        object.__setattr__(self, "reactionevent_file", reactionevent_file)
        object.__setattr__(self, "molecules_file", molecules_file)
        object.__setattr__(self, "reaction_file", reaction_file)


@dataclass(frozen=True)
class _EventNode:
    event_id: str
    reaction_key: str
    timestep_index: int
    before_timestep: int
    after_timestep: int
    source_row: int
    association_status: str
    reaction_smiles: str
    atom_ids: tuple[int, ...]
    reactant_terms: tuple[str, ...]
    product_terms: tuple[str, ...]
    reactant_instances: tuple[tuple[str, tuple[int, ...]], ...]
    product_instances: tuple[tuple[str, tuple[int, ...]], ...]


@dataclass(frozen=True)
class _EventEdge:
    from_event_id: str
    to_event_id: str
    molecule_instances: tuple[tuple[str, tuple[int, ...]], ...]
    carrier_atom_ids: frozenset[int]
    interval_gap: int
    idle_timestep_gap: int
    anchor_timestep_gap: int


@dataclass
class _SignatureAggregate:
    reaction_keys: tuple[str, ...]
    occurrence_count: int = 0
    supporting_replicates: set[str] = field(default_factory=set)
    atom_lineages: set[tuple[str, int]] = field(default_factory=set)
    lineage_sets: set[tuple[str, tuple[int, ...]]] = field(default_factory=set)
    interval_gaps: list[list[int]] = field(default_factory=list)
    idle_timestep_gaps: list[list[int]] = field(default_factory=list)
    anchor_timestep_gaps: list[list[int]] = field(default_factory=list)
    interval_spans: list[int] = field(default_factory=list)
    timestep_spans: list[int] = field(default_factory=list)
    example_path_ids: list[str] = field(default_factory=list)

    def add(self, occurrence: dict[str, Any]) -> None:
        replicate = str(occurrence["replicate"])
        lineage = tuple(int(value) for value in occurrence["lineage_atom_ids"])
        self.occurrence_count += 1
        self.supporting_replicates.add(replicate)
        self.atom_lineages.update((replicate, atom_id) for atom_id in lineage)
        self.lineage_sets.add((replicate, lineage))
        if not self.interval_gaps:
            edge_count = len(occurrence["edges"])
            self.interval_gaps = [[] for _ in range(edge_count)]
            self.idle_timestep_gaps = [[] for _ in range(edge_count)]
            self.anchor_timestep_gaps = [[] for _ in range(edge_count)]
        for index, edge in enumerate(occurrence["edges"]):
            self.interval_gaps[index].append(int(edge["interval_gap"]))
            self.idle_timestep_gaps[index].append(
                int(edge["idle_timestep_gap"])
            )
            self.anchor_timestep_gaps[index].append(
                int(edge["anchor_timestep_gap"])
            )
        self.interval_spans.append(int(occurrence["interval_span"]))
        self.timestep_spans.append(int(occurrence["anchor_timestep_span"]))
        if len(self.example_path_ids) < 5:
            self.example_path_ids.append(str(occurrence["path_id"]))


@dataclass
class _TraversalState:
    expansions: int = 0
    path_count: int = 0
    truncated: bool = False


def _bounded_integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed != value or parsed < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{label} must be at most {maximum}")
    return parsed


def _optional_nonnegative_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _bounded_integer(value, label, minimum=0)


def _instance_key(participant: dict[str, Any]) -> tuple[str, tuple[int, ...]] | None:
    species = str(participant.get("species", "")).strip()
    atom_ids = tuple(
        sorted({int(atom_id) for atom_id in participant.get("atom_ids", [])})
    )
    if not species or not atom_ids:
        return None
    return species, atom_ids


def _load_event_nodes(source: EventPathSource) -> tuple[list[_EventNode], dict[str, Any]]:
    opened = EVENT_EVIDENCE_STORE.open_required(
        source.reactionevent_file,
        source.molecules_file,
    )
    if not opened["association_available"]:
        raise EventPathAnalysisError(
            f"repeat {source.replicate!r} has no molecule/atom association; "
            "rebuild the event index with molecular evidence"
        )
    connection = _readonly_connection(Path(opened["index_path"]))
    nodes: list[_EventNode] = []
    try:
        records = connection.execute(
            f"""
            SELECT {_EVENT_SELECT_COLUMNS}
            FROM events
            ORDER BY timestep_index,source_row,event_id
            """
        )
        for record in records:
            try:
                payload = _event_payload_from_record(
                    record, event_index=len(nodes) + 1
                )
                stored_reaction_key = str(record[1])
                reactant_instances = tuple(
                    value
                    for participant in payload["reactant_participants"]
                    if (value := _instance_key(participant)) is not None
                )
                product_instances = tuple(
                    value
                    for participant in payload["product_participants"]
                    if (value := _instance_key(participant)) is not None
                )
                reactant_terms, product_terms = reaction_key(
                    str(payload["reactant"]),
                    str(payload["product"]),
                )
                nodes.append(
                    _EventNode(
                        event_id=str(payload["event_id"]),
                        reaction_key=stored_reaction_key,
                        timestep_index=int(payload["timestep_index"]),
                        before_timestep=int(payload["before_timestep"]),
                        after_timestep=int(payload["after_timestep"]),
                        source_row=int(payload["source_row"]),
                        association_status=str(payload["association_status"]),
                        reaction_smiles=str(payload["reaction_smiles"]),
                        atom_ids=tuple(
                            int(value) for value in payload["atom_id_list"]
                        ),
                        reactant_terms=tuple(reactant_terms),
                        product_terms=tuple(product_terms),
                        reactant_instances=reactant_instances,
                        product_instances=product_instances,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise IndexInvalidError(
                    "Event evidence index event-path payload is invalid: "
                    f"{exc}"
                ) from exc
    except sqlite3.Error as exc:
        raise IndexInvalidError(
            f"Event evidence index event-path scan failed: {exc}"
        ) from exc
    finally:
        connection.close()
    return nodes, opened


def _build_event_edges(
    nodes: Sequence[_EventNode],
) -> tuple[list[_EventEdge], dict[str, int]]:
    """Link a product instance only to its first unambiguous consumer."""

    active_producer: dict[tuple[str, tuple[int, ...]], str] = {}
    node_by_id = {node.event_id: node for node in nodes}
    edge_instances: dict[
        tuple[str, str], set[tuple[str, tuple[int, ...]]]
    ] = defaultdict(set)
    ambiguous_instance_count = 0
    connectable_node_count = 0
    unresolved_node_count = 0
    unresolved_species_barrier_count = 0

    by_interval: dict[int, list[_EventNode]] = defaultdict(list)
    for node in nodes:
        if node.association_status == "matched" and (
            node.reactant_instances or node.product_instances
        ):
            connectable_node_count += 1
        elif node.association_status != "matched":
            unresolved_node_count += 1
        by_interval[node.timestep_index].append(node)

    for timestep_index in sorted(by_interval):
        interval_nodes = sorted(
            by_interval[timestep_index],
            key=lambda node: (node.source_row, node.event_id),
        )
        unresolved_species = {
            species
            for node in interval_nodes
            if node.association_status != "matched"
            for species in (*node.reactant_terms, *node.product_terms)
        }
        if unresolved_species:
            invalidated = [
                instance
                for instance in active_producer
                if instance[0] in unresolved_species
            ]
            unresolved_species_barrier_count += len(invalidated)
            for instance in invalidated:
                active_producer.pop(instance, None)

        matched_nodes = [
            node
            for node in interval_nodes
            if node.association_status == "matched"
        ]
        consumers: dict[
            tuple[str, tuple[int, ...]], list[str]
        ] = defaultdict(list)
        producers: dict[
            tuple[str, tuple[int, ...]], list[str]
        ] = defaultdict(list)
        for node in matched_nodes:
            for instance in set(node.reactant_instances):
                consumers[instance].append(node.event_id)
            for instance in set(node.product_instances):
                producers[instance].append(node.event_id)

        # All products are activated only after the complete interval has
        # consumed its reactants.  Events authored in the same interval are
        # therefore never assigned an invented order.
        for instance, consumer_ids in consumers.items():
            previous_event_id = active_producer.pop(instance, None)
            unique_consumers = sorted(set(consumer_ids))
            if len(unique_consumers) != 1:
                ambiguous_instance_count += 1
                continue
            if previous_event_id is not None:
                previous = node_by_id[previous_event_id]
                current = node_by_id[unique_consumers[0]]
                if previous.timestep_index < current.timestep_index:
                    edge_instances[
                        (previous.event_id, current.event_id)
                    ].add(instance)

        for instance, producer_ids in producers.items():
            unique_producers = sorted(set(producer_ids))
            if len(unique_producers) != 1:
                active_producer.pop(instance, None)
                ambiguous_instance_count += 1
                continue
            active_producer[instance] = unique_producers[0]

    edges: list[_EventEdge] = []
    for (from_event_id, to_event_id), instances in edge_instances.items():
        previous = node_by_id[from_event_id]
        current = node_by_id[to_event_id]
        ordered_instances = tuple(
            sorted(instances, key=lambda item: (item[0], item[1]))
        )
        carrier_atom_ids = frozenset(
            atom_id
            for _species, atom_ids in ordered_instances
            for atom_id in atom_ids
        )
        edges.append(
            _EventEdge(
                from_event_id=from_event_id,
                to_event_id=to_event_id,
                molecule_instances=ordered_instances,
                carrier_atom_ids=carrier_atom_ids,
                interval_gap=current.timestep_index - previous.timestep_index,
                idle_timestep_gap=max(
                    0, current.before_timestep - previous.after_timestep
                ),
                anchor_timestep_gap=(
                    current.after_timestep - previous.after_timestep
                ),
            )
        )
    edges.sort(
        key=lambda edge: (
            node_by_id[edge.from_event_id].timestep_index,
            node_by_id[edge.from_event_id].source_row,
            node_by_id[edge.to_event_id].timestep_index,
            node_by_id[edge.to_event_id].source_row,
            edge.from_event_id,
            edge.to_event_id,
        )
    )
    return edges, {
        "event_node_count": len(nodes),
        "connectable_event_node_count": connectable_node_count,
        "event_edge_count": len(edges),
        "ambiguous_molecule_instance_count": ambiguous_instance_count,
        "unresolved_event_node_count": unresolved_node_count,
        "unresolved_species_barrier_count": (
            unresolved_species_barrier_count
        ),
    }


def _node_document(node: _EventNode) -> dict[str, Any]:
    return {
        "event_id": node.event_id,
        "reaction_key": node.reaction_key,
        "reaction_smiles": node.reaction_smiles,
        "timestep_index": node.timestep_index,
        "before_timestep": node.before_timestep,
        "after_timestep": node.after_timestep,
        "atom_ids": list(node.atom_ids),
        "reactant_instances": [
            {"species": species, "atom_ids": list(atom_ids)}
            for species, atom_ids in node.reactant_instances
        ],
        "product_instances": [
            {"species": species, "atom_ids": list(atom_ids)}
            for species, atom_ids in node.product_instances
        ],
    }


def _edge_document(edge: _EventEdge) -> dict[str, Any]:
    return {
        "from_event_id": edge.from_event_id,
        "to_event_id": edge.to_event_id,
        "molecule_instances": [
            {"species": species, "atom_ids": list(atom_ids)}
            for species, atom_ids in edge.molecule_instances
        ],
        "carrier_atom_ids": sorted(edge.carrier_atom_ids),
        "interval_gap": edge.interval_gap,
        "idle_timestep_gap": edge.idle_timestep_gap,
        "anchor_timestep_gap": edge.anchor_timestep_gap,
    }


def _path_identifier(replicate: str, event_ids: Sequence[str]) -> str:
    digest = hashlib.sha1(
        (replicate + "\0" + "\0".join(event_ids)).encode("utf-8")
    ).hexdigest()[:16]
    return f"rngpath_{digest}"


def _signature_identifier(reaction_keys: Sequence[str]) -> str:
    digest = hashlib.sha1("\0".join(reaction_keys).encode("utf-8")).hexdigest()[:16]
    return f"rngpathsig_{digest}"


def _enumerate_actual_paths(
    replicate: str,
    nodes: Sequence[_EventNode],
    edges: Sequence[_EventEdge],
    *,
    path_length: int,
    start_smiles: str,
    max_interval_gap: int | None,
    max_timestep_gap: int | None,
    max_expansions: int,
    on_path: Callable[[dict[str, Any]], None],
) -> _TraversalState:
    node_by_id = {node.event_id: node for node in nodes}
    adjacency: dict[str, list[_EventEdge]] = defaultdict(list)
    for edge in edges:
        if max_interval_gap is not None and edge.interval_gap > max_interval_gap:
            continue
        if (
            max_timestep_gap is not None
            and edge.idle_timestep_gap > max_timestep_gap
        ):
            continue
        adjacency[edge.from_event_id].append(edge)
    for edge_list in adjacency.values():
        edge_list.sort(
            key=lambda edge: (
                node_by_id[edge.to_event_id].timestep_index,
                node_by_id[edge.to_event_id].source_row,
                edge.to_event_id,
            )
        )

    state = _TraversalState()

    def emit(
        path_nodes: list[_EventNode],
        path_edges: list[_EventEdge],
        lineage: frozenset[int],
    ) -> None:
        event_ids = [node.event_id for node in path_nodes]
        occurrence = {
            "path_id": _path_identifier(replicate, event_ids),
            "replicate": replicate,
            "event_ids": event_ids,
            "reaction_keys": [node.reaction_key for node in path_nodes],
            "lineage_atom_ids": sorted(lineage),
            "lineage_atom_support_count": len(lineage),
            "events": [_node_document(node) for node in path_nodes],
            "edges": [_edge_document(edge) for edge in path_edges],
            "interval_span": (
                path_nodes[-1].timestep_index - path_nodes[0].timestep_index
            ),
            "anchor_timestep_span": (
                path_nodes[-1].after_timestep - path_nodes[0].after_timestep
            ),
        }
        state.path_count += 1
        on_path(occurrence)

    def walk(
        path_nodes: list[_EventNode],
        path_edges: list[_EventEdge],
        lineage: frozenset[int] | None,
    ) -> None:
        if state.truncated:
            return
        if len(path_nodes) == path_length:
            if lineage:
                emit(path_nodes, path_edges, lineage)
            return
        for edge in adjacency.get(path_nodes[-1].event_id, ()):
            if state.expansions >= max_expansions:
                state.truncated = True
                return
            state.expansions += 1
            next_lineage = (
                edge.carrier_atom_ids
                if lineage is None
                else lineage.intersection(edge.carrier_atom_ids)
            )
            if not next_lineage:
                continue
            walk(
                [*path_nodes, node_by_id[edge.to_event_id]],
                [*path_edges, edge],
                frozenset(next_lineage),
            )
            if state.truncated:
                return

    for node in nodes:
        if start_smiles and start_smiles not in node.reactant_terms:
            continue
        walk([node], [], None)
        if state.truncated:
            break
    return state


def _distribution(values: Sequence[int]) -> dict[str, int | float | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "max": None,
        }
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "mean": fmean(values),
        "max": max(values),
    }


def _aggregate_document(
    aggregate: _SignatureAggregate,
    *,
    replicate_count: int,
    statistics_complete: bool,
) -> dict[str, Any]:
    supporting_replicates = sorted(aggregate.supporting_replicates)
    return {
        "signature_id": _signature_identifier(aggregate.reaction_keys),
        "reaction_keys": list(aggregate.reaction_keys),
        "occurrence_count": aggregate.occurrence_count,
        "independent_atom_lineage_support_count": len(aggregate.atom_lineages),
        "independent_lineage_set_support_count": len(aggregate.lineage_sets),
        "supporting_replicates": supporting_replicates,
        "replicate_support_count": len(supporting_replicates),
        "replicate_reproduction_rate": (
            len(supporting_replicates) / replicate_count
            if replicate_count
            else 0.0
        ),
        "support_is_lower_bound": not statistics_complete,
        "interval_gap_by_edge": [
            {"edge_index": index + 1, **_distribution(values)}
            for index, values in enumerate(aggregate.interval_gaps)
        ],
        "idle_timestep_gap_by_edge": [
            {"edge_index": index + 1, **_distribution(values)}
            for index, values in enumerate(aggregate.idle_timestep_gaps)
        ],
        "anchor_timestep_gap_by_edge": [
            {"edge_index": index + 1, **_distribution(values)}
            for index, values in enumerate(aggregate.anchor_timestep_gaps)
        ],
        "interval_span": _distribution(aggregate.interval_spans),
        "anchor_timestep_span": _distribution(aggregate.timestep_spans),
        "example_path_ids": list(aggregate.example_path_ids),
    }


def enumerate_aggregate_reaction_paths(
    reactions: Iterable[Reaction],
    *,
    path_length: int = 3,
    start_smiles: str = "",
    max_paths: int = 100_000,
) -> dict[str, Any]:
    """Enumerate species-reachable reaction-key paths in an aggregate network.

    This function intentionally knows nothing about atom IDs or chronology.
    Its output is the comparison baseline, not evidence that a path occurred.
    """

    safe_length = _bounded_integer(
        path_length, "path_length", minimum=2, maximum=8
    )
    safe_max_paths = _bounded_integer(max_paths, "max_paths", minimum=1)
    network = ReactionNetwork(list(reactions))
    ordered_reactions = sorted(network.reactions, key=lambda reaction: reaction.key)
    by_reactant: dict[str, list[Reaction]] = defaultdict(list)
    for item in ordered_reactions:
        for species in set(item.reactant_smiles):
            by_reactant[species].append(item)
    for values in by_reactant.values():
        values.sort(key=lambda reaction: reaction.key)

    signatures: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
    truncated = False

    def walk(
        path: tuple[Reaction, ...],
        bridge_species: tuple[str, ...],
    ) -> None:
        nonlocal truncated
        if truncated:
            return
        if len(path) == safe_length:
            signature = tuple(reaction.key for reaction in path)
            if signature not in signatures and len(signatures) >= safe_max_paths:
                truncated = True
                return
            if len(signatures[signature]) < 20:
                signatures[signature].add(bridge_species)
            return
        current = path[-1]
        next_by_key: dict[str, tuple[Reaction, set[str]]] = {}
        for species in sorted(set(current.product_smiles)):
            for candidate in by_reactant.get(species, ()):
                existing = next_by_key.get(candidate.key)
                if existing is None:
                    next_by_key[candidate.key] = (candidate, {species})
                else:
                    existing[1].add(species)
        for key in sorted(next_by_key):
            candidate, bridges = next_by_key[key]
            # Each bridge choice is retained for auditing, while comparison
            # identity remains the canonical reaction-key sequence.
            for bridge in sorted(bridges):
                walk((*path, candidate), (*bridge_species, bridge))
                if truncated:
                    return

    starts = [
        reaction
        for reaction in ordered_reactions
        if not start_smiles or start_smiles in reaction.reactant_smiles
    ]
    for start in starts:
        walk((start,), ())
        if truncated:
            break

    path_documents = [
        {
            "reaction_keys": list(signature),
            "bridge_species_variants": [list(value) for value in sorted(variants)],
        }
        for signature, variants in sorted(signatures.items())
    ]
    return {
        "path_length": safe_length,
        "start_smiles": start_smiles,
        "paths": path_documents,
        "path_count": len(path_documents),
        "truncated": truncated,
        "max_paths": safe_max_paths,
    }


def _reaction_path_is_reachable(
    signature: tuple[str, ...],
    reactions_by_key: dict[str, Reaction],
    *,
    start_smiles: str,
) -> bool:
    try:
        path = [reactions_by_key[key] for key in signature]
    except KeyError:
        return False
    if start_smiles and start_smiles not in path[0].reactant_smiles:
        return False
    return all(
        set(left.product_smiles).intersection(right.reactant_smiles)
        for left, right in zip(path, path[1:])
    )


def _signature_sample(
    signatures: Iterable[tuple[str, ...]],
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [
        {
            "signature_id": _signature_identifier(signature),
            "reaction_keys": list(signature),
        }
        for signature in sorted(set(signatures))[:limit]
    ]


def _compare_aggregate_networks(
    sources: Sequence[EventPathSource],
    actual_by_replicate: dict[str, set[tuple[str, ...]]],
    actual_complete_by_replicate: dict[str, bool],
    *,
    path_length: int,
    start_smiles: str,
    max_network_paths: int,
) -> dict[str, Any]:
    per_replicate: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total_actual_pairs = 0
    total_confirmed_pairs = 0
    total_actual_only_pairs = 0
    total_network_pairs = 0
    total_aggregate_only_pairs = 0
    all_complete = True
    all_network_complete = True
    all_actual_complete = True

    for source in sources:
        reaction_path = str(source.reaction_file or "").strip()
        if not reaction_path:
            skipped.append(
                {
                    "replicate": source.replicate,
                    "reason": "reaction_file_not_supplied",
                }
            )
            continue
        if not Path(reaction_path).is_file():
            raise FileNotFoundError(
                f"aggregate reaction file not found for {source.replicate!r}: "
                f"{reaction_path}"
            )
        reactions = parse_reactionabcd(reaction_path, min_tp=1)
        network = ReactionNetwork(reactions)
        reactions_by_key = {
            reaction.key: reaction for reaction in network.reactions
        }
        baseline = enumerate_aggregate_reaction_paths(
            network.reactions,
            path_length=path_length,
            start_smiles=start_smiles,
            max_paths=max_network_paths,
        )
        network_signatures = {
            tuple(str(key) for key in path["reaction_keys"])
            for path in baseline["paths"]
        }
        actual_signatures = set(actual_by_replicate.get(source.replicate, set()))
        confirmed = {
            signature
            for signature in actual_signatures
            if _reaction_path_is_reachable(
                signature,
                reactions_by_key,
                start_smiles=start_smiles,
            )
        }
        actual_only = actual_signatures - confirmed
        aggregate_only_sample = network_signatures - actual_signatures
        actual_complete = actual_complete_by_replicate.get(
            source.replicate, True
        )
        network_complete = not bool(baseline["truncated"])
        all_network_complete = all_network_complete and network_complete
        all_actual_complete = all_actual_complete and actual_complete
        complete = network_complete and actual_complete
        all_complete = all_complete and complete
        known_network_signatures = network_signatures.union(confirmed)
        aggregate_only_count: int | None = (
            len(aggregate_only_sample) if complete else None
        )
        realization_rate: float | None = (
            len(confirmed) / len(known_network_signatures)
            if complete and known_network_signatures
            else (0.0 if complete else None)
        )
        per_replicate.append(
            {
                "replicate": source.replicate,
                "reaction_file": os.path.abspath(reaction_path),
                "aggregate_reachable_path_count": len(known_network_signatures),
                "aggregate_count_is_lower_bound": not network_complete,
                "actual_path_signature_count": len(actual_signatures),
                "actual_count_is_lower_bound": not actual_complete,
                "confirmed_actual_path_count": len(confirmed),
                "aggregate_only_path_count": aggregate_only_count,
                "actual_only_path_count": len(actual_only),
                "realization_rate": realization_rate,
                "comparison_complete": complete,
                "confirmed": _signature_sample(confirmed),
                "aggregate_only": _signature_sample(aggregate_only_sample),
                "actual_only": _signature_sample(actual_only),
            }
        )
        total_network_pairs += len(known_network_signatures)
        total_actual_pairs += len(actual_signatures)
        total_confirmed_pairs += len(confirmed)
        total_actual_only_pairs += len(actual_only)
        if complete:
            total_aggregate_only_pairs += len(aggregate_only_sample)

    compared_count = len(per_replicate)
    comparison_available = compared_count > 0
    comparison_complete = (
        comparison_available and all_complete and not skipped
    )
    return {
        "comparison_basis": (
            "aggregate species reachability versus strict-time exact-molecule "
            "and continuous-atom event paths"
        ),
        "comparison_available": comparison_available,
        "compared_replicate_count": compared_count,
        "skipped_replicates": skipped,
        "comparison_complete": comparison_complete,
        "aggregate_reachable_pair_count": total_network_pairs,
        "aggregate_count_is_lower_bound": (
            not all_network_complete or bool(skipped)
        ),
        "actual_pair_count": total_actual_pairs,
        "actual_count_is_lower_bound": (
            not all_actual_complete or bool(skipped)
        ),
        "confirmed_pair_count": total_confirmed_pairs,
        "aggregate_only_pair_count": (
            total_aggregate_only_pairs if comparison_complete else None
        ),
        "actual_only_pair_count": total_actual_only_pairs,
        "realization_rate": (
            total_confirmed_pairs / total_network_pairs
            if comparison_complete and total_network_pairs
            else (0.0 if comparison_complete else None)
        ),
        "per_replicate": per_replicate,
    }


def analyze_event_paths(
    sources: Iterable[EventPathSource],
    *,
    path_length: int = 3,
    start_smiles: str = "",
    max_interval_gap: int | None = None,
    max_timestep_gap: int | None = None,
    max_occurrence_details: int = 10_000,
    max_expansions: int = 1_000_000,
    max_network_paths: int = 100_000,
) -> dict[str, Any]:
    """Analyze concrete atom-continuous event paths across independent repeats.

    ``path_length=3`` implements the requested ``event1 -> event2 -> event3``
    analysis.  Longer paths use the same invariant: the intersection of atom
    IDs carried by every adjacent molecule-instance edge must remain nonempty.
    """

    source_list = list(sources)
    if not source_list:
        raise ValueError("at least one event path source is required")
    labels = [str(source.replicate).strip() for source in source_list]
    if len(set(labels)) != len(labels):
        raise ValueError("replicate labels must be unique")
    safe_path_length = _bounded_integer(
        path_length, "path_length", minimum=2, maximum=8
    )
    safe_interval_gap = _optional_nonnegative_integer(
        max_interval_gap, "max_interval_gap"
    )
    safe_timestep_gap = _optional_nonnegative_integer(
        max_timestep_gap, "max_timestep_gap"
    )
    safe_detail_limit = _bounded_integer(
        max_occurrence_details, "max_occurrence_details", minimum=0
    )
    safe_max_expansions = _bounded_integer(
        max_expansions, "max_expansions", minimum=1
    )
    safe_max_network_paths = _bounded_integer(
        max_network_paths, "max_network_paths", minimum=1
    )
    normalized_start = str(start_smiles or "").strip()

    aggregates: dict[tuple[str, ...], _SignatureAggregate] = {}
    occurrence_details: list[dict[str, Any]] = []
    source_documents: list[dict[str, Any]] = []
    actual_by_replicate: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    actual_complete_by_replicate: dict[str, bool] = {}
    total_occurrences = 0
    traversal_truncated = False
    total_atom_lineages: set[tuple[str, int]] = set()

    for source in source_list:
        nodes, opened = _load_event_nodes(source)
        edges, graph_summary = _build_event_edges(nodes)
        source_signatures: set[tuple[str, ...]] = set()

        def collect(occurrence: dict[str, Any]) -> None:
            nonlocal total_occurrences
            total_occurrences += 1
            signature = tuple(str(key) for key in occurrence["reaction_keys"])
            source_signatures.add(signature)
            actual_by_replicate[source.replicate].add(signature)
            aggregate = aggregates.setdefault(
                signature,
                _SignatureAggregate(reaction_keys=signature),
            )
            aggregate.add(occurrence)
            total_atom_lineages.update(
                (source.replicate, int(atom_id))
                for atom_id in occurrence["lineage_atom_ids"]
            )
            if len(occurrence_details) < safe_detail_limit:
                occurrence_details.append(occurrence)

        traversal = _enumerate_actual_paths(
            source.replicate,
            nodes,
            edges,
            path_length=safe_path_length,
            start_smiles=normalized_start,
            max_interval_gap=safe_interval_gap,
            max_timestep_gap=safe_timestep_gap,
            max_expansions=safe_max_expansions,
            on_path=collect,
        )
        traversal_truncated = traversal_truncated or traversal.truncated
        actual_complete_by_replicate[source.replicate] = not traversal.truncated
        source_documents.append(
            {
                "replicate": source.replicate,
                "reactionevent_file": os.path.abspath(
                    source.reactionevent_file
                ),
                "molecules_file": (
                    os.path.abspath(source.molecules_file)
                    if source.molecules_file
                    else ""
                ),
                "reaction_file": (
                    os.path.abspath(source.reaction_file)
                    if source.reaction_file
                    else ""
                ),
                "event_index": str(opened["index_path"]),
                "time_basis": str(opened["time_basis"]),
                **graph_summary,
                "actual_path_occurrence_count": traversal.path_count,
                "actual_path_signature_count": len(source_signatures),
                "traversal_expansions": traversal.expansions,
                "traversal_truncated": traversal.truncated,
            }
        )

    statistics_complete = not traversal_truncated
    aggregate_documents = [
        _aggregate_document(
            aggregate,
            replicate_count=len(source_list),
            statistics_complete=statistics_complete,
        )
        for aggregate in aggregates.values()
    ]
    aggregate_documents.sort(
        key=lambda item: (
            -int(item["replicate_support_count"]),
            -int(item["independent_atom_lineage_support_count"]),
            -int(item["occurrence_count"]),
            tuple(item["reaction_keys"]),
        )
    )
    comparison = _compare_aggregate_networks(
        source_list,
        actual_by_replicate,
        actual_complete_by_replicate,
        path_length=safe_path_length,
        start_smiles=normalized_start,
        max_network_paths=safe_max_network_paths,
    )
    return {
        "schema_version": EVENT_PATH_SCHEMA_VERSION,
        "semantics": {
            "node": "one indexed concrete RNG event",
            "edge": (
                "the first strictly later event that consumes the exact "
                "product molecule instance (same species and atom-ID set)"
            ),
            "path": (
                "every adjacent edge exists and at least one atom ID is "
                "continuous across all edges"
            ),
            "atom_id_scope": "replicate-local, 1-based trajectory atom ID",
        },
        "query": {
            "path_length": safe_path_length,
            "start_smiles": normalized_start,
            "max_interval_gap": safe_interval_gap,
            "max_timestep_gap": safe_timestep_gap,
            "max_occurrence_details": safe_detail_limit,
            "max_expansions_per_replicate": safe_max_expansions,
            "max_network_paths_per_replicate": safe_max_network_paths,
        },
        "summary": {
            "replicate_count": len(source_list),
            "actual_path_occurrence_count": total_occurrences,
            "actual_path_signature_count": len(aggregate_documents),
            "independent_atom_lineage_support_count": len(total_atom_lineages),
            "statistics_complete": statistics_complete,
            "traversal_truncated": traversal_truncated,
        },
        "sources": source_documents,
        "paths": aggregate_documents,
        "occurrences": occurrence_details,
        "occurrence_details_truncated": (
            total_occurrences > len(occurrence_details)
        ),
        "comparison": comparison,
    }


__all__ = [
    "EVENT_PATH_SCHEMA_VERSION",
    "EventPathAnalysisError",
    "EventPathSource",
    "analyze_event_paths",
    "enumerate_aggregate_reaction_paths",
]
