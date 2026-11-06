"""Complete reaction hyperedges and portable, bounded graph exports."""
from copy import deepcopy
import json
import xml.etree.ElementTree as ET

import pytest

from reacnet_scope import build_candidate_hypergraph, candidate_hypergraph_svg
from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts import rng_query_cli as cli
from scripts.webapp_dash import candidate_graph
from scripts.webapp_dash.candidate_workbench import context_key
from tests.test_candidate_graph import invoke


@pytest.fixture
def report():
    forward = dict(reaction_key='CCO+CCO+O->CC=O+O',
        reactants=['CCO', 'O', 'CCO'], products=['CC=O', 'O'],
        carried_from='CCO', carried_to='CC=O', event_count=7,
        forward_count=7, reverse_count=2, net_count=5, transfer_event_count=3,
        transfer_basis='event_local_dominant_atom_descendant', max_shared_atoms=3)
    isomer = dict(reaction_key='CCO->COC', reactants=['CCO'], products=['COC'],
        carried_from='CCO', carried_to='COC', event_count=3,
        forward_count=3, reverse_count=0, net_count=3, transfer_event_count=2)
    reverse = dict(reaction_key='CC=O+O->CCO+CCO+O',
        reactants=['CC=O', 'O'], products=['O', 'CCO', 'CCO'],
        carried_from='CC=O', carried_to='CCO', event_count=2,
        forward_count=2, reverse_count=7, net_count=-5, transfer_event_count=1)
    return dict(schema_version='reacnet-scope/indexed-candidates/v5',
        source_revision={'sample.csv': {'size': 42, 'mtime_ns': '123'}},
        query={'start': 'CCO', 'direction_view': 'observed',
               'count_scope': 'published_revision_all_transitions', 'max_expansions': 5},
        query_complete=False, display_truncated=True, truncation_reasons=['adjacency_budget'],
        paths=[dict(signature_id=signature, species=[s['carried_from'], s['carried_to']],
                    steps=[s], continuous_md='not_evaluated') for signature, s in [
            ('forward', forward), ('other-carrier', dict(forward,
                carried_from='O', carried_to='O', transfer_event_count=2)),
            ('isomer', isomer), ('reverse', reverse)]])


def test_hypergraph_preserves_multisets_exact_isomers_and_independent_counts(report):
    before = deepcopy(report)
    graph = build_candidate_hypergraph(report)
    assert {s['species'] for s in graph['species']} == {'CCO', 'COC', 'CC=O', 'O'}
    assert len(graph['hyperedges']) == 3
    forward, isomer, reverse = graph['hyperedges']
    assert forward['id'] != reverse['id']
    assert forward['event_count'] == 7  # Two carriers do not create 14 occurrences.
    assert (forward['forward_count'], forward['reverse_count'], forward['net_count']) == (7, 2, 5)
    assert {t['species']: t['coefficient'] for t in forward['tail']} == {'CCO': 2, 'O': 1}
    assert {t['species']: t['coefficient'] for t in reverse['head']} == {'CCO': 2, 'O': 1}
    assert [r['display_steps'][0]['transfer_event_count'] for r in graph['routes'][:2]] == [3, 2]
    assert {m['signature_id'] for m in forward['members']} == {'forward', 'other-carrier'}
    assert graph['provenance']['source_revision'] == report['source_revision']
    assert graph['provenance']['query_complete'] is False
    assert graph['provenance']['truncation_reasons'] == ['adjacency_budget']
    assert graph['routes'][0]['continuous_md'] == 'not_evaluated'
    assert report == before
    graph['provenance']['query']['max_expansions'] = 99
    assert report == before  # A consumer cannot mutate the source result.


