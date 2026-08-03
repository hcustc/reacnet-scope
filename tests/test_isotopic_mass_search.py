from __future__ import annotations

from pathlib import Path

import pytest

from reacnet_scope.formula import formula_isotopic_masses
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope import services as svc


def test_formula_mass_does_not_silently_expand_chlorine_isotopes() -> None:
    chlorine = formula_isotopic_masses("Cl")
    assert chlorine is not None
    assert [nominal for _, nominal in chlorine] == [35]
    assert [exact for exact, _ in chlorine] == pytest.approx(
        [34.968852682]
    )

    dichloromethane = formula_isotopic_masses("CH2Cl2")
    assert dichloromethane is not None
    assert [nominal for _, nominal in dichloromethane] == [84]


def test_exact_mass_search_only_matches_neutral_monoisotopic_mass(
    tmp_path: Path,
) -> None:
    species = tmp_path / "chlorine.species"
    species.write_text("Timestep 0: [Cl] 3 [C] 1\n", encoding="utf-8")
    SPECIES_COMPOSITION_STORE.build(str(species))
    artifacts = {"species": str(species)}

    chlorine_35 = svc.search_species_catalog(
        artifacts,
        "34.968852682",
        kind="mass",
        mass_tolerance=0,
    )
    chlorine_37 = svc.search_species_catalog(
        artifacts,
        "36.965902602",
        kind="mass",
        mass_tolerance=0,
    )

    assert [row["smiles"] for row in chlorine_35["rows"]] == ["[Cl]"]
    assert chlorine_37["rows"] == []
    assert chlorine_35["rows"][0]["nominal_mass"] == 35
    assert chlorine_35["rows"][0]["exact_mass"] == pytest.approx(34.968853)


def test_exact_mass_search_does_not_expand_chlorine_37(tmp_path: Path) -> None:
    species = tmp_path / "chlorine.species"
    species.write_text("Timestep 0: [Cl] 3\n", encoding="utf-8")
    SPECIES_COMPOSITION_STORE.build(str(species))

    result = svc.search_species_catalog(
        {"species": str(species)},
        "36.965902602",
        kind="mass",
        mass_tolerance=0,
    )

    assert result["rows"] == []


def test_species_catalog_mass_search_groups_structures_by_formula(
    tmp_path: Path,
) -> None:
    species = tmp_path / "isomers.species"
    species.write_text("Timestep 0: CCO 3 COC 2\n", encoding="utf-8")
    SPECIES_COMPOSITION_STORE.build(str(species))

    result = svc.search_species_catalog(
        {"species": str(species)},
        "39.994915",
        kind="mass",
        mass_tolerance=0.001,
    )

    assert result["n_rows"] == 1
    assert result["rows"][0]["formula"] == "C2O"
    assert result["rows"][0]["structure_count"] == 2
    assert result["rows"][0]["total_count"] == 5
    assert result["rows"][0]["smiles"] == "CCO"


def test_reaction_network_mass_search_does_not_expand_chlorine_37(tmp_path: Path) -> None:
    reaction = tmp_path / "chlorine.reactionabcd"
    reaction.write_text("1 [Cl]+[C]->[C][Cl]\n", encoding="utf-8")

    result = svc.search_species(
        {"reaction": str(reaction)},
        "36.965902602",
        kind="mass",
        mass_tolerance=0,
    )

    chlorine_rows = [row for row in result["rows"] if row["smiles"] == "[Cl]"]
    assert chlorine_rows == []


def test_reaction_network_mass_search_groups_structures_by_formula(
    tmp_path: Path,
) -> None:
    reaction = tmp_path / "formula-groups.reactionabcd"
    c5h4o_structures = [
        "[H][C]([H])[C]([H])[C]([H])[C][C][O]",
        "[O][C][C][C]([H])[C]([H])[C]([H])[H]",
        "[C]([H])([H])[C]([H])[C]([H])[C][C][O]",
    ]
    c6h8 = (
        "[H][C]([H])[C]([H])[C]([H])[C]([H])"
        "[C]([H])[C]([H])[H]"
    )
    reaction.write_text(
        "\n".join(
            [
                f"10 {c5h4o_structures[0]}->[C]",
                f"6 {c5h4o_structures[1]}->[C]",
                f"2 {c5h4o_structures[2]}->[C]",
                f"4 {c6h8}->[C]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = svc.search_species(
        {"reaction": str(reaction)},
        "80",
        kind="mass",
        mass_tolerance=0.1,
    )

    assert [row["formula"] for row in result["rows"]] == ["C5H4O", "C6H8"]
    assert result["rows"][0]["structure_count"] == 3
    assert result["rows"][0]["smiles"] == c5h4o_structures[0]
    assert result["rows"][0]["total_throughput"] == 18
    assert result["rows"][1]["structure_count"] == 1
    assert result["n_rows"] == 2
    assert result["n_visible_rows"] == 2


def test_formula_search_returns_all_structures_without_backend_limit(
    tmp_path: Path,
) -> None:
    reaction = tmp_path / "all-structures.reactionabcd"
    structures = [
        "[H][C]([H])[C]([H])[C]([H])[C][C][O]",
        "[O][C][C][C]([H])[C]([H])[C]([H])[H]",
        "[C]([H])([H])[C]([H])[C]([H])[C][C][O]",
    ]
    reaction.write_text(
        "\n".join(
            [
                f"10 {structures[0]}->[C]",
                f"6 {structures[1]}->[C]",
                f"2 {structures[2]}->[C]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = svc.search_species(
        {"reaction": str(reaction)},
        "C5H4O",
        kind="formula",
    )

    assert [row["smiles"] for row in result["rows"]] == structures
    assert result["n_rows"] == 3
    assert result["n_visible_rows"] == 3
