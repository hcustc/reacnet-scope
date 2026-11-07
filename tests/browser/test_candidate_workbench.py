"""Candidate browser acceptance with isolated RNG samples."""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect
from reacnet_scope import services as svc
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from tests.browser.workbench import query_species, reload_workbench


@pytest.mark.browser_area('species', 'candidate')
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
    expect(page.locator('#cp-start-message')).to_contain_text(
        '已带入当前选中的精确物种', timeout=15000,
    )
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


@pytest.mark.browser_area('candidate')
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


@pytest.mark.browser_area('candidate')
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


@pytest.mark.browser_area('candidate')
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
    reload_workbench(page)
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


@pytest.mark.browser_area('candidate')
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


@pytest.mark.browser_area('candidate')
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


@pytest.mark.browser_area('candidate')
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


@pytest.mark.browser_area('candidate')
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


@pytest.mark.browser_area('events', 'candidate')
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


@pytest.mark.browser_area('candidate')
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
    page.locator('#species-direct-route-btn').click()
    # Direct fixture injection skips the normal switch transaction. Wait for
    # its picker reset before typing into the newly activated dataset.
    with page.expect_response(lambda response: response.request.post_data is not None
                              and 'cp-start.options' in response.request.post_data_json.get('output', '')
                              and 'cp-context.data' in response.request.post_data_json.get('changedPropIds', [])) as reset:
        page.evaluate('dataset => window.dash_clientside.set_props("app-store", {data: dataset})', dataset)
    reset.value.finished()
    expect(page.locator('#topbar-rungroup')).to_have_text(dataset['label'])
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


@pytest.mark.browser_area("candidate")
def test_candidate_waypoints_and_exclusions(page):
    page.locator('#species-direct-route-btn').click()
    for side, species in [('start', 'CCO'), ('target', 'CC(=O)O')]:
        page.locator(f'#cp-{side}-query').fill(species)
        page.locator(f'#cp-{side}-find').click()
        expect(page.locator(f'#cp-{side}-message')).to_contain_text('匹配 1')
    page.locator('#cp-constraints > summary').click()
    for side, species in [('via', 'COC'), ('via', 'CC=O'), ('excluded', 'O'), ('excluded', '[H][H]')]:
        page.locator(f'#cp-{side}-query').fill(species)
        page.locator(f'#cp-{side}-find').click()
        expect(page.locator(f'#cp-{side}')).to_contain_text(species)
    screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
    if screenshots:
        folder = Path(screenshots)
        folder.mkdir(parents=True, exist_ok=True)
        page.locator('#cp-constraints').screenshot(path=str(folder / 'candidate-constraints-desktop.png'))
    page.set_viewport_size({'width': 390, 'height': 900})
    expect(page.locator('#cp-via')).to_be_visible()
    bounds = page.evaluate('({viewport: innerWidth, width: document.documentElement.scrollWidth})')
    assert bounds['width'] <= bounds['viewport'] + 1, bounds
    if screenshots:
        page.locator('#cp-constraints').screenshot(path=str(folder / 'candidate-constraints-mobile.png'))
    page.set_viewport_size({'width': 1366, 'height': 768})
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('1 条候选路线', timeout=30000)
    expect(page.locator('#cp-applied-constraints')).to_contain_text('COC → CC=O')
    expect(page.locator('#cp-applied-constraints')).to_contain_text('排除主线物种')
    page.locator('#cp-export-menu button.dropdown-toggle').click()
    with page.expect_download() as downloaded:
        page.locator('#cp-json').click()
    report = json.loads(Path(downloaded.value.path()).read_text())
    assert [p['species'] for p in report['paths']] == [['CCO', 'COC', 'CC=O', 'CC(=O)O']]
    assert report['query']['via_species'] == ['COC', 'CC=O']
    assert report['query']['excluded_species'] == ['O', '[H][H]']
    page.locator('#cp-search-settings > summary').click()
    page.locator('#cp-excluded-query').fill('COC')
    page.locator('#cp-excluded-find').click()
    expect(page.locator('#cp-excluded')).to_contain_text('COC')
    # Unsubmitted edits leave the currently applied query visible.
    expect(page.locator('#cp-applied-constraints')).not_to_contain_text('排除主线物种：COC')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('不能同时被排除', timeout=30000)
    expect(page.locator('#cp-results')).not_to_be_visible()


