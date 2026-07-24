from __future__ import annotations

import builtins
import csv
import json
import os
from pathlib import Path
from typing import Any

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts import rng_query_cli as cli


def _write_reaction_file(tmp_path: Path, *, name: str = "run.reactionabcd") -> Path:
    reaction = tmp_path / name
    reaction.write_text("4 [H] + [O] -> [H][O]\n", encoding="utf-8")
    return reaction


def _network_only_payload(*, paths: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "paths": [] if paths is None else paths,
        "query": {
            "start_smiles": "[H]",
            "direction": "downstream",
            "max_depth": 3,
            "max_branches": 5,
            "max_paths": 20,
            "max_expansions": 5000,
            "min_net_tp": 1,
            "min_directionality": 0.05,
            "score_version": "candidate-path/v1",
        },
        "source_signatures": {},
        "reason": "ok" if paths else "species_absent",
        "truncated": False,
        "expansions": 0,
        "score_version": "candidate-path/v1",
        "evidence_status": "network_only",
    }


def _one_path_payload() -> dict[str, Any]:
    step = {
        "reaction_key": "[H]+[O]+[O]->[H][O]+[O]",
        "traversal_direction": "downstream",
        "focal_input": "[H]",
        "focal_output": "[H][O]",
        "reactants": ["[H]", "[O]", "[O]"],
        "products": ["[H][O]", "[O]"],
        "forward_tp": 4,
        "reverse_tp": 1,
        "net_tp": 3,
        "net_share": 0.1234567890123456,
        "directionality": 0.6,
        "event_coverage": 0.75,
        "time_coverage": 0.2,
        "event_total": 4,
        "matched_event_total": 3,
        "distinct_intervals": 2,
        "evidence_status": "evidence_linked",
        "source_references": ["/cache/events.sqlite3"],
        "score": 0.4567890123456789,
        "score_version": "candidate-path/v1",
    }
    payload = _network_only_payload(
        paths=[
            {
                "rank": 1,
                "species": ["[H]", "[H][O]"],
                "steps": [step],
                "score": 0.567890123456789,
                "evidence_status": "evidence_linked",
                "score_version": "candidate-path/v1",
            }
        ]
    )
    payload["reason"] = "ok"
    payload["evidence_status"] = "evidence_linked"
    payload["expansions"] = 1
    return payload


def test_pathway_parser_supports_documented_options(tmp_path: Path) -> None:
    reaction = _write_reaction_file(tmp_path)

    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "[H]",
            "--direction",
            "upstream",
            "--max-depth",
            "4",
            "--max-branches",
            "6",
            "--max-paths",
            "8",
            "--max-expansions",
            "90",
            "--min-net-tp",
            "2",
            "--min-directionality",
            "0.25",
            "--out-json",
            str(tmp_path / "paths.json"),
            "--out-csv",
            str(tmp_path / "paths.csv"),
        ]
    )

    assert args.func is cli.cmd_pathway
    assert args.direction == "upstream"
    assert args.max_depth == 4
    assert args.max_branches == 6
    assert args.max_paths == 8
    assert args.max_expansions == 90
    assert args.min_net_tp == 2
    assert args.min_directionality == 0.25


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--max-depth", "0"),
        ("--max-branches", "0"),
        ("--max-paths", "0"),
        ("--max-expansions", "0"),
        ("--min-net-tp", "0"),
        ("--min-directionality", "-0.01"),
        ("--min-directionality", "1.01"),
        ("--max-depth", "1.5"),
    ],
)
def test_pathway_parser_rejects_invalid_bounds(
    tmp_path: Path,
    option: str,
    value: str,
) -> None:
    reaction = _write_reaction_file(tmp_path)

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "pathway",
                "--reac",
                str(reaction),
                "--start-smiles",
                "[H]",
                option,
                value,
            ]
        )


