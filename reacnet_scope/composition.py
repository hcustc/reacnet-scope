"""Persistent streaming index for Element Distribution Evolution."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from reacnet_scope.network import count_atoms_fast, formula_from_counts

from .indexes import (
    IndexBuildInProgressError,
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    _exclusive_build_lock,
    _assert_source_unchanged,
    _read_meta,
    _readonly_connection,
    _source_signature,
    dataset_id_for_source,
    resolve_dataset_paths,
)


COMPOSITION_INDEX_SCHEMA_VERSION = 6
_COMPOSITION_REQUIRED_TABLE_COLUMNS = {
    "meta": {"key", "value"},
    "timepoints": {
        "timestep",
        "source_offset",
        "distribution_json",
        "species_counts_json",
    },
    "species_summary": {
        "smiles",
        "formula",
        "elements_json",
        "total_count",
        "peak_count",
        "peak_timestep",
        "start_count",
        "end_count",
        "fwhm_longest_points",
    },
}


def _available_elements(meta: dict[str, str]) -> list[str]:
    try:
        values = json.loads(meta.get("available_elements", "[]"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise IndexInvalidError(
            "Element Distribution available elements metadata is invalid"
        ) from exc
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Z][a-z]?", value)
        for value in values
    ):
        raise IndexInvalidError(
            "Element Distribution available elements metadata is invalid"
        )
    return sorted(set(values))


def composition_index_path(species_file: str) -> Path:
    path, _size, _mtime_ns = _source_signature(species_file)
    return (
        resolve_dataset_paths(path, persist_identity=False).workspace_dir
        / "element-distribution.sqlite3"
    )


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


def matches_element_filters(
    atom_counts: dict[str, int],
    filters: dict[str, dict[str, Any]],
) -> bool:
    """Apply validated generic element-count filters."""
    for element, rule in filters.items():
        if not re.fullmatch(r"[A-Z][a-z]?", str(element)):
            raise ValueError(f"invalid element symbol: {element}")
        mode = str((rule or {}).get("mode") or "all")
        if mode not in {"all", "present", "absent", "range"}:
            raise ValueError(f"invalid element filter mode: {mode}")
        minimum = (rule or {}).get("min")
        maximum = (rule or {}).get("max")
        if mode != "range" and (minimum is not None or maximum is not None):
            raise ValueError(f"{mode} filter does not accept range bounds")
        minimum_value = int(minimum) if minimum is not None else None
        maximum_value = int(maximum) if maximum is not None else None
        if minimum_value is not None and minimum_value < 0:
            raise ValueError("element filter minimum must be non-negative")
        if maximum_value is not None and maximum_value < 0:
            raise ValueError("element filter maximum must be non-negative")
        if (
            minimum_value is not None
            and maximum_value is not None
            and minimum_value > maximum_value
        ):
            raise ValueError("element filter minimum exceeds maximum")
        value = int(atom_counts.get(str(element), 0))
        if mode == "present" and value <= 0:
            return False
        if mode == "absent" and value != 0:
            return False
        if minimum_value is not None and value < minimum_value:
            return False
        if maximum_value is not None and value > maximum_value:
            return False
    return True


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
                distribution_json TEXT NOT NULL,
                species_counts_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS species_summary(
                smiles TEXT PRIMARY KEY,
                formula TEXT NOT NULL,
                elements_json TEXT NOT NULL,
                total_count INTEGER NOT NULL,
                peak_count INTEGER NOT NULL,
                peak_timestep INTEGER NOT NULL,
                start_count INTEGER NOT NULL,
                end_count INTEGER NOT NULL,
                fwhm_longest_points INTEGER NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS species_summary_peaks "
            "ON species_summary(peak_count DESC,total_count DESC)"
        )
        return connection

    def _flush_species_stats(
        self,
        connection: sqlite3.Connection,
        species_stats: dict[str, tuple[str, str, int, int, int, int]],
    ) -> None:
        if not species_stats:
            return
        connection.executemany(
            """INSERT INTO species_summary(
                smiles,formula,elements_json,total_count,peak_count,peak_timestep,
                start_count,end_count,fwhm_longest_points
            ) VALUES(?,?,?,?,?,?,?,0,0)
            ON CONFLICT(smiles) DO UPDATE SET
                total_count=species_summary.total_count+excluded.total_count,
                start_count=MAX(species_summary.start_count,excluded.start_count),
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

    def _finalize_timeline_stats(
        self,
        connection: sqlite3.Connection,
        *,
        final_counts: dict[str, int],
        progress_callback: Any = None,
    ) -> None:
        """Derive end abundance and FWHM runs from prepared SQLite rows."""
        connection.execute("UPDATE species_summary SET end_count=0")
        connection.executemany(
            "UPDATE species_summary SET end_count=? WHERE smiles=?",
            [(int(count), smiles) for smiles, count in final_counts.items()],
        )
        thresholds = {
            str(smiles): float(peak_count) * 0.5
            for smiles, peak_count in connection.execute(
                "SELECT smiles,peak_count FROM species_summary WHERE peak_count>0"
            )
        }
        active: dict[str, int] = {}
        longest: dict[str, int] = {}
        row_count = int(
            connection.execute("SELECT COUNT(*) FROM timepoints").fetchone()[0]
        )
        for frame_index, (encoded,) in enumerate(
            connection.execute(
                "SELECT species_counts_json FROM timepoints ORDER BY timestep"
            ),
            1,
        ):
            counts = {
                str(smiles): int(count)
                for smiles, count in json.loads(str(encoded)).items()
            }
            current = {
                smiles
                for smiles, count in counts.items()
                if count >= thresholds.get(smiles, float("inf"))
            }
            for smiles in tuple(active):
                if smiles not in current:
                    longest[smiles] = max(
                        longest.get(smiles, 0), active.pop(smiles)
                    )
            for smiles in current:
                active[smiles] = active.get(smiles, 0) + 1
            if progress_callback and (
                frame_index == row_count or frame_index % 5000 == 0
            ):
                progress_callback(
                    {
                        "progress": 0.95 + 0.04 * frame_index / max(row_count, 1),
                        "phase": "summarizing_species",
                        "message": "Finalizing indexed Species abundance summaries",
                        "timepoints": frame_index,
                    }
                )
        for smiles, run in active.items():
            longest[smiles] = max(longest.get(smiles, 0), run)
        connection.executemany(
            "UPDATE species_summary SET fwhm_longest_points=? WHERE smiles=?",
            [(int(points), smiles) for smiles, points in longest.items()],
        )
        connection.commit()

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
        available_elements: set[str],
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
            "available_elements": json.dumps(
                sorted(available_elements),
                separators=(",", ":"),
            ),
            "updated_at_epoch": int(time.time()),
        }
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(key, str(value)) for key, value in values.items()],
        )
        connection.commit()

    def _validate_metadata(
        self,
        index_path: Path,
        *,
        path: str,
        size: int,
        mtime_ns: int,
    ) -> None:
        connection = _readonly_connection(index_path)
        try:
            meta = _read_meta(connection)
            if (
                int(meta.get("schema_version", 0) or 0)
                != COMPOSITION_INDEX_SCHEMA_VERSION
            ):
                raise IndexInvalidError(
                    "Element Distribution index schema is incompatible"
                )
            if meta.get("build_state") != "ready":
                raise IndexInvalidError(
                    "Element Distribution index is incomplete"
                )
            if meta.get("dataset_id") != dataset_id_for_source(path):
                raise IndexInvalidError(
                    "Element Distribution index dataset id is invalid"
                )
            if (
                int(meta.get("source_size", -1) or -1) != size
                or int(meta.get("source_mtime_ns", -1) or -1) != mtime_ns
            ):
                raise IndexStaleError(
                    "Element Distribution index source signature changed"
                )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not _COMPOSITION_REQUIRED_TABLE_COLUMNS.keys() <= tables:
                raise IndexInvalidError(
                    "Element Distribution index tables are incomplete"
                )
            for table, required_columns in (
                _COMPOSITION_REQUIRED_TABLE_COLUMNS.items()
            ):
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    )
                }
                if not required_columns <= columns:
                    raise IndexInvalidError(
                        "Element Distribution index "
                        f"{table} columns are incomplete"
                    )
            if int(meta.get("timepoint_count", -1) or -1) < 0:
                raise IndexInvalidError(
                    "Element Distribution index timepoint count is invalid"
                )
        except IndexNotReadyError:
            raise
        except (TypeError, ValueError, sqlite3.Error) as exc:
            raise IndexInvalidError(
                f"Element Distribution index metadata is invalid: {exc}"
            ) from exc
        finally:
            connection.close()

    def status(
        self,
        species_file: str,
        *,
        metadata_only: bool = False,
    ) -> dict[str, Any]:
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
                if metadata_only:
                    self._validate_metadata(
                        index_path,
                        path=path,
                        size=size,
                        mtime_ns=_mtime_ns,
                    )
                else:
                    self.open_required(path)
            except IndexStaleError:
                state = "stale"
            except IndexNotReadyError:
                state = "invalid"

        try:
            available_elements = _available_elements(meta)
        except IndexInvalidError:
            available_elements = []
            if state != "missing":
                state = "invalid"

        def display_int(value: Any) -> int:
            if not metadata_only:
                return int(value or 0)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        offset = display_int(meta.get("source_offset", 0))
        updated_at_epoch = display_int(meta.get("updated_at_epoch", 0))
        return {
            "state": state,
            "species_file": path,
            "species_size": size,
            "index_path": str(index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.is_file() else 0,
            "source_offset": offset,
            "progress": min(max(offset / max(size, 1), 0.0), 1.0),
            "timepoints": display_int(meta.get("timepoint_count", 0)),
            "unique_species": display_int(meta.get("unique_species", 0)),
            "available_elements": available_elements,
            "updated_at_epoch": updated_at_epoch or None,
        }

    def open_required(self, species_file: str) -> dict[str, Any]:
        path, size, mtime_ns = _source_signature(species_file)
        index_path = composition_index_path(path)
        if not index_path.is_file():
            building_path = Path(f"{index_path}.building")
            if building_path.is_file():
                raise IndexBuildInProgressError(
                    f"Element Distribution index is building: {building_path}"
                )
            raise IndexNotReadyError(
                "Element Distribution index is not ready; run "
                "`reacnet-scope prepare build element-distribution "
                f"{Path(path).parent}`"
            )
        connection = _readonly_connection(index_path)
        try:
            meta = _read_meta(connection)
            if int(meta.get("schema_version", 0) or 0) != COMPOSITION_INDEX_SCHEMA_VERSION:
                raise IndexInvalidError("Element Distribution index schema is incompatible")
            if meta.get("build_state") != "ready":
                raise IndexInvalidError("Element Distribution index is incomplete")
            if meta.get("dataset_id") != dataset_id_for_source(path):
                raise IndexInvalidError("Element Distribution index dataset id is invalid")
            if int(meta.get("source_size", -1) or -1) != size or int(meta.get("source_mtime_ns", -1) or -1) != mtime_ns:
                raise IndexStaleError(
                    "Element Distribution index is stale; run "
                    f"reacnet-scope prepare rebuild element-distribution {Path(path).parent}"
                )
            actual = int(connection.execute("SELECT COUNT(*) FROM timepoints").fetchone()[0])
            if actual != int(meta.get("timepoint_count", -1) or -1):
                raise IndexInvalidError("Element Distribution index row count is inconsistent")
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Element Distribution index is corrupt: {exc}") from exc
        finally:
            connection.close()
        return {
            "index_path": str(index_path),
            "species_file": path,
            "species_size": size,
            "species_mtime_ns": mtime_ns,
            "dataset_id": str(meta.get("dataset_id") or ""),
            "schema_version": int(meta.get("schema_version", 0) or 0),
            "timepoints": int(meta.get("timepoint_count", 0) or 0),
            "unique_species": int(meta.get("unique_species", 0) or 0),
            "available_elements": _available_elements(meta),
            "index_state": "cached_disk",
        }

    def build(self, species_file: str, *, progress_callback: Any = None) -> dict[str, Any]:
        resolve_dataset_paths(species_file, persist_identity=True)
        index_path = composition_index_path(species_file)
        with _exclusive_build_lock(index_path):
            path, size, mtime_ns = _source_signature(species_file)
            if index_path.is_file():
                try:
                    return self.open_required(path)
                except (IndexInvalidError, IndexStaleError):
                    # Schema upgrades only invalidate derived cache data.  Rebuild
                    # automatically so a normal resumed build stays
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
            species_cache: dict[str, tuple[dict[str, int], str, str]] = {}
            species_stats: dict[str, tuple[str, str, int, int, int, int]] = {}
            batch: list[tuple[int, int, str, str]] = []
            final_counts: dict[str, int] = {}
            if compatible:
                last_row = connection.execute(
                    "SELECT species_counts_json FROM timepoints "
                    "ORDER BY timestep DESC LIMIT 1"
                ).fetchone()
                if last_row is not None:
                    final_counts = {
                        str(smiles): int(count)
                        for smiles, count in json.loads(str(last_row[0])).items()
                    }
            try:
                available_elements = set(
                    json.loads(existing.get("available_elements", "[]"))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                available_elements = set()
            last_checkpoint = offset
            last_emit = 0.0
            self._write_meta(
                connection, path=path, size=size, mtime_ns=mtime_ns,
                source_offset=offset, timepoint_count=timepoints,
                unique_species=0, state="building",
                available_elements=available_elements,
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
                        distribution: dict[str, int] = {}
                        species_counts: dict[str, int] = {}
                        for smiles, count in pairs:
                            cached = species_cache.get(smiles)
                            if cached is None:
                                atom_counts = {
                                    str(element): int(value)
                                    for element, value in count_atoms_fast(
                                        smiles
                                    ).items()
                                    if int(value) > 0
                                }
                                formula = formula_from_counts(atom_counts)
                                cached = (
                                    atom_counts,
                                    formula,
                                    json.dumps(
                                        atom_counts,
                                        separators=(",", ":"),
                                        sort_keys=True,
                                    ),
                                )
                                species_cache[smiles] = cached
                            atom_counts, formula, elements_json = cached
                            available_elements.update(atom_counts)
                            previous = species_stats.get(smiles)
                            if previous is None:
                                species_stats[smiles] = (
                                    formula,
                                    elements_json,
                                    int(count),
                                    int(count),
                                    int(timestep),
                                    int(count) if timepoints == 0 else 0,
                                )
                            else:
                                peak_count = int(previous[3])
                                peak_timestep = int(previous[4])
                                if int(count) > peak_count:
                                    peak_count = int(count)
                                    peak_timestep = int(timestep)
                                species_stats[smiles] = (
                                    previous[0],
                                    previous[1],
                                    int(previous[2]) + int(count),
                                    peak_count,
                                    peak_timestep,
                                    int(previous[5]),
                                )
                            distribution[elements_json] = (
                                distribution.get(elements_json, 0) + int(count)
                            )
                            species_counts[smiles] = (
                                species_counts.get(smiles, 0) + int(count)
                            )
                        final_counts = species_counts
                        batch.append((
                            int(timestep),
                            int(line_offset),
                            json.dumps(
                                distribution,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            json.dumps(
                                species_counts,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                        ))
                        timepoints += 1
                        if len(batch) >= 1000:
                            connection.executemany(
                                "INSERT OR REPLACE INTO timepoints VALUES(?,?,?,?)", batch
                            )
                            batch.clear()
                        if offset - last_checkpoint >= 64 * 1024 * 1024:
                            if batch:
                                connection.executemany(
                                    "INSERT OR REPLACE INTO timepoints VALUES(?,?,?,?)", batch
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
                                available_elements=available_elements,
                            )
                            last_checkpoint = offset
                        now = time.monotonic()
                        if progress_callback and now - last_emit >= 1.0:
                            progress_callback({
                                "progress": min(offset / max(size, 1), 0.99),
                                "phase": "indexing_composition",
                                "message": f"Building Element Distribution index: {offset / max(size, 1) * 100:.1f}%",
                                "timepoints": timepoints,
                                "unique_species": len(species_cache),
                            })
                            last_emit = now
                if batch:
                    connection.executemany(
                        "INSERT OR REPLACE INTO timepoints VALUES(?,?,?,?)", batch
                    )
                self._flush_species_stats(connection, species_stats)
                self._finalize_timeline_stats(
                    connection,
                    final_counts=final_counts,
                    progress_callback=progress_callback,
                )
                indexed_species = int(
                    connection.execute("SELECT COUNT(*) FROM species_summary").fetchone()[0]
                )
                self._write_meta(
                    connection, path=path, size=size, mtime_ns=mtime_ns,
                    source_offset=size, timepoint_count=timepoints,
                    unique_species=indexed_species, state="ready",
                    available_elements=available_elements,
                )
            finally:
                connection.close()
            _assert_source_unchanged(path, size, mtime_ns)
            os.replace(building_path, index_path)
            if progress_callback:
                progress_callback({"progress": 1.0, "phase": "completed", "message": "Element Distribution index ready"})
            result = self.open_required(path)
            result["index_state"] = "built"
            return result

    def query(
        self,
        species_file: str,
        *,
        max_points: int = 1200,
        group_element: str | None = None,
        max_group_count: int | None = None,
        element_filters: dict[str, dict[str, Any]] | None = None,
        include_zero: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        meta = self.open_required(species_file)
        selected_element = str(group_element or "").strip()
        if not selected_element:
            raise ValueError("group_element is required")
        filters = dict(element_filters or {})

        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            timesteps = [int(row[0]) for row in connection.execute("SELECT timestep FROM timepoints ORDER BY timestep")]
            sampled = _sample_values(timesteps, max(2, min(int(max_points), 4000)))
            rows: list[dict[str, Any]] = []
            for offset in range(0, len(sampled), 500):
                selected = sampled[offset : offset + 500]
                placeholders = ",".join("?" for _ in selected)
                for timestep, _source_offset, distribution_json in connection.execute(
                    f"SELECT timestep,source_offset,distribution_json FROM timepoints "
                    f"WHERE timestep IN ({placeholders}) ORDER BY timestep",
                    selected,
                ):
                    merged_groups: dict[int, int] = {}
                    for encoded, count in json.loads(
                        str(distribution_json)
                    ).items():
                        atom_counts = {
                            str(element): int(value)
                            for element, value in json.loads(encoded).items()
                        }
                        if not matches_element_filters(atom_counts, filters):
                            continue
                        group_count = int(atom_counts.get(selected_element, 0))
                        if group_count == 0 and not include_zero:
                            continue
                        if (
                            max_group_count is not None
                            and group_count > int(max_group_count)
                        ):
                            continue
                        merged_groups[group_count] = (
                            merged_groups.get(group_count, 0) + int(count)
                        )
                    rows.extend(
                        {
                            "timestep": int(timestep),
                            "group_element": selected_element,
                            "group_count": group_count,
                            "count": count,
                            "group": f"{selected_element}{group_count}",
                        }
                        for group_count, count in sorted(merged_groups.items())
                    )
        finally:
            connection.close()
        return {
            "rows": rows,
            "timesteps": sampled,
            "meta": {
                **meta,
                "source_timepoints": len(timesteps),
                "sampled_timepoints": len(sampled),
                "query_seconds": round(time.perf_counter() - started, 4),
                "available_elements": list(meta.get("available_elements") or []),
                "group_element": selected_element,
                "max_group_count": max_group_count,
                "element_filters": filters,
                "include_zero": bool(include_zero),
                "sampling_stride": max(1, math.ceil(len(timesteps) / max(len(sampled), 1))),
            },
        }

    def snapshot(self, species_file: str, timestep: int) -> dict[str, Any]:
        """Read one exact abundance snapshot from prepared SQLite data."""
        meta = self.open_required(species_file)
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            row = connection.execute(
                "SELECT species_counts_json FROM timepoints WHERE timestep=?",
                (int(timestep),),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ValueError(f"Timestep {timestep} is not present in the composition index")
        counts = json.loads(str(row[0]))
        records: list[dict[str, Any]] = []
        for smiles, count in sorted(counts.items()):
            atom_counts = {
                str(element): int(value)
                for element, value in count_atoms_fast(smiles).items()
                if int(value) > 0
            }
            records.append(
                {
                    "smiles": smiles,
                    "count": int(count),
                    "elements": atom_counts,
                    "formula": formula_from_counts(atom_counts),
                }
            )
        return {"timestep": int(timestep), "records": records}

    def timesteps(self, species_file: str) -> list[int]:
        """Return every prepared source timestep in deterministic order."""
        meta = self.open_required(species_file)
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            return [
                int(row[0])
                for row in connection.execute(
                    "SELECT timestep FROM timepoints ORDER BY timestep"
                )
            ]
        finally:
            connection.close()

    def species_totals(self, species_file: str) -> dict[str, int]:
        """Return the prepared Species catalog and total abundance."""
        meta = self.open_required(species_file)
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            return {
                str(smiles): int(total)
                for smiles, total in connection.execute(
                    "SELECT smiles,total_count FROM species_summary ORDER BY smiles"
                )
            }
        finally:
            connection.close()

    def timeline_summary(self, species_file: str) -> dict[str, Any]:
        """Return prepared abundance classification statistics."""
        meta = self.open_required(species_file)
        timesteps = self.timesteps(species_file)
        if not timesteps:
            raise IndexInvalidError("Species abundance index has no timepoints")
        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            rows = list(
                connection.execute(
                    "SELECT smiles,start_count,end_count,peak_count,"
                    "peak_timestep,fwhm_longest_points "
                    "FROM species_summary ORDER BY smiles"
                )
            )
        finally:
            connection.close()
        intervals = [
            current - previous
            for previous, current in zip(timesteps, timesteps[1:])
            if current > previous
        ]
        step = next(
            (
                interval
                for interval in intervals
            ),
            1,
        )
        uniform_step = step if all(interval == step for interval in intervals) else None
        frame_by_timestep = {
            timestep: frame for frame, timestep in enumerate(timesteps)
        }
        return {
            "species_file": str(meta["species_file"]),
            "n_timesteps": len(timesteps),
            "first_timestep": timesteps[0],
            "last_timestep": timesteps[-1],
            "timestep_step": int(step),
            "uniform_timestep_step": uniform_step,
            "start_counts": {str(row[0]): int(row[1]) for row in rows},
            "end_counts": {str(row[0]): int(row[2]) for row in rows},
            "max_counts": {str(row[0]): int(row[3]) for row in rows},
            "max_timestep": {str(row[0]): int(row[4]) for row in rows},
            "max_analyzed_frame": {
                str(row[0]): int(frame_by_timestep[int(row[4])])
                for row in rows
            },
            "fwhm_longest_points": {
                str(row[0]): int(row[5]) for row in rows
            },
            "index": meta,
        }

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
            encoded_rows: dict[int, str] = {}
            for start in range(0, len(requested), 500):
                selected = requested[start : start + 500]
                placeholders = ",".join("?" for _ in selected)
                encoded_rows.update(
                    {
                        int(timestep): str(encoded)
                        for timestep, encoded in connection.execute(
                            f"SELECT timestep,species_counts_json FROM timepoints WHERE timestep IN ({placeholders})",
                            selected,
                        )
                    }
                )
        finally:
            connection.close()
        counts = {timestep: 0 for timestep in requested}
        for timestep, encoded in encoded_rows.items():
            counts[timestep] = int(json.loads(encoded).get(smiles, 0))
        return tuple(counts.items())

    def query_species_summary(
        self,
        species_file: str,
        *,
        group_element: str,
        group_count: int,
        element_filters: dict[str, dict[str, Any]] | None = None,
        current_timestep: int,
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
        selected_element = str(group_element)
        selected_count = int(group_count)
        filters = dict(element_filters or {})

        def selected(elements: dict[str, int]) -> bool:
            return bool(
                int(elements.get(selected_element, 0)) == selected_count
                and matches_element_filters(elements, filters)
            )

        connection = _readonly_connection(Path(meta["index_path"]))
        try:
            rows = []
            for (
                smiles,
                formula,
                elements_json,
                peak_count,
                peak_timestep,
            ) in connection.execute(
                "SELECT smiles,formula,elements_json,peak_count,peak_timestep "
                "FROM species_summary "
                "ORDER BY peak_count DESC,total_count DESC,smiles"
            ):
                if only_smiles and str(smiles) != str(only_smiles):
                    continue
                if exclude_smiles and str(smiles) == str(exclude_smiles):
                    continue
                elements = {
                    str(element): int(value)
                    for element, value in json.loads(str(elements_json)).items()
                }
                if not selected(elements):
                    continue
                rows.append(
                    {
                        "formula": str(formula),
                        "smiles": str(smiles),
                        "current_count": int(
                            current_counts.get(str(smiles), 0)
                        ),
                        "peak_count": int(peak_count),
                        "peak_timestep": int(peak_timestep),
                    }
                )
                if len(rows) >= max(1, min(int(limit), 500)):
                    break
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


def _group_bucket(
    element: str,
    count: int,
    *,
    bin_width: int,
    group_ranges: Sequence[Mapping[str, Any]],
) -> tuple[str, int]:
    for rule in group_ranges:
        minimum = rule.get("min")
        maximum = rule.get("max")
        if minimum is not None and count < int(minimum):
            continue
        if maximum is not None and count > int(maximum):
            continue
        return str(rule.get("label") or f"{element}{count}"), count
    width = max(1, int(bin_width))
    if width == 1:
        return f"{element}{count}", count
    start = (count // width) * width
    return f"{element}{start}-{start + width - 1}", start


def _transform_distribution_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    group_element: str,
    bin_width: int,
    group_ranges: Sequence[Mapping[str, Any]],
    smooth_window: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aggregated: dict[tuple[str, int, str, int], int] = {}
    for raw in rows:
        dataset = str(raw.get("dataset") or "dataset")
        timestep = int(raw["timestep"])
        label, bucket = _group_bucket(
            group_element,
            int(raw["group_count"]),
            bin_width=bin_width,
            group_ranges=group_ranges,
        )
        key = (dataset, timestep, label, bucket)
        aggregated[key] = aggregated.get(key, 0) + int(raw["count"])
    raw_rows = [
        {
            "dataset": dataset,
            "timestep": timestep,
            "group_element": group_element,
            "group_count": bucket,
            "group": label,
            "count": count,
        }
        for (dataset, timestep, label, bucket), count in sorted(
            aggregated.items()
        )
    ]
    window = max(1, int(smooth_window))
    if window == 1:
        return raw_rows, [dict(row) for row in raw_rows]
    transformed: list[dict[str, Any]] = []
    series_keys = sorted({(row["dataset"], row["group"]) for row in raw_rows})
    for dataset, group in series_keys:
        series = [
            row
            for row in raw_rows
            if row["dataset"] == dataset and row["group"] == group
        ]
        for index, row in enumerate(series):
            start = max(0, index - window + 1)
            values = [int(item["count"]) for item in series[start : index + 1]]
            transformed.append(
                {
                    **row,
                    "raw_count": int(row["count"]),
                    "count": sum(values) / len(values),
                }
            )
    transformed.sort(key=lambda row: (row["dataset"], row["timestep"], row["group"]))
    return raw_rows, transformed


def build_element_distribution_model(
    *,
    species_files: Mapping[str, str] | None = None,
    tidy_table: str = "",
    group_element: str,
    max_group_count: int | None = None,
    element_filters: Mapping[str, Mapping[str, Any]] | None = None,
    include_zero: bool = False,
    max_points: int = 1200,
    bin_width: int = 1,
    group_ranges: Sequence[Mapping[str, Any]] = (),
    smooth_window: int = 1,
) -> dict[str, Any]:
    """Build the one generic indexed/tidy/multi-dataset distribution model."""
    selected_element = str(group_element or "").strip()
    if not re.fullmatch(r"[A-Z][a-z]?", selected_element):
        raise ValueError("group_element must be a chemical symbol")
    filters = {
        str(element): dict(rule)
        for element, rule in (element_filters or {}).items()
    }
    source_rows: list[dict[str, Any]] = []
    source_meta: list[dict[str, Any]] = []
    for label, path in sorted((species_files or {}).items()):
        result = SPECIES_COMPOSITION_STORE.query(
            str(path),
            max_points=max_points,
            group_element=selected_element,
            max_group_count=max_group_count,
            element_filters=filters,
            include_zero=include_zero,
        )
        source_rows.extend(
            {**row, "dataset": str(label)} for row in result.get("rows") or []
        )
        source_meta.append(
            {
                "dataset": str(label),
                "source": str(path),
                "source_mode": "prepared_index",
                "meta": result.get("meta") or {},
            }
        )
    if tidy_table:
        path = Path(tidy_table).expanduser().resolve()
        table = (
            pd.read_excel(path)
            if path.suffix.casefold() in {".xlsx", ".xls"}
            else pd.read_csv(path)
        )
        required = {"time", "species", "count"}
        missing = sorted(required.difference(table.columns))
        if missing:
            raise ValueError(
                "tidy table is missing columns: " + ", ".join(missing)
            )
        for record in table.to_dict(orient="records"):
            elements = {
                str(element): int(value)
                for element, value in count_atoms_fast(str(record["species"])).items()
                if int(value) > 0
            }
            if not matches_element_filters(elements, filters):
                continue
            group_count = int(elements.get(selected_element, 0))
            if group_count == 0 and not include_zero:
                continue
            if max_group_count is not None and group_count > int(max_group_count):
                continue
            source_rows.append(
                {
                    "dataset": str(record.get("dataset") or record.get("system") or "tidy"),
                    "timestep": int(record["time"]),
                    "group_element": selected_element,
                    "group_count": group_count,
                    "count": int(record["count"]),
                }
            )
        source_meta.append(
            {
                "dataset": "tidy",
                "source": str(path),
                "source_mode": "tidy_table",
            }
        )
    if not source_meta:
        raise ValueError("at least one indexed species source or tidy table is required")
    raw_rows, rows = _transform_distribution_rows(
        source_rows,
        group_element=selected_element,
        bin_width=bin_width,
        group_ranges=group_ranges,
        smooth_window=smooth_window,
    )
    return {
        "schema_version": 1,
        "group_element": selected_element,
        "element_filters": filters,
        "include_zero": bool(include_zero),
        "rows": rows,
        "raw_rows": raw_rows,
        "sources": source_meta,
        "transform": {
            "bin_width": max(1, int(bin_width)),
            "group_ranges": [dict(rule) for rule in group_ranges],
            "smooth_window": max(1, int(smooth_window)),
        },
    }


__all__ = [
    "COMPOSITION_INDEX_SCHEMA_VERSION",
    "SPECIES_COMPOSITION_STORE",
    "SpeciesCompositionStore",
    "build_element_distribution_model",
    "composition_index_path",
]
