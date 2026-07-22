from __future__ import annotations

from pathlib import Path

from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.rng_events import event_output_status, query_rng_events
from scripts.webapp_dash import services as svc


def _frame(timestep: int) -> str:
    return (
        "ITEM: TIMESTEP\n"
        f"{timestep}\n"
        "ITEM: NUMBER OF ATOMS\n2\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type element x y z\n"
        "1 1 C 1 1 1\n"
        "2 2 O 2 2 2\n"
    )


def _rng_outputs(tmp_path: Path) -> tuple[Path, Path]:
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    # RNG molecule AtomIDs/BondIDs are zero-based; UI/trajectory IDs are one-based.
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n"
        "0,[O],1,\n"
        "10,[C][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    return reactionevent, molecules


def test_rng_event_query_preserves_stoichiometry_and_maps_atoms(tmp_path) -> None:
    reactionevent, molecules = _rng_outputs(tmp_path)

    status = event_output_status(str(reactionevent), str(molecules))
    assert status["state"] == "ready"
    result = query_rng_events(str(reactionevent), str(molecules), "[O] + [C] -> [C][O]")
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["before_timestep"] == 0
    assert row["after_timestep"] == 10
    assert row["rng_atom_ids"] == "0,1"
    assert row["atom_id_list"] == [1, 2]
    assert row["product_bonds"] == "1-2-1"
    assert row["association_status"] == "matched"


def test_dataset_scan_uses_rng_event_outputs_instead_of_route(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _rng_outputs(tmp_path)
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")

    status = svc.scan_dataset(str(tmp_path))
    artifacts = svc.artifacts_from_status(status)
    assert artifacts["reactionevent"] == str(reactionevent)
    assert artifacts["molecules"] == str(molecules)
    assert status["dataset"]["readiness"]["event_search"]["ready"] is True
    assert not Path(f"{trajectory}.route").exists()


def test_rng_event_visualization_reads_only_selected_atoms(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _rng_outputs(tmp_path)
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    artifacts = {
        "reactionevent": str(reactionevent),
        "molecules": str(molecules),
        "trajectory": str(trajectory),
    }
    row = svc.locate_rng_events(artifacts, "[C] + [O] -> [C][O]")["rows"][0]
    viewer = svc.build_rng_event_visualization(artifacts, row, before_frames=0, after_frames=0)

    assert [frame["frame"] for frame in viewer["frames"]] == [0, 10]
    assert viewer["atom_groups"]["core"] == [1, 2]
    assert all(len(frame["atoms"]) == 2 for frame in viewer["frames"])
