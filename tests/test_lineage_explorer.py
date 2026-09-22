from __future__ import annotations

import json
import sqlite3

import pytest

from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from tests.test_molecule_lineage import _write_lineage_fixture


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path / 'cache'))
    events, molecules, root = _write_lineage_fixture(tmp_path)
    return {'reactionevent': str(events), 'molecules': str(molecules)}, root


def test_split_merge_recurrence_and_paths_keep_all_atoms(case, monkeypatch):
    artifacts, event = case
    report = svc.start_lineage_explorer(artifacts, event_id=event)
    root = report['root']['segment_id']
    assert report['segments'][root]['atom_ids'] == [1, 2]
    def forbidden(*a, **k):
        raise AssertionError('online scan/build forbidden')
    monkeypatch.setattr(EVENT_EVIDENCE_STORE, 'build', forbidden)
    import reacnet_scope.lineage_segments as module
    monkeypatch.setattr(module, 'iter_csv_molecular_frames', forbidden)
    report = svc.expand_lineage_explorer(artifacts, report, segment_id=root)
    first = report['occurrences'][event]
    assert first['split'] and len(first['output_segments']) == 2
    assert {tuple(report['segments'][s]['atom_ids']) for s in first['output_segments']} == {(1,), (2,)}
    for _ in range(4):
        report = svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    assert len(report['occurrences']) == 4
    merges = [e for e in report['occurrences'].values() if e['merge']]
    assert len(merges) == 2
    repeated = [s for s in report['segments'].values() if s['species'] == '[C][H]']
    assert len(repeated) == 2 and repeated[0]['segment_id'] != repeated[1]['segment_id']
    target = next(s['segment_id'] for s in report['segments'].values() if s['species'] == '[C][O]')
    paths = svc.observed_lineage_paths(artifacts, report, target)
    assert len(paths['paths']) == 1
    assert len(paths['paths'][0]['events']) == 4
    assert paths['paths'][0]['retained_atoms'] == [1]
    assert paths['paths'][0]['lost_atoms'] == [2]
    assert not paths['truncated']
    merged = next(e for e in merges if e['transition_index'] == 2)
    oxygen = next(report['segments'][s] for s in merged['input_segments'] if report['segments'][s]['species'] == '[O]')
    assert (oxygen['start_frame'], oxygen['end_frame']) == (0, 2)
    ref = svc.lineage_frame_reference(artifacts, report, oxygen['segment_id'], 1)
    assert ref['timestep'] == 10 and ref['atom_ids'] == [3]


def test_nonadjacent_events_are_connected_only_by_full_segment(tmp_path, monkeypatch):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path/'cache'))
    event = tmp_path/'e.reactionevent.csv'
    mol = tmp_path/'e.molecules.csv'
    # Use same atoms in each state; long middle interval is fully recorded.
    event.write_text('Timestep_Index,Reactant,Product\n0,[C][O],[C]=[O]\n4,[C]=[O],[C]#[O]\n')
    mol.write_text('Timestep,Species,AtomIDs,BondIDs\n0,[C][O],0;1,0-1-1\n' +
                   ''.join(f'{f*10},[C]=[O],0;1,0-1-2\n' for f in range(1,5)) +
                   '50,[C]#[O],0;1,0-1-3\n')
    EVENT_EVIDENCE_STORE.build(str(event), str(mol))
    artifacts = {'reactionevent': str(event), 'molecules': str(mol)}
    row = EVENT_EVIDENCE_STORE.query_events(str(event), str(mol), '[C][O]->[C]=[O]', limit=1)['rows'][0]
    report = svc.start_lineage_explorer(artifacts, event_id=row['event_id'])
    report = svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    middle = next(s for s in report['segments'].values() if s['species']=='[C]=[O]')
    assert (middle['start_frame'],middle['end_frame']) == (1,4)
    report = svc.expand_lineage_explorer(artifacts, report, segment_id=middle['segment_id'])
    target = next(s['segment_id'] for s in report['segments'].values() if s['species']=='[C]#[O]')
    assert len(svc.observed_lineage_paths(artifacts, report, target)['paths']) == 1


