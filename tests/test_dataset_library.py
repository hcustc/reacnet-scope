import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from reacnet_scope import services as svc, dir_browser
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.app import _data_management_page
from scripts.webapp_dash import callbacks as cb, dataset_library
from scripts.webapp_dash.callbacks import _batch_managed_dataset_catalog
from scripts.webapp_dash.dataset_library import (
    _build_library_index, _control_snapshot, _library_index_control,
)
from tests.test_dataset_switch_callbacks import _payload


def _component_by_id(node, component_id):
    if getattr(node, 'id', None) == component_id:
        return node
    children = getattr(node, 'children', None)
    if children is None:
        return None
    for child in children if isinstance(children, (list, tuple)) else [children]:
        found = _component_by_id(child, component_id)
        if found is not None:
            return found
    return None


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(dir_browser, 'ALLOWED_ROOTS', [tmp_path])
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path / 'cache'))
    paths = []
    for name in ('A', 'B'):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
        (folder / 'run.species').write_text('Timestep 0: CCO 1\n')
        paths.append(str(folder))
    return paths


def test_import_folders_keeps_independent_identities_and_reports_partial_failures(folders):
    empty = Path(folders[0]).parent / 'empty'
    empty.mkdir()
    report = svc.inspect_dataset_folders([folders[0], folders[1], folders[0], str(empty), '/outside'])
    assert [e['label'] for e in report['entries']] == ['A', 'B']
    assert len({e['dataset_id'] for e in report['entries']}) == 2
    assert len(report['errors']) == 2
    for path in folders:
        assert (Path(path) / 'run.reactionabcd').read_text() == '1 CCO->COC\n'


def test_ambiguous_folder_is_not_silently_merged(folders):
    (Path(folders[0]) / 'second.reactionabcd').write_text('1 O->O\n')
    report = svc.inspect_dataset_folders([folders[0]])
    assert report['entries'] == []
    assert len(report['errors']) == 1


def test_import_does_not_evict_older_catalog_entries_at_recent_history_limit():
    records = [{'folder': f'/data/{i}', 'base': f'/data/{i}/run', 'label': str(i)} for i in range(25)]
    normalized = svc.normalise_dataset_library([*records, records[0]])
    assert len(normalized) == 25
    catalog = _batch_managed_dataset_catalog({}, [], normalized)
    assert len(catalog['options']) == 25
    assert all(option['label'].startswith('已导入') for option in catalog['options'])


def post(client, output, changed, inputs, states):
    return client.post('/_dash-update-component', json=_payload(client,
        output_contains=output, changed=changed, inputs=inputs, states=states))


def test_cancelled_or_replaced_import_cannot_register_late_entries():
    client = create_app().server.test_client()
    entry = {'folder': '/data/A', 'base': '/data/A/run'}
    for request in ({'token': 'new'}, {'token': 'old', 'cancelled': True}):
        response = post(client, 'dataset-library.data', 'library-result.data',
                        {'library-result': {'token': 'old', 'entries': [entry]}},
                        {'library-request': request, 'dataset-library': []})
        assert response.status_code == 204


def test_batch_registration_does_not_change_current_dataset(folders):
    client = create_app().server.test_client()
    result = {**svc.inspect_dataset_folders(folders), 'token': 'batch'}
    response = post(client, 'dataset-library.data', 'library-result.data',
                    {'library-result': result, 'app-store': {'dataset_id': 'unchanged'}},
                    {'library-request': {'token': 'batch'}, 'dataset-library': []})
    assert response.status_code == 200
    payload = response.get_json()['response']
    assert len(payload['dataset-library']['data']) == 2
    assert 'app-store' not in payload and 'dataset-session-store' not in payload


