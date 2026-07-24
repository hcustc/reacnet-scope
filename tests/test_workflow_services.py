from __future__ import annotations

from pathlib import Path

from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
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


def _workflow_artifacts(tmp_path: Path) -> dict[str, str]:
    species = tmp_path / "run.lammpstrj.species"
    moname = tmp_path / "run.lammpstrj.moname"
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    trajectory = tmp_path / "run.lammpstrj"
    species.write_text("Timestep 0: [C] 2 [O] 1\nTimestep 10: [C][O] 3\n", encoding="utf-8")
    moname.write_text("[C] 0\n[C][O] 0;1 0,1,1\n", encoding="utf-8")
    reaction.write_text("10 [C]+[O]->[C][O]\n4 [C][O]->[C]+[O]\n", encoding="utf-8")
    reactionevent.write_text("Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n", encoding="utf-8")
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n0,[O],1,\n10,[C][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")
    return {
        "species": str(species), "moname": str(moname), "reaction": str(reaction),
        "reactionevent": str(reactionevent), "molecules": str(molecules), "trajectory": str(trajectory),
    }


def test_species_catalog_is_sourced_from_species_and_optionally_enriched_by_moname(tmp_path: Path) -> None:
    artifacts = _workflow_artifacts(tmp_path)
    result = svc.search_species_catalog(artifacts, "CO", kind="formula")

    assert result["n_rows"] == 1
    row = result["rows"][0]
    assert row["smiles"] == "[C][O]"
    assert row["total_count"] == 3
    assert row["moname_available"] is True
    assert row["moname_bond_count"] == 1
    assert row["structure"].startswith("![")
    assert "/api/structure.svg?smiles=" in row["structure"]
    assert result["meta"]["catalog_size"] == 3


def test_channels_are_split_by_target_role_and_ranked_by_frequency(tmp_path: Path) -> None:
    artifacts = _workflow_artifacts(tmp_path)
    result = svc.collect_species_channels(artifacts, "[C][O]")

    assert result["production_rows"][0]["workflow_role"] == "produce"
    assert result["production_rows"][0]["forward_tp"] == 10
    assert result["consumption_rows"][0]["workflow_role"] == "consume"
    assert result["consumption_rows"][0]["forward_tp"] == 4


def test_representative_event_ranking_and_viewer_expose_bond_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    artifacts = _workflow_artifacts(tmp_path)
    TRAJECTORY_INDEX_STORE.build(artifacts["trajectory"])

    ranked = svc.rank_representative_events(artifacts, "[C] + [O] -> [C][O]")
    event = ranked["rows"][0]
    assert event["recommendation"] == "recommended"
    assert event["formed_bonds"] == "1-2-1"

    viewer = svc.build_rng_event_visualization(artifacts, event, before_frames=0, after_frames=0)
    assert viewer["atom_groups"]["core"] == [1, 2]
    assert viewer["bond_evidence"]["formed"] == ["1-2-1"]
    assert viewer["frames"][0]["bond_state"] == "before"
    assert viewer["frames"][1]["bond_state"] == "after"


def test_validation_records_upsert_by_dataset_and_event() -> None:
    first = svc.upsert_validation_record(
        [], dataset_id="dataset", species={"formula": "CO", "smiles": "[C][O]"},
        channel={"role_label": "生成", "reaction_smiles": "[C] + [O] -> [C][O]"},
        event={"event_id": "event-1", "before_timestep": 0, "after_timestep": 10},
        outcome="support", note="first", recorded_at="2026-07-22T00:00:00+00:00",
    )
    second = svc.upsert_validation_record(
        first, dataset_id="dataset", species={"formula": "CO", "smiles": "[C][O]"},
        channel={"role_label": "生成", "reaction_smiles": "[C] + [O] -> [C][O]"},
        event={"event_id": "event-1", "before_timestep": 0, "after_timestep": 10},
        outcome="exclude", note="updated", recorded_at="2026-07-22T00:01:00+00:00",
    )

    assert len(second) == 1
    assert second[0]["validation_outcome"] == "exclude"
    assert second[0]["note"] == "updated"
