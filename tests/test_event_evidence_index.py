from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import reacnet_scope.event_index as event_index_module
import reacnet_scope.rng_events as rng_events
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import (
    IndexInvalidError,
    IndexStaleError,
    event_evidence_index_path,
    resolve_dataset_paths,
)
from reacnet_scope.rng_events import RngEventDataError


REACTION_KEY = "[H]+[O]->[H][O]"


def write_rng_fixture(tmp_path: Path) -> tuple[Path, Path]:
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


def test_event_index_uses_dataset_local_cache_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, _molecules = write_rng_fixture(tmp_path)

    paths = resolve_dataset_paths(tmp_path, "run.lammpstrj")

    assert paths.event_index == paths.cache_dir / "events.sqlite3"
    assert event_evidence_index_path(str(reactionevent)) == paths.event_index


def test_event_store_publishes_dataset_local_index_and_pages(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)

    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    assert built["state"] == "ready"
    assert Path(built["index_path"]).name == "events.sqlite3"
    assert EVENT_EVIDENCE_STORE.open_required(
        str(reactionevent), str(molecules)
    )["query_only"] is True

    first = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent), str(molecules), REACTION_KEY, limit=1
    )
    second = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent), str(molecules), REACTION_KEY, limit=1, offset=1
    )

    assert first["total"] == 2
    assert first["limit"] == 1
    assert first["offset"] == 0
    assert first["evidence_status"] == "evidence_linked"
    assert first["rows"][0]["event_id"] != second["rows"][0]["event_id"]
    assert first["rows"][0]["atom_id_list"] == [1, 2]
    assert first["rows"][0]["product_bonds"] == "1-2-1"
    assert [row["association_status"] for row in first["rows"] + second["rows"]] == [
        "matched",
        "unresolved_hmm_timeline",
    ]


