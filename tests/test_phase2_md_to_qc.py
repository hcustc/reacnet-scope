"""Phase 2 golden benchmark tests for 3D geometry auditing, topology classification, and QC manifests."""

from __future__ import annotations

import io
import json
from zipfile import ZipFile

from reacnet_scope.event_package import (
    QC_MANIFEST_SCHEMA_VERSION,
    build_event_package,
    build_qc_manifest,
)
from reacnet_scope.geometry_audit import (
    BOND_OSCILLATION_ARTIFACT,
    GEOMETRY_AUDIT_SCHEMA,
    TRANSIENT_RECROSSING_COLLISION,
    VERIFIED_NET_TRANSFORMATION,
    audit_event_geometry,
)
from reacnet_scope.topology_classifier import (
    TOPOLOGY_CLASSIFIER_SCHEMA,
    classify_species_collection,
    classify_species_topology,
)
from scripts.rng_query_cli import main as cli_main


def _pair_viewer(
    event_id: str,
    elem_a: str,
    elem_b: str,
    distances: list[float],
    *,
    role: str,
    wrap_pbc: bool = False,
) -> dict:
    box = [(0.0, 20.0), (0.0, 20.0), (0.0, 20.0)]
    frames = []
    for idx, dist in enumerate(distances):
        xa = 19.5 if wrap_pbc else 1.0
        xb = (xa + dist) % 20.0 if wrap_pbc else 1.0 + dist
        frames.append(
            {
                "frame": idx * 10,
                "box": box,
                "cell": [[20, 0, 0], [0, 20, 0], [0, 0, 20]],
                "pbc": [True, True, True],
                "bond_state": "before" if idx < len(distances) // 2 else "after",
                "atoms": [
                    {
                        "id": 1,
                        "type": "1",
                        "element": elem_a,
                        "x": xa,
                        "y": 1.0,
                        "z": 1.0,
                        "display_x": 0.0,
                        "display_y": 0.0,
                        "display_z": 0.0,
                        "group": "core",
                    },
                    {
                        "id": 2,
                        "type": "2",
                        "element": elem_b,
                        "x": xb,
                        "y": 1.0,
                        "z": 1.0,
                        "display_x": dist,
                        "display_y": 0.0,
                        "display_z": 0.0,
                        "group": "core",
                    },
                ],
            }
        )
    bond_evidence = {
        "reactant": ["1-2-1"] if role == "broken" else [],
        "product": ["1-2-1"] if role == "formed" else [],
        "broken": ["1-2-1"] if role == "broken" else [],
        "formed": ["1-2-1"] if role == "formed" else [],
    }
    return {
        "event_id": event_id,
        "event": {"event_id": event_id, "association_status": "matched"},
        "frames": frames,
        "atom_groups": {"core": [1, 2], "participants": [1, 2], "environment": []},
        "bond_evidence": bond_evidence,
        "meta": {"type_element_map": {"1": elem_a, "2": elem_b}},
    }


def test_geometry_audit_verified_cleavage_with_pbc_wrapping() -> None:
    """Case C6_oxygen2: O-O cleavage persists across periodic boundary."""
    view = _pair_viewer(
        "rngevt_c6_oxygen2",
        "O",
        "O",
        [1.366, 1.480, 2.029, 2.410, 2.850],
        role="broken",
        wrap_pbc=True,
    )
    audit = audit_event_geometry(view)
    assert audit["schema_version"] == GEOMETRY_AUDIT_SCHEMA
    assert audit["verdict"] == VERIFIED_NET_TRANSFORMATION
    assert audit["pairs"][0]["start_distance_ang"] == 1.366
    assert audit["pairs"][0]["end_distance_ang"] == 2.85


def test_geometry_audit_growth_c6_ether_oscillation_artifact() -> None:
    """Case 3 (growth_C6): C967-O974 oscillates 1.247 -> 1.574 -> 1.915 -> 1.530 -> 1.209 -> 1.658 A."""
    view = _pair_viewer(
        "rngevt_growth_c6",
        "C",
        "O",
        [1.247, 1.574, 1.915, 1.530, 1.209, 1.658],
        role="broken",
    )
    audit = audit_event_geometry(view)
    assert audit["verdict"] == BOND_OSCILLATION_ARTIFACT
    assert audit["pairs"][0]["cutoff_crossings"] == 2


def test_geometry_audit_activation_o2_and_growth_c5_transient_collisions() -> None:
    """Case 4 (activation_O2 & growth_C5): grazing O-H stretch and brief C-C rebound."""
    o2_view = _pair_viewer(
        "rngevt_activation_o2",
        "O",
        "H",
        [0.98, 1.06, 1.181, 1.05, 0.99],
        role="broken",
    )
    assert audit_event_geometry(o2_view)["verdict"] == TRANSIENT_RECROSSING_COLLISION

    c5_view = _pair_viewer(
        "rngevt_growth_c5",
        "C",
        "C",
        [3.10, 1.95, 1.62, 2.25, 3.30],
        role="formed",
    )
    assert audit_event_geometry(c5_view)["verdict"] == TRANSIENT_RECROSSING_COLLISION


