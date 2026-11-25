"""Phase 1 golden benchmark regression tests derived from 2CP/O2 MD-to-QC workflows."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest

from reacnet_scope.event_package import build_event_package
from reacnet_scope.path_search import (
    CandidateReader,
    discover_indexed_candidates,
    materialize_candidate_adjacency,
)
from reacnet_scope.provenance import collect_runtime_provenance, parse_lammps_input_units
from reacnet_scope.skeleton_lineage import (
    SKELETON_LINEAGE_SCHEMA,
    build_skeleton_timeline,
    connectivity_signature,
    cyclomatic_ring_count,
)
from scripts.rng_query_cli import build_parser, _parse_atom_id_spec


def _make_candidate_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "events.sqlite3"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY, total_events INTEGER);
        CREATE TABLE events(
            event_id TEXT PRIMARY KEY,
            reaction_key TEXT,
            association_status TEXT,
            before_timestep INTEGER,
            after_timestep INTEGER,
            reactant_participants_json TEXT,
            product_participants_json TEXT
        );
        """
    )
    # Net reaction A->B where a spectator/exchange 2CP species appears on both
    # sides of the raw atom-connected component and was cancelled out of
    # reaction_key by net_reaction_key().
    con.execute(
        "INSERT INTO reaction_summary VALUES(?, ?)",
        ("A->B", 1),
    )
    con.execute(
        "INSERT INTO reaction_summary VALUES(?, ?)",
        ("2CP+O2->C6H4ClO+HO2", 2),
    )
    con.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
        (
            "evt_exchange",
            "A->B",
            "matched",
            100,
            110,
            json.dumps(
                [
                    {"species": "2CP", "atom_ids": [1, 2, 3, 4, 5, 6]},
                    {"species": "A", "atom_ids": [10, 11]},
                ]
            ),
            json.dumps(
                [
                    {"species": "2CP", "atom_ids": [1, 2, 3, 4, 5, 11]},
                    {"species": "B", "atom_ids": [6, 10]},
                ]
            ),
        ),
    )
    con.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,?,?)",
        (
            "evt_valid",
            "2CP+O2->C6H4ClO+HO2",
            "matched",
            200,
            210,
            json.dumps(
                [
                    {"species": "2CP", "atom_ids": [1, 2, 3, 4, 5, 6]},
                    {"species": "O2", "atom_ids": [20, 21]},
                ]
            ),
            json.dumps(
                [
                    {"species": "C6H4ClO", "atom_ids": [1, 2, 3, 4, 5]},
                    {"species": "HO2", "atom_ids": [6, 20, 21]},
                ]
            ),
        ),
    )
    materialize_candidate_adjacency(con)
    con.commit()
    con.close()
    return db_path


def test_carried_species_net_cancellation_does_not_crash_new_or_legacy_index(
    tmp_path: Path,
) -> None:
    db_path = _make_candidate_db(tmp_path)
    opened = {"index_path": str(db_path)}

    # 1. Newly materialized index must exclude off-side spectator transfer (2CP, B, A->B).
    con = sqlite3.connect(db_path)
    off_side = con.execute(
        "SELECT COUNT(*) FROM candidate_transfers WHERE source_species='2CP' AND reaction_key='A->B'"
    ).fetchone()[0]
    assert off_side == 0

    reader = CandidateReader(opened)
    try:
        report = discover_indexed_candidates(
            reader, start="2CP", mode="explore", max_steps=1, max_paths=50
        )
    finally:
        reader.close()
    assert report["status"] == "found"
    assert [p["species"] for p in report["paths"]] == [["2CP", "C6H4ClO"]]

    # 2. Even if a legacy pre-fix SQLite index already contains an off-side row,
    #    online query must filter it cleanly instead of raising ValueError.
    con.execute(
        "INSERT INTO candidate_transfers VALUES('2CP', 'B', 'A->B', 1, 1)"
    )
    con.commit()
    con.close()

    legacy_reader = CandidateReader(opened)
    try:
        legacy_report = discover_indexed_candidates(
            legacy_reader, start="2CP", mode="explore", max_steps=1, max_paths=50
        )
    finally:
        legacy_reader.close()
    assert legacy_report["status"] == "found"
    assert [p["species"] for p in legacy_report["paths"]] == [["2CP", "C6H4ClO"]]


