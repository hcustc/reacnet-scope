"""Candidate-path task inside the reaction workspace.

Requests and results carry dataset and query identities; late completions may
never repopulate a new dataset or replace a more recent search.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode
from uuid import uuid4

from . import ui_components as ui
from dash import ALL, Input, MATCH, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from reacnet_scope import services as svc
from . import candidate_graph


def context_key(store):
    store = store or {}
    return hashlib.sha256(json.dumps({k: store.get(k) for k in
        ('dataset_id', 'source_revision', 'artifacts')}, sort_keys=True).encode()).hexdigest()


def accept_result(raw, request, store):
    return bool(raw and request and raw.get('request_id') == request.get('request_id')
                and raw.get('context') == context_key(store) == request.get('context'))


def structure(species):
    return html.Div([
        html.Img(src='/api/structure.svg?' + urlencode({'smiles': species, 'width': 180, 'height': 110}),
                 alt=species),
        html.Strong(svc.candidate_formula(species) or species),
        html.Details([html.Summary('精确 RNG 标签'), html.Code(species)]),
    ], className='rs-candidate-structure')


def species_option(species):
    """Use an exact Species identity already selected in this dataset."""
    return {'label': html.Span([
        html.Img(src='/api/structure.svg?' + urlencode(
            {'smiles': species, 'width': 112, 'height': 58}), alt=species),
        html.Span(species)], className='rs-candidate-option'),
        'value': species, 'search': species + ' ' + (svc.candidate_formula(species) or '')}


CONTINUITY_LABELS = {'not_evaluated': '未检查', 'chain_found': '找到连续历史',
                     'not_observed_within_evidence': '未找到连续历史', 'inconclusive': '证据不足或检查截断'}


def bond_graph(participants, bonds):
    """Show exact event bond orders without guessing atom-element mappings."""
    import plotly.graph_objects as go
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    atoms = sorted({a for p in participants for a in p['atom_ids']})
    if len(atoms) > 100:
        return html.P('本侧超过 100 个原子；完整键记录可在事件工作区查看。')
    if not atoms:
        return html.P('本侧没有可显示的原子记录。')
    parsed = [tuple(map(int, b.split('-'))) for b in bonds]
    # Dummy atoms provide topology-only depiction, without guessing elements,
    # sanitizing valence, or deriving chemistry from representative species.
    molecule = Chem.RWMol()
    indices = {a: molecule.AddAtom(Chem.Atom(0)) for a in atoms}
    for a, b, _ in parsed:
        molecule.AddBond(indices[a], indices[b], Chem.BondType.SINGLE)
    rdDepictor.Compute2DCoords(molecule)
    coords = molecule.GetConformer()
    pos = {a: (coords.GetAtomPosition(i).x, coords.GetAtomPosition(i).y)
           for a, i in indices.items()}
    fig = go.Figure()
    for a, b, order in parsed:
        fig.add_trace(go.Scatter(x=[pos[a][0], pos[b][0]], y=[pos[a][1], pos[b][1]],
                                mode='lines', line={'width': 1 + order, 'color': '#52647d'},
                                hovertemplate=f'{a}–{b}：RNG 键级 {order}<extra></extra>', showlegend=False))
        fig.add_annotation(x=(pos[a][0]+pos[b][0])/2, y=(pos[a][1]+pos[b][1])/2,
                           text=str(order), showarrow=False, bgcolor='white')
    fig.add_trace(go.Scatter(x=[pos[a][0] for a in atoms], y=[pos[a][1] for a in atoms],
                            mode='markers+text', text=[str(a) for a in atoms], textposition='top center',
                            marker={'size': 10}, hoverinfo='text', showlegend=False))
    ranges = []
    for axis in (0, 1):
        lo, hi = min(p[axis] for p in pos.values()), max(p[axis] for p in pos.values())
        padding = max(.8, (hi - lo) * .15)
        ranges.append([lo - padding, hi + padding])
    fig.update_layout(height=320, margin=dict(l=20,r=20,t=35,b=20),
                      plot_bgcolor='white', paper_bgcolor='white', dragmode='pan',
                      meta={'home_ranges': ranges},
                      xaxis={'visible': False, 'range': ranges[0], 'constrain': 'domain'},
                      yaxis={'visible': False, 'range': ranges[1], 'scaleanchor': 'x',
                             'constrain': 'domain'})
    view_id = uuid4().hex
    return html.Div([
        html.Div([dbc.Button('还原视图', id={'type': 'bond-view-reset', 'index': view_id},
                             size='sm', outline=True),
                  html.Small(' 拖动平移 · 工具栏缩放 · 双击还原', className='text-muted')]),
        dcc.Graph(id={'type': 'bond-view', 'index': view_id}, figure=fig,
                  config={'displayModeBar': True, 'displaylogo': False, 'scrollZoom': False,
                          'doubleClick': 'reset',
                          'modeBarButtonsToRemove': ['select2d', 'lasso2d']})])


def actual_event_view(row):
    evidence = row.get('candidate_evidence') or {}
    if not evidence:
        return ''
    if not evidence.get('molecular_evidence_available'):
        return html.P('缺少分子证据，无法显示具体事件键结构或产物后续去向。')
    history = evidence.get('history') or {}
    kinds = {'exact': '精确键级也复原', 'topology': '连接关系复原（键级不同）', 'none': '未发现直接反向返回', 'unknown': '未确认'}
    followup = []
    for item in evidence['product_followup']:
        count = item['observed_frame_intervals']
        text = (f"到首次后续消耗相隔 {count} 个采样间隔" if count is not None else '后续持续时间未确认')
        followup.append(html.Li([html.Code(item['species']), '：' + text,
                                '；事件 ' + ', '.join(item['next_event_ids']) if item['next_event_ids'] else '']))
    return html.Div([html.Strong(f"具体事件 {row['event_id']}：实际键与后续去向"),
        html.P('节点为原子 ID；边上数字为该事件 RNG 键级。此图不使用代表标签推测元素或键级。'),
        dbc.Row([dbc.Col([html.Strong('反应前'), bond_graph(evidence['reactants'], evidence['reactant_bonds'])], md=6),
                 dbc.Col([html.Strong('反应后'), bond_graph(evidence['products'], evidence['product_bonds'])], md=6)], className='g-3'),
        html.P(f"后续返回：{kinds[history.get('return_kind', 'unknown')]}；"
               f"前序返回：{kinds[history.get('prior_kind', 'unknown')]}。"),
        html.Ul(followup),
        html.P(evidence['duration_note'])])


def _picker(side, label):
    return html.Div([
        dbc.Label(label, html_for=f'cp-{side}'),
        html.Div([dcc.Input(id=f'cp-{side}-query', placeholder='分子式或精确 RNG SMILES', debounce=True),
                  dbc.Button('检索', id=f'cp-{side}-find', size='sm'),
                  dbc.Button('使用当前物种', id=f'cp-{side}-current', size='sm', outline=True)],
                 className='rs-query-row'),
        dcc.Dropdown(id=f'cp-{side}', options=[], placeholder='先检索，再明确选择一个结构', clearable=True, optionHeight=72),
        html.Div(id=f'cp-{side}-message', role='status'),
        html.Details([html.Summary('放大结构与精确标签'),
                      html.Div(id=f'cp-{side}-preview')], className='rs-candidate-picker-preview'),
    ], className='rs-candidate-picker')


def layout():
    return html.Div([
        html.Div(id='cp-capability', role='status'),
        dbc.Button('前往RNG 数据准备', id='cp-prepare', size='sm', outline=True, style={'display': 'none'}),
        html.Details([
        html.Summary('搜索条件', id='cp-search-heading'),
        dcc.RadioItems(id='cp-mode', options=[{'label': '从起点看后续', 'value': 'explore'},
                       {'label': '从终点查前驱', 'value': 'reverse'},
                       {'label': '起点到终点', 'value': 'target'}], value='target', inline=True),
        html.Div([html.Div(_picker('start', '起始结构'), id='cp-start-wrap'),
                  html.Div(_picker('target', '终点结构'), id='cp-target-wrap')],
                 className='rs-candidate-pickers'),
        html.Div([html.Div([dbc.Label('最多展示'), dcc.Input(id='cp-limit', type='number', value=20, min=1, max=100)], className='rs-candidate-number'),
                  dbc.Button('搜索路径', id='cp-search', color='primary'),
                  dbc.Button('取消搜索', id='cp-cancel', outline=True, disabled=True)], className='rs-query-row'),
        html.Details([
        html.Summary(id='cp-advanced-heading'),
        dcc.RadioItems(id='cp-quality-view', options=[{'label': '折叠短暂往返', 'value': 'persistent'},
                       {'label': '原始事件视图', 'value': 'raw'}], value='persistent', inline=True),
        html.Div([dbc.Label('往返窗口（分析帧间隔）'), dcc.Input(id='cp-return-window', type='number', value=3, min=1, max=100),
                  dcc.RadioItems(id='cp-return-basis', options=[{'label': '同原子连接关系返回', 'value': 'topology'},
                                 {'label': '精确键级也返回', 'value': 'exact'}], value='topology', inline=True)], className='rs-candidate-advanced-row'),
        html.P('仅折叠有完整原子往返证据的事件；短寿命或低频本身不会被删除。可切换原始视图比较。'),
        html.Div([dbc.Label('可选最大步数（仅双端搜索）'),
                  dcc.Input(id='cp-depth', type='number', value=None, min=1, max=100,
                            placeholder='不限步数')], className='rs-candidate-number'),
        html.Div([dbc.Label('最多展开'),
                      dcc.Input(id='cp-expansions', type='number', value=2000, min=1, max=20000),
                      dbc.Label('最多待搜索分支'), dcc.Input(id='cp-frontier', type='number', value=5000, min=1, max=20000),
                      dbc.Label('最多检查路线前缀'), dcc.Input(id='cp-prefixes', type='number', value=10000, min=1, max=100000),
                      dbc.Label('最多检查候选'), dcc.Input(id='cp-examined', type='number', value=2000, min=1, max=20000),
                      dbc.Label('时间预算（秒）'), dcc.Input(id='cp-seconds', type='number', value=5, min=1, max=30)]),
        ], className='rs-candidate-advanced'),
        ], id='cp-search-settings', open=True, className='rs-candidate-search-settings'),
        html.Div(id='cp-message', role='status', **{'aria-live': 'polite'}),
        html.Div(id='cp-trail-view', className='rs-candidate-result-limits'),
        html.Div(id='cp-result-summary', role='status'),
        html.Div([
        html.Div([
        html.Div([
            dbc.Label('查看路线', html_for='cp-focus'),
            dbc.Button('上一条', id='cp-route-prev', size='sm', outline=True),
            dcc.Dropdown(id='cp-focus', options=[], clearable=False, placeholder='搜索后选择路线'),
            dbc.Button('下一条', id='cp-route-next', size='sm', outline=True),
            dbc.Button('沿选中路线继续一步', id='cp-continue', size='sm', outline=True),
            dbc.Button('上一页分支', id='cp-page-prev', size='sm', outline=True, disabled=True),
            dbc.Button('下一页分支', id='cp-page-next', size='sm', outline=True, disabled=True),
        ], className='rs-candidate-route-nav'),
        html.Div([dbc.Button('导出 JSON', id='cp-json', size='sm', outline=True, disabled=True),
                  dbc.Button('导出逐步 CSV', id='cp-csv', size='sm', outline=True, disabled=True),
                  dcc.Download(id='cp-download')], className='rs-query-row'),
        ], className='rs-candidate-results-toolbar'),
        candidate_graph.layout(),
        html.Details([html.Summary('路线表格与多路线比较'),
        ui.result_grid('cp-routes',
                    definitions=[
            {'name': '路线', 'id': 'route'}, {'name': '步数', 'id': 'steps'},
            {'name': '物种序列（分子式仅作显示）', 'id': 'species'},
            {'name': '各步事件数', 'id': 'counts'}, {'name': '其中往返事件', 'id': 'returns'}, {'name': '连续历史', 'id': 'continuity'}],
                    selection='multi',
                    page_size=10),
        html.P('勾选 2–3 条路线比较；事件数属于各步骤，不是整条路线的发生次数。', className='text-muted'),
        html.Div(id='cp-comparison')], className='rs-candidate-table-details'),
        html.Details([
            html.Summary('步骤证据与具体实例'),
            html.Div([
                html.Div([
                    html.Strong('反应步骤'),
                ]),
                dcc.Dropdown(id='cp-step', options=[], clearable=False,
                             placeholder='先选择一条路线和反应步骤'),
            ], className='rs-candidate-evidence-heading'),
            html.Div(id='cp-step-detail', className='rs-candidate-step-overview'),
            html.Details([
            html.Summary('全部支持事件与分页'),
            html.Div([
                html.Div([
                    dbc.Button('上一页', id='cp-prev', size='sm', outline=True),
                    dbc.Button('下一页', id='cp-next', size='sm', outline=True),
                ], className='rs-query-row'),
                html.Div(id='cp-event-status', role='status', className='rs-candidate-event-status'),
            ], className='rs-candidate-event-toolbar'),
            ui.result_grid('cp-events',
                    definitions=[{'name': '实例 ID', 'id': 'event_id'},
                {'name': 'Transition', 'id': 'timestep_index'},
                {'name': '分子关联', 'id': 'association_status'},
                {'name': '共享原子数', 'id': 'shared_atom_count'}],
                    selection=None,
                    sortable=False,
                    height=240),
            ], className='rs-candidate-event-list'),
            html.Div([
                dbc.Button('上一个实例', id='cp-instance-prev', size='sm', outline=True, disabled=True),
                dcc.Dropdown(id='cp-actual-event', options=[], clearable=False,
                             placeholder='选择一个具体反应实例'),
                dbc.Button('下一个实例', id='cp-instance-next', size='sm', outline=True, disabled=True),
                html.Span(id='cp-instance-position', className='rs-candidate-instance-position'),
            ], className='rs-candidate-instance-nav'),
            html.Div([
                dbc.Button('查看前后结构与轨迹', id='cp-open-events', size='sm', color='primary', disabled=True),
                dbc.Button('追踪参与分子的变化', id='cp-track-instance', size='sm', outline=True, disabled=True),
            ], className='rs-query-row rs-candidate-instance-actions'),
            html.Details([html.Summary('所选实例的实际键与后续去向'),
                          html.Div(id='cp-actual-structure', className='rs-candidate-instance-detail')]),
            html.Details([
                html.Summary('可选：查找整条路线的连续发生实例'),
                html.P('这项检查不改变候选路线身份。未找到、证据不足或尚未检查都不表示路线不成立。',
                       className='text-muted'),
                dbc.Button('开始检查', id='cp-check', size='sm', outline=True),
                dbc.Button('取消检查', id='cp-check-cancel', size='sm', outline=True, disabled=True),
                html.Div(id='cp-check-progress', role='status'),
                html.Div(id='cp-check-result', role='status'),
            ], className='rs-candidate-continuity-details'),
        ], id='cp-evidence', className='rs-candidate-evidence-workspace'),
        html.Details([html.Summary('当前路线的逐步结构'), html.Div(id='cp-detail')]),
        ], id='cp-results', style={'display': 'none'}),
        *[dcc.Store(id=name) for name in ['cp-request', 'cp-raw', 'cp-report', 'cp-event-page', 'cp-context', 'cp-nav-anchor',
                                        'cp-validation-request', 'cp-validation-raw']],
    ], className='rs-card rs-candidate-workbench')


def route_summary(report):
    if not report:
        return ''
    if report.get('error'):
        return dbc.Alert(report['error'], color='warning')
    count = len(report['paths'])
    mode = (report.get('query') or {}).get('mode')
    reachability = report.get('reachability_status')
    if mode == 'target' and reachability == 'found':
        status = f'已确认起点到终点存在候选连接；展示 {count} 条候选路线。'
    elif mode == 'target' and reachability == 'not_found':
        horizon = (report.get('query') or {}).get('max_steps')
        status = (f'已查完当前证据视图中不超过 {horizon} 步的连接，未找到候选路线。'
                  if horizon and report.get('horizon_limited') else
                  '已查完当前证据视图中的可达连接，未找到起点到终点的候选路线。')
    else:
        status = f'找到 {count} 条候选路线。' if count else '本次约束内未找到候选路线。'
    if not report['query_complete']:
        labels = {'time_budget': '时间预算', 'result_limit': '返回条数上限',
                  'expansion_budget': '展开预算', 'adjacency_budget': '局部邻接预算',
                  'frontier_budget': '待搜索分支上限', 'target_probe_budget': '目标连接检查预算',
                  'path_enumeration_budget': '路线枚举预算',
                  'candidate_exam_budget': '候选检查预算', 'target_match_limit': '目标连接读取预算'}
        prefix = ('已确认存在候选连接，但其他路线未查全；达到' if reachability == 'found' else
                  '搜索未完成，尚未找到候选路线；达到' if not count else
                  f'已返回 {count} 条候选路线，搜索未完成；达到')
        status = prefix + '、'.join(labels.get(r, r) for r in report['truncation_reasons']) + '。'
    elif report.get('display_truncated'):
        status += ' 展示条数已满；其余已检查路线未显示。'
    fields = (report.get('processing') or {}).get('fields') or {}
    miso = (fields.get('miso') or {}).get('value')
    identity = ('miso=1：代表标签连接，未核查逐帧精确键级。' if miso == 1 else
                'miso 未知：按 RNG 标签连接，不从结构外观推断。' if miso is None else f'按 RNG 标签连接（miso={miso}）。')
    transfer = ('主线规则：每个事件中，只沿继承当前反应物原子数最多的产物继续（并列均保留）。'
                if report.get('carrier_policy') == 'event_local_dominant_atom_descendant'
                else '当前仅有物种名称连通性，不得解读为物质转化路线。')
    query = report['query']
    quality = (f"往返折叠视图：窗口 {query.get('return_window_frames', 3)} 个分析帧间隔；"
               '仅全部支持事件均满足返回规则的步骤不参与搜索。' if query.get('quality_view') == 'persistent'
               else '原始事件视图：包含短暂往返；表中单列其事件数。')
    return html.Div([
        html.Div(status, className='rs-candidate-result-status' +
                 (' rs-candidate-search-warning' if not report['query_complete'] else '')),
        html.Div([
            html.Span('各步有独立事件支持；连续历史需另查。展示顺序不代表主通道、产率或机理可信度。'),
            html.Span(identity),
            html.Span(transfer, className='rs-candidate-search-warning')
            if report.get('carrier_policy') != 'event_local_dominant_atom_descendant' else None,
        ], className='rs-candidate-result-limits') if count else None,
        html.Details([html.Summary('搜索依据'), html.Div(quality), html.Div(transfer)],
                     className='rs-candidate-result-rules'),
    ], className='rs-candidate-result-note')


def comparison(paths):
    if len(paths) < 2:
        return ''
    if len(paths) > 3:
        return dbc.Alert('请最多选择 3 条路线比较。', color='warning')
    # Exact reaction AND carried-species identity define a shared step.
    keys = list(dict.fromkeys((s['reaction_key'], s['carried_from'], s['carried_to'])
                             for p in paths for s in p['steps']))
    rows = []
    for key, carried_from, carried_to in keys:
        cells = []
        for p in paths:
            positions = [str(i) for i, s in enumerate(p['steps'], 1)
                         if (s['reaction_key'], s['carried_from'], s['carried_to']) == (key, carried_from, carried_to)]
            cells.append(html.Td('第 ' + ','.join(positions) + ' 步' if positions else '—'))
        shared = all(cell.children != '—' for cell in cells)
        rows.append(html.Tr([html.Td('共同' if shared else '分支'), html.Td([
            html.Code(key), html.Div(f'连接载体：{carried_from} → {carried_to}')]), *cells]))
    return html.Div([html.H6('路线对照：精确反应及连接载体相同才算共同步骤'),
        dbc.Table([html.Thead(html.Tr([html.Th('类型'), html.Th('完整反应式'),
                   *[html.Th(f"路线 {p['display_rank']}") for p in paths]])), html.Tbody(rows)],
                  bordered=True, responsive=True)], className='rs-candidate-compare')


def register_callbacks(app):
    @app.callback(Output('cp-advanced-heading', 'children'),
                  Input('cp-quality-view', 'value'), Input('cp-return-window', 'value'),
                  Input('cp-return-basis', 'value'))
    def advanced_heading(view, window, basis):
        if view == 'raw':
            return '原始事件视图 · 高级选项'
        basis_label = '精确键级返回' if basis == 'exact' else '连接关系返回'
        return f'折叠短暂往返 · {window if window is not None else "待填写"} 个分析帧间隔 · {basis_label}（高级选项）'

    @app.callback(Output('cp-results', 'style'), Output('cp-search-settings', 'open'),
                  Output('cp-search-heading', 'children'),
                  Input('cp-report', 'data'), Input('app-store', 'data'))
    def presentation(report, store):
        if not report or report.get('context') != context_key(store) or report.get('error'):
            return {'display': 'none'}, True, '搜索条件'
        query = report.get('query') or {}
        start = svc.candidate_formula(query['start']) if query.get('start') else '候选前驱'
        target = svc.candidate_formula(query['target']) if query.get('target') else '后续物种'
        horizon = f" · 最多 {query['max_steps']} 步" if query.get('max_steps') and query.get('mode') == 'target' else ''
        heading = f"搜索条件 · {start} → {target}{horizon}（展开修改）"
        return {}, not bool(report.get('paths')), heading

    @app.callback(Output('cp-evidence', 'open'),
                  Input('cp-graph-pick', 'data'), Input('cp-request', 'data'), Input('app-store', 'data'),
                  State('cp-report', 'data'))
    def reveal_evidence(picked, _request, store, report):
        if ctx.triggered_id != 'cp-graph-pick' or not report or report.get('context') != context_key(store):
            return False
        data = candidate_graph.resolve_pick(report, picked)
        return True if data and data['kind'] in {'reaction', 'edge'} else no_update

    @app.callback(Output('cp-graph', 'elements'), Output('cp-graph-summary', 'children'),
                  Input('cp-report', 'data'), Input('app-store', 'data'),
                  Input('cp-graph-scope', 'value'), Input('cp-routes', 'selectedRows'),
                  Input('cp-focus', 'value'))
    def graph(report, store, scope, selected, focus):
        if not report or report.get('context') != context_key(store):
            return [], '搜索后显示路径合并图。'
        # Do not reset dragged positions or zoom just to highlight another route.
        if ctx.triggered_id in {'cp-focus', 'cp-routes'} and scope == 'all':
            return no_update, no_update
        paths = report.get('paths') or []
        if scope == 'selected':
            selected = set(ui.selected_ids(selected)) | {focus}
            paths = [p for p in paths if p['signature_id'] in selected]
        visible = {p['signature_id'] for p in paths}
        graph_elements = [e for e in candidate_graph.elements(report)
                          if any(m['signature_id'] in visible for m in e['data']['members'])]
        if scope == 'selected':
            # Reposition the visible subset so hidden branches do not leave
            # widely separated nodes that shrink the selected route on fit.
            positions = {e['data']['id']: e['position']
                         for e in candidate_graph.elements(dict(report, paths=paths)) if 'position' in e}
            for element in graph_elements:
                if element['data']['id'] in positions:
                    element['position'] = positions[element['data']['id']]
        species_count = sum(e['data']['kind'] == 'species' for e in graph_elements)
        step_count = sum(e['data']['kind'] == 'reaction' for e in graph_elements)
        note = '搜索截断；' if not report.get('query_complete', True) else ''
        if report.get('display_truncated'):
            note += '仅展示部分已检查路线。'
        return graph_elements, f'{len(paths)} 条路线 · {species_count} 个物种 · {step_count} 个反应步骤。{note}'

    @app.callback(Output('cp-graph', 'stylesheet'), Input('cp-graph', 'elements'),
                  Input('cp-focus', 'value'), Input('cp-routes', 'selectedRows'), Input('cp-step', 'value'))
    def graph_highlight(graph_elements, focus, selected, step_index):
        return candidate_graph.stylesheet(graph_elements or [], focus, ui.selected_ids(selected), step_index)

    @app.callback(Output('cp-graph', 'layout'), Input('cp-graph-reset', 'n_clicks'),
                  Input('cp-graph', 'elements'), Input('cp-graph-size', 'data'))
    def graph_layout(clicks, _elements, size):
        # The revision forces Cytoscape to rerun the same preset and fit on reset.
        return {'name': 'preset', 'fit': True, 'padding': 45, 'revision': clicks or 0,
                'viewport': size,
                'positions': {e['data']['id']: e['position'] for e in (_elements or []) if 'position' in e}}

    @app.callback(Output('cp-graph-pick', 'data'), Input('cp-graph', 'tapNodeData'),
                  Input('cp-graph', 'tapEdgeData'), Input('cp-report', 'data'), Input('app-store', 'data'),
                  State('cp-graph-pick', 'data'))
    def graph_pick(node, edge, report, store, previous):
        if not report or report.get('context') != context_key(store):
            return None
        picked = (edge if ctx.triggered_prop_ids.get('cp-graph.tapEdgeData') else node
                  ) if ctx.triggered_id == 'cp-graph' else previous
        data = candidate_graph.resolve_pick(report, picked)
        return {k: data[k] for k in ('id', 'context', 'query_request_id')} if data else None

    @app.callback(Output('cp-graph-inspector', 'children'), Input('cp-graph-pick', 'data'),
                  Input('cp-report', 'data'), Input('app-store', 'data'))
    def graph_inspector(picked, report, store):
        if not report or report.get('context') != context_key(store):
            return ''
        return candidate_graph.inspector(report, picked, structure)

    @app.callback(Output('cp-focus', 'value', allow_duplicate=True),
                  Input('cp-graph-pick', 'data'), Input('cp-route-prev', 'n_clicks'), Input('cp-route-next', 'n_clicks'),
                  Input({'type': 'cp-graph-route', 'signature': ALL, 'request': ALL, 'context': ALL}, 'n_clicks'),
                  State('cp-report', 'data'), State('app-store', 'data'), State('cp-focus', 'value'),
                  prevent_initial_call=True)
    def graph_navigate(picked, _prev, _next, clicks, report, store, focus):
        if not report or report.get('context') != context_key(store):
            raise PreventUpdate
        paths = report.get('paths') or []
        signatures = [p['signature_id'] for p in paths]
        if not signatures:
            raise PreventUpdate
        trigger = ctx.triggered_id
        if isinstance(trigger, dict):
            if (not any(clicks or []) or trigger.get('signature') not in signatures
                    or trigger.get('request') != (report.get('query_request_id') or '')
                    or trigger.get('context') != (report.get('context') or '')):
                raise PreventUpdate
            return trigger['signature']
        if trigger in {'cp-route-prev', 'cp-route-next'}:
            position = signatures.index(focus) if focus in signatures else 0
            return signatures[(position + (1 if trigger == 'cp-route-next' else -1)) % len(signatures)]
        data = candidate_graph.resolve_pick(report, picked)
        if not data:
            raise PreventUpdate
        related = [m['signature_id'] for m in data['members']]
        return focus if focus in related else related[0]

    app.clientside_callback(
        """function(n, figure) {
            if (!n || !figure) return window.dash_clientside.no_update;
            const result = JSON.parse(JSON.stringify(figure));
            const ranges = result.layout.meta.home_ranges;
            result.layout.xaxis.range = ranges[0];
            result.layout.yaxis.range = ranges[1];
            result.layout.xaxis.autorange = false;
            result.layout.yaxis.autorange = false;
            result.layout.uirevision = 'reset-' + n;
            return result;
        }""",
        Output({'type': 'bond-view', 'index': MATCH}, 'figure'),
        Input({'type': 'bond-view-reset', 'index': MATCH}, 'n_clicks'),
        State({'type': 'bond-view', 'index': MATCH}, 'figure'),
        prevent_initial_call=True,
    )
    @app.callback(Output('cp-context', 'data'), Input('app-store', 'data'), State('cp-context', 'data'))
    def context(store, previous):
        current = context_key(store)
        return current if current != previous else no_update

    @app.callback(Output('cp-capability', 'children'), Output('cp-prepare', 'style'), Input('app-store', 'data'))
    def capability(store):
        status = svc.candidate_search_status((store or {}).get('artifacts') or {})
        if status['available'] and not status.get('degraded'):
            return '', {'display': 'none'}
        return dbc.Alert(status['message'], color='warning'), {}

    @app.callback(Output('cp-direct-panel', 'style'), Output('cp-path-panel', 'style'), Input('reaction-task-tabs', 'value'))
    def task(tab):
        return ({'display': 'none'}, {}) if tab == 'candidates' else ({}, {'display': 'none'})

    @app.callback(Output('species-candidate-menu', 'disabled'),
                  Output('cp-from-species', 'disabled'), Output('cp-to-species', 'disabled'),
                  Input('app-store', 'data'))
    def shortcuts(store):
        disabled = not bool((store or {}).get('selected_smiles'))
        return disabled, disabled, disabled

    @app.callback(Output('reaction-task-tabs', 'value'), Output('cp-mode', 'value'),
                  Input('cp-from-species', 'n_clicks'), Input('cp-to-species', 'n_clicks'), prevent_initial_call=True)
    def open_shortcut(_from, _to):
        return 'candidates', 'explore' if ctx.triggered_id == 'cp-from-species' else 'reverse'

    for side in ('start', 'target'):
        def register_picker(side):
            @app.callback(Output(f'cp-{side}', 'options'), Output(f'cp-{side}', 'value'),
                          Output(f'cp-{side}-message', 'children'),
                          Input(f'cp-{side}-find', 'n_clicks'), Input(f'cp-{side}-current', 'n_clicks'),
                          Input('cp-context', 'data'), Input('cp-from-species', 'n_clicks'), Input('cp-to-species', 'n_clicks'),
                          Input('cp-nav-anchor', 'data'),
                          State('app-store', 'data'), State(f'cp-{side}-query', 'value'),
                          State(f'cp-{side}', 'options'), State(f'cp-{side}', 'value'),
                          prevent_initial_call=True)
            def find(_find, _current, _context, _from, _to, nav, store, query, options, value):
                if ctx.triggered_id == 'cp-context':
                    # Dataset changes must never retain a previous selection.
                    return [], None, ''
                if ctx.triggered_id == 'cp-nav-anchor':
                    if not nav or nav.get('side') != side or nav.get('context') != context_key(store):
                        return no_update, no_update, no_update
                    species = nav['species']
                    return [species_option(species)], species, '已沿选中路线带入精确结构。'
                if ctx.triggered_id in {'cp-from-species', 'cp-to-species'} and ctx.triggered_id != ('cp-from-species' if side == 'start' else 'cp-to-species'):
                    return no_update, no_update, no_update
                current = ctx.triggered_id in {f'cp-{side}-current', 'cp-from-species', 'cp-to-species'}
                if current:
                    species = str((store or {}).get('selected_smiles') or '').strip()
                    if not species:
                        return [], None, '请先在物种检索中选择一个精确结构。'
                    return [species_option(species)], species, '已带入当前选中的精确物种。'
                text = query
                try:
                    result = svc.search_candidate_species((store or {}).get('artifacts') or {}, text or '')
                except svc.ServiceError as exc:
                    return [], None, exc.message
                options = [species_option(r['species']) for r in result['rows']]
                selected = options[0]['value'] if len(options) == 1 else None
                return options, selected, ('显示前 100 个匹配，请用精确标签缩小范围。' if result['has_more'] else
                    f'匹配 {len(options)} 个精确结构。' + ('请选择一个。' if len(options) > 1 else ''))

            @app.callback(Output(f'cp-{side}-preview', 'children'), Input(f'cp-{side}', 'value'))
            def preview(value):
                return structure(value) if value else ''
        register_picker(side)

    @app.callback(Output('cp-start-wrap', 'style'), Output('cp-target-wrap', 'style'),
                  Output('cp-continue', 'style'), Output('cp-page-prev', 'style'),
                  Output('cp-page-next', 'style'), Input('cp-mode', 'value'))
    def target_mode(mode):
        return ({'display': 'none'} if mode == 'reverse' else {},
                {'display': 'none'} if mode == 'explore' else {},
                {'display': 'none'} if mode == 'target' else {},
                {'display': 'none'} if mode == 'target' else {},
                {'display': 'none'} if mode == 'target' else {})

    @app.callback(Output('cp-page-prev', 'disabled'), Output('cp-page-next', 'disabled'),
                  Input('cp-report', 'data'), Input('app-store', 'data'))
    def page_controls(report, store):
        if not report or report.get('context') != context_key(store):
            return True, True
        return report.get('previous_offset') is None, report.get('next_offset') is None

    @app.callback(Output('cp-trail-view', 'children'), Input('cp-nav-anchor', 'data'),
                  Input('cp-mode', 'value'), Input('app-store', 'data'))
    def browse_trail(nav, mode, store):
        if not nav or nav.get('mode') != mode or nav.get('context') != context_key(store):
            return ''
        return ('已浏览物种：' + ' → '.join(
            svc.candidate_formula(species) or species for species in nav['trail']) +
            '。这是浏览记录，不代表一条连续 MD 路线。')

    @app.callback(Output('cp-request', 'data'), Output('cp-message', 'children'), Output('cp-nav-anchor', 'data'),
                  Input('cp-search', 'n_clicks'), Input('cp-continue', 'n_clicks'),
                  Input('cp-page-prev', 'n_clicks'), Input('cp-page-next', 'n_clicks'),
                  Input('cp-cancel', 'n_clicks'), Input('cp-context', 'data'), State('app-store', 'data'),
                  State('cp-start', 'value'), State('cp-target', 'value'), State('cp-mode', 'value'),
                  State('cp-depth', 'value'), State('cp-limit', 'value'),
                  State('cp-quality-view', 'value'), State('cp-return-window', 'value'),
                  State('cp-return-basis', 'value'), State('cp-expansions', 'value'), State('cp-frontier', 'value'),
                  State('cp-prefixes', 'value'), State('cp-examined', 'value'), State('cp-seconds', 'value'),
                  State('cp-report', 'data'), State('cp-focus', 'value'),
                  State('cp-nav-anchor', 'data'), prevent_initial_call=True)
    def request(_run, _continue, _page_prev, _page_next, _cancel, _context,
                store, start, target, mode, depth, limit,
                quality_view, window, basis, expansions, frontier, prefixes, examined, seconds,
                report, focus, previous_nav):
        trigger = ctx.triggered_id
        if trigger not in {'cp-search', 'cp-continue', 'cp-page-prev', 'cp-page-next'}:
            return None, '已取消搜索。' if trigger == 'cp-cancel' else '', None
        if trigger in {'cp-page-prev', 'cp-page-next'}:
            if (not report or report.get('context') != context_key(store)
                    or (report.get('query') or {}).get('mode') not in {'explore', 'reverse'}):
                return no_update, '请先进行单端探索。', no_update
            offset = report.get('previous_offset' if trigger == 'cp-page-prev' else 'next_offset')
            if offset is None:
                return no_update, '已经到达当前分支列表边界。', no_update
            query = dict(report['query'], anchor_offset=offset)
            return dict(request_id=uuid4().hex, context=context_key(store),
                        artifacts=(store or {}).get('artifacts') or {}, query=query), '正在读取分支…', no_update
        nav = None
        if trigger == 'cp-continue':
            if (mode not in {'explore', 'reverse'} or not report
                    or report.get('context') != context_key(store)
                    or (report.get('query') or {}).get('mode') != mode):
                return no_update, '请先在单端探索结果中选择一条路线。', no_update
            path = next((p for p in report.get('paths', []) if p['signature_id'] == focus), None)
            if path is None:
                return no_update, '请先选择一条路线。', no_update
            species = path['species'][-1] if mode == 'explore' else path['species'][0]
            if mode == 'explore':
                start = species
            else:
                target = species
            old_trail = ((previous_nav or {}).get('trail')
                         if (previous_nav or {}).get('context') == context_key(store)
                         and (previous_nav or {}).get('mode') == mode else None)
            trail = list(old_trail) if old_trail else [
                report['query']['start'] if mode == 'explore' else report['query']['target']]
            trail = trail + [species] if mode == 'explore' else [species] + trail
            nav = {'side': 'start' if mode == 'explore' else 'target',
                   'species': species, 'context': context_key(store),
                   'mode': mode, 'trail': trail}
        if (mode != 'reverse' and not start) or (mode != 'explore' and not target):
            return None, '请先明确选择本次搜索所需的精确结构。', nav
        return dict(request_id=uuid4().hex, context=context_key(store), artifacts=(store or {}).get('artifacts') or {},
                    query=dict(start=start if mode != 'reverse' else '',
                               target=target if mode != 'explore' else '', mode=mode,
                               max_steps=depth if mode == 'target' else 1, max_paths=limit,
                               quality_view=quality_view, return_window_frames=window, return_basis=basis,
                               max_expansions=expansions, max_frontier=frontier,
                               max_prefixes=prefixes, max_candidates_examined=examined,
                               max_seconds=seconds)), '正在搜索…', nav

    @app.callback(Output('cp-raw', 'data'), Input('cp-request', 'data'), background=True,
                  cancel=[Input('cp-cancel', 'n_clicks')],
                  running=[(Output('cp-search', 'disabled'), True, False),
                           (Output('cp-cancel', 'disabled'), False, True)], prevent_initial_call=True)
    def run(request):
        if not request:
            return None
        result = {k: request[k] for k in ('request_id', 'context')}
        try:
            result['report'] = svc.search_candidate_paths(request['artifacts'], **request['query'])
        except svc.ServiceError as exc:
            result['report'] = {'error': exc.message}
        return result

    @app.callback(Output('cp-report', 'data'), Input('cp-raw', 'data'), Input('cp-request', 'data'),
                  Input('app-store', 'data'))
    def commit(raw, request, store):
        if accept_result(raw, request, store):
            return dict(raw['report'], context=raw['context'], query_request_id=raw['request_id'])
        return None

    @app.callback(Output('cp-validation-request', 'data'), Input('cp-check', 'n_clicks'),
                  Input('cp-check-cancel', 'n_clicks'),
                  Input('cp-request', 'data'), Input('cp-focus', 'value'), Input('app-store', 'data'),
                  State('cp-report', 'data'), prevent_initial_call=True)
    def validation_request(_click, _cancel, search_request, signature, store, report):
        if ctx.triggered_id != 'cp-check' or not report or report.get('context') != context_key(store):
            return None
        if not search_request or report.get('query_request_id') != search_request.get('request_id'):
            return None
        return dict(request_id=uuid4().hex, context=context_key(store), signature=signature,
                    search_request_id=search_request['request_id'], report=report,
                    artifacts=(store or {}).get('artifacts') or {})

    @app.callback(Output('cp-validation-raw', 'data'), Input('cp-validation-request', 'data'),
                  background=True, running=[(Output('cp-check', 'disabled'), True, False),
                                            (Output('cp-check-cancel', 'disabled'), False, True),
                                            (Output('cp-check-progress', 'children'), '正在检查连续历史…', '')],
                  cancel=[Input('cp-check-cancel', 'n_clicks'), Input('cp-search', 'n_clicks'),
                          Input('cp-cancel', 'n_clicks'), Input('app-store', 'data')], prevent_initial_call=True)
    def validate(request):
        if not request:
            return None
        result = {k: request[k] for k in ('request_id', 'context', 'search_request_id', 'signature')}
        try:
            result['validation'] = svc.check_candidate_continuity(
                request['artifacts'], request['report'], request['signature'])
        except svc.ServiceError as exc:
            result['validation'] = dict(status='inconclusive', error=exc.message)
        return result

    @app.callback(Output('cp-report', 'data', allow_duplicate=True), Input('cp-validation-raw', 'data'),
                  State('cp-validation-request', 'data'), State('cp-request', 'data'),
                  State('cp-report', 'data'), State('app-store', 'data'), prevent_initial_call=True)
    def commit_validation(raw, request, search_request, report, store):
        if not accept_result(raw, request, store) or not report or not search_request:
            return no_update
        if raw.get('search_request_id') != search_request.get('request_id') or report.get('query_request_id') != search_request.get('request_id'):
            return no_update
        updated = json.loads(json.dumps(report))
        for path in updated['paths']:
            if path['signature_id'] == raw['signature']:
                path['continuous_support'] = raw['validation']
                path['continuous_md'] = raw['validation']['status']
        return updated

    @app.callback(Output('cp-check-result', 'children'), Input('cp-focus', 'value'),
                  Input('cp-report', 'data'), Input('app-store', 'data'))
    def validation_result(signature, report, store):
        if not report or report.get('context') != context_key(store):
            return ''
        path = next((p for p in report.get('paths', []) if p['signature_id'] == signature), None)
        validation = (path or {}).get('continuous_support')
        if not validation:
            return '尚未检查；每次最多检查 1000 个状态、5 秒。'
        reasons = {'first_consumption_differs': '首次后续消耗没有接上路线的下一步',
                   'incompatible_carrier_bonds': '连接处的具体键状态不兼容',
                   'intervening_bond_history_not_indexed': '缺少间隔内每帧键状态的连续性证明',
                   'no_recorded_successor': '没有可确认的后续记录',
                   'missing_carrier_instances': '缺少可检查的分子实例',
                   'anchor_lineage_lost': '没有起始原子连续保留到该步',
                   'validation_budget': '达到检查状态预算', 'validation_time_budget': '达到检查时间预算'}
        witness = validation.get('witness') or {}
        return html.Div([html.Strong(CONTINUITY_LABELS[validation['status']]),
                         html.P('检查使用首次后续消耗，不跳过先前反应、间断或歧义。找到连续历史不构成机理证明。'),
                         html.P(' → '.join(p['event_id'] for p in witness.get('carrier_chain', []))),
                         html.Ul([html.Li(f"第 {b['step_index'] + 1} 步：{reasons.get(b['reason'], '证据存在间断或歧义')}；{b.get('next_event_id', b['event_id'])}")
                                  for b in validation.get('breakpoints', [])]),
                         html.P(validation.get('error', '')),
                         html.Details([html.Summary('检查与原子保留记录'), html.Pre(json.dumps(validation, ensure_ascii=False, indent=2))])])

    @app.callback(Output('cp-result-summary', 'children'), Output('cp-routes', 'rowData'),
                  Output('cp-routes', 'selectedRows'), Output('cp-focus', 'options'), Output('cp-focus', 'value'),
                  Output('cp-json', 'disabled'), Output('cp-csv', 'disabled'),
                  Output('cp-message', 'children', allow_duplicate=True), Input('cp-report', 'data'), Input('app-store', 'data'),
                  State('cp-focus', 'value'), prevent_initial_call=True)
    def results(report, store, previous_focus=None):
        if report and report.get('context') != context_key(store):
            report = None
        paths = (report or {}).get('paths') or []
        rows = [dict(id=p['signature_id'], route=i, steps=p['step_count'],
                     species=' → '.join(svc.candidate_formula(s) or s for s in p['species']),
                     counts=' / '.join(str(s.get('transfer_event_count', s['event_count'])) for s in p['steps']),
                     returns=' / '.join(str(s.get('quality', {}).get('folded_events', '未知')) for s in p['steps']),
                     continuity=CONTINUITY_LABELS.get(p.get('continuous_md'), '未检查'))
                for i, p in enumerate(paths, 1)]
        options = [{'label': f"路线 {i} · {p['step_count']} 步", 'value': p['signature_id']}
                   for i, p in enumerate(paths, 1)]
        focus = previous_focus if any(o['value'] == previous_focus for o in options) else options[0]['value'] if options else None
        return route_summary(report), rows, [], options, focus, not bool(report and not report.get('error')), not bool(paths), ''

    @app.callback(Output('cp-comparison', 'children'), Input('cp-routes', 'selectedRows'), Input('cp-report', 'data'), Input('app-store', 'data'))
    def compare(selected, report, store):
        if report and report.get('context') != context_key(store):
            return ''
        return comparison([dict(p, display_rank=i) for i, p in enumerate((report or {}).get('paths') or [], 1)
                           if p['signature_id'] in ui.selected_ids(selected)])

    @app.callback(Output('cp-detail', 'children'), Output('cp-step', 'options'), Output('cp-step', 'value'),
                  Input('cp-focus', 'value'), Input('cp-report', 'data'), Input('app-store', 'data'),
                  Input('cp-graph-pick', 'data'))
    def detail(signature, report, store, picked=None):
        if report and report.get('context') != context_key(store):
            return '', [], None
        path = next((p for p in (report or {}).get('paths') or [] if p['signature_id'] == signature), None)
        if not path:
            return '', [], None
        chain = []
        for i, species in enumerate(path['species']):
            if i:
                chain.append(html.Span('→', className='rs-candidate-arrow'))
            chain.append(structure(species))
        options = [{'label': f"第 {i+1} 步 · {s.get('transfer_event_count', s['event_count'])} 个支持事件", 'value': i} for i, s in enumerate(path['steps'])]
        data = candidate_graph.resolve_pick(report, picked)
        step_index = next((m['step_index'] for m in (data or {}).get('members', [])
                           if m['signature_id'] == signature), 0)
        return html.Div(chain, className='rs-candidate-chain'), options, step_index

    @app.callback(Output('cp-step-detail', 'children'), Output('cp-event-page', 'data'),
                  Output('cp-event-status', 'children'), Output('cp-events', 'rowData'),
                  Output('cp-prev', 'disabled'), Output('cp-next', 'disabled'),
                  Output('cp-actual-event', 'options'), Output('cp-actual-event', 'value'),
                  Output('cp-instance-prev', 'disabled'), Output('cp-instance-next', 'disabled'),
                  Output('cp-instance-position', 'children'), Output('cp-open-events', 'disabled'),
                  Output('cp-track-instance', 'disabled'),
                  Input('cp-step', 'value'), Input('cp-focus', 'value'), Input('cp-report', 'data'),
                  Input('cp-prev', 'n_clicks'), Input('cp-next', 'n_clicks'),
                  Input('cp-instance-prev', 'n_clicks'), Input('cp-instance-next', 'n_clicks'),
                  Input('app-store', 'data'), State('cp-event-page', 'data'),
                  State('cp-actual-event', 'value'))
    def events(step_index, signature, report, _prev, _next, _instance_prev,
               _instance_next, store, previous, current_event_id):
        empty = ('', None, '', [], True, True, [], None, True, True, '', True, True)
        if step_index is None or not report or report.get('context') != context_key(store):
            return empty
        same_page = bool(previous and previous.get('signature_id') == signature
                         and previous.get('step_index') == step_index
                         and previous.get('context') == report.get('context')
                         and previous.get('query_request_id') == report.get('query_request_id')
                         and previous.get('source_revision') == report.get('source_revision'))
        page = previous if same_page else None
        offset = int((page or {}).get('offset') or 0)
        trigger = ctx.triggered_id
        selection_policy = 'first'
        if trigger in {'cp-prev', 'cp-next'} and same_page:
            offset = max(0, offset + (25 if trigger == 'cp-next' else -25))
            page = None
            selection_policy = 'last' if trigger == 'cp-prev' else 'first'
        elif trigger in {'cp-instance-prev', 'cp-instance-next'} and same_page:
            rows = (page or {}).get('rows') or []
            index = next((i for i, row in enumerate(rows)
                          if row.get('event_id') == current_event_id), 0)
            direction = -1 if trigger == 'cp-instance-prev' else 1
            next_index = index + direction
            if 0 <= next_index < len(rows):
                current_event_id = rows[next_index].get('event_id')
                selection_policy = 'preserve'
            elif direction < 0 and offset > 0:
                offset = max(0, offset - 25)
                page = None
                selection_policy = 'last'
            elif direction > 0 and (page or {}).get('has_more'):
                offset += 25
                page = None
                selection_policy = 'first'
        if page is None:
            try:
                page = svc.candidate_step_events((store or {}).get('artifacts') or {}, report,
                                                 signature, step_index, offset)
                page = dict(page, context=report.get('context'),
                            query_request_id=report.get('query_request_id'),
                            source_revision=report.get('source_revision'))
            except (svc.ServiceError, ValueError, OSError) as exc:
                return '', None, str(exc), [], True, True, [], None, True, True, '', True, True
        step = next(p for p in report['paths'] if p['signature_id'] == signature)['steps'][step_index]
        shared_atoms = step.get('max_shared_atoms')
        evidence = html.Div([
            html.Div([*map(structure, step['reactants']), html.Span('→'), *map(structure, step['products'])], className='rs-candidate-chain'),
            html.P(f"支持 {step.get('transfer_event_count', step['event_count'])} 个事件 · "
                   f"最大共享 {shared_atoms if shared_atoms is not None else '未知'} 个原子。各步实例独立，不构成整条路线的连续历史。"),
            html.Details([html.Summary('精确反应式、主线与往返统计'), html.Code(step['reaction_key']),
            html.P(f"主线载体：{step['carried_from']} → {step['carried_to']}"),
            html.P(('局部原子传递：本步只列出主线产物继承当前反应物原子数最多的事件；'
                    f"本路线记录的最大共享原子数为 {step.get('max_shared_atoms')}。")
                   if step.get('transfer_basis') == 'event_local_dominant_atom_descendant'
                   else '仅物种名称连通：未核查当前反应物的原子是否进入主线产物。'),
            html.P(f"原始支持 {step.get('transfer_event_count', step['event_count'])} 次；"
                   f"其中短暂返回 {step.get('quality', {}).get('rapid_return_events', '未知')} 次，"
                   f"反向变化后复原 {step.get('quality', {}).get('reclosure_events', '未知')} 次；"
                   f"未折叠 {step.get('quality', {}).get('retained_events', '未知')} 次。两类返回可重叠，未折叠不等于稳定。"),
            html.P('事件列表保留本步骤的全部原始支持事件，包含折叠事件。')])])
        rows = page.get('rows') or []
        options = [{'label': (f"实例 {offset+i+1} · Transition {row.get('timestep_index')} · "
                              f"{row.get('association_status') or '状态未知'}"),
                    'value': row.get('event_id')} for i, row in enumerate(rows)]
        ids = [option['value'] for option in options]
        if selection_policy == 'last' and ids:
            selected_id = ids[-1]
        elif selection_policy == 'preserve' and current_event_id in ids:
            selected_id = current_event_id
        else:
            selected_id = current_event_id if current_event_id in ids else (ids[0] if ids else None)
        selected_row = next((row for row in rows if row.get('event_id') == selected_id), None)
        inspectable = bool(selected_row and selected_row.get('association_status') == 'matched'
                           and selected_row.get('atom_id_list'))
        absolute = offset + ids.index(selected_id) + 1 if selected_id in ids else 0
        position = f"实例 {absolute}/{page.get('total', 0)}" if selected_id else '没有可选实例'
        status = (f"本页实例 {offset+1 if rows else 0}–{offset+len(rows)} / {page['total']}；"
                  "Transition 是分析帧之间的区间，同一区间内不推断先后。")
        table_rows = [{k: row.get(k) for k in ('event_id', 'timestep_index',
                                                'association_status', 'shared_atom_count')}
                      for row in rows]
        previous_disabled = not selected_id or (absolute <= 1)
        next_disabled = not selected_id or (absolute >= int(page.get('total') or 0))
        return (evidence, page, status, table_rows, offset == 0, not page['has_more'],
                options, selected_id, previous_disabled, next_disabled, position,
                not inspectable, not inspectable)

    @app.callback(Output('cp-actual-structure', 'children'), Input('cp-actual-event', 'value'),
                  Input('cp-event-page', 'data'), Input('app-store', 'data'), State('cp-report', 'data'))
    def actual_structure(event_id, page, store, report):
        if not report or report.get('context') != context_key(store):
            return ''
        row = next((r for r in (page or {}).get('rows', []) if r['event_id'] == event_id), None)
        return actual_event_view(row) if row else ''

    @app.callback(Output('event-selected-store', 'data', allow_duplicate=True),
                  Output('event-bookmark-store', 'data', allow_duplicate=True),
                  Output('event-viewer-store', 'data', allow_duplicate=True),
                  Output('event-viewer-card', 'style', allow_duplicate=True),
                  Output('event-dft-store', 'data', allow_duplicate=True),
                  Output('molecule-lineage-store', 'data', allow_duplicate=True),
                  Output('molecule-lineage-results', 'style', allow_duplicate=True),
                  Input('cp-open-events', 'n_clicks'), Input('cp-track-instance', 'n_clicks'),
                  State('cp-event-page', 'data'), State('cp-actual-event', 'value'),
                  State('cp-report', 'data'), State('app-store', 'data'), prevent_initial_call=True)
    def open_instance(_inspect, _track, page, event_id, report, store):
        if (ctx.triggered_id not in {'cp-open-events', 'cp-track-instance'} or not page
                or not event_id or not report or report.get('context') != context_key(store)):
            raise PreventUpdate
        # Re-read the exact evidence page; never hand off a stale browser row.
        try:
            refreshed = svc.candidate_step_events((store or {}).get('artifacts') or {}, report,
                page['signature_id'], page['step_index'], page['offset'])
        except svc.ServiceError as exc:
            raise PreventUpdate from exc
        row = next((item for item in refreshed.get('rows') or []
                    if item.get('event_id') == event_id), None)
        if not row or row.get('association_status') != 'matched' or not row.get('atom_id_list'):
            raise PreventUpdate
        config = {'reaction_text': refreshed['reaction_key'], 'before_frames': 3,
                  'after_frames': 3, 'max_events': 25}
        selected = {'row': row, 'kind': 'rng_event', 'config': config,
                    'origin': {'kind': 'candidate_step',
                               'query_request_id': report.get('query_request_id'),
                               'signature_id': page['signature_id'],
                               **({'candidate_signature': page['candidate_signature']}
                                  if page.get('candidate_signature') else {}),
                               'step_index': page['step_index'], 'offset': page['offset'],
                               'action': ctx.triggered_id}}
        try:
            bookmark = svc.create_event_bookmark(store or {}, row, before_frames=3, after_frames=3)
        except svc.ServiceError:
            bookmark = None
        return selected, bookmark, None, {'display': 'none'}, None, None, {'display': 'none'}

    @app.callback(Output('cp-download', 'data'), Output('cp-message', 'children', allow_duplicate=True), Input('cp-json', 'n_clicks'), Input('cp-csv', 'n_clicks'),
                  State('cp-report', 'data'), State('app-store', 'data'), prevent_initial_call=True)
    def export(_json, _csv, report, store):
        if not report or report.get('error') or report.get('context') != context_key(store):
            raise PreventUpdate
        try:
            current_revision = svc.candidate_source_revision((store or {}).get('artifacts') or {})
        except svc.ServiceError as exc:
            return no_update, exc.message
        if current_revision != report.get('source_revision'):
            return no_update, '来源版本已变化，请重新搜索后导出。'
        if ctx.triggered_id == 'cp-csv':
            return dict(content=svc.candidate_paths_csv(report), filename='candidate-path-steps.csv', type='text/csv'), '已导出逐步 CSV。'
        return dict(content=json.dumps(report, ensure_ascii=False, indent=2), filename='candidate-paths.json', type='application/json'), '已导出 JSON。'
