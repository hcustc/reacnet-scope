from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import rng_tools.dir_browser as dir_browser
from rng_tools.dir_browser import DirBrowserError
from rng_tools.io import load_transition_table
from scripts.webapp.server import build_dataset_status_payload, build_transition_table_payload, pick_folder_with_system
from scripts.webapp_dash import services as dash_services


class TransitionTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self._allowed_roots_patch = patch.object(
            dir_browser, "ALLOWED_ROOTS", [Path(tempfile.gettempdir()).resolve()]
        )
        self._allowed_roots_patch.start()
        handle = tempfile.NamedTemporaryFile("w", suffix=".lammpstrj.table", delete=False)
        handle.write("[H] [H][H] [H][O]\n")
        handle.write("[H] 0 8 2\n")
        handle.write("[H][H] 7 0 0\n")
        handle.write("[H][O] 3 0 0\n")
        handle.close()
        self.path = Path(handle.name)

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)
        self._allowed_roots_patch.stop()

    def test_load_transition_matrix(self) -> None:
        parsed = load_transition_table(self.path)
        self.assertEqual(parsed["n_species"], 3)
        self.assertEqual(parsed["labels"], ["[H]", "[H][H]", "[H][O]"])
        self.assertEqual(parsed["matrix"][0], [0, 8, 2])

    def test_payload_ranks_species_and_edges(self) -> None:
        payload = build_transition_table_payload(
            {
                "table": [str(self.path)],
                "max_species": ["2"],
                "min_count": ["2"],
                "top_edges": ["5"],
            }
        )
        self.assertEqual(payload["meta"]["n_species_total"], 3)
        self.assertEqual(payload["meta"]["n_species_displayed"], 2)
        self.assertEqual(payload["meta"]["total_events"], 20)
        self.assertEqual(payload["species"][0]["smiles"], "[H]")
        self.assertEqual(payload["edges"][0]["count"], 8)
        self.assertEqual(payload["edges"][0]["source_formula"], "H")
        self.assertEqual(payload["edges"][0]["target_formula"], "H2")
        network = payload["network"]
        self.assertEqual(network["schema_version"], "observation-network/v1")
        self.assertEqual(network["model"], "species_reaction_bipartite")
        self.assertEqual(network["source"]["evidence_level"], "aggregate_observation")
        self.assertEqual(network["audit"]["status"], "not_available")
        self.assertEqual(len(network["reactions"]), len(payload["edges"]))
        self.assertTrue(all(edge["kind"] in {"reactant_of", "produces"} for edge in network["edges"]))
        self.assertIn("carbon_flux", network["weights"])
        self.assertIn("net_event_count", network["observed_transitions"][0])
        self.assertIsNone(network["observed_transitions"][0]["atom_transfer_count"])

    def test_observation_elements_add_only_semantic_labels(self) -> None:
        payload = dash_services.build_observation_elements(
            {"table": str(self.path)},
            min_count=2,
            max_species=2,
            top_edges=5,
        )

        self.assertEqual(payload["network_semantics"], "event_transfer")
        self.assertEqual(payload["evidence_level"], "aggregate_observation")
        self.assertNotIn("kinetic_flux", str(payload))
        species = next(
            item for item in payload["elements"]
            if item["data"].get("kind") == "species"
        )
        reaction = next(
            item for item in payload["elements"]
            if item["data"].get("kind") == "reaction"
        )
        edge = next(
            item for item in payload["elements"]
            if "source" in item["data"]
        )
        self.assertEqual(
            set(species["data"]),
            {
                "id",
                "label",
                "kind",
                "smiles",
                "formula",
                "rank",
                "incoming",
                "outgoing",
                "total",
            },
        )
        self.assertEqual(
            set(reaction["data"]),
            {
                "id",
                "label",
                "kind",
                "reaction_type",
                "event_count",
                "net_event_count",
                "ordinal",
            },
        )
        self.assertEqual(
            set(edge["data"]),
            {"id", "source", "target", "kind", "event_count"},
        )

    def test_rejects_non_square_matrix(self) -> None:
        self.path.write_text("[H] [O]\n[H] 0 1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must have 2 data rows"):
            load_transition_table(self.path)

    def test_reorders_labeled_rows_without_quadratic_label_lookups(self) -> None:
        self.path.write_text(
            "[H] [O] [C]\n"
            "[C] 1 2 3\n"
            "\n"
            "[H] 4 5 6\n"
            "[O] 7 8 9\n",
            encoding="utf-8",
        )
        parsed = load_transition_table(self.path)
        self.assertEqual(parsed["labels"], ["[H]", "[O]", "[C]"])
        self.assertEqual(parsed["matrix"], [[4, 5, 6], [7, 8, 9], [1, 2, 3]])

    def test_dataset_status_derives_related_rng_artifacts(self) -> None:
        root = self.path.with_suffix("")
        reaction_path = Path(f"{root}.reactionabcd")
        species_path = Path(f"{root}.species")
        route_path = Path(f"{root}.route")
        table_path = Path(f"{root}.table")
        for item in (reaction_path, species_path, route_path, table_path):
            item.write_text("fixture", encoding="utf-8")
        try:
            payload = build_dataset_status_payload({"reac": [str(reaction_path)]})
            artifacts = payload["dataset"]["artifacts"]
            self.assertTrue(artifacts["reaction"]["exists"])
            self.assertTrue(artifacts["species"]["exists"])
            self.assertTrue(artifacts["route"]["exists"])
            self.assertTrue(artifacts["table"]["exists"])
        finally:
            for item in (reaction_path, species_path, route_path, table_path):
                item.unlink(missing_ok=True)

    def test_dataset_status_leaves_ambiguous_folder_groups_unselected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            complete_base = root / "run.lammpstrj"
            for suffix in ("", ".reactionabcd", ".species", ".route", ".table", ".reactionevent.csv", ".molecules.csv"):
                Path(f"{complete_base}{suffix}").write_text("fixture", encoding="utf-8")
            Path(f"{root / 'partial.lammpstrj'}.species").write_text("fixture", encoding="utf-8")

            payload = build_dataset_status_payload({"dataset_dir": [directory]})
            dataset = payload["dataset"]
            self.assertEqual(dataset["label"], "未选择数据集")
            self.assertEqual(dataset["ready_count"], 0)
            self.assertFalse(any(item["selected"] for item in dataset["candidates"]))

    def test_dataset_status_rejects_folder_outside_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as allowed_directory, tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory)
            Path(f"{outside / 'private.lammpstrj'}.reactionabcd").write_text(
                "fixture", encoding="utf-8"
            )

            with patch.object(dir_browser, "ALLOWED_ROOTS", [Path(allowed_directory)]):
                with self.assertRaisesRegex(DirBrowserError, "路径超出允许范围"):
                    build_dataset_status_payload({"dataset_dir": [outside_directory]})

    def test_dataset_status_rejects_explicit_artifacts_outside_allowed_roots(self) -> None:
        explicit_params = (
            "reac",
            "species_file",
            "moname_file",
            "trajectory_file",
            "route_file",
            "table_file",
            "reactionevent_file",
            "molecules_file",
        )
        with tempfile.TemporaryDirectory() as allowed_directory, tempfile.TemporaryDirectory() as outside_directory:
            outside_path = Path(outside_directory) / "private.artifact"
            outside_path.write_text("fixture", encoding="utf-8")

            with patch.object(dir_browser, "ALLOWED_ROOTS", [Path(allowed_directory)]):
                for param in explicit_params:
                    with self.subTest(param=param):
                        with self.assertRaisesRegex(DirBrowserError, "路径超出允许范围"):
                            build_dataset_status_payload({param: [str(outside_path)]})

    def test_dataset_status_can_select_a_specific_folder_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            complete_base = root / "run.lammpstrj"
            partial_base = root / "partial.lammpstrj"
            for suffix in ("", ".reactionabcd", ".species", ".route", ".table"):
                Path(f"{complete_base}{suffix}").write_text("fixture", encoding="utf-8")
            Path(f"{partial_base}.species").write_text("fixture", encoding="utf-8")

            payload = build_dataset_status_payload(
                {
                    "dataset_dir": [directory],
                    "dataset_base": [str(partial_base)],
                }
            )
            dataset = payload["dataset"]
            self.assertEqual(dataset["label"], "partial.lammpstrj")
            self.assertEqual(dataset["ready_count"], 1)
            self.assertEqual(dataset["artifacts"]["species"]["source"], "folder")
            self.assertTrue(dataset["artifacts"]["species"]["exists"])
            self.assertFalse(dataset["artifacts"]["reaction"]["exists"])
            self.assertEqual(sum(1 for item in dataset["candidates"] if item["selected"]), 1)

    def test_dataset_status_keeps_selected_group_in_candidate_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected_base = root / "zz_selected.lammpstrj"
            Path(f"{selected_base}.species").write_text("fixture", encoding="utf-8")
            for index in range(13):
                base = root / f"complete_{index:02d}.lammpstrj"
                for suffix in ("", ".reactionabcd", ".species", ".route", ".table"):
                    Path(f"{base}{suffix}").write_text("fixture", encoding="utf-8")

            payload = build_dataset_status_payload(
                {
                    "dataset_dir": [directory],
                    "dataset_base": [str(selected_base)],
                }
            )
            dataset = payload["dataset"]
            self.assertEqual(dataset["label"], "zz_selected.lammpstrj")
            selected_base_resolved = str(selected_base.resolve())
            self.assertTrue(
                any(item["selected"] and item["base"] == selected_base_resolved for item in dataset["candidates"])
            )

    def test_pick_folder_with_system_returns_selected_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = Mock(returncode=0, stdout=f"{directory}\n", stderr="")
            with patch("scripts.webapp.server.sys.platform", "darwin"), patch(
                "scripts.webapp.server.subprocess.run",
                return_value=completed,
            ) as run_mock:
                payload = pick_folder_with_system(directory)

            self.assertFalse(payload["canceled"])
            self.assertEqual(payload["path"], str(Path(directory).resolve()))
            self.assertEqual(run_mock.call_args.args[0][0], "osascript")

    def test_pick_folder_with_system_handles_cancel(self) -> None:
        completed = Mock(returncode=1, stdout="", stderr="User canceled. (-128)")
        with patch("scripts.webapp.server.sys.platform", "darwin"), patch(
            "scripts.webapp.server.subprocess.run",
            return_value=completed,
        ):
            payload = pick_folder_with_system("")

        self.assertTrue(payload["canceled"])
        self.assertEqual(payload["path"], "")


if __name__ == "__main__":
    unittest.main()