def test_topology_classifier_distinguishes_c6h3clo2_isomers_and_df_core() -> None:
    """Case 1 (1,3-meta vs 1,4-para vs 1,2-ortho C6H3ClO2) and Case 6 (6-5-6 DF core)."""
    para_quinone = "O=C1C=CC(=O)C(Cl)=C1"
    ortho_quinone = "O=C1C(=O)C(Cl)=CC=C1"
    meta_diradical = "[O]c1cc([O])cc(Cl)c1"

    p_info = classify_species_topology(para_quinone)
    o_info = classify_species_topology(ortho_quinone)
    m_info = classify_species_topology(meta_diradical)

    assert p_info["schema_version"] == TOPOLOGY_CLASSIFIER_SCHEMA
    assert p_info["formula"] == "C6H3ClO2"
    assert o_info["formula"] == "C6H3ClO2"
    assert m_info["formula"] == "C6H3ClO2"
    assert p_info["oxygen_substitution_patterns"] == ["1,4-para-dioxy"]
    assert o_info["oxygen_substitution_patterns"] == ["1,2-ortho-dioxy"]
    assert m_info["oxygen_substitution_patterns"] == ["1,3-meta-dioxy"]

    collection = classify_species_collection(
        [para_quinone, ortho_quinone, meta_diradical], formula_filter="C6H3ClO2"
    )
    assert collection["species_count"] == 3
    assert len(collection["groups"]) == 3

    # Case 6: 6-5-6 dibenzofuran core vs 6-6-6 dioxin vs linked-5-5 vs fused-6-6 naphthalene
    df_smiles = "Oc1cc2oc3ccccc3c2cc1Cl"
    dd_smiles = "Clc1ccc2Oc3ccccc3Oc2c1"
    linked_c5 = "C1=CC=CC1C1C=CC=C1"
    naphthalene = "c1ccc2ccccc2c1"

    df_info = classify_species_topology(df_smiles)
    dd_info = classify_species_topology(dd_smiles)
    c5_info = classify_species_topology(linked_c5)
    naph_info = classify_species_topology(naphthalene)

    assert df_info["ring_system"] == "fused-6-5-6"
    assert df_info["core_motif"] == "dibenzofuran_6_5_6_core"
    assert dd_info["ring_system"] == "fused-6-6-6"
    assert dd_info["core_motif"] == "dibenzodioxin_6_6_6_core"
    assert c5_info["ring_system"] == "linked-5-5"
    assert naph_info["ring_system"] == "fused-6-6"


def test_qc_manifest_case5_external_c2h_abstraction_143_electrons() -> None:
    """Case 5 (665.94 ps): C12H8Cl2O2 + external C2H = 27 atoms, 143 electrons (D/Q)."""
    composition = (
        [("C", "1")] * 14
        + [("H", "2")] * 9
        + [("Cl", "3")] * 2
        + [("O", "4")] * 2
    )
    atoms_before = [
        {
            "id": i + 1,
            "type": t,
            "element": elem,
            "x": float(i) * 0.5,
            "y": 0.0,
            "z": 0.0,
            "display_x": float(i) * 0.5,
            "display_y": 0.0,
            "display_z": 0.0,
            "group": "core" if i in {0, 14} else "participant",
        }
        for i, (elem, t) in enumerate(composition)
    ]
    atoms_after = [dict(a) for a in atoms_before]
    atoms_after[14]["x"] = atoms_after[0]["x"] + 2.4  # H abstracted away

    view = {
        "event_id": "rngevt_66594_b8e09f36f670",
        "event": {
            "event_id": "rngevt_66594_b8e09f36f670",
            "association_status": "matched",
            "before_timestep": 6659400,
            "after_timestep": 6659500,
        },
        "frames": [
            {
                "frame": 6659400,
                "box": [(0, 30), (0, 30), (0, 30)],
                "cell": [[30, 0, 0], [0, 30, 0], [0, 0, 30]],
                "pbc": [True, True, True],
                "bond_state": "before",
                "atoms": atoms_before,
            },
            {
                "frame": 6659500,
                "box": [(0, 30), (0, 30), (0, 30)],
                "cell": [[30, 0, 0], [0, 30, 0], [0, 0, 30]],
                "pbc": [True, True, True],
                "bond_state": "after",
                "atoms": atoms_after,
            },
        ],
        "atom_groups": {
            "core": [1, 15],
            "participants": list(range(1, 28)),
            "environment": [],
        },
        "bond_evidence": {
            "reactant": ["1-15-1"],
            "product": [],
            "broken": ["1-15-1"],
            "formed": [],
        },
        "meta": {
            "timestep_ps": 0.0001,
            "coordinate_length_unit": "angstrom",
            "type_element_map": {"1": "C", "2": "H", "3": "Cl", "4": "O"},
        },
    }

    manifest = build_qc_manifest(view, scope="participants", charge=0)
    assert manifest["schema_version"] == QC_MANIFEST_SCHEMA_VERSION
    assert manifest["atom_count"] == 27
    assert manifest["formula"] == "C14H9Cl2O2"
    assert manifest["total_electrons"] == 143
    assert manifest["electron_parity"] == "odd"
    assert manifest["recommended_multiplicities"] == [2, 4]
    assert manifest["core_atom_ids"] == [1, 15]

    zip_a = build_event_package(view, scope="participants", include_qc_manifest=True)
    zip_b = build_event_package(view, scope="participants", include_qc_manifest=True)
    assert zip_a == zip_b
    with ZipFile(io.BytesIO(zip_a)) as archive:
        assert "qc_manifest.json" in archive.namelist()
        embedded = json.loads(archive.read("qc_manifest.json"))
    assert embedded["total_electrons"] == 143


def test_cli_classify_isomers_command(capsys) -> None:
    rc = cli_main(
        [
            "classify-isomers",
            "--smiles",
            "O=C1C=CC(=O)C(Cl)=C1,[O]c1cc([O])cc(Cl)c1",
            "--formula",
            "C6H3ClO2",
        ]
    )
    assert rc == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["species_count"] == 2
    assert len(captured["groups"]) == 2
