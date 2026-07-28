from __future__ import annotations

import os
from pathlib import Path

import pytest

from reacnet_scope.datasets import discover_dataset_candidates, choose_dataset_candidate


def test_discovery_groups_artifacts_without_opening_sources(tmp_path, monkeypatch):
    base = tmp_path / "rp3.lammpstrj"
    Path(f"{base}.reactionabcd").write_text("1 A->B\n")
    Path(f"{base}.species").write_text("Timestep 0: A 1\n")
    Path(f"{base}.reactionevent.csv").write_text(
        "Timestep_Index,Reactant,Product\n0,A,B\n"
    )
    Path(f"{base}.molecules.csv").write_text(
        "Timestep,Species,AtomIDs,BondIDs\n0,A,0,\n1,B,0,\n"
    )
    protected = {
        str(Path(f"{base}.reactionabcd")),
        str(Path(f"{base}.species")),
        str(Path(f"{base}.reactionevent.csv")),
        str(Path(f"{base}.molecules.csv")),
    }
    real_open = Path.open

    def forbidden_open(path, *args, **kwargs):
        if str(path) in protected:
            raise AssertionError("discovery opened source data")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_open)
    candidates = discover_dataset_candidates(tmp_path)

    assert [item["label"] for item in candidates] == ["rp3.lammpstrj"]
    assert candidates[0]["kinds"] == [
        "molecules", "reaction", "reactionevent", "species"
    ]
    assert candidates[0]["artifact_paths"] == {
        "molecules": str(Path(f"{base}.molecules.csv")),
        "reaction": str(Path(f"{base}.reactionabcd")),
        "reactionevent": str(Path(f"{base}.reactionevent.csv")),
        "species": str(Path(f"{base}.species")),
    }


def test_discovery_separates_prefixes_and_ignores_unknown_files(tmp_path):
    first = tmp_path / "first.lammpstrj"
    second = tmp_path / "second.lammpstrj"
    Path(f"{first}.species").write_text("fixture")
    Path(f"{second}.reactionabcd").write_text("fixture")
    Path(f"{second}.route").write_text("fixture")
    (tmp_path / "notes.txt").write_text("not a dataset artifact")

    candidates = discover_dataset_candidates(tmp_path)

    assert [item["label"] for item in candidates] == [
        "second.lammpstrj", "first.lammpstrj"
    ]
    assert [item["kinds"] for item in candidates] == [
        ["reaction", "route"], ["species"]
    ]


def test_discovery_sorts_by_completeness_then_latest_mtime(tmp_path):
    newer = tmp_path / "newer.lammpstrj"
    older = tmp_path / "older.lammpstrj"
    for base in (newer, older):
        Path(f"{base}.reactionabcd").write_text("fixture")
        Path(f"{base}.species").write_text("fixture")
    for path in (Path(f"{older}.reactionabcd"), Path(f"{older}.species")):
        os.utime(path, (100, 100))
    for path in (Path(f"{newer}.reactionabcd"), Path(f"{newer}.species")):
        os.utime(path, (200, 200))

    candidates = discover_dataset_candidates(tmp_path)

    assert [item["label"] for item in candidates] == [
        "newer.lammpstrj", "older.lammpstrj"
    ]


def test_discovery_ignores_macos_appledouble_sidecars(tmp_path):
    base = tmp_path / "run.lammpstrj"
    Path(f"{base}.reactionabcd").write_text("4 [H] -> [H][H]\n")
    Path(f"{base}.species").write_text("[H] 1\n")
    (tmp_path / "._run.lammpstrj.reactionabcd").write_bytes(b"metadata")
    (tmp_path / "._run.lammpstrj.species").write_bytes(b"metadata")

    candidates = discover_dataset_candidates(tmp_path)

    assert [item["label"] for item in candidates] == ["run.lammpstrj"]


def test_choose_dataset_candidate_requires_preference_when_ambiguous(tmp_path):
    first = tmp_path / "first.lammpstrj"
    second = tmp_path / "second.lammpstrj"
    Path(f"{first}.species").write_text("fixture")
    Path(f"{second}.species").write_text("fixture")
    candidates = discover_dataset_candidates(tmp_path)

    assert choose_dataset_candidate(candidates) is None
    assert choose_dataset_candidate(candidates, str(first.resolve())) == next(
        candidate for candidate in candidates if candidate["base"] == str(first.resolve())
    )


def test_choose_dataset_candidate_selects_the_only_candidate(tmp_path):
    base = tmp_path / "only.lammpstrj"
    Path(f"{base}.species").write_text("fixture")

    candidates = discover_dataset_candidates(tmp_path)

    assert choose_dataset_candidate(candidates) == candidates[0]


def test_discovery_rejects_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="dataset folder not found"):
        discover_dataset_candidates(tmp_path / "missing")
