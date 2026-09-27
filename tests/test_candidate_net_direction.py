"""Net direction is a full-equation count projection, not event pairing."""
import csv
import io
import json
import sqlite3

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.path_search import CandidateReader, discover_indexed_candidates, materialize_candidate_adjacency
from scripts.webapp_dash import candidate_graph as graph


@pytest.fixture
def reader(tmp_path):
    path = tmp_path / 'net.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY,total_events INTEGER)')
        con.executemany('INSERT INTO reaction_summary VALUES(?,?)', [
            ('CCO->COC', 111), ('COC->CCO', 110),
            ('CCO->CC=O', 5), ('CC=O->CCO', 5),
            ('CCO->CCO', 9),
            ('COC->CC=O', 3),  # A third edge need not be a reverse reaction.
            ('CCO+CCO->CCCCO', 8), ('CCCCO->CCO+CCO', 3),
            ('CCCCO->CCO', 20),  # Different stoichiometry must not cancel.
        ])
        materialize_candidate_adjacency(con)
    opened = CandidateReader({'index_path': str(path)})
    try:
        yield opened
    finally:
        opened.close()


def test_net_direction_counts_and_filtering_before_pagination(reader):
    report = discover_indexed_candidates(reader, 'CCO', mode='explore',
                                         direction_view='net', max_paths=1)
    first = report['paths'][0]['steps'][0]
    assert first['reaction_key'] == 'CCO+CCO->CCCCO'
    assert (first['forward_count'], first['reverse_count'], first['net_count']) == (8, 3, 5)
    assert report['next_offset'] == 1
    second = discover_indexed_candidates(reader, 'CCO', mode='explore',
                                          direction_view='net', max_paths=1, anchor_offset=1)
    step = second['paths'][0]['steps'][0]
    assert step['reaction_key'] == 'CCO->COC'
    assert (step['forward_count'], step['reverse_count'], step['net_count']) == (111, 110, 1)
    assert step['event_count'] == 111
    assert second['next_offset'] is None and second['query_complete']
    assert second['query']['direction_view'] == 'net'
    assert second['query']['quality_view'] == 'raw'
    assert second['query']['count_scope'] == 'published_revision_all_transitions'
    nodes = [e['data'] for e in graph.elements(second) if e['data']['kind'] == 'reaction']
    assert nodes[0]['label'] == 'R1 · 净 1 次'


def test_reverse_lookup_and_target_probe_obey_net_direction(reader):
    incoming = discover_indexed_candidates(reader, '', target='CCO', mode='reverse',
                                            direction_view='net')
    assert [p['species'] for p in incoming['paths']] == [['CCCCO', 'CCO']]
    report = discover_indexed_candidates(reader, 'COC', target='CCO', max_steps=1,
                                         direction_view='net')
    assert report['paths'] == [] and report['reachability_status'] == 'not_found'
    # Reuse of a reader cannot leak the previous query's direction filter.
    observed = discover_indexed_candidates(reader, 'COC', target='CCO', max_steps=1,
                                           direction_view='observed', quality_view='raw')
    assert observed['paths'][0]['steps'][0]['net_count'] == -1


def test_net_does_not_delete_multistep_cycles_or_change_candidate_identity(reader):
    reader.connection.close()
    with sqlite3.connect(reader.opened['index_path']) as con:
        con.execute("UPDATE candidate_reactions SET total_events=6 WHERE reaction_key='CC=O->CCO'")
    reader.connection = sqlite3.connect(reader.opened['index_path'])
    history = None
    for start, end in [('CCO', 'COC'), ('COC', 'CC=O'), ('CC=O', 'CCO')]:
        result = discover_indexed_candidates(reader, start, mode='explore', direction_view='net')
        path = next(p for p in result['paths'] if p['species'][-1] == end)
        observed = discover_indexed_candidates(reader, start, target=end, max_steps=1,
                                               direction_view='observed', quality_view='raw')
        assert path['candidate_signature'] == observed['paths'][0]['candidate_signature']
        if history is None:
            history = dict(mode='explore', trail=[start, end], steps=list(path['steps']))
        else:
            displayed = graph.with_browse_history(dict(result, paths=[path]), history)
            assert displayed['paths'][0]['species'] == history['trail'] + [end]
            history['trail'].append(end)
            history['steps'].extend(path['steps'])
    assert history['trail'] == ['CCO', 'COC', 'CC=O', 'CCO']


