from __future__ import annotations

import builtins
import os
import shlex
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import reacnet_scope.event_index as event_index_module
from reacnet_scope.event_index import (
    EVENT_EVIDENCE_STORE,
    EventIndexEvidenceProvider,
)
from rng_tools.network import Reaction, ReactionNetwork
from rng_tools.pathways import find_candidate_paths
from scripts.webapp import server as legacy_server
from scripts.webapp_dash import services as svc


REACTION_KEY = "[H]+[O]->[H][O]"


def _write_rng_sources(tmp_path: Path) -> tuple[Path, Path]:
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[H]+[O],[H][O]\n"
        "0,[O]+[H],[H][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[H],0,\n"
        "0,[O],1,\n"
        "10,[H][O],0;1,0-1-1\n"
        "20,[H][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    return reactionevent, molecules


def _write_reaction_file(tmp_path: Path, text: str | None = None) -> Path:
    reaction = tmp_path / "run.reactionabcd"
    reaction.write_text(
        text or "4 [H] + [O] -> [H][O]\n",
        encoding="utf-8",
    )
    return reaction


def _atomic_replace_preserving_size_and_mtime(
    path: Path,
    text: str,
) -> None:
    before = path.stat()
    replacement = path.with_name(f"{path.name}.replacement")
    replacement.write_text(text, encoding="utf-8")
    assert replacement.stat().st_size == before.st_size
    os.utime(
        replacement,
        ns=(before.st_atime_ns, before.st_mtime_ns),
    )
    os.replace(replacement, path)
    after = path.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ino != before.st_ino


@pytest.fixture
def indexed_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = _write_reaction_file(tmp_path)
    reactionevent, molecules = _write_rng_sources(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute(
            """
            UPDATE reaction_summary
            SET total_events=4,matched_events=3,distinct_intervals=2
            WHERE reaction_key=?
            """,
            (REACTION_KEY,),
        )
        connection.execute(
            "UPDATE meta SET value='10' WHERE key='available_intervals'"
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "reaction": str(reaction),
        "reactionevent": str(reactionevent),
        "molecules": str(molecules),
    }


@pytest.fixture
def reaction_only_artifacts(tmp_path: Path) -> dict[str, str]:
    return {"reaction": str(_write_reaction_file(tmp_path))}


def test_pathway_service_enriches_steps_from_event_summary(
    indexed_artifacts: dict[str, str],
) -> None:
    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    path = payload["paths"][0]
    step = path["steps"][0]
    assert path["species"] == ["[H]", "[H][O]"]
    assert path["formulas"] == ["H", "HO"]
    assert step["reactants"] == ["[H]", "[O]"]
    assert step["products"] == ["[H][O]"]
    assert step["event_coverage"] == pytest.approx(3 / 4)
    assert step["time_coverage"] == pytest.approx(2 / 10)
    assert step["evidence_status"] == "evidence_linked"
    assert step["event_total"] == 4
    assert step["matched_event_total"] == 3
    assert step["distinct_intervals"] == 2
    assert step["source_references"]
    assert payload["evidence_status"] == "evidence_linked"
    assert payload["score_version"] == "candidate-path/v1"
    assert payload["query"]["max_depth"] == 1
    assert payload["truncated"] is False
    assert payload["source_signatures"]["reactionabcd"]["path"] == os.path.abspath(
        indexed_artifacts["reaction"]
    )
    assert payload["source_signatures"]["event_index"]["path"].endswith(
        "events.sqlite3"
    )
    assert "preparation_command" not in payload


def test_pathway_service_degrades_without_event_index(
    reaction_only_artifacts: dict[str, str],
) -> None:
    payload = svc.find_pathways(
        reaction_only_artifacts,
        "[H]",
        max_depth=1,
    )

    step = payload["paths"][0]["steps"][0]
    expected = (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(reaction_only_artifacts['reaction']).parent))} "
        "--event-only"
    )
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"] == expected
    assert step["event_coverage"] is None
    assert step["time_coverage"] is None
    assert step["event_total"] is None
    assert step["matched_event_total"] is None
    assert step["distinct_intervals"] is None


