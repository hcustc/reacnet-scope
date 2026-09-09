from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.trajectory import (
    dataset_settings_path,
    load_coordinate_length_unit,
)
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


def _prepared_dataset(
    tmp_path: Path,
    monkeypatch,
    *,
    configured_workspace: bool = True,
) -> tuple[Path, str]:
    if configured_workspace:
        monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    else:
        monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
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


def test_export_event_cli_reads_local_sidecar_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory, event_id = _prepared_dataset(
        tmp_path,
        monkeypatch,
        configured_workspace=False,
    )
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
    assert tmp_path / ".reacnet-scope" in dataset_settings_path(
        str(trajectory)
    ).parents


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


def test_export_dft_geometry_cli_writes_selected_initial_geometries(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)
    target = tmp_path / "dft-geometry.zip"

    result = cli.main(
        [
            "export-dft-geometry",
            "--case",
            str(tmp_path),
            "--event-id",
            event_id,
            "--layout",
            "both",
            "--type-map",
            "1=C,2=O",
            "--source-unit",
            "angstrom",
            "--state",
            "reactants=0,1",
            "--state",
            "products=0,1",
            "--confirm-isolated-cluster",
            "--replicate",
            "replicate-01",
            "--out",
            str(target),
        ]
    )

    assert result == 0
    with ZipFile(target) as archive:
        assert "reactants.xyz" in archive.namelist()
        assert "products.xyz" in archive.namelist()
        assert "reactant-01-atoms-1-1.xyz" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["event"]["event_id"] == event_id
        assert manifest["cross_side_atom_ids_match"] is True
        assert manifest["source_signatures"]["trajectory_index"]["size"] > 0
        assert manifest["source_signatures"]["event_index"]["size"] > 0
        assert manifest["qc_handoff"]["status"] == "ready"
        readiness = json.loads(archive.read("reaction_readiness.json"))
        assert readiness["qc_handoff"]["status"] == "ready"
        assert readiness["kinetics_applicability"]["status"] == "insufficient_evidence"
        assert readiness["subject"]["replicate"] == "replicate-01"
        assert readiness["subject"]["atom_ids"] == {
            "product": [1, 2],
            "reactant": [1, 2],
        }
        occurrence = json.loads(archive.read("occurrence.json"))
        assert occurrence["event_id"] == event_id
    assert "DFT initial geometry package" in capsys.readouterr().out


def test_export_dft_geometry_cli_requires_unit_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)

    result = cli.main(
        [
            "export-dft-geometry",
            "--case",
            str(tmp_path),
            "--event-id",
            event_id,
            "--type-map",
            "1=C,2=O",
            "--out",
            str(tmp_path / "dft-geometry.zip"),
        ]
    )

    assert result == 2
    assert "坐标单位为 Å" in capsys.readouterr().err


def test_export_dft_geometry_cli_saves_unit_only_after_geometry_succeeds(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)

    failed = cli.main(
        [
            "export-dft-geometry",
            "--case",
            str(tmp_path),
            "--event-id",
            event_id,
            "--type-map",
            "1=C",
            "--save-unit-confirmation",
            "--confirm-isolated-cluster",
            "--state",
            "reactants=0,1",
            "--state",
            "products=0,1",
            "--out",
            str(tmp_path / "failed.zip"),
        ]
    )

    assert failed == 2
    assert load_coordinate_length_unit(str(trajectory)) is None
    assert not (tmp_path / "failed.zip").exists()
    capsys.readouterr()

    succeeded = cli.main(
        [
            "export-dft-geometry",
            "--case",
            str(tmp_path),
            "--event-id",
            event_id,
            "--type-map",
            "1=C,2=O",
            "--save-unit-confirmation",
            "--confirm-isolated-cluster",
            "--state",
            "reactants=0,1",
            "--state",
            "products=0,1",
            "--out",
            str(tmp_path / "succeeded.zip"),
        ]
    )

    assert succeeded == 0
    assert load_coordinate_length_unit(str(trajectory)) == "angstrom"


def test_export_dft_geometry_cli_requires_acknowledgement_for_review(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)
    target = tmp_path / "review.zip"
    args = [
        "export-dft-geometry",
        "--case",
        str(tmp_path),
        "--event-id",
        event_id,
        "--type-map",
        "1=C,2=O",
        "--source-unit",
        "angstrom",
        "--state",
        "reactants=0,1",
        "--state",
        "products=0,1",
        "--confirm-isolated-cluster",
        "--warning-atoms",
        "1",
        "--out",
        str(target),
    ]

    assert cli.main(args) == 2
    assert not target.exists()
    captured = capsys.readouterr()
    assert "qc_handoff.status=review_required" in captured.out
    assert "--acknowledge-review" in captured.err

    assert cli.main([*args, "--acknowledge-review"]) == 0
    with ZipFile(target) as archive:
        report = json.loads(archive.read("reaction_readiness.json"))
    assert report["qc_handoff"]["status"] == "review_required"


@pytest.mark.parametrize(
    "state_args",
    [
        ["--state", "reactant=0,1"],
        ["--state", "reactants=0,1", "--state", "reactants=0,3"],
    ],
)
def test_export_dft_geometry_cli_rejects_unknown_and_duplicate_states(
    tmp_path: Path,
    monkeypatch,
    capsys,
    state_args: list[str],
) -> None:
    _trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)
    target = tmp_path / "invalid-state.zip"

    result = cli.main(
        [
            "export-dft-geometry",
            "--case",
            str(tmp_path),
            "--event-id",
            event_id,
            "--type-map",
            "1=C,2=O",
            "--source-unit",
            "angstrom",
            *state_args,
            "--out",
            str(target),
        ]
    )

    assert result == 2
    assert not target.exists()
    error = capsys.readouterr().err
    assert "电子态" in error or "--state" in error
