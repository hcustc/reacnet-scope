from __future__ import annotations

from pathlib import Path

import pytest

from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from scripts.webapp_dash import services as svc


PARENT = "[H][O][C]1[C]([H])=[C]([H])[C]([H])=[C]([H])[C]=1[Cl]"


def _species_file(path: Path) -> Path:
    species = path / "oxidation.lammpstrj.species"
    species.write_text(
        f"Timestep 0: {PARENT} 8 [O]=[O] 52\n"
        f"Timestep 100: {PARENT} 6 [O]=[O] 48 [C][O] 2 [H][Cl] 1\n"
        "Timestep 200: [O]=[C]=[O] 4 [C][O] 3 [H][Cl] 5 [C][C][O] 2\n",
        encoding="utf-8",
    )
    return species


def test_composition_index_streams_and_queries_co_cl_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    species = _species_file(tmp_path)

    built = SPECIES_COMPOSITION_STORE.build(str(species))
    assert built["timepoints"] == 3

    result = SPECIES_COMPOSITION_STORE.query(
        str(species),
        max_points=10,
        max_carbon=6,
        max_oxygen=4,
        chlorine_mode="binary",
    )
    groups = {(row["timestep"], row["group"]): row["count"] for row in result["rows"]}
    assert groups[(0, "C6O1Cl1")] == 8
    assert groups[(200, "C1O2Cl0")] == 4
    assert groups[(200, "C0O0Cl1")] == 5
    markers = {(row["timestep"], row["formula"]): row["count"] for row in result["marker_rows"]}
    assert markers[(200, "CO2")] == 4
    assert markers[(200, "HCl")] == 5
    snapshot = SPECIES_COMPOSITION_STORE.snapshot(str(species), 100)
    assert next(row for row in snapshot["records"] if row["smiles"] == PARENT)["count"] == 6
    assert SPECIES_COMPOSITION_STORE.species_count_series(str(species), [0, 100, 200], PARENT) == {
        0: 8,
        100: 6,
        200: 0,
    }
    detail = SPECIES_COMPOSITION_STORE.query_species_summary(
        str(species),
        carbon=1,
        current_timestep=200,
        oxygen_state="oxygenated",
    )
    by_smiles = {row["smiles"]: row for row in detail["rows"]}
    assert by_smiles["[O]=[C]=[O]"]["current_count"] == 4
    assert by_smiles["[O]=[C]=[O]"]["peak_count"] == 4
    assert by_smiles["[O]=[C]=[O]"]["peak_timestep"] == 200
    assert by_smiles["[C][O]"]["current_count"] == 3


def test_composition_service_builds_filtered_series_and_drilldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    species = _species_file(tmp_path)
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        x_axis="ps",
        timestep_ps=0.001,
        max_carbon=6,
        reference_smiles=PARENT,
    )

    assert payload["view"] == "composition"
    assert payload["summary"]["reference_group"] == "C6O1Cl1"
    assert payload["summary"]["reference_smiles"] == PARENT
    assert payload["meta"]["source_timepoints"] == 3
    reference_series = next(
        row
        for row in payload["carbon_skeleton_rows"]
        if row["series"] == "参考物种" and row["timestep"] == 200
    )
    c1_series = next(
        row
        for row in payload["carbon_skeleton_rows"]
        if row["series"] == "C1" and row["timestep"] == 200
    )
    assert reference_series["count"] == 0
    assert c1_series["count"] == 7

    detail = svc.build_carbon_species_drilldown(payload, series="C1", timestep=200)
    assert detail["current_time"] == pytest.approx(0.2)
    assert detail["rows"][0]["current_count"] == 4
    assert detail["rows"][0]["peak_count"] == 4
    assert detail["rows"][0]["peak_time"] == pytest.approx(0.2)

    chlorinated = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        x_axis="ps",
        timestep_ps=0.001,
        max_carbon=6,
        chlorine_state="chlorinated",
        oxygen_state="oxygenated",
        reference_smiles=PARENT,
    )
    chlorinated_c1 = next(
        row
        for row in chlorinated["carbon_skeleton_rows"]
        if row["series"] == "C1" and row["timestep"] == 200
    )
    assert chlorinated_c1["count"] == 0


def test_reference_species_is_optional_and_never_inferred_from_abundance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    species = tmp_path / "generic.lammpstrj.species"
    reference = "[C][C]"
    species.write_text(
        f"Timestep 0: [C][O] 100 {reference} 2\n"
        f"Timestep 100: [C][O] 80 {reference} 1 [C][C][O] 3\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))

    without_reference = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        timestep_ps=0.002,
        max_carbon=3,
    )
    names = {row["series"] for row in without_reference["carbon_skeleton_rows"]}
    assert "参考物种" not in names
    assert not any(name.endswith("其他物种") for name in names)
    assert without_reference["summary"]["reference_smiles"] == ""

    with_reference = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        timestep_ps=0.002,
        max_carbon=3,
        reference_smiles=reference,
    )
    reference_at_100 = next(
        row
        for row in with_reference["carbon_skeleton_rows"]
        if row["series"] == "参考物种" and row["timestep"] == 100
    )
    other_c2_at_100 = next(
        row
        for row in with_reference["carbon_skeleton_rows"]
        if row["series"] == "C2 其他物种" and row["timestep"] == 100
    )
    assert reference_at_100["count"] == 1
    assert reference_at_100["x"] == pytest.approx(0.2)
    assert other_c2_at_100["count"] == 3
    assert with_reference["summary"]["reference_smiles"] == reference
    assert with_reference["summary"]["reference_carbon"] == 2

    reference_detail = svc.build_carbon_species_drilldown(
        with_reference,
        series="参考物种",
        timestep=100,
    )
    assert reference_detail["current_time"] == pytest.approx(0.2)
    assert reference_detail["rows"][0]["smiles"] == reference

    with pytest.raises(svc.ServiceError, match="Timestep 必须是正数"):
        svc.build_elemental_composition_evolution(
            {"species": str(species)},
            timestep_ps=0,
            max_carbon=3,
        )
