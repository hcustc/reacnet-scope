"""Reusable imported folders; one current dataset and independent comparison choices."""
from __future__ import annotations

import uuid
import json
import math
from dash import ALL, MATCH, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from reacnet_scope import services as svc
from reacnet_scope.indexes import IndexBuildInProgressError


_INDEX_KINDS = (
    ('composition', '物种丰度 / 元素分布', 'composition'),
    ('event', '事件检索', 'events'),
    ('trajectory', '轨迹证据', 'trajectory'),
)
_INDEX_KEYS = {kind: key for kind, _label, key in _INDEX_KINDS}


def _verified_library_target(entry):
    """Recheck a browser-stored reference before changing its workspace."""
    dataset_id = str(entry.get('dataset_id') or '')
    if not dataset_id:
        raise svc.ServiceError('RNG 数据身份缺失，请重新导入。', reason='missing_dataset_identity')
    validation = svc.validate_dataset_candidate(entry['folder'], entry['base'])
    if str(validation.get('dataset_id') or '') != dataset_id:
        raise svc.ServiceError('RNG 数据身份已变化，请重新导入。', reason='dataset_identity_changed')
    return validation


def _build_library_index(request):
    """Prepare one explicitly chosen capability for one verified library entry."""
    kind = str(request.get('kind') or '')
    if kind not in _INDEX_KEYS:
        raise svc.ServiceError('无效准备能力。', reason='invalid_preparation_kind')
    validation = _verified_library_target(request)
    if request.get('source_revision') and not svc.is_same_dataset_revision(request, validation):
        raise svc.ServiceError('来源文件已变化，请重新选择该来源。', reason='source_revision_changed')
    result = svc.prepare_dataset_workspace(
        request['folder'], base=request['base'], kind=kind,
        expected_dataset_id=request['dataset_id'],
    )
    if str(result.get('dataset_id') or '') != str(request['dataset_id']):
        raise svc.ServiceError('准备结果的数据身份不匹配，请重新检查。', reason='dataset_identity_changed')
    return result


def _library_index_control(entry, kind, item, request, result, cancel_result):
    """Render one capability without treating missing evidence as a failed build."""
    base = entry['base']
    state = str(item.get('state') or 'missing')
    task = item.get('task') or {}
    task_state = str(task.get('state') or '') if task.get('matches_current_revision') is True else ''
    active_task = task_state in {'running', 'cancel_requested'}
    source_available = bool(item.get('source_available'))
    pending = (
        isinstance(request, dict)
        and request.get('base') == base
        and request.get('kind') == kind
        and request.get('dataset_id') == entry.get('dataset_id')
        and request.get('token') != (result or {}).get('token')
    )
    progress = task.get('progress') if task.get('progress_trusted') else None
    progress_percent = (
        max(0, min(100, round(float(progress) * 100)))
        if isinstance(progress, (int, float)) and not isinstance(progress, bool)
        and math.isfinite(float(progress)) else None
    )
    if not source_available:
        state_text = '缺少源文件'
    elif active_task:
        state_text = (
            f'准备中 · {progress_percent}%'
            if progress_percent is not None else '准备中'
        )
        if task_state == 'cancel_requested':
            state_text = '正在取消'
    elif state == 'ready':
        state_text = '可用'
    elif state in {'stale', 'invalid'}:
        state_text = {'stale': '需要重建', 'invalid': '索引无效'}[state]
    elif task_state in {'interrupted', 'canceled', 'failed'}:
        state_text = {'interrupted': '已中断', 'canceled': '已取消',
                      'failed': '构建失败'}[task_state]
    elif state == 'building':
        state_text = '索引未完成'
    else:
        state_text = '尚未建立'
    if pending and not active_task:
        state_text = '正在启动准备任务'

    controls = [html.Span(state_text, className=f'rs-index-state is-{state}')]
    action = (
        '重新构建' if state in {'stale', 'invalid'}
        else '续建索引' if task_state in {'interrupted', 'canceled', 'failed'}
        else '准备索引'
    )
    controls.append(dbc.Button(
        action,
        id={'type': 'library-build-index', 'base': base, 'kind': kind},
        n_clicks=0, color='secondary', outline=True, size='sm',
        disabled=active_task or pending or not source_available or state == 'ready',
        style={'display': 'none'} if not source_available or state == 'ready' else None,
    ))
    if active_task or pending:
        bar_label = '正在取消索引准备' if task_state == 'cancel_requested' else '索引准备进度'
        known_progress = active_task and progress_percent is not None
        bar_attributes = {
            'role': 'progressbar',
            'aria-label': bar_label if known_progress else f'{bar_label}，进度未知',
            'aria-valuemin': 0,
            'aria-valuemax': 100,
        }
        if known_progress:
            bar_attributes['aria-valuenow'] = progress_percent
        controls.append(html.Div(
            html.Div(
                className='rs-library-index-progress-fill',
                style={'width': f'{progress_percent}%'} if known_progress else None,
            ),
            className=('rs-library-index-progress' if known_progress
                       else 'rs-library-index-progress is-indeterminate'),
            **bar_attributes,
        ))
    if active_task:
        controls.append(dbc.Button(
            '取消准备',
            id={'type': 'library-cancel-index', 'base': base, 'kind': kind},
            n_clicks=0, color='secondary', outline=True, size='sm',
            disabled=task_state == 'cancel_requested',
        ))
        if task.get('message'):
            controls.append(html.Small(str(task['message'])))

    for feedback in (result, cancel_result):
        if (isinstance(feedback, dict) and feedback.get('base') == base
                and feedback.get('kind') == kind
                and feedback.get('dataset_id') == entry.get('dataset_id')
                and feedback.get('message')):
            controls.append(html.Small(str(feedback['message']), role='status'))
    return controls


