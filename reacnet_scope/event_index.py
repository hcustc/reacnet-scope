"""Persistent evidence index for ReacNetGenerator-authored event outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping

import numpy as np

from .indexes import (
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    _exclusive_build_lock,
    _assert_source_unchanged,
    _read_meta,
    _readonly_connection,
    _source_signature,
    dataset_id_for_source,
    event_evidence_index_path,
    resolve_dataset_paths,
)
from .rng_events import (
    MoleculeComponent,
    MoleculeRow,
    RngEventDataError,
    _trajectory_bond_id,
    canonical_reaction_key,
    changed_components,
    net_reaction_key,
    reaction_key,
)
from .path_search import materialize_candidate_adjacency
from .timed_evidence import (
    NativeHdf5EvidenceAdapter,
    TimedEvidenceDataError,
    select_timed_evidence,
)


EVENT_EVIDENCE_SCHEMA_VERSION = 5
EVENT_ASSOCIATION_ALGORITHM_VERSION = 3
EVENT_CONTINUITY_SCHEMA_VERSION = 1
EVENT_CONTINUITY_ALGORITHM_VERSION = 2
_MEMBERSHIP_MATERIALIZATION_RESERVE_BYTES = 4 * 1024**3
_EVENT_BUILD_CHECKPOINT_INTERVALS = 100


def _available_memory_bytes() -> int:
    """Best-effort physical memory availability without a new dependency."""

    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES")) * int(
            os.sysconf("SC_PAGE_SIZE")
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return 0


def _can_materialize_membership(membership_bytes: int) -> bool:
    required = (
        max(0, int(membership_bytes)) * 2
        + _MEMBERSHIP_MATERIALIZATION_RESERVE_BYTES
    )
    return _available_memory_bytes() >= required


class EventNotFoundError(LookupError):
    """Raised when a ready event index does not contain one event ID."""


_REQUIRED_TABLE_COLUMNS = {
    "meta": {"key", "value"},
    "events": {
        "event_id",
        "reaction_key",
        "source_row",
        "timestep_index",
        "before_timestep",
        "after_timestep",
        "reactant_text",
        "product_text",
        "atom_ids_json",
        "reactant_bonds_json",
        "product_bonds_json",
        "reactant_participants_json",
        "product_participants_json",
        "association_status",
        "occurrence",
    },
    "event_atoms": {"event_id", "atom_id"},
    "event_species": {
        "event_id",
        "side",
        "species_smiles",
        "timestep_index",
        "occurrence",
    },
    "reaction_summary": {
        "reaction_key",
        "total_events",
        "matched_events",
        "distinct_intervals",
    },
}

_CONTINUITY_REQUIRED_TABLE_COLUMNS = {
    "continuity_species": {"species_id", "species_smiles"},
    "molecule_instances": {
        "instance_id",
        "replicate_id",
        "analyzed_frame",
        "source_timestep",
        "species_id",
        "species_smiles",
        "atom_ids_json",
        "bonds_json",
        "structure_key",
    },
    "event_participants": {
        "event_id",
        "side",
        "participant_index",
        "instance_id",
        "timestep_index",
        "species_id",
        "species_smiles",
        "structure_key",
    },
    "continuity_links": {
        "from_instance_id",
        "status",
        "next_timestep_index",
        "next_event_ids_json",
        "to_reactant_instance_id",
        "reason",
    },
    "continuity_diagnostics": {
        "diagnostic_id",
        "timestep_index",
        "species_id",
        "structure_key",
        "reason",
        "event_ids_json",
    },
}

_EVENT_SELECT_COLUMNS = """
    event_id,reaction_key,source_row,timestep_index,
    before_timestep,after_timestep,reactant_text,
    product_text,atom_ids_json,reactant_bonds_json,
    product_bonds_json,reactant_participants_json,
    product_participants_json,association_status,occurrence
"""

_EVENT_SELECT_COLUMNS_E = """
    e.event_id,e.reaction_key,e.source_row,e.timestep_index,
    e.before_timestep,e.after_timestep,e.reactant_text,
    e.product_text,e.atom_ids_json,e.reactant_bonds_json,
    e.product_bonds_json,e.reactant_participants_json,
    e.product_participants_json,e.association_status,e.occurrence
"""


def _strict_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
) -> int:
    try:
        if type(value) is int:
            parsed = value
        elif isinstance(value, str):
            digits = value[1:] if value.startswith("-") else value
            if (
                not digits
                or not digits.isascii()
                or not digits.isdecimal()
            ):
                raise TypeError
            parsed = int(value, 10)
        elif isinstance(value, float) and value.is_integer():
            parsed = int(value)
        else:
            raise TypeError
    except (TypeError, ValueError) as exc:
        raise IndexInvalidError(
            f"Event evidence index {label} is invalid"
        ) from exc
    if minimum is not None and parsed < minimum:
        raise IndexInvalidError(
            f"Event evidence index {label} is invalid"
        )
    return parsed


def _safe_meta_int(meta: dict[str, str], key: str) -> int:
    try:
        return _strict_int(meta.get(key), key, minimum=0)
    except IndexInvalidError:
        return 0


def _decode_json_list(raw: Any, label: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise IndexInvalidError(
            f"Event evidence index {label} payload is invalid"
        ) from exc
    if not isinstance(value, list):
        raise IndexInvalidError(
            f"Event evidence index {label} payload must be a list"
        )
    return value


def _participant_payload(
    molecules: Iterable[MoleculeRow],
) -> list[dict[str, Any]]:
    return [
        {
            "species": str(molecule.species),
            "atom_ids": sorted(atom_id + 1 for atom_id in molecule.atom_ids),
        }
        for molecule in molecules
    ]


def _decode_participants(raw: Any, label: str) -> list[dict[str, Any]]:
    values = _decode_json_list(raw, label)
    participants: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise IndexInvalidError(
                f"Event evidence index {label} participants are invalid"
            )
        species = value.get("species")
        atom_ids = value.get("atom_ids")
        if not isinstance(species, str) or not isinstance(atom_ids, list):
            raise IndexInvalidError(
                f"Event evidence index {label} participants are invalid"
            )
        if any(
            not isinstance(atom_id, int) or isinstance(atom_id, bool)
            for atom_id in atom_ids
        ):
            raise IndexInvalidError(
                f"Event evidence index {label} atom ids are invalid"
            )
        participants.append(
            {
                "species": species,
                "atom_ids": sorted(set(atom_ids)),
            }
        )
    return participants


def _event_payload_from_record(
    record: Iterable[Any],
    *,
    event_index: int,
) -> dict[str, Any]:
    (
        event_id,
        stored_key,
        source_row,
        timestep_index,
        before_timestep,
        after_timestep,
        reactant,
        product,
        atom_ids_json,
        reactant_bonds_json,
        product_bonds_json,
        reactant_participants_json,
        product_participants_json,
        association_status,
        occurrence,
    ) = record
    atom_values = _decode_json_list(atom_ids_json, "atom_ids_json")
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in atom_values
    ):
        raise IndexInvalidError(
            "Event evidence index atom_ids_json payload must contain integers"
        )
    atom_ids = sorted(set(atom_values))
    reactant_values = _decode_json_list(
        reactant_bonds_json, "reactant_bonds_json"
    )
    product_values = _decode_json_list(
        product_bonds_json, "product_bonds_json"
    )
    if any(
        not isinstance(value, str)
        for value in reactant_values + product_values
    ):
        raise IndexInvalidError(
            "Event evidence index bond payloads must contain strings"
        )
    if association_status not in {
        "matched",
        "unresolved_hmm_timeline",
        "reactionevent_only",
    }:
        raise IndexInvalidError(
            "Event evidence index association_status is invalid"
        )
    reactant_participants = _decode_participants(
        reactant_participants_json,
        "reactant_participants_json",
    )
    product_participants = _decode_participants(
        product_participants_json,
        "product_participants_json",
    )
    return {
        "event_index": event_index,
        "event_id": str(event_id),
        "reaction_key": str(stored_key),
        "source_row": int(source_row),
        "timestep_index": int(timestep_index),
        "before_timestep": int(before_timestep),
        "after_timestep": int(after_timestep),
        "anchor_frame": int(after_timestep),
        "reactant": str(reactant),
        "product": str(product),
        "reaction_smiles": f"{reactant} -> {product}",
        "occurrence": int(occurrence),
        "atom_ids": ",".join(map(str, atom_ids)),
        "atom_id_list": atom_ids,
        "rng_atom_ids": ",".join(
            str(atom_id - 1) for atom_id in atom_ids
        ),
        "atom_count": len(atom_ids),
        "reactant_bonds": ";".join(reactant_values),
        "product_bonds": ";".join(product_values),
        "reactant_participants": reactant_participants,
        "product_participants": product_participants,
        "association_status": str(association_status),
        "event_class": (
            "RNG 事件"
            if association_status == "matched"
            else (
                "RNG 事件区间"
                if association_status == "reactionevent_only"
                else "RNG 事件（原子关联不确定）"
            )
        ),
    }


def _write_meta(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    connection.executemany(
        """
        INSERT INTO meta(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        [(key, str(value)) for key, value in values.items()],
    )


