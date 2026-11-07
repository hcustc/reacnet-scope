"""Abundance browser acceptance with isolated RNG samples."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect
from reacnet_scope import services as svc
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from tests.browser.workbench import set_abundance_targets, wait_for_initial_callbacks


@pytest.mark.browser_area('abundance')
def test_trend_workspace_opens_single_source_evolution(page, workbench):
    _, dataset = workbench
    page.locator("#nav-evolution").click()
    expect(page.locator("#page-title")).to_have_text("演化")
    expect(page.locator("#page-evolution")).to_be_visible()
    expect(page.locator("#nav-evolution")).to_have_attribute("aria-current", "page")
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])


@pytest.mark.browser_area('abundance')
def test_abundance_rank_draws_in_place_and_explicit_detail_returns_to_same_rows(prepared_abundance_page, workbench):
    page = prepared_abundance_page
    page.locator('#nav-evolution').click()
    expect(page.locator('#evolution-rank-grid')).not_to_be_visible()
    page.locator('#evolution-open-finder').click()
    page.locator('#evolution-ranking .accordion-button').click()
    expect(page.locator('#evolution-rank-status')).to_contain_text('丰度计数之和', timeout=15000)
    rows = page.locator('#evolution-rank-grid .ag-center-cols-container .ag-row')
    expect(rows).not_to_have_count(0)
    species = rows.first.locator('[col-id="smiles"]').inner_text()
    rows.first.click()
    expect(page.locator('#page-evolution')).to_be_visible()
    expect(page.locator('#evolution-graph .scatterlayer .trace')).not_to_have_count(0, timeout=15000)
    expect(page.locator('#evolution-rank-open-btn')).to_be_enabled()
    page.locator('#evolution-rank-open-btn').click()
    expect(page.locator('#page-species')).to_be_visible()
    expect(page.locator('#detail-body')).to_contain_text(species)
    assert page.evaluate('sessionStorage.getItem("research-species")') in (None, 'null')
    expect(page.locator('#species-handoff-back-btn')).to_be_visible(timeout=10000)
    page.locator('#species-handoff-back-btn').click()
    expect(page.locator('#page-evolution')).to_be_visible()
    page.locator('#evolution-open-finder').click()
    expect(rows.first.locator('[col-id="smiles"]')).to_have_text(species)


@pytest.mark.browser_area('abundance')
def test_species_comparison_searches_large_catalog_without_loading_all_options(page, workbench):
    _, current = workbench
    species = Path(current['folder']) / 'many.species'
    species.write_text(
        'Timestep 0: ' + ' '.join(f"{'C' * size} 1" for size in range(1, 121)) + '\n'
    )
    SPECIES_COMPOSITION_STORE.build(str(species))

    page.locator('#nav-evolution').click()
    page.locator('#evolution-open-compare-btn').click()
    page.get_by_text('未在列表中？手工添加 Species 文件').click()
    page.locator('#species-compare-path').fill(str(species))
    page.locator('#species-compare-add-path').click()
    card = page.locator('#species-compare-sources .rs-compare-source-card').filter(has_text=str(species))
    expect(card).to_have_count(1)
    expect(card).to_contain_text('120 个精确物种')
    picker = card.locator('.rs-compare-target-field')
    picker.click()
    picker.locator('input.dash-dropdown-search').fill('C' * 120)
    match = picker.get_by_role('option', name='C' * 120 + ' · 总数 1')
    expect(match).to_be_visible()
    assert picker.locator('input.dash-options-list-option-checkbox').count() <= 51
    match.click()
    expect(picker).to_contain_text('C' * 120)


@pytest.mark.browser_area('abundance')
def test_abundance_comparison_stays_in_page_and_returns_to_current_inputs(prepared_abundance_page, workbench):
    page = prepared_abundance_page
    _, dataset = workbench
    page.locator('#nav-evolution').click()
    expect(page.locator('#evolution-target-entry')).to_be_visible()
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(0)
    set_abundance_targets(page, 'CCO')
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(1)
    page.locator('#evolution-open-compare-btn').click()
    expect(page.locator('#page-evolution')).to_be_visible()
    expect(page.locator('#compare-species-panel')).to_be_visible()
    expect(page.locator('#evolution-current-panel')).not_to_be_visible()
    expect(page.locator('#nav-evolution')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#topbar-rungroup')).to_have_text(dataset['label'])
    page.locator('#evolution-close-compare-btn').click()
    expect(page.locator('#evolution-current-panel')).to_be_visible()
    expect(page.locator('#compare-species-panel')).not_to_be_visible()
    expect(page.locator('#evolution-targets')).to_have_value('CCO')
    wait_for_initial_callbacks(page)
    set_abundance_targets(page, 'C2')
    expect(page.locator('#evolution-target-meaning')).to_contain_text('C 原子数 = 2，其他元素不限')
    page.locator('#evolution-query-run').click()
    expect(page.locator('#evolution-focus')).to_contain_text('汇总 4 个精确物种')
    page.wait_for_function('document.querySelector("#evolution-graph .js-plotly-plot")?.data?.[0]?.y?.join() === "2,1,1"')
    page.locator('#evolution-open-settings').click()
    page.locator('#evolution-settings-section > summary').click()
    page.locator('#evolution-distribution-tab').click()
    expect(page.locator('#page-element-distribution')).to_be_visible()
    expect(page.locator('#nav-evolution')).to_have_attribute('aria-current', 'page')


@pytest.mark.browser_area('abundance')
def test_carbon_condition_aggregates_without_switching_modes(page, workbench):
    folder = Path(workbench[1]['folder']) / 'six-carbon'
    folder.mkdir(exist_ok=True)
    base = folder / 'carbon'
    ortho = '[H]OC1=C(Cl)C([H])=C([H])C([H])=C1[H]'
    para = '[H]OC1=C([H])C([H])=C(Cl)C([H])=C1[H]'
    carbon = '[C]' * 6
    other_carbons = ' '.join(f"{'[C]' * n} {n}" for n in range(1, 6))
    base.with_suffix('.reactionabcd').write_text(f'1 {ortho}->{para}\n')
    base.with_suffix('.species').write_text(''.join(
        f'Timestep {step}: {ortho} {count} {para} 3 {carbon} 2 [O]=[O] 20 {other_carbons}\n'
        for step, count in [(0, 10), (10, 4), (20, 2)]))
    SPECIES_COMPOSITION_STORE.build(str(base.with_suffix('.species')))
    dataset = svc.validate_dataset_candidate(str(folder), str(base))
    dataset.update(context_state='active', ready=True)
    page.evaluate('dataset => window.dash_clientside.set_props("app-store", {data: dataset})', dataset)
    wait_for_initial_callbacks(page)
    page.set_viewport_size({'width': 1720, 'height': 1000})
    page.locator('#nav-evolution').click()
    wait_for_initial_callbacks(page)
    expect(page.locator('#evolution-browse-mode')).not_to_be_visible()
    expect(page.locator('#evolution-rank-grid')).not_to_be_visible()
    expect(page.locator('#evolution-filter-query')).not_to_be_visible()
    six_targets = 'C6\nC5\nC4\nC3\nC2\nC1'
    set_abundance_targets(page, six_targets)
    expect(page.locator('#evolution-target-count')).to_have_text('6 个目标')
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(6)
    assert page.locator('#evolution-target-meaning').inner_text().count('其他元素不限') == 1
    if screenshots := os.environ.get('REACNET_SCOPE_SCREENSHOTS'):
        Path(screenshots).mkdir(parents=True, exist_ok=True)
        page.locator('#evolution-rank-panel').screenshot(path=str(Path(screenshots) / 'query-six-targets-desktop.png'))
    expect(page.locator('#evolution-targets')).to_have_value(six_targets)
    page.locator('#evolution-query-run').click()
    expect(page.locator('#evolution-match-details > summary')).to_contain_text('6 个条件')
    expect(page.locator('.rs-evolution-match-body')).not_to_be_visible()
    assert page.locator('#evolution-focus').bounding_box()['height'] <= 32
    page.wait_for_function("document.querySelector('#evolution-graph .js-plotly-plot')?.data?.length === 6")
    if screenshots:
        page.locator('#evolution-trend-panel').screenshot(path=str(Path(screenshots) / 'match-details-collapsed.png'))
    page.locator('#evolution-match-details > summary').focus()
    page.keyboard.press('Enter')
    expect(page.locator('.rs-evolution-match-body > div')).to_have_count(6)
    expect(page.locator('.rs-evolution-match-body')).to_contain_text('C6 · C 原子数 = 6 · 汇总 3 个精确物种')
    if screenshots:
        page.locator('#evolution-trend-panel').screenshot(path=str(Path(screenshots) / 'match-details-expanded.png'))
    page.locator('#evolution-match-details > summary').click()
    expect(page.locator('.rs-evolution-match-body')).not_to_be_visible()
    set_abundance_targets(page, f'C6\nformula:C6\nsmiles:{ortho}')
    expect(page.locator('#evolution-target-meaning')).to_contain_text('C 原子数 = 6，其他元素不限')
    expect(page.locator('#evolution-target-meaning')).to_contain_text('完整分子式 C6')
    page.locator('#evolution-query-run').click()
    expect(page.locator('#evolution-focus')).to_contain_text('汇总 3 个精确物种')
    page.wait_for_function('''() => {
        const curves = document.querySelector('#evolution-graph .js-plotly-plot')?.data;
        return curves?.length === 3 && curves[0].y.join() === '15,9,7'
            && curves[1].y.join() === '2,2,2' && curves[2].y.join() === '10,4,2';
    }''')
    with page.expect_download() as download:
        page.locator('#evolution-csv-btn').click()
    import csv
    rows = list(csv.reader(Path(download.value.path()).read_text().splitlines()))
    assert [float(row[2]) for row in rows[1:]] == [15, 9, 7]
    assert rows[0][2] != rows[0][3]
    if screenshots := os.environ.get('REACNET_SCOPE_SCREENSHOTS'):
        folder = Path(screenshots)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / 'carbon-unified-desktop.png'))
    # Unopened drawers must not prevent the initial query; closing keeps settings.
    expect(page.locator('#evolution-smooth')).not_to_be_visible()
    top = page.locator('#evolution-rank-panel').bounding_box()
    chart = page.locator('#evolution-trend-panel').bounding_box()
    assert top['y'] + top['height'] <= chart['y']
    assert abs(top['width'] - chart['width']) <= 2
    page.locator('#evolution-open-settings').click()
    page.locator('#evolution-processing-settings > summary').focus()
    page.keyboard.press('Enter')
    page.locator('#evolution-smooth').fill('3')
    page.wait_for_function('''() => {
        const box = document.getElementById('evolution-settings-drawer').getBoundingClientRect();
        return box.left >= 0 && box.right <= innerWidth + 1;
    }''')
    if screenshots:
        page.screenshot(path=str(folder / 'curve-settings-drawer.png'))
    page.locator('#evolution-settings-apply').click()
    expect(page.locator('#evolution-settings-drawer')).not_to_be_visible()
    page.wait_for_function('''() => {
        const values = document.querySelector('#evolution-graph .js-plotly-plot')?.data?.[0]?.y;
        return values?.map(v => Math.round(v * 1000000)).join() === '12000000,10333333,8000000';
    }''')
    page.locator('#evolution-open-settings').click()
    expect(page.locator('#evolution-smooth')).to_have_value('3')
    page.keyboard.press('Escape')
    expect(page.locator('#evolution-settings-drawer')).not_to_be_visible()
    expect(page.locator('#evolution-open-settings')).to_be_focused()
    page.set_viewport_size({'width': 390, 'height': 900})
    page.wait_for_function('''() => {
        const plot = document.querySelector('#evolution-graph .main-svg')?.getBoundingClientRect();
        const card = document.getElementById('evolution-trend-panel').getBoundingClientRect();
        return plot && plot.width <= card.width && plot.right <= innerWidth;
    }''')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    expect(page.locator('#evolution-target-preview')).to_be_visible()
    if screenshots:
        page.screenshot(path=str(folder / 'carbon-unified-mobile.png'))
    page.locator('#evolution-open-settings').click()
    expect(page.locator('#evolution-smooth')).to_have_value('3')
    page.wait_for_function('''() => {
        const box = document.getElementById('evolution-settings-drawer').getBoundingClientRect();
        return box.left >= 0 && box.right <= innerWidth + 1;
    }''')
    # Browser bounds can contain subpixel rounding at the viewport edge.
    drawer = page.locator('#evolution-settings-drawer').bounding_box()
    assert drawer['width'] <= page.viewport_size['width'] + 1
    if screenshots:
        page.screenshot(path=str(folder / 'curve-settings-mobile.png'))
    page.keyboard.press('Escape')


@pytest.mark.browser_area('abundance')
def test_slow_abundance_query_is_not_replaced_by_clearing_rank_selection(prepared_abundance_page, monkeypatch):
    page = prepared_abundance_page
    original = svc.build_species_evolution
    submitted = []

    def delayed_evolution(artifacts, targets, **kwargs):
        # The actual service still computes the result; model a large source's read latency.
        submitted.append(list(targets))
        time.sleep(0.7)
        return original(artifacts, targets, **kwargs)

    monkeypatch.setattr(svc, 'build_species_evolution', delayed_evolution)
    page.locator('#nav-evolution').click()
    wait_for_initial_callbacks(page)
    set_abundance_targets(page, 'C2')
    page.locator('#evolution-query-run').click()
    page.wait_for_function("document.querySelector('#evolution-graph .js-plotly-plot')?.data?.[0]?.y?.join() === '2,1,1'", timeout=5000)
    assert submitted == [['C2']]
    # Selecting a ranked species remains an explicit query, and a subsequent manual
    # query must clear that selection without replacing the running request.
    page.locator('#evolution-open-finder').click()
    page.locator('#evolution-ranking .accordion-button').click()
    row = page.locator('#evolution-rank-grid .ag-center-cols-container .ag-row').first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator('#evolution-focus')).to_contain_text('当前物种')
    page.keyboard.press('Escape')
    set_abundance_targets(page, 'C2')
    page.locator('#evolution-query-run').click()
    expect(page.locator('#evolution-focus')).to_contain_text('汇总 4 个精确物种')
    page.wait_for_function("document.querySelector('#evolution-graph .js-plotly-plot')?.data?.[0]?.y?.join() === '2,1,1'", timeout=5000)
    assert len(submitted) == 3 and submitted[-1] == ['C2']
    page.locator('#evolution-open-finder').click()
    expect(page.locator('#evolution-rank-grid .ag-row-selected')).to_have_count(0)


@pytest.mark.browser_area('abundance')
def test_abundance_target_chips_support_edit_remove_batch_cancel_and_pending_query(prepared_abundance_page):
    page = prepared_abundance_page
    page.locator('#nav-evolution').click()
    wait_for_initial_callbacks(page)
    targets = 'C6\nC5\nC4\nC3\nC2\nC1\nformula:C6H5ClO'
    set_abundance_targets(page, targets)
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(7)
    expect(page.locator('#evolution-targets')).not_to_be_visible()
    page.get_by_role('button', name='编辑 C6', exact=True).click()
    expect(page.locator('#evolution-target-entry')).to_be_focused()
    expect(page.locator('#evolution-target-entry')).to_have_value('C6')
    page.locator('#evolution-target-entry').fill('C7')
    page.locator('#evolution-target-add').click()
    expect(page.get_by_role('button', name='编辑 C7', exact=True)).to_be_visible()
    page.get_by_role('button', name='删除 C7', exact=True).click()
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(6)
    page.locator('#evolution-target-entry').fill('C6')
    page.locator('#evolution-target-entry').press('Enter')
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(7)
    page.get_by_role('button', name='编辑 C5', exact=True).click()
    page.locator('#evolution-target-entry').fill('C8')
    page.locator('#evolution-target-cancel').click()
    expect(page.get_by_role('button', name='编辑 C5', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='编辑 C8', exact=True)).to_have_count(0)
    page.locator('#evolution-batch-open').click()
    page.locator('#evolution-batch-text').fill('C2')
    page.locator('#evolution-batch-cancel').click()
    expect(page.locator('#evolution-batch-modal')).not_to_be_visible()
    expect(page.locator('.modal-backdrop')).to_have_count(0)
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(7)
    set_abundance_targets(page, targets)
    if screenshots := os.environ.get('REACNET_SCOPE_SCREENSHOTS'):
        folder = Path(screenshots); folder.mkdir(parents=True, exist_ok=True)
        page.mouse.move(5, 5)
        page.locator('#evolution-rank-panel').screenshot(path=str(folder / 'target-input-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    if screenshots:
        page.locator('#evolution-rank-panel').screenshot(path=str(folder / 'target-input-mobile.png'))
    page.set_viewport_size({'width': 1366, 'height': 950})
    set_abundance_targets(page, 'C2')
    page.locator('#evolution-target-entry').fill('formula:O')
    # Submit without first clicking Add: both targets must enter one plot request.
    page.locator('#evolution-query-run').click()
    page.wait_for_function('''() => {
        const curves = document.querySelector('#evolution-graph .js-plotly-plot')?.data;
        return curves?.length === 2 && curves[0].y.join() === '2,1,1' && curves[1].y.join() === '1,0,0';
    }''', timeout=15000)
    expect(page.locator('#evolution-target-entry')).to_have_value('')
    expect(page.locator('#evolution-targets')).to_have_value('C2\nformula:O')
    page.locator('#evolution-target-entry').evaluate('''input => {
        const data = new DataTransfer(); data.setData('text/plain', 'C3\\nC4');
        input.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: data}));
    }''')
    expect(page.locator('#evolution-target-entry')).to_have_value('C3; C4')
    page.locator('#evolution-target-entry').press('Enter')
    expect(page.locator('#evolution-target-preview .rs-target-chip')).to_have_count(4)


@pytest.mark.browser_smoke
@pytest.mark.browser_area('abundance')
def test_abundance_csv_download_keeps_raw_points_for_the_drawn_curves(prepared_abundance_page):
    import csv
    page = prepared_abundance_page
    page.locator('#nav-evolution').click()
    wait_for_initial_callbacks(page)
    expect(page.locator('#evolution-csv-btn')).to_be_disabled()
    set_abundance_targets(page, '两碳::C2\n氧气::formula:O')
    page.locator('#evolution-open-settings').click()
    page.locator('#evolution-processing-settings > summary').click()
    page.locator('#evolution-normalize').click()
    page.get_by_text('窗口最大值', exact=True).click()
    page.locator('#evolution-smooth').fill('3')
    page.locator('#evolution-settings-section > summary').click()
    page.locator('#evolution-downsample').fill('1')
    page.locator('#evolution-curve-filter').fill('两碳')
    page.locator('#evolution-settings-apply').click()
    page.wait_for_function('''() => {
        const curves = document.querySelector('#evolution-graph .js-plotly-plot')?.data;
        return curves?.length === 1 && curves[0].x.join() === '0,20' && curves[0].y.join() === '0.75,0.5';
    }''', timeout=15000)
    expect(page.locator('#evolution-csv-btn')).to_have_text('下载数据 CSV')
    expect(page.locator('#evolution-csv-btn')).to_be_enabled()
    with page.expect_download() as downloaded:
        page.locator('#evolution-csv-btn').click()
    assert downloaded.value.suggested_filename == 'abundance_raw.csv'
    contents = Path(downloaded.value.path()).read_bytes()
    assert contents.startswith(b'\xef\xbb\xbf')
    rows = list(csv.reader(contents.decode('utf-8-sig').splitlines()))
    assert rows[0] == ['analyzed_frame', 'source_timestep', '两碳']
    assert [row[:2] for row in rows[1:]] == [['0', '0'], ['1', '10'], ['2', '20']]
    assert [float(row[2]) for row in rows[1:]] == [2, 1, 1]
    expect(page.locator('#evolution-csv-btn')).to_be_enabled()
