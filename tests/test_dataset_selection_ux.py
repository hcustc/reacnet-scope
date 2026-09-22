from __future__ import annotations

import json
from pathlib import Path

import pytest

import reacnet_scope.dir_browser as dir_browser
from reacnet_scope import services as svc
from scripts.webapp_dash.app import create_app
from scripts.webapp_dash.navigation import PAGE_LABELS


def _layout_ids(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        props = node.get("props") or {}
        component_id = props.get("id")
        if isinstance(component_id, str):
            found.add(component_id)
        for value in node.values():
            found.update(_layout_ids(value))
    elif isinstance(node, list):
        for value in node:
            found.update(_layout_ids(value))
    return found


def _multi_outputs(output: str) -> list[dict[str, str]]:
    return [
        {
            "id": token.split(".")[0],
            "property": token.split(".")[1].split("@")[0],
        }
        for token in output.strip(".").split("...")
    ]


def test_dataset_navigation_and_layout_use_confirmed_product_language() -> None:
    client = create_app().server.test_client()
    layout = client.get("/_dash-layout").get_json()
    layout_text = json.dumps(layout, ensure_ascii=False)
    ids = _layout_ids(layout)

    assert PAGE_LABELS["data-management"] == "RNG 数据"
    assert {
        "import-selection", "import-files", "import-group", "import-filter",
        "import-add-folder", "data-apply-btn", "data-overview-actions",
        "dataset-switch-request", "dataset-switch-validation",
    } <= ids
    assert "选择数据" in layout_text
    assert "开始分析" in layout_text
    assert "dir-browser-select-btn" not in ids
    assert "data-review-view" not in ids
    assert "启动目录" not in layout_text
    assert "RNG 数据公共前缀" not in layout_text
    assert "开始物种检索" not in layout_text


def test_dataset_folder_resolves_to_the_only_discovered_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    base = tmp_path / "run.lammpstrj"
    Path(f"{base}.species").touch()

    candidate = svc.resolve_dataset_folder_candidate(str(tmp_path))

    assert candidate == {
        "folder": str(tmp_path),
        "base": str(base),
        "label": tmp_path.name,
    }


def test_dataset_folder_rejects_zero_or_multiple_datasets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])

    with pytest.raises(svc.ServiceError) as missing:
        svc.resolve_dataset_folder_candidate(str(tmp_path))
    assert missing.value.reason == "dataset_not_found"

    for name in ("first.lammpstrj", "second.lammpstrj"):
        Path(f"{tmp_path / name}.species").touch()
    with pytest.raises(svc.ServiceError) as ambiguous:
        svc.resolve_dataset_folder_candidate(str(tmp_path))
    assert ambiguous.value.reason == "ambiguous_dataset_folder"
