from __future__ import annotations

import builtins
import csv
import io
import json
import os
import shlex
import sqlite3
from pathlib import Path
from typing import Any

import networkx as nx
import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from rng_tools.network import Reaction, ReactionNetwork
from scripts.webapp_dash import services as svc


REACTION_KEY = "[H]+[O]->[H][O]"
NODE_COLUMNS = [
    "id",
    "kind",
    "label",
    "smiles",
    "formula",
    "reaction_key",
    "reactants_json",
    "products_json",
    "forward_tp",
    "reverse_tp",
    "net_tp",
    "event_total",
    "matched_event_total",
    "event_coverage",
    "evidence_status",
]
EDGE_COLUMNS = [
    "id",
    "source",
    "target",
    "role",
    "species_smiles",
    "coefficient",
    "reaction_key",
]


def _write_reaction_file(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "run.reactionabcd"
    path.write_text(
        text or "4 [H] + [O] -> [H][O]\n",
        encoding="utf-8",
    )
    return path


def _write_rng_sources(tmp_path: Path) -> tuple[Path, Path]:
    reactionevent = tmp_path / "run.lammpstrj.reactionevent.csv"
    molecules = tmp_path / "run.lammpstrj.molecules.csv"
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "0,[H]+[O],[H][O]\n",
        encoding="utf-8",
    )
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "0,[H],0,\n"
        "0,[O],1,\n"
        "10,[H][O],0;1,0-1-1\n",
        encoding="utf-8",
    )
    return reactionevent, molecules


