from pathlib import Path

import pytest
from reacnet_scope import services as svc, dir_browser
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.callbacks import _batch_managed_dataset_catalog
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