def test_parse_lammps_input_units_metal_real_and_unresolved(tmp_path: Path) -> None:
    metal_in = tmp_path / "input.lammps"
    metal_in.write_text(
        "# 2CP production run\n"
        "units metal\n"
        "timestep 0.0001 # ps\n"
        "dump trajectory all custom 100 trajectory.lammpstrj id type x y z\n",
        encoding="utf-8",
    )
    parsed = parse_lammps_input_units(metal_in)
    assert parsed["status"] == "confirmed_from_lammps_input"
    assert parsed["units_style"] == "metal"
    assert parsed["timestep_ps"] == pytest.approx(0.0001)
    assert parsed["coordinate_length_unit"] == "angstrom"
    assert len(parsed["source_sha256"]) == 64

    real_in = tmp_path / "real.lammps"
    real_in.write_text("units real\ntimestep 0.25\n", encoding="utf-8")
    real_parsed = parse_lammps_input_units(real_in)
    assert real_parsed["timestep_ps"] == pytest.approx(0.00025)
    assert real_parsed["coordinate_length_unit"] == "angstrom"

    var_in = tmp_path / "var.lammps"
    var_in.write_text("units metal\ntimestep ${DT}\n", encoding="utf-8")
    var_parsed = parse_lammps_input_units(var_in)
    assert var_parsed["status"] == "partial_from_lammps_input"
    assert var_parsed["timestep_ps"] is None
    assert var_parsed["coordinate_length_unit"] == "angstrom"


def test_event_package_records_deterministic_provenance() -> None:
    viewer = {
        "event_id": "rngevt_c5_co",
        "event": {
            "event_id": "rngevt_c5_co",
            "reaction_smiles": "A->B+CO",
            "before_timestep": 7254400,
            "after_timestep": 7254500,
            "association_status": "matched",
        },
        "frames": [
            {
                "frame": 7254400,
                "box": [(0, 20), (0, 20), (0, 20)],
                "cell": [[20, 0, 0], [0, 20, 0], [0, 0, 20]],
                "pbc": [True, True, True],
                "bond_state": "before",
                "atoms": [
                    {
                        "id": 300,
                        "type": "1",
                        "element": "C",
                        "x": 1.0,
                        "y": 1.0,
                        "z": 1.0,
                        "display_x": 0.0,
                        "display_y": 0.0,
                        "display_z": 0.0,
                        "group": "core",
                    }
                ],
            }
        ],
        "atom_groups": {"core": [300], "participants": [300], "environment": []},
        "bond_evidence": {"reactant": [], "product": [], "broken": [], "formed": []},
        "meta": {
            "timestep_ps": 0.0001,
            "coordinate_length_unit": "angstrom",
            "type_element_map": {"1": "C"},
        },
    }
    prov = collect_runtime_provenance(extra={"source_kind": "timeline_h5"})
    zip_a = build_event_package(viewer, scope="participants", provenance=prov)
    zip_b = build_event_package(viewer, scope="participants", provenance=prov)
    assert zip_a == zip_b

    with ZipFile(io.BytesIO(zip_a)) as archive:
        doc = json.loads(archive.read("event.json"))
    assert doc["provenance"]["software"] == "reacnet-scope"
    assert doc["provenance"]["source_kind"] == "timeline_h5"
    assert doc["time_axis"]["unit"] == "ps"
    assert doc["coordinate_length_unit"] == "angstrom"


def test_bond_flips_follow_adjacent_orders_even_with_one_representative_name():
    samples = [
        ("A", ["1-2-1", "2-3-1"]),
        ("A", ["1-2-2", "2-3-2"]),  # simultaneous pair changes form one transition
        ("B", ["3-2-2", "2-1-2"]),  # label, endpoint orientation and row order only
        ("A", ["1-2-1", "2-3-1"]),
    ]
    segments = [dict(segment_id=str(i), start_frame=i, end_frame=i, species=name,
                     bonds=bonds, atom_ids=[1, 2, 3], source_id=1)
                for i, (name, bonds) in enumerate(samples)]
    def count(window):
        report = build_skeleton_timeline(window, anchor_atom_ids=[1, 2, 3], oscillation_max_frames=0)
        assert report["plateau_count"] == 1
        return report["total_bond_order_flips_merged"]

    assert count(segments[:2]) == 1
    assert count(segments[1:3]) == 0
    assert count(segments) == 2


