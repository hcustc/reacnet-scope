"""A bounded display projection of returned candidates, never a new path search."""
from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlencode

import dash_bootstrap_components as dbc
import dash_cytoscape as cyto
from dash import dcc, html

from reacnet_scope import services as svc
from reacnet_scope.candidate_hypergraph import hypergraph_id as element_id


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
        'label': 'data(label)', 'font-size': 10, 'text-rotation': 'autorotate',
        'line-color': '#bdc9d6', 'target-arrow-color': '#bdc9d6', 'width': 1.5,
        'arrow-scale': 0.9,
    }},
    {'selector': '.anchor', 'style': {'border-width': 3, 'border-color': '#23887d'}},
    {'selector': '.target', 'style': {'border-width': 3, 'border-color': '#b07629'}},
    {'selector': 'node.browse-history', 'style': {
        'border-color': '#7b8798', 'background-color': '#7b8798',
    }},
    {'selector': 'edge.browse-history', 'style': {
        'line-color': '#7b8798', 'target-arrow-color': '#7b8798',
        'line-style': 'dashed', 'width': 2.5,
    }},
]


BROWSE_HISTORY_SIGNATURE = '__browse_history__'


def step_id(step):
    # One diamond represents the complete directed Reaction Type. Candidate
    # carriers remain separate in route membership and highlighted edges.
    return element_id('reaction', step['reaction_key'])


def with_browse_history(report, nav):
    """Prepend/append the selected browsing chain to current one-step results.

    The returned report is a display projection only. Current route signatures
    and step indexes remain bound to the current query, while earlier selected
    steps have no current step index and therefore cannot masquerade as current
    Step Evidence.
    """
    if not report or not nav:
        return report
    query = report.get('query') or {}
    mode = query.get('mode')
    if (mode not in {'explore', 'reverse'}
            or nav.get('mode') != mode
            or nav.get('context') != report.get('context')):
        return report
    history_species = list(nav.get('trail') or [])
    history_steps = list(nav.get('steps') or [])
    if not history_steps or len(history_species) != len(history_steps) + 1:
        return report
    frontier = history_species[-1] if mode == 'explore' else history_species[0]
    expected = query.get('start') if mode == 'explore' else query.get('target')
    if frontier != expected:
        return report
    current_paths = list(report.get('paths') or [])
    if any(not p.get('species') or
           (p['species'][0] if mode == 'explore' else p['species'][-1]) != frontier
           for p in current_paths):
        return report
    paths = []
    for path in current_paths:
        current_steps = list(path.get('steps') or [])
        current_species = list(path.get('species') or [])
        if mode == 'explore':
            species = history_species + current_species[1:]
            steps = history_steps + current_steps
            display_indexes = [None] * len(history_steps) + list(range(len(current_steps)))
        else:
            species = current_species[:-1] + history_species
            steps = current_steps + history_steps
            display_indexes = list(range(len(current_steps))) + [None] * len(history_steps)
        paths.append(dict(path, species=species, steps=steps, step_count=len(steps),
                          _display_step_indexes=display_indexes))
    if not paths:
        paths = [{
            'signature_id': BROWSE_HISTORY_SIGNATURE,
            'species': history_species,
            'steps': history_steps,
            'step_count': len(history_steps),
            '_display_step_indexes': [None] * len(history_steps),
        }]
    return dict(report, paths=paths, browse_history_step_count=len(history_steps))


def elements(report):
    """Adapt the shared core hypergraph to Cytoscape without new analysis."""
    if (report or {}).get('error'):
        return []
    projection = svc.build_candidate_hypergraph(report or {})
    nodes, edges = [], []
    query = (report or {}).get('query') or {}
    layers = defaultdict(list)
    for vertex in projection['species']:
        species = vertex['species']
        nodes.append({'data': dict(vertex, kind='species',
            label=f"S{vertex['display_number']} · {svc.candidate_formula(species) or species}",
            image='/api/structure.svg?' + urlencode({'smiles': species, 'width': 180, 'height': 110}))})
    for reaction in projection['hyperedges']:
        count_label = (f"净 {reaction['net_count']} 次" if query.get('direction_view') == 'net'
                       else f"类型 {reaction['event_count']} 次")
        nodes.append({'data': dict(
            {k: v for k, v in reaction.items() if k not in {'tail', 'head'}},
            kind='reaction', label=f"R{reaction['display_number']} · {count_label}")})
        for side, field in [('reactant', 'tail'), ('product', 'head')]:
            for term in reaction[field]:
                edges.append({'data': dict(
                    {k: v for k, v in term.items() if k not in {'coefficient', 'species_id'}},
                    source=term['species_id'] if side == 'reactant' else reaction['id'],
                    target=reaction['id'] if side == 'reactant' else term['species_id'],
                    kind='edge', reaction_id=reaction['id'], side=side,
                    multiplicity=term['coefficient'],
                    label=f"×{term['coefficient']}" if term['coefficient'] > 1 else '')})
    for node in nodes:
        layers[node['data']['layout_layer']].append(node)
    for layer, items in layers.items():
        spacing = 145 if layer % 2 == 0 else 100
        for row, node in enumerate(items):
            node['position'] = {'x': layer * 120, 'y': (row - (len(items) - 1) / 2) * spacing}
            species = node['data'].get('species')
            classes = []
            if species and species == query.get('start'):
                classes.append('anchor')
            elif species and species == query.get('target'):
                classes.append('target')
            node['classes'] = ' '.join(classes)
    for element in [*nodes, *edges]:
        data = element['data']
        if data['kind'] != 'species' and data['members'] and all(m.get('historical') for m in data['members']):
            data['historical'] = True
            element['classes'] = 'browse-history'
        data.update(context=(report or {}).get('context'),
                    query_request_id=(report or {}).get('query_request_id'))
    return [*nodes, *edges]


