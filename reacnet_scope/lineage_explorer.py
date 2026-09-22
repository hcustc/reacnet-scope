"""Segment/occurrence Explorer and observed paths over its concrete lineage.

All online operations are bounded reads of the published segment substrate.
No species network, maximum-overlap carrier choice, or event quality scoring.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .event_index import EVENT_EVIDENCE_STORE
from .lineage_segments import VERSION
from .lineage_provenance import normalize_elements, select_anchors, stoichiometry, annotate_path
from .indexes import IndexNotReadyError
from .service_types import ServiceError
from .workspace_services import _event_artifact_paths

SCHEMA = 'molecular-lineage-explorer/v2'
MAX_SEGMENTS = 500
MAX_OCCURRENCES = 250


def _revision(artifacts):
    source, molecules = _event_artifact_paths(artifacts)
    if not source:
        raise ServiceError('请先加载具有 Reaction Occurrence 和分子证据的数据集。', reason='missing_source')
    try:
        opened = EVENT_EVIDENCE_STORE.open_required(source, molecules)
        revision = {str(Path(p).resolve()): {'size': Path(p).stat().st_size,
                'mtime_ns': str(Path(p).stat().st_mtime_ns)} for p in (source, molecules, opened['index_path']) if p}
        if artifacts.get('trajectory'):
            from .trajectory import dataset_settings_path
            for path in [Path(artifacts['trajectory']), dataset_settings_path(artifacts['trajectory'])]:
                revision[str(path.resolve())] = ({'size': path.stat().st_size, 'mtime_ns': str(path.stat().st_mtime_ns)}
                                                if path.is_file() else {'status': 'missing'})
        return revision
    except (IndexNotReadyError, OSError, ValueError) as exc:
        raise ServiceError(str(exc), reason='lineage_index_unavailable') from exc


@contextmanager
def _snapshot(artifacts, report=None):
    revision = _revision(artifacts)
    if report is not None and (report.get('schema_version') != SCHEMA or report.get('source_revision') != revision):
        raise ServiceError('谱系来源已变化，请重新选择起点。', reason='source_changed')
    source, molecules = _event_artifact_paths(artifacts)
    opened = EVENT_EVIDENCE_STORE.open_required(source, molecules)
    con = sqlite3.connect(Path(opened['index_path']).as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    deadline = time.monotonic() + 5
    con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        version = con.execute("SELECT value FROM meta WHERE key='lineage_segments_version'").fetchone()
        if not version or version[0] != VERSION:
            raise ServiceError('Molecular Lineage Explorer 需要连续分子段索引；请在数据集准备中重建事件索引。', reason='lineage_rebuild_required')
        if report is not None:
            root = con.execute('''SELECT m.analyzed_frame,m.atom_ids_json,s.segment_id FROM molecule_instances m
                JOIN lineage_instance_segments s USING(instance_id) WHERE m.instance_id=?''',
                (report.get('root', {}).get('instance_id'),)).fetchone()
            if (not root or root['segment_id'] != report['root'].get('segment_id') or
                    root['analyzed_frame'] != report['root'].get('analyzed_frame') or
                    json.loads(root['atom_ids_json']) != report.get('root_atom_ids')):
                raise ServiceError('谱系起点已失效，请重新选择。', reason='bad_root')
            policy = report.get('anchor_policy') or {}
            anchors = select_anchors(report['root_atom_ids'], mode=policy.get('mode'),
                elements=policy.get('elements', []), atom_ids=policy.get('atom_ids', []),
                atom_elements=report.get('atom_elements'))
            if anchors != report.get('anchor_atom_ids'):
                raise ServiceError('锚点与选择规则不一致，请重新打开起点。', reason='invalid_anchor')
            if len(report.get('segments', {})) > MAX_SEGMENTS or len(report.get('occurrences', {})) > MAX_OCCURRENCES:
                raise ServiceError('谱系超过交互预算。', reason='lineage_budget')
        yield _Reader(con), revision, deadline
        if _revision(artifacts) != revision:
            raise ServiceError('谱系查询期间来源已变化，请重新选择起点。', reason='source_changed')
    except sqlite3.OperationalError as exc:
        raise ServiceError('谱系读取未完成：' + str(exc), reason='lineage_read_incomplete') from exc
    finally:
        con.close()


def lineage_explorer_status(artifacts):
    try:
        with _snapshot(artifacts):
            return {'available': True, 'message': '连续分子段索引已就绪。'}
    except (ServiceError, OSError, ValueError) as exc:
        return {'available': False, 'message': str(exc)}


def _components(atoms, bonds):
    neighbors = {a: set() for a in atoms}
    for bond in bonds:
        a, b, _ = bond.split('-', 2)
        a, b = int(a), int(b)
        if a in neighbors and b in neighbors:
            neighbors[a].add(b)
            neighbors[b].add(a)
    result = []
    unseen = set(atoms)
    while unseen:
        pending = [min(unseen)]
        component = set()
        while pending:
            atom = pending.pop()
            if atom in component:
                continue
            component.add(atom)
            pending.extend(neighbors[atom] - component)
        unseen -= component
        result.append(sorted(component))
    return result


class _Reader:
    def __init__(self, con):
        self.con = con

    def segment(self, sid, anchors):
        row = self.con.execute('''SELECT s.*,q.species,q.atoms,q.bonds,
            a.timestep AS start_timestep,b.timestep AS end_timestep
            FROM lineage_segments s JOIN lineage_states q USING(state_key)
            JOIN lineage_frames a ON a.frame=s.start_frame
            JOIN lineage_frames b ON b.frame=s.end_frame WHERE segment_id=?''', (sid,)).fetchone()
        if row is None:
            raise ServiceError('所选分子段不存在。', reason='missing_segment')
        result = dict(row)
        atoms, bonds = json.loads(result.pop('atoms')), json.loads(result.pop('bonds'))
        result.update(atom_ids=atoms, bonds=bonds, retained_atoms=sorted(set(atoms) & anchors),
                      lost_atoms=sorted(anchors - set(atoms)), gained_atoms=sorted(set(atoms) - anchors),
                      metric_reference='selected_root_anchors', connected_components=_components(atoms, bonds),
                      retained_components=_components(set(atoms) & anchors, bonds))
        result['previous'] = self.boundary(result, 'backward')
        result['next'] = self.boundary(result, 'forward')
        return result

    def boundary(self, segment, direction):
        if segment['conflict']:
            return {'status': 'stop', 'event_id': None}
        forward = direction == 'forward'
        start, end = segment['start_frame'], segment['end_frame']
        transition = end if forward else start - 1
        if transition < 0:
            return {'status': 'observation_boundary', 'event_id': None}
        # Only source states on the same source stream can be connected.
        frames = self.con.execute('SELECT source_id FROM lineage_frames WHERE frame IN (?,?) ORDER BY frame',
                                  (transition, transition+1)).fetchall()
        if len(frames) != 2 or frames[0][0] != frames[1][0]:
            return {'status': 'observation_boundary', 'event_id': None}
        # Unresolved records stop only overlapping atoms (or unknown membership).
        barrier = self.con.execute('''SELECT o.event_id FROM lineage_occurrences o JOIN events e USING(event_id)
            WHERE o.status='stop' AND o.transition_index BETWEEN ? AND ? AND
            (e.atom_ids_json='[]' OR EXISTS (SELECT 1 FROM event_atoms a
                JOIN lineage_segment_atoms s ON s.atom_id=a.atom_id
                WHERE a.event_id=e.event_id AND s.segment_id=?)) LIMIT 1''',
            (start if forward else max(0, start-1), end if forward else max(0, end-1), segment['segment_id'])).fetchone()
        if barrier:
            return {'status': 'stop', 'event_id': None, 'boundary_event_id': barrier[0]}
        side = 'reactant' if forward else 'product'
        rows = self.con.execute('''SELECT DISTINCT p.event_id,o.status FROM lineage_segment_ports p
            JOIN lineage_occurrences o USING(event_id) WHERE p.segment_id=? AND p.side=?
            AND o.transition_index=? ORDER BY p.event_id LIMIT 2''',
            (segment['segment_id'], side, transition)).fetchall()
        if len(rows) == 1 and rows[0]['status'] == 'mapped':
            return {'status': 'available', 'event_id': rows[0]['event_id']}
        return {'status': 'stop', 'event_id': None}

    def occurrence(self, event_id, anchors):
        row = self.con.execute('''SELECT o.transition_index,o.status,e.reaction_key,e.before_timestep,e.after_timestep,
            e.reactant_bonds_json,e.product_bonds_json FROM lineage_occurrences o
            JOIN events e USING(event_id) WHERE event_id=?''', (event_id,)).fetchone()
        if not row or row['status'] != 'mapped':
            raise ServiceError('该事件没有可展开的 segment 关联。', reason='lineage_stop')
        ports = self.con.execute('SELECT side,participant_index,segment_id FROM lineage_segment_ports WHERE event_id=? ORDER BY side,participant_index LIMIT ?', (event_id, MAX_SEGMENTS + 1)).fetchall()
        if len(ports) > MAX_SEGMENTS:
            raise ServiceError('该事件参与者超过当前交互预算，保持原图。', reason='lineage_budget')
        segments = {p['segment_id']: self.segment(p['segment_id'], anchors) for p in ports}
        inputs = [p['segment_id'] for p in ports if p['side'] == 'reactant']
        outputs = [p['segment_id'] for p in ports if p['side'] == 'product']
        provenance = []
        for source in inputs:
            a = set(segments[source]['atom_ids'])
            for target in outputs:
                b = set(segments[target]['atom_ids'])
                if a & b:
                    provenance.append({'from_segment': source, 'to_segment': target,
                                       'retained_atoms': sorted(a & b), 'lost_atoms': sorted(a - b),
                                       'gained_atoms': sorted(b - a)})
        before, after = set(json.loads(row['reactant_bonds_json'])), set(json.loads(row['product_bonds_json']))
        event = dict(event_id=event_id, transition_index=row['transition_index'], reaction_type=row['reaction_key'],
                     before_frame=row['transition_index'], after_frame=row['transition_index']+1,
                     before_timestep=row['before_timestep'], after_timestep=row['after_timestep'],
                     input_segments=inputs, output_segments=outputs, provenance=provenance,
                     reaction_stoichiometry={'reactants': stoichiometry(inputs, segments),
                                             'products': stoichiometry(outputs, segments)},
                     split=any(sum(p['from_segment'] == s for p in provenance) > 1 for s in inputs),
                     merge=any(sum(p['to_segment'] == s for p in provenance) > 1 for s in outputs),
                     broken_bonds=sorted(before-after), formed_bonds=sorted(after-before))
        return event, segments


def start_lineage_explorer(artifacts, *, event_id='', side='reactant', participant_index=0, instance_id='',
                           anchor_mode='all_atoms', anchor_elements=(), anchor_atom_ids=(), atom_elements=None):
    with _snapshot(artifacts) as (reader, revision, _):
        if instance_id:
            row = reader.con.execute('''SELECT m.instance_id,m.analyzed_frame,s.segment_id FROM molecule_instances m
                JOIN lineage_instance_segments s USING(instance_id) WHERE m.instance_id=?''', (instance_id,)).fetchone()
        else:
            row = reader.con.execute('''SELECT p.instance_id,m.analyzed_frame,s.segment_id FROM event_participants p
                JOIN molecule_instances m USING(instance_id) JOIN lineage_instance_segments s USING(instance_id)
                WHERE p.event_id=? AND p.side=? AND p.participant_index=?''', (event_id, side, participant_index)).fetchone()
        if row is None:
            raise ServiceError('所选具体 molecule instance 无法映射到连续分子段。', reason='lineage_stop')
        segment = reader.segment(row['segment_id'], set())
        mapping = normalize_elements(atom_elements)
        evidence = {'source': 'provided' if atom_elements is not None else 'unavailable'}
        if atom_elements is None and artifacts.get('trajectory'):
            from .trajectory import TrajectoryDependencyError
            ref = dict(reader.con.execute('SELECT * FROM lineage_frames WHERE frame=?', (row['analyzed_frame'],)).fetchone())
            try:
                parsed, _ = _original_frame(artifacts, ref, revision)
                mapping = normalize_elements({a: data['element'] for a, data in parsed['atoms'].items() if data.get('element')})
                evidence = {'source': 'exact_original_frame', 'frame': ref['frame'], 'timestep': ref['timestep']}
            except (ServiceError, OSError, ValueError, TrajectoryDependencyError) as exc:
                evidence['message'] = str(exc)
        anchors = set(select_anchors(segment['atom_ids'], mode=anchor_mode, elements=anchor_elements,
                                     atom_ids=anchor_atom_ids, atom_elements=mapping))
        segment = reader.segment(row['segment_id'], anchors)
        return dict(schema_version=SCHEMA, source_revision=revision, root=dict(row),
                    root_atom_ids=segment['atom_ids'], atom_elements={str(a): e for a, e in mapping.items()},
                    element_evidence=evidence,
                    anchor_policy={'mode': anchor_mode, 'elements': sorted(set(anchor_elements)),
                                   'atom_ids': sorted(set(anchor_atom_ids))},
                    anchor_atom_ids=sorted(anchors), segments={segment['segment_id']: segment},
                    occurrences={}, expanded=[], limits={'segments': MAX_SEGMENTS, 'occurrences': MAX_OCCURRENCES},
                    last_action={'status': 'ready'})


def expand_lineage_explorer(artifacts, report, *, segment_id='', direction='forward', all_branches=False):
    if direction not in {'forward', 'backward'}:
        raise ServiceError('请选择向前或向后追踪。', reason='invalid_direction')
    if len(report.get('segments', {})) > MAX_SEGMENTS or len(report.get('occurrences', {})) > MAX_OCCURRENCES:
        raise ServiceError('谱系超过交互预算，请重新选择起点。', reason='lineage_budget')
    result = copy.deepcopy(report)
    with _snapshot(artifacts, report) as (reader, _, deadline):
        anchors = set(report['anchor_atom_ids'])
        selected = list(report['segments']) if all_branches else [segment_id]
        completed = set(result['expanded'])
        added = 0
        result['last_action'] = {'status': 'complete', 'direction': direction}
        for sid in selected:
            if sid not in result['segments']:
                raise ServiceError('请从当前谱系选择一个分子段。', reason='bad_selection')
            token = f'{direction}:{sid}'
            if token in completed:
                continue
            if time.monotonic() >= deadline or added >= 20:
                result['last_action'] = {'status': 'query_budget', 'direction': direction}
                break
            segment = reader.segment(sid, anchors)
            result['segments'][sid] = segment
            boundary = segment['next' if direction == 'forward' else 'previous']
            if boundary['status'] != 'available':
                completed.add(token)
                result['last_action'] = boundary
                continue
            try:
                event, segments = reader.occurrence(boundary['event_id'], anchors)
            except ServiceError as exc:
                if exc.reason != 'lineage_budget':
                    raise
                result['last_action'] = {'status': 'graph_budget'}
                break
            if len(set(result['segments']) | set(segments)) > MAX_SEGMENTS or (
                    event['event_id'] not in result['occurrences'] and len(result['occurrences']) >= MAX_OCCURRENCES):
                result['last_action'] = {'status': 'graph_budget'}
                break  # Never publish a partial split/merge hyperedge.
            result['segments'].update(segments)
            result['occurrences'][event['event_id']] = event
            completed.add(token)
            added += 1
        result['expanded'] = sorted(completed)
    return result


def observed_lineage_paths(artifacts, report, target_segment, *, max_paths=20):
    if isinstance(max_paths, bool) or not isinstance(max_paths, int) or not 1 <= max_paths <= 100:
        raise ServiceError('路径返回数必须在 1–100 之间。', reason='invalid_limit')
    if target_segment not in report['segments']:
        raise ServiceError('请选择当前 lineage 中的目标分子段。', reason='bad_selection')
    with _snapshot(artifacts, report) as (reader, _, deadline):
        # Rehydrate every selected occurrence from the revisioned index. Browser
        # graph JSON cannot supply invented ports, times, or atom transfers.
        outgoing = {}
        anchors = set(report['anchor_atom_ids'])
        concrete_segments = {report['root']['segment_id']: reader.segment(report['root']['segment_id'], anchors)}
        concrete_events = {}
        for event_id in report['occurrences']:
            event, segments = reader.occurrence(event_id, anchors)
            concrete_events[event_id] = event
            concrete_segments.update(segments)
            for port in event['provenance']:
                a, b = port['from_segment'], port['to_segment']
                if a == b:
                    continue
                if segments[a]['next'].get('event_id') != event_id or segments[b]['previous'].get('event_id') != event_id:
                    continue
                outgoing.setdefault(a, []).append((b, event_id, event['transition_index'], set(port['retained_atoms'])))
        root = report['root']['segment_id']
        paths, pending = [], [([root], [], anchors, -1)]
        examined = 0
        while pending and len(paths) < max_paths and examined < 5000 and time.monotonic() < deadline:
            nodes, events, retained, previous = pending.pop()
            examined += 1
            if nodes[-1] == target_segment:
                if events:
                    paths.append({'segments': nodes, 'events': events, 'retained_atoms': sorted(retained),
                                  'lost_atoms': sorted(anchors-retained), 'start_frame': report['root']['analyzed_frame']})
                continue
            for target, event, transition, atoms in sorted(outgoing.get(nodes[-1], []), reverse=True):
                if target in nodes or transition <= previous or not retained & atoms:
                    continue
                if not events and transition < report['root']['analyzed_frame']:
                    continue
                pending.append((nodes+[target], events+[event], retained & atoms, transition))
        paths = [annotate_path(path, report, concrete_segments, concrete_events, outgoing) for path in paths]
        return {'paths': paths, 'truncated': bool(pending), 'examined': examined,
                'schema_version': SCHEMA, 'anchor_policy': report['anchor_policy'],
                'scope': 'currently_expanded_lineage', 'target_segment': target_segment,
                'source_revision': report['source_revision']}


def lineage_frame_reference(artifacts, report, segment_id, frame):
    if segment_id not in report['segments']:
        raise ServiceError('请选择当前谱系中的分子段。', reason='bad_selection')
    with _snapshot(artifacts, report) as (reader, _, _):
        segment = reader.segment(segment_id, set(report['anchor_atom_ids']))
        if isinstance(frame, bool) or int(frame) != frame or not segment['start_frame'] <= frame <= segment['end_frame']:
            raise ServiceError('请选择该 segment 范围内的分析帧。', reason='bad_frame')
        ref = dict(reader.con.execute('SELECT * FROM lineage_frames WHERE frame=?', (int(frame),)).fetchone())
        return dict(ref, segment_id=segment_id, atom_ids=segment['atom_ids'], species=segment['species'],
                    bonds=segment['bonds'], source_revision=report['source_revision'])


def lineage_occurrence_record(artifacts, report, event_id):
    if event_id not in report['occurrences']:
        raise ServiceError('请从当前谱系选择具体事件。', reason='bad_selection')
    with _snapshot(artifacts, report):
        source, molecules = _event_artifact_paths(artifacts)
        return EVENT_EVIDENCE_STORE.get_event(source, molecules, event_id)


def export_lineage_explorer(artifacts, report, *, target_segment=None):
    """Rehydrate exported facts; a browser report only selects identities."""
    with _snapshot(artifacts, report) as (reader, _, _):
        result = copy.deepcopy(report)
        anchors = set(report['anchor_atom_ids'])
        result['segments'] = {sid: reader.segment(sid, anchors) for sid in report['segments']}
        result['occurrences'] = {}
        for event_id in report['occurrences']:
            event, segments = reader.occurrence(event_id, anchors)
            result['occurrences'][event_id] = event
            result['segments'].update(segments)
        if len(result['segments']) > MAX_SEGMENTS:
            raise ServiceError('导出超过交互预算。', reason='lineage_budget')
        paths = observed_lineage_paths(artifacts, result, target_segment) if target_segment else None
        return {'lineage': result, 'observed_paths': paths}


def lineage_frame_data(artifacts, report, segment_id, frame):
    """Read exactly one indexed original frame, never nearest-frame substitution."""
    ref = lineage_frame_reference(artifacts, report, segment_id, frame)
    parsed, block = _original_frame(artifacts, ref, report['source_revision'])
    atoms = parsed['atoms']
    if not set(ref['atom_ids']) <= set(atoms):
        raise ServiceError('原始帧未包含该分子段的全部 Atom IDs。', reason='missing_atoms')
    return dict(reference=ref, atoms=[atoms[a] for a in ref['atom_ids']], raw_text=block.decode('utf-8'))


def _original_frame(artifacts, ref, revision):
    from .indexes import TRAJECTORY_INDEX_STORE
    from .trajectory import read_lammps_frame_block, load_type_element_map
    trajectory = artifacts.get('trajectory')
    if not trajectory or ref['source_id'] != 1:
        raise ServiceError('尚未关联该 source 的原始轨迹。', reason='missing_trajectory')
    try:
        index = TRAJECTORY_INDEX_STORE.open_required(trajectory)
        offsets = index.offsets_for([ref['timestep']])
    except IndexNotReadyError as exc:
        raise ServiceError(str(exc), reason='trajectory_index_required') from exc
    if not offsets:
        raise ServiceError('原始轨迹中缺少该精确 timestep。', reason='missing_frame')
    start, end = offsets[ref['timestep']]
    length = end - start
    if length > 16 * 1024 * 1024:
        raise ServiceError('该原始帧超过交互读取上限。', reason='frame_budget')
    before = (Path(trajectory).stat().st_size, Path(trajectory).stat().st_mtime_ns)
    with open(trajectory, 'rb') as handle:
        handle.seek(start)
        block = handle.read(length)
    parsed = read_lammps_frame_block(block, type_element_map=load_type_element_map(trajectory))
    if parsed['frame'] != ref['timestep']:
        raise ServiceError('原始帧与索引 timestep 不一致。', reason='source_changed')
    if before != (Path(trajectory).stat().st_size, Path(trajectory).stat().st_mtime_ns) or _revision(artifacts) != revision:
        raise ServiceError('读取期间来源已变化。', reason='source_changed')
    return parsed, block
