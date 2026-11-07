"""Select related browser workflows, then distribute them across CI jobs."""

from pathlib import Path
import os

import pytest


if os.environ.get('REACNET_SCOPE_BROWSER_TESTS') == '1':
    from tests.browser.workbench import page, prepared_abundance_page, workbench


def _parse_shard(value: str) -> tuple[int, int]:
    try:
        index, total = (int(part) for part in value.split("/"))
        if not 1 <= index <= total:
            raise ValueError
    except ValueError as exc:
        raise pytest.UsageError("--browser-shard must be INDEX/TOTAL with 1 <= INDEX <= TOTAL") from exc
    return index, total


def pytest_addoption(parser):
    parser.addoption(
        "--browser-scope", default="full", metavar="full|smoke,AREA",
        help="full runs both navigation layouts; scoped runs use compact navigation.",
    )
    parser.addoption(
        "--browser-shard", type=_parse_shard, metavar="INDEX/TOTAL",
        help="Select one browser shard (1-based); omit to run all browser cases.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "browser_smoke: critical everyday browser workflow")
    config.addinivalue_line("markers", "browser_area(*names): related browser workflow areas")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    # A root-level run discovers this conftest after option parsing.
    shard = config.getoption("--browser-shard", default=None)
    scope = config.getoption("--browser-scope", default="full")
    scopes = set(scope.split(","))
    allowed = {"full", "smoke", "abundance", "candidate", "dataset", "events", "species", "reactions"}
    if not scopes <= allowed or ("full" in scopes and len(scopes) != 1):
        raise pytest.UsageError(f"Invalid --browser-scope: {scope}")
    index, total = shard or (1, 1)
    browser_dir = Path(__file__).parent
    selected, deselected = [], []
    position = 0
    for item in items:
        if browser_dir not in item.path.parents:
            selected.append(item)
            continue
        areas = {area for marker in item.iter_markers("browser_area") for area in marker.args}
        critical = item.get_closest_marker("browser_smoke") is not None
        classic = getattr(item, "callspec", None) is not None and item.callspec.params.get("workbench") == "0"
        if "full" not in scopes and (classic or not (
            areas & scopes or ("smoke" in scopes and critical)
        )):
            deselected.append(item)
            continue
        (selected if position % total == index - 1 else deselected).append(item)
        position += 1
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