def participant_node(edge, species_node):
    data = edge['data']
    return {'data': dict(species_node['data'], id=element_id('participant', data['id']),
                         members=data['members'], carrier_members=[],
                         layout_context_for=data['reaction_id'], layout_side=data['side']),
            'position': dict(species_node['position']), 'classes': ''}


def local_participants(graph, visible):
    """Repeat context-only participants beside each reaction in the reading view.

    Copies retain the exact Species and its S label; they do not represent new
    Species or Molecule Instances. Reaction diamonds and stoichiometry stay intact.
    """
    nodes = {e['data']['id']: e for e in graph if e['data']['kind'] != 'edge'}
    edges, copies = [], []
    for element in graph:
        data = element['data']
        if data['kind'] != 'edge':
            continue
        edge = dict(element, data=dict(data))
        if not any(m['signature_id'] in visible for m in data['carrier_members']):
            end = 'source' if data['side'] == 'reactant' else 'target'
            copy = participant_node(element, nodes[data[end]])
            copies.append(copy)
            edge['data'][end] = copy['data']['id']
            # Cytoscape cannot change source/target with a data update. Give
            # this display edge its own identity so switching views removes
            # and adds the edge together with its local participant node.
            edge['data']['id'] = element_id('participant-edge', data['id'])
        edges.append(edge)
    connected = {e['data'][end] for e in edges for end in ('source', 'target')}
    return [e for e in nodes.values() if e['data']['id'] in connected] + copies + edges


def resolve_pick(report, picked):
    """Resolve only current report identities, never trust browser membership data."""
    if not report or not picked or any(picked.get(k) != report.get(k)
                                       for k in ('context', 'query_request_id')):
        return None
    graph = elements(report)
    data = next((e['data'] for e in graph if e['data']['id'] == picked.get('id')), None)
    if data:
        return data
    nodes = {e['data']['id']: e for e in graph if e['data']['kind'] == 'species'}
    for edge in graph:
        data = edge['data']
        if data['kind'] == 'edge' and element_id('participant-edge', data['id']) == picked.get('id'):
            # Resolve the display edge back to current authoritative evidence;
            # never accept route membership supplied by the browser.
            return data
        if data['kind'] == 'edge' and element_id('participant', data['id']) == picked.get('id'):
            end = 'source' if data['side'] == 'reactant' else 'target'
            return participant_node(edge, nodes[data[end]])['data']
    return None


