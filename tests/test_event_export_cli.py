from __future__ import annotations

import io
import json
import sqlite3
from contextlib import closing
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
from tests.test_timed_evidence import write_timeline


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
    extended_product: bool = False,
    unreported_states: str = "",
    last_timestep: int = 60,
) -> tuple[Path, str]:
    if configured_workspace:
        monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    else:
        monkeypatch.delenv("REACNET_SCOPE_CACHE_DIR", raising=False)
    trajectory = tmp_path / "run.lammpstrj"
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    timesteps = range(0, last_timestep + 1, 10) if extended_product or unreported_states else (0, 10)
    trajectory.write_text("".join(_frame(step) for step in timesteps), encoding="utf-8")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n"
        + ("1,[C][O],[C]=[O]\n" if extended_product else ""),
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n"
        "0,[O],1,\n"
        "10,[C][O],0;1,0-1-1\n"
        + ("".join(f"{step},[C]=[O],0;1,0-1-2\n" for step in range(20, 70, 10)) if extended_product else "")
        + unreported_states,
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


def test_export_event_plateau_preserves_endpoints_and_extends_bond_order_variants(
    tmp_path: Path, monkeypatch
) -> None:
    _, event_id = _prepared_dataset(tmp_path, monkeypatch, extended_product=True)
    target = tmp_path / "plateau.zip"
    assert cli.main([
        "export-event", "--case", str(tmp_path), "--event-id", event_id,
        "--window-mode", "plateau", "--qc-manifest", "--type-map", "1=C,2=O",
        "--out", str(target),
    ]) == 0
    with ZipFile(target) as archive:
        event = json.loads(archive.read("event.json"))
        manifest = json.loads(archive.read("qc_manifest.json"))
    assert [frame["source_timestep"] for frame in event["frames"]] == list(range(0, 70, 10))
    assert [frame["bond_state"] for frame in event["frames"]] == ["before", "after", *["unassigned"] * 5]
    selection = event["selection"]["extraction_parameters"]
    assert selection["window_mode"] == "plateau"
    assert selection["plateau_window"]["reactant_carrier_atom_ids"] == [[1], [2]]
    assert selection["padding_truncated"] is False
    assert manifest["participant_coverage"]["status"] == "complete"
    assert manifest["total_electrons"] == 14


@pytest.mark.parametrize("interruption", [
    "40,[C],0,\n40,[O],1,\n",                      # carrier splits
    "40,[C][O],0;1,0-1-1\n40,[O],1,\n",            # partial atom conflict
    "40,[C][O],0;1,0-1-1\n40,[C]=[O],0;1,0-1-2\n", # overlapping bond-order assignments
])
def test_plateau_uses_unreported_states_and_stops_before_interruption(tmp_path, monkeypatch, interruption):
    # Only the 0 -> 10 reaction has an event port. The later exact states have
    # occupancy, and the original state returns after the interruption.
    tail = ("20,[C][O],0;1,0-1-2\n30,[C][O],0;1,0-1-1\n" + interruption
            + "50,[C][O],0;1,0-1-1\n60,[C][O],0;1,0-1-1\n")
    _, event_id = _prepared_dataset(tmp_path, monkeypatch, unreported_states=tail)
    out = tmp_path / "plateau.zip"
    assert cli.main(["export-event", "--case", str(tmp_path), "--event-id", event_id,
                     "--window-mode", "plateau", "--out", str(out)]) == 0
    with ZipFile(out) as archive:
        doc = json.loads(archive.read("event.json"))
    assert [f["source_timestep"] for f in doc["frames"]] == [0, 10, 20, 30]
    assert [f["bond_state"] for f in doc["frames"]] == ["before", "after", "unassigned", "unassigned"]


