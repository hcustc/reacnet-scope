from __future__ import annotations

import csv
import io
import json
import sqlite3

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from reacnet_scope.candidate_evidence import validate_carrier_chain

A, B, C = '[C][C]', '[C]=[C]', '[C]#[C]'


def source(tmp_path, *, real_chain=False, return_delay=1):
    """A->B->A on atoms 1/2; unrelated B->C on 3/4; optional real chain."""
    event_file = tmp_path / 'case.reactionevent.csv'
    molecules = tmp_path / 'case.molecules.csv'
    events = [(0, A, B), (return_delay, B, A), (4, B, C)]
    if real_chain:
        events += [(6, A, B), (7, B, C)]
    with event_file.open('w') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestep_Index', 'Reactant', 'Product'])
        writer.writerows(events)
    with molecules.open('w') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestep', 'Species', 'AtomIDs', 'BondIDs'])
        for frame in range(10):
            first = B if 1 <= frame <= return_delay else A
            states = [(first, 0, 1), (B if frame <= 4 else C, 2, 3)]
            if real_chain:
                states.append((A if frame <= 6 else B if frame == 7 else C, 4, 5))
            for species, x, y in states:
                order = {A: 1, B: 2, C: 3}[species]
                writer.writerow([frame * 100, species, f'{x};{y}', f'{x}-{y}-{order}'])
    EVENT_EVIDENCE_STORE.build(str(event_file), str(molecules))
    return {'reactionevent': str(event_file), 'molecules': str(molecules)}


def test_return_only_edge_folded_but_raw_evidence_and_identity_preserved(tmp_path):
    artifacts = source(tmp_path)
    folded = svc.search_candidate_paths(artifacts, A, target=C)
    raw = svc.search_candidate_paths(artifacts, A, target=C, quality_view='raw')
    assert folded['query_complete'] and folded['paths'] == []
    assert len(raw['paths']) == 1
    step = raw['paths'][0]['steps'][0]
    assert step['quality']['rapid_return_events'] == 1
    assert step['quality']['retained_events'] == 0
    page = svc.candidate_step_events(artifacts, raw, raw['paths'][0]['signature_id'], 0)
    actual = page['rows'][0]['candidate_evidence']
    assert actual['product_bonds'] == ['1-2-2']
    assert actual['history']['return_gap'] == 1
    assert actual['product_followup'][0]['observed_frame_intervals'] == 1
    assert actual['history']['return_kind'] == 'exact'
    checked = svc.check_candidate_continuity(artifacts, raw, raw['paths'][0]['signature_id'])
    assert checked['status'] == 'not_observed_within_evidence'
    assert checked['breakpoints'][0]['reason'] == 'first_consumption_differs'


def test_short_lived_genuine_chain_survives_and_has_retention_witness(tmp_path, monkeypatch):
    artifacts = source(tmp_path, real_chain=True)
    def forbidden(*args, **kwargs):
        raise AssertionError('online query attempted a build')
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, 'build', forbidden)
    report = svc.search_candidate_paths(artifacts, A, target=C)
    raw = svc.search_candidate_paths(artifacts, A, target=C, quality_view='raw')
    path = report['paths'][0]
    assert path['signature_id'] == raw['paths'][0]['signature_id']
    assert path['steps'][0]['quality']['retained_events'] == 1
    assert path['steps'][0]['transfer_event_count'] == 2
    checked = svc.check_candidate_continuity(artifacts, report, path['signature_id'])
    assert checked['status'] == 'chain_found'
    assert checked['witness']['max_continuous_anchor_set'] == [5, 6]
    assert len(checked['witness']['carrier_chain']) == 2
    path['continuous_support'] = checked
    path['continuous_md'] = checked['status']
    rows = list(csv.DictReader(io.StringIO(svc.candidate_paths_csv(report))))
    assert json.loads(rows[0]['continuous_support'])['status'] == 'chain_found'
    assert json.loads(rows[0]['quality'])['folded_events'] == 1