def test_segment_preparation_merges_ranges_across_blocks_and_splits_sources(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import numpy as np
    import zlib
    import reacnet_scope.lineage_segments as segments

    monkeypatch.setattr(segments, 'FRAME_BLOCK', 8)
    row = SimpleNamespace(species='[C]', atom_ids=[0], bond_ids=[])

    class Native:
        def iter_frame_references(self):
            return ((frame, frame * 10, 1 if frame < 5 else 2, frame)
                    for frame in range(40))

        def iter_molecular_state_blocks(self):
            yield np.array([0, 8]), np.array([2, 10]), row
            yield np.array([1, 4]), np.array([5, 6]), row
            yield np.array([32]), np.array([34]), row

    with sqlite3.connect(tmp_path / 'segments.sqlite') as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('''CREATE TABLE molecule_instances(instance_id TEXT,analyzed_frame INTEGER,
            species_smiles TEXT,atom_ids_json TEXT,bonds_json TEXT)''')
        con.execute('''CREATE TABLE event_participants(event_id TEXT,side TEXT,
            participant_index INTEGER,instance_id TEXT)''')
        con.execute('CREATE TABLE events(event_id TEXT,timestep_index INTEGER,association_status TEXT)')
        con.executemany('INSERT INTO molecule_instances VALUES(?,?,?,?,?)',
                        [(f'instance-{frame}', frame, '[C]', '[1]', '[]') for frame in (2, 5, 9, 32)])
        segments.materialize_segments(con, replicate_id='fixture', native=Native())
        rows = con.execute('''SELECT m.instance_id,s.start_frame,s.end_frame
            FROM lineage_instance_segments m JOIN lineage_segments s USING(segment_id)
            ORDER BY m.instance_id''').fetchall()
        assert rows == [('instance-2', 0, 4), ('instance-32', 32, 34),
                        ('instance-5', 5, 6), ('instance-9', 8, 10)]
        assert con.execute('SELECT segment_count FROM lineage_state_occupancy').fetchone()[0] == 4
        chunks = con.execute('SELECT block_start,packed_frames FROM lineage_occupancy_chunks ORDER BY block_start').fetchall()
        assert [start for start, _ in chunks] == [0, 8, 32]
        assert b''.join(zlib.decompress(chunk) for _, chunk in chunks) == bytes((0b01111111, 0b00000111, 0b00000111))


def test_segment_preparation_skips_empty_blocks_after_boundary_end(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import numpy as np
    import reacnet_scope.lineage_segments as segments

    monkeypatch.setattr(segments, 'FRAME_BLOCK', 8)
    row = SimpleNamespace(species='[C]', atom_ids=[0], bond_ids=[])

    class Native:
        def iter_frame_references(self):
            return ((frame, frame, 1, frame) for frame in range(40))

        def iter_molecular_state_blocks(self):
            yield np.array([0, 32]), np.array([7, 34]), row

    with sqlite3.connect(tmp_path / 'sparse-segments.sqlite') as con:
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        con.execute('''CREATE TABLE molecule_instances(instance_id TEXT,analyzed_frame INTEGER,
            species_smiles TEXT,atom_ids_json TEXT,bonds_json TEXT)''')
        con.execute('''CREATE TABLE event_participants(event_id TEXT,side TEXT,
            participant_index INTEGER,instance_id TEXT)''')
        con.execute('CREATE TABLE events(event_id TEXT,timestep_index INTEGER,association_status TEXT)')
        con.execute("INSERT INTO molecule_instances VALUES('later',33,'[C]','[1]','[]')")
        segments.materialize_segments(con, replicate_id='sparse', native=Native())
        assert con.execute('''SELECT start_frame,end_frame FROM lineage_segments''').fetchall() == [(32, 34)]
        assert con.execute('SELECT segment_count FROM lineage_state_occupancy').fetchone()[0] == 2
        assert [row[0] for row in con.execute('SELECT block_start FROM lineage_occupancy_chunks ORDER BY block_start')] == [0, 32]


def test_stale_revision_and_missing_segment_index(case):
    artifacts, event = case
    report = svc.start_lineage_explorer(artifacts, event_id=event)
    opened = EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'],artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        con.execute("DELETE FROM meta WHERE key='lineage_segments_version'")
    with pytest.raises(svc.ServiceError, match='变化'):
        svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    assert not svc.lineage_explorer_status(artifacts)['available']


def test_observed_path_does_not_trust_browser_edges(case):
    artifacts, event = case
    report = svc.start_lineage_explorer(artifacts, event_id=event)
    report = svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    target = next(iter(report['occurrences'].values()))['output_segments'][0]
    report['occurrences'][event]['provenance'] = []
    assert svc.observed_lineage_paths(artifacts, report, target)['paths']


def test_native_segment_adapter_and_online_no_hdf5(tmp_path, monkeypatch):
    import h5py
    from tests.test_timed_evidence import write_timeline
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path/'cache'))
    path = write_timeline(tmp_path/'test.timeline.h5', schema_version='2')
    EVENT_EVIDENCE_STORE.build(str(path))
    row = EVENT_EVIDENCE_STORE.query_events(str(path), '', '[C]+[O]->[C][O]', limit=1)['rows'][0]
    def forbidden(*a, **k):
        raise AssertionError('online opened HDF5')
    monkeypatch.setattr(h5py,'File',forbidden)
    report = svc.start_lineage_explorer({'timeline':str(path)},event_id=row['event_id'])
    report = svc.expand_lineage_explorer({'timeline':str(path)},report,all_branches=True)
    assert len(report['segments']) == 3
    assert next(iter(report['occurrences'].values()))['merge']


