"""Reusable imported folders; one current dataset and independent comparison choices."""
from __future__ import annotations

import uuid
import json
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from reacnet_scope import services as svc


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
    _verified_library_target(request)
    result = svc.prepare_dataset_workspace(
        request['folder'], base=request['base'], kind=kind,
    )
    if str(result.get('dataset_id') or '') != str(request['dataset_id']):
        raise svc.ServiceError('准备结果的数据身份不匹配，请重新检查。', reason='dataset_identity_changed')
    return result


def _library_index_control(entry, kind, item, request, result, cancel_result):
    """Render one capability without treating missing evidence as a failed build."""
    base = entry['base']
    state = str(item.get('state') or 'missing')
    task = item.get('task') or {}
    task_state = str(task.get('state') or '')
    source_available = bool(item.get('source_available'))
    pending = (
        isinstance(request, dict)
        and request.get('base') == base
        and request.get('kind') == kind
        and request.get('dataset_id') == entry.get('dataset_id')
        and request.get('token') != (result or {}).get('token')
    )
    progress = task.get('progress') if task.get('progress_trusted') else None
    if not source_available:
        state_text = '缺少源文件'
    elif state == 'building':
        state_text = (
            f'准备中 · {float(progress) * 100:.0f}%'
            if isinstance(progress, (int, float)) else '准备中'
        )
        if task_state == 'cancel_requested':
            state_text = '正在取消'
    elif state == 'ready':
        state_text = '可用'
    elif task_state in {'interrupted', 'canceled', 'failed', 'superseded'}:
        state_text = {'interrupted': '已中断', 'canceled': '已取消',
                      'failed': '构建失败', 'superseded': '来源已变化'}[task_state]
    else:
        state_text = {'stale': '需要重建', 'invalid': '索引无效'}.get(state, '尚未建立')
    if pending and state != 'building':
        state_text = '正在启动准备任务'

    controls = [html.Span(state_text, className=f'rs-index-state is-{state}')]
    if source_available and state != 'ready':
        action = (
            '续建索引' if task_state in {'interrupted', 'canceled', 'failed', 'superseded'}
            else '重新构建' if state in {'stale', 'invalid'}
            else '准备索引'
        )
        controls.append(dbc.Button(
            action,
            id={'type': 'library-build-index', 'base': base, 'kind': kind},
            n_clicks=0, color='secondary', outline=True, size='sm',
            disabled=state == 'building' or pending,
        ))
    if state == 'building':
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
        dcc.Store(id='library-build-request'),
        dcc.Store(id='library-build-result'),
        dcc.Store(id='library-cancel-result'),
        dcc.Interval(id='library-index-refresh', interval=5000, disabled=True),
    ])


