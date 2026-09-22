"""The graph preserves exact candidate identity and navigates existing evidence."""
from copy import deepcopy
import json

import pytest

from scripts.webapp_dash import candidate_graph as graph
from scripts.webapp_dash.candidate_workbench import context_key


@pytest.fixture
def report():
    def step(key, start, end, count):
        reactants, products = key.split('->')
        return dict(reaction_key=key, carried_from=start, carried_to=end,
                    reactants=reactants.split('+'), products=products.split('+'),
                    event_count=count, transfer_event_count=count)

    shared = step('CC=O->CC(=O)O', 'CC=O', 'CC(=O)O', 5)
    steps = [
        [step('CCO->CC=O', 'CCO', 'CC=O', 7), shared],
        [step('CCO+O->CC=O+[H][H]', 'CCO', 'CC=O', 2), shared],
        [step('CCO->COC', 'CCO', 'COC', 3), step('COC->CC=O', 'COC', 'CC=O', 1), shared],
    ]
    return dict(context=context_key({'dataset_id': 'one'}), query_request_id='search-one',
                query={'start': 'CCO', 'target': 'CC(=O)O'}, query_complete=False,
                paths=[dict(signature_id=f'path-{i}', step_count=len(s), steps=s,
                            species=[s[0]['carried_from'], *[t['carried_to'] for t in s]])
                       for i, s in enumerate(steps)])


def test_shared_species_and_steps_keep_isomers_reaction_context_and_counts(report):
    before = deepcopy(report)
    data = [e['data'] for e in graph.elements(report)]
    species = [d for d in data if d['kind'] == 'species']
    reactions = [d for d in data if d['kind'] == 'reaction']
    assert {d['species'] for d in species} == {'CCO', 'COC', 'CC=O', 'CC(=O)O'}
    assert len(species) == 4
    assert len(reactions) == 5
    alternatives = [d for d in reactions if d['carried_from'] == 'CCO' and d['carried_to'] == 'CC=O']
    assert len(alternatives) == 2  # Same carrier pair, different complete reactions.
    shared = next(d for d in reactions if d['reaction_key'] == 'CC=O->CC(=O)O')
    assert shared['event_count'] == 5  # Not 15 when three routes share it.
    assert shared['members'] == [dict(signature_id=f'path-{i}', step_index=j)
                                 for i, j in [(0, 1), (1, 1), (2, 2)]]
    assert len([d for d in data if d['kind'] == 'edge']) == 10
    assert report == before


def test_shared_identity_retains_direction_stoichiometry_and_carrier_pairs():
    base = dict(reaction_key='CCO+CCO->COC', carried_from='CCO', carried_to='COC')
    assert len({graph.step_id(s) for s in [base,
        dict(base, reaction_key='CCO->COC'),
        dict(base, reaction_key='COC->CCO+CCO', carried_from='COC', carried_to='CCO'),
        dict(base, carried_to='O')]}) == 4


def test_click_rejects_stale_queries_and_uses_report_membership(report):
    picked = graph.elements(report)[0]['data']
    forged = dict(picked, members=[dict(signature_id='injected', step_index=99)])
    assert graph.resolve_pick(report, forged)['members'] == picked['members']
    assert graph.resolve_pick(dict(report, query_request_id='new-search'), picked) is None
    assert graph.resolve_pick(dict(report, context='new-dataset'), picked) is None
    assert graph.resolve_pick(None, picked) is None
    assert graph.resolve_pick(report, dict(picked, id='missing')) is None
    assert graph.elements({'paths': []}) == []


@pytest.fixture
def app():
    from scripts.webapp_dash.app import create_app
    return create_app()


def invoke(app, prefix, changed, values, expected_status=200):
    key = next(k for k in app.callback_map if k.startswith(prefix))
    callback = app.callback_map[key]
    output = callback['output']
    if isinstance(output, list):
        outputs = [dict(id=o.component_id, property=o.component_property) for o in output]
    else:
        outputs = dict(id=output.component_id, property=output.component_property)
    def supplied(items):
        return [dict(item, value=values.get(f'{item["id"]}.{item["property"]}', values.get(item['id'])))
                for item in items]
    response = app.server.test_client().post('/_dash-update-component', json=dict(
        output=key, outputs=outputs, changedPropIds=[changed],
        inputs=supplied(callback['inputs']), state=supplied(callback['state'])))
    assert response.status_code == expected_status, response.data
    return response.get_json()['response'] if expected_status == 200 else {}


