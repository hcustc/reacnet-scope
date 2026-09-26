"""Candidate-path task inside the reaction workspace.

Requests and results carry dataset and query identities; late completions may
never repopulate a new dataset or replace a more recent search.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
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


def event_page_matches(page, report, store, signature, step_index):
    return bool(page and report and report.get('context') == context_key(store)
                and page.get('context') == report.get('context')
                and page.get('query_request_id') == report.get('query_request_id')
                and page.get('source_revision') == report.get('source_revision')
                and page.get('signature_id') == signature
                and page.get('step_index') == step_index)


def observation_rows(page):
    """Display rows carry the evidence page identity, not just a row number."""
    scope = {k: page.get(k) for k in ('context', 'query_request_id', 'source_revision',
                                     'signature_id', 'step_index', 'offset')}
    labels = {'matched': '已关联', 'unmatched': '未关联', 'ambiguous': '待解析'}
    return [dict(event_id=row['event_id'], observation=page.get('offset', 0) + i + 1,
                 timestep_index=row.get('timestep_index'), shared_atom_count=row.get('shared_atom_count'),
                 association=labels.get(row.get('association_status'), '待解析'), scope=scope)
            for i, row in enumerate(page.get('rows') or [])]


def selected_observation(page, selected):
    row = ui.selected_row(selected, observation_rows(page or {}))
    if not row or selected[0].get('scope') != row['scope']:
        return None
    return next((r for r in page.get('rows', []) if r['event_id'] == row['event_id']), None)


def step_overview(step):
    def side(participants, carrier, label):
        cards = []
        for species, count in Counter(participants).items():
            cards.append(html.Div([
                html.Small('主线载体' if species == carrier else '其他参与物'),
                structure(species, show_identity=False),
                html.Span(f'× {count}', className='rs-candidate-stoichiometry') if count > 1 else None,
            ], className='rs-candidate-participant' + (' is-carrier' if species == carrier else '')))
        return html.Div([html.Small(label, className='rs-candidate-side-label'),
                         html.Div(cards, className='rs-candidate-participants')])
    support = step.get('transfer_event_count', step['event_count'])
    support_label = ('本步主线转移' if step.get('transfer_basis') == 'event_local_dominant_atom_descendant'
                     else '本步事件支持（未核查原子转移）')
    return html.Div([
        html.Div([side(step['reactants'], step['carried_from'], '反应物'),
                  html.Div('↓', className='rs-candidate-equation-arrow', **{'aria-label': '生成'}),
                  side(step['products'], step['carried_to'], '产物')], className='rs-candidate-equation'),
        html.P(f"{support_label}：{support} 次 · 该反应类型总计：{step['event_count']} 次。"),
        html.P(f"正向 {step['forward_count']} 次 · 逆向 {step['reverse_count']} 次 · 净 {step['net_count']} 次。"
               '按完整反应式及计量配对，统计当前 RNG 数据全部观测区间。')
        if 'net_count' in step else None,
        html.Details([html.Summary('反应身份与统计口径'), html.Code(step['reaction_key']),
            html.P(f"主线载体：{step['carried_from']} → {step['carried_to']}"),
            html.P(f"支持事件中最多共享 {step.get('max_shared_atoms') if step.get('max_shared_atoms') is not None else '未知'} 个原子；"
                   '每次的共享数见下方观测列表，不表示整条路线的原子保留。'),
            html.P('支持事件已核查当前反应物到主线产物的局部优势原子传递。'
                   if step.get('transfer_basis') == 'event_local_dominant_atom_descendant'
                   else '仅物种名称连通：未核查当前反应物的原子是否进入主线产物。'),
            html.P(f"其中短暂返回 {step.get('quality', {}).get('rapid_return_events', '未知')} 次；"
                   f"反向变化后复原 {step.get('quality', {}).get('reclosure_events', '未知')} 次；"
                   f"未折叠 {step.get('quality', {}).get('retained_events', '未知')} 次。"
                   '两类返回可重叠，未折叠不等于稳定。'),
            html.P('列表保留全部原始支持事件，包含折叠事件。分析区间为相邻分析帧之间的 Transition，区间内不推断先后。'),
        ]),
    ])


def structure(species, *, show_identity=True):
    return html.Div([
        html.Img(src='/api/structure.svg?' + urlencode({'smiles': species, 'width': 180, 'height': 110}),
                 alt=species),
        html.Strong(svc.candidate_formula(species) or species),
        html.Details([html.Summary('精确 RNG 标签'), html.Code(species)]) if show_identity else None,
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
MAX_BROWSE_STEPS = 100


def _browse_chain(segments, mode):
    """Return one directed display chain from continue actions, or an empty pair."""
    ordered = list(segments or [])
    if mode == 'reverse':
        ordered.reverse()
    species_chain = []
    steps = []
    for segment in ordered:
        species = list(segment.get('species') or [])
        segment_steps = list(segment.get('steps') or [])
        if not segment_steps or len(species) != len(segment_steps) + 1:
            return [], []
        if species_chain and species_chain[-1] != species[0]:
            return [], []
        species_chain = species if not species_chain else species_chain + species[1:]
        steps.extend(segment_steps)
    return species_chain, steps


def _browse_navigation(segments, mode, context, anchor=None):
    trail, steps = _browse_chain(segments, mode)
    if not trail and anchor:
        trail = [anchor]
    frontier = (trail[-1] if mode == 'explore' else trail[0]) if trail else anchor
    return {'side': 'start' if mode == 'explore' else 'target',
            'species': frontier, 'context': context, 'mode': mode,
            'trail': trail, 'steps': steps, 'segments': list(segments or [])}


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


def event_participants_view(row, context):
    groups = []
    for side, label in [('reactant', '反应物'), ('product', '产物')]:
        cards = []
        for index, participant in enumerate(row.get(f'{side}_participants') or []):
            cards.append(html.Div([
                structure(participant['species'], show_identity=False),
                html.Small(f"{label} {index + 1} · {len(participant.get('atom_ids') or [])} 个原子",
                           title='原子 ID：' + ', '.join(map(str, participant.get('atom_ids') or []))),
                dbc.Button('追踪这个产物' if side == 'product' else '追踪这个反应物',
                           id={'type': 'event-track-participant', 'event': row['event_id'],
                               'side': side, 'index': index, 'context': context},
                           n_clicks=0, size='sm', outline=True,
                           disabled=row.get('association_status') != 'matched' or not participant.get('atom_ids')),
            ], className='rs-event-participant'))
        if cards:
            groups.append(html.Div([html.Strong(label), html.Div(cards, className='rs-candidate-participants')]))
    return html.Div(groups, className='rs-event-participants')


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
    return html.Div([html.Strong('这次反应的 RNG 键变化'),
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
        dcc.Dropdown(id=f'cp-{side}', options=[], placeholder='先检索，再明确选择一个结构',
                     clearable=True, searchable=False, optionHeight=72),
        dcc.Store(id=f'cp-{side}-manual-search', data=False),
        html.Div(id=f'cp-{side}-message', role='status', className='rs-candidate-picker-message'),
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
        dcc.RadioItems(id='cp-direction-view', options=[
            {'label': '净转化方向', 'value': 'net'},
            {'label': '全部观测方向', 'value': 'observed'}], value='net', inline=True),
        html.P('净转化按完整正逆反应的事件数之差定向；净零不连边，原始事件仍可核查。'),
        html.Div([html.Div([dbc.Label('最多展示'), dcc.Input(id='cp-limit', type='number', value=20, min=1, max=100)], className='rs-candidate-number'),
                  dbc.Button('搜索路径', id='cp-search', color='primary'),
                  dbc.Button('取消搜索', id='cp-cancel', outline=True, disabled=True)], className='rs-query-row'),
        html.Details([
        html.Summary(id='cp-advanced-heading'),
        html.Div([dcc.RadioItems(id='cp-quality-view', options=[{'label': '折叠短暂往返', 'value': 'persistent'},
                       {'label': '原始事件视图', 'value': 'raw'}], value='raw', inline=True),
        html.Div([dbc.Label('往返窗口（分析帧间隔）'), dcc.Input(id='cp-return-window', type='number', value=3, min=1, max=100),
                  dcc.RadioItems(id='cp-return-basis', options=[{'label': '同原子连接关系返回', 'value': 'topology'},
                                 {'label': '精确键级也返回', 'value': 'exact'}], value='topology', inline=True)], className='rs-candidate-advanced-row'),
        html.P('返回折叠仅用于全部观测方向；净转化方向始终使用原始总计数，不作短时折叠。图上物种重复不作为事件返回依据。'),
        ], id='cp-return-controls', style={'display': 'none'}),
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
            dbc.Label('路线', html_for='cp-focus'),
            dbc.Button('上一条', id='cp-route-prev', size='sm', outline=True),
            dcc.Dropdown(id='cp-focus', options=[], clearable=False, placeholder='搜索后选择路线'),
            dbc.Button('下一条', id='cp-route-next', size='sm', outline=True),
        ], className='rs-candidate-route-nav'),
        html.Div([
            dbc.Button('继续探索一步', id='cp-continue', size='sm', color='primary'),
            dbc.Button('返回上一步', id='cp-back', size='sm', outline=True, disabled=True),
            dbc.Button('上一页分支', id='cp-page-prev', size='sm', outline=True, disabled=True),
            dbc.Button('下一页分支', id='cp-page-next', size='sm', outline=True, disabled=True),
        ], className='rs-candidate-browse-nav'),
        dbc.DropdownMenu([
            dbc.DropdownMenuItem('候选路线 JSON', id='cp-json', disabled=True),
            dbc.DropdownMenuItem('逐步证据 CSV', id='cp-csv', disabled=True),
        ], label='导出', id='cp-export-menu', size='sm', align_end=True,
           toggle_style={'whiteSpace': 'nowrap'}, className='rs-candidate-export'),
        dcc.Download(id='cp-download'),
        ], className='rs-candidate-results-toolbar'),
        html.Details([
            html.Summary('整条路线的连续历史', id='cp-continuity-heading'),
            html.P('检查是否存在一条连续的分子历史，依次支持当前路线。各步分别有事件支持不等于整条路线连续发生。',
                     className='text-muted'),
            dbc.Button('检查当前路线', id='cp-check', size='sm', outline=True),
            dbc.Button('取消检查', id='cp-check-cancel', size='sm', outline=True, disabled=True),
            html.Div(id='cp-check-progress', role='status'),
            html.Div(id='cp-check-result', role='status'),
        ], className='rs-candidate-continuity-details'),
        html.Div([html.Div([
            html.Div([dbc.Label('定位步骤', html_for='cp-step'),
                      dcc.Dropdown(id='cp-step', options=[], clearable=False,
                                   placeholder='选择路线后定位某一步')], className='rs-candidate-step-nav'),
            candidate_graph.layout()], className='rs-candidate-map'), html.Details([
            html.Summary('选择图中一步，查看反应与观测', id='cp-evidence-title'),
            html.Div(id='cp-step-detail', className='rs-candidate-step-overview'),
            html.Div([
                html.Strong('支持本步的观测'),
                html.Small('选择一行，再查看这次反应。', className='text-muted'),
            ], className='rs-candidate-observations-heading'),
            ui.result_grid('cp-events', definitions=[
                {'name': '观测', 'id': 'observation', 'minWidth': 60, 'maxWidth': 75},
                {'name': '分析区间', 'id': 'timestep_index', 'minWidth': 95},
                {'name': '共享原子', 'id': 'shared_atom_count', 'minWidth': 90},
                {'name': '分子关联', 'id': 'association', 'minWidth': 85},
                {'name': '事件 ID', 'id': 'event_id', 'hide': True}],
                selection='single', sortable=False, height=200),
            html.Div([
                html.Div(id='cp-event-status', role='status', className='rs-candidate-event-status'),
                html.Div([
                    dbc.Button('上一页', id='cp-prev', size='sm', outline=True, disabled=True),
                    dbc.Button('下一页', id='cp-next', size='sm', outline=True, disabled=True),
                ], className='rs-candidate-event-pager'),
            ], className='rs-candidate-event-toolbar'),
            html.Div(id='cp-event-selection-note', role='status', className='rs-candidate-event-status'),
            dbc.Button('查看这次反应', id='cp-open-events', size='sm', color='primary', disabled=True),
        ], id='cp-evidence', className='rs-candidate-evidence-workspace')],
                 className='rs-candidate-investigation'),
        html.Details([html.Summary('路线表格与多路线比较'),
        ui.result_grid('cp-routes',
                    definitions=[
            {'name': '路线', 'id': 'route'}, {'name': '步数', 'id': 'steps'},
            {'name': '物种序列（分子式仅作显示）', 'id': 'species'},
            {'name': '各步事件数', 'id': 'counts'}, {'name': '其中往返事件', 'id': 'returns'}, {'name': '连续历史', 'id': 'continuity'}],
                    selection='multi',
                    page_size=10),
        html.P('勾选 2–3 条路线比较；事件数属于各步骤，不是整条路线的发生次数。', className='text-muted'),
        html.Div(id='cp-comparison'),
        html.Details([html.Summary('当前路线的逐步结构'), html.Div(id='cp-detail')]),
        ], className='rs-candidate-table-details'),
        ], id='cp-results', style={'display': 'none'}),
        *[dcc.Store(id=name) for name in ['cp-request', 'cp-raw', 'cp-report', 'cp-event-page', 'cp-context', 'cp-nav-anchor', 'cp-species-handoff',
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
    direction = ('净转化方向：按当前 RNG 数据全部观测区间的完整正逆反应计数相减，只展开净值为正的方向。'
                 if query.get('direction_view') == 'net' else '全部观测方向：保留有事件记录的方向。')
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
        html.Div(direction, className='rs-candidate-result-limits'),
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
    @app.callback(Output('cp-return-controls', 'style'), Input('cp-direction-view', 'value'))
    def return_controls(direction):
        return {'display': 'none'} if direction == 'net' else {}

    @app.callback(Output('cp-advanced-heading', 'children'),
                  Input('cp-direction-view', 'value'),
                  Input('cp-quality-view', 'value'), Input('cp-return-window', 'value'),
                  Input('cp-return-basis', 'value'))
    def advanced_heading(direction, view, window, basis):
        if direction == 'net':
            return '净转化方向 · 使用原始总计数（高级选项）'
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
                  Input('cp-focus', 'value'), Input('cp-nav-anchor', 'data'))
    def graph(report, store, scope, selected, focus, nav):
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
        display_report = candidate_graph.with_browse_history(report, nav)
        if not report.get('paths') and display_report.get('browse_history_step_count'):
            visible.add(candidate_graph.BROWSE_HISTORY_SIGNATURE)
        graph_elements = [e for e in candidate_graph.elements(display_report)
                          if any(m['signature_id'] in visible for m in e['data']['members'])]
        if scope == 'selected':
            # Reposition the visible subset so hidden branches do not leave
            # widely separated nodes that shrink the selected route on fit.
            subset = candidate_graph.with_browse_history(dict(report, paths=paths), nav)
            positions = {e['data']['id']: e
                         for e in candidate_graph.elements(subset) if 'position' in e}
            for element in graph_elements:
                if element['data']['id'] in positions:
                    positioned = positions[element['data']['id']]
                    element['position'] = positioned['position']
                    for key in ('layout_layer', 'layout_order'):
                        element['data'][key] = positioned['data'][key]
            graph_elements = candidate_graph.local_participants(graph_elements, visible)
        species_count = len({e['data']['species'] for e in graph_elements if e['data']['kind'] == 'species'})
        step_count = sum(e['data']['kind'] == 'reaction' for e in graph_elements)
        history_count = display_report.get('browse_history_step_count', 0)
        note = '搜索截断；' if not report.get('query_complete', True) else ''
        if report.get('display_truncated'):
            note += '仅展示部分已检查路线。'
        history_note = f' · 已保留 {history_count} 个浏览步骤' if history_count else ''
        return graph_elements, (f'{len(paths)} 条路线 · {species_count} 个物种 · '
                                f'{step_count} 个反应步骤{history_note}。{note}')

    @app.callback(Output('cp-graph-base-style', 'data'), Input('cp-graph', 'elements'),
                  Input('cp-focus', 'value'), Input('cp-routes', 'selectedRows'), Input('cp-step', 'value'))
    def graph_highlight(graph_elements, focus, selected, step_index):
        return candidate_graph.stylesheet(graph_elements or [], focus, ui.selected_ids(selected), step_index)

    @app.callback(Output('cp-graph', 'layout'), Output('cp-graph-layout-state', 'data'),
                  Input('cp-graph-reset', 'n_clicks'), Input('cp-graph-locate', 'n_clicks'),
                  Input('cp-graph', 'elements'), Input('cp-graph-size', 'data'),
                  Input('cp-graph-scope', 'value'), Input('cp-step', 'value'),
                  State('cp-focus', 'value'), State('cp-graph', 'extent'),
                  State('cp-graph-layout-state', 'data'))
    def graph_layout(reset, locate, graph, size, scope, step_index, focus, extent, previous):
        if not graph or not size or not size.get('width'):
            return no_update, no_update
        key = json.dumps([scope, [(e['data']['id'], e['data'].get('query_request_id'),
                                  e['data'].get('layout_layer'), e['data'].get('layout_order')) for e in graph]])
        same = (previous or {}).get('key') == key
        resized = (previous or {}).get('size') != size
        selected_step = next((e['data']['id'] for e in graph if e['data']['kind'] == 'reaction'
                              and any(m['signature_id'] == focus and m['step_index'] == step_index
                                      for m in e['data']['members'])), None)
        if not same:
            layout = candidate_graph.fitted_layout(graph, size, reading=scope != 'all')
        elif resized or ctx.triggered_id in {'cp-graph-reset', 'cp-graph-locate', 'cp-graph-size', 'cp-step'}:
            positions = previous['positions']
            layout = {'name': 'preset', 'positions': positions, 'padding': 32}
            if ctx.triggered_id == 'cp-graph-reset' or (resized and scope == 'all'):
                layout['fit'] = True
            else:
                zoom = ((previous.get('size') or size)['width'] / extent['w']) if extent and extent.get('w') else 1
                point = positions.get(selected_step)
                if not resized and ctx.triggered_id == 'cp-step' and (not point or extent and
                        extent['x1'] + 60 < point['x'] < extent['x2'] - 60 and
                        extent['y1'] + 60 < point['y'] < extent['y2'] - 60):
                    return no_update, no_update
                layout.update(candidate_graph.reading_viewport(positions, size, selected_step,
                    zoom=max(.9, min(2.5, zoom)) if scope != 'all' or ctx.triggered_id == 'cp-graph-locate' else zoom))
        else:
            return no_update, no_update
        layout.update(revision=[reset, locate, step_index], viewport=size)
        return layout, {'key': key, 'positions': layout['positions'], 'size': size}

    app.clientside_callback(
        """function(base, extent, size, scope) {
            const zoom = extent && extent.w && size ? size.width / extent.w : 1;
            const compact = scope === 'all' && zoom < 0.65;
            const styles = (base || []).slice();
            if (compact) {
                styles.push({selector: 'node[kind = "species"]', style: {
                    'background-image': 'none', width: 36, height: 28, label: ''}});
                styles.push({selector: 'node[kind = "reaction"]', style: {label: ''}});
            }
            const note = compact ? '总览仅定位分支；放大查看结构，点击菱形查看步骤。' :
                scope === 'all' ? '仅展示返回候选的合并图；菱形次数为反应类型总数。' :
                '当前与勾选路线 · 拖动画布阅读长路线，或用“定位步骤”跳转。';
            return [styles, note];
        }""",
        Output('cp-graph', 'stylesheet'), Output('cp-graph-view-note', 'children'),
        Input('cp-graph-base-style', 'data'), Input('cp-graph', 'extent'),
        Input('cp-graph-size', 'data'), Input('cp-graph-scope', 'value'))

    app.clientside_callback(
        """function(plus, minus, extent, size) {
            if (!extent || !size || !extent.w || !size.width) {
                return dash_clientside.no_update;
            }
            const trigger = dash_clientside.callback_context.triggered_id;
            // Cytoscape's zoom prop is an input, not a live viewport reading.
            // Its read-only extent follows both auto-fit and wheel zoom.
            const current = size.width / extent.w;
            const zoom = Math.max(0.08, Math.min(2.5, current *
                (trigger === 'cp-graph-zoom-in' ? 1.25 : 0.8)));
            // Preset without positions preserves even manually dragged nodes.
            // A new command also works when reset returned to an earlier zoom.
            return {name: 'preset', fit: false, zoom: zoom,
                revision: (plus || 0) + (minus || 0), pan: {
                    x: size.width / 2 - (extent.x1 + extent.x2) / 2 * zoom,
                    y: size.height / 2 - (extent.y1 + extent.y2) / 2 * zoom
                }};
        }""",
        Output('cp-graph', 'layout', allow_duplicate=True),
        Input('cp-graph-zoom-in', 'n_clicks'), Input('cp-graph-zoom-out', 'n_clicks'),
        State('cp-graph', 'extent'), State('cp-graph-size', 'data'),
        prevent_initial_call=True)

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
        related = [m['signature_id'] for m in
                   (data.get('carrier_members') or data['members'])]
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
        if tab == 'candidates':
            return {'display': 'none'}, {}
        if tab == 'direct':
            return {}, {'display': 'none'}
        return {'display': 'none'}, {'display': 'none'}

    @app.callback(Output('cp-from-species', 'disabled'), Input('app-store', 'data'))
    def shortcuts(store):
        disabled = not bool((store or {}).get('selected_smiles'))
        return disabled

    @app.callback(Output('reaction-task-tabs', 'value'), Output('cp-mode', 'value'),
                  Output('cp-species-handoff', 'data'),
                  Input('cp-from-species', 'n_clicks'),
                  State('app-store', 'data'),
                  prevent_initial_call=True)
    def open_shortcut(clicks, store):
        if ctx.triggered_id != 'cp-from-species' or not clicks:
            raise PreventUpdate
        species = str((store or {}).get('selected_smiles') or '').strip()
        if not species:
            raise PreventUpdate
        return 'candidates', 'explore', {
            'species': species,
            'context': context_key(store),
            'side': 'start',
        }

    for side in ('start', 'target'):
        def register_picker(side):
            @app.callback(Output(f'cp-{side}', 'options'), Output(f'cp-{side}', 'value'),
                          Output(f'cp-{side}-message', 'children'),
                          Output(f'cp-{side}-manual-search', 'data'),
                          Output(f'cp-{side}-query', 'value'),
                          Input(f'cp-{side}-find', 'n_clicks'), Input(f'cp-{side}-current', 'n_clicks'),
                          Input('cp-context', 'data'), Input('cp-species-handoff', 'data'),
                          Input('cp-nav-anchor', 'data'), Input('cp-mode', 'value'),
                          State('app-store', 'data'), State(f'cp-{side}-query', 'value'),
                          State(f'cp-{side}', 'options'), State(f'cp-{side}', 'value'),
                          State(f'cp-{side}-manual-search', 'data'),
                          prevent_initial_call=True)
            def find(_find, _current, _context, handoff, nav, mode,
                     store, query, options, value, manual_search):
                if ctx.triggered_id == 'cp-context':
                    # Dataset changes must never retain a previous selection.
                    return [], None, '', False, ''
                if ctx.triggered_id == 'cp-nav-anchor':
                    if not nav or nav.get('side') != side or nav.get('context') != context_key(store):
                        return no_update, no_update, no_update, no_update, no_update
                    species = nav['species']
                    return [species_option(species)], species, '已沿选中路线带入精确结构。', False, ''
                if ctx.triggered_id in {'cp-species-handoff', 'cp-mode'}:
                    # Mode changes must not replace a query the user has started here.
                    if ctx.triggered_id == 'cp-mode' and (manual_search or str(query or '').strip()):
                        return no_update, no_update, no_update, no_update, no_update
                    valid_handoff = (
                        isinstance(handoff, dict)
                        and handoff.get('context') == context_key(store)
                        and str(handoff.get('species') or '').strip()
                    )
                    if not valid_handoff:
                        return no_update, no_update, no_update, no_update, no_update
                    species = str(handoff['species']).strip()
                    if ctx.triggered_id == 'cp-mode' and mode in {'reverse', 'explore'}:
                        anchor_side = 'target' if mode == 'reverse' else 'start'
                    else:
                        anchor_side = handoff.get('side') or ('target' if mode == 'reverse' else 'start')
                    if side == anchor_side:
                        return [species_option(species)], species, '已带入当前选中的精确物种。', False, ''
                    if value == species:
                        return [], None, '', False, ''
                    return no_update, no_update, no_update, no_update, no_update
                current = ctx.triggered_id == f'cp-{side}-current'
                if current:
                    species = str((store or {}).get('selected_smiles') or '').strip()
                    if not species:
                        return [], None, '请先在物种检索中选择一个精确结构。', False, no_update
                    return [species_option(species)], species, '已带入当前选中的精确物种。', False, ''
                text = str(query or '').strip()
                if not text:
                    return [], None, '请输入分子式或精确 RNG SMILES 后检索。', True, no_update
                try:
                    result = svc.search_candidate_species((store or {}).get('artifacts') or {}, text)
                except svc.ServiceError as exc:
                    return [], None, exc.message, True, no_update
                options = [species_option(r['species']) for r in result['rows']]
                if not options:
                    message = f'当前候选路径索引中没有匹配“{text}”的精确结构。请检查分子式，或粘贴精确 RNG SMILES。'
                    return [{
                        'label': f'当前候选路径索引中没有匹配“{text}”的精确结构',
                        'value': '__no_candidate_species_match__',
                        'disabled': True,
                    }], None, message, True, no_update
                selected = options[0]['value'] if len(options) == 1 else None
                return options, selected, ('显示前 100 个匹配，请用精确标签缩小范围。' if result['has_more'] else
                    f'匹配 {len(options)} 个精确结构。' + ('请选择一个。' if len(options) > 1 else '')), True, no_update

            @app.callback(Output(f'cp-{side}-preview', 'children'), Input(f'cp-{side}', 'value'))
            def preview(value):
                return structure(value) if value else ''
        register_picker(side)

    @app.callback(Output('cp-start-wrap', 'style'), Output('cp-target-wrap', 'style'),
                  Output('cp-continue', 'style'), Output('cp-back', 'style'),
                  Output('cp-page-prev', 'style'),
                  Output('cp-page-next', 'style'), Input('cp-mode', 'value'))
    def target_mode(mode):
        return ({'display': 'none'} if mode == 'reverse' else {},
                {'display': 'none'} if mode == 'explore' else {},
                {'display': 'none'} if mode == 'target' else {},
                {'display': 'none'} if mode == 'target' else {},
                {'display': 'none'} if mode == 'target' else {},
                {'display': 'none'} if mode == 'target' else {})

    @app.callback(Output('cp-back', 'disabled'), Input('cp-nav-anchor', 'data'),
                  Input('cp-mode', 'value'), Input('app-store', 'data'))
    def back_control(nav, mode, store):
        return not bool(nav and nav.get('context') == context_key(store)
                        and nav.get('mode') == mode and nav.get('segments'))

    @app.callback(Output('cp-page-prev', 'disabled'), Output('cp-page-next', 'disabled'),
                  Input('cp-report', 'data'), Input('app-store', 'data'))
    def page_controls(report, store):
        if not report or report.get('context') != context_key(store):
            return True, True
        return report.get('previous_offset') is None, report.get('next_offset') is None

    @app.callback(Output('cp-trail-view', 'children'), Input('cp-nav-anchor', 'data'),
                  Input('cp-mode', 'value'), Input('app-store', 'data'))
    def browse_trail(nav, mode, store):
        if (not nav or nav.get('mode') != mode or nav.get('context') != context_key(store)
                or len(nav.get('trail') or []) < 2):
            return ''
        return ('已选浏览路径：' + ' → '.join(
            svc.candidate_formula(species) or species for species in nav['trail']) +
            '。图中灰色虚线为先前选择；这是候选探索上下文，不代表一条连续 MD 路线。')

    @app.callback(Output('cp-request', 'data'), Output('cp-message', 'children'), Output('cp-nav-anchor', 'data'),
                  Input('cp-search', 'n_clicks'), Input('cp-continue', 'n_clicks'), Input('cp-back', 'n_clicks'),
                  Input('cp-page-prev', 'n_clicks'), Input('cp-page-next', 'n_clicks'),
                  Input('cp-cancel', 'n_clicks'), Input('cp-context', 'data'), State('app-store', 'data'),
                  State('cp-start', 'value'), State('cp-target', 'value'), State('cp-mode', 'value'),
                  State('cp-depth', 'value'), State('cp-limit', 'value'),
                  State('cp-direction-view', 'value'), State('cp-quality-view', 'value'), State('cp-return-window', 'value'),
                  State('cp-return-basis', 'value'), State('cp-expansions', 'value'), State('cp-frontier', 'value'),
                  State('cp-prefixes', 'value'), State('cp-examined', 'value'), State('cp-seconds', 'value'),
                  State('cp-report', 'data'), State('cp-focus', 'value'),
                  State('cp-nav-anchor', 'data'), prevent_initial_call=True)
    def request(_run, _continue, _back, _page_prev, _page_next, _cancel, _context,
                store, start, target, mode, depth, limit,
                direction_view, quality_view, window, basis, expansions, frontier, prefixes, examined, seconds,
                report, focus, previous_nav):
        trigger = ctx.triggered_id
        if trigger not in {'cp-search', 'cp-continue', 'cp-back', 'cp-page-prev', 'cp-page-next'}:
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
        if trigger == 'cp-back':
            valid_nav = (previous_nav or {}) if (
                (previous_nav or {}).get('context') == context_key(store)
                and (previous_nav or {}).get('mode') == mode) else {}
            segments = list(valid_nav.get('segments') or [])
            if mode not in {'explore', 'reverse'} or not segments:
                return no_update, '当前没有可返回的浏览步骤。', no_update
            previous = segments.pop()
            query = dict(previous.get('query') or {})
            if query.get('mode') != mode:
                return no_update, '浏览上下文已失效，请重新搜索。', None
            anchor = query.get('start') if mode == 'explore' else query.get('target')
            nav = _browse_navigation(segments, mode, context_key(store), anchor)
            return dict(request_id=uuid4().hex, context=context_key(store),
                        artifacts=(store or {}).get('artifacts') or {}, query=query), '正在返回上一步…', nav
        if trigger == 'cp-continue':
            if (mode not in {'explore', 'reverse'} or not report
                    or report.get('context') != context_key(store)
                    or (report.get('query') or {}).get('mode') != mode):
                return no_update, '请先在单端探索结果中选择一条路线。', no_update
            path = next((p for p in report.get('paths', []) if p['signature_id'] == focus), None)
            if path is None:
                return no_update, '请先选择一条路线。', no_update
            valid_nav = (previous_nav or {}) if (
                (previous_nav or {}).get('context') == context_key(store)
                and (previous_nav or {}).get('mode') == mode) else {}
            segments = list(valid_nav.get('segments') or [])
            old_trail, old_steps = _browse_chain(segments, mode)
            if segments and not old_trail:
                segments = []
                old_steps = []
            expected_frontier = (old_trail[-1] if mode == 'explore' else old_trail[0]) if old_trail else None
            report_anchor = report['query']['start'] if mode == 'explore' else report['query']['target']
            if expected_frontier and expected_frontier != report_anchor:
                segments = []
                old_steps = []
            if len(old_steps) + len(path.get('steps') or []) > MAX_BROWSE_STEPS:
                return no_update, f'已达浏览上限（{MAX_BROWSE_STEPS} 步），请返回或重新搜索。', no_update
            species = path['species'][-1] if mode == 'explore' else path['species'][0]
            if mode == 'explore':
                start = species
            else:
                target = species
            segments.append({'query': dict(report['query']), 'species': list(path['species']),
                             'steps': list(path['steps'])})
            nav = _browse_navigation(segments, mode, context_key(store))
            query = dict(report['query'], start=start if mode != 'reverse' else '',
                         target=target if mode != 'explore' else '', anchor_offset=0, max_steps=1)
            return dict(request_id=uuid4().hex, context=context_key(store),
                        artifacts=(store or {}).get('artifacts') or {}, query=query), '正在搜索…', nav
        if (mode != 'reverse' and not start) or (mode != 'explore' and not target):
            return None, '请先明确选择本次搜索所需的精确结构。', nav
        return dict(request_id=uuid4().hex, context=context_key(store), artifacts=(store or {}).get('artifacts') or {},
                    query=dict(start=start if mode != 'reverse' else '',
                               target=target if mode != 'explore' else '', mode=mode,
                               max_steps=depth if mode == 'target' else 1, max_paths=limit,
                               direction_view=direction_view or 'net',
                               quality_view='raw' if (direction_view or 'net') == 'net' else quality_view,
                               return_window_frames=window, return_basis=basis,
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

    @app.callback(Output('cp-continuity-heading', 'children'), Input('cp-focus', 'value'),
                  Input('cp-report', 'data'), Input('app-store', 'data'))
    def continuity_heading(signature, report, store):
        paths = report.get('paths', []) if report and report.get('context') == context_key(store) else []
        path = next((p for p in paths if p['signature_id'] == signature), {})
        return '整条路线的连续历史 · ' + CONTINUITY_LABELS.get(path.get('continuous_md'), '未检查')

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
        preferred = (data or {}).get('carrier_members') or (data or {}).get('members', [])
        step_index = next((m['step_index'] for m in preferred
                           if m['signature_id'] == signature), 0)
        return html.Div(chain, className='rs-candidate-chain'), options, step_index

    @app.callback(Output('cp-step-detail', 'children'), Output('cp-event-page', 'data'),
                  Output('cp-event-status', 'children'), Output('cp-events', 'rowData'),
                  Output('cp-prev', 'disabled'), Output('cp-next', 'disabled'),
                  Output('cp-events', 'selectedRows'), Output('cp-events', 'style'),
                  Output('cp-evidence-title', 'children'),
                  Input('cp-step', 'value'), Input('cp-focus', 'value'), Input('cp-report', 'data'),
                  Input('cp-prev', 'n_clicks'), Input('cp-next', 'n_clicks'),
                  Input('app-store', 'data'), State('cp-event-page', 'data'))
    def events(step_index, signature, report, _prev, _next, store, previous):
        empty = ('', None, '', [], True, True, [], {'height': '150px', 'width': '100%'},
                 '选择图中一步，查看反应与观测')
        if step_index is None or not report or report.get('context') != context_key(store):
            return empty
        path = next((p for p in report.get('paths', []) if p['signature_id'] == signature), None)
        if not path or not 0 <= step_index < len(path['steps']):
            return empty
        same_page = event_page_matches(previous, report, store, signature, step_index)
        offset = int(previous.get('offset') or 0) if same_page else 0
        if ctx.triggered_id in {'cp-prev', 'cp-next'} and same_page:
            offset = max(0, offset + (25 if ctx.triggered_id == 'cp-next' else -25))
        try:
            page = svc.candidate_step_events((store or {}).get('artifacts') or {}, report,
                                             signature, step_index, offset)
            page = dict(page, context=report.get('context'),
                        query_request_id=report.get('query_request_id'),
                        source_revision=report.get('source_revision'))
        except (svc.ServiceError, ValueError, OSError) as exc:
            return (step_overview(path['steps'][step_index]), None, str(exc), [], True, True, [],
                    empty[7], f'第 {step_index + 1} 步 · 观测读取失败')
        rows = observation_rows(page)
        total = page.get('total', 0)
        status = f'观测 {offset + 1}–{offset + len(rows)} / {total}' if rows else '本步没有可显示的观测。'
        title = f"第 {step_index + 1} / {len(path['steps'])} 步 · {total} 次观测支持"
        return (step_overview(path['steps'][step_index]), page, status, rows,
                offset == 0, not page.get('has_more'), [],
                {'height': f'{min(8, max(2, len(rows))) * 36 + 42}px', 'width': '100%'}, title)

    @app.callback(Output('cp-open-events', 'disabled'), Output('cp-event-selection-note', 'children'),
                  Input('cp-events', 'selectedRows'), Input('cp-event-page', 'data'),
                  Input('cp-report', 'data'), Input('cp-focus', 'value'), Input('cp-step', 'value'),
                  Input('app-store', 'data'))
    def observation_selection(selected, page, report, signature, step_index, store):
        if not event_page_matches(page, report, store, signature, step_index):
            return True, ''
        row = selected_observation(page, selected)
        if not row:
            return True, '选择一次观测，查看实际键变化与可用结构证据。'
        note = f"已选择分析区间 {row.get('timestep_index')} 的观测。"
        if row.get('association_status') != 'matched':
            note += '参与分子尚未解析，可查看已有元数据。'
        return False, note

    @app.callback(Output('event-selected-store', 'data', allow_duplicate=True),
                  Output('event-bookmark-store', 'data', allow_duplicate=True),
                  Output('event-viewer-store', 'data', allow_duplicate=True),
                  Output('event-viewer-card', 'style', allow_duplicate=True),
                  Output('event-dft-store', 'data', allow_duplicate=True),
                  Output('molecule-lineage-store', 'data', allow_duplicate=True),
                  Output('molecule-lineage-results', 'style', allow_duplicate=True),
                  Input('cp-open-events', 'n_clicks'),
                  State('cp-event-page', 'data'), State('cp-events', 'selectedRows'),
                  State('cp-focus', 'value'), State('cp-step', 'value'),
                  State('cp-report', 'data'), State('app-store', 'data'), prevent_initial_call=True)
    def open_instance(_inspect, page, selected_rows, signature, step_index, report, store):
        if not _inspect or not event_page_matches(page, report, store, signature, step_index):
            raise PreventUpdate
        selected_row = selected_observation(page, selected_rows)
        if not selected_row:
            raise PreventUpdate
        event_id = selected_row['event_id']
        # Re-read the exact evidence page; never hand off a stale browser row.
        try:
            refreshed = svc.candidate_step_events((store or {}).get('artifacts') or {}, report,
                page['signature_id'], page['step_index'], page['offset'])
        except svc.ServiceError as exc:
            raise PreventUpdate from exc
        row = next((item for item in refreshed.get('rows') or []
                    if item.get('event_id') == event_id), None)
        if not row:
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
