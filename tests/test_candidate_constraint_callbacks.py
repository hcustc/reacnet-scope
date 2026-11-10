"""The submitted constraints survive navigation and reset with dataset context."""
import json

import pytest
from dash.exceptions import PreventUpdate
from dash._callback_context import context_value
from dash._utils import AttributeDict
from plotly.utils import PlotlyJSONEncoder

from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.candidate_workbench import context_key, route_summary, accept_result
from reacnet_scope import services as svc


@pytest.fixture(scope='module')
def app():
    return create_app()


def callback(app, output):
    item = next(value for key, value in app.callback_map.items()
                if output in key and value.get('callback')
                and value['callback'].__module__.endswith('candidate_workbench'))
    return item['callback'].__wrapped__


def invoke(function, trigger, *args):
    token = context_value.set(AttributeDict(triggered_inputs=[{'prop_id': trigger, 'value': 1}]))
    try:
        return function(*args)
    finally:
        context_value.reset(token)


def request_args(store, report=None, nav=None, via=None, excluded=None):
    return [1, None, None, None, None, None, None, store, 'CCO', 'CC(=O)O', 'target',
            None, 20, 'net', 'raw', 3, 'topology', 2000, 5000, 10000, 2000, 5,
            report, 'route', nav, via or [], excluded or []]


def test_new_query_submits_ordered_constraints_and_old_result_is_rejected(app):
    request = callback(app, 'cp-request.data')
    store = {'dataset_id': 'run', 'source_revision': 'one', 'artifacts': {}}
    old, _, _ = invoke(request, 'cp-search.n_clicks',
        *request_args(store, via=['COC', 'CC=O'], excluded=['O']))
    assert old['query']['via_species'] == ['COC', 'CC=O']
    assert old['query']['excluded_species'] == ['O']
    new, _, _ = invoke(request, 'cp-search.n_clicks',
        *request_args(store, via=['CC=O'], excluded=['COC']))
    assert not accept_result(dict(old, report={}), new, store)


@pytest.mark.parametrize('action', ['cp-page-next', 'cp-continue', 'cp-back'])
def test_navigation_uses_submitted_constraints_not_edited_controls(app, action):
    request = callback(app, 'cp-request.data')
    store = {'dataset_id': 'run', 'source_revision': 'one', 'artifacts': {}}
    query = dict(start='CCO', target='', mode='explore', max_steps=1,
                 excluded_species=['O'], via_species=[])
    path = dict(signature_id='route', species=['CCO', 'COC'], steps=[{}])
    revision = {'sample.csv': {'size': 42, 'mtime_ns': '123'}}
    report = dict(context=context_key(store), query=query, paths=[path], next_offset=20,
                  source_revision=revision)
    nav = dict(context=context_key(store), mode='explore', segments=[dict(
        query=query, source_revision=revision, species=['CCO', 'COC'], steps=[{}])])
    args = request_args(store, report, nav, via=['CC=O'], excluded=['COC'])
    args[10] = 'explore'
    result, _, _ = invoke(request, action + '.n_clicks', *args)
    assert result['query']['excluded_species'] == ['O']
    assert result['query']['via_species'] == []


@pytest.mark.parametrize('side', ['via', 'excluded'])
def test_constraint_picker_preserves_selection_and_resets_on_dataset_change(app, monkeypatch, side):
    find = callback(app, f'cp-{side}.options')
    store = {'dataset_id': 'run', 'artifacts': {}}
    monkeypatch.setattr(svc, 'search_candidate_species',
                        lambda *_: dict(rows=[{'species': 'CC=O'}], has_more=False))
    options, selected, _, _ = invoke(find, f'cp-{side}-find.n_clicks',
        1, None, None, store, 'CC=O', [{'value': 'COC', 'label': 'COC'}], ['COC'], None)
    assert selected == ['COC', 'CC=O']
    assert {item['value'] for item in options} == {'COC', 'CC=O'}
    monkeypatch.setattr(svc, 'search_candidate_species', lambda *_: dict(rows=[], has_more=False))
    _, kept, message, _ = invoke(find, f'cp-{side}-find.n_clicks',
        2, None, None, store, 'unknown', options, selected, None)
    assert kept == selected and '已选约束保留' in message
    assert invoke(find, 'cp-context.data', 2, None, None,
                  dict(store, dataset_id='new'), 'unknown', options, selected, None) == ([], [], '', '')


def test_single_anchor_does_not_apply_hidden_waypoints_and_summary_uses_report(app):
    store = {'dataset_id': 'run', 'source_revision': 'one', 'artifacts': {}}
    args = request_args(store, via=['COC'], excluded=['O'])
    args[10] = 'explore'
    result, _, _ = invoke(callback(app, 'cp-request.data'), 'cp-search.n_clicks', *args)
    assert result['query']['via_species'] == []
    report = dict(query={**result['query'], 'via_species': ['COC', 'CC=O']}, paths=[],
                  query_complete=True, reachability_status='not_found')
    text = json.dumps(route_summary(report), cls=PlotlyJSONEncoder, ensure_ascii=False)
    assert 'COC → CC=O' in text and '排除主线物种：O' in text
    assert '本次途经与排除条件内' in text
    reset, _, nav = invoke(callback(app, 'cp-request.data'), 'cp-context.data', *args, result, '')
    assert reset is None and nav is None
    # A context change with nothing to clear leaves the request untouched.
    with pytest.raises(PreventUpdate):
        invoke(callback(app, 'cp-request.data'), 'cp-context.data', *args, None, '')
