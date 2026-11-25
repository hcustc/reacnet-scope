"""A Species detail uses its own exact identity without a global research anchor."""

import json
from types import SimpleNamespace

import pytest
from dash.exceptions import PreventUpdate

from scripts.webapp_dash import candidate_workbench
from scripts.webapp_dash.callbacks import evolution, page_navigation, reaction_channels
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.dataset_scope import species_focus_for


def _callback(app, name):
    return next(item["callback"].__wrapped__ for item in app.callback_map.values()
                if item.get("callback") and item["callback"].__name__ == name)


def test_detail_route_shortcut_uses_the_viewed_exact_species(monkeypatch):
    shortcut = _callback(create_app(), "open_shortcut")
    dataset = {"dataset_id": "run-1", "source_revision": "v1",
               "artifacts": {"reaction": "/run/reaction"}}
    monkeypatch.setattr(candidate_workbench, "ctx", SimpleNamespace(triggered_id="cp-from-species"))
    task, mode, handoff = shortcut(1, dataset, species_focus_for(dataset, "B"))
    assert (task, mode, handoff["species"], handoff["side"]) == (
        "candidates", "explore", "B", "start",
    )
    assert handoff["context"] == candidate_workbench.context_key(dataset)


def test_removed_research_state_has_no_layout_or_callback_dependencies():
    client = create_app().server.test_client()
    layout = json.dumps(client.get("/_dash-layout").get_json())
    dependencies = json.dumps(client.get("/_dash-dependencies").get_json())
    for old_id in ("research-species", "research-start-btn", "research-overview",
                   "research-graph-species"):
        assert old_id not in layout
        assert old_id not in dependencies


def test_channel_focus_stays_bound_to_displayed_channel():
    dataset = {"dataset_id": "run-1", "source_revision": "v1", "artifacts": {}}
    revised = {**dataset, "source_revision": "v2"}
    focus = {"species": "A", "context": candidate_workbench.context_key(dataset)}
    detail = species_focus_for(dataset, "B")
    assert reaction_channels._channel_focus_species(focus, dataset, detail) == "A"
    # A stale channel falls back to the detail focus of the same context only.
    assert reaction_channels._channel_focus_species(focus, revised, detail) == ""
    assert reaction_channels._channel_focus_species(
        focus, revised, species_focus_for(revised, "B")) == "B"


def test_abundance_shortcut_prefills_the_standard_species_browser(monkeypatch):
    app = create_app()
    shortcut = _callback(app, "_open_species_abundance")
    dataset = {"dataset_id": "run-1", "source_revision": "v1"}
    detail = species_focus_for(dataset, "Oc1ccccc1Cl")
    monkeypatch.setattr(evolution, "ctx", SimpleNamespace(triggered_id="species-to-evolution-btn"))
    context = candidate_workbench.context_key(dataset)
    row = {"smiles": "Oc1ccccc1Cl", "formula": "C6H5ClO", "total_count": 100}
    mode, focus, target, picker, curve_filter, single, multiple, selected, ranking_open = shortcut(
        1, dataset, {"context": context}, [row], detail,
    )
    assert mode == "species"
    assert focus == {"species": "Oc1ccccc1Cl", "targets": ["Oc1ccccc1Cl"], "context": context}
    assert (target, picker, curve_filter, single, multiple, selected) == (
        "smiles:Oc1ccccc1Cl", [], "", "", "", {"ids": []},
    )
    assert ranking_open is None

    render = _callback(app, "_render_species_abundance_focus")
    identity = str(render(focus, dataset, mode)[0])
    assert "Oc1ccccc1Cl" in identity
    assert "Oc1ccc(Cl)cc1" not in identity
    assert "来源已变化" in render(focus, {**dataset, "source_revision": "v2"}, mode)[0]
    assert render(focus, dataset, "group") == ([], {"display": "none"})

    # A target outside the bounded ranking still pre-fills the exact query.
    result = shortcut(1, dataset, {"context": context}, [], detail)
    assert result[2] == "smiles:" + row["smiles"]
    assert result[-2:] == ({"ids": []}, None)


