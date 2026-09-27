"""A Species detail uses its own exact identity without a global research anchor."""

import json
from types import SimpleNamespace

import pytest
from dash.exceptions import PreventUpdate

from scripts.webapp_dash import candidate_workbench, callbacks
from scripts.webapp_dash.app import create_app


def _callback(app, name):
    return next(item["callback"].__wrapped__ for item in app.callback_map.values()
                if item.get("callback") and item["callback"].__name__ == name)


def test_detail_route_shortcut_uses_the_viewed_exact_species(monkeypatch):
    shortcut = _callback(create_app(), "open_shortcut")
    dataset = {"dataset_id": "run-1", "source_revision": "v1",
               "artifacts": {"reaction": "/run/reaction"}, "selected_smiles": "B"}
    monkeypatch.setattr(candidate_workbench, "ctx", SimpleNamespace(triggered_id="cp-from-species"))
    task, mode, handoff = shortcut(1, dataset)
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
    dataset = {"dataset_id": "run-1", "source_revision": "v1", "artifacts": {},
               "selected_smiles": "B"}
    focus = {"species": "A", "context": candidate_workbench.context_key(dataset)}
    assert callbacks._channel_focus_species(focus, dataset) == "A"
    assert callbacks._channel_focus_species(focus, {**dataset, "source_revision": "v2"}) == "B"


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