def _event_id(
    timestep_index: int,
    reaction_key_text: str,
    atom_ids: list[int],
    *,
    unresolved_ordinal: int = 0,
) -> str:
    """Derive an ID from occurrence meaning, never source storage position."""

    normalized_atoms = sorted(set(int(value) for value in atom_ids))
    unresolved = int(unresolved_ordinal) if not normalized_atoms else 0
    digest = hashlib.sha1(
        (
            f"{timestep_index}|{reaction_key_text}|"
            f"{','.join(map(str, normalized_atoms))}|{unresolved}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"rngevt_{timestep_index}_{digest}"


def _event_species_rows(
    event_id: str,
    timestep_index: int,
    reaction_terms: tuple[tuple[str, ...], tuple[str, ...]],
) -> list[tuple[str, str, str, int, int]]:
    rows: list[tuple[str, str, str, int, int]] = []
    for side, terms in zip(
        ("reactant", "product"),
        reaction_terms,
        strict=True,
    ):
        occurrences: dict[str, int] = defaultdict(int)
        for species in terms:
            occurrences[species] += 1
            rows.append(
                (
                    event_id,
                    side,
                    species,
                    int(timestep_index),
                    occurrences[species],
                )
            )
    return rows


def _continuity_species_id(species: str) -> str:
    digest = hashlib.sha1(str(species).encode("utf-8")).hexdigest()[:20]
    return f"rngsp_{digest}"


def _continuity_bonds(
    raw_bonds: Any,
    atom_ids: Iterable[int],
) -> tuple[str, ...]:
    selected = {int(value) for value in atom_ids}
    values = (
        raw_bonds
        if isinstance(raw_bonds, (list, tuple))
        else str(raw_bonds or "").split(";")
    )
    bonds: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        parts = text.split("-", 2)
        if len(parts) != 3:
            raise IndexInvalidError(
                f"Event evidence continuity bond is invalid: {text!r}"
            )
        try:
            first, second = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise IndexInvalidError(
                f"Event evidence continuity bond is invalid: {text!r}"
            ) from exc
        if first not in selected or second not in selected:
            continue
        left, right = sorted((first, second))
        bonds.add(f"{left}-{right}-{parts[2]}")
    return tuple(sorted(bonds))


def _continuity_structure_key(
    species_id: str,
    atom_ids: Iterable[int],
    bonds: Iterable[str],
) -> str:
    payload = json.dumps(
        [
            str(species_id),
            sorted({int(value) for value in atom_ids}),
            sorted({str(value) for value in bonds}),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _continuity_instance_id(
    replicate_id: str,
    analyzed_frame: int,
    structure_key: str,
) -> str:
    digest = hashlib.sha1(
        f"{replicate_id}\0{int(analyzed_frame)}\0{structure_key}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return f"rngmol_{digest}"


def _continuity_tables_available(
    connection: sqlite3.Connection,
    meta: Mapping[str, str],
) -> bool:
    try:
        if _strict_int(
            meta.get("continuity_schema_version", 0),
            "continuity_schema_version",
            minimum=0,
        ) != EVENT_CONTINUITY_SCHEMA_VERSION:
            return False
        if _strict_int(
            meta.get("continuity_algorithm_version", 0),
            "continuity_algorithm_version",
            minimum=0,
        ) != EVENT_CONTINUITY_ALGORITHM_VERSION:
            return False
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not _CONTINUITY_REQUIRED_TABLE_COLUMNS.keys() <= tables:
            return False
        for table, required in _CONTINUITY_REQUIRED_TABLE_COLUMNS.items():
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if not required <= columns:
                return False
    except (IndexInvalidError, sqlite3.Error):
        return False
    return True


def _read_csv_header(
    source: BinaryIO,
    *,
    required: set[str],
    label: str,
) -> tuple[tuple[str, ...], int]:
    source.seek(0)
    raw = source.readline()
    if not raw:
        raise RngEventDataError(f"{label} CSV is empty")
    try:
        rows = list(csv.reader([raw.decode("utf-8")], strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RngEventDataError(f"{label} CSV header is invalid") from exc
    fields = tuple(str(value) for value in rows[0]) if rows else ()
    if not required.issubset(fields):
        raise RngEventDataError(f"{label} CSV columns are incompatible")
    return fields, source.tell()


def _parse_csv_record(
    raw: bytes,
    fields: tuple[str, ...],
    *,
    label: str,
) -> dict[str, str]:
    try:
        rows = list(csv.reader([raw.decode("utf-8")], strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RngEventDataError(
            f"{label} CSV must contain one complete record per line"
        ) from exc
    if len(rows) != 1 or len(rows[0]) != len(fields):
        raise RngEventDataError(f"{label} CSV record is incompatible")
    return dict(zip(fields, (str(value) for value in rows[0]), strict=True))


def _read_event_group(
    source: BinaryIO,
    fields: tuple[str, ...],
    *,
    last_interval: int,
    last_source_row: int,
) -> tuple[list[dict[str, Any]], int, int] | None:
    group: list[dict[str, Any]] = []
    interval: int | None = None
    source_row = last_source_row
    while True:
        record_start = source.tell()
        raw = source.readline()
        if not raw:
            break
        row = _parse_csv_record(raw, fields, label="reactionevent")
        try:
            current_interval = int(row["Timestep_Index"])
        except (KeyError, ValueError) as exc:
            raise RngEventDataError(
                "reactionevent CSV contains an invalid Timestep_Index"
            ) from exc
        if interval is None:
            if current_interval <= last_interval:
                raise RngEventDataError(
                    "reactionevent CSV must be sorted by Timestep_Index"
                )
            interval = current_interval
        elif current_interval != interval:
            if current_interval < interval:
                raise RngEventDataError(
                    "reactionevent CSV must be sorted by Timestep_Index"
                )
            source.seek(record_start)
            break
        source_row += 1
        reactant = str(row.get("Reactant", "")).strip()
        product = str(row.get("Product", "")).strip()
        normalized = reaction_key(reactant, product)
        group.append(
            {
                "source_row": source_row,
                "timestep_index": current_interval,
                "reactant": reactant,
                "product": product,
                "reaction_key": normalized,
                "reaction_key_text": canonical_reaction_key(*normalized),
            }
        )
    if interval is None:
        return None
    return group, source.tell(), source_row


def _read_molecule_group(
    source: BinaryIO,
    fields: tuple[str, ...],
    *,
    frame_index: int,
    previous_timestep: int | None,
) -> tuple[int, int, tuple[MoleculeRow, ...], int] | None:
    rows: list[MoleculeRow] = []
    timestep: int | None = None
    frame_start = source.tell()
    while True:
        record_start = source.tell()
        raw = source.readline()
        if not raw:
            break
        row = _parse_csv_record(raw, fields, label="molecules")
        try:
            current_timestep = int(row["Timestep"])
        except (KeyError, ValueError) as exc:
            raise RngEventDataError(
                "molecules CSV contains an invalid Timestep"
            ) from exc
        if timestep is None:
            if (
                previous_timestep is not None
                and current_timestep <= previous_timestep
            ):
                raise RngEventDataError(
                    "molecules CSV must be sorted by increasing Timestep"
                )
            timestep = current_timestep
        elif current_timestep != timestep:
            if current_timestep < timestep:
                raise RngEventDataError(
                    "molecules CSV must be sorted by increasing Timestep"
                )
            source.seek(record_start)
            break
        try:
            atom_ids = frozenset(
                int(value)
                for value in str(row.get("AtomIDs", "")).split(";")
                if value
            )
        except ValueError as exc:
            raise RngEventDataError(
                "molecules CSV contains an invalid AtomIDs value"
            ) from exc
        bonds = tuple(
            value
            for value in str(row.get("BondIDs", "")).split(";")
            if value
        )
        rows.append(
            MoleculeRow(
                str(row.get("Species", "")),
                atom_ids,
                bonds,
            )
        )
    if timestep is None:
        return None
    return frame_index, timestep, tuple(rows), frame_start


class EventEvidenceStore:
    """Offline builder and strict read-only reader for RNG event evidence."""

    @staticmethod
    def _expected_path(reactionevent_file: str) -> Path:
        return resolve_dataset_paths(
            os.path.abspath(reactionevent_file),
            persist_identity=False,
        ).event_index

    @staticmethod
    def _source_pair(
        reactionevent_file: str,
        molecules_file: str = "",
    ) -> tuple[tuple[str, int, int], tuple[str, int, int]]:
        reaction_source = _source_signature(reactionevent_file)
        molecule_path = str(molecules_file or "").strip()
        molecule_source = (
            _source_signature(molecule_path)
            if molecule_path and Path(molecule_path).is_file()
            else ("", 0, 0)
        )
        return reaction_source, molecule_source

    @staticmethod
    def _connect_for_build(target: Path) -> sqlite3.Connection:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(target))
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events(
                event_id TEXT PRIMARY KEY,
                reaction_key TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                timestep_index INTEGER NOT NULL,
                before_timestep INTEGER NOT NULL,
                after_timestep INTEGER NOT NULL,
                reactant_text TEXT NOT NULL,
                product_text TEXT NOT NULL,
                atom_ids_json TEXT NOT NULL,
                reactant_bonds_json TEXT NOT NULL,
                product_bonds_json TEXT NOT NULL,
                reactant_participants_json TEXT NOT NULL,
                product_participants_json TEXT NOT NULL,
                association_status TEXT NOT NULL,
                occurrence INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS events_by_reaction
            ON events(reaction_key,timestep_index,source_row,event_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS events_by_reaction_time
            ON events(reaction_key,after_timestep,event_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_atoms(
                event_id TEXT NOT NULL,
                atom_id INTEGER NOT NULL,
                PRIMARY KEY(event_id,atom_id),
                FOREIGN KEY(event_id) REFERENCES events(event_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS event_atoms_by_atom
            ON event_atoms(atom_id,event_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_species(
                event_id TEXT NOT NULL,
                side TEXT NOT NULL,
                species_smiles TEXT NOT NULL,
                timestep_index INTEGER NOT NULL,
                occurrence INTEGER NOT NULL,
                PRIMARY KEY(event_id,side,species_smiles,occurrence),
                FOREIGN KEY(event_id) REFERENCES events(event_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS event_species_lookup
            ON event_species(
                side,species_smiles,timestep_index,event_id
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reaction_summary(
                reaction_key TEXT PRIMARY KEY,
                total_events INTEGER NOT NULL,
                matched_events INTEGER NOT NULL,
                distinct_intervals INTEGER NOT NULL
            )
            """
        )
        return connection

    @staticmethod
    def _materialize_continuity(
        connection: sqlite3.Connection,
        *,
        replicate_id: str,
    ) -> dict[str, int]:
        """Build query-independent molecule continuity from indexed events."""

        for table in (
            "continuity_diagnostics",
            "continuity_links",
            "event_participants",
            "molecule_instances",
            "continuity_species",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute(
            """
            CREATE TABLE continuity_species(
                species_id TEXT PRIMARY KEY,
                species_smiles TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE molecule_instances(
                instance_id TEXT PRIMARY KEY,
                replicate_id TEXT NOT NULL,
                analyzed_frame INTEGER NOT NULL,
                source_timestep INTEGER NOT NULL,
                species_id TEXT NOT NULL,
                species_smiles TEXT NOT NULL,
                atom_ids_json TEXT NOT NULL,
                bonds_json TEXT NOT NULL,
                structure_key TEXT NOT NULL,
                FOREIGN KEY(species_id) REFERENCES continuity_species(species_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX molecule_instances_by_structure
            ON molecule_instances(species_id,structure_key,analyzed_frame)
            """
        )
        connection.execute(
            """
            CREATE TABLE event_participants(
                event_id TEXT NOT NULL,
                side TEXT NOT NULL,
                participant_index INTEGER NOT NULL,
                instance_id TEXT NOT NULL,
                timestep_index INTEGER NOT NULL,
                species_id TEXT NOT NULL,
                species_smiles TEXT NOT NULL,
                structure_key TEXT NOT NULL,
                PRIMARY KEY(event_id,side,participant_index),
                FOREIGN KEY(event_id) REFERENCES events(event_id),
                FOREIGN KEY(instance_id) REFERENCES molecule_instances(instance_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX event_participants_lookup
            ON event_participants(
                side,species_id,timestep_index,event_id,participant_index
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX event_participants_by_instance
            ON event_participants(instance_id,event_id,side)
            """
        )
        connection.execute(
            """
            CREATE TABLE continuity_links(
                from_instance_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                next_timestep_index INTEGER NOT NULL,
                next_event_ids_json TEXT NOT NULL,
                to_reactant_instance_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                FOREIGN KEY(from_instance_id)
                    REFERENCES molecule_instances(instance_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE continuity_diagnostics(
                diagnostic_id TEXT PRIMARY KEY,
                timestep_index INTEGER NOT NULL,
                species_id TEXT NOT NULL,
                structure_key TEXT NOT NULL,
                reason TEXT NOT NULL,
                event_ids_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX continuity_diagnostics_formation_filter
            ON continuity_diagnostics(
                species_id,timestep_index,reason
            )
            """
        )
        connection.execute("DROP TABLE IF EXISTS temp.continuity_active")
        connection.execute(
            """
            CREATE TEMP TABLE continuity_active(
                species_id TEXT NOT NULL,
                atom_key TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                structure_key TEXT NOT NULL,
                PRIMARY KEY(species_id,atom_key)
            ) WITHOUT ROWID
            """
        )

        instance_count = 0
        participant_count = 0
        link_count = 0
        diagnostic_count = 0
        known_species_ids: set[str] = set()
        known_instance_ids: set[str] = set()

        def add_diagnostic(
            timestep_index: int,
            species_id: str,
            structure_key: str,
            reason: str,
            event_ids: Iterable[str],
        ) -> None:
            nonlocal diagnostic_count
            normalized_events = sorted({str(value) for value in event_ids})
            identity = json.dumps(
                [
                    int(timestep_index),
                    str(species_id),
                    str(structure_key),
                    str(reason),
                    normalized_events,
                ],
                separators=(",", ":"),
            )
            diagnostic_id = (
                "rngcontdiag_"
                + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20]
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO continuity_diagnostics(
                    diagnostic_id,timestep_index,species_id,structure_key,
                    reason,event_ids_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    diagnostic_id,
                    int(timestep_index),
                    str(species_id),
                    str(structure_key),
                    str(reason),
                    json.dumps(normalized_events, separators=(",", ":")),
                ),
            )
            diagnostic_count += 1

        def participant_rows(
            event: Mapping[str, Any],
            side: str,
            *,
            species_rows: dict[str, tuple[str, str]],
            instance_rows: dict[str, tuple[Any, ...]],
            participant_inserts: list[tuple[Any, ...]],
        ) -> list[dict[str, Any]]:
            nonlocal participant_count
            output: list[dict[str, Any]] = []
            analyzed_frame = int(event["timestep_index"]) + (
                1 if side == "product" else 0
            )
            source_timestep = int(
                event["after_timestep"]
                if side == "product"
                else event["before_timestep"]
            )
            for index, participant in enumerate(
                event.get(f"{side}_participants") or []
            ):
                species = str(participant.get("species") or "").strip()
                atom_ids = tuple(
                    sorted(
                        {
                            int(value)
                            for value in participant.get("atom_ids") or []
                        }
                    )
                )
                if not species or not atom_ids:
                    continue
                species_id = _continuity_species_id(species)
                bonds = _continuity_bonds(
                    event.get(f"{side}_bonds"), atom_ids
                )
                structure_key = _continuity_structure_key(
                    species_id, atom_ids, bonds
                )
                instance_id = _continuity_instance_id(
                    replicate_id, analyzed_frame, structure_key
                )
                species_rows[species_id] = (species_id, species)
                instance_rows[instance_id] = (
                    instance_id,
                    replicate_id,
                    analyzed_frame,
                    source_timestep,
                    species_id,
                    species,
                    json.dumps(atom_ids, separators=(",", ":")),
                    json.dumps(bonds, separators=(",", ":")),
                    structure_key,
                )
                participant_inserts.append(
                    (
                        str(event["event_id"]),
                        side,
                        index,
                        instance_id,
                        int(event["timestep_index"]),
                        species_id,
                        species,
                        structure_key,
                    )
                )
                participant_count += 1
                output.append(
                    {
                        "event_id": str(event["event_id"]),
                        "participant_index": index,
                        "instance_id": instance_id,
                        "species_id": species_id,
                        "species_smiles": species,
                        "atom_ids": atom_ids,
                        "atom_key": json.dumps(
                            atom_ids, separators=(",", ":")
                        ),
                        "structure_key": structure_key,
                    }
                )
            return output

        def process_interval(events: list[dict[str, Any]]) -> None:
            nonlocal instance_count, link_count
            if not events:
                return
            timestep_index = int(events[0]["timestep_index"])
            participants_by_event: dict[
                str, dict[str, list[dict[str, Any]]]
            ] = {}
            species_rows: dict[str, tuple[str, str]] = {}
            instance_rows: dict[str, tuple[Any, ...]] = {}
            participant_inserts: list[tuple[Any, ...]] = []
            unresolved_species: set[str] = set()
            for event in events:
                participants_by_event[str(event["event_id"])] = {
                    "reactant": participant_rows(
                        event,
                        "reactant",
                        species_rows=species_rows,
                        instance_rows=instance_rows,
                        participant_inserts=participant_inserts,
                    ),
                    "product": participant_rows(
                        event,
                        "product",
                        species_rows=species_rows,
                        instance_rows=instance_rows,
                        participant_inserts=participant_inserts,
                    ),
                }
                if event.get("association_status") != "matched":
                    terms = reaction_key(
                        str(event.get("reactant") or ""),
                        str(event.get("product") or ""),
                    )
                    unresolved_species.update((*terms[0], *terms[1]))

            new_species_ids = species_rows.keys() - known_species_ids
            connection.executemany(
                """
                INSERT INTO continuity_species(species_id,species_smiles)
                VALUES(?,?)
                """,
                [species_rows[species_id] for species_id in new_species_ids],
            )
            known_species_ids.update(new_species_ids)
            new_instance_ids = instance_rows.keys() - known_instance_ids
            connection.executemany(
                """
                INSERT INTO molecule_instances(
                    instance_id,replicate_id,analyzed_frame,
                    source_timestep,species_id,species_smiles,
                    atom_ids_json,bonds_json,structure_key
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                [instance_rows[instance_id] for instance_id in new_instance_ids],
            )
            known_instance_ids.update(new_instance_ids)
            instance_count += len(new_instance_ids)
            connection.executemany(
                """
                INSERT INTO event_participants(
                    event_id,side,participant_index,instance_id,
                    timestep_index,species_id,species_smiles,structure_key
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                participant_inserts,
            )

            for species in sorted(unresolved_species):
                species_id = _continuity_species_id(species)
                active = connection.execute(
                    """
                    SELECT atom_key,instance_id,structure_key
                    FROM continuity_active WHERE species_id=?
                    """,
                    (species_id,),
                ).fetchall()
                unresolved_event_ids = [
                    str(event["event_id"])
                    for event in events
                    if event.get("association_status") != "matched"
                    and species
                    in {
                        *reaction_key(
                            str(event.get("reactant") or ""),
                            str(event.get("product") or ""),
                        )[0],
                        *reaction_key(
                            str(event.get("reactant") or ""),
                            str(event.get("product") or ""),
                        )[1],
                    }
                ]
                for atom_key, instance_id, structure_key in active:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO continuity_links(
                            from_instance_id,status,next_timestep_index,
                            next_event_ids_json,to_reactant_instance_id,reason
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            instance_id,
                            "unresolved_barrier",
                            timestep_index,
                            json.dumps(
                                sorted(unresolved_event_ids),
                                separators=(",", ":"),
                            ),
                            "",
                            "unresolved_species_barrier",
                        ),
                    )
                    link_count += 1
                    add_diagnostic(
                        timestep_index,
                        species_id,
                        str(structure_key),
                        "unresolved_species_barrier",
                        unresolved_event_ids,
                    )
                    connection.execute(
                        """
                        DELETE FROM continuity_active
                        WHERE species_id=? AND atom_key=?
                        """,
                        (species_id, atom_key),
                    )

            consumers: dict[
                tuple[str, str], list[dict[str, Any]]
            ] = defaultdict(list)
            producers: dict[
                tuple[str, str], list[dict[str, Any]]
            ] = defaultdict(list)
            for event in events:
                if event.get("association_status") != "matched":
                    continue
                rows = participants_by_event[str(event["event_id"])]
                for row in rows["reactant"]:
                    consumers[(row["species_id"], row["atom_key"])].append(row)
                for row in rows["product"]:
                    producers[(row["species_id"], row["atom_key"])].append(row)

            ambiguous_keys: dict[tuple[str, str], str] = {}
            for key, rows in producers.items():
                event_ids = {str(row["event_id"]) for row in rows}
                if len(rows) != 1 or len(event_ids) != 1:
                    ambiguous_keys[key] = "multiple_producers_same_transition"
            for key in consumers.keys() & producers.keys():
                consumer_rows = consumers[key]
                producer_rows = producers[key]
                if (
                    len(consumer_rows) != 1
                    or len(producer_rows) != 1
                    or str(consumer_rows[0]["event_id"])
                    != str(producer_rows[0]["event_id"])
                ):
                    ambiguous_keys[key] = (
                        "same_transition_event_dependency_ambiguous"
                    )

            for (species_id, atom_key), reason in sorted(
                ambiguous_keys.items()
            ):
                rows = [
                    *consumers.get((species_id, atom_key), []),
                    *producers.get((species_id, atom_key), []),
                ]
                event_ids = sorted({str(row["event_id"]) for row in rows})
                previous = connection.execute(
                    """
                    SELECT instance_id,structure_key FROM continuity_active
                    WHERE species_id=? AND atom_key=?
                    """,
                    (species_id, atom_key),
                ).fetchone()
                structure_key = str(
                    previous[1]
                    if previous is not None
                    else rows[0]["structure_key"]
                )
                if previous is not None:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO continuity_links(
                            from_instance_id,status,next_timestep_index,
                            next_event_ids_json,to_reactant_instance_id,reason
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            str(previous[0]),
                            "ambiguous",
                            timestep_index,
                            json.dumps(event_ids, separators=(",", ":")),
                            "",
                            reason,
                        ),
                    )
                    link_count += 1
                    connection.execute(
                        """
                        DELETE FROM continuity_active
                        WHERE species_id=? AND atom_key=?
                        """,
                        (species_id, atom_key),
                    )
                add_diagnostic(
                    timestep_index,
                    species_id,
                    structure_key,
                    reason,
                    event_ids,
                )

            for (species_id, atom_key), rows in sorted(consumers.items()):
                if (species_id, atom_key) in ambiguous_keys:
                    continue
                previous = connection.execute(
                    """
                    SELECT instance_id,structure_key FROM continuity_active
                    WHERE species_id=? AND atom_key=?
                    """,
                    (species_id, atom_key),
                ).fetchone()
                connection.execute(
                    """
                    DELETE FROM continuity_active
                    WHERE species_id=? AND atom_key=?
                    """,
                    (species_id, atom_key),
                )
                if previous is None:
                    continue
                event_ids = sorted({str(row["event_id"]) for row in rows})
                if len(rows) == 1 and len(event_ids) == 1:
                    status = "matched"
                    to_instance = str(rows[0]["instance_id"])
                    reason = ""
                else:
                    status = "ambiguous"
                    to_instance = ""
                    reason = "multiple_consumers_same_transition"
                    add_diagnostic(
                        timestep_index,
                        species_id,
                        str(previous[1]),
                        reason,
                        event_ids,
                    )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO continuity_links(
                        from_instance_id,status,next_timestep_index,
                        next_event_ids_json,to_reactant_instance_id,reason
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        str(previous[0]),
                        status,
                        timestep_index,
                        json.dumps(event_ids, separators=(",", ":")),
                        to_instance,
                        reason,
                    ),
                )
                link_count += 1

            for (species_id, atom_key), rows in sorted(producers.items()):
                if (species_id, atom_key) in ambiguous_keys:
                    continue
                event_ids = sorted({str(row["event_id"]) for row in rows})
                row = rows[0]
                previous = connection.execute(
                    """
                    SELECT instance_id,structure_key FROM continuity_active
                    WHERE species_id=? AND atom_key=?
                    """,
                    (species_id, atom_key),
                ).fetchone()
                if previous is not None:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO continuity_links(
                            from_instance_id,status,next_timestep_index,
                            next_event_ids_json,to_reactant_instance_id,reason
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            str(previous[0]),
                            "ambiguous",
                            timestep_index,
                            json.dumps(event_ids, separators=(",", ":")),
                            "",
                            "producer_replaced_without_unique_consumption",
                        ),
                    )
                    link_count += 1
                    add_diagnostic(
                        timestep_index,
                        species_id,
                        str(previous[1]),
                        "producer_replaced_without_unique_consumption",
                        event_ids,
                    )
                connection.execute(
                    """
                    INSERT INTO continuity_active(
                        species_id,atom_key,instance_id,structure_key
                    ) VALUES(?,?,?,?)
                    ON CONFLICT(species_id,atom_key) DO UPDATE SET
                        instance_id=excluded.instance_id,
                        structure_key=excluded.structure_key
                    """,
                    (
                        species_id,
                        atom_key,
                        str(row["instance_id"]),
                        str(row["structure_key"]),
                    ),
                )

        cursor = connection.execute(
            f"""
            SELECT {_EVENT_SELECT_COLUMNS}
            FROM events
            ORDER BY timestep_index,source_row,event_id
            """
        )
        interval_events: list[dict[str, Any]] = []
        interval_index: int | None = None
        event_index = 0
        for record in cursor:
            event_index += 1
            event = _event_payload_from_record(record, event_index=event_index)
            current = int(event["timestep_index"])
            if interval_index is not None and current != interval_index:
                process_interval(interval_events)
                interval_events = []
            interval_index = current
            interval_events.append(event)
        process_interval(interval_events)
        connection.execute("DROP TABLE temp.continuity_active")
        _write_meta(
            connection,
            {
                "continuity_schema_version": EVENT_CONTINUITY_SCHEMA_VERSION,
                "continuity_algorithm_version": (
                    EVENT_CONTINUITY_ALGORITHM_VERSION
                ),
                "continuity_instance_count": instance_count,
                "continuity_participant_count": participant_count,
                "continuity_link_count": link_count,
                "continuity_diagnostic_count": diagnostic_count,
            },
        )
        return {
            "instance_count": instance_count,
            "participant_count": participant_count,
            "link_count": link_count,
            "diagnostic_count": diagnostic_count,
        }

    @staticmethod
    def _validate_meta(
        meta: dict[str, str],
        reaction_source: tuple[str, int, int],
        molecule_source: tuple[str, int, int],
    ) -> None:
        reaction_path, reaction_size, reaction_mtime_ns = reaction_source
        molecule_path, molecule_size, molecule_mtime_ns = molecule_source
        if _strict_int(
            meta.get("schema_version"),
            "schema_version",
        ) != EVENT_EVIDENCE_SCHEMA_VERSION:
            raise IndexInvalidError("Event evidence index schema is incompatible")
        association_available = meta.get("association_available", "0") == "1"
        if association_available and _strict_int(
            meta.get("association_algorithm_version"),
            "association_algorithm_version",
        ) != EVENT_ASSOCIATION_ALGORITHM_VERSION:
            raise IndexInvalidError(
                "Event evidence index association algorithm is incompatible"
            )
        if meta.get("build_state") != "ready":
            raise IndexInvalidError("Event evidence index is not complete")
        if meta.get("dataset_id") != dataset_id_for_source(reaction_path):
            raise IndexInvalidError("Event evidence index dataset id is invalid")
        checks = (
            ("reactionevent_size", reaction_size, "reaction-event size"),
            (
                "reactionevent_mtime_ns",
                reaction_mtime_ns,
                "reaction-event modification time",
            ),
            ("molecules_size", molecule_size, "molecules size"),
            (
                "molecules_mtime_ns",
                molecule_mtime_ns,
                "molecules modification time",
            ),
        )
        for key, expected, label in checks:
            actual: str | int = meta.get(key, "")
            if isinstance(expected, int):
                actual = _strict_int(actual, key, minimum=0)
            if actual != expected:
                raise IndexStaleError(f"Event evidence index {label} changed")

    def _open_validated(
        self,
        index_path: Path,
        reaction_source: tuple[str, int, int],
        molecule_source: tuple[str, int, int],
    ) -> dict[str, Any]:
        connection = _readonly_connection(index_path)
        try:
            meta = _read_meta(connection)
            self._validate_meta(meta, reaction_source, molecule_source)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {
                "meta",
                "events",
                "event_atoms",
                "event_species",
                "reaction_summary",
            }.issubset(tables):
                raise IndexInvalidError("Event evidence index tables are incomplete")
            for table, required_columns in _REQUIRED_TABLE_COLUMNS.items():
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    )
                }
                if not required_columns.issubset(columns):
                    raise IndexInvalidError(
                        f"Event evidence index {table} columns are incomplete"
                    )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='events_by_reaction_time'"
            ).fetchone() is None:
                raise IndexInvalidError(
                    "Event evidence index reaction timing lookup is missing"
                )
            event_count = _strict_int(
                meta.get("event_count"),
                "event_count",
                minimum=0,
            )
            reaction_types = _strict_int(
                meta.get("reaction_type_count"),
                "reaction_type_count",
                minimum=0,
            )
            available_intervals = _strict_int(
                meta.get("available_intervals"),
                "available_intervals",
                minimum=0,
            )
            continuity_available = _continuity_tables_available(
                connection, meta
            )
            query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
        except IndexNotReadyError:
            raise
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event evidence index is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()
        return {
            "state": "ready",
            "index_path": str(index_path),
            "dataset_id": meta["dataset_id"],
            "event_count": event_count,
            "reaction_types": reaction_types,
            "available_intervals": available_intervals,
            "association_available": meta.get(
                "association_available", "1"
            )
            == "1",
            "source_kind": meta.get("source_kind", "legacy_csv"),
            "source_schema_version": meta.get("source_schema_version", ""),
            "time_basis": meta.get("time_basis", "physical_timestep"),
            "continuity_available": continuity_available,
            "continuity_schema_version": (
                EVENT_CONTINUITY_SCHEMA_VERSION
                if continuity_available
                else 0
            ),
            "continuity_algorithm_version": (
                EVENT_CONTINUITY_ALGORITHM_VERSION
                if continuity_available
                else 0
            ),
            "query_only": query_only,
        }

    def status(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
        *,
        metadata_only: bool = False,
    ) -> dict[str, Any]:
        index_path = self._expected_path(reactionevent_file)
        building_path = Path(f"{index_path}.building")
        reaction_path = Path(reactionevent_file)
        native_path = str(reaction_path).lower().endswith(".timeline.h5")
        native_selection = None
        molecule_text = str(molecules_file or "").strip()
        molecule_path = Path(molecule_text) if molecule_text else None
        molecule_available = bool(
            molecule_path is not None and molecule_path.is_file()
        )
        if not reaction_path.is_file():
            return {
                "state": "missing_source",
                "index_path": str(index_path),
                "building_path": str(building_path),
                "reactionevent_file": str(reaction_path),
                "molecules_file": molecule_text,
            }
        if (
            native_path
            and not metadata_only
        ):
            try:
                native_selection = select_timed_evidence(
                    timeline_file=str(reaction_path)
                )
            except TimedEvidenceDataError as exc:
                return {
                    "state": exc.state,
                    "index_path": str(index_path),
                    "building_path": str(building_path),
                    "primary_file": str(reaction_path.resolve()),
                    "timeline_file": str(reaction_path.resolve()),
                    "source_kind": "native_hdf5",
                    "message": str(exc),
                }
            molecule_available = native_selection.molecule_enabled
        active = index_path if index_path.is_file() else building_path
        state = (
            "ready"
            if index_path.is_file()
            else ("building" if building_path.exists() else "missing")
        )
        details: dict[str, Any] = {}
        if index_path.is_file():
            try:
                details = self.open_required(
                    str(reaction_path), molecule_text
                )
            except IndexStaleError:
                state = "stale"
            except IndexNotReadyError:
                state = "invalid"
        meta: dict[str, str] = {}
        if active.exists():
            try:
                connection = _readonly_connection(active)
                try:
                    meta = _read_meta(connection)
                finally:
                    connection.close()
            except IndexNotReadyError:
                meta = {}
        reactionevent_size = int(
            meta.get("reactionevent_size", reaction_path.stat().st_size) or 0
        )
        reactionevent_offset = _safe_meta_int(
            meta,
            "reactionevent_offset",
        )
        molecules_size = int(meta.get("molecules_size", 0) or 0)
        molecules_offset = _safe_meta_int(meta, "molecules_offset")
        return {
            "state": state,
            "index_path": str(index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.exists() else 0,
            "source_size": reactionevent_size,
            "source_offset": reactionevent_offset,
            "progress": min(
                max(
                    reactionevent_offset / max(reactionevent_size, 1),
                    0.0,
                ),
                1.0,
            ),
            "reactionevent_file": str(reaction_path.resolve()),
            "primary_file": str(reaction_path.resolve()),
            "timeline_file": (
                str(reaction_path.resolve()) if native_path else ""
            ),
            "source_kind": str(
                details.get(
                    "source_kind",
                    "native_hdf5"
                    if native_path
                    else "legacy_csv",
                )
            ),
            "source_schema_version": str(
                details.get(
                    "source_schema_version",
                    meta.get(
                        "source_schema_version",
                        native_selection.schema_version
                        if native_selection
                        else "",
                    ),
                )
            ),
            "capabilities": list(
                native_selection.capabilities
                if native_selection
                else (
                    ("reaction", "molecule")
                    if (
                        molecule_available
                        or meta.get("association_available", "0") == "1"
                    )
                    else ("reaction",)
                )
            ),
            "molecules_file": (
                str(molecule_path.resolve())
                if molecule_path is not None and molecule_path.is_file()
                else ""
            ),
            "molecules_size": molecules_size,
            "molecules_offset": molecules_offset,
            "association_available": bool(
                details.get(
                    "association_available",
                    meta.get(
                        "association_available",
                        "1" if molecule_available else "0",
                    )
                    == "1",
                )
            ),
            "continuity_available": bool(
                details.get(
                    "continuity_available",
                    _safe_meta_int(meta, "continuity_schema_version")
                    == EVENT_CONTINUITY_SCHEMA_VERSION,
                )
            ),
            "continuity_schema_version": int(
                details.get(
                    "continuity_schema_version",
                    _safe_meta_int(meta, "continuity_schema_version"),
                )
                or 0
            ),
            "time_basis": str(
                details.get(
                    "time_basis",
                    meta.get(
                        "time_basis",
                        (
                            "physical_timestep"
                            if molecule_available
                            else "timestep_index"
                        ),
                    ),
                )
            ),
            "event_count": int(
                details.get(
                    "event_count",
                    _safe_meta_int(meta, "event_count"),
                )
                or 0
            ),
            "reaction_types": int(
                details.get(
                    "reaction_types",
                    _safe_meta_int(meta, "reaction_type_count"),
                )
                or 0
            ),
            "available_intervals": int(
                details.get(
                    "available_intervals",
                    _safe_meta_int(meta, "available_intervals"),
                )
                or 0
            ),
            "updated_at_epoch": (
                _safe_meta_int(meta, "updated_at_epoch") or None
            ),
            "workspace_path": str(index_path.parent),
        }

    def open_required(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
    ) -> dict[str, Any]:
        reaction_source, molecule_source = self._source_pair(
            reactionevent_file, molecules_file
        )
        index_path = event_evidence_index_path(reaction_source[0])
        if not index_path.is_file():
            raise IndexNotReadyError(
                "Event evidence index is not ready; run "
                f"reacnet-scope prepare build event {reaction_source[0]}"
            )
        return self._open_validated(
            index_path, reaction_source, molecule_source
        )

    def open_continuity_required(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
    ) -> dict[str, Any]:
        """Open event evidence that includes the Species Fate substrate."""

        opened = self.open_required(reactionevent_file, molecules_file)
        if not opened.get("association_available"):
            raise IndexInvalidError(
                "Species Fate requires exact molecule/atom association"
            )
        if not opened.get("continuity_available"):
            raise IndexInvalidError(
                "Species Fate continuity schema is missing; run "
                f"reacnet-scope prepare rebuild event {reactionevent_file}"
            )
        return opened

    def _build_reactionevent_only_unlocked(
        self,
        reaction_source: tuple[str, int, int],
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        index_path = event_evidence_index_path(reaction_source[0])
        if index_path.is_file():
            return self.open_required(reaction_source[0], "")
        building_path = Path(f"{index_path}.building")
        if building_path.exists():
            building_path.unlink()
        connection = self._connect_for_build(building_path)
        event_count = 0
        reaction_types: set[str] = set()
        completed_interval = -1
        last_source_row = 0
        try:
            _write_meta(
                connection,
                {
                    "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                    "build_state": "building",
                    "dataset_id": dataset_id_for_source(
                        reaction_source[0]
                    ),
                    "reactionevent_file": reaction_source[0],
                    "reactionevent_size": reaction_source[1],
                    "reactionevent_mtime_ns": reaction_source[2],
                    "molecules_file": "",
                    "molecules_size": 0,
                    "molecules_mtime_ns": 0,
                    "association_available": 0,
                    "association_algorithm_version": 0,
                    "time_basis": "timestep_index",
                    "event_count": 0,
                    "reaction_type_count": 0,
                    "available_intervals": 0,
                    "updated_at_epoch": int(time.time()),
                },
            )
            with open(
                reaction_source[0],
                newline="",
                encoding="utf-8",
            ) as handle:
                reader = csv.DictReader(handle)
                required = {"Timestep_Index", "Reactant", "Product"}
                if not required.issubset(set(reader.fieldnames or [])):
                    raise RngEventDataError(
                        "reactionevent CSV columns are incompatible"
                    )
                interval_summary: dict[str, list[int]] = defaultdict(
                    lambda: [0, 0]
                )

                def flush_summary() -> None:
                    for key, (total, matched) in interval_summary.items():
                        reaction_types.add(key)
                        connection.execute(
                            """
                            INSERT INTO reaction_summary(
                                reaction_key,total_events,matched_events,
                                distinct_intervals
                            ) VALUES(?,?,?,1)
                            ON CONFLICT(reaction_key) DO UPDATE SET
                                total_events=total_events+excluded.total_events,
                                matched_events=matched_events+excluded.matched_events,
                                distinct_intervals=distinct_intervals+1
                            """,
                            (key, total, matched),
                        )
                    interval_summary.clear()

                for source_row, raw in enumerate(reader, 1):
                    try:
                        interval = int(raw["Timestep_Index"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise RngEventDataError(
                            "reactionevent CSV contains an invalid "
                            "Timestep_Index"
                        ) from exc
                    if interval < completed_interval:
                        raise RngEventDataError(
                            "reactionevent CSV must be sorted by "
                            "Timestep_Index"
                        )
                    if completed_interval >= 0 and interval != completed_interval:
                        flush_summary()
                    completed_interval = interval
                    last_source_row = source_row
                    reactant = str(raw.get("Reactant", "")).strip()
                    product = str(raw.get("Product", "")).strip()
                    terms = reaction_key(reactant, product)
                    normalized_key = canonical_reaction_key(*terms)
                    occurrence = interval_summary[normalized_key][0] + 1
                    event_id = _event_id(
                        interval,
                        normalized_key,
                        [],
                        unresolved_ordinal=occurrence,
                    )
                    participants = [
                        [
                            {"species": species, "atom_ids": []}
                            for species in side_terms
                        ]
                        for side_terms in terms
                    ]
                    connection.execute(
                        """
                        INSERT INTO events(
                            event_id,reaction_key,source_row,timestep_index,
                            before_timestep,after_timestep,reactant_text,
                            product_text,atom_ids_json,reactant_bonds_json,
                            product_bonds_json,reactant_participants_json,
                            product_participants_json,association_status,
                            occurrence
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            event_id,
                            normalized_key,
                            source_row,
                            interval,
                            interval,
                            interval + 1,
                            reactant,
                            product,
                            "[]",
                            "[]",
                            "[]",
                            json.dumps(
                                participants[0], separators=(",", ":")
                            ),
                            json.dumps(
                                participants[1], separators=(",", ":")
                            ),
                            "reactionevent_only",
                            occurrence,
                        ),
                    )
                    connection.executemany(
                        """
                        INSERT INTO event_species(
                            event_id,side,species_smiles,timestep_index,
                            occurrence
                        ) VALUES(?,?,?,?,?)
                        """,
                        _event_species_rows(event_id, interval, terms),
                    )
                    interval_summary[normalized_key][0] += 1
                    event_count += 1
                    if event_count % 10_000 == 0:
                        connection.commit()
                        if progress_callback:
                            progress_callback(
                                {
                                    "progress": min(
                                        handle.buffer.tell()
                                        / max(reaction_source[1], 1),
                                        0.95,
                                    ),
                                    "phase": "indexing_reactionevent_only",
                                    "message": (
                                        "Indexing reactionevent chronology"
                                    ),
                                }
                            )
                flush_summary()
            materialize_candidate_adjacency(connection)
            _write_meta(
                connection,
                {
                    "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                    "build_state": "ready",
                    "dataset_id": dataset_id_for_source(
                        reaction_source[0]
                    ),
                    "reactionevent_file": reaction_source[0],
                    "reactionevent_size": reaction_source[1],
                    "reactionevent_mtime_ns": reaction_source[2],
                    "molecules_file": "",
                    "molecules_size": 0,
                    "molecules_mtime_ns": 0,
                    "association_available": 0,
                    "association_algorithm_version": 0,
                    "time_basis": "timestep_index",
                    "reactionevent_offset": reaction_source[1],
                    "molecules_offset": 0,
                    "completed_interval": completed_interval,
                    "last_source_row": last_source_row,
                    "molecule_frame_index": 0,
                    "previous_molecule_timestep": "",
                    "molecule_frame_count": 0,
                    "event_count": event_count,
                    "reaction_type_count": len(reaction_types),
                    "available_intervals": max(
                        completed_interval + 1, 0
                    ),
                    "updated_at_epoch": int(time.time()),
                },
            )
            connection.commit()
        finally:
            connection.close()
        _assert_source_unchanged(
            reaction_source[0], reaction_source[1], reaction_source[2]
        )
        os.replace(building_path, index_path)
        if progress_callback:
            progress_callback(
                {
                    "progress": 1.0,
                    "phase": "completed",
                    "message": "Reactionevent chronology index ready",
                }
            )
        result = self.open_required(reaction_source[0], "")
        result["resumed"] = False
        return result

    def _build_native_hdf5_unlocked(
        self,
        selection: Any,
        reaction_source: tuple[str, int, int],
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """Stream one native timeline into the storage-independent index."""

        dataset_id = dataset_id_for_source(reaction_source[0])
        index_path = event_evidence_index_path(reaction_source[0])
        if index_path.is_file():
            try:
                opened = self.open_required(reaction_source[0], "")
            except (IndexInvalidError, IndexStaleError):
                index_path.unlink()
            else:
                opened["resumed"] = False
                return opened
        building_path = Path(f"{index_path}.building")
        membership_path = Path(f"{building_path}.membership")
        membership_overlay_path = Path(f"{membership_path}.overlay-f")
        connection = self._connect_for_build(building_path)
        existing = {
            str(key): str(value)
            for key, value in connection.execute("SELECT key,value FROM meta")
        }
        association_available = bool(selection.molecule_enabled)
        exact_transition_evidence = bool(
            association_available
            and selection.transition_evidence_enabled
        )
        association_source = (
            "transition_evidence"
            if exact_transition_evidence
            else ("molecule_ranges" if association_available else "none")
        )
        compatible = bool(existing) and (
            int(existing.get("schema_version", 0) or 0)
            == EVENT_EVIDENCE_SCHEMA_VERSION
            and existing.get("build_state") == "building"
            and existing.get("source_kind") == "native_hdf5"
            and existing.get("source_schema_version")
            == selection.schema_version
            and existing.get("association_source", association_source)
            == association_source
            and existing.get("reactionevent_file") == reaction_source[0]
            and int(existing.get("reactionevent_size", -1) or -1)
            == reaction_source[1]
            and int(existing.get("reactionevent_mtime_ns", -1) or -1)
            == reaction_source[2]
            and (existing.get("association_available", "0") == "1")
            == association_available
            and (
                not association_available
                or int(
                    existing.get("association_algorithm_version", 0) or 0
                )
                == EVENT_ASSOCIATION_ALGORITHM_VERSION
            )
        )
        if existing and not compatible:
            connection.close()
            building_path.unlink(missing_ok=True)
            membership_path.unlink(missing_ok=True)
            membership_overlay_path.unlink(missing_ok=True)
            connection = self._connect_for_build(building_path)
            existing = {}
        resumed = compatible
        try:
            with NativeHdf5EvidenceAdapter(selection) as adapter:
                membership: np.ndarray[Any, Any] | None = None
                primary_membership: np.memmap[Any, Any] | None = None
                overlay_membership: np.memmap[Any, Any] | None = None
                range_offset = int(existing.get("range_offset", 0) or 0)
                membership_complete = (
                    exact_transition_evidence
                    or existing.get("membership_complete", "0") == "1"
                )
                membership_layout = existing.get("membership_layout", "C")
                if membership_layout not in {"C", "F", "C+F"}:
                    membership_layout = "C"
                expected_membership_size = 0
                can_materialize_membership = False
                if association_available and not exact_transition_evidence:
                    expected_membership_size = int(
                        np.prod(adapter.membership_shape)
                        * adapter.membership_dtype.itemsize
                    )
                    can_materialize_membership = _can_materialize_membership(
                        expected_membership_size
                    )
                primary_membership_valid = (
                    compatible
                    and membership_path.is_file()
                    and membership_path.stat().st_size
                    == expected_membership_size
                )
                overlay_membership_valid = (
                    compatible
                    and membership_overlay_path.is_file()
                    and membership_overlay_path.stat().st_size
                    == expected_membership_size
                )
                if association_available and not exact_transition_evidence:
                    membership_range_count = int(
                        adapter.handle["molecule_ranges/molecule_id"].shape[0]
                    )
                    if not primary_membership_valid:
                        membership_path.unlink(missing_ok=True)
                        membership_overlay_path.unlink(missing_ok=True)
                        range_offset = 0
                        membership_complete = False
                        membership_layout = (
                            "F" if can_materialize_membership else "C"
                        )
                        overlay_membership_valid = False
                    elif membership_layout == "C+F" and not overlay_membership_valid:
                        membership_overlay_path.unlink(missing_ok=True)
                        range_offset = int(
                            existing.get(
                                "membership_base_range_offset",
                                range_offset,
                            )
                            or 0
                        )
                        membership_complete = False

                    if (
                        membership_layout in {"F", "C+F"}
                        and not can_materialize_membership
                    ):
                        raise TimedEvidenceDataError(
                            "column-major molecule membership requires "
                            "additional RAM before transition indexing",
                            state="resource-exhausted",
                        )

                    if (
                        membership_layout == "C"
                        and primary_membership_valid
                        and not membership_complete
                        and range_offset >= membership_range_count
                    ):
                        membership_complete = True

                    if (
                        membership_layout == "C"
                        and primary_membership_valid
                        and not membership_complete
                        and can_materialize_membership
                    ):
                        membership_layout = "C+F"
                        membership_overlay_path.unlink(missing_ok=True)
                        overlay_membership_valid = False
                        _write_meta(
                            connection,
                            {
                                "membership_layout": membership_layout,
                                "membership_base_range_offset": range_offset,
                                "updated_at_epoch": int(time.time()),
                            },
                        )
                        connection.commit()

                    def checkpoint_membership(
                        completed_offset: int, molecule_id: int
                    ) -> None:
                        _write_meta(
                            connection,
                            {
                                "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                                "build_state": "building",
                                "dataset_id": dataset_id,
                                "source_kind": "native_hdf5",
                                "source_schema_version": selection.schema_version,
                                "association_source": association_source,
                                "reactionevent_file": reaction_source[0],
                                "reactionevent_size": reaction_source[1],
                                "reactionevent_mtime_ns": reaction_source[2],
                                "molecules_file": "",
                                "molecules_size": 0,
                                "molecules_mtime_ns": 0,
                                "association_available": 1,
                                "association_algorithm_version": (
                                    EVENT_ASSOCIATION_ALGORITHM_VERSION
                                ),
                                "time_basis": "physical_timestep",
                                "range_offset": completed_offset,
                                "completed_molecule_id": molecule_id,
                                "membership_layout": membership_layout,
                                "membership_complete": 0,
                                "event_count": 0,
                                "reaction_type_count": 0,
                                "available_intervals": int(
                                    selection.frame_count or 1
                                )
                                - 1,
                                "updated_at_epoch": int(time.time()),
                            },
                        )
                        connection.commit()

                    def report_membership_progress(
                        completed_offset: int, molecule_id: int
                    ) -> None:
                        if progress_callback:
                            progress_callback(
                                {
                                    "progress": min(
                                        completed_offset
                                        / max(
                                            membership_range_count,
                                            1,
                                        )
                                        * 0.45,
                                        0.45,
                                    ),
                                    "phase": "indexing_molecule_ranges",
                                    "message": (
                                        f"Indexed {completed_offset:,}/"
                                        f"{membership_range_count:,} native "
                                        "molecule ranges "
                                        f"(molecule {molecule_id:,})"
                                    ),
                                }
                            )

                    if membership_complete:
                        primary_membership = np.memmap(
                            membership_path,
                            dtype=adapter.membership_dtype,
                            mode="r+",
                            shape=adapter.membership_shape,
                            order=(
                                "F" if membership_layout == "F" else "C"
                            ),
                        )
                        if membership_layout == "C+F":
                            overlay_membership = np.memmap(
                                membership_overlay_path,
                                dtype=adapter.membership_dtype,
                                mode="r+",
                                shape=adapter.membership_shape,
                                order="F",
                            )
                    else:
                        build_path = (
                            membership_overlay_path
                            if membership_layout == "C+F"
                            else membership_path
                        )
                        build_resume = (
                            overlay_membership_valid
                            if membership_layout == "C+F"
                            else primary_membership_valid
                        )
                        built_membership = adapter.build_membership(
                            build_path,
                            start_offset=range_offset,
                            resume=build_resume,
                            order=(
                                "F"
                                if membership_layout in {"F", "C+F"}
                                else "C"
                            ),
                            checkpoint=checkpoint_membership,
                            progress=(
                                report_membership_progress
                                if progress_callback
                                else None
                            ),
                        )
                        if membership_layout == "C+F":
                            overlay_membership = built_membership
                            primary_membership = np.memmap(
                                membership_path,
                                dtype=adapter.membership_dtype,
                                mode="r+",
                                shape=adapter.membership_shape,
                                order="C",
                            )
                        else:
                            primary_membership = built_membership
                        membership_complete = True
                        _write_meta(
                            connection,
                            {
                                "membership_complete": 1,
                                "membership_layout": membership_layout,
                                "updated_at_epoch": int(time.time()),
                            },
                        )
                        connection.commit()

                    def report_materialization_progress(
                        completed_atoms: int, total_atoms: int
                    ) -> None:
                        if progress_callback:
                            progress_callback(
                                {
                                    "progress": 0.45,
                                    "phase": "materializing_molecule_membership",
                                    "message": (
                                        f"Materialized {completed_atoms:,}/"
                                        f"{total_atoms:,} membership atom columns"
                                    ),
                                }
                            )

                    if membership_layout == "C":
                        membership = primary_membership
                    elif membership_layout == "F":
                        assert primary_membership is not None
                        membership = adapter.materialize_membership(
                            primary_membership,
                            progress=(
                                report_materialization_progress
                                if progress_callback
                                else None
                            ),
                        )
                    else:
                        assert primary_membership is not None
                        assert overlay_membership is not None
                        membership = adapter.materialize_membership(
                            primary_membership,
                            overlay=overlay_membership,
                            progress=(
                                report_materialization_progress
                                if progress_callback
                                else None
                            ),
                        )

                if not existing:
                    _write_meta(
                        connection,
                        {
                            "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                            "build_state": "building",
                            "dataset_id": dataset_id,
                            "source_kind": "native_hdf5",
                            "source_schema_version": selection.schema_version,
                            "association_source": association_source,
                            "reactionevent_file": reaction_source[0],
                            "reactionevent_size": reaction_source[1],
                            "reactionevent_mtime_ns": reaction_source[2],
                            "molecules_file": "",
                            "molecules_size": 0,
                            "molecules_mtime_ns": 0,
                            "association_available": int(
                                association_available
                            ),
                            "association_algorithm_version": (
                                EVENT_ASSOCIATION_ALGORITHM_VERSION
                                if association_available
                                else 0
                            ),
                            "time_basis": "physical_timestep",
                            "range_offset": range_offset,
                            "membership_layout": membership_layout,
                            "membership_complete": int(
                                membership_complete
                            ),
                            "completed_transition": -1,
                            "last_source_row": 0,
                            "event_count": 0,
                            "reaction_type_count": 0,
                            "available_intervals": int(
                                selection.frame_count or 1
                            )
                            - 1,
                            "updated_at_epoch": int(time.time()),
                        },
                    )
                    connection.commit()

                completed_transition = int(
                    existing.get("completed_transition", -1) or -1
                )
                source_row = int(existing.get("last_source_row", 0) or 0)
                event_count = int(existing.get("event_count", 0) or 0)
                reaction_type_count = int(
                    existing.get("reaction_type_count", 0) or 0
                )
                transition_count = int(selection.frame_count or 1) - 1
                transition_progress_base = (
                    0.0 if exact_transition_evidence else 0.45
                )
                transition_progress_span = 0.95 - transition_progress_base
                for batch in adapter.iter_transitions(
                    membership,
                    start=completed_transition + 1,
                ):
                    pools: dict[
                        tuple[tuple[str, ...], tuple[str, ...]],
                        deque[MoleculeComponent],
                    ] = defaultdict(deque)
                    if association_available:
                        for component in changed_components(
                            batch.before_molecules,
                            batch.after_molecules,
                        ):
                            pools[component.net_key].append(component)
                    occurrences: dict[str, int] = defaultdict(int)
                    summary_counts: dict[str, list[int]] = defaultdict(
                        lambda: [0, 0]
                    )
                    for aggregate in batch.reactions:
                        terms = aggregate.reaction_terms
                        normalized_key = canonical_reaction_key(*terms)
                        event_net_key = net_reaction_key(*terms)
                        for _logical_ordinal in range(aggregate.count):
                            source_row += 1
                            occurrences[normalized_key] += 1
                            component = (
                                pools[event_net_key].popleft()
                                if pools[event_net_key]
                                else None
                            )
                            rng_atom_ids = (
                                list(component.atom_ids) if component else []
                            )
                            atom_ids = [value + 1 for value in rng_atom_ids]
                            if component is not None:
                                status = "matched"
                            elif association_available:
                                status = "unresolved_hmm_timeline"
                            else:
                                status = "reactionevent_only"
                            event_id = _event_id(
                                batch.transition_index,
                                normalized_key,
                                atom_ids,
                                unresolved_ordinal=occurrences[
                                    normalized_key
                                ],
                            )
                            reactant_bonds = (
                                [
                                    _trajectory_bond_id(value)
                                    for value in component.reactant_bonds
                                ]
                                if component
                                else []
                            )
                            product_bonds = (
                                [
                                    _trajectory_bond_id(value)
                                    for value in component.product_bonds
                                ]
                                if component
                                else []
                            )
                            if component:
                                reactant_participants = _participant_payload(
                                    component.reactant_molecules
                                )
                                product_participants = _participant_payload(
                                    component.product_molecules
                                )
                            elif not association_available:
                                reactant_participants = [
                                    {"species": value, "atom_ids": []}
                                    for value in terms[0]
                                ]
                                product_participants = [
                                    {"species": value, "atom_ids": []}
                                    for value in terms[1]
                                ]
                            else:
                                reactant_participants = []
                                product_participants = []
                            connection.execute(
                                """
                                INSERT INTO events(
                                    event_id,reaction_key,source_row,
                                    timestep_index,before_timestep,
                                    after_timestep,reactant_text,product_text,
                                    atom_ids_json,reactant_bonds_json,
                                    product_bonds_json,
                                    reactant_participants_json,
                                    product_participants_json,
                                    association_status,occurrence
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (
                                    event_id,
                                    normalized_key,
                                    source_row,
                                    batch.transition_index,
                                    batch.before_timestep,
                                    batch.after_timestep,
                                    aggregate.reactant,
                                    aggregate.product,
                                    json.dumps(atom_ids, separators=(",", ":")),
                                    json.dumps(
                                        reactant_bonds, separators=(",", ":")
                                    ),
                                    json.dumps(
                                        product_bonds, separators=(",", ":")
                                    ),
                                    json.dumps(
                                        reactant_participants,
                                        separators=(",", ":"),
                                    ),
                                    json.dumps(
                                        product_participants,
                                        separators=(",", ":"),
                                    ),
                                    status,
                                    occurrences[normalized_key],
                                ),
                            )
                            connection.executemany(
                                """
                                INSERT INTO event_atoms(event_id,atom_id)
                                VALUES(?,?)
                                """,
                                [(event_id, value) for value in atom_ids],
                            )
                            connection.executemany(
                                """
                                INSERT INTO event_species(
                                    event_id,side,species_smiles,
                                    timestep_index,occurrence
                                ) VALUES(?,?,?,?,?)
                                """,
                                _event_species_rows(
                                    event_id,
                                    batch.transition_index,
                                    terms,
                                ),
                            )
                            summary_counts[normalized_key][0] += 1
                            summary_counts[normalized_key][1] += int(
                                status == "matched"
                            )
                            event_count += 1
                    for normalized_key, counts in summary_counts.items():
                        if connection.execute(
                            "SELECT 1 FROM reaction_summary WHERE reaction_key=?",
                            (normalized_key,),
                        ).fetchone() is None:
                            reaction_type_count += 1
                        connection.execute(
                            """
                            INSERT INTO reaction_summary(
                                reaction_key,total_events,matched_events,
                                distinct_intervals
                            ) VALUES(?,?,?,1)
                            ON CONFLICT(reaction_key) DO UPDATE SET
                                total_events=total_events+excluded.total_events,
                                matched_events=matched_events+excluded.matched_events,
                                distinct_intervals=distinct_intervals+1
                            """,
                            (normalized_key, counts[0], counts[1]),
                        )
                    completed_transition = batch.transition_index
                    _write_meta(
                        connection,
                        {
                            "completed_transition": completed_transition,
                            "last_source_row": source_row,
                            "event_count": event_count,
                            "reaction_type_count": reaction_type_count,
                            "reactionevent_offset": int(
                                reaction_source[1]
                                * (completed_transition + 1)
                                / max(transition_count, 1)
                            ),
                            "updated_at_epoch": int(time.time()),
                        },
                    )
                    should_checkpoint = (
                        (completed_transition + 1) % 100 == 0
                        or completed_transition + 1 == transition_count
                    )
                    if should_checkpoint:
                        connection.commit()
                    if progress_callback and should_checkpoint:
                        progress_callback(
                            {
                                "progress": transition_progress_base
                                + transition_progress_span
                                * (completed_transition + 1)
                                / max(transition_count, 1),
                                "phase": "checkpoint_event_index",
                                "message": (
                                    "Checkpointed native transition "
                                    f"{completed_transition}"
                                ),
                                "resumed": resumed,
                            }
                        )
                actual_event_count = int(
                    connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                )
                actual_type_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM reaction_summary"
                    ).fetchone()[0]
                )
                if (
                    actual_event_count != event_count
                    or actual_type_count != reaction_type_count
                ):
                    raise IndexInvalidError(
                        "Native event evidence checkpoint counts are inconsistent"
                    )
                if association_available:
                    if progress_callback:
                        progress_callback(
                            {
                                "progress": 0.98,
                                "phase": "building_molecular_continuity",
                                "message": "Building molecular continuity substrate",
                                "resumed": resumed,
                            }
                        )
                    self._materialize_continuity(
                        connection,
                        replicate_id=dataset_id,
                    )
                materialize_candidate_adjacency(connection)
                _write_meta(
                    connection,
                    {
                        "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                        "build_state": "ready",
                        "dataset_id": dataset_id,
                        "source_kind": "native_hdf5",
                        "source_schema_version": selection.schema_version,
                        "association_source": association_source,
                        "reactionevent_file": reaction_source[0],
                        "reactionevent_size": reaction_source[1],
                        "reactionevent_mtime_ns": reaction_source[2],
                        "molecules_file": "",
                        "molecules_size": 0,
                        "molecules_mtime_ns": 0,
                        "association_available": int(association_available),
                        "association_algorithm_version": (
                            EVENT_ASSOCIATION_ALGORITHM_VERSION
                            if association_available
                            else 0
                        ),
                        "time_basis": "physical_timestep",
                        "reactionevent_offset": reaction_source[1],
                        "molecules_offset": 0,
                        "completed_transition": completed_transition,
                        "last_source_row": source_row,
                        "membership_complete": int(membership_complete),
                        "event_count": event_count,
                        "reaction_type_count": reaction_type_count,
                        "molecule_frame_count": int(
                            selection.frame_count or 0
                        ),
                        "available_intervals": transition_count,
                        "updated_at_epoch": int(time.time()),
                    },
                )
                connection.commit()
                if isinstance(membership, np.memmap):
                    membership.flush()
                if membership is not None:
                    del membership
                if overlay_membership is not None:
                    overlay_membership.flush()
                    del overlay_membership
                if primary_membership is not None:
                    primary_membership.flush()
                    del primary_membership
        finally:
            connection.close()
        _assert_source_unchanged(
            reaction_source[0], reaction_source[1], reaction_source[2]
        )
        os.replace(building_path, index_path)
        membership_path.unlink(missing_ok=True)
        membership_overlay_path.unlink(missing_ok=True)
        if progress_callback:
            progress_callback(
                {
                    "progress": 1.0,
                    "phase": "completed",
                    "message": "Native event evidence index ready",
                }
            )
        result = self.open_required(reaction_source[0], "")
        result["resumed"] = resumed
        return result

    def build(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        is_native = str(reactionevent_file).lower().endswith(".timeline.h5")
        selection = select_timed_evidence(
            timeline_file=reactionevent_file if is_native else "",
            reactionevent_file="" if is_native else reactionevent_file,
            molecules_file=molecules_file,
        )
        reaction_source, molecule_source = self._source_pair(
            selection.primary_file,
            selection.molecules_file,
        )
        resolve_dataset_paths(reaction_source[0], persist_identity=True)
        index_path = event_evidence_index_path(reaction_source[0])
        with _exclusive_build_lock(index_path):
            if selection.kind == "native_hdf5":
                return self._build_native_hdf5_unlocked(
                    selection,
                    reaction_source,
                    progress_callback=progress_callback,
                )
            if index_path.is_file():
                try:
                    opened = self.open_required(
                        reaction_source[0], molecule_source[0]
                    )
                except (IndexInvalidError, IndexStaleError):
                    index_path.unlink()
                else:
                    opened["resumed"] = False
                    return opened
            if not molecule_source[0]:
                return self._build_reactionevent_only_unlocked(
                    reaction_source,
                    progress_callback=progress_callback,
                )
            building_path = Path(f"{index_path}.building")
            connection = self._connect_for_build(building_path)
            existing = {
                str(key): str(value)
                for key, value in connection.execute(
                    "SELECT key,value FROM meta"
                )
            }
            compatible = bool(existing) and (
                int(existing.get("schema_version", 0) or 0)
                == EVENT_EVIDENCE_SCHEMA_VERSION
                and existing.get("build_state") == "building"
                and existing.get("reactionevent_file") == reaction_source[0]
                and int(existing.get("reactionevent_size", -1) or -1)
                == reaction_source[1]
                and int(existing.get("reactionevent_mtime_ns", -1) or -1)
                == reaction_source[2]
                and existing.get("molecules_file") == molecule_source[0]
                and int(
                    existing.get("association_algorithm_version", 0) or 0
                )
                == EVENT_ASSOCIATION_ALGORITHM_VERSION
                and int(existing.get("molecules_size", -1) or -1)
                == molecule_source[1]
                and int(existing.get("molecules_mtime_ns", -1) or -1)
                == molecule_source[2]
            )
            if existing and not compatible:
                connection.close()
                building_path.unlink(missing_ok=True)
                connection = self._connect_for_build(building_path)
                existing = {}

            with closing(connection), open(
                reaction_source[0], "rb"
            ) as reaction_handle, open(
                molecule_source[0], "rb"
            ) as molecule_handle:
                reaction_fields, first_event_offset = _read_csv_header(
                    reaction_handle,
                    required={"Timestep_Index", "Reactant", "Product"},
                    label="reactionevent",
                )
                molecule_fields, first_molecule_offset = _read_csv_header(
                    molecule_handle,
                    required={"Timestep", "Species", "AtomIDs", "BondIDs"},
                    label="molecules",
                )
                dataset_id = dataset_id_for_source(reaction_source[0])

                if compatible:
                    event_offset = int(
                        existing.get(
                            "reactionevent_offset", first_event_offset
                        )
                        or first_event_offset
                    )
                    molecule_offset = int(
                        existing.get(
                            "molecules_offset", first_molecule_offset
                        )
                        or first_molecule_offset
                    )
                    completed_interval = int(
                        existing.get("completed_interval", -1) or -1
                    )
                    last_source_row = int(
                        existing.get("last_source_row", 0) or 0
                    )
                    molecule_frame_index = int(
                        existing.get("molecule_frame_index", 0) or 0
                    )
                    previous_molecule_timestep = (
                        int(existing["previous_molecule_timestep"])
                        if existing.get("previous_molecule_timestep", "")
                        else None
                    )
                    event_count = int(existing.get("event_count", 0) or 0)
                    reaction_type_count = int(
                        existing.get("reaction_type_count", 0) or 0
                    )
                else:
                    event_offset = first_event_offset
                    molecule_offset = first_molecule_offset
                    completed_interval = -1
                    last_source_row = 0
                    molecule_frame_index = 0
                    previous_molecule_timestep = None
                    event_count = 0
                    reaction_type_count = 0
                    _write_meta(
                        connection,
                        {
                            "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                            "build_state": "building",
                            "dataset_id": dataset_id,
                            "reactionevent_file": reaction_source[0],
                            "reactionevent_size": reaction_source[1],
                            "reactionevent_mtime_ns": reaction_source[2],
                            "molecules_file": molecule_source[0],
                            "molecules_size": molecule_source[1],
                            "molecules_mtime_ns": molecule_source[2],
                            "association_available": 1,
                            "association_algorithm_version": (
                                EVENT_ASSOCIATION_ALGORITHM_VERSION
                            ),
                            "time_basis": "physical_timestep",
                            "reactionevent_offset": event_offset,
                            "molecules_offset": molecule_offset,
                            "completed_interval": completed_interval,
                            "last_source_row": last_source_row,
                            "molecule_frame_index": molecule_frame_index,
                            "previous_molecule_timestep": "",
                            "event_count": 0,
                            "reaction_type_count": 0,
                            "updated_at_epoch": int(time.time()),
                        },
                    )
                    connection.commit()

                resumed = compatible and (
                    completed_interval >= 0
                    or event_offset > first_event_offset
                    or molecule_offset > first_molecule_offset
                )
                reaction_handle.seek(event_offset)
                molecule_handle.seek(molecule_offset)

                current_molecule = _read_molecule_group(
                    molecule_handle,
                    molecule_fields,
                    frame_index=molecule_frame_index,
                    previous_timestep=previous_molecule_timestep,
                )

                intervals_since_checkpoint = 0

                def write_interval_checkpoint() -> None:
                    if current_molecule is None:
                        raise IndexInvalidError(
                            "Event evidence checkpoint has no molecule frame"
                        )
                    _write_meta(
                        connection,
                        {
                            "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                            "build_state": "building",
                            "dataset_id": dataset_id,
                            "reactionevent_file": reaction_source[0],
                            "reactionevent_size": reaction_source[1],
                            "reactionevent_mtime_ns": reaction_source[2],
                            "molecules_file": molecule_source[0],
                            "molecules_size": molecule_source[1],
                            "molecules_mtime_ns": molecule_source[2],
                            "association_available": 1,
                            "association_algorithm_version": (
                                EVENT_ASSOCIATION_ALGORITHM_VERSION
                            ),
                            "time_basis": "physical_timestep",
                            "reactionevent_offset": event_offset,
                            "molecules_offset": current_molecule[3],
                            "completed_interval": completed_interval,
                            "last_source_row": last_source_row,
                            "molecule_frame_index": molecule_frame_index,
                            "previous_molecule_timestep": (
                                previous_molecule_timestep
                            ),
                            "event_count": event_count,
                            "reaction_type_count": reaction_type_count,
                            "updated_at_epoch": int(time.time()),
                        },
                    )
                    connection.commit()

                try:
                    while True:
                        event_group = _read_event_group(
                            reaction_handle,
                            reaction_fields,
                            last_interval=completed_interval,
                            last_source_row=last_source_row,
                        )
                        if event_group is None:
                            break
                        events, next_event_offset, next_source_row = event_group
                        timestep_index = int(events[0]["timestep_index"])

                        while (
                            current_molecule is not None
                            and current_molecule[0] < timestep_index
                        ):
                            previous_molecule_timestep = current_molecule[1]
                            molecule_frame_index = current_molecule[0] + 1
                            current_molecule = _read_molecule_group(
                                molecule_handle,
                                molecule_fields,
                                frame_index=molecule_frame_index,
                                previous_timestep=previous_molecule_timestep,
                            )
                        if (
                            current_molecule is None
                            or current_molecule[0] != timestep_index
                        ):
                            raise RngEventDataError(
                                "molecules timeline does not cover "
                                f"reaction-event interval {timestep_index}"
                            )
                        before_frame = current_molecule
                        after_frame = _read_molecule_group(
                            molecule_handle,
                            molecule_fields,
                            frame_index=before_frame[0] + 1,
                            previous_timestep=before_frame[1],
                        )
                        if after_frame is None:
                            raise RngEventDataError(
                                "molecules timeline does not cover "
                                f"reaction-event interval {timestep_index + 1}"
                            )

                        pools: dict[
                            tuple[tuple[str, ...], tuple[str, ...]],
                            deque[MoleculeComponent],
                        ] = defaultdict(deque)
                        for component in changed_components(
                            before_frame[2], after_frame[2]
                        ):
                            pools[component.net_key].append(component)
                        occurrences: dict[str, int] = defaultdict(int)
                        summary_counts: dict[str, list[int]] = defaultdict(
                            lambda: [0, 0]
                        )
                        for event in events:
                            normalized_key = str(
                                event["reaction_key_text"]
                            )
                            occurrences[normalized_key] += 1
                            event_net_key = net_reaction_key(
                                *event["reaction_key"]
                            )
                            component = (
                                pools[event_net_key].popleft()
                                if pools[event_net_key]
                                else None
                            )
                            rng_atom_ids = (
                                list(component.atom_ids) if component else []
                            )
                            atom_ids = [
                                atom_id + 1 for atom_id in rng_atom_ids
                            ]
                            reactant_bonds = (
                                [
                                    _trajectory_bond_id(bond)
                                    for bond in component.reactant_bonds
                                ]
                                if component
                                else []
                            )
                            product_bonds = (
                                [
                                    _trajectory_bond_id(bond)
                                    for bond in component.product_bonds
                                ]
                                if component
                                else []
                            )
                            status = (
                                "matched"
                                if component
                                else "unresolved_hmm_timeline"
                            )
                            event_id = _event_id(
                                timestep_index,
                                normalized_key,
                                atom_ids,
                                unresolved_ordinal=occurrences[
                                    normalized_key
                                ],
                            )
                            reactant_participants = (
                                _participant_payload(
                                    component.reactant_molecules
                                )
                                if component
                                else []
                            )
                            product_participants = (
                                _participant_payload(
                                    component.product_molecules
                                )
                                if component
                                else []
                            )
                            connection.execute(
                                """
                                INSERT INTO events(
                                    event_id,reaction_key,source_row,
                                    timestep_index,before_timestep,
                                    after_timestep,reactant_text,product_text,
                                    atom_ids_json,reactant_bonds_json,
                                    product_bonds_json,
                                    reactant_participants_json,
                                    product_participants_json,
                                    association_status,occurrence
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (
                                    event_id,
                                    normalized_key,
                                    int(event["source_row"]),
                                    timestep_index,
                                    int(before_frame[1]),
                                    int(after_frame[1]),
                                    str(event["reactant"]),
                                    str(event["product"]),
                                    json.dumps(
                                        atom_ids, separators=(",", ":")
                                    ),
                                    json.dumps(
                                        reactant_bonds,
                                        separators=(",", ":"),
                                    ),
                                    json.dumps(
                                        product_bonds,
                                        separators=(",", ":"),
                                    ),
                                    json.dumps(
                                        reactant_participants,
                                        separators=(",", ":"),
                                    ),
                                    json.dumps(
                                        product_participants,
                                        separators=(",", ":"),
                                    ),
                                    status,
                                    occurrences[normalized_key],
                                ),
                            )
                            connection.executemany(
                                """
                                INSERT INTO event_atoms(event_id,atom_id)
                                VALUES(?,?)
                                """,
                                [
                                    (event_id, atom_id)
                                    for atom_id in atom_ids
                                ],
                            )
                            connection.executemany(
                                """
                                INSERT INTO event_species(
                                    event_id,side,species_smiles,
                                    timestep_index,occurrence
                                ) VALUES(?,?,?,?,?)
                                """,
                                _event_species_rows(
                                    event_id,
                                    timestep_index,
                                    event["reaction_key"],
                                ),
                            )
                            summary_counts[normalized_key][0] += 1
                            summary_counts[normalized_key][1] += int(
                                status == "matched"
                            )

                        for normalized_key, (
                            total_count,
                            matched_count,
                        ) in summary_counts.items():
                            if connection.execute(
                                """
                                SELECT 1 FROM reaction_summary
                                WHERE reaction_key=?
                                """,
                                (normalized_key,),
                            ).fetchone() is None:
                                reaction_type_count += 1
                            connection.execute(
                                """
                                INSERT INTO reaction_summary(
                                    reaction_key,total_events,
                                    matched_events,distinct_intervals
                                ) VALUES(?,?,?,1)
                                ON CONFLICT(reaction_key) DO UPDATE SET
                                    total_events=total_events+excluded.total_events,
                                    matched_events=matched_events+excluded.matched_events,
                                    distinct_intervals=distinct_intervals+1
                                """,
                                (
                                    normalized_key,
                                    total_count,
                                    matched_count,
                                ),
                            )

                        event_count += len(events)
                        event_offset = next_event_offset
                        completed_interval = timestep_index
                        last_source_row = next_source_row
                        current_molecule = after_frame
                        molecule_frame_index = after_frame[0]
                        previous_molecule_timestep = before_frame[1]
                        intervals_since_checkpoint += 1
                        should_checkpoint = (
                            intervals_since_checkpoint
                            >= _EVENT_BUILD_CHECKPOINT_INTERVALS
                        )
                        if should_checkpoint:
                            write_interval_checkpoint()
                            intervals_since_checkpoint = 0
                        if progress_callback:
                            progress_callback(
                                {
                                    "progress": min(
                                        event_offset
                                        / max(reaction_source[1], 1),
                                        1.0,
                                    ),
                                    "phase": (
                                        "checkpoint_event_index"
                                        if should_checkpoint
                                        else "indexing_event_evidence"
                                    ),
                                    "message": (
                                        (
                                            "Checkpointed event evidence "
                                            if should_checkpoint
                                            else "Indexed event evidence "
                                        )
                                        + f"interval {timestep_index}"
                                    ),
                                    "resumed": resumed,
                                }
                            )

                    if intervals_since_checkpoint:
                        write_interval_checkpoint()
                        intervals_since_checkpoint = 0
                        if progress_callback:
                            progress_callback(
                                {
                                    "progress": min(
                                        event_offset
                                        / max(reaction_source[1], 1),
                                        1.0,
                                    ),
                                    "phase": "checkpoint_event_index",
                                    "message": (
                                        "Checkpointed event evidence "
                                        f"interval {completed_interval}"
                                    ),
                                    "resumed": resumed,
                                }
                            )

                    if current_molecule is None:
                        molecule_frame_count = molecule_frame_index
                    else:
                        molecule_frame_count = current_molecule[0] + 1
                        while True:
                            previous_molecule_timestep = current_molecule[1]
                            next_frame = _read_molecule_group(
                                molecule_handle,
                                molecule_fields,
                                frame_index=current_molecule[0] + 1,
                                previous_timestep=previous_molecule_timestep,
                            )
                            if next_frame is None:
                                break
                            current_molecule = next_frame
                            molecule_frame_count = current_molecule[0] + 1

                    actual_event_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM events"
                        ).fetchone()[0]
                    )
                    actual_reaction_type_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM reaction_summary"
                        ).fetchone()[0]
                    )
                    if (
                        actual_event_count != event_count
                        or actual_reaction_type_count != reaction_type_count
                    ):
                        raise IndexInvalidError(
                            "Event evidence checkpoint counts are inconsistent"
                        )
                    if progress_callback:
                        progress_callback(
                            {
                                "progress": 0.98,
                                "phase": "building_molecular_continuity",
                                "message": "Building molecular continuity substrate",
                                "resumed": resumed,
                            }
                        )
                    self._materialize_continuity(
                        connection,
                        replicate_id=dataset_id,
                    )
                    materialize_candidate_adjacency(connection)
                    _write_meta(
                        connection,
                        {
                            "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                            "build_state": "ready",
                            "dataset_id": dataset_id,
                            "reactionevent_file": reaction_source[0],
                            "reactionevent_size": reaction_source[1],
                            "reactionevent_mtime_ns": reaction_source[2],
                            "molecules_file": molecule_source[0],
                            "molecules_size": molecule_source[1],
                            "molecules_mtime_ns": molecule_source[2],
                            "association_available": 1,
                            "association_algorithm_version": (
                                EVENT_ASSOCIATION_ALGORITHM_VERSION
                            ),
                            "time_basis": "physical_timestep",
                            "reactionevent_offset": reaction_source[1],
                            "molecules_offset": molecule_source[1],
                            "completed_interval": completed_interval,
                            "last_source_row": last_source_row,
                            "molecule_frame_index": max(
                                molecule_frame_count - 1, 0
                            ),
                            "previous_molecule_timestep": (
                                current_molecule[1]
                                if current_molecule is not None
                                else ""
                            ),
                            "event_count": event_count,
                            "reaction_type_count": reaction_type_count,
                            "molecule_frame_count": molecule_frame_count,
                            "available_intervals": max(
                                molecule_frame_count - 1, 0
                            ),
                            "updated_at_epoch": int(time.time()),
                        },
                    )
                    connection.commit()
                finally:
                    connection.close()
            _assert_source_unchanged(
                reaction_source[0], reaction_source[1], reaction_source[2]
            )
            _assert_source_unchanged(
                molecule_source[0], molecule_source[1], molecule_source[2]
            )
            os.replace(building_path, index_path)
            if progress_callback:
                progress_callback(
                    {
                        "progress": 1.0,
                        "phase": "completed",
                        "message": "Event evidence index ready",
                    }
                )
        result = self.open_required(reaction_source[0], molecule_source[0])
        result["resumed"] = resumed
        return result

    def get_event(
        self,
        reactionevent_file: str,
        molecules_file: str,
        event_id: str,
    ) -> dict[str, Any]:
        """Return one validated event payload by its stable primary key."""
        opened = self.open_required(reactionevent_file, molecules_file)
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            record = connection.execute(
                f"""
                SELECT {_EVENT_SELECT_COLUMNS}
                FROM events
                WHERE event_id=?
                """,
                (str(event_id),),
            ).fetchone()
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event evidence index is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()
        if record is None:
            raise EventNotFoundError(
                f"Event evidence index does not contain event {event_id}"
            )
        try:
            return _event_payload_from_record(record, event_index=1)
        except (TypeError, ValueError) as exc:
            raise IndexInvalidError(
                f"Event evidence index payload is invalid: {exc}"
            ) from exc

    def query_events(
        self,
        reactionevent_file: str,
        molecules_file: str,
        reaction_key: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> dict[str, Any]:
        opened = self.open_required(reactionevent_file, molecules_file)
        safe_limit = max(1, min(int(limit), 10_000))
        safe_offset = max(0, int(offset))
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE reaction_key=?",
                    (str(reaction_key),),
                ).fetchone()[0]
            )
            records = connection.execute(
                f"""
                SELECT {_EVENT_SELECT_COLUMNS}
                FROM events
                WHERE reaction_key=?
                ORDER BY timestep_index,source_row,event_id
                LIMIT ? OFFSET ?
                """,
                (str(reaction_key), safe_limit, safe_offset),
            ).fetchall()
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event evidence index is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()
        rows: list[dict[str, Any]] = []
        try:
            for page_index, record in enumerate(records, safe_offset + 1):
                rows.append(
                    _event_payload_from_record(
                        record,
                        event_index=page_index,
                    )
                )
        except (TypeError, ValueError) as exc:
            raise IndexInvalidError(
                f"Event evidence index payload is invalid: {exc}"
            ) from exc
        source_label = (
            "timeline"
            if opened.get("source_kind") == "native_hdf5"
            else "reactionevent"
        )
        source_signatures = {
            source_label: {
                "path": os.path.abspath(reactionevent_file),
                "size": os.path.getsize(reactionevent_file),
                "mtime_ns": os.stat(reactionevent_file).st_mtime_ns,
            },
        }
        if str(molecules_file or "").strip():
            source_signatures["molecules"] = {
                "path": os.path.abspath(molecules_file),
                "size": os.path.getsize(molecules_file),
                "mtime_ns": os.stat(molecules_file).st_mtime_ns,
            }
        return {
            "rows": rows,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
            "evidence_status": "evidence_linked",
            "association_available": opened["association_available"],
            "time_basis": opened["time_basis"],
            "source_signatures": source_signatures,
        }

    def reaction_time_summary(
        self,
        reactionevent_file: str,
        molecules_file: str,
        reaction_keys: Iterable[str],
    ) -> dict[str, dict[str, int | None]]:
        """Summarize all indexed occurrences, independent of event pages."""
        opened = self.open_required(reactionevent_file, molecules_file)
        selected = sorted({str(key) for key in reaction_keys if str(key)})
        result: dict[str, dict[str, int | None]] = {
            key: {"total": 0, "first_after_timestep": None,
                  "last_after_timestep": None} for key in selected
        }
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            for start in range(0, len(selected), 500):
                chunk = selected[start:start + 500]
                if not chunk:
                    continue
                placeholders = ",".join("?" for _ in chunk)
                for key, count, first, last in connection.execute(
                    f"SELECT reaction_key,COUNT(*),MIN(after_timestep),"
                    f"MAX(after_timestep) FROM events WHERE reaction_key IN "
                    f"({placeholders}) GROUP BY reaction_key", chunk
                ):
                    result[str(key)] = {
                        "total": int(count),
                        "first_after_timestep": int(first),
                        "last_after_timestep": int(last),
                    }
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Event timing summary is corrupt: {exc}") from exc
        finally:
            connection.close()
        return result

    def reaction_time_bins(
        self,
        reactionevent_file: str,
        molecules_file: str,
        reaction_keys: Iterable[str],
        *,
        start: float,
        end: float,
        width: float,
    ) -> dict[str, Any]:
        """Count occurrences in half-open after-frame coordinate bins."""
        if not all(math.isfinite(float(x)) for x in (start, end, width)):
            raise ValueError("time window and bin width must be finite")
        if width <= 0 or end <= start:
            raise ValueError("time window and bin width must be positive")
        n_bins = math.ceil((end - start) / width)
        if n_bins > 500:
            raise ValueError("time window exceeds the 500-bin limit")
        opened = self.open_required(reactionevent_file, molecules_file)
        keys = list(dict.fromkeys(str(key) for key in reaction_keys if str(key)))
        output = {key: [0] * n_bins for key in keys}
        totals = {key: 0 for key in keys}
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            for key in keys:
                for bucket, count in connection.execute(
                    "SELECT CAST((after_timestep-?)/? AS INTEGER),COUNT(*) "
                    "FROM events WHERE reaction_key=? AND after_timestep>=? "
                    "AND after_timestep<? GROUP BY 1",
                    (start, width, key, start, end),
                ):
                    index = int(bucket)
                    if not 0 <= index < n_bins:
                        raise IndexInvalidError("Event timing bin is outside window")
                    output[key][index] = int(count)
                    totals[key] += int(count)
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Event timing bins are corrupt: {exc}") from exc
        finally:
            connection.close()
        return {"counts": output, "totals": totals, "bin_count": n_bins,
                "start": start, "end": end, "width": width,
                "time_basis": opened["time_basis"]}

    def query_events_window(
        self,
        reactionevent_file: str,
        molecules_file: str,
        reaction_key: str,
        *,
        start: float,
        end: float,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Page one exact reaction and one half-open after-frame interval."""
        if not math.isfinite(float(start)) or not math.isfinite(float(end)) or end <= start:
            raise ValueError("invalid event time window")
        if not 1 <= int(limit) <= 100 or int(offset) < 0:
            raise ValueError("event page size or offset is invalid")
        opened = self.open_required(reactionevent_file, molecules_file)
        connection = _readonly_connection(Path(opened["index_path"]))
        where = "reaction_key=? AND after_timestep>=? AND after_timestep<?"
        params = (str(reaction_key), start, end)
        try:
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM events WHERE {where}", params
            ).fetchone()[0])
            records = connection.execute(
                f"SELECT {_EVENT_SELECT_COLUMNS} FROM events WHERE {where} "
                "ORDER BY after_timestep,event_id LIMIT ? OFFSET ?",
                (*params, int(limit), int(offset)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Event timing page is corrupt: {exc}") from exc
        finally:
            connection.close()
        rows = [_event_payload_from_record(record, event_index=int(offset) + i)
                for i, record in enumerate(records, 1)]
        return {"rows": rows, "total": total, "offset": int(offset),
                "limit": int(limit), "has_more": int(offset) + len(rows) < total,
                "time_basis": opened["time_basis"]}

    def reaction_counts(
        self,
        reactionevent_file: str,
        molecules_file: str,
        reaction_keys: Iterable[str],
        *,
        before_timestep: int,
        after_timestep: int,
    ) -> dict[str, int]:
        """Count exact Reaction Occurrences inside one closed evidence window."""
        opened = self.open_required(reactionevent_file, molecules_file)
        selected = sorted({str(key) for key in reaction_keys if str(key)})
        if not selected:
            return {}
        start = int(before_timestep)
        end = int(after_timestep)
        if end <= start:
            raise ValueError("reaction count window must have positive duration")
        output = {key: 0 for key in selected}
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            for offset in range(0, len(selected), 500):
                chunk = selected[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                for key, total in connection.execute(
                    f"""
                    SELECT reaction_key,COUNT(*)
                    FROM events
                    WHERE reaction_key IN ({placeholders})
                      AND before_timestep>=?
                      AND after_timestep<=?
                    GROUP BY reaction_key
                    """,
                    [*chunk, start, end],
                ):
                    output[str(key)] = int(total)
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event evidence index is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()
        return output

    def query_adjacent_events(
        self,
        reactionevent_file: str,
        molecules_file: str,
        event_id: str,
        *,
        intermediate_smiles: str,
        direction: str = "backward",
        limit: int = 20,
        include_total: bool = True,
    ) -> dict[str, Any]:
        """Find earlier/later authored events joined by one exact SMILES."""
        opened = self.open_required(reactionevent_file, molecules_file)
        direction_text = str(direction or "backward")
        if direction_text not in {"backward", "forward"}:
            raise ValueError("direction must be backward or forward")
        bridge = str(intermediate_smiles or "").strip()
        if not bridge:
            raise ValueError("intermediate_smiles is required")
        safe_limit = max(1, min(int(limit), 1000))
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            anchor_record = connection.execute(
                f"""
                SELECT {_EVENT_SELECT_COLUMNS}
                FROM events
                WHERE event_id=?
                """,
                (str(event_id),),
            ).fetchone()
            if anchor_record is None:
                raise IndexInvalidError(
                    f"Event evidence index does not contain event {event_id}"
                )
            anchor = _event_payload_from_record(
                anchor_record,
                event_index=1,
            )
            anchor_side = (
                anchor["reactant_participants"]
                if direction_text == "backward"
                else anchor["product_participants"]
            )
            if bridge not in {
                str(participant["species"]) for participant in anchor_side
            }:
                raise ValueError(
                    "intermediate_smiles is not on the selected anchor side"
                )
            candidate_side = (
                "product" if direction_text == "backward" else "reactant"
            )
            comparator = "<" if direction_text == "backward" else ">"
            order = "DESC" if direction_text == "backward" else "ASC"
            total = (
                int(
                    connection.execute(
                        f"""
                        SELECT COUNT(DISTINCT e.event_id)
                        FROM event_species AS s
                        JOIN events AS e ON e.event_id=s.event_id
                        WHERE s.side=? AND s.species_smiles=?
                          AND e.timestep_index {comparator} ?
                        """,
                        (
                            candidate_side,
                            bridge,
                            int(anchor["timestep_index"]),
                        ),
                    ).fetchone()[0]
                )
                if include_total
                else None
            )
            records = connection.execute(
                f"""
                SELECT {_EVENT_SELECT_COLUMNS}
                FROM events AS e
                WHERE e.event_id IN (
                    SELECT s.event_id
                    FROM event_species AS s
                    WHERE s.side=? AND s.species_smiles=?
                      AND s.timestep_index {comparator} ?
                )
                ORDER BY e.timestep_index {order},e.source_row,e.event_id
                LIMIT ?
                """,
                (
                    candidate_side,
                    bridge,
                    int(anchor["timestep_index"]),
                    safe_limit,
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event species index is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()
        rows: list[dict[str, Any]] = []
        for rank, record in enumerate(records, 1):
            candidate = _event_payload_from_record(
                record,
                event_index=rank,
            )
            candidate.update(
                candidate_rank=rank,
                direction=direction_text,
                intermediate_smiles=bridge,
                interval_gap=abs(
                    int(anchor["timestep_index"])
                    - int(candidate["timestep_index"])
                ),
                timestep_gap=max(
                    0,
                    (
                        int(anchor["before_timestep"])
                        - int(candidate["after_timestep"])
                        if direction_text == "backward"
                        else int(candidate["before_timestep"])
                        - int(anchor["after_timestep"])
                    ),
                ),
                evidence_level="rng_event",
                time_basis=opened["time_basis"],
                can_assert_order=True,
            )
            rows.append(candidate)
        return {
            "anchor": anchor,
            "direction": direction_text,
            "intermediate_smiles": bridge,
            "rows": rows,
            "total": total,
            "limit": safe_limit,
            "evidence_level": "rng_event",
            "time_basis": opened["time_basis"],
            "can_assert_order": True,
            "association_available": opened["association_available"],
        }

    def query_nearest_atom_events(
        self,
        reactionevent_file: str,
        molecules_file: str,
        atom_ids: Iterable[int],
        *,
        timestep_index: int,
        direction: str,
        limit: int = 256,
    ) -> dict[str, Any]:
        """Return the nearest indexed interval involving any supplied atom.

        This deliberately does not infer molecular continuity.  Consumers
        must still verify the exact participant Species and atom-ID set on
        the appropriate side of each returned event.
        """

        opened = self.open_required(reactionevent_file, molecules_file)
        if not opened["association_available"]:
            raise IndexInvalidError(
                "Event evidence index has no molecule/atom association"
            )
        direction_text = str(direction or "").strip().lower()
        if direction_text not in {"backward", "forward"}:
            raise ValueError("direction must be backward or forward")
        normalized_atoms = sorted(
            {
                _strict_int(value, "atom_id", minimum=1)
                for value in atom_ids
            }
        )
        if not normalized_atoms:
            raise ValueError("atom_ids must contain at least one atom")
        safe_limit = max(1, min(int(limit), 10_000))
        comparator = "<" if direction_text == "backward" else ">"
        order = "DESC" if direction_text == "backward" else "ASC"
        placeholders = ",".join("?" for _value in normalized_atoms)
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            records = connection.execute(
                f"""
                SELECT DISTINCT {_EVENT_SELECT_COLUMNS_E}
                FROM events AS e
                JOIN event_atoms AS a ON a.event_id=e.event_id
                WHERE a.atom_id IN ({placeholders})
                  AND e.timestep_index {comparator} ?
                ORDER BY e.timestep_index {order},e.source_row,e.event_id
                LIMIT ?
                """,
                (
                    *normalized_atoms,
                    int(timestep_index),
                    safe_limit,
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event atom index is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()

        rows: list[dict[str, Any]] = []
        nearest_interval: int | None = None
        for rank, record in enumerate(records, 1):
            candidate = _event_payload_from_record(record, event_index=rank)
            candidate_interval = int(candidate["timestep_index"])
            if nearest_interval is None:
                nearest_interval = candidate_interval
            if candidate_interval != nearest_interval:
                break
            rows.append(candidate)
        return {
            "rows": rows,
            "direction": direction_text,
            "atom_ids": normalized_atoms,
            "anchor_timestep_index": int(timestep_index),
            "nearest_timestep_index": nearest_interval,
            "candidate_limit": safe_limit,
            "candidate_limit_reached": len(records) >= safe_limit,
            "association_available": True,
            "time_basis": opened["time_basis"],
        }

    def reaction_summary(
        self,
        reactionevent_file: str,
        molecules_file: str,
        reaction_keys: Iterable[str],
    ) -> dict[str, dict[str, Any]]:
        opened = self.open_required(reactionevent_file, molecules_file)
        selected = sorted({str(key) for key in reaction_keys if str(key)})
        if not selected:
            return {}
        output: dict[str, dict[str, Any]] = {}
        connection = _readonly_connection(Path(opened["index_path"]))
        try:
            for start in range(0, len(selected), 500):
                chunk = selected[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                for key, total, matched, intervals in connection.execute(
                    f"""
                    SELECT reaction_key,total_events,matched_events,
                           distinct_intervals
                    FROM reaction_summary
                    WHERE reaction_key IN ({placeholders})
                    """,
                    chunk,
                ):
                    total_events = _strict_int(
                        total,
                        "reaction summary total_events",
                        minimum=0,
                    )
                    matched_events = _strict_int(
                        matched,
                        "reaction summary matched_events",
                        minimum=0,
                    )
                    distinct_intervals = _strict_int(
                        intervals,
                        "reaction summary distinct_intervals",
                        minimum=0,
                    )
                    if matched_events > total_events:
                        raise IndexInvalidError(
                            "Event evidence index reaction summary "
                            "matched_events is invalid"
                        )
                    if distinct_intervals > opened["available_intervals"]:
                        raise IndexInvalidError(
                            "Event evidence index reaction summary "
                            "distinct_intervals is invalid"
                        )
                    output[str(key)] = {
                        "reaction_key": str(key),
                        "total_events": total_events,
                        "matched_events": matched_events,
                        "distinct_intervals": distinct_intervals,
                        "available_intervals": opened[
                            "available_intervals"
                        ],
                    }
        except sqlite3.Error as exc:
            raise IndexInvalidError(
                f"Event evidence index reaction summary is corrupt: {exc}"
            ) from exc
        finally:
            connection.close()
        return output

    def clear(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
    ) -> dict[str, Any]:
        del molecules_file
        workspace = resolve_dataset_paths(
            os.path.abspath(reactionevent_file)
        )
        index_path = workspace.event_index
        try:
            index_path.resolve().relative_to(workspace.workspace_dir.resolve())
        except ValueError as exc:
            raise IndexInvalidError(
                "event evidence index path escapes Dataset Workspace"
            ) from exc

        targets = (
            index_path,
            Path(f"{index_path}.building"),
            Path(f"{index_path}.building.membership"),
            Path(f"{index_path}.building.membership.overlay-f"),
        )
        removed: list[str] = []
        released_bytes = 0
        with _exclusive_build_lock(index_path):
            for target in targets:
                if not target.is_file():
                    continue
                released_bytes += target.stat().st_size
                target.unlink()
                removed.append(str(target))
        return {
            "kind": "event",
            "index_path": str(index_path),
            "removed": removed,
            "released_bytes": released_bytes,
        }


EVENT_EVIDENCE_STORE = EventEvidenceStore()
