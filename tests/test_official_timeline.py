from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import h5py
import numpy as np
import pytest

from reacnet_scope import services
from reacnet_scope import _official_timeline as official
from reacnet_scope import timed_evidence
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.datasets import discover_dataset_candidates
from reacnet_scope.prepare import discover_dataset
from reacnet_scope.trajectory import save_linked_trajectory
from reacnet_scope.timed_evidence import (
    NativeHdf5EvidenceAdapter, TimedEvidenceDataError, select_timed_evidence,
)
from scripts import rng_query_cli as cli
from tests.test_event_export_cli import _frame
from tests.test_timed_evidence import write_legacy_evidence, write_timeline


def write_official_timeline(path: Path, *, copies: int = 1, reverse: bool = False) -> Path:
    """Independent upstream 1.1 fixture: C + O -> CO, optionally back later."""
    strings = h5py.string_dtype("utf-8")
    frames = 5 if reverse else 2
    with h5py.File(path, "w") as handle:
        handle.attrs.update({
            "format": "reacnetgenerator-timeline", "schema_version": "1.1",
            "status": "complete", "rng_version": "test", "created_utc": "2026-01-01T00:00:00Z",
            "capabilities": json.dumps(["molecule_ranges", "reaction_events", "transition_evidence"]),
            "configuration": json.dumps({"parameters": {"runHMM": False, "stepinterval": 1},
                                         "explicit_parameters": []}),
            "atom_index_convention": "zero-based RNG canonical atom order",
            "molecule_range_basis": "observed signal",
        })

        def put(name, values, *, text=False):
            handle.create_dataset(name, data=np.asarray(values, dtype=object if text else "<i8"),
                                  dtype=strings if text else "<i8")

        put("sources/path", ["run.lammpstrj"], text=True)
        put("sources/size_bytes", [0])
        put("sources/mtime_ns", [0])
        put("frames/timestep", np.arange(frames) * 10)
        put("frames/source_id", np.zeros(frames))
        put("frames/source_frame", np.arange(frames))
        put("atoms/type", [0, 1] * copies)
        put("atoms/type_name", ["C", "O"], text=True)
        put("species/name", ["[C]", "[O]", "[C][O]"], text=True)
        molecule_atoms = [[atom] for atom in range(copies * 2)] + [
            [2 * i, 2 * i + 1] for i in range(copies)
        ]
        put("molecules/species_id", [0, 1] * copies + [2] * copies)
        put("molecules/atom_offsets", np.r_[0, np.cumsum(list(map(len, molecule_atoms)))])
        put("molecules/atom_index", [atom for atoms in molecule_atoms for atom in atoms])
        put("molecules/bond_offsets", [0] * (2 * copies + 1) + list(range(1, copies + 1)))
        put("molecules/bond_atom_index_1", np.arange(copies) * 2)
        put("molecules/bond_atom_index_2", np.arange(copies) * 2 + 1)
        put("molecules/bond_order", [1] * copies)
        ranges = []
        for molecule in range(1, 3 * copies + 1):
            if molecule <= 2 * copies:
                ranges.append((molecule, 0, 0))
                if reverse:
                    ranges.append((molecule, 4, 4))
            else:
                ranges.append((molecule, 1, 3 if reverse else 1))
        for name, values in zip(("molecule_id", "start_frame", "end_frame"), zip(*ranges), strict=True):
            put(f"molecule_ranges/{name}", values)
        put("reaction_types/reactant", ["[C]+[O]"] + (["[C][O]"] if reverse else []), text=True)
        put("reaction_types/product", ["[C][O]"] + (["[C]+[O]"] if reverse else []), text=True)
        put("reaction_types/total_count", [copies] * (2 if reverse else 1))
        put("reaction_events/transition", [0, 3] if reverse else [0])
        put("reaction_events/reaction_type_id", [0, 1] if reverse else [0])
        put("reaction_events/count", [copies] * (2 if reverse else 1))
        transitions, types, ids, sides, endpoints, old, new = [], [], [], [], [], [], []
        for backward in range(2 if reverse else 1):
            for i in range(copies):
                transitions.append(3 if backward else 0)
                types.append(backward)
                left, right, product = 2 * i + 1, 2 * i + 2, 2 * copies + i + 1
                ids.extend([product, left, right] if backward else [left, right, product])
                sides.extend([0, 1, 1] if backward else [0, 0, 1])
                endpoints.append((2 * i, 2 * i + 1))
                old.append(backward)
                new.append(1 - backward)
        put("transition_evidence/transition", transitions)
        put("transition_evidence/reaction_type_id", types)
        put("transition_evidence/participant_offsets", np.arange(len(types) + 1) * 3)
        put("transition_evidence/participant_molecule_id", ids)
        put("transition_evidence/participant_side", sides)
        put("transition_evidence/bond_change_offsets", np.arange(len(types) + 1))
        put("transition_evidence/bond_atom_index_1", [pair[0] for pair in endpoints])
        put("transition_evidence/bond_atom_index_2", [pair[1] for pair in endpoints])
        put("transition_evidence/before_order", old)
        put("transition_evidence/after_order", new)
    return path


