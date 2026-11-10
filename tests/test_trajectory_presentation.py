"""The Plotly fallback must honor the same atom scope as the main viewer."""
from __future__ import annotations

import pytest

from scripts.webapp_dash.callbacks.events import _event_frame_figure


@pytest.mark.parametrize("scope", ["core", "participants"])
@pytest.mark.parametrize("selected_ids", [[], [99]])
def test_empty_atom_scope_does_not_display_context_atoms(scope, selected_ids):
    viewer = {
        "frames": [{"frame": 0, "atoms": [{"id": 1, "element": "C", "x": 1, "y": 2, "z": 3}]}],
        "atom_groups": {scope: selected_ids, "context": [1]},
    }
    figure = _event_frame_figure(viewer, 0, scope)
    assert not figure.data
    assert figure.layout.annotations[0].text == "当前显示范围没有可渲染原子"


def test_legacy_participant_groups_include_both_reaction_sides():
    viewer = {
        "frames": [{"frame": 0, "atoms": [
            {"id": 1, "x": 1, "y": 2, "z": 3},
            {"id": 2, "x": 4, "y": 5, "z": 6},
            {"id": 3, "x": 7, "y": 8, "z": 9},
        ]}],
        "atom_groups": {"reactant": [1], "product": [2], "context": [1, 2, 3]},
    }
    figure = _event_frame_figure(viewer, 0, "participants")
    assert list(figure.data[0].x) == [1, 4]

    viewer["atom_groups"]["participants"] = []
    assert not _event_frame_figure(viewer, 0, "participants").data
