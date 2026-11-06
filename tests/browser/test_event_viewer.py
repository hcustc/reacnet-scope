"""Exercise trajectory presentation through the shipped clientside renderer."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

if os.environ.get("REACNET_SCOPE_BROWSER_TESTS") != "1":
    pytest.skip("Browser acceptance is opt-in; see docs/agents/testing.md", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.browser_area("events")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def viewer_page(browser):
    page = browser.new_page()
    page.set_content(
        '<div id="event-trajectory-3dmol"></div>'
        '<div id="event-core-atom-list"></div>'
        '<div id="event-atom-inspector-body"></div>'
    )
    # Replace only the WebGL surface; selection, clearing, and inspector DOM use
    # the actual application renderer, without depending on GPU availability.
    page.add_script_tag(content="""
        window.__models = [];
        window.__shapes = [];
        window.__labels = [];
        window.$3Dmol = {createViewer() {return {
            removeAllModels() {window.__models = [];},
            removeAllShapes() {window.__shapes = [];},
            removeAllLabels() {window.__labels = [];},
            render() {}, resize() {}, zoomTo() {},
            getView() {return [];}, setView() {},
            addModel() {return {
                addAtoms(atoms) {window.__models.push(atoms);},
                setStyle() {}, setHoverable() {}, setClickable() {},
            };},
            addSphere(shape) {window.__shapes.push(shape); return shape;},
            addLabel(label) {window.__labels.push(label); return label;},
            addLine() {}, removeShape() {}, removeLabel() {},
        };}};
    """)
    source = Path(__file__).resolve().parents[2] / "scripts/webapp_dash/assets/event_viewer.js"
    page.add_script_tag(path=str(source))
    try:
        yield page
    finally:
        page.close()


def render(page, viewer, scope="participants"):
    return page.evaluate(
        "([data, scope]) => window.dash_clientside.reacnetScope.renderEventTrajectory(0, scope, data, [])",
        [viewer, scope],
    )


def trajectory(*, core=None, participants=None, unit=None):
    return {
        "event_id": "event-one",
        "frames": [{"frame": 0, "atoms": [
            {"id": 1, "element": "C", "x": 1, "y": 2, "z": 3, "group": "core"},
            {"id": 2, "element": "O", "x": 4, "y": 5, "z": 6, "group": "environment"},
        ]}],
        "atom_groups": {"core": [1] if core is None else core,
                        "participants": [1] if participants is None else participants,
                        "context": [1, 2]},
        "meta": {"coordinate_length_unit": unit},
    }


@pytest.mark.parametrize("scope", ["core", "participants"])
def test_empty_scope_clears_previous_atoms_and_inspector(viewer_page, scope):
    render(viewer_page, trajectory())
    viewer_page.locator("#event-core-atom-list button").click()
    expect(viewer_page.locator("#event-atom-inspector-body")).to_contain_text("已固定")

    message = render(viewer_page, trajectory(core=[], participants=[]), scope)

    assert message == "当前显示范围没有可渲染原子"
    assert viewer_page.evaluate("window.__models") == []
    assert viewer_page.evaluate("window.__shapes") == []
    assert viewer_page.locator("#event-core-atom-list button").count() == 0
    expect(viewer_page.locator("#event-atom-inspector-body")).not_to_contain_text("已固定")


def test_scope_with_no_atoms_in_current_frame_clears_previous_geometry(viewer_page):
    render(viewer_page, trajectory())
    message = render(viewer_page, trajectory(core=[99]), "core")
    assert message == "当前显示范围没有可渲染原子"
    assert viewer_page.evaluate("window.__models") == []


@pytest.mark.parametrize("unit, suffix", [(None, "单位未确认"), ("angstrom", "Å")])
def test_atom_inspector_uses_confirmed_coordinate_unit(viewer_page, unit, suffix):
    render(viewer_page, trajectory(unit=unit))
    viewer_page.locator("#event-core-atom-list button").click()
    coordinates = viewer_page.locator("#event-atom-inspector-body dd").nth(5)
    expect(coordinates).to_have_text(f"(1.000, 2.000, 3.000) {suffix}")


def test_valid_scope_only_renders_selected_atoms(viewer_page):
    render(viewer_page, trajectory(), "participants")
    assert viewer_page.evaluate("window.__models.map(atoms => atoms.map(atom => atom.serial))") == [[1]]
    render(viewer_page, trajectory(), "context")
    assert viewer_page.evaluate("window.__models.map(atoms => atoms.map(atom => atom.serial))") == [[1, 2]]


def test_legacy_participant_groups_include_both_reaction_sides(viewer_page):
    viewer = trajectory()
    viewer["atom_groups"] = {"reactant": [1], "product": [2], "context": [1, 2]}
    render(viewer_page, viewer)
    assert viewer_page.evaluate("window.__models.map(atoms => atoms.map(atom => atom.serial))") == [[1, 2]]

    viewer["atom_groups"]["participants"] = []
    render(viewer_page, viewer)
    assert viewer_page.evaluate("window.__models") == []