def test_using_imported_folder_uses_existing_switch_transaction_and_preserves_origin(folders):
    client = create_app().server.test_client()
    entry = svc.inspect_dataset_folders([folders[1]])['entries'][0]
    response = post(client, 'dataset-switch-request.data', 'library-use.n_clicks',
                    {'library-use': 1, 'page-store': {'page': 'reactions'}},
                    {'dataset-library': [entry], 'library-select': entry['base'],
                     'dataset-switch-transaction': {}})
    assert response.status_code == 200
    request = response.get_json()['response']['dataset-switch-request']['data']
    assert request['candidate']['base'] == entry['base']
    assert request['origin'] == {'page': 'reactions', 'library': True}
    assert request['state'] == 'validating'
    validation = svc.validate_dataset_candidate(entry['folder'], entry['base'])
    response = post(client, 'dataset-context-commit.data', 'dataset-switch-transaction.data',
                    {'dataset-switch-transaction': {**request, 'state': 'succeeded', 'validation': validation}},
                    {'app-store': {}, 'recent-datasets': []})
    assert response.status_code == 200
    body = response.get_json()['response']
    assert body['dataset-switch-navigation']['data']['page'] == 'reactions'
    assert body['app-store']['data']['dataset_id'] == validation['dataset_id']


def test_library_row_click_starts_switch_without_hidden_selector(folders, monkeypatch):
    """A row click must enter the switch reducer without another callback hop."""
    entry = svc.inspect_dataset_folders([folders[1]])['entries'][0]
    app = create_app()
    dependency = next(
        item for item in app.server.test_client().get('/_dash-dependencies').get_json()
        if 'dataset-switch-request.data' in str(item.get('output') or '')
        and any('library-use-entry' in str(value['id']) for value in item['inputs'])
    )
    assert any('library-use-entry' in str(item['id']) for item in dependency['inputs'])
    reducer = app.callback_map[dependency['output']]['callback'].__wrapped__
    trigger = {'base': entry['base'], 'type': 'library-use-entry'}
    click = {'prop_id': json.dumps(trigger, separators=(',', ':')) + '.n_clicks',
             'value': 1}
    for triggered_id, changed in (
        (trigger, [click]),
        ('page-store', [{'prop_id': 'page-store.data', 'value': {'page': 'data-management'}}, click]),
    ):
        monkeypatch.setattr(cb, 'ctx', SimpleNamespace(
            triggered_id=triggered_id, triggered=changed,
        ))
        transaction, request = reducer(
            0, 0, [1], 0, 0, None, {'page': 'reactions'},
            None, None, [entry], {}, [],
        )
        assert request == transaction
        assert request['state'] == 'validating'
        assert request['candidate']['base'] == entry['base']
        assert request['origin'] == {'page': 'reactions', 'library': True}


def test_dataset_entries_open_unified_page_and_focus_relevant_content():
    client = create_app().server.test_client()
    for trigger, focus in [('nav-data-management', 'library-add-more'),
                           ('open-data-modal', 'library-management-panel'),
                           ('page-capability-manage-btn', 'data-prep-event-btn')]:
        response = post(client, 'page-store.data', trigger + '.n_clicks',
                        {trigger: 1}, {'page-store': {'page': 'events'}})
        assert response.status_code == 200
        result = response.get_json()['response']
        assert result['page-store']['data']['page'] == 'data-management'
        assert result['dataset-focus-request']['data']['target'] == focus


def test_dataset_management_has_no_comparison_controls():
    from scripts.webapp_dash.dataset_library import management_panel
    from scripts.webapp_dash.navigation import PAGE_WORKSPACES, WORKSPACE_PAGE_IDS
    import json
    from plotly.utils import PlotlyJSONEncoder
    layout = json.dumps(management_panel(), cls=PlotlyJSONEncoder, ensure_ascii=False)
    assert 'library-compare' not in layout
    assert '至少两个' not in layout
    assert 'library-add-more' in layout
    assert PAGE_WORKSPACES['evolution'] == 'evolution'
    assert PAGE_WORKSPACES['reaction-compare'] == 'reactions'
    assert 'batch-compare' not in WORKSPACE_PAGE_IDS


def test_imported_list_is_visible_before_analysis_tools_with_no_current_dataset(folders):
    from plotly.utils import PlotlyJSONEncoder

    page = _data_management_page()
    panel = _component_by_id(page, 'library-management-panel')
    assert _component_by_id(panel, 'data-candidate-summary') is not None
    assert _component_by_id(panel, 'library-management-list') is not None
    layout = json.dumps(page, cls=PlotlyJSONEncoder, ensure_ascii=False)
    assert layout.index('library-management-list') < layout.index('data-overview-actions')

    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    client = create_app().server.test_client()
    response = post(client, 'library-management-list.children', 'dataset-library.data',
                    {'dataset-library': [entry]}, {'app-store': {}})
    assert response.status_code == 200
    rendered = json.dumps(response.get_json()['response'], ensure_ascii=False)
    assert '切换到此数据' in rendered
    assert 'library-switch-feedback' in rendered


