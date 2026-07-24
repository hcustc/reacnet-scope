"""Persistent evidence index for ReacNetGenerator-authored event outputs."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from .indexes import (
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    _exclusive_build_lock,
    _read_meta,
    _readonly_connection,
    _source_signature,
    dataset_id_for_source,
    event_evidence_index_path,
    resolve_dataset_paths,
)
from .rng_events import (
    MoleculeComponent,
    _changed_components,
    _trajectory_bond_id,
    canonical_reaction_key,
    load_event_rows,
    load_molecule_timeline,
)


EVENT_EVIDENCE_SCHEMA_VERSION = 1


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
        if (
            int(meta.get("schema_version", 0) or 0)
            != EVENT_EVIDENCE_SCHEMA_VERSION
        ):
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
                actual = int(actual or -1)
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
            event_count = int(meta.get("event_count", -1) or -1)
            reaction_types = int(meta.get("reaction_type_count", -1) or -1)
            if event_count < 0 or reaction_types < 0:
                raise IndexInvalidError("Event evidence index counts are invalid")
            actual_events = int(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            )
            actual_reactions = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reaction_summary"
                ).fetchone()[0]
            )
            if actual_events != event_count or actual_reactions != reaction_types:
                raise IndexInvalidError(
                    "Event evidence index row counts are inconsistent"
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
            "available_intervals": int(
                meta.get("available_intervals", 0) or 0
            ),
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
                details.get("event_count", meta.get("event_count", 0)) or 0
            ),
            "reaction_types": int(
                details.get(
                    "reaction_types", meta.get("reaction_type_count", 0)
                )
                or 0
            ),
            "available_intervals": int(
                details.get(
                    "available_intervals",
                    meta.get("available_intervals", 0),
                )
                or 0
            ),
            "updated_at_epoch": int(meta.get("updated_at_epoch", 0) or 0)
            or None,
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
            building_path.unlink(missing_ok=True)
            connection = self._connect_for_build(building_path)
            try:
                event_rows = load_event_rows(reaction_source[0])
                molecule_timesteps, molecule_timeline = load_molecule_timeline(
                    molecule_source[0]
                )
                frame_rows = {
                    index: molecule_timeline[timestep]
                    for index, timestep in enumerate(molecule_timesteps)
                }
                event_groups: dict[int, list[dict[str, Any]]] = defaultdict(
                    list
                )
                for event in event_rows:
                    event_groups[int(event["timestep_index"])].append(
                        dict(event)
                    )
                for timestep_index in sorted(event_groups):
                    if (
                        timestep_index < 0
                        or timestep_index + 1 >= len(molecule_timesteps)
                    ):
                        raise IndexInvalidError(
                            "Event interval is outside the molecules timeline"
                        )
                    pools: dict[
                        tuple[tuple[str, ...], tuple[str, ...]],
                        deque[MoleculeComponent],
                    ] = defaultdict(deque)
                    for component in _changed_components(
                        frame_rows[timestep_index],
                        frame_rows[timestep_index + 1],
                    ):
                        pools[component.key].append(component)
                    occurrences: dict[str, int] = defaultdict(int)
                    for event in event_groups[timestep_index]:
                        reaction_key = str(event["reaction_key_text"])
                        occurrences[reaction_key] += 1
                        component = (
                            pools[event["reaction_key"]].popleft()
                            if pools[event["reaction_key"]]
                            else None
                        )
                        rng_atom_ids = list(component.atom_ids) if component else []
                        atom_ids = [atom_id + 1 for atom_id in rng_atom_ids]
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
                        connection.execute(
                            """
                            INSERT INTO events(
                                event_id,reaction_key,source_row,timestep_index,
                                before_timestep,after_timestep,reactant_text,
                                product_text,atom_ids_json,reactant_bonds_json,
                                product_bonds_json,association_status,occurrence
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                _event_id(
                                    timestep_index,
                                    int(event["source_row"]),
                                    atom_ids,
                                ),
                                reaction_key,
                                int(event["source_row"]),
                                timestep_index,
                                int(molecule_timesteps[timestep_index]),
                                int(molecule_timesteps[timestep_index + 1]),
                                str(event["reactant"]),
                                str(event["product"]),
                                json.dumps(atom_ids, separators=(",", ":")),
                                json.dumps(
                                    reactant_bonds, separators=(",", ":")
                                ),
                                json.dumps(
                                    product_bonds, separators=(",", ":")
                                ),
                                (
                                    "matched"
                                    if component
                                    else "unresolved_hmm_timeline"
                                ),
                                occurrences[reaction_key],
                            ),
                        )
                connection.execute("DELETE FROM reaction_summary")
                connection.execute(
                    """
                    INSERT INTO reaction_summary(
                        reaction_key,total_events,matched_events,
                        distinct_intervals
                    )
                    SELECT reaction_key,COUNT(*),
                           SUM(CASE WHEN association_status='matched'
                               THEN 1 ELSE 0 END),
                           COUNT(DISTINCT timestep_index)
                    FROM events
                    GROUP BY reaction_key
                    """
                )
                event_count = int(
                    connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                )
                reaction_type_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM reaction_summary"
                    ).fetchone()[0]
                )
                _write_meta(
                    connection,
                    {
                        "schema_version": EVENT_EVIDENCE_SCHEMA_VERSION,
                        "build_state": "ready",
                        "dataset_id": dataset_id_for_source(reaction_source[0]),
                        "reactionevent_file": reaction_source[0],
                        "reactionevent_size": reaction_source[1],
                        "reactionevent_mtime_ns": reaction_source[2],
                        "molecules_file": molecule_source[0],
                        "molecules_size": molecule_source[1],
                        "molecules_mtime_ns": molecule_source[2],
                        "reactionevent_offset": reaction_source[1],
                        "molecules_offset": molecule_source[1],
                        "completed_interval": max(event_groups, default=-1),
                        "event_count": event_count,
                        "reaction_type_count": reaction_type_count,
                        "molecule_frame_count": len(molecule_timesteps),
                        "available_intervals": max(
                            len(molecule_timesteps) - 1, 0
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
        result["resumed"] = False
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
        finally:
            connection.close()
        rows: list[dict[str, Any]] = []
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
            atom_ids = [int(value) for value in json.loads(atom_ids_json)]
            reactant_bonds = [
                str(value) for value in json.loads(reactant_bonds_json)
            ]
            product_bonds = [
                str(value) for value in json.loads(product_bonds_json)
            ]
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
                    output[str(key)] = {
                        "reaction_key": str(key),
                        "total_events": int(total),
                        "matched_events": int(matched),
                        "distinct_intervals": int(intervals),
                        "available_intervals": int(
                            opened["available_intervals"]
                        ),
                    }
        finally:
            connection.close()
        return output


EVENT_EVIDENCE_STORE = EventEvidenceStore()
