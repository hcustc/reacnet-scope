"""The graph preserves exact candidate identity and navigates existing evidence."""
from copy import deepcopy
import json

import pytest

from scripts.webapp_dash import candidate_graph as graph
from scripts.webapp_dash.candidate_workbench import _browse_chain, context_key


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
    assert {d['species'] for d in species} == {'CCO', 'COC', 'CC=O', 'CC(=O)O', 'O', '[H][H]'}
    assert len(species) == 6
    assert len(reactions) == 5
    alternatives = [d for d in reactions if d['reaction_key'] in {
        'CCO->CC=O', 'CCO+O->CC=O+[H][H]'}]
    assert len(alternatives) == 2  # Same carrier pair, different complete reactions.
    shared = next(d for d in reactions if d['reaction_key'] == 'CC=O->CC(=O)O')
    assert shared['event_count'] == 5  # Not 15 when three routes share it.
    assert shared['members'] == [dict(signature_id=f'path-{i}', step_index=j)
                                 for i, j in [(0, 1), (1, 1), (2, 2)]]
    assert len([d for d in data if d['kind'] == 'edge']) == 12
    assert report == before


def test_reaction_node_identity_retains_direction_and_stoichiometry():
    base = dict(reaction_key='CCO+CCO->COC', carried_from='CCO', carried_to='COC')
    assert len({graph.step_id(s) for s in [base,
        dict(base, reaction_key='CCO->COC'),
        dict(base, reaction_key='COC->CCO+CCO', carried_from='COC', carried_to='CCO'),
        dict(base, carried_to='O')]}) == 3


def test_repeated_reactant_keeps_stoichiometry_on_one_edge():
    step = dict(reaction_key='A+A->B', carried_from='A', carried_to='B',
                reactants=['A', 'A'], products=['B'], event_count=2)
    data = [element['data'] for element in graph.elements({'paths': [
        dict(signature_id='route', species=['A', 'B'], steps=[step])]})]
    incoming = [item for item in data if item['kind'] == 'edge' and item['side'] == 'reactant']
    assert len(incoming) == 1
    assert incoming[0]['species'] == 'A'
    assert incoming[0]['multiplicity'] == 2
    assert incoming[0]['label'] == '×2'


def test_two_carriers_share_one_complete_reaction_node_without_merging_routes(app):
    key = '[Cl]+C5H3O->C5H3ClO'
    def route(source, signature, shared):
        step = dict(reaction_key=key, carried_from=source, carried_to='C5H3ClO',
                    reactants=['[Cl]', 'C5H3O'], products=['C5H3ClO'],
                    event_count=19, transfer_event_count=19, max_shared_atoms=shared)
        return dict(signature_id=signature, species=[source, 'C5H3ClO'], steps=[step])
    report = dict(context=context_key({'dataset_id': 'one'}),
                  query_request_id='bimolecular-search',
                  paths=[route('[Cl]', 'chlorine', 1), route('C5H3O', 'ring', 9)])

    elements = graph.elements(report)
    data = [element['data'] for element in elements]
    reactions = [item for item in data if item['kind'] == 'reaction']
    assert len(reactions) == 1
    assert reactions[0]['event_count'] == 19  # The same events are not counted twice.
    assert {item['species'] for item in data if item['kind'] == 'species'} == {
        '[Cl]', 'C5H3O', 'C5H3ClO'}
    incoming = {item['species']: item for item in data
                if item['kind'] == 'edge' and item['side'] == 'reactant'}
    assert set(incoming) == {'[Cl]', 'C5H3O'}
    assert {member['signature_id'] for member in incoming['[Cl]']['carrier_members']} == {'chlorine'}
    assert {member['signature_id'] for member in incoming['C5H3O']['carrier_members']} == {'ring'}
    assert all(item['target'] == reactions[0]['id'] for item in incoming.values())
    styles = {entry['selector']: entry['style'] for entry in
              graph.stylesheet(elements, 'chlorine', [], 0)}
    assert styles['#' + incoming['[Cl]']['id']]['line-color'] == '#bd6b16'
    assert styles['#' + incoming['C5H3O']['id']]['line-style'] == 'dotted'
    # Clicking the other reactant's edge selects its carrier route even when
    # that reactant is also context for the currently focused route.
    picked = dict(incoming['C5H3O'], context=report['context'],
                  query_request_id=report['query_request_id'])
    selected = invoke(app, 'cp-focus.value@', 'cp-graph-pick.data', {
        'cp-report': report, 'app-store': {'dataset_id': 'one'},
        'cp-focus': 'chlorine', 'cp-graph-pick': picked})
    assert selected['cp-focus']['value'] == 'ring'


