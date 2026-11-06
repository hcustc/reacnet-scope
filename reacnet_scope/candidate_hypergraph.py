"""Portable hypergraph projection of bounded Candidate results.

This module only consumes returned routes. It does not read a dataset, discover
new routes in their union, or infer molecular continuity from connectivity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence


CANDIDATE_HYPERGRAPH_SCHEMA_VERSION = 'reacnet-scope/candidate-hypergraph/v1'


def hypergraph_id(kind: str, *identity: str) -> str:
    """Stable exact-identity IDs shared with the existing graph UI."""
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return f'{kind}-{digest}'


def build_candidate_hypergraph(report: Mapping[str, Any], *,
                              signature_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """Preserve complete directed reactions, coefficients and route membership.

    Shared Reaction Types carry their original counts once. Carrier-specific
    support stays on each display step. An optional ``_display_step_indexes``
    distinguishes browsing history from steps of the current Candidate.
    """
    if report.get('error'):
        raise ValueError('不能从失败的候选查询导出网络。')
    species_nodes: dict[str, dict[str, Any]] = {}
    reactions: dict[str, dict[str, Any]] = {}
    layers: dict[str, int] = {}
    routes = []

    def add_member(items, member):
        if member not in items:
            items.append(dict(member))

    def species_node(species, layer):
        sid = hypergraph_id('species', species)
        if sid not in species_nodes:
            species_nodes[sid] = dict(id=sid, species=species,
                display_number=len(species_nodes) + 1, members=[], carrier_members=[])
        layers[sid] = min(layers.get(sid, layer), layer)
        return species_nodes[sid]

    for path in report.get('paths') or []:
        signature = path['signature_id']
        steps = path['steps']
        indexes = path.get('_display_step_indexes', list(range(len(steps))))
        if len(indexes) != len(steps):
            raise ValueError('浏览步骤与当前候选步骤的映射不一致。')
        route = dict(signature_id=signature,
                     candidate_signature=path.get('candidate_signature'),
                     candidate_identity=deepcopy(path.get('candidate_identity')),
                     continuous_md=path.get('continuous_md', 'not_evaluated'),
                     continuous_support=deepcopy(path.get('continuous_support')),
                     display_species=list(path['species']), display_steps=[])
        routes.append(route)
        for depth, species in enumerate(path['species']):
            node = species_node(species, depth * 2)
            index = indexes[min(depth, len(steps) - 1)] if steps else None
            member = dict(signature_id=signature, step_index=index)
            if index is None:
                member['historical'] = True
            add_member(node['members'], member)
            add_member(node['carrier_members'], member)
        for depth, step in enumerate(steps):
            if not step.get('reactants') or not step.get('products'):
                raise ValueError('超图需要完整反应物和产物，不能用主线载体替代。')
            key = step['reaction_key']
            rid = hypergraph_id('reaction', key)
            member = dict(signature_id=signature, step_index=indexes[depth])
            if indexes[depth] is None:
                member['historical'] = True
            if rid not in reactions:
                reactions[rid] = dict(id=rid, reaction_key=key,
                    display_number=len(reactions) + 1, event_count=step['event_count'],
                    forward_count=step.get('forward_count'),
                    reverse_count=step.get('reverse_count'), net_count=step.get('net_count'),
                    members=[], tail=[], head=[])
            reaction = reactions[rid]
            # A union must never silently merge conflicting complete equations
            # or counts under a single Reaction Type identity.
            for side, field in (('tail', 'reactants'), ('head', 'products')):
                existing = {t['species']: t['coefficient'] for t in reaction[side]}
                if existing and existing != dict(Counter(step[field])):
                    raise ValueError('相同反应身份对应了不同的完整计量。')
            for field in ('event_count', 'forward_count', 'reverse_count', 'net_count'):
                if reaction[field] != step.get(field):
                    raise ValueError('相同反应身份的计数不一致，请重新查询。')
            add_member(reaction['members'], member)
            layers[rid] = min(layers.get(rid, depth * 2 + 1), depth * 2 + 1)
            for side, field, carrier, layer in (
                ('tail', 'reactants', step['carried_from'], depth * 2),
                ('head', 'products', step['carried_to'], depth * 2 + 2),
            ):
                if carrier not in step[field]:
                    raise ValueError('主线载体不在完整反应参与物中。')
                terms = {t['species']: t for t in reaction[side]}
                for species, coefficient in Counter(step[field]).items():
                    node = species_node(species, layer)
                    add_member(node['members'], member)
                    if species == carrier:
                        add_member(node['carrier_members'], member)
                    if species not in terms:
                        terms[species] = dict(
                            id=hypergraph_id('edge', key,
                                'reactant' if side == 'tail' else 'product', species),
                            species_id=node['id'], species=species, coefficient=coefficient,
                            members=[], carrier_members=[])
                        reaction[side].append(terms[species])
                    add_member(terms[species]['members'], member)
                    if species == carrier:
                        add_member(terms[species]['carrier_members'], member)
            route['display_steps'].append(dict(
                hyperedge_id=rid, step_index=indexes[depth],
                historical=indexes[depth] is None,
                **{field: deepcopy(step.get(field)) for field in (
                    'carried_from', 'carried_to', 'transfer_event_count',
                    'max_shared_atoms', 'transfer_basis', 'quality')}))

    if signature_ids is not None:
        visible = set(signature_ids)
        if visible - {p['signature_id'] for p in routes}:
            raise ValueError('所选路线不在当前候选结果中。')
        routes = [p for p in routes if p['signature_id'] in visible]
        for node in [*species_nodes.values(), *reactions.values()]:
            for field in ('members', 'carrier_members'):
                if field in node:
                    node[field] = [m for m in node[field] if m['signature_id'] in visible]
        species_nodes = {k: v for k, v in species_nodes.items() if v['members']}
        reactions = {k: v for k, v in reactions.items() if v['members']}
        for reaction in reactions.values():
            for term in [*reaction['tail'], *reaction['head']]:
                for field in ('members', 'carrier_members'):
                    term[field] = [m for m in term[field] if m['signature_id'] in visible]

    by_layer = defaultdict(list)
    for node in [*species_nodes.values(), *reactions.values()]:
        by_layer[layers[node['id']]].append(node)
    for layer, nodes in by_layer.items():
        for order, node in enumerate(nodes):
            node.update(layout_layer=layer, layout_order=order)

    # The original discovery flags describe the query, never completeness of
    # a global network. Keep even empty/truncated queries distinguishable.
    provenance = {key: deepcopy(value) for key, value in report.items()
                  if key not in {'paths', 'cycle_closures', 'browse_history_step_count'}}
    return dict(schema_version=CANDIDATE_HYPERGRAPH_SCHEMA_VERSION,
        scope='returned_candidate_projection',
        semantics='Complete reaction participants; independent Step Evidence. '
                  'The union does not imply new Candidates or continuous molecular history.',
        provenance=provenance,
        selection=dict(scope='selected_routes' if signature_ids is not None else 'returned_routes',
                       signature_ids=[p['signature_id'] for p in routes]),
        browse_history_step_count=report.get('browse_history_step_count', 0),
        species=list(species_nodes.values()), hyperedges=list(reactions.values()), routes=routes)