@pytest.mark.browser_area("candidate")
def test_candidate_graph_downloads_offline(page):
    page.locator('#species-direct-route-btn').click()
    for side, species in [('start', 'CCO'), ('target', 'CC(=O)O')]:
        page.locator(f'#cp-{side}-query').fill(species)
        page.locator(f'#cp-{side}-find').click()
        expect(page.locator(f'#cp-{side}-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('3 条候选路线', timeout=30000)
    expect(page.locator('#cp-graph-summary')).to_contain_text('1 条路线')
    selected = None
    for trigger in ('cp-hypergraph-json', 'cp-svg'):
        page.locator('#cp-export-menu button.dropdown-toggle').click()
        with page.expect_download() as graph_download:
            page.locator('#' + trigger).click()
        content = Path(graph_download.value.path()).read_text()
        if trigger == 'cp-svg':
            root = ET.fromstring(content)
            graph_content = root.find('{http://www.w3.org/2000/svg}metadata').text
            # The standalone file needs no app structure endpoint or CDN.
            preview_context = page.context.browser.new_context()
            preview = preview_context.new_page()
            external = []
            preview.on('request', lambda req: external.append(req.url) if req.url.startswith('http') else None)
            try:
                preview.set_content(content)
                expect(preview.locator('svg image').first).to_be_visible()
                expect(preview.locator('svg')).to_contain_text('净')
                screenshots = os.environ.get('REACNET_SCOPE_SCREENSHOTS')
                if screenshots:
                    folder = Path(screenshots)
                    folder.mkdir(parents=True, exist_ok=True)
                    preview.screenshot(path=str(folder / 'candidate-network-export.png'), full_page=True)
                assert external == []
            finally:
                preview_context.close()
        else:
            graph_content = content
        exported = json.loads(graph_content)
        assert len(exported['routes']) == 1
        assert len(exported['hyperedges']) == 2
        assert exported['provenance']['query']['start'] == 'CCO'
        assert exported['provenance']['source_revision']
        if selected is None:
            selected = exported
        else:
            assert exported == selected
    page.locator('#cp-graph-scope').get_by_text('返回路线总览').click()
    expect(page.locator('#cp-graph-summary')).to_contain_text('3 条路线')
    page.locator('#cp-export-menu button.dropdown-toggle').click()
    with page.expect_download() as graph_download:
        page.locator('#cp-hypergraph-json').click()
    overview = json.loads(Path(graph_download.value.path()).read_text())
    assert len(overview['routes']) == 3
    assert overview['provenance']['source_revision'] == selected['provenance']['source_revision']
    assert {v['species'] for v in overview['species']} == {'CCO', 'COC', 'CC=O', 'CC(=O)O', 'O', '[H][H]'}


@pytest.mark.browser_area("candidate")
def test_candidate_history_rebuild_requires_new_search(page, workbench):
    _, dataset = workbench
    artifacts = dataset['artifacts']
    source = Path(artifacts['reactionevent'])
    original = source.read_text()
    page.locator('#species-direct-route-btn').click()
    page.locator('#cp-mode').get_by_text('从起点看后续', exact=True).click()
    page.locator('#cp-start-query').fill('CCO')
    page.locator('#cp-start-find').click()
    expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
    page.locator('#cp-search').click()
    expect(page.locator('#cp-result-summary')).to_contain_text('候选路线', timeout=30000)
    expect(page.locator('#cp-continue')).to_be_enabled()
    page.locator('#cp-continue').click()
    expect(page.locator('#cp-trail-view')).to_contain_text('已选浏览路径')
    expect(page.locator('#cp-continue')).to_be_enabled(timeout=30000)
    page.locator('#cp-export-menu button.dropdown-toggle').click()
    with page.expect_download() as downloaded:
        page.locator('#cp-json').click()
    current = json.loads(Path(downloaded.value.path()).read_text())
    start = current['paths'][0]['species'][-1]
    try:
        source.write_text(f'Timestep_Index,Reactant,Product\n0,{start},CCO\n')
        EVENT_EVIDENCE_STORE.build(str(source), artifacts.get('molecules', ''))
        page.locator('#cp-continue').click()
        expect(page.locator('#cp-result-summary')).to_contain_text('来源版本已变化', timeout=30000)
        expect(page.locator('#cp-results')).not_to_be_visible()
        page.locator('#cp-start-query').fill(start)
        page.locator('#cp-start-find').click()
        expect(page.locator('#cp-start-message')).to_contain_text('匹配 1')
        page.locator('#cp-search').click()
        expect(page.locator('#cp-result-summary')).to_contain_text('1 条候选路线', timeout=30000)
        expect(page.locator('#cp-graph-summary')).not_to_contain_text('浏览步骤')
        page.locator('#cp-export-menu button.dropdown-toggle').click()
        with page.expect_download() as downloaded:
            page.locator('#cp-hypergraph-json').click()
        graph = json.loads(Path(downloaded.value.path()).read_text())
        assert [reaction['reaction_key'] for reaction in graph['hyperedges']] == [f'{start}->CCO']
        assert graph['browse_history_step_count'] == 0
        assert graph['provenance']['source_revision'] != current['source_revision']
    finally:
        source.write_text(original)
        EVENT_EVIDENCE_STORE.build(str(source), artifacts.get('molecules', ''))
