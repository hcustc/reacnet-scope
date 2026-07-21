from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rng_tools.io import load_transition_table
from scripts.webapp.server import build_dataset_status_payload, build_transition_table_payload


class TransitionTableTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile("w", suffix=".lammpstrj.table", delete=False)
        handle.write("[H] [H][H] [H][O]\n")
        handle.write("[H] 0 8 2\n")
        handle.write("[H][H] 7 0 0\n")
        handle.write("[H][O] 3 0 0\n")
        handle.close()
        self.path = Path(handle.name)

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)

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

    def test_rejects_non_square_matrix(self) -> None:
        self.path.write_text("[H] [O]\n[H] 0 1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must have 2 data rows"):
            load_transition_table(self.path)

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


if __name__ == "__main__":
    unittest.main()