@pytest.mark.parametrize('middle', ['[C]=[O],0;1,0-1-1', '[N],2,'])
def test_hidden_state_change_or_absence_ends_segment(tmp_path, monkeypatch, middle):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path/'cache'))
    event, mol = tmp_path/'x.reactionevent.csv', tmp_path/'x.molecules.csv'
    event.write_text('Timestep_Index,Reactant,Product\n0,[C][O],[C]=[O]\n4,[C]=[O],[C]#[O]\n')
    mol.write_text('Timestep,Species,AtomIDs,BondIDs\n0,[C][O],0;1,0-1-1\n'
                   '10,[C]=[O],0;1,0-1-2\n20,'+middle+'\n'
                   '30,[C]=[O],0;1,0-1-2\n40,[C]=[O],0;1,0-1-2\n50,[C]#[O],0;1,0-1-3\n')
    EVENT_EVIDENCE_STORE.build(str(event),str(mol))
    artifacts={'reactionevent':str(event),'molecules':str(mol)}
    row=EVENT_EVIDENCE_STORE.query_events(str(event),str(mol),'[C][O]->[C]=[O]',limit=1)['rows'][0]
    report=svc.start_lineage_explorer(artifacts,event_id=row['event_id'],side='product')
    segment=report['segments'][report['root']['segment_id']]
    assert (segment['start_frame'],segment['end_frame'])==(1,1)
    assert segment['next']['status']=='stop'
    report=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    assert report['occurrences']=={}


def test_remerge_preserves_both_histories_without_combining_paths(case):
    artifacts,event=case
    report=svc.start_lineage_explorer(artifacts,event_id=event)
    report=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    report=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    target=next(s for s in report['segments'].values() if s['species']=='[C][H]' and s['start_frame']==2)
    assert target['retained_atoms']==[1,2]
    paths=svc.observed_lineage_paths(artifacts,report,target['segment_id'])
    assert {tuple(p['retained_atoms']) for p in paths['paths']}=={(1,),(2,)}
    assert all(len(p['events'])==2 for p in paths['paths'])


def test_lineage_graph_budget_never_publishes_half_split(case, monkeypatch):
    import reacnet_scope.lineage_explorer as module
    artifacts,event=case
    report=svc.start_lineage_explorer(artifacts,event_id=event)
    monkeypatch.setattr(module,'MAX_SEGMENTS',2)
    limited=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    assert limited['occurrences']=={} and len(limited['segments'])==1
    assert limited['last_action']['status']=='graph_budget'


