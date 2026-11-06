"""Events browser acceptance with isolated RNG samples."""
from __future__ import annotations

import os

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect
from scripts.webapp_dash.callbacks import _event_columns, _event_table_rows


@pytest.mark.browser_area('events')
def test_event_enter_and_progressive_options_preserve_values(page):
    page.locator('#nav-events').click()
    expect(page.locator('#event-rxn-before')).not_to_be_visible()
    page.locator('#event-window-summary').click()
    page.locator('#event-rxn-before').fill('0')
    page.locator('#event-rxn-after').fill('5')
    expect(page.locator('#event-window-summary')).to_have_text('轨迹窗口：前 0 帧 / 后 5 帧')
    page.locator('#event-window-summary').click()
    page.locator('#event-reaction-text').fill('CCO->CC=O')
    page.locator('#event-reaction-text').press('Enter')
    expect(page.locator('#event-grid .ag-center-cols-container .ag-row')).not_to_have_count(0, timeout=15000)
    page.locator('#nav-reactions').click()
    expect(page.locator('#rxn-share-options')).not_to_be_visible()
    page.locator('#rxn-with-share').check()
    expect(page.locator('#rxn-share-options')).to_be_visible()
    page.locator('#rxn-share-abs').check()
    page.locator('#rxn-with-share').uncheck()
    expect(page.locator('#rxn-share-options')).not_to_be_visible()
    page.locator('#rxn-with-share').check()
    expect(page.locator('#rxn-share-abs')).to_be_checked()


@pytest.mark.browser_area('events')
def test_late_qc_preview_is_not_displayed_after_event_changes(page):
    with page.expect_response(
        lambda response: response.request.post_data is not None
        and '"event-dft-response.data"' in response.request.post_data
    ) as captured:
        page.evaluate("window.dash_clientside.set_props('event-dft-preview-btn', {n_clicks: 1})")
    request_data = captured.value.request.post_data_json
    preview_request = next(item["value"] for item in request_data["inputs"]
                           if item["id"] == "event-dft-request")
    expect(page.locator("#event-dft-validation")).not_to_be_empty(timeout=15000)
    stale = {
        "request_id": preview_request["id"],
        "payload": {"readiness_report": {"qc_handoff": {"status": "ready"}}},
        "validation": "QC OLD PREVIEW",
        "summary": [],
        "options": [],
        "file": None,
        "panel": {"display": "block"},
        "disabled": False,
    }
    page.evaluate("response => window.dash_clientside.set_props('event-dft-response', {data: response})", stale)
    expect(page.locator("#event-dft-validation")).to_contain_text("QC OLD PREVIEW")
    page.evaluate("window.dash_clientside.set_props('event-selected-store', {data: {row: {event_id: 'new-event'}}})")
    expect(page.locator("#event-dft-validation")).not_to_contain_text("QC OLD PREVIEW")
    page.evaluate("response => window.dash_clientside.set_props('event-dft-response', {data: response})", stale)
    expect(page.locator("#event-dft-validation")).not_to_contain_text("QC OLD PREVIEW")
    expect(page.locator("#event-dft-download-btn")).to_be_disabled()