def test_golden_case1_c5_co_merges_high_temp_carbonyl_bond_order_flips() -> None:
    """Case 1 (C5_CO): 2500 K C=O stretching flips single/double bond labels across 1-frame segments."""
    ring_atoms = [301, 302, 303, 304, 305, 310, 311]
    base_ring_bonds = [
        "301-302-1",
        "302-303-2",
        "303-304-1",
        "304-305-1",
        "305-301-2",
        "302-310-1",
    ]
    segments = [
        {
            "segment_id": "seg_1",
            "species": "O=C1C(Cl)=CC=C1",
            "atom_ids": ring_atoms,
            "bonds": [*base_ring_bonds, "304-311-2"],
            "start_frame": 72545,
            "end_frame": 72545,
            "start_timestep": 7254500,
            "end_timestep": 7254500,
        },
        {
            "segment_id": "seg_2",
            "species": "[O]C1C(Cl)=CC=C1",
            "atom_ids": ring_atoms,
            "bonds": [*base_ring_bonds, "304-311-1"],
            "start_frame": 72546,
            "end_frame": 72546,
            "start_timestep": 7254600,
            "end_timestep": 7254600,
        },
        {
            "segment_id": "seg_3",
            "species": "O=C1C(Cl)=CC=C1",
            "atom_ids": ring_atoms,
            "bonds": [*base_ring_bonds, "304-311-2"],
            "start_frame": 72547,
            "end_frame": 72550,
            "start_timestep": 7254700,
            "end_timestep": 7255000,
        },
    ]

    strict = build_skeleton_timeline(
        segments, anchor_atom_ids=[301, 302, 303, 304, 305], mode="strict"
    )
    assert strict["plateau_count"] == 3

    skeleton = build_skeleton_timeline(
        segments,
        anchor_atom_ids=[301, 302, 303, 304, 305],
        mode="skeleton",
        oscillation_max_frames=0,
        timestep_ps=0.0001,
    )
    assert skeleton["schema_version"] == SKELETON_LINEAGE_SCHEMA
    assert skeleton["plateau_count"] == 1
    assert skeleton["total_bond_order_flips_merged"] == 2
    plateau = skeleton["timeline"][0]
    assert plateau["start_frame"] == 72545
    assert plateau["end_frame"] == 72550
    assert plateau["frame_count"] == 6
    assert plateau["duration_ps"] == pytest.approx(0.05)
    assert plateau["ring_count"] == 1
    assert plateau["representative_species"] == "O=C1C(Cl)=CC=C1"
    assert plateau["raw_species_variants"] == ["O=C1C(Cl)=CC=C1", "[O]C1C(Cl)=CC=C1"]
    assert plateau["continuity_basis"] == "skeleton_connectivity_bond_order_merged"


def test_golden_case3_growth_c6_folds_ether_bridge_oscillation() -> None:
    """Case 3 (growth_C6): C967-O974 ether bridge crosses cutoff for 1 frame and snaps back."""
    dimer_atoms = [1, 2, 3, 4]
    ether_bonds = ["1-2-1", "2-3-1", "3-4-1"]
    split_bonds = ["1-2-1", "3-4-1"]  # C967-O974 temporarily stretched past cutoff
    segments = [
        {
            "segment_id": "ether_pre",
            "species": "C12H8Cl2O2",
            "atom_ids": dimer_atoms,
            "bonds": ether_bonds,
            "start_frame": 57950,
            "end_frame": 57961,
            "start_timestep": 5795000,
            "end_timestep": 5796100,
        },
        {
            "segment_id": "frag_transient",
            "species": "C6H4ClO",
            "atom_ids": [1, 2],
            "bonds": ["1-2-1"],
            "start_frame": 57962,
            "end_frame": 57962,
            "start_timestep": 5796200,
            "end_timestep": 5796200,
        },
        {
            "segment_id": "ether_post",
            "species": "C12H8Cl2O2",
            "atom_ids": dimer_atoms,
            "bonds": ether_bonds,
            "start_frame": 57963,
            "end_frame": 57980,
            "start_timestep": 5796300,
            "end_timestep": 5798000,
        },
    ]

    unfolded = build_skeleton_timeline(
        segments, anchor_atom_ids=[1, 2], mode="skeleton", oscillation_max_frames=0
    )
    assert unfolded["plateau_count"] == 3

    folded = build_skeleton_timeline(
        segments, anchor_atom_ids=[1, 2], mode="skeleton", oscillation_max_frames=2
    )
    assert folded["plateau_count"] == 1
    assert folded["total_oscillation_excursions_folded"] == 1
    plateau = folded["timeline"][0]
    assert plateau["frame_count"] == 31
    assert plateau["oscillation_excursions_folded"] == 1
    assert plateau["folded_frame_count"] == 1
    assert plateau["folded_species"] == ["C6H4ClO"]
    assert plateau["continuity_basis"] == "skeleton_connectivity_with_folded_oscillations"


def test_connectivity_signature_and_cli_parsing() -> None:
    sig1, _, _ = connectivity_signature([1, 2, 3], ["1-2-1", "2-3-2", "3-1-1"])
    sig2, _, _ = connectivity_signature([1, 2, 3], ["1-2-2", "2-3-1", "3-1-1"])
    assert sig1 == sig2
    assert cyclomatic_ring_count([1, 2, 3], [(1, 2), (2, 3), (1, 3)]) == 1

    assert _parse_atom_id_spec("300..305,310") == [300, 301, 302, 303, 304, 305, 310]

    parser = build_parser()
    ns = parser.parse_args(
        [
            "trace-skeleton",
            "--case",
            "/tmp/case",
            "--atoms",
            "300..305",
            "--mode",
            "skeleton",
            "--oscillation-max-frames",
            "2",
        ]
    )
    assert ns.mode == "skeleton"
    assert ns.oscillation_max_frames == 2

    ns_export = parser.parse_args(
        [
            "export-event",
            "--case",
            "/tmp/case",
            "--event-id",
            "rngevt_1",
            "--lammps-input",
            "/tmp/input.lammps",
            "--out",
            "/tmp/out.zip",
        ]
    )
    assert ns_export.lammps_input == "/tmp/input.lammps"
