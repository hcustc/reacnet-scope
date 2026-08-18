from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from reacnet_scope import dir_browser, services as svc
from reacnet_scope import event_index as event_index_module
from reacnet_scope import species_fate as species_fate_module
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import IndexInvalidError
from reacnet_scope.species_fate import (
    SpeciesFateQueryError,
    analyze_species_fate,
    species_fate_to_json,
    species_fate_tables_zip,
)
from scripts.rng_query_cli import cmd_species_fate


def _write_source(
    tmp_path: Path,
    reactions: list[tuple[int, str, str]],
    frames: list[tuple[int, list[tuple[str, tuple[int, ...], tuple[str, ...]]]]],
) -> tuple[Path, Path]:
    reactionevent = tmp_path / "fate.reactionevent.csv"
    molecules = tmp_path / "fate.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        + "".join(f"{index},{left},{right}\n" for index, left, right in reactions),
        encoding="utf-8",
    )
    molecule_rows = ["Timestep,Species,AtomIDs,BondIDs\n"]
    for timestep, entries in frames:
        for species, atom_ids, bonds in entries:
            molecule_rows.append(
                f"{timestep},{species},{';'.join(map(str, atom_ids))},"
                f"{';'.join(bonds)}\n"
            )
    molecules.write_text("".join(molecule_rows), encoding="utf-8")
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    return reactionevent, molecules


