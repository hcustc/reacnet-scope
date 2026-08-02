from __future__ import annotations

import io
import json
from zipfile import ZipFile

import pytest

from reacnet_scope.event_package import (
    EVENT_PACKAGE_SCHEMA_VERSION,
    build_event_package,
)

ase_read = pytest.importorskip("ase.io").read


def _viewer(*, complete_mapping: bool = True) -> dict:
    element2 = "O" if complete_mapping else ""
    label2 = element2 or "T2"
    frames = []
    for timestep, bond_state in ((0, "before"), (10, "after")):
        frames.append(
            {
                "frame": timestep,
                "box": [(0, 10), (0, 10), (0, 10)],
                "box_header": "ITEM: BOX BOUNDS pp pp pp",
                "box_lines": ["0 10", "0 10", "0 10"],
                "cell": [[10, 0, 0], [0, 10, 0], [0, 0, 10]],
                "pbc": [True, True, True],
                "bond_state": bond_state,
                "atoms": [
                    {
                        "id": 1,
                        "type": "1",
                        "element": "C",
                        "label": "C",
                        "x": 0.2,
                        "y": 1.0,
                        "z": 1.0,
                        "display_x": -0.2,
                        "display_y": 0.0,
                        "display_z": 0.0,
                        "group": "core",
                    },
                    {
                        "id": 2,
                        "type": "2",
                        "element": element2,
                        "label": label2,
                        "x": 9.8,
                        "y": 1.0,
                        "z": 1.0,
                        "display_x": 0.2,
                        "display_y": 0.0,
                        "display_z": 0.0,
                        "group": "participant",
                    },
                    {
                        "id": 3,
                        "type": "1",
                        "element": "C",
                        "label": "C",
                        "x": 5.0,
                        "y": 5.0,
                        "z": 5.0,
                        "display_x": 4.8,
                        "display_y": 4.0,
                        "display_z": 4.0,
                        "group": "environment",
                    },
                ],
            }
        )
    return {
        "event_id": "rngevt_example",
        "event": {
            "event_id": "rngevt_example",
            "reaction_smiles": "[C]+[O] -> [C][O]",
            "reactant": "[C]+[O]",
            "product": "[C][O]",
            "before_timestep": 0,
            "after_timestep": 10,
            "association_status": "matched",
        },
        "frames": frames,
        "atom_groups": {
            "core": [1],
            "participants": [1, 2],
            "environment": [3],
            "context": [1, 2, 3],
        },
        "bond_evidence": {
            "reactant": ["1-2-1"],
            "product": ["1-2-2"],
            "broken": ["1-2-1"],
            "formed": ["1-2-2"],
        },
        "meta": {
            "reaction_smiles": "[C]+[O] -> [C][O]",
            "verification_status": "matched",
            "type_element_map": {"1": "C", **({"2": "O"} if complete_mapping else {})},
            "extraction": {
                "before_frames": 2,
                "after_frames": 4,
                "environment_radius": 4.0,
                "max_environment_atoms": 500,
            },
            "environment": {
                "radius": 4.0,
                "max_environment_atoms": 500,
                "selected_environment_count": 1,
                "truncated": False,
            },
        },
        "source_signatures": {
            "trajectory": {"path": "/data/run.lammpstrj", "size": 123, "mtime_ns": 456}
        },
    }


def test_event_package_is_deterministic_and_contains_auditable_members() -> None:
    viewer = _viewer()

    first = build_event_package(viewer, scope="participants")
    second = build_event_package(viewer, scope="participants")

    assert first == second
    with ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == [
            "event.json",
            "trajectory.lammpstrj",
            "trajectory.extxyz",
            "bonds.csv",
            "README.txt",
        ]
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        document = json.loads(archive.read("event.json"))
        assert document["schema_version"] == EVENT_PACKAGE_SCHEMA_VERSION
        assert document["selection"]["scope"] == "participants"
        assert document["selection"]["atom_ids"] == [1, 2]
        assert document["selection"]["extraction_parameters"] == {
            "after_frames": 4,
            "before_frames": 2,
            "environment_radius": 4.0,
            "max_environment_atoms": 500,
        }
        assert document["atom_mapping"]["rng_to_trajectory"] == [
            {"rng_atom_id": 0, "trajectory_atom_id": 1},
            {"rng_atom_id": 1, "trajectory_atom_id": 2},
        ]
        assert document["atom_mapping"]["type_to_element"] == {
            "1": "C",
            "2": "O",
        }
        assert document["extxyz_included"] is True
        assert document["source_signatures"]["trajectory"]["size"] == 123
        lammps = archive.read("trajectory.lammpstrj").decode()
        assert lammps.count("ITEM: TIMESTEP") == 2
        assert "ITEM: NUMBER OF ATOMS\n2" in lammps
        bonds = archive.read("bonds.csv").decode()
        assert "reactant,1,2,1,broken" in bonds
        assert "product,1,2,2,formed" in bonds

        images = ase_read(
            io.StringIO(archive.read("trajectory.extxyz").decode()),
            format="extxyz",
            index=":",
        )
        assert len(images) == 2
        assert images[0].get_chemical_symbols() == ["C", "O"]
        assert images[0].pbc.tolist() == [True, True, True]
        assert images[0].arrays["original_id"].tolist() == [1, 2]
        assert images[0].positions[:, 0].tolist() == [-0.2, 0.2]


def test_partial_mapping_omits_only_extxyz_and_keeps_lammps() -> None:
    payload = build_event_package(_viewer(complete_mapping=False))

    with ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == [
            "event.json",
            "trajectory.lammpstrj",
            "bonds.csv",
            "README.txt",
        ]
        document = json.loads(archive.read("event.json"))
        assert document["extxyz_included"] is False
        assert "ITEM: ATOMS id type x y z" in archive.read(
            "trajectory.lammpstrj"
        ).decode()
        assert "omitted" in archive.read("README.txt").decode()


def test_environment_scope_includes_context_atoms() -> None:
    payload = build_event_package(_viewer(), scope="environment")

    with ZipFile(io.BytesIO(payload)) as archive:
        document = json.loads(archive.read("event.json"))
        assert document["selection"]["atom_ids"] == [1, 2, 3]
        assert document["frames"][0]["atom_count"] == 3