def test_pathway_command_exports_schema_and_one_csv_row_per_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = _write_reaction_file(tmp_path)
    out_json = tmp_path / "exports" / "paths.json"
    out_csv = tmp_path / "exports" / "paths.csv"
    payload = _one_path_payload()
    second_step = dict(payload["paths"][0]["steps"][0])
    second_step.update(
        reaction_key="[H][O]->[H]+[O]",
        focal_input="[H][O]",
        focal_output="[O]",
        reactants=["[H][O]"],
        products=["[H]", "[O]"],
    )
    payload["paths"][0]["steps"].append(second_step)
    payload["paths"][0]["species"].append("[O]")
    monkeypatch.setattr(cli, "find_pathways_service", lambda *_args, **_kwargs: payload)
    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "[H]",
            "--out-json",
            str(out_json),
            "--out-csv",
            str(out_csv),
        ]
    )

    assert cli.cmd_pathway(args) == 0

    document = json.loads(out_json.read_text(encoding="utf-8"))
    assert document["schema_version"] == "reacnet-scope/pathways/v1"
    assert document["paths"][0]["steps"][0]["reactants"] == ["[H]", "[O]", "[O]"]
    with out_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert [row["step_index"] for row in rows] == ["1", "2"]
    row = rows[0]
    assert row["path_rank"] == "1"
    assert row["step_index"] == "1"
    assert json.loads(row["reactants"]) == ["[H]", "[O]", "[O]"]
    assert json.loads(row["products"]) == ["[H][O]", "[O]"]
    assert float(row["net_share"]) == 0.1234567890123456
    assert float(row["directionality"]) == 0.6
    assert float(row["event_coverage"]) == 0.75
    assert float(row["time_coverage"]) == 0.2
    assert float(row["path_score"]) == 0.567890123456789
    assert float(row["step_score"]) == 0.4567890123456789
    assert row["evidence_status"] == "evidence_linked"
    assert row["score_version"] == "candidate-path/v1"


def test_pathway_command_infers_event_sources_and_preserves_prepare_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "data set"
    folder.mkdir()
    reaction = _write_reaction_file(folder, name="sample trajectory.reactionabcd")
    captured: dict[str, Any] = {}
    reported_command = (
        "reacnet-scope-prepare 'service quoted directory' --event-only"
    )

    def fake_find(artifacts: dict[str, str], start_smiles: str, **limits: Any) -> dict[str, Any]:
        captured.update(artifacts=artifacts, start_smiles=start_smiles, limits=limits)
        payload = _network_only_payload()
        payload["preparation_command"] = reported_command
        return payload

    monkeypatch.setattr(cli, "find_pathways_service", fake_find)
    args = cli.build_parser().parse_args(
        ["pathway", "--reac", str(reaction), "--start-smiles", "[H]"]
    )

    assert cli.cmd_pathway(args) == 0

    base = str(reaction)[: -len(".reactionabcd")]
    assert captured["artifacts"] == {
        "reaction": str(reaction),
        "reactionevent": f"{base}.reactionevent.csv",
        "molecules": f"{base}.molecules.csv",
    }
    assert capsys.readouterr().err.strip() == reported_command


@pytest.mark.parametrize("index_state", ["stale", "invalid"])
def test_pathway_command_preserves_service_rebuild_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    index_state: str,
) -> None:
    folder = tmp_path / "data set"
    folder.mkdir()
    reaction = _write_reaction_file(folder)
    expected = f"reacnet-scope-prepare '{folder}' --rebuild event"
    payload = _network_only_payload()
    payload["event_index_state"] = index_state
    payload["preparation_command"] = expected
    monkeypatch.setattr(
        cli,
        "find_pathways_service",
        lambda *_args, **_kwargs: payload,
    )
    args = cli.build_parser().parse_args(
        ["pathway", "--reac", str(reaction), "--start-smiles", "[H]"]
    )

    assert cli.cmd_pathway(args) == 0

    assert capsys.readouterr().err.strip() == expected


