from __future__ import annotations

import builtins
import copy
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
from scripts.webapp import server as legacy_server
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


def _hardlink_rewrite_preserving_size_and_mtime(
    path: Path,
    link: Path,
    text: str,
) -> None:
    before = path.stat()
    os.link(path, link)
    with link.open("r+b") as handle:
        handle.write(text.encode("utf-8"))
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns


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
        def get_with_signature(
            self,
            path: str,
            min_tp: int,
        ) -> tuple[ReactionNetwork, dict[str, Any]]:
            calls.append((path, min_tp))
            return network, legacy_server.reaction_source_signature(path)

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


@pytest.mark.parametrize("service_name", ["mechanism", "pathway"])
def test_get_only_store_cannot_pair_stale_network_with_current_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [C]\n")
    stale_network = ReactionNetwork(
        [Reaction(("[H]",), ("[O]",), 4)]
    )

    class StaleLegacyStore:
        def get(self, _path: str, _min_tp: int) -> ReactionNetwork:
            return stale_network

    monkeypatch.setattr(svc, "STORE", StaleLegacyStore())
    artifacts = {"reaction": str(reaction)}
    if service_name == "mechanism":
        payload = svc.build_mechanism_elements(
            artifacts,
            anchor_smiles="[H]",
            max_depth=1,
        )
        species = {
            node["smiles"]
            for node in payload["nodes"]
            if node["kind"] == "species"
        }
    else:
        payload = svc.find_pathways(
            artifacts,
            "[H]",
            max_depth=1,
        )
        species = set(payload["paths"][0]["species"])

    assert species == {"[H]", "[C]"}
    assert payload["source_signatures"]["reactionabcd"] == (
        legacy_server.reaction_source_signature(str(reaction))
    )


@pytest.mark.parametrize("service_name", ["mechanism", "pathway"])
def test_get_only_store_invalid_utf8_maps_to_bad_reac(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
) -> None:
    reaction = tmp_path / "run.reactionabcd"
    reaction.write_bytes(b"4 A -> B\n\xff")

    class ForbiddenLegacyStore:
        def get(self, *_args: Any) -> ReactionNetwork:
            raise AssertionError("unverifiable get must not be called")

    monkeypatch.setattr(svc, "STORE", ForbiddenLegacyStore())
    with pytest.raises(svc.ServiceError) as caught:
        if service_name == "mechanism":
            svc.build_mechanism_elements(
                {"reaction": str(reaction)},
                anchor_smiles="A",
            )
        else:
            svc.find_pathways(
                {"reaction": str(reaction)},
                "A",
            )

    assert caught.value.reason == "bad_reac"


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
        def get_with_signature(
            self,
            _path: str,
            _min_tp: int,
        ) -> tuple[ReactionNetwork, dict[str, Any]]:
            signature = legacy_server.reaction_source_signature(
                str(reaction_path)
            )
            os.replace(replacement, reaction_path)
            return network, signature

    monkeypatch.setattr(svc, "STORE", ReplacingStore())

    with pytest.raises(svc.ServiceError) as caught:
        svc.build_mechanism_elements(
            reaction_artifacts,
            anchor_smiles="A",
        )
    assert caught.value.reason == "reaction_source_stale"


