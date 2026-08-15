from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

import reacnet_scope.event_index as event_index
import reacnet_scope.timed_evidence as timed_evidence
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import resolve_dataset_paths
from reacnet_scope import dir_browser, prepare
from reacnet_scope.timed_evidence import (
    NativeHdf5EvidenceAdapter,
    TimedEvidenceDataError,
    TimedEvidenceSelection,
    select_timed_evidence,
)
from reacnet_scope import services as dash_services


def write_legacy_evidence(base: Path, *, molecules: bool = True) -> tuple[Path, Path]:
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecule_file = Path(f"{base}.molecules.csv")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    if molecules:
        molecule_file.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            "0,[C],0,\n"
            "0,[O],1,\n"
            "10,[C][O],0;1,0-1-1\n",
            encoding="utf-8",
        )
    return reactionevent, molecule_file


def write_timeline(
    path: Path,
    *,
    status: str = "complete",
    schema_version: str = "1",
    source_count: int = 1,
    reaction_enabled: bool = True,
    molecule_enabled: bool = True,
    reaction_count: int = 1,
) -> Path:
    string_dtype = h5py.string_dtype("utf-8")
    with h5py.File(path, "w") as handle:
        handle.attrs.update(
            {
                "schema_version": schema_version,
                "status": status,
                "frame_count": 2,
                "stepinterval": 1,
                "source_order": '["run.lammpstrj"]',
                "reaction_enabled": reaction_enabled,
                "molecule_enabled": molecule_enabled,
            }
        )
        sources = handle.create_group("sources")
        sources.create_dataset(
            "path",
            data=np.asarray(
                [f"run-{index}.lammpstrj" for index in range(source_count)],
                dtype=object,
            ),
            dtype=string_dtype,
        )
        sources.create_dataset(
            "ordinal",
            data=np.arange(source_count, dtype=np.uint32),
        )
        frames = handle.create_group("frames")
        frames.create_dataset("source_id", data=np.ones(2, dtype=np.uint32))
        frames.create_dataset("source_frame", data=np.arange(2, dtype=np.uint64))
        frames.create_dataset("timestep", data=np.asarray([0, 10], dtype=np.int64))

        if reaction_enabled:
            reaction_types = handle.create_group("reaction_types")
            reaction_types.create_dataset(
                "reactant", data=np.asarray(["[C]+[O]"], dtype=object), dtype=string_dtype
            )
            reaction_types.create_dataset(
                "product", data=np.asarray(["[C][O]"], dtype=object), dtype=string_dtype
            )
            reaction_types.create_dataset(
                "total_count",
                data=np.asarray([reaction_count], dtype=np.uint64),
            )
            reaction_events = handle.create_group("reaction_events")
            reaction_events.create_dataset("block_start", data=np.asarray([0], dtype=np.uint64))
            reaction_events.create_dataset("block_length", data=np.asarray([1], dtype=np.uint64))
            reaction_events.create_dataset("transition_index", data=np.asarray([0], dtype=np.uint64))
            reaction_events.create_dataset("reaction_id", data=np.asarray([1], dtype=np.uint32))
            reaction_events.create_dataset(
                "count", data=np.asarray([reaction_count], dtype=np.uint64)
            )

            if schema_version == "2":
                handle.attrs.update(
                    {
                        "transition_evidence_enabled": True,
                        "reaction_active_transition_index_available": True,
                    }
                )
                transition_evidence = handle.create_group(
                    "transition_evidence"
                )
                transition_evidence.create_dataset(
                    "block_start", data=np.asarray([0], dtype=np.uint64)
                )
                transition_evidence.create_dataset(
                    "block_length", data=np.asarray([1], dtype=np.uint64)
                )
                transition_evidence.create_dataset(
                    "transition_index", data=np.asarray([0], dtype=np.uint64)
                )
                transition_evidence.create_dataset(
                    "reaction_id", data=np.asarray([1], dtype=np.uint32)
                )
                transition_evidence.create_dataset(
                    "participant_offsets",
                    data=np.asarray([0, 3], dtype=np.uint64),
                )
                transition_evidence.create_dataset(
                    "participant_molecule_id",
                    data=np.asarray([1, 2, 3], dtype=np.uint64),
                )
                transition_evidence.create_dataset(
                    "participant_side",
                    data=np.asarray([0, 0, 1], dtype=np.uint8),
                )
                transition_evidence.create_dataset(
                    "bond_change_offsets",
                    data=np.asarray([0, 1], dtype=np.uint64),
                )
                transition_evidence.create_dataset(
                    "bond_atoms",
                    data=np.asarray([[0, 1]], dtype=np.uint64),
                )
                transition_evidence.create_dataset(
                    "before_order", data=np.asarray([0], dtype=np.int16)
                )
                transition_evidence.create_dataset(
                    "after_order", data=np.asarray([1], dtype=np.int16)
                )

        if molecule_enabled:
            species = handle.create_group("species")
            species.create_dataset(
                "name",
                data=np.asarray(["[C]", "[O]", "[C][O]"], dtype=object),
                dtype=string_dtype,
            )
            molecules_group = handle.create_group("molecules")
            molecules_group.create_dataset("molecule_id", data=np.asarray([1, 2, 3], dtype=np.uint64))
            molecules_group.create_dataset("species_id", data=np.asarray([1, 2, 3], dtype=np.uint32))
            molecules_group.create_dataset("atom_offsets", data=np.asarray([0, 1, 2, 4], dtype=np.uint64))
            molecules_group.create_dataset("atom_ids", data=np.asarray([0, 1, 0, 1], dtype=np.uint64))
            molecules_group.create_dataset("bond_offsets", data=np.asarray([0, 0, 0, 1], dtype=np.uint64))
            molecules_group.create_dataset("bond_atoms", data=np.asarray([[0, 1]], dtype=np.uint64))
            molecules_group.create_dataset("bond_order", data=np.asarray([1], dtype=np.int16))
            ranges = handle.create_group("molecule_ranges")
            ranges.create_dataset("molecule_id", data=np.asarray([1, 2, 3], dtype=np.uint64))
            ranges.create_dataset("start_frame", data=np.asarray([0, 0, 1], dtype=np.uint64))
            ranges.create_dataset("end_frame", data=np.asarray([0, 0, 1], dtype=np.uint64))
    return path