def fitted_layout(graph, size=None, *, reading=False):
    """Choose the readable orientation of the existing bounded projection.

    Only coordinates change: shared reaction/species identities, participants,
    directions and route membership remain untouched. A broad one-step fan
    benefits from top-to-bottom flow; long routes usually read left-to-right.
    """
    nodes = {e['data']['id']: e for e in graph if 'position' in e}
    layers = defaultdict(list)
    neighbors = defaultdict(lambda: defaultdict(list))
    for nid, node in nodes.items():
        if node['data'].get('layout_context_for'):
            continue
        layers[node['data']['layout_layer']].append(nid)
    # Cytoscape writes rendered/dragged coordinates back to elements. Never
    # reinterpret those coordinates as semantic ranks when resetting/resizing.
    layers = {layer: sorted(ids, key=lambda nid: nodes[nid]['data']['layout_order'])
              for layer, ids in sorted(layers.items())}
    for element in graph:
        data = element['data']
        if data['kind'] != 'edge':
            continue
        source, target = data['source'], data['target']
        if source in nodes and target in nodes:
            neighbors[source]['out'].append(target)
            neighbors[target]['in'].append(source)

    def arrangement(vertical):
        positions = {}
        # Keep enough room for the structure card and its label at native scale.
        across, along = (174, 150) if vertical else (138, 210)
        for depth, ids in enumerate(layers.values()):
            for row, nid in enumerate(ids):
                positions[nid] = [depth * along, (row - (len(ids) - 1) / 2) * across]
        for ids in layers.values():
            reactions = [nid for nid in ids if nodes[nid]['data']['kind'] == 'reaction']
            desired = {}
            for nid in reactions:
                sides = [[i for i in ids if i in positions] for ids in neighbors[nid].values()]
                # Align diamonds with the more spread-out side of a fan.
                side = max(sides, key=lambda ids: sum(abs(positions[i][1]) for i in ids), default=[])
                desired[nid] = sum(positions[i][1] for i in side) / len(side) if side else positions[nid][1]
            previous = float('-inf')
            for nid in sorted(reactions, key=lambda nid: desired[nid]):
                previous = max(desired[nid], previous + (100 if vertical else 68))
                positions[nid][1] = previous
        context_rows = Counter()
        for nid, node in nodes.items():
            reaction = node['data'].get('layout_context_for')
            if not reaction:
                continue
            side = node['data']['layout_side']
            context_rows[reaction, side] += 1
            along_reaction, across_reaction = positions[reaction]
            direction = -1 if side == 'reactant' else 1
            positions[nid] = [along_reaction + direction * (70 if vertical else 65),
                              across_reaction + direction * (174 if vertical else 140) * context_rows[reaction, side]]
        return {nid: {'x': p[1] if vertical else p[0], 'y': p[0] if vertical else p[1]}
                for nid, p in positions.items()}

    width = max(1, (size or {}).get('width', 1000) - 64)
    height = max(1, (size or {}).get('height', 560) - 64)

    def fit(positions):
        if not positions:
            return 1
        xs = [p['x'] for p in positions.values()]
        ys = [p['y'] for p in positions.values()]
        return min(width / (max(xs) - min(xs) + 150),
                   height / (max(ys) - min(ys) + 128), 1.25)

    horizontal, vertical = arrangement(False), arrangement(True)
    use_vertical = ((size or {}).get('width', 1000) < 600 if reading
                    else fit(vertical) > fit(horizontal) * 1.12)
    positions = vertical if use_vertical else horizontal
    result = {'name': 'preset', 'fit': True, 'padding': 32, 'positions': positions}
    if reading and positions:
        result.update(reading_viewport(positions, size))
    return result


def reading_viewport(positions, size, focus=None, zoom=None):
    """Keep a long route readable and centre on one step instead of shrinking it."""
    width, height = (size or {}).get('width', 1000), (size or {}).get('height', 560)
    xs, ys = [p['x'] for p in positions.values()], [p['y'] for p in positions.values()]
    fit = min((width - 64) / (max(xs) - min(xs) + 150),
              (height - 64) / (max(ys) - min(ys) + 128))
    zoom = max(.9, min(1.15, fit)) if zoom is None else zoom
    centre = positions.get(focus)
    if not centre:
        centre = {'x': (min(xs) + max(xs)) / 2, 'y': (min(ys) + max(ys)) / 2}
        if fit < .9:
            # Start at the leading edge, leaving the viewport for the route
            # ahead rather than an empty half-canvas before its first node.
            if (max(xs) - min(xs)) / width >= (max(ys) - min(ys)) / height:
                centre['x'] = min(xs) + (width / 2 - 90) / zoom
            else:
                centre['y'] = min(ys) + (height / 2 - 80) / zoom
    return {'fit': False, 'zoom': zoom, 'pan': {
        'x': width / 2 - centre['x'] * zoom, 'y': height / 2 - centre['y'] * zoom}}


def stylesheet(graph, focus, selected, step_index):
    result = list(STYLESHEET)
    selected = set(selected or [])
    for element in graph:
        data = element['data']
        members = data['members']
        route_members = data.get('carrier_members', members)
        active = any(m['signature_id'] == focus for m in route_members)
        compared = any(m['signature_id'] in selected for m in route_members)
        context = data['kind'] != 'reaction' and any(
            m['signature_id'] == focus for m in members) and not active
        current_step = data['kind'] != 'species' and any(
            m['signature_id'] == focus and m['step_index'] == step_index for m in route_members)
        color = '#bd6b16' if current_step else '#6850bd' if active else '#248e9b'
        style = {}
        if data.get('historical'):
            if data['kind'] == 'edge':
                style = {'line-color': '#7b8798', 'target-arrow-color': '#7b8798',
                         'line-style': 'dashed', 'width': 2.5}
            else:
                style = {'border-color': '#7b8798', 'border-width': 3,
                         'background-color': '#7b8798'}
        elif active or compared:
            if data['kind'] == 'edge':
                style = {'line-color': color, 'target-arrow-color': color, 'width': 3.5 if active else 2.5}
            else:
                style = {'border-color': color, 'border-width': 3,
                         **({'background-color': color} if data['kind'] == 'reaction' else {})}
        elif context:
            style = ({'line-style': 'dotted', 'line-color': '#9aa9bb',
                      'target-arrow-color': '#9aa9bb'} if data['kind'] == 'edge'
                     else {'border-style': 'dotted', 'border-color': '#9aa9bb'})
        if style:
            result.append({'selector': f'#{data["id"]}', 'style': style})
    return result


