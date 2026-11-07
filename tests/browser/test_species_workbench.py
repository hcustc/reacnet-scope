"""Species browser acceptance with isolated RNG samples."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect
from tests.browser.workbench import query_species, set_abundance_targets


@pytest.mark.browser_area('species')
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


@pytest.mark.browser_area('species')
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


@pytest.mark.browser_area('species', 'abundance')
def test_species_shortcuts_keep_context_and_compact_header(prepared_abundance_page, workbench):
    page = prepared_abundance_page
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

    def batch_shortcut_after_derived_controls(route):
        payload = route.request.post_data_json or {}
        changed = payload.get('changedPropIds') or []
        if ('evolution-graph.figure' in str(payload.get('output') or '')
                and 'species-to-evolution-btn.n_clicks' in changed):
            # Dash can batch the button after the controls it updates. Make
            # that order deterministic instead of relying on request timing.
            payload['changedPropIds'] = ['evolution-group-element.value', *[
                value for value in changed if value != 'evolution-group-element.value'
            ]]
            route.continue_(post_data=json.dumps(payload))
        else:
            route.continue_()

    page.route('**/_dash-update-component', batch_shortcut_after_derived_controls)
    page.locator('#species-to-evolution-btn').click()
    expect(page.locator('#page-evolution.active')).to_be_visible()
    expect(page.locator('#evolution-targets')).to_have_value(f'smiles:{species}')
    expect(page.locator('#evolution-target-preview')).to_be_visible()
    expect(page.locator('#evolution-query-run')).to_be_visible()
    assert page.locator('#evolution-target-entry').evaluate(
        'element => element.closest("#evolution-rank-panel") !== null'
    )
    expect(page.locator('#evolution-graph .scatterlayer .trace')).not_to_have_count(0)
    expect(page.locator('#evolution-focus')).to_be_visible()
    expect(page.locator('#evolution-focus')).to_contain_text(species)
    expect(page.locator('#evolution-species-browser')).to_be_visible()
    expect(page.locator('#evolution-browse-controls')).to_be_visible()
    expect(page.locator('#evolution-browse-mode')).not_to_be_visible()
    expect(page.locator('#evolution-rank-grid')).not_to_be_visible()
    expect(page.locator('#evolution-back-btn')).to_be_visible()
    expect(page.locator('#analysis-back-bar')).not_to_be_visible()
    if screenshots := os.environ.get('REACNET_SCOPE_SCREENSHOTS'):
        folder = Path(screenshots)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / 'abundance-preselected.png'))
    page.locator('#evolution-open-finder').click()
    page.locator('#evolution-filter-query').fill('C2O')
    page.locator('#evolution-filter-search').click()
    expect(page.locator('#evolution-filter-grid .ag-center-cols-container .ag-row')).to_have_count(3)
    page.locator('#evolution-filter-grid .ag-center-cols-container .ag-row').filter(
        has=page.get_by_text('COC', exact=True),
    ).locator('[col-id="smiles"]').click()
    page.locator('#evolution-filter-replace').click()
    expect(page.locator('#evolution-focus')).to_contain_text('COC')
    expect(page.locator('#evolution-targets')).to_have_value('smiles:COC')
    expect(page.locator('#evolution-target-settings')).to_be_visible()
    set_abundance_targets(page, 'smiles:CCO')
    page.locator('#evolution-query-run').click()
    expect(page.locator('#evolution-focus')).to_contain_text('CCO')
    page.locator('#evolution-open-settings').click()
    page.locator('#evolution-start-timestep').fill('10')
    page.locator('#evolution-end-timestep').fill('20')
    page.locator('#evolution-settings-apply').click()
    page.wait_for_function('document.querySelector("#evolution-graph .js-plotly-plot")?.data?.[0]?.x?.join() === "10,20"')

    page.locator('#evolution-back-btn').click()
    expect(page.locator('#species-production-btn')).to_have_count(0)
    page.locator('#cp-from-species').click()
    expect(page.locator('#cp-path-panel')).to_be_visible()
    expect(page.locator('#cp-start')).to_contain_text(species)
    page.locator('#cp-mode').get_by_text('起点到终点').click()
    expect(page.locator('#cp-start')).to_contain_text(species)


@pytest.mark.browser_area('species', 'reactions')
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


@pytest.mark.browser_area('species')
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


@pytest.mark.browser_area('species')
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
