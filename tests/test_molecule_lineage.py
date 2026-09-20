from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.molecule_lineage import (
    LineageElementMappingError,
    MoleculeLineageError,
    build_molecule_lineage,
    continue_molecule_lineage,
    molecule_lineage_to_csv,
)
from reacnet_scope import services as svc


def _write_lineage_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    reactionevent = tmp_path / "lineage.reactionevent.csv"
    molecules = tmp_path / "lineage.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[C][H],[C]+[H]\n"
        "1,[C]+[H],[C][H]\n"
        "2,[C][H]+[O],[C]([H])[O]\n"
        "3,[C]([H])[O],[C][O]+[H]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C][H],0;1,0-1-1\n"
        "0,[O],2,\n"
        "10,[C],0,\n"
        "10,[H],1,\n"
        "10,[O],2,\n"
        "20,[C][H],0;1,0-1-1\n"
        "20,[O],2,\n"
        "30,[C]([H])[O],0;1;2,0-1-1;0-2-1\n"
        "40,[C][O],0;2,0-2-1\n"
        "40,[H],1,\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    root = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent),
        str(molecules),
        "[C][H]->[C]+[H]",
        limit=1,
    )["rows"][0]
    return reactionevent, molecules, str(root["event_id"])


def test_lineage_tracks_exact_instance_and_folds_fast_recrossing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)

    report = build_molecule_lineage(
        str(reactionevent),
        str(molecules),
        event_id=event_id,
        side="reactant",
        participant_index=0,
        atom_elements={1: "C", 2: "H", 3: "O"},
    )

    assert report["query"]["anchor_atom_ids"] == [1]
    assert report["summary"]["event_count"] == 4
    assert report["summary"]["recrossing_episode_count"] == 1
    assert report["summary"]["aggregate_trend"] == "growth"
    assert [row["category"] for row in report["event_nodes"]] == [
        "split",
        "merge",
        "merge",
        "split",
    ]
    episode = report["recrossing_episodes"][0]
    assert episode["interval_gap"] == 1
    assert len(episode["event_ids"]) == 2
    assert episode["exact_structure_return"] is True

    hydrogen_nodes = [
        row for row in report["molecule_nodes"] if row["species"] == "[H]"
    ]
    assert hydrogen_nodes
    assert all(row["role"] == "context" for row in hydrogen_nodes)
    oxygen_reactant = next(
        row
        for row in report["molecule_nodes"]
        if row["species"] == "[O]" and row["timestep_index"] == 2
    )
    assert oxygen_reactant["role"] == "context"

    raw_event_ids = {
        element["data"]["event_id"]
        for element in report["views"]["raw"]["elements"]
        if element["data"].get("kind") == "event"
    }
    persistent_event_ids = {
        element["data"]["event_id"]
        for element in report["views"]["persistent"]["elements"]
        if element["data"].get("kind") == "event"
    }
    assert set(episode["event_ids"]).issubset(raw_event_ids)
    assert set(episode["event_ids"]).isdisjoint(persistent_event_ids)
    assert any(
        element["data"].get("kind") == "recrossing"
        for element in report["views"]["persistent"]["elements"]
    )


def test_non_hydrogen_anchor_requires_complete_root_element_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)

    with pytest.raises(LineageElementMappingError, match="atom IDs: 2"):
        build_molecule_lineage(
            str(reactionevent),
            str(molecules),
            event_id=event_id,
            side="reactant",
            participant_index=0,
            atom_elements={1: "C"},
        )


def test_explicit_atom_anchor_works_without_element_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)

    report = build_molecule_lineage(
        str(reactionevent),
        str(molecules),
        event_id=event_id,
        side="reactant",
        participant_index=0,
        anchor_mode="atom_ids",
        anchor_atom_ids=[1],
        depth_backward=0,
        depth_forward=1,
    )

    assert report["root"]["anchor_atom_ids"] == [1]
    # The raw view keeps both halves of the one-change recrossing episode.
    assert report["summary"]["event_count"] == 2
    assert report["summary"]["aggregate_trend"] == "undetermined"


def test_lineage_csv_contains_query_evidence_and_truncation_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)
    report = build_molecule_lineage(
        str(reactionevent),
        str(molecules),
        event_id=event_id,
        side="reactant",
        participant_index=0,
        atom_elements={1: "C", 2: "H", 3: "O"},
        depth_forward=1,
    )

    rows = list(csv.DictReader(io.StringIO(molecule_lineage_to_csv(report))))
    record_types = {row["record_type"] for row in rows}
    assert {
        "query",
        "context",
        "source_signature",
        "segment",
        "branch_summary",
        "molecule",
        "event",
        "edge",
        "truncation",
    }.issubset(record_types)
    assert any(row["reason"] == "persistent_depth_limit" for row in rows)
    assert all(row["payload_json"] for row in rows)


