from __future__ import annotations

import json
from pathlib import Path

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import ROUTE_INDEX_STORE, resolve_dataset_paths
from reacnet_scope import prepare
from scripts.webapp_dash import services as svc


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
    assert payload["events"]["state"] == "needs_preparation"
    assert payload["trajectory"]["state"] == "missing"
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


def test_prepare_event_only_builds_manifest_v2_and_safe_clear(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base, reactionevent, molecules = _event_only_dataset(tmp_path)

    assert prepare.main([str(tmp_path), "--event-only"]) == 0

    paths = resolve_dataset_paths(tmp_path, base.name)
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
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
