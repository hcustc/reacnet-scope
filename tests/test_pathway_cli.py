from __future__ import annotations

import builtins
import csv
import importlib.util
import os
from pathlib import Path

import pytest

from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope import services as svc
from scripts import rng_query_cli as cli


def _write_reaction_file(tmp_path: Path, *, name: str = "run.reactionabcd") -> Path:
    reaction = tmp_path / name
    reaction.write_text("4 [H] + [O] -> [H][O]\n", encoding="utf-8")
    return reaction


def test_species_evolution_cli_uses_index_and_exports_raw_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    reaction = _write_reaction_file(tmp_path)
    species = tmp_path / "run.species"
    species.write_text(
        "Timestep 0: [H] 0\nTimestep 10: [H] 10\nTimestep 20: [H] 0\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))
    output = tmp_path / "evolution.csv"
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if os.path.abspath(os.fspath(file)) == os.path.abspath(species):
            raise AssertionError("formal CLI opened raw Species source")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    args = cli.build_parser().parse_args(
        [
            "species-evolution",
            "--reac",
            str(reaction),
            "--species-file",
            str(species),
            "--target",
            "smiles:[H]",
            "--normalize",
            "max",
            "--smooth-window",
            "3",
            "--out-csv",
            str(output),
        ]
    )

    assert args.func is cli.cmd_species_evolution
    assert args.func(args) == 0
    assert "source_mode=prepared_index" in capsys.readouterr().out
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["source_timestep"] for row in rows] == ["0", "10", "20"]
    assert [row["[H]"] for row in rows] == ["0.0", "10.0", "0.0"]


def test_intermediate_candidates_cli_is_not_available() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["intermediate-candidates"])


def test_candidate_paths_cli_accepts_multiple_exact_start_species() -> None:
    args = cli.build_parser().parse_args(
        [
            "candidate-paths",
            "--source",
            "rep1=/data/run.lammpstrj",
            "--reac",
            "/data/run.lammpstrj.reactionabcd",
            "--start",
            "CCO",
            "--start",
            "O",
            "--min-steps",
            "2",
            "--max-steps",
            "5",
        ]
    )

    assert args.func is cli.cmd_candidate_paths
    assert args.start == ["CCO", "O"]
    assert args.min_steps == 2
    assert args.max_steps == 5


def test_candidate_paths_cli_uses_bounded_discovery_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prefix = tmp_path / "run.lammpstrj"
    reaction = Path(f"{prefix}.reactionabcd")
    reactionevent = Path(f"{prefix}.reactionevent.csv")
    molecules = Path(f"{prefix}.molecules.csv")
    reaction.write_text("8 A->B\n6 B->C\n", encoding="utf-8")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,A,B\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n0,A,0,\n10,B,0,\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_discover(artifacts, starts, **kwargs):
        captured.update(
            artifacts=artifacts,
            starts=starts,
            kwargs=kwargs,
        )
        return {
            "path_count": 1,
            "truncated": False,
            "energy_status": "not_provided",
            "paths": [
                {
                    "rank": 1,
                    "score": 1.0,
                    "minimum_step_occurrence_count": 1,
                    "start_species": "A",
                    "species": ["A", "B", "C"],
                    "reaction_keys": ["A->B", "B->C"],
                }
            ],
        }

    monkeypatch.setattr(
        svc,
        "discover_candidate_paths_for_dash",
        fake_discover,
    )
    args = cli.build_parser().parse_args(
        [
            "candidate-paths",
            "--source",
            f"rep1={prefix}",
            "--reac",
            str(reaction),
            "--start",
            "A",
        ]
    )

    assert args.func(args) == 0
    assert captured["starts"] == ["A"]
    assert captured["kwargs"]["max_expansions"] == 5_000
    assert "# candidate_paths=1" in capsys.readouterr().out


def test_candidate_path_module_and_public_service_are_available() -> None:
    assert importlib.util.find_spec("reacnet_scope.candidate_paths") is not None
    assert hasattr(svc, "discover_candidate_paths_for_dash")
