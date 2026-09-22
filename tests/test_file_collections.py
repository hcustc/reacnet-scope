from pathlib import Path

import pytest

from reacnet_scope import dir_browser, services as svc
from reacnet_scope import file_collections as fc
from reacnet_scope.dataset_context import capture_dataset_revision
from reacnet_scope.prepare import discover_dataset, run_preparation
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "abundance" / "run.species"
    reaction = tmp_path / "network" / "run.reactionabcd"
    species.parent.mkdir()
    reaction.parent.mkdir()
    species.write_text("Timestep 0: [H] 2\nTimestep 10: [H][H] 1\n")
    reaction.write_text("[H] + [H] -> [H][H] 1\n")
    return {"species": str(species), "reaction": str(reaction)}


def test_cross_directory_collection_prepares_queries_and_restores(sources):
    candidate = svc.collection_candidate(sources, "氢气运行")
    validation = svc.validate_file_collection_candidate(candidate)
    assert not Path(candidate["base"]).exists()
    svc.commit_file_collection(validation)
    current = svc.current_dataset_from_validation(validation)
    assert svc.revalidate_current_dataset(current)["state"] == "active"
    assert discover_dataset(candidate["base"])["species"] == sources["species"]
    assert run_preparation(action="build", case=candidate["base"], capability="element-distribution") == 0
    assert SPECIES_COMPOSITION_STORE.species_count_series(sources["species"], [0, 10], "[H]") == {0: 2, 10: 0}
    assert svc.dataset_preparation_status(candidate["folder"], base=candidate["base"])["composition"]["state"] == "ready"
    assert Path(sources["species"]).read_text() == "Timestep 0: [H] 2\nTimestep 10: [H][H] 1\n"


def test_supplement_keeps_identity_and_rejects_late_definition(sources, tmp_path):
    original = svc.collection_candidate({"species": sources["species"]}, "run")
    valid = svc.validate_file_collection_candidate(original)
    svc.commit_file_collection(valid)
    supplement = svc.collection_candidate(sources, "run", existing_base=original["base"])
    pending = svc.validate_file_collection_candidate(supplement)
    competing = svc.collection_candidate({"species": sources["species"]}, "renamed", existing_base=original["base"])
    svc.commit_file_collection(svc.validate_file_collection_candidate(competing))
    with pytest.raises(svc.ServiceError, match="另一请求"):
        svc.commit_file_collection(pending)
    retry = svc.collection_candidate(sources, "run", existing_base=original["base"])
    svc.commit_file_collection(svc.validate_file_collection_candidate(retry))
    assert fc.read_collection(original["base"])["dataset_id"] == original["collection_id"]


def test_source_change_between_validation_and_commit_is_rejected(sources):
    candidate = svc.collection_candidate(sources, "run")
    validation = svc.validate_file_collection_candidate(candidate)
    Path(sources["species"]).write_text("Timestep 0: [O] 1\n")
    with pytest.raises(svc.ServiceError, match="源文件已变化"):
        svc.commit_file_collection(validation)
    assert not Path(candidate["base"]).exists()


def test_folder_and_multiselect_have_same_file_collection(sources):
    direct = fc.collect_files(sources.values())
    folders = fc.collect_files([str(Path(p).parent) for p in sources.values()])
    assert direct == folders
    preview = fc.preview_file_collection(direct["paths"])
    assert len(preview["groups"]) == 1
    assert preview["groups"][0]["artifact_paths"] == sources


def test_duplicate_roles_split_runs_and_explicit_merge_reports_conflict(sources, tmp_path):
    copy = tmp_path / "other" / "run.species"
    copy.parent.mkdir()
    copy.write_text("Timestep 0: [O] 1\n")
    paths = [sources["species"], str(copy)]
    assert len(fc.preview_file_collection(paths)["groups"]) == 2
    merged = fc.preview_file_collection(paths, {p: "merged" for p in paths})
    assert not merged["groups"][0]["valid"]
    assert "多个来源" in merged["groups"][0]["errors"][0]


def test_selection_bounds_permission_and_unsupported_are_visible(sources, tmp_path, monkeypatch):
    other = tmp_path / "note.txt"
    other.write_text("not evidence")
    result = fc.collect_files([str(other), "/etc/passwd", sources["species"]])
    assert result["paths"] == [sources["species"]]
    assert {e["reason"] for e in result["errors"]} == {"unsupported_artifact", "path_out_of_bounds"}
    monkeypatch.setattr(fc, "MAX_ENTRIES", 0)
    assert fc.collect_files([str(Path(sources["species"]).parent)])["truncated"]