def test_click_rejects_stale_queries_and_uses_report_membership(report):
    picked = graph.elements(report)[0]['data']
    forged = dict(picked, members=[dict(signature_id='injected', step_index=99)])
    assert graph.resolve_pick(report, forged)['members'] == picked['members']
    assert graph.resolve_pick(dict(report, query_request_id='new-search'), picked) is None
    assert graph.resolve_pick(dict(report, context='new-dataset'), picked) is None
    assert graph.resolve_pick(None, picked) is None
    assert graph.resolve_pick(report, dict(picked, id='missing')) is None
    assert graph.elements({'paths': []}) == []


def test_wide_fan_fits_structures_without_changing_evidence():
    paths = []
    for i in range(1, 8):
        product = 'C' * i
        step = dict(reaction_key=f'CCO->{product}', carried_from='CCO',
                    carried_to=product, reactants=['CCO'], products=[product], event_count=i)
        paths.append(dict(signature_id=str(i), species=['CCO', product], steps=[step]))
    elements = graph.elements({'paths': paths})
    before = deepcopy(elements)
    positions = graph.fitted_layout(elements, {'width': 1200, 'height': 600})['positions']
    root = positions[graph.element_id('species', 'CCO')]
    products = [positions[graph.element_id('species', 'C' * i)] for i in range(1, 8)]
    assert all(p['y'] > root['y'] for p in products)
    assert len({p['y'] for p in products}) == 1
    assert min(b['x'] - a['x'] for a, b in zip(products, products[1:])) >= 150
    assert max(p['x'] for p in products) - min(p['x'] for p in products) <= 1100
    assert elements == before  # No pruning, renumbering, or changed membership.
    for element in elements:
        if 'position' in element:
            element['position'] = positions[element['data']['id']]
    # A resize/reset must not read the browser's new x coordinates as ranks.
    assert graph.fitted_layout(elements, {'width': 1200, 'height': 600})['positions'] == positions


def test_long_route_changes_orientation_for_narrow_canvas(report):
    elements = graph.elements(dict(report, paths=[report['paths'][2]]))
    first, last = [graph.element_id('species', species) for species in ('CCO', 'CC(=O)O')]
    wide = graph.fitted_layout(elements, {'width': 1500, 'height': 500})['positions']
    narrow = graph.fitted_layout(elements, {'width': 360, 'height': 600})['positions']
    assert wide[first]['y'] == wide[last]['y']
    assert wide[first]['x'] < wide[last]['x']
    assert narrow[first]['x'] == narrow[last]['x']
    assert narrow[first]['y'] < narrow[last]['y']
    assert graph.fitted_layout([])['positions'] == {}


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


def test_candidate_request_accepts_three_anchor_modes_and_continues(app, report):
    values = {'app-store': {'dataset_id': 'one'},
              'cp-start': 'CCO', 'cp-target': 'CC(=O)O', 'cp-mode': 'target',
              'cp-depth': None, 'cp-limit': 20, 'cp-quality-view': 'persistent',
              'cp-return-window': 3, 'cp-return-basis': 'topology',
              'cp-expansions': 2000, 'cp-frontier': 5000,
              'cp-prefixes': 10000, 'cp-examined': 2000, 'cp-seconds': 5}
    target = invoke(app, '..cp-request.data', 'cp-search.n_clicks', values)
    assert target['cp-request']['data']['query']['max_steps'] is None
    values['cp-mode'] = 'reverse'
    reverse = invoke(app, '..cp-request.data', 'cp-search.n_clicks', values)
    assert reverse['cp-request']['data']['query'] == {
        **target['cp-request']['data']['query'], 'start': '', 'mode': 'reverse', 'max_steps': 1}
    values['cp-mode'] = 'explore'
    explore = invoke(app, '..cp-request.data', 'cp-search.n_clicks', values)
    assert explore['cp-request']['data']['query']['target'] == ''
    assert explore['cp-request']['data']['query']['max_steps'] == 1
    continued_report = dict(report, query=dict(report['query'], mode='explore'))
    continued = invoke(app, '..cp-request.data', 'cp-continue.n_clicks', dict(values,
        **{'cp-report': continued_report, 'cp-focus': 'path-0'}))
    assert continued['cp-request']['data']['query']['start'] == 'CC(=O)O'
    nav = continued['cp-nav-anchor']['data']
    assert nav['species'] == 'CC(=O)O'
    assert nav['trail'] == ['CCO', 'CC=O', 'CC(=O)O']

    assert len(nav['steps']) == 2
    assert nav['segments'][0]['query']['start'] == 'CCO'
    back = invoke(app, '..cp-request.data', 'cp-back.n_clicks', dict(values,
        **{'cp-report': dict(continued_report, query=continued['cp-request']['data']['query']),
           'cp-nav-anchor': nav, 'cp-focus': 'path-0'}))
    assert back['cp-request']['data']['query']['start'] == 'CCO'
    assert back['cp-nav-anchor']['data']['trail'] == ['CCO']
    assert back['cp-nav-anchor']['data']['segments'] == []
    paged_report = dict(continued_report, query=dict(continued_report['query'],
        anchor_offset=0, max_paths=1), next_offset=1)
    paged = invoke(app, '..cp-request.data', 'cp-page-next.n_clicks', dict(values,
        **{'cp-report': paged_report}))
    assert paged['cp-request']['data']['query']['anchor_offset'] == 1


