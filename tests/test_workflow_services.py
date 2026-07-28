from __future__ import annotations

from pathlib import Path

from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import ROUTE_INDEX_STORE, TRAJECTORY_INDEX_STORE
from scripts.webapp_dash import services as svc


def _frame(timestep: int) -> str:
    return (
        "ITEM: TIMESTEP\n"
        f"{timestep}\n"
        "ITEM: NUMBER OF ATOMS\n2\n"
        "ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type element x y z\n"
        "1 1 C 1 1 1\n"
        "2 2 O 2 2 2\n"
    )


def _analysis_artifacts(tmp_path: Path) -> dict[str, str]:
    species = tmp_path / "run.lammpstrj.species"
    moname = tmp_path / "run.lammpstrj.moname"
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    trajectory = tmp_path / "run.lammpstrj"
    species.write_text("Timestep 0: [C] 2 [O] 1\nTimestep 10: [C][O] 3\n", encoding="utf-8")
    moname.write_text("[C] 0\n[C][O] 0;1 0,1,1\n", encoding="utf-8")
    reaction.write_text("10 [C]+[O]->[C][O]\n4 [C][O]->[C]+[O]\n", encoding="utf-8")
    reactionevent.write_text("Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n", encoding="utf-8")
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n0,[O],1,\n10,[C][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    trajectory.write_text(_frame(0) + _frame(10), encoding="utf-8")
    return {
        "species": str(species), "moname": str(moname), "reaction": str(reaction),
        "reactionevent": str(reactionevent), "molecules": str(molecules), "trajectory": str(trajectory),
    }


def test_species_catalog_is_sourced_from_species_and_optionally_enriched_by_moname(tmp_path: Path) -> None:
    artifacts = _analysis_artifacts(tmp_path)
    result = svc.search_species_catalog(artifacts, "CO", kind="formula")

    assert result["n_rows"] == 1
    row = result["rows"][0]
    assert row["smiles"] == "[C][O]"
    assert row["total_count"] == 3
    assert row["moname_available"] is True
    assert row["moname_bond_count"] == 1
    assert row["structure"].startswith("![")
    assert "/api/structure.svg?smiles=" in row["structure"]
    assert result["meta"]["catalog_size"] == 3


def test_species_catalog_recovers_transient_reaction_network_species(
    tmp_path: Path,
) -> None:
    species = tmp_path / "transient.species"
    reaction = tmp_path / "transient.reactionabcd"
    transient = "[H][C]1[C]([H])[C](=[O])[C]1[Cl]"
    species.write_text("Timestep 0: [O]=[O] 4\n", encoding="utf-8")
    reaction.write_text(
        f"1 {transient}->[C]=[O]+[H][C]([H])=[C][Cl]\n",
        encoding="utf-8",
    )

    result = svc.search_species_catalog(
        {"species": str(species), "reaction": str(reaction)},
        transient,
        kind="smiles",
    )

    assert result["n_rows"] == 1
    assert result["rows"][0]["smiles"] == transient
    assert result["rows"][0]["total_count"] == 0
    assert result["rows"][0]["catalog_source"] == ".reactionabcd"
    assert result["meta"]["reaction_network_fallback"] is True


def test_species_catalog_matches_equivalent_canonical_smiles(
    tmp_path: Path,
) -> None:
    species = tmp_path / "canonical.species"
    species.write_text("Timestep 0: CCO 2\n", encoding="utf-8")

    result = svc.search_species_catalog(
        {"species": str(species)},
        "OCC",
        kind="smiles",
    )

    assert result["rows"][0]["smiles"] == "CCO"
    assert result["meta"]["canonical_match"] is True


def test_channels_are_split_by_target_role_and_ranked_by_frequency(tmp_path: Path) -> None:
    artifacts = _analysis_artifacts(tmp_path)
    result = svc.collect_species_channels(artifacts, "[C][O]")

    assert result["production_rows"][0]["role_label"] == "生成"
    assert result["production_rows"][0]["forward_tp"] == 10
    assert result["production_rows"][0]["reactant_smiles"] == ["[C]", "[O]"]
    assert result["production_rows"][0]["product_smiles"] == ["[C][O]"]
    assert result["consumption_rows"][0]["role_label"] == "消耗"
    assert result["consumption_rows"][0]["forward_tp"] == 4


def test_selected_channel_structure_detail_preserves_repeated_terms() -> None:
    detail = svc.build_channel_structure_detail(
        {
            "reaction_smiles": "[H] + [H] + [O] -> [H][O][H]",
            "reaction_formulas": "H + H + O -> H2O",
            "reactant_smiles": ["[H]", "[H]", "[O]"],
            "product_smiles": ["[H][O][H]"],
            "reactant_formulas": ["H", "H", "O"],
            "product_formulas": ["H2O"],
        }
    )

    assert detail["ok"] is True
    assert [item["smiles"] for item in detail["reactants"]] == ["[H]", "[H]", "[O]"]
    assert [item["formula"] for item in detail["reactants"]] == ["H", "H", "O"]
    assert [item["occurrence"] for item in detail["reactants"]] == [1, 2, 1]
    assert [item["occurrence_total"] for item in detail["reactants"]] == [2, 2, 1]
    assert detail["reactants"][0]["structure_url"].endswith(
        "&width=180&height=116&show_h=1"
    )
    assert [item["smiles"] for item in detail["products"]] == ["[H][O][H]"]


def test_selected_channel_structure_detail_falls_back_to_spaced_reaction_text() -> None:
    detail = svc.build_channel_structure_detail(
        {"reaction_smiles": "[NH4+] + [O-] -> [NH3] + [OH]"}
    )

    assert [item["smiles"] for item in detail["reactants"]] == ["[NH4+]", "[O-]"]
    assert [item["smiles"] for item in detail["products"]] == ["[NH3]", "[OH]"]


def test_representative_event_ranking_and_viewer_expose_bond_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    artifacts = _analysis_artifacts(tmp_path)
    EVENT_EVIDENCE_STORE.build(
        artifacts["reactionevent"], artifacts["molecules"]
    )
    TRAJECTORY_INDEX_STORE.build(artifacts["trajectory"])

    ranked = svc.rank_representative_events(
        artifacts,
        "[C] + [O] -> [C][O]",
    )
    event = ranked["rows"][0]
    assert event["recommendation"] == "recommended"
    assert event["formed_bonds"] == "1-2-1"
    assert event["atom_id_list"] == [1, 2]

    viewer = svc.build_rng_event_visualization(artifacts, event, before_frames=0, after_frames=0)
    assert viewer["atom_groups"]["core"] == [1, 2]
    assert viewer["bond_evidence"]["formed"] == ["1-2-1"]
    assert viewer["frames"][0]["bond_state"] == "before"
    assert viewer["frames"][1]["bond_state"] == "after"


def test_event_viewer_exports_frames_trajectory_and_viewer_helpers() -> None:
    viewer = {
        "event_id": "event-1",
        "frames": [
            {
                "frame": 10,
                "box": [(0, 10), (0, 10), (0, 10)],
                "bond_state": "before",
                "atoms": [
                    {
                        "id": 2,
                        "type": "2",
                        "element": "O",
                        "x": 2.0,
                        "y": 2.0,
                        "z": 2.0,
                        "group": "core",
                    },
                    {
                        "id": 1,
                        "type": "1",
                        "element": "C",
                        "x": 1.0,
                        "y": 1.0,
                        "z": 1.0,
                        "group": "core",
                    },
                ],
            }
        ],
        "atom_groups": {"core": [1, 2], "context": [2, 1]},
    }

    frames_csv = svc.event_viewer_frames_csv(viewer)
    trajectory = svc.event_viewer_trajectory_text(viewer)
    expression = svc.event_viewer_ovito_expression(viewer)
    vmd = svc.event_viewer_vmd_script(
        viewer,
        trajectory_name="event-1_subset.lammpstrj",
    )

    assert "frame,atom_id,type,element" in frames_csv
    assert "10,2,2,O,2.0,2.0,2.0,core,before" in frames_csv
    assert "ITEM: NUMBER OF ATOMS\n2" in trajectory
    assert "ITEM: ATOMS id type element x y z" in trajectory
    assert "ParticleIdentifier == 1 || ParticleIdentifier == 2" == expression
    assert 'mol new "event-1_subset.lammpstrj"' in vmd
    assert "Original LAMMPS atom IDs: 1 2" in vmd


def test_continuous_composition_cancels_only_one_selected_intermediate() -> None:
    composed = svc.compose_continuous_reaction_pair(
        {
            "event_id": "anchor",
            "reactant_participants": [
                {"species": "B", "atom_ids": []},
                {"species": "B", "atom_ids": []},
            ],
            "product_participants": [
                {"species": "C", "atom_ids": []}
            ],
        },
        {
            "event_id": "previous",
            "reactant_smiles": ["A"],
            "product_smiles": ["B", "B"],
        },
        direction="backward",
        intermediate_smiles="B",
    )

    assert composed["reactant_smiles"] == ["A", "B"]
    assert composed["product_smiles"] == ["B", "C"]
    assert composed["event_ids"] == ["previous", "anchor"]
    assert composed["cancelled_intermediate"] == "B"


def test_continuous_search_degrades_to_exact_smiles_network_candidates(
    tmp_path: Path,
) -> None:
    reaction = tmp_path / "network.reactionabcd"
    reaction.write_text(
        "12 A->[NH4+]\n"
        "9 X->NH4\n"
        "7 [NH4+]->B\n",
        encoding="utf-8",
    )
    anchor = {
        "reaction_smiles": "[NH4+] -> B",
        "reactant_smiles": ["[NH4+]"],
        "product_smiles": ["B"],
    }

    result = svc.find_continuous_reactions(
        {"reaction": str(reaction)},
        anchor,
        direction="backward",
        intermediate_smiles="[NH4+]",
    )

    assert result["evidence_level"] == "network_only"
    assert result["can_assert_order"] is False
    assert result["time_basis"] == "none"
    assert [row["reaction_smiles"] for row in result["rows"]] == [
        "A -> [NH4+]"
    ]


def test_continuous_network_does_not_join_same_formula_isomers(
    tmp_path: Path,
) -> None:
    reaction = tmp_path / "isomers.reactionabcd"
    reaction.write_text(
        "12 A->[O][C]\n"
        "20 X->[C][O]\n"
        "7 [O][C]->B\n",
        encoding="utf-8",
    )

    result = svc.find_continuous_reactions(
        {"reaction": str(reaction)},
        {
            "reaction_smiles": "[O][C] -> B",
            "reactant_smiles": ["[O][C]"],
            "product_smiles": ["B"],
        },
        direction="backward",
        intermediate_smiles="[O][C]",
    )

    assert [row["reaction_smiles"] for row in result["rows"]] == [
        "A -> [O][C]"
    ]


def test_continuous_search_prefers_reactionevent_without_molecules(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent = tmp_path / "events.reactionevent.csv"
    reaction = tmp_path / "events.reactionabcd"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "2,A,B\n"
        "8,B,C\n",
        encoding="utf-8",
    )
    reaction.write_text("3 A->B\n4 B->C\n", encoding="utf-8")
    EVENT_EVIDENCE_STORE.build(str(reactionevent))
    anchor = svc.locate_rng_events(
        {"reactionevent": str(reactionevent)}, "B -> C"
    )["rows"][0]

    result = svc.find_continuous_reactions(
        {
            "reactionevent": str(reactionevent),
            "reaction": str(reaction),
        },
        anchor,
        direction="backward",
        intermediate_smiles="B",
    )

    assert result["evidence_level"] == "rng_event"
    assert result["time_basis"] == "timestep_index"
    assert result["association_available"] is False
    assert result["rows"][0]["reaction_smiles"] == "A -> B"


def test_continuous_search_uses_prepared_route_frames(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = tmp_path / "route.reactionabcd"
    route = tmp_path / "route.route"
    reaction.write_text("9 A->B\n12 B->C\n", encoding="utf-8")
    route.write_text(
        "Atom 1 C: 0 A -> 10 B -> 20 C\n",
        encoding="utf-8",
    )
    ROUTE_INDEX_STORE.build(str(route))
    anchor = {
        "reaction_smiles": "B -> C",
        "reactant_smiles": ["B"],
        "product_smiles": ["C"],
    }

    result = svc.find_continuous_reactions(
        {"reaction": str(reaction), "route": str(route)},
        anchor,
        direction="backward",
        intermediate_smiles="B",
    )

    assert result["evidence_level"] == "route"
    assert result["time_basis"] == "route_frame"
    assert result["can_assert_order"] is True
    assert result["rows"][0]["candidate_start_frame"] == 0
    assert result["rows"][0]["candidate_end_frame"] == 10
    assert result["rows"][0]["anchor_start_frame"] == 10
    assert result["rows"][0]["anchor_end_frame"] == 20


def test_continuous_route_candidates_use_prepared_species_net_change(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = tmp_path / "validated.reactionabcd"
    route = tmp_path / "validated.route"
    species = tmp_path / "validated.species"
    reaction.write_text("9 A->B\n12 B->C\n", encoding="utf-8")
    route.write_text(
        "Atom 1 C: 0 A -> 10 B -> 20 C\n",
        encoding="utf-8",
    )
    species.write_text(
        "Timestep 0: A 1\n"
        "Timestep 10: B 1\n"
        "Timestep 20: C 1\n",
        encoding="utf-8",
    )
    ROUTE_INDEX_STORE.build(str(route))
    SPECIES_COMPOSITION_STORE.build(str(species))

    result = svc.find_continuous_reactions(
        {
            "reaction": str(reaction),
            "route": str(route),
            "species": str(species),
        },
        {
            "reaction_smiles": "B -> C",
            "reactant_smiles": ["B"],
            "product_smiles": ["C"],
        },
        direction="backward",
        intermediate_smiles="B",
    )

    assert result["evidence_level"] == "route_species"
    assert result["rows"][0]["species_validated"] is True


def test_core_continuous_route_search_uses_strict_read_budgets(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = tmp_path / "core.reactionabcd"
    route = tmp_path / "core.route"
    species = tmp_path / "core.species"
    reaction.write_text("9 A->B\n12 B->C\n", encoding="utf-8")
    route.write_text(
        "Atom 1 C: 0 A -> 10 B -> 20 C\n",
        encoding="utf-8",
    )
    species.write_text(
        "Timestep 0: A 1\n"
        "Timestep 10: B 1\n"
        "Timestep 20: C 1\n",
        encoding="utf-8",
    )
    ROUTE_INDEX_STORE.build(str(route))
    SPECIES_COMPOSITION_STORE.build(str(species))

    result = svc.find_continuous_reactions(
        {
            "reaction": str(reaction),
            "route": str(route),
            "species": str(species),
        },
        {
            "reaction_smiles": "B -> C",
            "reactant_smiles": ["B"],
            "product_smiles": ["C"],
        },
        direction="backward",
        intermediate_smiles="B",
        limit=100,
        core_only=True,
    )

    assert result["evidence_level"] == "route"
    assert result["limit"] == 10
    assert result["meta"]["search_stage"] == "core_shortlist"
    assert result["meta"]["budgets"] == {
        "candidate_limit": 10,
        "network_candidate_pool": 20,
        "route_hits_per_reaction": 200,
        "species_validation": False,
    }
