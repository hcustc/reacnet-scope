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


@pytest.fixture(scope="module")
def workbench(tmp_path_factory):
    root = tmp_path_factory.mktemp("browser-workbench")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("REACNET_SCOPE_COMPACT_NAV", "0")
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
                             'if (!sessionStorage.getItem("page-store")) {'
                             'sessionStorage.setItem("page-store",JSON.stringify({page:"species"}));}')
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


def test_navigation_preserves_dataset_and_restores_page(page, workbench):
    _, dataset = workbench
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])
    for target, label in [
        ("reactions", "反应路径"),
        ("trajectory", "证据核查"),
        ("batch-compare", "物种趋势"),
        ("data-management", "RNG 数据"),
        ("species", "物种发现"),
    ]:
        page.locator(f"#nav-{target}").click()
        expect(page.locator(f"#page-{target}")).to_be_visible()
        expect(page.locator(f"#nav-{target}")).to_have_attribute("aria-current", "page")
        expect(page.locator("#page-title")).to_have_text(label)
        expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])
    page.locator("#data-pick-btn").click()
    expect(page.locator("#import-path")).to_be_visible()
    page.locator("#dir-browser-cancel-btn").click()
    expect(page.locator("#page-species")).to_be_visible()
    assert page.evaluate('JSON.parse(sessionStorage.getItem("dataset-session-store")).base') == dataset["base"]
    page.locator("#nav-reactions").click()
    expect(page.locator("#page-title")).to_have_text("反应路径")
    page.wait_for_function('JSON.parse(sessionStorage.getItem("page-store")).page === "reactions"')
    page.reload()
    expect(page.locator("#page-reactions")).to_be_visible(timeout=30000)
    expect(page.locator("#nav-reactions")).to_have_attribute("aria-current", "page")
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])


def test_trend_workspace_opens_single_source_evolution(page, workbench):
    _, dataset = workbench
    page.locator("#nav-data-management").click()
    card = page.locator("#data-overview-actions .rs-workflow-card").filter(has_text="物种趋势")
    expect(card).to_be_visible()
    expect(card.locator(".rs-capability-state")).to_have_text("需准备索引")
    expect(card).not_to_contain_text("状态待检查")
    card.get_by_role("button", name="进入工作区").click()
    expect(page.locator("#page-title")).to_have_text("物种趋势")
    tasks = page.locator("#workspace-task-nav")
    expect(tasks.get_by_role("button", name="多来源对比", exact=True)).to_be_visible()
    tasks.get_by_role("button", name="时间演化", exact=True).click()
    expect(page.locator("#page-evolution")).to_be_visible()
    expect(page.locator("#nav-batch-compare")).to_have_attribute("aria-current", "page")
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"])


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
            expect(workspace.locator('#library-management-panel h3')).to_have_text('RNG 数据管理')
            expect(workspace.get_by_role('button', name='添加数据', exact=True)).to_have_count(1)
            page.set_viewport_size({'width': 1800, 'height': 900})
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


@pytest.mark.parametrize(
    ("shortcut", "mode", "side"),
    [("cp-from-species", "explore", "start"),
     ("cp-to-species", "reverse", "target")],
)
def test_selected_species_opens_candidate_with_exact_anchor(page, shortcut, mode, side):
    query_species(page)
    first = page.locator('#species-grid .ag-center-cols-container .ag-row[row-index="0"]')
    species = first.locator('[col-id="smiles"]').inner_text()
    first.locator('[col-id="smiles"]').click()
    expect(page.locator('#detail-body')).to_contain_text(species)
    menu = page.locator('#species-candidate-menu .dropdown-toggle')
    expect(menu).to_be_enabled()
    menu.click()
    page.locator(f'#{shortcut}').click()
    expect(page.locator('#page-reactions.active')).to_be_visible()
    expect(page.locator('#cp-path-panel')).to_be_visible()
    expect(page.locator(f'#cp-{side}-message')).to_contain_text('已带入当前选中的精确物种')
    expect(page.locator(f'#cp-{side}')).to_contain_text(species)
    assert page.locator('#cp-mode input:checked').input_value() == mode


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


