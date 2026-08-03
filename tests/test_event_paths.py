from __future__ import annotations

import json
from pathlib import Path

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.event_paths import (
    EVENT_PATH_SCHEMA_VERSION,
    EventPathAnalysisError,
    EventPathSource,
    analyze_event_paths,
    enumerate_aggregate_reaction_paths,
)
from reacnet_scope.network import Reaction
from scripts import rng_query_cli as cli


def _write_chain_source(
    folder: Path,
    name: str,
    atom_ids: tuple[int, ...],
) -> EventPathSource:
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / name
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reaction_file = Path(f"{base}.reactionabcd")
    event_lines = ["Timestep_Index,Reactant,Product"]
    for interval, (left, right) in enumerate(
        (("A", "B"), ("B", "C"), ("C", "D"))
    ):
        event_lines.extend(
            f"{interval},{left},{right}" for _atom_id in atom_ids
        )
    reactionevent.write_text("\n".join(event_lines) + "\n", encoding="utf-8")

    molecule_lines = ["Timestep,Species,AtomIDs,BondIDs"]
    for timestep, species in ((0, "A"), (10, "B"), (20, "C"), (30, "D")):
        molecule_lines.extend(
            f"{timestep},{species},{atom_id}," for atom_id in atom_ids
        )
    molecules.write_text("\n".join(molecule_lines) + "\n", encoding="utf-8")
    reaction_file.write_text(
        "5 A->B\n"
        "2 X->B\n"
        "5 B->C\n"
        "5 C->D\n"
        "2 C->Z\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    return EventPathSource(
        replicate=name,
        reactionevent_file=str(reactionevent),
        molecules_file=str(molecules),
        reaction_file=str(reaction_file),
    )


def _write_mismatched_instance_source(folder: Path) -> EventPathSource:
    base = folder / "mismatched"
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reaction_file = Path(f"{base}.reactionabcd")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,A,B\n"
        "1,B,C\n"
        "2,C,D\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,A,0,\n"
        "0,B,1,\n"
        "10,B,0,\n"
        "10,B,1,\n"
        "20,B,0,\n"
        "20,C,1,\n"
        "30,B,0,\n"
        "30,D,1,\n",
        encoding="utf-8",
    )
    reaction_file.write_text(
        "1 A->B\n1 B->C\n1 C->D\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    return EventPathSource(
        replicate="mismatched",
        reactionevent_file=str(reactionevent),
        molecules_file=str(molecules),
        reaction_file=str(reaction_file),
    )


def _write_recrossing_source(folder: Path) -> EventPathSource:
    base = folder / "recrossing"
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,A,B\n"
        "1,B,C\n"
        "2,C,B\n"
        "3,B,D\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,A,0,\n"
        "10,B,0,\n"
        "20,C,0,\n"
        "30,B,0,\n"
        "40,D,0,\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    return EventPathSource(
        replicate="recrossing",
        reactionevent_file=str(reactionevent),
        molecules_file=str(molecules),
    )


def _write_unresolved_barrier_source(folder: Path) -> EventPathSource:
    base = folder / "unresolved-barrier"
    reactionevent = Path(f"{base}.reactionevent.csv")
    molecules = Path(f"{base}.molecules.csv")
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,A,B\n"
        "1,B,Q\n"
        "2,B,C\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,A,0,\n"
        "10,B,0,\n"
        "20,B,0,\n"
        "30,C,0,\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    return EventPathSource(
        replicate="unresolved-barrier",
        reactionevent_file=str(reactionevent),
        molecules_file=str(molecules),
    )