def _control_snapshot(value):
    """Compare visible control state without resetting a button's click count."""
    if hasattr(value, 'to_plotly_json'):
        value = value.to_plotly_json()
    if isinstance(value, dict):
        return {key: _control_snapshot(item) for key, item in value.items() if key != 'n_clicks'}
    if isinstance(value, list):
        return [_control_snapshot(item) for item in value]
    return value


def _clicked(trigger):
    for group in ctx.inputs_list:
        for item in group if isinstance(group, list) else [group]:
            component_id = item.get('id')
            if isinstance(component_id, str):
                try:
                    component_id = json.loads(component_id)
                except (TypeError, ValueError):
                    continue
            if component_id == trigger and isinstance(item.get('value'), (int, float)):
                return item['value'] > 0
    return False


def stores():
    return html.Div([
        dcc.Store(id='dataset-library', storage_type='local', data=[]),
        dcc.Store(id='library-draft', data=[]),
        dcc.Store(id='library-request'),
        dcc.Store(id='library-result'),
        dcc.Store(id='library-seen-current'),
        dcc.Store(id='library-cancel-result'),
        dcc.Store(id='library-index-refresh', data=0),
    ])


def selector():
    return html.Div([
        html.Label('切换到已导入数据', htmlFor='library-select'),
        dcc.Dropdown(id='library-select', options=[], placeholder='选择已导入的 RNG 文件夹', clearable=False),
        html.Small('选择后点击切换，验证成功才更新当前数据。'),
        dbc.Button('切换数据', id='library-use', color='primary', size='sm', disabled=True),
        html.Div(id='library-switch-status', role='status', **{'aria-live': 'polite'}),
    ], className='rs-dataset-switch-options')


def current_dataset_menu():
    """One current-data identity and an explicit switch, shared by both shells."""
    return html.Details([
        html.Summary([
            html.Span('当前 RNG 数据', className='rs-dataset-caption'),
            html.Span(id='topbar-rungroup', children='未选择', className='rs-dataset-label'),
            html.Span(id='topbar-status', children='未加载数据', className='rs-badge rs-bad',
                      role='status', **{'aria-live': 'polite'}),
            html.Span('▾', className='rs-dataset-chevron', **{'aria-hidden': 'true'}),
        ], title='查看当前数据或切换已导入数据'),
        html.Div(selector(), className='rs-dataset-popover'),
        html.Span(id='topbar-folder', children='未选择', hidden=True),
    ], id='current-dataset-menu', className='rs-dataset-menu')


