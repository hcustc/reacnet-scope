from __future__ import annotations

from pathlib import Path

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from rng_tools import dir_browser
from reacnet_scope.rng_events import (
    canonical_reaction_key,
    event_output_status,
    query_rng_events,
    reaction_key,
)
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


def test_canonical_reaction_key_sorts_each_side_and_preserves_multiplicity() -> None:
    assert canonical_reaction_key(("[O]", "[H]", "[H]"), ("[H][O][H]",)) == (
        "[H]+[H]+[O]->[H][O][H]"
    )


def test_reaction_key_does_not_split_charge_signs_inside_smiles_brackets() -> None:
    normalized = reaction_key("[NH4+]+[OH-]", "[NH3]+[OH2]")

    assert normalized == (
        ("[NH4+]", "[OH-]"),
        ("[NH3]", "[OH2]"),
    )
    assert canonical_reaction_key(*normalized) == (
        "[NH4+]+[OH-]->[NH3]+[OH2]"
    )


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
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    reactionevent, molecules = _rng_outputs(tmp_path)
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")

    status = svc.scan_dataset(str(tmp_path))
    artifacts = svc.artifacts_from_status(status)
    assert artifacts["reactionevent"] == str(reactionevent)
    assert artifacts["molecules"] == str(molecules)
    assert status["dataset"]["readiness"]["event_search"]["ready"] is False
    assert (
        status["dataset"]["readiness"]["event_search"]["state"]
        == "needs_preparation"
    )
    assert not Path(f"{trajectory}.route").exists()

    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    prepared = svc.scan_dataset(str(tmp_path))
    assert prepared["dataset"]["readiness"]["event_search"]["ready"] is True


def test_rng_event_service_requires_prepared_index(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _rng_outputs(tmp_path)
    artifacts = {
        "reactionevent": str(reactionevent),
        "molecules": str(molecules),
    }

    with pytest.raises(svc.ServiceError) as error:
        svc.locate_rng_events(artifacts, "[C] + [O] -> [C][O]")

    assert error.value.reason == "event_index_not_ready"
    assert "reacnet-scope-prepare" in error.value.message
    assert "--event-only" in error.value.message


def test_stale_event_index_recommends_explicit_rebuild(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    reactionevent, molecules = _rng_outputs(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    reactionevent.write_text(
        reactionevent.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    status = svc.scan_dataset(str(tmp_path))
    event_status = status["dataset"]["readiness"]["event_search"]
    assert event_status["state"] == "stale"
    assert event_status["preparation_command"].endswith("--rebuild event")
    preparation = svc.dataset_preparation_status(str(tmp_path))
    assert preparation["event_command"].endswith("--rebuild event")

    with pytest.raises(svc.ServiceError) as error:
        svc.locate_rng_events(
            {
                "reactionevent": str(reactionevent),
                "molecules": str(molecules),
            },
            "[C] + [O] -> [C][O]",
        )
    assert error.value.reason == "event_index_stale"
    assert "--rebuild event" in error.value.message


def test_rng_event_visualization_reads_only_selected_atoms(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _rng_outputs(tmp_path)
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
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