def test_explorer_callbacks_keep_revision_and_request_identity(case):
    from scripts.webapp_dash.app import create_app
    from scripts.webapp_dash.candidate_workbench import context_key
    from scripts.webapp_dash.lineage_explorer import graph_elements, accept_payload, detail_view
    artifacts,event=case
    app=create_app()
    assert app.server.test_client().get('/_dash-layout').status_code==200
    assert app.server.test_client().get('/_dash-dependencies').status_code==200
    store={'dataset_id':'fixture','artifacts':artifacts}
    request={'request_id':'one','context':context_key(store),'artifacts':artifacts,'action':'start',
             'report':None,'focus':{},'instance':'reactant:0','selected':{'row':{'event_id':event}}}
    run=app.callback_map['lx-raw.data']['callback'].__wrapped__
    raw=run(request)
    assert 'error' not in raw
    assert accept_payload(raw,request,store)
    assert not accept_payload(raw,dict(request,request_id='two'),store)
    assert not accept_payload(raw,request,dict(store,dataset_id='different'))
    commit=app.callback_map['lx-state.data']['callback'].__wrapped__
    state=commit(raw,request,store,None)
    request.update(action='next',request_id='two',report=state['report'],focus=raw['focus'])
    second=run(request)
    state=commit(second,request,store,state)
    assert len(state['report']['segments'])==3
    assert len(graph_elements(state['report']))==7  # 3 segments + event + 3 ports
    assert detail_view(state['report'],state['focus'])[0]
    request.update(action='event',report=state['report'],focus=state['focus'])
    assert run(request)['occurrence']['event_id']==event
    assert commit(second,None,store,state) is None


def test_original_frame_is_exact_and_read_only(case,tmp_path):
    from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
    artifacts,event=case
    trajectory=tmp_path/'trajectory.lammpstrj'
    trajectory.write_text('ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n3\n'
                          'ITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\n'
                          'ITEM: ATOMS id element x y z\n1 C 1 1 1\n2 H 2 1 1\n3 O 3 1 1\n')
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    artifacts=dict(artifacts,trajectory=str(trajectory))
    report=svc.start_lineage_explorer(artifacts,event_id=event)
    frame=svc.lineage_frame_data(artifacts,report,report['root']['segment_id'],0)
    assert [a['id'] for a in frame['atoms']]==[1,2]
    assert frame['raw_text']==trajectory.read_text()
    report=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    target=next(s for s in report['segments'].values() if s['species']=='[H]')
    with pytest.raises(svc.ServiceError,match='精确 timestep'):
        svc.lineage_frame_data(artifacts,report,target['segment_id'],1)


def test_native_ranges_coalesce_across_chunks_without_crossing_gaps(tmp_path,monkeypatch):
    from tests.test_timed_evidence import write_membership_timeline
    from reacnet_scope.timed_evidence import NativeHdf5EvidenceAdapter
    import reacnet_scope.timed_evidence as module
    selection=write_membership_timeline(tmp_path/'ranges.timeline.h5', frame_count=9,
        molecule_atoms=[[0]], ranges=[(1,0,0),(1,1,1),(1,2,2),(1,4,4),(1,5,5),(1,6,6),(1,8,8)])
    monkeypatch.setattr(module,'_MEMBERSHIP_RANGE_CHUNK_SIZE',4)
    with NativeHdf5EvidenceAdapter(selection) as adapter:
        spans=list(adapter.iter_molecular_state_spans())
    assert [(a,b) for a,b,_ in spans]==[(0,2),(4,4),(5,6),(8,8)]