def test_event_paths_are_concrete_time_ordered_and_atom_continuous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    rep1 = _write_chain_source(tmp_path / "rep1", "rep1", (0, 1))
    rep2 = _write_chain_source(tmp_path / "rep2", "rep2", (0,))

    report = analyze_event_paths([rep1, rep2])

    assert report["schema_version"] == EVENT_PATH_SCHEMA_VERSION
    assert report["summary"] == {
        "replicate_count": 2,
        "actual_path_occurrence_count": 3,
        "actual_path_signature_count": 1,
        "independent_atom_lineage_support_count": 3,
        "statistics_complete": True,
        "traversal_truncated": False,
    }
    assert len(report["occurrences"]) == 3
    assert all(len(item["events"]) == 3 for item in report["occurrences"])
    assert all(len(item["edges"]) == 2 for item in report["occurrences"])
    assert all(item["lineage_atom_ids"] for item in report["occurrences"])
    for occurrence in report["occurrences"]:
        event_times = [event["timestep_index"] for event in occurrence["events"]]
        assert event_times == sorted(event_times)
        assert len(set(event_times)) == 3
        assert [edge["interval_gap"] for edge in occurrence["edges"]] == [1, 1]
        assert [edge["idle_timestep_gap"] for edge in occurrence["edges"]] == [0, 0]
        assert [edge["anchor_timestep_gap"] for edge in occurrence["edges"]] == [10, 10]

    path = report["paths"][0]
    assert path["reaction_keys"] == ["A->B", "B->C", "C->D"]
    assert path["occurrence_count"] == 3
    assert path["independent_atom_lineage_support_count"] == 3
    assert path["independent_lineage_set_support_count"] == 3
    assert path["supporting_replicates"] == ["rep1", "rep2"]
    assert path["replicate_reproduction_rate"] == 1.0
    assert path["anchor_timestep_gap_by_edge"] == [
        {
            "edge_index": 1,
            "count": 3,
            "min": 10,
            "median": 10,
            "mean": 10.0,
            "max": 10,
        },
        {
            "edge_index": 2,
            "count": 3,
            "min": 10,
            "median": 10,
            "mean": 10.0,
            "max": 10,
        },
    ]

    comparison = report["comparison"]
    assert comparison["comparison_complete"] is True
    assert comparison["aggregate_reachable_pair_count"] == 8
    assert comparison["actual_pair_count"] == 2
    assert comparison["confirmed_pair_count"] == 2
    assert comparison["aggregate_only_pair_count"] == 6
    assert comparison["actual_only_pair_count"] == 0
    assert comparison["realization_rate"] == 0.25


def test_species_reachability_without_same_molecule_instance_is_not_actual_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_mismatched_instance_source(tmp_path)

    report = analyze_event_paths([source])

    assert report["summary"]["actual_path_occurrence_count"] == 0
    assert report["paths"] == []
    assert report["comparison"]["aggregate_reachable_pair_count"] == 1
    assert report["comparison"]["aggregate_only_pair_count"] == 1
    assert report["comparison"]["realization_rate"] == 0.0


def test_molecule_instance_connects_only_to_its_first_later_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_recrossing_source(tmp_path)

    report = analyze_event_paths([source], path_length=2)

    observed = {
        tuple(occurrence["reaction_keys"])
        for occurrence in report["occurrences"]
    }
    assert observed == {
        ("A->B", "B->C"),
        ("B->C", "C->B"),
        ("C->B", "B->D"),
    }
    assert ("A->B", "B->D") not in observed


def test_unresolved_same_species_event_blocks_cross_event_shortcut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_unresolved_barrier_source(tmp_path)

    report = analyze_event_paths([source], path_length=2)

    assert report["summary"]["actual_path_occurrence_count"] == 0
    assert report["sources"][0]["unresolved_event_node_count"] == 1
    assert report["sources"][0]["unresolved_species_barrier_count"] == 1


def test_occurrence_detail_limit_does_not_truncate_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_chain_source(tmp_path, "many", (0, 1, 2))

    report = analyze_event_paths([source], max_occurrence_details=1)

    assert len(report["occurrences"]) == 1
    assert report["occurrence_details_truncated"] is True
    assert report["summary"]["actual_path_occurrence_count"] == 3
    assert report["summary"]["statistics_complete"] is True
    assert report["paths"][0]["occurrence_count"] == 3


