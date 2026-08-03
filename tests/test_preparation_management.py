from __future__ import annotations

import json
import time
from pathlib import Path

from rng_tools import dir_browser
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import ROUTE_INDEX_STORE, resolve_dataset_paths
from reacnet_scope import prepare
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash import services as svc


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
    assert "索引就绪状态" in cache_text
    assert "危险操作：清理索引缓存" in cache_text
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
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base = tmp_path / "run.lammpstrj"
    Path(f"{base}.reactionabcd").touch()
    Path(f"{base}.species").touch()
    candidate = {
        "folder": str(tmp_path),
        "base": str(base),
        "label": base.name,
    }

    def fake_prepare(folder: str, *, base: str, kind: str):
        return {
            "ok": True,
            "kind": kind,
            "rebuilt": False,
            "status": {"state": "ready", "event_count": 3},
        }

    monkeypatch.setattr(svc, "prepare_dataset_cache", fake_prepare)
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
    route = Path(f"{trajectory}.route")
    reaction = Path(f"{trajectory}.reactionabcd")
    species = Path(f"{trajectory}.species")
    reactionevent = Path(f"{trajectory}.reactionevent.csv")
    molecules = Path(f"{trajectory}.molecules.csv")
    trajectory.write_text("ITEM: TIMESTEP\n0\n", encoding="utf-8")
    route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
    reaction.write_text("1 C->O\n", encoding="utf-8")
    species.write_text("Timestep 0: C 1\n", encoding="utf-8")
    reactionevent.write_text("Timestep_Index,Reactant,Product\n0,C,O\n", encoding="utf-8")
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n0,C,0,\n10,O,0,\n",
        encoding="utf-8",
    )
    ROUTE_INDEX_STORE.build(str(route))

    payload = svc.dataset_preparation_status(str(tmp_path))
    assert payload["dataset_id"]
    assert "/datasets/" in payload["cache_dir"]
    assert payload["cache_configured"] is True
    assert payload["cache_writable"] is True
    assert payload["events"]["state"] == "needs_preparation"
    assert payload["events"]["source_available"] is True
    assert payload["trajectory"]["state"] == "missing"
    assert payload["trajectory"]["source_available"] is True
    assert payload["rng_event_command"] == "--reaction-event --show-molecule-time"

    cleared = svc.clear_dataset_index(str(tmp_path), kind="route")
    assert cleared["released_bytes"] > 0
    assert route.exists()
    assert ROUTE_INDEX_STORE.status(str(route))["state"] == "missing"


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
        prepare.main([str(tmp_path), "--event-only"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse errors must terminate the command
        raise AssertionError("ambiguous dataset was accepted")

    stderr = capsys.readouterr().err
    assert "dataset directory is ambiguous; pass --base" in stderr
    assert "Traceback" not in stderr


def test_prepare_event_only_builds_manifest_v3_and_safe_clear(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)

    assert prepare.main([str(tmp_path), "--event-only"]) == 0

    paths = resolve_dataset_paths(tmp_path, base.name)
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["indexes"]["rng_events"]["kind"] == "legacy_csv"
    assert manifest["indexes"]["event"]["state"] == "ready"
    assert manifest["settings"] == {
        "path": str(paths.cache_dir / "dataset-settings.json"),
        "exists": False,
    }

    source_bytes = reactionevent.read_bytes(), molecules.read_bytes()
    assert prepare.main([str(tmp_path), "--clear", "event"]) == 0
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

    built = svc.prepare_dataset_cache(
        str(tmp_path),
        base=str(base),
        kind="event",
    )
    rebuilt = svc.prepare_dataset_cache(
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
    assert status["cache_writable"] is False

    try:
        svc.prepare_dataset_cache(
            str(tmp_path),
            base=str(base),
            kind="event",
        )
    except svc.ServiceError as exc:
        assert exc.reason == "cache_not_writable"
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

    trajectory = svc.prepare_dataset_cache(
        str(tmp_path),
        base=str(base),
        kind="trajectory",
    )
    composition = svc.prepare_dataset_cache(
        str(tmp_path),
        base=str(base),
        kind="composition",
    )

    assert trajectory["status"]["state"] == "ready"
    assert trajectory["status"]["frames"] == 2
    assert composition["status"]["state"] == "ready"
    assert composition["status"]["timepoints"] == 1
    assert (base.read_bytes(), species.read_bytes()) == source_bytes


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
        built = svc.prepare_dataset_cache(
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

    assert prepare.main([str(tmp_path)]) == 0

    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "ready"


def test_event_only_discovers_paired_rng_outputs_without_reaction_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    Path(f"{base}.reactionabcd").unlink()

    assert prepare.main([str(tmp_path), "--event-only"]) == 0

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

    assert prepare.main([str(tmp_path), "--event-only"]) == 0

    status = EVENT_EVIDENCE_STORE.status(str(reactionevent), "")
    assert status["state"] == "ready"
    assert status["association_available"] is False
    assert status["time_basis"] == "timestep_index"


def test_prepare_can_clear_event_cache_after_sources_are_removed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)
    assert prepare.main([str(tmp_path), "--event-only"]) == 0
    index_path = resolve_dataset_paths(tmp_path, base.name).event_index
    reactionevent.unlink()
    molecules.unlink()

    assert (
        prepare.main(
            [
                str(tmp_path),
                "--base",
                base.name,
                "--clear",
                "event",
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
    paths.cache_dir.mkdir(parents=True)
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