def test_same_transition_duplicate_participation_cannot_order_events(case):
    from reacnet_scope.lineage_segments import materialize_segments
    artifacts,event=case
    opened=EVENT_EVIDENCE_STORE.open_required(artifacts['reactionevent'],artifacts['molecules'])
    with sqlite3.connect(opened['index_path']) as con:
        # A second port mapping to the same before/after instances must remain
        # ambiguous; never invent a sequential ordering inside this transition.
        row=con.execute('SELECT * FROM events WHERE event_id=?',(event,)).fetchone()
        columns=[r[1] for r in con.execute('PRAGMA table_info(events)')]
        values=list(row); values[columns.index('event_id')]='duplicate'
        con.execute('INSERT INTO events VALUES('+','.join('?' for _ in values)+')',values)
        con.execute("INSERT INTO event_participants SELECT 'duplicate',side,participant_index,instance_id,timestep_index,species_id,species_smiles,structure_key FROM event_participants WHERE event_id=?",(event,))
        materialize_segments(con,replicate_id='fixture',molecules_file=artifacts['molecules'])
    report=svc.start_lineage_explorer(artifacts,event_id=event)
    assert report['segments'][report['root']['segment_id']]['next']['status']=='stop'
    assert svc.expand_lineage_explorer(artifacts,report,all_branches=True)['occurrences']=={}


@pytest.fixture
def chlorine_case(tmp_path, monkeypatch):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path/'cache'))
    events, molecules, _ = _write_lineage_fixture(tmp_path)
    for file in [events, molecules]:
        file.write_text(file.read_text().replace('[H]', '[Cl]'))
    EVENT_EVIDENCE_STORE.build(str(events), str(molecules))
    root = EVENT_EVIDENCE_STORE.query_events(str(events), str(molecules), '[C][Cl]->[C]+[Cl]', limit=1)['rows'][0]['event_id']
    return {'reactionevent':str(events), 'molecules':str(molecules)}, root


def test_carbon_anchor_keeps_merge_stoichiometry_and_original_chlorine(chlorine_case):
    from scripts.webapp_dash.lineage_explorer import graph_elements, path_details
    artifacts, event = chlorine_case
    report = svc.start_lineage_explorer(artifacts, event_id=event, anchor_mode='elements',
        anchor_elements=['C'], atom_elements={1:'C', 2:'Cl', 3:'O'})
    assert report['anchor_atom_ids'] == [1]
    for _ in range(4):
        report = svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    target = next(s['segment_id'] for s in report['segments'].values() if s['species']=='[C][O]')
    path, = svc.observed_lineage_paths(artifacts, report, target)['paths']
    assert path['all_anchors_continuous']
    assert path['steps'][0]['chlorine']['departed_ids'] == [2]
    step = path['steps'][1]
    assert step['chlorine']['original_ids_returned'] == [2]
    assert step['return_provenance'][0]['status'] == 'observed'
    assert len(step['entry_sources']) == 1
    assert path['return_episodes'][0]['analyzed_frame_intervals'] == 1
    assert path['return_episodes'][0]['basis'] == 'exact_bonds'
    assert path['steps'][2]['reaction_stoichiometry']['reactants'] == [
        {'species':'[C][Cl]', 'coefficient':1}, {'species':'[O]', 'coefficient':1}]
    nodes = [e['data']['id'] for e in graph_elements(report, path) if 'source' not in e['data']]
    assert set(path['steps'][2]['input_segments']) <= set(nodes)
    assert path_details(path)
    # Export re-reads complete occurrence facts instead of browser annotations.
    report['occurrences'][event]['reaction_type'] = 'invented'
    exported = svc.export_lineage_explorer(artifacts, report, target_segment=target)
    assert exported['lineage']['occurrences'][event]['reaction_type'] != 'invented'
    assert exported['observed_paths']['paths'] == [path]


def test_anchor_missing_mapping_explicit_ids_and_policy_validation(case):
    artifacts, event = case
    with pytest.raises(svc.ServiceError, match='元素映射'):
        svc.start_lineage_explorer(artifacts, event_id=event, anchor_mode='elements', anchor_elements=['C'])
    report = svc.start_lineage_explorer(artifacts, event_id=event, anchor_mode='atom_ids', anchor_atom_ids=[1])
    assert report['element_evidence']['source'] == 'unavailable'
    report['anchor_atom_ids'] = [2]
    with pytest.raises(svc.ServiceError, match='锚点'):
        svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    with pytest.raises(svc.ServiceError, match='起始具体分子'):
        svc.start_lineage_explorer(artifacts, event_id=event, anchor_mode='atom_ids', anchor_atom_ids=[3])