def write_membership_timeline(
    path: Path,
    *,
    frame_count: int,
    molecule_atoms: list[list[int]],
    ranges: list[tuple[int, int, int]],
) -> TimedEvidenceSelection:
    string_dtype = h5py.string_dtype("utf-8")
    molecule_count = len(molecule_atoms)
    atom_offsets = np.zeros(molecule_count + 1, dtype=np.uint64)
    atom_offsets[1:] = np.cumsum(
        [len(values) for values in molecule_atoms],
        dtype=np.uint64,
    )
    with h5py.File(path, "w") as handle:
        frames = handle.create_group("frames")
        frames.create_dataset(
            "timestep",
            data=np.arange(frame_count, dtype=np.int64),
        )
        reaction_types = handle.create_group("reaction_types")
        reaction_types.create_dataset(
            "reactant",
            data=np.asarray([], dtype=object),
            dtype=string_dtype,
        )
        reaction_types.create_dataset(
            "product",
            data=np.asarray([], dtype=object),
            dtype=string_dtype,
        )
        reaction_events = handle.create_group("reaction_events")
        reaction_events.create_dataset(
            "block_start",
            data=np.zeros(max(frame_count - 1, 0), dtype=np.uint64),
        )
        reaction_events.create_dataset(
            "block_length",
            data=np.zeros(max(frame_count - 1, 0), dtype=np.uint64),
        )
        reaction_events.create_dataset(
            "count",
            data=np.asarray([], dtype=np.uint64),
        )
        species = handle.create_group("species")
        species.create_dataset(
            "name",
            data=np.asarray(["[C]"], dtype=object),
            dtype=string_dtype,
        )
        molecules = handle.create_group("molecules")
        molecules.create_dataset(
            "molecule_id",
            data=np.arange(1, molecule_count + 1, dtype=np.uint64),
        )
        molecules.create_dataset(
            "species_id",
            data=np.ones(molecule_count, dtype=np.uint32),
        )
        molecules.create_dataset("atom_offsets", data=atom_offsets)
        molecules.create_dataset(
            "atom_ids",
            data=np.asarray(
                [atom for values in molecule_atoms for atom in values],
                dtype=np.uint64,
            ),
        )
        molecules.create_dataset(
            "bond_offsets",
            data=np.zeros(molecule_count + 1, dtype=np.uint64),
        )
        molecules.create_dataset(
            "bond_atoms",
            data=np.empty((0, 2), dtype=np.uint64),
        )
        molecules.create_dataset(
            "bond_order",
            data=np.asarray([], dtype=np.int16),
        )
        range_group = handle.create_group("molecule_ranges")
        range_group.create_dataset(
            "molecule_id",
            data=np.asarray([item[0] for item in ranges], dtype=np.uint64),
        )
        range_group.create_dataset(
            "start_frame",
            data=np.asarray([item[1] for item in ranges], dtype=np.uint64),
        )
        range_group.create_dataset(
            "end_frame",
            data=np.asarray([item[2] for item in ranges], dtype=np.uint64),
        )
    return TimedEvidenceSelection(
        kind="native_hdf5",
        primary_file=str(path),
        source_files=(str(path),),
        timeline_file=str(path),
        reaction_enabled=False,
        molecule_enabled=True,
        frame_count=frame_count,
    )


