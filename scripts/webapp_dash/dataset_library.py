"""Reusable imported folders; one current dataset and independent comparison choices."""
from __future__ import annotations

import uuid
import json
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from reacnet_scope import services as svc


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
        dcc.Interval(id='library-index-refresh', interval=5000, disabled=False),
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
                    html.Span('索引状态: ', className='rs-index-status-label'),
                    html.Span('Species ', id={'type': 'library-entry-species-status', 'base': entry['base']},
                              className='rs-index-status-badge'),
                    html.Span(' | Event ', className='rs-index-separator'),
                    html.Span(id={'type': 'library-entry-event-status', 'base': entry['base']},
                              className='rs-index-status-badge'),
                    html.Span(' | Trajectory ', className='rs-index-separator'),
                    html.Span(id={'type': 'library-entry-trajectory-status', 'base': entry['base']},
                              className='rs-index-status-badge'),
                ], className='rs-library-index-status'),
                dbc.Button('构建缺失索引', id={'type': 'library-build-index', 'base': entry['base']},
                           n_clicks=0, color='secondary', outline=True, size='sm'),
                dbc.Button('使用此RNG 数据', id={'type': 'library-use-entry', 'base': entry['base']},
                           n_clicks=0, color='primary', outline=True),
                dbc.Button('移出列表', id={'type': 'library-forget-entry', 'base': entry['base']},
                           n_clicks=0, color='link'),
                html.Div(id={'type': 'library-entry-progress', 'base': entry['base']},
                         className='rs-library-build-progress'),
            ], className='rs-library-item'))
        return rows or html.P('尚未导入RNG 数据。点击”添加RNG 数据”导入 RNG 文件夹。')

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

    @app.callback(
        Output({'type': 'library-entry-species-status', 'base': ALL}, 'children'),
        Output({'type': 'library-entry-event-status', 'base': ALL}, 'children'),
        Output({'type': 'library-entry-trajectory-status', 'base': ALL}, 'children'),
        Input('dataset-library', 'data'),
        Input('library-index-refresh', 'n_intervals'),
        State({'type': 'library-entry-species-status', 'base': ALL}, 'id'),
    )
    def update_index_status(records, _interval, ids):
        """Update index status badges for all imported datasets."""
        if not ids:
            return [], [], []

        entries = svc.normalise_dataset_library(records)
        base_to_entry = {e['base']: e for e in entries}

        species_status = []
        event_status = []
        trajectory_status = []

        for id_dict in ids:
            base = id_dict['base']
            entry = base_to_entry.get(base)

            if not entry:
                species_status.append('?')
                event_status.append('?')
                trajectory_status.append('?')
                continue

            try:
                status = svc.dataset_preparation_status(
                    entry['folder'],
                    base=entry['base']
                )

                # Species index is always ready (built-in)
                species_status.append('✓')

                # Event index
                event_state = (status.get('events') or {}).get('state', 'missing')
                event_status.append('✓' if event_state == 'ready' else '✗')

                # Trajectory index
                traj_state = (status.get('trajectory') or {}).get('state', 'missing')
                trajectory_status.append('✓' if traj_state == 'ready' else '✗')

            except Exception:
                species_status.append('?')
                event_status.append('?')
                trajectory_status.append('?')

        return species_status, event_status, trajectory_status

    @app.callback(
        Output({'type': 'library-entry-progress', 'base': ALL}, 'children'),
        Output({'type': 'library-build-index', 'base': ALL}, 'disabled'),
        Input({'type': 'library-build-index', 'base': ALL}, 'n_clicks'),
        State('dataset-library', 'data'),
        State({'type': 'library-entry-progress', 'base': ALL}, 'id'),
        State({'type': 'library-build-index', 'base': ALL}, 'id'),
        background=True,
        progress=[
            Output({'type': 'library-entry-progress', 'base': ALL}, 'children'),
        ],
        running=[
            (Output({'type': 'library-build-index', 'base': ALL}, 'disabled'), True, False),
        ],
        prevent_initial_call=True,
    )
    def build_dataset_index(set_progress, _clicks, records, progress_ids, button_ids):
        """Build missing indices for a specific dataset entry with real-time progress tracking."""
        trigger = ctx.triggered_id
        if not isinstance(trigger, dict) or not _clicked(trigger):
            raise PreventUpdate

        target_base = trigger['base']
        entries = svc.normalise_dataset_library(records)
        target_entry = next((e for e in entries if e['base'] == target_base), None)

        if not target_entry:
            return [no_update] * len(progress_ids), [no_update] * len(button_ids)

        # Helper to update progress for target entry only
        def update_progress(message, color='info'):
            progress_updates = []
            for id_dict in progress_ids:
                if id_dict['base'] == target_base:
                    if isinstance(message, str):
                        progress_updates.append(
                            html.Div([
                                html.Span(message, style={'color': f'var(--rs-text-{color})' if color != 'info' else 'var(--rs-muted)'})
                            ])
                        )
                    else:
                        progress_updates.append(message)
                else:
                    progress_updates.append(no_update)
            set_progress([progress_updates])

        try:
            # Initial progress update
            update_progress(f'正在检查 {target_entry["label"]} 的索引状态...')

            # Get current status
            status = svc.dataset_preparation_status(
                target_entry['folder'],
                base=target_entry['base']
            )

            # Determine what needs building
            event_state = (status.get('events') or {}).get('state', 'missing')
            traj_state = (status.get('trajectory') or {}).get('state', 'missing')
            comp_state = (status.get('composition') or {}).get('state', 'missing')

            tasks_to_build = []
            if event_state not in {'ready', 'building'}:
                tasks_to_build.append('event')
            if traj_state not in {'ready', 'building'}:
                tasks_to_build.append('trajectory')
            if comp_state not in {'ready', 'building'}:
                tasks_to_build.append('composition')

            if not tasks_to_build:
                # All indices ready
                update_progress('所有索引已就绪', 'success')
                results = []
                for id_dict in progress_ids:
                    if id_dict['base'] == target_base:
                        results.append(html.Span('所有索引已就绪', style={'color': 'var(--rs-success)'}))
                    else:
                        results.append(no_update)
                return results, [no_update] * len(button_ids)

            # Build each missing index with progress updates
            build_results = []
            label_map = {
                'event': '事件索引',
                'trajectory': '轨迹索引',
                'composition': '元素分布索引',
            }

            for idx, kind in enumerate(tasks_to_build, 1):
                update_progress(f'正在构建 {label_map[kind]} ({idx}/{len(tasks_to_build)})...')

                try:
                    result = svc.prepare_dataset_workspace(
                        target_entry['folder'],
                        base=target_entry['base'],
                        kind=kind,
                    )

                    if result.get('existing_task'):
                        build_results.append(f"{label_map[kind]}已在运行")
                        update_progress(f'{label_map[kind]}已在后台运行，跳过 ({idx}/{len(tasks_to_build)})')
                    elif result.get('canceled'):
                        build_results.append(f"{label_map[kind]}已取消")
                        update_progress(f'{label_map[kind]}已取消 ({idx}/{len(tasks_to_build)})')
                    else:
                        action = "已重建" if result.get('rebuilt') else "已建立"
                        # Get record count
                        status_result = result.get('status') or {}
                        count = (
                            status_result.get('event_count')
                            if kind == 'event'
                            else status_result.get('frames')
                            if kind == 'trajectory'
                            else status_result.get('timepoints')
                        )
                        count_text = f" · {int(count):,} 条记录" if count is not None else ""
                        build_results.append(f"{label_map[kind]}{action}{count_text}")
                        update_progress(f'{label_map[kind]}{action}{count_text} ({idx}/{len(tasks_to_build)})')

                except svc.ServiceError as exc:
                    build_results.append(f"{label_map[kind]}失败: {exc.message}")
                    update_progress(f'{label_map[kind]}失败: {exc.message}', 'danger')
                except Exception as exc:
                    build_results.append(f"{label_map[kind]}失败: {str(exc)}")
                    update_progress(f'{label_map[kind]}失败: {str(exc)}', 'danger')

            # Final result message
            result_msg = html.Div([
                html.Span(' · '.join(build_results)),
                html.Small(' (索引状态将在 5 秒内自动刷新)', style={'display': 'block', 'marginTop': '4px', 'color': 'var(--rs-muted)'})
            ])

            # Update only the target entry's progress
            results = []
            for id_dict in progress_ids:
                if id_dict['base'] == target_base:
                    results.append(result_msg)
                else:
                    results.append(no_update)

            return results, [no_update] * len(button_ids)

        except Exception as exc:
            update_progress(f'错误: {str(exc)}', 'danger')
            results = []
            for id_dict in progress_ids:
                if id_dict['base'] == target_base:
                    results.append(html.Span(f'错误: {str(exc)}', style={'color': 'var(--rs-text-danger)'}))
                else:
                    results.append(no_update)
            return results, [no_update] * len(button_ids)


def management_panel():
    return html.Section([
        html.Div([html.H3('已导入RNG 数据'),
                  dbc.Button('添加RNG 数据', id='library-add-more', color='primary')],
                 className='rs-library-heading'),
        html.P('添加 RNG 文件夹并选择一个RNG 数据用于分析。移出列表不会删除原始文件。'),
        html.Div(id='library-management-list'),
    ], id='library-management-panel', className='rs-card rs-library-management')