def test_graph_filters_highlights_clicks_and_dataset_reset(app, report):
    values = {'cp-report': report, 'app-store': {'dataset_id': 'one'},
              'cp-graph-scope': 'all', 'cp-routes': [], 'cp-focus': 'path-0'}
    rendered = invoke(app, '..cp-graph.elements', 'cp-report.data', values)
    elements = rendered['cp-graph']['elements']
    assert len(elements) == 19
    assert '截断' in rendered['cp-graph-summary']['children']

    # Clicking a branch outside the current route chooses an existing route.
    picked = next(e['data'] for e in elements if e['data'].get('reaction_key') == 'COC->CC=O')
    selection = invoke(app, 'cp-graph-pick.data', 'cp-graph.tapNodeData', dict(values,
        **{'cp-graph.tapNodeData': picked}))['cp-graph-pick']['data']
    nav = invoke(app, 'cp-focus.value@', 'cp-graph-pick.data', dict(values,
        **{'cp-graph-pick': selection}))
    assert nav['cp-focus']['value'] == 'path-2'
    values['cp-focus'] = 'path-2'
    detail = invoke(app, '..cp-detail.children', 'cp-focus.value', dict(values,
        **{'cp-graph-pick': selection}))
    assert detail['cp-step']['value'] == 1
    styles = graph.stylesheet(elements, 'path-2', ['path-0'], 1)
    assert any(s['selector'] == '#' + picked['id'] and s['style']['border-color'] == '#bd6b16'
               for s in styles)

    filtered = invoke(app, '..cp-graph.elements', 'cp-graph-scope.value', dict(values,
        **{'cp-graph-scope': 'selected'}))['cp-graph']['elements']
    assert len(filtered) == 13  # Four exact species and three two-edge steps.
    assert {e['data']['label'] for e in filtered if 'label' in e['data']} <= {
        e['data']['label'] for e in elements if 'label' in e['data']}
    stale = dict(values, **{'app-store': {'dataset_id': 'two'}})
    assert invoke(app, '..cp-graph.elements', 'app-store.data', stale)['cp-graph']['elements'] == []
    assert invoke(app, 'cp-graph-pick.data', 'app-store.data', dict(stale,
        **{'cp-graph.tapNodeData': picked}))['cp-graph-pick']['data'] is None
    cleared = invoke(app, '..cp-detail.children', 'app-store.data', stale)
    assert cleared['cp-step']['value'] is None and cleared['cp-detail']['children'] == ''


def test_clicking_shared_edge_locates_step_in_current_route(app, report):
    edge = next(e['data'] for e in graph.elements(report)
                if e['data']['kind'] == 'edge' and len(e['data']['members']) == 3)
    values = {'cp-report': report, 'app-store': {'dataset_id': 'one'}, 'cp-focus': 'path-2',
              'cp-graph.tapEdgeData': edge}
    picked = invoke(app, 'cp-graph-pick.data', 'cp-graph.tapEdgeData', values)['cp-graph-pick']['data']
    values['cp-graph-pick'] = picked
    assert invoke(app, 'cp-focus.value@', 'cp-graph-pick.data', values)['cp-focus']['value'] == 'path-2'
    assert invoke(app, '..cp-detail.children', 'cp-graph-pick.data', values)['cp-step']['value'] == 2


def test_related_route_buttons_are_bound_to_their_search_and_dataset(app, report):
    callback = next(v for k, v in app.callback_map.items() if k.startswith('cp-focus.value@'))
    pattern = next(i['id'] for i in callback['inputs'] if i['id'].startswith('{'))
    values = {'cp-report': report, 'app-store': {'dataset_id': 'one'}, 'cp-focus': 'path-0', pattern: [1]}
    button = dict(type='cp-graph-route', signature='path-2', request='search-one', context=report['context'])
    def changed(identity):
        return json.dumps(identity, sort_keys=True, separators=(',', ':')) + '.n_clicks'
    assert invoke(app, 'cp-focus.value@', changed(button), values)['cp-focus']['value'] == 'path-2'
    invoke(app, 'cp-focus.value@', changed(dict(button, request='old-search')), values, expected_status=204)
    invoke(app, 'cp-focus.value@', changed(dict(button, context='old-dataset')), values, expected_status=204)