def test_selection_keeps_graph_numbers_and_removes_other_carrier_members(report):
    full = build_candidate_hypergraph(report)
    selected = build_candidate_hypergraph(report, signature_ids=['other-carrier'])
    assert len(selected['hyperedges']) == 1 and len(selected['species']) == 3
    assert selected['selection']['signature_ids'] == ['other-carrier']
    numbers = {n['id']: n['display_number'] for n in full['species']}
    assert all(n['display_number'] == numbers[n['id']] for n in selected['species'])
    terms = {t['species']: t for t in selected['hyperedges'][0]['tail']}
    assert terms['CCO']['carrier_members'] == []
    assert terms['O']['carrier_members'] == [dict(signature_id='other-carrier', step_index=0)]
    assert selected['provenance']['query_complete'] is False
    with pytest.raises(ValueError, match='所选路线'):
        build_candidate_hypergraph(report, signature_ids=['absent'])


def test_empty_result_retains_query_status_and_invalid_evidence_does_not_become_empty(report):
    graph = build_candidate_hypergraph(dict(report, paths=[]))
    assert graph['species'] == graph['hyperedges'] == graph['routes'] == []
    assert graph['provenance']['query_complete'] is False
    assert '没有返回' in candidate_hypergraph_svg(graph)
    with pytest.raises(ValueError, match='失败'):
        build_candidate_hypergraph({'error': 'missing index'})
    for field, value, message in [('reactants', [], '完整反应物'),
                                  ('event_count', 8, '计数不一致'),
                                  ('reactants', ['CCO', 'O'], '完整计量')]:
        broken = deepcopy(report)
        broken['paths'][1]['steps'][0][field] = value
        with pytest.raises(ValueError, match=message):
            build_candidate_hypergraph(broken)


def test_svg_contains_direction_coefficients_offline_structures_and_exact_metadata(report):
    graph = build_candidate_hypergraph(dict(report, carrier_policy='species_connectivity_only',
        query=dict(report['query'], direction_view='net')),
                                     signature_ids=['forward', 'isomer'])
    svg = ET.fromstring(candidate_hypergraph_svg(graph))
    ns = {'s': 'http://www.w3.org/2000/svg'}
    assert json.loads(svg.find('s:metadata', ns).text) == graph
    labels = [e.text for e in svg.findall('.//s:text', ns)]
    assert '×2' in labels and 'R1 · 净 5 次' in labels
    assert any('搜索未完成' in label for label in labels)
    assert any('未核查原子转移' in label for label in labels)
    images = svg.findall('.//s:image', ns)
    assert len(images) == 4
    assert all(e.attrib['href'].startswith('data:image/svg+xml;base64,') for e in images)
    edge_ids = {t['id'] for r in graph['hyperedges'] for t in r['tail'] + r['head']}
    arrows = [e for e in svg.findall('s:path', ns) if e.attrib.get('id') in edge_ids]
    assert len(arrows) == 6 and all('marker-end' in e.attrib for e in arrows)
    oversized = dict(graph, species=graph['species'] * 126)
    with pytest.raises(ValueError, match='500'):
        candidate_hypergraph_svg(oversized)


def test_browse_history_stays_separate_from_current_step_and_identity(report):
    current = dict(report, query={'mode': 'explore', 'start': 'CC=O'}, context='dataset',
        paths=[dict(signature_id='current', candidate_signature='original-current-candidate',
            species=['CC=O', 'CCO'], steps=[report['paths'][-1]['steps'][0]])])
    nav = dict(context='dataset', mode='explore', trail=['CCO', 'CC=O'],
               steps=[report['paths'][0]['steps'][0]],
               segments=[dict(report['paths'][0], source_revision=report['source_revision'])])
    graph = build_candidate_hypergraph(candidate_graph.with_browse_history(current, nav))
    assert graph['routes'][0]['candidate_signature'] == 'original-current-candidate'
    assert [s['step_index'] for s in graph['routes'][0]['display_steps']] == [None, 0]
    assert [s['historical'] for s in graph['routes'][0]['display_steps']] == [True, False]
    assert graph['browse_history_step_count'] == 1
    assert 'stroke-dasharray="6 4"' in candidate_hypergraph_svg(graph)


