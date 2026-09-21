from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.path_search import CandidateReader, discover_indexed_candidates
from scripts.webapp_dash.candidate_workbench import accept_result, comparison, context_key


@pytest.fixture
def source(tmp_path):
    # Steps deliberately occur in reverse chronological order and have no
    # molecular association: they still establish network candidate routes.
    path = tmp_path / 'sample.reactionevent.csv'
    path.write_text('Timestep_Index,Reactant,Product\n'
                    '0,CC=O,CC(=O)O\n'
                    '1,CCO+O,CC=O+[H][H]\n'
                    '2,CCO,COC\n'
                    '3,COC,CC(=O)O\n'
                    '4,CC=O,CCO\n'
                    '5,CCO+O,CC=O+[H][H]\n'
                    '6,CCO+CCO,CCCCO\n')
    EVENT_EVIDENCE_STORE.build(str(path))
    return {'reactionevent': str(path)}


def test_independent_steps_create_target_candidates_without_molecules(source):
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O', max_steps=3)
    assert report['query_complete']
    assert len(report['paths']) == 2
    assert {tuple(p['species']) for p in report['paths']} == {
        ('CCO', 'CC=O', 'CC(=O)O'), ('CCO', 'COC', 'CC(=O)O')}
    route = next(p for p in report['paths'] if p['species'][1] == 'CC=O')
    assert route['continuous_md'] == 'not_evaluated'
    assert route['steps'][0]['reactants'] == ['CCO', 'O']
    assert route['steps'][0]['products'] == ['CC=O', '[H][H]']
    assert [s['event_count'] for s in route['steps']] == [2, 1]
    assert report['cycle_closures']
    assert svc.candidate_step_events(source, report, route['signature_id'], 0)['total'] == 2


def test_formula_selects_all_exact_isomers_and_search_does_not_merge(source):
    matches = svc.search_candidate_species(source, 'C2H6O')
    assert {r['species'] for r in matches['rows']} == {'CCO', 'COC'}
    report = svc.search_candidate_paths(source, 'COC', target='CC=O', max_steps=4)
    assert report['paths'] == []  # no reverse inference or formula bridge
    assert report['status'] == 'not_found_within_constraints'


def test_counts_stoichiometry_limits_and_exports(source):
    report = svc.search_candidate_paths(source, 'CCO', mode='explore', max_steps=1, max_paths=100)
    route = next(p for p in report['paths'] if p['species'][-1] == 'CCCCO')
    assert route['steps'][0]['reactants'] == ['CCO', 'CCO']
    assert report['horizon_limited'] and report['query_complete']
    rows = list(csv.DictReader(io.StringIO(svc.candidate_paths_csv(report))))
    assert all(json.loads(r['source_revision']) == report['source_revision'] for r in rows)
    limited = svc.search_candidate_paths(source, 'CCO', mode='explore', max_paths=1)
    assert not limited['query_complete'] and 'result_limit' in limited['truncation_reasons']
    assert len(limited['paths']) == 1
    no_result = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O', max_expansions=1)
    assert not no_result['query_complete'] and no_result['status'] == 'truncated/inconclusive'


def test_index_query_never_reads_raw_or_aggregate_network(source, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('raw scan/build forbidden during online query')
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, 'build', forbidden)
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if str(path) == source['reactionevent']:
            forbidden()
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O')
    assert len(report['paths']) == 2
    opened = EVENT_EVIDENCE_STORE.open_required(source['reactionevent'])
    connection = sqlite3.connect(opened['index_path'])
    plan = connection.execute('EXPLAIN QUERY PLAN SELECT reaction_key FROM candidate_adjacency WHERE species=? ORDER BY reaction_key LIMIT 20', ('CCO',)).fetchall()
    assert any('SEARCH' in row[3] and 'species=?' in row[3] for row in plan)
    connection.close()


def test_old_index_requires_explicit_rebuild_and_source_change_rejects(source):
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O')
    opened = EVENT_EVIDENCE_STORE.open_required(source['reactionevent'])
    with sqlite3.connect(opened['index_path']) as con:
        con.execute("DELETE FROM meta WHERE key='candidate_adjacency_version'")
    status = svc.candidate_search_status(source)
    assert not status['available'] and '重建' in status['message']
    Path(opened['index_path']).unlink()
    EVENT_EVIDENCE_STORE.build(source['reactionevent'])
    with pytest.raises(svc.ServiceError, match='版本'):
        svc.candidate_step_events(source, report, report['paths'][0]['signature_id'], 0)


def test_late_result_cannot_replace_new_request_or_dataset():
    store = {'dataset_id': 'a', 'source_revision': {'x': 1}, 'artifacts': {'reactionevent': '/a'}}
    request = {'request_id': 'one', 'context': context_key(store)}
    raw = dict(request, report={})
    assert accept_result(raw, request, store)
    assert not accept_result(raw, dict(request, request_id='two'), store)
    assert not accept_result(raw, request, dict(store, dataset_id='b'))
    assert not accept_result(raw, None, store)


