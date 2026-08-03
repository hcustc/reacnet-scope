from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import resolve_dataset_paths
from reacnet_scope import prepare
from reacnet_scope.timed_evidence import (
    TimedEvidenceDataError,
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
    timeline = write_timeline(tmp_path / "run.timeline.h5", schema_version="2")

    with pytest.raises(TimedEvidenceDataError, match="schema") as error:
        select_timed_evidence(timeline_file=str(timeline))

    assert error.value.state == "incompatible"


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
    assert membership.is_file()

    cleared = EVENT_EVIDENCE_STORE.clear(str(timeline))

    assert str(membership) in cleared["removed"]
    assert not membership.exists()
