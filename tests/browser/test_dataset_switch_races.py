"""Controlled response ordering for the two-phase dataset switch."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect

from reacnet_scope import services as svc
from tests.browser.workbench import wait_for_initial_callbacks


_TRANSACTION = """() => {
    const state = window.store.getState();
    return state.paths.strs['dataset-switch-transaction']
        .reduce((node, key) => node[key], state.layout).props.data || {};
}"""


@pytest.mark.browser_area('dataset')
def test_old_candidate_refresh_cannot_erase_menu_switch(page, workbench):
    _, current = workbench
    base = Path(current['folder']) / 'race-switch'
    base.with_suffix('.reactionabcd').write_text('1 CCO->COC\n')
    base.with_suffix('.species').write_text('Timestep 0: CCO 1\n')
    other = svc.validate_dataset_candidate(str(base.parent), str(base))
    records = [{key: item[key] for key in ('folder', 'base', 'label', 'dataset_id')}
               for item in (current, other)]
    page.evaluate("records => window.dash_clientside.set_props('dataset-library', {data: records})", records)
    page.locator('#nav-reactions').click()
    expect(page.locator('#page-reactions')).to_be_visible()
    page.locator('#current-dataset-menu > summary').click()
    page.locator('#library-select').click()
    page.get_by_text(f"{other['label']} · {other['folder']}", exact=True).click()
    expect(page.locator('#library-use')).to_be_enabled()
    wait_for_initial_callbacks(page)
    page.evaluate("window.readSwitchTransaction = " + _TRANSACTION)
    # A selected candidate lets the test wait for either the old HTTP refresh
    # to be held or the synchronous browser refresh to finish.
    page.evaluate("value => window.dash_clientside.set_props('dataset-browser-candidate', {data: value})", current)
    page.wait_for_function("readSwitchTransaction().state === 'candidate-selected'")
    wait_for_initial_callbacks(page)
    held = {}

    def intercept(route):
        payload = route.request.post_data_json or {}
        if (payload.get('changedPropIds') == ['dataset-browser-candidate.data']
                and 'dataset-switch-transaction.data' in payload.get('output', '')):
            held['refresh'] = (route, route.fetch())
            page.evaluate('window.oldRefreshHeld = true')
        elif payload.get('output') == 'dataset-switch-validation.data':
            held['validation'] = (route, route.fetch())
            page.evaluate('window.validationHeld = true')
        else:
            route.continue_()

    page.route('**/_dash-update-component', intercept)
    try:
        page.evaluate("window.dash_clientside.set_props('dataset-browser-candidate', {data: null})")
        page.wait_for_function("window.oldRefreshHeld || readSwitchTransaction().state === 'idle'")
        page.locator('#library-use').click()
        page.wait_for_function("readSwitchTransaction().state === 'validating'")
        # Old code has a pending server refresh here. Deliver it only after
        # the menu request is live; fixed code has already refreshed locally.
        if 'refresh' in held:
            route, response = held.pop('refresh')
            route.fulfill(response=response)
            page.wait_for_function("readSwitchTransaction().state === 'idle'")
        page.wait_for_function('window.validationHeld === true')
        route, response = held.pop('validation')
        assert response.json()['response']['dataset-switch-validation']['data']['ok']
        route.fulfill(response=response)
        expect(page.locator('#topbar-rungroup')).to_have_text(other['label'], timeout=10000)
        expect(page.locator('#page-reactions')).to_be_visible()
        expect(page.locator('#library-select')).not_to_be_visible()
        assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).dataset_id') == other['dataset_id']
    finally:
        for route, response in held.values():
            route.fulfill(response=response)
        page.unroute('**/_dash-update-component', intercept)


@pytest.mark.parametrize('state', ['validating', 'succeeded'])
@pytest.mark.browser_area('dataset')
def test_library_transaction_survives_candidate_clear_and_same_page_refresh(page, workbench, state):
    _, current = workbench
    page.evaluate('window.readSwitchTransaction = ' + _TRANSACTION)
    # No switch request is emitted here: isolate refresh ownership with either
    # an active validation or a successful no-op on the current source.
    transaction = {'state': state, 'request_id': 'library-refresh-owner',
                   'origin': {'page': 'species', 'library': True},
                   'candidate': current, 'validation': current}
    page.evaluate("value => window.dash_clientside.set_props('dataset-switch-transaction', {data: value})", transaction)
    page.wait_for_function('state => readSwitchTransaction().state === state', arg=state)
    wait_for_initial_callbacks(page)
    before_refresh = page.evaluate('readSwitchTransaction()')
    page.evaluate("window.dash_clientside.set_props('dataset-browser-candidate', {data: null})")
    page.evaluate("window.dash_clientside.set_props('page-store', {data: {"
                  "page: 'species', version: 2, dataset_return: {page: 'species', trigger: 'data-pick-btn'}"
                  "}})")
    expect(page.locator('#page-species')).to_be_visible()
    wait_for_initial_callbacks(page)
    assert page.evaluate('readSwitchTransaction()') == before_refresh


@pytest.mark.browser_area('dataset')
def test_cancelled_preview_transaction_rejects_late_validation(page, workbench):
    _, current = workbench
    page.evaluate('window.readSwitchTransaction = ' + _TRANSACTION)
    wait_for_initial_callbacks(page)
    dependencies = page.request.get(workbench[0] + '/_dash-dependencies').json()
    refreshes = [item for item in dependencies
                 if 'dataset-switch-transaction.data' in item['output']
                 and item.get('clientside_function')
                 and any(value['id'] in {'dataset-browser-candidate', 'page-store'}
                         for value in item['inputs'])]

    def reduce_preview(candidate, page_store, transaction, changed):
        dependency = next(item for item in refreshes
                          if any(value['id'] == changed.split('.')[0] for value in item['inputs']))
        values = {'dataset-browser-candidate': candidate, 'page-store': page_store,
                  'dataset-switch-transaction': transaction}
        args = [values[value['id']] for value in dependency['inputs'] + dependency['state']]
        return page.evaluate("""({refresh, args, changed}) => {
            const previous = window.dash_clientside.callback_context;
            window.dash_clientside.callback_context = {triggered: [{prop_id: changed}]};
            try {
                return window.dash_clientside[refresh.namespace][refresh.function_name](...args);
            } finally {
                window.dash_clientside.callback_context = previous;
            }
        }""", {'refresh': dependency['clientside_function'], 'args': args, 'changed': changed})

    selected = reduce_preview(current, {'page': 'data-management'}, {},
                              'dataset-browser-candidate.data')
    assert selected['state'] == 'candidate-selected'
    assert selected['candidate']['dataset_id'] == current['dataset_id']
    for changed, reason in (('dataset-browser-candidate.data', 'candidate_changed'),
                            ('page-store.data', 'left_workspace')):
        transaction = svc.begin_dataset_switch(current, origin={'page': 'species'})
        # Invoke the registered browser reducer with Dash's trigger context,
        # then feed its actual output to the live validation resolver. Folder
        # enumeration and draft construction are outside this cancellation seam.
        cancelled = reduce_preview(None, {'page': 'species'}, transaction, changed)
        assert cancelled['state'] == 'superseded'
        assert cancelled['reason'] == reason
        assert cancelled['request_id'] == transaction['request_id']
        page.evaluate("value => window.dash_clientside.set_props('dataset-switch-transaction', {data: value})", cancelled)
        page.wait_for_function("readSwitchTransaction().state === 'superseded'")
        with page.expect_response(lambda response: response.request.post_data is not None
                                  and response.request.post_data_json.get('changedPropIds') == ['dataset-switch-validation.data']
                                  and 'dataset-switch-transaction.data' in response.request.post_data_json.get('output', '')) as late:
            page.evaluate("value => window.dash_clientside.set_props('dataset-switch-validation', {data: value})",
                          {'request_id': transaction['request_id'], 'ok': True,
                           'validation': current, 'completed_ns': time.time_ns()})
        assert late.value.status == 204
        assert page.evaluate('readSwitchTransaction().state') == 'superseded'
        expect(page.locator('#topbar-rungroup')).to_have_text(current['label'])
        assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).dataset_id') == current['dataset_id']
