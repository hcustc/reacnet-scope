"""Run every browser case once across bounded CI jobs."""

from pathlib import Path

import pytest


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
        "--browser-shard", type=_parse_shard, metavar="INDEX/TOTAL",
        help="Select one browser shard (1-based); omit to run all browser cases.",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    # A root-level run discovers this conftest after option parsing.
    shard = config.getoption("--browser-shard", default=None)
    if shard is None:
        return
    index, total = shard
    browser_dir = Path(__file__).parent
    selected, deselected = [], []
    position = 0
    for item in items:
        if browser_dir not in item.path.parents:
            selected.append(item)
            continue
        (selected if position % total == index - 1 else deselected).append(item)
        position += 1
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