def test_native_timed_evidence_wins_over_legacy_csv(tmp_path: Path) -> None:
    base = tmp_path / "run.lammpstrj"
    reactionevent, molecules = write_legacy_evidence(base)
    timeline = write_timeline(Path(f"{base}.timeline.h5"))

    selected = select_timed_evidence(
        timeline_file=str(timeline),
        reactionevent_file=str(reactionevent),
        molecules_file=str(molecules),
    )

    assert selected.kind == "native_hdf5"
    assert selected.primary_file == str(timeline.resolve())
    assert selected.schema_version == "1"
    assert selected.reaction_enabled is True
    assert selected.molecule_enabled is True
    assert selected.frame_count == 2


def test_legacy_csv_is_selected_when_native_source_is_absent(tmp_path: Path) -> None:
    base = tmp_path / "run.lammpstrj"
    reactionevent, molecules = write_legacy_evidence(base, molecules=False)

    selected = select_timed_evidence(
        timeline_file=str(Path(f"{base}.timeline.h5")),
        reactionevent_file=str(reactionevent),
        molecules_file=str(molecules),
    )

    assert selected.kind == "legacy_csv"
    assert selected.primary_file == str(reactionevent.resolve())
    assert selected.reaction_enabled is True
    assert selected.molecule_enabled is False


def test_existing_incomplete_native_source_does_not_fall_back_to_csv(tmp_path: Path) -> None:
    base = tmp_path / "run.lammpstrj"
    reactionevent, molecules = write_legacy_evidence(base)
    timeline = write_timeline(Path(f"{base}.timeline.h5"), status="running")

    with pytest.raises(TimedEvidenceDataError, match="not complete") as error:
        select_timed_evidence(
            timeline_file=str(timeline),
            reactionevent_file=str(reactionevent),
            molecules_file=str(molecules),
        )

    assert error.value.state == "incomplete"


def test_unknown_native_schema_is_rejected(tmp_path: Path) -> None:
    timeline = write_timeline(
        tmp_path / "run.timeline.h5", schema_version="999"
    )

    with pytest.raises(TimedEvidenceDataError, match="schema") as error:
        select_timed_evidence(timeline_file=str(timeline))

    assert error.value.state == "incompatible"