def test_library_row_reports_loading_failure_and_current_state(monkeypatch):
    app = create_app()
    callback = next(
        entry['callback'].__wrapped__
        for key, entry in app.callback_map.items()
        if 'library-switch-feedback' in key
    )
    ids = [{'type': 'library-use-entry', 'base': '/data/A'},
           {'type': 'library-use-entry', 'base': '/data/B'}]
    monkeypatch.setattr(dataset_library, 'ctx', SimpleNamespace(
        triggered_id=ids[1],
        inputs_list=[[{'id': ids[0], 'value': 0}, {'id': ids[1], 'value': 1}]],
    ))
    labels, disabled, _styles, feedback, _forget = callback({}, {}, [0, 1], ids)
    assert labels[1] == '正在加载…' and disabled[1] is True
    assert '正在发起切换' in str(feedback[1])

    monkeypatch.setattr(dataset_library, 'ctx', SimpleNamespace(triggered_id='dataset-switch-transaction'))
    request = {'state': 'validating', 'candidate': {'base': '/data/B'}}
    labels, disabled, _styles, feedback, forget_disabled = callback(request, {}, [0, 1], ids)
    assert labels == ['切换到此数据', '正在加载…']
    assert disabled == [True, True]
    assert forget_disabled == [True, True]
    assert '正在检查RNG 数据' in str(feedback[1])

    request = {'state': 'failed', 'candidate': {'base': '/data/B'}, 'message': '源文件已变化'}
    labels, disabled, _styles, feedback, _forget = callback(request, {}, [0, 1], ids)
    assert labels == ['切换到此数据', '切换到此数据']
    assert disabled == [False, False]
    assert '源文件已变化' in str(feedback[1])

    request = {'state': 'succeeded', 'candidate': {'base': '/data/B'}}
    labels, disabled, _styles, feedback, _forget = callback(request, {}, [0, 1], ids)
    assert labels[1] == '正在加载…' and disabled == [True, True]
    assert '正在载入分析上下文' in str(feedback[1])

    labels, disabled, styles, feedback, forget_disabled = callback(
        request, {'base': '/data/B'}, [0, 1], ids,
    )
    assert disabled == [False, True]
    assert styles[1] == {'display': 'none'}
    assert forget_disabled == [False, True]
    assert feedback[0] == ''
    assert '加载成功，已设为当前 RNG 数据' in str(feedback[1])


def test_library_switch_failure_focuses_its_visible_row():
    client = create_app().server.test_client()
    response = post(
        client, 'dataset-focus-request.data', 'dataset-switch-transaction.data',
        {'dataset-switch-transaction': {
            'state': 'failed', 'candidate': {'base': '/data/B'},
            'origin': {'library': True}, 'message': '源文件已变化',
        }}, {},
    )
    assert response.status_code == 200
    target = response.get_json()['response']['dataset-focus-request']['data']['target']
    assert json.loads(target) == {'base': '/data/B', 'type': 'library-switch-feedback'}


def test_library_use_is_not_swallowed_by_batched_page_navigation(folders):
    client = create_app().server.test_client()
    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    payload = _payload(client, output_contains='dataset-switch-request.data',
                       changed='page-store.data',
                       inputs={'page-store': {'page': 'data-management'}, 'library-use': 1},
                       states={'library-select': entry['base'], 'dataset-library': [entry],
                               'dataset-switch-transaction': {}})
    payload['changedPropIds'] = ['page-store.data', 'library-use.n_clicks']
    response = client.post('/_dash-update-component', json=payload)
    assert response.status_code == 200
    assert response.get_json()['response']['dataset-switch-request']['data']['candidate']['base'] == entry['base']


def test_import_current_folder_without_building_a_batch_list(folders):
    client = create_app().server.test_client()
    response = post(client, 'library-request.data', 'library-import.n_clicks',
                    {'library-import': 1}, {'library-draft': [], 'import-location': {'path': folders[0]}})
    request = response.get_json()['response']['library-request']['data']
    assert request['paths'] == [folders[0]]
    assert len(svc.inspect_dataset_folders(request['paths'])['entries']) == 1


