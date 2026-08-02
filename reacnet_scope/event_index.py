"""Persistent evidence index for ReacNetGenerator-authored event outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from .indexes import (
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    _exclusive_build_lock,
    _cache_root,
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


EVENT_EVIDENCE_SCHEMA_VERSION = 3
EVENT_ASSOCIATION_ALGORITHM_VERSION = 2


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

_EVENT_SELECT_COLUMNS = """
    event_id,reaction_key,source_row,timestep_index,
    before_timestep,after_timestep,reactant_text,
    product_text,atom_ids_json,reactant_bonds_json,
    product_bonds_json,reactant_participants_json,
    product_participants_json,association_status,occurrence
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
        _stored_key,
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


def _event_id(timestep_index: int, source_row: int, atom_ids: list[int]) -> str:
    digest = hashlib.sha1(
        (
            f"{timestep_index}|{source_row}|"
            f"{','.join(map(str, atom_ids))}"
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
        return resolve_dataset_paths(os.path.abspath(reactionevent_file)).event_index

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
        if molecule_path and _strict_int(
            meta.get("association_algorithm_version"),
            "association_algorithm_version",
        ) != EVENT_ASSOCIATION_ALGORITHM_VERSION:
            raise IndexInvalidError(
                "Event evidence index association algorithm is incompatible"
            )
        if meta.get("build_state") != "ready":
            raise IndexInvalidError("Event evidence index is not complete")
        checks = (
            ("reactionevent_file", reaction_path, "reaction-event path"),
            ("reactionevent_size", reaction_size, "reaction-event size"),
            (
                "reactionevent_mtime_ns",
                reaction_mtime_ns,
                "reaction-event modification time",
            ),
            ("molecules_file", molecule_path, "molecules path"),
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
        if meta.get("dataset_id") != dataset_id_for_source(reaction_path):
            raise IndexInvalidError("Event evidence index dataset id is invalid")

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
            "time_basis": meta.get("time_basis", "physical_timestep"),
            "query_only": query_only,
        }

    def status(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
    ) -> dict[str, Any]:
        index_path = self._expected_path(reactionevent_file)
        building_path = Path(f"{index_path}.building")
        reaction_path = Path(reactionevent_file)
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
            "cache_dir": str(index_path.parent),
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
                f"reacnet-scope-prepare {Path(reaction_source[0]).parent} "
                "--event-only"
            )
        return self._open_validated(
            index_path, reaction_source, molecule_source
        )

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
                    event_id = _event_id(interval, source_row, [])
                    occurrence = interval_summary[normalized_key][0] + 1
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

    def build(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        reaction_source, molecule_source = self._source_pair(
            reactionevent_file, molecules_file
        )
        index_path = event_evidence_index_path(reaction_source[0])
        with _exclusive_build_lock(index_path):
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
                            "dataset_id": dataset_id_for_source(
                                reaction_source[0]
                            ),
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
                                int(event["source_row"]),
                                atom_ids,
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
                        _write_meta(
                            connection,
                            {
                                "schema_version": (
                                    EVENT_EVIDENCE_SCHEMA_VERSION
                                ),
                                "build_state": "building",
                                "dataset_id": dataset_id_for_source(
                                    reaction_source[0]
                                ),
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
                                "reactionevent_offset": next_event_offset,
                                "molecules_offset": after_frame[3],
                                "completed_interval": timestep_index,
                                "last_source_row": next_source_row,
                                "molecule_frame_index": after_frame[0],
                                "previous_molecule_timestep": before_frame[1],
                                "event_count": event_count,
                                "reaction_type_count": reaction_type_count,
                                "updated_at_epoch": int(time.time()),
                            },
                        )
                        connection.commit()
                        event_offset = next_event_offset
                        completed_interval = timestep_index
                        last_source_row = next_source_row
                        current_molecule = after_frame
                        molecule_frame_index = after_frame[0]
                        previous_molecule_timestep = before_frame[1]
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
                                        f"interval {timestep_index}"
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
        source_signatures = {
            "reactionevent": {
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
        index_path = resolve_dataset_paths(
            os.path.abspath(reactionevent_file)
        ).event_index
        cache_root = _cache_root().resolve()
        try:
            index_path.resolve().relative_to(cache_root)
        except ValueError as exc:
            raise IndexInvalidError(
                "event evidence index path escapes REACNET_SCOPE_CACHE_DIR"
            ) from exc

        targets = (index_path, Path(f"{index_path}.building"))
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


class EventIndexEvidenceProvider:
    """Adapt one ready event index to the candidate-path evidence protocol."""

    def __init__(
        self,
        reactionevent_file: str,
        molecules_file: str = "",
        *,
        store: EventEvidenceStore = EVENT_EVIDENCE_STORE,
        opened: dict[str, Any] | None = None,
    ) -> None:
        self._reactionevent_file = os.path.abspath(reactionevent_file)
        self._molecules_file = (
            os.path.abspath(molecules_file)
            if str(molecules_file or "").strip()
            else ""
        )
        self._store = store
        self._opened = dict(
            opened
            if opened is not None
            else store.open_required(
                self._reactionevent_file,
                self._molecules_file,
            )
        )
        self._index_path = os.path.abspath(str(self._opened["index_path"]))
        self._source_identities = {
            "reactionevent": self._identity(self._reactionevent_file),
            "event_index": self._identity(self._index_path),
        }
        if self._molecules_file:
            self._source_identities["molecules"] = self._identity(
                self._molecules_file
            )
        self._source_signatures = {
            "reactionevent": self._signature(self._reactionevent_file),
            "event_index": {
                **self._signature(self._index_path),
                "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
            },
        }
        if self._molecules_file:
            self._source_signatures["molecules"] = self._signature(
                self._molecules_file
            )

    @staticmethod
    def _identity(path_text: str) -> tuple[int, int, int, int] | None:
        try:
            stat = os.stat(path_text)
        except OSError:
            return None
        return (
            int(stat.st_dev),
            int(stat.st_ino),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )

    @staticmethod
    def _signature(path_text: str) -> dict[str, Any]:
        path = os.path.abspath(path_text)
        try:
            stat = os.stat(path)
        except OSError:
            return {"path": path}
        return {
            "path": path,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    @property
    def source_signatures(self) -> dict[str, dict[str, Any]]:
        return {
            name: dict(signature)
            for name, signature in self._source_signatures.items()
        }

    def assert_current(self) -> None:
        """Reject source replacement instead of mixing query snapshots."""
        paths = {
            "reactionevent": self._reactionevent_file,
            "event_index": self._index_path,
        }
        if self._molecules_file:
            paths["molecules"] = self._molecules_file
        for name, path in paths.items():
            expected = self._source_identities[name]
            if expected is None:
                continue
            actual = self._identity(path)
            if actual != expected:
                error_type = (
                    IndexInvalidError
                    if name == "event_index"
                    else IndexStaleError
                )
                raise error_type(
                    f"Event evidence {name} changed during pathway query"
                )

    def reaction_summaries(
        self,
        reaction_keys: Iterable[str],
    ) -> dict[str, dict[str, Any]]:
        selected = tuple(
            sorted({str(key) for key in reaction_keys if str(key)})
        )
        if not selected:
            return {}
        self.assert_current()
        try:
            found = self._store.reaction_summary(
                self._reactionevent_file,
                self._molecules_file,
                selected,
            )
        except IndexNotReadyError:
            raise
        except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
            raise IndexInvalidError(
                f"Event evidence batch query is invalid: {exc}"
            ) from exc
        self.assert_current()
        available_intervals = _strict_int(
            self._opened.get("available_intervals"),
            "available_intervals",
            minimum=0,
        )
        summaries: dict[str, dict[str, Any]] = {}
        for key in selected:
            summaries[key] = {
                "reaction_key": key,
                "total_events": 0,
                "matched_events": 0,
                "distinct_intervals": 0,
                "available_intervals": available_intervals,
                "source_references": (self._index_path,),
                **dict(found.get(key, {})),
            }
            total_events = _strict_int(
                summaries[key].get("total_events"),
                "reaction summary total_events",
                minimum=0,
            )
            matched_events = _strict_int(
                summaries[key].get("matched_events"),
                "reaction summary matched_events",
                minimum=0,
            )
            distinct_intervals = _strict_int(
                summaries[key].get("distinct_intervals"),
                "reaction summary distinct_intervals",
                minimum=0,
            )
            if matched_events > total_events:
                raise IndexInvalidError(
                    "Event evidence index reaction summary "
                    "matched_events is invalid"
                )
            if distinct_intervals > available_intervals:
                raise IndexInvalidError(
                    "Event evidence index reaction summary "
                    "distinct_intervals is invalid"
                )
            summaries[key].update(
                total_events=total_events,
                matched_events=matched_events,
                distinct_intervals=distinct_intervals,
                available_intervals=available_intervals,
            )
            summaries[key]["source_references"] = (self._index_path,)
        return summaries
