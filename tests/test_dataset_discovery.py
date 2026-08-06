from __future__ import annotations

import os
from pathlib import Path

import pytest

from reacnet_scope.datasets import discover_dataset_candidates, choose_dataset_candidate
from reacnet_scope.prepare import discover_dataset


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

    by_label = {item["label"]: item for item in candidates}
    assert set(by_label) == {"second.lammpstrj", "first.lammpstrj"}
    assert by_label["second.lammpstrj"]["kinds"] == ["reaction"]
    assert by_label["first.lammpstrj"]["kinds"] == ["species"]


def test_discovery_sorts_stably_by_name_not_completeness_or_mtime(tmp_path):
    alpha = tmp_path / "Alpha.lammpstrj"
    beta = tmp_path / "beta.lammpstrj"
    Path(f"{alpha}.species").write_text("fixture")
    Path(f"{beta}.reactionabcd").write_text("fixture")
    Path(f"{beta}.species").write_text("fixture")
    Path(f"{beta}.reactionevent.csv").write_text("fixture")
    os.utime(Path(f"{alpha}.species"), (100, 100))
    for path in (
        Path(f"{beta}.reactionabcd"),
        Path(f"{beta}.species"),
        Path(f"{beta}.reactionevent.csv"),
    ):
        os.utime(path, (200, 200))

    candidates = discover_dataset_candidates(tmp_path)

    assert [item["label"] for item in candidates] == [
        "Alpha.lammpstrj", "beta.lammpstrj"
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


def test_discovery_recognizes_timeline_without_opening_it(tmp_path, monkeypatch):
    base = tmp_path / "native.lammpstrj"
    timeline = Path(f"{base}.timeline.h5")
    timeline.write_bytes(b"not opened during discovery")
    real_open = Path.open

    def forbidden_open(path, *args, **kwargs):
        if path == timeline:
            raise AssertionError("discovery opened timeline data")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_open)

    candidates = discover_dataset_candidates(tmp_path)

    assert candidates[0]["base"] == str(base)
    assert candidates[0]["kinds"] == ["timeline"]
    assert candidates[0]["artifact_paths"] == {"timeline": str(timeline)}


def test_prepare_discovery_accepts_timeline_path_and_directory(tmp_path):
    base = tmp_path / "native.lammpstrj"
    timeline = Path(f"{base}.timeline.h5")
    timeline.touch()

    assert discover_dataset(str(timeline))["base"] == str(base)
    discovered = discover_dataset(str(tmp_path))
    assert discovered["base"] == str(base)
    assert discovered["timeline"] == str(timeline)