def selector():
    return html.Div([
        html.Label('选择已导入数据', htmlFor='library-select', className='visually-hidden'),
        dcc.Dropdown(id='library-select', options=[], placeholder='选择已导入的 RNG 文件夹', clearable=False),
        dbc.Button('使用', id='library-use', color='primary', size='sm', disabled=True),
        html.Span(id='library-switch-status', role='status'),
    ], className='rs-library-selector')


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
    @app.callback(Output('library-view', 'value'),
                  Input('nav-data-management', 'n_clicks'), Input('open-data-modal', 'n_clicks'),
                  Input('data-browser-index-btn', 'n_clicks'), Input('page-capability-manage-btn', 'n_clicks'),
                  Input('cp-prepare', 'n_clicks'), prevent_initial_call=True)
    def choose_view(*_):
        return 'library' if ctx.triggered_id == 'nav-data-management' else 'tasks'

    @app.callback(Output('library-management-panel', 'style'), Output('library-tasks-panel', 'style'),
                  Input('library-view', 'value'))
    def show_view(view):
        return ({'display': 'none'}, {}) if view == 'tasks' else ({}, {'display': 'none'})

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
                              id={'type': 'library-entry-current', 'base': entry['base']})
                ]),
                html.Div([
                    html.Div([
                        html.Span(label, className='rs-index-status-label'),
                        html.Span('正在检查…',
                                  id={'type': 'library-index-control', 'base': entry['base'], 'kind': kind},
                                  className='rs-library-index-control'),
                    ], className='rs-library-index-row')
                    for kind, label, _key in _INDEX_KINDS
                ], className='rs-library-index-status'),
                dbc.Button('使用此RNG 数据', id={'type': 'library-use-entry', 'base': entry['base']},
                           n_clicks=0, color='primary', outline=True),
                dbc.Button('移出列表', id={'type': 'library-forget-entry', 'base': entry['base']},
                           n_clicks=0, color='link'),
            ], className='rs-library-item'))
        return rows or html.P('尚未导入RNG 数据。点击“添加RNG 数据”导入 RNG 文件夹。')

    @app.callback(Output({'type': 'library-entry-current', 'base': ALL}, 'children'),
                  Input('app-store', 'data'), Input({'type': 'library-entry-current', 'base': ALL}, 'id'))
    def current_entry(current, ids):
        return ['当前使用' if item['base'] == (current or {}).get('base') else '已导入' for item in ids or []]

    @app.callback(Output('library-select', 'value'), Output('library-use', 'n_clicks'),
                  Input({'type': 'library-use-entry', 'base': ALL}, 'n_clicks'),
                  State('library-use', 'n_clicks'), prevent_initial_call=True)
    def use_entry(_clicks, previous):
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not _clicked(trigger):
            raise PreventUpdate
        return trigger['base'], (previous or 0) + 1

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

    @app.callback(Output('library-index-refresh', 'disabled'),
                  Input('page-store', 'data'), Input('library-view', 'value'))
    def refresh_library_only_when_visible(page_store, view):
        return (page_store or {}).get('page') != 'data-management' or view != 'library'

    @app.callback(
        Output({'type': 'library-index-control', 'base': ALL, 'kind': ALL}, 'children'),
        Input('dataset-library', 'data'),
        Input('library-index-refresh', 'n_intervals'),
        Input('library-build-request', 'data'),
        Input('library-build-result', 'data'),
        Input('library-cancel-result', 'data'),
        Input({'type': 'library-index-control', 'base': ALL, 'kind': ALL}, 'id'),
        State({'type': 'library-index-control', 'base': ALL, 'kind': ALL}, 'children'),
    )
    def update_index_status(records, _interval, request, result, cancel_result, ids, previous):
        """Read published indexes and persisted task progress for visible entries."""
        entries = {entry['base']: entry for entry in svc.normalise_dataset_library(records)}
        statuses = {}
        rendered = []
        for control_id in ids or []:
            base, kind = control_id['base'], control_id['kind']
            entry = entries.get(base)
            if entry is None:
                rendered.append('已移出列表')
                continue
            if base not in statuses:
                try:
                    svc.validate_browse_path(entry['folder'])
                    svc.validate_browse_path(entry['base'])
                    status = svc.dataset_preparation_status(entry['folder'], base=entry['base'])
                    if str(status.get('dataset_id') or '') != str(entry.get('dataset_id') or ''):
                        raise svc.ServiceError('RNG 数据身份已变化，请重新导入。', reason='dataset_identity_changed')
                    statuses[base] = status
                except svc.ServiceError as exc:
                    statuses[base] = exc
            status = statuses[base]
            if isinstance(status, svc.ServiceError):
                rendered.append(f'状态不可用：{status.message}')
                continue
            controls = _library_index_control(
                entry, kind, status.get(_INDEX_KEYS[kind]) or {},
                request, result, cancel_result,
            )
            prior = previous[len(rendered)] if previous and len(previous) > len(rendered) else None
            rendered.append(no_update if _control_snapshot(controls) == _control_snapshot(prior)
                            else controls)
        return rendered

    @app.callback(
        Output('library-build-request', 'data'),
        Input({'type': 'library-build-index', 'base': ALL, 'kind': ALL}, 'n_clicks'),
        State('dataset-library', 'data'), prevent_initial_call=True,
    )
    def request_library_index(_clicks, records):
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not _clicked(trigger):
            raise PreventUpdate
        entry = next((item for item in svc.normalise_dataset_library(records)
                      if item['base'] == trigger['base']), None)
        if entry is None:
            raise PreventUpdate
        return {**entry, 'kind': trigger['kind'], 'token': uuid.uuid4().hex}

    @app.callback(
        Output('library-build-result', 'data'), Input('library-build-request', 'data'),
        background=True, prevent_initial_call=True,
    )
    def build_library_index(request):
        if not isinstance(request, dict) or not request.get('token'):
            raise PreventUpdate
        try:
            result = _build_library_index(request)
        except svc.ServiceError as exc:
            return {**request, 'ok': False, 'message': exc.message}
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


def management_panel():
    return html.Section([
        html.Div([html.H3('已导入RNG 数据'),
                  dbc.Button('添加RNG 数据', id='library-add-more', color='primary')],
                 className='rs-library-heading'),
        html.P('添加 RNG 文件夹并选择一个RNG 数据用于分析。移出列表不会删除原始文件。'),
        html.Div(id='library-management-list'),
    ], id='library-management-panel', className='rs-card rs-library-management')