@pytest.mark.parametrize(
    ("start_smiles", "limits", "reason"),
    [
        ("missing", {}, "species_absent"),
        ("[H][O]", {}, "no_positive_net_continuation"),
        ("[H]", {"min_net_tp": 5}, "filtered_by_thresholds"),
    ],
)
def test_ready_index_preserves_query_evidence_context_for_empty_results(
    indexed_artifacts: dict[str, str],
    start_smiles: str,
    limits: dict[str, Any],
    reason: str,
) -> None:
    payload = svc.find_pathways(
        indexed_artifacts,
        start_smiles,
        max_depth=1,
        **limits,
    )

    assert payload["paths"] == []
    assert payload["reason"] == reason
    assert payload["evidence_status"] == "evidence_linked"
    assert "preparation_command" not in payload
    assert set(payload["source_signatures"]) >= {
        "reactionabcd",
        "reactionevent",
        "molecules",
        "event_index",
    }


def test_unavailable_index_empty_result_remains_network_only(
    reaction_only_artifacts: dict[str, str],
) -> None:
    payload = svc.find_pathways(
        reaction_only_artifacts,
        "missing",
        max_depth=1,
    )

    assert payload["paths"] == []
    assert payload["reason"] == "species_absent"
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"].endswith("--event-only")


def test_pathway_service_reads_no_event_source_csv(
    indexed_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = {
        os.path.abspath(indexed_artifacts["reactionevent"]),
        os.path.abspath(indexed_artifacts["molecules"]),
    }
    real_open = builtins.open

    def guarded_open(file: Any, *args: Any, **kwargs: Any):
        if os.path.abspath(os.fspath(file)) in protected:
            raise AssertionError("pathway requests must use SQLite evidence only")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    assert payload["paths"][0]["steps"][0]["evidence_status"] == "evidence_linked"


def test_pathway_service_never_builds_event_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = _write_reaction_file(tmp_path)
    reactionevent, molecules = _write_rng_sources(tmp_path)

    def forbidden_build(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pathway request attempted to build event index")

    monkeypatch.setattr(EVENT_EVIDENCE_STORE, "build", forbidden_build)

    payload = svc.find_pathways(
        {
            "reaction": str(reaction),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        "[H]",
        max_depth=1,
    )

    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"].endswith("--event-only")


def test_invalid_event_index_degrades_with_exact_rebuild_command(
    indexed_artifacts: dict[str, str],
) -> None:
    index_path = EVENT_EVIDENCE_STORE.open_required(
        indexed_artifacts["reactionevent"],
        indexed_artifacts["molecules"],
    )["index_path"]
    connection = sqlite3.connect(index_path)
    try:
        connection.execute("DROP TABLE reaction_summary")
        connection.commit()
    finally:
        connection.close()

    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    expected = (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(indexed_artifacts['reactionevent']).parent))} "
        "--rebuild event"
    )
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"] == expected


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("total_events", "broken"),
        ("total_events", 4.9),
        ("matched_events", 2.9),
        ("distinct_intervals", 2.9),
        ("distinct_intervals", 11),
    ],
)
def test_corrupt_summary_discovered_during_batch_degrades_to_network_only(
    indexed_artifacts: dict[str, str],
    column: str,
    value: Any,
) -> None:
    index_path = EVENT_EVIDENCE_STORE.open_required(
        indexed_artifacts["reactionevent"],
        indexed_artifacts["molecules"],
    )["index_path"]
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            f"UPDATE reaction_summary SET {column}=? WHERE reaction_key=?",
            (value, REACTION_KEY),
        )
        connection.commit()
    finally:
        connection.close()

    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    expected = (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(indexed_artifacts['reactionevent']).parent))} "
        "--rebuild event"
    )
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"] == expected
    step = payload["paths"][0]["steps"][0]
    assert step["event_coverage"] is None
    assert step["time_coverage"] is None


