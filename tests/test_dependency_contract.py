from __future__ import annotations

from pathlib import Path
import tomllib


def test_graph_and_trajectory_dependency_contracts() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert "networkx>=3.2,<4" in data["project"]["dependencies"]
    assert data["project"]["optional-dependencies"]["trajectory"] == ["ase>=3.23,<4"]
