from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from reacnet_scope.composition import (
    SPECIES_COMPOSITION_STORE,
    build_element_distribution_model,
)
from reacnet_scope.indexes import IndexInvalidError, IndexStaleError
from reacnet_scope import services as svc


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


def test_invalid_available_elements_metadata_is_reported_as_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = _species_file(tmp_path)
    built = SPECIES_COMPOSITION_STORE.build(str(species))
    import sqlite3

    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute(
            "UPDATE meta SET value=? WHERE key='available_elements'",
            ("not-json",),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError, match="available elements"):
        SPECIES_COMPOSITION_STORE.open_required(str(species))
    assert SPECIES_COMPOSITION_STORE.status(str(species))["state"] == "invalid"


def test_source_change_during_composition_build_is_not_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = _species_file(tmp_path)
    changed = False

    def mutate_source(_update: dict[str, object]) -> None:
        nonlocal changed
        if not changed:
            species.write_text(
                species.read_text(encoding="utf-8") + "Timestep 300: [N] 1\n",
                encoding="utf-8",
            )
            changed = True

    with pytest.raises(IndexStaleError, match="source changed"):
        SPECIES_COMPOSITION_STORE.build(
            str(species), progress_callback=mutate_source
        )
    assert not Path(
        SPECIES_COMPOSITION_STORE.status(str(species))["index_path"]
    ).is_file()


def test_element_distribution_index_streams_and_queries_selected_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    species = _species_file(tmp_path)

    built = SPECIES_COMPOSITION_STORE.build(str(species))
    assert built["timepoints"] == 3

    result = SPECIES_COMPOSITION_STORE.query(
        str(species),
        max_points=10,
        group_element="C",
        max_group_count=6,
    )
    groups = {(row["timestep"], row["group"]): row["count"] for row in result["rows"]}
    assert groups[(0, "C6")] == 8
    assert groups[(200, "C1")] == 7
    assert (200, "C0") not in groups
    snapshot = SPECIES_COMPOSITION_STORE.snapshot(str(species), 100)
    assert next(row for row in snapshot["records"] if row["smiles"] == PARENT)["count"] == 6
    assert SPECIES_COMPOSITION_STORE.species_count_series(str(species), [0, 100, 200], PARENT) == {
        0: 8,
        100: 6,
        200: 0,
    }
    detail = SPECIES_COMPOSITION_STORE.query_species_summary(
        str(species),
        group_element="C",
        group_count=1,
        current_timestep=200,
        element_filters={"O": {"mode": "present"}},
    )
    by_smiles = {row["smiles"]: row for row in detail["rows"]}
    assert by_smiles["[O]=[C]=[O]"]["current_count"] == 4
    assert by_smiles["[O]=[C]=[O]"]["peak_count"] == 4
    assert by_smiles["[O]=[C]=[O]"]["peak_timestep"] == 200
    assert by_smiles["[C][O]"]["current_count"] == 3


def test_element_distribution_index_discovers_and_groups_arbitrary_elements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "nitrogen-sulfur.lammpstrj.species"
    species.write_text(
        "Timestep 0: [N]#[N] 2 [S] 3 [N][S] 4\n"
        "Timestep 10: [N]#[N] 1 [S] 5 [N][S] 6\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))

    result = SPECIES_COMPOSITION_STORE.query(
        str(species),
        group_element="N",
        max_group_count=2,
        element_filters={"S": {"mode": "present"}},
        include_zero=True,
        max_points=10,
    )

    assert result["meta"]["available_elements"] == ["N", "S"]
    groups = {
        (row["timestep"], row["group"]): row["count"]
        for row in result["rows"]
    }
    assert groups == {
        (0, "N0"): 3,
        (0, "N1"): 4,
        (10, "N0"): 5,
        (10, "N1"): 6,
    }
    with pytest.raises(ValueError, match="minimum exceeds maximum"):
        SPECIES_COMPOSITION_STORE.query(
            str(species),
            group_element="N",
            element_filters={"S": {"mode": "range", "min": 3, "max": 1}},
        )


def test_element_distribution_service_uses_selected_element_and_filters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "nitrogen-sulfur.lammpstrj.species"
    species.write_text(
        "Timestep 0: [N]#[N] 2 [S] 3 [N][S] 4\n"
        "Timestep 10: [N]#[N] 1 [S] 5 [N][S] 6\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        group_element="N",
        max_group_count=2,
        element_filters={"S": {"mode": "present"}},
        include_zero=True,
        x_axis="step",
    )

    assert payload["summary"]["group_element"] == "N"
    assert payload["meta"]["available_elements"] == ["N", "S"]
    assert "carbon_skeleton_rows" not in payload
    assert {
        (row["timestep"], row["series"]): row["count"]
        for row in payload["distribution_rows"]
    } == {
        (0, "N0"): 3,
        (0, "N1"): 4,
        (10, "N0"): 5,
        (10, "N1"): 6,
    }


def test_element_distribution_defaults_to_timestep_without_confirmed_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = _species_file(tmp_path)
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        group_element="C",
    )

    assert payload["x_name"] == "Timestep"
    assert payload["summary"]["timestep_ps"] is None
    assert {row["x"] for row in payload["distribution_rows"]} == {0, 100, 200}


