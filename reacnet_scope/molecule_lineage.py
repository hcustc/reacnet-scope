"""Bounded, atom-anchored lineage queries over indexed RNG events.

The module treats ReacNetGenerator event records as authored evidence.  It
does not infer reactions from coordinates and it never joins molecule
instances by SMILES alone: persistence requires an exact Species and atom-ID
set, while structural recrossing additionally requires the same intramolecular
bond set.
"""

from __future__ import annotations

import csv
import io
import json
import os
from collections import deque
from dataclasses import dataclass
from hashlib import sha1
from typing import Any, Iterable, Mapping

from .event_index import EVENT_EVIDENCE_STORE, EventNotFoundError


MOLECULE_LINEAGE_SCHEMA_VERSION = "molecule-lineage/v1"


class MoleculeLineageError(RuntimeError):
    """Raised when indexed evidence cannot support a requested lineage."""


class LineageElementMappingError(MoleculeLineageError):
    """Raised when a chemical anchor rule lacks an atom-element mapping."""


def _bounded_integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed != value or not minimum <= parsed <= maximum:
        raise ValueError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return parsed


def _participant_key(
    participant: Mapping[str, Any],
) -> tuple[str, tuple[int, ...]]:
    species = str(participant.get("species") or "").strip()
    atom_ids = tuple(
        sorted({int(value) for value in participant.get("atom_ids") or []})
    )
    if not species or not atom_ids:
        raise MoleculeLineageError(
            "Molecular evidence contains an empty participant"
        )
    return species, atom_ids


def _parse_bonds(value: Any) -> tuple[tuple[int, int, str], ...]:
    raw_values = (
        value
        if isinstance(value, (list, tuple))
        else str(value or "").split(";")
    )
    bonds: set[tuple[int, int, str]] = set()
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        parts = text.split("-", 2)
        if len(parts) != 3:
            raise MoleculeLineageError(
                f"Molecular evidence contains an invalid bond: {text!r}"
            )
        try:
            first, second = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise MoleculeLineageError(
                f"Molecular evidence contains an invalid bond: {text!r}"
            ) from exc
        left, right = sorted((first, second))
        bonds.add((left, right, str(parts[2])))
    return tuple(sorted(bonds))


def _participant_bonds(
    event: Mapping[str, Any],
    side: str,
    atom_ids: Iterable[int],
) -> tuple[tuple[int, int, str], ...]:
    selected = {int(value) for value in atom_ids}
    return tuple(
        bond
        for bond in _parse_bonds(event.get(f"{side}_bonds"))
        if bond[0] in selected and bond[1] in selected
    )