def test_return_does_not_restore_anchor_continuity(case):
    artifacts, event = case
    report = svc.start_lineage_explorer(artifacts, event_id=event)
    for _ in range(2):
        report = svc.expand_lineage_explorer(artifacts, report, all_branches=True)
    target = next(s['segment_id'] for s in report['segments'].values() if s['start_frame']==2)
    paths = svc.observed_lineage_paths(artifacts, report, target)['paths']
    assert len(paths)==2
    assert all(p['root_atom_ids_present_at_target']==[1,2] and not p['all_anchors_continuous'] for p in paths)
    assert all(p['steps'][-1]['chlorine']['mapping_status']=='unavailable' for p in paths)


def test_o2_addition_preserves_anchor_and_repeated_species_stoichiometry(tmp_path, monkeypatch):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path/'cache'))
    events, mol = tmp_path/'o.reactionevent.csv', tmp_path/'o.molecules.csv'
    events.write_text('Timestep_Index,Reactant,Product\n0,[C][Cl]+[O][O],[Cl][C][O][O]\n1,[Cl][C][O][O],[C][Cl]+[O]+[O]\n')
    mol.write_text('Timestep,Species,AtomIDs,BondIDs\n0,[C][Cl],0;1,0-1-1\n0,[O][O],2;3,2-3-1\n10,[Cl][C][O][O],0;1;2;3,0-1-1;0-2-1;2-3-1\n20,[C][Cl],0;1,0-1-1\n20,[O],2,\n20,[O],3,\n')
    EVENT_EVIDENCE_STORE.build(str(events),str(mol))
    event=EVENT_EVIDENCE_STORE.query_events(str(events),str(mol),'[C][Cl]+[O][O]->[Cl][C][O][O]',limit=1)['rows'][0]['event_id']
    artifacts={'reactionevent':str(events),'molecules':str(mol)}
    report=svc.start_lineage_explorer(artifacts,event_id=event,anchor_mode='heavy_atoms',atom_elements={1:'C',2:'Cl',3:'O',4:'O'})
    for _ in range(2):
        report=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    target=next(s['segment_id'] for s in report['segments'].values() if s['start_frame']==2 and s['species']=='[C][Cl]')
    path,=svc.observed_lineage_paths(artifacts,report,target)['paths']
    assert path['all_anchors_continuous']
    assert path['steps'][0]['external_atom_ids_added']==[3,4]
    assert path['steps'][1]['reaction_stoichiometry']['products']==[{'species':'[C][Cl]','coefficient':1},{'species':'[O]','coefficient':2}]


def test_multistep_return_external_cl_and_unexpanded_side_branch():
    from reacnet_scope.lineage_provenance import annotate_path
    # A concrete carbon projection; the original Cl leaves and returns later.
    # The side branch is deliberately absent from the expanded graph.
    atoms=[[1,2],[1],[1,3],[1,2],[1,4]]
    segments={str(i):{'species':s,'atom_ids':a,'bonds':b,'start_frame':f} for i,(s,a,b,f) in enumerate(zip(
        ['[C][Cl]','[C]','[C][O]','[C][Cl]','[C][Cl]'],atoms,
        [['1-2-1'],[],['1-3-1'],['1-2-1'],['1-4-1']],[0,101,103,107,110]))}
    events={str(i):{'event_id':str(i),'transition_index':f,'reaction_type':'full reaction',
        'reaction_stoichiometry':{},'input_segments':[str(i)],'output_segments':[str(i+1)]}
        for i,f in enumerate([100,102,106,109])}
    report={'root_atom_ids':[1,2],'anchor_atom_ids':[1],'anchor_policy':{'mode':'atom_ids','atom_ids':[1]},
        'atom_elements':{1:'C',2:'Cl',3:'O',4:'Cl'},'root':{'analyzed_frame':0}}
    path=annotate_path({'segments':list(segments),'events':list(events)},report,segments,events,{})
    assert path['return_episodes']==[{'from_segment':'0','to_segment':'3','from_position':0,'to_position':3,
        'basis':'exact_bonds','events':['0','1','2'],'analyzed_frame_intervals':6}]
    assert path['steps'][2]['return_provenance'][0]['status']=='not_resolved_in_expanded_graph'
    assert path['steps'][2]['chlorine']['original_ids_returned']==[2]
    assert path['steps'][3]['chlorine']['external_ids_added']==[4]
    assert path['steps'][3]['chlorine']['original_ids_returned']==[]


