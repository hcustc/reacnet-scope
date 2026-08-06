"""Unified offline data preparation command for ReacNet Scope."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .indexes import (
    IndexBuildInProgressError,
    clear_index,
    inspect_workspace_storage,
    resolve_dataset_paths,
    TRAJECTORY_INDEX_STORE,
)
from .composition import SPECIES_COMPOSITION_STORE
from .event_index import EVENT_EVIDENCE_STORE
from .timed_evidence import (
    TimedEvidenceDataError,
    TimedEvidenceSelection,
    native_membership_bytes,
    select_timed_evidence,
)


def discover_dataset(case: str, base: str = "") -> dict[str, str]:
    root = Path(case).expanduser().resolve()
    if root.is_file():
        stem = str(root)
    elif root.is_dir():
        candidates = sorted(root.glob("*.reactionabcd"))
        if base:
            stem = str((root / base).resolve()) if not os.path.isabs(base) else str(Path(base).resolve())
        elif len(candidates) == 1:
            stem = str(candidates[0])[: -len(".reactionabcd")]
        else:
            reaction_stems = {
                str(path)[: -len(".reactionabcd")] for path in candidates
            }
            species_stems = {
                str(path)[: -len(".species")]
                for path in root.glob("*.species")
            }
            trajectory_stems = {
                str(path) for path in root.glob("*.lammpstrj")
            }
            event_stems = {
                str(path)[: -len(".reactionevent.csv")]
                for path in root.glob("*.reactionevent.csv")
            }
            molecule_stems = {
                str(path)[: -len(".molecules.csv")]
                for path in root.glob("*.molecules.csv")
            }
            timeline_stems = {
                str(path)[: -len(".timeline.h5")]
                for path in root.glob("*.timeline.h5")
            }
            stems = (
                reaction_stems
                | species_stems
                | trajectory_stems
                | event_stems
                | molecule_stems
                | timeline_stems
            )
            if len(stems) != 1:
                raise RuntimeError("dataset directory is ambiguous; pass --base")
            stem = stems.pop()
    else:
        raise FileNotFoundError(f"dataset path not found: {root}")
    if stem.endswith(".reactionabcd"):
        stem = stem[: -len(".reactionabcd")]
    if stem.endswith(".reactionevent.csv"):
        stem = stem[: -len(".reactionevent.csv")]
    if stem.endswith(".molecules.csv"):
        stem = stem[: -len(".molecules.csv")]
    if stem.endswith(".timeline.h5"):
        stem = stem[: -len(".timeline.h5")]
    return {
        "base": stem,
        "reaction": f"{stem}.reactionabcd",
        "species": f"{stem}.species",
        "table": f"{stem}.table",
        "trajectory": stem,
        "reactionevent": f"{stem}.reactionevent.csv",
        "molecules": f"{stem}.molecules.csv",
        "timeline": f"{stem}.timeline.h5",
    }


def _manifest_path(dataset: dict[str, str]) -> Path:
    paths = resolve_dataset_paths(Path(dataset["base"]).parent, Path(dataset["base"]).name)
    paths.workspace_dir.mkdir(parents=True, exist_ok=True)
    return paths.manifest


def _preparation_task_path(
    dataset: dict[str, str],
    capability: str,
    *,
    persist_identity: bool = True,
) -> Path:
    paths = resolve_dataset_paths(
        Path(dataset["base"]).parent,
        Path(dataset["base"]).name,
        persist_identity=persist_identity,
    )
    return paths.workspace_dir / "tasks" / f"{capability}.json"


def _write_task_record(path: Path, task: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(task, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def _task_record_lock(path: Path):
    """Serialize task registration and cancellation record mutations."""
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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
            f"Preparation Task registry is locked: {lock_path}"
        )
    try:
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


class PreparationSourceRevisionChangedError(RuntimeError):
    """The task's bound source revision changed while it was running."""