def test_step_handoff_revalidates_and_compares_exact_carriers(source):
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O')
    paths = [dict(p, display_rank=i) for i, p in enumerate(report['paths'], 1)]
    assert comparison(paths)
    with pytest.raises(svc.ServiceError):
        svc.candidate_step_events(source, report, 'stale', 0)
    with pytest.raises(svc.ServiceError):
        svc.candidate_step_events(source, report, paths[0]['signature_id'], 99)


def test_render_structure_and_route_views(source):
    from scripts.webapp_dash.candidate_workbench import structure, route_summary
    from plotly.utils import PlotlyJSONEncoder
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O')
    assert 'structure.svg' in json.dumps(structure('CCO'), cls=PlotlyJSONEncoder)
    assert '候选路线' in json.dumps(route_summary(report), cls=PlotlyJSONEncoder, ensure_ascii=False)


def test_cli_shares_indexed_query(source, tmp_path, capsys):
    from scripts.rng_query_cli import main
    out = tmp_path / 'report.json'
    assert main(['candidate-search', '--source', source['reactionevent'], '--start', 'CCO',
                 '--target', 'CC(=O)O', '--out-json', str(out)]) == 0
    report = json.loads(out.read_text())
    assert len(report['paths']) == 2 and report['schema_version'].endswith('/v2')
    capsys.readouterr()


def test_unrelated_network_growth_does_not_expand_local_query(tmp_path):
    from reacnet_scope.path_search import materialize_candidate_adjacency
    reads = []
    for size in (100, 10000):
        index = tmp_path / f'graph-{size}.sqlite'
        with sqlite3.connect(index) as con:
            con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            con.execute('CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY,total_events INTEGER)')
            con.executemany('INSERT INTO reaction_summary VALUES(?,1)',
                            [('CCO->CC=O',), ('CC=O->CC(=O)O',)])
            con.executemany('INSERT INTO reaction_summary VALUES(?,1)',
                            ((f'[C:{i+100}]->[C:{i+101}]',) for i in range(size)))
            materialize_candidate_adjacency(con)
        reader = CandidateReader({'index_path': str(index)})
        try:
            result = discover_indexed_candidates(reader, 'CCO', target='CC(=O)O')
            reads.append(result['adjacency_rows_read'])
            assert len(result['paths']) == 1
            assert result['query_complete']
        finally:
            reader.close()
    assert reads == [2, 2]


def test_source_revision_survives_browser_json_numbers(source):
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O')
    assert all(isinstance(v['mtime_ns'], str) for v in report['source_revision'].values())
    transported = json.loads(json.dumps(report))
    page = svc.candidate_step_events(source, transported, transported['paths'][0]['signature_id'], 0)
    assert page['total'] > 0


def test_dash_result_and_event_callbacks_clear_on_dataset_change(source):
    from scripts.webapp_dash.app import create_app
    app = create_app()
    store = {'dataset_id': 'first', 'artifacts': source}
    report = dict(svc.search_candidate_paths(source, 'CCO', target='CC(=O)O'), context=context_key(store))
    render_key = next(k for k in app.callback_map if k.startswith('..cp-result-summary.children'))
    render = app.callback_map[render_key]['callback'].__wrapped__
    assert len(render(report, store)[1]) == 2
    stale = render(report, dict(store, dataset_id='second'))
    assert stale[0] == '' and stale[1] == [] and stale[4] is None
    assert stale[5] and stale[6]  # stale downloads disabled
    request_key = 'cp-raw.data'
    assert app.callback_map[request_key]['callback'].__wrapped__(None) is None


def test_native_candidate_and_evidence_queries_do_not_open_hdf5(tmp_path, monkeypatch):
    import h5py
    from tests.test_timed_evidence import write_timeline
    source = write_timeline(tmp_path / 'run.timeline.h5')
    EVENT_EVIDENCE_STORE.build(str(source))
    def forbidden(*args, **kwargs):
        raise AssertionError('online candidate query opened native source')
    monkeypatch.setattr(h5py, 'File', forbidden)
    artifacts = {'timeline': str(source)}
    report = svc.search_candidate_paths(artifacts, '[C]', target='[C][O]')
    assert len(report['paths']) == 1
    assert report['carrier_policy'] == 'event_local_dominant_atom_descendant'
    assert report['paths'][0]['steps'][0]['reactants'] == ['[C]', '[O]']
    page = svc.candidate_step_events(artifacts, report, report['paths'][0]['signature_id'], 0)
    assert page['total'] == 1 and page['rows'][0]['association_status'] == 'matched'
    assert page['rows'][0]['shared_atom_count'] == 1