def layout():
    return html.Div([
        html.Div([
            html.H6('反应路径图'),
            dcc.RadioItems(id='cp-graph-scope', options=[{'label': '路线阅读', 'value': 'selected'},
                {'label': '返回路线总览', 'value': 'all'}], value='selected', inline=True),
            html.Div(id='cp-graph-summary', role='status'),
            html.Div([
                html.Button('−', id='cp-graph-zoom-out', className='btn btn-outline-secondary btn-sm',
                           title='缩小路径图', **{'aria-label': '缩小路径图'}),
                html.Button('+', id='cp-graph-zoom-in', className='btn btn-outline-secondary btn-sm',
                           title='放大路径图', **{'aria-label': '放大路径图'}),
                dbc.Button('定位当前步骤', id='cp-graph-locate', size='sm', outline=True),
                dbc.Button('适应全部', id='cp-graph-reset', size='sm', outline=True),
            ], className='rs-candidate-graph-controls'),
        ], className='rs-candidate-graph-heading'),
        html.Div([
            cyto.Cytoscape(id='cp-graph', elements=[], layout={'name': 'preset', 'fit': True, 'padding': 45},
                           stylesheet=STYLESHEET, style={'width': '100%'},
                           minZoom=0.08, maxZoom=2.5, wheelSensitivity=0.2,
                           responsive=True, className='rs-candidate-graph-canvas'),
            html.Div(id='cp-graph-inspector', className='rs-candidate-graph-inspector'),
        ], className='rs-candidate-graph-body'),
        html.Div([
            html.Div([html.Span('● 当前路线', className='rs-candidate-legend-focus'),
                      html.Span('● 当前步骤', className='rs-candidate-legend-step'),
                      html.Span('● 勾选路线', className='rs-candidate-legend-compare'),
                      html.Span('··· 同一步其他参与物', className='text-muted'),
                      html.Span('┄ 已选浏览历史', className='rs-candidate-legend-history')],
                     className='rs-candidate-legend'),
            html.Small(id='cp-graph-view-note', className='text-muted'),
            html.Details([html.Summary('读图说明'), html.Small(
                '菱形是完整反应类型，不是过渡态；净转化视图只沿净计数为正的记录方向展开；全部观测视图保留两个记录方向。布局不是时间轴。紫线只表示所选路线，不代表主通道或高产率。灰色点线保留其他参与物；灰色虚线是浏览历史。阅读视图中重复的 S 编号是同一物种的显示副本，不表示新的分子实例。图中“净”是完整正逆反应的事件数之差，“类型”是该方向总事件数；均统计当前 RNG 数据全部观测区间。步骤面板保留正向、逆向、净计数和主线转移支持数。多步循环不表示同一分子实际返回。')]),
        ], className='rs-candidate-graph-footer'),
        dcc.Store(id='cp-graph-pick'),
        dcc.Store(id='cp-graph-size'),
        dcc.Store(id='cp-graph-layout-state'),
        dcc.Store(id='cp-graph-base-style', data=STYLESHEET),
    ], className='rs-candidate-graph-panel')


def inspector(report, picked, structure):
    data = resolve_pick(report, picked)
    if not data or data['kind'] != 'species':
        return ''
    content = [html.H6('物种'), structure(data['species'])]
    related = {m['signature_id'] for m in data['members']}
    carried = {m['signature_id'] for m in data['carrier_members']}
    content.extend([html.H6(f'涉及此物种的路线 · {len(related)}'), html.Div([
        dbc.Button(f"路线 {i} · {'主线' if p['signature_id'] in carried else '反应参与'}",
                   id={'type': 'cp-graph-route', 'signature': p['signature_id'],
                                  'request': report.get('query_request_id') or '',
                                  'context': report.get('context') or ''},
                   n_clicks=0, size='sm', outline=True)
        for i, p in enumerate(report['paths'], 1) if p['signature_id'] in related
    ], className='rs-candidate-graph-routes')])
    return content