def _source_revision(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        return {"path": str(path), "exists": False, "fingerprint": ""}
    stat = path.stat()
    revision = {
        "path": str(path.resolve()),
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }
    encoded = json.dumps(revision, sort_keys=True, separators=(",", ":"))
    return {
        **revision,
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _capability_source_revision(
    dataset: dict[str, str], capability: str
) -> dict[str, Any]:
    if capability == "event":
        primary, molecules = _event_source_paths(dataset)
        paths = [primary, *([molecules] if molecules else [])]
    elif capability == "trajectory":
        paths = [dataset["trajectory"]]
    elif capability == "composition":
        paths = [dataset["species"]]
    else:
        paths = []
    artifacts = [_source_revision(path) for path in paths]
    encoded = json.dumps(
        artifacts,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "artifacts": artifacts,
    }


def _process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(
                0x1000,
                False,
                process_id,
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _process_start_token(process_id: int) -> str:
    """Return an OS process-instance token so a reused PID is never signaled."""
    if process_id <= 0:
        return ""
    if sys.platform.startswith("linux"):
        try:
            stat_text = Path(f"/proc/{process_id}/stat").read_text(
                encoding="utf-8"
            )
            command_end = stat_text.rfind(")")
            if command_end < 0:
                return ""
            # Fields after ``comm`` start at proc field 3; starttime is 22.
            fields_after_command = stat_text[command_end + 1 :].split()
            return fields_after_command[19]
        except (OSError, IndexError):
            return ""
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
            if not handle:
                return ""
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            ctypes.windll.kernel32.CloseHandle(handle)
            return str(creation.value) if ok else ""
        except (AttributeError, OSError):
            return ""
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(process_id)],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""
    return ""


def _task_process_is_owner(task: dict[str, Any]) -> bool:
    try:
        process_id = int(task.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    recorded = str(task.get("process_start_token") or "")
    return bool(
        process_id > 0
        and recorded
        and _process_is_running(process_id)
        and _process_start_token(process_id) == recorded
    )


_ACTIVE_TASK_STATES = {"running", "cancel_requested"}


def _read_preparation_task(
    dataset: dict[str, str], capability: str
) -> dict[str, Any]:
    """Read and recover one task record while holding its process lock."""
    task_path = _preparation_task_path(
        dataset,
        capability,
        persist_identity=False,
    )
    if not task_path.is_file():
        return {}
    with _task_record_lock(task_path):
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        if not isinstance(task, dict):
            return {}
        if (
            str(task.get("state") or "") in _ACTIVE_TASK_STATES
            and not _task_process_is_owner(task)
        ):
            task.update(
                {
                    "state": "interrupted",
                    "message": (
                        "Preparation worker is no longer running; "
                        "committed checkpoints may be resumed."
                    ),
                    "updated_at_epoch": int(time.time()),
                }
            )
            _write_task_record(task_path, task)
        return dict(task)


def _run_preparation_task(
    dataset: dict[str, str],
    *,
    capability: str,
    source_file: str,
    action: str,
    operation,
    report,
) -> Any:
    task_path = _preparation_task_path(dataset, capability)
    paths = resolve_dataset_paths(
        Path(dataset["base"]).parent,
        Path(dataset["base"]).name,
    )
    bound_revision = _capability_source_revision(dataset, capability)
    task: dict[str, Any] = {
        "task_version": 2,
        "dataset_id": paths.dataset_id,
        "dataset_label": Path(dataset["base"]).name,
        "folder": str(Path(dataset["base"]).parent),
        "base": str(Path(dataset["base"])),
        "capability": capability,
        "action": action,
        "state": "running",
        "pid": os.getpid(),
        "process_start_token": _process_start_token(os.getpid()),
        "source_revision": bound_revision,
        "source_artifact_revision": _source_revision(source_file),
        "started_at_epoch": int(time.time()),
        "updated_at_epoch": int(time.time()),
        "phase": "starting",
    }
    with _task_record_lock(task_path):
        try:
            existing_task = json.loads(task_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            existing_task = {}
        if (
            str(existing_task.get("state") or "") in _ACTIVE_TASK_STATES
            and _task_process_is_owner(existing_task)
        ):
            print(
                json.dumps(
                    {"existing_preparation_task": existing_task},
                    ensure_ascii=False,
                )
            )
            return dict(existing_task)
        _write_task_record(task_path, task)

    def task_report(update: dict[str, Any]) -> None:
        if _capability_source_revision(dataset, capability) != bound_revision:
            raise PreparationSourceRevisionChangedError(
                "Preparation Task source revision changed; unpublished work was discarded."
            )
        with _task_record_lock(task_path):
            try:
                current = json.loads(task_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                current = task
            if str(current.get("state") or "") == "cancel_requested":
                raise KeyboardInterrupt
            task.update(
                {
                    "phase": str(update.get("phase") or task.get("phase") or "running"),
                    "message": str(update.get("message") or ""),
                    "updated_at_epoch": int(time.time()),
                }
            )
            progress = update.get("progress")
            if isinstance(progress, (int, float)):
                task["progress"] = min(max(float(progress), 0.0), 1.0)
                task["progress_trusted"] = True
            _write_task_record(task_path, task)
        report(update)

    try:
        result = operation(task_report)
    except KeyboardInterrupt:
        task.update(
            {
                "state": "canceled",
                "updated_at_epoch": int(time.time()),
            }
        )
        with _task_record_lock(task_path):
            _write_task_record(task_path, task)
        raise
    except PreparationSourceRevisionChangedError as exc:
        task.update(
            {
                "state": "superseded",
                "message": str(exc),
                "updated_at_epoch": int(time.time()),
            }
        )
        with _task_record_lock(task_path):
            _write_task_record(task_path, task)
        raise
    except Exception as exc:
        task.update(
            {
                "state": "failed",
                "message": str(exc),
                "updated_at_epoch": int(time.time()),
            }
        )
        with _task_record_lock(task_path):
            _write_task_record(task_path, task)
        raise
    if _capability_source_revision(dataset, capability) != bound_revision:
        task.update(
            {
                "state": "superseded",
                "message": (
                    "Preparation Task source revision changed before completion; "
                    "the result is not current evidence."
                ),
                "updated_at_epoch": int(time.time()),
            }
        )
        with _task_record_lock(task_path):
            _write_task_record(task_path, task)
        raise PreparationSourceRevisionChangedError(str(task["message"]))
    with _task_record_lock(task_path):
        try:
            current = json.loads(task_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            current = task
        task.update(
            {
                "state": (
                    "canceled"
                    if str(current.get("state") or "") == "cancel_requested"
                    else "completed"
                ),
                "progress": 1.0,
                "progress_trusted": True,
                "phase": "completed",
                "updated_at_epoch": int(time.time()),
            }
        )
        _write_task_record(task_path, task)
    return result


def request_cancellation(
    case: str,
    *,
    base: str = "",
    capability: str,
) -> bool:
    """Request cancellation of one persisted Preparation Task."""
    dataset = discover_dataset(case, base)
    if capability == "all":
        results = [
            request_cancellation(
                case,
                base=base,
                capability=item,
            )
            for item in ("event", "trajectory", "composition")
        ]
        return any(results)
    task_path = _preparation_task_path(dataset, capability)
    with _task_record_lock(task_path):
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        if str(task.get("state") or "") not in _ACTIVE_TASK_STATES:
            return False
        if not _task_process_is_owner(task):
            task.update(
                {
                    "state": "interrupted",
                    "message": (
                        "Preparation worker is no longer running; "
                        "committed checkpoints may be resumed."
                    ),
                    "updated_at_epoch": int(time.time()),
                }
            )
            _write_task_record(task_path, task)
            return False
        task["state"] = "cancel_requested"
        task["cancel_requested_epoch"] = int(time.time())
        _write_task_record(task_path, task)
    try:
        process_id = int(task.get("pid") or 0)
        recorded_token = str(task.get("process_start_token") or "")
        if (
            process_id > 0
            and recorded_token
            and _process_start_token(process_id) == recorded_token
        ):
            os.kill(
                process_id,
                signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT,
            )
    except (OSError, TypeError, ValueError):
        pass
    return True


def preparation_task_status(
    case: str,
    *,
    base: str = "",
    capability: str,
) -> dict[str, Any]:
    """Return one task, reclassifying a dead persisted worker."""
    dataset = discover_dataset(case, base)
    return _read_preparation_task(dataset, capability)


def _select_event_source(
    dataset: dict[str, str],
) -> TimedEvidenceSelection:
    return select_timed_evidence(
        timeline_file=dataset["timeline"],
        reactionevent_file=dataset["reactionevent"],
        molecules_file=dataset["molecules"],
    )


def _event_source_paths(dataset: dict[str, str]) -> tuple[str, str]:
    """Resolve native precedence, retaining a path for safe cache clearing."""

    if Path(dataset["timeline"]).is_file():
        return dataset["timeline"], ""
    return (
        dataset["reactionevent"],
        dataset["molecules"] if Path(dataset["molecules"]).is_file() else "",
    )


def build_manifest(dataset: dict[str, str]) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for kind in (
        "reaction",
        "species",
        "table",
        "trajectory",
        "timeline",
        "reactionevent",
        "molecules",
    ):
        path = Path(dataset[kind])
        item: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            stat = path.stat()
            item.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        artifacts[kind] = item
    trajectory_status = (
        TRAJECTORY_INDEX_STORE.status(dataset["trajectory"])
        if artifacts["trajectory"]["exists"]
        else {"state": "missing"}
    )
    composition_status = (
        SPECIES_COMPOSITION_STORE.status(dataset["species"])
        if artifacts["species"]["exists"]
        else {"state": "missing"}
    )
    event_primary, event_molecules = _event_source_paths(dataset)
    if Path(event_primary).is_file():
        event_status = EVENT_EVIDENCE_STORE.status(
            event_primary, event_molecules, metadata_only=True
        )
        try:
            timed_evidence = {
                "state": "ready",
                **_select_event_source(dataset).as_dict(),
            }
        except TimedEvidenceDataError as exc:
            timed_evidence = {
                "state": exc.state,
                "message": str(exc),
            }
    else:
        event_status = {"state": "missing_source"}
        timed_evidence = {
            "state": "missing",
            "message": "timed reaction evidence is missing",
        }
    paths = resolve_dataset_paths(
        Path(dataset["base"]).parent, Path(dataset["base"]).name
    )
    settings_path = paths.workspace_dir / "dataset-settings.json"
    return {
        "manifest_version": 3,
        "dataset_id": paths.dataset_id,
        "base": dataset["base"],
        "updated_at_epoch": int(time.time()),
        "artifacts": artifacts,
        "indexes": {
            "trajectory": trajectory_status,
            "composition": composition_status,
            "event": event_status,
            "rng_events": timed_evidence,
        },
        "settings": {
            "path": str(settings_path),
            "exists": settings_path.is_file(),
        },
    }


def write_manifest(dataset: dict[str, str]) -> Path:
    target = _manifest_path(dataset)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(build_manifest(dataset), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def _capacity_check(
    dataset: dict[str, str],
    *,
    include_trajectory: bool,
    include_composition: bool,
    include_event: bool,
) -> None:
    workspace = resolve_dataset_paths(
        Path(dataset["base"]).parent,
        Path(dataset["base"]).name,
    ).workspace_dir
    storage = inspect_workspace_storage(workspace)
    free = storage.free_bytes
    if free is None:
        raise RuntimeError(
            f"cannot inspect Dataset Workspace capacity at {workspace}"
        )
    required = 1024**3
    if include_trajectory and Path(dataset["trajectory"]).is_file():
        required += max(256 * 1024**2, Path(dataset["trajectory"]).stat().st_size // 100)
    if include_composition and Path(dataset["species"]).is_file():
        required += max(128 * 1024**2, Path(dataset["species"]).stat().st_size // 2)
    if include_event:
        event_primary, event_molecules = _event_source_paths(dataset)
        event_bytes = sum(
            Path(path).stat().st_size
            for path in (event_primary, event_molecules)
            if path and Path(path).is_file()
        )
        required += max(128 * 1024**2, event_bytes * 2)
        try:
            selection = _select_event_source(dataset)
        except TimedEvidenceDataError:
            selection = None
        if selection is not None:
            required += native_membership_bytes(selection)
    if free < required:
        raise RuntimeError(
            f"insufficient Dataset Workspace capacity: need about {required / 1024**3:.1f} GiB, "
            f"have {free / 1024**3:.1f} GiB at {workspace}"
        )


def run_preparation(
    *,
    action: str,
    case: str,
    capability: str = "all",
    base: str = "",
) -> int:
    capability = str(capability or "all")
    internal_capability = (
        "composition"
        if capability == "element-distribution"
        else capability
    )
    try:
        dataset = discover_dataset(case, base)
    except (FileNotFoundError, RuntimeError) as exc:
        raise RuntimeError(str(exc)) from exc
    if action == "cancel":
        canceled = request_cancellation(
            case,
            base=base,
            capability=internal_capability,
        )
        print(
            f"Cancellation requested for {capability} Preparation Task."
            if canceled
            else f"No active {capability} Preparation Task."
        )
        return 0
    selected_trajectory = bool(
        action in {"build", "rebuild"}
        and (
            internal_capability == "trajectory"
            or (
                internal_capability == "all"
                and Path(dataset["trajectory"]).is_file()
            )
        )
    )
    selected_composition = bool(
        action in {"build", "rebuild"}
        and (
            internal_capability == "composition"
            or (
                internal_capability == "all"
                and Path(dataset["species"]).is_file()
            )
        )
    )
    selected_event = bool(
        action in {"build", "rebuild"}
        and (
            internal_capability == "event"
            or (
                internal_capability == "all"
                and bool(
                    Path(dataset["timeline"]).is_file()
                    or Path(dataset["reactionevent"]).is_file()
                )
            )
        )
    )
    trajectory_needs_build = (
        selected_trajectory
        and Path(dataset["trajectory"]).is_file()
        and TRAJECTORY_INDEX_STORE.status(dataset["trajectory"])["state"] != "ready"
    )
    composition_needs_build = (
        selected_composition
        and Path(dataset["species"]).is_file()
        and SPECIES_COMPOSITION_STORE.status(dataset["species"])["state"] != "ready"
    )
    event_primary, event_molecules = _event_source_paths(dataset)
    event_needs_build = bool(
        selected_event
        and Path(event_primary).is_file()
        and EVENT_EVIDENCE_STORE.status(
            event_primary, event_molecules, metadata_only=True
        )["state"]
        != "ready"
    )
    if action in {"build", "rebuild"}:
        selected_tasks = (
            ("trajectory", selected_trajectory),
            ("composition", selected_composition),
            ("event", selected_event),
        )
        for task_capability, selected in selected_tasks:
            if not selected:
                continue
            existing_task = _read_preparation_task(
                dataset, task_capability
            )
            if str(existing_task.get("state") or "") in _ACTIVE_TASK_STATES:
                print(
                    json.dumps(
                        {"existing_preparation_task": existing_task},
                        ensure_ascii=False,
                    )
                )
                return 0
    if action in {"build", "rebuild"}:
        _capacity_check(
            dataset,
            include_trajectory=trajectory_needs_build,
            include_composition=composition_needs_build,
            include_event=event_needs_build,
        )

    def report(update: dict[str, Any]) -> None:
        print(f"[{float(update.get('progress', 0.0)) * 100:6.2f}%] {update.get('message', '')}", flush=True)

    if action in {"clear", "rebuild"}:
        target = internal_capability
        try:
            if target in {"trajectory", "all"} and Path(dataset["trajectory"]).is_file():
                clear_index(dataset["trajectory"], kind="trajectory")
            if target in {"composition", "all"} and Path(dataset["species"]).is_file():
                SPECIES_COMPOSITION_STORE.clear(dataset["species"])
            if target in {"event", "all"}:
                EVENT_EVIDENCE_STORE.clear(
                    event_primary, event_molecules
                )
        except IndexBuildInProgressError:
            for task_capability in ("trajectory", "composition", "event"):
                existing_task = _read_preparation_task(
                    dataset, task_capability
                )
                if str(existing_task.get("state") or "") in _ACTIVE_TASK_STATES:
                    print(
                        json.dumps(
                            {"existing_preparation_task": existing_task},
                            ensure_ascii=False,
                        )
                    )
                    return 0
            raise
        if action == "clear":
            print(f"Manifest: {write_manifest(dataset)}")
            return 0
    if action in {"build", "rebuild"}:
        try:
            if selected_trajectory:
                if not Path(dataset["trajectory"]).is_file():
                    raise FileNotFoundError(f"trajectory file not found: {dataset['trajectory']}")
                _run_preparation_task(
                    dataset,
                    capability="trajectory",
                    source_file=dataset["trajectory"],
                    action=action,
                    operation=lambda task_report: TRAJECTORY_INDEX_STORE.build(
                        dataset["trajectory"],
                        progress_callback=task_report,
                    ),
                    report=report,
                )
            if selected_composition:
                if not Path(dataset["species"]).is_file():
                    raise FileNotFoundError(f"species file not found: {dataset['species']}")
                _run_preparation_task(
                    dataset,
                    capability="composition",
                    source_file=dataset["species"],
                    action=action,
                    operation=lambda task_report: SPECIES_COMPOSITION_STORE.build(
                        dataset["species"],
                        progress_callback=task_report,
                    ),
                    report=report,
                )
            if selected_event:
                try:
                    selection = _select_event_source(dataset)
                except TimedEvidenceDataError as exc:
                    if exc.state == "missing":
                        raise FileNotFoundError(
                            "timed reaction evidence not found: expected "
                            f"{dataset['timeline']} or "
                            f"{dataset['reactionevent']}"
                        ) from exc
                    raise
                if not Path(selection.primary_file).is_file():
                    raise FileNotFoundError(
                        "timed reaction evidence not found: "
                        f"{selection.primary_file}"
                    )
                _run_preparation_task(
                    dataset,
                    capability="event",
                    source_file=selection.primary_file,
                    action=action,
                    operation=lambda task_report: EVENT_EVIDENCE_STORE.build(
                        selection.primary_file,
                        selection.molecules_file,
                        progress_callback=task_report,
                    ),
                    report=report,
                )
        except KeyboardInterrupt:
            print("Preparation canceled; committed checkpoints were preserved.")
            write_manifest(dataset)
            return 130
    manifest = write_manifest(dataset)
    print(json.dumps(build_manifest(dataset)["indexes"], ensure_ascii=False, indent=2))
    print(f"Manifest: {manifest}")
    return 0


def configure_parser(
    parser: argparse.ArgumentParser,
    *,
    handler: Any = None,
) -> argparse.ArgumentParser:
    """Attach the single formal preparation command grammar to a parser."""
    operations = parser.add_subparsers(dest="prepare_action", required=True)

    def add_dataset_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "case", help="Dataset directory or dataset base path"
        )
        command.add_argument(
            "--base",
            default="",
            help="Dataset base name when a directory contains multiple runs",
        )
        if handler is not None:
            command.set_defaults(func=handler)

    status = operations.add_parser("status")
    add_dataset_arguments(status)
    for action in ("build", "rebuild", "cancel", "clear"):
        command = operations.add_parser(action)
        command.add_argument(
            "capability",
            choices=("trajectory", "element-distribution", "event", "all"),
        )
        add_dataset_arguments(command)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare persistent ReacNet Scope indexes offline"
    )
    return configure_parser(parser)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_preparation(
            action=str(args.prepare_action),
            capability=str(getattr(args, "capability", "all")),
            case=str(args.case),
            base=str(args.base or ""),
        )
    except (FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
