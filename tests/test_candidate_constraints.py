"""Ordered waypoints constrain complete routes; exclusions only constrain carriers."""
from contextlib import contextmanager
import csv
import io
import json
from pathlib import Path
import sqlite3

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.path_search import (
    CandidateReader, discover_indexed_candidates, materialize_candidate_adjacency,
)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'constraints.reactionevent.csv'
    path.write_text('Timestep_Index,Reactant,Product\n'
                    '0,CCO+O,CC=O+[H][H]\n'
                    '1,CCO+O,CC=O+[H][H]\n'
                    '2,CCO,COC\n3,COC,CC=O\n4,CC=O,CC(=O)O\n'
                    '5,CCO,CC(=O)O\n6,COC,CC(=O)O\n')
    EVENT_EVIDENCE_STORE.build(str(path))
    return {'reactionevent': str(path)}


def test_ordered_waypoints_search_beyond_shortcuts_and_preserve_identity(source):
    complete = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O')
    result = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O',
        via_species=['COC', 'CC=O'], max_paths=1, max_candidates_examined=1)
    assert [p['species'] for p in result['paths']] == [['CCO', 'COC', 'CC=O', 'CC(=O)O']]
    assert result['reachability_status'] == 'found'
    same = next(p for p in complete['paths'] if p['species'] == result['paths'][0]['species'])
    assert result['paths'][0]['candidate_signature'] == same['candidate_signature']
    assert result['query']['via_species'] == ['COC', 'CC=O']
    assert result['paths'][0]['continuous_md'] == 'not_evaluated'


@pytest.mark.parametrize('via', [['CC=O', 'COC'], ['[H][H]']])
def test_wrong_order_or_coproduct_does_not_satisfy_waypoints(source, via):
    result = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O', via_species=via)
    assert result['paths'] == []
    assert result['reachability_status'] == 'not_found'
    assert result['query_complete']


def test_excluded_participants_remain_in_equation_and_counts(source):
    result = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O', via_species=['CC=O'],
        excluded_species=['O', '[H][H]', 'COC', 'O'])
    assert [p['species'] for p in result['paths']] == [['CCO', 'CC=O', 'CC(=O)O']]
    first = result['paths'][0]['steps'][0]
    assert first['reactants'] == ['CCO', 'O']
    assert first['products'] == ['CC=O', '[H][H]']
    assert first['event_count'] == first['forward_count'] == 2
    assert result['query']['excluded_species'] == ['COC', 'O', '[H][H]']
    events = svc.candidate_step_events(source, result, result['paths'][0]['signature_id'], 0)
    assert events['total'] == 2
    rows = list(csv.DictReader(io.StringIO(svc.candidate_paths_csv(result))))
    assert all(json.loads(row['query']) == result['query'] for row in rows)
    graph = svc.build_candidate_hypergraph(result)
    assert graph['provenance']['query'] == result['query']


@pytest.mark.parametrize('mode,start,target,excluded,expected', [
    ('explore', 'CCO', '', ['CC(=O)O', 'CC=O', '[H][H]'], ['CCO', 'COC']),
    ('reverse', '', 'CC(=O)O', ['CC=O', 'CCO'], ['COC', 'CC(=O)O']),
])
def test_exclusions_precede_pagination(source, mode, start, target, excluded, expected):
    result = svc.search_candidate_paths(source, start, target=target, mode=mode,
                                        excluded_species=excluded, max_paths=1)
    assert [p['species'] for p in result['paths']] == [expected]
    assert result['query_complete']
    assert result['next_offset'] is None


@contextmanager
def graph_reader(tmp_path, equations, matched=False):
    index = tmp_path / 'constraints.sqlite'
    with sqlite3.connect(index) as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('CREATE TABLE reaction_summary(reaction_key TEXT PRIMARY KEY,total_events INTEGER)')
        con.executemany('INSERT INTO reaction_summary VALUES(?,1)', [(e,) for e in equations])
        if matched:
            con.execute('''CREATE TABLE events(event_id TEXT PRIMARY KEY, reaction_key TEXT,
                association_status TEXT, reactant_participants_json TEXT, product_participants_json TEXT)''')
            for i, equation in enumerate(equations):
                left, right = equation.split('->')
                # Each event independently supports the transfer; IDs differ between steps.
                con.execute('INSERT INTO events VALUES(?,?,?,?,?)', (str(i), equation, 'matched',
                    json.dumps([{'species': left, 'atom_ids': [i + 1]}]),
                    json.dumps([{'species': right, 'atom_ids': [i + 1]}])))
        materialize_candidate_adjacency(con)
    reader = CandidateReader({'index_path': str(index)})
    try:
        yield reader
    finally:
        reader.close()


def test_reachable_legs_cannot_be_joined_through_repeated_species(tmp_path):
    with graph_reader(tmp_path, ['CCO->CC=O', 'CC=O->COC', 'COC->CC=O', 'CC=O->CC(=O)O']) as reader:
        result = discover_indexed_candidates(reader, 'CCO', target='CC(=O)O', via_species=['COC'])
    assert result['paths'] == []
    assert result['reachability_status'] == 'not_found'
    assert result['query_complete']


