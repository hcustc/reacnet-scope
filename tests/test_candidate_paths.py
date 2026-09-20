from __future__ import annotations

import csv
from pathlib import Path

import pytest

import reacnet_scope
from reacnet_scope import event_paths as event_paths_module
from reacnet_scope.candidate_paths import (
    CANDIDATE_PATH_SCHEMA_VERSION,
    EnergyEvidence,
    discover_network_candidate_routes,
    load_energy_evidence_csv,
    rank_candidate_paths,
)
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.network import Reaction, ReactionNetwork
from reacnet_scope import services as svc


def _event_report() -> dict:
    return {
        "summary": {
            "replicate_count": 2,
            "actual_path_occurrence_count": 6,
            "actual_path_signature_count": 2,
            "statistics_complete": True,
            "traversal_truncated": False,
        },
        "sources": [{"replicate": "rep1"}, {"replicate": "rep2"}],
        "paths": [
            {
                "signature_id": "fast",
                "reaction_keys": ["CCO->CC=O", "CC=O->CC(=O)O"],
                "occurrence_count": 5,
                "independent_atom_lineage_support_count": 5,
                "replicate_support_count": 2,
                "replicate_reproduction_rate": 1.0,
                "interval_gap_by_edge": [{"median": 1}],
                "interval_span": {"median": 1},
                "support_is_lower_bound": False,
            },
            {
                "signature_id": "slow",
                "reaction_keys": ["CCO->CC=O", "CC=O->CC#N"],
                "occurrence_count": 1,
                "independent_atom_lineage_support_count": 1,
                "replicate_support_count": 1,
                "replicate_reproduction_rate": 0.5,
                "interval_gap_by_edge": [{"median": 4}],
                "interval_span": {"median": 4},
                "support_is_lower_bound": False,
            },
        ],
    }


def _network() -> ReactionNetwork:
    return ReactionNetwork(
        [
            Reaction(("CCO",), ("CC=O",), 20),
            Reaction(("CC=O",), ("CC(=O)O",), 10),
            Reaction(("CC=O",), ("CC#N",), 2),
        ]
    )


def test_ranked_paths_are_sampled_multi_metric_and_deterministic() -> None:
    first = rank_candidate_paths(_network(), _event_report(), ["CCO", "O"])
    second = rank_candidate_paths(_network(), _event_report(), ["O", "CCO"])

    assert first == second
    assert first["schema_version"] == CANDIDATE_PATH_SCHEMA_VERSION
    assert first["energy_status"] == "not_provided"
    assert [path["signature_id"] for path in first["paths"]] == ["fast", "slow"]
    best = first["paths"][0]
    assert best["species"] == ("CCO", "CC=O", "CC(=O)O")
    assert best["occurrence_count"] == 5
    assert best["temporal_score"] == 1.0
    assert best["continuity_score"] == 1.0
    assert best["steps"][0]["reactants"] == ("CCO",)
    assert best["steps"][0]["products"] == ("CC=O",)
    assert 0 <= best["structure_score"] <= 1
    assert 0 <= best["score"] <= 1
    assert reacnet_scope.rank_candidate_paths is rank_candidate_paths
    assert reacnet_scope.discover_event_paths is not None


def test_energy_evidence_is_explicit_and_reports_partial_coverage() -> None:
    result = rank_candidate_paths(
        _network(),
        _event_report(),
        ["CCO"],
        energy_evidence={
            "CCO->CC=O": EnergyEvidence(
                score=0.8,
                delta_energy=-0.4,
                barrier=0.2,
                unit="eV",
                source="thermo-analysis.csv",
            )
        },
    )

    assert result["energy_status"] == "provided"
    best = result["paths"][0]
    assert best["energy_coverage"] == 0.5
    assert best["energy_score"] == pytest.approx(0.4)
    assert best["steps"][0]["delta_energy"] == -0.4
    assert best["steps"][1]["energy_score"] is None

    unmatched = rank_candidate_paths(
        _network(),
        _event_report(),
        ["CCO"],
        energy_evidence={"N->O": EnergyEvidence(score=1.0)},
    )
    assert unmatched["paths"][0]["energy_score"] == 0.0
    assert unmatched["paths"][0]["energy_coverage"] == 0.0


def test_energy_csv_requires_user_normalized_scores(tmp_path: Path) -> None:
    path = tmp_path / "energy.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["reaction_key", "score", "delta_energy", "barrier", "unit"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "reaction_key": "CCO -> CC=O",
                "score": "0.75",
                "delta_energy": "-0.2",
                "barrier": "0.3",
                "unit": "eV",
            }
        )

    result = load_energy_evidence_csv(path)

    assert result["CCO->CC=O"].score == 0.75
    assert result["CCO->CC=O"].source == str(path.resolve())


def test_paths_not_starting_from_requested_species_are_excluded() -> None:
    result = rank_candidate_paths(_network(), _event_report(), ["N"])

    assert result["paths"] == []
    assert result["path_count"] == 0


def test_network_candidate_search_honors_expansion_cap_before_deeper_work() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B",), ("C",), 9),
            Reaction(("C",), ("D",), 8),
        ]
    )

    result = discover_network_candidate_routes(
        network,
        ["A"],
        minimum_path_length=2,
        maximum_path_length=3,
        max_expansions=1,
    )

    assert result["paths"] == []
    assert result["summary"]["expansions"] == 1
    assert result["summary"]["traversal_truncated"] is True


def test_dataset_service_discovers_only_indexed_step_evidence_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base = tmp_path / "run.lammpstrj"
    reaction = Path(f"{base}.reactionabcd")
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reaction.write_text("8 A->B\n6 B->C\n4 C->D\n", encoding="utf-8")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,A,B\n1,B,C\n2,C,D\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,A,0,\n10,B,0,\n20,C,0,\n30,D,0,\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    result = svc.discover_candidate_paths_for_dash(
        {
            "reaction": str(reaction),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        "missing\nA",
        minimum_path_length=2,
        maximum_path_length=3,
    )

    assert result["path_count"] == 2
    assert {
        tuple(path["reaction_keys"])
        for path in result["paths"]
    } == {
        ("A->B", "B->C"),
        ("A->B", "B->C", "C->D"),
    }
    assert all(path["occurrence_count"] == 1 for path in result["paths"])
    assert all(
        path["minimum_step_occurrence_count"] == 1
        for path in result["paths"]
    )
    assert all(
        [step["reaction_key"] for step in path["step_evidence"]]
        == list(path["reaction_keys"])
        for path in result["paths"]
    )


def test_candidate_discovery_never_builds_the_global_event_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate discovery must stay on bounded network/index lookups."""
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    base = tmp_path / "run.lammpstrj"
    reaction = Path(f"{base}.reactionabcd")
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reaction.write_text("8 A->B\n6 B->C\n", encoding="utf-8")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,A,B\n1,B,C\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,A,0,\n10,B,0,\n20,C,0,\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    def reject_global_event_graph(*_args, **_kwargs):
        raise AssertionError("candidate discovery attempted a global event scan")

    monkeypatch.setattr(
        event_paths_module,
        "_load_event_nodes",
        reject_global_event_graph,
    )

    result = svc.discover_candidate_paths_for_dash(
        {
            "reaction": str(reaction),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        "A",
        minimum_path_length=2,
        maximum_path_length=2,
        max_expansions=10,
    )

    assert [tuple(path["reaction_keys"]) for path in result["paths"]] == [
        ("A->B", "B->C")
    ]
