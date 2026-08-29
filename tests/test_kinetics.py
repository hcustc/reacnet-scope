from __future__ import annotations

import math
from pathlib import Path

import pytest

from reacnet_scope import kinetics
from reacnet_scope import analysis_services as analysis_svc
from reacnet_scope import queries as query_svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
from reacnet_scope.kinetics import (
    AVOGADRO_ANGSTROM3_TO_LITRE,
    KineticsInputError,
    estimate_mass_action_rate,
)
from reacnet_scope.rng_events import canonical_reaction_key, reaction_key
from reacnet_scope import services as svc


def test_channel_time_conversion_can_be_confirmed_on_current_dataset(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    species = tmp_path / "run.species"
    species.write_text("Timestep 0: [H] 1\n", encoding="utf-8")
    artifacts = {"species": str(species)}

    assert svc.channel_timestep_ps(artifacts) is None
    assert svc.confirm_channel_timestep_ps(artifacts, 0.00025) == 0.00025
    assert svc.channel_timestep_ps(artifacts) == 0.00025


def test_channel_volume_source_can_link_cross_directory_trajectory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(
        analysis_svc,
        "validate_browse_path",
        lambda value: Path(value).expanduser().resolve(),
    )
    species_dir = tmp_path / "rng_data"
    trajectory_dir = tmp_path / "raw_data"
    species_dir.mkdir()
    trajectory_dir.mkdir()
    species = species_dir / "run.lammpstrj.species"
    trajectory = trajectory_dir / "run.lammpstrj"
    species.write_text("Timestep 0: [H] 1\n", encoding="utf-8")
    trajectory.write_text(_trajectory_frame(0), encoding="utf-8")
    artifacts = {"species": str(species)}

    before = svc.channel_volume_evidence(artifacts)
    linked = svc.configure_channel_volume_source(
        artifacts,
        str(trajectory),
        confirm_angstrom=True,
    )
    restored = svc.channel_volume_evidence(artifacts)

    assert before["reason"] == "missing_trajectory"
    assert linked["trajectory"] == str(trajectory.resolve())
    assert linked["source"] == "workspace_link"
    assert linked["coordinate_length_unit"] == "angstrom"
    assert linked["reason"] == "trajectory_index_not_ready"
    assert restored == linked


def test_dataset_scan_restores_linked_cross_directory_trajectory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    resolve_path = lambda value: Path(value).expanduser().resolve()
    monkeypatch.setattr(analysis_svc, "validate_browse_path", resolve_path)
    monkeypatch.setattr(query_svc, "validate_browse_path", resolve_path)
    species_dir = tmp_path / "rng_data"
    trajectory_dir = tmp_path / "raw_data"
    species_dir.mkdir()
    trajectory_dir.mkdir()
    base = species_dir / "run.lammpstrj"
    species = Path(f"{base}.species")
    reaction = Path(f"{base}.reactionabcd")
    trajectory = trajectory_dir / "run.lammpstrj"
    species.write_text("Timestep 0: [H] 1\n", encoding="utf-8")
    reaction.write_text("1 [H]->[H]\n", encoding="utf-8")
    trajectory.write_text(_trajectory_frame(0), encoding="utf-8")
    svc.configure_channel_volume_source(
        {"species": str(species)},
        str(trajectory),
    )

    status = svc.scan_dataset(str(species_dir), base=str(base))

    assert svc.artifacts_from_status(status)["trajectory"] == str(
        trajectory.resolve()
    )
    descriptor = status["dataset"]["artifacts"]["trajectory"]
    assert descriptor["source"] == "workspace_link"
    assert svc.channel_volume_evidence(
        svc.artifacts_from_status(status)
    )["source"] == "workspace_link"


@pytest.mark.parametrize("value", [None, 0, -1, float("nan")])
def test_channel_time_conversion_rejects_unconfirmed_values(
    tmp_path, value
) -> None:
    species = tmp_path / "run.species"
    species.touch()

    with pytest.raises(svc.ServiceError, match="大于 0"):
        svc.confirm_channel_timestep_ps({"species": str(species)}, value)


def test_estimates_unimolecular_apparent_rate_from_left_endpoint_exposure() -> None:
    result = estimate_mass_action_rate(
        event_count=9,
        timesteps=[0, 10, 20],
        timestep_ps=0.1,
        reactants=["A"],
        species_counts={"A": {0: 10, 10: 8, 20: 4}},
    )

    assert result["status"] == "estimated"
    assert result["reaction_order"] == 1
    assert result["observation_time_ps"] == 2.0
    assert result["event_frequency_per_ps"] == 4.5
    assert result["exposure"] == 18.0
    assert result["exposure_unit"] == "molecule·ps"
    assert result["k_app"] == 0.5
    assert result["k_app_unit"] == "ps⁻¹"
    assert result["ci95_low"] < result["k_app"] < result["ci95_high"]


def test_aligned_count_estimate_matches_mapping_estimate() -> None:
    expected = estimate_mass_action_rate(
        event_count=9,
        timesteps=[0, 10, 20],
        timestep_ps=0.1,
        reactants=["A"],
        species_counts={"A": {0: 10, 10: 8, 20: 4}},
    )

    actual = kinetics.estimate_mass_action_rate_aligned(
        event_count=9,
        timesteps=[0, 10, 20],
        timestep_ps=0.1,
        reactants=["A"],
        species_counts={"A": [10, 8, 4]},
    )

    assert actual == expected


def test_estimates_bimolecular_rate_with_volume_and_molar_units() -> None:
    result = estimate_mass_action_rate(
        event_count=6,
        timesteps=[0, 10, 20],
        timestep_ps=0.1,
        reactants=["A", "B"],
        species_counts={
            "A": {0: 10, 10: 10, 20: 10},
            "B": {0: 5, 10: 5, 20: 5},
        },
        volumes_angstrom3={0: 1000.0, 10: 1000.0, 20: 1000.0},
    )

    assert result["reaction_order"] == 2
    assert result["exposure"] == 0.1
    assert result["exposure_unit"] == "molecule²·ps·Å⁻³"
    assert math.isclose(
        result["k_app"],
        60.0 * AVOGADRO_ANGSTROM3_TO_LITRE,
    )
    assert result["k_app_unit"] == "L·mol⁻¹·ps⁻¹"


def test_repeated_reactant_uses_falling_factorial_mass_action_convention() -> None:
    result = estimate_mass_action_rate(
        event_count=3,
        timesteps=[0, 10, 20],
        timestep_ps=0.1,
        reactants=["A", "A"],
        species_counts={"A": {0: 4, 10: 3, 20: 2}},
        volumes_angstrom3={0: 1000.0, 10: 1000.0, 20: 1000.0},
    )

    # Deterministic mass-action convention: n_A(n_A-1), without a silent 1/2.
    assert result["exposure"] == pytest.approx(0.018)
    assert result["reactant_stoichiometry"] == {"A": 2}


def test_zero_events_reports_an_upper_bound_instead_of_zero_uncertainty() -> None:
    result = estimate_mass_action_rate(
        event_count=0,
        timesteps=[0, 10],
        timestep_ps=0.1,
        reactants=["A"],
        species_counts={"A": {0: 5, 10: 5}},
    )

    assert result["k_app"] == 0.0
    assert result["ci95_low"] == 0.0
    assert result["ci95_high"] > 0.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "event_count": 1,
                "timesteps": [0, 10],
                "timestep_ps": 0.1,
                "reactants": ["A", "B", "C"],
                "species_counts": {"A": {0: 1}, "B": {0: 1}, "C": {0: 1}},
            },
            "first- and second-order",
        ),
        (
            {
                "event_count": 1,
                "timesteps": [0, 10],
                "timestep_ps": 0.1,
                "reactants": ["A", "B"],
                "species_counts": {"A": {0: 1}, "B": {0: 1}},
            },
            "volume",
        ),
        (
            {
                "event_count": 1,
                "timesteps": [10, 0],
                "timestep_ps": 0.1,
                "reactants": ["A"],
                "species_counts": {"A": {10: 1}},
            },
            "strictly increasing",
        ),
    ],
)
def test_rejects_unsupported_or_incomplete_kinetic_inputs(kwargs, message) -> None:
    with pytest.raises(KineticsInputError, match=message):
        estimate_mass_action_rate(**kwargs)


