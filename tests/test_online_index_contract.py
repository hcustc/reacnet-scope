from __future__ import annotations

import builtins
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from reacnet_scope.indexes import (
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    RouteIndexStore,
    TrajectoryIndexStore,
    clear_index,
)
from scripts.webapp.server import read_trajectory_requested_frame_blocks


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


class OnlineIndexContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.previous_cache = os.environ.get("REACNET_SCOPE_CACHE_DIR")
        os.environ["REACNET_SCOPE_CACHE_DIR"] = str(self.cache)

    def tearDown(self) -> None:
        if self.previous_cache is None:
            os.environ.pop("REACNET_SCOPE_CACHE_DIR", None)
        else:
            os.environ["REACNET_SCOPE_CACHE_DIR"] = self.previous_cache
        self.temp.cleanup()

    def test_missing_indexes_fail_fast_without_writes(self) -> None:
        route = self.root / "run.route"
        trajectory = self.root / "run.lammpstrj"
        route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
        trajectory.write_bytes(_frame(0))
        started = time.monotonic()
        with self.assertRaises(IndexNotReadyError):
            RouteIndexStore().open_required(str(route))
        with self.assertRaises(IndexNotReadyError):
            TrajectoryIndexStore().open_required(str(trajectory))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(self.cache.exists())

    def test_route_query_never_opens_route_source(self) -> None:
        route = self.root / "run.route"
        route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
        store = RouteIndexStore()
        store.build(str(route))
        real_open = builtins.open

        def guarded_open(file, *args, **kwargs):
            if os.path.abspath(os.fspath(file)) == os.path.abspath(route):
                raise AssertionError("online Route query opened the source .route file")
            return real_open(file, *args, **kwargs)

        query = {
            "reactant_token_set": {"C"},
            "product_token_set": {"O"},
            "match_mode": "canonical_smiles",
        }
        with mock.patch("builtins.open", side_effect=guarded_open):
            result = store.query_reaction_hits(str(route), query, max_hits=10)
        self.assertEqual(result["matched_atom_transitions"], 1)

    def test_changed_source_is_reported_stale(self) -> None:
        route = self.root / "run.route"
        route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
        store = RouteIndexStore()
        store.build(str(route))
        route.write_text("Atom 1 C: 0 C -> 20 O\n", encoding="utf-8")
        with self.assertRaises(IndexStaleError):
            store.open_required(str(route))
        self.assertEqual(store.status(str(route))["state"], "stale")

    def test_truncated_index_is_invalid_even_if_manifest_would_be_ready(self) -> None:
        route = self.root / "run.route"
        route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
        store = RouteIndexStore()
        result = store.build(str(route))
        Path(result["index_path"]).write_bytes(b"truncated")
        with self.assertRaises(IndexInvalidError):
            store.open_required(str(route))
        self.assertEqual(store.status(str(route))["state"], "invalid")

    def test_clear_index_removes_only_current_dataset_cache(self) -> None:
        route = self.root / "run.route"
        route.write_text("Atom 1 C: 0 C -> 10 O\n", encoding="utf-8")
        result = RouteIndexStore().build(str(route))
        cleared = clear_index(str(route), kind="route")
        self.assertEqual(cleared["kind"], "route")
        self.assertGreater(cleared["released_bytes"], 0)
        self.assertIn(result["index_path"], cleared["removed"])
        self.assertTrue(route.is_file())
        self.assertFalse(Path(result["index_path"]).exists())

    def test_trajectory_read_uses_bounded_seek_and_read(self) -> None:
        trajectory = self.root / "run.lammpstrj"
        frame_bytes = [_frame(frame) for frame in (0, 10, 20)]
        trajectory.write_bytes(b"".join(frame_bytes))
        TrajectoryIndexStore().build(str(trajectory))
        real_open = builtins.open
        counters = {"open": 0, "seek": 0, "read": 0, "bytes": 0, "iter": 0}

        class MonitoredFile:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def __enter__(self):
                self.wrapped.__enter__()
                return self

            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)

            def seek(self, *args):
                counters["seek"] += 1
                return self.wrapped.seek(*args)

            def read(self, *args):
                counters["read"] += 1
                value = self.wrapped.read(*args)
                counters["bytes"] += len(value)
                return value

            def __iter__(self):
                counters["iter"] += 1
                return iter(self.wrapped)

        def monitored_open(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            if os.path.abspath(os.fspath(file)) == os.path.abspath(trajectory):
                counters["open"] += 1
                return MonitoredFile(handle)
            return handle

        with mock.patch("builtins.open", side_effect=monitored_open):
            blocks = read_trajectory_requested_frame_blocks(str(trajectory), [10])
        self.assertEqual(set(blocks), {10})
        self.assertEqual(counters["open"], 1)
        self.assertEqual(counters["seek"], 1)
        self.assertEqual(counters["read"], 1)
        self.assertEqual(counters["bytes"], len(frame_bytes[1]))
        self.assertEqual(counters["iter"], 0)
        self.assertEqual(list(self.cache.rglob("*.building")), [])
        self.assertEqual(list(self.cache.rglob("*-wal")), [])


if __name__ == "__main__":
    unittest.main()
