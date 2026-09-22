"""Opt-in real browser acceptance, using only isolated small RNG fixtures.

REACNET_SCOPE_BROWSER_TESTS=1 uv run --locked --group browser pytest -q tests/browser
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from threading import Thread

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from reacnet_scope import services as svc
from reacnet_scope import dir_browser
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts.webapp_dash.app import create_app


@pytest.fixture(scope="module")
def workbench(tmp_path_factory):
    root = tmp_path_factory.mktemp("browser-workbench")
    with pytest.MonkeyPatch.context() as patch:
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
        EVENT_EVIDENCE_STORE.build(str(events))
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
                             'sessionStorage.setItem("page-store",JSON.stringify({page:"species"}));')
        try:
            page.goto(url)
            expect(page.locator("#species-query")).to_be_visible(timeout=30000)
            expect(page.locator("#species-search-btn")).to_be_enabled(timeout=15000)
            expect(page).to_have_title("ReacNet Scope (Dash)")
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
    expect(page.locator("#species-detail-close")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#species-detail-close")).not_to_be_visible()
    with page.expect_download() as downloaded:
        page.locator("#species-csv-btn").click()
    assert "COC" in Path(downloaded.value.path()).read_text(encoding="utf-8-sig")
    page.locator("#species-query").fill("39.99491462")
    expect(page.locator("#species-mass-field")).to_be_visible()
    expect(page.locator("#species-query-feedback")).to_contain_text("条件已修改")
    page.set_viewport_size({"width": 760, "height": 900})
    expect(page.locator("#topbar-rungroup")).to_be_visible()
    dataset_bounds = page.locator("#topbar-rungroup").bounding_box()
    topbar_bounds = page.locator(".rs-topbar").bounding_box()
    assert dataset_bounds["y"] + dataset_bounds["height"] <= topbar_bounds["y"] + topbar_bounds["height"]
    first.locator('[col-id="smiles"]').click()
    expect(page.locator("#species-detail-close")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.keyboard.press("Escape")
    expect(page.locator("#species-detail-close")).not_to_be_visible()


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


def test_five_workspaces_and_reaction_query_states(page):
    expect(page.locator("#data-pick-btn")).to_be_visible()
    page.locator("#nav-reactions").click()
    page.locator("#workspace-task-nav").get_by_role("button", name="候选路径").click()
    expect(page.locator("#cp-start-query")).to_be_visible()
    page.locator("#workspace-task-nav").get_by_role("button", name="具体事件").click()
    expect(page.locator("#page-events")).to_be_visible()
    page.locator("#workspace-task-nav").get_by_role("button", name="直接通道与反应式").click()
    expect(page.locator("#rxn-reactants")).to_be_visible()
    page.locator("#rxn-reactants").fill("C2O")
    page.locator("#rxn-reactants").press("Enter")
    expect(page.locator("#rxn-alert")).to_contain_text("找到", timeout=15000)
    page.locator("#rxn-products").fill("O")
    expect(page.locator("#rxn-query-feedback")).to_contain_text("条件已修改")
    page.locator("#rxn-products").press("Enter")
    expect(page.locator("#rxn-query-feedback")).to_contain_text("没有匹配反应", timeout=15000)
    for nav, target in [("nav-trajectory", "page-trajectory"),
                        ("nav-data-management", "page-data-management"),
                        ("nav-species", "page-species")]:
        page.locator("#" + nav).click()
        expect(page.locator("#" + target)).to_be_visible()
        if target == "page-data-management":
            expect(page.locator("#page-title")).to_be_visible()

        if os.environ.get("REACNET_SCOPE_SCREENSHOTS"):
            page.screenshot(path=str(Path(os.environ["REACNET_SCOPE_SCREENSHOTS"]) / f"{target}.png"))


def test_first_viewport_and_zero_mass_tolerance(page):
    query_species(page, query="C4O", count=10)
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
    expect(page.locator('#library-tasks-panel')).not_to_be_visible()
    expect(page.locator('#workspace-task-nav')).not_to_be_visible()
    expect(page.locator('#page-title')).to_have_text('RNG 数据')
    page.locator('#library-view').get_by_text('当前数据与准备任务', exact=True).click()
    expect(page.locator('#library-tasks-panel')).to_be_visible()
    page.locator('#nav-data-management').click()
    expect(page.locator('#library-management-panel')).to_be_visible()
    page.locator('#library-add-more').click()
    page.get_by_text('一次填写多个文件夹路径', exact=True).click()
    page.locator('#library-paths').fill('\n'.join([*paths, str(root / 'missing')]))
    page.locator('#library-add-paths').click()
    expect(page.locator('#library-draft-list .rs-library-item')).to_have_count(3)
    page.locator('#library-import').click()
    expect(page.locator('#library-import-status')).to_contain_text('已导入 2 个文件夹；1 个未导入', timeout=30000)
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == dataset['base']
    page.locator('#nav-species').click()
    for path in paths:
        page.locator('#library-select').click()
        page.get_by_text(f'{Path(path).name} · {path}', exact=True).click()
        page.locator('#library-use').click()
        expect(page.locator('.rs-topbar .rs-meta')).to_contain_text(Path(path).name, timeout=20000)
        expect(page.locator('#page-species')).to_be_visible()
    page.locator('#nav-data-management').click()
    expect(page.locator('#library-management-list')).to_contain_text('import-A')
    expect(page.locator('#library-management-list')).to_contain_text('import-B')
    screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
    if screenshots:
        Path(screenshots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshots) / 'dataset-library.png'))
    for path in paths:
        row = page.locator('#library-management-list .rs-library-item').filter(has_text=path)
        row.get_by_role('button', name='使用此RNG 数据').click()
        expect(page.locator('#topbar-rungroup')).to_have_text(Path(path).name, timeout=20000)
        expect(page.locator('#library-management-panel')).to_be_visible()
    expect(page.locator('#library-compare-selection')).to_have_count(0)
    expect(page.locator('#data-open-batch-compare-btn')).not_to_be_visible()
    page.locator('#nav-species').click()
    page.locator('#workspace-task-nav').get_by_role('button', name='多来源对比').click()
    expect(page.locator('#page-batch-compare')).to_be_visible()
    expect(page.locator('#nav-species')).to_have_attribute('aria-current', 'page')
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


def test_reaction_comparison_is_an_analysis_task(page):
    page.locator('#nav-reactions').click()
    page.locator('#workspace-task-nav').get_by_role('button', name='多来源对比').click()
    expect(page.locator('#page-reaction-compare')).to_be_visible()
    expect(page.locator('#batch-managed-selector')).to_be_visible()
    expect(page.locator('#species-compare-managed')).not_to_be_visible()
    expect(page.locator('#nav-reactions')).to_have_attribute('aria-current', 'page')
    page.locator('#workspace-task-nav').get_by_role('button', name='直接通道与反应式').click()
    expect(page.locator('#page-reactions')).to_be_visible()


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
