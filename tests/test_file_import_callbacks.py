"""The production file picker and existing switch transaction operate together."""
import json
from pathlib import Path

import pytest
from reacnet_scope import services as svc, dir_browser
from scripts.webapp_dash.app import create_app
from tests.test_dataset_switch_callbacks import _payload


@pytest.fixture
def imported(tmp_path, monkeypatch):
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "workspace"))
    paths = [tmp_path / "one" / "run.species", tmp_path / "two" / "run.reactionabcd"]
    for path in paths:
        path.parent.mkdir()
        path.write_text("Timestep 0: [H] 2\n" if path.suffix == ".species" else "1 [H] + [H] -> [H][H]\n")
    app = create_app()
    client = app.server.test_client()
    client.import_collector = app.callback_map["import-collect-result.data"]["callback"].__wrapped__
    return client, list(map(str, paths))


def post(client, output, changed, inputs=None, states=None):
    result = client.post("/_dash-update-component", json=_payload(client, output_contains=output,
        changed=changed, inputs=inputs or {}, states=states or {}))
    assert result.status_code == 200, result.get_data(as_text=True)
    return result.get_json()["response"]


def collect_into_draft(client, button, state):
    request = post(client, "import-collect-request.data", f"{button}.n_clicks", {button: 1}, state)["import-collect-request"]["data"]
    collected = client.import_collector(request)
    return post(client, "import-selection.data", "import-collect-result.data", {"import-collect-result": collected},
                {"import-selection": state.get("import-selection"), "import-collect-request": request})


def add(client, paths, selection=None):
    return collect_into_draft(client, "import-add-paths",
                {"import-paths": "\n".join(paths), "import-selection": selection or {"paths": []}})


def test_cross_directory_add_preview_commit_and_direct_analysis(imported):
    client, paths = imported
    selection = add(client, paths)["import-selection"]["data"]
    assert selection["paths"] == paths
    view = post(client, "import-preview.data", "import-selection.data", {"import-selection": selection})
    preview = view["import-preview"]["data"]
    assert len(preview["groups"]) == 1
    result = post(client, "import-group-summary.children", "import-preview.data",
                  {"import-preview": preview, "import-group": "run"}, {"import-selection": selection})
    candidate = result["dataset-browser-candidate"]["data"]
    assert not Path(candidate["base"]).exists()
    transaction = svc.begin_dataset_switch(candidate, request_id="first")
    validation = svc.validate_file_collection_candidate(candidate)
    # Round trip through a browser: exact nanoseconds must remain strings.
    assert isinstance(validation["source_revision"]["artifacts"][0]["mtime_ns"], str)
    resolved = post(client, "dataset-switch-transaction.data", "dataset-switch-validation.data",
                    {"dataset-switch-validation": {"ok": True, "request_id": "first", "validation": validation}},
                    {"dataset-switch-transaction": transaction})["dataset-switch-transaction"]["data"]
    assert resolved["state"] == "succeeded"
    assert Path(candidate["base"]).exists()
    committed = post(client, "dataset-context-commit.data", "dataset-switch-transaction.data",
                     {"dataset-switch-transaction": resolved}, {"app-store": {}, "recent-datasets": []})
    assert committed["dataset-switch-navigation"]["data"]["page"] == "species"
    assert committed["app-store"]["data"]["artifacts"]["species"] == paths[0]


def test_cancelled_validation_does_not_publish_collection(imported):
    client, paths = imported
    candidate = svc.collection_candidate(dict(zip(("species", "reaction"), paths)), "run")
    validation = svc.validate_file_collection_candidate(candidate)
    transaction = svc.supersede_dataset_switch(svc.begin_dataset_switch(candidate, request_id="old"), reason="cancelled")
    response = client.post("/_dash-update-component", json=_payload(client,
        output_contains="dataset-switch-transaction.data", changed="dataset-switch-validation.data",
        inputs={"dataset-switch-validation": {"ok": True, "request_id": "old", "validation": validation}},
        states={"dataset-switch-transaction": transaction}))
    assert response.status_code == 204
    assert not Path(candidate["base"]).exists()