def test_builder_only_runs_global_counts_once_at_finalization(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    statements: list[str] = []
    real_connect = EVENT_EVIDENCE_STORE._connect_for_build

    def traced_connect(target):
        connection = real_connect(target)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(
        EVENT_EVIDENCE_STORE, "_connect_for_build", traced_connect
    )

    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    global_counts = [
        " ".join(statement.upper().split())
        for statement in statements
        if "SELECT COUNT(*) FROM EVENTS" in statement.upper()
        or "SELECT COUNT(*) FROM REACTION_SUMMARY" in statement.upper()
    ]
    assert global_counts == [
        "SELECT COUNT(*) FROM EVENTS",
        "SELECT COUNT(*) FROM REACTION_SUMMARY",
    ]


def test_event_store_summarizes_known_reactions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    summaries = EVENT_EVIDENCE_STORE.reaction_summary(
        str(reactionevent),
        str(molecules),
        [REACTION_KEY, "unknown->reaction"],
    )

    assert set(summaries) == {REACTION_KEY}
    assert summaries[REACTION_KEY] == {
        "reaction_key": REACTION_KEY,
        "total_events": 2,
        "matched_events": 1,
        "distinct_intervals": 1,
        "available_intervals": 2,
    }


def test_event_status_distinguishes_source_and_index_states(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    missing = tmp_path / "missing.reactionevent.csv"
    missing_molecules = tmp_path / "missing.molecules.csv"
    assert EVENT_EVIDENCE_STORE.status(str(missing), str(missing_molecules))[
        "state"
    ] == "missing_source"

    reactionevent, molecules = write_rng_fixture(tmp_path)
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "missing"

    building_path = Path(f"{event_evidence_index_path(str(reactionevent))}.building")
    building_path.parent.mkdir(parents=True, exist_ok=True)
    building_path.touch()
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "building"


def test_changed_event_source_is_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    reactionevent.write_text(
        reactionevent.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(IndexStaleError):
        EVENT_EVIDENCE_STORE.open_required(str(reactionevent), str(molecules))
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "stale"


def test_incomplete_event_index_is_invalid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute("DROP TABLE reaction_summary")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError):
        EVENT_EVIDENCE_STORE.open_required(str(reactionevent), str(molecules))
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "invalid"


def test_event_index_with_missing_required_column_is_invalid(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute("ALTER TABLE events RENAME TO original_events")
        connection.execute(
            "CREATE TABLE events(event_id TEXT PRIMARY KEY, reaction_key TEXT)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError, match="columns"):
        EVENT_EVIDENCE_STORE.open_required(str(reactionevent), str(molecules))


def test_malformed_event_json_is_reported_as_invalid_index(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute("UPDATE events SET atom_ids_json='not-json'")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError, match="payload"):
        EVENT_EVIDENCE_STORE.query_events(
            str(reactionevent), str(molecules), REACTION_KEY, limit=1
        )


def test_structurally_invalid_event_json_is_reported_as_invalid_index(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute("UPDATE events SET atom_ids_json='{}'")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError, match="list"):
        EVENT_EVIDENCE_STORE.query_events(
            str(reactionevent), str(molecules), REACTION_KEY, limit=1
        )


def test_corrupt_numeric_metadata_reports_invalid_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute(
            "UPDATE meta SET value='bad' WHERE key='event_count'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError, match="event_count"):
        EVENT_EVIDENCE_STORE.open_required(
            str(reactionevent), str(molecules)
        )
    assert EVENT_EVIDENCE_STORE.status(
        str(reactionevent), str(molecules)
    )["state"] == "invalid"


def test_reaction_summary_translates_invalid_numeric_payload(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    connection = sqlite3.connect(built["index_path"])
    try:
        connection.execute(
            "UPDATE reaction_summary SET total_events='bad'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(IndexInvalidError, match="summary"):
        EVENT_EVIDENCE_STORE.reaction_summary(
            str(reactionevent), str(molecules), [REACTION_KEY]
        )


@pytest.mark.parametrize("value", [True, False, 4.9, b"4"])
def test_strict_integer_parser_rejects_non_integer_values(value) -> None:
    with pytest.raises(IndexInvalidError, match="count"):
        event_index_module._strict_int(value, "count", minimum=0)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        " 10",
        "10 ",
        "1_0",
        "１０",
        "+10",
        "-",
    ],
)
def test_strict_integer_parser_rejects_noncanonical_text(value: str) -> None:
    with pytest.raises(IndexInvalidError, match="count"):
        event_index_module._strict_int(value, "count")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0),
        ("10", 10),
        ("0010", 10),
        ("-10", -10),
    ],
)
def test_strict_integer_parser_accepts_ascii_decimal_text(
    value: str,
    expected: int,
) -> None:
    assert event_index_module._strict_int(value, "count") == expected


def test_event_query_translates_sqlite_failures_to_invalid_index(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    real_readonly = event_index_module._readonly_connection
    calls = 0

    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.DatabaseError("simulated corruption")

        def close(self):
            return None

    def fail_query_connection(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            return BrokenConnection()
        return real_readonly(path)

    monkeypatch.setattr(
        event_index_module,
        "_readonly_connection",
        fail_query_connection,
    )

    with pytest.raises(IndexInvalidError, match="corrupt"):
        EVENT_EVIDENCE_STORE.query_events(
            str(reactionevent), str(molecules), REACTION_KEY, limit=1
        )


def test_changed_components_has_public_compatibility_name() -> None:
    assert rng_events.changed_components is rng_events._changed_components


def test_event_builder_resumes_after_committed_interval_without_duplicates(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    published = event_evidence_index_path(str(reactionevent))
    checkpoints = 0

    def interrupt(update):
        nonlocal checkpoints
        if update.get("phase") == "checkpoint_event_index":
            checkpoints += 1
            assert not published.exists()
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EVENT_EVIDENCE_STORE.build(
            str(reactionevent),
            str(molecules),
            progress_callback=interrupt,
        )

    assert checkpoints == 1
    assert EVENT_EVIDENCE_STORE.status(str(reactionevent), str(molecules))[
        "state"
    ] == "building"

    result = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    rows = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent), str(molecules), REACTION_KEY, limit=100
    )["rows"]

    assert result["resumed"] is True
    assert len(rows) == 2
    assert len({row["event_id"] for row in rows}) == 2


def test_event_builder_publishes_only_a_ready_database(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    published = event_evidence_index_path(str(reactionevent))
    real_replace = event_index_module.os.replace
    replacements: list[tuple[Path, Path]] = []

    def checked_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        connection = sqlite3.connect(source_path)
        try:
            state = connection.execute(
                "SELECT value FROM meta WHERE key='build_state'"
            ).fetchone()[0]
        finally:
            connection.close()
        assert state == "ready"
        assert not target_path.exists()
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr(event_index_module.os, "replace", checked_replace)

    EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    assert replacements == [(Path(f"{published}.building"), published)]


def test_event_builder_rejects_decreasing_event_intervals(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    reactionevent.write_text(
        "Timestep_Index,Reactant,Product\n"
        "1,[H]+[O],[H][O]\n"
        "0,[H]+[O],[H][O]\n",
        encoding="utf-8",
    )

    with pytest.raises(RngEventDataError, match="sorted"):
        EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))


def test_event_builder_rejects_decreasing_molecule_timesteps(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    molecules.write_text(
        "Timestep,Species,AtomIDs,BondIDs\n"
        "10,[H],0,\n"
        "10,[O],1,\n"
        "0,[H][O],0;1,0-1-1\n",
        encoding="utf-8",
    )

    with pytest.raises(RngEventDataError, match="sorted"):
        EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))


def test_event_store_clear_removes_only_cache_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    source_bytes = reactionevent.read_bytes(), molecules.read_bytes()
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

    cleared = EVENT_EVIDENCE_STORE.clear(str(reactionevent), str(molecules))

    assert cleared["kind"] == "event"
    assert built["index_path"] in cleared["removed"]
    assert cleared["released_bytes"] > 0
    assert not Path(built["index_path"]).exists()
    assert (reactionevent.read_bytes(), molecules.read_bytes()) == source_bytes


def test_event_store_clear_works_after_sources_are_removed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)
    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    reactionevent.unlink()
    molecules.unlink()

    cleared = EVENT_EVIDENCE_STORE.clear(
        str(reactionevent), str(molecules)
    )

    assert built["index_path"] in cleared["removed"]
    assert not Path(built["index_path"]).exists()
