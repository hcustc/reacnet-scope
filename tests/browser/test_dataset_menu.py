"""Switch-menu feedback stays responsive while server work is outstanding."""
from __future__ import annotations

import os
import time

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect

@pytest.mark.browser_area('dataset')
def test_switch_menu_selection_does_not_wait_for_server_feedback(page, workbench):
    _, current = workbench
    other = {"folder": current["folder"], "base": current["base"] + "-other",
             "dataset_id": "other", "label": "另一个来源"}
    held = []

    def hold_feedback(route):
        payload = route.request.post_data_json or {}
        if "library-use.disabled" in str(payload.get("output") or ""):
            held.append(route)
        else:
            route.continue_()

    page.route("**/_dash-update-component", hold_feedback)
    try:
        page.evaluate("data => window.dash_clientside.set_props('dataset-browser-candidate', {data})", other)
        page.evaluate("entry => window.dash_clientside.set_props('dataset-library', {data: [entry]})", other)
        page.locator("#current-dataset-menu > summary").click()
        page.locator("#library-select").click()
        page.get_by_text(f"另一个来源 · {current['folder']}", exact=True).click()
        expect(page.locator("#library-use")).to_be_enabled(timeout=1500)
        expect(page.locator("#topbar-rungroup")).to_have_text(current["label"])

        page.evaluate("window.dash_clientside.set_props({type: 'dataset-bound-operation', name: 'species'}, {data: true})")
        expect(page.locator("#library-use")).to_be_disabled(timeout=1500)
        expect(page.locator("#library-switch-status")).to_contain_text("等待当前分析完成", timeout=1500)
        page.evaluate("window.dash_clientside.set_props({type: 'dataset-bound-operation', name: 'species'}, {data: false})")
        expect(page.locator("#library-use")).to_be_enabled(timeout=1500)

        request = {"state": "validating", "request_id": "menu-request",
                   "origin": {"library": True, "page": "species"},
                   "candidate": other, "deadline_ns": time.time_ns() + 60_000_000_000}
        page.evaluate("data => window.dash_clientside.set_props('dataset-switch-transaction', {data})", request)
        expect(page.locator("#library-switch-status")).to_contain_text("正在切换", timeout=1500)
        page.evaluate("window.dash_clientside.set_props('dataset-browser-candidate', {data: null})")
        page.evaluate("window.dash_clientside.set_props('page-store', {data: {page: 'species', version: 2}})")
        expect(page.locator("#library-use")).to_be_disabled(timeout=1500)
        expect(page.locator("#library-switch-status")).to_contain_text("正在切换", timeout=1500)

        page.evaluate("window.dash_clientside.set_props('page-store', {data: {page: 'events', version: 2}})")
        # Cancellation and its feedback use two local callback turns. The held
        # server response still makes the old implementation fail indefinitely.
        expect(page.locator("#library-switch-status")).not_to_contain_text("正在切换", timeout=5000)
        expect(page.locator("#library-use")).to_be_enabled(timeout=5000)
        with page.expect_response(lambda response: response.request.post_data is not None
                                  and response.request.post_data_json.get("changedPropIds") == ["dataset-switch-validation.data"]
                                  and "dataset-switch-transaction.data" in response.request.post_data_json.get("output", "")) as late:
            page.evaluate("data => window.dash_clientside.set_props('dataset-switch-validation', {data})",
                          {"request_id": "menu-request", "ok": True,
                           "validation": {"dataset_id": "stale", "label": "旧结果"},
                           "completed_ns": time.time_ns()})
        assert late.value.status == 204
        expect(page.locator("#topbar-rungroup")).to_have_text(current["label"])
    finally:
        for route in held:
            route.fulfill(status=204)
        page.unroute("**/_dash-update-component", hold_feedback)
