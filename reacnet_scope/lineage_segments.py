"""Offline exact continuity segments and occurrence ports for the Explorer.

The versioned substrate is additive to the existing event index. All writes
occur in its unpublished staging transaction; readers never build this data.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import zlib
from itertools import groupby
from pathlib import Path

import numpy as np
from .timed_evidence import iter_csv_molecular_frames, _merge_inclusive_ranges

VERSION = '2'
FRAME_BLOCK = 262144


def _segment_bounds(packed, frame, lower, upper):
    """Find the exact occupied run around one occurrence frame in bounded blocks."""
    cursor = frame
    while cursor > lower:
        start = max(lower, cursor - FRAME_BLOCK)
        bits = np.unpackbits(packed[start // 8:(cursor + 7) // 8], bitorder='little')
        absent = np.flatnonzero(bits[start % 8:start % 8 + cursor - start] == 0)
        if absent.size:
            lower = start + int(absent[-1]) + 1
            break
        cursor = start
    cursor = frame + 1
    while cursor < upper:
        stop = min(upper, cursor + FRAME_BLOCK)
        bits = np.unpackbits(packed[cursor // 8:(stop + 7) // 8], bitorder='little')
        absent = np.flatnonzero(bits[cursor % 8:cursor % 8 + stop - cursor] == 0)
        if absent.size:
            upper = cursor + int(absent[0])
            break
        cursor = stop
    return lower, upper - 1


def state_record(species, atoms, bonds):
    atoms = sorted(set(map(int, atoms)))
    parsed = set()
    for bond in bonds:
        a, b, order = str(bond).split('-', 2)
        a, b = sorted((int(a), int(b)))
        if a not in atoms or b not in atoms:
            raise ValueError('Molecular bond endpoint is outside its atom set')
        parsed.add((a, b, order))
    bonds = [f'{a}-{b}-{order}' for a, b, order in sorted(parsed)]
    payload = json.dumps([species, atoms, bonds], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest(), atoms, bonds


def _source_state(row):
    # Both supported adapters use zero-based source Atom IDs.
    bonds = []
    for value in row.bond_ids:
        a, b, order = value.split('-', 2)
        bonds.append(f'{int(a)+1}-{int(b)+1}-{order}')
    return state_record(row.species, [a + 1 for a in row.atom_ids], bonds)


def materialize_segments(connection, *, replicate_id, native=None, molecules_file='', progress=None):
    if native is None and not molecules_file:
        return
    connection.executescript('''
        DROP TABLE IF EXISTS lineage_segment_ports;
        DROP TABLE IF EXISTS lineage_instance_segments;
        DROP TABLE IF EXISTS lineage_occurrences;
        DROP TABLE IF EXISTS lineage_segment_atoms;
        DROP TABLE IF EXISTS lineage_segments;
        DROP TABLE IF EXISTS lineage_states;
        DROP TABLE IF EXISTS lineage_state_occupancy;
        DROP TABLE IF EXISTS lineage_frames;
        CREATE TABLE lineage_frames(frame INTEGER PRIMARY KEY,timestep INTEGER,source_id INTEGER,source_frame INTEGER);
        CREATE TABLE lineage_states(state_key TEXT PRIMARY KEY,species TEXT,atoms TEXT,bonds TEXT);
        CREATE TABLE lineage_state_occupancy(state_key TEXT PRIMARY KEY,frame_count INTEGER,
            segment_count INTEGER);
        DROP TABLE IF EXISTS lineage_occupancy_chunks;
        CREATE TABLE lineage_occupancy_chunks(state_key TEXT,block_start INTEGER,packed_frames BLOB,
            PRIMARY KEY(state_key,block_start));
        CREATE TABLE lineage_segments(segment_id TEXT PRIMARY KEY,state_key TEXT,start_frame INTEGER,end_frame INTEGER,
            conflict INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX lineage_segments_state ON lineage_segments(state_key,start_frame,end_frame);
        CREATE TABLE lineage_segment_atoms(atom_id INTEGER,segment_id TEXT,start_frame INTEGER,end_frame INTEGER);
        CREATE INDEX lineage_segment_atoms_order ON lineage_segment_atoms(atom_id,start_frame,end_frame);
        CREATE INDEX lineage_segment_atoms_by_segment ON lineage_segment_atoms(segment_id,atom_id);
        CREATE TABLE lineage_instance_segments(instance_id TEXT PRIMARY KEY,segment_id TEXT);
        CREATE TABLE lineage_segment_ports(event_id TEXT,side TEXT,participant_index INTEGER,segment_id TEXT,
            PRIMARY KEY(event_id,side,participant_index));
        CREATE INDEX lineage_segment_ports_lookup ON lineage_segment_ports(segment_id,side,event_id);
        CREATE TABLE lineage_occurrences(event_id TEXT PRIMARY KEY,transition_index INTEGER,status TEXT);
        CREATE INDEX lineage_occurrences_transition ON lineage_occurrences(transition_index,status);
        DROP TABLE IF EXISTS temp.lineage_chunks;
        CREATE TEMP TABLE lineage_chunks(state_key TEXT,ranges BLOB);
        DROP TABLE IF EXISTS temp.lineage_instance_states;
        CREATE TEMP TABLE lineage_instance_states(instance_id TEXT,frame INTEGER,state_key TEXT);
        DROP TABLE IF EXISTS temp.lineage_boundaries;
        CREATE TEMP TABLE lineage_boundaries(frame INTEGER PRIMARY KEY);
        DROP TABLE IF EXISTS temp.lineage_active_blocks;
        CREATE TEMP TABLE lineage_active_blocks(block_start INTEGER PRIMARY KEY);
    ''')

    def tick(n):
        if progress and n % 4096 == 0:
            progress({'phase': 'building_lineage_segments', 'progress': .98,
                      'message': f'Preparing exact molecular segments: {n} state ranges'})

    def span(starts, ends, row):
        key, atoms, bonds = _source_state(row)
        if not atoms:
            raise ValueError('Molecular state has no atoms')
        connection.execute('INSERT OR IGNORE INTO lineage_states VALUES(?,?,?,?)',
                           (key, row.species, json.dumps(atoms), json.dumps(bonds)))
        values = np.column_stack((starts, ends)).astype('<i8', copy=False)
        connection.execute('INSERT INTO lineage_chunks VALUES(?,?)', (key, zlib.compress(values.tobytes(), 1)))

    if native is not None:
        connection.executemany('INSERT INTO lineage_frames VALUES(?,?,?,?)', native.iter_frame_references())
        for count, (starts, ends, row) in enumerate(native.iter_molecular_state_blocks(), 1):
            span(starts, ends, row)
            tick(count)
    else:
        pending = {}
        pending_count = 0

        def flush():
            for row, values in pending.values():
                values = np.asarray(values, dtype=np.int64)
                starts, ends = _merge_inclusive_ranges(values, values)
                span(starts, ends, row)
            pending.clear()

        for frame, timestep, rows in iter_csv_molecular_frames(molecules_file):
            connection.execute('INSERT INTO lineage_frames VALUES(?,?,1,?)', (frame, timestep, frame))
            for row in rows:
                key, _, _ = _source_state(row)
                if key not in pending:
                    pending[key] = (row, [])
                pending[key][1].append(frame)
                pending_count += 1
                if pending_count >= 4096:
                    flush()
                    pending_count = 0
            tick(frame)
        flush()
    connection.execute('CREATE INDEX temp.lineage_chunks_order ON lineage_chunks(state_key)')
    # Source boundaries are indexed on disk; a dataset may contain many files.
    connection.execute('''INSERT INTO lineage_boundaries SELECT a.frame FROM lineage_frames a
        JOIN lineage_frames b ON b.frame=a.frame-1 WHERE a.source_id<>b.source_id''')

    for count, (instance, frame, species, atoms, bonds) in enumerate(connection.execute('''SELECT instance_id,analyzed_frame,
            species_smiles,atom_ids_json,bonds_json FROM molecule_instances'''), 1):
        tick(count)
        key, _, _ = state_record(species, json.loads(atoms), json.loads(bonds))
        connection.execute('INSERT INTO lineage_instance_states VALUES(?,?,?)', (instance,frame,key))
    connection.execute('CREATE INDEX temp.lineage_instance_states_key ON lineage_instance_states(state_key,frame)')
    frame_count = connection.execute('SELECT COUNT(*) FROM lineage_frames').fetchone()[0]
    atom_ids = [r[0] for r in connection.execute('SELECT DISTINCT value FROM lineage_states,json_each(atoms) ORDER BY value')]
    atom_index = {a:i for i,a in enumerate(atom_ids)}
    width = (frame_count+7)//8
    logical_segments = 0
    # Disk-backed packed atom occupancy is a temporary integrity check, not a
    # second molecule detector. It flags overlapping authored states at any frame.
    with tempfile.TemporaryDirectory(prefix='scope-lineage-atoms-') as directory:
        shape=(max(1,len(atom_ids)),max(1,width))
        occupied=np.memmap(Path(directory)/'occupied',mode='w+',dtype=np.uint8,shape=shape)
        conflicts=np.memmap(Path(directory)/'conflicts',mode='w+',dtype=np.uint8,shape=shape)
        for count,(key, rows) in enumerate(groupby(connection.execute(
                'SELECT state_key,ranges FROM lineage_chunks ORDER BY state_key'),lambda r:r[0]),1):
            tick(count)
            connection.execute('DELETE FROM lineage_active_blocks')
            # A disk-backed difference vector accepts overlapping ranges in any
            # chunk order. Only one fixed-size frame block enters process memory.
            delta=np.memmap(Path(directory)/'state-delta',mode='w+',dtype=np.int64,
                            shape=(frame_count+1,))
            for _,blob in rows:
                intervals=np.frombuffer(zlib.decompress(blob),dtype='<i8').reshape((-1,2))
                np.add.at(delta,intervals[:,0],1)
                np.add.at(delta,intervals[:,1]+1,-1)
                first=intervals[:,0]//FRAME_BLOCK
                last=intervals[:,1]//FRAME_BLOCK
                single=first==last
                connection.executemany('INSERT OR IGNORE INTO lineage_active_blocks VALUES(?)',
                    ((int(block)*FRAME_BLOCK,) for block in np.unique(first[single])))
                connection.executemany('INSERT OR IGNORE INTO lineage_active_blocks VALUES(?)',
                    ((block*FRAME_BLOCK,) for start,end in zip(first[~single],last[~single])
                     for block in range(int(start),int(end)+1)))
            packed=np.memmap(Path(directory)/'state-packed',mode='w+',dtype=np.uint8,
                             shape=(max(1,width),))
            atoms=json.loads(connection.execute('SELECT atoms FROM lineage_states WHERE state_key=?',(key,)).fetchone()[0])
            segment_count=0
            carry=0
            previous_present=False
            previous_end=0
            for (block_start,) in connection.execute(
                    'SELECT block_start FROM lineage_active_blocks ORDER BY block_start'):
                if block_start>previous_end:
                    # A range ending at the preceding block's last frame has
                    # its closing delta in the skipped empty block.
                    carry=0
                    previous_present=False
                block_end=min(frame_count,block_start+FRAME_BLOCK)
                counts=np.cumsum(delta[block_start:block_end],dtype=np.int64)
                counts+=carry
                carry=int(counts[-1])
                present=counts>0
                starts=present & ~np.r_[previous_present,present[:-1]]
                boundaries=[r[0]-block_start for r in connection.execute(
                    'SELECT frame FROM lineage_boundaries WHERE frame>=? AND frame<?',
                    (block_start,block_end))]
                if boundaries:
                    starts[boundaries] |= present[boundaries]
                segment_count+=int(np.count_nonzero(starts))
                previous_present=bool(present[-1])
                bits=np.packbits(present,bitorder='little')
                byte_start=block_start//8
                packed[byte_start:byte_start+len(bits)]=bits
                if np.any(bits):
                    connection.execute('INSERT INTO lineage_occupancy_chunks VALUES(?,?,?)',
                                       (key,block_start,zlib.compress(bits.tobytes(),1)))
                    for atom in atoms:
                        i=atom_index[atom]
                        end=byte_start+len(bits)
                        conflicts[i,byte_start:end] |= occupied[i,byte_start:end] & bits
                        occupied[i,byte_start:end] |= bits
                previous_end=block_end
            logical_segments+=segment_count
            connection.execute('INSERT INTO lineage_state_occupancy VALUES(?,?,?)',
                               (key,frame_count,segment_count))
            del delta
            current_segment=None
            instances=connection.execute('''SELECT instance_id,frame FROM lineage_instance_states
                WHERE state_key=? ORDER BY frame''',(key,))
            for instance,frame in instances:
                if frame<0 or frame>=frame_count or not packed[frame//8] & (1 << (frame%8)):
                    continue
                if current_segment is None or not current_segment[0]<=frame<=current_segment[1]:
                    lower=connection.execute('SELECT MAX(frame) FROM lineage_boundaries WHERE frame<=?',
                                             (frame,)).fetchone()[0] or 0
                    upper=connection.execute('SELECT MIN(frame) FROM lineage_boundaries WHERE frame>?',
                                             (frame,)).fetchone()[0] or frame_count
                    start,end=_segment_bounds(packed,frame,lower,upper)
                    sid='seg_'+hashlib.sha256(f'{replicate_id}:{key}:{start}:{end}'.encode()).hexdigest()[:32]
                    added=connection.execute('INSERT OR IGNORE INTO lineage_segments VALUES(?,?,?,?,0)',
                                             (sid,key,start,end)).rowcount
                    if added:
                        connection.executemany('INSERT INTO lineage_segment_atoms VALUES(?,?,?,?)',
                                               ((a,sid,start,end) for a in atoms))
                    current_segment=(start,end,sid)
                sid=current_segment[2]
                connection.execute('INSERT INTO lineage_instance_segments VALUES(?,?)',(instance,sid))
            del packed
        for count,(atom,sid,start,end) in enumerate(connection.execute('SELECT * FROM lineage_segment_atoms'),1):
            tick(count)
            for byte_start in range(start//8,end//8+1,FRAME_BLOCK//8):
                byte_end=min(end//8+1,byte_start+FRAME_BLOCK//8)
                bits=np.unpackbits(conflicts[atom_index[atom],byte_start:byte_end],bitorder='little')
                first=max(start,byte_start*8)-byte_start*8
                last=min(end+1,byte_end*8)-byte_start*8
                if bits[first:last].any():
                    connection.execute('UPDATE lineage_segments SET conflict=1 WHERE segment_id=?',(sid,))
                    break
        del occupied,conflicts
    connection.execute('DROP TABLE temp.lineage_boundaries')
    connection.execute('DROP TABLE temp.lineage_active_blocks')
    connection.execute('DELETE FROM lineage_instance_segments WHERE segment_id IN (SELECT segment_id FROM lineage_segments WHERE conflict=1)')
    connection.execute('DROP TABLE temp.lineage_chunks')
    connection.execute('DROP TABLE temp.lineage_instance_states')
    connection.execute('''INSERT INTO lineage_segment_ports
        SELECT p.event_id,p.side,p.participant_index,m.segment_id FROM event_participants p
        JOIN lineage_instance_segments m USING(instance_id)''')
    for count, (event, transition, status) in enumerate(connection.execute('SELECT event_id,timestep_index,association_status FROM events ORDER BY timestep_index,event_id'), 1):
        tick(count)
        expected = connection.execute('SELECT COUNT(*) FROM event_participants WHERE event_id=?', (event,)).fetchone()[0]
        ports = connection.execute('''SELECT p.side,s.segment_id,s.start_frame,s.end_frame FROM lineage_segment_ports p
            JOIN lineage_segments s USING(segment_id) WHERE p.event_id=?''', (event,)).fetchall()
        resolved = status == 'matched' and len(ports) == expected and expected > 0
        inputs = {r[1] for r in ports if r[0] == 'reactant'}
        outputs = {r[1] for r in ports if r[0] == 'product'}
        resolved = resolved and bool(inputs) and bool(outputs)
        for side, sid, start, end in ports:
            if sid in inputs & outputs:  # unchanged participant is context
                continue
            resolved = resolved and (end == transition if side == 'reactant' else start == transition+1)
        connection.execute('INSERT INTO lineage_occurrences VALUES(?,?,?)',
                           (event, transition, 'mapped' if resolved else 'stop'))
    connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('lineage_segments_version', VERSION))
    connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('lineage_logical_segment_count',str(logical_segments)))