def test_browsing_does_not_select_or_replace_current_and_rejects_outside(imported):
    client, paths = imported
    values = post(client, "import-location.data", "import-browse-go.n_clicks", {"import-browse-go": 1},
                  {"import-path": str(Path(paths[0]).parent)})
    assert values["import-location"]["data"]["files"][0]["value"] == paths[0]
    assert "app-store" not in values and "dataset-browser-candidate" not in values
    rejected = post(client, "import-location.data", "import-browse-go.n_clicks", {"import-browse-go": 1},
                    {"import-path": "/etc", "import-location": values["import-location"]["data"]})
    assert "import-location" not in rejected
    assert "允许范围" in rejected["import-browse-message"]["children"]


def test_folder_add_and_conflicting_roles_are_visible(imported):
    client, paths = imported
    folder = collect_into_draft(client, "import-add-folder",
                  {"import-location": {"path": str(Path(paths[0]).parent)}, "import-selection": {"paths": []}})
    selection = add(client, [paths[1]], folder["import-selection"]["data"])["import-selection"]["data"]
    assert set(selection["paths"]) == set(paths)
    duplicate = Path(paths[1]).parent / "other.species"
    duplicate.write_text("Timestep 0: [O] 1\n")
    preview = svc.preview_file_collection([*paths, str(duplicate)], {p: "run" for p in [*paths, str(duplicate)]})
    result = post(client, "import-group-summary.children", "import-preview.data",
                  {"import-preview": preview, "import-group": "run"}, {"import-selection": {}})
    assert result["dataset-browser-candidate"]["data"] is None
    assert "多个来源" in json.dumps(result, ensure_ascii=False)


def test_missing_recent_preserves_draft_and_corrupt_recent_is_rejected(imported):
    client, paths = imported
    trigger = '{"index":["ALL"],"type":"dir-browser-recent-entry"}'
    response = post(client, "import-selection.data", '{"index":0,"type":"dir-browser-recent-entry"}.n_clicks',
                    {trigger: [1]}, {"import-selection": {"paths": paths}, "recent-datasets": []})
    assert "import-selection" not in response
    assert "import-feedback" in response


def test_auto_preparation_is_revision_bound_and_deduplicated(imported):
    client, paths = imported
    candidate = svc.collection_candidate(dict(zip(("species", "reaction"), paths)), "run")
    validation = svc.validate_file_collection_candidate(candidate)
    svc.commit_file_collection(validation)
    current = svc.current_dataset_from_validation(validation)
    result = post(client, "import-auto-request.data", "app-store.data",
                  {"app-store": current, "page-store": {"page": "species"}}, {"import-auto-attempts": []})
    request = result["import-auto-request"]["data"]
    assert request["kind"] == "composition"
    duplicate = client.post("/_dash-update-component", json=_payload(client, output_contains="import-auto-request.data",
        changed="app-store.data", inputs={"app-store": current, "page-store": {"page": "species"}},
        states={"import-auto-attempts": result["import-auto-attempts"]["data"]}))
    assert duplicate.status_code == 204
    late = client.post("/_dash-update-component", json=_payload(client, output_contains="app-store.data",
        changed="import-auto-result.data", inputs={"import-auto-result": {**request, "ok": True}},
        states={"app-store": {**current, "dataset_id": "different"}}))
    assert late.status_code == 204


def test_query_waits_for_preparation_then_runs_submitted_question_once(imported, monkeypatch):
    client, paths = imported
    candidate = svc.collection_candidate(dict(zip(("species", "reaction"), paths)), "run")
    validation = svc.validate_file_collection_candidate(candidate)
    svc.commit_file_collection(validation)
    current = svc.current_dataset_from_validation(validation)
    calls = []
    def query(artifacts, targets, **kwargs):
        calls.append({**kwargs, "targets": targets})
        return {"curves": [], "x_values": [], "meta": {}}
    monkeypatch.setattr(svc, "build_species_evolution", query)
    pending_result = post(client, "evolution-payload-store.data", "evolution-search-btn.n_clicks",
        {"evolution-search-btn": 1}, {"app-store": current, "evolution-targets": "H2"})
    pending = pending_result["import-pending-evolution"]["data"]
    assert not calls
    assert "evolution-payload-store" not in pending_result
    prepared = {"current": current, "kind": "composition", "ok": True}
    finished = post(client, "evolution-payload-store.data", "import-auto-result.data",
        {"evolution-search-btn": 1, "import-auto-result": prepared},
        {"app-store": current, "evolution-targets": "CH4", "import-pending-evolution": pending})
    assert len(calls) == 1 and calls[0]["targets"] == ["H2"]
    assert finished["import-pending-evolution"]["data"] is None
    repeated = client.post("/_dash-update-component", json=_payload(client, output_contains="evolution-payload-store.data",
        changed="import-auto-result.data", inputs={"import-auto-result": prepared},
        states={"app-store": current, "import-pending-evolution": None}))
    assert repeated.status_code == 204
    assert len(calls) == 1


