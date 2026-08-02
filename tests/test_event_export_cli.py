from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.trajectory import dataset_settings_path
from scripts import rng_query_cli as cli


def _frame(timestep: int) -> str:
    return (
        "ITEM: TIMESTEP\n"
        f"{timestep}\n"
        "ITEM: NUMBER OF ATOMS\n2\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n"
        "1 1 1.0 1.0 1.0\n"
        "2 2 2.0 1.0 1.0\n"
    )


def _prepared_dataset(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "run.lammpstrj"
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n"
        "0,[O],1,\n"
        "10,[C][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    reaction.write_text("1 [C]+[O]->[C][O]\n", encoding="utf-8")
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    event_id = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent),
        str(molecules),
        "[C]+[O]->[C][O]",
        limit=1,
    )["rows"][0]["event_id"]
    return trajectory, event_id


def test_export_event_cli_writes_package_without_persisting_type_override(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)
    target = tmp_path / "event.zip"

    result = cli.main(
        [
            "export-event",
            "--case",
            str(tmp_path),
            "--event-id",
            event_id,
            "--scope",
            "participants",
            "--before-frames",
            "0",
            "--after-frames",
            "0",
            "--type-map",
            "1=C,2=O",
            "--out",
            str(target),
        ]
    )

    assert result == 0
    assert target.is_file()
    assert not dataset_settings_path(str(trajectory)).exists()
    with ZipFile(target) as archive:
        assert "trajectory.extxyz" in archive.namelist()
        document = json.loads(archive.read("event.json"))
        assert document["event"]["event_id"] == event_id
        assert document["type_element_map"] == {"1": "C", "2": "O"}
        assert document["source_signatures"]["trajectory"]["size"] > 0
        assert archive.read("trajectory.extxyz")
    assert "wrote event package" in capsys.readouterr().out


def test_export_event_cli_rejects_unknown_event_and_existing_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)
    target = tmp_path / "event.zip"
    target.write_bytes(b"original")
    base_args = [
        "export-event",
        "--case",
        str(tmp_path),
        "--type-map",
        "1=C,2=O",
        "--out",
        str(target),
    ]

    assert cli.main([*base_args, "--event-id", "unknown"]) == 2
    assert cli.main([*base_args, "--event-id", event_id]) == 2
    assert target.read_bytes() == b"original"

    assert cli.main([*base_args, "--event-id", event_id, "--force"]) == 0
    with ZipFile(io.BytesIO(target.read_bytes())) as archive:
        assert archive.namelist()[0] == "event.json"
    captured = capsys.readouterr()
    assert "does not contain event unknown" in captured.err
    assert "output already exists" in captured.err