@pytest.fixture
def reaction_artifacts(tmp_path: Path) -> dict[str, str]:
    return {"reaction": str(_write_reaction_file(tmp_path))}


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
            SET total_events=5,matched_events=4,distinct_intervals=1
            WHERE reaction_key=?
            """,
            (REACTION_KEY,),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "reaction": str(reaction),
        "reactionevent": str(reactionevent),
        "molecules": str(molecules),
    }


def test_build_mechanism_elements_uses_cached_network_and_keeps_raw_payload(
    reaction_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = ReactionNetwork(
        [Reaction(("[H]", "[O]"), ("[H][O]",), 4)]
    )
    calls: list[tuple[str, int]] = []

    class RecordingStore:
        def get(self, path: str, min_tp: int) -> ReactionNetwork:
            calls.append((path, min_tp))
            return network

    monkeypatch.setattr(svc, "STORE", RecordingStore())

    payload = svc.build_mechanism_elements(
        reaction_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    assert calls == [(reaction_artifacts["reaction"], 1)]
    assert payload["ok"] is True
    assert payload["schema_version"] == "reacnet-scope/mechanism-network/v1"
    assert payload["network_semantics"] == "mechanism"
    assert payload["evidence_level"] == "reaction_passage_counts"
    assert payload["evidence_status"] == "network_only"
    assert payload["query"]["max_depth"] == 1
    assert payload["nodes"]
    assert payload["edges"]
    assert payload["elements"]
    assert {
        item["data"]["id"] for item in payload["elements"]
    } == {
        item["id"] for item in [*payload["nodes"], *payload["edges"]]
    }
    assert "kinetic_flux" not in json.dumps(payload)


def test_ready_event_index_is_batched_and_never_reads_event_csv(
    indexed_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = {
        os.path.abspath(indexed_artifacts["reactionevent"]),
        os.path.abspath(indexed_artifacts["molecules"]),
    }
    real_open = builtins.open
    real_summary = EVENT_EVIDENCE_STORE.reaction_summary
    batches: list[tuple[str, ...]] = []

    def guarded_open(file: Any, *args: Any, **kwargs: Any):
        if os.path.abspath(os.fspath(file)) in protected:
            raise AssertionError("mechanism service must use SQLite evidence")
        return real_open(file, *args, **kwargs)

    def recording_summary(
        reactionevent: str,
        molecules: str,
        keys: Any,
    ) -> dict[str, dict[str, Any]]:
        batches.append(tuple(keys))
        return real_summary(reactionevent, molecules, keys)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(
        EVENT_EVIDENCE_STORE,
        "reaction_summary",
        recording_summary,
    )

    payload = svc.build_mechanism_elements(
        indexed_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    reaction = next(
        node for node in payload["nodes"] if node["kind"] == "reaction"
    )
    assert batches == [(REACTION_KEY,)]
    assert payload["evidence_level"] == "event_evidence_linked"
    assert payload["evidence_status"] == "evidence_linked"
    assert reaction["event_total"] == 5
    assert reaction["matched_event_total"] == 4
    assert reaction["event_coverage"] == pytest.approx(0.8)
    assert reaction["evidence_status"] == "evidence_linked"
    assert set(payload["source_signatures"]) == {
        "reactionabcd",
        "reactionevent",
        "molecules",
        "event_index",
    }
    assert "preparation_command" not in payload


def test_unavailable_event_index_never_builds_and_reports_prepare_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reaction = _write_reaction_file(tmp_path)
    reactionevent, molecules = _write_rng_sources(tmp_path)

    def forbidden_build(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("web requests must never build event indexes")

    monkeypatch.setattr(EVENT_EVIDENCE_STORE, "build", forbidden_build)
    payload = svc.build_mechanism_elements(
        {
            "reaction": str(reaction),
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        },
        anchor_smiles="[H]",
        max_depth=1,
    )

    assert payload["evidence_level"] == "reaction_passage_counts"
    assert payload["evidence_status"] == "network_only"
    assert payload["preparation_command"] == (
        "reacnet-scope-prepare "
        f"{shlex.quote(str(reactionevent.parent))} --event-only"
    )
    assert set(payload["source_signatures"]) == {"reactionabcd"}


def test_corrupt_event_index_degrades_to_network_only_with_rebuild_command(
    indexed_artifacts: dict[str, str],
) -> None:
    index_path = EVENT_EVIDENCE_STORE.open_required(
        indexed_artifacts["reactionevent"],
        indexed_artifacts["molecules"],
    )["index_path"]
    connection = sqlite3.connect(index_path)
    try:
        connection.execute(
            "UPDATE reaction_summary SET total_events='broken' "
            "WHERE reaction_key=?",
            (REACTION_KEY,),
        )
        connection.commit()
    finally:
        connection.close()

    payload = svc.build_mechanism_elements(
        indexed_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    reaction = next(
        node for node in payload["nodes"] if node["kind"] == "reaction"
    )
    assert payload["evidence_level"] == "reaction_passage_counts"
    assert payload["evidence_status"] == "network_only"
    assert set(payload["source_signatures"]) == {"reactionabcd"}
    assert payload["preparation_command"].endswith("--rebuild event")
    assert reaction["event_total"] is None
    assert reaction["evidence_status"] == "network_only"


def test_absent_anchor_preserves_ready_event_snapshot_context(
    indexed_artifacts: dict[str, str],
) -> None:
    payload = svc.build_mechanism_elements(
        indexed_artifacts,
        anchor_smiles="absent",
    )

    assert payload["meta"]["reason"] == "species_absent"
    assert payload["evidence_level"] == "event_evidence_linked"
    assert payload["evidence_status"] == "evidence_linked"
    assert set(payload["source_signatures"]) == {
        "reactionabcd",
        "reactionevent",
        "molecules",
        "event_index",
    }
    assert "preparation_command" not in payload


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ({}, "bad_mechanism_query"),
        ({"anchor_smiles": ""}, "bad_mechanism_query"),
        ({"anchor_smiles": "A", "direction": "sideways"}, "bad_mechanism_query"),
        ({"anchor_smiles": "A", "max_depth": 0}, "bad_mechanism_query"),
        ({"anchor_smiles": "A", "unexpected": 1}, "bad_mechanism_query"),
    ],
)
def test_invalid_mechanism_queries_are_service_errors(
    reaction_artifacts: dict[str, str],
    query: dict[str, Any],
    reason: str,
) -> None:
    with pytest.raises(svc.ServiceError) as caught:
        svc.build_mechanism_elements(reaction_artifacts, **query)
    assert caught.value.reason == reason


def test_mechanism_service_requires_reactionabcd(tmp_path: Path) -> None:
    wrong = tmp_path / "run.txt"
    wrong.write_text("4 A -> B\n", encoding="utf-8")

    with pytest.raises(svc.ServiceError) as caught:
        svc.build_mechanism_elements(
            {"reaction": str(wrong)},
            anchor_smiles="A",
        )
    assert caught.value.reason == "missing_reac"


def test_reaction_source_replacement_after_cache_load_is_rejected(
    reaction_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reaction_path = Path(reaction_artifacts["reaction"])
    replacement = tmp_path / "replacement.reactionabcd"
    replacement.write_text("9 A -> C\n", encoding="utf-8")
    network = ReactionNetwork([Reaction(("A",), ("B",), 4)])

    class ReplacingStore:
        def get(self, _path: str, _min_tp: int) -> ReactionNetwork:
            os.replace(replacement, reaction_path)
            return network

    monkeypatch.setattr(svc, "STORE", ReplacingStore())

    with pytest.raises(svc.ServiceError) as caught:
        svc.build_mechanism_elements(
            reaction_artifacts,
            anchor_smiles="A",
        )
    assert caught.value.reason == "reaction_source_stale"


def test_csv_exports_have_exact_columns_and_round_trip_structured_cells(
    indexed_artifacts: dict[str, str],
) -> None:
    payload = svc.build_mechanism_elements(
        indexed_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    node_csv = svc.export_mechanism_graph(payload, "node-csv")
    edge_csv = svc.export_mechanism_graph(payload, "edge-csv")

    assert isinstance(node_csv, str)
    assert isinstance(edge_csv, str)
    node_reader = csv.DictReader(io.StringIO(node_csv))
    edge_reader = csv.DictReader(io.StringIO(edge_csv))
    assert node_reader.fieldnames == NODE_COLUMNS
    assert edge_reader.fieldnames == EDGE_COLUMNS
    nodes = list(node_reader)
    edges = list(edge_reader)
    reaction = next(row for row in nodes if row["kind"] == "reaction")
    assert json.loads(reaction["reactants_json"]) == ["[H]", "[O]"]
    assert json.loads(reaction["products_json"]) == ["[H][O]"]
    assert reaction["event_coverage"] == "0.8"
    assert len(edges) == len(payload["edges"])


def test_csv_export_quotes_delimiters_newlines_and_unicode() -> None:
    payload = {
        "schema_version": "reacnet-scope/mechanism-network/v1",
        "network_semantics": "mechanism",
        "evidence_level": "reaction_passage_counts",
        "anchor_smiles": "=A,一\n二",
        "query": {},
        "source_signatures": {},
        "nodes": [
            {
                "id": "species:1",
                "kind": "species",
                "label": "=A,一\n二",
                "smiles": "=A,一\n二",
                "formula": None,
            }
        ],
        "edges": [],
        "meta": {},
    }

    exported = svc.export_mechanism_graph(payload, "node-csv")
    restored = list(csv.DictReader(io.StringIO(exported)))

    assert restored[0]["label"] == "=A,一\n二"
    assert restored[0]["smiles"] == "=A,一\n二"
    assert restored[0]["formula"] == ""


def test_graph_exports_use_stored_payload_and_retain_metadata(
    reaction_artifacts: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = svc.build_mechanism_elements(
        reaction_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("export must not rebuild the mechanism")

    monkeypatch.setattr(svc, "STORE", forbidden)
    cytoscape = svc.export_mechanism_graph(payload, "cytoscape-json")
    graphml = svc.export_mechanism_graph(payload, "graphml")
    gexf = svc.export_mechanism_graph(payload, "gexf")

    assert isinstance(cytoscape, dict)
    assert cytoscape["data"]["schema_version"] == payload["schema_version"]
    assert cytoscape["data"]["network_semantics"] == "mechanism"
    assert cytoscape["data"]["evidence_level"] == payload["evidence_level"]
    assert cytoscape["data"]["query"] == payload["query"]
    assert cytoscape["data"]["source_signatures"] == payload["source_signatures"]
    assert isinstance(graphml, bytes)
    assert isinstance(gexf, bytes)
    assert len(nx.read_graphml(io.BytesIO(graphml))) == len(payload["nodes"])
    assert len(nx.read_gexf(io.BytesIO(gexf))) == len(payload["nodes"])


def test_export_rejects_unknown_format_with_all_supported_names(
    reaction_artifacts: dict[str, str],
) -> None:
    payload = svc.build_mechanism_elements(
        reaction_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    with pytest.raises(ValueError) as caught:
        svc.export_mechanism_graph(payload, "json")

    assert all(
        name in str(caught.value)
        for name in (
            "cytoscape-json",
            "graphml",
            "gexf",
            "node-csv",
            "edge-csv",
        )
    )