def test_late_file_enumeration_preserves_a_changed_draft(imported):
    client, paths = imported
    request = post(client, "import-collect-request.data", "import-add-paths.n_clicks", {"import-add-paths": 1},
                   {"import-paths": paths[0], "import-selection": {"paths": []}})["import-collect-request"]["data"]
    result = client.import_collector(request)
    response = post(client, "import-selection.data", "import-collect-result.data", {"import-collect-result": result},
                    {"import-selection": {"paths": [paths[1]]}, "import-collect-request": request})
    assert "import-selection" not in response
    assert "选择已改变" in response["import-feedback"]["children"]


def test_multiple_datasets_require_explicit_choice(imported):
    client, paths = imported
    other = Path(paths[1]).parent / "other.species"
    other.write_text("Timestep 0: [O] 1\n")
    result = post(client, "import-preview.data", "import-selection.data",
                  {"import-selection": {"paths": [paths[0], str(other)]}})
    assert len(result["import-group"]["options"]) == 2
    assert result["import-group"]["value"] is None


def test_page_change_does_not_terminate_previous_dataset_preparation(imported):
    client, paths = imported
    candidate = svc.collection_candidate(dict(zip(("species", "reaction"), paths)), "run")
    validation = svc.validate_file_collection_candidate(candidate)
    current = svc.current_dataset_from_validation(validation)
    response = client.post("/_dash-update-component", json=_payload(client, output_contains="import-auto-request.data",
        changed="page-store.data", inputs={"app-store": current, "page-store": {"page": "species"}},
        states={"import-auto-attempts": [], "import-auto-request": {"token": "still-running"}, "import-auto-result": None}))
    assert response.status_code == 204


def test_import_navigation_survives_simultaneous_zero_click_hydration(imported):
    client, _paths = imported
    payload = _payload(client, output_contains="page-store.data", changed="dataset-switch-navigation.data",
        inputs={"dataset-switch-navigation": {"page": "species", "request_id": "ready"}},
        states={"page-store": {"page": "data-management"}})
    payload["changedPropIds"] = ['{"page":"species","type":"data-overview-open-page"}.n_clicks', "dataset-switch-navigation.data"]
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200
    assert response.get_json()["response"]["page-store"]["data"]["page"] == "species"


def test_explicit_clear_wins_over_batched_page_hydration(imported):
    client, paths = imported
    payload = _payload(client, output_contains='import-selection.data',
        changed='page-store.data', inputs={'page-store': {'page': 'data-management'}, 'import-clear': 1},
        states={'import-selection': {'paths': paths, 'assignments': {}}})
    payload['changedPropIds'] = ['page-store.data', 'import-clear.n_clicks']
    result = client.post('/_dash-update-component', json=payload)
    assert result.status_code == 200
    assert result.json['response']['import-selection']['data'] == {'paths': [], 'assignments': {}}



def test_host_files_need_no_upload_service_and_preview_does_not_start_analysis(imported, monkeypatch):
    client, paths = imported
    monkeypatch.delenv('REACNET_SCOPE_UPLOAD_DIR', raising=False)
    monkeypatch.delenv('REACNET_SCOPE_TUSD_URL', raising=False)
    assert not any('/api/uploads' in rule.rule for rule in client.application.url_map.iter_rules())
    browse = post(client, 'import-location.data', 'import-browse-go.n_clicks',
                  {'import-browse-go': 1}, {'import-path': str(Path(paths[0]).parent)})
    assert browse['import-location']['data']['files'][0]['value'] == paths[0]
    candidate = svc.collection_candidate(dict(zip(('species', 'reaction'), paths)), 'run')
    result = post(client, 'dataset-switch-request.data', 'dataset-browser-candidate.data',
                  {'dataset-browser-candidate': candidate})
    assert result['dataset-switch-transaction']['data']['state'] == 'candidate-selected'
    assert 'dataset-switch-request' not in result
    started = post(client, 'dataset-switch-request.data', 'data-apply-btn.n_clicks',
                   {'data-apply-btn': 1, 'dataset-browser-candidate': candidate})
    assert started['dataset-switch-request']['data']['state'] == 'validating'