def test_official_dictionary_ids_and_split_bonds_match_existing_exact_evidence(tmp_path):
    upstream = write_official_timeline(tmp_path / "official.timeline.h5")
    fork = write_timeline(tmp_path / "fork.timeline.h5", schema_version="2")
    evidence = []
    for path in (upstream, fork):
        selection = select_timed_evidence(timeline_file=str(path))
        with NativeHdf5EvidenceAdapter(selection) as adapter:
            evidence.append(adapter.transition(0, None))
            assert adapter.uses_exact_transition_evidence
            assert adapter.membership_shape == (2, 2)
    assert evidence[0] == evidence[1]
    assert evidence[0].after_molecules[0].bond_ids == ("0-1-1",)


def test_selection_and_resource_estimates_do_not_scan_official_payload(tmp_path, monkeypatch):
    path = write_official_timeline(tmp_path / "test.timeline.h5")

    def forbidden(*args, **kwargs):
        raise AssertionError("selection must not prepare event blocks")

    monkeypatch.setattr(official.OfficialTimelineView, "__init__", forbidden)
    selection = select_timed_evidence(timeline_file=str(path))
    assert selection.schema_version == "1.1"
    assert selection.frame_count == 2
    assert timed_evidence.native_membership_bytes(selection) == 16


def test_bounded_normalization_without_optional_full_array_caches(tmp_path, monkeypatch):
    path = write_official_timeline(tmp_path / "test.timeline.h5", copies=2, reverse=True)
    selection = select_timed_evidence(timeline_file=str(path))
    monkeypatch.setattr(official, "BLOCK_ROWS", 1)
    monkeypatch.setattr(timed_evidence, "_can_cache_native_arrays", lambda *a, **k: False)
    getitem = h5py.Dataset.__getitem__
    watched = ("/reaction_events/", "/transition_evidence/", "/molecules/bond_atom_index_")

    def bounded(dataset, key, *args, **kwargs):
        if dataset.name.startswith(watched):
            assert key is not Ellipsis
            assert not isinstance(key, slice) or key.stop is not None
        return getitem(dataset, key, *args, **kwargs)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", bounded)
    with NativeHdf5EvidenceAdapter(selection) as adapter:
        events = list(adapter.iter_transitions(None))
        assert [len(event.reactions) for event in events] == [2, 0, 0, 2]
        assert [r.count for e in events for r in e.reactions] == [1] * 4
        assert list(adapter.iter_frame_references()) == [(i, i * 10, 0, i) for i in range(5)]


@pytest.mark.parametrize("attribute,value,state", [
    ("schema_version", "1.2", "incompatible"),
    ("status", "incomplete", "incomplete"),
    ("format", "another-format", "incompatible"),
    ("capabilities", '[]', "incompatible"),
    ("configuration", '{}', "incompatible"),
    ("molecule_range_basis", "HMM signal", "incompatible"),
    ("atom_index_convention", "one-based", "incompatible"),
])
def test_invalid_official_source_never_falls_back_to_csv(tmp_path, attribute, value, state):
    base = tmp_path / "run.lammpstrj"
    reaction, molecules = write_legacy_evidence(base)
    path = write_official_timeline(Path(f"{base}.timeline.h5"))
    with h5py.File(path, "r+") as handle:
        handle.attrs[attribute] = value
    with pytest.raises(TimedEvidenceDataError) as caught:
        select_timed_evidence(timeline_file=str(path), reactionevent_file=str(reaction), molecules_file=str(molecules))
    assert caught.value.state == state


@pytest.mark.parametrize("name,index,value", [
    ("molecules/species_id", 0, -1),
    ("molecules/atom_index", 0, 99),
    ("molecules/bond_order", 0, -1),
    ("transition_evidence/reaction_type_id", 0, -1),
    ("transition_evidence/participant_side", 0, 256),
    ("transition_evidence/participant_molecule_id", 0, 99),
    ("transition_evidence/after_order", 0, 2),
    ("reaction_events/count", 0, 2),
    ("reaction_types/total_count", 0, 2),
    ("molecule_ranges/start_frame", 0, 1),
    ("frames/source_id", 0, 1),
    ("frames/source_frame", 1, 99),
    ("transition_evidence/participant_offsets", 1, -1),
])
def test_invalid_consumed_evidence_is_rejected_before_publication(tmp_path, monkeypatch, name, index, value):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    path = write_official_timeline(tmp_path / "test.timeline.h5")
    with h5py.File(path, "r+") as handle:
        handle[name][index] = value
    with pytest.raises(TimedEvidenceDataError):
        EVENT_EVIDENCE_STORE.build(str(path))
    assert EVENT_EVIDENCE_STORE.status(str(path))["state"] != "ready"


