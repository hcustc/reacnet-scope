from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path

import pytest
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as conditions
from selenium.webdriver.support.ui import WebDriverWait
from werkzeug.serving import make_server

from reacnet_scope import dir_browser
from reacnet_scope import services as svc
from scripts.webapp_dash.app import create_app


def _wait_for_focus(browser, wait, target_id: str) -> None:
    try:
        wait.until(
            lambda driver: driver.switch_to.active_element.get_attribute("id")
            == target_id
        )
    except TimeoutException:
        target = browser.find_element(By.ID, target_id)
        pytest.fail(
            "focus did not reach "
            f"{target_id!r}; active={browser.switch_to.active_element.get_attribute('id')!r}, "
            f"target tabindex={target.get_attribute('tabindex')!r}"
        )


def _firefox_paths() -> tuple[str, str] | None:
    snap_root = Path("/snap/firefox/current/usr/lib/firefox")
    candidates = (
        (snap_root / "firefox", Path("/snap/bin/geckodriver")),
        (Path(shutil.which("firefox") or ""), Path(shutil.which("geckodriver") or "")),
    )
    for binary, driver in candidates:
        if binary.is_file() and driver.exists():
            return str(binary), str(driver)
    return None


def _dataset_context(folder: Path, name: str) -> dict:
    base = folder / name
    Path(f"{base}.reactionabcd").write_text("1 C -> CO\n", encoding="utf-8")
    Path(f"{base}.species").write_text("Timestep 0: C 1\n", encoding="utf-8")
    validation = svc.validate_dataset_candidate(str(folder), str(base))
    return svc.current_dataset_from_validation(validation)


def _install_session_dataset(browser, context: dict) -> None:
    browser.execute_script(
        """
        sessionStorage.setItem("dataset-session-store", arguments[0]);
        sessionStorage.setItem("dataset-session-store-timestamp", Date.now());
        """,
        json.dumps(context),
    )


def _wait_for_dataset(browser, wait, label: str) -> None:
    wait.until(
        lambda driver: driver.find_element(By.ID, "topbar-folder").text == label
    )


@pytest.fixture
def dash_url():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app = create_app()
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def firefox_driver(monkeypatch, tmp_path):
    paths = _firefox_paths()
    if paths is None:
        pytest.skip("Firefox and geckodriver are required for browser acceptance")
    binary, driver_path = paths
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    options = Options()
    options.binary_location = binary
    options.add_argument("-headless")
    service = Service(
        driver_path,
        log_output=str(tmp_path / "geckodriver.log"),
    )
    try:
        browser = webdriver.Firefox(options=options, service=service)
    except WebDriverException as exc:
        pytest.fail(f"Firefox browser acceptance could not start: {exc}")
    try:
        yield browser
    finally:
        browser.quit()


def test_dataset_picker_restores_focus_after_keyboard_cancel(
    dash_url,
    firefox_driver,
) -> None:
    browser = firefox_driver
    wait = WebDriverWait(browser, 10)
    browser.get(dash_url)

    origin = wait.until(
        conditions.element_to_be_clickable((By.ID, "species-open-data-modal"))
    )
    origin.click()
    wait.until(
        lambda driver: "active"
        in driver.find_element(By.ID, "page-data-management").get_attribute(
            "class"
        ).split()
    )
    _wait_for_focus(browser, wait, "data-candidate-summary")

    browser.find_element(By.ID, "data-pick-btn").click()
    wait.until(conditions.visibility_of_element_located((By.ID, "data-browser-view")))
    _wait_for_focus(browser, wait, "data-browser-title")

    cancel = wait.until(
        conditions.element_to_be_clickable((By.ID, "dir-browser-cancel-btn"))
    )
    cancel.send_keys(Keys.ENTER)
    wait.until(
        lambda driver: "active"
        in driver.find_element(By.ID, "page-species").get_attribute("class").split()
    )
    _wait_for_focus(browser, wait, "species-open-data-modal")


def test_current_dataset_page_and_query_are_isolated_between_tabs(
    dash_url,
    firefox_driver,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    alpha = _dataset_context(tmp_path, "alpha")
    beta = _dataset_context(tmp_path, "beta")
    browser = firefox_driver
    wait = WebDriverWait(browser, 10)

    browser.get(dash_url)
    first_tab = browser.current_window_handle
    _install_session_dataset(browser, alpha)
    browser.refresh()
    _wait_for_dataset(browser, wait, "alpha")

    alpha_query = wait.until(
        conditions.element_to_be_clickable((By.ID, "species-query"))
    )
    alpha_query.send_keys("alpha-query", Keys.TAB)
    browser.find_element(By.ID, "nav-reactions").click()
    wait.until(
        lambda driver: "active"
        in driver.find_element(By.ID, "page-reactions").get_attribute("class").split()
    )
    wait.until(
        lambda driver: driver.execute_script(
            "const value = sessionStorage.getItem('page-store'); "
            "return value ? JSON.parse(value).page : null;"
        )
        == "reactions"
    )

    browser.execute_script("window.open(arguments[0], '_blank');", dash_url)
    wait.until(lambda driver: len(driver.window_handles) == 2)
    second_tab = next(handle for handle in browser.window_handles if handle != first_tab)
    browser.switch_to.window(second_tab)
    wait.until(conditions.element_to_be_clickable((By.ID, "nav-species"))).click()
    wait.until(
        lambda driver: driver.execute_script(
            "const value = sessionStorage.getItem('page-store'); "
            "return value ? JSON.parse(value).page : null;"
        )
        == "species"
    )
    beta_query = wait.until(
        conditions.element_to_be_clickable((By.ID, "species-query"))
    )
    beta_query.clear()
    beta_query.send_keys("beta-query", Keys.TAB)
    assert "active" in browser.find_element(By.ID, "page-species").get_attribute(
        "class"
    ).split()
    _install_session_dataset(browser, beta)
    browser.refresh()
    _wait_for_dataset(browser, wait, "beta")
    assert browser.find_element(By.ID, "species-query").get_attribute("value") == (
        "beta-query"
    )

    browser.switch_to.window(first_tab)
    assert browser.find_element(By.ID, "topbar-folder").text == "alpha"
    assert browser.find_element(By.ID, "species-query").get_attribute("value") == (
        "alpha-query"
    )
    assert "active" in browser.find_element(By.ID, "page-reactions").get_attribute(
        "class"
    ).split()
    browser.refresh()
    _wait_for_dataset(browser, wait, "alpha")
    assert browser.find_element(By.ID, "species-query").get_attribute("value") == (
        "alpha-query"
    )

    browser.switch_to.window(second_tab)
    assert browser.find_element(By.ID, "topbar-folder").text == "beta"
    assert browser.find_element(By.ID, "species-query").get_attribute("value") == (
        "beta-query"
    )