def test_pathway_main_maps_missing_reaction_to_stable_usage_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.reactionabcd"

    exit_code = cli.main(
        [
            "pathway",
            "--reac",
            str(missing),
            "--start-smiles",
            "[H]",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "需要 .reactionabcd 文件" in captured.err
    assert "Traceback" not in captured.err


def test_pathway_command_does_not_swallow_unexpected_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = _write_reaction_file(tmp_path)

    def fail_unexpectedly(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(cli, "find_pathways_service", fail_unexpectedly)
    args = cli.build_parser().parse_args(
        ["pathway", "--reac", str(reaction), "--start-smiles", "[H]"]
    )

    with pytest.raises(RuntimeError, match="unexpected failure"):
        cli.cmd_pathway(args)


def test_pathway_command_without_exports_prints_unrounded_ranked_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reaction = _write_reaction_file(tmp_path)
    monkeypatch.setattr(
        cli,
        "find_pathways_service",
        lambda *_args, **_kwargs: _one_path_payload(),
    )
    args = cli.build_parser().parse_args(
        ["pathway", "--reac", str(reaction), "--start-smiles", "[H]"]
    )

    assert cli.cmd_pathway(args) == 0

    stdout = capsys.readouterr().out
    assert "rank,score,steps,evidence_status,species" in stdout
    assert "1,0.567890123456789,1,evidence_linked,[H] -> [H][O]" in stdout


def test_pathway_command_never_builds_or_reads_event_source_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = _write_reaction_file(tmp_path)
    reactionevent = tmp_path / "run.reactionevent.csv"
    molecules = tmp_path / "run.molecules.csv"
    reactionevent.write_text("not,read\n", encoding="utf-8")
    molecules.write_text("not,read\n", encoding="utf-8")
    protected = {os.path.abspath(reactionevent), os.path.abspath(molecules)}
    real_open = builtins.open

    def guarded_open(file: Any, *args: Any, **kwargs: Any):
        if os.path.abspath(os.fspath(file)) in protected:
            raise AssertionError("pathway CLI must not scan event source CSV")
        return real_open(file, *args, **kwargs)

    def forbidden_build(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pathway CLI must not build an event index")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, "build", forbidden_build)
    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "[H]",
            "--max-depth",
            "1",
            "--out-json",
            str(tmp_path / "paths.json"),
        ]
    )

    assert cli.cmd_pathway(args) == 0
    payload = json.loads((tmp_path / "paths.json").read_text(encoding="utf-8"))
    assert payload["evidence_status"] == "network_only"


def test_pathway_json_export_uses_sibling_temp_and_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = _write_reaction_file(tmp_path)
    target = tmp_path / "paths.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        calls.append((os.fspath(source), os.fspath(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(cli, "find_pathways_service", lambda *_args, **_kwargs: _network_only_payload())
    monkeypatch.setattr(cli.os, "replace", recording_replace)
    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "[H]",
            "--out-json",
            str(target),
        ]
    )

    assert cli.cmd_pathway(args) == 0
    assert calls == [(f"{target}.tmp", str(target))]
    assert not Path(f"{target}.tmp").exists()
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == (
        "reacnet-scope/pathways/v1"
    )


def test_pathway_json_replace_failure_preserves_target_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = _write_reaction_file(tmp_path)
    target = tmp_path / "paths.json"
    original = '{"old": true}\n'
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "find_pathways_service", lambda *_args, **_kwargs: _network_only_payload())

    def failed_replace(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(cli.os, "replace", failed_replace)
    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "[H]",
            "--out-json",
            str(target),
        ]
    )

    with pytest.raises(OSError, match="replace failed"):
        cli.cmd_pathway(args)

    assert target.read_text(encoding="utf-8") == original
    assert not Path(f"{target}.tmp").exists()


def test_pathway_empty_result_writes_valid_empty_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reaction = _write_reaction_file(tmp_path)
    out_json = tmp_path / "empty.json"
    out_csv = tmp_path / "empty.csv"
    monkeypatch.setattr(cli, "find_pathways_service", lambda *_args, **_kwargs: _network_only_payload())
    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "absent",
            "--out-json",
            str(out_json),
            "--out-csv",
            str(out_csv),
        ]
    )

    assert cli.cmd_pathway(args) == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["paths"] == []
    with out_csv.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == []
    assert "species_absent" in capsys.readouterr().out


def test_pathway_cli_ready_index_empty_result_keeps_linked_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = _write_reaction_file(tmp_path)
    reactionevent = tmp_path / "run.reactionevent.csv"
    molecules = tmp_path / "run.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[H]+[O],[H][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[H],0,\n"
        "0,[O],1,\n"
        "10,[H][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    out_json = tmp_path / "empty-linked.json"
    args = cli.build_parser().parse_args(
        [
            "pathway",
            "--reac",
            str(reaction),
            "--start-smiles",
            "absent",
            "--out-json",
            str(out_json),
        ]
    )

    assert cli.cmd_pathway(args) == 0

    captured = capsys.readouterr()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["paths"] == []
    assert payload["reason"] == "species_absent"
    assert payload["evidence_status"] == "evidence_linked"
    assert set(payload["source_signatures"]) >= {
        "reactionabcd",
        "reactionevent",
        "molecules",
        "event_index",
    }
    assert captured.err == ""
