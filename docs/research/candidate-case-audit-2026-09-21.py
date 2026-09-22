"""Read-only, bounded audit of the chlorophenol candidate-search case.

Run from the repository root with .venv/bin/python and --index PATH --out PATH.
No RNG rerun, index build, or production software change is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from reacnet_scope.path_search import CandidateReader, discover_indexed_candidates
from reacnet_scope.trajectory import read_lammps_frame_block

START = '[H][O][C]1=[C]([Cl])[C]([H])=[C]([H])[C]([H])=[C]1[H]'
TARGET = '[H][C]1[C]([H])[C]([O])[C]([Cl])[C]1[H]'


def stamp(path):
    s = Path(path).stat()
    return {'size': s.st_size, 'mtime_ns': str(s.st_mtime_ns)}


def decode(row):
    result = dict(row)
    for key in list(result):
        if key.endswith('_json'):
            result[key[:-5]] = json.loads(result.pop(key))
    return result


def participants_key(parts):
    return sorted((p['species'], tuple(sorted(p['atom_ids']))) for p in parts)


def connectivity(bonds):
    return sorted(tuple(map(int, b.split('-')[:2])) for b in bonds)


def native_events(handle, transition):
    """Explicit IDs, not row positions; HDF atom IDs are zero-based."""
    g = handle['transition_evidence']
    begin = int(g['block_start'][transition])
    end = begin + int(g['block_length'][transition])
    molecule_ids = handle['molecules/molecule_id'][:]
    result = []
    for row in range(begin, end):
        rid = int(g['reaction_id'][row]) - 1
        item = {'native_row_zero_based': row,
                'reaction_key': handle['reaction_types/reactant'].asstr()[rid] + '->'
                                + handle['reaction_types/product'].asstr()[rid],
                'reactant_participants': [], 'product_participants': [],
                'reactant_bonds': [], 'product_bonds': []}
        a, b = map(int, g['participant_offsets'][row:row + 2])
        for mid, side in zip(g['participant_molecule_id'][a:b], g['participant_side'][a:b]):
            j = int(np.searchsorted(molecule_ids, mid))
            assert int(molecule_ids[j]) == int(mid)
            aa, ab = map(int, handle['molecules/atom_offsets'][j:j + 2])
            ba, bb = map(int, handle['molecules/bond_offsets'][j:j + 2])
            sid = int(handle['molecules/species_id'][j]) - 1
            prefix = 'reactant' if int(side) == 0 else 'product'
            item[prefix + '_participants'].append({
                'species': handle['species/name'].asstr()[sid],
                'atom_ids': sorted(int(v) + 1 for v in handle['molecules/atom_ids'][aa:ab]),
                'native_molecule_id': int(mid)})
            item[prefix + '_bonds'].extend(
                f'{int(pair[0]) + 1}-{int(pair[1]) + 1}-{int(order)}'
                for pair, order in zip(handle['molecules/bond_atoms'][ba:bb],
                                       handle['molecules/bond_order'][ba:bb]))
        result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--index', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    index = Path(args.index).resolve()
    before = stamp(index)
    reader = CandidateReader({'index_path': str(index)})
    con = reader.connection
    con.row_factory = sqlite3.Row
    meta = dict(con.execute('SELECT key,value FROM meta'))
    source = Path(meta['reactionevent_file'])
    source_stamp = stamp(source)
    assert source_stamp == {'size': int(meta['reactionevent_size']),
                            'mtime_ns': meta['reactionevent_mtime_ns']}
    run = json.loads((source.parent / 'rng_run.json').read_text())
    baseline = discover_indexed_candidates(reader, START, target=TARGET, max_steps=6)
    wider = discover_indexed_candidates(reader, START, target=TARGET, max_steps=6,
                                        max_frontier=20000, max_seconds=30)
    assert len(baseline['paths']) == 2 and len(wider['paths']) == 6
    assert all([s['transfer_event_count'] for s in p['steps']] == [2, 77, 1]
               for p in baseline['paths'])
    events = {}

    def fetch(eid):
        if eid not in events:
            events[eid] = decode(con.execute('SELECT * FROM events WHERE event_id=?', (eid,)).fetchone())
        return events[eid]

    def support(step):
        rows = con.execute('''SELECT event_id FROM candidate_transfer_events
            WHERE source_species=? AND product_species=? AND reaction_key=? ORDER BY event_id''',
            (step['carried_from'], step['carried_to'], step['reaction_key'])).fetchall()
        return [fetch(r['event_id']) for r in rows]

    step_ids = [[r['event_id'] for r in support(s)] for s in baseline['paths'][0]['steps']]
    support(baseline['paths'][1]['steps'][-1])
    first_steps = {p['steps'][0]['reaction_key']: p['steps'][0] for p in wider['paths']}
    returns = []
    for step in first_steps.values():
        for event in support(step):
            participant = con.execute('''SELECT * FROM event_participants
                WHERE event_id=? AND side='product' AND species_smiles=?''',
                (event['event_id'], step['carried_to'])).fetchone()
            link = decode(con.execute('SELECT * FROM continuity_links WHERE from_instance_id=?',
                                      (participant['instance_id'],)).fetchone())
            following = [fetch(eid) for eid in link['next_event_ids']]
            assert len(following) == 1
            nxt = following[0]
            same_participants = participants_key(event['reactant_participants']) == participants_key(nxt['product_participants'])
            returns.append({'event_id': event['event_id'], 'transition': event['timestep_index'],
                            'next_event_id': nxt['event_id'], 'next_transition': nxt['timestep_index'],
                            'frame_gap': nxt['timestep_index'] - event['timestep_index'],
                            'same_species_and_atoms_return': same_participants,
                            'same_connectivity_return': connectivity(event['reactant_bonds']) == connectivity(nxt['product_bonds']),
                            'exact_bonds_return': set(event['reactant_bonds']) == set(nxt['product_bonds']),
                            'continuity_link': link})
    assert len(returns) == 7
    assert all(r['frame_gap'] == 1 and r['same_species_and_atoms_return'] and r['same_connectivity_return'] for r in returns)
    closures = [fetch(eid) for eid in step_ids[1]]
    groups = Counter(tuple(e['atom_ids']) for e in closures)
    first_carriers = [p['atom_ids'] for eid in step_ids[0] for p in fetch(eid)['product_participants']
                      if p['species'] == baseline['paths'][0]['species'][1]]
    assert not any(set(a) & set(b) for a in first_carriers for b in groups)
    closure_previous = []
    for event in closures:
        placeholders = ','.join('?' for _ in event['atom_ids'])
        previous_rows = con.execute(f'''SELECT DISTINCT e.* FROM event_atoms a JOIN events e USING(event_id)
            WHERE a.atom_id IN ({placeholders}) AND e.timestep_index<?
            ORDER BY e.timestep_index DESC LIMIT 2''',
            (*event['atom_ids'], event['timestep_index'])).fetchall()
        assert len(previous_rows) < 2 or previous_rows[0]['timestep_index'] != previous_rows[1]['timestep_index'], event['event_id']
        prev = previous_rows[0] if previous_rows else None
        if prev:
            prev = decode(prev)
            fetch(prev['event_id'])
            closure_previous.append({'closure_event_id': event['event_id'],
                                     'previous_event_id': prev['event_id'],
                                     'previous_reaction': prev['reaction_key'],
                                     'frame_gap': event['timestep_index'] - prev['timestep_index'],
                                     'ring_connectivity_return': connectivity(prev['reactant_bonds']) == connectivity(event['product_bonds']),
                                     'ring_exact_bonds_return': set(prev['reactant_bonds']) == set(event['product_bonds']),
                                     'is_same_atom_set_reverse':
                                         participants_key(prev['reactant_participants']) == participants_key(event['product_participants'])
                                         and participants_key(prev['product_participants']) == participants_key(event['reactant_participants'])})
    validation = []
    with h5py.File(source, 'r') as handle:
        for event in list(events.values()):
            # source_row belongs to Scope normalization; it is NOT a native HDF row ID.
            matches = [n for n in native_events(handle, event['timestep_index'])
                       if n['reaction_key'] == event['reaction_key']
                       and all(participants_key(n[s + '_participants']) == participants_key(event[s + '_participants'])
                               and set(n[s + '_bonds']) == set(event[s + '_bonds']) for s in ('reactant', 'product'))]
            assert len(matches) == 1, event['event_id']
            validation.append({'event_id': event['event_id'], 'matched': True,
                               'native_row_zero_based': matches[0]['native_row_zero_based']})
        sample_timesteps = [int(handle['frames/timestep'][i]) for i in (10744, 10745, 10746)]
    geometry = []
    md = source.parent.parent / 'md'
    trajectory = md / 'trajectory.lammpstrj'
    trajectory_index = md / '.reacnet-scope/datasets/004640dd9b1e4d93b539/trajectory.sqlite3'
    tc = sqlite3.connect(trajectory_index.as_uri() + '?mode=ro', uri=True)
    tm = dict(tc.execute('SELECT key,value FROM meta'))
    assert stamp(trajectory) == {'size': int(tm['source_size']), 'mtime_ns': tm['source_mtime_ns']}
    assert 'units metal' in (md / 'input.lammps').read_text()
    from ase.geometry import find_mic
    for transition, pairs in [(10744, [(898, 903), (902, 903)]), (130119, [(534, 539), (538, 539)])]:
        for frame in range(transition - 1, transition + 4):
            a, b = tc.execute('SELECT byte_start,byte_end FROM frames WHERE timestep=?', (frame * 100,)).fetchone()
            with trajectory.open('rb') as stream:
                stream.seek(a)
                data = read_lammps_frame_block(stream.read(b - a), type_element_map={'1': 'C', '2': 'Cl', '3': 'H', '4': 'O'})
            for left, right in pairs:
                xyz = lambda atom: np.array([data['atoms'][atom][axis] for axis in ('x', 'y', 'z')])
                distance = float(find_mic(xyz(left) - xyz(right), data['cell'], data['pbc'])[1])
                geometry.append({'case_transition': transition, 'analyzed_frame': frame,
                                 'timestep': frame * 100, 'atom_ids': [left, right], 'distance_angstrom': distance})
    tc.close()
    reader.close()
    assert stamp(index) == before and stamp(source) == source_stamp
    result = {'index': str(index), 'index_stamp': before, 'source': str(source),
              'source_stamp': source_stamp, 'meta': meta, 'run_metadata': run,
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'baseline': {k: v for k, v in baseline.items() if k != 'cycle_closures'},
              'wider_three_step_paths': wider['paths'], 'first_step_returns': returns,
              'closure_groups': [{'atom_ids': list(atoms), 'event_count': count} for atoms, count in groups.items()],
              'closure_previous_events': closure_previous, 'first_step_carriers': first_carriers,
              'all_first_carriers_disjoint_from_closure_carriers': True,
              'raw_hdf5_validation': validation, 'events': events,
              'sample_timesteps': sample_timesteps, 'sample_interval_fs': (sample_timesteps[1] - sample_timesteps[0]) * run['timestep_ps'] * 1000,
              'geometry': geometry, 'geometry_units_source': str(md / 'input.lammps'),
              'source_and_index_unchanged': True}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'baseline_paths': len(baseline['paths']), 'raw_events_verified': len(validation),
                      'first_step_one_frame_returns': len(returns), 'closure_groups': [n for n in groups.values()],
                      'closure_preceded_by_same_atom_reverse': sum(r['is_same_atom_set_reverse'] for r in closure_previous),
                      'output': args.out}, ensure_ascii=False))


if __name__ == '__main__':
    main()
