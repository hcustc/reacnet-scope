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
    reaction_key,
)


EVENT_EVIDENCE_SCHEMA_VERSION = 1
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
        "association_status",
        "occurrence",
    },
    "reaction_summary": {
        "reaction_key",
        "total_events",
        "matched_events",
        "distinct_intervals",
    },
}


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
        molecules_file: str,
    ) -> tuple[tuple[str, int, int], tuple[str, int, int]]:
        return _source_signature(reactionevent_file), _source_signature(molecules_file)

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
            if not {"meta", "events", "reaction_summary"}.issubset(tables):
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
            "query_only": query_only,
        }

    def status(
        self,
        reactionevent_file: str,
        molecules_file: str,
    ) -> dict[str, Any]:
        index_path = self._expected_path(reactionevent_file)
        building_path = Path(f"{index_path}.building")
        reaction_path = Path(reactionevent_file)
        molecule_path = Path(molecules_file)
        if not reaction_path.is_file() or not molecule_path.is_file():
            return {
                "state": "missing_source",
                "index_path": str(index_path),
                "building_path": str(building_path),
                "reactionevent_file": str(reaction_path),
                "molecules_file": str(molecule_path),
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
                    str(reaction_path), str(molecule_path)
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
        return {
            "state": state,
            "index_path": str(index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.exists() else 0,
            "reactionevent_file": str(reaction_path.resolve()),
            "molecules_file": str(molecule_path.resolve()),
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
        molecules_file: str,
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

    def build(
        self,
        reactionevent_file: str,
        molecules_file: str,
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        reaction_source, molecule_source = self._source_pair(
            reactionevent_file, molecules_file
        )
        index_path = event_evidence_index_path(reaction_source[0])
        with _exclusive_build_lock(index_path):
            if index_path.is_file():
                return self.open_required(
                    reaction_source[0], molecule_source[0]
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
                            pools[component.key].append(component)
                        occurrences: dict[str, int] = defaultdict(int)
                        summary_counts: dict[str, list[int]] = defaultdict(
                            lambda: [0, 0]
                        )
                        for event in events:
                            normalized_key = str(
                                event["reaction_key_text"]
                            )
                            occurrences[normalized_key] += 1
                            component = (
                                pools[event["reaction_key"]].popleft()
                                if pools[event["reaction_key"]]
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
                            connection.execute(
                                """
                                INSERT INTO events(
                                    event_id,reaction_key,source_row,
                                    timestep_index,before_timestep,
                                    after_timestep,reactant_text,product_text,
                                    atom_ids_json,reactant_bonds_json,
                                    product_bonds_json,association_status,
                                    occurrence
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (
                                    _event_id(
                                        timestep_index,
                                        int(event["source_row"]),
                                        atom_ids,
                                    ),
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
                                    status,
                                    occurrences[normalized_key],
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
                """
                SELECT event_id,reaction_key,source_row,timestep_index,
                       before_timestep,after_timestep,reactant_text,
                       product_text,atom_ids_json,reactant_bonds_json,
                       product_bonds_json,association_status,occurrence
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
                    association_status,
                    occurrence,
                ) = record
                atom_values = _decode_json_list(
                    atom_ids_json, "atom_ids_json"
                )
                if any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in atom_values
                ):
                    raise IndexInvalidError(
                        "Event evidence index atom_ids_json payload "
                        "must contain integers"
                    )
                atom_ids = list(atom_values)
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
                        "Event evidence index bond payloads "
                        "must contain strings"
                    )
                reactant_bonds = list(reactant_values)
                product_bonds = list(product_values)
                if association_status not in {
                    "matched",
                    "unresolved_hmm_timeline",
                }:
                    raise IndexInvalidError(
                        "Event evidence index association_status is invalid"
                    )
                rows.append(
                    {
                        "event_index": page_index,
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
                        "reactant_bonds": ";".join(reactant_bonds),
                        "product_bonds": ";".join(product_bonds),
                        "association_status": str(association_status),
                        "event_class": (
                            "RNG 事件"
                            if association_status == "matched"
                            else "RNG 事件（原子关联不确定）"
                        ),
                    }
                )
        except (TypeError, ValueError) as exc:
            raise IndexInvalidError(
                f"Event evidence index payload is invalid: {exc}"
            ) from exc
        return {
            "rows": rows,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
            "evidence_status": "evidence_linked",
            "source_signatures": {
                "reactionevent": {
                    "path": os.path.abspath(reactionevent_file),
                    "size": os.path.getsize(reactionevent_file),
                    "mtime_ns": os.stat(reactionevent_file).st_mtime_ns,
                },
                "molecules": {
                    "path": os.path.abspath(molecules_file),
                    "size": os.path.getsize(molecules_file),
                    "mtime_ns": os.stat(molecules_file).st_mtime_ns,
                },
            },
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
        molecules_file: str,
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
        molecules_file: str,
        *,
        store: EventEvidenceStore = EVENT_EVIDENCE_STORE,
        opened: dict[str, Any] | None = None,
    ) -> None:
        self._reactionevent_file = os.path.abspath(reactionevent_file)
        self._molecules_file = os.path.abspath(molecules_file)
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
            "molecules": self._identity(self._molecules_file),
            "event_index": self._identity(self._index_path),
        }
        self._source_signatures = {
            "reactionevent": self._signature(self._reactionevent_file),
            "molecules": self._signature(self._molecules_file),
            "event_index": {
                **self._signature(self._index_path),
                "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
            },
        }

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
            "molecules": self._molecules_file,
            "event_index": self._index_path,
        }
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