def test_explicit_batch_does_not_implicitly_include_browsed_folder(folders):
    client = create_app().server.test_client()
    response = post(client, 'library-request.data', 'library-import.n_clicks',
                    {'library-import': 1}, {'library-draft': [folders[1]], 'import-location': {'path': folders[0]}})
    assert response.get_json()['response']['library-request']['data']['paths'] == [folders[1]]


def test_library_build_prepares_only_selected_entry_and_capability(folders):
    entries = svc.inspect_dataset_folders(folders)['entries']
    result = _build_library_index({**entries[1], 'kind': 'composition'})

    assert result['dataset_id'] == entries[1]['dataset_id']
    target = svc.dataset_preparation_status(entries[1]['folder'], base=entries[1]['base'])
    other = svc.dataset_preparation_status(entries[0]['folder'], base=entries[0]['base'])
    assert target['composition']['state'] == 'ready'
    assert other['composition']['state'] != 'ready'
    assert target['events']['state'] != 'ready'
    assert target['trajectory']['state'] != 'ready'


def test_library_build_rejects_stale_dataset_identity_before_preparation(folders, monkeypatch):
    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    monkeypatch.setattr(svc, 'prepare_dataset_workspace',
                        lambda *_args, **_kwargs: pytest.fail('unexpected preparation'))

    with pytest.raises(svc.ServiceError, match='身份已变化'):
        _build_library_index({**entry, 'dataset_id': 'another-dataset', 'kind': 'composition'})


def test_comparison_build_rejects_changed_source_revision_before_preparation(folders, monkeypatch):
    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    revision = svc.validate_dataset_candidate(entry['folder'], entry['base'])['source_revision']
    (Path(folders[0]) / 'run.species').write_text('Timestep 0: CCO 2\n')
    monkeypatch.setattr(svc, 'prepare_dataset_workspace',
                        lambda *_args, **_kwargs: pytest.fail('unexpected preparation'))

    with pytest.raises(svc.ServiceError, match='来源文件已变化'):
        _build_library_index({**entry, 'source_revision': revision, 'kind': 'composition'})


def test_library_build_passes_expected_identity_into_preparation(folders, monkeypatch):
    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    calls = []

    def prepare(_folder, **kwargs):
        calls.append(kwargs)
        return {'dataset_id': entry['dataset_id'], 'ok': True}

    monkeypatch.setattr(svc, 'prepare_dataset_workspace', prepare)

    _build_library_index({**entry, 'kind': 'composition'})

    assert calls[0]['expected_dataset_id'] == entry['dataset_id']


def test_preparation_rejects_wrong_expected_identity_before_build(folders, monkeypatch):
    from reacnet_scope import prepare

    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    monkeypatch.setattr(prepare, 'run_preparation',
                        lambda **_kwargs: pytest.fail('preparation must not start'))

    with pytest.raises(svc.ServiceError, match='身份'):
        svc.prepare_dataset_workspace(
            entry['folder'], base=entry['base'], kind='composition',
            expected_dataset_id='another-dataset',
        )


def test_preparation_rechecks_expected_identity_before_starting_work(folders, monkeypatch):
    from reacnet_scope import dataset_context, prepare, workspace_services

    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    monkeypatch.setattr(dataset_context, 'validate_dataset_candidate',
                        lambda *_args: {'dataset_id': entry['dataset_id']})
    monkeypatch.setattr(workspace_services, 'dataset_preparation_status',
                        lambda *_args, **_kwargs: {'dataset_id': 'another-dataset'})
    monkeypatch.setattr(prepare, 'run_preparation',
                        lambda **_kwargs: pytest.fail('preparation must not start'))

    with pytest.raises(svc.ServiceError, match='身份'):
        svc.prepare_dataset_workspace(
            entry['folder'], base=entry['base'], kind='composition',
            expected_dataset_id=entry['dataset_id'],
        )


