"""Offline builders and strict read-only readers for large ReacNet files.

The public boundary in this module is intentional:

* ``build``/``clear`` are preparation-process operations.
* ``open_required`` and query helpers are online-safe and never build, repair,
  migrate, checkpoint, or fall back to scanning a source file.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - production deployment is Linux
    fcntl = None

from rng_tools.network import smiles_to_formula_fast
from rng_tools.reaction import canonical_smiles


ROUTE_INDEX_SCHEMA_VERSION = 3
TRAJECTORY_INDEX_SCHEMA_VERSION = 3
FORMULA_RE = re.compile(r"^([A-Z][a-z]?\d*)+$")
ROUTE_LINE_RE = re.compile(r"^\s*Atom\s+(\d+)\s+\S+:\s*(.*)$")
ROUTE_STEP_RE = re.compile(r"(\d+)\s+(\S+)")


class IndexNotReadyError(RuntimeError):
    """A required offline index has not been published yet."""


class IndexStaleError(IndexNotReadyError):
    """An index does not match the current source file signature."""


class IndexInvalidError(IndexNotReadyError):
    """An index exists but is incomplete, incompatible, or corrupt."""


class IndexBuildInProgressError(RuntimeError):
    """A requested index is locked by a live offline preparation process."""


def _cache_root(*, create: bool = False) -> Path:
    configured = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
    if not configured:
        raise RuntimeError("REACNET_SCOPE_CACHE_DIR must be set")
    root = Path(configured).expanduser().resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_stem(path: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(path).stem).strip("._")
    return cleaned[:80] or fallback


def _source_signature(path_text: str) -> tuple[str, int, int]:
    path = os.path.abspath(path_text)
    try:
        stat = os.stat(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"source file not found: {path}") from exc
    if not os.path.isfile(path):
        raise FileNotFoundError(f"source file not found: {path}")
    return path, int(stat.st_size), int(stat.st_mtime_ns)


def dataset_id_for_source(path: str) -> str:
    absolute = os.path.abspath(path)
    for suffix in (".reactionabcd", ".species", ".route", ".table"):
        if absolute.endswith(suffix):
            absolute = absolute[: -len(suffix)]
            break
    return hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class DatasetPaths:
    """The sole cache-layout contract shared by CLI, Dash and readers."""

    source_root: Path
    base: Path
    dataset_id: str
    cache_dir: Path
    manifest: Path
    route_index: Path
    trajectory_index: Path


def resolve_dataset_paths(
    source_root: str | os.PathLike[str],
    base: str = "",
    *,
    cache_root: str | os.PathLike[str] | None = None,
) -> DatasetPaths:
    """Resolve every prepared-data path without independently joining strings.

    ``source_root`` is normally the ReacNetGenerator output directory and
    ``base`` is its run basename.  Readers may omit ``base`` and pass a source
    file; suffixes are then stripped before calculating the dataset id.
    """
    root = Path(source_root).expanduser().resolve()
    candidate = Path(base).expanduser() if base else root
    if not candidate.is_absolute():
        candidate = root / candidate if root.is_dir() else root
    absolute = os.path.abspath(str(candidate))
    for suffix in (".reactionabcd", ".species", ".route", ".table"):
        if absolute.endswith(suffix):
            absolute = absolute[: -len(suffix)]
            break
    base_path = Path(absolute)
    resolved_cache = (
        Path(cache_root).expanduser().resolve()
        if cache_root is not None
        else _cache_root()
    )
    dataset_id = dataset_id_for_source(str(base_path))
    cache_dir = resolved_cache / "datasets" / dataset_id
    return DatasetPaths(
        source_root=base_path.parent,
        base=base_path,
        dataset_id=dataset_id,
        cache_dir=cache_dir,
        manifest=cache_dir / "manifest.json",
        route_index=cache_dir / "route.sqlite3",
        trajectory_index=cache_dir / "trajectory.sqlite3",
    )


def route_index_path(route_file: str) -> Path:
    path, _size, _mtime_ns = _source_signature(route_file)
    return resolve_dataset_paths(path).route_index


def trajectory_index_path(trajectory_file: str) -> Path:
    path, _size, _mtime_ns = _source_signature(trajectory_file)
    return resolve_dataset_paths(path).trajectory_index


def _legacy_route_index_path(route_file: str) -> Path:
    """Locate the v3 index layout published before dataset directories.

    This path is intentionally private: only the offline builder migrates it;
    Dash may merely validate and read it while prompting the user to run CLI.
    """
    path, size, mtime_ns = _source_signature(route_file)
    digest = hashlib.sha256(
        f"route|v{ROUTE_INDEX_SCHEMA_VERSION}|{path}|{size}|{mtime_ns}".encode("utf-8")
    ).hexdigest()[:20]
    return _cache_root() / "route" / f"{_safe_stem(path, 'route')}.{digest}.sqlite3"


def _readonly_connection(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise IndexInvalidError(f"cannot open index read-only: {path}: {exc}") from exc


@contextmanager
def _exclusive_build_lock(index_path: Path):
    """Prevent two preparation processes from building the same index."""
    lock_path = Path(f"{index_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise IndexBuildInProgressError(
                    f"an offline preparation process owns: {lock_path}"
                ) from exc
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _read_meta(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        return {str(key): str(value) for key, value in connection.execute("SELECT key, value FROM meta")}
    except sqlite3.Error as exc:
        raise IndexInvalidError(f"index metadata is unreadable: {exc}") from exc


def _find_stale_published_index(
    directory: Path,
    stem: str,
    source_path: str,
) -> tuple[Path, dict[str, str]] | None:
    """Find an older published signature without opening the source file."""
    if not directory.is_dir():
        return None
    for candidate in directory.glob(f"{stem}.*.sqlite3"):
        try:
            connection = _readonly_connection(candidate)
            try:
                meta = _read_meta(connection)
            finally:
                connection.close()
        except IndexNotReadyError:
            continue
        if meta.get("source_file") == source_path and meta.get("build_state") == "ready":
            return candidate, meta
    return None


def _has_stale_published_index(directory: Path, stem: str, source_path: str) -> bool:
    return _find_stale_published_index(directory, stem, source_path) is not None


def _validate_meta(
    meta: dict[str, str],
    *,
    source_path: str,
    source_size: int,
    source_mtime_ns: int,
    schema_version: int,
    kind: str,
) -> None:
    if int(meta.get("schema_version", 0) or 0) != schema_version:
        raise IndexInvalidError(f"{kind} index schema is incompatible")
    if meta.get("build_state") != "ready":
        raise IndexInvalidError(f"{kind} index is not complete")
    if meta.get("source_file") != source_path:
        raise IndexStaleError(f"{kind} index source path changed")
    if int(meta.get("source_size", -1) or -1) != source_size:
        raise IndexStaleError(f"{kind} index source size changed")
    if int(meta.get("source_mtime_ns", -1) or -1) != source_mtime_ns:
        raise IndexStaleError(f"{kind} index source modification time changed")
    if meta.get("dataset_id") != dataset_id_for_source(source_path):
        raise IndexInvalidError(f"{kind} index dataset id is invalid")


@lru_cache(maxsize=400_000)
def _normalize_route_label(token: str) -> tuple[str, str]:
    raw = str(token or "").strip()
    if not raw:
        return "", ""
    canonical = canonical_smiles(raw) or ""
    formula = raw if FORMULA_RE.fullmatch(raw) else ""
    if not formula:
        try:
            formula = str(smiles_to_formula_fast(canonical or raw) or "")
        except Exception:
            formula = ""
    return canonical, formula


class RouteIndexStore:
    """Persistent Route index with explicit offline build and online read APIs."""

    def _open_validated(self, index_path: Path, path: str, size: int, mtime_ns: int) -> dict[str, Any]:
        connection = _readonly_connection(index_path)
        try:
            meta = _read_meta(connection)
            _validate_meta(
                meta, source_path=path, source_size=size, source_mtime_ns=mtime_ns,
                schema_version=ROUTE_INDEX_SCHEMA_VERSION, kind="Route",
            )
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"transitions", "meta"}.issubset(tables):
                raise IndexInvalidError("Route index tables are incomplete")
            expected_rows = int(meta.get("indexed_transitions", -1) or -1)
            actual_rows = int(connection.execute("SELECT MAX(rowid) FROM transitions").fetchone()[0] or 0)
            if expected_rows < 0 or actual_rows != expected_rows:
                raise IndexInvalidError("Route index transition count is inconsistent")
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Route index is corrupt: {exc}") from exc
        finally:
            connection.close()
        return {
            "index_path": str(index_path), "route_file": path, "route_size": size,
            "route_mtime": mtime_ns / 1_000_000_000,
            "scanned_atoms": int(meta.get("scanned_atoms", 0) or 0),
            "indexed_transitions": int(meta.get("indexed_transitions", 0) or 0),
            "index_state": "cached_disk",
        }

    def _published_path(self, path: str) -> Path | None:
        current = route_index_path(path)
        if current.is_file():
            return current
        legacy = _legacy_route_index_path(path)
        return legacy if legacy.is_file() else None

    def status(self, route_file: str) -> dict[str, Any]:
        path, size, _mtime_ns = _source_signature(route_file)
        index_path = route_index_path(path)
        published_path = self._published_path(path)
        building_path = Path(f"{index_path}.building")
        active = published_path or building_path
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
        offset = int(meta.get("source_offset", 0) or 0)
        state = "ready" if published_path else ("building" if building_path.exists() else "missing")
        if published_path:
            try:
                self.open_required(path)
            except IndexStaleError:
                state = "stale"
            except IndexNotReadyError:
                state = "invalid"
        elif state == "missing":
            stale = _find_stale_published_index(index_path.parent, "route", path)
            if stale is not None:
                active, meta = stale
                offset = int(meta.get("source_offset", 0) or 0)
                state = "stale"
        return {
            "state": state,
            "route_file": path,
            "route_size": size,
            "index_path": str(published_path or index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.exists() else 0,
            "source_offset": offset,
            "progress": min(max(offset / max(size, 1), 0.0), 1.0),
            "scanned_atoms": int(meta.get("scanned_atoms", 0) or 0),
            "indexed_transitions": int(meta.get("indexed_transitions", 0) or 0),
            "updated_at_epoch": int(meta.get("updated_at_epoch", meta.get("built_at_epoch", 0)) or 0) or None,
            "cache_dir": str((published_path or index_path).parent),
        }

    def open_required(self, route_file: str) -> dict[str, Any]:
        path, size, mtime_ns = _source_signature(route_file)
        index_path = self._published_path(path)
        if index_path is None:
            expected_path = route_index_path(path)
            if _has_stale_published_index(expected_path.parent, "route", path):
                raise IndexStaleError(f"Route index is stale; rerun reacnet-scope-prepare for: {path}")
            raise IndexNotReadyError(
                f"Route index is not ready; run reacnet-scope-prepare for: {path}"
            )
        return self._open_validated(index_path, path, size, mtime_ns)

    def _connect_for_build(self, target: Path) -> sqlite3.Connection:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(target))
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS transitions(
                atom_id INTEGER NOT NULL,
                start_frame INTEGER NOT NULL,
                end_frame INTEGER NOT NULL,
                from_label TEXT NOT NULL,
                to_label TEXT NOT NULL,
                from_canonical TEXT NOT NULL,
                to_canonical TEXT NOT NULL,
                from_formula TEXT NOT NULL,
                to_formula TEXT NOT NULL
            )"""
        )
        return connection

    def _checkpoint_route_build(
        self,
        connection: sqlite3.Connection,
        *,
        route_file: str,
        mtime: float,
        size: int,
        source_offset: int,
        scanned_atoms: int,
        indexed_transitions: int,
    ) -> None:
        path = os.path.abspath(route_file)
        current_mtime_ns = int(os.stat(path).st_mtime_ns)
        rows = {
            "schema_version": ROUTE_INDEX_SCHEMA_VERSION,
            "build_state": "building",
            "source_file": path,
            "source_size": int(size),
            "source_mtime_ns": current_mtime_ns,
            "source_offset": int(source_offset),
            "dataset_id": dataset_id_for_source(path),
            "scanned_atoms": int(scanned_atoms),
            "indexed_transitions": int(indexed_transitions),
            "updated_at_epoch": int(time.time()),
        }
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(key, str(value)) for key, value in rows.items()],
        )
        connection.commit()

    def build(self, route_file: str, *, progress_callback: Any = None) -> dict[str, Any]:
        index_path = route_index_path(route_file)
        with _exclusive_build_lock(index_path):
            return self._build_unlocked(route_file, progress_callback=progress_callback)

    def _build_unlocked(self, route_file: str, *, progress_callback: Any = None) -> dict[str, Any]:
        path, size, mtime_ns = _source_signature(route_file)
        mtime = mtime_ns / 1_000_000_000
        index_path = route_index_path(path)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        if index_path.exists():
            return self.open_required(path)
        legacy_path = _legacy_route_index_path(path)
        if legacy_path.is_file():
            # Validate before rename.  No source file is opened beyond the
            # signature check already performed above, and rename is atomic
            # because both paths are constrained beneath one cache root.
            self._open_validated(legacy_path, path, size, mtime_ns)
            os.replace(legacy_path, index_path)
            return self.open_required(path)
        building_path = Path(f"{index_path}.building")
        connection = self._connect_for_build(building_path)
        existing = {str(k): str(v) for k, v in connection.execute("SELECT key,value FROM meta")}
        compatible = bool(existing) and (
            int(existing.get("schema_version", 0) or 0) == ROUTE_INDEX_SCHEMA_VERSION
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
        scanned_atoms = int(existing.get("scanned_atoms", 0) or 0) if compatible else 0
        indexed = int(existing.get("indexed_transitions", 0) or 0) if compatible else 0
        resumed = offset > 0
        self._checkpoint_route_build(
            connection,
            route_file=path,
            mtime=mtime,
            size=size,
            source_offset=offset,
            scanned_atoms=scanned_atoms,
            indexed_transitions=indexed,
        )
        batch: list[tuple[Any, ...]] = []
        last_checkpoint = offset
        last_emit = 0.0
        try:
            with open(path, "rb") as source:
                source.seek(offset)
                for raw_line in source:
                    offset += len(raw_line)
                    match = ROUTE_LINE_RE.match(raw_line.decode("utf-8", errors="ignore").strip())
                    if not match:
                        continue
                    scanned_atoms += 1
                    atom_id = int(match.group(1))
                    steps = [(int(hit.group(1)), hit.group(2)) for hit in ROUTE_STEP_RE.finditer(match.group(2))]
                    if len(steps) >= 2:
                        previous_frame, previous_label = steps[0]
                        previous_canonical, previous_formula = _normalize_route_label(previous_label)
                        for frame, label in steps[1:]:
                            current_canonical, current_formula = _normalize_route_label(label)
                            if label != previous_label:
                                batch.append((
                                    atom_id, previous_frame, frame, previous_label, label,
                                    previous_canonical, current_canonical, previous_formula, current_formula,
                                ))
                                indexed += 1
                            previous_frame, previous_label = frame, label
                            previous_canonical, previous_formula = current_canonical, current_formula
                    if len(batch) >= 5000:
                        connection.executemany("INSERT INTO transitions VALUES(?,?,?,?,?,?,?,?,?)", batch)
                        batch.clear()
                    if offset - last_checkpoint >= 256 * 1024 * 1024:
                        if batch:
                            connection.executemany("INSERT INTO transitions VALUES(?,?,?,?,?,?,?,?,?)", batch)
                            batch.clear()
                        self._checkpoint_route_build(
                            connection,
                            route_file=path,
                            mtime=mtime,
                            size=size,
                            source_offset=offset,
                            scanned_atoms=scanned_atoms,
                            indexed_transitions=indexed,
                        )
                        last_checkpoint = offset
                    now = time.monotonic()
                    if progress_callback and now - last_emit >= 1.0:
                        progress_callback({
                            "progress": min(offset / max(size, 1), 0.9),
                            "phase": "indexing_route",
                            "message": f"Building Route index: {offset / max(size, 1) * 100:.1f}%",
                            "resumed": resumed,
                        })
                        last_emit = now
            if batch:
                connection.executemany("INSERT INTO transitions VALUES(?,?,?,?,?,?,?,?,?)", batch)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_transitions_atom ON transitions(atom_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_transitions_frames ON transitions(start_frame,end_frame)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_transitions_canonical ON transitions(from_canonical,to_canonical)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_transitions_formula ON transitions(from_formula,to_formula)")
            self._checkpoint_route_build(
                connection,
                route_file=path,
                mtime=mtime,
                size=size,
                source_offset=size,
                scanned_atoms=scanned_atoms,
                indexed_transitions=indexed,
            )
            connection.execute("UPDATE meta SET value='ready' WHERE key='build_state'")
            connection.commit()
        finally:
            connection.close()
        os.replace(building_path, index_path)
        if progress_callback:
            progress_callback({"progress": 1.0, "phase": "completed", "message": "Route index ready"})
        result = self.open_required(path)
        result["index_state"] = "built"
        result["resumed"] = resumed
        return result

    # Compatibility for offline callers. Online code must use open_required/query.
    def get(self, route_file: str, *, progress_callback: Any = None, **_kwargs: Any) -> dict[str, Any]:
        return self.build(route_file, progress_callback=progress_callback)

    def clear(self, route_file: str) -> list[str]:
        return list(clear_index(route_file, kind="route")["removed"])

    def query_reaction_hits(
        self,
        route_file: str,
        reaction_query: dict[str, Any],
        *,
        max_hits: int = 2000,
        progress_callback: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        meta = self.open_required(route_file)
        reactants = sorted(str(value) for value in reaction_query["reactant_token_set"])
        products = sorted(str(value) for value in reaction_query["product_token_set"])
        if not reactants or not products:
            return {"hits": [], "scanned_atoms": 0, "matched_atom_transitions": 0, "route_index": meta}
        mode = str(reaction_query.get("match_mode") or "canonical_smiles")
        from_column = "from_canonical" if mode == "canonical_smiles" else "from_formula"
        to_column = "to_canonical" if mode == "canonical_smiles" else "to_formula"
        left = ",".join("?" for _ in reactants)
        right = ",".join("?" for _ in products)
        sql = (
            "SELECT atom_id,start_frame,end_frame,from_label,to_label,"
            f"{from_column},{to_column} FROM transitions WHERE "
            f"(({from_column} IN ({left}) AND {to_column} IN ({right})) OR "
            f"({from_column} IN ({right}) AND {to_column} IN ({left}))) "
            "ORDER BY start_frame,end_frame,atom_id LIMIT ?"
        )
        params = [*reactants, *products, *products, *reactants, max(1, min(int(max_hits), 100_000))]
        connection = _readonly_connection(Path(meta["index_path"]))
        hits: list[dict[str, Any]] = []
        try:
            for atom_id, start, end, from_label, to_label, from_token, to_token in connection.execute(sql, params):
                direction = (
                    "reactant_to_product"
                    if str(from_token) in reaction_query["reactant_token_set"] and str(to_token) in reaction_query["product_token_set"]
                    else "product_to_reactant"
                )
                hits.append({
                    "atom_id": int(atom_id), "start_frame": int(start), "end_frame": int(end),
                    "anchor_frame": int(end), "from_label": str(from_label), "to_label": str(to_label),
                    "from_token": str(from_token), "to_token": str(to_token), "direction": direction,
                })
        finally:
            connection.close()
        if progress_callback:
            progress_callback({"progress": 1.0, "phase": "querying_route_index", "message": f"Matched {len(hits)} transitions"})
        return {
            "hits": hits,
            "scanned_atoms": meta["scanned_atoms"],
            "matched_atom_transitions": len(hits),
            "route_index": meta,
        }


@dataclass
class TrajectoryFrameIndex:
    trajectory_file: str
    mtime: float
    size: int
    index_path: str

    @property
    def frames(self) -> list[int]:
        connection = _readonly_connection(Path(self.index_path))
        try:
            return [int(row[0]) for row in connection.execute("SELECT timestep FROM frames ORDER BY timestep")]
        finally:
            connection.close()

    @property
    def frame_offsets(self) -> dict[int, tuple[int, int]]:
        connection = _readonly_connection(Path(self.index_path))
        try:
            return {int(frame): (int(start), int(end)) for frame, start, end in connection.execute(
                "SELECT timestep,byte_start,byte_end FROM frames"
            )}
        finally:
            connection.close()

    def offsets_for(self, frames: Iterable[int]) -> dict[int, tuple[int, int]]:
        selected = sorted({int(frame) for frame in frames})
        if not selected:
            return {}
        placeholders = ",".join("?" for _ in selected)
        connection = _readonly_connection(Path(self.index_path))
        try:
            return {int(frame): (int(start), int(end)) for frame, start, end in connection.execute(
                f"SELECT timestep,byte_start,byte_end FROM frames WHERE timestep IN ({placeholders})",
                selected,
            )}
        finally:
            connection.close()


class TrajectoryIndexStore:
    """SQLite trajectory-offset index with no online scan fallback."""

    def status(self, trajectory_file: str) -> dict[str, Any]:
        path, size, _mtime_ns = _source_signature(trajectory_file)
        index_path = trajectory_index_path(path)
        building_path = Path(f"{index_path}.building")
        active = index_path if index_path.exists() else building_path
        meta: dict[str, str] = {}
        if active.exists():
            try:
                connection = _readonly_connection(active)
                try:
                    meta = _read_meta(connection)
                finally:
                    connection.close()
            except IndexNotReadyError:
                pass
        state = "ready" if index_path.exists() else ("building" if building_path.exists() else "missing")
        if index_path.exists():
            try:
                self.open_required(path)
            except IndexStaleError:
                state = "stale"
            except IndexNotReadyError:
                state = "invalid"
        elif state == "missing":
            stale = _find_stale_published_index(index_path.parent, "trajectory", path)
            if stale is not None:
                active, meta = stale
                state = "stale"
        offset = int(meta.get("source_offset", 0) or 0)
        return {
            "state": state,
            "trajectory_file": path,
            "trajectory_size": size,
            "index_path": str(index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.exists() else 0,
            "source_offset": offset,
            "progress": min(max(offset / max(size, 1), 0.0), 1.0),
            "frames": int(meta.get("frame_count", 0) or 0),
            "updated_at_epoch": int(meta.get("updated_at_epoch", meta.get("built_at_epoch", 0)) or 0) or None,
            "cache_dir": str(index_path.parent),
        }

    def open_required(self, trajectory_file: str) -> TrajectoryFrameIndex:
        path, size, mtime_ns = _source_signature(trajectory_file)
        index_path = trajectory_index_path(path)
        if not index_path.is_file():
            if _has_stale_published_index(index_path.parent, "trajectory", path):
                raise IndexStaleError(f"Trajectory index is stale; rerun reacnet-scope-prepare for: {path}")
            raise IndexNotReadyError(
                f"Trajectory index is not ready; run reacnet-scope-prepare for: {path}"
            )
        connection = _readonly_connection(index_path)
        try:
            meta = _read_meta(connection)
            _validate_meta(
                meta,
                source_path=path,
                source_size=size,
                source_mtime_ns=mtime_ns,
                schema_version=TRAJECTORY_INDEX_SCHEMA_VERSION,
                kind="Trajectory",
            )
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"frames", "meta"}.issubset(tables):
                raise IndexInvalidError("Trajectory index tables are incomplete")
            frame_count = int(meta.get("frame_count", -1) or -1)
            if frame_count < 0:
                raise IndexInvalidError("Trajectory index frame count is invalid")
            actual_frames = int(connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
            if actual_frames != frame_count:
                raise IndexInvalidError("Trajectory index frame count is inconsistent")
        except sqlite3.Error as exc:
            raise IndexInvalidError(f"Trajectory index is corrupt: {exc}") from exc
        finally:
            connection.close()
        return TrajectoryFrameIndex(path, mtime_ns / 1_000_000_000, size, str(index_path))

    def peek(self, trajectory_file: str) -> TrajectoryFrameIndex | None:
        try:
            return self.open_required(trajectory_file)
        except IndexNotReadyError:
            return None

    def _connect_for_build(self, target: Path) -> sqlite3.Connection:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(target))
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS frames(timestep INTEGER PRIMARY KEY,byte_start INTEGER NOT NULL,byte_end INTEGER NOT NULL CHECK(byte_end>byte_start))"
        )
        return connection

    def _write_checkpoint(
        self,
        connection: sqlite3.Connection,
        *,
        path: str,
        size: int,
        mtime_ns: int,
        source_offset: int,
        frame_count: int,
        state: str = "building",
    ) -> None:
        values = {
            "schema_version": TRAJECTORY_INDEX_SCHEMA_VERSION,
            "build_state": state,
            "source_file": path,
            "source_size": size,
            "source_mtime_ns": mtime_ns,
            "source_offset": source_offset,
            "dataset_id": dataset_id_for_source(path),
            "frame_count": frame_count,
            "updated_at_epoch": int(time.time()),
        }
        connection.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(key, str(value)) for key, value in values.items()],
        )
        connection.commit()

    # Compatibility helper used by existing checkpoint tests.
    def _persist_build_checkpoint(
        self,
        trajectory_file: str,
        *,
        mtime: float,
        size: int,
        source_offset: int,
        frames: list[int],
        frame_offsets: dict[int, tuple[int, int]],
    ) -> None:
        path = os.path.abspath(trajectory_file)
        target = Path(f"{trajectory_index_path(path)}.building")
        connection = self._connect_for_build(target)
        try:
            connection.executemany(
                "INSERT OR REPLACE INTO frames(timestep,byte_start,byte_end) VALUES(?,?,?)",
                [(int(frame), int(frame_offsets[frame][0]), int(frame_offsets[frame][1])) for frame in frames],
            )
            self._write_checkpoint(
                connection,
                path=path,
                size=size,
                mtime_ns=int(round(mtime * 1_000_000_000)),
                source_offset=source_offset,
                frame_count=len(frames),
            )
        finally:
            connection.close()

    def build(self, trajectory_file: str, *, progress_callback: Any = None) -> TrajectoryFrameIndex:
        index_path = trajectory_index_path(trajectory_file)
        with _exclusive_build_lock(index_path):
            return self._build_unlocked(trajectory_file, progress_callback=progress_callback)

    def _build_unlocked(self, trajectory_file: str, *, progress_callback: Any = None) -> TrajectoryFrameIndex:
        path, size, mtime_ns = _source_signature(trajectory_file)
        index_path = trajectory_index_path(path)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        if index_path.exists():
            return self.open_required(path)
        building_path = Path(f"{index_path}.building")
        connection = self._connect_for_build(building_path)
        meta = {str(k): str(v) for k, v in connection.execute("SELECT key,value FROM meta")}
        compatible = bool(meta) and (
            int(meta.get("schema_version", 0) or 0) == TRAJECTORY_INDEX_SCHEMA_VERSION
            and meta.get("build_state") == "building"
            and meta.get("source_file") == path
            and int(meta.get("source_size", -1) or -1) == size
            and int(meta.get("source_mtime_ns", -1) or -1) == mtime_ns
        )
        if meta and not compatible:
            connection.close()
            building_path.unlink(missing_ok=True)
            connection = self._connect_for_build(building_path)
            meta = {}
        offset = int(meta.get("source_offset", 0) or 0) if compatible else 0
        frame_count = int(meta.get("frame_count", 0) or 0) if compatible else 0
        resumed = offset > 0
        self._write_checkpoint(
            connection,
            path=path,
            size=size,
            mtime_ns=mtime_ns,
            source_offset=offset,
            frame_count=frame_count,
        )
        current_frame: int | None = None
        current_start: int | None = None
        last_checkpoint = offset
        last_emit = 0.0
        try:
            with open(path, "rb") as source:
                source.seek(offset)
                while True:
                    block_start = source.tell()
                    line = source.readline()
                    if not line:
                        break
                    if not line.startswith(b"ITEM: TIMESTEP"):
                        continue
                    timestep_line = source.readline()
                    if not timestep_line:
                        break
                    if current_frame is not None and current_start is not None and block_start > current_start:
                        connection.execute(
                            "INSERT OR REPLACE INTO frames VALUES(?,?,?)",
                            (current_frame, current_start, block_start),
                        )
                        frame_count += 1
                    try:
                        current_frame = int(timestep_line.strip().split()[0])
                    except (ValueError, IndexError):
                        current_frame = None
                    current_start = block_start
                    position = source.tell()
                    if block_start - last_checkpoint >= 1024 * 1024 * 1024:
                        self._write_checkpoint(
                            connection,
                            path=path,
                            size=size,
                            mtime_ns=mtime_ns,
                            source_offset=block_start,
                            frame_count=frame_count,
                        )
                        last_checkpoint = block_start
                    now = time.monotonic()
                    if progress_callback and now - last_emit >= 1.0:
                        progress_callback({
                            "progress": min(position / max(size, 1), 1.0),
                            "phase": "indexing_trajectory",
                            "message": f"Building trajectory index: {position / max(size, 1) * 100:.1f}%",
                            "resumed": resumed,
                        })
                        last_emit = now
            if current_frame is not None and current_start is not None and size > current_start:
                connection.execute("INSERT OR REPLACE INTO frames VALUES(?,?,?)", (current_frame, current_start, size))
            frame_count = int(connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
            self._write_checkpoint(
                connection,
                path=path,
                size=size,
                mtime_ns=mtime_ns,
                source_offset=size,
                frame_count=frame_count,
                state="ready",
            )
        finally:
            connection.close()
        os.replace(building_path, index_path)
        if progress_callback:
            progress_callback({"progress": 1.0, "phase": "completed", "message": "Trajectory index ready"})
        return self.open_required(path)

    def get(self, trajectory_file: str, *, progress_callback: Any = None, **_kwargs: Any) -> TrajectoryFrameIndex:
        return self.build(trajectory_file, progress_callback=progress_callback)

    def clear(self, trajectory_file: str) -> list[str]:
        return list(clear_index(trajectory_file, kind="trajectory")["removed"])


ROUTE_INDEX_STORE = RouteIndexStore()
TRAJECTORY_INDEX_STORE = TrajectoryIndexStore()


def clear_index(source_file: str, *, kind: str) -> dict[str, Any]:
    """Safely remove one current-source index after acquiring its build lock.

    This intentionally accepts only the source file and index kind.  The
    target path is derived internally, constrained to the configured cache,
    and cannot point at any ReacNetGenerator output file.
    """
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "route":
        index_path = route_index_path(source_file)
        legacy_path = _legacy_route_index_path(source_file)
    elif normalized_kind == "trajectory":
        index_path = trajectory_index_path(source_file)
    else:
        raise ValueError("kind must be 'route' or 'trajectory'")

    cache_root = _cache_root().resolve()
    try:
        index_path.resolve().relative_to(cache_root)
    except ValueError as exc:
        raise IndexInvalidError("index path escapes REACNET_SCOPE_CACHE_DIR") from exc

    source_path, _source_size, _source_mtime_ns = _source_signature(source_file)
    prefix = "route." if normalized_kind == "route" else "trajectory."
    targets: set[Path] = {index_path, Path(f"{index_path}.building")}
    if normalized_kind == "route":
        targets.add(legacy_path)
    if index_path.parent.is_dir():
        for candidate in index_path.parent.glob(f"{prefix}*.sqlite3*"):
            if candidate.name.endswith(".lock") or not candidate.is_file():
                continue
            try:
                connection = _readonly_connection(candidate)
                try:
                    meta = _read_meta(connection)
                finally:
                    connection.close()
            except IndexNotReadyError:
                continue
            if meta.get("source_file") == source_path:
                targets.add(candidate)

    removed: list[str] = []
    released_bytes = 0
    with _exclusive_build_lock(index_path):
        for target in sorted(targets):
            if target.exists():
                released_bytes += target.stat().st_size
                target.unlink()
                removed.append(str(target))
    return {
        "kind": normalized_kind,
        "index_path": str(index_path),
        "removed": removed,
        "released_bytes": released_bytes,
    }