def test_folder_add_requires_location_and_idle_collector(imported):
    client, paths = imported
    for location, idle, expected in [({}, True, True), ({'path': str(Path(paths[0]).parent)}, True, False), ({'path': str(Path(paths[0]).parent)}, False, True)]:
        result = post(client, 'import-add-folder.disabled', 'import-location.data',
                      {'import-location': location, 'import-collect-cancel': idle})
        assert result['import-add-folder']['disabled'] == expected


def test_page_hydration_does_not_reset_open_file_browser(imported):
    client, paths = imported
    response = client.post('/_dash-update-component', json=_payload(client,
        output_contains='import-location.data', changed='page-store.data',
        inputs={'page-store': {'page': 'data-management'}},
        states={'import-location': {'path': str(Path(paths[0]).parent)}}))
    assert response.status_code == 204


def toggle(client, path, selection):
    identity = json.dumps({'type': 'import-toggle', 'path': path}, sort_keys=True, separators=(',', ':'))
    payload = _payload(client, output_contains='import-selection.data', changed=identity + '.n_clicks', inputs={},
                       states={'import-selection': selection})
    for item in payload['inputs']:
        if 'import-toggle' in str(item['id']):
            item['value'] = [selection.get('click_counts', {}).get(path, 0) + 1]
    for item in payload['state']:
        if 'import-toggle' in str(item['id']):
            item['value'] = [{'type': 'import-toggle', 'path': path}]
    response = client.post('/_dash-update-component', json=payload)
    assert response.status_code == 200, response.text
    return response.json['response']['import-selection']['data']


def test_checkbox_draft_survives_directories_and_filter_and_can_uncheck(imported):
    client, paths = imported
    selected = toggle(client, paths[0], {'paths': [], 'assignments': {}})
    selected = toggle(client, paths[1], selected)
    assert selected['paths'] == paths
    location = svc.browse_import_files(str(Path(paths[0]).parent))
    result = post(client, 'import-file-options.children', 'import-filter.value',
                  {'import-manual-files': True, 'import-location': location, 'import-selection': selected, 'import-filter': 'absent'})
    assert 'app-store' not in result and 'import-selection' not in result
    result = post(client, 'import-file-options.children', 'import-filter.value',
                  {'import-manual-files': True, 'import-location': location, 'import-selection': selected, 'import-filter': ''})
    assert result['import-file-options']['children'][0]['props']['aria-checked'] == 'true'
    assert toggle(client, paths[0], selected)['paths'] == [paths[1]]


def test_bulk_grouping_only_changes_selected_files_and_requires_explicit_editor(imported):
    client, paths = imported
    selection = {'paths': paths, 'assignments': {paths[0]: 'one', paths[1]: 'two'}}
    preview = svc.preview_file_collection(paths, selection['assignments'])
    result = post(client, 'import-selected-panel.style', 'import-preview.data',
                  {'import-preview': preview, 'import-selection': selection, 'import-edit-groups': False})
    assert result['import-group-editor']['style'] == {'display': 'none'}
    result = post(client, 'import-selection.data', 'import-move.n_clicks', {'import-move': 1},
                  {'import-selection': selection, 'import-move-files': [paths[1]], 'import-move-target': 'one'})
    assert result['import-selection']['data']['assignments'] == dict.fromkeys(paths, 'one')


