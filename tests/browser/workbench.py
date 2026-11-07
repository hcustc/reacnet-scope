"""Isolated small RNG fixtures and browser readiness helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from reacnet_scope import dir_browser
from reacnet_scope import services as svc
from reacnet_scope.composition import SPECIES_COMPOSITION_STORE
from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
from scripts.webapp_dash.app import create_app


def wait_for_initial_callbacks(page):
    # Session restoration can finish after the first visible frame.
    page.wait_for_function("""() => {
        const callbacks = window.store?.getState().callbacks;
        return callbacks && ['requested', 'prioritized', 'executing', 'watched', 'blocked']
            .every(key => callbacks[key].length === 0);
    }""", timeout=60000)


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
        # Server-driven views can finish after the first visible page frame.
        # Match the query timeout; immediate-feedback tests keep explicit limits.
        expect.set_options(timeout=15000)
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
            wait_for_initial_callbacks(page)
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


@pytest.fixture
def prepared_abundance_page(workbench, request):
    url, dataset = workbench
    SPECIES_COMPOSITION_STORE.build(dataset['artifacts']['species'])
    prepared = svc.validate_dataset_candidate(dataset['folder'], dataset['base'])
    prepared.update(context_state='active', ready=True)
    yield from page.__wrapped__((url, prepared), request)


def set_abundance_targets(page, value):
    page.locator('#evolution-batch-open').click()
    page.locator('#evolution-batch-text').fill(value)
    page.locator('#evolution-batch-save').click()
    expect(page.locator('#evolution-batch-modal')).not_to_be_visible()


def query_species(page, query="C2O", count=3):
    page.locator("#species-query").fill(query)
    page.locator("#species-query").press("Enter")
    expect(page.locator("#species-grid .ag-center-cols-container .ag-row")).to_have_count(count, timeout=15000)


def reload_workbench(page):
    """Wait for session restoration and picker resets before interacting."""
    page.reload()
    wait_for_initial_callbacks(page)
