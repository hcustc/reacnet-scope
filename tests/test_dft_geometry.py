from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pytest

from reacnet_scope.dft_geometry import (
    DFT_GEOMETRY_SCHEMA_VERSION,
    DftGeometryError,
    DftGeometryRequest,
    build_dft_geometry_bundle,
)
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.trajectory import (
    TrajectoryFrameError,
    dataset_settings_path,
    load_coordinate_length_unit,
    load_type_element_map,
    save_coordinate_length_unit,
    save_type_element_map,
)


def _frame(timestep: int, first_x: float, second_x: float) -> str:
    return (
        "ITEM: TIMESTEP\n"
        f"{timestep}\n"
        "ITEM: NUMBER OF ATOMS\n2\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n"
        f"1 1 {first_x} 1.0 1.0\n"
        f"2 2 {second_x} 1.0 1.0\n"
    )


def _case(tmp_path: Path, monkeypatch) -> tuple[dict[str, str], dict]:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "run.lammpstrj"
    trajectory.write_text(
        _frame(0, 0.2, 9.8) + _frame(10, 0.1, 9.9),
        encoding="utf-8",
    )
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    event = {
        "event_id": "rngevt-pbc-association",
        "reaction_smiles": "[C]+[O] -> [C][O]",
        "association_status": "matched",
        "before_timestep": 0,
        "after_timestep": 10,
        "reactant_bonds": "",
        "product_bonds": "1-2-1",
        "reactant_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[O]", "atom_ids": [2]},
        ],
        "product_participants": [
            {"species": "[C][O]", "atom_ids": [1, 2]},
        ],
    }
    return {"trajectory": str(trajectory)}, event


def _xyz_x_values(text: str) -> list[float]:
    return [float(line.split()[1]) for line in text.splitlines()[2:]]