def test_continue_preserves_submitted_direction_and_new_search_resets_history(app, report):
    report['query'].update(mode='explore', target='', direction_view='net', quality_view='raw')
    values = {'app-store': {'dataset_id': 'one'}, 'cp-mode': 'explore',
              'cp-start': 'CCO', 'cp-target': '', 'cp-depth': None, 'cp-limit': 20,
              'cp-report': report, 'cp-focus': 'path-0',
              'cp-direction-view': 'observed', 'cp-quality-view': 'persistent',
              'cp-return-window': 3, 'cp-return-basis': 'topology'}
    continued = invoke(app, '..cp-request.data', 'cp-continue.n_clicks', values)
    query = continued['cp-request']['data']['query']
    assert query['direction_view'] == 'net' and query['quality_view'] == 'raw'
    values['cp-nav-anchor'] = continued['cp-nav-anchor']['data']
    searched = invoke(app, '..cp-request.data', 'cp-search.n_clicks', values)
    assert searched['cp-nav-anchor']['data'] is None
    assert searched['cp-request']['data']['query']['direction_view'] == 'observed'
    assert searched['cp-request']['data']['query']['quality_view'] == 'persistent'


def test_browse_history_is_joined_to_current_branches_without_becoming_current_evidence(report):
    history = report['paths'][0]
    nav = {
        'context': report['context'], 'mode': 'explore',
        'trail': history['species'], 'steps': history['steps'],
    }
    current_step = dict(reaction_key='CC(=O)O->CO2', carried_from='CC(=O)O',
                        carried_to='CO2', reactants=['CC(=O)O'], products=['CO2'],
                        event_count=4, transfer_event_count=4)
    current = dict(report, query_request_id='search-two',
                   query={'start': 'CC(=O)O', 'target': '', 'mode': 'explore'},
                   paths=[dict(signature_id='current-0', step_count=1,
                               species=['CC(=O)O', 'CO2'], steps=[current_step])])

    display = graph.with_browse_history(current, nav)
    path = display['paths'][0]
    assert path['species'] == ['CCO', 'CC=O', 'CC(=O)O', 'CO2']
    assert path['_display_step_indexes'] == [None, None, 0]
    elements = graph.elements(display)
    historical = [e['data'] for e in elements
                  if e['data']['kind'] == 'reaction' and e['data'].get('historical')]
    assert {e['reaction_key'] for e in historical} == {'CCO->CC=O', 'CC=O->CC(=O)O'}
    current_reaction = next(e['data'] for e in elements
                            if e['data'].get('reaction_key') == 'CC(=O)O->CO2')
    assert not current_reaction.get('historical')
    styles = graph.stylesheet(elements, 'current-0', [], 0)
    assert any(s['selector'] == '#' + current_reaction['id']
               and s['style']['background-color'] == '#bd6b16' for s in styles)


def test_empty_next_search_still_displays_selected_browse_history(report):
    history = report['paths'][0]
    current = dict(report, query={'start': history['species'][-1], 'target': '', 'mode': 'explore'},
                   paths=[])
    display = graph.with_browse_history(current, {
        'context': report['context'], 'mode': 'explore',
        'trail': history['species'], 'steps': history['steps'],
    })
    assert display['paths'][0]['signature_id'] == graph.BROWSE_HISTORY_SIGNATURE
    assert display['paths'][0]['species'] == history['species']