def test_native_schema_two_uses_exact_transition_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(
        tmp_path / "run.lammpstrj.timeline.h5", schema_version="2"
    )

    def forbidden_membership_build(*_args, **_kwargs):
        raise AssertionError("schema 2 rebuilt molecule-range membership")

    monkeypatch.setattr(
        NativeHdf5EvidenceAdapter,
        "build_membership",
        forbidden_membership_build,
    )

    selected = select_timed_evidence(timeline_file=str(timeline))
    built = EVENT_EVIDENCE_STORE.build(str(timeline))
    result = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=10
    )
    metadata_status = EVENT_EVIDENCE_STORE.status(
        str(timeline), metadata_only=True
    )

    assert selected.schema_version == "2"
    assert built["state"] == "ready"
    assert built["source_schema_version"] == "2"
    assert built["event_count"] == 1
    assert result["total"] == 1
    assert result["rows"][0]["association_status"] == "matched"
    assert result["rows"][0]["atom_id_list"] == [1, 2]
    assert metadata_status["timeline_file"] == str(timeline.resolve())
    assert metadata_status["source_schema_version"] == "2"
    assert metadata_status["capabilities"] == ["reaction", "molecule"]


def test_multiple_native_sources_are_explicitly_unsupported(tmp_path: Path) -> None:
    timeline = write_timeline(tmp_path / "run.timeline.h5", source_count=2)

    with pytest.raises(TimedEvidenceDataError, match="multiple sources") as error:
        select_timed_evidence(timeline_file=str(timeline))

    assert error.value.state == "unsupported"


def test_native_aggregate_count_builds_logical_occurrences(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(
        tmp_path / "run.lammpstrj.timeline.h5",
        reaction_count=2,
    )

    built = EVENT_EVIDENCE_STORE.build(str(timeline))
    result = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=10
    )

    assert built["state"] == "ready"
    assert built["event_count"] == 2
    assert built["source_kind"] == "native_hdf5"
    assert result["total"] == 2
    assert [row["association_status"] for row in result["rows"]] == [
        "matched",
        "unresolved_hmm_timeline",
    ]
    assert result["rows"][0]["atom_id_list"] == [1, 2]
    assert result["rows"][1]["atom_id_list"] == []
    assert len({row["event_id"] for row in result["rows"]}) == 2


def test_native_online_queries_do_not_open_hdf5(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(tmp_path / "run.lammpstrj.timeline.h5")
    EVENT_EVIDENCE_STORE.build(str(timeline))

    def forbidden_hdf_open(*_args, **_kwargs):
        raise AssertionError("online query opened native source")

    monkeypatch.setattr(h5py, "File", forbidden_hdf_open)

    result = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=10
    )

    assert result["total"] == 1


def test_native_and_legacy_sources_produce_the_same_semantic_event_ids(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(
        tmp_path / "native.lammpstrj.timeline.h5",
        reaction_count=2,
    )
    legacy_base = tmp_path / "legacy.lammpstrj"
    reactionevent, molecules = write_legacy_evidence(legacy_base)
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[C]+[O],[C][O]\n"
        "0,[O]+[C],[C][O]\n",
        encoding="utf-8",
    )

    EVENT_EVIDENCE_STORE.build(str(timeline))
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    native = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=10
    )
    legacy = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent),
        str(molecules),
        "[C]+[O]->[C][O]",
        limit=10,
    )

    assert [row["event_id"] for row in native["rows"]] == [
        row["event_id"] for row in legacy["rows"]
    ]


