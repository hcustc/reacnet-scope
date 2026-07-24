"""Persistent streaming index for C/O/Cl species-composition trajectories."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from rng_tools.network import count_atoms_fast, formula_from_counts

from .indexes import (
    IndexBuildInProgressError,
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    _exclusive_build_lock,
    _read_meta,
    _readonly_connection,
    _source_signature,
    dataset_id_for_source,
    resolve_dataset_paths,
)


COMPOSITION_INDEX_SCHEMA_VERSION = 4
MARKER_FORMULAE = frozenset({"CO", "CO2", "HCl", "O2"})


def composition_index_path(species_file: str) -> Path:
    path, _size, _mtime_ns = _source_signature(species_file)
    return resolve_dataset_paths(path).cache_dir / "composition.sqlite3"


def _parse_species_line(raw_line: bytes) -> tuple[int, list[tuple[str, int]]] | None:
    text = raw_line.decode("utf-8", errors="ignore").strip()
    if not text.startswith("Timestep ") or ":" not in text:
        return None
    prefix, body = text.split(":", 1)
    try:
        timestep = int(prefix.split()[1])
    except (IndexError, ValueError):
        return None
    tokens = body.split()
    pairs: list[tuple[str, int]] = []
    cursor = 0
    while cursor + 1 < len(tokens):
        try:
            count = int(tokens[cursor + 1])
        except ValueError:
            cursor += 1
            continue
        pairs.append((tokens[cursor], count))
        cursor += 2
    return timestep, pairs


def _sample_values(values: list[int], limit: int) -> list[int]:
    if len(values) <= limit:
        return values
    stride = (len(values) - 1) / float(limit - 1)
    indices = sorted({min(len(values) - 1, int(round(index * stride))) for index in range(limit)})
    return [values[index] for index in indices]


class SpeciesCompositionStore:
    """Offline builder and read-only sampler for composition trajectories."""

    def _connect_for_build(self, target: Path) -> sqlite3.Connection:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(target))
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS timepoints(
                timestep INTEGER PRIMARY KEY,
                source_offset INTEGER NOT NULL,
                composition_json TEXT NOT NULL,
                marker_json TEXT NOT NULL,
                parent_count INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS species_summary(
                smiles TEXT PRIMARY KEY,
                formula TEXT NOT NULL,
                carbon INTEGER NOT NULL,
                oxygen INTEGER NOT NULL,
                chlorine INTEGER NOT NULL,
                total_count INTEGER NOT NULL,
                peak_count INTEGER NOT NULL,
                peak_timestep INTEGER NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS species_summary_elements "
            "ON species_summary(carbon,oxygen,chlorine,peak_count DESC)"
        )
        return connection

    def _flush_species_stats(
        self,
        connection: sqlite3.Connection,
        species_stats: dict[str, tuple[str, int, int, int, int, int, int]],
    ) -> None:
        if not species_stats:
            return
        connection.executemany(
            """INSERT INTO species_summary(
                smiles,formula,carbon,oxygen,chlorine,total_count,peak_count,peak_timestep
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(smiles) DO UPDATE SET
                total_count=species_summary.total_count+excluded.total_count,
                peak_timestep=CASE
                    WHEN excluded.peak_count>species_summary.peak_count
                    THEN excluded.peak_timestep ELSE species_summary.peak_timestep END,
                peak_count=MAX(species_summary.peak_count,excluded.peak_count)""",
            [
                (smiles, *values)
                for smiles, values in species_stats.items()
            ],
        )
        species_stats.clear()

    def _write_meta(
        self,
        connection: sqlite3.Connection,
        *,
        path: str,
        size: int,
        mtime_ns: int,
        source_offset: int,
        timepoint_count: int,
        unique_species: int,
        state: str,
        parent_smiles: str,
    ) -> None:
        values = {
            "schema_version": COMPOSITION_INDEX_SCHEMA_VERSION,
            "build_state": state,
            "source_file": path,
            "source_size": size,
            "source_mtime_ns": mtime_ns,
            "source_offset": source_offset,
            "dataset_id": dataset_id_for_source(path),
            "timepoint_count": timepoint_count,
            "unique_species": unique_species,
            "parent_smiles": parent_smiles,
            "updated_at_epoch": int(time.time()),
        }
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(key, str(value)) for key, value in values.items()],
        )
        connection.commit()

    def status(self, species_file: str) -> dict[str, Any]:
        path, size, _mtime_ns = _source_signature(species_file)
        index_path = composition_index_path(path)
        building_path = Path(f"{index_path}.building")
        active = index_path if index_path.is_file() else building_path
        meta: dict[str, str] = {}
        if active.is_file():
            try:
                connection = _readonly_connection(active)
                try:
                    meta = _read_meta(connection)
                finally:
                    connection.close()
            except (IndexNotReadyError, sqlite3.Error):
                meta = {}
        state = "ready" if index_path.is_file() else ("building" if building_path.is_file() else "missing")
        if state == "ready":
            try:
                self.open_required(path)
            except IndexStaleError:
                state = "stale"
            except IndexNotReadyError:
                state = "invalid"
        offset = int(meta.get("source_offset", 0) or 0)
        return {
            "state": state,
            "species_file": path,
            "species_size": size,
            "index_path": str(index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.is_file() else 0,
            "source_offset": offset,
            "progress": min(max(offset / max(size, 1), 0.0), 1.0),
            "timepoints": int(meta.get("timepoint_count", 0) or 0),
            "unique_species": int(meta.get("unique_species", 0) or 0),
            "updated_at_epoch": int(meta.get("updated_at_epoch", 0) or 0) or None,
        }

    def open_required(self, species_file: str) -> dict[str, Any]:
        path, size, mtime_ns = _source_signature(species_file)
        index_path = composition_index_path(path)
        if not index_path.is_file():
            building_path = Path(f"{index_path}.building")
            if building_path.is_file():
                raise IndexBuildInProgressError(
                    f"Species composition index is building: {building_path}"
                )
            raise IndexNotReadyError(
                "Species composition index is not ready; run "
                f"`reacnet-scope-prepare {Path(path).parent} --composition-only`"
            )
        connection = _readonly_connection(index_path)
        try:
            meta = _read_meta(connection)
            if int(meta.get("schema_version", 0) or 0) != COMPOSITION_INDEX_SCHEMA_VERSION:
                raise IndexInvalidError("Species composition index schema is incompatible")
            if meta.get("build_state") != "ready":
                raise IndexInvalidError("Species composition index is incomplete")
            if meta.get("source_file") != path:
                raise IndexInvalidError("Species composition index source path does not match")
            if int(meta.get("source_size", -1) or -1) != size or int(meta.get("source_mtime_ns", -1) or -1) != mtime_ns:
                raise IndexStaleError(
                    f"Species composition index is stale; rerun reacnet-scope-prepare for: {path}"
                )
            actual = int(connection.execute("SELECT COUNT(*) FROM timepoints").fetchone()[0])
            if actual != int(meta.get("timepoint_count", -1) or -1):
                raise IndexInvalidError("Species composition index row count is inconsistent")
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Species composition index is corrupt: {exc}") from exc
        finally:
            connection.close()
        return {
            "index_path": str(index_path),
            "species_file": path,
            "species_size": size,
            "timepoints": int(meta.get("timepoint_count", 0) or 0),
            "unique_species": int(meta.get("unique_species", 0) or 0),
            "parent_smiles": str(meta.get("parent_smiles") or ""),
            "index_state": "cached_disk",
        }

    def build(self, species_file: str, *, progress_callback: Any = None) -> dict[str, Any]:
        index_path = composition_index_path(species_file)
        with _exclusive_build_lock(index_path):
            path, size, mtime_ns = _source_signature(species_file)
            if index_path.is_file():
                try:
                    return self.open_required(path)
                except (IndexInvalidError, IndexStaleError):
                    # Schema upgrades only invalidate derived cache data.  Rebuild
                    # automatically so the normal --composition-only command stays
                    # sufficient after an application update.
                    index_path.unlink()
            building_path = Path(f"{index_path}.building")
            connection = self._connect_for_build(building_path)
            existing = {str(key): str(value) for key, value in connection.execute("SELECT key,value FROM meta")}
            compatible = bool(existing) and (
                int(existing.get("schema_version", 0) or 0) == COMPOSITION_INDEX_SCHEMA_VERSION
                and existing.get("build_state") == "building"
                and existing.get("source_file") == path
                and int(existing.get("source_size", -1) or -1) == size
                and int(existing.get("source_mtime_ns", -1) or -1) == mtime_ns
            )
            if existing and not compatible:
                connection.close()
                building_path.unlink(missing_ok=True)
                connection = self._connect_for_build(building_path)
                existing = {}
            offset = int(existing.get("source_offset", 0) or 0) if compatible else 0
            timepoints = int(existing.get("timepoint_count", 0) or 0) if compatible else 0
            species_cache: dict[str, tuple[int, int, int, str]] = {}
            species_stats: dict[str, tuple[str, int, int, int, int, int, int]] = {}
            batch: list[tuple[int, int, str, str, int]] = []
            # Kept empty for schema compatibility. Reference species are a
            # user-selected query concern, never something inferred while
            # building a dataset-wide composition index.
            parent_smiles = ""
            last_checkpoint = offset
            last_emit = 0.0
            self._write_meta(
                connection, path=path, size=size, mtime_ns=mtime_ns,
                source_offset=offset, timepoint_count=timepoints,
                unique_species=0, state="building", parent_smiles=parent_smiles,
            )
            try:
                with open(path, "rb") as source:
                    source.seek(offset)
                    for raw_line in source:
                        line_offset = offset
                        offset += len(raw_line)
                        parsed = _parse_species_line(raw_line)
                        if parsed is None:
                            continue
                        timestep, pairs = parsed
                        composition: dict[str, int] = {}
                        markers: dict[str, int] = {}
                        for smiles, count in pairs:
                            cached = species_cache.get(smiles)
                            if cached is None:
                                atom_counts = count_atoms_fast(smiles)
                                formula = formula_from_counts(atom_counts)
                                cached = (
                                    int(atom_counts.get("C", 0)),
                                    int(atom_counts.get("O", 0)),
                                    int(atom_counts.get("Cl", 0)),
                                    formula,
                                )
                                species_cache[smiles] = cached
                            carbon, oxygen, chlorine, formula = cached
                            previous = species_stats.get(smiles)
                            if previous is None:
                                species_stats[smiles] = (
                                    formula,
                                    carbon,
                                    oxygen,
                                    chlorine,
                                    int(count),
                                    int(count),
                                    int(timestep),
                                )
                            else:
                                peak_count = int(previous[5])
                                peak_timestep = int(previous[6])
                                if int(count) > peak_count:
                                    peak_count = int(count)
                                    peak_timestep = int(timestep)
                                species_stats[smiles] = (
                                    previous[0],
                                    int(previous[1]),
                                    int(previous[2]),
                                    int(previous[3]),
                                    int(previous[4]) + int(count),
                                    peak_count,
                                    peak_timestep,
                                )
                            key = f"{carbon},{oxygen},{chlorine}"
                            composition[key] = composition.get(key, 0) + int(count)
                            if formula in MARKER_FORMULAE:
                                markers[formula] = markers.get(formula, 0) + int(count)
                        batch.append((
                            int(timestep),
                            int(line_offset),
                            json.dumps(composition, separators=(",", ":"), sort_keys=True),
                            json.dumps(markers, separators=(",", ":"), sort_keys=True),
                            0,
                        ))
                        timepoints += 1
                        if len(batch) >= 1000:
                            connection.executemany(
                                "INSERT OR REPLACE INTO timepoints VALUES(?,?,?,?,?)", batch
                            )
                            batch.clear()
                        if offset - last_checkpoint >= 64 * 1024 * 1024:
                            if batch:
                                connection.executemany(
                                    "INSERT OR REPLACE INTO timepoints VALUES(?,?,?,?,?)", batch
                                )
                                batch.clear()
                            self._flush_species_stats(connection, species_stats)
                            indexed_species = int(
                                connection.execute("SELECT COUNT(*) FROM species_summary").fetchone()[0]
                            )
                            self._write_meta(
                                connection, path=path, size=size, mtime_ns=mtime_ns,
                                source_offset=offset, timepoint_count=timepoints,
                                unique_species=indexed_species, state="building",
                                parent_smiles=parent_smiles,
                            )
                            last_checkpoint = offset
                        now = time.monotonic()
                        if progress_callback and now - last_emit >= 1.0:
                            progress_callback({
                                "progress": min(offset / max(size, 1), 0.99),
                                "phase": "indexing_composition",
                                "message": f"Building C/O/Cl composition index: {offset / max(size, 1) * 100:.1f}%",
                                "timepoints": timepoints,
                                "unique_species": len(species_cache),
                            })
                            last_emit = now
                if batch:
                    connection.executemany(
                        "INSERT OR REPLACE INTO timepoints VALUES(?,?,?,?,?)", batch
                    )
                self._flush_species_stats(connection, species_stats)
                indexed_species = int(
                    connection.execute("SELECT COUNT(*) FROM species_summary").fetchone()[0]
                )
                self._write_meta(
                    connection, path=path, size=size, mtime_ns=mtime_ns,
                    source_offset=size, timepoint_count=timepoints,
                    unique_species=indexed_species, state="ready",
                    parent_smiles=parent_smiles,
                )
            finally:
                connection.close()
            os.replace(building_path, index_path)
            if progress_callback:
                progress_callback({"progress": 1.0, "phase": "completed", "message": "C/O/Cl composition index ready"})
            result = self.open_required(path)
            result["index_state"] = "built"
            return result

    def query(
        self,
        species_file: str,
        *,
        max_points: int = 1200,
        max_carbon: int | None = 6,
        max_oxygen: int | None = 4,
        chlorine_mode: str = "binary",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        meta = self.open_required(species_file)
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            timesteps = [int(row[0]) for row in connection.execute("SELECT timestep FROM timepoints ORDER BY timestep")]
            sampled = _sample_values(timesteps, max(2, min(int(max_points), 4000)))
            rows: list[dict[str, Any]] = []
            marker_rows: list[dict[str, Any]] = []
            parent_rows: list[dict[str, int]] = []
            for offset in range(0, len(sampled), 500):
                selected = sampled[offset : offset + 500]
                placeholders = ",".join("?" for _ in selected)
                for timestep, _source_offset, composition_json, marker_json, parent_count in connection.execute(
                    f"SELECT timestep,source_offset,composition_json,marker_json,parent_count FROM timepoints "
                    f"WHERE timestep IN ({placeholders}) ORDER BY timestep",
                    selected,
                ):
                    parent_rows.append(
                        {"timestep": int(timestep), "count": int(parent_count)}
                    )
                    merged: dict[tuple[int, int, int], int] = {}
                    for key, count in json.loads(str(composition_json)).items():
                        carbon, oxygen, chlorine = (int(value) for value in key.split(","))
                        if max_carbon is not None and carbon > max_carbon:
                            continue
                        if max_oxygen is not None and oxygen > max_oxygen:
                            continue
                        chlorine_value = 1 if chlorine_mode == "binary" and chlorine > 0 else chlorine
                        merged_key = (carbon, oxygen, chlorine_value)
                        merged[merged_key] = merged.get(merged_key, 0) + int(count)
                    for (carbon, oxygen, chlorine), count in merged.items():
                        rows.append({
                            "timestep": int(timestep),
                            "carbon": carbon,
                            "oxygen": oxygen,
                            "chlorine": chlorine,
                            "count": count,
                            "group": f"C{carbon}O{oxygen}Cl{chlorine}",
                        })
                    for formula, count in json.loads(str(marker_json)).items():
                        marker_rows.append({
                            "timestep": int(timestep),
                            "formula": str(formula),
                            "count": int(count),
                        })
        finally:
            connection.close()
        return {
            "rows": rows,
            "marker_rows": marker_rows,
            "parent_rows": parent_rows,
            "timesteps": sampled,
            "meta": {
                **meta,
                "source_timepoints": len(timesteps),
                "sampled_timepoints": len(sampled),
                "query_seconds": round(time.perf_counter() - started, 4),
                "chlorine_mode": chlorine_mode,
                "max_carbon": max_carbon,
                "max_oxygen": max_oxygen,
                "sampling_stride": max(1, math.ceil(len(timesteps) / max(len(sampled), 1))),
            },
        }

    def snapshot(self, species_file: str, timestep: int) -> dict[str, Any]:
        """Read one indexed timestep from the source without scanning the file."""
        meta = self.open_required(species_file)
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            row = connection.execute(
                "SELECT source_offset FROM timepoints WHERE timestep=?", (int(timestep),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ValueError(f"Timestep {timestep} is not present in the composition index")
        with open(str(meta["species_file"]), "rb") as source:
            source.seek(int(row[0]))
            parsed = _parse_species_line(source.readline())
        if parsed is None or int(parsed[0]) != int(timestep):
            raise IndexInvalidError("Species composition index offset is inconsistent")
        records: list[dict[str, Any]] = []
        for smiles, count in parsed[1]:
            atom_counts = count_atoms_fast(smiles)
            records.append(
                {
                    "smiles": smiles,
                    "count": int(count),
                    "carbon": int(atom_counts.get("C", 0)),
                    "oxygen": int(atom_counts.get("O", 0)),
                    "chlorine": int(atom_counts.get("Cl", 0)),
                    "formula": formula_from_counts(atom_counts),
                }
            )
        return {"timestep": int(timestep), "records": records}

    def species_count_series(
        self, species_file: str, timesteps: list[int], smiles: str
    ) -> dict[int, int]:
        """Return an exact-SMILES abundance series at indexed timesteps."""
        path, size, mtime_ns = _source_signature(species_file)
        requested = sorted({int(value) for value in timesteps})
        if not requested:
            return {}
        return dict(
            self._cached_species_count_series(
                path,
                size,
                mtime_ns,
                tuple(requested),
                str(smiles),
            )
        )

    @lru_cache(maxsize=32)
    def _cached_species_count_series(
        self,
        species_file: str,
        source_size: int,
        source_mtime_ns: int,
        requested: tuple[int, ...],
        smiles: str,
    ) -> tuple[tuple[int, int], ...]:
        """Read a bounded exact-species series and cache it by source revision."""
        del source_size, source_mtime_ns
        meta = self.open_required(species_file)
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            offsets: dict[int, int] = {}
            for start in range(0, len(requested), 500):
                selected = requested[start : start + 500]
                placeholders = ",".join("?" for _ in selected)
                offsets.update(
                    {
                        int(timestep): int(offset)
                        for timestep, offset in connection.execute(
                            f"SELECT timestep,source_offset FROM timepoints WHERE timestep IN ({placeholders})",
                            selected,
                        )
                    }
                )
        finally:
            connection.close()
        counts = {timestep: 0 for timestep in requested}
        with open(str(meta["species_file"]), "rb") as source:
            for timestep in requested:
                offset = offsets.get(timestep)
                if offset is None:
                    continue
                source.seek(offset)
                parsed = _parse_species_line(source.readline())
                if parsed is None:
                    continue
                counts[timestep] = sum(int(count) for item, count in parsed[1] if item == smiles)
        return tuple(counts.items())

    def query_species_summary(
        self,
        species_file: str,
        *,
        carbon: int,
        current_timestep: int,
        chlorine_state: str = "all",
        oxygen_state: str = "all",
        only_smiles: str = "",
        exclude_smiles: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return exact species peaks plus abundance at one indexed timestep."""
        started = time.perf_counter()
        meta = self.open_required(species_file)
        snapshot = self.snapshot(species_file, current_timestep)
        current_counts = {
            str(record["smiles"]): int(record["count"])
            for record in snapshot["records"]
        }
        clauses = ["carbon=?"]
        params: list[Any] = [int(carbon)]
        if chlorine_state == "chlorinated":
            clauses.append("chlorine>0")
        elif chlorine_state == "unchlorinated":
            clauses.append("chlorine=0")
        if oxygen_state == "oxygenated":
            clauses.append("oxygen>0")
        elif oxygen_state == "unoxygenated":
            clauses.append("oxygen=0")
        if only_smiles:
            clauses.append("smiles=?")
            params.append(str(only_smiles))
        if exclude_smiles:
            clauses.append("smiles<>?")
            params.append(str(exclude_smiles))
        params.append(max(1, min(int(limit), 500)))
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            rows = [
                {
                    "formula": str(formula),
                    "smiles": str(smiles),
                    "current_count": int(current_counts.get(str(smiles), 0)),
                    "peak_count": int(peak_count),
                    "peak_timestep": int(peak_timestep),
                }
                for smiles, formula, peak_count, peak_timestep in connection.execute(
                    "SELECT smiles,formula,peak_count,peak_timestep "
                    f"FROM species_summary WHERE {' AND '.join(clauses)} "
                    "ORDER BY peak_count DESC, total_count DESC, smiles LIMIT ?",
                    params,
                )
            ]
        finally:
            connection.close()
        return {
            "rows": rows,
            "timestep": int(current_timestep),
            "query_seconds": round(time.perf_counter() - started, 4),
        }

    def clear(self, species_file: str) -> list[str]:
        index_path = composition_index_path(species_file)
        targets = (index_path, Path(f"{index_path}.building"))
        removed: list[str] = []
        with _exclusive_build_lock(index_path):
            for target in targets:
                if target.is_file():
                    target.unlink()
                    removed.append(str(target))
        return removed


SPECIES_COMPOSITION_STORE = SpeciesCompositionStore()


__all__ = [
    "COMPOSITION_INDEX_SCHEMA_VERSION",
    "MARKER_FORMULAE",
    "SPECIES_COMPOSITION_STORE",
    "SpeciesCompositionStore",
    "composition_index_path",
]