def test_window_is_explicit_and_can_change_result_without_changing_identity(tmp_path):
    artifacts = source(tmp_path, return_delay=2)
    one = svc.search_candidate_paths(artifacts, A, target=C, return_window_frames=1)
    two = svc.search_candidate_paths(artifacts, A, target=C, return_window_frames=2)
    assert len(one['paths']) == 1 and not two['paths']
    assert one['query']['return_window_frames'] == 1


def test_validation_budget_and_missing_followup_are_inconclusive(tmp_path):
    artifacts = source(tmp_path, real_chain=True)
    report = svc.search_candidate_paths(artifacts, A, target=C)
    result = svc.check_candidate_continuity(artifacts, report, report['paths'][0]['signature_id'], max_states=1)
    assert result['status'] == 'inconclusive'
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'], artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        con.execute('DELETE FROM continuity_links')
        result = validate_carrier_chain(con, report['paths'][0])
    assert result['status'] == 'inconclusive'


def test_cli_can_export_checked_raw_route(tmp_path, capsys):
    from scripts.rng_query_cli import main
    artifacts = source(tmp_path)
    out = tmp_path / 'out.json'
    assert main(['candidate-search', '--source', artifacts['reactionevent'], '--molecules', artifacts['molecules'],
                 '--start', A, '--target', C, '--quality-view', 'raw', '--check-top', '1', '--out-json', str(out)]) == 0
    report = json.loads(out.read_text())
    assert report['paths'][0]['continuous_md'] == 'not_observed_within_evidence'
    capsys.readouterr()


def test_old_result_rejected_after_source_revision_changes(tmp_path):
    artifacts = source(tmp_path)
    report = svc.search_candidate_paths(artifacts, A, target=C, quality_view='raw')
    report['source_revision'] = {}
    with pytest.raises(svc.ServiceError, match='来源版本'):
        svc.check_candidate_continuity(artifacts, report, report['paths'][0]['signature_id'])


def test_miso_label_cannot_hide_carrier_bond_mismatch(tmp_path):
    artifacts = source(tmp_path, real_chain=True)
    report = svc.search_candidate_paths(artifacts, A, target=C)
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'], artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        # Simulate distinct bond state at the next consumer, despite same Species.
        con.execute("UPDATE continuity_links SET to_reactant_instance_id=(SELECT instance_id FROM molecule_instances WHERE species_smiles=? LIMIT 1) WHERE from_instance_id IN (SELECT product_instance_id FROM candidate_carrier_pairs WHERE source_species=?)", (A, A))
        result = validate_carrier_chain(con, report['paths'][0])
    assert result['status'] == 'inconclusive'
    assert any(b['reason'] == 'incompatible_carrier_bonds' for b in result['breakpoints'])


def test_same_transition_cannot_create_return_evidence(tmp_path):
    from reacnet_scope.path_search import materialize_candidate_adjacency
    artifacts = source(tmp_path)
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'], artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        con.execute('UPDATE events SET timestep_index=0 WHERE timestep_index=1')
        materialize_candidate_adjacency(con)
        assert con.execute("SELECT COUNT(*) FROM candidate_event_history WHERE return_kind IN ('exact','topology')").fetchone()[0] == 0


def test_unresolved_same_transition_blocks_return_independent_of_row_order(tmp_path):
    from reacnet_scope.candidate_evidence import materialize_candidate_evidence
    artifacts = source(tmp_path)
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'], artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        con.execute("UPDATE events SET timestep_index=1,association_status='unresolved',atom_ids_json='[]' WHERE timestep_index=4")
        materialize_candidate_evidence(con)
        assert con.execute("SELECT COUNT(*) FROM candidate_event_history WHERE return_kind IN ('exact','topology')").fetchone()[0] == 0


