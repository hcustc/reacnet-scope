"""Self-contained SVG figures for bounded Candidate hypergraph projections."""
from __future__ import annotations

import base64
from collections import defaultdict
import json
import math
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .candidate_hypergraph import CANDIDATE_HYPERGRAPH_SCHEMA_VERSION
from .path_search import NETWORK_ONLY_POLICY, candidate_formula
from .queries import smiles_to_svg


MAX_SVG_NODES = 500
SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)


def candidate_hypergraph_svg(graph: Mapping[str, Any]) -> str:
    """Draw the complete supplied projection, rejecting oversized figures.

    Structures are embedded, without remote assets. Exact identities, counts,
    query limits and source revision remain in the SVG's JSON metadata.
    """
    if graph.get('schema_version') != CANDIDATE_HYPERGRAPH_SCHEMA_VERSION:
        raise ValueError('不支持的候选超图版本。')
    nodes = [*graph['species'], *graph['hyperedges']]
    if len(nodes) > MAX_SVG_NODES:
        raise ValueError(f'图中超过 {MAX_SVG_NODES} 个节点，请选择较少路线后导出 SVG。')
    layers = defaultdict(list)
    for node in nodes:
        layers[node['layout_layer']].append(node)
    positions = {}
    rows = max((len(layer) for layer in layers.values()), default=1)
    body_height = max(160, rows * 150)
    width = max(720, (max(layers, default=0) + 1) * 210 + 100)
    height = body_height + 145
    for layer, items in layers.items():
        for row, node in enumerate(sorted(items, key=lambda n: n['layout_order'])):
            positions[node['id']] = (150 + layer * 210,
                90 + body_height / 2 + (row - (len(items) - 1) / 2) * 150)
    root = ET.Element(f'{{{SVG}}}svg', dict(viewBox=f'0 0 {width} {height}',
        width=str(width), height=str(height), role='img'))

    def add(parent, tag, attrs=None, text=None):
        element = ET.SubElement(parent, f'{{{SVG}}}{tag}',
                                {k: str(v) for k, v in (attrs or {}).items()})
        if text is not None:
            element.text = str(text)
        return element

    add(root, 'title', text='候选路线的有向反应网络')
    add(root, 'metadata', {'id': 'candidate-hypergraph'},
        json.dumps(graph, ensure_ascii=False, sort_keys=True))
    add(root, 'rect', dict(width='100%', height='100%', fill='white'))
    text_style = {'font-family': 'sans-serif', 'font-size': 13, 'fill': '#263346'}
    add(root, 'text', dict(text_style, x=30, y=30, **{'font-size': 18}),
        f"候选网络 · {len(graph['routes'])} 条展示路线 · {len(graph['hyperedges'])} 个反应类型")
    provenance = graph['provenance']
    query = provenance.get('query') or {}
    net = query.get('direction_view') == 'net'
    notes = ['净转化计数' if net else '原始反应类型计数', '各步证据独立；不表示连续分子历史']
    if provenance.get('carrier_policy') == NETWORK_ONLY_POLICY:
        notes.append('仅物种连通性；未核查原子转移')
    if provenance.get('query_complete') is False:
        notes.append('搜索未完成/已截断')
    if provenance.get('display_truncated'):
        notes.append('仅部分查询结果')
    if graph.get('browse_history_step_count'):
        notes.append(f"含 {graph['browse_history_step_count']} 步浏览历史")
    add(root, 'text', dict(text_style, x=30, y=55), ' · '.join(notes))
    defs = add(root, 'defs')
    for name, color in [('context', '#9aaabd'), ('carrier', '#bd6b16'), ('history', '#7b8798')]:
        marker = add(defs, 'marker', dict(id='arrow-' + name, viewBox='0 -4 8 8',
            refX=7, refY=0, markerWidth=8, markerHeight=8, orient='auto'))
        add(marker, 'path', dict(d='M0,-4 L8,0 L0,4 Z', fill=color))

    def boundary(point, other, species):
        dx, dy = other[0] - point[0], other[1] - point[1]
        if species:
            scale = 1 / max(abs(dx) / 84, abs(dy) / 56, 1e-9)
        else:
            scale = 20 / max(math.hypot(dx, dy), 1e-9)
        return point[0] + dx * scale, point[1] + dy * scale

    for reaction in graph['hyperedges']:
        for side in ('tail', 'head'):
            for term in reaction[side]:
                species_point, reaction_point = positions[term['species_id']], positions[reaction['id']]
                start = boundary(species_point, reaction_point, True)
                end = boundary(reaction_point, species_point, False)
                if side == 'head':
                    start, end = end, start
                history = all(m.get('historical') for m in term['members'])
                style = 'history' if history else 'carrier' if term['carrier_members'] else 'context'
                color = {'history': '#7b8798', 'carrier': '#bd6b16', 'context': '#9aaabd'}[style]
                # Separate opposite incidences when a species occurs on both
                # sides, or a displayed cycle returns to an earlier column.
                bend = 24 if side == 'tail' else -24
                mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + bend
                edge = add(root, 'path', dict(id=term['id'],
                    d=f'M {start[0]},{start[1]} Q {mx},{my} {end[0]},{end[1]}',
                    fill='none', stroke=color, **{'stroke-width': 2,
                    'marker-end': f'url(#arrow-{style})',
                    'stroke-dasharray': '6 4' if history else 'none'}))
                add(edge, 'title', text=f"{term['species']} ×{term['coefficient']} · {reaction['reaction_key']}")
                if term['coefficient'] > 1:
                    add(root, 'text', dict(text_style, x=mx, y=my - 5, **{'text-anchor': 'middle'}),
                        f"×{term['coefficient']}")

    for node in graph['species']:
        x, y = positions[node['id']]
        group = add(root, 'g', {'id': node['id']})
        add(group, 'title', text=node['species'])
        color = '#23887d' if node['species'] == query.get('start') else '#b07629' if node['species'] == query.get('target') else '#bac8d7'
        add(group, 'rect', dict(x=x - 84, y=y - 56, width=168, height=112,
                               rx=8, fill='white', stroke=color, **{'stroke-width': 2}))
        try:
            drawing = smiles_to_svg(node['species'], width=160, height=80)
        except (ValueError, RuntimeError):
            add(group, 'text', dict(text_style, x=x, y=y, **{'text-anchor': 'middle'}), '结构不可绘制')
        else:
            data = base64.b64encode(drawing.encode()).decode('ascii')
            add(group, 'image', dict(x=x - 80, y=y - 52, width=160, height=80,
                                    href='data:image/svg+xml;base64,' + data))
        add(group, 'text', dict(text_style, x=x, y=y + 45, **{'text-anchor': 'middle'}),
            f"S{node['display_number']} · {candidate_formula(node['species']) or node['species']}")

    for node in graph['hyperedges']:
        x, y = positions[node['id']]
        group = add(root, 'g', {'id': node['id']})
        add(group, 'title', text=node['reaction_key'])
        add(group, 'polygon', dict(points=f'{x},{y - 20} {x + 20},{y} {x},{y + 20} {x - 20},{y}',
                                   fill='#e9eef5', stroke='#9aaabd'))
        count = f"净 {node['net_count']}" if net else str(node['event_count'])
        add(group, 'text', dict(text_style, x=x, y=y + 45, **{'text-anchor': 'middle'}),
            f"R{node['display_number']} · {count} 次")
    if not nodes:
        add(root, 'text', dict(text_style, x=30, y=130), '本次查询没有返回可绘制的候选路线。')
    add(root, 'text', dict(text_style, x=30, y=height - 20),
        '箭头保留记录方向；×n 为计量数；橙色为主线载体，灰色为其他参与物，虚线为浏览历史。')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)
