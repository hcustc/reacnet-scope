"""First-passage Species Fate Analysis over prepared molecular continuity."""

from __future__ import annotations

import csv
import heapq
import io
import json
import os
import re
import sqlite3
import statistics
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .event_index import (
    EVENT_CONTINUITY_ALGORITHM_VERSION,
    EVENT_CONTINUITY_SCHEMA_VERSION,
    EVENT_EVIDENCE_STORE,
    _EVENT_SELECT_COLUMNS,
    _event_payload_from_record,
)
from .indexes import IndexInvalidError, _readonly_connection


SPECIES_FATE_SCHEMA_VERSION = "species-fate/v1"
SPECIES_FATE_ALGORITHM_VERSION = 2
_ELEMENT_SYMBOLS = frozenset(
    """
    H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co
    Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb
    Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re
    Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es
    Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
    """.split()
)


class SpeciesFateError(RuntimeError):
    """Raised when a trustworthy Species Fate Result cannot be produced."""


class SpeciesFateQueryError(ValueError):
    """Raised when normalized Fate Query parameters are invalid."""


@dataclass(frozen=True)
class _Branch:
    instance_id: str
    anchor_ids: tuple[int, ...]


@dataclass(frozen=True)
class _ActiveEpisode:
    episode_id: str
    anchors: tuple[int, ...]
    birth_frame: int
    close_frame: int
    ordinal: int
    detail: dict[str, Any] | None


class _ActiveEpisodeIndex:
    """Index only episodes whose inclusive activity window can match a candidate."""

    def __init__(self) -> None:
        self._by_id: dict[str, _ActiveEpisode] = {}
        self._by_anchors: dict[tuple[int, ...], dict[str, _ActiveEpisode]] = (
            defaultdict(dict)
        )
        self._by_atom: dict[int, dict[str, _ActiveEpisode]] = defaultdict(dict)
        self._expiry: list[tuple[int, int, str]] = []
        self._next_ordinal = 0

    def expire_before(self, frame: int) -> None:
        while self._expiry and self._expiry[0][0] < frame:
            _close_frame, _ordinal, episode_id = heapq.heappop(self._expiry)
            episode = self._by_id.pop(episode_id, None)
            if episode is None:
                continue
            exact = self._by_anchors[episode.anchors]
            exact.pop(episode_id, None)
            if not exact:
                del self._by_anchors[episode.anchors]
            for atom_id in episode.anchors:
                containing = self._by_atom[atom_id]
                containing.pop(episode_id, None)
                if not containing:
                    del self._by_atom[atom_id]

    def exact(self, anchors: tuple[int, ...]) -> list[_ActiveEpisode]:
        return sorted(
            self._by_anchors.get(anchors, {}).values(),
            key=lambda row: row.ordinal,
        )

    def overlapping(self, anchors: tuple[int, ...]) -> list[_ActiveEpisode]:
        matches: dict[str, _ActiveEpisode] = {}
        for atom_id in anchors:
            matches.update(self._by_atom.get(atom_id, {}))
        return sorted(matches.values(), key=lambda row: row.ordinal)

    def add(
        self,
        episode: Mapping[str, Any],
        *,
        detail: dict[str, Any] | None,
    ) -> None:
        episode_id = str(episode["formation_episode_id"])
        anchors = tuple(int(value) for value in episode["anchor_atom_ids"])
        indexed = _ActiveEpisode(
            episode_id=episode_id,
            anchors=anchors,
            birth_frame=int(episode["birth"]["analyzed_frame"]),
            close_frame=int(episode["close_frame"]),
            ordinal=self._next_ordinal,
            detail=detail,
        )
        self._next_ordinal += 1
        self._by_id[episode_id] = indexed
        self._by_anchors[anchors][episode_id] = indexed
        for atom_id in anchors:
            self._by_atom[atom_id][episode_id] = indexed
        heapq.heappush(
            self._expiry,
            (indexed.close_frame, indexed.ordinal, indexed.episode_id),
        )


