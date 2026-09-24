import json
from pathlib import Path

import pytest
from reacnet_scope import services as svc, dir_browser
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.callbacks import _batch_managed_dataset_catalog
from scripts.webapp_dash.dataset_library import (
    _build_library_index, _control_snapshot, _library_index_control,
)
from tests.test_dataset_switch_callbacks import _payload


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


def test_dataset_entry_defaults_to_library_and_preparation_entry_opens_tasks():
    client = create_app().server.test_client()
    for trigger, expected in [('nav-data-management', 'library'), ('open-data-modal', 'tasks'),
                              ('page-capability-manage-btn', 'tasks')]:
        response = post(client, 'library-view.value', trigger + '.n_clicks', {trigger: 1}, {})
        assert response.status_code == 200
        assert response.get_json()['response']['library-view']['value'] == expected


def test_dataset_management_has_no_comparison_controls():
    from scripts.webapp_dash.dataset_library import management_panel
    from scripts.webapp_dash.navigation import PAGE_WORKSPACES, WORKSPACE_PAGE_IDS
    import json
    from plotly.utils import PlotlyJSONEncoder
    layout = json.dumps(management_panel(), cls=PlotlyJSONEncoder, ensure_ascii=False)
    assert 'library-compare' not in layout
    assert '至少两个' not in layout
    assert 'library-add-more' in layout
    assert PAGE_WORKSPACES['batch-compare'] == 'species'
    assert PAGE_WORKSPACES['reaction-compare'] == 'reactions'
    assert 'batch-compare' not in WORKSPACE_PAGE_IDS


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
    assert not any(getattr(item, 'id', None) for item in missing)

    building = _library_index_control(entry, 'composition', {
        'state': 'building', 'source_available': True,
        'task': {'state': 'running', 'progress': 0.3, 'progress_trusted': True,
                 'matches_current_revision': True},
    }, None, None, None)
    assert building[0].children == '准备中 · 30%'
    assert any((getattr(item, 'id', None) or {}).get('type') == 'library-cancel-index'
               for item in building)
    assert all(item.disabled for item in building
               if (getattr(item, 'id', None) or {}).get('type') == 'library-build-index')


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
    pattern = '{"base":["ALL"],"kind":["ALL"],"type":"library-build-index"}'
    changed = f'{json.dumps(clicked, sort_keys=True, separators=(",", ":"))}.n_clicks'
    payload = _payload(client, output_contains='library-build-request.data', changed=changed,
                       inputs={pattern: [1]}, states={'dataset-library': [entry]})
    for item in payload['inputs']:
        if item['id'] == pattern:
            item['id'] = clicked
            item['value'] = 1

    response = client.post('/_dash-update-component', json=payload)

    assert response.status_code == 200
    body = response.get_json()['response']
    request = body['library-build-request']['data']
    assert request['base'] == entry['base']
    assert request['dataset_id'] == entry['dataset_id']
    assert request['kind'] == 'composition'
    assert 'app-store' not in body
