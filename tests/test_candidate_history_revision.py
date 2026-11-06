"""Browsing history must retain its published evidence revision."""
import json
import xml.etree.ElementTree as ET

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.candidate_workbench import context_key
from tests.test_candidate_constraint_callbacks import callback
from tests.test_candidate_graph import invoke


@pytest.fixture(scope='module')
def app():
    return create_app()


@pytest.fixture(params=['explore', 'reverse'])
def browsing(tmp_path, monkeypatch, app, request):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path / 'workspace'))
    source = tmp_path / 'history.reactionevent.csv'
    source.write_text('Timestep_Index,Reactant,Product\n0,CCO,COC\n1,COC,CC=O\n')
    EVENT_EVIDENCE_STORE.build(str(source))
    artifacts = {'reactionevent': str(source)}
    store = {'dataset_id': 'same', 'artifacts': artifacts}
    mode = request.param
    query = dict(start='CCO', mode=mode) if mode == 'explore' else dict(target='CC=O', mode=mode)
    old = dict(svc.search_candidate_paths(artifacts, **query), context=context_key(store))
    values = {'app-store': store, 'cp-report': old, 'cp-mode': mode,
              'cp-focus': old['paths'][0]['signature_id'], 'cp-graph-scope': 'all',
              'cp-routes': []}
    continued = invoke(app, '..cp-request.data', 'cp-continue.n_clicks', values)
    values['cp-request'] = continued['cp-request']['data']
    nav = continued['cp-nav-anchor']['data']
    current_query = continued['cp-request']['data']['query']
    return source, old, values, nav, current_query


@pytest.mark.parametrize('trigger', ['cp-hypergraph-json', 'cp-svg'])
def test_exports_reject_history_after_in_place_source_rebuild(app, browsing, trigger):
    source, old, values, nav, query = browsing
    # The next query uses the same browser dataset context, but its index no
    # longer contains the reaction retained in the browsing history.
    remaining = 'COC,CC=O' if query['mode'] == 'explore' else 'CCO,COC'
    source.write_text('Timestep_Index,Reactant,Product\n0,' + remaining + '\n')
    EVENT_EVIDENCE_STORE.build(str(source))
    store = values['app-store']
    new = dict(svc.search_candidate_paths(store['artifacts'], **query), context=context_key(store))
    assert old['source_revision'] != new['source_revision']
    assert old['context'] == new['context']
    values.update({'cp-report': new, 'cp-nav-anchor': nav,
                   'cp-focus': new['paths'][0]['signature_id'], trigger: 1})
    exported = invoke(app, '..cp-download.data', trigger + '.n_clicks', values)
    assert 'cp-download' not in exported
    assert '浏览历史' in exported['cp-message']['children']
    assert '重新搜索' in exported['cp-message']['children']
    rendered = invoke(app, '..cp-graph.elements', 'cp-report.data', values)
    assert rendered['cp-graph']['elements'] == []
    assert '浏览历史' in rendered['cp-graph-summary']['children']


@pytest.mark.parametrize('trigger', ['cp-hypergraph-json', 'cp-svg'])
def test_same_revision_history_remains_exportable(app, browsing, trigger):
    _, old, values, nav, query = browsing
    store = values['app-store']
    new = dict(svc.search_candidate_paths(store['artifacts'], **query), context=context_key(store))
    assert nav['segments'][0]['source_revision'] == old['source_revision'] == new['source_revision']
    values.update({'cp-report': new, 'cp-nav-anchor': nav,
                   'cp-focus': new['paths'][0]['signature_id'], trigger: 1})
    exported = invoke(app, '..cp-download.data', trigger + '.n_clicks', values)
    content = exported['cp-download']['data']['content']
    if trigger == 'cp-svg':
        content = ET.fromstring(content).find('{http://www.w3.org/2000/svg}metadata').text
    graph = json.loads(content)
    assert {r['reaction_key'] for r in graph['hyperedges']} == {'CCO->COC', 'COC->CC=O'}
    assert graph['provenance']['source_revision'] == old['source_revision']
    assert sum(step['historical'] for step in graph['routes'][0]['display_steps']) == 1


@pytest.mark.parametrize('action', ['cp-continue', 'cp-back', 'cp-page-next'])
def test_navigation_rejects_mixed_revisions(app, browsing, action):
    _, _, values, nav, query = browsing
    store = values['app-store']
    current = dict(svc.search_candidate_paths(store['artifacts'], **query), context=context_key(store))
    nav['segments'][0]['source_revision'] = {'outdated': {'size': 1, 'mtime_ns': '1'}}
    values.update({'cp-report': current, 'cp-nav-anchor': nav,
                   'cp-focus': current['paths'][0]['signature_id']})
    response = invoke(app, '..cp-request.data', action + '.n_clicks', values)
    assert 'cp-request' not in response
    assert response['cp-nav-anchor']['data'] is None
    assert '重新搜索' in response['cp-message']['children']


def test_queued_navigation_cannot_accept_a_rebuilt_revision(app, browsing):
    source, _, values, _, _ = browsing
    queued = values['cp-request']
    remaining = 'COC,CC=O' if queued['query']['mode'] == 'explore' else 'CCO,COC'
    source.write_text('Timestep_Index,Reactant,Product\n0,' + remaining + '\n')
    EVENT_EVIDENCE_STORE.build(str(source))
    result = callback(app, 'cp-raw.data')(queued)
    assert 'paths' not in result['report']
    assert '来源版本已变化' in result['report']['error']


@pytest.mark.parametrize('legacy', ['missing_revision', 'missing_segments'])
def test_unverifiable_legacy_history_requires_a_new_search(app, browsing, legacy):
    _, _, values, nav, query = browsing
    store = values['app-store']
    current = dict(svc.search_candidate_paths(store['artifacts'], **query), context=context_key(store))
    if legacy == 'missing_revision':
        nav['segments'][0].pop('source_revision')
    else:
        nav.pop('segments')
    values.update({'cp-report': current, 'cp-nav-anchor': nav, 'cp-hypergraph-json': 1})
    response = invoke(app, '..cp-download.data', 'cp-hypergraph-json.n_clicks', values)
    assert 'cp-download' not in response
    assert '无法核验' in response['cp-message']['children']