def test_library_control_distinguishes_missing_source_and_task_progress():
    entry = {'base': '/data/run', 'dataset_id': 'dataset-a'}
    missing = _library_index_control(entry, 'trajectory',
                                     {'state': 'missing', 'source_available': False}, None, None, None)
    assert missing[0].children == '缺少源文件'
    assert missing[1].disabled is True
    assert missing[1].style == {'display': 'none'}

    building = _library_index_control(entry, 'composition', {
        'state': 'building', 'source_available': True,
        'task': {'state': 'running', 'progress': 0.3, 'progress_trusted': True,
                 'matches_current_revision': True},
    }, None, None, None)
    assert building[0].children == '准备中 · 30%'
    progress = next(item for item in building if getattr(item, 'role', None) == 'progressbar')
    assert progress.to_plotly_json()['props']['aria-valuenow'] == 30
    assert progress.children.style == {'width': '30%'}
    assert any((getattr(item, 'id', None) or {}).get('type') == 'library-cancel-index'
               for item in building)
    assert all(item.disabled for item in building
               if (getattr(item, 'id', None) or {}).get('type') == 'library-build-index')

    unknown = _library_index_control(entry, 'composition', {
        'state': 'building', 'source_available': True,
        'task': {'state': 'running', 'matches_current_revision': True},
    }, None, None, None)
    progress = next(item for item in unknown if getattr(item, 'role', None) == 'progressbar')
    assert 'is-indeterminate' in progress.className
    assert 'aria-valuenow' not in progress.to_plotly_json()['props']

    pending = _library_index_control(entry, 'composition', {
        'state': 'missing', 'source_available': True,
    }, {'base': entry['base'], 'kind': 'composition', 'dataset_id': entry['dataset_id'],
        'token': 'request-1'}, None, None)
    assert pending[0].children == '正在启动准备任务'
    assert any(getattr(item, 'role', None) == 'progressbar' for item in pending)


def test_library_control_resumes_canceled_checkpoint_instead_of_showing_active_build():
    entry = {'base': '/data/run', 'dataset_id': 'dataset-a'}
    controls = _library_index_control(entry, 'composition', {
        'state': 'building', 'source_available': True,
        'task': {'state': 'canceled', 'matches_current_revision': True},
    }, None, None, None)

    assert controls[0].children == '已取消'
    assert any(getattr(item, 'children', None) == '续建索引' and not item.disabled
               for item in controls if getattr(item, 'id', None))
    assert not any((getattr(item, 'id', None) or {}).get('type') == 'library-cancel-index'
                   for item in controls)


def test_canceled_composition_checkpoint_is_resumable_in_library(folders):
    from reacnet_scope import prepare
    from reacnet_scope.composition import composition_index_path

    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    svc.dataset_preparation_status(entry['folder'], base=entry['base'])
    species = f"{entry['base']}.species"
    checkpoint = Path(f'{composition_index_path(species)}.building')
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b'incomplete checkpoint')
    dataset = prepare.discover_dataset(entry['folder'], Path(entry['base']).name)
    task_path = prepare._preparation_task_path(dataset, 'composition')
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(json.dumps({
        'state': 'canceled',
        'source_revision': prepare._capability_source_revision(dataset, 'composition'),
    }))

    status = svc.dataset_preparation_status(entry['folder'], base=entry['base'])
    controls = _library_index_control(
        entry, 'composition', status['composition'], None, None, None,
    )

    assert status['composition']['state'] == 'building'
    assert status['composition']['task']['matches_current_revision'] is True
    assert controls[0].children == '已取消'
    assert any(getattr(item, 'children', None) == '续建索引' for item in controls)


def test_library_control_ignores_terminal_task_from_old_source_revision():
    entry = {'base': '/data/run', 'dataset_id': 'dataset-a'}
    controls = _library_index_control(entry, 'composition', {
        'state': 'stale', 'source_available': True,
        'task': {'state': 'failed', 'matches_current_revision': False},
    }, None, None, None)

    assert controls[0].children == '需要重建'
    assert any(getattr(item, 'children', None) == '重新构建' for item in controls)


def test_library_does_not_label_old_revision_failure_as_current(folders):
    from reacnet_scope import prepare

    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    svc.prepare_dataset_workspace(
        entry['folder'], base=entry['base'], kind='composition',
        expected_dataset_id=entry['dataset_id'],
    )
    dataset = prepare.discover_dataset(entry['folder'], Path(entry['base']).name)
    old_revision = prepare._capability_source_revision(dataset, 'composition')
    task_path = prepare._preparation_task_path(dataset, 'composition')
    task_path.write_text(json.dumps({
        'state': 'failed', 'source_revision': old_revision,
    }))
    Path(f"{entry['base']}.species").write_text('Timestep 0: CCO 200\n')

    status = svc.dataset_preparation_status(entry['folder'], base=entry['base'])
    controls = _library_index_control(
        entry, 'composition', status['composition'], None, None, None,
    )

    assert status['composition']['state'] == 'stale'
    assert status['composition']['task']['matches_current_revision'] is False
    assert controls[0].children == '需要重建'
    assert any(getattr(item, 'children', None) == '重新构建' for item in controls)


