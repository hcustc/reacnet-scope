"""A bounded display projection of returned candidates, never a new path search."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from urllib.parse import urlencode

import dash_bootstrap_components as dbc
import dash_cytoscape as cyto
from dash import dcc, html

from reacnet_scope import services as svc


STYLESHEET = [
    {'selector': 'node', 'style': {
        'label': 'data(label)', 'font-size': 12, 'color': '#263346',
        'text-wrap': 'wrap', 'text-max-width': 150, 'text-valign': 'bottom',
        'text-margin-y': 6, 'border-width': 1.5, 'border-color': '#bac8d7',
        'background-color': '#ffffff',
    }},
    {'selector': 'node[kind = "species"]', 'style': {
        'shape': 'round-rectangle', 'width': 126, 'height': 90,
        'background-image': 'data(image)', 'background-fit': 'contain',
        'background-clip': 'node',
    }},
    {'selector': 'node[kind = "reaction"]', 'style': {
        'shape': 'diamond', 'width': 28, 'height': 28,
        'background-color': '#e9eef5', 'text-max-width': 95, 'font-size': 11,
    }},
    {'selector': 'edge', 'style': {
        'curve-style': 'bezier', 'target-arrow-shape': 'triangle',
        'line-color': '#bdc9d6', 'target-arrow-color': '#bdc9d6', 'width': 1.5,
        'arrow-scale': 0.9,
    }},
    {'selector': '.anchor', 'style': {'border-width': 3, 'border-color': '#23887d'}},
    {'selector': '.target', 'style': {'border-width': 3, 'border-color': '#b07629'}},
]


def element_id(kind, *identity):
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return f'{kind}-{digest}'


def step_id(step):
    # Full directed stoichiometry and BOTH exact carriers define one shared step.
    return element_id('reaction', step['reaction_key'], step['carried_from'], step['carried_to'])


def elements(report):
    """Merge exact identities; keep route membership and counts without summing."""
    paths = (report or {}).get('paths') or []
    nodes = {}
    edges = {}
    layers = defaultdict(list)
    ranks = {}
    reaction_numbers = {}
    species_number = 0
    for path in paths:
        signature = path['signature_id']
        for depth, species in enumerate(path['species']):
            sid = element_id('species', species)
            if sid not in nodes:
                species_number += 1
                ranks[sid] = depth * 2
                nodes[sid] = {'data': {
                    'id': sid, 'kind': 'species', 'species': species,
                    'label': f'S{species_number} · {svc.candidate_formula(species) or species}',
                    'image': '/api/structure.svg?' + urlencode({'smiles': species, 'width': 180, 'height': 110}),
                    'members': [],
                }}
            ranks[sid] = min(ranks[sid], depth * 2)
            nodes[sid]['data']['members'].append({'signature_id': signature, 'step_index': min(depth, len(path['steps']) - 1)})
        for depth, step in enumerate(path['steps']):
            rid = step_id(step)
            member = {'signature_id': signature, 'step_index': depth}
            if rid not in nodes:
                reaction_numbers[rid] = len(reaction_numbers) + 1
                count = step.get('transfer_event_count', step['event_count'])
                nodes[rid] = {'data': {
                    'id': rid, 'kind': 'reaction', 'reaction_key': step['reaction_key'],
                    'carried_from': step['carried_from'], 'carried_to': step['carried_to'],
                    'label': f'R{reaction_numbers[rid]} · {count} 次',
                    'event_count': count, 'members': [],
                }}
            nodes[rid]['data']['members'].append(member)
            for side, source, target in (
                ('in', element_id('species', step['carried_from']), rid),
                ('out', rid, element_id('species', step['carried_to'])),
            ):
                eid = f'{rid}-{side}'
                if eid not in edges:
                    edges[eid] = {'data': {'id': eid, 'source': source, 'target': target,
                                           'kind': 'edge', 'reaction_id': rid, 'members': []}}
                edges[eid]['data']['members'].append(member)
    for rid in reaction_numbers:
        ranks[rid] = ranks[element_id('species', nodes[rid]['data']['carried_from'])] + 1
    for nid, layer in ranks.items():
        layers[layer].append(nid)
    # Stable left-to-right positions preserve orientation while highlighting routes.
    # The union may have back edges; it is not asserted to be a DAG or a new route.
    for layer, ids in layers.items():
        spacing = 145 if layer % 2 == 0 else 100
        for row, nid in enumerate(ids):
            nodes[nid]['position'] = {'x': layer * 120, 'y': (row - (len(ids) - 1) / 2) * spacing}
    query = (report or {}).get('query') or {}
    for node in nodes.values():
        species = node['data'].get('species')
        node['classes'] = ('anchor' if species and species == query.get('start') else
                           'target' if species and species == query.get('target') else '')
    result = [*nodes.values(), *edges.values()]
    for element in result:
        element['data'].update(context=(report or {}).get('context'),
                               query_request_id=(report or {}).get('query_request_id'))
    return result


def resolve_pick(report, picked):
    """Resolve only current report identities, never trust browser membership data."""
    if not report or not picked or any(picked.get(k) != report.get(k)
                                       for k in ('context', 'query_request_id')):
        return None
    return next((e['data'] for e in elements(report) if e['data']['id'] == picked.get('id')), None)


def stylesheet(graph, focus, selected, step_index):
    result = list(STYLESHEET)
    selected = set(selected or [])
    for element in graph:
        data = element['data']
        members = data['members']
        active = any(m['signature_id'] == focus for m in members)
        compared = any(m['signature_id'] in selected for m in members)
        current_step = data['kind'] != 'species' and any(
            m['signature_id'] == focus and m['step_index'] == step_index for m in members)
        color = '#bd6b16' if current_step else '#6850bd' if active else '#248e9b'
        style = {}
        if active or compared:
            if data['kind'] == 'edge':
                style = {'line-color': color, 'target-arrow-color': color, 'width': 3.5 if active else 2.5}
            else:
                style = {'border-color': color, 'border-width': 3,
                         **({'background-color': color} if data['kind'] == 'reaction' else {})}
        if style:
            result.append({'selector': f'#{data["id"]}', 'style': style})
    return result


def layout():
    return html.Div([
        html.Div([html.H6('路径合并图'), html.Div(id='cp-graph-summary', role='status')], className='rs-candidate-graph-heading'),
        html.Div([
            dcc.RadioItems(id='cp-graph-scope', options=[{'label': '全部返回路线', 'value': 'all'},
                {'label': '当前与勾选路线', 'value': 'selected'}], value='all', inline=True),
            dbc.Button('还原视图', id='cp-graph-reset', size='sm', outline=True),
        ], className='rs-query-row'),
        html.P('结构卡片为主线物种，菱形为反应步骤；点击节点或连线查看详情。拖动平移，滚轮缩放。', className='text-muted'),
        html.Div([
            cyto.Cytoscape(id='cp-graph', elements=[], layout={'name': 'preset', 'fit': True, 'padding': 45},
                           stylesheet=STYLESHEET, style={'width': '100%', 'height': '540px'},
                           minZoom=0.08, maxZoom=2.5, wheelSensitivity=0.2,
                           responsive=True, className='rs-candidate-graph-canvas'),
            html.Div(id='cp-graph-inspector', className='rs-candidate-graph-inspector'),
        ], className='rs-candidate-graph-body'),
        html.Div([html.Span('● 当前路线', className='rs-candidate-legend-focus'),
                  html.Span('● 当前步骤', className='rs-candidate-legend-step'),
                  html.Span('● 勾选路线', className='rs-candidate-legend-compare')], className='rs-candidate-legend'),
        html.P('仅合并本次返回路线中的精确 RNG 物种；同分子式不合并。反应次数为该步骤的原始支持事件数，共享步骤不累加。'
               '图上的连通不代表一条已验证的连续历史。', className='text-muted'),
        dcc.Store(id='cp-graph-pick'),
        dcc.Store(id='cp-graph-size'),
    ], className='rs-candidate-graph-panel')


def inspector(report, picked, structure):
    data = resolve_pick(report, picked)
    if not data:
        return html.P('点击共享物种查看经过它的路线；点击菱形或连线定位反应步骤。')
    if data['kind'] == 'edge':
        data = next(e['data'] for e in elements(report) if e['data']['id'] == data['reaction_id'])
    if data['kind'] == 'species':
        content = [html.H6('主线物种'), structure(data['species'])]
    else:
        member = data['members'][0]
        path = next(p for p in report['paths'] if p['signature_id'] == member['signature_id'])
        step = path['steps'][member['step_index']]
        quality = step.get('quality') or {}
        content = [html.H6(data['label']), html.Code(data['reaction_key']),
                   html.P(f"主线载体：{data['carried_from']} → {data['carried_to']}"),
                   html.P(f"原始支持 {data['event_count']} 次；其中折叠 {quality.get('folded_events', '未知')} 次。"),
                   html.Small('完整反应式包含共反应物和副产物；下方步骤证据可查看具体结构与事件。')]
    related = {m['signature_id'] for m in data['members']}
    content.extend([html.H6(f'经过此处的路线 · {len(related)}'), html.Div([
        dbc.Button(f'路线 {i}', id={'type': 'cp-graph-route', 'signature': p['signature_id'],
                                  'request': report.get('query_request_id') or '',
                                  'context': report.get('context') or ''},
                   n_clicks=0, size='sm', outline=True)
        for i, p in enumerate(report['paths'], 1) if p['signature_id'] in related
    ], className='rs-candidate-graph-routes')])
    return content