def test_reverse_continue_actions_remain_a_forward_directed_display_chain():
    def segment(start, end):
        step = dict(reaction_key=f'{start}->{end}', carried_from=start, carried_to=end,
                    event_count=1, transfer_event_count=1)
        return {'species': [start, end], 'steps': [step]}

    trail, steps = _browse_chain([segment('A', 'T'), segment('B', 'A')], 'reverse')
    assert trail == ['B', 'A', 'T']
    assert [step['reaction_key'] for step in steps] == ['B->A', 'A->T']


def test_graph_filters_highlights_clicks_and_dataset_reset(app, report):
    values = {'cp-report': report, 'app-store': {'dataset_id': 'one'},
              'cp-graph-scope': 'all', 'cp-routes': [], 'cp-focus': 'path-0'}
    rendered = invoke(app, '..cp-graph.elements', 'cp-report.data', values)
    elements = rendered['cp-graph']['elements']
    assert len(elements) == 23
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
    # The third route has branches above/below it in the union. Focusing it
    # must form a compact row rather than retaining those union positions.
    assert {e['position']['y'] for e in filtered if 'position' in e} == {0}
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


def test_step_evidence_reveals_only_current_graph_selection(app, report):
    picked = next(e['data'] for e in graph.elements(report) if e['data']['kind'] == 'reaction')
    values = {'cp-report': report, 'app-store': {'dataset_id': 'one'}, 'cp-graph-pick': picked}
    assert invoke(app, 'cp-evidence.open', 'cp-graph-pick.data', values)['cp-evidence']['open'] is True
    assert invoke(app, 'cp-evidence.open', 'cp-request.data', values)['cp-evidence']['open'] is False
    stale = dict(values, **{'app-store': {'dataset_id': 'two'}})
    assert invoke(app, 'cp-evidence.open', 'cp-graph-pick.data', stale)['cp-evidence']['open'] is False


def test_ready_capability_is_quiet_but_missing_evidence_keeps_recovery(app, monkeypatch):
    from reacnet_scope import services as svc

    monkeypatch.setattr(svc, 'candidate_search_status', lambda _: dict(available=True, message='ready'))
    ready = invoke(app, '..cp-capability.children', 'app-store.data', {'app-store': {}})
    assert ready['cp-capability']['children'] == ''
    assert ready['cp-prepare']['style'] == {'display': 'none'}

    monkeypatch.setattr(svc, 'candidate_search_status', lambda _: dict(available=False, message='缺少事件索引'))
    missing = invoke(app, '..cp-capability.children', 'app-store.data', {'app-store': {}})
    assert '缺少事件索引' in json.dumps(missing, ensure_ascii=False)
    assert missing['cp-prepare']['style'] == {}


def test_search_presentation_resets_for_new_dataset_and_keeps_empty_result_editable(app, report):
    values = {'cp-report': report, 'app-store': {'dataset_id': 'one'}}
    found = invoke(app, '..cp-results.style', 'cp-report.data', values)
    assert found['cp-search-settings']['open'] is False
    assert found['cp-results']['style'] == {}
    empty = invoke(app, '..cp-results.style', 'cp-report.data', dict(values,
        **{'cp-report': dict(report, paths=[])}))
    assert empty['cp-search-settings']['open'] is True
    reset = invoke(app, '..cp-results.style', 'app-store.data', dict(values,
        **{'app-store': {'dataset_id': 'two'}}))
    assert reset['cp-results']['style'] == {'display': 'none'}
    assert reset['cp-search-settings']['open'] is True


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


