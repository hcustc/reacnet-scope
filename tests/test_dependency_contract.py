from __future__ import annotations

from pathlib import Path
import tomllib


def test_trajectory_dependency_contract() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["optional-dependencies"]["trajectory"] == ["ase>=3.23,<4"]