def test_same_metadata_atomic_replacement_invalidates_mechanism_cache(
    tmp_path: Path,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [O]\n")
    artifacts = {"reaction": str(reaction)}

    first = svc.build_mechanism_elements(
        artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )
    _atomic_replace_preserving_size_and_mtime(
        reaction,
        "4 [H] -> [C]\n",
    )
    second = svc.build_mechanism_elements(
        artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )

    first_species = {
        node["smiles"]
        for node in first["nodes"]
        if node["kind"] == "species"
    }
    second_species = {
        node["smiles"]
        for node in second["nodes"]
        if node["kind"] == "species"
    }
    assert first_species == {"[H]", "[O]"}
    assert second_species == {"[C]", "[H]"}
    assert first["source_signatures"]["reactionabcd"]["sha256"] != (
        second["source_signatures"]["reactionabcd"]["sha256"]
    )


def test_content_digest_invalidates_coarse_metadata_hardlink_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [O]\n")
    store = legacy_server.NetworkStore()
    fixed_identity = legacy_server._reaction_source_identity(str(reaction))
    monkeypatch.setattr(
        legacy_server,
        "_reaction_source_identity",
        lambda _path: fixed_identity,
    )

    first = store.get(str(reaction), 1)
    _hardlink_rewrite_preserving_size_and_mtime(
        reaction,
        tmp_path / "run.hardlink",
        "4 [H] -> [C]\n",
    )
    second = store.get(str(reaction), 1)

    assert first.reactions[0].product_smiles == ("[O]",)
    assert second.reactions[0].product_smiles == ("[C]",)
    assert second is not first


@pytest.mark.parametrize("service_name", ["mechanism", "pathway"])
def test_shared_services_export_digest_for_hardlink_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [O]\n")
    artifacts = {"reaction": str(reaction)}
    monkeypatch.setattr(svc, "STORE", legacy_server.NetworkStore())

    if service_name == "mechanism":
        call = lambda: svc.build_mechanism_elements(  # noqa: E731
            artifacts,
            anchor_smiles="[H]",
            max_depth=1,
        )
    else:
        call = lambda: svc.find_pathways(  # noqa: E731
            artifacts,
            "[H]",
            max_depth=1,
        )
    first = call()
    _hardlink_rewrite_preserving_size_and_mtime(
        reaction,
        tmp_path / f"{service_name}.hardlink",
        "4 [H] -> [C]\n",
    )
    second = call()

    first_signature = first["source_signatures"]["reactionabcd"]
    second_signature = second["source_signatures"]["reactionabcd"]
    assert len(first_signature["sha256"]) == 64
    assert first_signature["sha256"] != second_signature["sha256"]


def test_reaction_replacement_during_store_load_is_exact_stale_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [O]\n")
    replacement = tmp_path / "replacement.reactionabcd"
    replacement.write_text("4 [H] -> [C]\n", encoding="utf-8")
    before = reaction.stat()
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    real_parse = legacy_server.parse_reactionabcd

    def replacing_parse(path: str, *, min_tp: int) -> list[Reaction]:
        parsed = real_parse(path, min_tp=min_tp)
        os.replace(replacement, reaction)
        return parsed

    monkeypatch.setattr(legacy_server, "parse_reactionabcd", replacing_parse)
    monkeypatch.setattr(svc, "STORE", legacy_server.NetworkStore())

    with pytest.raises(svc.ServiceError) as caught:
        svc.build_mechanism_elements(
            {"reaction": str(reaction)},
            anchor_smiles="[H]",
            max_depth=1,
        )

    assert caught.value.reason == "reaction_source_stale"


@pytest.mark.parametrize("service_name", ["mechanism", "pathway"])
def test_hardlink_rewrite_during_snapshot_load_is_exact_stale_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
) -> None:
    reaction = _write_reaction_file(tmp_path, "4 [H] -> [O]\n")
    hardlink = tmp_path / "during-load.hardlink"
    os.link(reaction, hardlink)
    before = reaction.stat()
    real_parse = legacy_server.parse_reactionabcd
    rewrote = False

    def replacing_parse(source: Any, *, min_tp: int) -> list[Reaction]:
        nonlocal rewrote
        parsed = real_parse(source, min_tp=min_tp)
        if not rewrote:
            rewrote = True
            with hardlink.open("r+b") as handle:
                handle.write(b"4 [H] -> [C]\n")
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            os.utime(
                reaction,
                ns=(before.st_atime_ns, before.st_mtime_ns),
            )
        return parsed

    monkeypatch.setattr(legacy_server, "parse_reactionabcd", replacing_parse)
    monkeypatch.setattr(svc, "STORE", legacy_server.NetworkStore())

    with pytest.raises(svc.ServiceError) as caught:
        if service_name == "mechanism":
            svc.build_mechanism_elements(
                {"reaction": str(reaction)},
                anchor_smiles="[H]",
                max_depth=1,
            )
        else:
            svc.find_pathways(
                {"reaction": str(reaction)},
                "[H]",
                max_depth=1,
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
    payload = svc.build_mechanism_network(
        ReactionNetwork(
            [Reaction(("=A,一\n二",), ("终点",), 1)]
        ),
        anchor_smiles="=A,一\n二",
        max_depth=1,
    )
    species = next(
        node
        for node in payload["nodes"]
        if node.get("smiles") == "=A,一\n二"
    )
    species["label"] = "=A,一\n二"
    species["formula"] = None

    exported = svc.export_mechanism_graph(payload, "node-csv")
    restored = list(csv.DictReader(io.StringIO(exported)))
    row = next(item for item in restored if "=A,一\n二" in item["smiles"])

    assert row["label"] == "'=A,一\n二"
    assert row["smiles"] == "'=A,一\n二"
    assert row["formula"] == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+SUM(A1:A2)", "'+SUM(A1:A2)"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
        (" =1+1", "' =1+1"),
        ("\t=1+1", "'\t=1+1"),
        ("\r=1+1", "'\r=1+1"),
        ("\n=1+1", "'\n=1+1"),
        ("普通文本", "普通文本"),
    ],
)
def test_csv_export_neutralizes_spreadsheet_formulas(
    value: str,
    expected: str,
) -> None:
    payload = svc.build_mechanism_network(
        ReactionNetwork([Reaction((value,), ("终点",), 1)]),
        anchor_smiles=value,
        max_depth=1,
    )
    species = next(
        node
        for node in payload["nodes"]
        if node.get("smiles") == value
    )
    species["label"] = value
    species["formula"] = value
    before = copy.deepcopy(payload)

    exported = svc.export_mechanism_graph(payload, "node-csv")
    restored = list(csv.DictReader(io.StringIO(exported)))
    row = next(item for item in restored if item["smiles"] == expected)

    assert row["label"] == expected
    assert row["smiles"] == expected
    assert row["formula"] == expected
    assert payload == before