def test_returning_cl_requires_expanded_side_history(tmp_path, monkeypatch):
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path/'cache'))
    events, mol = tmp_path/'branch.reactionevent.csv', tmp_path/'branch.molecules.csv'
    events.write_text('Timestep_Index,Reactant,Product\n0,[C][Cl],[C]+[Cl]\n1,[Cl]+[O],[Cl][O]\n2,[C]+[Cl][O],[C][Cl]+[O]\n')
    mol.write_text('Timestep,Species,AtomIDs,BondIDs\n0,[C][Cl],0;1,0-1-1\n0,[O],2,\n10,[C],0,\n10,[Cl],1,\n10,[O],2,\n20,[C],0,\n20,[Cl][O],1;2,1-2-1\n30,[C][Cl],0;1,0-1-1\n30,[O],2,\n')
    EVENT_EVIDENCE_STORE.build(str(events),str(mol))
    event = EVENT_EVIDENCE_STORE.query_events(str(events),str(mol),'[C][Cl]->[C]+[Cl]',limit=1)['rows'][0]['event_id']
    artifacts={'reactionevent':str(events),'molecules':str(mol)}
    report=svc.start_lineage_explorer(artifacts,event_id=event,anchor_mode='elements',anchor_elements=['C'],atom_elements={1:'C',2:'Cl',3:'O'})
    report=svc.expand_lineage_explorer(artifacts,report,segment_id=report['root']['segment_id'])
    carbon=next(s['segment_id'] for s in report['segments'].values() if s['species']=='[C]')
    report=svc.expand_lineage_explorer(artifacts,report,segment_id=carbon)
    target=next(s['segment_id'] for s in report['segments'].values() if s['start_frame']==3 and s['species']=='[C][Cl]')
    path,=svc.observed_lineage_paths(artifacts,report,target)['paths']
    assert path['steps'][-1]['returned_root_atom_ids']==[2]
    assert path['steps'][-1]['return_provenance'][0]['status']=='not_resolved_in_expanded_graph'
    report=svc.expand_lineage_explorer(artifacts,report,all_branches=True)
    path,=svc.observed_lineage_paths(artifacts,report,target)['paths']
    assert len(path['events'])==2
    trace=path['steps'][-1]['return_provenance'][0]
    assert trace['status']=='observed' and len(trace['events'])==3


def test_original_frame_supplies_elements_and_binds_revision(case,tmp_path):
    from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE
    artifacts,event=case
    trajectory=tmp_path/'elements.lammpstrj'
    trajectory.write_text('ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n3\nITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\nITEM: ATOMS id element x y z\n1 C 1 1 1\n2 H 2 1 1\n3 O 3 1 1\n')
    TRAJECTORY_INDEX_STORE.build(str(trajectory))
    artifacts=dict(artifacts,trajectory=str(trajectory))
    report=svc.start_lineage_explorer(artifacts,event_id=event,anchor_mode='elements',anchor_elements=['C'])
    assert report['anchor_atom_ids']==[1]
    assert report['atom_elements']=={'1':'C','2':'H','3':'O'}
    assert report['element_evidence']['source']=='exact_original_frame'
    trajectory.write_text(trajectory.read_text().replace('1 C','1 N'))
    with pytest.raises(svc.ServiceError,match='变化'):
        svc.export_lineage_explorer(artifacts,report)


def test_missing_original_trajectory_does_not_block_atom_id_lineage(case, tmp_path):
    artifacts,event=case
    report=svc.start_lineage_explorer(dict(artifacts,trajectory=str(tmp_path/'absent.lammpstrj')),event_id=event)
    assert report['anchor_atom_ids']==[1,2]
    assert report['element_evidence']['source']=='unavailable'
    assert report['element_evidence']['message']