def test_generic_distribution_model_reuses_multi_tidy_bins_ranges_and_smoothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = tmp_path / "first.species"
    second = tmp_path / "second.species"
    first.write_text("Timestep 0: [N][N] 2\nTimestep 10: [N][N] 4\n", encoding="utf-8")
    second.write_text("Timestep 0: [N][N][N] 3\n", encoding="utf-8")
    for source in (first, second):
        SPECIES_COMPOSITION_STORE.build(str(source))
    tidy = tmp_path / "extra.csv"
    tidy.write_text(
        "time,species,count,dataset\n0,[N][N][N][N],5,third\n",
        encoding="utf-8",
    )

    model = build_element_distribution_model(
        species_files={"first": str(first), "second": str(second)},
        tidy_table=str(tidy),
        group_element="N",
        bin_width=2,
        group_ranges=[{"label": "large", "min": 4, "max": None}],
        smooth_window=2,
    )

    assert {source["source_mode"] for source in model["sources"]} == {
        "prepared_index",
        "tidy_table",
    }
    assert {row["dataset"] for row in model["raw_rows"]} == {
        "first",
        "second",
        "third",
    }
    assert next(row for row in model["raw_rows"] if row["dataset"] == "third")["group"] == "large"
    smoothed = [row for row in model["rows"] if row["dataset"] == "first"]
    assert [row["count"] for row in smoothed] == [2.0, 3.0]


def test_installed_element_distribution_cli_uses_generic_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(workspace))
    species = tmp_path / "nitrogen-sulfur.lammpstrj.species"
    species.write_text(
        "Timestep 0: [N]#[N] 2 [S] 3 [N][S] 4\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))
    environment = os.environ.copy()
    executable = Path(sys.executable).with_name("reacnet-scope")

    completed = subprocess.run(
        [
            str(executable),
            "element-distribution",
            str(tmp_path),
            "--base",
            "nitrogen-sulfur.lammpstrj",
            "--group-element",
            "N",
            "--max-group-count",
            "2",
            "--filter",
            "S=present",
            "--include-zero",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["group_element"] == "N"
    assert payload["element_filters"] == {"S": {"mode": "present"}}
    assert {(row["group"], row["count"]) for row in payload["rows"]} == {
        ("N0", 3),
        ("N1", 4),
    }


def test_composition_service_builds_filtered_series_and_drilldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    species = _species_file(tmp_path)
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        x_axis="ps",
        timestep_ps=0.001,
        group_element="C",
        max_group_count=6,
        reference_smiles=PARENT,
    )

    assert payload["view"] == "element-distribution"
    assert payload["summary"]["reference_group"] == "C6"
    assert payload["summary"]["reference_smiles"] == PARENT
    assert payload["meta"]["source_timepoints"] == 3
    reference_series = next(
        row
        for row in payload["distribution_rows"]
        if row["series"] == "参考物种" and row["timestep"] == 200
    )
    c1_series = next(
        row
        for row in payload["distribution_rows"]
        if row["series"] == "C1" and row["timestep"] == 200
    )
    assert reference_series["count"] == 0
    assert c1_series["count"] == 7

    detail = svc.build_element_distribution_species_drilldown(
        payload,
        series="C1",
        timestep=200,
    )
    assert detail["current_time"] == pytest.approx(0.2)
    assert detail["rows"][0]["current_count"] == 4
    assert detail["rows"][0]["peak_count"] == 4
    assert detail["rows"][0]["peak_time"] == pytest.approx(0.2)

    chlorinated = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        x_axis="ps",
        timestep_ps=0.001,
        group_element="C",
        max_group_count=6,
        element_filters={
            "Cl": {"mode": "present"},
            "O": {"mode": "present"},
        },
        reference_smiles=PARENT,
    )
    assert not any(
        row["series"] == "C1"
        for row in chlorinated["distribution_rows"]
    )


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
        x_axis="ps",
        timestep_ps=0.002,
        group_element="C",
        max_group_count=3,
    )
    names = {row["series"] for row in without_reference["distribution_rows"]}
    assert "参考物种" not in names
    assert not any(name.endswith("其他物种") for name in names)
    assert without_reference["summary"]["reference_smiles"] == ""

    with_reference = svc.build_elemental_composition_evolution(
        {"species": str(species)},
        x_axis="ps",
        timestep_ps=0.002,
        group_element="C",
        max_group_count=3,
        reference_smiles=reference,
    )
    reference_at_100 = next(
        row
        for row in with_reference["distribution_rows"]
        if row["series"] == "参考物种" and row["timestep"] == 100
    )
    other_c2_at_100 = next(
        row
        for row in with_reference["distribution_rows"]
        if row["series"] == "C2 其他物种" and row["timestep"] == 100
    )
    assert reference_at_100["count"] == 1
    assert reference_at_100["x"] == pytest.approx(0.2)
    assert other_c2_at_100["count"] == 3
    assert with_reference["summary"]["reference_smiles"] == reference
    assert with_reference["summary"]["reference_group_count"] == 2

    reference_detail = svc.build_element_distribution_species_drilldown(
        with_reference,
        series="参考物种",
        timestep=100,
    )
    assert reference_detail["current_time"] == pytest.approx(0.2)
    assert reference_detail["rows"][0]["smiles"] == reference

    with pytest.raises(svc.ServiceError, match="Timestep 换算必须是正数"):
        svc.build_elemental_composition_evolution(
            {"species": str(species)},
            x_axis="ps",
            timestep_ps=0,
            group_element="C",
            max_group_count=3,
        )