def _materialize_synthetic_continuity(
    database: Path,
    events: list[
        tuple[
            str,
            int,
            list[tuple[str, tuple[int, ...]]],
            list[tuple[str, tuple[int, ...]]],
        ]
    ],
) -> sqlite3.Connection:
    connection = EVENT_EVIDENCE_STORE._connect_for_build(database)
    for source_row, (event_id, timestep, reactants, products) in enumerate(events):
        reactant_rows = [
            {"species": species, "atom_ids": list(atom_ids)}
            for species, atom_ids in reactants
        ]
        product_rows = [
            {"species": species, "atom_ids": list(atom_ids)}
            for species, atom_ids in products
        ]
        left = "+".join(species for species, _atom_ids in reactants)
        right = "+".join(species for species, _atom_ids in products)
        atom_ids = sorted(
            {
                atom_id
                for _species, participant_atoms in [*reactants, *products]
                for atom_id in participant_atoms
            }
        )
        connection.execute(
            """
            INSERT INTO events(
                event_id,reaction_key,source_row,timestep_index,
                before_timestep,after_timestep,reactant_text,product_text,
                atom_ids_json,reactant_bonds_json,product_bonds_json,
                reactant_participants_json,product_participants_json,
                association_status,occurrence
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                f"{'+'.join(sorted(left.split('+')))}->{'+'.join(sorted(right.split('+')))}",
                source_row,
                timestep,
                timestep * 10,
                (timestep + 1) * 10,
                left,
                right,
                json.dumps(atom_ids),
                "[]",
                "[]",
                json.dumps(reactant_rows),
                json.dumps(product_rows),
                "matched",
                1,
            ),
        )
    EVENT_EVIDENCE_STORE._materialize_continuity(
        connection, replicate_id="replicate-synthetic"
    )
    return connection


def test_fate_tracks_one_resolved_first_passage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("B", (0,), ())]),
        ],
    )

    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"product": ["B"], "unused": ["X"]},
        anchor_mode="all_atoms",
    )

    assert report["status"] == "complete"
    assert report["summary"]["formation_count"] == 1
    assert report["summary"]["fully_resolved_count"] == 1
    assert report["summary"]["resolution_fraction"] == 1.0
    assert report["summary"]["fate_signatures"][0]["members"] == [
        {"endpoint_category": "product", "multiplicity": 1}
    ]
    assert report["summary"]["endpoint_marginals"] == [
        {
            "endpoint_category": "product",
            "episode_occurrence_count": 1,
            "terminal_instance_count": 1,
            "marginal_occurrence_probability": 1.0,
            "unconditional_mean_multiplicity": 1.0,
            "conditional_mean_multiplicity": 1.0,
        },
        {
            "endpoint_category": "unused",
            "episode_occurrence_count": 0,
            "terminal_instance_count": 0,
            "marginal_occurrence_probability": 0.0,
            "unconditional_mean_multiplicity": 0.0,
            "conditional_mean_multiplicity": None,
        },
    ]
    episode = report["episodes"][0]
    assert episode["anchor_atom_ids"] == [1]
    assert episode["initial_residence_time"]["frames"] == 0
    assert episode["terminal_instances"][0]["anchor_atom_ids"] == [1]
    assert len(episode["event_ids"]) == 2


def test_target_return_is_suppressed_inside_active_episode(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [
            (0, "X", "A"),
            (1, "A", "C"),
            (2, "C", "A"),
            (3, "A", "B"),
        ],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("C", (0,), ())]),
            (30, [("A", (0,), ())]),
            (40, [("B", (0,), ())]),
        ],
    )

    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
    )

    assert report["summary"]["formation_count"] == 1
    assert len(report["suppressed_return_candidates"]) == 1
    assert len(report["episodes"][0]["event_ids"]) == 4


def test_descendant_complete_fate_preserves_terminal_multiplicity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "P+P")],
        [
            (0, [("X", (0, 1), ("0-1-1",))]),
            (10, [("A", (0, 1), ("0-1-1",))]),
            (20, [("P", (0,), ()), ("P", (1,), ())]),
        ],
    )

    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"fragment": ["P"]},
        anchor_mode="all_atoms",
    )

    episode = report["episodes"][0]
    assert episode["fully_resolved"] is True
    assert episode["topology"] == "complex"
    assert episode["fate_signature"]["members"] == [
        {"endpoint_category": "fragment", "multiplicity": 2}
    ]
    assert {
        tuple(row["anchor_atom_ids"]) for row in episode["terminal_instances"]
    } == {(1,), (2,)}
    assert report["summary"]["endpoint_marginals"][0][
        "unconditional_mean_multiplicity"
    ] == 2.0
    assert report["summary"]["topology_eligible_count"] == 0


def test_heavy_atom_policy_fails_closed_without_mapping(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("B", (0,), ())]),
        ],
    )

    with pytest.raises(SpeciesFateQueryError, match="reliable atom ID"):
        analyze_species_fate(
            str(reactionevent),
            str(molecules),
            target_species="A",
            endpoint_categories={"product": ["B"]},
        )


def test_old_event_index_remains_readable_but_fate_requires_rebuild(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A")],
        [(0, [("X", (0,), ())]), (10, [("A", (0,), ())])],
    )
    opened = EVENT_EVIDENCE_STORE.open_required(
        str(reactionevent), str(molecules)
    )
    connection = sqlite3.connect(opened["index_path"])
    try:
        for table in (
            "continuity_diagnostics",
            "continuity_links",
            "event_participants",
            "molecule_instances",
            "continuity_species",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM meta WHERE key LIKE 'continuity_%'")
        connection.commit()
    finally:
        connection.close()

    reopened = EVENT_EVIDENCE_STORE.open_required(
        str(reactionevent), str(molecules)
    )
    assert reopened["continuity_available"] is False
    with pytest.raises(IndexInvalidError, match="prepare rebuild event"):
        EVENT_EVIDENCE_STORE.open_continuity_required(
            str(reactionevent), str(molecules)
        )


def test_relational_export_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("B", (0,), ())]),
        ],
    )
    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
    )

    first = species_fate_tables_zip(report)
    assert first == species_fate_tables_zip(report)
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["fate_result_id"] == report["fate_result_id"]
        assert "episodes.csv" in archive.namelist()
        assert "molecule_instances.csv" in archive.namelist()
        assert "continuity_edges.csv" in archive.namelist()


def test_species_fate_cli_writes_both_formal_exports(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("B", (0,), ())]),
        ],
    )
    json_path = tmp_path / "fate-result.json"
    tables_path = tmp_path / "fate-tables.zip"

    exit_code = cmd_species_fate(
        Namespace(
            case=str(tmp_path),
            base="fate",
            target="A",
            endpoint=[("product", "B")],
            atom_element=[],
            anchor_mode="all_atoms",
            anchor_element=[],
            anchor_atom_id=[],
            formation_start_frame=0,
            formation_end_frame=None,
            followup_end_frame=None,
            minimum_followup_frames=0,
            max_events_per_episode=10_000,
            max_active_branches=1_000,
            detail_retention_limit=1_000,
            out_json=str(json_path),
            out_tables=str(tables_path),
            force=False,
        )
    )

    assert exit_code == 0
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "complete"
    with zipfile.ZipFile(tables_path) as archive:
        assert "manifest.json" in archive.namelist()
    output = json.loads(capsys.readouterr().out)
    assert output["formation_count"] == 1
    assert json_path.read_text(encoding="utf-8") == species_fate_to_json(
        json.loads(json_path.read_text(encoding="utf-8"))
    )


def test_dataset_candidate_preserves_ready_continuity_status(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("B", (0,), ())]),
        ],
    )

    candidate = svc.browse_dataset_location(str(tmp_path))["datasets"][0]

    assert candidate["index_states"]["event"] == "ready"
    assert candidate["analysis_capabilities"]["event_search"]["state"] == "ready"
    assert candidate["analysis_capabilities"]["species_fate"]["state"] == "ready"


def test_cli_preflights_table_conflict_before_overwriting_json(
    tmp_path: Path, capsys
) -> None:
    json_path = tmp_path / "fate-result.json"
    tables_path = tmp_path / "fate-tables.zip"
    json_path.write_text("keep-me\n", encoding="utf-8")
    tables_path.write_bytes(b"existing")

    exit_code = cmd_species_fate(
        Namespace(
            endpoint=[("product", "B")],
            atom_element=[],
            out_json=str(json_path),
            out_tables=str(tables_path),
            force=False,
        )
    )

    assert exit_code == 2
    assert json_path.read_text(encoding="utf-8") == "keep-me\n"
    assert tables_path.read_bytes() == b"existing"
    assert "pass --force" in capsys.readouterr().err


def test_query_identity_is_replicate_scoped_and_normalizes_elements(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))

    def report_for(root: Path, hydrogen: str) -> dict:
        root.mkdir()
        reactionevent, molecules = _write_source(
            root,
            [(0, "X", "A"), (1, "A", "B")],
            [
                (0, [("X", (0, 1), ("0-1-1",))]),
                (10, [("A", (0, 1), ("0-1-1",))]),
                (20, [("B", (0, 1), ("0-1-1",))]),
            ],
        )
        return analyze_species_fate(
            str(reactionevent),
            str(molecules),
            target_species="A",
            endpoint_categories={"product": ["B"]},
            atom_elements={1: hydrogen, 2: "c"},
            anchor_mode="NON_HYDROGEN",
        )

    first = report_for(tmp_path / "first", "h")
    same_dataset = analyze_species_fate(
        str(tmp_path / "first" / "fate.reactionevent.csv"),
        str(tmp_path / "first" / "fate.molecules.csv"),
        target_species="A",
        endpoint_categories={"product": ["B"]},
        atom_elements={2: "C", 1: "H"},
        anchor_mode="heavy_atoms",
    )
    second = report_for(tmp_path / "second", "H")

    assert first["fate_query_id"] == same_dataset["fate_query_id"]
    assert first["query"]["anchor_mode"] == "heavy_atoms"
    assert first["query"]["atom_elements"] == {"1": "H", "2": "C"}
    assert first["episodes"][0]["anchor_atom_ids"] == [2]
    assert first["fate_query_id"] != second["fate_query_id"]


def test_invalid_anchor_policy_fails_even_without_formation_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "B")],
        [(0, [("X", (0,), ())]), (10, [("B", (0,), ())])],
    )

    with pytest.raises(SpeciesFateQueryError, match="anchor_mode"):
        analyze_species_fate(
            str(reactionevent),
            str(molecules),
            target_species="X",
            endpoint_categories={"product": ["B"]},
            anchor_mode="guess_heavy_atoms",
        )
    with pytest.raises(SpeciesFateQueryError, match="invalid element symbol"):
        analyze_species_fate(
            str(reactionevent),
            str(molecules),
            target_species="X",
            endpoint_categories={"product": ["B"]},
            atom_elements={1: "not-an-element"},
            anchor_mode="all_atoms",
        )


def test_reaction_order_does_not_change_canonical_path_identity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))

    def analyze(root: Path, reactants: str) -> dict:
        root.mkdir()
        reactionevent, molecules = _write_source(
            root,
            [(0, "X", "A"), (1, reactants, "B")],
            [
                (0, [("X", (0,), ()), ("Q", (1,), ())]),
                (10, [("A", (0,), ()), ("Q", (1,), ())]),
                (20, [("B", (0, 1), ("0-1-1",))]),
            ],
        )
        return analyze_species_fate(
            str(reactionevent),
            str(molecules),
            target_species="A",
            endpoint_categories={"product": ["B"]},
            anchor_mode="all_atoms",
        )

    first = analyze(tmp_path / "left", "A+Q")
    second = analyze(tmp_path / "right", "Q+A")

    first_episode = first["episodes"][0]
    second_episode = second["episodes"][0]
    assert first_episode["first_exit_channel"]["reaction_key"] == "A+Q->B"
    assert (
        first_episode["first_exit_channel"]["reaction_key"]
        == second_episode["first_exit_channel"]["reaction_key"]
    )
    assert (
        first_episode["linear_reaction_type_sequence"]
        == second_episode["linear_reaction_type_sequence"]
        == ["A+Q->B"]
    )


def test_evidence_censor_boundary_is_part_of_raw_trace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "C"), (2, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (20, [("A", (0,), ())]),
            (30, [("B", (0,), ())]),
        ],
    )
    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
    )

    episode = report["episodes"][0]
    assert episode["fully_resolved"] is False
    censor_event_ids = {
        event_id
        for row in episode["censored_anchor_subsets"]
        for event_id in row["event_ids"]
    }
    assert censor_event_ids
    assert censor_event_ids <= set(episode["event_ids"])
    assert not any(
        event["reaction_key"] == "A->B" for event in episode["events"]
    )


def test_global_episode_limit_returns_incomplete_without_prefix_statistics(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B"), (2, "Y", "A"), (3, "A", "B")],
        [
            (0, [("X", (0,), ()), ("Y", (1,), ())]),
            (10, [("A", (0,), ()), ("Y", (1,), ())]),
            (20, [("B", (0,), ()), ("Y", (1,), ())]),
            (30, [("B", (0,), ()), ("A", (1,), ())]),
            (40, [("B", (0,), ()), ("B", (1,), ())]),
        ],
    )
    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
        global_episode_limit=1,
    )

    assert report["status"] == "incomplete"
    assert report["summary"] is None
    assert report["episode_count"] is None
    assert report["processed_episode_count"] == 1
    assert report["global_incompleteness"]["reason"] == "global_episode_limit"


def test_closed_formation_episodes_are_not_rescanned_for_each_candidate(
    monkeypatch,
) -> None:
    candidate_count = 200
    anchor_iterations = 0

    class CountingAnchors(list[int]):
        def __iter__(self):
            nonlocal anchor_iterations
            anchor_iterations += 1
            return super().__iter__()

    class FakeReader:
        opened = {"dataset_id": "replicate-synthetic", "available_intervals": 200}

        def __init__(self, _reactionevent_file: str, _molecules_file: str) -> None:
            pass

        def close(self) -> None:
            pass

        def clear_traversal_cache(self) -> None:
            pass

        def species_catalog(self) -> dict[str, str]:
            return {"A": "species-a", "B": "species-b"}

        def formation_candidates(self, *_args, **_kwargs) -> list[dict]:
            return [
                {
                    "event_id": f"birth-{frame}",
                    "instance_id": f"instance-{frame}",
                    "analyzed_frame": frame,
                    "atom_ids": [frame + 1],
                }
                for frame in range(candidate_count)
            ]

    def fake_traverse(_reader, birth, anchors, **_kwargs) -> dict:
        frame = int(birth["analyzed_frame"])
        return {
            "formation_episode_id": f"episode-{frame}",
            "birth": dict(birth),
            "anchor_atom_ids": CountingAnchors(anchors),
            "close_frame": frame,
            "return_candidates": [],
        }

    monkeypatch.setattr(species_fate_module, "_ContinuityReader", FakeReader)
    monkeypatch.setattr(species_fate_module, "_traverse_episode", fake_traverse)
    monkeypatch.setattr(
        species_fate_module,
        "_episode_for_aggregation",
        lambda episode: {"formation_episode_id": episode["formation_episode_id"]},
    )
    monkeypatch.setattr(species_fate_module, "_aggregate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        species_fate_module,
        "_build_result_document",
        lambda _reader, **kwargs: {
            "episodes": list(kwargs["retained_episodes"])
        },
    )

    report = species_fate_module.analyze_species_fate(
        "unused.reactionevent.csv",
        "unused.molecules.csv",
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
    )

    assert len(report["episodes"]) == candidate_count
    assert anchor_iterations <= candidate_count * 3


def test_detail_retention_releases_unretained_raw_episode_traces(
    monkeypatch,
) -> None:
    import gc
    import weakref

    payload_refs: list[weakref.ReferenceType] = []
    cache_clear_count = 0

    class RawTrace:
        pass

    class FakeReader:
        opened = {"dataset_id": "replicate-synthetic", "available_intervals": 5}

        def __init__(self, _reactionevent_file: str, _molecules_file: str) -> None:
            pass

        def close(self) -> None:
            pass

        def clear_traversal_cache(self) -> None:
            nonlocal cache_clear_count
            cache_clear_count += 1

        def species_catalog(self) -> dict[str, str]:
            return {"A": "species-a", "B": "species-b"}

        def formation_candidates(self, *_args, **_kwargs) -> list[dict]:
            return [
                {
                    "event_id": f"birth-{frame}",
                    "instance_id": f"instance-{frame}",
                    "analyzed_frame": frame,
                    "atom_ids": [frame + 1],
                }
                for frame in range(5)
            ]

    def fake_traverse(_reader, birth, anchors, **_kwargs) -> dict:
        payload = RawTrace()
        payload_refs.append(weakref.ref(payload))
        frame = int(birth["analyzed_frame"])
        return {
            "formation_episode_id": f"episode-{frame}",
            "birth": dict(birth),
            "anchor_atom_ids": list(anchors),
            "close_frame": frame,
            "return_candidates": [],
            "raw_trace": payload,
        }

    monkeypatch.setattr(species_fate_module, "_ContinuityReader", FakeReader)
    monkeypatch.setattr(species_fate_module, "_traverse_episode", fake_traverse)
    monkeypatch.setattr(
        species_fate_module,
        "_episode_for_aggregation",
        lambda episode: {"formation_episode_id": episode["formation_episode_id"]},
    )
    monkeypatch.setattr(species_fate_module, "_aggregate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        species_fate_module,
        "_build_result_document",
        lambda _reader, **kwargs: {
            "episodes": list(kwargs["retained_episodes"]),
            "processed_episode_count": len(kwargs["episode_summaries"]),
        },
    )

    report = species_fate_module.analyze_species_fate(
        "unused.reactionevent.csv",
        "unused.molecules.csv",
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
        detail_retention_limit=2,
    )
    gc.collect()

    assert report["processed_episode_count"] == 5
    assert len(report["episodes"]) == 2
    assert cache_clear_count == 5
    assert sum(reference() is not None for reference in payload_refs) == 2


def test_legacy_event_build_checkpoints_in_bounded_interval_batches(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    checkpointed_intervals: list[int] = []
    original_write_meta = event_index_module._write_meta

    def record_write_meta(connection, values) -> None:
        if (
            values.get("build_state") == "building"
            and int(values.get("completed_interval", -1)) >= 0
        ):
            checkpointed_intervals.append(int(values["completed_interval"]))
        original_write_meta(connection, values)

    monkeypatch.setattr(event_index_module, "_write_meta", record_write_meta)
    interval_count = 205
    reactions = [
        (index, "A", "B") if index % 2 == 0 else (index, "B", "A")
        for index in range(interval_count)
    ]
    frames = [
        (
            index * 10,
            [("A" if index % 2 == 0 else "B", (0,), ())],
        )
        for index in range(interval_count + 1)
    ]

    _write_source(tmp_path, reactions, frames)

    assert checkpointed_intervals == [99, 199, 204]


def test_time_distributions_include_frames_and_source_timesteps(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_source(
        tmp_path,
        [(0, "X", "A"), (1, "A", "B")],
        [
            (0, [("X", (0,), ())]),
            (10, [("A", (0,), ())]),
            (25, [("B", (0,), ())]),
        ],
    )
    report = analyze_species_fate(
        str(reactionevent),
        str(molecules),
        target_species="A",
        endpoint_categories={"product": ["B"]},
        anchor_mode="all_atoms",
    )

    distributions = report["summary"]["time_distributions"]
    assert distributions["terminal_first_passage_frames"]["count"] == 1
    assert distributions["terminal_first_passage_source_timesteps"]["mean"] == 15.0
    assert distributions["descendant_completion_source_timesteps"]["count"] == 1


def test_multiple_producers_create_a_continuity_barrier(tmp_path: Path) -> None:
    connection = _materialize_synthetic_continuity(
        tmp_path / "multiple-producers.sqlite",
        [
            ("birth", 0, [("X", (1,))], [("A", (1,))]),
            ("producer-1", 1, [("Y", (1,))], [("A", (1,))]),
            ("producer-2", 1, [("Z", (1,))], [("A", (1,))]),
            ("later-consumer", 2, [("A", (1,))], [("B", (1,))]),
        ],
    )
    try:
        birth_instance = connection.execute(
            """
            SELECT instance_id FROM event_participants
            WHERE event_id='birth' AND side='product'
            """
        ).fetchone()[0]
        link = connection.execute(
            """
            SELECT status,next_timestep_index,next_event_ids_json,reason
            FROM continuity_links WHERE from_instance_id=?
            """,
            (birth_instance,),
        ).fetchone()
    finally:
        connection.close()

    assert link[0] == "ambiguous"
    assert link[1] == 1
    assert json.loads(link[2]) == ["producer-1", "producer-2"]
    assert link[3] == "multiple_producers_same_transition"
    assert "later-consumer" not in json.loads(link[2])


def test_cross_occurrence_consumer_producer_dependency_is_ambiguous(
    tmp_path: Path,
) -> None:
    connection = _materialize_synthetic_continuity(
        tmp_path / "event-dependency.sqlite",
        [
            ("birth", 0, [("X", (1,))], [("A", (1,))]),
            ("consumer", 1, [("A", (1,))], [("C", (1,))]),
            ("producer", 1, [("D", (1,))], [("A", (1,))]),
            ("later-consumer", 2, [("A", (1,))], [("B", (1,))]),
        ],
    )
    try:
        birth_instance = connection.execute(
            """
            SELECT instance_id FROM event_participants
            WHERE event_id='birth' AND side='product'
            """
        ).fetchone()[0]
        link = connection.execute(
            """
            SELECT status,next_event_ids_json,reason
            FROM continuity_links WHERE from_instance_id=?
            """,
            (birth_instance,),
        ).fetchone()
        diagnostic = connection.execute(
            """
            SELECT reason FROM continuity_diagnostics
            WHERE timestep_index=1
            """
        ).fetchone()[0]
    finally:
        connection.close()

    assert link[0] == "ambiguous"
    assert json.loads(link[1]) == ["consumer", "producer"]
    assert link[2] == "same_transition_event_dependency_ambiguous"
    assert diagnostic == "same_transition_event_dependency_ambiguous"
