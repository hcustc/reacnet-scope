"""Reactions browser acceptance with isolated RNG samples."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect


@pytest.mark.browser_smoke
@pytest.mark.browser_area('reactions')
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


@pytest.mark.browser_area('reactions')
def test_reaction_comparison_is_an_analysis_task(page):
    page.locator('#nav-reactions').click()
    page.locator('#reaction-open-compare-btn').click()
    expect(page.locator('#page-reaction-compare')).to_be_visible()
    expect(page.locator('#batch-managed-selector')).to_be_visible()
    expect(page.locator('#species-compare-managed')).not_to_be_visible()
    expect(page.locator('#nav-reactions')).to_have_attribute('aria-current', 'page')
    page.locator('#reaction-compare-back-btn').click()
    expect(page.locator('#page-reactions')).to_be_visible()
