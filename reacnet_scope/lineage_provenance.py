"""Anchor continuity and atom provenance on concrete, time-directed paths.

These annotations neither prune merge events nor classify returns as noise.
Element assignments are declared inputs, never inferred from Species order.
"""
from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping

from .service_types import ServiceError


def normalize_elements(values):
    from rdkit import Chem

    symbols = {Chem.GetPeriodicTable().GetElementSymbol(i) for i in range(1, 119)}
    result = {}
    if values is not None and not isinstance(values, Mapping):
        raise ServiceError('元素映射必须为 Atom ID → 元素对象。', reason='invalid_atom_elements')
    for key, element in (values or {}).items():
        if isinstance(key, bool) or not str(key).isdigit() or str(key).startswith('0') or not isinstance(element, str) or element not in symbols:
            raise ServiceError('元素映射须为正整数 Atom ID → 有效元素符号。', reason='invalid_atom_elements')
        result[int(key)] = element
    return result


def select_anchors(root_atoms, *, mode='all_atoms', elements=(), atom_ids=(), atom_elements=None):
    root = set(root_atoms)
    mapping = normalize_elements(atom_elements)
    if mode == 'all_atoms':
        selected = root
    elif mode == 'atom_ids':
        if any(isinstance(a, bool) or not isinstance(a, int) for a in atom_ids):
            raise ServiceError('锚点 Atom ID 必须为整数。', reason='invalid_anchor')
        selected = set(atom_ids)
        if not selected <= root:
            raise ServiceError('锚点 Atom ID 必须属于起始具体分子。', reason='invalid_anchor')
    elif mode in {'elements', 'heavy_atoms'}:
        missing = sorted(root - mapping.keys())
        if missing:
            raise ServiceError('缺少起始分子的可靠元素映射，不能按元素选择锚点；可提供映射或指定 Atom IDs。',
                               reason='missing_atom_elements')
        allowed = set(elements)
        if mode == 'elements' and not allowed:
            raise ServiceError('请选择锚点元素。', reason='invalid_anchor')
        selected = {a for a in root if mapping[a] != 'H'} if mode == 'heavy_atoms' else {
            a for a in root if mapping[a] in allowed}
    else:
        raise ServiceError('未知锚点选择方式。', reason='invalid_anchor')
    if not selected:
        raise ServiceError('锚点规则未选中任何原子，请更改选择。', reason='empty_anchor')
    return sorted(selected)


def stoichiometry(segment_ids, segments):
    counts = Counter(segments[s]['species'] for s in segment_ids)
    return [{'species': species, 'coefficient': count} for species, count in sorted(counts.items())]


def _state_key(segment, topology=False):
    bonds = tuple(sorted('-'.join(b.split('-')[:2]) if topology else b for b in segment['bonds']))
    return (None if topology else segment['species']), tuple(sorted(segment['atom_ids'])), bonds


def _atom_trace(atom, root, target, outgoing, first_transition):
    """A side branch is certified only through already expanded exact ports."""
    queue = deque([(root, [], [root], first_transition - 1)])
    seen = {root}
    while queue:
        node, events, segments, previous = queue.popleft()
        if node == target:
            return {'status': 'observed', 'events': events, 'segments': segments}
        for to, event, transition, atoms in outgoing.get(node, []):
            if atom in atoms and transition > previous and to not in seen:
                seen.add(to)
                queue.append((to, events + [event], segments + [to], transition))
    return {'status': 'not_resolved_in_expanded_graph', 'events': [], 'segments': []}


def annotate_path(path, report, segments, occurrences, outgoing):
    """Separate present-again root atoms from uninterrupted anchor retention."""
    root_atoms = set(report['root_atom_ids'])
    anchors = set(report['anchor_atom_ids'])
    mapping = normalize_elements(report.get('atom_elements'))
    retained = set(anchors)
    steps = []
    returns = []
    exact_seen, topology_seen = {}, {}
    for position, sid in enumerate(path['segments']):
        segment = segments[sid]
        exact, topology = _state_key(segment), _state_key(segment, True)
        earlier = exact_seen.get(exact, topology_seen.get(topology))
        if earlier is not None:
            returns.append({'from_segment': path['segments'][earlier], 'to_segment': sid,
                            'from_position': earlier, 'to_position': position,
                            'basis': 'exact_bonds' if exact in exact_seen else 'topology',
                            'events': path['events'][earlier:position],
                            'analyzed_frame_intervals': event_interval(occurrences, path, earlier, position)})
        exact_seen[exact] = topology_seen[topology] = position
        if not position:
            continue
        before_sid = path['segments'][position - 1]
        before = set(segments[before_sid]['atom_ids'])
        after = set(segment['atom_ids'])
        departed, entered = before - after, after - before
        returning = entered & root_atoms
        external = entered - root_atoms
        retained &= after
        event = occurrences[path['events'][position - 1]]
        sources = [{'segment_id': source, 'atom_ids': sorted(entered & set(segments[source]['atom_ids']))}
                   for source in event['input_segments'] if entered & set(segments[source]['atom_ids'])]
        destinations = [{'segment_id': target, 'atom_ids': sorted(departed & set(segments[target]['atom_ids']))}
                        for target in event['output_segments'] if departed & set(segments[target]['atom_ids'])]
        missing = sorted((before | after) - mapping.keys())
        chlorine = lambda atoms: sorted(a for a in atoms if mapping.get(a) == 'Cl')
        steps.append({
            'event_id': event['event_id'], 'transition_index': event['transition_index'],
            'from_segment': before_sid, 'to_segment': sid,
            'reaction_type': event['reaction_type'], 'reaction_stoichiometry': event['reaction_stoichiometry'],
            'input_segments': event['input_segments'], 'output_segments': event['output_segments'],
            'continuously_retained_anchor_ids': sorted(retained),
            'anchor_ids_present': sorted(anchors & after),
            'all_anchors_continuous': retained == anchors,
            'departed_atom_ids': sorted(departed), 'entered_atom_ids': sorted(entered),
            'returned_root_atom_ids': sorted(returning), 'external_atom_ids_added': sorted(external),
            'entry_sources': sources, 'departure_destinations': destinations,
            'return_provenance': [{'atom_id': a, **_atom_trace(a, path['segments'][0], sid, outgoing,
                                      report['root']['analyzed_frame'])} for a in sorted(returning)],
            'chlorine': {'mapping_status': 'complete' if not missing else 'partial' if mapping else 'unavailable',
                         'unmapped_atom_ids': missing, 'departed_ids': chlorine(departed),
                         'original_ids_returned': chlorine(returning),
                         'external_ids_added': chlorine(external)},
        })
    return dict(path, anchor_policy=report['anchor_policy'], anchor_atom_ids=sorted(anchors),
                continuously_retained_anchor_ids=sorted(retained),
                all_anchors_continuous=retained == anchors,
                root_atom_ids_present_at_target=sorted(root_atoms & set(segments[path['segments'][-1]]['atom_ids'])),
                steps=steps, return_episodes=returns,
                interpretation='observed_anchor_projection_not_mechanism_or_net_formation')


def event_interval(occurrences, path, start, end):
    """Sampled intervals between departure and return transitions, not residence."""
    return (occurrences[path['events'][end - 1]]['transition_index']
            - occurrences[path['events'][start]]['transition_index'])