@pytest.mark.parametrize("mode", ["dtype", "missing", "offset_end", "multiple_sources"])
def test_official_column_contract_and_existing_single_source_boundary(tmp_path, mode):
    path = write_official_timeline(tmp_path / "test.timeline.h5")
    with h5py.File(path, "r+") as handle:
        if mode == "dtype":
            values = handle["frames/source_id"][:].astype("uint64")
            del handle["frames/source_id"]
            handle["frames/source_id"] = values
        elif mode == "missing":
            del handle["transition_evidence/bond_atom_index_2"]
        elif mode == "offset_end":
            handle["molecules/atom_offsets"][-1] = 99
        else:
            for name, values in (("path", ["a", "b"]), ("size_bytes", [0, 0]), ("mtime_ns", [0, 0])):
                del handle[f"sources/{name}"]
                handle.create_dataset(f"sources/{name}", data=values,
                                      dtype=h5py.string_dtype("utf-8") if name == "path" else "<i8")
    with pytest.raises(TimedEvidenceDataError) as caught:
        select_timed_evidence(timeline_file=str(path))
    assert caught.value.state == ("unsupported" if mode == "multiple_sources" else
                                  "incomplete" if mode == "missing" else "incompatible")


def test_official_sampling_and_repeated_timesteps_preserve_frame_identity(tmp_path):
    path = write_official_timeline(tmp_path / "test.timeline.h5")
    with h5py.File(path, "r+") as handle:
        config = json.loads(handle.attrs["configuration"])
        config["parameters"]["stepinterval"] = 2
        handle.attrs["configuration"] = json.dumps(config)
        handle["frames/source_frame"][:] = [0, 2]
        handle["frames/timestep"][:] = [10, 10]
    with NativeHdf5EvidenceAdapter(select_timed_evidence(timeline_file=str(path))) as adapter:
        assert list(adapter.iter_frame_references()) == [(0, 10, 0, 0), (1, 10, 0, 2)]
        assert adapter.transition(0, None).transition_index == 0


def test_exact_participants_must_be_present_at_the_correct_frame(tmp_path):
    path = write_official_timeline(tmp_path / "test.timeline.h5")
    with h5py.File(path, "r+") as handle:
        handle["molecule_ranges/start_frame"][-1] = 0
        handle["molecule_ranges/end_frame"][-1] = 0
    with NativeHdf5EvidenceAdapter(select_timed_evidence(timeline_file=str(path))) as adapter:
        with pytest.raises(TimedEvidenceDataError, match="absent"):
            adapter.transition(0, None)


def test_empty_event_tables_and_single_frame_are_valid(tmp_path):
    path = write_official_timeline(tmp_path / "test.timeline.h5")
    with h5py.File(path, "r+") as handle:
        for group in ("reaction_events", "reaction_types", "transition_evidence"):
            for name in list(handle[group]):
                dtype = handle[f"{group}/{name}"].dtype
                del handle[f"{group}/{name}"]
                handle.create_dataset(f"{group}/{name}", data=[0] if name.endswith("offsets") else [], dtype=dtype)
        for name in ("source_id", "source_frame", "timestep"):
            del handle[f"frames/{name}"]
            handle.create_dataset(f"frames/{name}", data=[0], dtype="<i8")
        for name in ("molecule_id", "start_frame", "end_frame"):
            values = handle[f"molecule_ranges/{name}"][:2]
            del handle[f"molecule_ranges/{name}"]
            handle.create_dataset(f"molecule_ranges/{name}", data=values)
    with NativeHdf5EvidenceAdapter(select_timed_evidence(timeline_file=str(path))) as adapter:
        assert list(adapter.iter_transitions(None)) == []
        assert len(list(adapter.iter_molecular_state_spans())) == 2


def test_counts_stable_identity_continuity_and_online_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    path = write_official_timeline(tmp_path / "test.timeline.h5", copies=2, reverse=True)
    original = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
    EVENT_EVIDENCE_STORE.build(str(path))
    rows = EVENT_EVIDENCE_STORE.query_events(str(path), "", "[C]+[O]->[C][O]", limit=10)["rows"]
    assert len(rows) == 2
    assert len({row["event_id"] for row in rows}) == 2
    assert {row["atom_ids"] for row in rows} == {"1,2", "3,4"}
    assert original == (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)

    def forbidden(*args, **kwargs):
        raise AssertionError("online queries must use published indexes")

    monkeypatch.setattr(h5py, "File", forbidden)
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, "build", forbidden)
    artifacts = {"timeline": str(path)}
    report = services.start_lineage_explorer(artifacts, event_id=rows[0]["event_id"])
    report = services.expand_lineage_explorer(artifacts, report, all_branches=True)
    product = next(segment for segment in report["segments"].values() if segment["species"] == "[C][O]")
    assert (product["start_frame"], product["end_frame"]) == (1, 3)
    report = services.expand_lineage_explorer(artifacts, report, segment_id=product["segment_id"])
    assert len(report["occurrences"]) == 2
    assert len(EVENT_EVIDENCE_STORE.query_events(str(path), "", "[C][O]->[C]+[O]", limit=10)["rows"]) == 2