def test_prepare_cli_builds_native_manifest_and_dash_query_uses_it(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base = tmp_path / "native.lammpstrj"
    timeline = write_timeline(Path(f"{base}.timeline.h5"))

    assert prepare.main(["build", "event", str(tmp_path)]) == 0

    paths = resolve_dataset_paths(tmp_path, base.name)
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    result = dash_services.locate_rng_events(
        {"timeline": str(timeline)},
        "[C]+[O] -> [C][O]",
    )

    assert manifest["manifest_version"] == 3
    assert manifest["artifacts"]["timeline"]["exists"] is True
    assert manifest["indexes"]["rng_events"]["kind"] == "native_hdf5"
    assert manifest["indexes"]["event"]["source_kind"] == "native_hdf5"
    assert result["total"] == 1
    assert result["meta"]["source_kind"] == "native_hdf5"


def test_native_timeline_without_trajectory_reports_missing_trajectory_source(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base = tmp_path / "native.lammpstrj"
    write_timeline(Path(f"{base}.timeline.h5"))

    scan = dash_services.scan_dataset(str(tmp_path), base=str(base))
    status = dash_services.dataset_preparation_status(
        str(tmp_path),
        base=str(base),
    )

    assert scan["dataset"]["artifacts"]["trajectory"]["exists"] is False
    assert "trajectory" not in dash_services.artifacts_from_status(scan)
    assert status["events"]["source_available"] is True
    assert status["trajectory"]["source_available"] is False
    assert status["trajectory"]["state"] == "missing"
    assert (
        status["analysis_capabilities"]["trajectory_evidence"]["state"]
        == "missing-source"
    )
    assert status["trajectory_command"] == ""


def test_native_reaction_only_source_builds_chronology(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(
        tmp_path / "reaction-only.timeline.h5",
        molecule_enabled=False,
    )

    built = EVENT_EVIDENCE_STORE.build(str(timeline))
    result = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=1
    )

    assert built["association_available"] is False
    assert built["time_basis"] == "physical_timestep"
    assert result["rows"][0]["association_status"] == "reactionevent_only"
    assert result["rows"][0]["before_timestep"] == 0
    assert result["rows"][0]["after_timestep"] == 10


def test_native_membership_build_resumes_from_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(tmp_path / "resume.timeline.h5")

    def interrupt(update: dict[str, object]) -> None:
        if update.get("phase") == "indexing_molecule_ranges":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EVENT_EVIDENCE_STORE.build(
            str(timeline), progress_callback=interrupt
        )

    assert EVENT_EVIDENCE_STORE.status(str(timeline))["state"] == "building"
    resumed = EVENT_EVIDENCE_STORE.build(str(timeline))

    assert resumed["state"] == "ready"
    assert resumed["resumed"] is True
    assert resumed["event_count"] == 1


def test_native_membership_resumes_row_major_checkpoint_with_overlay(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(timed_evidence, "_MEMBERSHIP_RANGE_CHUNK_SIZE", 1)
    monkeypatch.setattr(timed_evidence, "_MEMBERSHIP_CHECKPOINT_CHUNKS", 1)
    monkeypatch.setattr(
        event_index,
        "_can_materialize_membership",
        lambda _size: False,
    )
    timeline = write_timeline(tmp_path / "row-major-resume.timeline.h5")

    def interrupt(update: dict[str, object]) -> None:
        if update.get("phase") == "indexing_molecule_ranges":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EVENT_EVIDENCE_STORE.build(
            str(timeline),
            progress_callback=interrupt,
        )

    workspace = resolve_dataset_paths(str(timeline))
    membership_path = Path(f"{workspace.event_index}.building.membership")
    assert membership_path.is_file()
    assert not Path(f"{membership_path}.overlay-f").exists()

    used_overlay: list[bool] = []
    original_materialize = NativeHdf5EvidenceAdapter.materialize_membership

    def tracking_materialize(
        adapter: NativeHdf5EvidenceAdapter,
        membership: np.ndarray[Any, Any],
        *,
        overlay: np.ndarray[Any, Any] | None = None,
        progress: Any = None,
    ) -> np.ndarray[Any, Any]:
        used_overlay.append(overlay is not None)
        return original_materialize(
            adapter,
            membership,
            overlay=overlay,
            progress=progress,
        )

    monkeypatch.setattr(
        event_index,
        "_can_materialize_membership",
        lambda _size: True,
    )
    monkeypatch.setattr(
        NativeHdf5EvidenceAdapter,
        "materialize_membership",
        tracking_materialize,
    )
    resumed = EVENT_EVIDENCE_STORE.build(str(timeline))

    assert resumed["state"] == "ready"
    assert resumed["resumed"] is True
    assert resumed["event_count"] == 1
    assert used_overlay == [True]
    assert not membership_path.exists()
    assert not Path(f"{membership_path}.overlay-f").exists()


def test_native_membership_merges_unsorted_overlapping_ranges(
    tmp_path: Path,
) -> None:
    selection = write_membership_timeline(
        tmp_path / "ranges.timeline.h5",
        frame_count=8,
        molecule_atoms=[[0, 1]],
        ranges=[(1, 3, 4), (1, 0, 1), (1, 2, 2), (1, 6, 6)],
    )

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        membership = adapter.build_membership(tmp_path / "membership.bin")
        actual = np.asarray(membership).copy()
        del membership

    expected = np.zeros((8, 2), dtype=np.uint32)
    expected[0:5, :] = 1
    expected[6, :] = 1
    np.testing.assert_array_equal(actual, expected)


def test_native_membership_expands_many_disjoint_ranges(
    tmp_path: Path,
) -> None:
    selection = write_membership_timeline(
        tmp_path / "disjoint.timeline.h5",
        frame_count=24,
        molecule_atoms=[[0, 1]],
        ranges=[(1, frame, frame) for frame in range(0, 24, 2)],
    )

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        membership = adapter.build_membership(tmp_path / "membership.bin")
        actual = np.asarray(membership).copy()
        del membership

    expected = np.zeros((24, 2), dtype=np.uint32)
    expected[::2, :] = 1
    np.testing.assert_array_equal(actual, expected)


def test_native_membership_uses_column_major_layout(
    tmp_path: Path,
) -> None:
    selection = write_membership_timeline(
        tmp_path / "column-major.timeline.h5",
        frame_count=24,
        molecule_atoms=[[0, 1]],
        ranges=[(1, frame, frame) for frame in range(0, 24, 2)],
    )

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        membership = adapter.build_membership(tmp_path / "membership.bin")
        assert membership.flags.f_contiguous
        del membership


def test_native_membership_materializes_row_major_overlay(
    tmp_path: Path,
) -> None:
    selection = write_membership_timeline(
        tmp_path / "materialize.timeline.h5",
        frame_count=6,
        molecule_atoms=[[0], [1]],
        ranges=[(1, 0, 2), (2, 3, 5)],
    )
    base_path = tmp_path / "base.bin"
    overlay_path = tmp_path / "overlay.bin"

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        base = np.memmap(
            base_path,
            dtype=adapter.membership_dtype,
            mode="w+",
            shape=adapter.membership_shape,
            order="C",
        )
        base[0:3, 0] = 1
        base.flush()
        overlay = np.memmap(
            overlay_path,
            dtype=adapter.membership_dtype,
            mode="w+",
            shape=adapter.membership_shape,
            order="F",
        )
        overlay[3:6, 1] = 2
        overlay.flush()

        materialized = adapter.materialize_membership(
            base,
            overlay=overlay,
        )
        assert materialized.flags.c_contiguous
        np.testing.assert_array_equal(
            materialized,
            np.asarray(
                [
                    [1, 0],
                    [1, 0],
                    [1, 0],
                    [0, 2],
                    [0, 2],
                    [0, 2],
                ],
                dtype=np.uint32,
            ),
        )
        del materialized
        del overlay
        del base


def test_native_membership_resumes_inside_one_molecule_group(
    tmp_path: Path, monkeypatch
) -> None:
    selection = write_membership_timeline(
        tmp_path / "resume-group.timeline.h5",
        frame_count=8,
        molecule_atoms=[[0]],
        ranges=[(1, frame, frame) for frame in range(0, 8, 2)],
    )
    target = tmp_path / "membership.bin"
    checkpoints: list[tuple[int, int]] = []
    monkeypatch.setattr(timed_evidence, "_MEMBERSHIP_RANGE_CHUNK_SIZE", 2)
    monkeypatch.setattr(timed_evidence, "_MEMBERSHIP_CHECKPOINT_CHUNKS", 1)

    def interrupt(offset: int, molecule_id: int) -> None:
        checkpoints.append((offset, molecule_id))
        raise KeyboardInterrupt

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        with pytest.raises(KeyboardInterrupt):
            adapter.build_membership(target, checkpoint=interrupt)

    assert checkpoints == [(2, 1)]
    with NativeHdf5EvidenceAdapter(selection) as adapter:
        membership = adapter.build_membership(
            target,
            start_offset=2,
            resume=True,
        )
        actual = np.asarray(membership).copy()
        del membership

    expected = np.zeros((8, 1), dtype=np.uint32)
    expected[::2, 0] = 1
    np.testing.assert_array_equal(actual, expected)


def test_native_membership_reuses_loaded_atom_definitions(
    tmp_path: Path, monkeypatch
) -> None:
    molecule_count = 600
    selection = write_membership_timeline(
        tmp_path / "definitions.timeline.h5",
        frame_count=2,
        molecule_atoms=[[index] for index in range(molecule_count)],
        ranges=[(index + 1, 0, 0) for index in range(molecule_count)],
    )
    atom_reads = 0
    original_getitem = h5py.Dataset.__getitem__

    def tracking_getitem(dataset: h5py.Dataset, key: Any) -> Any:
        nonlocal atom_reads
        if dataset.name == "/molecules/atom_ids":
            atom_reads += 1
        return original_getitem(dataset, key)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", tracking_getitem)

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        # Exercise the bounded prefetch path used when large definition arrays
        # cannot be retained by the adapter's all-definition cache.
        adapter._atom_ids_data = None
        membership = adapter.build_membership(tmp_path / "membership.bin")
        np.testing.assert_array_equal(
            membership[0],
            np.arange(1, molecule_count + 1, dtype=np.uint32),
        )
        del membership

    assert atom_reads <= 2


def test_native_membership_separates_progress_from_durable_checkpoints(
    tmp_path: Path, monkeypatch
) -> None:
    molecule_count = 600
    selection = write_membership_timeline(
        tmp_path / "checkpoint.timeline.h5",
        frame_count=2,
        molecule_atoms=[[index] for index in range(molecule_count)],
        ranges=[(index + 1, 0, 0) for index in range(molecule_count)],
    )
    current_time = 0.0

    def clock() -> float:
        nonlocal current_time
        current_time += 1.1
        return current_time

    monkeypatch.setattr(timed_evidence, "monotonic", clock, raising=False)
    monkeypatch.setattr(
        timed_evidence,
        "_MEMBERSHIP_RANGE_CHUNK_SIZE",
        100,
    )
    checkpoints: list[tuple[int, int]] = []
    progress_updates: list[tuple[int, int]] = []

    with NativeHdf5EvidenceAdapter(selection) as adapter:
        membership = adapter.build_membership(
            tmp_path / "membership.bin",
            checkpoint=lambda offset, molecule_id: checkpoints.append(
                (offset, molecule_id)
            ),
            progress=lambda offset, molecule_id: progress_updates.append(
                (offset, molecule_id)
            ),
        )
        del membership

    assert checkpoints == [(molecule_count, molecule_count)]
    assert len(progress_updates) > 1
    assert progress_updates[-1] == (molecule_count, molecule_count)


def test_clear_removes_interrupted_native_membership(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    timeline = write_timeline(tmp_path / "clear.timeline.h5")

    def interrupt(update: dict[str, object]) -> None:
        if update.get("phase") == "indexing_molecule_ranges":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EVENT_EVIDENCE_STORE.build(
            str(timeline), progress_callback=interrupt
        )
    status = EVENT_EVIDENCE_STORE.status(str(timeline))
    membership = Path(f"{status['building_path']}.membership")
    overlay = Path(f"{membership}.overlay-f")
    assert membership.is_file()
    overlay.write_bytes(b"interrupted overlay")

    cleared = EVENT_EVIDENCE_STORE.clear(str(timeline))

    assert str(membership) in cleared["removed"]
    assert str(overlay) in cleared["removed"]
    assert not membership.exists()
    assert not overlay.exists()
