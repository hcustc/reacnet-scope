"""Lewis annotation preparation, alias search and original-identity handoff."""
import json
import os
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in", allow_module_level=True)

from playwright.sync_api import expect
from reacnet_scope import services as svc
from tests.browser.workbench import wait_for_initial_callbacks


@pytest.mark.browser_area("species")
@pytest.mark.parametrize("original,other,alias", [
    ("[C][O]", "[C]=[O]", "[C-]#[O+]"),
    ("[F][C][F]", "F[C]F", "F[C]F"),
])
def test_prepare_search_export_and_open_original_species(page, workbench, original, other, alias):
    _, previous = workbench
    folder = Path(previous["folder"]) / "lewis-demo"
    folder.mkdir(exist_ok=True)
    base = folder / "carbon-monoxide"
    base.with_suffix(".reactionabcd").write_text(f"2 {original}->{other}\n")
    base.with_suffix(".species").write_text(f"Timestep 0: {original} 2 {other} 3\n")
    (folder / "rng_run.json").write_text('{"parameters":{"miso":1}}')
    dataset = svc.validate_dataset_candidate(str(folder), str(base))
    dataset.update(context_state="active", ready=True)
    page.evaluate('dataset => window.dash_clientside.set_props("app-store", {data: dataset})', dataset)
    wait_for_initial_callbacks(page)
    page.locator("#lewis-workbench > summary").click()
    page.locator("#lewis-prepare").click()
    expect(page.locator("#lewis-status")).to_contain_text("请先明确选择中性片段假设", timeout=30000)
    page.locator("#lewis-neutral input").check()
    page.locator("#lewis-prepare").click()
    expect(page.locator("#lewis-status")).to_contain_text("已准备", timeout=60000)
    expect(page.locator("#lewis-results")).to_contain_text(original, timeout=30000)
    page.locator("#lewis-alias").fill(alias)
    page.locator("#lewis-search").click()
    expect(page.locator("#lewis-results tbody tr")).to_have_count(2)
    with page.expect_download() as download:
        page.locator("#lewis-export-json").click()
    report = json.loads(Path(download.value.path()).read_text())
    assert {row["original_species"] for row in report["rows"]} == {original, other}
    assert report["rows"][0]["chemical_validity"] == "not_established"
    assert report["query"]["alias"] == alias
    assert all(row["schema_version"] == "reacnet-scope/lewis-annotation/v2" for row in report["rows"])
    if alias == "F[C]F":
        for row in report["rows"]:
            assert row["model_unpaired_electrons"] == 0
            carbon = next(atom for atom in row["model_graph"]["atoms"] if atom["element"] == "C")
            assert carbon["lone_pairs"] == 1
            assert row["unique_electronic_state"] == "not_established"
    page.locator("#lewis-results").get_by_role("button", name=original, exact=True).click()
    expect(page.locator("#lewis-workbench")).not_to_be_visible()
    expect(page.locator("#species-result-stage")).not_to_be_visible()
    expect(page.locator("#detail-body")).to_contain_text("Lewis 候选注释 · 已生成候选")
    expect(page.locator("#detail-body .rs-detail-smiles")).to_have_text(original)
    expect(page.locator('iframe[title="Lewis 候选结构"]')).to_be_visible()
    expect(page.locator("#detail-body")).to_contain_text("不能据此判定实际自旋")
    page.locator("#detail-body").scroll_into_view_if_needed()
    screenshots = os.environ.get("REACNET_SCOPE_SCREENSHOTS")
    if screenshots:
        Path(screenshots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshots) / f"lewis-detail-{'carbene' if alias == 'F[C]F' else 'co'}.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("#detail-body .rs-detail-smiles")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