@pytest.mark.parametrize("trigger", ["evolution-open-compare-btn", "evolution-close-compare-btn"])
def test_species_abundance_comparison_keeps_the_return_to_species(monkeypatch, trigger):
    navigate = _callback(create_app(), "_navigate")
    monkeypatch.setattr(page_navigation, "ctx", SimpleNamespace(
        triggered_id=trigger, triggered=[{"prop_id": f"{trigger}.n_clicks", "value": 1}],
    ))
    result = navigate(None, {"page": "evolution", "version": 2, "entry": "species"})
    state = next(item for item in result if isinstance(item, dict) and item.get("page") == "evolution")
    assert state["entry"] == "species"


def test_stale_event_page_cannot_render_or_open_after_new_query(monkeypatch):
    app = create_app()
    dataset = {"dataset_id": "run-1", "source_revision": "v1", "artifacts": {}}
    report = {"context": candidate_workbench.context_key(dataset),
              "query_request_id": "new", "source_revision": "v1"}
    page = {"query_request_id": "old", "source_revision": "v1",
            "rows": [{"event_id": "event-old"}]}
    selected = candidate_workbench.observation_rows(page)
    assert _callback(app, "observation_selection")(selected, page, report, "route", 0, dataset) == (True, "")
    monkeypatch.setattr(candidate_workbench, "ctx", SimpleNamespace(triggered_id="cp-open-events"))
    with pytest.raises(PreventUpdate):
        _callback(app, "open_instance")(1, page, selected, "route", 0, report, dataset)


@pytest.mark.parametrize('field,value', [('query_request_id', 'old'), ('source_revision', 'v0'),
                                        ('signature_id', 'other'), ('step_index', 1), ('offset', 25)])
def test_event_selection_is_bound_to_its_evidence_page(field, value):
    page = dict(context='dataset', query_request_id='query', source_revision='v1',
                signature_id='route', step_index=0, offset=0, rows=[{'event_id': 'shared-event'}])
    selected = candidate_workbench.observation_rows({**page, field: value})
    assert candidate_workbench.selected_observation(page, selected) is None
    assert candidate_workbench.selected_observation(page, candidate_workbench.observation_rows(page)) == page['rows'][0]


def test_unresolved_observation_remains_inspectable_with_its_limitation():
    dataset = {'dataset_id': 'run', 'source_revision': 'v1'}
    report = dict(context=candidate_workbench.context_key(dataset), query_request_id='query', source_revision='v1')
    page = dict(report, signature_id='route', step_index=0, offset=0,
                rows=[{'event_id': 'event', 'association_status': 'unmatched', 'timestep_index': 8}])
    disabled, note = _callback(create_app(), 'observation_selection')(
        candidate_workbench.observation_rows(page), page, report, 'route', 0, dataset)
    assert disabled is False
    assert '尚未解析' in note


def test_species_focus_from_an_earlier_dataset_context_is_ignored():
    from scripts.webapp_dash.dataset_scope import current_species_focus, empty_species_focus

    old = {"dataset_id": "run-1", "source_revision": "v1", "artifacts": {}}
    new = {"dataset_id": "run-2", "source_revision": "v1", "artifacts": {}}
    late = species_focus_for(old, "B", "B", "species_grid")
    assert current_species_focus(late, old)["selected_smiles"] == "B"
    # A focus written after a dataset switch by a late response is not applied.
    assert current_species_focus(late, new) == empty_species_focus()
    assert current_species_focus(None, new) == empty_species_focus()


def test_species_selection_never_rewrites_the_dataset_context():
    dependencies = create_app().server.test_client().get("/_dash-dependencies").get_json()
    focus_writers = [item for item in dependencies
                     if "species-focus.data" in str(item.get("output") or "")]
    assert focus_writers
    # Only dataset context transitions (switch, session restore, refresh)
    # replace app-store; they reset the focus in the same update.
    transitions = {"dataset-switch-transaction", "dataset-session-restore",
                   "data-current-refresh-btn"}
    for item in focus_writers:
        if "app-store.data" in str(item["output"]):
            assert {entry["id"] for entry in item["inputs"]} <= transitions, item["output"]