def test_dft_geometry_reconstructs_pbc_complex_and_is_deterministic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    request = DftGeometryRequest(
        layout="both",
        source_length_unit="angstrom",
        atom_type_map={"1": "C", "2": "O"},
        electronic_states={
            "reactants": {"charge": 0, "multiplicity": 1},
            "products": (0, 1),
        },
    )

    bundle = build_dft_geometry_bundle(artifacts, event, request)
    second = build_dft_geometry_bundle(artifacts, event, request)

    assert bundle.manifest["schema_version"] == DFT_GEOMETRY_SCHEMA_VERSION
    assert bundle.manifest["cross_side_atom_ids_match"] is True
    assert bundle.manifest["selection"]["participant_index_base"] == 0
    assert bundle.manifest["selection"]["participant_number_base"] == 1
    assert bundle.manifest["requested_electronic_states"] == {
        "products": {"charge": 0, "multiplicity": 1, "status": "user_supplied"},
        "reactants": {"charge": 0, "multiplicity": 1, "status": "user_supplied"},
    }
    assert sorted(bundle.geometries) == [
        "product-01-atoms-1-2.xyz",
        "products.xyz",
        "reactant-01-atoms-1-1.xyz",
        "reactant-02-atoms-2-2.xyz",
        "reactants.xyz",
    ]
    reactant_x = _xyz_x_values(bundle.geometries["reactants.xyz"])
    product_x = _xyz_x_values(bundle.geometries["products.xyz"])
    assert abs(reactant_x[0] - reactant_x[1]) == pytest.approx(0.4)
    assert abs(product_x[0] - product_x[1]) == pytest.approx(0.2)
    assert "charge=0 multiplicity=1" in bundle.geometries["reactants.xyz"]
    reactant_meta = next(
        item
        for item in bundle.manifest["geometries"]
        if item["file"] == "reactants.xyz"
    )
    assert reactant_meta["component_placement"][0][
        "max_contact_residual_angstrom"
    ] == pytest.approx(0.0)
    assert bundle.to_zip() == second.to_zip()
    with ZipFile(io.BytesIO(bundle.to_zip())) as archive:
        assert archive.namelist()[:3] == [
            "manifest.json",
            "atom_map.csv",
            "README.txt",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["event"]["event_id"] == event["event_id"]
        atom_map = archive.read("atom_map.csv").decode()
        assert "reactants.xyz,1,reactant,0,0,1,[C],1,1,C" in atom_map
        assert "not transition states" in archive.read("README.txt").decode()


def test_dft_geometry_selection_can_export_one_side_and_instance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    bundle = build_dft_geometry_bundle(
        artifacts,
        event,
        DftGeometryRequest(
            include_products=False,
            reactant_indices=(1,),
            layout="separate",
            source_length_unit="angstrom",
            atom_type_map={"1": "C", "2": "O"},
        ),
    )

    assert list(bundle.geometries) == ["reactant-02-atoms-2-2.xyz"]
    assert bundle.geometries["reactant-02-atoms-2-2.xyz"].splitlines()[0] == "1"
    assert bundle.manifest["cross_side_atom_ids_match"] is None


@pytest.mark.parametrize(
    ("event_update", "geometry_request", "reason"),
    [
        (
            {"association_status": "unresolved_hmm_timeline"},
            DftGeometryRequest(source_length_unit="angstrom", atom_type_map={"1": "C", "2": "O"}),
            "unresolved_event",
        ),
        (
            {},
            DftGeometryRequest(atom_type_map={"1": "C", "2": "O"}),
            "unconfirmed_length_unit",
        ),
        (
            {},
            DftGeometryRequest(source_length_unit="angstrom", atom_type_map={"1": "C"}),
            "incomplete_element_mapping",
        ),
        (
            {},
            DftGeometryRequest(
                source_length_unit="angstrom",
                atom_type_map={"1": "C", "2": "O"},
                max_atom_count=1,
            ),
            "atom_limit_exceeded",
        ),
    ],
)
def test_dft_geometry_fails_closed(
    tmp_path: Path,
    monkeypatch,
    event_update: dict,
    geometry_request: DftGeometryRequest,
    reason: str,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    event.update(event_update)

    with pytest.raises(DftGeometryError) as captured:
        build_dft_geometry_bundle(artifacts, event, geometry_request)

    assert captured.value.reason == reason


def test_coordinate_unit_confirmation_preserves_other_dataset_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, _event = _case(tmp_path, monkeypatch)
    trajectory = artifacts["trajectory"]
    save_type_element_map(trajectory, {"1": "C", "2": "O"})

    path = save_coordinate_length_unit(trajectory)

    assert path.is_file()
    assert load_coordinate_length_unit(trajectory) == "angstrom"
    assert load_type_element_map(trajectory) == {"1": "C", "2": "O"}


def test_concurrent_dataset_setting_updates_keep_both_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, _event = _case(tmp_path, monkeypatch)
    trajectory = artifacts["trajectory"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(save_type_element_map, trajectory, {"1": "C", "2": "O"}),
            pool.submit(save_coordinate_length_unit, trajectory),
        ]
        for future in futures:
            future.result()

    assert load_type_element_map(trajectory) == {"1": "C", "2": "O"}
    assert load_coordinate_length_unit(trajectory) == "angstrom"


def test_dft_geometry_unwraps_triclinic_cell(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "triclinic.lammpstrj"

    def frame(timestep: int) -> str:
        return (
            "ITEM: TIMESTEP\n"
            f"{timestep}\n"
            "ITEM: NUMBER OF ATOMS\n2\n"
            "ITEM: BOX BOUNDS xy xz yz pp pp pp\n"
            "0 10 2\n0 10 0\n0 10 0\n"
            "ITEM: ATOMS id type x y z\n"
            "1 1 0.1 1 1\n"
            "2 2 7.9 1 1\n"
        )

    trajectory.write_text(frame(0) + frame(10), encoding="utf-8")
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    event = {
        "event_id": "triclinic-event",
        "association_status": "matched",
        "before_timestep": 0,
        "after_timestep": 10,
        "reactant_bonds": "",
        "product_bonds": "1-2-1",
        "reactant_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[O]", "atom_ids": [2]},
        ],
        "product_participants": [
            {"species": "[C][O]", "atom_ids": [1, 2]},
        ],
    }

    bundle = build_dft_geometry_bundle(
        {"trajectory": str(trajectory)},
        event,
        DftGeometryRequest(
            source_length_unit="angstrom",
            atom_type_map={"1": "C", "2": "O"},
        ),
    )

    values = _xyz_x_values(bundle.geometries["products.xyz"])
    assert abs(values[0] - values[1]) == pytest.approx(0.2)


def test_dft_geometry_checks_periodic_ring_closure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "ring.lammpstrj"

    def frame(timestep: int) -> str:
        return (
            "ITEM: TIMESTEP\n"
            f"{timestep}\n"
            "ITEM: NUMBER OF ATOMS\n3\n"
            "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
            "ITEM: ATOMS id type x y z\n"
            "1 1 0.1 1 1\n"
            "2 1 9.9 1 1\n"
            "3 1 0.3 1 1\n"
        )

    trajectory.write_text(frame(0) + frame(10), encoding="utf-8")
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    participants = [{"species": "[C]1[C][C]1", "atom_ids": [1, 2, 3]}]
    event = {
        "event_id": "ring-event",
        "association_status": "matched",
        "before_timestep": 0,
        "after_timestep": 10,
        "reactant_bonds": "1-2-1;2-3-1;1-3-1",
        "product_bonds": "1-2-1;2-3-1;1-3-1",
        "reactant_participants": participants,
        "product_participants": participants,
    }

    bundle = build_dft_geometry_bundle(
        {"trajectory": str(trajectory)},
        event,
        DftGeometryRequest(
            include_products=False,
            source_length_unit="angstrom",
            atom_type_map={"1": "C"},
        ),
    )

    values = _xyz_x_values(bundle.geometries["reactants.xyz"])
    assert max(values) - min(values) == pytest.approx(0.4)


def test_dft_geometry_rejects_bond_crossing_molecule_instances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    event["reactant_bonds"] = "1-2-1"

    with pytest.raises(DftGeometryError) as captured:
        build_dft_geometry_bundle(
            artifacts,
            event,
            DftGeometryRequest(
                include_products=False,
                source_length_unit="angstrom",
                atom_type_map={"1": "C", "2": "O"},
            ),
        )

    assert captured.value.reason == "inconsistent_molecular_topology"


@pytest.mark.parametrize(
    ("request_update", "event_update", "reason"),
    [
        ({"electronic_states": {"reactant": (0, 1)}}, {}, "unknown_electronic_state"),
        ({"electronic_states": {"reactants": (0.5, 1)}}, {}, "invalid_electronic_state"),
        ({}, {"before_timestep": 0.5}, "missing_exact_frame"),
    ],
)
def test_dft_geometry_rejects_unknown_states_and_fractional_integers(
    tmp_path: Path,
    monkeypatch,
    request_update: dict,
    event_update: dict,
    reason: str,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    event.update(event_update)
    values = {
        "source_length_unit": "angstrom",
        "atom_type_map": {"1": "C", "2": "O"},
        **request_update,
    }

    with pytest.raises(DftGeometryError) as captured:
        build_dft_geometry_bundle(artifacts, event, DftGeometryRequest(**values))

    assert captured.value.reason == reason


def test_dft_geometry_rejects_inconsistent_multiple_contact_images(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "multiple-contacts.lammpstrj"

    def frame(timestep: int) -> str:
        return (
            "ITEM: TIMESTEP\n"
            f"{timestep}\n"
            "ITEM: NUMBER OF ATOMS\n4\n"
            "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
            "ITEM: ATOMS id type x y z\n"
            "1 1 0.1 1 1\n"
            "2 1 0.3 1 1\n"
            "3 1 9.9 1 1\n"
            "4 1 5.0 1 1\n"
        )

    trajectory.write_text(frame(0) + frame(10), encoding="utf-8")
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    event = {
        "event_id": "multiple-contact-event",
        "association_status": "matched",
        "before_timestep": 0,
        "after_timestep": 10,
        "reactant_bonds": "1-2-1;3-4-1",
        "product_bonds": "1-2-1;3-4-1;1-3-1;2-4-1",
        "reactant_participants": [
            {"species": "[C][C]", "atom_ids": [1, 2]},
            {"species": "[C][C]", "atom_ids": [3, 4]},
        ],
        "product_participants": [
            {"species": "[C]1[C][C][C]1", "atom_ids": [1, 2, 3, 4]},
        ],
    }

    with pytest.raises(DftGeometryError) as captured:
        build_dft_geometry_bundle(
            {"trajectory": str(trajectory)},
            event,
            DftGeometryRequest(
                include_products=False,
                source_length_unit="angstrom",
                atom_type_map={"1": "C"},
            ),
        )

    assert captured.value.reason == "inconsistent_contact_image"


@pytest.mark.parametrize(
    ("box_header", "box_lines", "coordinates", "expected_x_distance"),
    [
        (
            "ITEM: BOX BOUNDS ff ff ff",
            "0 10\n0 10\n0 10\n",
            ((1.0, 1.0), (4.0, 9.0)),
            3.0,
        ),
        (
            "ITEM: BOX BOUNDS pp ff ff",
            "0 10\n0 10\n0 10\n",
            ((0.2, 1.0), (9.8, 9.0)),
            0.4,
        ),
    ],
)
def test_dft_geometry_handles_nonperiodic_and_partial_periodicity(
    tmp_path: Path,
    monkeypatch,
    box_header: str,
    box_lines: str,
    coordinates: tuple[tuple[float, float], tuple[float, float]],
    expected_x_distance: float,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    trajectory = tmp_path / "mixed-pbc.lammpstrj"

    def frame(timestep: int) -> str:
        return (
            "ITEM: TIMESTEP\n"
            f"{timestep}\n"
            "ITEM: NUMBER OF ATOMS\n2\n"
            f"{box_header}\n{box_lines}"
            "ITEM: ATOMS id type x y z\n"
            f"1 1 {coordinates[0][0]} {coordinates[0][1]} 1\n"
            f"2 1 {coordinates[1][0]} {coordinates[1][1]} 1\n"
        )

    trajectory.write_text(frame(0) + frame(10), encoding="utf-8")
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    event = {
        "event_id": "mixed-pbc-event",
        "association_status": "matched",
        "before_timestep": 0,
        "after_timestep": 10,
        "reactant_bonds": "",
        "product_bonds": "1-2-1",
        "reactant_participants": [
            {"species": "[C]", "atom_ids": [1]},
            {"species": "[C]", "atom_ids": [2]},
        ],
        "product_participants": [
            {"species": "[C][C]", "atom_ids": [1, 2]},
        ],
    }
    bundle = build_dft_geometry_bundle(
        {"trajectory": str(trajectory)},
        event,
        DftGeometryRequest(
            source_length_unit="angstrom",
            atom_type_map={"1": "C"},
        ),
    )

    values = _xyz_x_values(bundle.geometries["reactants.xyz"])
    assert abs(values[0] - values[1]) == pytest.approx(expected_x_distance)


def test_corrupt_dataset_settings_fail_explicitly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifacts, event = _case(tmp_path, monkeypatch)
    settings = dataset_settings_path(artifacts["trajectory"], persist_identity=True)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{broken", encoding="utf-8")

    with pytest.raises(TrajectoryFrameError, match="数据集设置文件无效"):
        build_dft_geometry_bundle(
            artifacts,
            event,
            DftGeometryRequest(atom_type_map={"1": "C", "2": "O"}),
        )
