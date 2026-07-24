from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.indexes import (
    IndexInvalidError,
    IndexStaleError,
    event_evidence_index_path,
    resolve_dataset_paths,
)


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