def test_cli_exports_from_prepared_index_without_raw_source_reads(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path / 'workspace'))
    source = tmp_path / 'run.reactionevent.csv'
    source.write_text('Timestep_Index,Reactant,Product\n0,CCO+CCO,COC\n1,COC,CCO+CCO\n2,CCO+CCO,COC\n')
    EVENT_EVIDENCE_STORE.build(str(source))
    original_open = open
    def guarded_open(file, *args, **kwargs):
        if str(file) == str(source):
            raise AssertionError('graph export opened raw RNG evidence')
        return original_open(file, *args, **kwargs)
    monkeypatch.setattr('builtins.open', guarded_open)
    json_path, svg_path = tmp_path / 'graph.json', tmp_path / 'graph.svg'
    args = cli.build_parser().parse_args(['candidate-search', '--source', str(source),
        '--start', 'CCO', '--mode', 'explore', '--direction-view', 'net',
        '--out-hypergraph-json', str(json_path), '--out-svg', str(svg_path)])
    assert args.func(args) == 0
    report = json.loads(capsys.readouterr().out)
    graph = json.loads(json_path.read_text())
    assert graph['provenance']['source_revision'] == report['source_revision']
    assert len(graph['hyperedges']) == 1
    reaction = graph['hyperedges'][0]
    assert (reaction['event_count'], reaction['reverse_count'], reaction['net_count']) == (2, 1, 1)
    assert [(t['species'], t['coefficient']) for t in reaction['tail']] == [('CCO', 2)]
    assert graph['routes'][0]['continuous_md'] == 'not_evaluated'
    assert graph['provenance']['query_complete']
    ET.fromstring(svg_path.read_text())


@pytest.mark.parametrize('trigger', ['cp-hypergraph-json', 'cp-svg'])
def test_dash_download_matches_selection_and_checks_source_revision(report, monkeypatch, trigger):
    from scripts.webapp_dash.app import create_app
    store = {'dataset_id': 'one'}
    report = dict(report, context=context_key(store))
    monkeypatch.setattr(svc, 'candidate_source_revision', lambda _: report['source_revision'])
    values = {'cp-report': report, 'app-store': store, 'cp-graph-scope': 'selected',
              'cp-focus': 'isomer', 'cp-routes': [], trigger: 1}
    response = invoke(create_app(), '..cp-download.data', trigger + '.n_clicks', values)
    download = response['cp-download']['data']
    content = download['content']
    if trigger == 'cp-svg':
        root = ET.fromstring(content)
        content = root.find('{http://www.w3.org/2000/svg}metadata').text
    exported = json.loads(content)
    assert [r['signature_id'] for r in exported['routes']] == ['isomer']
    assert {v['species'] for v in exported['species']} == {'CCO', 'COC'}
    assert exported['hyperedges'][0]['event_count'] == 3
    assert exported['hyperedges'][0]['display_number'] == 2
    assert exported['provenance']['query_complete'] is False
    # Replacing the dataset prevents a stale result from being downloaded.
    values['app-store'] = {'dataset_id': 'other'}
    invoke(create_app(), '..cp-download.data', trigger + '.n_clicks', values, expected_status=204)
    values['app-store'] = store
    revisions = iter([report['source_revision'], {'changed': True}])
    monkeypatch.setattr(svc, 'candidate_source_revision', lambda _: next(revisions))
    response = invoke(create_app(), '..cp-download.data', trigger + '.n_clicks', values)
    assert 'cp-download' not in response
    assert '版本已变化' in response['cp-message']['children']


def test_failed_query_clears_display_without_hiding_error_as_a_valid_empty_graph(report):
    from scripts.webapp_dash.app import create_app
    store = {'dataset_id': 'one'}
    broken = dict(report, context=context_key(store), error='事件索引失效')
    assert candidate_graph.elements(broken) == []
    response = invoke(create_app(), '..cp-graph.elements', 'cp-report.data',
                      {'cp-report': broken, 'app-store': store, 'cp-graph-scope': 'all'})
    assert response['cp-graph']['elements'] == []
    assert '查询失败' in response['cp-graph-summary']['children']