def test_event_index_counts_exact_occurrences_inside_exposure_window(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent = tmp_path / "run.reactionevent.csv"
    molecules = tmp_path / "run.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[C]+[O],[C][O]\n"
        "1,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n0,[O],1,\n"
        "10,[C][O],0;1,0-1-1\n"
        "10,[C],2,\n10,[O],3,\n"
        "20,[C][O],2;3,2-3-1\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    key = canonical_reaction_key(*reaction_key("[C]+[O]", "[C][O]"))

    assert EVENT_EVIDENCE_STORE.reaction_counts(
        str(reactionevent),
        str(molecules),
        [key],
        before_timestep=0,
        after_timestep=10,
    ) == {key: 1}


def _trajectory_frame(timestep: int) -> str:
    return (
        "ITEM: TIMESTEP\n"
        f"{timestep}\n"
        "ITEM: NUMBER OF ATOMS\n1\n"
        "ITEM: BOX BOUNDS pp pp pp\n"
        "0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n"
        "1 1 1 1 1\n"
    )


def test_species_channels_include_auditable_apparent_rate_constants(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(
        analysis_svc,
        "validate_browse_path",
        lambda value: Path(value).expanduser().resolve(),
    )
    trajectory_dir = tmp_path / "raw_data"
    trajectory_dir.mkdir()
    trajectory = trajectory_dir / "run.lammpstrj"
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    species = tmp_path / "run.lammpstrj.species"
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    trajectory.write_text(
        "".join(_trajectory_frame(value) for value in (0, 10, 20)),
        encoding="utf-8",
    )
    reaction.write_text("2 [C]+[O]->[C][O]\n", encoding="utf-8")
    species.write_text(
        "Timestep 0: [C] 10 [O] 5 [C][O] 0\n"
        "Timestep 10: [C] 10 [O] 5 [C][O] 1\n"
        "Timestep 20: [C] 10 [O] 5 [C][O] 2\n",
        encoding="utf-8",
    )
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[C]+[O],[C][O]\n"
        "1,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n0,[O],1,\n"
        "10,[C][O],0;1,0-1-1\n"
        "10,[C],2,\n10,[O],3,\n"
        "20,[C][O],2;3,2-3-1\n",
        encoding="utf-8",
    )
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    SPECIES_COMPOSITION_STORE.build(str(species))
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    svc.save_timestep_ps(str(species), 0.1)
    svc.configure_channel_volume_source(
        {"species": str(species)},
        str(trajectory),
        confirm_angstrom=True,
    )

    result = svc.collect_species_channels(
        {
            "reaction": str(reaction),
            "species": str(species),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        "[C][O]",
        top=10,
    )

    row = result["production_rows"][0]
    assert result["kinetics"]["status"] == "estimated"
    assert row["event_count"] == 2
    assert row["event_frequency_per_ps"] == 1.0
    assert row["k_app_unit"] == "L·mol⁻¹·ps⁻¹"
    assert row["k_app"] == pytest.approx(20 * AVOGADRO_ANGSTROM3_TO_LITRE)
    assert row["kinetic_model"] == "stoichiometric_mass_action"


def test_species_channels_explain_missing_simulation_box_by_direction(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    species = tmp_path / "run.lammpstrj.species"
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reaction.write_text("1 [C]+[O]->[C][O]\n", encoding="utf-8")
    species.write_text(
        "Timestep 0: [C] 2 [O] 2 [C][O] 1\n"
        "Timestep 10: [C] 1 [O] 1 [C][O] 1\n",
        encoding="utf-8",
    )
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[C],0,\n0,[O],1,\n"
        "10,[C][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    SPECIES_COMPOSITION_STORE.build(str(species))
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    svc.save_timestep_ps(str(species), 0.1)

    result = svc.collect_species_channels(
        {
            "reaction": str(reaction),
            "species": str(species),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        "[C][O]",
        top=10,
    )

    production = result["production_rows"][0]
    assert production["k_app"] is None
    assert production["kinetics_reason"] == "missing_trajectory"
    assert production["kinetics_reason_message"].startswith(
        "缺少用于双分子表观 k"
    )
    assert production["reverse_k_app_unit"] == "ps⁻¹"
    assert production["reverse_kinetics_reason"] == ""
    assert result["kinetics"]["reason_counts"]["missing_trajectory"] >= 1
    assert "双分子通道缺少关联的 .lammpstrj" in result["kinetics"][
        "message"
    ]
    assert "晶胞" not in result["kinetics"]["message"]


def test_species_channels_can_defer_kinetics_for_interactive_first_paint(
    tmp_path,
    monkeypatch,
) -> None:
    reaction = tmp_path / "run.lammpstrj.reactionabcd"
    reaction.write_text("2 [C]+[O]->[C][O]\n", encoding="utf-8")

    def reject_sync_kinetics(*_args, **_kwargs):
        raise AssertionError("interactive channel load attempted kinetics")

    monkeypatch.setattr(
        analysis_svc,
        "_enrich_channel_kinetics",
        reject_sync_kinetics,
    )

    result = svc.collect_species_channels(
        {"reaction": str(reaction)},
        "[C][O]",
        top=10,
        include_kinetics=False,
    )

    assert result["production_rows"]
    assert result["kinetics"]["status"] == "deferred"
    assert result["production_rows"][0]["kinetics_reason"] == "deferred"