@pytest.mark.browser_area('events')
def test_late_trajectory_response_cannot_replace_a_new_event(page):
    source = page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store"))')
    fingerprint = (source.get('source_revision') or {}).get('fingerprint') or ''

    def select(event_id, token):
        page.evaluate(
            "selection => window.dash_clientside.set_props('event-selected-store', {data: selection})",
            {'row': {'event_id': event_id}, 'kind': 'rng_event', 'selection_token': token},
        )
        page.wait_for_timeout(500)

    def staged(event_id, token):
        return {
            'key': {
                'trigger': 'trajectory-refresh-btn',
                'selected_event_id': event_id,
                'selected_token': token,
                'drilldown_event_id': '',
                'drilldown_token': None,
                'dataset_id': source.get('dataset_id') or '',
                'fingerprint': fingerprint,
                'clicks': [0, 0, 0, 0],
            },
            'values': [
                {'event_id': event_id, 'frames': []}, {'display': 'block'},
                {'namespace': 'dash_html_components', 'type': 'Div',
                 'props': {'children': f'VIEWER {event_id}'}},
                '', '', '', 0, 0, 0, {}, [], '',
            ],
        }

    select('event-one', 'selection-one')
    page.evaluate(
        "response => window.dash_clientside.set_props('event-viewer-response', {data: response})",
        staged('event-one', 'selection-one'),
    )
    expect(page.locator('#event-viewer-summary')).to_contain_text('VIEWER event-one')
    select('event-two', 'selection-two')
    page.evaluate(
        "response => window.dash_clientside.set_props('event-viewer-response', {data: response})",
        staged('event-two', 'selection-two'),
    )
    expect(page.locator('#event-viewer-summary')).to_contain_text('VIEWER event-two')
    page.evaluate(
        "response => window.dash_clientside.set_props('event-viewer-response', {data: response})",
        staged('event-one', 'selection-one'),
    )
    expect(page.locator('#event-viewer-summary')).to_contain_text('VIEWER event-two')
    expect(page.locator('#event-viewer-card')).to_have_css('display', 'block')

    def lineage(event_id, token):
        return {
            'key': {
                'selected_event_id': event_id,
                'selected_token': token,
                'viewer_event_id': event_id,
                'dataset_id': source.get('dataset_id') or '',
                'fingerprint': fingerprint,
                'clicks': [0, 0],
            },
            'values': [
                {'query': {'event_id': event_id},
                 'context': {'dataset_id': source.get('dataset_id') or '',
                             'source_revision': {'fingerprint': fingerprint}},
                 'views': {}, 'event_nodes': [], 'branch_summaries': []},
                '', f'LINEAGE {event_id}', '', {}, False, False,
            ],
        }

    page.evaluate(
        "response => window.dash_clientside.set_props('molecule-lineage-response', {data: response})",
        lineage('event-two', 'selection-two'),
    )
    expect(page.locator('#molecule-lineage-summary')).to_contain_text('LINEAGE event-two')
    page.evaluate(
        "response => window.dash_clientside.set_props('molecule-lineage-response', {data: response})",
        lineage('event-one', 'selection-one'),
    )
    expect(page.locator('#molecule-lineage-summary')).to_contain_text('LINEAGE event-two')


@pytest.mark.browser_area('events')
def test_event_query_without_time_conversion_keeps_ps_cells_blank(page):
    page.locator("#nav-events").click()
    rows = _event_table_rows([{
        "event_id": "event-1",
        "event_index": 1,
        "timestep_index": 0,
        "before_timestep": 0,
        "after_timestep": 1,
        "before_time_ps": None,
        "after_time_ps": None,
        "time_unit": "analyzed_frame",
    }])
    page.evaluate(
        "({rows, columns}) => window.dash_clientside.set_props('event-grid', "
        "{rowData: rows, columnDefs: columns})",
        {"rows": rows, "columns": _event_columns(rows)},
    )
    row = page.locator("#event-grid .ag-center-cols-container .ag-row").first
    expect(row).to_be_visible()
    expect(row.locator('[col-id="time_unit"]')).to_have_text("analyzed_frame")
    expect(row.locator('[col-id="before_time_ps"]')).to_be_empty()
    expect(row.locator('[col-id="after_time_ps"]')).to_be_empty()


@pytest.mark.browser_area('events')
def test_independent_event_query_uses_common_detail_and_preserves_result(page):
    page.locator('#nav-events').click()
    page.locator('#event-reaction-text').fill('CCO -> COC')
    page.locator('#event-rxn-btn').click()
    row = page.locator('#event-grid .ag-center-cols-container .ag-row').first
    expect(row).to_be_visible(timeout=15000)
    row.click()
    expect(page.locator('#event-detail-panel')).to_be_visible(timeout=15000)
    expect(page.locator('#event-detail-body')).to_contain_text('事件')
    expect(page.locator('#event-detail-expand-btn')).to_be_disabled()
    page.keyboard.press('Escape')
    expect(page.locator('#event-detail-panel')).not_to_be_visible()
    expect(row).to_be_visible()
