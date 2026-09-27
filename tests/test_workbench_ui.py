"""Result identity and form edge cases shared by the workspaces."""
import math
import json
from types import SimpleNamespace

import pytest

from scripts.webapp_dash.ui_components import columns, query_context, row_identity, selected_row
from scripts.webapp_dash.ui_state import species_query


def test_sorted_selection_resolves_exact_structure_and_current_values():
    selected = {"formula": "C2O", "smiles": "COC", "tp": 1}
    current = [{"formula": "C2O", "smiles": "CCO", "tp": 2},
               {"formula": "C2O", "smiles": "COC", "tp": 7}]
    assert row_identity(current[0]) != row_identity(current[1])
    assert selected_row([selected], current) == current[1]
    assert selected_row([selected], current[:1]) is None
    assert selected_row([0], current) is None


def test_aggregate_and_source_rows_have_distinct_stable_identities():
    assert row_identity({"formula": "C2O", "structure_count": 3}) == '["formula","C2O"]'
    assert row_identity({"id": "path-4", "smiles": "CCO"}) == '["id","path-4"]'
    assert row_identity({"name": "run", "folder": "/a"}) != row_identity({"name": "run", "folder": "/b"})


def test_zero_mass_tolerance_is_preserved_and_irrelevant_tolerance_is_ignored():
    assert species_query(" 39.9949 ", "auto", 0)["mass_tolerance"] == 0
    assert species_query("CCO", "smiles", -1)["mass_tolerance"] == 0.5
    assert species_query("40", "mass", None)["mass_tolerance"] == 0.5


@pytest.mark.parametrize("tolerance", [-1, math.inf, math.nan, "invalid"])
def test_mass_tolerance_rejects_invalid_values(tolerance):
    with pytest.raises((ValueError, TypeError)):
        species_query("40", "mass", tolerance)


def test_result_context_ignores_ui_changes_but_binds_source_revision():
    first = {"dataset_id": "A", "source_revision": "1", "artifacts": {"reaction": "/a"}}
    assert query_context(first) == query_context({**first, "selected_smiles": "CCO"})
    assert query_context(first) != query_context({**first, "source_revision": "2"})


def test_grid_numeric_columns_and_unconfirmed_rates():
    result = columns([{"id": "mass", "name": "精确质量", "type": "numeric"},
                      {"id": "k_app_display", "name": "k"}])
    assert result[0]["cellDataType"] == "number"
    assert result[1]["hide"] is True


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("clicks", [0, 1])
def test_task_navigation_matches_clicked_button_not_another_buttons_old_count(monkeypatch, encoded, clicks):
    from dash.exceptions import PreventUpdate
    from scripts.webapp_dash import callbacks
    from scripts.webapp_dash.app import create_app

    app = create_app()
    callback = next(item for item in app.callback_map.values()
                    if item.get("callback") and item["callback"].__name__ == "_navigate")
    target = {"type": "workspace-open-page", "page": "events"}
    other = {"type": "workspace-open-page", "page": "reactions"}
    monkeypatch.setattr(callbacks, "ctx", SimpleNamespace(
        triggered_id=target,
        triggered=[{"prop_id": json.dumps(target) + ".n_clicks", "value": None}],
        inputs_list=[[{"id": json.dumps(other) if encoded else other, "value": 1},
                      {"id": json.dumps(target) if encoded else target, "value": clicks}]],
    ))
    navigate = callback["callback"].__wrapped__
    if not clicks:
        with pytest.raises(PreventUpdate):
            navigate({}, {"page": "reactions"})
    else:
        result = navigate({}, {"page": "reactions"})
        outputs = {str(output): value for output, value in zip(callback["output"], result)}
        assert outputs["page-store.data"]["page"] == "events"


def test_switching_reaction_tasks_preserves_mounted_navigation_buttons():
    from scripts.webapp_dash.app import _workspace_task_navigation, create_app

    app = create_app()
    assert "workspace-task-nav.children" not in app.callback_map
    navigation = _workspace_task_navigation()
    pages = [button.id["page"] for button in navigation.children]
    assert pages == [
        "species",
        "reactions",
        "reaction-candidates",
        "reaction-related",
        "reaction-compare",
        "evolution",
        "element-distribution",
        "events",
    ]
    assert all(button.n_clicks == 0 for button in navigation.children)
