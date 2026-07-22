from __future__ import annotations

import sqlite3
import os

from reacnet_scope.indexes import ROUTE_INDEX_STORE, _legacy_route_index_path, route_index_path
from scripts.webapp.server import RouteTransitionIndexStore, route_transition_index_path


def _route_lines() -> list[str]:
    return [
        "Atom 1 C: 0 C -> 10 O\n",
        "Atom 2 C: 0 C -> 10 O\n",
        "Atom 3 C: 0 C -> 10 O\n",
    ]


def test_route_index_is_persisted_in_configured_cache(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "nvme-cache"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(cache_dir))
    route = tmp_path / "sample.route"
    route.write_text("".join(_route_lines()), encoding="utf-8")

    first_store = RouteTransitionIndexStore()
    built = first_store.get(str(route))
    assert built["index_state"] == "built"
    assert str(built["index_path"]).startswith(str(cache_dir / "datasets"))
    assert first_store.status(str(route))["state"] == "ready"

    second_store = RouteTransitionIndexStore()
    reused = second_store.get(str(route))
    assert reused["index_state"] == "cached_disk"
    assert reused["indexed_transitions"] == 3

    removed = second_store.clear(str(route))
    assert removed == [str(built["index_path"])]
    assert second_store.status(str(route))["state"] == "missing"


def test_route_index_resumes_from_committed_source_offset(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "nvme-cache"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(cache_dir))
    route = tmp_path / "resume.route"
    lines = _route_lines()
    route.write_text("".join(lines), encoding="utf-8")
    stat = route.stat()
    store = RouteTransitionIndexStore()
    index_path = route_transition_index_path(
        str(route),
        mtime=stat.st_mtime,
        size=stat.st_size,
    )
    building_path = index_path.with_suffix(index_path.suffix + ".building")
    conn = store._connect_for_build(building_path)
    conn.execute(
        """
        INSERT INTO transitions(
            atom_id, start_frame, end_frame,
            from_label, to_label,
            from_canonical, to_canonical,
            from_formula, to_formula
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 0, 10, "C", "O", "C", "O", "C", "O"),
    )
    store._checkpoint_route_build(
        conn,
        route_file=str(route),
        mtime=stat.st_mtime,
        size=stat.st_size,
        source_offset=len(lines[0].encode("utf-8")),
        scanned_atoms=1,
        indexed_transitions=1,
    )
    conn.close()

    result = store.get(str(route))
    assert result["resumed"] is True
    assert result["scanned_atoms"] == 3
    assert result["indexed_transitions"] == 3
    with sqlite3.connect(result["index_path"]) as final_conn:
        assert final_conn.execute("SELECT COUNT(*) FROM transitions").fetchone()[0] == 3
        assert dict(final_conn.execute("SELECT key, value FROM meta"))["build_state"] == "ready"


def test_legacy_route_index_is_readable_then_migrated_without_rebuilding(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "nvme-cache"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(cache_dir))
    route = tmp_path / "case.lammpstrj.route"
    route.write_text("".join(_route_lines()), encoding="utf-8")
    ROUTE_INDEX_STORE.build(str(route))
    current = route_index_path(str(route))
    legacy = _legacy_route_index_path(str(route))
    legacy.parent.mkdir(parents=True, exist_ok=True)
    os.replace(current, legacy)

    status = ROUTE_INDEX_STORE.status(str(route))
    assert status["state"] == "ready"
    assert status["index_path"] == str(legacy)
    assert ROUTE_INDEX_STORE.open_required(str(route))["indexed_transitions"] == 3

    migrated = ROUTE_INDEX_STORE.build(str(route))
    assert migrated["index_path"] == str(current)
    assert current.is_file()
    assert not legacy.exists()