def test_five_workspaces_and_reaction_query_states(page):
    expect(page.locator("#data-pick-btn")).to_be_visible()
    page.locator("#nav-reactions").click()
    page.locator("#workspace-task-nav").get_by_role("button", name="候选路径").click()
    expect(page.locator("#cp-start-query")).to_be_visible()
    page.locator("#workspace-task-nav").get_by_role("button", name="具体事件").click()
    expect(page.locator("#page-events")).to_be_visible()
    page.locator("#workspace-task-nav").get_by_role("button", name="反应路径").click()
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


def test_event_query_without_time_conversion_keeps_ps_cells_blank(page):
    page.locator("#nav-reactions").click()
    page.locator("#workspace-task-nav").get_by_role("button", name="具体事件").click()
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
    page.locator('#nav-batch-compare').click()
    expect(page.locator('#page-batch-compare')).to_be_visible()
    expect(page.locator('#nav-batch-compare')).to_have_attribute('aria-current', 'page')
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
            page.evaluate("sessionStorage.setItem('page-store', JSON.stringify({page:'batch-compare'}))")
            page.reload()
            expect(page.locator('#page-batch-compare')).to_be_visible(timeout=20000)
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
    page.locator('#workspace-task-nav').get_by_role('button', name='反应对比').click()
    expect(page.locator('#page-reaction-compare')).to_be_visible()
    expect(page.locator('#batch-managed-selector')).to_be_visible()
    expect(page.locator('#species-compare-managed')).not_to_be_visible()
    expect(page.locator('#nav-reactions')).to_have_attribute('aria-current', 'page')
    page.locator('#workspace-task-nav').get_by_role('button', name='反应路径').click()
    expect(page.locator('#page-reactions')).to_be_visible()


def test_species_comparison_searches_large_catalog_without_loading_all_options(page, workbench):
    _, current = workbench
    species = Path(current['folder']) / 'many.species'
    species.write_text(
        'Timestep 0: ' + ' '.join(f"{'C' * size} 1" for size in range(1, 121)) + '\n'
    )
    SPECIES_COMPOSITION_STORE.build(str(species))

    page.locator('#nav-batch-compare').click()
    page.get_by_text('未在列表中？手工添加 Species 文件').click()
    page.locator('#species-compare-path').fill(str(species))
    page.locator('#species-compare-add-path').click()
    card = page.locator('#species-compare-sources .rs-compare-source-card')
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


def test_candidate_search_focus_and_progressive_evidence(page):
    page.locator('#nav-reactions').click()
    page.locator('#workspace-task-nav').get_by_role('button', name='候选路径').click()
    expect(page.locator('#cp-results')).not_to_be_visible()
    expect(page.locator('#cp-return-window')).not_to_be_visible()
    expect(page.locator('#cp-advanced-heading')).to_contain_text('3 个分析帧间隔')
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
    page.locator('#cp-graph-scope').get_by_text('全部返回路线').click()
    expect(page.locator('#cp-graph-summary')).to_contain_text('3 条路线')
    page.locator('#cp-graph-scope').get_by_text('当前与勾选路线').click()
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
    expect(page.locator('#cp-events')).not_to_be_visible()
    page.locator('.rs-candidate-event-list > summary').click()
    expect(page.locator('#cp-events')).to_be_visible()
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


def test_candidate_single_anchor_forward_and_precursor_browsing(page):
    page.locator('#nav-reactions').click()
    page.locator('#workspace-task-nav').get_by_role('button', name='候选路径').click()
    page.locator('#cp-mode').get_by_text('从终点查前驱').click()
    expect(page.locator('#cp-start-wrap')).not_to_be_visible()
    page.locator('#cp-target-query').fill('CC(=O)O')
    page.locator('#cp-target-find').click()
    expect(page.locator('#cp-target-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('候选路线', timeout=30000)
    with page.expect_download() as downloaded:
        page.locator('#cp-json').click()
    reverse = json.loads(Path(downloaded.value.path()).read_text())
    assert reverse['query']['mode'] == 'reverse'
    assert reverse['paths'][0]['species'] == ['CC=O', 'CC(=O)O']
    page.locator('#cp-continue').click()
    expect(page.locator('#cp-trail-view')).to_contain_text('已浏览物种', timeout=30000)
    expect(page.locator('#cp-search-heading')).to_have_text(
        '搜索条件 · 候选前驱 → C2H4O（展开修改）', timeout=30000)
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
    expect(page.locator('#cp-trail-view')).to_contain_text('已浏览物种', timeout=30000)
