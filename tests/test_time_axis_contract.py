from __future__ import annotations

import csv
import io
import json

import pytest

from reacnet_scope import services as svc
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE


def _write_species(path) -> None:
    path.write_text(
        "Timestep 0: C 0\n"
        "Timestep 10: C 10\n"
        "Timestep 20: C 0\n",
        encoding="utf-8",
    )


def test_species_evolution_defaults_to_timestep_and_persists_explicit_conversion(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "run.species"
    _write_species(species)
    SPECIES_COMPOSITION_STORE.build(str(species))
    artifacts = {"species": str(species)}

    step_payload = svc.build_species_evolution(
        artifacts,
        ["smiles:C"],
    )

    assert step_payload["x_name"] == "timestep"
    assert step_payload["x_values"] == [0.0, 10.0, 20.0]

    with pytest.raises(svc.ServiceError, match="必须为每个数据集分别确认"):
        svc.build_species_evolution(
            artifacts,
            ["smiles:C"],
            x_axis="ps",
        )

    confirmed = svc.build_species_evolution(
        artifacts,
        ["smiles:C"],
        x_axis="ps",
        timestep_ps=0.002,
    )
    persisted = svc.build_species_evolution(
        artifacts,
        ["smiles:C"],
        x_axis="ps",
    )

    assert confirmed["x_values"] == [0.0, 0.02, 0.04]
    assert persisted["x_values"] == confirmed["x_values"]


def test_intermediate_candidates_use_analyzed_frames_without_conversion(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "candidate.species"
    _write_species(species)
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_intermediate_candidates(
        {"species": str(species)},
        with_flux=False,
        fwhm_min_frames=1,
    )

    assert payload["meta"]["time_axis"] == "analyzed_frame"
    assert payload["meta"]["dt_ps"] is None
    assert payload["rows"][0]["fwhm_frames"] == 1
    assert payload["rows"][0]["peak_analyzed_frame"] == 1.0
    assert payload["rows"][0]["fwhm_ps"] is None
    assert payload["rows"][0]["peak_time_ps"] is None

    physical = svc.build_intermediate_candidates(
        {"species": str(species)},
        timestep_ps=0.002,
        product_ratio_min=0.96,
        reactant_start_ratio_min=0.91,
        fwhm_min_frames=1,
    )
    assert physical["rows"][0]["peak_time_ps"] == 0.02
    assert physical["meta"]["flux_enrichment"] == {
        "requested": True,
        "available": False,
        "applied": False,
        "reason": "reaction_network_missing",
    }
    assert physical["rule_version"] == "intermediate-classification/v1"
    assert physical["scoring_version"] == "intermediate-score/v1"
    exported = svc.intermediate_candidates_to_csv(physical)
    exported_row = next(csv.DictReader(io.StringIO(exported)))
    exported_query = json.loads(exported_row["query_parameters_json"])
    assert exported_row["rule_version"] == "intermediate-classification/v1"
    assert exported_query["product_ratio_min"] == 0.96
    assert exported_query["reactant_start_ratio_min"] == 0.91


def test_species_evolution_csv_keeps_raw_values_and_both_coordinates(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "raw-export.species"
    _write_species(species)
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_species_evolution(
        {"species": str(species)},
        ["smiles:C"],
        normalize="max",
        smooth_window=3,
        downsample=2,
    )
    exported = svc.evolution_to_csv(payload).splitlines()

    assert payload["meta"]["source_mode"] == "prepared_index"
    assert payload["meta"]["n_timestep_full"] == 3
    assert len(payload["x_values"]) == 2
    assert exported == [
        "analyzed_frame,source_timestep,C",
        "0,0,0.0",
        "1,10,10.0",
        "2,20,0.0",
    ]


def test_multi_dataset_evolution_csv_keeps_each_source_coordinates(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    first = tmp_path / "first.species"
    second = tmp_path / "second.species"
    first.write_text("Timestep 0: C 1\nTimestep 10: C 2\n", encoding="utf-8")
    second.write_text("Timestep 5: C 3\nTimestep 20: C 4\n", encoding="utf-8")
    for source in (first, second):
        SPECIES_COMPOSITION_STORE.build(str(source))

    with pytest.raises(svc.ServiceError, match="不能批量套用"):
        svc.build_species_evolution(
            {},
            ["smiles:C"],
            species_files=f"first::{first}\nsecond::{second}",
            x_axis="ps",
            timestep_ps=0.002,
        )

    payload = svc.build_species_evolution(
        {},
        ["smiles:C"],
        species_files=f"first::{first}\nsecond::{second}",
        downsample=0,
    )
    exported = svc.evolution_to_csv(payload)

    assert "first,,C,0,0,1.0" in exported
    assert "first,,C,1,10,2.0" in exported
    assert "second,,C,0,5,3.0" in exported
    assert "second,,C,1,20,4.0" in exported


def test_intermediate_uses_index_position_for_irregular_analyzed_frames(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    species = tmp_path / "irregular.species"
    species.write_text(
        "Timestep 5: C 0\n"
        "Timestep 10: C 2\n"
        "Timestep 30: C 9\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))

    payload = svc.build_intermediate_candidates(
        {"species": str(species)},
        kind="all",
        with_flux=False,
        require_fwhm=False,
        timestep_ps=0.002,
    )

    assert payload["rows"][0]["peak_analyzed_frame"] == 2
    assert payload["rows"][0]["peak_time_ps"] == 0.06
    assert payload["rows"][0]["fwhm_ps"] is None
    assert payload["meta"]["dt_ps"] is None