def test_csv_export_preserves_numeric_negative_values() -> None:
    # Schema counts are nonnegative, but the low-level spreadsheet guard must
    # still preserve numeric values rather than treating them as formulas.
    assert svc._mechanism_csv_value(-1) == -1


@pytest.mark.parametrize(
    "format_name",
    ["cytoscape-json", "graphml", "gexf", "node-csv", "edge-csv"],
)
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_schema", "schema_version"),
        ("event_transfer", "network_semantics"),
        ("missing_node_field", "label"),
        ("dangling_edge", "unknown target"),
        ("missing_edge_field", "coefficient"),
        ("evidence_contradiction", "evidence_level"),
        ("missing_semantic_edge", "semantic edges"),
    ],
)
def test_all_exports_share_complete_mechanism_payload_validation(
    reaction_artifacts: dict[str, str],
    format_name: str,
    mutation: str,
    message: str,
) -> None:
    payload = svc.build_mechanism_elements(
        reaction_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )
    tampered = copy.deepcopy(payload)
    if mutation == "missing_schema":
        tampered.pop("schema_version")
    elif mutation == "event_transfer":
        tampered["network_semantics"] = "event_transfer"
    elif mutation == "missing_node_field":
        tampered["nodes"][0].pop("label")
    elif mutation == "dangling_edge":
        tampered["edges"][0]["target"] = "reaction:missing"
    elif mutation == "missing_edge_field":
        tampered["edges"][0].pop("coefficient")
    elif mutation == "evidence_contradiction":
        tampered["evidence_level"] = "event_evidence_linked"
    else:
        tampered["edges"].pop()

    with pytest.raises(ValueError, match=message):
        svc.export_mechanism_graph(tampered, format_name)


@pytest.mark.parametrize(
    "format_name",
    ["cytoscape-json", "graphml", "gexf", "node-csv", "edge-csv"],
)
def test_all_exports_reject_relabelled_species_with_old_stable_id(
    reaction_artifacts: dict[str, str],
    format_name: str,
) -> None:
    payload = svc.build_mechanism_elements(
        reaction_artifacts,
        anchor_smiles="[H]",
        max_depth=1,
    )
    tampered = copy.deepcopy(payload)
    species = next(
        node for node in tampered["nodes"]
        if node["kind"] == "species"
    )
    species["smiles"] = "整体改标"

    with pytest.raises(ValueError, match="stable identity"):
        svc.export_mechanism_graph(tampered, format_name)


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


def test_mechanism_cytoscape_reaction_retains_dash_detail_fields(
    tmp_path: Path,
) -> None:
    reaction = _write_reaction_file(
        tmp_path,
        "9 [H] + [O] + [O] -> [H][O] + [O]\n"
        "2 [H][O] + [O] -> [H] + [O] + [O]\n",
    )

    payload = svc.build_mechanism_elements(
        {"reaction": str(reaction)},
        anchor_smiles="[H]",
        max_depth=1,
    )

    node = next(
        item["data"]
        for item in payload["elements"]
        if item["data"].get("kind") == "reaction"
    )
    assert node["reactants"] == ["[H]", "[O]", "[O]"]
    assert node["products"] == ["[H][O]", "[O]"]
    assert node["forward_tp"] == 9
    assert node["reverse_tp"] == 2
    assert node["net_tp"] == 7
    assert node["evidence_status"] == "network_only"
    assert node["event_total"] is None
    assert node["matched_event_total"] is None
    assert node["event_coverage"] is None