def test_collection_revision_includes_explicit_paths_even_with_equal_stats(sources, tmp_path):
    import os
    first = Path(sources["species"])
    second = tmp_path / "copy.species"
    second.write_bytes(first.read_bytes())
    os.utime(second, ns=(first.stat().st_atime_ns, first.stat().st_mtime_ns))
    candidate = svc.collection_candidate(sources, "run")
    changed = {**candidate, "artifact_paths": {**sources, "species": str(second)}}
    assert capture_dataset_revision(candidate) != capture_dataset_revision(changed)


def test_cross_directory_csv_pair_prepares_real_event_evidence(sources, tmp_path):
    event = tmp_path / "events" / "chemistry.reactionevent.csv"
    molecules = tmp_path / "structures" / "members.molecules.csv"
    event.parent.mkdir()
    molecules.parent.mkdir()
    event.write_text("Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n")
    molecules.write_text("Timestep,Species,AtomIDs,BondIDs\n0,[C],0,\n0,[O],1,\n10,[C][O],0;1,0-1-1\n")
    candidate = svc.collection_candidate({"reactionevent": str(event), "molecules": str(molecules)}, "cross-directory event")
    svc.commit_file_collection(svc.validate_file_collection_candidate(candidate))
    assert svc.prepare_dataset_workspace(candidate["folder"], base=candidate["base"], kind="event", automatic=True)["ok"]
    payload = svc.locate_rng_events(candidate["artifact_paths"], reaction_text="[C]+[O]->[C][O]")
    assert payload["rows"][0]["atom_id_list"] == [1, 2]
    assert svc.prepare_dataset_workspace(candidate["folder"], base=candidate["base"], kind="event", automatic=True)["reused"]


def test_broken_native_source_never_falls_back_to_selected_csv(sources, tmp_path):
    timeline = tmp_path / "broken.timeline.h5"
    timeline.write_bytes(b"not HDF5")
    event = tmp_path / "valid.reactionevent.csv"
    event.write_text("Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n")
    candidate = svc.collection_candidate({"timeline": str(timeline), "reactionevent": str(event)}, "broken native")
    svc.commit_file_collection(svc.validate_file_collection_candidate(candidate))
    with pytest.raises(svc.ServiceError):
        svc.prepare_dataset_workspace(candidate["folder"], base=candidate["base"], kind="event", automatic=True)


def test_rebinding_collection_invalidates_captured_preparation_revision(sources, tmp_path):
    from reacnet_scope.prepare import _capability_source_revision
    candidate = svc.collection_candidate(sources, "original")
    svc.commit_file_collection(svc.validate_file_collection_candidate(candidate))
    captured = discover_dataset(candidate["base"])
    before = _capability_source_revision(captured, "composition")
    other = tmp_path / "new.species"
    other.write_text("Timestep 0: [O] 1\n")
    updated = svc.collection_candidate({**sources, "species": str(other)}, "new source", existing_base=candidate["base"])
    svc.commit_file_collection(svc.validate_file_collection_candidate(updated))
    assert _capability_source_revision(captured, "composition") != before


def test_same_stat_source_rebind_marks_changed_capability(sources, tmp_path):
    import os
    first = Path(sources["species"])
    replacement = tmp_path / "new.species"
    replacement.write_bytes(first.read_bytes())
    os.utime(replacement, ns=(first.stat().st_atime_ns, first.stat().st_mtime_ns))
    candidate = svc.collection_candidate(sources, "original")
    validation = svc.validate_file_collection_candidate(candidate)
    svc.commit_file_collection(validation)
    current = svc.current_dataset_from_validation(validation)
    updated = svc.collection_candidate({**sources, "species": str(replacement)}, "rebound", existing_base=candidate["base"])
    svc.commit_file_collection(svc.validate_file_collection_candidate(updated))
    changed = svc.revalidate_current_dataset(current)
    assert changed["state"] == "revision-changed"
    assert "species" in changed["context"]["invalidated_artifacts"]


def test_failed_definition_write_preserves_old_definition(sources, monkeypatch):
    original = svc.collection_candidate({"species": sources["species"]}, "run")
    svc.commit_file_collection(svc.validate_file_collection_candidate(original))
    old = Path(original["base"]).read_bytes()
    supplement = svc.collection_candidate(sources, "run", existing_base=original["base"])
    pending = svc.validate_file_collection_candidate(supplement)
    def full_disk(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(fc.os, "replace", full_disk)
    with pytest.raises(svc.ServiceError) as error:
        svc.commit_file_collection(pending)
    assert error.value.reason == "workspace_write_failed"
    assert Path(original["base"]).read_bytes() == old
