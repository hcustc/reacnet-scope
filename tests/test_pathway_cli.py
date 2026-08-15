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


def test_automatic_candidate_paths_cli_is_not_available() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["candidate-paths"])


def test_automatic_candidate_path_modules_and_public_services_are_removed() -> None:
    assert importlib.util.find_spec("reacnet_scope.pathways") is None
    assert importlib.util.find_spec("reacnet_scope.pathway_export") is None
    assert not hasattr(svc, "find_pathways")
    assert not hasattr(svc, "build_pathway_elements")