def _strict_integer(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        parsed = int(value.strip())
    else:
        raise SpeciesFateQueryError(f"{label} must be an integer")
    if minimum is not None and parsed < minimum:
        raise SpeciesFateQueryError(f"{label} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise SpeciesFateQueryError(f"{label} must be at most {maximum}")
    return parsed


def _normalize_anchor_mode(value: Any) -> str:
    mode = str(value or "heavy_atoms").strip().lower()
    normalized = {
        "heavy_atoms": "heavy_atoms",
        "non_hydrogen": "heavy_atoms",
        "all": "all_atoms",
        "all_atoms": "all_atoms",
        "elements": "elements",
        "atom_ids": "atom_ids",
    }.get(mode)
    if normalized is None:
        raise SpeciesFateQueryError(
            "anchor_mode must be heavy_atoms, all_atoms, elements, or atom_ids"
        )
    return normalized


def _normalize_element(value: Any, label: str) -> str:
    text = str(value or "").strip()
    normalized = text[:1].upper() + text[1:].lower()
    if normalized not in _ELEMENT_SYMBOLS:
        raise SpeciesFateQueryError(f"{label} contains an invalid element symbol")
    return normalized


def _normalize_atom_elements(
    values: Mapping[int, str] | None,
) -> dict[int, str]:
    normalized: dict[int, str] = {}
    for raw_atom_id, raw_element in (values or {}).items():
        atom_id = _strict_integer(raw_atom_id, "atom_elements Atom ID", minimum=0)
        element = _normalize_element(raw_element, "atom_elements")
        previous = normalized.get(atom_id)
        if previous is not None and previous != element:
            raise SpeciesFateQueryError(
                f"atom_elements contains conflicting mappings for Atom ID {atom_id}"
            )
        normalized[atom_id] = element
    return dict(sorted(normalized.items()))


def _decode_list(raw: Any, label: str) -> list[Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError) as exc:
        raise SpeciesFateError(f"continuity {label} is invalid") from exc
    if not isinstance(value, list):
        raise SpeciesFateError(f"continuity {label} must be a list")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_id(prefix: str, value: Any, *, length: int = 20) -> str:
    return f"{prefix}_{sha1(_canonical_json(value).encode('utf-8')).hexdigest()[:length]}"


def _source_signature(path: str) -> dict[str, Any]:
    resolved = os.path.abspath(path)
    stat = os.stat(resolved)
    return {
        "path": resolved,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _normalize_endpoints(
    target_species: str,
    endpoint_categories: Mapping[str, Iterable[str]],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    target = str(target_species or "").strip()
    if not target:
        raise SpeciesFateQueryError("target_species is required")
    normalized: dict[str, list[str]] = {}
    species_to_category: dict[str, str] = {}
    for raw_name, raw_species in endpoint_categories.items():
        name = str(raw_name or "").strip()
        if not name:
            raise SpeciesFateQueryError("endpoint category names must not be empty")
        values = sorted({str(value).strip() for value in raw_species if str(value).strip()})
        if not values:
            raise SpeciesFateQueryError(
                f"endpoint category {name!r} must contain a Species"
            )
        if target in values:
            raise SpeciesFateQueryError(
                "endpoint categories must not contain the target Species"
            )
        for species in values:
            previous = species_to_category.get(species)
            if previous is not None:
                raise SpeciesFateQueryError(
                    f"Species {species!r} belongs to both {previous!r} and {name!r}"
                )
            species_to_category[species] = name
        normalized[name] = values
    if not normalized:
        raise SpeciesFateQueryError("at least one endpoint category is required")
    return dict(sorted(normalized.items())), species_to_category


def _resolve_anchors(
    atom_ids: Sequence[int],
    *,
    anchor_mode: str,
    atom_elements: Mapping[int, str],
    anchor_elements: Iterable[str],
    anchor_atom_ids: Iterable[int],
) -> tuple[int, ...]:
    available = {int(value) for value in atom_ids}
    mode = _normalize_anchor_mode(anchor_mode)
    if mode == "all_atoms":
        selected = available
    elif mode == "atom_ids":
        selected = {
            _strict_integer(value, "anchor_atom_ids", minimum=0)
            for value in anchor_atom_ids
        }
        unknown = selected - available
        if unknown:
            raise SpeciesFateQueryError(
                "anchor Atom IDs are not in the formation instance: "
                + ",".join(map(str, sorted(unknown)))
            )
    elif mode in {"heavy_atoms", "elements"}:
        missing = sorted(value for value in available if not atom_elements.get(value))
        if missing:
            raise SpeciesFateQueryError(
                "anchor policy requires a reliable atom ID to element mapping for: "
                + ",".join(map(str, missing))
            )
        allowed = (
            {
                _normalize_element(value, "anchor_elements")
                for value in anchor_elements
                if str(value).strip()
            }
            if mode == "elements"
            else {str(atom_elements[value]) for value in available if str(atom_elements[value]) != "H"}
        )
        if mode == "elements" and not allowed:
            raise SpeciesFateQueryError("anchor_elements must not be empty")
        selected = {
            value for value in available if str(atom_elements[value]) in allowed
        }
    if not selected:
        raise SpeciesFateQueryError("anchor policy selected no atoms")
    return tuple(sorted(selected))


class _ContinuityReader:
    def __init__(
        self,
        reactionevent_file: str,
        molecules_file: str,
    ) -> None:
        opened = EVENT_EVIDENCE_STORE.open_continuity_required(
            reactionevent_file, molecules_file
        )
        self.opened = opened
        self.reactionevent_file = str(reactionevent_file)
        self.molecules_file = str(molecules_file)
        self.connection = _readonly_connection(Path(str(opened["index_path"])))
        self._instances: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._participants: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def close(self) -> None:
        self.connection.close()

    def clear_traversal_cache(self) -> None:
        """Release raw rows after one episode has copied its retained evidence."""

        self._instances.clear()
        self._events.clear()
        self._participants.clear()

    def species_catalog(self) -> dict[str, str]:
        return {
            str(species): str(species_id)
            for species_id, species in self.connection.execute(
                "SELECT species_id,species_smiles FROM continuity_species"
            )
        }

    def formation_candidates(
        self,
        target_species_id: str,
        *,
        start_frame: int,
        end_frame: int,
    ) -> list[dict[str, Any]]:
        excluded_event_ids = {
            str(event_id)
            for (event_ids_json,) in self.connection.execute(
                """
                SELECT event_ids_json
                FROM continuity_diagnostics
                WHERE species_id=?
                  AND timestep_index BETWEEN ? AND ?
                  AND reason IN (
                      'multiple_producers_same_transition',
                      'same_transition_event_dependency_ambiguous'
                  )
                """,
                (
                    str(target_species_id),
                    max(0, int(start_frame) - 1),
                    max(0, int(end_frame) - 1),
                ),
            )
            for event_id in _decode_list(event_ids_json, "diagnostic event_ids")
        }
        records = self.connection.execute(
            """
            SELECT ep.event_id,ep.participant_index,ep.instance_id,
                   ep.timestep_index,mi.analyzed_frame,mi.source_timestep,
                   mi.species_id,mi.species_smiles,mi.atom_ids_json,
                   mi.bonds_json,mi.structure_key,e.source_row
            FROM event_participants AS ep
            JOIN molecule_instances AS mi ON mi.instance_id=ep.instance_id
            JOIN events AS e ON e.event_id=ep.event_id
            WHERE ep.side='product' AND ep.species_id=?
              AND mi.analyzed_frame BETWEEN ? AND ?
              AND e.association_status='matched'
              AND NOT EXISTS (
                  SELECT 1 FROM event_participants AS reactant
                  WHERE reactant.event_id=ep.event_id
                    AND reactant.side='reactant'
                    AND reactant.structure_key=ep.structure_key
              )
            ORDER BY mi.analyzed_frame,e.source_row,ep.event_id,
                     ep.participant_index
            """,
            (str(target_species_id), int(start_frame), int(end_frame)),
        ).fetchall()
        output: list[dict[str, Any]] = []
        for record in records:
            (
                event_id,
                participant_index,
                instance_id,
                timestep_index,
                analyzed_frame,
                source_timestep,
                species_id,
                species_smiles,
                atom_ids_json,
                bonds_json,
                structure_key,
                source_row,
            ) = record
            if str(event_id) in excluded_event_ids:
                continue
            output.append(
                {
                    "event_id": str(event_id),
                    "participant_index": int(participant_index),
                    "instance_id": str(instance_id),
                    "timestep_index": int(timestep_index),
                    "analyzed_frame": int(analyzed_frame),
                    "source_timestep": int(source_timestep),
                    "species_id": str(species_id),
                    "species": str(species_smiles),
                    "atom_ids": [int(value) for value in _decode_list(atom_ids_json, "atom_ids")],
                    "bonds": [str(value) for value in _decode_list(bonds_json, "bonds")],
                    "structure_key": str(structure_key),
                    "source_row": int(source_row),
                }
            )
        return output

    def instance(self, instance_id: str) -> dict[str, Any]:
        cached = self._instances.get(str(instance_id))
        if cached is not None:
            return cached
        record = self.connection.execute(
            """
            SELECT instance_id,replicate_id,analyzed_frame,source_timestep,
                   species_id,species_smiles,atom_ids_json,bonds_json,
                   structure_key
            FROM molecule_instances WHERE instance_id=?
            """,
            (str(instance_id),),
        ).fetchone()
        if record is None:
            raise SpeciesFateError(
                f"continuity instance {instance_id!r} is missing"
            )
        row = {
            "instance_id": str(record[0]),
            "replicate_id": str(record[1]),
            "analyzed_frame": int(record[2]),
            "source_timestep": int(record[3]),
            "species_id": str(record[4]),
            "species": str(record[5]),
            "atom_ids": [int(value) for value in _decode_list(record[6], "atom_ids")],
            "bonds": [str(value) for value in _decode_list(record[7], "bonds")],
            "structure_key": str(record[8]),
        }
        self._instances[str(instance_id)] = row
        return row

    def link(self, instance_id: str) -> dict[str, Any] | None:
        record = self.connection.execute(
            """
            SELECT status,next_timestep_index,next_event_ids_json,
                   to_reactant_instance_id,reason
            FROM continuity_links WHERE from_instance_id=?
            """,
            (str(instance_id),),
        ).fetchone()
        if record is None:
            return None
        return {
            "status": str(record[0]),
            "next_timestep_index": int(record[1]),
            "event_ids": [str(value) for value in _decode_list(record[2], "next_event_ids")],
            "to_reactant_instance_id": str(record[3]),
            "reason": str(record[4]),
        }

    def event(self, event_id: str) -> dict[str, Any]:
        cached = self._events.get(str(event_id))
        if cached is not None:
            return cached
        record = self.connection.execute(
            f"SELECT {_EVENT_SELECT_COLUMNS} FROM events WHERE event_id=?",
            (str(event_id),),
        ).fetchone()
        if record is None:
            raise SpeciesFateError(f"continuity event {event_id!r} is missing")
        row = _event_payload_from_record(record, event_index=1)
        self._events[str(event_id)] = row
        return row

    def participants(self, event_id: str, side: str) -> list[dict[str, Any]]:
        key = (str(event_id), str(side))
        cached = self._participants.get(key)
        if cached is not None:
            return cached
        rows = []
        for participant_index, instance_id in self.connection.execute(
            """
            SELECT participant_index,instance_id
            FROM event_participants
            WHERE event_id=? AND side=?
            ORDER BY participant_index
            """,
            key,
        ):
            instance = dict(self.instance(str(instance_id)))
            instance["participant_index"] = int(participant_index)
            rows.append(instance)
        self._participants[key] = rows
        return rows


def _distribution(values: Sequence[int | float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    numeric = [float(value) for value in values]
    return {
        "count": len(numeric),
        "min": min(numeric),
        "median": statistics.median(numeric),
        "mean": statistics.fmean(numeric),
        "max": max(numeric),
    }


def _fate_signature(terminals: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    multiplicity = Counter(str(row["endpoint_category"]) for row in terminals)
    members = [
        {"endpoint_category": category, "multiplicity": int(count)}
        for category, count in sorted(multiplicity.items())
    ]
    return {
        "fate_signature_id": _stable_id("rngfatesig", members),
        "members": members,
        "label": " + ".join(
            f"{row['endpoint_category']} × {row['multiplicity']}" for row in members
        ),
    }


def _episode_identifier(
    replicate_id: str,
    birth: Mapping[str, Any],
    anchors: Sequence[int],
) -> str:
    return _stable_id(
        "rngfateep",
        [
            str(replicate_id),
            str(birth["event_id"]),
            str(birth["instance_id"]),
            sorted(int(value) for value in anchors),
        ],
        length=24,
    )


def _censor(
    branch: _Branch,
    *,
    reason: str,
    frame: int,
    event_ids: Iterable[str] = (),
    detail: str = "",
) -> dict[str, Any]:
    return {
        "instance_id": branch.instance_id,
        "anchor_atom_ids": list(branch.anchor_ids),
        "reason": str(reason),
        "analyzed_frame": int(frame),
        "event_ids": sorted({str(value) for value in event_ids}),
        "detail": str(detail),
    }


def _traverse_episode(
    reader: _ContinuityReader,
    birth: Mapping[str, Any],
    anchors: tuple[int, ...],
    *,
    endpoint_by_species: Mapping[str, str],
    target_species_id: str,
    followup_end_frame: int,
    max_events: int,
    max_active_branches: int,
) -> dict[str, Any]:
    replicate_id = str(reader.opened["dataset_id"])
    episode_id = _episode_identifier(replicate_id, birth, anchors)
    active: list[_Branch] = [_Branch(str(birth["instance_id"]), anchors)]
    terminals: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    event_rows: dict[str, dict[str, Any]] = {
        str(birth["event_id"]): reader.event(str(birth["event_id"]))
    }
    topology = "linear"
    processed_events = 0
    first_exit: dict[str, Any] | None = None
    initial_structure_key = str(birth["structure_key"])
    initial_residence: dict[str, Any] | None = None
    cumulative_target_frames = 0
    cumulative_target_timesteps = 0
    molecule_nodes: dict[str, dict[str, Any]] = {}
    continuity_edges: dict[str, dict[str, Any]] = {}

    def retain_instance(instance_id: str, retained_anchors: Iterable[int]) -> None:
        instance = dict(reader.instance(instance_id))
        anchors_for_instance = sorted({int(value) for value in retained_anchors})
        existing = molecule_nodes.get(instance_id)
        if existing is None:
            instance["anchor_atom_ids"] = anchors_for_instance
            molecule_nodes[instance_id] = instance
        else:
            existing["anchor_atom_ids"] = sorted(
                set(existing["anchor_atom_ids"]) | set(anchors_for_instance)
            )

    def retain_boundary_events(event_ids: Iterable[str]) -> None:
        for event_id in sorted({str(value) for value in event_ids if str(value)}):
            event_rows.setdefault(event_id, reader.event(event_id))

    retain_instance(str(birth["instance_id"]), anchors)

    while active:
        if len(active) > max_active_branches:
            for branch in active:
                instance = reader.instance(branch.instance_id)
                censored.append(
                    _censor(
                        branch,
                        reason="active_branch_limit",
                        frame=int(instance["analyzed_frame"]),
                    )
                )
            active = []
            break

        schedulable: list[tuple[int, str, _Branch, dict[str, Any]]] = []
        remaining: list[_Branch] = []
        for branch in active:
            instance = reader.instance(branch.instance_id)
            link = reader.link(branch.instance_id)
            if link is None:
                censored.append(
                    _censor(
                        branch,
                        reason="followup_endpoint",
                        frame=followup_end_frame,
                        detail="no later observed participation",
                    )
                )
                continue
            next_frame = int(link["next_timestep_index"]) + 1
            if next_frame > followup_end_frame:
                censored.append(
                    _censor(
                        branch,
                        reason="followup_endpoint",
                        frame=followup_end_frame,
                        event_ids=link["event_ids"],
                    )
                )
                continue
            if link["status"] != "matched" or len(link["event_ids"]) != 1:
                retain_boundary_events(link["event_ids"])
                censored.append(
                    _censor(
                        branch,
                        reason="evidence_censored",
                        frame=int(link["next_timestep_index"]),
                        event_ids=link["event_ids"],
                        detail=link["reason"] or link["status"],
                    )
                )
                continue
            schedulable.append(
                (
                    int(link["next_timestep_index"]),
                    str(link["event_ids"][0]),
                    branch,
                    link,
                )
            )
        if not schedulable:
            active = remaining
            continue

        next_timestep = min(value[0] for value in schedulable)
        next_event_id = min(
            value[1] for value in schedulable if value[0] == next_timestep
        )
        group = [
            value
            for value in schedulable
            if value[0] == next_timestep and value[1] == next_event_id
        ]
        active = [
            value[2]
            for value in schedulable
            if not (value[0] == next_timestep and value[1] == next_event_id)
        ]
        if processed_events >= max_events:
            retain_boundary_events([next_event_id])
            for _time, _event_id, branch, _link in group:
                censored.append(
                    _censor(
                        branch,
                        reason="event_limit",
                        frame=next_timestep,
                        event_ids=[next_event_id],
                    )
                )
            continue

        event = reader.event(next_event_id)
        event_rows[next_event_id] = event
        processed_events += 1
        if len(group) > 1:
            topology = "complex"
        incoming_anchors = set(
            value
            for _time, _event_id, branch, _link in group
            for value in branch.anchor_ids
        )
        products = reader.participants(next_event_id, "product")
        allocations: list[tuple[dict[str, Any], tuple[int, ...]]] = []
        assignment_count: Counter[int] = Counter()
        for product in products:
            retained = tuple(
                sorted(incoming_anchors.intersection(product["atom_ids"]))
            )
            if retained:
                allocations.append((product, retained))
                assignment_count.update(retained)
        invalid_anchors = sorted(
            value
            for value in incoming_anchors
            if assignment_count.get(value, 0) != 1
        )
        if invalid_anchors:
            reason = (
                "anchor_missing"
                if any(assignment_count.get(value, 0) == 0 for value in invalid_anchors)
                else "anchor_duplicate"
            )
            for _time, _event_id, branch, _link in group:
                censored.append(
                    _censor(
                        branch,
                        reason=reason,
                        frame=next_timestep,
                        event_ids=[next_event_id],
                        detail=",".join(map(str, invalid_anchors)),
                    )
                )
            continue
        if len(allocations) != len(group):
            topology = "complex"
        if len(allocations) > 1:
            topology = "complex"

        event_before = int(event["before_timestep"])
        event_after = int(event["after_timestep"])
        for _time, _event_id, branch, _link in group:
            current = reader.instance(branch.instance_id)
            retain_instance(branch.instance_id, branch.anchor_ids)
            if current["species_id"] == target_species_id:
                cumulative_target_frames += max(
                    0, next_timestep - int(current["analyzed_frame"])
                )
                cumulative_target_timesteps += max(
                    0, event_before - int(current["source_timestep"])
                )

        identity_continues = (
            len(group) == 1
            and len(allocations) == 1
            and str(allocations[0][0]["structure_key"])
            == str(reader.instance(group[0][2].instance_id)["structure_key"])
        )
        if not identity_continues and first_exit is None:
            first_exit = {
                "event_id": next_event_id,
                "reaction_type": str(event["reaction_key"]),
                "reaction_key": str(event["reaction_key"]),
                "timestep_index": next_timestep,
                "before_timestep": event_before,
                "after_timestep": event_after,
            }
            initial_residence = {
                "frames": max(0, next_timestep - int(birth["analyzed_frame"])),
                "source_timesteps": max(
                    0, event_before - int(birth["source_timestep"])
                ),
                "start_frame": int(birth["analyzed_frame"]),
                "end_frame": next_timestep,
                "start_timestep": int(birth["source_timestep"]),
                "end_timestep": event_before,
            }

        for product, retained in allocations:
            retain_instance(str(product["instance_id"]), retained)
            for _time, _event_id, branch, _link in group:
                allocated = sorted(
                    set(branch.anchor_ids).intersection(retained)
                )
                if not allocated:
                    continue
                edge_payload = {
                    "from_instance_id": branch.instance_id,
                    "event_id": next_event_id,
                    "to_instance_id": str(product["instance_id"]),
                    "anchor_atom_ids": allocated,
                }
                edge_id = _stable_id("rngfateedge", edge_payload, length=24)
                continuity_edges[edge_id] = {
                    "edge_id": edge_id,
                    **edge_payload,
                }
            category = endpoint_by_species.get(str(product["species"]))
            if category is not None:
                terminals.append(
                    {
                        "terminal_instance_id": str(product["instance_id"]),
                        "endpoint_category": category,
                        "species_id": str(product["species_id"]),
                        "species": str(product["species"]),
                        "anchor_atom_ids": list(retained),
                        "first_passage_event_id": next_event_id,
                        "timestep_index": next_timestep,
                        "analyzed_frame": int(product["analyzed_frame"]),
                        "source_timestep": int(product["source_timestep"]),
                        "terminal_first_passage_time": {
                            "frames": int(product["analyzed_frame"])
                            - int(birth["analyzed_frame"]),
                            "source_timesteps": int(product["source_timestep"])
                            - int(birth["source_timestep"]),
                        },
                    }
                )
            else:
                active.append(_Branch(str(product["instance_id"]), retained))

    terminal_anchor_ids = [
        int(value)
        for terminal in terminals
        for value in terminal["anchor_atom_ids"]
    ]
    partition_complete = (
        not censored
        and len(terminal_anchor_ids) == len(set(terminal_anchor_ids))
        and set(terminal_anchor_ids) == set(anchors)
    )
    fully_resolved = bool(partition_complete)
    signature = _fate_signature(terminals) if fully_resolved else None
    sorted_terminals = sorted(
        terminals,
        key=lambda row: (
            int(row["timestep_index"]),
            str(row["endpoint_category"]),
            str(row["terminal_instance_id"]),
        ),
    )
    first_hit_time = None
    first_hit_fate: list[dict[str, Any]] = []
    if sorted_terminals:
        first_frame = int(sorted_terminals[0]["analyzed_frame"])
        first_rows = [
            row for row in sorted_terminals if int(row["analyzed_frame"]) == first_frame
        ]
        first_hit_time = {
            "frames": first_frame - int(birth["analyzed_frame"]),
            "source_timesteps": min(
                int(row["source_timestep"]) for row in first_rows
            )
            - int(birth["source_timestep"]),
        }
        first_hit_fate = _fate_signature(first_rows)["members"]
    completion_time = None
    if fully_resolved and sorted_terminals:
        last = max(sorted_terminals, key=lambda row: int(row["analyzed_frame"]))
        completion_time = {
            "frames": int(last["analyzed_frame"]) - int(birth["analyzed_frame"]),
            "source_timesteps": int(last["source_timestep"])
            - int(birth["source_timestep"]),
        }
    close_frame_candidates = [
        *(int(row["analyzed_frame"]) for row in terminals),
        *(int(row["analyzed_frame"]) for row in censored),
        int(birth["analyzed_frame"]),
    ]
    ordered_events = sorted(
        event_rows.values(),
        key=lambda row: (
            int(row["timestep_index"]),
            int(row["source_row"]),
            str(row["event_id"]),
        ),
    )
    downstream_events = [
        row for row in ordered_events if str(row["event_id"]) != str(birth["event_id"])
    ]
    return {
        "formation_episode_id": episode_id,
        "replicate_id": replicate_id,
        "birth": dict(birth),
        "anchor_atom_ids": list(anchors),
        "status": "fully_resolved" if fully_resolved else "censored",
        "fully_resolved": fully_resolved,
        "partition_complete": partition_complete,
        "fate_signature": signature,
        "partial_fate": None if fully_resolved else {
            "terminal_instances": sorted_terminals,
            "censored_anchor_subsets": censored,
        },
        "terminal_instances": sorted_terminals,
        "censored_anchor_subsets": censored,
        "censoring_reasons": sorted({str(row["reason"]) for row in censored}),
        "first_hit_fate": first_hit_fate,
        "first_exit_channel": first_exit,
        "initial_residence_time": initial_residence,
        "first_hit_time": first_hit_time,
        "descendant_completion_time": completion_time,
        "cumulative_target_branch_time": {
            "frames": cumulative_target_frames,
            "source_timesteps": cumulative_target_timesteps,
            "is_lower_bound": not fully_resolved,
        },
        "topology": topology,
        "linear_reaction_type_sequence": (
            [str(row["reaction_key"]) for row in downstream_events]
            if topology == "linear" and fully_resolved
            else None
        ),
        "event_ids": [str(row["event_id"]) for row in ordered_events],
        "events": ordered_events,
        "molecule_instances": sorted(
            molecule_nodes.values(),
            key=lambda row: (
                int(row["analyzed_frame"]),
                str(row["instance_id"]),
            ),
        ),
        "continuity_edges": sorted(
            continuity_edges.values(), key=lambda row: str(row["edge_id"])
        ),
        "close_frame": max(close_frame_candidates),
        "processed_event_count": processed_events,
        "overlapping_active_episode_ids": [],
        "overlapping_anchor_ids": {},
        "return_candidates": [],
    }


def _aggregate(
    episodes: Sequence[Mapping[str, Any]],
    *,
    endpoint_categories: Iterable[str],
    formation_start_frame: int,
    formation_end_frame: int,
    followup_end_frame: int,
) -> dict[str, Any]:
    eligible = [row for row in episodes if not row.get("insufficient_followup")]
    resolved = [row for row in eligible if row.get("fully_resolved")]
    resolved_count = len(resolved)
    signatures: dict[str, dict[str, Any]] = {}
    for episode in resolved:
        signature = dict(episode["fate_signature"])
        signature_id = str(signature["fate_signature_id"])
        aggregate = signatures.setdefault(
            signature_id,
            {
                **signature,
                "episode_count": 0,
                "formation_episode_ids": [],
            },
        )
        aggregate["episode_count"] += 1
        aggregate["formation_episode_ids"].append(
            str(episode["formation_episode_id"])
        )
    for aggregate in signatures.values():
        aggregate["resolved_episode_conditional_probability"] = (
            aggregate["episode_count"] / resolved_count
            if resolved_count
            else None
        )

    categories = sorted({str(value) for value in endpoint_categories})
    marginals = []
    for category in categories:
        per_episode = [
            sum(
                str(row["endpoint_category"]) == category
                for row in episode["terminal_instances"]
            )
            for episode in resolved
        ]
        occurrences = sum(value > 0 for value in per_episode)
        total = sum(per_episode)
        marginals.append(
            {
                "endpoint_category": category,
                "episode_occurrence_count": occurrences,
                "terminal_instance_count": total,
                "marginal_occurrence_probability": (
                    occurrences / resolved_count if resolved_count else None
                ),
                "unconditional_mean_multiplicity": (
                    total / resolved_count if resolved_count else None
                ),
                "conditional_mean_multiplicity": (
                    total / occurrences if occurrences else None
                ),
            }
        )

    censor_counter: Counter[str] = Counter()
    for episode in episodes:
        for reason in set(episode.get("censoring_reasons") or []):
            censor_counter[str(reason)] += 1
    formation_resolution: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for episode in episodes:
        frame = int(episode["birth"]["analyzed_frame"])
        formation_resolution[frame][0] += 1
        formation_resolution[frame][1] += int(bool(episode["fully_resolved"]))
    followup_potential = [
        followup_end_frame - int(row["birth"]["analyzed_frame"])
        for row in episodes
    ]
    followup_observed = [
        int(row["close_frame"]) - int(row["birth"]["analyzed_frame"])
        for row in episodes
    ]

    first_exit_counts: Counter[str] = Counter(
        str(row["first_exit_channel"]["reaction_type"])
        for row in resolved
        if row.get("first_exit_channel")
    )
    linear_rows = [
        row
        for row in resolved
        if row.get("linear_reaction_type_sequence") is not None
    ]
    linear_counts: Counter[str] = Counter(
        _canonical_json(row["linear_reaction_type_sequence"])
        for row in linear_rows
    )
    return {
        "formation_count": len(episodes),
        "main_cohort_size": len(eligible),
        "insufficient_followup_count": len(episodes) - len(eligible),
        "fully_resolved_count": resolved_count,
        "censored_count": sum(not row.get("fully_resolved") for row in episodes),
        "resolution_fraction": (
            resolved_count / len(eligible) if eligible else None
        ),
        "censoring_fraction": (
            sum(not row.get("fully_resolved") for row in episodes) / len(episodes)
            if episodes
            else None
        ),
        "censoring_by_reason": [
            {
                "reason": reason,
                "episode_count": count,
                "fraction_of_created_episodes": (
                    count / len(episodes) if episodes else None
                ),
            }
            for reason, count in sorted(censor_counter.items())
        ],
        "fate_signatures": sorted(
            signatures.values(), key=lambda row: str(row["fate_signature_id"])
        ),
        "endpoint_marginals": marginals,
        "first_exit_channels": [
            {
                "reaction_type": channel,
                "episode_count": count,
                "first_exit_channel_conditional_probability": (
                    count / resolved_count if resolved_count else None
                ),
            }
            for channel, count in sorted(first_exit_counts.items())
        ],
        "linear_paths": [
            {
                "linear_reaction_type_sequence": json.loads(sequence),
                "episode_count": count,
                "linear_path_conditional_probability": (
                    count / len(linear_rows) if linear_rows else None
                ),
            }
            for sequence, count in sorted(linear_counts.items())
        ],
        "topology_eligible_count": len(linear_rows),
        "topology_eligible_fraction": (
            len(linear_rows) / resolved_count if resolved_count else None
        ),
        "complex_topology_count": sum(
            row.get("topology") != "linear" for row in episodes
        ),
        "formation_time_resolution": [
            {
                "analyzed_frame": frame,
                "episode_count": counts[0],
                "fully_resolved_count": counts[1],
                "resolution_rate": counts[1] / counts[0],
            }
            for frame, counts in sorted(formation_resolution.items())
        ],
        "followup_distribution": {
            "potential_frames": _distribution(followup_potential),
            "observed_frames": _distribution(followup_observed),
        },
        "formation_exposure": {
            "start_frame": formation_start_frame,
            "end_frame": formation_end_frame,
            "frame_count": max(0, formation_end_frame - formation_start_frame + 1),
            "followup_end_frame": followup_end_frame,
        },
        "time_distributions": {
            "initial_residence_frames": _distribution(
                [
                    int(row["initial_residence_time"]["frames"])
                    for row in resolved
                    if row.get("initial_residence_time")
                ]
            ),
            "initial_residence_source_timesteps": _distribution(
                [
                    int(row["initial_residence_time"]["source_timesteps"])
                    for row in resolved
                    if row.get("initial_residence_time")
                ]
            ),
            "terminal_first_passage_frames": _distribution(
                [
                    int(terminal["terminal_first_passage_time"]["frames"])
                    for row in resolved
                    for terminal in row["terminal_instances"]
                ]
            ),
            "terminal_first_passage_source_timesteps": _distribution(
                [
                    int(
                        terminal["terminal_first_passage_time"][
                            "source_timesteps"
                        ]
                    )
                    for row in resolved
                    for terminal in row["terminal_instances"]
                ]
            ),
            "first_hit_frames": _distribution(
                [
                    int(row["first_hit_time"]["frames"])
                    for row in resolved
                    if row.get("first_hit_time")
                ]
            ),
            "first_hit_source_timesteps": _distribution(
                [
                    int(row["first_hit_time"]["source_timesteps"])
                    for row in resolved
                    if row.get("first_hit_time")
                ]
            ),
            "descendant_completion_frames": _distribution(
                [
                    int(row["descendant_completion_time"]["frames"])
                    for row in resolved
                    if row.get("descendant_completion_time")
                ]
            ),
            "descendant_completion_source_timesteps": _distribution(
                [
                    int(
                        row["descendant_completion_time"]["source_timesteps"]
                    )
                    for row in resolved
                    if row.get("descendant_completion_time")
                ]
            ),
            "cumulative_target_branch_frames": _distribution(
                [
                    int(row["cumulative_target_branch_time"]["frames"])
                    for row in resolved
                ]
            ),
            "cumulative_target_branch_source_timesteps": _distribution(
                [
                    int(
                        row["cumulative_target_branch_time"][
                            "source_timesteps"
                        ]
                    )
                    for row in resolved
                ]
            ),
        },
    }


def _episode_for_aggregation(episode: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only fields consumed by aggregate statistics."""

    return {
        "formation_episode_id": str(episode["formation_episode_id"]),
        "birth": {
            "analyzed_frame": int(episode["birth"]["analyzed_frame"]),
        },
        "insufficient_followup": bool(episode.get("insufficient_followup")),
        "fully_resolved": bool(episode.get("fully_resolved")),
        "fate_signature": episode.get("fate_signature"),
        "terminal_instances": [
            {
                "endpoint_category": str(terminal["endpoint_category"]),
                "terminal_first_passage_time": dict(
                    terminal["terminal_first_passage_time"]
                ),
            }
            for terminal in episode.get("terminal_instances") or []
        ],
        "censoring_reasons": list(episode.get("censoring_reasons") or []),
        "close_frame": int(episode["close_frame"]),
        "first_exit_channel": episode.get("first_exit_channel"),
        "linear_reaction_type_sequence": episode.get(
            "linear_reaction_type_sequence"
        ),
        "topology": str(episode.get("topology") or ""),
        "initial_residence_time": episode.get("initial_residence_time"),
        "first_hit_time": episode.get("first_hit_time"),
        "descendant_completion_time": episode.get(
            "descendant_completion_time"
        ),
        "cumulative_target_branch_time": dict(
            episode["cumulative_target_branch_time"]
        ),
    }


def _build_result_document(
    reader: _ContinuityReader,
    *,
    query: Mapping[str, Any],
    query_id: str,
    status: str,
    summary: Mapping[str, Any] | None,
    episode_summaries: Sequence[Mapping[str, Any]],
    retained_episodes: Sequence[dict[str, Any]],
    detail_limit: int,
    suppressed_returns: Sequence[Mapping[str, Any]],
    failure: Mapping[str, Any] | None = None,
    global_incompleteness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_signatures = {
        "reaction_evidence": _source_signature(
            reader.reactionevent_file
        ),
        "event_index": _source_signature(str(reader.opened["index_path"])),
    }
    molecular_source = reader.molecules_file
    if molecular_source and Path(molecular_source).is_file():
        source_signatures["molecular_evidence"] = _source_signature(
            molecular_source
        )
    result_identity = {
        "query_id": query_id,
        "dataset_id": str(reader.opened["dataset_id"]),
        "source_signatures": source_signatures,
        "continuity_schema_version": EVENT_CONTINUITY_SCHEMA_VERSION,
        "continuity_algorithm_version": EVENT_CONTINUITY_ALGORITHM_VERSION,
        "fate_algorithm_version": SPECIES_FATE_ALGORITHM_VERSION,
    }
    retained = list(retained_episodes)
    for episode in retained:
        episode["details_retained"] = True
    return {
        "schema_version": SPECIES_FATE_SCHEMA_VERSION,
        "algorithm_version": SPECIES_FATE_ALGORITHM_VERSION,
        "status": status,
        "fate_query_id": query_id,
        "fate_result_id": _stable_id(
            "rngfateresult", result_identity, length=24
        ),
        "replicate_id": str(reader.opened["dataset_id"]),
        "query": dict(query),
        "summary": dict(summary) if summary is not None else None,
        "episodes": retained,
        "episode_count": (
            len(episode_summaries) if status == "complete" else None
        ),
        "processed_episode_count": len(episode_summaries),
        "episode_count_complete": status == "complete",
        "detail_retention": {
            "limit": detail_limit,
            "retained_episode_count": len(retained),
            "omitted_episode_count": max(
                0, len(episode_summaries) - len(retained)
            ),
            "details_complete": len(retained) == len(episode_summaries),
            "selection_rule": "chronological_birth_order",
        },
        "suppressed_return_candidates": list(suppressed_returns),
        "left_censored_initial_instances": [],
        "left_censored_detection_complete": False,
        "unresolved_evidence": {
            "diagnostic_count": int(
                reader.connection.execute(
                    "SELECT COUNT(*) FROM continuity_diagnostics"
                ).fetchone()[0]
            )
        },
        "failure": dict(failure) if failure else None,
        "global_incompleteness": (
            dict(global_incompleteness) if global_incompleteness else None
        ),
        "source_signatures": source_signatures,
        "versions": {
            "event_continuity_schema": EVENT_CONTINUITY_SCHEMA_VERSION,
            "event_continuity_algorithm": EVENT_CONTINUITY_ALGORITHM_VERSION,
            "species_fate_algorithm": SPECIES_FATE_ALGORITHM_VERSION,
        },
    }


def analyze_species_fate(
    reactionevent_file: str,
    molecules_file: str,
    *,
    target_species: str,
    endpoint_categories: Mapping[str, Iterable[str]],
    atom_elements: Mapping[int, str] | None = None,
    anchor_mode: str = "heavy_atoms",
    anchor_elements: Iterable[str] = (),
    anchor_atom_ids: Iterable[int] = (),
    formation_start_frame: int = 0,
    formation_end_frame: int | None = None,
    followup_end_frame: int | None = None,
    minimum_followup_frames: int = 0,
    max_events_per_episode: int = 10_000,
    max_active_branches: int = 1_000,
    detail_retention_limit: int = 1_000,
    global_episode_limit: int | None = None,
) -> dict[str, Any]:
    """Analyze descendant-complete first-passage fates for one exact Species."""

    normalized_endpoints, endpoint_by_species = _normalize_endpoints(
        target_species, endpoint_categories
    )
    normalized_anchor_mode = _normalize_anchor_mode(anchor_mode)
    normalized_anchor_elements = sorted(
        {
            _normalize_element(value, "anchor_elements")
            for value in anchor_elements
            if str(value).strip()
        }
    )
    if normalized_anchor_mode == "elements" and not normalized_anchor_elements:
        raise SpeciesFateQueryError("anchor_elements must not be empty")
    normalized_anchor_atom_ids = sorted(
        {
            _strict_integer(value, "anchor_atom_ids", minimum=0)
            for value in anchor_atom_ids
        }
    )
    if normalized_anchor_mode == "atom_ids" and not normalized_anchor_atom_ids:
        raise SpeciesFateQueryError("anchor_atom_ids must not be empty")
    effective_anchor_elements = (
        normalized_anchor_elements
        if normalized_anchor_mode == "elements"
        else []
    )
    effective_anchor_atom_ids = (
        normalized_anchor_atom_ids
        if normalized_anchor_mode == "atom_ids"
        else []
    )
    start_frame = _strict_integer(
        formation_start_frame, "formation_start_frame", minimum=0
    )
    min_followup = _strict_integer(
        minimum_followup_frames, "minimum_followup_frames", minimum=0
    )
    max_events = _strict_integer(
        max_events_per_episode,
        "max_events_per_episode",
        minimum=1,
        maximum=10_000_000,
    )
    max_branches = _strict_integer(
        max_active_branches,
        "max_active_branches",
        minimum=1,
        maximum=1_000_000,
    )
    detail_limit = _strict_integer(
        detail_retention_limit,
        "detail_retention_limit",
        minimum=0,
        maximum=1_000_000,
    )
    normalized_global_limit = (
        None
        if global_episode_limit is None
        else _strict_integer(
            global_episode_limit,
            "global_episode_limit",
            minimum=1,
            maximum=10_000_000,
        )
    )
    normalized_elements = _normalize_atom_elements(atom_elements)
    effective_atom_elements = (
        normalized_elements
        if normalized_anchor_mode in {"heavy_atoms", "elements"}
        else {}
    )

    reader = _ContinuityReader(reactionevent_file, molecules_file)
    try:
        available_intervals = int(reader.opened.get("available_intervals") or 0)
        default_end = max(0, available_intervals)
        end_frame = _strict_integer(
            default_end if formation_end_frame is None else formation_end_frame,
            "formation_end_frame",
            minimum=start_frame,
        )
        followup_frame = _strict_integer(
            default_end if followup_end_frame is None else followup_end_frame,
            "followup_end_frame",
            minimum=end_frame,
        )
        catalog = reader.species_catalog()
        target = str(target_species).strip()
        target_species_id = catalog.get(target)
        if target_species_id is None:
            raise SpeciesFateQueryError(
                "target Species is not present in the event continuity catalog"
            )
        missing_endpoints = sorted(
            species
            for values in normalized_endpoints.values()
            for species in values
            if species not in catalog
        )
        if missing_endpoints:
            raise SpeciesFateQueryError(
                "endpoint Species are not present in the event continuity catalog: "
                + ", ".join(missing_endpoints)
            )
        candidates = reader.formation_candidates(
            target_species_id,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        query = {
            "replicate_id": str(reader.opened["dataset_id"]),
            "target_species_id": target_species_id,
            "target_species": target,
            "anchor_mode": normalized_anchor_mode,
            "anchor_elements": effective_anchor_elements,
            "anchor_atom_ids": effective_anchor_atom_ids,
            "atom_elements": {
                str(atom_id): element
                for atom_id, element in effective_atom_elements.items()
            },
            "endpoint_categories": normalized_endpoints,
            "endpoint_category_species_ids": {
                category: [catalog[species] for species in species_values]
                for category, species_values in normalized_endpoints.items()
            },
            "formation_start_frame": start_frame,
            "formation_end_frame": end_frame,
            "followup_end_frame": followup_frame,
            "minimum_followup_frames": min_followup,
            "max_events_per_episode": max_events,
            "max_active_branches": max_branches,
            "detail_retention_limit": detail_limit,
            "global_episode_limit": normalized_global_limit,
        }
        query_id = _stable_id("rngfatequery", query, length=24)
        episode_summaries: list[dict[str, Any]] = []
        retained_episodes: list[dict[str, Any]] = []
        active_episodes = _ActiveEpisodeIndex()
        suppressed_returns: list[dict[str, Any]] = []
        failure: dict[str, Any] | None = None
        global_incompleteness: dict[str, Any] | None = None
        for candidate in candidates:
            anchors = _resolve_anchors(
                candidate["atom_ids"],
                anchor_mode=normalized_anchor_mode,
                atom_elements=effective_atom_elements,
                anchor_elements=effective_anchor_elements,
                anchor_atom_ids=effective_anchor_atom_ids,
            )
            frame = int(candidate["analyzed_frame"])
            active_episodes.expire_before(frame)
            active_exact = active_episodes.exact(anchors)
            if len(active_exact) > 1:
                failure = {
                    "reason": "internal_invariant_violation",
                    "message": (
                        "one formation candidate matches multiple ACTIVE episodes"
                    ),
                    "event_id": str(candidate["event_id"]),
                    "instance_id": str(candidate["instance_id"]),
                    "matching_formation_episode_ids": sorted(
                        row.episode_id
                        for row in active_exact
                    ),
                }
                break
            if active_exact:
                record = {
                    "event_id": str(candidate["event_id"]),
                    "instance_id": str(candidate["instance_id"]),
                    "analyzed_frame": frame,
                    "anchor_atom_ids": list(anchors),
                    "active_episode_id": active_exact[0].episode_id,
                }
                if active_exact[0].detail is not None:
                    active_exact[0].detail["return_candidates"].append(record)
                suppressed_returns.append(record)
                continue
            overlapping = active_episodes.overlapping(anchors)
            if (
                normalized_global_limit is not None
                and len(episode_summaries) >= normalized_global_limit
            ):
                global_incompleteness = {
                    "reason": "global_episode_limit",
                    "message": (
                        "Fate traversal stopped before the complete formation "
                        "cohort was processed"
                    ),
                    "limit": normalized_global_limit,
                    "next_candidate_event_id": str(candidate["event_id"]),
                }
                break
            episode = _traverse_episode(
                reader,
                candidate,
                anchors,
                endpoint_by_species=endpoint_by_species,
                target_species_id=target_species_id,
                followup_end_frame=followup_frame,
                max_events=max_events,
                max_active_branches=max_branches,
            )
            reader.clear_traversal_cache()
            episode["insufficient_followup"] = (
                followup_frame - frame < min_followup
            )
            episode["overlapping_active_episode_ids"] = [
                row.episode_id for row in overlapping
            ]
            episode["overlapping_anchor_ids"] = {
                row.episode_id: sorted(set(row.anchors).intersection(anchors))
                for row in overlapping
            }
            detail = episode if len(retained_episodes) < detail_limit else None
            if detail is not None:
                retained_episodes.append(detail)
            episode_summaries.append(_episode_for_aggregation(episode))
            active_episodes.add(episode, detail=detail)

        if failure is not None:
            return _build_result_document(
                reader,
                query=query,
                query_id=query_id,
                status="failed",
                summary=None,
                episode_summaries=(),
                retained_episodes=(),
                detail_limit=detail_limit,
                suppressed_returns=(),
                failure=failure,
            )
        if global_incompleteness is not None:
            return _build_result_document(
                reader,
                query=query,
                query_id=query_id,
                status="incomplete",
                summary=None,
                episode_summaries=episode_summaries,
                retained_episodes=retained_episodes,
                detail_limit=detail_limit,
                suppressed_returns=suppressed_returns,
                global_incompleteness=global_incompleteness,
            )

        summary = _aggregate(
            episode_summaries,
            endpoint_categories=normalized_endpoints,
            formation_start_frame=start_frame,
            formation_end_frame=end_frame,
            followup_end_frame=followup_frame,
        )
        return _build_result_document(
            reader,
            query=query,
            query_id=query_id,
            status="complete",
            summary=summary,
            episode_summaries=episode_summaries,
            retained_episodes=retained_episodes,
            detail_limit=detail_limit,
            suppressed_returns=suppressed_returns,
        )
    except (IndexInvalidError, sqlite3.Error) as exc:
        raise SpeciesFateError(str(exc)) from exc
    finally:
        reader.close()


def species_fate_catalog(
    reactionevent_file: str,
    molecules_file: str,
) -> list[dict[str, str]]:
    """Return stable exact-Species choices from prepared continuity evidence."""

    reader = _ContinuityReader(reactionevent_file, molecules_file)
    try:
        return [
            {"species_id": species_id, "species": species}
            for species, species_id in sorted(reader.species_catalog().items())
        ]
    finally:
        reader.close()


def species_fate_to_json(result: Mapping[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _csv_bytes(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(fields), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field: (
                    _canonical_json(row.get(field))
                    if isinstance(row.get(field), (dict, list, tuple))
                    else row.get(field)
                )
                for field in fields
            }
        )
    return output.getvalue().encode("utf-8")


def species_fate_tables_zip(result: Mapping[str, Any]) -> bytes:
    """Return a deterministic relational Fate Result export."""

    summary = dict(result.get("summary") or {})
    episodes = list(result.get("episodes") or [])
    terminal_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    censor_rows: list[dict[str, Any]] = []
    molecule_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    for episode in episodes:
        episode_id = str(episode.get("formation_episode_id") or "")
        for terminal in episode.get("terminal_instances") or []:
            terminal_rows.append({"formation_episode_id": episode_id, **terminal})
        for position, event in enumerate(episode.get("events") or []):
            event_rows.append(
                {
                    "formation_episode_id": episode_id,
                    "event_position": position,
                    **event,
                }
            )
        for censoring in episode.get("censored_anchor_subsets") or []:
            censor_rows.append(
                {"formation_episode_id": episode_id, **censoring}
            )
        for molecule in episode.get("molecule_instances") or []:
            molecule_rows.append(
                {"formation_episode_id": episode_id, **molecule}
            )
        for edge in episode.get("continuity_edges") or []:
            edge_rows.append({"formation_episode_id": episode_id, **edge})
    files = {
        "manifest.json": (
            json.dumps(
                {
                    key: result.get(key)
                    for key in (
                        "schema_version",
                        "algorithm_version",
                        "status",
                        "fate_query_id",
                        "fate_result_id",
                        "replicate_id",
                        "query",
                        "episode_count",
                        "processed_episode_count",
                        "episode_count_complete",
                        "detail_retention",
                        "failure",
                        "global_incompleteness",
                        "source_signatures",
                        "versions",
                    )
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
        "summary.csv": _csv_bytes([summary], sorted(summary)),
        "fate_signatures.csv": _csv_bytes(
            list(summary.get("fate_signatures") or []),
            (
                "fate_signature_id",
                "label",
                "members",
                "episode_count",
                "resolved_episode_conditional_probability",
                "formation_episode_ids",
            ),
        ),
        "endpoint_marginals.csv": _csv_bytes(
            list(summary.get("endpoint_marginals") or []),
            (
                "endpoint_category",
                "episode_occurrence_count",
                "terminal_instance_count",
                "marginal_occurrence_probability",
                "unconditional_mean_multiplicity",
                "conditional_mean_multiplicity",
            ),
        ),
        "pathways.csv": _csv_bytes(
            [
                *list(summary.get("first_exit_channels") or []),
                *list(summary.get("linear_paths") or []),
            ],
            (
                "reaction_type",
                "linear_reaction_type_sequence",
                "episode_count",
                "first_exit_channel_conditional_probability",
                "linear_path_conditional_probability",
            ),
        ),
        "episodes.csv": _csv_bytes(
            episodes,
            (
                "formation_episode_id",
                "replicate_id",
                "status",
                "fully_resolved",
                "anchor_atom_ids",
                "fate_signature",
                "censoring_reasons",
                "topology",
                "event_ids",
                "close_frame",
                "insufficient_followup",
                "overlapping_active_episode_ids",
            ),
        ),
        "terminal_instances.csv": _csv_bytes(
            terminal_rows,
            (
                "formation_episode_id",
                "terminal_instance_id",
                "endpoint_category",
                "species_id",
                "species",
                "anchor_atom_ids",
                "first_passage_event_id",
                "timestep_index",
                "analyzed_frame",
                "source_timestep",
                "terminal_first_passage_time",
            ),
        ),
        "episode_events.csv": _csv_bytes(
            event_rows,
            (
                "formation_episode_id",
                "event_position",
                "event_id",
                "reaction_key",
                "timestep_index",
                "before_timestep",
                "after_timestep",
                "reaction_smiles",
                "association_status",
            ),
        ),
        "molecule_instances.csv": _csv_bytes(
            molecule_rows,
            (
                "formation_episode_id",
                "instance_id",
                "replicate_id",
                "analyzed_frame",
                "source_timestep",
                "species_id",
                "species",
                "atom_ids",
                "bonds",
                "structure_key",
                "anchor_atom_ids",
            ),
        ),
        "continuity_edges.csv": _csv_bytes(
            edge_rows,
            (
                "formation_episode_id",
                "edge_id",
                "from_instance_id",
                "event_id",
                "to_instance_id",
                "anchor_atom_ids",
            ),
        ),
        "censoring.csv": _csv_bytes(
            censor_rows,
            (
                "formation_episode_id",
                "instance_id",
                "anchor_atom_ids",
                "reason",
                "analyzed_frame",
                "event_ids",
                "detail",
            ),
        ),
        "time_statistics.csv": _csv_bytes(
            [
                {"metric": metric, **dict(values)}
                for metric, values in sorted(
                    dict(summary.get("time_distributions") or {}).items()
                )
            ],
            ("metric", "count", "min", "median", "mean", "max"),
        ),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    return output.getvalue()


__all__ = [
    "SPECIES_FATE_ALGORITHM_VERSION",
    "SPECIES_FATE_SCHEMA_VERSION",
    "SpeciesFateError",
    "SpeciesFateQueryError",
    "analyze_species_fate",
    "species_fate_catalog",
    "species_fate_tables_zip",
    "species_fate_to_json",
]