def test_service_cli_export_and_original_event_drilldown(tmp_path, monkeypatch, capsys):
    from scripts.rng_query_cli import main
    source = tmp_path / 'case.reactionevent.csv'
    source.write_text('Timestep_Index,Reactant,Product\n0,CCO,COC\n1,COC,CCO\n2,CCO,COC\n')
    EVENT_EVIDENCE_STORE.build(str(source))
    artifacts = {'reactionevent': str(source)}
    def forbidden(*args, **kwargs):
        raise AssertionError('online net query must not rebuild indexes')
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, 'build', forbidden)
    report = svc.search_candidate_paths(artifacts, 'CCO', target='COC', direction_view='net')
    assert svc.search_candidate_paths(artifacts, **report['query'])['query'] == report['query']
    page = svc.candidate_step_events(artifacts, report, report['paths'][0]['signature_id'], 0)
    assert page['total'] == 2  # Net 1 is not one selected surviving event.
    next_report = svc.search_candidate_paths(artifacts, 'COC', mode='explore', direction_view='net')
    nav = dict(mode='explore', trail=['CCO', 'COC'], steps=report['paths'][0]['steps'])
    displayed = graph.with_browse_history(next_report, nav)
    reactions = [e['data'] for e in graph.elements(displayed) if e['data']['kind'] == 'reaction']
    assert [(r['reaction_key'], r['label']) for r in reactions] == [('CCO->COC', 'R1 · 净 1 次')]
    row = next(csv.DictReader(io.StringIO(svc.candidate_paths_csv(report))))
    assert (row['forward_count'], row['reverse_count'], row['net_count']) == ('2', '1', '1')
    assert json.loads(row['query'])['direction_view'] == 'net'
    assert main(['candidate-search', '--source', str(source), '--start', 'CCO',
                 '--target', 'COC', '--direction-view', 'net']) == 0
    cli = json.loads(capsys.readouterr().out)
    assert cli['query'] == report['query']
    assert cli['paths'][0]['steps'][0]['net_count'] == 1


@pytest.mark.parametrize('unresolved_reverse', [False, True])
def test_atom_transfer_reader_uses_full_counts_without_return_folding(tmp_path, unresolved_reverse):
    a, b, c = '[C][C]', '[C]=[C]', '[C]#[C]'
    source = tmp_path / 'atoms.reactionevent.csv'
    molecules = tmp_path / 'atoms.molecules.csv'
    with source.open('w') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestep_Index', 'Reactant', 'Product'])
        writer.writerows([(0, a, b), (1, b, a), (2, a, b), (3, b, c)])
        if unresolved_reverse:
            writer.writerow([4, b, a])
    with molecules.open('w') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestep', 'Species', 'AtomIDs', 'BondIDs'])
        for frame, species in enumerate([a, b, a, b, c, c]):
            writer.writerow([frame * 100, species, '0;1', f'0-1-{ {a: 1, b: 2, c: 3}[species]}'])
    EVENT_EVIDENCE_STORE.build(str(source), str(molecules))
    artifacts = {'reactionevent': str(source), 'molecules': str(molecules)}
    for query in [dict(start=a, target=c), dict(start=a, mode='explore'),
                  dict(target=b, mode='reverse')]:
        report = svc.search_candidate_paths(artifacts, **query, direction_view='net', max_paths=1)
        assert report['carrier_policy'] == 'event_local_dominant_atom_descendant'
        if unresolved_reverse:
            assert report['paths'] == [] and report['query_complete']
        else:
            step = report['paths'][0]['steps'][0]
            assert (step['forward_count'], step['reverse_count'], step['net_count']) == (2, 1, 1)
            assert step['transfer_event_count'] == 2
            assert report['query']['quality_view'] == 'raw'
            assert step['quality']['folded_events'] > 0  # Facts do not filter the net view.
