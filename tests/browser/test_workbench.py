"""Opt-in real browser acceptance, using only isolated small RNG fixtures.

REACNET_SCOPE_BROWSER_TESTS=1 uv run --locked --group browser pytest -q tests/browser
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from threading import Thread

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from reacnet_scope import services as svc
from reacnet_scope import dir_browser
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.callbacks import _event_columns, _event_table_rows


@pytest.fixture(scope="module", params=["0", "1"], ids=["classic", "compact"])
def workbench(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("browser-workbench")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("REACNET_SCOPE_COMPACT_NAV", request.param)
        patch.setenv("REACNET_SCOPE_CACHE_DIR", str(root / "workspace"))
        patch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(root))
        patch.setattr(dir_browser, "ALLOWED_ROOTS", [root])
        base = root / "demo"
        base.with_suffix(".reactionabcd").write_text(
            "2 CCO+O->CC=O+[H][H]\n1 CC=O->CC(=O)O\n1 CCO->COC\n1 COC->CC=O\n1 CCO->CC=O\n"
            "1 CCCCO->CCCOC\n1 CCOCC->CC(C)CO\n1 CC(C)(C)O->CCC(C)O\n"
            "1 CC(C)OC->CC(=O)CC\n1 CCCC=O->C=CCCO\n")
        base.with_suffix(".species").write_text(
            "Timestep 0: CCO 1 O 1 COC 1\nTimestep 10: CC=O 1 [H][H] 1\nTimestep 20: CC(=O)O 1\n")
        events = base.with_suffix(".reactionevent.csv")
        events.write_text("Timestep_Index,Reactant,Product\n0,CC=O,CC(=O)O\n"
                          "1,CCO+O,CC=O+[H][H]\n2,CCO,COC\n3,COC,CC=O\n"
                          "4,CCO,CC=O\n5,CCO+O,CC=O+[H][H]\n")
        molecules = base.with_suffix(".molecules.csv")
        molecules.write_text(
            "Timestep,Species,AtomIDs,BondIDs\n"
            + "".join(f"{step},CCO,0,\n" for step in range(7))
        )
        EVENT_EVIDENCE_STORE.build(str(events), str(molecules))
        context = svc.validate_dataset_candidate(str(root), str(base))
        context.update(context_state="active", ready=True)
        server = make_server("127.0.0.1", 0, create_app().server, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", context
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def page(workbench, request):
    url, dataset = workbench
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("request", lambda req: external.append(req.url) if req.url.startswith("http") and not req.url.startswith(url + "/") else None)
        page.add_init_script('sessionStorage.setItem("dataset-session-store",JSON.stringify(' + json.dumps(dataset) + '));'
                             'if (!sessionStorage.getItem("page-store")) {'
                             'sessionStorage.setItem("page-store",JSON.stringify({page:"species"}));}')
        try:
            page.goto(url)
            expect(page.locator("#species-query")).to_be_visible(timeout=30000)
            expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"], timeout=30000)
            expect(page.locator("#species-search-btn")).to_be_enabled(timeout=15000)
            expect(page).to_have_title("ReacNet Scope (Dash)", timeout=30000)
            yield page
            assert not errors, errors
            assert not external, external
            assert page.locator(".dash-error-card").count() == 0
        finally:
            screenshots = os.environ.get("REACNET_SCOPE_SCREENSHOTS")
            if screenshots:
                folder = Path(screenshots)
                folder.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(folder / f"{request.node.name}.png"))
            browser.close()


def query_species(page, query="C2O", count=3):
    page.locator("#species-query").fill(query)
    page.locator("#species-query").press("Enter")
    expect(page.locator("#species-grid .ag-center-cols-container .ag-row")).to_have_count(count, timeout=15000)


def test_navigation_preserves_dataset_and_restores_page(page, workbench):
    _, dataset = workbench
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])
    for target, label in [
        ("reactions", "反应"),
        ("evolution", "演化"),
        ("events", "事件"),
        ("data-management", "RNG 数据"),
        ("species", "物种"),
    ]:
        page.locator(f"#nav-{target}").click()
        expect(page.locator(f"#page-{target}")).to_be_visible()
        expect(page.locator(f"#nav-{target}")).to_have_attribute("aria-current", "page")
        expect(page.locator("#page-title")).to_have_text(label)
        assert page.locator(".rs-page-heading").evaluate(
            "element => getComputedStyle(element).position"
        ) == "absolute"
        expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])
    page.locator("#data-pick-btn").click()
    expect(page.locator("#import-path")).to_be_visible()
    page.locator("#dir-browser-cancel-btn").click()
    expect(page.locator("#page-species")).to_be_visible()
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == dataset["base"]
    page.locator("#nav-reactions").click()
    expect(page.locator("#page-title")).to_have_text("反应")
    page.wait_for_function('JSON.parse(sessionStorage.getItem("page-store")).page === "reactions"')
    page.reload()
    expect(page.locator("#page-reactions")).to_be_visible(timeout=30000)
    expect(page.locator("#nav-reactions")).to_have_attribute("aria-current", "page")
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"], timeout=30000)


def test_primary_navigation_has_no_mixed_visible_frame(page):
    page.evaluate(
        """
        () => {
            window.__navigationFrames = [];
            const capture = () => {
                const activePage = document.querySelector('.rs-page.active');
                const activeNav = document.querySelector(
                    '.rs-top-nav-item[aria-current="page"]'
                );
                const tasks = document.querySelector('#workspace-task-nav');
                const snapshot = {
                    activePage: activePage ? activePage.id : '',
                    activeNav: activeNav ? activeNav.id : '',
                    context: document.querySelector('#topbar-page-context').textContent.trim(),
                    tasksVisible: tasks.getClientRects().length > 0,
                    tasks: [...tasks.querySelectorAll('button')]
                        .filter(button => button.getClientRects().length > 0)
                        .map(button => button.textContent.trim()),
                };
                const previous = window.__navigationFrames.at(-1);
                if (!previous || JSON.stringify(previous) !== JSON.stringify(snapshot)) {
                    window.__navigationFrames.push(snapshot);
                }
            };
            new MutationObserver(capture).observe(document.querySelector('#app-body'), {
                subtree: true,
                childList: true,
                attributes: true,
                characterData: true,
            });
            window.__captureNavigationFrame = capture;
        }
        """
    )
    targets = [('reactions', '反应'), ('evolution', '演化'),
               ('events', '事件'), ('species', '物种')]
    for target, label in targets:
        page.evaluate('window.__navigationFrames = []; window.__captureNavigationFrame();')
        page.locator(f'#nav-{target}').click()
        expect(page.locator(f'#page-{target}')).to_be_visible()
        expect(page.locator('#topbar-page-context')).to_have_text(label)
        task_buttons = page.locator('#workspace-task-nav button:visible')
        expect(task_buttons).to_have_count(0)
        frames = page.evaluate('window.__navigationFrames')
        mixed = [
            frame for frame in frames
            if frame['activePage'] == f'page-{target}' and (
                frame['activeNav'] != f'nav-{target}'
                or frame['context'] != label
                or frame['tasks']
            )
        ]
        assert not mixed, mixed


def test_trend_workspace_opens_single_source_evolution(page, workbench):
    _, dataset = workbench
    page.locator("#nav-evolution").click()
    expect(page.locator("#page-title")).to_have_text("演化")
    expect(page.locator("#page-evolution")).to_be_visible()
    expect(page.locator("#nav-evolution")).to_have_attribute("aria-current", "page")
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])


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


@pytest.mark.parametrize('width', [1366, 768])
def test_workspace_return_restores_scroll_without_query_and_revision_clears_it(page, workbench, width):
    _, dataset = workbench
    page.set_viewport_size({'width': width, 'height': 500})
    query_species(page)
    requests = []
    page.on('request', lambda req: requests.append(req.post_data or '')
            if '/_dash-update-component' in req.url else None)
    page.locator('.rs-main').evaluate('''element => {
        const scroller = element.scrollHeight > element.clientHeight ? element : document.scrollingElement;
        scroller.scrollTop = 160;
    }''')
    scroll_position = 'document.querySelector(".rs-main").scrollTop + document.scrollingElement.scrollTop'
    page.wait_for_function(f'{scroll_position} > 40')
    position = page.evaluate(scroll_position)
    # Allow the passive scroll event to save the viewport position.
    page.wait_for_timeout(100)
    page.locator('#nav-events').click()
    expect(page.locator('#page-events')).to_be_visible()
    page.wait_for_function(f'{scroll_position} === 0')
    page.locator('#nav-species').click()
    expect(page.locator('#page-species')).to_be_visible()
    page.wait_for_function(f'position => Math.abs(({scroll_position}) - position) <= 2', arg=position)
    expect(page.locator('#species-grid .ag-center-cols-container .ag-row')).to_have_count(3)
    assert not any('"output":"species-search-btn-response.data"' in request.replace(' ', '') for request in requests)
    page.locator('#nav-events').click()
    page.evaluate("context => window.dash_clientside.set_props('app-store', {data: context})",
                  {**dataset, 'source_revision': 'new-revision'})
    page.wait_for_timeout(200)
    page.locator('#nav-species').click()
    page.wait_for_function(f'{scroll_position} === 0')


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


def test_abundance_rank_draws_in_place_and_explicit_detail_returns_to_same_rows(page, workbench):
    _, dataset = workbench
    SPECIES_COMPOSITION_STORE.build(dataset['artifacts']['species'])
    page.locator('#nav-evolution').click()
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
    expect(rows.first.locator('[col-id="smiles"]')).to_have_text(species)


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


def test_mass_search_detail_keeps_species_information_in_view(page):
    page.locator("#species-query").fill("39.994915")
    page.locator("#species-query").press("Enter")
    formula = page.locator("#species-grid .ag-center-cols-container .ag-row").first
    expect(formula).to_be_visible(timeout=15000)
    formula.locator('[col-id="formula"]').click()
    structure = page.locator("#species-structure-grid .ag-center-cols-container .ag-row").first
    expect(structure).to_be_visible(timeout=15000)
    structure.locator('[col-id="smiles"]').click()
    expect(page.locator("#detail-body")).to_be_visible()
    expect(page.locator("#detail-body")).to_contain_text("精确质量")
    expect(page.locator("#species-structure-stage")).not_to_be_visible()

    bounds = page.evaluate("""() => {
        const panel = document.querySelector('#detail-panel');
        const stats = document.querySelector('#detail-body .rs-detail-stats');
        return {
            panelRight: panel.getBoundingClientRect().right,
            statsRight: stats.getBoundingClientRect().right,
            scrollWidth: panel.scrollWidth,
            clientWidth: panel.clientWidth,
        };
    }""")
    assert bounds["statsRight"] <= bounds["panelRight"] + 1, bounds
    assert bounds["scrollWidth"] <= bounds["clientWidth"] + 1, bounds
    page.locator("#species-stage-back-btn").click()
    expect(page.locator("#species-structure-stage")).to_be_visible()


def test_exact_selection_export_and_responsive_detail(page):
    query_species(page)
    grid = page.locator("#species-grid")
    grid.locator('.ag-header-cell[col-id="smiles"]').click()
    grid.locator('.ag-header-cell[col-id="smiles"]').click()
    first = grid.locator('.ag-center-cols-container .ag-row[row-index="0"]')
    expect(first.locator('[col-id="smiles"]')).to_have_text("COC")
    first.locator('[col-id="smiles"]').hover()
    expect(page.locator(".rs-structure-tooltip img")).to_be_visible(timeout=5000)
    first.locator('[col-id="smiles"]').click()
    expect(page.locator("#detail-body")).to_contain_text("COC")
    expect(page.locator("#species-stage-back-btn")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#species-detail-stage")).not_to_be_visible()
    with page.expect_download() as downloaded:
        page.locator("#species-csv-btn").click()
    assert "COC" in Path(downloaded.value.path()).read_text(encoding="utf-8-sig")
    page.locator("#species-query").fill("39.99491462")
    expect(page.locator("#species-mass-field")).to_be_visible()
    expect(page.locator("#species-query-feedback")).to_contain_text("条件已修改")
    for width in (768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        expect(page.locator("#topbar-rungroup")).to_be_visible()
        dataset_bounds = page.locator("#topbar-rungroup").bounding_box()
        topbar_bounds = page.locator(".rs-topbar, .rs-navbar-compact").bounding_box()
        assert dataset_bounds["y"] + dataset_bounds["height"] <= topbar_bounds["y"] + topbar_bounds["height"]
        first.locator('[col-id="smiles"]').click()
        expect(page.locator("#species-stage-back-btn")).to_be_visible()
        detail_bounds = page.locator("#species-detail-stage").bounding_box()
        assert detail_bounds["y"] >= topbar_bounds["y"] + topbar_bounds["height"]
        overflow = page.evaluate("""() => ({
            viewport: innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            offenders: [...document.querySelectorAll('body *')]
                .filter(element => {
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.right > innerWidth + 1;
                })
                .slice(0, 12)
                .map(element => {
                    const rect = element.getBoundingClientRect();
                    return {
                        selector: `${element.tagName.toLowerCase()}#${element.id}.${element.className}`,
                        left: rect.left,
                        right: rect.right,
                        width: rect.width,
                    };
                }),
        })""")
        assert overflow["scrollWidth"] <= overflow["viewport"], overflow
        page.keyboard.press("Escape")
        expect(page.locator("#species-detail-stage")).not_to_be_visible()


def test_selected_species_opens_candidate_then_chooses_path_mode(page):
    query_species(page)
    first = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"]')
    species = first.locator('[col-id="smiles"]').inner_text()
    first.locator('[col-id="smiles"]').click()
    expect(page.locator('#detail-body')).to_contain_text(species)
    expect(page.locator('#species-candidate-menu')).to_have_count(0)
    shortcut = page.locator('#cp-from-species')
    expect(shortcut).to_be_enabled()
    shortcut.click()
    expect(page.locator('#page-reactions.active')).to_be_visible()
    expect(page.locator('#nav-species')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#cp-path-panel')).to_be_visible()
    expect(page.locator('#cp-mode label')).to_have_count(3)
    start_message = page.locator('#cp-start-message').inner_text()
    assert '已带入当前选中的精确物种' in start_message, {
        'message': start_message,
        'start': page.locator('#cp-start').inner_text(),
        'handoff': page.evaluate('JSON.parse(sessionStorage.getItem("cp-species-handoff") || "null")'),
    }
    expect(page.locator('#cp-start')).to_contain_text(species)
    assert page.locator('#cp-mode input:checked').input_value() == 'explore'

    page.locator('#cp-mode').get_by_text('从终点查前驱').click()
    expect(page.locator('#cp-start-wrap')).not_to_be_visible()
    expect(page.locator('#cp-target-wrap')).to_be_visible()
    expect(page.locator('#cp-target-message')).to_contain_text('已带入当前选中的精确物种')
    expect(page.locator('#cp-target')).to_contain_text(species)

    page.locator('#cp-mode').get_by_text('起点到终点').click()
    expect(page.locator('#cp-start-wrap')).to_be_visible()
    expect(page.locator('#cp-target-wrap')).to_be_visible()
    expect(page.locator('#cp-start')).to_contain_text(species)


def test_candidate_formula_choices_survive_mode_switch_after_species_handoff(page):
    query_species(page)
    first = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"]')
    first.locator('[col-id="smiles"]').click()
    page.locator('#cp-from-species').click()
    expect(page.locator('#cp-path-panel')).to_be_visible()
    page.locator('#cp-start-query').fill('C2H6O')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 2 个精确结构')
    page.locator('#cp-mode').get_by_text('起点到终点').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 2 个精确结构')
    page.locator('#cp-start').click()
    choices = page.locator('.dash-dropdown-options')
    expect(choices).to_contain_text('CCO')
    expect(choices).to_contain_text('COC')


def test_candidate_formula_many_choices_visible_after_species_handoff(page, monkeypatch):
    monkeypatch.setattr(svc, 'search_candidate_species', lambda _artifacts, _query: {
        'rows': [{'species': f'[{index}CH4]'} for index in range(1, 59)],
        'has_more': False,
    })
    query_species(page)
    first = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"]')
    first.locator('[col-id="smiles"]').click()
    page.locator('#cp-from-species').click()
    expect(page.locator('#cp-path-panel')).to_be_visible()
    page.locator('#cp-mode').get_by_text('起点到终点').click()
    page.locator('#cp-target-current').click()
    expect(page.locator('#cp-target-message')).to_contain_text('已带入当前选中的精确物种')
    page.locator('#cp-start-query').fill('C6H5ClO')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 58 个精确结构')
    page.set_viewport_size({'width': 1835, 'height': 160})
    page.locator('#cp-start').click()
    first_option = page.locator('.dash-dropdown-options [role="option"]').first
    expect(first_option).to_contain_text('[1CH4]')
    visible_pixels = first_option.evaluate('''option => {
        const content = option.closest('.dash-dropdown-content').getBoundingClientRect();
        const row = option.getBoundingClientRect();
        return Math.max(0, Math.min(content.bottom, row.bottom, innerHeight)
            - Math.max(content.top, row.top, 0));
    }''')
    assert visible_pixels > 0, '候选已加载，但下拉框没有给首个结构留下可见空间'
    first_option.click()
    expect(page.locator('#cp-start')).to_contain_text('[1CH4]')


def test_species_shortcuts_keep_context_and_compact_header(page, workbench):
    SPECIES_COMPOSITION_STORE.build(workbench[1]['artifacts']['species'])
    query_species(page)
    first = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"]')
    species = first.locator('[col-id="smiles"]').inner_text()
    first.locator('[col-id="smiles"]').click()
    expect(page.locator('#detail-body')).to_contain_text(species)

    page.locator('#species-to-channels-btn').click()
    expect(page.locator('#rxn-channel-view')).to_be_visible()
    page.wait_for_function('''() => {
        const state = JSON.parse(sessionStorage.getItem('page-store') || '{}');
        return state.page === 'reactions' && state.entry === 'species';
    }''', timeout=10000)
    expect(page.locator('#analysis-back-btn')).to_be_visible()
    expect(page.locator('#rxn-channel-back-btn')).not_to_be_visible()
    gap = page.evaluate('''() => {
        const back = document.querySelector('#analysis-back-btn').getBoundingClientRect();
        const settings = document.querySelector('#rxn-channel-time-settings').getBoundingClientRect();
        return settings.top - back.bottom;
    }''')
    assert gap <= 24, gap

    page.locator('#analysis-back-btn').click()
    expect(page.locator('#species-detail-stage')).to_be_visible()
    page.locator('#species-to-evolution-btn').click()
    expect(page.locator('#page-evolution.active')).to_be_visible()
    expect(page.locator('#evolution-targets')).to_have_value(species)
    expect(page.locator('#evolution-graph .scatterlayer .trace')).not_to_have_count(0)

    page.locator('#analysis-back-btn').click()
    expect(page.locator('#species-production-btn')).to_have_count(0)
    page.locator('#cp-from-species').click()
    expect(page.locator('#cp-path-panel')).to_be_visible()
    expect(page.locator('#cp-start')).to_contain_text(species)
    page.locator('#cp-mode').get_by_text('起点到终点').click()
    expect(page.locator('#cp-start')).to_contain_text(species)


def test_species_detail_and_reaction_focus_use_local_species_without_research_state(page):
    query_species(page)
    first = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"]')
    species = first.locator('[col-id="smiles"]').inner_text()
    first.locator('[col-id="smiles"]').click()
    expect(page.locator('#species-detail-stage')).to_be_visible()
    expect(page.locator('#research-start-btn')).to_have_count(0)
    assert page.evaluate('sessionStorage.getItem("research-species")') in (None, 'null')
    page.locator('#species-to-channels-btn').click()
    expect(page.locator('#rxn-channel-view')).to_be_visible()
    page.locator('#rxn-production-grid .ag-center-cols-container .ag-row').first.click()
    expect(page.locator('#rxn-channel-detail')).not_to_contain_text(
        '在上方表格中选择一条通道', timeout=15000)
    candidates = page.locator('[id*="species-open-detail"]')
    for index in range(candidates.count()):
        button = candidates.nth(index)
        identity = json.loads(button.get_attribute('id'))
        if identity.get('smiles') and identity['smiles'] != species:
            local_species = identity['smiles']
            button.click()
            break
    else:
        pytest.fail('fixture did not expose a different reaction participant')
    expect(page.locator('#page-species')).to_be_visible()
    expect(page.locator('#detail-body')).to_contain_text(local_species)
    expect(page.locator('#research-start-btn')).to_have_count(0)
    page.locator('#species-handoff-back-btn').click()
    expect(page.locator('#page-reactions')).to_be_visible()
    expect(page.locator('#rxn-channel-detail')).to_contain_text(local_species)


def test_late_query_responses_do_not_replace_current_results(page):
    responses = []
    def capture(response):
        if response.request.post_data and '"species-search-btn-response.data"' in response.request.post_data:
            responses.append(response.json()["response"]["species-search-btn-response"]["data"])
    page.on("response", capture)
    query_species(page)
    assert responses
    current = responses[-1]
    for stale in ("request", "dataset", "revision"):
        old = copy.deepcopy(current)
        old["values"][3] = "STALE RESULT"
        if stale == "request":
            old["request"] -= 1
        elif stale == "dataset":
            old["context"]["dataset_id"] = "old-dataset"
        else:
            old["context"]["source_revision"] = "old-revision"
        page.evaluate("response => window.dash_clientside.set_props('species-search-btn-response', {data: response})", old)
        page.wait_for_timeout(250)
        expect(page.locator("#species-alert")).not_to_contain_text("STALE RESULT")
        expect(page.locator("#species-grid .ag-center-cols-container .ag-row")).to_have_count(3)


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


def test_analysis_workspaces_and_reaction_query_states(page):
    expect(page.locator("#data-pick-btn")).to_be_visible()
    page.locator("#species-direct-route-btn").click()
    expect(page.locator("#cp-start-query")).to_be_visible()
    page.locator("#nav-events").click()
    expect(page.locator("#page-events")).to_be_visible()
    page.locator("#nav-reactions").click()
    expect(page.locator("#rxn-reactants")).to_be_visible()
    page.locator("#rxn-reactants").fill("C2O")
    page.locator("#rxn-reactants").press("Enter")
    expect(page.locator("#rxn-alert")).to_contain_text("找到", timeout=15000)
    page.locator("#rxn-products").fill("O")
    expect(page.locator("#rxn-query-feedback")).to_contain_text("条件已修改")
    page.locator("#rxn-products").press("Enter")
    expect(page.locator("#rxn-query-feedback")).to_contain_text("没有匹配反应", timeout=15000)
    for nav, target in [("nav-evolution", "page-evolution"),
                        ("nav-data-management", "page-data-management"),
                        ("nav-species", "page-species")]:
        page.locator("#" + nav).click()
        expect(page.locator("#" + target)).to_be_visible()
        if target == "page-data-management":
            expect(page.locator("#page-header")).not_to_be_visible()

        if os.environ.get("REACNET_SCOPE_SCREENSHOTS"):
            page.screenshot(path=str(Path(os.environ["REACNET_SCOPE_SCREENSHOTS"]) / f"{target}.png"))


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


def test_first_viewport_and_zero_mass_tolerance(page):
    expect(page.locator('#species-results-card')).not_to_be_visible()
    query_species(page, query="C4O", count=10)
    expect(page.locator('#species-results-card')).to_be_visible()
    eighth = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="7"]')
    bounds = eighth.bounding_box()
    assert bounds and bounds["y"] + bounds["height"] <= 768
    if os.environ.get("REACNET_SCOPE_SCREENSHOTS"):
        page.screenshot(path=str(Path(os.environ["REACNET_SCOPE_SCREENSHOTS"]) / "desktop-results.png"))
    page.locator("#species-query").fill("40")
    expect(page.locator("#species-mass-field")).to_be_visible()
    page.locator("#species-mass-tol").fill("0")
    page.locator("#species-mass-tol").press("Enter")
    expect(page.locator("#species-empty-state")).to_contain_text("没有匹配结果", timeout=15000)
    expect(page.locator("#species-mass-tol")).to_have_value("0")


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
            page.locator('#nav-species').click()
            expect(page.locator('#species-empty-state')).to_contain_text(
                '多来源趋势对比可直接选择多个已导入来源', timeout=15000,
            )
            expect(page.locator('#species-results-card')).to_be_visible()
            expect(page.locator('#species-open-compare-btn')).to_have_count(0)
            page.add_init_script(
                "sessionStorage.setItem('page-store', JSON.stringify({page:'batch-compare'}))"
            )
            page.reload()
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
    page.evaluate('(entries) => localStorage.setItem("dataset-library", JSON.stringify(entries))', entries)
    page.reload()
    page.locator('#nav-data-management').click()
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


def test_reaction_comparison_is_an_analysis_task(page):
    page.locator('#nav-reactions').click()
    page.locator('#reaction-open-compare-btn').click()
    expect(page.locator('#page-reaction-compare')).to_be_visible()
    expect(page.locator('#batch-managed-selector')).to_be_visible()
    expect(page.locator('#species-compare-managed')).not_to_be_visible()
    expect(page.locator('#nav-reactions')).to_have_attribute('aria-current', 'page')
    page.locator('#reaction-compare-back-btn').click()
    expect(page.locator('#page-reactions')).to_be_visible()


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


def test_candidate_net_direction_and_observed_switch(page, workbench):
    _, original = workbench
    root = Path(original['folder']) / 'net-direction'
    root.mkdir(exist_ok=True)
    base = root / 'net'
    base.with_suffix('.reactionabcd').write_text('2 CCO->COC\n1 COC->CCO\n')
    base.with_suffix('.species').write_text('Timestep 0: CCO 1\nTimestep 1: COC 1\n')
    source = base.with_suffix('.reactionevent.csv')
    source.write_text('Timestep_Index,Reactant,Product\n0,CCO,COC\n1,COC,CCO\n2,CCO,COC\n')
    EVENT_EVIDENCE_STORE.build(str(source))
    dataset = svc.validate_dataset_candidate(str(root), str(base))
    dataset.update(context_state='active', ready=True)
    page.add_init_script('sessionStorage.setItem("dataset-session-store",JSON.stringify(' +
                         json.dumps(dataset) + '));')
    page.reload()
    page.locator('#species-direct-route-btn').click()
    page.locator('#cp-mode').get_by_text('从起点看后续', exact=True).click()
    page.locator('#cp-start-query').fill('CCO')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
    expect(page.locator('#cp-direction-view input[value="net"]')).to_be_checked()
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('净转化方向', timeout=30000)
    canvas = page.locator('#cp-graph')
    def reaction_data():
        return canvas.evaluate('''element => {
            const cy = [...element.querySelectorAll('*'), element].find(e => e._cyreg)._cyreg.cy;
            return cy.nodes().filter(n => n.data('kind') === 'reaction').map(n => n.data());
        }''')
    expect(page.locator('#cp-graph-summary')).to_contain_text('1 条路线')
    nodes = reaction_data()
    assert [(n['reaction_key'], n['label']) for n in nodes] == [('CCO->COC', 'R1 · 净 1 次')]
    canvas.scroll_into_view_if_needed()
    position = canvas.evaluate('''element => {
        const cy = [...element.querySelectorAll('*'), element].find(e => e._cyreg)._cyreg.cy;
        return cy.nodes().filter(n => n.data('kind') === 'reaction')[0].renderedPosition();
    }''')
    canvas.click(position=position)
    expect(page.locator('#cp-step-detail')).to_contain_text('正向 2 次 · 逆向 1 次 · 净 1 次')
    page.locator('#cp-continue').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('未找到候选路线', timeout=30000)
    assert [n['reaction_key'] for n in reaction_data()] == ['CCO->COC']
    page.locator('#cp-back').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('找到 1 条候选路线', timeout=30000)
    page.locator('#cp-search-settings > summary').click()
    page.locator('#cp-direction-view').get_by_text('全部观测方向', exact=True).click()
    page.locator('#cp-start-query').fill('COC')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('全部观测方向', timeout=30000)
    nodes = reaction_data()
    assert [(n['reaction_key'], n['label']) for n in nodes] == [('COC->CCO', 'R1 · 类型 1 次')]


def test_candidate_search_focus_and_progressive_evidence(page):
    page.locator('#species-direct-route-btn').click()
    expect(page.locator('#cp-results')).not_to_be_visible()
    expect(page.locator('#cp-return-window')).not_to_be_visible()
    expect(page.locator('#cp-advanced-heading')).to_contain_text('净转化方向')
    # This fixture has reaction evidence without molecular associations: keep
    # the evidence limitation visible even in the compact result view.
    expect(page.locator('#cp-capability')).not_to_be_empty()
    for side, species in [('start', 'CCO'), ('target', 'CC(=O)O')]:
        page.locator(f'#cp-{side}-query').fill(species)
        page.locator(f'#cp-{side}-find').click()
        expect(page.locator(f'#cp-{side}-message')).to_contain_text('匹配 1')
        expect(page.locator(f'#cp-{side}-preview')).not_to_be_visible()
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('候选路线', timeout=30000)
    expect(page.locator('#cp-result-summary')).to_contain_text('不得解读为物质转化路线')
    expect(page.locator('#cp-start-query')).not_to_be_visible()
    expect(page.locator('#cp-graph-summary')).to_contain_text('1 条路线')
    expect(page.locator('#cp-graph-inspector')).not_to_be_visible()
    expect(page.locator('#cp-step-detail')).not_to_be_visible()
    page.locator('#cp-route-next').click()
    expect(page.locator('#cp-focus')).to_contain_text('路线 2')
    page.locator('#cp-graph-scope').get_by_text('返回路线总览').click()
    expect(page.locator('#cp-graph-summary')).to_contain_text('3 条路线')
    # Context participants must keep their edges after the local copies are
    # replaced by shared overview nodes (Cytoscape endpoint IDs are immutable).
    page.wait_for_function("""() => {
        const element = document.getElementById('cp-graph');
        const cy = [...element.querySelectorAll('*'), element].find(e => e._cyreg)._cyreg.cy;
        return cy.nodes().length > 0 && cy.nodes().every(n => n.degree() > 0);
    }""")
    page.locator('#cp-graph-scope').get_by_text('路线阅读').click()
    expect(page.locator('#cp-graph-summary')).to_contain_text('1 条路线')
    # Read canvas positions, then use real pointer clicks to exercise opening,
    # manually closing, and reopening the evidence for another step.
    canvas = page.locator('#cp-graph')
    for index in (0, 1):
        canvas.scroll_into_view_if_needed()
        position = canvas.evaluate("""(element, index) => {
            const host = [...element.querySelectorAll('*'), element].find(e => e._cyreg);
            return host._cyreg.cy.nodes().filter(n => n.data('kind') === 'reaction')[index].renderedPosition();
        }""", index)
        canvas.click(position=position)
        expect(page.locator('#cp-step-detail')).to_be_visible()
        page.locator('#cp-evidence > summary').click()
    page.locator('#cp-evidence > summary').click()
    expect(page.locator('#cp-step-detail')).to_be_visible()
    expect(page.locator('#cp-events')).to_be_visible()
    page.locator('#cp-export-menu button.dropdown-toggle').click()
    with page.expect_download() as downloaded:
        page.locator('#cp-json').click()
    report = json.loads(Path(downloaded.value.path()).read_text())
    assert len(report['paths']) == 3
    assert report['query']['start'] == 'CCO'
    page.locator('#cp-search-settings > summary').click()
    page.locator('#cp-search').click()
    expect(page.locator('#cp-start-query')).not_to_be_visible(timeout=30000)
    for width in (768, 390):
        page.set_viewport_size({'width': width, 'height': 900})
        page.locator('#cp-search-settings > summary').click()
        expect(page.locator('#cp-start-query')).to_be_visible()
        bounds = page.evaluate('({viewport: innerWidth, width: document.documentElement.scrollWidth})')
        assert bounds['width'] <= bounds['viewport'] + 1, bounds
        page.locator('#cp-search-settings > summary').click()


def test_candidate_fan_layout_zoom_and_evidence_space(page, workbench):
    # Isolated display fixture: network connectivity only, with its limitation
    # visible. Seven distinct branches exercise the wide fan from the UI brief.
    folder = Path(workbench[1]['folder']) / 'fan-layout'
    folder.mkdir(exist_ok=True)
    base = folder / 'fan'
    anchor = 'CCCCCCCC'
    products = ['C' * i for i in range(1, 8)]
    base.with_suffix('.reactionabcd').write_text(''.join(f'1 {anchor}->{p}\n' for p in products))
    base.with_suffix('.species').write_text(f'Timestep 0: {anchor} 1\n')
    events = base.with_suffix('.reactionevent.csv')
    events.write_text('Timestep_Index,Reactant,Product\n' + ''.join(
        f'{i},{anchor},{p}\n' for i, p in enumerate(products)))
    molecules = base.with_suffix('.molecules.csv')
    molecules.write_text('Timestep,Species,AtomIDs,BondIDs\n' + ''.join(
        f'{i},{anchor},0,\n' for i in range(8)))
    EVENT_EVIDENCE_STORE.build(str(events), str(molecules))
    dataset = svc.validate_dataset_candidate(str(folder), str(base))
    dataset.update(context_state='active', ready=True)
    page.evaluate('dataset => window.dash_clientside.set_props("app-store", {data: dataset})', dataset)
    page.set_viewport_size({'width': 1600, 'height': 1000})
    page.locator('#species-direct-route-btn').click()
    page.locator('#cp-mode').get_by_text('从起点看后续').click()
    page.locator('#cp-start-query').fill(anchor)
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('7 条候选路线', timeout=30000)
    page.locator('#cp-graph-scope').get_by_text('返回路线总览').click()
    expect(page.locator('#cp-graph-summary')).to_contain_text('7 条路线')
    canvas = page.locator('#cp-graph')
    canvas.scroll_into_view_if_needed()
    # Use the same component lookup as the real pointer-interaction test above.
    cy_expression = "[...document.querySelectorAll('#cp-graph *'), document.getElementById('cp-graph')].find(e => e._cyreg)._cyreg.cy"
    page.wait_for_function(f"() => {{const cy = {cy_expression}; return cy.nodes().length === 15 && cy.nodes()[0].renderedWidth() >= 80;}}")
    closed_width = canvas.bounding_box()['width']
    zoom = page.evaluate(f'() => {cy_expression}.zoom()')
    page.locator('#cp-graph-zoom-in').click()
    page.wait_for_function(f'() => {cy_expression}.zoom() > {zoom * 1.1}')
    page.locator('#cp-graph-reset').click()
    page.wait_for_function(f'() => Math.abs({cy_expression}.zoom() - {zoom}) < 0.05')
    page.locator('#cp-graph-zoom-in').click()
    page.wait_for_function(f'() => {cy_expression}.zoom() > {zoom * 1.1}')
    page.locator('#cp-graph-zoom-out').click()
    page.wait_for_function(f'() => Math.abs({cy_expression}.zoom() - {zoom}) < 0.05')
    position = page.evaluate(f"() => {cy_expression}.nodes().filter(n => n.data('kind') === 'reaction')[0].renderedPosition()")
    canvas.click(position=position)
    expect(page.locator('#cp-step-detail')).to_be_visible()
    expect(page.locator('#cp-actual-structure')).to_have_count(0)
    page.wait_for_function(f"() => document.getElementById('cp-graph').clientWidth < {closed_width - 300}")
    page.wait_for_function(f"""() => {{const cy = {cy_expression};
        const b = cy.nodes().boundingBox({{includeLabels: false}}), e = cy.extent();
        return e.x1 <= b.x1 && e.x2 >= b.x2 && e.y1 <= b.y1 && e.y2 >= b.y2;
    }}""")
    screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
    if screenshots:
        Path(screenshots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshots) / 'candidate-fan-evidence-desktop.png'))
    page.locator('#cp-evidence > summary').click()
    page.wait_for_function(f"() => document.getElementById('cp-graph').clientWidth >= {closed_width - 1}")
    page.wait_for_function(f"() => {{const cy = {cy_expression}; return cy.width() >= {closed_width - 1} && cy.nodes()[0].renderedWidth() >= 80;}}")
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    if screenshots:
        page.screenshot(path=str(Path(screenshots) / 'candidate-fan-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.locator('#cp-graph-reset').click()
    expect(canvas).to_be_visible()


def test_candidate_species_search_explains_exact_empty_result(page):
    page.locator('#species-direct-route-btn').click()
    page.locator('#cp-mode').get_by_text('从起点看后续').click()
    page.locator('#cp-start-query').fill('C6H5ClO')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text(
        '当前候选路径索引中没有匹配“C6H5ClO”的精确结构'
    )
    page.locator('#cp-start').click()
    expect(page.get_by_text(
        '当前候选路径索引中没有匹配“C6H5ClO”的精确结构', exact=True,
    )).to_be_visible()


def test_candidate_single_anchor_forward_and_precursor_browsing(page):
    page.locator('#species-direct-route-btn').click()
    page.locator('#cp-mode').get_by_text('从终点查前驱').click()
    expect(page.locator('#cp-start-wrap')).not_to_be_visible()
    page.locator('#cp-target-query').fill('CC(=O)O')
    page.locator('#cp-target-find').click()
    expect(page.locator('#cp-target-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('候选路线', timeout=30000)
    page.locator('#cp-export-menu button.dropdown-toggle').click()
    with page.expect_download() as downloaded:
        page.locator('#cp-json').click()
    reverse = json.loads(Path(downloaded.value.path()).read_text())
    assert reverse['query']['mode'] == 'reverse'
    assert reverse['paths'][0]['species'] == ['CC=O', 'CC(=O)O']
    page.locator('#cp-continue').click()
    expect(page.locator('#cp-trail-view')).to_contain_text('已选浏览路径', timeout=30000)
    expect(page.locator('#cp-search-heading')).to_have_text(
        '搜索条件 · 候选前驱 → C2H4O（展开修改）', timeout=30000)
    expect(page.locator('#cp-graph-summary')).to_contain_text('已保留 1 个浏览步骤', timeout=30000)
    expect(page.locator('#cp-back')).to_be_enabled()
    assert page.locator('#cp-graph').evaluate("""element => {
        const host = [...element.querySelectorAll('*'), element].find(e => e._cyreg);
        return host._cyreg.cy.nodes().filter(n => n.data('historical') === true).length;
    }""") == 1
    page.locator('#cp-search-settings > summary').click()
    page.locator('#cp-mode').get_by_text('从起点看后续').click()
    expect(page.locator('#cp-target-wrap')).not_to_be_visible()
    page.locator('#cp-start-query').fill('CCO')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
    page.locator('#cp-limit').fill('1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-search-heading')).to_have_text(
        '搜索条件 · C2H6O → 后续物种（展开修改）', timeout=30000)
    expect(page.locator('#cp-page-next')).to_be_enabled(timeout=30000)
    page.locator('#cp-page-next').click()
    expect(page.locator('#cp-page-prev')).to_be_enabled(timeout=30000)
    page.locator('#cp-continue').click()
    expect(page.locator('#cp-trail-view')).to_contain_text('已选浏览路径', timeout=30000)
    expect(page.locator('#cp-graph-summary')).to_contain_text('已保留 1 个浏览步骤', timeout=30000)


def test_abundance_comparison_stays_in_page_and_returns_to_current_inputs(page, workbench):
    _, dataset = workbench
    SPECIES_COMPOSITION_STORE.build(dataset['artifacts']['species'])
    page.locator('#nav-evolution').click()
    page.locator('#evolution-trend-panel .accordion-button').first.click()
    page.locator('#evolution-targets').fill('CCO')
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
    page.locator('#evolution-browse-mode').click()
    page.get_by_text('按元素原子数分组', exact=True).click()
    expect(page.locator('#evolution-group-browser')).to_be_visible()
    expect(page.locator('#page-evolution')).to_be_visible()
    page.locator('#evolution-group-count').fill('2')
    expect(page.locator('#evolution-graph .scatterlayer .trace')).not_to_have_count(0, timeout=15000)
    page.locator('#evolution-distribution-tab').click()
    expect(page.locator('#page-element-distribution')).to_be_visible()
    expect(page.locator('#nav-evolution')).to_have_attribute('aria-current', 'page')


def test_pages_have_no_empty_top_band(page):
    def assert_compact_top(label):
        page.wait_for_timeout(500)
        measured = page.evaluate('''() => {
            const rect = el => {
                const r = el.getBoundingClientRect();
                return {top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height)};
            };
            const shown = el => {
                const r = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && style.visibility !== 'hidden'
                    && !el.classList.contains('visually-hidden');
            };
            const main = document.querySelector('.rs-main');
            const active = document.querySelector('.rs-page.active');
            const header = document.querySelector('#page-header');
            const status = document.querySelector('#page-data-status');
            const children = [...main.children].filter(shown);
            const beforePage = children.slice(0, children.indexOf(active));
            const pageChildren = [...active.children].filter(shown);
            return {
                main: rect(main),
                children: children.map(el => ({id: el.id || el.className, ...rect(el)})),
                beforePage: beforePage.map(el => ({id: el.id, ...rect(el)})),
                page: {id: active.id, ...rect(active)},
                firstPageChild: pageChildren.length
                    ? {id: pageChildren[0].id, className: pageChildren[0].className, ...rect(pageChildren[0])}
                    : null,
                status: {visible: shown(header), className: status.className, text: status.innerText},
            };
        }''')
        first = measured['children'][0]
        assert 0 <= first['top'] - measured['main']['top'] <= 26, (label, measured)
        previous = first
        for item in measured['children'][1:]:
            if item['id'] == measured['page']['id']:
                assert item['top'] - previous['bottom'] <= 24, (label, measured)
                break
            assert item['id'] in {'analysis-back-bar', 'page-header'}, (label, measured)
            assert item['top'] - previous['bottom'] <= 24, (label, measured)
            previous = item
        if measured['status']['visible']:
            assert 'is-blocked' in measured['status']['className'], (label, measured)
            assert measured['status']['text'].strip(), (label, measured)
        child = measured['firstPageChild']
        assert child and child['top'] - measured['page']['top'] <= 24, (label, measured)
        assert not (
            any(item['id'] == 'analysis-back-bar' for item in measured['beforePage'])
            and 'rs-analysis-back-bar' in child['className']
        ), (label, measured)

    assert_compact_top('species')
    for name in ('reactions', 'evolution', 'events', 'data-management', 'species'):
        page.locator(f'#nav-{name}').click()
        expect(page.locator(f'#page-{name}')).to_be_visible()
        assert_compact_top(name)
    query_species(page)
    page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"] [col-id="smiles"]').click()
    expect(page.locator('#species-detail-stage')).to_be_visible()
    assert_compact_top('species detail')
    page.locator('#species-to-channels-btn').click()
    expect(page.locator('#rxn-channel-view')).to_be_visible()
    expect(page.locator('#analysis-back-btn')).to_be_visible()
    assert_compact_top('channel')
    page.locator('#analysis-back-btn').click()
    expect(page.locator('#species-detail-stage')).to_be_visible()
    page.locator('#species-to-evolution-btn').click()
    expect(page.locator('#page-evolution.active')).to_be_visible()
    assert_compact_top('evolution focus')
    page.locator('#analysis-back-btn').click()
    expect(page.locator('#species-detail-stage')).to_be_visible()
    page.locator('#cp-from-species').click()
    expect(page.locator('#cp-path-panel')).to_be_visible()
    assert_compact_top('candidate')
    page.locator('#nav-reactions').click()
    expect(page.locator('#rxn-query-card')).to_be_visible()
    page.locator('#reaction-open-compare-btn').click()
    expect(page.locator('#page-reaction-compare.active')).to_be_visible()
    assert_compact_top('reaction compare')
    page.locator('#nav-evolution').click()
    expect(page.locator('#evolution-current-panel')).to_be_visible()
    page.locator('#evolution-browse-mode').click()
    page.get_by_text('按元素原子数分组', exact=True).click()
    page.locator('#evolution-distribution-tab').click()
    expect(page.locator('#page-element-distribution.active')).to_be_visible()
    assert_compact_top('element distribution')
    page.evaluate('window.dash_clientside.set_props("page-store", {data: {page: "trajectory", version: 2}})')
    expect(page.locator('#page-trajectory.active')).to_be_visible()
    assert_compact_top('trajectory')


def test_candidate_nonfirst_observation_to_product_tracking_and_return(page, workbench):
    folder = Path(workbench[1]['folder']) / 'observed-route'
    folder.mkdir(exist_ok=True)
    base = folder / 'observed'
    base.with_suffix('.reactionabcd').write_text('3 [C]+[O]->[C][O]\n')
    base.with_suffix('.species').write_text('Timestep 0: [C] 3 [O] 3\nTimestep 30: [C][O] 3\n')
    events = base.with_suffix('.reactionevent.csv')
    events.write_text('Timestep_Index,Reactant,Product\n' + ''.join(
        f'{i},[C]+[O],[C][O]\n' for i in range(3)))
    rows = ['Timestep,Species,AtomIDs,BondIDs\n']
    for frame in range(4):
        for pair in range(3):
            a, b = pair * 2, pair * 2 + 1
            if pair < frame:
                rows.append(f'{frame * 10},[C][O],{a};{b},{a}-{b}-1\n')
            else:
                rows.extend([f'{frame * 10},[C],{a},\n', f'{frame * 10},[O],{b},\n'])
    molecules = base.with_suffix('.molecules.csv')
    molecules.write_text(''.join(rows))
    EVENT_EVIDENCE_STORE.build(str(events), str(molecules))
    dataset = svc.validate_dataset_candidate(str(folder), str(base))
    dataset.update(context_state='active', ready=True)
    page.evaluate('dataset => window.dash_clientside.set_props("app-store", {data: dataset})', dataset)
    page.set_viewport_size({'width': 1600, 'height': 1000})
    page.locator('#species-direct-route-btn').click()
    page.locator('#cp-mode').get_by_text('从起点看后续').click()
    page.locator('#cp-start-query').fill('[C]')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('1 条候选路线', timeout=30000)
    page.locator('#cp-evidence > summary').click()
    expect(page.locator('#cp-events .ag-center-cols-container .ag-row')).to_have_count(3)
    expect(page.locator('#cp-open-events')).to_be_disabled()
    second = page.locator('#cp-events .ag-center-cols-container .ag-row[row-index="1"]')
    second.click()
    expect(page.locator('#cp-open-events')).to_be_enabled()
    page.locator('#cp-open-events').click()
    expect(page.locator('#event-detail-panel')).to_be_visible(timeout=15000)
    expect(page.locator('#event-detail-body')).to_contain_text('Transition 1')
    expect(page.locator('#event-detail-body')).to_contain_text('这次反应的 RNG 键变化')
    expect(page.locator('#event-detail-body')).to_contain_text('当前来源没有轨迹坐标')
    expect(page.locator('#event-detail-body .js-plotly-plot')).to_have_count(2)
    page.locator('#event-detail-body').get_by_role('button', name='追踪这个产物', exact=True).click()
    expect(page.locator('#page-trajectory')).to_have_class('rs-page active')
    expect(page.locator('#lx-instance')).to_contain_text('产物 1')
    expect(page.locator('#lx-card')).to_have_attribute('open', '')
    page.locator('#trajectory-back-events-btn').click()
    expect(page.locator('#cp-evidence')).to_be_visible()
    expect(page.locator('#cp-focus')).to_contain_text('路线 1')
    expect(second).to_have_attribute('aria-selected', 'true')
    # A subsequent query cannot reopen the previous query's event drawer.
    page.locator('#event-detail-close-btn').click()
    page.locator('#cp-search-settings > summary').click()
    page.locator('#cp-search').click()
    expect(page.locator('#cp-events .ag-row[aria-selected="true"]')).to_have_count(0)
    expect(page.locator('#cp-open-events')).to_be_disabled()
    expect(page.locator('#event-detail-panel')).not_to_be_visible()


def test_candidate_long_route_reading_overview_and_step_location(page, workbench):
    # A bounded network-only fixture isolates layout; no mechanistic claim.
    folder = Path(workbench[1]['folder']) / 'long-route'
    folder.mkdir(exist_ok=True)
    base = folder / 'long'
    species = ['C' * i for i in range(1, 12)]
    base.with_suffix('.reactionabcd').write_text(''.join(f'1 {a}->{b}\n' for a, b in zip(species, species[1:])))
    base.with_suffix('.species').write_text('Timestep 0: C 1\n')
    events = base.with_suffix('.reactionevent.csv')
    events.write_text('Timestep_Index,Reactant,Product\n' + ''.join(
        f'{i},{a},{b}\n' for i, (a, b) in enumerate(zip(species, species[1:]))))
    molecules = base.with_suffix('.molecules.csv')
    molecules.write_text('Timestep,Species,AtomIDs,BondIDs\n' + ''.join(f'{i},C,0,\n' for i in range(11)))
    EVENT_EVIDENCE_STORE.build(str(events), str(molecules))
    dataset = svc.validate_dataset_candidate(str(folder), str(base))
    dataset.update(context_state='active', ready=True)
    page.evaluate('dataset => window.dash_clientside.set_props("app-store", {data: dataset})', dataset)
    page.locator('#species-direct-route-btn').click()
    for side, query in [('start', species[0]), ('target', species[-1])]:
        page.locator(f'#cp-{side}-query').fill(query)
        page.locator(f'#cp-{side}-find').click()
        expect(page.locator(f'#cp-{side}-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-graph-summary')).to_contain_text('10 个反应步骤', timeout=30000)
    cy = "[...document.querySelectorAll('#cp-graph *'), document.getElementById('cp-graph')].find(e => e._cyreg)._cyreg.cy"
    page.wait_for_function(f'() => {cy}.zoom() >= 0.89')
    page.locator('#cp-step').click()
    page.locator('.dash-dropdown-options [role="option"]').filter(has_text='第 10 步').click()
    page.locator('#cp-graph-locate').click()
    page.wait_for_function(f"""() => {{const cy = {cy};
        const n = cy.nodes().filter(n => n.data('kind') === 'reaction' && n.data('members').some(m => m.step_index === 9))[0];
        const p = n.renderedPosition(); return Math.abs(p.x - cy.width()/2) < 5 && Math.abs(p.y - cy.height()/2) < 5;
    }}""")
    page.locator('#cp-graph-scope').get_by_text('返回路线总览').click()
    page.wait_for_function(f'() => {cy}.zoom() < 0.65')
    expect(page.locator('#cp-graph-view-note')).to_contain_text('总览仅定位分支')
    page.locator('#cp-graph-locate').click()
    page.wait_for_function(f'() => {cy}.zoom() >= 0.89')
    expect(page.locator('#cp-graph-view-note')).not_to_contain_text('总览仅定位分支')
    page.locator('#cp-graph-scope').get_by_text('路线阅读').click()
    page.set_viewport_size({'width': 390, 'height': 900})
    page.locator('#cp-graph-locate').click()
    page.wait_for_function(f'() => {cy}.zoom() >= 0.89')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
