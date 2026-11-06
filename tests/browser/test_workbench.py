"""Navigation browser acceptance with isolated RNG samples."""
from __future__ import annotations

import os

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect
from tests.browser.workbench import query_species, reload_workbench


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
    reload_workbench(page)
    expect(page.locator("#page-reactions")).to_be_visible(timeout=30000)
    expect(page.locator("#nav-reactions")).to_have_attribute("aria-current", "page")
    expect(page.locator("#topbar-rungroup")).to_have_text(dataset["label"], timeout=30000)


@pytest.mark.browser_smoke
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
    page.locator('#evolution-back-btn').click()
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
    page.locator('#evolution-open-settings').click()
    page.locator('#evolution-settings-section > summary').click()
    page.locator('#evolution-distribution-tab').click()
    expect(page.locator('#page-element-distribution.active')).to_be_visible()
    assert_compact_top('element distribution')
    page.evaluate('window.dash_clientside.set_props("page-store", {data: {page: "trajectory", version: 2}})')
    expect(page.locator('#page-trajectory.active')).to_be_visible()
    assert_compact_top('trajectory')