def test_return_quality_buckets_stay_bounded_for_many_distinct_gaps(tmp_path):
    from reacnet_scope.candidate_evidence import materialize_candidate_evidence, quality_summary

    with sqlite3.connect(tmp_path / 'many-gaps.sqlite') as con:
        con.execute('''CREATE TABLE events(event_id TEXT PRIMARY KEY,timestep_index INTEGER,
            association_status TEXT,reactant_participants_json TEXT,product_participants_json TEXT,
            reactant_bonds_json TEXT,product_bonds_json TEXT,atom_ids_json TEXT)''')
        con.execute('''CREATE TABLE candidate_transfer_events(source_species TEXT,
            product_species TEXT,reaction_key TEXT,event_id TEXT,shared_atoms INTEGER)''')
        transition = 0
        for gap in range(1, 202):
            transition += gap
            event_id = f'event-{gap}'
            con.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',
                        (event_id, transition, 'matched', '[]', '[]', '[]', '[]', '[1]'))
            con.execute('INSERT INTO candidate_transfer_events VALUES(?,?,?,?,1)',
                        ('[C]', '[O]', '[C]->[O]', event_id))
        materialize_candidate_evidence(con)
        buckets = con.execute('SELECT COUNT(*) FROM candidate_quality_counts').fetchone()[0]
        assert buckets <= 102
        summary = quality_summary(con, '[C]', '[O]', '[C]->[O]', 100, 'exact')
        assert summary['evaluated_events'] == 201
        assert summary['exact_return_events'] == 99


def test_matching_endpoints_do_not_certify_unindexed_intervening_frames(tmp_path):
    artifacts = source(tmp_path, real_chain=True)
    report = svc.search_candidate_paths(artifacts, A, target=C)
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'], artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        instance = con.execute("SELECT product_instance_id FROM candidate_carrier_pairs WHERE source_species=? ORDER BY event_id DESC LIMIT 1", (A,)).fetchone()[0]
        con.execute('''INSERT INTO molecule_instances SELECT instance_id||'_gap',replicate_id,analyzed_frame+2,
            source_timestep+200,species_id,species_smiles,atom_ids_json,bonds_json,structure_key
            FROM molecule_instances WHERE instance_id=?''', (instance,))
        con.execute("UPDATE continuity_links SET to_reactant_instance_id=? WHERE from_instance_id=?", (instance+'_gap', instance))
        result = validate_carrier_chain(con, report['paths'][0])
    assert result['status'] == 'inconclusive'
    assert any(b['reason'] == 'intervening_bond_history_not_indexed' for b in result['breakpoints'])


def test_topology_return_is_not_exact_bond_return(tmp_path):
    from reacnet_scope.candidate_evidence import materialize_candidate_evidence, quality_summary
    artifacts = source(tmp_path)
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'], artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        con.execute('UPDATE events SET product_bonds_json=? WHERE timestep_index=1', ('["1-2-2"]',))
        materialize_candidate_evidence(con)
        key = A + '->' + B
        assert quality_summary(con, A, B, key, 3, 'topology')['folded_events'] == 1
        assert quality_summary(con, A, B, key, 3, 'exact')['folded_events'] == 0


def test_validation_late_result_does_not_replace_new_search(tmp_path):
    from scripts.webapp_dash.app import create_app
    from scripts.webapp_dash.candidate_workbench import context_key
    from dash import no_update
    app = create_app()
    key = next(k for k, v in app.callback_map.items() if k.startswith('cp-report.data@')
               and any(i['id'] == 'cp-validation-raw' for i in v['inputs']))
    callback = app.callback_map[key]['callback'].__wrapped__
    store = {'dataset_id': 'one', 'artifacts': {}}
    raw = dict(request_id='validation', context=context_key(store), search_request_id='old', signature='route',
               validation={'status': 'chain_found'})
    request = dict(raw)
    report = dict(query_request_id='new', paths=[{'signature_id': 'route'}])
    assert callback(raw, request, {'request_id': 'new'}, report, store) is no_update


def test_actual_event_view_renders_recorded_bond_order(tmp_path):
    from plotly.utils import PlotlyJSONEncoder
    from scripts.webapp_dash.candidate_workbench import actual_event_view

    artifacts = source(tmp_path)
    raw = svc.search_candidate_paths(artifacts, A, target=C, quality_view='raw')
    page = svc.candidate_step_events(artifacts, raw, raw['paths'][0]['signature_id'], 0)
    rendered = json.dumps(actual_event_view(page['rows'][0]), cls=PlotlyJSONEncoder,
                          ensure_ascii=False)
    assert '1–2：RNG 键级 2' in rendered
    assert '1–2：RNG 键级 1' in rendered