@pytest.mark.parametrize("boundary", ["source", "missing_frame"])
def test_plateau_stops_at_analyzed_frame_boundary(tmp_path, monkeypatch, boundary):
    from reacnet_scope.indexes import event_evidence_index_path

    trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch, extended_product=True)
    index = event_evidence_index_path(str(trajectory) + ".reactionevent.csv")
    with closing(sqlite3.connect(index)) as con, con:
        if boundary == "source":
            con.execute("UPDATE lineage_frames SET source_id=2 WHERE frame>=4")
        else:
            con.execute("DELETE FROM lineage_frames WHERE frame=4")
    out = tmp_path / "bounded.zip"
    assert cli.main(["export-event", "--case", str(tmp_path), "--event-id", event_id,
                     "--window-mode", "plateau", "--out", str(out)]) == 0
    with ZipFile(out) as archive:
        frames = json.loads(archive.read("event.json"))["frames"]
    assert [f["source_timestep"] for f in frames] == [0, 10, 20, 30]


def test_plateau_crosses_occupancy_blocks_and_retains_padding_limit(tmp_path, monkeypatch):
    from reacnet_scope import lineage_segments

    monkeypatch.setattr(lineage_segments, "FRAME_BLOCK", 8)
    tail = "".join(f"{step},[C][O],0;1,0-1-{1 + (step // 10) % 2}\n"
                   for step in range(20, 1040, 10))
    _, event_id = _prepared_dataset(tmp_path, monkeypatch, unreported_states=tail, last_timestep=1030)
    out = tmp_path / "capped.zip"
    assert cli.main(["export-event", "--case", str(tmp_path), "--event-id", event_id,
                     "--window-mode", "plateau", "--out", str(out)]) == 0
    with ZipFile(out) as archive:
        doc = json.loads(archive.read("event.json"))
    assert [f["source_timestep"] for f in doc["frames"]] == list(range(0, 1020, 10))
    assert doc["selection"]["extraction_parameters"]["padding_truncated"]


def test_existing_lineage_lookup_preparation_is_atomic_and_retryable(tmp_path, monkeypatch):
    from reacnet_scope import lineage_segments
    from reacnet_scope.indexes import event_evidence_index_path

    trajectory, event_id = _prepared_dataset(tmp_path, monkeypatch)
    source, molecules = str(trajectory) + ".reactionevent.csv", str(trajectory) + ".molecules.csv"
    index = event_evidence_index_path(source)
    args = ["export-event", "--case", str(tmp_path), "--event-id", event_id,
            "--window-mode", "plateau", "--out", str(tmp_path / "export.zip")]
    with closing(sqlite3.connect(index)) as con, con:
        con.execute("DROP TABLE lineage_atom_states")
        con.execute("DROP TABLE lineage_state_ids")
    assert cli.main(args) != 0

    build = lineage_segments.build_state_atom_lookup
    def interrupted(con):
        build(con)
        raise RuntimeError("interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(lineage_segments, "build_state_atom_lookup", interrupted)
        with pytest.raises(RuntimeError, match="interrupted"):
            EVENT_EVIDENCE_STORE.build(source, molecules)
    with closing(sqlite3.connect(index)) as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='lineage_atom_states'").fetchone()
    EVENT_EVIDENCE_STORE.build(source, molecules)
    EVENT_EVIDENCE_STORE.build(source, molecules)
    assert cli.main(args) == 0


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


def test_export_dft_geometry_cli_selects_native_timed_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory, _legacy_event_id = _prepared_dataset(tmp_path, monkeypatch)
    timeline = write_timeline(
        Path(f"{trajectory}.timeline.h5"), schema_version="2"
    )
    EVENT_EVIDENCE_STORE.build(str(timeline))
    event_id = EVENT_EVIDENCE_STORE.query_events(
        str(timeline), "", "[C]+[O]->[C][O]", limit=1
    )["rows"][0]["event_id"]
    target = tmp_path / "native-source.zip"
    result = cli.main(
        [
            "export-dft-geometry",
            "--case", str(tmp_path),
            "--event-id", event_id,
            "--type-map", "1=C,2=O",
            "--source-unit", "angstrom",
            "--state", "reactants=0,1",
            "--state", "products=0,1",
            "--confirm-isolated-cluster",
            "--out", str(target),
        ]
    )

    assert result == 0
    with ZipFile(target) as archive:
        occurrence = json.loads(archive.read("occurrence.json"))
        assert occurrence["event_id"] == event_id
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["source_signatures"]["timeline"]["path"] == str(timeline)


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
