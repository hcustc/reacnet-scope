"""Offline builders and strict read-only readers for large ReacNet files.

The public boundary in this module is intentional:

* ``build``/``clear`` are preparation-process operations.
* ``open_required`` and query helpers are online-safe and never build, repair,
  migrate, checkpoint, or fall back to scanning a source file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable


_replace_file = os.replace

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

TRAJECTORY_INDEX_SCHEMA_VERSION = 3
_TRAJECTORY_REQUIRED_TABLE_COLUMNS = {
    "meta": {"key", "value"},
    "frames": {"timestep", "byte_start", "byte_end"},
}
DATASET_SUFFIXES = (
    ".timeline.h5",
    ".reactionevent.csv",
    ".molecules.csv",
    ".reactionabcd",
    ".species",
    ".table",
)
DATASET_ID_RE = re.compile(r"^[0-9a-f]{20}$")


class IndexNotReadyError(RuntimeError):
    """A required offline index has not been published yet."""


class IndexStaleError(IndexNotReadyError):
    """An index does not match the current source file signature."""


class IndexInvalidError(IndexNotReadyError):
    """An index exists but is incomplete, incompatible, or corrupt."""


class IndexBuildInProgressError(RuntimeError):
    """A requested index is locked by a live offline preparation process."""


_REMOTE_OR_SHARED_FILESYSTEMS = frozenset(
    {
        "9p",
        "afs",
        "beegfs",
        "ceph",
        "cifs",
        "davfs",
        "fuse.rclone",
        "fuse.s3fs",
        "fuse.sshfs",
        "glusterfs",
        "gpfs",
        "lustre",
        "nfs",
        "nfs4",
        "osxfuse",
        "remote",
        "smb",
        "smb2",
        "smb3",
        "smbfs",
        "webdav",
    }
)


def _decode_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _linux_filesystem_type(path: Path) -> str:
    try:
        lines = Path("/proc/self/mountinfo").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError:
        return ""
    resolved = path.resolve()
    best_mount: Path | None = None
    best_type = ""
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mount = Path(_decode_mount_path(fields[4])).resolve()
            filesystem_type = fields[separator + 1]
            resolved.relative_to(mount)
        except (IndexError, ValueError, OSError):
            continue
        if best_mount is None or len(mount.parts) > len(best_mount.parts):
            best_mount = mount
            best_type = filesystem_type
    return best_type


def _windows_filesystem_type(path: Path) -> str:
    if str(path).startswith("\\\\"):
        return "remote"
    try:
        import ctypes

        drive_type = ctypes.windll.kernel32.GetDriveTypeW(path.anchor)
    except (AttributeError, OSError):
        return ""
    return "remote" if int(drive_type) == 4 else ""


def _macos_filesystem_type(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["stat", "-f", "%T", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


@lru_cache(maxsize=256)
def _platform_filesystem_type(path: Path) -> str:
    if os.name == "nt":
        return _windows_filesystem_type(path)
    if sys.platform == "darwin":
        return _macos_filesystem_type(path)
    if sys.platform.startswith("linux"):
        return _linux_filesystem_type(path)
    return ""


@dataclass(frozen=True)
class WorkspacePolicy:
    """Choose whether a dataset location is suitable for a sidecar."""

    filesystem_type: Callable[[Path], str] = _platform_filesystem_type

    def requires_central_workspace(self, path: Path) -> bool:
        kind = str(self.filesystem_type(path) or "").strip().casefold()
        return kind in _REMOTE_OR_SHARED_FILESYSTEMS


@dataclass(frozen=True)
class WorkspaceStorageStatus:
    """Filesystem facts for a Dataset Workspace target."""

    target: Path
    existing_ancestor: Path
    writable: bool
    free_bytes: int | None


def inspect_workspace_storage(
    target: str | os.PathLike[str],
) -> WorkspaceStorageStatus:
    """Inspect one workspace target without creating or mutating it."""
    resolved_target = Path(target).expanduser().resolve()
    existing = resolved_target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    writable = bool(
        existing.is_dir() and os.access(existing, os.W_OK | os.X_OK)
    )
    try:
        free_bytes = int(shutil.disk_usage(existing).free)
    except OSError:
        free_bytes = None
    return WorkspaceStorageStatus(
        target=resolved_target,
        existing_ancestor=existing,
        writable=writable,
        free_bytes=free_bytes,
    )


def _platform_workspace_root() -> Path:
    if os.name == "nt":
        parent = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or Path.home() / "AppData" / "Local"
        )
    elif sys.platform == "darwin":
        parent = Path.home() / "Library" / "Application Support"
    else:
        parent = Path(
            os.environ.get("XDG_DATA_HOME")
            or Path.home() / ".local" / "share"
        )
    return (parent / "reacnet-scope" / "workspaces").expanduser().resolve()


def _cache_root(*, create: bool = False) -> Path:
    configured = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else _platform_workspace_root()
    )
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


def _dataset_base(path: str) -> str:
    absolute = os.path.abspath(path)
    for suffix in DATASET_SUFFIXES:
        if absolute.endswith(suffix):
            absolute = absolute[: -len(suffix)]
            break
    return absolute


def _path_derived_dataset_id(path: str) -> str:
    absolute = _dataset_base(path)
    return hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:20]


def _dataset_base_is_active(path_text: str) -> bool:
    base = Path(_dataset_base(path_text))
    return base.is_file() or any(
        Path(f"{base}{suffix}").is_file()
        for suffix in DATASET_SUFFIXES
    )


def _dataset_artifact_candidates(base_path: Path) -> tuple[Path, ...]:
    return (
        base_path,
        *(Path(f"{base_path}{suffix}") for suffix in DATASET_SUFFIXES),
    )


def _dataset_anchor_token(base_path: Path) -> str:
    """Return a move-stable local filesystem anchor for copy detection."""
    for candidate in _dataset_artifact_candidates(base_path):
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if candidate.is_file():
            return f"{int(stat.st_dev)}:{int(stat.st_ino)}"
    return ""


def _dataset_source_fingerprint(base_path: Path) -> str:
    """Hash discovery-safe source metadata without opening large artifacts."""
    digest = hashlib.sha256()
    found = False
    for candidate in _dataset_artifact_candidates(base_path):
        try:
            stat = candidate.stat()
            if not candidate.is_file():
                continue
            found = True
            digest.update(candidate.name.encode("utf-8", errors="surrogateescape"))
            digest.update(str(int(stat.st_size)).encode("ascii"))
            digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
        except OSError:
            continue
    return digest.hexdigest() if found else ""


def _anchor_device(token: str) -> str:
    """Return the device component of a local filesystem anchor."""
    device, separator, _inode = str(token or "").partition(":")
    return device if separator and device.isdigit() else ""


def _anchor_matches_move(
    recorded: str,
    current: str,
    *,
    recorded_fingerprint: str = "",
    current_fingerprint: str = "",
) -> bool:
    """Recognize same-filesystem and cross-filesystem directory moves.

    A same-filesystem copy changes only the inode and must receive a new
    identity.  A device change cannot preserve an inode, so an otherwise
    unique inactive record is treated as a cross-filesystem move.
    """
    if not recorded or not current:
        return False
    if recorded == current:
        return True
    recorded_device = _anchor_device(recorded)
    current_device = _anchor_device(current)
    return bool(
        recorded_device
        and current_device
        and recorded_device != current_device
        and recorded_fingerprint
        and recorded_fingerprint == current_fingerprint
    )


def _valid_identity_record(record: Any) -> bool:
    return bool(
        isinstance(record, dict)
        and DATASET_ID_RE.fullmatch(str(record.get("dataset_id") or ""))
    )


@contextmanager
def _workspace_identity_lock(registry: Path):
    lock_path = registry.with_suffix(".lock")
    descriptor: int | None = None
    for _attempt in range(500):
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
            )
            break
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > 30
            except FileNotFoundError:
                continue
            if stale:
                lock_path.unlink(missing_ok=True)
                continue
            time.sleep(0.01)
    if descriptor is None:
        raise IndexBuildInProgressError(
            f"Dataset Workspace identity is locked: {lock_path}"
        )
    try:
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _persistent_dataset_id(base_path: Path, workspace_root: Path) -> str:
    """Resolve and persist identity in the Dataset Workspace manifest."""
    registry = workspace_root / "workspace-manifest.json"
    try:
        workspace_root.mkdir(parents=True, exist_ok=True)
        with _workspace_identity_lock(registry):
            try:
                payload = json.loads(registry.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            records = [
                record
                for record in (payload.get("datasets") or [])
                if _valid_identity_record(record)
            ]
            active_path = os.path.normcase(str(base_path.resolve()))
            base_name = base_path.name
            source_anchor = _dataset_anchor_token(base_path)
            source_fingerprint = _dataset_source_fingerprint(base_path)
            selected: dict[str, Any] | None = None
            for record in records:
                if (
                    str(record.get("base_name") or "") == base_name
                    and os.path.normcase(str(record.get("active_path") or ""))
                    == active_path
                ):
                    selected = record
                    break
            if selected is None:
                inactive = [
                    record
                    for record in records
                    if str(record.get("base_name") or "") == base_name
                    and not _dataset_base_is_active(
                        str(record.get("active_path") or "")
                    )
                ]
                if len(inactive) == 1:
                    candidate = inactive[0]
                    recorded_anchor = str(candidate.get("source_anchor") or "")
                    recorded_fingerprint = str(
                        candidate.get("source_fingerprint") or ""
                    )
                    if _anchor_matches_move(
                        recorded_anchor,
                        source_anchor,
                        recorded_fingerprint=recorded_fingerprint,
                        current_fingerprint=source_fingerprint,
                    ) or bool(
                        not recorded_anchor
                        and recorded_fingerprint
                        and recorded_fingerprint == source_fingerprint
                    ):
                        selected = candidate
                        selected["active_path"] = str(base_path.resolve())
            if selected is None:
                selected = {
                    "dataset_id": uuid.uuid4().hex[:20],
                    "base_name": base_name,
                    "active_path": str(base_path.resolve()),
                    "source_anchor": source_anchor,
                    "source_fingerprint": source_fingerprint,
                }
                records.append(selected)
            selected["source_anchor"] = source_anchor
            selected["source_fingerprint"] = source_fingerprint
            selected["active_path"] = str(base_path.resolve())
            selected["last_seen_epoch"] = int(time.time())
            document = {"manifest_version": 1, "datasets": records}
            temporary = registry.with_name(
                f".{registry.name}.{os.getpid()}.tmp"
            )
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _replace_file(temporary, registry)
            return str(selected["dataset_id"])
    except (IndexBuildInProgressError, OSError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "Dataset Identity could not be persisted; the active Workspace "
            f"was not changed: {registry}"
        ) from exc


def _existing_dataset_id(base_path: Path, workspace_root: Path) -> str:
    """Resolve a known identity without creating or updating any files."""
    fallback = _path_derived_dataset_id(str(base_path))
    registry = workspace_root / "workspace-manifest.json"
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(payload, dict):
        return fallback
    records = [
        record
        for record in (payload.get("datasets") or [])
        if _valid_identity_record(record)
    ]
    active_path = os.path.normcase(str(base_path.resolve()))
    base_name = base_path.name
    source_fingerprint = _dataset_source_fingerprint(base_path)
    for record in records:
        if (
            str(record.get("base_name") or "") == base_name
            and os.path.normcase(str(record.get("active_path") or ""))
            == active_path
        ):
            return str(record.get("dataset_id") or fallback)
    inactive = [
        record
        for record in records
        if str(record.get("base_name") or "") == base_name
        and not _dataset_base_is_active(
            str(record.get("active_path") or "")
        )
    ]
    if len(inactive) == 1:
        recorded_anchor = str(inactive[0].get("source_anchor") or "")
        current_anchor = _dataset_anchor_token(base_path)
        if _anchor_matches_move(
            recorded_anchor,
            current_anchor,
            recorded_fingerprint=str(
                inactive[0].get("source_fingerprint") or ""
            ),
            current_fingerprint=source_fingerprint,
        ):
            return str(inactive[0].get("dataset_id") or fallback)
    return fallback


def dataset_id_for_source(path: str) -> str:
    """Return the persistent Dataset Identity for one source artifact."""
    return resolve_dataset_paths(path, persist_identity=False).dataset_id


def _assert_source_unchanged(
    source_file: str,
    expected_size: int,
    expected_mtime_ns: int,
) -> None:
    """Refuse to publish an index built from a changing source revision."""
    _path, size, mtime_ns = _source_signature(source_file)
    if size != int(expected_size) or mtime_ns != int(expected_mtime_ns):
        raise IndexStaleError(
            f"source changed during preparation; index was not published: {_path}"
        )


@dataclass(frozen=True)
class DatasetPaths:
    """The sole Dataset Workspace layout shared by CLI, Dash and readers."""

    source_root: Path
    base: Path
    dataset_id: str
    workspace_dir: Path
    manifest: Path
    trajectory_index: Path
    event_index: Path


def resolve_dataset_paths(
    source_root: str | os.PathLike[str],
    base: str = "",
    *,
    cache_root: str | os.PathLike[str] | None = None,
    workspace_policy: WorkspacePolicy | None = None,
    persist_identity: bool = True,
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
    absolute = _dataset_base(str(candidate))
    base_path = Path(absolute)
    configured_cache = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
    if cache_root is not None:
        resolved_cache = Path(cache_root).expanduser().resolve()
    elif configured_cache:
        resolved_cache = _cache_root()
    elif (workspace_policy or WorkspacePolicy()).requires_central_workspace(
        base_path.parent
    ):
        resolved_cache = _platform_workspace_root()
    elif base_path.parent.is_dir() and os.access(
        base_path.parent,
        os.W_OK | os.X_OK,
    ):
        resolved_cache = base_path.parent / ".reacnet-scope"
    else:
        resolved_cache = _platform_workspace_root()
    dataset_id = (
        _persistent_dataset_id(base_path, resolved_cache)
        if persist_identity
        else _existing_dataset_id(base_path, resolved_cache)
    )
    workspace_dir = resolved_cache / "datasets" / dataset_id
    return DatasetPaths(
        source_root=base_path.parent,
        base=base_path,
        dataset_id=dataset_id,
        workspace_dir=workspace_dir,
        manifest=workspace_dir / "manifest.json",
        trajectory_index=workspace_dir / "trajectory.sqlite3",
        event_index=workspace_dir / "events.sqlite3",
    )


def trajectory_index_path(trajectory_file: str) -> Path:
    path, _size, _mtime_ns = _source_signature(trajectory_file)
    return resolve_dataset_paths(
        path,
        persist_identity=False,
    ).trajectory_index


def event_evidence_index_path(reactionevent_file: str) -> Path:
    path, _size, _mtime_ns = _source_signature(reactionevent_file)
    return resolve_dataset_paths(path, persist_identity=False).event_index


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
    locked = False
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise IndexBuildInProgressError(
                    f"an offline preparation process owns: {lock_path}"
                ) from exc
            locked = True
        elif msvcrt is not None:  # pragma: no cover - exercised on Windows
            try:
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise IndexBuildInProgressError(
                    f"an offline preparation process owns: {lock_path}"
                ) from exc
            locked = True
        else:  # pragma: no cover - every supported OS has one backend
            raise RuntimeError("no cross-process file locking backend is available")
        yield
    finally:
        if locked and fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif locked and msvcrt is not None:  # pragma: no cover - Windows
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
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
    if int(meta.get("source_size", -1) or -1) != source_size:
        raise IndexStaleError(f"{kind} index source size changed")
    if int(meta.get("source_mtime_ns", -1) or -1) != source_mtime_ns:
        raise IndexStaleError(f"{kind} index source modification time changed")
    if meta.get("dataset_id") != dataset_id_for_source(source_path):
        raise IndexInvalidError(f"{kind} index dataset id is invalid")


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
            _validate_meta(
                meta,
                source_path=path,
                source_size=size,
                source_mtime_ns=mtime_ns,
                schema_version=TRAJECTORY_INDEX_SCHEMA_VERSION,
                kind="Trajectory",
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not _TRAJECTORY_REQUIRED_TABLE_COLUMNS.keys() <= tables:
                raise IndexInvalidError("Trajectory index tables are incomplete")
            for table, required_columns in (
                _TRAJECTORY_REQUIRED_TABLE_COLUMNS.items()
            ):
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    )
                }
                if not required_columns <= columns:
                    raise IndexInvalidError(
                        f"Trajectory index {table} columns are incomplete"
                    )
            if int(meta.get("frame_count", -1) or -1) < 0:
                raise IndexInvalidError(
                    "Trajectory index frame count is invalid"
                )
        except IndexNotReadyError:
            raise
        except (TypeError, ValueError, sqlite3.Error) as exc:
            raise IndexInvalidError(
                f"Trajectory index metadata is invalid: {exc}"
            ) from exc
        finally:
            connection.close()

    def status(
        self,
        trajectory_file: str,
        *,
        metadata_only: bool = False,
    ) -> dict[str, Any]:
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
        elif state == "missing":
            stale = _find_stale_published_index(index_path.parent, "trajectory", path)
            if stale is not None:
                active, meta = stale
                state = "stale"

        def display_int(value: Any) -> int:
            if not metadata_only:
                return int(value or 0)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        offset = display_int(meta.get("source_offset", 0))
        updated_at_epoch = display_int(
            meta.get(
                "updated_at_epoch",
                meta.get("built_at_epoch", 0),
            )
        )
        return {
            "state": state,
            "trajectory_file": path,
            "trajectory_size": size,
            "index_path": str(index_path),
            "building_path": str(building_path),
            "index_size": active.stat().st_size if active.exists() else 0,
            "source_offset": offset,
            "progress": min(max(offset / max(size, 1), 0.0), 1.0),
            "frames": display_int(meta.get("frame_count", 0)),
            "updated_at_epoch": updated_at_epoch or None,
            "workspace_path": str(index_path.parent),
        }

    def open_required(self, trajectory_file: str) -> TrajectoryFrameIndex:
        path, size, mtime_ns = _source_signature(trajectory_file)
        index_path = trajectory_index_path(path)
        if not index_path.is_file():
            if _has_stale_published_index(index_path.parent, "trajectory", path):
                raise IndexStaleError(
                    "Trajectory index is stale; run "
                    f"reacnet-scope prepare rebuild trajectory {path}"
                )
            raise IndexNotReadyError(
                "Trajectory index is not ready; run "
                f"reacnet-scope prepare build trajectory {path}"
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
        resolve_dataset_paths(trajectory_file, persist_identity=True)
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
            and meta.get("dataset_id") == dataset_id_for_source(path)
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
        _assert_source_unchanged(path, size, mtime_ns)
        os.replace(building_path, index_path)
        if progress_callback:
            progress_callback({"progress": 1.0, "phase": "completed", "message": "Trajectory index ready"})
        return self.open_required(path)

    def get(self, trajectory_file: str, *, progress_callback: Any = None, **_kwargs: Any) -> TrajectoryFrameIndex:
        return self.build(trajectory_file, progress_callback=progress_callback)

    def clear(self, trajectory_file: str) -> list[str]:
        return list(clear_index(trajectory_file, kind="trajectory")["removed"])


TRAJECTORY_INDEX_STORE = TrajectoryIndexStore()


def clear_index(source_file: str, *, kind: str) -> dict[str, Any]:
    """Safely remove one current-source index after acquiring its build lock.

    This intentionally accepts only the source file and index kind.  The
    target path is derived internally, constrained to the resolved Dataset
    Workspace, and cannot point at any ReacNetGenerator output file.
    """
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "trajectory":
        index_path = trajectory_index_path(source_file)
    else:
        raise ValueError("kind must be 'trajectory'")

    workspace_root = resolve_dataset_paths(source_file).workspace_dir.resolve()
    try:
        index_path.resolve().relative_to(workspace_root)
    except ValueError as exc:
        raise IndexInvalidError("index path escapes Dataset Workspace") from exc

    source_path, _source_size, _source_mtime_ns = _source_signature(source_file)
    prefix = "trajectory."
    targets: set[Path] = {index_path, Path(f"{index_path}.building")}
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