def toolbar_actions():
    return html.Div([
        html.Span(id='topbar-index-status', className='rs-index-global-state'),
        dbc.Button('添加数据', id='data-pick-btn', color='secondary', size='sm', outline=True),
        dbc.DropdownMenu([
            dbc.DropdownMenuItem('RNG 数据与准备任务', id='open-data-modal'),
            dbc.DropdownMenuItem('刷新索引状态', id='data-prep-refresh-btn'),
        ], label='数据与任务', color='secondary', size='sm',
            toggle_style={'background': 'white', 'color': 'var(--rs-muted)'},
            align_end=True, className='rs-data-menu'),
    ], className='rs-dataset-actions ms-auto')


def import_panel():
    return html.Section([
        html.Div([html.H3('批量导入文件夹'), html.Small('可选：先把多个文件夹导入列表，再到各分析页选择使用')],
                 className='rs-library-import-heading'),
        dbc.Button('加入当前文件夹', id='library-add-current', color='secondary', outline=True, size='sm'),
        html.Details([html.Summary('一次填写多个文件夹路径'),
                      dbc.Textarea(id='library-paths', placeholder='每行一个 RNG 输出文件夹；各自作为独立RNG 数据', rows=3),
                      dbc.Button('加入待导入列表', id='library-add-paths', size='sm', color='secondary')]),
        html.Div(id='library-draft-list'),
        html.Div(id='library-draft-feedback', role='status'),
        html.Div([dbc.Button('导入所选文件夹', id='library-import', color='primary', size='sm'),
                  dbc.Button('取消导入', id='library-import-cancel', color='secondary', size='sm', disabled=True)], className='rs-result-actions'),
        html.Div(id='library-import-status', role='status'),
        html.Details([html.Summary('已导入的RNG 数据'), html.Div(id='library-catalog-list')]),
    ], className='rs-library-import')