def test_expansion_limit_marks_support_and_comparison_as_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_chain_source(tmp_path, "bounded", (0,))

    report = analyze_event_paths([source], max_expansions=1)

    assert report["summary"]["statistics_complete"] is False
    assert report["summary"]["traversal_truncated"] is True
    assert report["sources"][0]["traversal_truncated"] is True
    assert report["comparison"]["comparison_complete"] is False
    assert report["comparison"]["actual_count_is_lower_bound"] is True
    assert report["comparison"]["aggregate_only_pair_count"] is None
    assert report["comparison"]["realization_rate"] is None


def test_missing_aggregate_network_is_reported_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_recrossing_source(tmp_path)

    report = analyze_event_paths([source])

    assert report["comparison"]["comparison_available"] is False
    assert report["comparison"]["comparison_complete"] is False
    assert report["comparison"]["realization_rate"] is None
    assert report["comparison"]["skipped_replicates"] == [
        {"replicate": "recrossing", "reason": "reaction_file_not_supplied"}
    ]


def test_reactionevent_only_index_cannot_assert_atomic_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent = tmp_path / "only.reactionevent.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n0,A,B\n1,B,C\n2,C,D\n",
        encoding="utf-8",
    )
    EVENT_EVIDENCE_STORE.build(str(reactionevent))
    source = EventPathSource(
        replicate="only",
        reactionevent_file=str(reactionevent),
        molecules_file=str(tmp_path / "missing.molecules.csv"),
    )

    with pytest.raises(EventPathAnalysisError, match="no molecule/atom association"):
        analyze_event_paths([source])


def test_aggregate_path_enumeration_is_explicitly_bounded() -> None:
    reactions = [
        Reaction(("A",), ("B",), 1),
        Reaction(("X",), ("B",), 1),
        Reaction(("B",), ("C",), 1),
        Reaction(("C",), ("D",), 1),
        Reaction(("C",), ("Z",), 1),
    ]

    complete = enumerate_aggregate_reaction_paths(reactions, max_paths=10)
    bounded = enumerate_aggregate_reaction_paths(reactions, max_paths=2)

    assert complete["path_count"] == 4
    assert complete["truncated"] is False
    assert bounded["path_count"] == 2
    assert bounded["truncated"] is True


def test_replicate_labels_must_be_unique(tmp_path: Path) -> None:
    source = EventPathSource(
        replicate="same",
        reactionevent_file=str(tmp_path / "events.csv"),
        molecules_file=str(tmp_path / "molecules.csv"),
    )

    with pytest.raises(ValueError, match="replicate labels must be unique"):
        analyze_event_paths([source, source])


def test_event_paths_cli_accepts_repeat_prefixes_and_exports_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    source = _write_chain_source(tmp_path / "data", "run", (0,))
    common_prefix = source.reactionevent_file[: -len(".reactionevent.csv")]
    output = tmp_path / "reports" / "event-paths.json"

    exit_code = cli.main(
        [
            "event-paths",
            "--source",
            f"replicate-1={common_prefix}",
            "--out-json",
            str(output),
        ]
    )

    assert exit_code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == EVENT_PATH_SCHEMA_VERSION
    assert document["summary"]["actual_path_occurrence_count"] == 1
    assert document["paths"][0]["replicate_reproduction_rate"] == 1.0
    terminal = capsys.readouterr().out
    assert "actual_occurrences=1" in terminal
    assert "realization_rate=0.25" in terminal


def test_event_paths_cli_source_prefers_native_timeline(tmp_path: Path) -> None:
    base = tmp_path / "run.lammpstrj"
    timeline = Path(f"{base}.timeline.h5")
    timeline.touch()

    source = cli._event_path_source_from_spec(f"native={base}")

    assert source.reactionevent_file == str(timeline)
    assert source.molecules_file == ""


def test_event_paths_cli_rejects_malformed_source_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["event-paths", "--source", "missing-equals"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "REPLICATE=COMMON_PREFIX" in captured.err
    assert "Traceback" not in captured.err