def test_fractional_available_intervals_degrades_with_exact_rebuild_command(
    indexed_artifacts: dict[str, str],
) -> None:
    index_path = EVENT_EVIDENCE_STORE.open_required(
        indexed_artifacts["reactionevent"],
        indexed_artifacts["molecules"],
    )["index_path"]
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            "UPDATE meta SET value=10.9 WHERE key='available_intervals'"
        )
        connection.commit()
    finally:
        connection.close()

    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    expected = (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(indexed_artifacts['reactionevent']).parent))} "
        "--rebuild event"
    )
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"] == expected
    assert payload["paths"][0]["steps"][0]["event_coverage"] is None


@pytest.mark.parametrize(
    "corrupt_value",
    ["1_0", " 10", "10 ", "１０", "", "+10"],
)
def test_noncanonical_available_intervals_text_degrades_with_exact_rebuild_command(
    indexed_artifacts: dict[str, str],
    corrupt_value: str,
) -> None:
    index_path = EVENT_EVIDENCE_STORE.open_required(
        indexed_artifacts["reactionevent"],
        indexed_artifacts["molecules"],
    )["index_path"]
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            "UPDATE meta SET value=? WHERE key='available_intervals'",
            (corrupt_value,),
        )
        connection.commit()
    finally:
        connection.close()

    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    expected = (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(Path(indexed_artifacts['reactionevent']).parent))} "
        "--rebuild event"
    )
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"] == expected
    assert payload["paths"][0]["steps"][0]["event_coverage"] is None


