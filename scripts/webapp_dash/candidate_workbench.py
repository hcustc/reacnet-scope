"""Candidate-path task inside the reaction workspace.

Requests and results carry dataset and query identities; late completions may
never repopulate a new dataset or replace a more recent search.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode
from uuid import uuid4

from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from reacnet_scope import services as svc
from reacnet_scope.path_search import candidate_formula as smiles_to_formula_fast


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
        html.Strong(smiles_to_formula_fast(species) or species),
        html.Details([html.Summary('精确 RNG 标签'), html.Code(species)]),
    ], className='rs-candidate-structure')


def _picker(side, label):
    return html.Div([
        dbc.Label(label, html_for=f'cp-{side}'),
        html.Div([dcc.Input(id=f'cp-{side}-query', placeholder='分子式或精确 RNG SMILES', debounce=True),
                  dbc.Button('检索', id=f'cp-{side}-find', size='sm'),
                  dbc.Button('使用当前物种', id=f'cp-{side}-current', size='sm', outline=True)],
                 className='rs-query-row'),
        dcc.Dropdown(id=f'cp-{side}', options=[], placeholder='先检索，再明确选择一个结构', clearable=True, optionHeight=72),
        html.Div(id=f'cp-{side}-message', role='status'),
        html.Div(id=f'cp-{side}-preview'),
    ], className='rs-candidate-picker')


def layout():
    return html.Div([
        html.H5('候选反应路径'),
        html.P('各步由独立事件支持，通过精确物种衔接；不要求同一个分子走完整条路线。'),
        html.Div(id='cp-capability', role='status'),
        dbc.Button('前往数据集准备', id='cp-prepare', size='sm', outline=True),
        dcc.RadioItems(id='cp-mode', options=[{'label': '起点到目标', 'value': 'target'},
                       {'label': '从起点探索', 'value': 'explore'}], value='target', inline=True),
        html.Div([_picker('start', '起始结构'), html.Div(_picker('target', '目标结构'), id='cp-target-wrap')],
                 className='rs-candidate-pickers'),
        html.Div([html.Div([dbc.Label('最大步数'), dcc.Input(id='cp-depth', type='number', value=4, min=1, max=8)], className='rs-candidate-number'),
                  html.Div([dbc.Label('最多返回'), dcc.Input(id='cp-limit', type='number', value=20, min=1, max=100)], className='rs-candidate-number'),
                  dbc.Button('搜索路径', id='cp-search', color='primary'),
                  dbc.Button('取消搜索', id='cp-cancel', outline=True, disabled=True)], className='rs-query-row'),
        html.Div(id='cp-message', role='status', **{'aria-live': 'polite'}),
        html.Div(id='cp-result-summary', role='status'),
        html.Div([dbc.Button('导出 JSON', id='cp-json', size='sm', outline=True, disabled=True),
                  dbc.Button('导出逐步 CSV', id='cp-csv', size='sm', outline=True, disabled=True),
                  dcc.Download(id='cp-download')], className='rs-query-row'),
        dash_table.DataTable(id='cp-routes', data=[], columns=[
            {'name': '路线', 'id': 'route'}, {'name': '步数', 'id': 'steps'},
            {'name': '物种序列（分子式仅作显示）', 'id': 'species'},
            {'name': '各步事件数', 'id': 'counts'}, {'name': '连续历史', 'id': 'continuity'}],
            row_selectable='multi', selected_row_ids=[], page_size=10,
            style_cell={'textAlign': 'left', 'whiteSpace': 'normal', 'height': 'auto'},
            style_table={'overflowX': 'auto'}),
        html.P('勾选 2–3 条路线比较；事件数属于各步骤，不是整条路线的发生次数。', className='text-muted'),
        html.Div(id='cp-comparison'),
        dbc.Label('查看路线'), dcc.Dropdown(id='cp-focus', options=[], clearable=False),
        html.Div(id='cp-detail'),
        dbc.Label('查看步骤证据'), dcc.Dropdown(id='cp-step', options=[], clearable=False),
        html.Div(id='cp-step-detail'),
        html.Div([dbc.Button('上一页事件', id='cp-prev', size='sm', outline=True),
                  dbc.Button('下一页事件', id='cp-next', size='sm', outline=True),
                  dbc.Button('在事件工作区核查', id='cp-open-events', size='sm', color='primary')],
                 className='rs-query-row'),
        html.Div(id='cp-event-status', role='status'),
        dash_table.DataTable(id='cp-events', columns=[{'name': '事件 ID', 'id': 'event_id'},
            {'name': 'Transition', 'id': 'timestep_index'},
            {'name': '分子关联', 'id': 'association_status'}], data=[], page_action='none',
            style_table={'overflowX': 'auto'}, style_cell={'textAlign': 'left'}),
        *[dcc.Store(id=name) for name in ['cp-request', 'cp-raw', 'cp-report', 'cp-event-page', 'cp-context']],
    ], className='rs-card rs-candidate-workbench')


def route_summary(report):
    if not report:
        return ''
    if report.get('error'):
        return dbc.Alert(report['error'], color='warning')
    count = len(report['paths'])
    status = f'找到 {count} 条候选路线，按步数由少到多展示。' if count else '本次约束内未找到候选路线。'
    if not report['query_complete']:
        labels = {'time_budget': '时间预算', 'result_limit': '返回条数上限',
                  'expansion_budget': '展开预算', 'adjacency_budget': '局部邻接预算',
                  'frontier_budget': '待搜索分支上限', 'target_probe_budget': '目标连接检查预算'}
        status = (f'本次返回 {count} 条候选路线，搜索未完成；达到' if count else
                  '搜索未完成，尚未找到候选路线；达到')
        status += '、'.join(labels[r] for r in report['truncation_reasons']) + '，不能据此断言其他路线不存在。'
    fields = (report.get('processing') or {}).get('fields') or {}
    miso = (fields.get('miso') or {}).get('value')
    identity = ('miso=1：按 RNG 代表标签连接，未核查连接处每帧的精确键级。' if miso == 1 else
                '按精确 RNG 标签连接；miso 未知，不从结构外观推断。' if miso is None else f'已记录 miso={miso}；按 RNG 标签连接。')
    return dbc.Alert([html.Div(status), html.Div(identity), html.Small(
        f"最大 {report['query']['max_steps']} 步；搜索顺序不代表主通道、产率或机理可信度。")],
        color='warning' if not report['query_complete'] else 'info')


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
    @app.callback(Output('cp-context', 'data'), Input('app-store', 'data'), State('cp-context', 'data'))
    def context(store, previous):
        current = context_key(store)
        return current if current != previous else no_update

    @app.callback(Output('cp-capability', 'children'), Input('app-store', 'data'))
    def capability(store):
        status = svc.candidate_search_status((store or {}).get('artifacts') or {})
        return dbc.Alert(status['message'], color='success' if status['available'] else 'warning')

    @app.callback(Output('cp-direct-panel', 'style'), Output('cp-path-panel', 'style'), Input('reaction-task-tabs', 'value'))
    def task(tab):
        return ({'display': 'none'}, {}) if tab == 'candidates' else ({}, {'display': 'none'})

    @app.callback(Output('cp-from-species', 'disabled'), Output('cp-to-species', 'disabled'), Input('app-store', 'data'))
    def shortcuts(store):
        disabled = not bool((store or {}).get('selected_smiles'))
        return disabled, disabled

    @app.callback(Output('reaction-task-tabs', 'value'), Output('cp-mode', 'value'),
                  Input('cp-from-species', 'n_clicks'), Input('cp-to-species', 'n_clicks'), prevent_initial_call=True)
    def open_shortcut(_from, _to):
        return 'candidates', 'explore' if ctx.triggered_id == 'cp-from-species' else 'target'

    for side in ('start', 'target'):
        def register_picker(side):
            @app.callback(Output(f'cp-{side}', 'options'), Output(f'cp-{side}', 'value'),
                          Output(f'cp-{side}-message', 'children'),
                          Input(f'cp-{side}-find', 'n_clicks'), Input(f'cp-{side}-current', 'n_clicks'),
                          Input('cp-context', 'data'), Input('cp-from-species', 'n_clicks'), Input('cp-to-species', 'n_clicks'),
                          State('app-store', 'data'), State(f'cp-{side}-query', 'value'),
                          State(f'cp-{side}', 'options'), State(f'cp-{side}', 'value'),
                          prevent_initial_call=True)
            def find(_find, _current, _context, _from, _to, store, query, options, value):
                if ctx.triggered_id == 'cp-context':
                    # Dataset changes must never retain a previous selection.
                    return [], None, ''
                if ctx.triggered_id in {'cp-from-species', 'cp-to-species'} and ctx.triggered_id != ('cp-from-species' if side == 'start' else 'cp-to-species'):
                    return no_update, no_update, no_update
                current = ctx.triggered_id in {f'cp-{side}-current', 'cp-from-species', 'cp-to-species'}
                text = (store or {}).get('selected_smiles') if current else query
                try:
                    result = svc.search_candidate_species((store or {}).get('artifacts') or {}, text or '')
                except svc.ServiceError as exc:
                    return [], None, exc.message
                options = [{'label': html.Span([html.Img(src='/api/structure.svg?' + urlencode(
                    {'smiles': r['species'], 'width': 112, 'height': 58}), alt=r['species']),
                    html.Span(r['species'])], className='rs-candidate-option'),
                    'value': r['species'], 'search': r['species'] + ' ' + r['formula']}
                    for r in result['rows']]
                selected = options[0]['value'] if len(options) == 1 else None
                return options, selected, ('显示前 100 个匹配，请用精确标签缩小范围。' if result['has_more'] else
                    f'匹配 {len(options)} 个精确结构。' + ('请选择一个。' if len(options) > 1 else ''))

            @app.callback(Output(f'cp-{side}-preview', 'children'), Input(f'cp-{side}', 'value'))
            def preview(value):
                return structure(value) if value else ''
        register_picker(side)

    @app.callback(Output('cp-target-wrap', 'style'), Input('cp-mode', 'value'))
    def target_mode(mode):
        return {'display': 'none'} if mode == 'explore' else {}

    @app.callback(Output('cp-request', 'data'), Output('cp-message', 'children'),
                  Input('cp-search', 'n_clicks'), Input('cp-cancel', 'n_clicks'), Input('cp-context', 'data'), State('app-store', 'data'),
                  State('cp-start', 'value'), State('cp-target', 'value'), State('cp-mode', 'value'),
                  State('cp-depth', 'value'), State('cp-limit', 'value'), prevent_initial_call=True)
    def request(_run, _cancel, _context, store, start, target, mode, depth, limit):
        if ctx.triggered_id != 'cp-search':
            return None, '已取消搜索。' if ctx.triggered_id == 'cp-cancel' else ''
        if not start or (mode == 'target' and not target):
            return None, '请先明确选择起始结构和目标结构。'
        return dict(request_id=uuid4().hex, context=context_key(store), artifacts=(store or {}).get('artifacts') or {},
                    query=dict(start=start, target=target or '', mode=mode, max_steps=depth, max_paths=limit)), '正在搜索…'

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
            return dict(raw['report'], context=raw['context'])
        return None

    @app.callback(Output('cp-result-summary', 'children'), Output('cp-routes', 'data'),
                  Output('cp-routes', 'selected_row_ids'), Output('cp-focus', 'options'), Output('cp-focus', 'value'),
                  Output('cp-json', 'disabled'), Output('cp-csv', 'disabled'),
                  Output('cp-message', 'children', allow_duplicate=True), Input('cp-report', 'data'), Input('app-store', 'data'), prevent_initial_call=True)
    def results(report, store):
        if report and report.get('context') != context_key(store):
            report = None
        paths = (report or {}).get('paths') or []
        rows = [dict(id=p['signature_id'], route=i, steps=p['step_count'],
                     species=' → '.join(smiles_to_formula_fast(s) or s for s in p['species']),
                     counts=' / '.join(str(s['event_count']) for s in p['steps']), continuity='未检查')
                for i, p in enumerate(paths, 1)]
        options = [{'label': f"路线 {i} · {p['step_count']} 步", 'value': p['signature_id']}
                   for i, p in enumerate(paths, 1)]
        return route_summary(report), rows, [], options, (options[0]['value'] if options else None), not bool(report and not report.get('error')), not bool(paths), ('查询结束。' if report else '')

    @app.callback(Output('cp-comparison', 'children'), Input('cp-routes', 'selected_row_ids'), Input('cp-report', 'data'), Input('app-store', 'data'))
    def compare(selected, report, store):
        if report and report.get('context') != context_key(store):
            return ''
        return comparison([dict(p, display_rank=i) for i, p in enumerate((report or {}).get('paths') or [], 1)
                           if p['signature_id'] in (selected or [])])

    @app.callback(Output('cp-detail', 'children'), Output('cp-step', 'options'), Output('cp-step', 'value'),
                  Input('cp-focus', 'value'), Input('cp-report', 'data'), Input('app-store', 'data'))
    def detail(signature, report, store):
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
        options = [{'label': f"第 {i+1} 步 · {s['event_count']} 个事件", 'value': i} for i, s in enumerate(path['steps'])]
        return html.Div(chain, className='rs-candidate-chain'), options, 0

    @app.callback(Output('cp-step-detail', 'children'), Output('cp-event-page', 'data'),
                  Output('cp-event-status', 'children'), Output('cp-events', 'data'),
                  Output('cp-prev', 'disabled'), Output('cp-next', 'disabled'), Output('cp-open-events', 'disabled'),
                  Input('cp-step', 'value'), Input('cp-focus', 'value'), Input('cp-report', 'data'),
                  Input('cp-prev', 'n_clicks'), Input('cp-next', 'n_clicks'),
                  Input('app-store', 'data'), State('cp-event-page', 'data'))
    def events(step_index, signature, report, _prev, _next, store, previous):
        empty = ('', None, '', [], True, True, True)
        if step_index is None or not report or report.get('context') != context_key(store):
            return empty
        offset = 0
        if ctx.triggered_id in {'cp-prev', 'cp-next'} and previous and previous.get('signature_id') == signature and previous.get('step_index') == step_index:
            offset = max(0, previous['offset'] + (25 if ctx.triggered_id == 'cp-next' else -25))
        try:
            page = svc.candidate_step_events((store or {}).get('artifacts') or {}, report, signature, step_index, offset)
        except (svc.ServiceError, ValueError, OSError) as exc:
            return '', None, str(exc), [], True, True, True
        step = next(p for p in report['paths'] if p['signature_id'] == signature)['steps'][step_index]
        evidence = html.Div([html.H6('完整反应式'), html.Code(step['reaction_key']),
            html.Div([*map(structure, step['reactants']), html.Span('→'), *map(structure, step['products'])], className='rs-candidate-chain'),
            html.P(f"主线载体：{step['carried_from']} → {step['carried_to']}"),
            html.P('以下各事件只支持本步骤；它们不自动与其他步骤组成连续历史。')])
        return evidence, page, f"事件 {offset+1 if page['rows'] else 0}–{offset+len(page['rows'])} / {page['total']}；Transition 是分析帧之间的区间，不是 ps。", [
            {k: row.get(k) for k in ('event_id', 'timestep_index', 'association_status')} for row in page['rows']], offset == 0, not page['has_more'], not bool(page['rows'])

    @app.callback(Output('event-grid-store', 'data', allow_duplicate=True),
                  Output('event-grid', 'data', allow_duplicate=True), Output('event-grid', 'columns', allow_duplicate=True),
                  Output('event-reaction-text', 'value', allow_duplicate=True),
                  Output('event-alert', 'children', allow_duplicate=True),
                  Input('cp-open-events', 'n_clicks'), State('cp-event-page', 'data'),
                  State('cp-report', 'data'), State('app-store', 'data'), prevent_initial_call=True)
    def open_events(_, page, report, store):
        if not page or not report or report.get('context') != context_key(store):
            raise PreventUpdate
        # Re-read the exact page; never pass stale event table rows across tools.
        try:
            page = svc.candidate_step_events((store or {}).get('artifacts') or {}, report,
                page['signature_id'], page['step_index'], page['offset'])
        except svc.ServiceError as exc:
            return no_update, no_update, no_update, no_update, exc.message
        from .callbacks import _event_columns, _event_table_rows
        rows = page['rows']
        workflow = dict(rows=rows, kind='rng_event', config={'reaction_text': page['reaction_key'], 'max_events': 25},
                        meta={'message': '来自候选路径的一步独立事件证据'})
        return workflow, _event_table_rows(rows), _event_columns(), page['reaction_key'], '来自候选路径的独立步骤；请选择事件查看轨迹。'

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
