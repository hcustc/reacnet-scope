from __future__ import annotations

from pathlib import Path
import tomllib


def test_trajectory_dependency_contract() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["optional-dependencies"]["trajectory"] == ["ase>=3.23,<4"]


def test_default_development_environment_can_run_the_complete_test_suite() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    groups = data["dependency-groups"]

    assert data["tool"]["uv"]["default-groups"] == ["dev"]
    assert groups["dev"] == [{"include-group": "test"}]
    assert set(groups["test"]) == {
        "pytest>=8,<9",
        *data["project"]["optional-dependencies"]["web"],
        *data["project"]["optional-dependencies"]["trajectory"],
    }
