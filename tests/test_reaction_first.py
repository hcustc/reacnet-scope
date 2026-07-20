from __future__ import annotations

import unittest
from pathlib import Path

from scripts.webapp.server import (
    ROUTE_TRANSITION_INDEX_STORE,
    _classify_reaction_candidate_rows,
    _prepare_reaction_query,
    _collect_reaction_species_token_snapshots,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "reaction_first"


class ReactionFirstFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.query = _prepare_reaction_query((FIXTURE_DIR / "reaction.smiles").read_text().strip())
        cls.route_file = str(FIXTURE_DIR / "event.route")
        cls.species_file = str(FIXTURE_DIR / "event.species")

    def test_route_fixture_resolves_forward_transition(self) -> None:
        result = ROUTE_TRANSITION_INDEX_STORE.query_reaction_hits(self.route_file, self.query)
        self.assertEqual(result["matched_atom_transitions"], 1)
        hit = result["hits"][0]
        self.assertEqual((hit["from_token"], hit["to_token"]), ("C", "O"))
        self.assertEqual(hit["direction"], "reactant_to_product")

    def _candidate(self) -> dict[str, object]:
        result = ROUTE_TRANSITION_INDEX_STORE.query_reaction_hits(self.route_file, self.query)
        hit = result["hits"][0]
        return {
            "candidate_id": "fixture-event",
            "event_id": "fixture-event",
            "comparison_before_frame": 0,
            "comparison_after_frame": 10,
            "route_event_start_frame": hit["start_frame"],
            "anchor_frame": hit["end_frame"],
            "window_frames": [0, 5, 10],
            "context_atom_ids": [1],
            "trajectory_sampling_status": "good",
            "context_reconstruction_mode": "same_molecule_union",
            "visualization_ready": True,
            "route_confidence": 0.9,
        }

    def _snapshots(self) -> dict[int, dict[str, int]]:
        return _collect_reaction_species_token_snapshots(
            self.species_file,
            requested_frames=[0, 10],
            query_tokens=self.query["reactant_tokens"] + self.query["product_tokens"],
            match_mode=self.query["match_mode"],
        )

    def test_positive_fixture_is_verified(self) -> None:
        accepted, candidates, discarded = _classify_reaction_candidate_rows(
            [self._candidate()], reaction_query=self.query, species_snapshots=self._snapshots()
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["verification_status"], "verified_exact")
        self.assertEqual(accepted[0]["selected_event_class"], "verified")
        self.assertFalse(candidates)
        self.assertFalse(discarded)

    def test_partial_and_zero_net_are_not_verified(self) -> None:
        snapshots = self._snapshots()
        partial = {0: {"C": 1, "O": 0}, 10: {"C": 1, "O": 1}}
        accepted, candidates, discarded = _classify_reaction_candidate_rows(
            [self._candidate()], reaction_query=self.query, species_snapshots=partial
        )
        self.assertFalse(accepted)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["verification_status"], "candidate_partial")
        accepted, candidates, discarded = _classify_reaction_candidate_rows(
            [self._candidate()], reaction_query=self.query, species_snapshots=snapshots | {10: {"C": 1, "O": 0}}
        )
        self.assertFalse(accepted)
        self.assertFalse(candidates)
        self.assertEqual(discarded[0]["failure_reason"], "net_reaction_zero")


if __name__ == "__main__":
    unittest.main()
