from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from reacnet_scope import dir_browser, indexes
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import (
    IndexBuildInProgressError,
    IndexInvalidError,
    TRAJECTORY_INDEX_STORE,
    WorkspacePolicy,
    inspect_workspace_storage,
    resolve_dataset_paths,
)
from reacnet_scope import prepare
from scripts.webapp_dash.app import create_app
from reacnet_scope import services as svc


def _layout_node_by_id(node, component_id: str):
    if isinstance(node, dict):
        if (node.get("props") or {}).get("id") == component_id:
            return node
        for value in node.values():
            found = _layout_node_by_id(value, component_id)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _layout_node_by_id(value, component_id)
            if found is not None:
                return found
    return None


def _seed_legacy_workspace(
    workspace_root: Path,
    base: Path,
    artifacts: dict[str, Path],
    *,
    index_content: bytes,
) -> tuple[str, Path, Path]:
    legacy_id = hashlib.sha256(str(base).encode("utf-8")).hexdigest()[:20]
    legacy_workspace = workspace_root / "datasets" / legacy_id
    legacy_workspace.mkdir(parents=True)
    descriptors = {}
    for name, artifact in artifacts.items():
        stat = artifact.stat()
        descriptors[name] = {
            "path": str(artifact),
            "exists": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    (legacy_workspace / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "dataset_id": legacy_id,
                "base": str(base),
                "artifacts": descriptors,
            }
        ),
        encoding="utf-8",
    )
    legacy_index = legacy_workspace / "events.sqlite3"
    legacy_index.write_bytes(index_content)
    return legacy_id, legacy_workspace, legacy_index


def test_workspace_storage_inspection_uses_nearest_existing_ancestor(
    tmp_path,
) -> None:
    target = tmp_path / "not-created" / "datasets" / "example"

    status = inspect_workspace_storage(target)

    assert status.target == target
    assert status.existing_ancestor == tmp_path
    assert status.writable is True
    assert status.free_bytes is not None


def test_first_identity_registry_adopts_matching_legacy_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(workspace_root))
    source = tmp_path / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    retired_route = tmp_path / "run.route"
    retired_route.write_text("legacy route evidence\n", encoding="utf-8")
    base = tmp_path / "run"
    legacy_id, legacy_workspace, legacy_index = _seed_legacy_workspace(
        workspace_root,
        base,
        {"species": source, "route": retired_route},
        index_content=b"legacy-index-must-stay-in-place",
    )

    resolved = resolve_dataset_paths(source)

    assert resolved.dataset_id == legacy_id
    assert resolved.workspace_dir == legacy_workspace
    assert legacy_index.read_bytes() == b"legacy-index-must-stay-in-place"
    registry = json.loads(
        (workspace_root / "workspace-manifest.json").read_text(encoding="utf-8")
    )
    assert registry["datasets"][0]["dataset_id"] == legacy_id


def test_first_identity_registry_rejects_legacy_workspace_for_changed_source(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(workspace_root))
    source = tmp_path / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    base = tmp_path / "run"
    legacy_id, legacy_workspace, legacy_index = _seed_legacy_workspace(
        workspace_root,
        base,
        {"species": source},
        index_content=b"index-for-an-older-source-revision",
    )
    source.write_text("Timestep 0: [O] 200\n", encoding="utf-8")

    resolved = resolve_dataset_paths(source)

    assert resolved.dataset_id != legacy_id
    assert resolved.workspace_dir != legacy_workspace
    assert legacy_index.read_bytes() == b"index-for-an-older-source-revision"