def test_four_rng_filenames_restore_one_suggestion_without_overwriting_explicit_groups(imported):
    client, paths = imported
    parent = Path(paths[0]).parent
    files = []
    for suffix in ('', '.species', '.reactionabcd', '.timeline.h5'):
        path = parent / ('trajectory.lammpstrj' + suffix)
        path.touch()
        files.append(str(path))
    preview = svc.preview_file_collection(files)
    assert len(preview['groups']) == 1
    selection = {'paths': files, 'assignments': dict(zip(files, ['trajectory', 'custom', 'custom', 'custom']))}
    view = post(client, 'import-preview.data', 'import-selection.data', {'import-selection': selection})
    assert len(view['import-preview']['data']['groups']) == 2
    reset = post(client, 'import-selection.data', 'import-regroup.n_clicks', {'import-regroup': 1},
                 {'import-selection': selection})['import-selection']['data']
    assert reset['assignments'] == {}
    assert len(svc.preview_file_collection(reset['paths'], reset['assignments'])['groups']) == 1


@pytest.mark.parametrize('count', [4, 50, 500])
def test_large_picker_lists_keep_every_selected_file(imported, count):
    client, paths = imported
    parent = Path(paths[0]).parent
    files = []
    for number in range(count):
        path = parent / f'长文件名_独立运行_{number:03d}.species'
        path.touch()
        files.append(str(path))
    result = post(client, 'import-preview.data', 'import-selection.data',
                  {'import-selection': {'paths': files, 'assignments': {}}})
    assert len(result['import-preview']['data']['files']) == count
    assert len(result['import-files']['children']) == count


def test_recent_collection_focuses_source_instead_of_workspace(imported):
    client, paths = imported
    candidate = svc.collection_candidate(dict(zip(('species', 'reaction'), paths)), 'run')
    validation = svc.validate_file_collection_candidate(candidate)
    svc.commit_file_collection(validation)
    current = svc.current_dataset_from_validation(validation)
    result = post(client, 'import-selection.data', 'import-supplement.n_clicks', {'import-supplement': 1},
                  {'app-store': current})['import-selection']['data']
    assert result['focus_path'] in {str(Path(p).parent) for p in paths}
    focused = post(client, 'import-location.data', 'import-selection.data', {'import-selection': result})
    assert focused['import-location']['data']['path'] == result['focus_path']
    assert focused['import-location']['data']['path'] != candidate['folder']


def test_new_toggle_invalidates_pending_directory_scan(imported):
    client, paths = imported
    request = post(client, 'import-collect-request.data', 'import-add-paths.n_clicks', {'import-add-paths': 1},
                   {'import-paths': paths[0], 'import-selection': {'paths': [], 'assignments': {}}})['import-collect-request']['data']
    result = client.import_collector(request)
    selected = toggle(client, paths[1], {'paths': [], 'assignments': {}})
    late = post(client, 'import-selection.data', 'import-collect-result.data', {'import-collect-result': result},
                {'import-selection': selected, 'import-collect-request': request})
    assert 'import-selection' not in late
    assert '选择已改变' in late['import-feedback']['children']


def test_batched_clicks_apply_all_changes_and_mount_does_not_toggle_again(imported):
    client, paths = imported
    ids = [{'type': 'import-toggle', 'path': path} for path in paths]
    changed = [json.dumps(identity, sort_keys=True, separators=(',', ':')) + '.n_clicks' for identity in ids]
    payload = _payload(client, output_contains='import-selection.data', changed=changed[0], inputs={},
                       states={'import-selection': {'paths': [], 'assignments': {}}})
    payload['changedPropIds'] = changed
    for item in payload['inputs']:
        if 'import-toggle' in str(item['id']):
            item['value'] = [1, 1]
    for item in payload['state']:
        if 'import-toggle' in str(item['id']):
            item['value'] = ids
    response = client.post('/_dash-update-component', json=payload)
    assert response.status_code == 200
    selection = response.json['response']['import-selection']['data']
    assert selection['paths'] == paths
    for item in payload['state']:
        if item['id'] == 'import-selection':
            item['value'] = selection
    assert client.post('/_dash-update-component', json=payload).status_code == 204


def test_paginated_list_filters_all_entries_and_preserves_checked_state(imported):
    client, paths = imported
    rows = [{'label': f'run{n:03d}.species', 'value': f'/example/run{n:03d}.species', 'size': 100} for n in range(500)]
    selection = {'paths': [rows[0]['value'], rows[-1]['value']], 'assignments': {}}
    def page(number, query=''):
        return post(client, 'import-file-options.children', 'import-list-page.data',
                    {'import-manual-files': True, 'import-location': {'files': rows}, 'import-selection': selection,
                     'import-list-page': number, 'import-filter': query})
    first = page(0)
    assert len(first['import-file-options']['children']) == 50
    assert first['import-file-options']['children'][0]['props']['aria-checked'] == 'true'
    last = page(9)
    assert len(last['import-file-options']['children']) == 50
    assert last['import-file-options']['children'][-1]['props']['aria-checked'] == 'true'
    assert last['import-list-next']['disabled'] is True
    filtered = page(0, '499')
    assert len(filtered['import-file-options']['children']) == 1
    assert filtered['import-file-options']['children'][0]['props']['aria-checked'] == 'true'