def test_target_after_first_adjacency_page_is_not_silently_excluded(tmp_path):
    from reacnet_scope.path_search import materialize_candidate_adjacency
    index = tmp_path / 'hub.sqlite'
    with sqlite3.connect(index) as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY,total_events INTEGER)')
        con.executemany('INSERT INTO reaction_summary VALUES(?,1)',
                        ((f'[C]->[C:{i}]',) for i in range(256)))
        con.execute("INSERT INTO reaction_summary VALUES('[C]->[O]',1)")
        materialize_candidate_adjacency(con)
    reader = CandidateReader({'index_path': str(index)})
    try:
        result = discover_indexed_candidates(reader, '[C]', target='[O]', max_steps=1)
    finally:
        reader.close()
    assert [p['species'] for p in result['paths']] == [['[C]', '[O]']]


def test_queued_target_connections_survive_exhausted_adjacency_budget(tmp_path):
    from reacnet_scope.path_search import materialize_candidate_adjacency
    index = tmp_path / 'budget.sqlite'
    with sqlite3.connect(index) as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY,total_events INTEGER)')
        con.executemany('INSERT INTO reaction_summary VALUES(?,1)',
                        [(key,) for key in ['[C]->[C:1]', '[C]->[C:2]', '[C]->[C:3]',
                                            '[C:1]->[N:1]', '[C:1]->[N:2]', '[C:2]->[O]']])
        materialize_candidate_adjacency(con)
    reader = CandidateReader({'index_path': str(index)})
    try:
        result = discover_indexed_candidates(reader, '[C]', target='[O]', max_expansions=4)
    finally:
        reader.close()
    assert [p['species'] for p in result['paths']] == [['[C]', '[C:2]', '[O]']]
    assert not result['query_complete']
    assert 'adjacency_budget' in result['truncation_reasons']
    assert result['target_probes'] <= 4


def test_target_results_stay_shortest_first_and_do_not_duplicate_direct_steps(tmp_path):
    path = tmp_path / 'lengths.reactionevent.csv'
    path.write_text('Timestep_Index,Reactant,Product\n'
                    '0,CCO,CC=O\n1,CCO,COC\n2,COC,CC=O\n')
    EVENT_EVIDENCE_STORE.build(str(path))
    artifacts = {'reactionevent': str(path)}
    report = svc.search_candidate_paths(artifacts, 'CCO', target='CC=O', max_steps=4)
    assert [p['species'] for p in report['paths']] == [['CCO', 'CC=O'], ['CCO', 'COC', 'CC=O']]
    report = svc.search_candidate_paths(artifacts, 'CCO', target='CC=O', max_steps=1)
    assert [p['species'] for p in report['paths']] == [['CCO', 'CC=O']]
    assert report['query_complete'] and report['horizon_limited']


def test_incomplete_empty_summary_does_not_claim_no_route(source):
    from scripts.webapp_dash.candidate_workbench import route_summary
    from plotly.utils import PlotlyJSONEncoder
    report = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O', max_expansions=1)
    rendered = json.dumps(route_summary(report), cls=PlotlyJSONEncoder, ensure_ascii=False)
    assert '搜索未完成，尚未找到' in rendered
    assert '目标连接检查预算' in rendered


def test_carrier_follows_event_local_dominant_atom_descendant(tmp_path):
    from reacnet_scope.path_search import materialize_candidate_adjacency
    index = tmp_path / 'atom-transfer.sqlite'
    first = '[C][C]+[O]->[H]+[C][C][O]'
    second = '[H]+[N]->[N][H]'
    with sqlite3.connect(index) as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY,total_events INTEGER)')
        con.executemany('INSERT INTO reaction_summary VALUES(?,1)', [(first,), (second,)])
        con.execute('''CREATE TABLE events(
            event_id TEXT PRIMARY KEY, reaction_key TEXT, association_status TEXT,
            reactant_participants_json TEXT, product_participants_json TEXT)''')
        con.execute('INSERT INTO events VALUES(?,?,?,?,?)', ('one', first, 'matched',
            json.dumps([{'species': '[C][C]', 'atom_ids': [1, 2]},
                        {'species': '[O]', 'atom_ids': [3]}]),
            json.dumps([{'species': '[H]', 'atom_ids': [4]},
                        {'species': '[C][C][O]', 'atom_ids': [1, 2, 3]}])))
        con.execute('INSERT INTO events VALUES(?,?,?,?,?)', ('two', second, 'matched',
            json.dumps([{'species': '[H]', 'atom_ids': [5]},
                        {'species': '[N]', 'atom_ids': [6]}]),
            json.dumps([{'species': '[N][H]', 'atom_ids': [5, 6]}])))
        materialize_candidate_adjacency(con)
    reader = CandidateReader({'index_path': str(index)})
    try:
        result = discover_indexed_candidates(reader, '[C][C]', target='[N][H]', max_steps=2)
        explored = discover_indexed_candidates(reader, '[C][C]', mode='explore', max_steps=1)
    finally:
        reader.close()
    assert result['paths'] == []
    assert [path['species'] for path in explored['paths']] == [['[C][C]', '[C][C][O]']]
    step = explored['paths'][0]['steps'][0]
    assert step['transfer_event_count'] == 1
    assert step['max_shared_atoms'] == 2
    assert result['carrier_policy'] == 'event_local_dominant_atom_descendant'