def test_prefixes_reaching_same_species_keep_waypoint_progress_and_visited_set(tmp_path):
    with graph_reader(tmp_path, ['CCO->CC=O', 'CCO->COC', 'COC->CC=O',
                                 'CC=O->CC(=O)O']) as reader:
        result = discover_indexed_candidates(reader, 'CCO', target='CC(=O)O', via_species=['COC'])
    assert [p['species'] for p in result['paths']] == [['CCO', 'COC', 'CC=O', 'CC(=O)O']]


@pytest.mark.parametrize('matched', [False, True])
def test_exclusion_filters_all_adjacency_modes_and_reader_can_be_reused(tmp_path, matched):
    with graph_reader(tmp_path, ['CCO->CC=O', 'CCO->COC', 'CC=O->CC(=O)O',
                                 'COC->CC(=O)O'], matched=matched) as reader:
        query = dict(quality_view='raw', excluded_species=['CC=O'], max_paths=1)
        for start, target, mode, expected in [
            ('CCO', '', 'explore', ['CCO', 'COC']),
            ('', 'CC(=O)O', 'reverse', ['COC', 'CC(=O)O']),
            ('CCO', 'CC(=O)O', 'target', ['CCO', 'COC', 'CC(=O)O']),
        ]:
            result = discover_indexed_candidates(reader, start, target=target, mode=mode, **query)
            assert [p['species'] for p in result['paths']] == [expected]
            assert result['query_complete']
        original = discover_indexed_candidates(reader, 'CCO', target='CC(=O)O', quality_view='raw')
        assert len(original['paths']) == 2


def test_budget_limited_waypoint_miss_is_inconclusive_and_horizon_is_explicit(source):
    truncated = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O',
                                           via_species=['COC', 'CC=O'], max_prefixes=1)
    assert truncated['paths'] == []
    assert truncated['reachability_status'] == 'inconclusive'
    assert not truncated['query_complete']
    row, = csv.DictReader(io.StringIO(svc.candidate_paths_csv(truncated)))
    assert row['record_type'] == 'query' and row['signature_id'] == '' and row['step'] == ''
    assert row['reachability_status'] == 'inconclusive'
    assert json.loads(row['query'])['via_species'] == ['COC', 'CC=O']
    limited = svc.search_candidate_paths(source, 'CCO', target='CC(=O)O',
                                         via_species=['COC', 'CC=O'], max_steps=2)
    assert limited['paths'] == []
    assert limited['reachability_status'] == 'not_found'
    assert limited['query_complete'] and limited['horizon_limited']
    row, = csv.DictReader(io.StringIO(svc.candidate_paths_csv(limited)))
    assert row['record_type'] == 'query' and row['reachability_status'] == 'not_found'


@pytest.mark.parametrize('query,message', [
    ({'via_species': 'COC'}, '列表'),
    ({'via_species': ['COC', 'COC']}, '重复'),
    ({'via_species': ['CCO']}, '中间物种'),
    ({'via_species': ['CC(=O)O']}, '中间物种'),
    ({'via_species': ['C2H6O']}, '精确结构'),
    ({'via_species': ['COC'], 'excluded_species': ['COC']}, '同时被排除'),
    ({'excluded_species': ['CCO']}, '同时被排除'),
    ({'excluded_species': ['CC(=O)O']}, '同时被排除'),
    ({'excluded_species': [' CO C']}, '无空白'),
    ({'via_species': ['COC'], 'mode': 'explore'}, '双端|起点到终点'),
    ({'via_species': ['COC'] * 101}, '100'),
])
def test_invalid_constraints_fail_explicitly(source, query, message):
    with pytest.raises(svc.ServiceError, match=message):
        svc.search_candidate_paths(source, 'CCO', target='CC(=O)O', **query)


def test_constrained_query_does_not_read_raw_source_or_rebuild(source, monkeypatch):
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert str(path) != source['reactionevent'], 'online raw-source read'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    def forbidden(*args, **kwargs):
        pytest.fail('online preparation')
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, 'build', forbidden)
    assert svc.search_candidate_paths(source, 'CCO', target='CC(=O)O',
        via_species=['COC'], excluded_species=['O'])['paths']


def test_cli_accepts_repeated_exact_constraints_and_exports_them(source, tmp_path, capsys):
    from scripts.rng_query_cli import main
    out = tmp_path / 'query.json'
    assert main(['candidate-search', '--source', source['reactionevent'], '--start', 'CCO',
        '--target', 'CC(=O)O', '--via-species', 'COC', '--via-species', 'CC=O',
        '--exclude-species', 'O', '--exclude-species', '[H][H]', '--out-json', str(out)]) == 0
    result = json.loads(out.read_text())
    assert result['query']['via_species'] == ['COC', 'CC=O']
    assert result['query']['excluded_species'] == ['O', '[H][H]']
    assert [p['species'] for p in result['paths']] == [['CCO', 'COC', 'CC=O', 'CC(=O)O']]
    assert main(['candidate-search', '--source', source['reactionevent'], '--start', 'CCO',
        '--target', 'CC(=O)O', '--exclude-species', 'CCO']) == 2
    assert '同时被排除' in capsys.readouterr().err
