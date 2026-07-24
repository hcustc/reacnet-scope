from __future__ import annotations

import builtins
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope import composition as composition_module
from reacnet_scope import event_index as event_index_module
from reacnet_scope import indexes as indexes_module
from reacnet_scope.indexes import (
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
    RouteIndexStore,
    TrajectoryIndexStore,
    clear_index,
)
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts.webapp_dash import services as dash_services
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

    def test_event_query_never_opens_event_source_csvs(self) -> None:
        reactionevent = self.root / "run.lammpstrj.reactionevent.csv"
        molecules = self.root / "run.lammpstrj.molecules.csv"
        reactionevent.write_text(
            "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
            encoding="utf-8",
        )
        molecules.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            "0,[C],0,\n"
            "0,[O],1,\n"
            "10,[C][O],0;1,0-1-1\n",
            encoding="utf-8",
        )
        EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
        real_open = builtins.open
        protected = {
            os.path.abspath(reactionevent),
            os.path.abspath(molecules),
        }

        def guarded_open(file, *args, **kwargs):
            if os.path.abspath(os.fspath(file)) in protected:
                raise AssertionError("online event query opened an RNG source CSV")
            return real_open(file, *args, **kwargs)

        artifacts = {
            "reactionevent": str(reactionevent),
            "molecules": str(molecules),
        }
        with mock.patch("builtins.open", side_effect=guarded_open):
            result = dash_services.locate_rng_events(
                artifacts, "[O] + [C] -> [C][O]"
            )

        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["atom_id_list"], [1, 2])

    def test_dataset_scan_never_opens_event_source_csvs(self) -> None:
        reactionevent = self.root / "run.lammpstrj.reactionevent.csv"
        molecules = self.root / "run.lammpstrj.molecules.csv"
        reactionevent.write_text(
            "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
            encoding="utf-8",
        )
        molecules.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            "0,[C],0,\n"
            "0,[O],1,\n"
            "10,[C][O],0;1,0-1-1\n",
            encoding="utf-8",
        )
        EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
        protected = {
            os.path.abspath(reactionevent),
            os.path.abspath(molecules),
        }
        real_path_open = Path.open

        def guarded_path_open(path, *args, **kwargs):
            if os.path.abspath(path) in protected:
                raise AssertionError("dataset scan opened an RNG source CSV")
            return real_path_open(path, *args, **kwargs)

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(dir_browser, "ALLOWED_ROOTS", [self.root]), mock.patch(
            "pathlib.Path.open", new=guarded_path_open
        ):
            result = dash_services.scan_dataset(str(self.root))

        self.assertTrue(
            result["dataset"]["readiness"]["event_search"]["ready"]
        )

    def test_browser_snapshot_never_opens_reacnet_source_artifacts(self) -> None:
        base = self.root / "run.lammpstrj"
        source_paths = [
            Path(f"{base}.reactionabcd"),
            Path(f"{base}.species"),
            Path(f"{base}.moname"),
            base,
            Path(f"{base}.route"),
            Path(f"{base}.table"),
            Path(f"{base}.reactionevent.csv"),
            Path(f"{base}.molecules.csv"),
        ]
        for path in source_paths:
            path.touch()
        protected = {os.path.abspath(path) for path in source_paths}
        real_open = builtins.open
        real_path_open = Path.open

        def guarded_open(file, *args, **kwargs):
            try:
                candidate = os.path.abspath(os.fspath(file))
            except TypeError:
                candidate = ""
            if candidate in protected:
                raise AssertionError("browser snapshot opened a ReacNet source artifact")
            return real_open(file, *args, **kwargs)

        def guarded_path_open(path, *args, **kwargs):
            if os.path.abspath(path) in protected:
                raise AssertionError("browser snapshot opened a ReacNet source artifact")
            return real_path_open(path, *args, **kwargs)

        real_read_text = Path.read_text

        def guarded_read_text(path, *args, **kwargs):
            if path.name == "manifest.json":
                raise AssertionError("browser snapshot read a preparation manifest")
            return real_read_text(path, *args, **kwargs)

        def forbidden_facade(*_args, **_kwargs):
            raise AssertionError("browser snapshot invoked the preparation facade")

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(dash_services, "ALLOWED_ROOTS", [self.root]), mock.patch.object(
            dir_browser, "ALLOWED_ROOTS", [self.root]
        ), mock.patch("builtins.open", side_effect=guarded_open), mock.patch(
            "pathlib.Path.open", new=guarded_path_open
        ), mock.patch("pathlib.Path.read_text", new=guarded_read_text), mock.patch.object(
            dash_services, "dataset_preparation_status", side_effect=forbidden_facade
        ), mock.patch.object(
            dash_services, "scan_dataset", side_effect=forbidden_facade
        ), mock.patch.object(
            dash_services, "build_dataset_status_payload", side_effect=forbidden_facade
        ):
            snapshot = dash_services.browse_dataset_location(str(self.root))

        self.assertEqual(len(snapshot["datasets"]), 1)
        self.assertEqual(snapshot["datasets"][0]["score"], len(source_paths))

    def test_browser_ready_index_status_reads_sqlite_metadata_only(self) -> None:
        trajectory = self.root / "run.lammpstrj"
        species = Path(f"{trajectory}.species")
        reactionevent = Path(f"{trajectory}.reactionevent.csv")
        molecules = Path(f"{trajectory}.molecules.csv")
        trajectory.write_bytes(_frame(0))
        species.write_text("Timestep 0: [C] 1\n", encoding="utf-8")
        reactionevent.write_text(
            "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
            encoding="utf-8",
        )
        molecules.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            "0,[C],0,\n"
            "0,[O],1,\n"
            "10,[C][O],0;1,0-1-1\n",
            encoding="utf-8",
        )
        TrajectoryIndexStore().build(str(trajectory))
        SPECIES_COMPOSITION_STORE.build(str(species))
        EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

        self.assertEqual(
            TrajectoryIndexStore().status(str(trajectory)),
            TrajectoryIndexStore().status(
                str(trajectory), metadata_only=True
            ),
        )
        self.assertEqual(
            SPECIES_COMPOSITION_STORE.status(str(species)),
            SPECIES_COMPOSITION_STORE.status(
                str(species), metadata_only=True
            ),
        )

        forbidden_reads: list[str] = []
        payload_tables = {
            "events",
            "frames",
            "reaction_summary",
            "species_summary",
            "timepoints",
        }
        real_index_connection = indexes_module._readonly_connection
        real_composition_connection = composition_module._readonly_connection
        real_event_connection = event_index_module._readonly_connection

        def guard_payload_reads(connection):
            def authorize(action, arg1, _arg2, _database, _trigger):
                if action == sqlite3.SQLITE_READ and arg1 in payload_tables:
                    forbidden_reads.append(str(arg1))
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            return connection

        def guarded_index_connection(path):
            return guard_payload_reads(real_index_connection(path))

        def guarded_composition_connection(path):
            return guard_payload_reads(real_composition_connection(path))

        def guarded_event_connection(path):
            return guard_payload_reads(real_event_connection(path))

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(
            dash_services, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            dir_browser, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            indexes_module,
            "_readonly_connection",
            side_effect=guarded_index_connection,
        ), mock.patch.object(
            composition_module,
            "_readonly_connection",
            side_effect=guarded_composition_connection,
        ), mock.patch.object(
            event_index_module,
            "_readonly_connection",
            side_effect=guarded_event_connection,
        ):
            snapshot = dash_services.browse_dataset_location(str(self.root))

        self.assertEqual(
            snapshot["datasets"][0]["index_states"]["event"], "ready"
        )
        self.assertEqual(
            snapshot["datasets"][0]["index_states"]["trajectory"], "ready"
        )
        self.assertEqual(
            snapshot["datasets"][0]["index_states"]["composition"], "ready"
        )
        self.assertEqual(forbidden_reads, [])

    def test_browser_marks_corrupt_prepared_indexes_invalid(self) -> None:
        """A broken cache must not make the whole browser snapshot unusable."""
        trajectory = self.root / "run.lammpstrj"
        species = Path(f"{trajectory}.species")
        reactionevent = Path(f"{trajectory}.reactionevent.csv")
        molecules = Path(f"{trajectory}.molecules.csv")
        trajectory.write_bytes(_frame(0))
        species.write_text("Timestep 0: [C] 1\n", encoding="utf-8")
        reactionevent.write_text(
            "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
            encoding="utf-8",
        )
        molecules.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            "0,[C],0,\n"
            "0,[O],1,\n"
            "10,[C][O],0;1,0-1-1\n",
            encoding="utf-8",
        )
        trajectory_index = TrajectoryIndexStore().build(str(trajectory))
        composition_index = SPECIES_COMPOSITION_STORE.build(str(species))
        event_index = EVENT_EVIDENCE_STORE.build(
            str(reactionevent), str(molecules)
        )
        for index_path in (
            trajectory_index.index_path,
            composition_index["index_path"],
            event_index["index_path"],
        ):
            Path(index_path).write_bytes(b"not a SQLite database")

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(
            dash_services, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            dir_browser, "ALLOWED_ROOTS", [self.root]
        ):
            snapshot = dash_services.browse_dataset_location(str(self.root))

        self.assertEqual(len(snapshot["datasets"]), 1)
        self.assertEqual(
            snapshot["datasets"][0]["index_states"],
            {
                "event": "invalid",
                "trajectory": "invalid",
                "composition": "invalid",
            },
        )

    def test_browser_contains_database_errors_from_prepared_indexes(
        self,
    ) -> None:
        """A store-level SQLite error is isolated to the affected index state."""
        trajectory = self.root / "run.lammpstrj"
        species = Path(f"{trajectory}.species")
        reactionevent = Path(f"{trajectory}.reactionevent.csv")
        molecules = Path(f"{trajectory}.molecules.csv")
        trajectory.write_bytes(_frame(0))
        species.write_text("Timestep 0: [C] 1\n", encoding="utf-8")
        reactionevent.write_text(
            "Timestep_Index,Reactant,Product\n0,[C]+[O],[C][O]\n",
            encoding="utf-8",
        )
        molecules.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            "0,[C],0,\n"
            "0,[O],1,\n"
            "10,[C][O],0;1,0-1-1\n",
            encoding="utf-8",
        )
        TrajectoryIndexStore().build(str(trajectory))
        SPECIES_COMPOSITION_STORE.build(str(species))
        EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))

        def corrupt_connection(*_args, **_kwargs):
            raise sqlite3.DatabaseError("simulated corrupt SQLite index")

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(
            dash_services, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            dir_browser, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            event_index_module,
            "_readonly_connection",
            side_effect=corrupt_connection,
        ), mock.patch.object(
            indexes_module,
            "_readonly_connection",
            side_effect=corrupt_connection,
        ), mock.patch.object(
            composition_module,
            "_readonly_connection",
            side_effect=corrupt_connection,
        ):
            snapshot = dash_services.browse_dataset_location(str(self.root))

        self.assertEqual(
            snapshot["datasets"][0]["index_states"],
            {
                "event": "invalid",
                "trajectory": "invalid",
                "composition": "invalid",
            },
        )

    def test_browser_reports_malformed_trajectory_metadata_as_invalid(
        self,
    ) -> None:
        trajectory = self.root / "run.lammpstrj"
        trajectory.write_bytes(_frame(0))
        built = TrajectoryIndexStore().build(str(trajectory))
        connection = sqlite3.connect(built.index_path)
        try:
            connection.execute(
                "UPDATE meta SET value='bad' WHERE key='frame_count'"
            )
            connection.commit()
        finally:
            connection.close()

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(
            dash_services, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            dir_browser, "ALLOWED_ROOTS", [self.root]
        ):
            snapshot = dash_services.browse_dataset_location(str(self.root))

        self.assertEqual(
            snapshot["datasets"][0]["index_states"]["trajectory"], "invalid"
        )

    def test_browser_reports_malformed_composition_metadata_as_invalid(
        self,
    ) -> None:
        trajectory = self.root / "run.lammpstrj"
        species = Path(f"{trajectory}.species")
        species.write_text("Timestep 0: [C] 1\n", encoding="utf-8")
        built = SPECIES_COMPOSITION_STORE.build(str(species))
        connection = sqlite3.connect(built["index_path"])
        try:
            connection.execute(
                "UPDATE meta SET value='bad' WHERE key='timepoint_count'"
            )
            connection.commit()
        finally:
            connection.close()

        import rng_tools.dir_browser as dir_browser

        with mock.patch.object(
            dash_services, "ALLOWED_ROOTS", [self.root]
        ), mock.patch.object(
            dir_browser, "ALLOWED_ROOTS", [self.root]
        ):
            snapshot = dash_services.browse_dataset_location(str(self.root))

        self.assertEqual(
            snapshot["datasets"][0]["index_states"]["composition"], "invalid"
        )

    def test_metadata_only_status_validates_schema_and_table_metadata(
        self,
    ) -> None:
        trajectory = self.root / "run.lammpstrj"
        species = Path(f"{trajectory}.species")
        trajectory.write_bytes(_frame(0))
        species.write_text("Timestep 0: [C] 1\n", encoding="utf-8")
        trajectory_index = TrajectoryIndexStore().build(str(trajectory))
        composition_index = SPECIES_COMPOSITION_STORE.build(str(species))

        connection = sqlite3.connect(trajectory_index.index_path)
        try:
            connection.execute(
                "UPDATE meta SET value='999' WHERE key='schema_version'"
            )
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(composition_index["index_path"])
        try:
            connection.execute("DROP TABLE species_summary")
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(
            TrajectoryIndexStore().status(
                str(trajectory), metadata_only=True
            )["state"],
            "invalid",
        )
        self.assertEqual(
            SPECIES_COMPOSITION_STORE.status(
                str(species), metadata_only=True
            )["state"],
            "invalid",
        )

    def test_metadata_only_status_validates_source_signatures(self) -> None:
        trajectory = self.root / "run.lammpstrj"
        species = Path(f"{trajectory}.species")
        trajectory.write_bytes(_frame(0))
        species.write_text("Timestep 0: [C] 1\n", encoding="utf-8")
        TrajectoryIndexStore().build(str(trajectory))
        SPECIES_COMPOSITION_STORE.build(str(species))

        trajectory.write_bytes(_frame(0) + _frame(10))
        species.write_text(
            "Timestep 0: [C] 1\nTimestep 10: [O] 1\n",
            encoding="utf-8",
        )

        self.assertEqual(
            TrajectoryIndexStore().status(
                str(trajectory), metadata_only=True
            )["state"],
            "stale",
        )
        self.assertEqual(
            SPECIES_COMPOSITION_STORE.status(
                str(species), metadata_only=True
            )["state"],
            "stale",
        )


if __name__ == "__main__":
    unittest.main()