def test_atomic_event_index_replacement_during_batch_degrades_to_network_only(
    indexed_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    index_path = Path(
        EVENT_EVIDENCE_STORE.open_required(
            indexed_artifacts["reactionevent"],
            indexed_artifacts["molecules"],
        )["index_path"]
    )
    replacement = tmp_path / "replacement.sqlite3"
    shutil.copy2(index_path, replacement)
    real_summary = EVENT_EVIDENCE_STORE.reaction_summary

    def replacing_summary(
        reactionevent_file: str,
        molecules_file: str,
        reaction_keys: Any,
    ) -> dict[str, dict[str, Any]]:
        rows = real_summary(
            reactionevent_file,
            molecules_file,
            reaction_keys,
        )
        os.replace(replacement, index_path)
        return rows

    monkeypatch.setattr(
        EVENT_EVIDENCE_STORE,
        "reaction_summary",
        replacing_summary,
    )

    payload = svc.find_pathways(indexed_artifacts, "[H]", max_depth=1)

    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"].endswith("--rebuild event")
    assert payload["paths"][0]["steps"][0]["event_coverage"] is None


def test_reaction_source_replacement_after_network_load_is_not_returned(
    reaction_only_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reaction_path = Path(reaction_only_artifacts["reaction"])
    replacement = tmp_path / "replacement.reactionabcd"
    replacement.write_text("9 [H] -> [H][H]\n", encoding="utf-8")
    real_get = svc.STORE.get

    class ReplacingNetworkStore:
        def get_with_signature(
            self,
            path: str,
            min_tp: int,
        ) -> tuple[ReactionNetwork, dict[str, Any]]:
            network = real_get(path, min_tp)
            signature = legacy_server.reaction_source_signature(path)
            os.replace(replacement, reaction_path)
            return network, signature

    monkeypatch.setattr(svc, "STORE", ReplacingNetworkStore())

    with pytest.raises(svc.ServiceError) as caught:
        svc.find_pathways(reaction_only_artifacts, "[H]", max_depth=1)

    assert caught.value.reason == "reaction_source_stale"


def test_same_metadata_atomic_replacement_invalidates_shared_pathway_cache(
    tmp_path: Path,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [O]\n")
    artifacts = {"reaction": str(reaction)}

    first = svc.find_pathways(artifacts, "[H]", max_depth=1)
    _atomic_replace_preserving_size_and_mtime(
        reaction,
        "4 [H] -> [C]\n",
    )
    second = svc.find_pathways(artifacts, "[H]", max_depth=1)

    assert first["paths"][0]["species"] == ["[H]", "[O]"]
    assert second["paths"][0]["species"] == ["[H]", "[C]"]
    assert first["source_signatures"]["reactionabcd"]["sha256"] != (
        second["source_signatures"]["reactionabcd"]["sha256"]
    )


def test_ready_index_missing_summary_returns_linked_zero_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = _write_reaction_file(tmp_path, "7 A -> B\n")
    reactionevent, molecules = _write_rng_sources(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    payload = svc.find_pathways(
        {
            "reaction": str(reaction),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        "A",
        max_depth=1,
    )

    step = payload["paths"][0]["steps"][0]
    assert step["evidence_status"] == "evidence_linked"
    assert step["event_coverage"] == 0.0
    assert step["time_coverage"] == 0.0
    assert step["event_total"] == 0
    assert step["matched_event_total"] == 0
    assert step["distinct_intervals"] == 0
    assert step["source_references"]


def test_event_provider_performs_one_batched_store_lookup_per_search() -> None:
    network = ReactionNetwork(
        [
            Reaction(("A",), ("B",), 10),
            Reaction(("B",), ("C",), 8),
        ]
    )

    class RecordingStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def open_required(self, *_args: str) -> dict[str, Any]:
            return {
                "state": "ready",
                "index_path": "/tmp/events.sqlite3",
                "available_intervals": 10,
            }

        def reaction_summary(
            self,
            _reactionevent: str,
            _molecules: str,
            keys: tuple[str, ...],
        ) -> dict[str, dict[str, Any]]:
            self.calls.append(tuple(keys))
            return {}

    store = RecordingStore()
    provider = EventIndexEvidenceProvider(
        "/tmp/run.reactionevent.csv",
        "/tmp/run.molecules.csv",
        store=store,
    )

    find_candidate_paths(
        network,
        "A",
        max_depth=2,
        evidence_provider=provider,
    )

    assert store.calls == [("A->B", "B->C")]


def test_event_summary_chunks_sqlite_in_queries_at_500_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = _write_rng_sources(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    real_readonly = event_index_module._readonly_connection
    connection_calls = 0
    chunk_sizes: list[int] = []

    class RecordingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(self, statement: str, params: Any = ()):
            if "WHERE reaction_key IN" in statement:
                chunk_sizes.append(len(params))
            return self.connection.execute(statement, params)

        def close(self) -> None:
            self.connection.close()

    def recording_readonly(path: Path):
        nonlocal connection_calls
        connection_calls += 1
        connection = real_readonly(path)
        if connection_calls == 2:
            return RecordingConnection(connection)
        return connection

    monkeypatch.setattr(
        event_index_module,
        "_readonly_connection",
        recording_readonly,
    )

    EVENT_EVIDENCE_STORE.reaction_summary(
        str(reactionevent),
        str(molecules),
        [f"R{index}->P{index}" for index in range(1001)],
    )

    assert chunk_sizes == [500, 500, 1]


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("max_depth", 0),
        ("max_branches", 0),
        ("max_paths", 0),
        ("max_expansions", 0),
        ("min_net_tp", 0),
        ("min_directionality", 2),
        ("direction", "sideways"),
        ("unexpected_limit", 1),
    ],
)
def test_invalid_pathway_limits_raise_bad_pathway_query(
    reaction_only_artifacts: dict[str, str],
    keyword: str,
    value: Any,
) -> None:
    with pytest.raises(svc.ServiceError) as caught:
        svc.find_pathways(
            reaction_only_artifacts,
            "[H]",
            **{keyword: value},
        )

    assert caught.value.reason == "bad_pathway_query"


def test_pathway_service_requires_reactionabcd_artifact(tmp_path: Path) -> None:
    wrong = tmp_path / "run.txt"
    wrong.write_text("4 A -> B\n", encoding="utf-8")

    with pytest.raises(svc.ServiceError) as caught:
        svc.find_pathways({"reaction": str(wrong)}, "A")

    assert caught.value.reason == "missing_reac"


def test_pathway_service_uses_existing_network_cache(
    reaction_only_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = ReactionNetwork([Reaction(("[H]", "[O]"), ("[H][O]",), 4)])
    calls: list[tuple[str, int]] = []

    class RecordingNetworkStore:
        def get_with_signature(
            self,
            path: str,
            min_tp: int,
        ) -> tuple[ReactionNetwork, dict[str, Any]]:
            calls.append((path, min_tp))
            return network, legacy_server.reaction_source_signature(path)

    monkeypatch.setattr(svc, "STORE", RecordingNetworkStore())

    svc.find_pathways(reaction_only_artifacts, "[H]", max_depth=1)

    assert calls == [(reaction_only_artifacts["reaction"], 1)]


def test_pathway_elements_preserve_exact_species_and_complete_hyperedges() -> None:
    payload = {
        "paths": [
            {
                "rank": 2,
                "species": ["[H]", "[H][O]"],
                "steps": [
                    {
                        "reaction_key": "[H]+[O]+[O]->[H][O]+[O]",
                        "reactants": ["[H]", "[O]", "[O]"],
                        "products": ["[H][O]", "[O]"],
                        "focal_input": "[H]",
                        "focal_output": "[H][O]",
                        "evidence_status": "network_only",
                        "score": 0.4,
                    }
                ],
            }
        ]
    }

    elements = svc.build_pathway_elements(payload)

    species = [
        item for item in elements
        if "species" in str(item.get("classes") or "").split()
    ]
    reactions = [
        item for item in elements
        if "reaction" in str(item.get("classes") or "").split()
    ]
    edges = [item for item in elements if "source" in (item.get("data") or {})]
    assert {item["data"]["smiles"] for item in species} == {
        "[H]",
        "[O]",
        "[H][O]",
    }
    assert len(reactions) == 1
    assert reactions[0]["data"]["id"].endswith("path-2-step-1")
    assert reactions[0]["data"]["reaction_text"] == (
        "[H] + [O] + [O] -> [H][O] + [O]"
    )
    assert "path-rank-2" in reactions[0]["classes"].split()
    assert "network-only" in reactions[0]["classes"].split()
    assert len(edges) == 5
    assert sum(edge["data"]["source"].startswith("species:") for edge in edges) == 3
    assert sum(edge["data"]["target"].startswith("species:") for edge in edges) == 2


def test_pathway_elements_use_collision_safe_node_ids() -> None:
    payload = {
        "paths": [
            {
                "rank": 1,
                "species": ["a:b", "a/b"],
                "steps": [
                    {
                        "reaction_key": "same",
                        "reactants": ["a:b"],
                        "products": ["a/b"],
                        "focal_input": "a:b",
                        "focal_output": "a/b",
                        "evidence_status": "evidence_linked",
                        "score": 0.9,
                    }
                ],
            },
            {
                "rank": 2,
                "species": ["a:b", "a/b"],
                "steps": [
                    {
                        "reaction_key": "same",
                        "reactants": ["a:b"],
                        "products": ["a/b"],
                        "focal_input": "a:b",
                        "focal_output": "a/b",
                        "evidence_status": "evidence_linked",
                        "score": 0.8,
                    }
                ],
            },
        ]
    }

    elements = svc.build_pathway_elements(payload)
    node_ids = [
        item["data"]["id"]
        for item in elements
        if "source" not in item["data"]
    ]

    assert len(node_ids) == len(set(node_ids))
