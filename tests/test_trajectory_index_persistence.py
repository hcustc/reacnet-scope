from __future__ import annotations

from scripts.webapp.server import TrajectoryIndexStore


def _frame(frame: int) -> bytes:
    return (
        "ITEM: TIMESTEP\n"
        f"{frame}\n"
        "ITEM: NUMBER OF ATOMS\n"
        "1\n"
        "ITEM: BOX BOUNDS pp pp pp\n"
        "0 10\n0 10\n0 10\n"
        "ITEM: ATOMS id type x y z\n"
        "1 1 1 1 1\n"
    ).encode("utf-8")


def test_trajectory_index_persists_and_reloads(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "nvme-cache"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(cache_dir))
    trajectory = tmp_path / "sample.lammpstrj"
    trajectory.write_bytes(b"".join(_frame(frame) for frame in (0, 10, 20)))

    first_store = TrajectoryIndexStore()
    built = first_store.get(str(trajectory))
    assert built.frames == [0, 10, 20]
    assert first_store.status(str(trajectory))["state"] == "ready"

    second_store = TrajectoryIndexStore()
    reused = second_store.get(str(trajectory))
    assert reused.frames == [0, 10, 20]
    assert reused.frame_offsets == built.frame_offsets

    removed = second_store.clear(str(trajectory))
    assert len(removed) == 1
    assert second_store.status(str(trajectory))["state"] == "missing"


def test_trajectory_index_resumes_from_checkpoint(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "nvme-cache"
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(cache_dir))
    trajectory = tmp_path / "resume.lammpstrj"
    first = _frame(0)
    trajectory.write_bytes(first + _frame(10) + _frame(20))
    stat = trajectory.stat()
    store = TrajectoryIndexStore()
    store._persist_build_checkpoint(
        str(trajectory),
        mtime=stat.st_mtime,
        size=stat.st_size,
        source_offset=len(first),
        frames=[0],
        frame_offsets={0: (0, len(first))},
    )

    status = store.status(str(trajectory))
    assert status["state"] == "building"
    assert status["frames"] == 1
    resumed = store.get(str(trajectory))
    assert resumed.frames == [0, 10, 20]
    assert resumed.frame_offsets[0] == (0, len(first))
    assert store.status(str(trajectory))["state"] == "ready"