def test_first_identity_registry_adopts_legacy_workspace_after_directory_move(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original"
    original.mkdir()
    source = original / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    legacy_id, _legacy_workspace, _legacy_index = _seed_legacy_workspace(
        original / ".reacnet-scope",
        original / "run",
        {"species": source},
        index_content=b"legacy-index-moved-with-sidecar",
    )
    moved = tmp_path / "moved"
    shutil.move(str(original), moved)

    resolved = resolve_dataset_paths(moved / "run.species")

    moved_workspace = moved / ".reacnet-scope" / "datasets" / legacy_id
    assert resolved.dataset_id == legacy_id
    assert resolved.workspace_dir == moved_workspace
    assert (moved_workspace / "events.sqlite3").read_bytes() == (
        b"legacy-index-moved-with-sidecar"
    )


def test_first_identity_registry_rejects_copied_legacy_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original"
    original.mkdir()
    source = original / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    legacy_id, _legacy_workspace, legacy_index = _seed_legacy_workspace(
        original / ".reacnet-scope",
        original / "run",
        {"species": source},
        index_content=b"legacy-index-belongs-to-original",
    )
    copied = tmp_path / "copied"
    shutil.copytree(original, copied)

    resolved = resolve_dataset_paths(copied / "run.species")

    copied_legacy_index = (
        copied
        / ".reacnet-scope"
        / "datasets"
        / legacy_id
        / "events.sqlite3"
    )
    assert resolved.dataset_id != legacy_id
    assert legacy_index.read_bytes() == b"legacy-index-belongs-to-original"
    assert copied_legacy_index.read_bytes() == (
        b"legacy-index-belongs-to-original"
    )


def test_existing_identity_registry_does_not_adopt_legacy_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(workspace_root))
    source = tmp_path / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    base = tmp_path / "run"
    legacy_id, legacy_workspace, legacy_index = _seed_legacy_workspace(
        workspace_root,
        base,
        {"species": source},
        index_content=b"legacy-index-outside-migration-window",
    )
    (workspace_root / "workspace-manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "datasets": [
                    {
                        "dataset_id": "0123456789abcdefabcd",
                        "base_name": "other-run",
                        "active_path": str(tmp_path / "other-run"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_dataset_paths(source)

    assert resolved.dataset_id != legacy_id
    assert resolved.workspace_dir != legacy_workspace
    assert legacy_index.read_bytes() == (
        b"legacy-index-outside-migration-window"
    )


def test_first_identity_registry_rejects_ambiguous_legacy_workspaces(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(workspace_root))
    source = tmp_path / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    direct_id, direct_workspace, direct_index = _seed_legacy_workspace(
        workspace_root,
        tmp_path / "run",
        {"species": source},
        index_content=b"direct-legacy-index",
    )
    old_parent = tmp_path / "old"
    old_parent.mkdir()
    old_source = old_parent / "run.species"
    shutil.copy2(source, old_source)
    moved_id, moved_workspace, moved_index = _seed_legacy_workspace(
        workspace_root,
        old_parent / "run",
        {"species": old_source},
        index_content=b"moved-legacy-index",
    )
    shutil.rmtree(old_parent)

    resolved = resolve_dataset_paths(source)

    assert resolved.dataset_id not in {direct_id, moved_id}
    assert resolved.workspace_dir not in {direct_workspace, moved_workspace}
    assert direct_index.read_bytes() == b"direct-legacy-index"
    assert moved_index.read_bytes() == b"moved-legacy-index"


def test_dataset_identity_distinguishes_copy_made_after_unresolved_move(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original"
    original.mkdir()
    source = original / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    original_id = resolve_dataset_paths(source).dataset_id

    moved = tmp_path / "moved"
    shutil.move(str(original), moved)
    copied = tmp_path / "copied"
    shutil.copytree(moved, copied)

    moved_id = resolve_dataset_paths(moved / "run.species").dataset_id
    copied_id = resolve_dataset_paths(copied / "run.species").dataset_id

    assert moved_id == original_id
    assert copied_id != original_id


def test_dataset_identity_accepts_cross_filesystem_move_anchor(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original-device"
    original.mkdir()
    source = original / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    original_id = resolve_dataset_paths(source).dataset_id
    moved = tmp_path / "moved-device"
    original.rename(moved)
    real_anchor = indexes._dataset_anchor_token

    monkeypatch.setattr(
        indexes,
        "_dataset_anchor_token",
        lambda base: (
            f"999999:{real_anchor(base).partition(':')[2]}"
            if real_anchor(base)
            else ""
        ),
    )

    assert resolve_dataset_paths(moved / "run.species").dataset_id == original_id


def test_cross_device_same_name_with_different_content_gets_new_identity(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original-device"
    original.mkdir()
    source = original / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    original_id = resolve_dataset_paths(source).dataset_id
    source.unlink()
    replacement = tmp_path / "replacement-device" / "run.species"
    replacement.parent.mkdir()
    replacement.write_text("Timestep 0: [O] 9\n", encoding="utf-8")
    real_anchor = indexes._dataset_anchor_token
    monkeypatch.setattr(
        indexes,
        "_dataset_anchor_token",
        lambda base: (
            f"999999:{real_anchor(base).partition(':')[2]}"
            if real_anchor(base)
            else ""
        ),
    )

    assert resolve_dataset_paths(replacement).dataset_id != original_id


def test_dataset_identity_lock_failure_never_falls_back_to_path_hash(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    source = tmp_path / "run.species"
    source.write_text("Timestep 0: [H] 1\n", encoding="utf-8")

    class BrokenLock:
        def __enter__(self):
            raise indexes.IndexBuildInProgressError("busy")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        indexes, "_workspace_identity_lock", lambda _registry: BrokenLock()
    )

    with pytest.raises(RuntimeError, match="could not be persisted"):
        resolve_dataset_paths(source)


def test_preparation_discovers_species_only_dataset(tmp_path) -> None:
    species = tmp_path / "species-only.lammpstrj.species"
    species.write_text("Timestep 0: [H] 2\n", encoding="utf-8")

    dataset = prepare.discover_dataset(str(tmp_path))

    assert dataset["species"] == str(species)


def test_preparation_discovers_trajectory_only_dataset(tmp_path) -> None:
    trajectory = tmp_path / "trajectory-only.lammpstrj"
    trajectory.write_text("ITEM: TIMESTEP\n0\n", encoding="utf-8")

    dataset = prepare.discover_dataset(str(tmp_path))

    assert dataset["trajectory"] == str(trajectory)


def test_corrupt_dataset_identity_cannot_escape_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(workspace))
    source = tmp_path / "run.species"
    source.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    workspace.mkdir()
    (workspace / "workspace-manifest.json").write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "dataset_id": "../../outside",
                        "base_name": "run",
                        "active_path": str(tmp_path / "run"),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    paths = resolve_dataset_paths(source, persist_identity=False)

    assert paths.workspace_dir.parent == workspace / "datasets"
    assert paths.dataset_id != "../../outside"
    assert paths.workspace_dir.resolve().is_relative_to(workspace.resolve())


def test_cancellation_does_not_signal_pid_with_different_start_token(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    task_path = prepare._preparation_task_path(
        prepare.discover_dataset(str(tmp_path), base.name),
        "event",
    )
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 4242,
                "process_start_token": "old-process",
            }
        ),
        encoding="utf-8",
    )
    signaled: list[tuple[int, int]] = []
    monkeypatch.setattr(prepare, "_process_start_token", lambda _pid: "new-process")
    monkeypatch.setattr(prepare.os, "kill", lambda pid, sig: signaled.append((pid, sig)))

    assert prepare.request_cancellation(
        str(tmp_path), base=base.name, capability="event"
    ) is False
    assert all(signal_value == 0 for _pid, signal_value in signaled)
    assert json.loads(task_path.read_text(encoding="utf-8"))["state"] == "interrupted"


def test_cache_management_is_visible_without_global_path_overrides() -> None:
    app = create_app()
    layout = app.server.test_client().get("/_dash-layout").get_json()
    cache_card = _layout_node_by_id(layout, "data-cache-management")
    details = _layout_node_by_id(layout, "data-advanced-tools")
    workspace_meta = _layout_node_by_id(layout, "data-prep-cache-meta")

    assert cache_card is not None
    assert cache_card["type"] == "Div"
    assert details is None
    assert workspace_meta is not None
    cache_text = json.dumps(cache_card, ensure_ascii=False)
    assert "Analysis Capability 与 Preparation Task" in cache_text
    assert "data-preparation-tasks" in cache_text
    assert "危险操作：清理派生索引" in cache_text
    for component_id in (
        "data-prep-status",
        "data-prep-event-command",
        "data-prep-event-copy",
        "data-prep-trajectory-command",
        "data-prep-trajectory-copy",
        "data-prep-composition-command",
        "data-prep-composition-copy",
        "data-prep-event-btn",
        "data-prep-trajectory-btn",
        "data-prep-composition-btn",
        "data-prep-cancel-btn",
        "data-clear-event-btn",
        "data-clear-trajectory-btn",
        "data-clear-composition-btn",
    ):
        assert component_id in cache_text
    assert "路径覆盖与高级设置" not in cache_text
    assert "data-global-min-tp" not in cache_text
    assert "data-overrides-apply-btn" not in cache_text
    assert "等效 CLI 命令" in cache_text
    assert "data-rng-event-command" not in cache_text
    assert "元素分布索引" in cache_text
    assert "C/O/Cl 组成索引" not in cache_text


def test_cache_build_controls_use_a_cancellable_background_callback() -> None:
    app = create_app()
    dependencies = app.server.test_client().get("/_dash-dependencies").get_json()
    dependency = next(
        item
        for item in dependencies
        if item.get("output") == "data-prep-action-alert.children"
    )

    assert [item["id"] for item in dependency["inputs"]] == [
        "data-prep-event-btn",
        "data-prep-trajectory-btn",
        "data-prep-composition-btn",
    ]
    assert dependency.get("background") == {"interval": 1000}
    assert dependency["running"]["running"]["data-prep-cancel-btn.disabled"] is False
    assert dependency["running"]["runningOff"]["data-prep-cancel-btn.disabled"] is True


def test_cache_build_background_callback_dispatches_and_returns(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    candidate = {
        "folder": str(tmp_path),
        "base": str(base),
        "label": base.name,
    }
    app = create_app()
    client = app.server.test_client()
    payload = {
        "output": "data-prep-action-alert.children",
        "outputs": {
            "id": "data-prep-action-alert",
            "property": "children",
        },
        "changedPropIds": ["data-prep-event-btn.n_clicks"],
        "inputs": [
            {"id": "data-prep-event-btn", "property": "n_clicks", "value": 1},
            {
                "id": "data-prep-trajectory-btn",
                "property": "n_clicks",
                "value": 0,
            },
            {
                "id": "data-prep-composition-btn",
                "property": "n_clicks",
                "value": 0,
            },
        ],
        "state": [
            {
                "id": "dataset-browser-candidate",
                "property": "data",
                "value": candidate,
            },
            {"id": "app-store", "property": "data", "value": {}},
        ],
    }

    launched = client.post("/_dash-update-component", json=payload)
    assert launched.status_code == 200
    job = launched.get_json()
    assert job.get("cacheKey")
    assert job.get("job")

    completed = None
    for _ in range(100):
        polled = client.post(
            "/_dash-update-component"
            f"?cacheKey={job['cacheKey']}&job={job['job']}",
            json=payload,
        )
        assert polled.status_code == 200
        body = polled.get_json() or {}
        if (body.get("response") or {}).get("data-prep-action-alert"):
            completed = body
            break
        time.sleep(0.02)

    assert completed is not None
    assert "事件索引已建立" in json.dumps(
        completed,
        ensure_ascii=False,
    )
    paths = resolve_dataset_paths(tmp_path, base.name)
    assert tmp_path / ".reacnet-scope" in paths.workspace_dir.parents
    assert paths.event_index.is_file()


def test_normalise_recent_datasets_ignores_malformed_loaded_at() -> None:
    records = svc.normalise_recent_datasets(
        [
            {"folder": "/valid", "base": "/valid/run", "label": "valid", "loaded_at": 3},
            {"folder": "/bad", "base": "/bad/run", "label": "bad", "loaded_at": "not-a-time"},
        ]
    )

    assert records == [
        {"folder": "/valid", "base": "/valid/run", "label": "valid", "loaded_at": 3}
    ]


def test_dataset_preparation_status_and_safe_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    trajectory = tmp_path / "run.lammpstrj"
    reaction = Path(f"{trajectory}.reactionabcd")
    species = Path(f"{trajectory}.species")
    reactionevent = Path(f"{trajectory}.reactionevent.csv")
    molecules = Path(f"{trajectory}.molecules.csv")
    trajectory.write_text("ITEM: TIMESTEP\n0\n", encoding="utf-8")
    reaction.write_text("1 C->O\n", encoding="utf-8")
    species.write_text("Timestep 0: C 1\n", encoding="utf-8")
    reactionevent.write_text("Timestep_Index,Reactant,Product\n0,C,O\n", encoding="utf-8")
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n0,C,0,\n10,O,0,\n",
        encoding="utf-8",
    )
    TRAJECTORY_INDEX_STORE.build(str(trajectory))

    payload = svc.dataset_preparation_status(str(tmp_path))
    assert payload["dataset_id"]
    assert "/datasets/" in payload["workspace_path"]
    assert payload["workspace_resolved"] is True
    assert payload["workspace_writable"] is True
    assert payload["events"]["state"] == "needs_preparation"
    assert payload["events"]["source_available"] is True
    assert payload["trajectory"]["state"] == "ready"
    assert payload["trajectory"]["source_available"] is True
    assert payload["rng_event_command"] == "--reaction-event --show-molecule-time"
    assert payload["event_command"].startswith("reacnet-scope prepare ")
    assert "reacnet-scope-prepare" not in payload["event_command"]
    assert "uv run" not in payload["event_command"]

    cleared = svc.clear_dataset_index(str(tmp_path), kind="trajectory")
    assert cleared["released_bytes"] > 0
    assert trajectory.exists()
    assert TRAJECTORY_INDEX_STORE.status(str(trajectory))["state"] == "missing"


def test_dash_equivalent_prepare_command_runs_through_the_installed_cli(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    payload = svc.dataset_preparation_status(str(tmp_path), base=str(base))
    environment = os.environ.copy()
    environment.pop("REACNET_SCOPE_CACHE_DIR", None)

    completed = subprocess.run(
        shlex.split(payload["event_command"]),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "ready"


def _event_only_dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "run.lammpstrj"
    Path(f"{base}.reactionabcd").write_text("1 [H]+[O]->[H][O]\n", encoding="utf-8")
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[H]+[O],[H][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[H],0,\n"
        "0,[O],1,\n"
        "10,[H][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    return base, reactionevent, molecules


def test_prepare_reports_ambiguous_dataset_as_cli_error(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    Path(f"{tmp_path / 'run-a.lammpstrj'}.reactionabcd").touch()
    Path(f"{tmp_path / 'run-b.lammpstrj'}.reactionabcd").touch()

    try:
        prepare.main(["build", "event", str(tmp_path)])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse errors must terminate the command
        raise AssertionError("ambiguous dataset was accepted")

    stderr = capsys.readouterr().err
    assert "dataset directory is ambiguous; pass --base" in stderr
    assert "Traceback" not in stderr


def test_installed_cli_exposes_prepare_command(tmp_path) -> None:
    base = tmp_path / "run.lammpstrj"
    Path(f"{base}.reactionabcd").write_text(
        "1 [H]+[O]->[H][O]\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["REACNET_SCOPE_CACHE_DIR"] = str(tmp_path / "workspace-root")
    executable = Path(sys.executable).with_name("reacnet-scope")

    completed = subprocess.run(
        [
            str(executable),
            "prepare",
            "status",
            str(tmp_path),
            "--base",
            base.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Manifest:" in completed.stdout


def test_installed_prepare_exposes_formal_operations_without_route_modes() -> None:
    executable = Path(sys.executable).with_name("reacnet-scope")

    completed = subprocess.run(
        [str(executable), "prepare", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    for operation in ("status", "build", "rebuild", "cancel", "clear"):
        assert operation in completed.stdout
    assert "route" not in completed.stdout.casefold()


def test_core_preparation_parser_has_no_route_mode(capsys) -> None:
    try:
        prepare.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover - argparse help always exits
        raise AssertionError("prepare --help did not exit")

    assert "route" not in capsys.readouterr().out.casefold()


def test_installed_prepare_cancel_is_idempotent_without_an_active_task(
    tmp_path,
) -> None:
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    environment = os.environ.copy()
    environment.pop("REACNET_SCOPE_CACHE_DIR", None)
    executable = Path(sys.executable).with_name("reacnet-scope")

    completed = subprocess.run(
        [
            str(executable),
            "prepare",
            "cancel",
            "event",
            str(tmp_path),
            "--base",
            base.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "No active event Preparation Task" in completed.stdout


def test_cancel_all_marks_every_active_preparation_task(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    tasks = resolve_dataset_paths(tmp_path, base.name).workspace_dir / "tasks"
    tasks.mkdir(parents=True)
    for capability in ("event", "trajectory"):
        (tasks / f"{capability}.json").write_text(
            json.dumps(
                {
                    "state": "running",
                    "pid": 0,
                    "capability": capability,
                }
            ),
            encoding="utf-8",
        )

    assert prepare.request_cancellation(
        str(tmp_path),
        base=base.name,
        capability="all",
    ) is False

    assert {
        json.loads((tasks / f"{capability}.json").read_text())["state"]
        for capability in ("event", "trajectory")
    } == {"interrupted"}


def test_duplicate_active_preparation_task_is_not_overwritten(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base, reactionevent, _molecules = _event_only_dataset(tmp_path)
    dataset = prepare.discover_dataset(str(tmp_path), base.name)
    task_path = (
        resolve_dataset_paths(tmp_path, base.name).workspace_dir
        / "tasks"
        / "event.json"
    )
    task_path.parent.mkdir(parents=True)
    original = {
        "state": "running",
        "pid": os.getpid(),
        "process_start_token": prepare._process_start_token(os.getpid()),
        "capability": "event",
    }
    task_path.write_text(json.dumps(original), encoding="utf-8")
    operation_called = False

    def operation(_report):
        nonlocal operation_called
        operation_called = True

    returned = prepare._run_preparation_task(
        dataset,
        capability="event",
        source_file=str(reactionevent),
        action="build",
        operation=operation,
        report=lambda _update: None,
    )

    assert operation_called is False
    assert returned == original
    assert json.loads(task_path.read_text(encoding="utf-8")) == original


def test_task_status_reclassifies_dead_worker_and_preserves_checkpoint(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    dataset = prepare.discover_dataset(str(tmp_path), base.name)
    task_path = prepare._preparation_task_path(dataset, "event")
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 99999999,
                "process_start_token": "gone",
                "checkpoint": {"source_offset": 42},
            }
        ),
        encoding="utf-8",
    )

    status = prepare.preparation_task_status(
        str(tmp_path), base=base.name, capability="event"
    )

    assert status["state"] == "interrupted"
    assert status["checkpoint"] == {"source_offset": 42}
    assert json.loads(task_path.read_text(encoding="utf-8"))["state"] == "interrupted"


def test_rebuild_returns_active_task_before_clearing_index(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    dataset = prepare.discover_dataset(str(tmp_path), base.name)
    task_path = prepare._preparation_task_path(dataset, "event")
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": os.getpid(),
                "process_start_token": prepare._process_start_token(os.getpid()),
                "capability": "event",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prepare.EVENT_EVIDENCE_STORE,
        "clear",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active rebuild must not clear")
        ),
    )

    assert prepare.run_preparation(
        action="rebuild",
        case=str(tmp_path),
        base=base.name,
        capability="event",
    ) == 0
    assert "existing_preparation_task" in capsys.readouterr().out


def test_macos_process_start_token_uses_process_creation_text(
    monkeypatch,
) -> None:
    completed = subprocess.CompletedProcess(
        ["ps"], 0, stdout="Mon Aug  3 12:34:56 2026\n", stderr=""
    )
    monkeypatch.setattr(prepare.sys, "platform", "darwin")
    monkeypatch.setattr(prepare.subprocess, "run", lambda *_args, **_kwargs: completed)

    assert prepare._process_start_token(321) == "Mon Aug  3 12:34:56 2026"


def test_installed_prepare_cancels_an_active_task_and_records_the_result(
    tmp_path,
) -> None:
    base = tmp_path / "run.lammpstrj"
    Path(f"{base}.reactionabcd").write_text("1 [C]->[C]\n", encoding="utf-8")
    Path(f"{base}.species").write_text(
        "".join(
            f"Timestep {timestep}: [C] 1 [O] 1\n"
            for timestep in range(300_000)
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("REACNET_SCOPE_CACHE_DIR", None)
    executable = Path(sys.executable).with_name("reacnet-scope")
    paths = resolve_dataset_paths(tmp_path, base.name)
    task_path = paths.workspace_dir / "tasks" / "composition.json"
    builder = subprocess.Popen(
        [
            str(executable),
            "prepare",
            "build",
            "element-distribution",
            str(tmp_path),
            "--base",
            base.name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not task_path.is_file():
            if builder.poll() is not None:
                break
            time.sleep(0.01)
        assert task_path.is_file(), builder.communicate(timeout=5)

        canceled = subprocess.run(
            [
                str(executable),
                "prepare",
                "cancel",
                "element-distribution",
                str(tmp_path),
                "--base",
                base.name,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        stdout, stderr = builder.communicate(timeout=10)
    finally:
        if builder.poll() is None:
            builder.terminate()
            builder.wait(timeout=5)

    assert canceled.returncode == 0, canceled.stderr
    assert "Cancellation requested" in canceled.stdout
    assert builder.returncode == 130, (stdout, stderr)
    assert json.loads(task_path.read_text(encoding="utf-8"))["state"] == "canceled"


def test_project_exports_only_the_unified_reacnet_scope_command() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    assert project["project"]["scripts"] == {
        "reacnet-scope": "scripts.rng_query_cli:main"
    }


def test_unified_cli_exposes_dash_serve_options() -> None:
    executable = Path(sys.executable).with_name("reacnet-scope")

    completed = subprocess.run(
        [str(executable), "serve", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--host" in completed.stdout
    assert "--port" in completed.stdout
    assert "Dash" in completed.stdout


def test_installed_prepare_uses_local_sidecar_without_cache_override(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    environment = os.environ.copy()
    environment.pop("REACNET_SCOPE_CACHE_DIR", None)
    executable = Path(sys.executable).with_name("reacnet-scope")

    completed = subprocess.run(
        [
            str(executable),
            "prepare",
            "build",
            "event",
            str(tmp_path),
            "--base",
            base.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    paths = resolve_dataset_paths(tmp_path, base.name)
    assert tmp_path / ".reacnet-scope" in paths.workspace_dir.parents
    assert paths.event_index.is_file()

    source_bytes = Path(f"{base}.reactionevent.csv").read_bytes()
    cleared = subprocess.run(
        [
            str(executable),
            "prepare",
            "clear",
            "event",
            str(tmp_path),
            "--base",
            base.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert cleared.returncode == 0, cleared.stderr
    assert Path(f"{base}.reactionevent.csv").read_bytes() == source_bytes
    assert not paths.event_index.exists()


def test_local_workspace_identity_survives_a_dataset_directory_move(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original"
    original.mkdir()
    base, _reactionevent, _molecules = _event_only_dataset(original)
    before = resolve_dataset_paths(original, base.name)

    moved = tmp_path / "moved"
    original.rename(moved)
    after = resolve_dataset_paths(moved, base.name)

    assert after.dataset_id == before.dataset_id
    assert after.workspace_dir == moved / ".reacnet-scope" / "datasets" / before.dataset_id


def test_active_dataset_copy_gets_an_independent_move_stable_identity(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original"
    original.mkdir()
    base, _reactionevent, _molecules = _event_only_dataset(original)
    original_paths = resolve_dataset_paths(original, base.name)

    copied = tmp_path / "copied"
    shutil.copytree(original, copied)
    copied_paths = resolve_dataset_paths(copied, base.name)
    assert copied_paths.dataset_id != original_paths.dataset_id

    moved_copy = tmp_path / "moved-copy"
    copied.rename(moved_copy)
    moved_paths = resolve_dataset_paths(moved_copy, base.name)

    assert moved_paths.dataset_id == copied_paths.dataset_id
    assert resolve_dataset_paths(original, base.name).dataset_id == original_paths.dataset_id


def test_moved_dataset_reuses_compatible_sidecar_indexes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original"
    original.mkdir()
    base, reactionevent, molecules = _event_only_dataset(original)
    assert prepare.main(["build", "event", str(original)]) == 0
    before = resolve_dataset_paths(original, base.name)

    moved = tmp_path / "moved"
    original.rename(moved)
    moved_reactionevent = moved / reactionevent.name
    moved_molecules = moved / molecules.name

    status = EVENT_EVIDENCE_STORE.status(
        str(moved_reactionevent),
        str(moved_molecules),
    )

    assert resolve_dataset_paths(moved, base.name).dataset_id == before.dataset_id
    assert status["state"] == "ready"


def test_moved_dataset_reuses_compatible_trajectory_index(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    original = tmp_path / "original-trajectory"
    original.mkdir()
    trajectory = original / "run.lammpstrj"
    trajectory.write_text(
        "ITEM: TIMESTEP\n0\n"
        "ITEM: NUMBER OF ATOMS\n1\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n1 1 1 1 1\n",
        encoding="utf-8",
    )
    Path(f"{trajectory}.reactionabcd").write_text(
        "1 [H]->[H]\n",
        encoding="utf-8",
    )
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    before = resolve_dataset_paths(original, trajectory.name)

    moved = tmp_path / "moved-trajectory"
    original.rename(moved)
    moved_trajectory = moved / trajectory.name

    opened = TRAJECTORY_INDEX_STORE.open_required(str(moved_trajectory))

    assert opened.frames == [0]
    assert resolve_dataset_paths(moved, trajectory.name).dataset_id == before.dataset_id


def test_remote_dataset_uses_the_platform_workspace_without_configuration(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    dataset = tmp_path / "remote-dataset"
    dataset.mkdir()
    base, _reactionevent, _molecules = _event_only_dataset(dataset)
    policy = WorkspacePolicy(filesystem_type=lambda _path: "nfs")

    paths = resolve_dataset_paths(
        dataset,
        base.name,
        workspace_policy=policy,
    )

    assert dataset / ".reacnet-scope" not in paths.workspace_dir.parents
    assert tmp_path / "user-data" / "reacnet-scope" / "workspaces" in (
        paths.workspace_dir.parents
    )


def test_installed_prepare_safely_clears_local_sidecar_index(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    base = tmp_path / "run.lammpstrj"
    base.write_text(
        "ITEM: TIMESTEP\n0\n"
        "ITEM: NUMBER OF ATOMS\n1\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n1 1 1 1 1\n",
        encoding="utf-8",
    )
    Path(f"{base}.reactionabcd").write_text("1 [H]->[H]\n", encoding="utf-8")
    source_bytes = base.read_bytes()
    environment = os.environ.copy()
    environment.pop("REACNET_SCOPE_CACHE_DIR", None)
    executable = Path(sys.executable).with_name("reacnet-scope")
    command = [
        str(executable),
        "prepare",
    ]

    built = subprocess.run(
        [
            *command,
            "build",
            "trajectory",
            str(tmp_path),
            "--base",
            base.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert built.returncode == 0, built.stderr
    paths = resolve_dataset_paths(tmp_path, base.name)
    assert paths.trajectory_index.is_file()

    cleared = subprocess.run(
        [
            *command,
            "clear",
            "trajectory",
            str(tmp_path),
            "--base",
            base.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert cleared.returncode == 0, cleared.stderr
    assert base.read_bytes() == source_bytes
    assert not paths.trajectory_index.exists()


def test_prepare_event_only_builds_manifest_v3_and_safe_clear(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)

    assert prepare.main(["build", "event", str(tmp_path)]) == 0

    paths = resolve_dataset_paths(tmp_path, base.name)
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["indexes"]["rng_events"]["kind"] == "legacy_csv"
    assert manifest["indexes"]["event"]["state"] == "ready"
    assert manifest["settings"] == {
        "path": str(paths.workspace_dir / "dataset-settings.json"),
        "exists": False,
    }

    source_bytes = reactionevent.read_bytes(), molecules.read_bytes()
    assert prepare.main(["clear", "event", str(tmp_path)]) == 0
    assert (reactionevent.read_bytes(), molecules.read_bytes()) == source_bytes
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "missing"


def test_ui_preparation_service_builds_and_rebuilds_rng_event_cache(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    source_bytes = reactionevent.read_bytes(), molecules.read_bytes()

    built = svc.prepare_dataset_workspace(
        str(tmp_path),
        base=str(base),
        kind="event",
    )
    rebuilt = svc.prepare_dataset_workspace(
        str(tmp_path),
        base=str(base),
        kind="event",
    )

    assert built["ok"] is True
    assert built["rebuilt"] is False
    assert built["status"]["state"] == "ready"
    assert built["status"]["progress"] == 1.0
    assert built["status"]["source_offset"] == reactionevent.stat().st_size
    assert rebuilt["rebuilt"] is True
    assert rebuilt["status"]["state"] == "ready"
    assert (reactionevent.read_bytes(), molecules.read_bytes()) == source_bytes


def test_ui_preparation_service_rejects_an_unwritable_cache_target(
    tmp_path,
    monkeypatch,
) -> None:
    cache_file = tmp_path / "not-a-cache-directory"
    cache_file.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(cache_file))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)

    status = svc.dataset_preparation_status(str(tmp_path), base=str(base))
    assert status["workspace_writable"] is False

    try:
        svc.prepare_dataset_workspace(
            str(tmp_path),
            base=str(base),
            kind="event",
        )
    except svc.ServiceError as exc:
        assert exc.reason == "workspace_not_writable"
    else:  # pragma: no cover - the service must reject this target
        raise AssertionError("unwritable cache target was accepted")


def test_ui_preparation_service_builds_trajectory_and_composition_caches(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    base.write_text(
        "ITEM: TIMESTEP\n0\nITEM: TIMESTEP\n10\n",
        encoding="utf-8",
    )
    species = Path(f"{base}.species")
    species.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    source_bytes = base.read_bytes(), species.read_bytes()

    trajectory = svc.prepare_dataset_workspace(
        str(tmp_path),
        base=str(base),
        kind="trajectory",
    )
    composition = svc.prepare_dataset_workspace(
        str(tmp_path),
        base=str(base),
        kind="composition",
    )

    assert trajectory["status"]["state"] == "ready"
    assert trajectory["status"]["frames"] == 2
    assert composition["status"]["state"] == "ready"
    assert composition["status"]["timepoints"] == 1
    assert (base.read_bytes(), species.read_bytes()) == source_bytes


def test_dash_cancel_service_uses_persisted_preparation_task(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def fake_cancel(case, *, base="", capability):
        calls.append((case, base, capability))
        return True

    monkeypatch.setattr(prepare, "request_cancellation", fake_cancel)

    result = svc.cancel_dataset_preparation(
        str(tmp_path), base=str(base), kind="all"
    )

    assert result["cancellation_requested"] is True
    assert calls == [(str(tmp_path.resolve()), base.name, "all")]


def test_ui_clear_service_manages_all_visible_index_types(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    base.write_text(
        "ITEM: TIMESTEP\n0\nITEM: TIMESTEP\n10\n",
        encoding="utf-8",
    )
    species = Path(f"{base}.species")
    species.write_text("Timestep 0: [H] 2\n", encoding="utf-8")
    source_bytes = {
        path: path.read_bytes()
        for path in (base, species, reactionevent, molecules)
    }

    for kind in ("event", "trajectory", "composition"):
        built = svc.prepare_dataset_workspace(
            str(tmp_path),
            base=str(base),
            kind=kind,
        )
        assert built["status"]["state"] == "ready"

    for kind in ("event", "trajectory", "composition"):
        cleared = svc.clear_dataset_index(
            str(tmp_path),
            base=str(base),
            kind=kind,
        )
        assert cleared["kind"] == kind
        assert cleared["removed"]
        assert cleared["released_bytes"] > 0

    assert {path: path.read_bytes() for path in source_bytes} == source_bytes
    status = svc.dataset_preparation_status(str(tmp_path), base=str(base))
    assert status["events"]["state"] == "needs_preparation"
    assert status["trajectory"]["state"] == "missing"
    assert status["composition"]["state"] == "missing"


def test_default_preparation_builds_available_event_index(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    _base, reactionevent, molecules = _event_only_dataset(tmp_path)

    assert prepare.main(["build", "all", str(tmp_path)]) == 0

    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "ready"


def test_event_only_discovers_paired_rng_outputs_without_reaction_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    Path(f"{base}.reactionabcd").unlink()

    assert prepare.main(["build", "event", str(tmp_path)]) == 0

    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "ready"


def test_event_only_prepares_unpaired_reactionevent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    Path(f"{base}.reactionabcd").unlink()
    molecules.unlink()

    assert prepare.main(["build", "event", str(tmp_path)]) == 0

    status = EVENT_EVIDENCE_STORE.status(str(reactionevent), "")
    assert status["state"] == "ready"
    assert status["association_available"] is False
    assert status["time_basis"] == "timestep_index"


def test_prepare_can_clear_event_cache_after_sources_are_removed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    assert prepare.main(["build", "event", str(tmp_path)]) == 0
    index_path = resolve_dataset_paths(tmp_path, base.name).event_index
    reactionevent.unlink()
    molecules.unlink()

    assert (
        prepare.main(
            [
                "clear",
                "event",
                str(tmp_path),
                "--base",
                base.name,
            ]
        )
        == 0
    )

    assert not index_path.exists()


def test_scan_dataset_reads_version_one_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base, _reactionevent, _molecules = _event_only_dataset(tmp_path)
    paths = resolve_dataset_paths(tmp_path, base.name)
    paths.workspace_dir.mkdir(parents=True)
    paths.manifest.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "dataset_id": paths.dataset_id,
                "base": str(base),
                "artifacts": {},
                "indexes": {},
            }
        ),
        encoding="utf-8",
    )

    status = svc.scan_dataset(str(tmp_path))

    assert status["dataset"]["manifest"]["found"] is True
    assert status["dataset"]["manifest"]["dataset_id"] == paths.dataset_id