def register_callbacks(app):
    @app.callback(Output('library-management-list', 'children'),
                  Input('dataset-library', 'data'), State('app-store', 'data'))
    def management(records, current):
        entries = svc.normalise_dataset_library(records)
        rows = []
        for entry in entries:
            active = entry['base'] == (current or {}).get('base')
            rows.append(html.Div([
                html.Div([
                    html.Strong(entry['label']),
                    html.Small(entry['folder']),
                    html.Span('当前使用' if active else '已导入',
                              id={'type': 'library-entry-current', 'base': entry['base']},
                              className=('rs-library-entry-status is-current' if active
                                         else 'rs-library-entry-status'))
                ]),
                html.Div([
                    html.Div([
                        html.Span(label, className='rs-index-status-label'),
                        html.Div([
                            html.Div('正在检查…',
                                     id={'type': 'library-index-control', 'base': entry['base'], 'kind': kind},
                                     className='rs-library-index-state'),
                            dbc.Button('准备索引',
                                       id={'type': 'library-build-index', 'base': entry['base'], 'kind': kind},
                                       n_clicks=0, color='secondary', outline=True, size='sm',
                                       disabled=True, style={'display': 'none'}),
                            dbc.Button('取消准备',
                                       id={'type': 'library-cancel-index', 'base': entry['base'], 'kind': kind},
                                       n_clicks=0, color='secondary', outline=True, size='sm',
                                       disabled=True, style={'display': 'none'}),
                        ], className='rs-library-index-control'),
                        dcc.Store(id={'type': 'library-build-request', 'base': entry['base'], 'kind': kind}),
                        dcc.Store(id={'type': 'library-build-result', 'base': entry['base'], 'kind': kind}),
                    ], className='rs-library-index-row')
                    for kind, label, _key in _INDEX_KINDS
                ], className='rs-library-index-status'),
                html.Div([
                    dbc.Button('切换到此数据', id={'type': 'library-use-entry', 'base': entry['base']},
                               n_clicks=0, color='primary', outline=True,
                               style={'display': 'none'} if active else None),
                    html.Span(id={'type': 'library-switch-feedback', 'base': entry['base']},
                              className='rs-library-switch-feedback', role='status',
                              **{'aria-live': 'polite'}),
                ], className='rs-library-use-control'),
                dbc.Button('移出列表', id={'type': 'library-forget-entry', 'base': entry['base']},
                           n_clicks=0, color='link', disabled=active,
                           title='仅移出列表，不删除原始文件'),
            ], className='rs-library-item'))
        return rows or html.P('尚未导入RNG 数据。点击”添加数据”导入 RNG 文件夹。')

    @app.callback(Output({'type': 'library-entry-current', 'base': ALL}, 'children'),
                  Output({'type': 'library-entry-current', 'base': ALL}, 'className'),
                  Input('app-store', 'data'), Input({'type': 'library-entry-current', 'base': ALL}, 'id'))
    def current_entry(current, ids):
        active_base = (current or {}).get('base')
        active = [item['base'] == active_base for item in ids or []]
        return (['当前使用' if selected else '已导入' for selected in active],
                ['rs-library-entry-status is-current' if selected
                 else 'rs-library-entry-status' for selected in active])

    @app.callback(
        Output({'type': 'library-use-entry', 'base': ALL}, 'children'),
        Output({'type': 'library-use-entry', 'base': ALL}, 'disabled'),
        Output({'type': 'library-use-entry', 'base': ALL}, 'style'),
        Output({'type': 'library-switch-feedback', 'base': ALL}, 'children'),
        Output({'type': 'library-forget-entry', 'base': ALL}, 'disabled'),
        Input('dataset-switch-transaction', 'data'),
        Input('app-store', 'data'),
        Input({'type': 'library-use-entry', 'base': ALL}, 'n_clicks'),
        Input({'type': 'library-use-entry', 'base': ALL}, 'id'),
    )
    def entry_switch_state(transaction, current, _clicks, ids):
        request = transaction if isinstance(transaction, dict) else {}
        active_base = str((current or {}).get('base') or '')
        target_base = str((request.get('candidate') or {}).get('base') or '')
        state = str(request.get('state') or '')
        pending_base = ''
        if isinstance(ctx.triggered_id, dict) and ctx.triggered_id.get('type') == 'library-use-entry':
            if _clicked(ctx.triggered_id):
                pending_base = str(ctx.triggered_id.get('base') or '')
        applying = state == 'succeeded' and target_base != active_base
        busy = bool(pending_base or state == 'validating' or applying)
        labels, disabled, styles, feedback, forget_disabled = [], [], [], [], []
        for item in ids or []:
            base = str(item['base'])
            active = bool(active_base and base == active_base)
            selected = base == (pending_base or target_base)
            labels.append('正在加载…' if selected and busy else '切换到此数据')
            disabled.append(active or busy)
            styles.append({'display': 'none'} if active else {})
            forget_disabled.append(active or busy)
            if selected and busy:
                message = ('正在发起切换…' if pending_base else
                           '正在检查RNG 数据…' if state == 'validating' else
                           '正在载入分析上下文…')
                feedback.append(html.Span([
                    html.Span(className='rs-library-switch-spinner', **{'aria-hidden': 'true'}),
                    message,
                ]))
            elif selected and state == 'failed':
                feedback.append(html.Span(
                    str(request.get('message') or '切换失败，请重试。'),
                    className='rs-library-switch-error',
                ))
            elif selected and active and state == 'succeeded':
                feedback.append(html.Span([
                    html.Span('✓', className='rs-library-success-icon',
                              **{'aria-hidden': 'true'}),
                    '加载成功，已设为当前 RNG 数据',
                ], className='rs-library-switch-success'))
            else:
                feedback.append('')
        return labels, disabled, styles, feedback, forget_disabled

    @app.callback(Output('library-draft', 'data'), Output('library-draft-feedback', 'children'),
                  Input('library-add-current', 'n_clicks'), Input('library-add-paths', 'n_clicks'),
                  Input({'type': 'library-add-folder', 'path': ALL}, 'n_clicks'),
                  Input({'type': 'library-remove-draft', 'path': ALL}, 'n_clicks'),
                  State('import-location', 'data'), State('library-paths', 'value'),
                  State('library-draft', 'data'), prevent_initial_call=True)
    def draft(_current, _paths, _folders, _remove, location, text, existing):
        trigger = ctx.triggered_id
        paths = list(existing or [])
        if isinstance(trigger, dict):
            if not _clicked(trigger):
                raise PreventUpdate
            path = trigger['path']
            if trigger['type'] == 'library-remove-draft':
                return [p for p in paths if p != path], ''
            added = [path]
        elif trigger == 'library-add-current':
            added = [(location or {}).get('path')]
        else:
            added = str(text or '').splitlines()
        paths = list(dict.fromkeys([*paths, *(str(p).strip() for p in added if p and str(p).strip())]))
        if len(paths) > 100:
            return no_update, '每次最多导入 100 个文件夹，请分批添加。'
        return paths, '' if paths else '请先打开或填写 RNG 输出文件夹。'

    @app.callback(Output('library-draft-list', 'children'), Input('library-draft', 'data'))
    def draft_list(paths):
        return [html.Div([html.Span(path, title=path), dbc.Button('移除', size='sm', color='link',
                    id={'type': 'library-remove-draft', 'path': path}, n_clicks=0)], className='rs-library-item')
                for path in paths or []] or html.P('可直接导入当前文件夹。批量导入时，可逐个加入列表，也可一次填写多个路径。')

    @app.callback(Output('library-import', 'children'), Input('library-draft', 'data'))
    def import_label(paths):
        return f'导入列表中的 {len(paths)} 个文件夹' if paths else '导入当前文件夹'

    @app.callback(Output('library-request', 'data'),
                  Input('library-import', 'n_clicks'), Input('library-import-cancel', 'n_clicks'),
                  State('library-draft', 'data'), State('import-location', 'data'), prevent_initial_call=True)
    def request(_import, _cancel, paths, location):
        if ctx.triggered_id == 'library-import-cancel':
            return {'token': uuid.uuid4().hex, 'cancelled': True}
        selected = list(paths or [])
        if not selected and (location or {}).get('path'):
            selected = [location['path']]
        return {'token': uuid.uuid4().hex, 'paths': selected}

    @app.callback(Output('library-result', 'data'), Input('library-request', 'data'),
                  background=True, cancel=[Input('library-import-cancel', 'n_clicks')],
                  running=[(Output('library-import', 'disabled'), True, False),
                           (Output('library-import-cancel', 'disabled'), False, True)],
                  prevent_initial_call=True)
    def inspect(request):
        if not request or request.get('cancelled') or not request.get('paths'):
            raise PreventUpdate
        try:
            result = svc.inspect_dataset_folders(request.get('paths'))
        except svc.ServiceError as exc:
            result = {'entries': [], 'errors': [{'path': '', 'message': str(exc)}]}
        return {**result, 'token': request['token']}

    @app.callback(Output('dataset-library', 'data'), Output('library-seen-current', 'data'),
                  Input('library-result', 'data'), Input('app-store', 'data'),
                  Input({'type': 'library-forget', 'base': ALL}, 'n_clicks'),
                  Input({'type': 'library-forget-entry', 'base': ALL}, 'n_clicks'),
                  State('library-request', 'data'), State('dataset-library', 'data'), State('library-seen-current', 'data'))
    def commit(result, current, _forget, _forget_entry, request, catalog, seen_current):
        entries = svc.normalise_dataset_library(catalog)
        trigger = ctx.triggered_id
        if isinstance(trigger, dict):
            if not _clicked(trigger):
                raise PreventUpdate
            return [e for e in entries if e['base'] != trigger['base']], no_update
        if trigger == 'library-result':
            if not result or result.get('token') != (request or {}).get('token') or (request or {}).get('cancelled'):
                raise PreventUpdate
            additions = result.get('entries', [])
        else:
            if not (current or {}).get('dataset_id') or current.get('base') == seen_current:
                raise PreventUpdate
            additions = [current]
        merged = svc.normalise_dataset_library([*entries, *additions])
        return (no_update if merged == entries else merged,
                (current or {}).get('base') if trigger != 'library-result' else no_update)

    @app.callback(Output('library-import-status', 'children'),
                  Input('library-request', 'data'), Input('library-result', 'data'))
    def status(request, result):
        if not request:
            return ''
        if request.get('cancelled'):
            return '已取消本次导入，已导入的RNG 数据保留。'
        if not request.get('paths'):
            return '请先打开需要导入的 RNG 输出文件夹，或加入待导入列表。'
        if (result or {}).get('token') != request.get('token'):
            return f"正在检查 {len(request.get('paths', []))} 个文件夹…"
        entries, errors = result.get('entries', []), result.get('errors', [])
        return html.Div([html.Strong(f'已导入 {len(entries)} 个文件夹；{len(errors)} 个未导入。'),
                         *[html.P([html.Code(e['path']), '：', e['message']]) for e in errors]])

    @app.callback(Output('library-select', 'options'), Output('library-catalog-list', 'children'),
                  Input('dataset-library', 'data'), Input('app-store', 'data'))
    def catalog(records, current):
        entries = svc.normalise_dataset_library(records)
        options = [{'label': f"{e['label']} · {e['folder']}", 'value': e['base']} for e in entries]
        rows = [html.Div([html.Span([html.Strong(e['label']), html.Small(e['folder'])]),
                         dbc.Button('移出列表', id={'type': 'library-forget', 'base': e['base']},
                                    n_clicks=0, size='sm', color='link')], className='rs-library-item') for e in entries]
        return options, rows or '尚未导入RNG 数据。'

    @app.callback(Output('library-use', 'disabled'), Output('library-switch-status', 'children'),
                  Input('library-select', 'value'), Input('dataset-library', 'data'),
                  Input('dataset-switch-transaction', 'data'),
                  Input({'type': 'dataset-bound-operation', 'name': ALL}, 'data'))
    def switch_status(selected, records, transaction, operations):
        busy = (transaction or {}).get('state') == 'validating'
        message = '正在切换…' if busy else ''
        if (transaction or {}).get('state') == 'failed':
            message = transaction.get('message', '')
        valid = any(e['base'] == selected for e in svc.normalise_dataset_library(records))
        return not valid or busy or any(operations or []), message

    @app.callback(
        Output({'type': 'library-index-control', 'base': ALL, 'kind': ALL}, 'children'),
        Output({'type': 'library-build-index', 'base': ALL, 'kind': ALL}, 'children'),
        Output({'type': 'library-build-index', 'base': ALL, 'kind': ALL}, 'disabled'),
        Output({'type': 'library-build-index', 'base': ALL, 'kind': ALL}, 'style'),
        Output({'type': 'library-cancel-index', 'base': ALL, 'kind': ALL}, 'disabled'),
        Output({'type': 'library-cancel-index', 'base': ALL, 'kind': ALL}, 'style'),
        Input('dataset-library', 'data'),
        Input('library-index-refresh', 'data'),
        Input({'type': 'library-index-control', 'base': ALL, 'kind': ALL}, 'id'),
        State({'type': 'library-build-request', 'base': ALL, 'kind': ALL}, 'data'),
        State({'type': 'library-build-result', 'base': ALL, 'kind': ALL}, 'data'),
        State('library-cancel-result', 'data'),
        State({'type': 'library-index-control', 'base': ALL, 'kind': ALL}, 'children'),
        State({'type': 'library-build-index', 'base': ALL, 'kind': ALL}, 'id'),
        State({'type': 'library-cancel-index', 'base': ALL, 'kind': ALL}, 'id'),
    )
    def update_index_status(records, _refresh, ids, requests, results,
                            cancel_result, previous, build_ids, cancel_ids):
        """Read published indexes and persisted task progress for visible entries."""
        entries = {entry['base']: entry for entry in svc.normalise_dataset_library(records)}
        requests_by_key = {(item['base'], item['kind']): item for item in requests or []
                           if isinstance(item, dict) and item.get('base') and item.get('kind')}
        results_by_key = {(item['base'], item['kind']): item for item in results or []
                          if isinstance(item, dict) and item.get('base') and item.get('kind')}
        statuses = {}
        rendered = []
        button_state = {}
        for control_id in ids or []:
            base, kind = control_id['base'], control_id['kind']
            key = (base, kind)
            entry = entries.get(base)
            if entry is None:
                rendered.append('已移出列表')
                button_state[key] = ('准备索引', True, {'display': 'none'}, True, {'display': 'none'})
                continue
            if base not in statuses:
                try:
                    svc.validate_browse_path(entry['folder'])
                    svc.validate_browse_path(entry['base'])
                    status = svc.dataset_preparation_status(entry['folder'], base=entry['base'])
                    if str(status.get('dataset_id') or '') != str(entry.get('dataset_id') or ''):
                        raise svc.ServiceError('RNG 数据身份已变化，请重新导入。', reason='dataset_identity_changed')
                    statuses[base] = status
                except (svc.ServiceError, OSError, IndexBuildInProgressError) as exc:
                    statuses[base] = exc
            status = statuses[base]
            if isinstance(status, (svc.ServiceError, OSError, IndexBuildInProgressError)):
                rendered.append(html.Span(f'状态不可用：{status}', role='status'))
                button_state[key] = ('准备索引', True, {'display': 'none'}, True, {'display': 'none'})
                continue
            controls = _library_index_control(
                entry, kind, status.get(_INDEX_KEYS[kind]) or {},
                requests_by_key.get(key), results_by_key.get(key), cancel_result,
            )
            content = [item for item in controls if not getattr(item, 'id', None)]
            build = next(item for item in controls
                         if (getattr(item, 'id', None) or {}).get('type') == 'library-build-index')
            cancel = next((item for item in controls
                           if (getattr(item, 'id', None) or {}).get('type') == 'library-cancel-index'), None)
            button_state[key] = (build.children, build.disabled, build.style,
                                 cancel.disabled if cancel else True,
                                 getattr(cancel, 'style', None) if cancel else {'display': 'none'})
            prior = previous[len(rendered)] if previous and len(previous) > len(rendered) else None
            rendered.append(no_update if _control_snapshot(content) == _control_snapshot(prior)
                            else content)
        hidden = ('准备索引', True, {'display': 'none'}, True, {'display': 'none'})
        build_state = [button_state.get((item['base'], item['kind']), hidden) for item in build_ids or []]
        cancel_state = [button_state.get((item['base'], item['kind']), hidden) for item in cancel_ids or []]
        return (rendered,
                [item[0] for item in build_state],
                [item[1] for item in build_state],
                [item[2] for item in build_state],
                [item[3] for item in cancel_state],
                [item[4] for item in cancel_state])

    @app.callback(
        Output({'type': 'library-build-request', 'base': MATCH, 'kind': MATCH}, 'data'),
        Input({'type': 'library-build-index', 'base': MATCH, 'kind': MATCH}, 'n_clicks'),
        State('dataset-library', 'data'), prevent_initial_call=True,
    )
    def request_library_index(clicks, records):
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not clicks:
            raise PreventUpdate
        entry = next((item for item in svc.normalise_dataset_library(records)
                      if item['base'] == trigger['base']), None)
        if entry is None:
            raise PreventUpdate
        return {**entry, 'kind': trigger['kind'], 'token': uuid.uuid4().hex}

    @app.callback(
        Output({'type': 'library-build-result', 'base': MATCH, 'kind': MATCH}, 'data'),
        Input({'type': 'library-build-request', 'base': MATCH, 'kind': MATCH}, 'data'),
        background=True, prevent_initial_call=True,
    )
    def build_library_index(request):
        if not isinstance(request, dict) or not request.get('token'):
            raise PreventUpdate
        try:
            result = _build_library_index(request)
        except (svc.ServiceError, OSError, IndexBuildInProgressError) as exc:
            return {**request, 'ok': False, 'message': str(exc)}
        message = (
            '同类任务已在运行。' if result.get('existing_task')
            else '任务已取消，检查点已保留。' if result.get('canceled')
            else '索引已重建。' if result.get('rebuilt')
            else '索引已就绪。'
        )
        return {**request, 'ok': True, 'message': message}

    @app.callback(
        Output('library-cancel-result', 'data'),
        Input({'type': 'library-cancel-index', 'base': ALL, 'kind': ALL}, 'n_clicks'),
        State('dataset-library', 'data'), prevent_initial_call=True,
    )
    def cancel_library_index(_clicks, records):
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not _clicked(trigger):
            raise PreventUpdate
        entry = next((item for item in svc.normalise_dataset_library(records)
                      if item['base'] == trigger['base']), None)
        if entry is None:
            raise PreventUpdate
        try:
            _verified_library_target(entry)
            result = svc.cancel_dataset_preparation(
                entry['folder'], base=entry['base'], kind=trigger['kind'],
            )
            message = result['message']
        except svc.ServiceError as exc:
            message = exc.message
        return {**entry, 'kind': trigger['kind'], 'message': message}


def management_panel(*, current_summary=None):
    return html.Section([
        html.Div([html.H3('RNG 数据管理'),
                  dbc.Button('添加数据', id='library-add-more', color='primary')],
                 className='rs-library-heading'),
        *([current_summary] if current_summary is not None else []),
        html.Div(id='library-management-list'),
    ], id='library-management-panel', className='rs-library-management')