def _signature(
    species: str,
    atom_ids: Iterable[int],
    bonds: Iterable[tuple[int, int, str]],
) -> str:
    payload = {
        "species": str(species),
        "atom_ids": sorted(int(value) for value in atom_ids),
        "bonds": [list(value) for value in sorted(bonds)],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _node_id(event_id: str, side: str, participant_index: int) -> str:
    return f"mol::{event_id}::{side}::{participant_index}"


def _event_node_id(event_id: str) -> str:
    return f"event::{event_id}"


def _bond_text(bond: tuple[int, int, str]) -> str:
    return f"{bond[0]}-{bond[1]}-{bond[2]}"


def _atom_changes(event: Mapping[str, Any]) -> dict[str, list[str]]:
    reactant = set(_parse_bonds(event.get("reactant_bonds")))
    product = set(_parse_bonds(event.get("product_bonds")))
    return {
        "formed_bonds": [_bond_text(value) for value in sorted(product - reactant)],
        "broken_bonds": [_bond_text(value) for value in sorted(reactant - product)],
        "order_changed_bonds": sorted(
            {
                f"{left}-{right}"
                for left, right, _order in reactant | product
                if {
                    order
                    for first, second, order in reactant | product
                    if first == left and second == right
                }
                and len(
                    {
                        order
                        for first, second, order in reactant | product
                        if first == left and second == right
                    }
                ) > 1
            }
        ),
    }


def _event_category(event: Mapping[str, Any]) -> str:
    reactants = event.get("reactant_participants") or []
    products = event.get("product_participants") or []
    reactant_count = len(reactants)
    product_count = len(products)
    if reactant_count > 1 and product_count == 1:
        return "merge"
    if reactant_count == 1 and product_count > 1:
        return "split"
    if reactant_count == product_count == 1:
        reactant_key = _participant_key(reactants[0])
        product_key = _participant_key(products[0])
        if reactant_key[1] == product_key[1]:
            return "rearrangement"
        if len(product_key[1]) > len(reactant_key[1]):
            return "fragment gain"
        if len(product_key[1]) < len(reactant_key[1]):
            return "fragment loss"
    if reactant_count > 1 and product_count > 1:
        return "substitution/transfer"
    return "complex"


def _source_signature(path: str) -> dict[str, Any]:
    source = os.path.abspath(path)
    stat = os.stat(source)
    return {
        "path": source,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


@dataclass(frozen=True)
class _HistoryEntry:
    signature: str
    node_id: str
    timestep_index: int
    persistent_depth: int
    node_position: int
    event_position: int


@dataclass(frozen=True)
class _TraversalState:
    direction: str
    node_id: str
    atom_ids: tuple[int, ...]
    species: str
    anchor_atom_ids: tuple[int, ...]
    boundary_timestep_index: int
    persistent_depth: int
    history: tuple[_HistoryEntry, ...]
    path_node_ids: tuple[str, ...]
    path_event_ids: tuple[str, ...]
    pending_event_id: str = ""


class _LineageBuilder:
    def __init__(
        self,
        reactionevent_file: str,
        molecules_file: str,
        *,
        root_event: Mapping[str, Any],
        root_side: str,
        root_participant_index: int,
        anchor_atom_ids: Iterable[int],
        atom_elements: Mapping[int, str],
        depth_backward: int,
        depth_forward: int,
        max_molecule_nodes: int,
        recrossing_window: int,
    ) -> None:
        self.reactionevent_file = reactionevent_file
        self.molecules_file = molecules_file
        self.root_event = dict(root_event)
        self.root_side = root_side
        self.root_participant_index = root_participant_index
        self.root_anchor_ids = tuple(sorted(set(anchor_atom_ids)))
        self.atom_elements = {
            int(atom_id): str(element)
            for atom_id, element in atom_elements.items()
            if str(element)
        }
        self.depth_by_direction = {
            "backward": depth_backward,
            "forward": depth_forward,
        }
        self.max_molecule_nodes = max_molecule_nodes
        self.recrossing_window = recrossing_window
        self.molecules: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.episodes: dict[str, dict[str, Any]] = {}
        self.truncations: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._event_cache = {str(root_event["event_id"]): dict(root_event)}
        self._visited: set[tuple[str, str, tuple[int, ...]]] = set()

    def _add_molecule(
        self,
        event: Mapping[str, Any],
        side: str,
        participant_index: int,
        *,
        role: str,
        anchor_atom_ids: Iterable[int] = (),
    ) -> str | None:
        node_id = _node_id(str(event["event_id"]), side, participant_index)
        participant = (event.get(f"{side}_participants") or [])[participant_index]
        species, atom_ids = _participant_key(participant)
        anchors = sorted(set(int(value) for value in anchor_atom_ids))
        existing = self.molecules.get(node_id)
        if existing is not None:
            existing["anchor_atom_ids"] = sorted(
                set(existing["anchor_atom_ids"]) | set(anchors)
            )
            role_rank = {"context": 0, "active": 1, "root": 2}
            if role_rank[role] > role_rank[existing["role"]]:
                existing["role"] = role
            return node_id
        if len(self.molecules) >= self.max_molecule_nodes:
            return None
        bonds = _participant_bonds(event, side, atom_ids)
        known_elements = [
            self.atom_elements.get(atom_id, "") for atom_id in atom_ids
        ]
        self.molecules[node_id] = {
            "node_id": node_id,
            "kind": "molecule",
            "event_id": str(event["event_id"]),
            "side": side,
            "participant_index": int(participant_index),
            "frame": int(
                event["before_timestep"]
                if side == "reactant"
                else event["after_timestep"]
            ),
            "timestep_index": int(event["timestep_index"]),
            "species": species,
            "atom_ids": list(atom_ids),
            "anchor_atom_ids": anchors,
            "bonds": [_bond_text(value) for value in bonds],
            "structure_signature": _signature(species, atom_ids, bonds),
            "role": role,
            "heavy_atom_count": (
                sum(value != "H" for value in known_elements)
                if all(known_elements)
                else None
            ),
            "element_mapping_complete": all(known_elements),
        }
        return node_id

    def _add_edge(
        self,
        source: str,
        target: str,
        kind: str,
        *,
        active: bool = False,
    ) -> None:
        edge_id = f"{kind}::{source}::{target}"
        current = self.edges.get(edge_id)
        if current is not None:
            current["active"] = bool(current["active"] or active)
            return
        self.edges[edge_id] = {
            "edge_id": edge_id,
            "source": source,
            "target": target,
            "kind": kind,
            "active": bool(active),
        }

    def _ensure_event(self, event: Mapping[str, Any], direction: str) -> None:
        event_id = str(event["event_id"])
        changes = _atom_changes(event)
        row = self.events.get(event_id)
        if row is None:
            self.events[event_id] = {
                "node_id": _event_node_id(event_id),
                "kind": "event",
                "event_id": event_id,
                "timestep_index": int(event["timestep_index"]),
                "before_timestep": int(event["before_timestep"]),
                "after_timestep": int(event["after_timestep"]),
                "reaction_smiles": str(event["reaction_smiles"]),
                "reactant": str(event["reactant"]),
                "product": str(event["product"]),
                "anchor_frame": int(event["anchor_frame"]),
                "atom_id_list": [
                    int(value) for value in event.get("atom_id_list") or []
                ],
                "reactant_bonds": str(event.get("reactant_bonds") or ""),
                "product_bonds": str(event.get("product_bonds") or ""),
                "reactant_participants": list(
                    event.get("reactant_participants") or []
                ),
                "product_participants": list(
                    event.get("product_participants") or []
                ),
                "category": _event_category(event),
                "directions": [direction],
                "association_status": str(event["association_status"]),
                **changes,
                "recrossing_episode_ids": [],
            }
        elif direction not in row["directions"]:
            row["directions"].append(direction)

    def _nearest_transition(
        self,
        state: _TraversalState,
    ) -> Mapping[str, Any] | None:
        if state.pending_event_id:
            return self._event_cache[state.pending_event_id]
        query = EVENT_EVIDENCE_STORE.query_nearest_atom_events(
            self.reactionevent_file,
            self.molecules_file,
            state.anchor_atom_ids,
            timestep_index=state.boundary_timestep_index,
            direction=state.direction,
        )
        candidates = query["rows"]
        if not candidates:
            return None
        incoming_side = (
            "reactant" if state.direction == "forward" else "product"
        )
        expected = (state.species, state.atom_ids)
        exact = [
            candidate
            for candidate in candidates
            if expected
            in {
                _participant_key(participant)
                for participant in candidate.get(
                    f"{incoming_side}_participants"
                )
                or []
            }
        ]
        if len(exact) == 1:
            event = exact[0]
            self._event_cache[str(event["event_id"])] = event
            return event
        reason = "ambiguous_exact_transition" if exact else "continuity_barrier"
        self.truncations.append(
            {
                "reason": reason,
                "direction": state.direction,
                "from_node_id": state.node_id,
                "nearest_timestep_index": query["nearest_timestep_index"],
                "candidate_event_ids": [
                    str(value["event_id"]) for value in (exact or candidates)
                ],
            }
        )
        return None

    def _outgoing(
        self,
        event: Mapping[str, Any],
        direction: str,
        anchors: Iterable[int],
    ) -> list[tuple[int, Mapping[str, Any], tuple[int, ...]]]:
        side = "product" if direction == "forward" else "reactant"
        anchor_set = set(anchors)
        outgoing = []
        for index, participant in enumerate(event.get(f"{side}_participants") or []):
            _species, atom_ids = _participant_key(participant)
            retained = tuple(sorted(anchor_set.intersection(atom_ids)))
            if retained:
                outgoing.append((index, participant, retained))
        return outgoing

    def _matching_history(
        self,
        state: _TraversalState,
        signature: str,
        event_timestep_index: int,
    ) -> _HistoryEntry | None:
        for entry in reversed(state.history):
            gap = abs(event_timestep_index - entry.timestep_index)
            if gap > self.recrossing_window:
                continue
            if entry.signature == signature:
                return entry
        return None

    def _record_episode(
        self,
        state: _TraversalState,
        returned_node_id: str,
        event_id: str,
        match: _HistoryEntry,
        event_timestep_index: int,
    ) -> str:
        event_ids = list(state.path_event_ids[match.event_position :]) + [event_id]
        member_nodes = list(state.path_node_ids[match.node_position :]) + [
            returned_node_id
        ]
        identity = json.dumps(
            [match.node_id, returned_node_id, event_ids], separators=(",", ":")
        )
        episode_id = f"recross::{sha1(identity.encode()).hexdigest()[:12]}"
        if episode_id not in self.episodes:
            self.episodes[episode_id] = {
                "episode_id": episode_id,
                "kind": "recrossing",
                "start_node_id": match.node_id,
                "return_node_id": returned_node_id,
                "event_ids": event_ids,
                "member_node_ids": member_nodes,
                "interval_gap": abs(event_timestep_index - match.timestep_index),
                "classification": "fast recrossing",
                "exact_structure_return": True,
            }
            for member_event_id in event_ids:
                event_row = self.events.get(member_event_id)
                if event_row is not None:
                    event_row["recrossing_episode_ids"].append(episode_id)
        return episode_id

    def _process_transition(
        self,
        state: _TraversalState,
        event: Mapping[str, Any],
    ) -> list[_TraversalState]:
        event_id = str(event["event_id"])
        direction = state.direction
        incoming_side = "reactant" if direction == "forward" else "product"
        outgoing_side = "product" if direction == "forward" else "reactant"
        incoming_participants = event.get(f"{incoming_side}_participants") or []
        expected = (state.species, state.atom_ids)
        incoming_indexes = [
            index
            for index, participant in enumerate(incoming_participants)
            if _participant_key(participant) == expected
        ]
        if len(incoming_indexes) != 1:
            self.truncations.append(
                {
                    "reason": "incoming_instance_mismatch",
                    "direction": direction,
                    "from_node_id": state.node_id,
                    "event_id": event_id,
                }
            )
            return []

        outgoing = self._outgoing(event, direction, state.anchor_atom_ids)
        if not outgoing:
            self.truncations.append(
                {
                    "reason": "anchor_not_retained",
                    "direction": direction,
                    "from_node_id": state.node_id,
                    "event_id": event_id,
                }
            )
        projected_ids = {
            _node_id(event_id, side, index)
            for side in ("reactant", "product")
            for index, _participant in enumerate(
                event.get(f"{side}_participants") or []
            )
        }
        new_node_count = len(projected_ids.difference(self.molecules))
        if len(self.molecules) + new_node_count > self.max_molecule_nodes:
            self.truncations.append(
                {
                    "reason": "molecule_node_limit",
                    "direction": direction,
                    "from_node_id": state.node_id,
                    "event_id": event_id,
                    "max_molecule_nodes": self.max_molecule_nodes,
                }
            )
            return []

        preview: list[tuple[int, Mapping[str, Any], tuple[int, ...], str, _HistoryEntry | None]] = []
        for index, participant, retained in outgoing:
            species, atom_ids = _participant_key(participant)
            bonds = _participant_bonds(event, outgoing_side, atom_ids)
            signature = _signature(species, atom_ids, bonds)
            preview.append(
                (
                    index,
                    participant,
                    retained,
                    signature,
                    self._matching_history(
                        state,
                        signature,
                        int(event["timestep_index"]),
                    ),
                )
            )
        depth_limit = self.depth_by_direction[direction]
        if state.persistent_depth >= depth_limit and not any(
            match is not None
            and match.persistent_depth + 1 <= depth_limit
            for _index, _participant, _retained, _signature, match in preview
        ):
            self.truncations.append(
                {
                    "reason": "persistent_depth_limit",
                    "direction": direction,
                    "from_node_id": state.node_id,
                    "next_event_id": event_id,
                    "limit": depth_limit,
                }
            )
            return []

        self._ensure_event(event, direction)
        event_node_id = _event_node_id(event_id)
        active_outgoing_indexes = {item[0] for item in preview}
        for side in ("reactant", "product"):
            for index, _participant in enumerate(
                event.get(f"{side}_participants") or []
            ):
                role = (
                    "active"
                    if side == outgoing_side and index in active_outgoing_indexes
                    else "context"
                )
                anchors = next(
                    (
                        retained
                        for output_index, _item, retained, _signature, _match in preview
                        if side == outgoing_side and output_index == index
                    ),
                    (),
                )
                molecule_node_id = self._add_molecule(
                    event,
                    side,
                    index,
                    role=role,
                    anchor_atom_ids=anchors,
                )
                assert molecule_node_id is not None
                if side == "reactant":
                    self._add_edge(
                        molecule_node_id,
                        event_node_id,
                        "reaction",
                        active=(
                            (side == incoming_side and index in incoming_indexes)
                            or (
                                side == outgoing_side
                                and index in active_outgoing_indexes
                            )
                        ),
                    )
                else:
                    self._add_edge(
                        event_node_id,
                        molecule_node_id,
                        "reaction",
                        active=(
                            (
                                side == outgoing_side
                                and index in active_outgoing_indexes
                            )
                            or (
                                side == incoming_side
                                and index in incoming_indexes
                            )
                        ),
                    )

        incoming_node_id = _node_id(event_id, incoming_side, incoming_indexes[0])
        if incoming_node_id != state.node_id:
            if direction == "forward":
                self._add_edge(state.node_id, incoming_node_id, "persistence", active=True)
            else:
                self._add_edge(incoming_node_id, state.node_id, "persistence", active=True)

        next_states: list[_TraversalState] = []
        for index, participant, retained, signature, match in preview:
            species, atom_ids = _participant_key(participant)
            output_node_id = _node_id(event_id, outgoing_side, index)
            next_depth = (
                match.persistent_depth + 1
                if match is not None
                else state.persistent_depth + 1
            )
            next_path_nodes = state.path_node_ids + (output_node_id,)
            next_path_events = state.path_event_ids + (event_id,)
            if match is not None:
                self._record_episode(
                    state,
                    output_node_id,
                    event_id,
                    match,
                    int(event["timestep_index"]),
                )
            next_history = state.history + (
                _HistoryEntry(
                    signature=signature,
                    node_id=output_node_id,
                    timestep_index=int(event["timestep_index"]),
                    persistent_depth=next_depth,
                    node_position=len(next_path_nodes) - 1,
                    event_position=len(next_path_events),
                ),
            )
            next_states.append(
                _TraversalState(
                    direction=direction,
                    node_id=output_node_id,
                    atom_ids=atom_ids,
                    species=species,
                    anchor_atom_ids=retained,
                    boundary_timestep_index=int(event["timestep_index"]),
                    persistent_depth=next_depth,
                    history=next_history,
                    path_node_ids=next_path_nodes,
                    path_event_ids=next_path_events,
                )
            )
        return next_states

    def build(self) -> dict[str, Any]:
        participants = self.root_event.get(f"{self.root_side}_participants") or []
        root_id = self._add_molecule(
            self.root_event,
            self.root_side,
            self.root_participant_index,
            role="root",
            anchor_atom_ids=self.root_anchor_ids,
        )
        assert root_id is not None
        root = self.molecules[root_id]
        root_history = (
            _HistoryEntry(
                signature=str(root["structure_signature"]),
                node_id=root_id,
                timestep_index=int(root["timestep_index"]),
                persistent_depth=0,
                node_position=0,
                event_position=0,
            ),
        )
        root_species, root_atom_ids = _participant_key(
            participants[self.root_participant_index]
        )
        queue: deque[_TraversalState] = deque()
        for direction in ("backward", "forward"):
            pending = ""
            if (
                direction == "forward" and self.root_side == "reactant"
            ) or (
                direction == "backward" and self.root_side == "product"
            ):
                pending = str(self.root_event["event_id"])
            queue.append(
                _TraversalState(
                    direction=direction,
                    node_id=root_id,
                    atom_ids=root_atom_ids,
                    species=root_species,
                    anchor_atom_ids=self.root_anchor_ids,
                    boundary_timestep_index=int(
                        self.root_event["timestep_index"]
                    ),
                    persistent_depth=0,
                    history=root_history,
                    path_node_ids=(root_id,),
                    path_event_ids=(),
                    pending_event_id=pending,
                )
            )

        while queue:
            state = queue.popleft()
            visit_key = (
                state.direction,
                state.node_id,
                state.anchor_atom_ids,
            )
            if visit_key in self._visited:
                continue
            self._visited.add(visit_key)
            event = self._nearest_transition(state)
            if event is None:
                continue
            queue.extend(self._process_transition(state, event))

        molecules = sorted(
            self.molecules.values(),
            key=lambda row: (
                int(row["frame"]),
                0 if row["side"] == "reactant" else 1,
                str(row["node_id"]),
            ),
        )
        events = sorted(
            self.events.values(),
            key=lambda row: (int(row["timestep_index"]), str(row["event_id"])),
        )
        edges = sorted(self.edges.values(), key=lambda row: str(row["edge_id"]))
        episodes = sorted(
            self.episodes.values(),
            key=lambda row: (int(row["interval_gap"]), str(row["episode_id"])),
        )
        raw_elements = _graph_elements(molecules, events, edges)
        persistent_elements = _persistent_graph_elements(
            molecules,
            events,
            edges,
            episodes,
        )
        root_heavy = root.get("heavy_atom_count")
        terminal_heavy = [
            row["heavy_atom_count"]
            for row in molecules
            if row["role"] in {"root", "active"}
            and row.get("heavy_atom_count") is not None
            and not any(
                edge["source"] == row["node_id"] and edge["active"]
                for edge in edges
            )
        ]
        trend = "undetermined"
        if root_heavy is not None and terminal_heavy:
            changes = {int(value) - int(root_heavy) for value in terminal_heavy}
            if changes == {0}:
                trend = "structurally stable"
            elif min(changes) >= 0 and max(changes) > 0:
                trend = "growth"
            elif max(changes) <= 0 and min(changes) < 0:
                trend = "degradation"
            else:
                trend = "mixed"
        return {
            "schema_version": MOLECULE_LINEAGE_SCHEMA_VERSION,
            "root": dict(root),
            "molecule_nodes": molecules,
            "event_nodes": events,
            "edges": edges,
            "recrossing_episodes": episodes,
            "truncations": self.truncations,
            "warnings": list(dict.fromkeys(self.warnings)),
            "views": {
                "raw": {"elements": raw_elements},
                "persistent": {"elements": persistent_elements},
            },
            "summary": {
                "molecule_node_count": len(molecules),
                "event_count": len(events),
                "recrossing_episode_count": len(episodes),
                "truncation_count": len(self.truncations),
                "aggregate_trend": trend,
            },
        }


def _graph_elements(
    molecules: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for row in molecules:
        label = f"{row['species']}\n{','.join(map(str, row['atom_ids']))}"
        elements.append(
            {
                "data": {
                    "id": row["node_id"],
                    "label": label,
                    "kind": "molecule",
                    "event_id": row["event_id"],
                    "side": row["side"],
                    "participant_index": row["participant_index"],
                    "species": row["species"],
                    "atom_ids": row["atom_ids"],
                },
                "classes": f"molecule {row['role']}",
            }
        )
    for row in events:
        elements.append(
            {
                "data": {
                    "id": row["node_id"],
                    "label": f"{row['category']}\n{row['before_timestep']}→{row['after_timestep']}",
                    "kind": "event",
                    "event_id": row["event_id"],
                    "reaction_smiles": row["reaction_smiles"],
                },
                "classes": "event " + row["category"].replace("/", "-"),
            }
        )
    for row in edges:
        elements.append(
            {
                "data": {
                    "id": row["edge_id"],
                    "source": row["source"],
                    "target": row["target"],
                    "kind": row["kind"],
                },
                "classes": f"{row['kind']} {'active' if row['active'] else 'context'}",
            }
        )
    return elements


def _persistent_graph_elements(
    molecules: list[Mapping[str, Any]],
    events: list[Mapping[str, Any]],
    edges: list[Mapping[str, Any]],
    episodes: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    hidden: set[str] = set()
    accepted: list[Mapping[str, Any]] = []
    occupied_events: set[str] = set()
    for episode in episodes:
        event_ids = set(str(value) for value in episode["event_ids"])
        if event_ids & occupied_events:
            continue
        occupied_events.update(event_ids)
        accepted.append(episode)
        hidden.update(_event_node_id(value) for value in event_ids)
        endpoints = {episode["start_node_id"], episode["return_node_id"]}
        hidden.update(
            row["node_id"]
            for row in molecules
            if row["event_id"] in event_ids and row["node_id"] not in endpoints
        )
    elements = _graph_elements(
        [row for row in molecules if row["node_id"] not in hidden],
        [row for row in events if row["node_id"] not in hidden],
        [
            row
            for row in edges
            if row["source"] not in hidden and row["target"] not in hidden
        ],
    )
    for episode in accepted:
        episode_id = str(episode["episode_id"])
        elements.append(
            {
                "data": {
                    "id": episode_id,
                    "label": f"fast recrossing\nΔ={episode['interval_gap']} frames",
                    "kind": "recrossing",
                    "episode_id": episode_id,
                    "event_ids": episode["event_ids"],
                },
                "classes": "recrossing",
            }
        )
        for suffix, source, target in (
            ("in", episode["start_node_id"], episode_id),
            ("out", episode_id, episode["return_node_id"]),
        ):
            elements.append(
                {
                    "data": {
                        "id": f"episode-edge::{episode_id}::{suffix}",
                        "source": source,
                        "target": target,
                        "kind": "recrossing",
                    },
                    "classes": "recrossing active",
                }
            )
    return elements


def _resolve_anchor_ids(
    participant: Mapping[str, Any],
    *,
    anchor_mode: str,
    atom_elements: Mapping[int, str],
    anchor_elements: Iterable[str],
    anchor_atom_ids: Iterable[int],
) -> tuple[int, ...]:
    _species, participant_ids = _participant_key(participant)
    participant_set = set(participant_ids)
    mode = str(anchor_mode or "non_hydrogen").strip().lower()
    if mode == "all":
        selected = participant_set
    elif mode == "atom_ids":
        requested = {int(value) for value in anchor_atom_ids}
        unknown = requested.difference(participant_set)
        if unknown:
            raise ValueError(
                "anchor atom IDs are not part of the selected molecule: "
                + ",".join(map(str, sorted(unknown)))
            )
        selected = requested
    elif mode in {"non_hydrogen", "elements"}:
        missing = sorted(
            atom_id for atom_id in participant_ids if not atom_elements.get(atom_id)
        )
        if missing:
            raise LineageElementMappingError(
                "Selected molecule lacks a confirmed element mapping for atom IDs: "
                + ",".join(map(str, missing))
            )
        allowed = (
            {str(value).strip() for value in anchor_elements if str(value).strip()}
            if mode == "elements"
            else {
                str(atom_elements[atom_id])
                for atom_id in participant_ids
                if str(atom_elements[atom_id]) != "H"
            }
        )
        if mode == "elements" and not allowed:
            raise ValueError("anchor_elements must not be empty")
        selected = {
            atom_id
            for atom_id in participant_ids
            if str(atom_elements[atom_id]) in allowed
        }
    else:
        raise ValueError(
            "anchor_mode must be non_hydrogen, all, elements, or atom_ids"
        )
    if not selected:
        if mode == "non_hydrogen":
            raise LineageElementMappingError(
                "The selected molecule has no non-hydrogen atoms; use all atoms explicitly"
            )
        raise ValueError("the anchor rule selected no atoms")
    return tuple(sorted(selected))


def build_molecule_lineage(
    reactionevent_file: str,
    molecules_file: str,
    *,
    event_id: str,
    side: str,
    participant_index: int,
    atom_elements: Mapping[int, str] | None = None,
    anchor_mode: str = "non_hydrogen",
    anchor_elements: Iterable[str] = (),
    anchor_atom_ids: Iterable[int] = (),
    depth_backward: int = 3,
    depth_forward: int = 3,
    max_molecule_nodes: int = 100,
    recrossing_window: int = 5,
) -> dict[str, Any]:
    """Build a bounded lineage around one concrete event participant."""

    side_text = str(side or "").strip().lower()
    if side_text not in {"reactant", "product"}:
        raise ValueError("side must be reactant or product")
    participant_position = _bounded_integer(
        participant_index,
        "participant_index",
        minimum=0,
        maximum=10_000,
    )
    backward = _bounded_integer(
        depth_backward, "depth_backward", minimum=0, maximum=20
    )
    forward = _bounded_integer(
        depth_forward, "depth_forward", minimum=0, maximum=20
    )
    node_limit = _bounded_integer(
        max_molecule_nodes,
        "max_molecule_nodes",
        minimum=1,
        maximum=10_000,
    )
    window = _bounded_integer(
        recrossing_window,
        "recrossing_window",
        minimum=0,
        maximum=10_000,
    )
    try:
        root_event = EVENT_EVIDENCE_STORE.get_event(
            reactionevent_file,
            molecules_file,
            str(event_id),
        )
    except EventNotFoundError as exc:
        raise MoleculeLineageError(str(exc)) from exc
    if root_event.get("association_status") != "matched":
        raise MoleculeLineageError(
            "The selected event has no exact molecule/atom association"
        )
    participants = root_event.get(f"{side_text}_participants") or []
    if participant_position >= len(participants):
        raise ValueError("participant_index is outside the selected event side")
    normalized_elements = {
        int(atom_id): str(element).strip()
        for atom_id, element in (atom_elements or {}).items()
        if str(element).strip()
    }
    anchors = _resolve_anchor_ids(
        participants[participant_position],
        anchor_mode=anchor_mode,
        atom_elements=normalized_elements,
        anchor_elements=anchor_elements,
        anchor_atom_ids=anchor_atom_ids,
    )
    builder = _LineageBuilder(
        reactionevent_file,
        molecules_file,
        root_event=root_event,
        root_side=side_text,
        root_participant_index=participant_position,
        anchor_atom_ids=anchors,
        atom_elements=normalized_elements,
        depth_backward=backward,
        depth_forward=forward,
        max_molecule_nodes=node_limit,
        recrossing_window=window,
    )
    report = builder.build()
    report["query"] = {
        "event_id": str(event_id),
        "side": side_text,
        "participant_index": participant_position,
        "anchor_mode": str(anchor_mode),
        "anchor_elements": sorted(
            {str(value) for value in anchor_elements if str(value)}
        ),
        "anchor_atom_ids": list(anchors),
        "depth_backward": backward,
        "depth_forward": forward,
        "max_molecule_nodes": node_limit,
        "recrossing_window": window,
    }
    source_label = (
        "timeline"
        if str(reactionevent_file).lower().endswith(".timeline.h5")
        else "reactionevent"
    )
    report["source_signatures"] = {
        source_label: _source_signature(reactionevent_file),
        **(
            {"molecules": _source_signature(molecules_file)}
            if str(molecules_file or "").strip()
            else {}
        ),
    }
    return report


def molecule_lineage_to_csv(report: Mapping[str, Any]) -> str:
    """Serialize every auditable lineage record into one typed CSV stream."""

    output = io.StringIO()
    columns = [
        "record_type",
        "record_id",
        "event_id",
        "timestep_index",
        "frame",
        "side",
        "species",
        "atom_ids",
        "category",
        "source",
        "target",
        "reason",
        "payload_json",
    ]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()

    def write(record_type: str, record: Mapping[str, Any], **values: Any) -> None:
        writer.writerow(
            {
                "record_type": record_type,
                "payload_json": json.dumps(
                    record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                **values,
            }
        )

    write("query", report.get("query") or {}, record_id="query")
    for row in report.get("molecule_nodes") or []:
        write(
            "molecule",
            row,
            record_id=row.get("node_id"),
            event_id=row.get("event_id"),
            timestep_index=row.get("timestep_index"),
            frame=row.get("frame"),
            side=row.get("side"),
            species=row.get("species"),
            atom_ids=";".join(map(str, row.get("atom_ids") or [])),
        )
    for row in report.get("event_nodes") or []:
        write(
            "event",
            row,
            record_id=row.get("node_id"),
            event_id=row.get("event_id"),
            timestep_index=row.get("timestep_index"),
            category=row.get("category"),
        )
    for row in report.get("edges") or []:
        write(
            "edge",
            row,
            record_id=row.get("edge_id"),
            source=row.get("source"),
            target=row.get("target"),
            category=row.get("kind"),
        )
    for row in report.get("recrossing_episodes") or []:
        write(
            "recrossing",
            row,
            record_id=row.get("episode_id"),
            category=row.get("classification"),
        )
    for index, row in enumerate(report.get("truncations") or [], 1):
        write(
            "truncation",
            row,
            record_id=f"truncation-{index}",
            event_id=row.get("event_id") or row.get("next_event_id"),
            reason=row.get("reason"),
        )
    return output.getvalue()