def open_folder(client, folder, selection=None):
    from scripts.webapp_dash.file_import import _selection_revision
    selection = selection or {'paths': [], 'assignments': {}}
    location = post(client, 'import-location.data', 'import-browse-go.n_clicks',
                    {'import-browse-go': 1, 'import-selection': selection},
                    {'import-path': str(folder)})['import-location']['data']
    assert location['selection_revision'] == _selection_revision(selection)
    selected = post(client, 'import-selection.data', 'import-location.data',
                    {'import-location': location}, {'import-selection': selection})['import-selection']['data']
    return location, selected


def test_open_rng_folder_selects_all_artifacts_and_enables_one_candidate(imported):
    client, paths = imported
    folder = Path(paths[0]).parent
    for suffix in ('', '.species', '.reactionabcd', '.timeline.h5'):
        (folder / ('trajectory.lammpstrj' + suffix)).touch()
    Path(paths[0]).unlink()
    location, selection = open_folder(client, folder)
    assert len(selection['paths']) == 4
    assert selection['folder'] == str(folder)
    rendered = post(client, 'import-preview.data', 'import-selection.data', {'import-selection': selection})
    preview = rendered['import-preview']['data']
    assert len(preview['groups']) == 1
    assert rendered['import-group']['value'] == 'trajectory'
    result = post(client, 'import-group-summary.children', 'import-preview.data',
                  {'import-preview': preview, 'import-group': 'trajectory'}, {'import-selection': selection})
    assert result['dataset-browser-candidate']['data']['label'] == folder.name
    assert len(result['dataset-browser-candidate']['data']['artifact_paths']) == 4
    listing = post(client, 'import-file-options.children', 'import-location.data',
                   {'import-location': location, 'import-selection': selection})
    assert all(item['type'] == 'Div' and 'aria-checked' not in item['props'] for item in listing['import-file-options']['children'])
    display = post(client, 'import-selected-panel.style', 'import-preview.data',
                   {'import-preview': preview, 'import-selection': selection})
    assert display['import-group-edit-toggle']['style'] == {'display': 'none'}


def test_new_folder_replaces_preview_and_empty_folder_cannot_reuse_previous_data(imported, tmp_path):
    client, paths = imported
    _, first = open_folder(client, Path(paths[0]).parent)
    _, second = open_folder(client, Path(paths[1]).parent, first)
    assert second['paths'] == [paths[1]]
    empty = tmp_path / 'empty'
    empty.mkdir()
    _, final = open_folder(client, empty, second)
    assert final['paths'] == []
    assert final['assignments'] == {}


def test_late_folder_preview_cannot_replace_cancelled_selection(imported):
    client, paths = imported
    from scripts.webapp_dash.file_import import _selection_revision
    initial = {'paths': [], 'assignments': {}}
    location = {**svc.browse_import_files(str(Path(paths[0]).parent)), 'selection_revision': _selection_revision(initial)}
    cancelled = {**initial, 'epoch': 'cancelled'}
    response = client.post('/_dash-update-component', json=_payload(client,
        output_contains='import-selection.data', changed='import-location.data',
        inputs={'import-location': location}, states={'import-selection': cancelled}))
    assert response.status_code == 204


def test_truncated_folder_is_not_offered_as_complete_dataset(imported):
    client, paths = imported
    selection = {'paths': paths, 'assignments': {}, 'folder_truncated': True}
    preview = svc.preview_file_collection(paths)
    result = post(client, 'import-group-summary.children', 'import-preview.data',
                  {'import-preview': preview, 'import-group': 'run'}, {'import-selection': selection})
    assert result['dataset-browser-candidate']['data'] is None
    assert '上限' in str(result['import-group-summary']['children'])