def test_reading_view_localizes_shared_participants_without_changing_identity():
    steps = [dict(reaction_key=f'{start}+O+O->{end}+O', carried_from=start, carried_to=end,
                  reactants=[start, 'O', 'O'], products=[end, 'O'], event_count=5)
             for start, end in [('CCO', 'CC=O'), ('CC=O', 'CC(=O)O')]]
    report = dict(context='dataset', query_request_id='query', paths=[
        dict(signature_id='route', species=['CCO', 'CC=O', 'CC(=O)O'], steps=steps)])
    original = graph.elements(report)
    before = deepcopy(original)
    localized = graph.local_participants(original, {'route'})
    copies = [e for e in localized if e['data'].get('layout_context_for')]
    assert len(copies) == 4  # Both sides of both reactions retain oxygen.
    oxygen = next(e for e in original if e['data'].get('species') == 'O')
    assert {e['data']['species'] for e in copies} == {'O'}
    assert {e['data']['label'] for e in copies} == {oxygen['data']['label']}
    assert len({e['data']['id'] for e in copies}) == 4
    assert [e['data']['multiplicity'] for e in localized
            if e['data']['kind'] == 'edge' and e['data']['species'] == 'O'] == [2, 1, 2, 1]
    layout = graph.fitted_layout(localized, {'width': 1200, 'height': 600}, reading=True)
    for node in copies:
        data = node['data']
        assert graph.resolve_pick(report, dict(data, species='forged'))['species'] == 'O'
        assert graph.resolve_pick(dict(report, query_request_id='new'), data) is None
        point, reaction = layout['positions'][data['id']], layout['positions'][data['layout_context_for']]
        assert abs(point['x'] - reaction['x']) == 65
        assert abs(point['y'] - reaction['y']) == 140
    assert original == before


def test_long_route_keeps_readable_scale_and_can_locate_a_later_step():
    species = ['C' * i for i in range(1, 12)]
    steps = [dict(reaction_key=f'{a}->{b}', carried_from=a, carried_to=b,
                  reactants=[a], products=[b], event_count=1) for a, b in zip(species, species[1:])]
    elements = graph.elements({'paths': [dict(signature_id='route', species=species, steps=steps)]})
    size = {'width': 1100, 'height': 600}
    layout = graph.fitted_layout(elements, size, reading=True)
    assert layout['fit'] is False and layout['zoom'] >= .9
    start = layout['positions'][graph.element_id('species', 'C')]
    assert 70 <= start['x'] * layout['zoom'] + layout['pan']['x'] <= 110
    target = graph.step_id(steps[-1])
    viewport = graph.reading_viewport(layout['positions'], size, focus=target, zoom=1)
    point = layout['positions'][target]
    assert point['x'] + viewport['pan']['x'] == size['width'] / 2
    assert point['y'] + viewport['pan']['y'] == size['height'] / 2
    assert graph.fitted_layout(elements, size)['fit'] is True


def test_overview_refits_when_size_and_elements_change_in_one_update(app, report):
    graph_elements = graph.elements(report)
    values = {'cp-graph': graph_elements, 'cp-graph-size': {'width': 1400, 'height': 600},
              'cp-graph-scope': 'all', 'cp-step': 0, 'cp-focus': 'path-0'}
    initial = invoke(app, '..cp-graph.layout', 'cp-graph.elements', values)
    values['cp-graph-layout-state'] = initial['cp-graph-layout-state']['data']
    values['cp-graph-size'] = {'width': 900, 'height': 600}
    # Cytoscape emits updated elements while the ResizeObserver updates width;
    # the first trigger alone does not tell us whether the canvas resized.
    resized = invoke(app, '..cp-graph.layout', 'cp-graph.elements', values)
    assert resized['cp-graph']['layout']['fit'] is True
    assert resized['cp-graph']['layout']['viewport'] == {'width': 900, 'height': 600}
    assert resized['cp-graph']['layout']['positions'] == initial['cp-graph']['layout']['positions']


def test_rendered_edge_identity_never_changes_endpoints_between_views(report):
    overview = graph.elements(report)
    reading = graph.local_participants(overview, {'path-0', 'path-1', 'path-2'})
    def endpoints(elements):
        return {e['data']['id']: (e['data']['source'], e['data']['target'])
                for e in elements if e['data']['kind'] == 'edge'}
    before, after = endpoints(reading), endpoints(overview)
    for edge_id in before.keys() & after.keys():
        assert before[edge_id] == after[edge_id]
    original = {e['data']['id']: e['data'] for e in overview if e['data']['kind'] == 'edge'}
    for edge in reading:
        data = edge['data']
        if data['kind'] != 'edge':
            continue
        resolved = graph.resolve_pick(report, dict(data, members=[{'signature_id': 'forged', 'step_index': 9}]))
        canonical = next(e for e in original.values() if e['reaction_id'] == data['reaction_id']
                         and e['side'] == data['side'] and e['species'] == data['species'])
        assert resolved['members'] == canonical['members']
        assert resolved['multiplicity'] == canonical['multiplicity']
        assert graph.resolve_pick(dict(report, query_request_id='new'), data) is None