def test_library_control_snapshot_preserves_click_count_across_idle_poll():
    entry = {'base': '/data/run', 'dataset_id': 'dataset-a'}
    controls = _library_index_control(entry, 'composition', {
        'state': 'missing', 'source_available': True,
    }, None, None, None)
    prior = [item.to_plotly_json() for item in controls]
    prior[1]['props']['n_clicks'] = 1

    assert _control_snapshot(controls) == _control_snapshot(prior)


def test_library_build_request_targets_imported_entry_without_current_switch(folders):
    client = create_app().server.test_client()
    entry = svc.inspect_dataset_folders([folders[1]])['entries'][0]
    clicked = {'type': 'library-build-index', 'base': entry['base'], 'kind': 'composition'}
    request_id = {'type': 'library-build-request', 'base': entry['base'], 'kind': 'composition'}
    changed = f'{json.dumps(clicked, sort_keys=True, separators=(",", ":"))}.n_clicks'
    dependency = next(item for item in client.get('/_dash-dependencies').get_json()
                      if 'library-build-request' in item['output'])
    payload = {
        'output': dependency['output'],
        'outputs': {'id': request_id, 'property': 'data'},
        'changedPropIds': [changed],
        'inputs': [{'id': clicked, 'property': 'n_clicks', 'value': 1}],
        'state': [{'id': 'dataset-library', 'property': 'data', 'value': [entry]}],
    }

    response = client.post('/_dash-update-component', json=payload)

    assert response.status_code == 200
    body = response.get_json()['response']
    request = next(iter(body.values()))['data']
    assert request['base'] == entry['base']
    assert request['dataset_id'] == entry['dataset_id']
    assert request['kind'] == 'composition'
    assert 'app-store' not in body


def test_library_status_shows_workspace_read_error_instead_of_500(folders, monkeypatch):
    client = create_app().server.test_client()
    entry = svc.inspect_dataset_folders([folders[0]])['entries'][0]
    control = {'type': 'library-index-control', 'base': entry['base'], 'kind': 'event'}
    build = {'type': 'library-build-index', 'base': entry['base'], 'kind': 'event'}
    cancel = {'type': 'library-cancel-index', 'base': entry['base'], 'kind': 'event'}
    dependency = next(item for item in client.get('/_dash-dependencies').get_json()
                      if 'library-index-control' in item['output'])
    monkeypatch.setattr(svc, 'dataset_preparation_status',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            OSError(30, 'Read-only file system')))

    def value(item):
        if item['id'] == 'dataset-library':
            return [entry]
        if item['property'] == 'id':
            if 'library-build-index' in str(item['id']):
                return [build]
            if 'library-cancel-index' in str(item['id']):
                return [cancel]
            return [control]
        if item['property'] == 'children':
            return [['索引无效']]
        return [] if isinstance(item['id'], str) and 'library-build-' in item['id'] else None

    response = client.post('/_dash-update-component', json={
        'output': dependency['output'],
        'outputs': [
            [{'id': control, 'property': 'children'}],
            [{'id': build, 'property': 'children'}],
            [{'id': build, 'property': 'disabled'}],
            [{'id': build, 'property': 'style'}],
            [{'id': cancel, 'property': 'disabled'}],
            [{'id': cancel, 'property': 'style'}],
        ],
        'changedPropIds': ['library-index-refresh.data'],
        'inputs': [{**item, 'value': value(item)} for item in dependency['inputs']],
        'state': [{**item, 'value': value(item)} for item in dependency['state']],
    })

    assert response.status_code == 200
    key = json.dumps(control, sort_keys=True, separators=(',', ':'))
    shown = response.get_json()['response'][key]['children']
    if isinstance(shown, list):
        shown = shown[0]
    assert shown['props']['role'] == 'status'
    assert 'Read-only file system' in shown['props']['children']