def test_event_ids_are_independent_of_upstream_or_fork_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    upstream = write_official_timeline(tmp_path / "official.timeline.h5")
    fork = write_timeline(tmp_path / "fork.timeline.h5", schema_version="2")
    ids = []
    for path in (upstream, fork):
        EVENT_EVIDENCE_STORE.build(str(path))
        ids.append(EVENT_EVIDENCE_STORE.query_events(str(path), "", "[C]+[O]->[C][O]", limit=1)["rows"][0]["event_id"])
    assert ids[0] == ids[1]


@pytest.mark.parametrize("output_directory", [False, True])
def test_official_events_export_matching_trajectory_and_dft_geometry(tmp_path, monkeypatch, output_directory):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(_frame(0) + _frame(10))
    case = tmp_path / "output" if output_directory else tmp_path
    case.mkdir(exist_ok=True)
    reaction = case / "reactionabcd" if output_directory else Path(f"{trajectory}.reactionabcd")
    reaction.write_text("1 [C]+[O]->[C][O]\n")
    path = write_official_timeline(case / "timeline.h5" if output_directory else Path(f"{trajectory}.timeline.h5"))
    if output_directory:
        save_linked_trajectory(str(path), str(trajectory))
    EVENT_EVIDENCE_STORE.build(str(path))
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    row = EVENT_EVIDENCE_STORE.query_events(str(path), "", "[C]+[O]->[C][O]", limit=1)["rows"][0]
    for command, extra in (
        ("export-event", ["--before-frames", "0", "--after-frames", "0"]),
        ("export-dft-geometry", ["--source-unit", "angstrom", "--state", "reactants=0,1",
                                 "--state", "products=0,1", "--confirm-isolated-cluster"]),
    ):
        target = tmp_path / f"{command}.zip"
        assert cli.main([command, "--case", str(case), "--event-id", row["event_id"],
                         "--type-map", "1=C,2=O", *extra, "--out", str(target)]) == 0
        with ZipFile(target) as archive:
            document = json.loads(archive.read("event.json" if command == "export-event" else "occurrence.json"))
            event = document["event"] if command == "export-event" else document
            assert event["event_id"] == row["event_id"]
            assert any(name.endswith((".xyz", ".extxyz")) for name in archive.namelist())


def test_official_output_directory_discovery_and_explicit_file_collections(tmp_path, monkeypatch):
    from reacnet_scope.file_collections import artifact_role, collect_files
    from reacnet_scope.queries import build_dataset_status_payload
    from reacnet_scope import dir_browser

    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    path = write_official_timeline(tmp_path / "timeline.h5")
    (tmp_path / "reactionabcd").write_text("1 [C]+[O]->[C][O]\n")
    (tmp_path / "species").write_text("test fixture; discovery must not parse it\n")
    candidates = discover_dataset_candidates(tmp_path)
    assert len(candidates) == 1
    assert candidates[0]["artifact_paths"] == {
        "timeline": str(path), "reaction": str(tmp_path / "reactionabcd"), "species": str(tmp_path / "species")
    }
    for case, base in ((str(tmp_path), ""), (str(path), ""), (str(tmp_path), "timeline.h5")):
        dataset = discover_dataset(case, base)
        assert dataset["timeline"] == str(path)
        assert dataset["trajectory"] == ""
        assert dataset["base"] == str(path)
    assert artifact_role(path) == ("timeline", "timeline.h5")
    assert str(path) in collect_files([str(path)])["paths"]
    status = build_dataset_status_payload({"dataset_dir": [str(tmp_path)]})
    assert status["dataset"]["artifacts"]["timeline"]["exists"]
    assert not status["dataset"]["artifacts"]["trajectory"]["exists"]


def test_official_default_name_does_not_silently_join_another_dataset(tmp_path):
    write_official_timeline(tmp_path / "timeline.h5")
    (tmp_path / "other.reactionabcd").write_text("1 [C]+[O]->[C][O]\n")
    with pytest.raises(RuntimeError, match="ambiguous"):
        discover_dataset(str(tmp_path))
    assert discover_dataset(str(tmp_path), "timeline.h5")["timeline"] == str(tmp_path / "timeline.h5")