def test_lineage_continues_one_budget_stopped_branch_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)
    initial = build_molecule_lineage(
        str(reactionevent),
        str(molecules),
        event_id=event_id,
        side="reactant",
        participant_index=0,
        anchor_mode="atom_ids",
        anchor_atom_ids=[1],
        depth_backward=1,
        depth_forward=1,
        dataset_id="fixture-dataset",
        source_revision={"fingerprint": "fixture-revision"},
    )
    branch = next(
        row
        for row in initial["branch_summaries"]
        if row["direction"] == "forward" and row["can_continue"]
    )

    continued = continue_molecule_lineage(
        str(reactionevent),
        str(molecules),
        initial,
        branch_id=branch["branch_id"],
        persistent_depth=2,
        max_molecule_nodes=100,
        dataset_id="fixture-dataset",
        source_revision={"fingerprint": "fixture-revision"},
    )

    assert initial["summary"]["segment_count"] == 1
    assert continued["summary"]["segment_count"] == 2
    assert continued["summary"]["event_count"] == 4
    assert len({row["event_id"] for row in continued["event_nodes"]}) == len(
        continued["event_nodes"]
    )
    assert len({row["node_id"] for row in continued["molecule_nodes"]}) == len(
        continued["molecule_nodes"]
    )
    old_stop = next(
        row
        for row in continued["truncations"]
        if row["truncation_id"] == branch["truncation_id"]
    )
    assert old_stop["continued_by_segment_id"] == "segment-002"
    assert continued["segments"][1]["parent_branch_id"] == branch["branch_id"]


def test_lineage_continuation_rejects_a_changed_source_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)
    report = build_molecule_lineage(
        str(reactionevent),
        str(molecules),
        event_id=event_id,
        side="reactant",
        participant_index=0,
        anchor_mode="atom_ids",
        anchor_atom_ids=[1],
        depth_forward=1,
        dataset_id="fixture-dataset",
        source_revision={"fingerprint": "fixture-revision"},
    )
    branch = next(row for row in report["branch_summaries"] if row["can_continue"])

    with pytest.raises(MoleculeLineageError, match="source revision changed"):
        continue_molecule_lineage(
            str(reactionevent),
            str(molecules),
            report,
            branch_id=branch["branch_id"],
            dataset_id="fixture-dataset",
            source_revision={"fingerprint": "new-revision"},
        )


def test_lineage_reports_observation_boundary_as_a_noncontinuable_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)

    report = build_molecule_lineage(
        str(reactionevent),
        str(molecules),
        event_id=event_id,
        side="reactant",
        participant_index=0,
        anchor_mode="atom_ids",
        anchor_atom_ids=[1],
        directions=("backward",),
    )

    assert report["summary"]["event_count"] == 0
    assert len(report["branch_summaries"]) == 1
    branch = report["branch_summaries"][0]
    assert branch["stop_reason"] == "observation_boundary"
    assert branch["status"] == "observation_boundary"
    assert branch["can_continue"] is False


def test_lineage_service_reads_confirmed_elements_from_one_indexed_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules, event_id = _write_lineage_fixture(tmp_path)
    trajectory = tmp_path / "lineage.lammpstrj"

    def frame(timestep: int) -> str:
        return (
            "ITEM: TIMESTEP\n"
            f"{timestep}\n"
            "ITEM: NUMBER OF ATOMS\n3\n"
            "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
            "ITEM: ATOMS id type x y z\n"
            "1 1 1 1 1\n"
            "2 2 2 1 1\n"
            "3 3 3 1 1\n"
        )

    trajectory.write_text(
        "".join(frame(value) for value in (0, 10, 20, 30, 40)),
        encoding="utf-8",
    )
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    event = EVENT_EVIDENCE_STORE.get_event(
        str(reactionevent), str(molecules), event_id
    )

    report = svc.build_molecule_lineage_analysis(
        {
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
            "trajectory": str(trajectory),
        },
        event,
        side="reactant",
        participant_index=0,
        viewer={"meta": {"type_element_map": {"1": "C", "2": "H", "3": "O"}}},
    )

    assert report["root"]["anchor_atom_ids"] == [1]
    assert report["summary"]["aggregate_trend"] == "growth"
    assert report["source_signatures"]["trajectory"]["path"] == str(
        trajectory.resolve()
    )
