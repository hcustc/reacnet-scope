"""Dataset browser acceptance with isolated RNG samples."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright
from reacnet_scope import services as svc
from scripts.webapp_dash.background import SpawnDiskcacheManager
from tests.browser.workbench import reload_workbench, wait_for_initial_callbacks


@pytest.mark.browser_smoke
@pytest.mark.browser_area('dataset')
def test_current_dataset_menu_switches_explicitly_and_keeps_analysis_entry(page, workbench):
    _, dataset = workbench
    base = Path(dataset['folder']) / 'quick-switch'
    base.with_suffix('.reactionabcd').write_text('1 CCO->COC\n')
    base.with_suffix('.species').write_text('Timestep 0: CCO 1\n')
    other = svc.validate_dataset_candidate(str(base.parent), str(base))
    records = [{key: item[key] for key in ('folder', 'base', 'label', 'dataset_id')}
               for item in (dataset, other)]
    page.evaluate("records => window.dash_clientside.set_props('dataset-library', {data: records})", records)
    page.locator('#nav-reactions').click()
    page.locator('#current-dataset-menu > summary').click()
    page.locator('#library-select').click()
    page.get_by_text(f"{other['label']} · {other['folder']}", exact=True).click()
    expect(page.locator('#library-use')).to_be_enabled()
    expect(page.locator('#topbar-rungroup')).to_have_text(dataset['label'])
    page.locator('#library-use').click()
    expect(page.locator('#topbar-rungroup')).to_have_text(other['label'], timeout=20000)
    expect(page.locator('#page-reactions')).to_be_visible()
    expect(page.locator('#library-select')).not_to_be_visible()
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).dataset_id') == other['dataset_id']
    for width in (1366, 768, 375):
        page.set_viewport_size({'width': width, 'height': 900})
        page.locator('#current-dataset-menu > summary').click()
        expect(page.locator('#library-select')).to_be_visible()
        expect(page.locator('#topbar-rungroup')).to_be_visible()
        bounds = page.locator('.rs-dataset-popover').bounding_box()
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(screenshots) / f'dataset-switch-{width}.png'))
        page.locator('#current-dataset-menu > summary').click()


@pytest.mark.browser_area('dataset')
def test_current_dataset_menu_failure_keeps_current_data_and_visible_reason(page, workbench):
    _, dataset = workbench
    entry = {'folder': dataset['folder'], 'base': str(Path(dataset['folder']) / 'missing'),
             'dataset_id': 'missing', 'label': '不可用来源'}
    page.evaluate("entry => window.dash_clientside.set_props('dataset-library', {data: [entry]})", entry)
    page.locator('#current-dataset-menu > summary').click()
    page.locator('#library-select').click()
    page.get_by_text(f"不可用来源 · {dataset['folder']}", exact=True).click()
    page.locator('#library-use').click()
    expect(page.locator('#library-switch-status')).not_to_be_empty(timeout=15000)
    expect(page.locator('#topbar-rungroup')).to_have_text(dataset['label'])
    expect(page.locator('#library-select')).to_be_visible()
    expect(page.locator('#library-use')).to_be_enabled(timeout=15000)
    page.keyboard.press('Escape')
    expect(page.locator('#library-select')).not_to_be_visible()
    expect(page.locator('#current-dataset-menu > summary')).to_be_focused()


@pytest.mark.browser_area('dataset')
def test_empty_data_page_lists_imports_and_shows_switch_feedback(workbench):
    url, dataset = workbench
    entry = {key: dataset[key] for key in ('folder', 'base', 'label', 'dataset_id')}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        try:
            page.add_init_script(
                'localStorage.setItem("dataset-library", JSON.stringify(' + json.dumps([entry]) + '));'
                'sessionStorage.setItem("page-store", JSON.stringify({page:"data-management"}));'
            )
            page.goto(url)
            wait_for_initial_callbacks(page)
            expect(page.locator('#page-data-management.active')).to_be_visible(timeout=30000)
            expect(page.locator('.rs-current-dataset-empty')).to_be_visible(timeout=15000)
            workspace = page.locator('#data-library-workspace')
            expect(workspace.locator('#data-candidate-summary')).to_be_visible()
            expect(workspace.locator('#library-management-panel')).to_be_visible()
            expect(
                workspace.locator('#library-management-panel #data-candidate-summary')
            ).to_be_visible()
            expect(workspace.locator('#library-management-panel h3')).to_have_text('RNG 数据管理')
            expect(workspace.get_by_role('button', name='添加数据', exact=True)).to_have_count(1)
            page.set_viewport_size({'width': 1800, 'height': 900})
            empty_summary = workspace.locator('.rs-current-dataset-empty')
            expect(empty_summary).to_have_text('当前未选择用于分析的数据')
            expect(workspace.locator('#library-management-panel > p')).to_have_count(0)
            tools = page.locator('#data-overview-actions .rs-workflow-card')
            expect(tools).to_have_count(4)
            offsets = page.locator('.rs-workflow-grid').evaluate(
                '(grid) => [...grid.children].map(card => card.offsetTop)')
            assert max(offsets) - min(offsets) <= 1, offsets
            expect(page.locator('#data-overview-actions')).not_to_contain_text('管理RNG 数据')
            item = page.locator('#library-management-list .rs-library-item').first
            expect(item).to_contain_text(dataset['label'], timeout=15000)
            assert item.bounding_box()['y'] < page.locator('#data-overview-actions').bounding_box()['y']
            screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
            if screenshots:
                Path(screenshots).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(screenshots) / 'unified-dataset-library-empty.png'))
            page.evaluate(
                """
                () => {
                    window.__datasetSummaryFrames = [];
                    const workspace = document.querySelector('#data-library-workspace');
                    const capture = () => {
                        const panel = workspace.querySelector('.rs-data-summary-panel');
                        const summary = workspace.querySelector('#data-candidate-summary');
                        window.__datasetSummaryFrames.push({
                            panelVisible: getComputedStyle(panel).display !== 'none',
                            hasCurrentRow: Boolean(
                                workspace.querySelector('.rs-library-entry-status.is-current')
                            ),
                            summary: summary.textContent.trim(),
                        });
                    };
                    new MutationObserver(capture).observe(workspace, {
                        subtree: true,
                        childList: true,
                        attributes: true,
                        characterData: true,
                    });
                    capture();
                }
                """
            )
            item.get_by_role('button', name='切换到此数据').click()
            expect(item.locator('.rs-library-switch-feedback')).to_contain_text(
                '正在', timeout=15000,
            )
            expect(page.locator('#data-candidate-summary')).to_contain_text(
                dataset['label'], timeout=30000,
            )
            expect(item.get_by_role('button', name='切换到此数据')).not_to_be_visible()
            expect(item.locator('.rs-library-switch-success')).to_contain_text(
                '加载成功，已设为当前 RNG 数据', timeout=15000,
            )
            expect(item.locator('.rs-library-entry-status.is-current')).to_be_visible()
            expect(workspace.locator('.rs-data-summary-panel')).not_to_be_visible()
            frames = page.evaluate('window.__datasetSummaryFrames')
            assert not any(
                frame['panelVisible']
                and not frame['hasCurrentRow']
                and dataset['label'] in frame['summary']
                for frame in frames
            ), frames
            if screenshots:
                page.screenshot(path=str(Path(screenshots) / 'unified-dataset-library.png'))
        finally:
            browser.close()


@pytest.mark.browser_area('dataset', 'abundance')
def test_import_multiple_folders_switch_and_compare(page, workbench):
    _, dataset = workbench
    root = Path(dataset['folder'])
    paths = []
    for name in ('import-A', 'import-B'):
        folder = root / name
        folder.mkdir(exist_ok=True)
        (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
        (folder / 'run.species').write_text('Timestep 0: CCO 1 COC 1\n')
        paths.append(str(folder))
    page.locator('#nav-data-management').click()
    expect(page.locator('#library-management-panel')).to_be_visible()
    expect(page.locator('#data-candidate-summary')).not_to_be_visible()
    expect(page.locator('#library-management-list .rs-library-entry-status.is-current')).to_be_visible()
    expect(page.locator('#data-cache-management')).to_be_visible()
    expect(page.locator('#library-view')).to_have_count(0)
    expect(page.locator('#workspace-task-nav')).not_to_be_visible()
    expect(page.locator('#page-title')).to_have_text('RNG 数据')
    page.locator('#library-add-more').click()
    page.get_by_text('一次填写多个文件夹路径', exact=True).click()
    page.locator('#library-paths').fill('\n'.join([*paths, str(root / 'missing')]))
    page.locator('#library-add-paths').click()
    expect(page.locator('#library-draft-list .rs-library-item')).to_have_count(3)
    page.locator('#library-import').click()
    expect(page.locator('#library-import-status')).to_contain_text('已导入 2 个文件夹；1 个未导入', timeout=30000)
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == dataset['base']
    page.locator('#nav-data-management').click()
    expect(page.locator('#library-management-panel')).to_be_visible()
    expect(page.locator('#library-management-list')).to_contain_text('import-A')
    expect(page.locator('#library-management-list')).to_contain_text('import-B')
    screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
    if screenshots:
        Path(screenshots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshots) / 'dataset-library.png'))
    for path in paths:
        row = page.locator('#library-management-list .rs-library-item').filter(has_text=path)
        row.get_by_role('button', name='切换到此数据').click()
        expect(page.locator('#topbar-rungroup')).to_have_text(Path(path).name, timeout=20000)
        expect(page.locator('#page-data-management')).to_be_visible()
    expect(page.locator('#library-compare-selection')).to_have_count(0)
    expect(page.locator('#data-open-batch-compare-btn')).not_to_be_visible()
    page.locator('#nav-evolution').click()
    page.locator('#evolution-open-compare-btn').click()
    expect(page.locator('#page-evolution')).to_be_visible()
    expect(page.locator('#nav-evolution')).to_have_attribute('aria-current', 'page')
    page.wait_for_function('JSON.parse(sessionStorage.getItem("page-store")).compare_sources === true', timeout=10000)
    expect(page.locator('#compare-species-panel')).to_be_visible(timeout=10000)
    for path in paths:
        option = page.get_by_text(f'{Path(path).name} · 需准备丰度索引', exact=True)
        if not option.is_visible():
            page.locator('#species-compare-managed').click()
        option.click()
    page.keyboard.press('Escape')
    expect(page.locator('#species-compare-managed')).to_contain_text('import-A')
    expect(page.locator('#species-compare-managed')).to_contain_text('import-B')
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == paths[1] + '/run'
    records = page.evaluate('JSON.parse(localStorage.getItem("dataset-library"))')
    assert {p + '/run' for p in paths} <= {r['base'] for r in records}


@pytest.mark.browser_area('dataset', 'abundance')
def test_compare_imported_species_without_current_dataset(workbench):
    url, dataset = workbench
    root = Path(dataset['folder'])
    folders = [root / name for name in ('compare-A', 'compare-B')]
    for folder in folders:
        folder.mkdir(exist_ok=True)
        (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
        species = folder / 'run.species'
        species.write_text('Timestep 0: CCO 2\nTimestep 10: CCO 1 COC 1\n')
    entries = svc.inspect_dataset_folders([str(folder) for folder in folders])['entries']
    assert len(entries) == 2

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text)
                if message.type == 'error' else None)
        page.add_init_script('localStorage.setItem("dataset-library", JSON.stringify('
                             + json.dumps(entries) + '));')
        try:
            page.goto(url)
            wait_for_initial_callbacks(page)
            page.locator('#nav-species').click()
            expect(page.locator('#species-empty-state')).to_contain_text(
                '多来源趋势对比可直接选择多个已导入来源', timeout=15000,
            )
            expect(page.locator('#species-results-card')).to_be_visible()
            expect(page.locator('#species-open-compare-btn')).to_have_count(0)
            page.add_init_script(
                "sessionStorage.setItem('page-store', JSON.stringify({page:'batch-compare'}))"
            )
            reload_workbench(page)
            expect(page.locator('#page-evolution')).to_be_visible(timeout=20000)
            expect(page.locator('#page-description')).to_have_text(
                '查看累计采样丰度、丰度趋势与元素分布。'
            )
            assert page.evaluate(
                "JSON.parse(sessionStorage.getItem('page-store')).compare_sources"
            ) is True
            expect(page.locator('#species-compare-managed')).to_be_visible(timeout=20000)
            expect(page.locator('#page-data-status')).to_contain_text('可直接选择多个来源', timeout=30000)
            for folder in folders:
                option = page.get_by_text(f'{folder.name} · 需准备丰度索引', exact=True)
                if not option.is_visible():
                    page.locator('#species-compare-managed').click()
                option.click()
            page.keyboard.press('Escape')
            cards = page.locator('#species-compare-sources .rs-compare-source-card')
            expect(cards).to_have_count(2)
            expect(page.locator('#species-compare-run')).to_be_disabled()
            for card in (cards.nth(0), cards.nth(1)):
                prepare = card.get_by_role('button', name='准备索引')
                expect(prepare).to_be_enabled(timeout=15000)
                prepare.click()
            for card in (cards.nth(0), cards.nth(1)):
                expect(card).to_contain_text('丰度索引可用', timeout=35000)
            for card in (cards.nth(0), cards.nth(1)):
                card.get_by_text('按分子式 / SMILES / 质量数检索').click()
                card.locator('input[id*="species-compare-query"]').fill('C2O')
                card.get_by_role('button', name='检索', exact=True).click()
                expect(card.locator('.rs-compare-search-status')).to_contain_text('精确 Species')
                picker = card.locator('.rs-compare-target-field')
                picker.click()
                picker.get_by_role('option', name='C2O · CCO · 总数 3').click()
            expect(page.locator('#species-compare-run')).to_be_enabled()
            page.locator('#species-compare-run').click()
            expect(page.locator('#species-compare-summary .ag-center-cols-container .ag-row')).to_have_count(2)
            controls = page.locator('#species-compare-sources').bounding_box()
            graph = page.locator('#species-compare-graph').bounding_box()
            assert controls and graph and controls['x'] + controls['width'] < graph['x']
            assert abs(controls['y'] - graph['y']) < 170
            screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
            if screenshots:
                Path(screenshots).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(screenshots) / 'species-comparison-with-curves.png'))
            expect(page.locator('#topbar-rungroup')).to_have_text('未选择')
            assert not page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).dataset_id')
            page.locator('#evolution-close-compare-btn').click()
            expect(page.locator('#evolution-current-panel')).to_be_visible()
            expect(page.locator('#species-compare-managed')).not_to_be_visible()
            assert not errors, errors
        finally:
            browser.close()


@pytest.mark.browser_area('dataset')
def test_imported_dataset_builds_selected_index_without_switching_current(page, workbench):
    _, current = workbench
    folder = Path(current['folder']) / 'direct-index'
    folder.mkdir(exist_ok=True)
    (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
    (folder / 'run.species').write_text('Timestep 0: CCO 1 COC 1\n')

    page.locator('#nav-data-management').click()
    page.locator('#library-add-more').click()
    page.get_by_text('一次填写多个文件夹路径', exact=True).click()
    page.locator('#library-paths').fill(str(folder))
    page.locator('#library-add-paths').click()
    page.locator('#library-import').click()
    expect(page.locator('#library-import-status')).to_contain_text(
        '已导入 1 个文件夹；0 个未导入', timeout=30000,
    )
    page.locator('#nav-data-management').click()
    row = page.locator('#library-management-list .rs-library-item').filter(has_text=str(folder))
    composition = row.locator('.rs-library-index-row').first
    expect(composition).to_contain_text('尚未建立', timeout=15000)
    with page.expect_response(
        lambda response: response.url.endswith('/_dash-update-component')
        and 'library-build-request' in (response.request.post_data or ''),
        timeout=15000,
    ) as request_response:
        composition.get_by_role('button', name='准备索引').click()
    assert request_response.value.status == 200, request_response.value.text()
    expect(composition).to_contain_text('可用', timeout=30000)

    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == current['base']
    status = svc.dataset_preparation_status(str(folder), base=str(folder / 'run'))
    assert status['composition']['state'] == 'ready'
    assert status['events']['state'] != 'ready'
    assert status['trajectory']['state'] != 'ready'


@pytest.mark.browser_area('dataset')
def test_imported_index_builds_for_two_entries_finish_independently(page, workbench, monkeypatch):
    root = Path(workbench[1]['folder'])
    folders = [root / name for name in ('parallel-A', 'parallel-B')]
    for folder in folders:
        folder.mkdir()
        (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
        (folder / 'run.species').write_text('Timestep 0: CCO 1\n')
    entries = svc.inspect_dataset_folders([str(folder) for folder in folders])['entries']
    assert len(entries) == 2

    def slow_prepare(folder, *, expected_dataset_id, **_kwargs):
        Path(folder, 'started').write_text('started')
        time.sleep(12)
        Path(folder, 'finished').write_text('finished')
        return {'dataset_id': expected_dataset_id, 'ok': True}

    original_status = svc.dataset_preparation_status

    def preparing_status(folder, *, base):
        status = original_status(folder, base=base)
        if (Path(folder) in folders and Path(folder, 'started').exists()
                and not Path(folder, 'finished').exists()):
            status['composition'] = {
                **status['composition'], 'state': 'building',
                'task': {'state': 'running', 'progress': 0.3,
                         'progress_trusted': True, 'matches_current_revision': True},
            }
        return status

    monkeypatch.setattr(svc, 'prepare_dataset_workspace', slow_prepare)
    monkeypatch.setattr(svc, 'dataset_preparation_status', preparing_status)
    original_dispatch = SpawnDiskcacheManager.call_job_fn

    def dispatch_slow_worker(manager, key, job_fn, args, context):
        # Spawned workers import their own modules; install the slow operation
        # at the worker boundary instead of relying on inherited parent patches.
        def slow_job(*job_args):
            svc.prepare_dataset_workspace = slow_prepare
            job_fn(*job_args)

        return original_dispatch(manager, key, slow_job, args, context)

    monkeypatch.setattr(SpawnDiskcacheManager, 'call_job_fn', dispatch_slow_worker)
    page.evaluate('(entries) => localStorage.setItem("dataset-library", JSON.stringify(entries))', entries)
    reload_workbench(page)
    expect(page.locator('#topbar-rungroup')).to_have_text(workbench[1]['label'], timeout=30000)
    page.locator('#nav-data-management').click()
    expect(page.locator('#page-data-management')).to_be_visible()
    rows = [page.locator('#library-management-list .rs-library-item').filter(has_text=str(folder))
            for folder in folders]
    for row in rows:
        expect(row.locator('.rs-library-index-row').first).to_contain_text('尚未建立', timeout=15000)
    rows[0].locator('.rs-library-index-row').first.get_by_role('button', name='准备索引').click()
    deadline = time.monotonic() + 15
    while not (folders[0] / 'started').exists() and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert (folders[0] / 'started').exists()
    expect(rows[0].locator('.rs-library-index-progress')).to_be_visible(timeout=10000)
    expect(rows[0].locator('.rs-library-index-progress')).to_have_attribute('role', 'progressbar')
    expect(rows[0].locator('.rs-library-index-progress')).to_have_attribute('aria-valuenow', '30')
    expect(rows[0].locator('.rs-library-index-progress-fill')).to_have_attribute('style', 'width: 30%;')
    rows[1].locator('.rs-library-index-row').first.get_by_role('button', name='准备索引').click()

    for row, folder in zip(rows, folders):
        expect(row.locator('.rs-library-index-row').first).to_contain_text('索引已就绪', timeout=35000)
        assert (folder / 'finished').exists()


@pytest.mark.browser_area('dataset')
def test_import_browsed_rng_folder_without_batch_selection(page, workbench):
    _, current = workbench
    folder = Path(current['folder']) / 'direct-import'
    folder.mkdir(exist_ok=True)
    (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
    (folder / 'run.species').write_text('Timestep 0: CCO 1\n')
    page.locator('#nav-data-management').click()
    expect(page.locator('#page-title')).to_have_text('RNG 数据')
    page.locator('#library-add-more').click()
    page.locator('#import-path').fill(str(folder))
    page.locator('#import-browse-go').click()
    expect(page.locator('#import-file-options')).to_contain_text('run.species', timeout=15000)
    expect(page.locator('#library-import')).to_have_text('导入当前文件夹')
    expect(page.locator('#library-draft-list .rs-library-item')).to_have_count(0)
    page.locator('#library-import').click()
    expect(page.locator('#library-import-status')).to_contain_text('已导入 1 个文件夹；0 个未导入', timeout=30000)
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == current['base']
    page.locator('#nav-data-management').click()
    expect(page.locator('#library-management-list')).to_contain_text('direct-import')
