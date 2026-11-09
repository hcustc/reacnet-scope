from __future__ import annotations

from pathlib import Path
import tomllib


def _project() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_extras_map_one_to_one_onto_install_purposes() -> None:
    extras = _project()["project"]["optional-dependencies"]

    # web + trajectory is the documented deployment; plot serves CLI PNG export.
    assert extras == {
        "web": [
            "dash[diskcache]>=4,<5",
            "dash-bootstrap-components>=2,<3",
            "dash-cytoscape>=1,<2",
            "dash-ag-grid>=35.2,<36",
        ],
        "trajectory": ["ase>=3.23,<4"],
        "plot": ["matplotlib>=3.8"],
    }


def test_no_requirement_is_declared_twice_across_core_and_extras() -> None:
    project = _project()["project"]
    names = [
        requirement.split("[")[0].split(">")[0].split("<")[0].split("=")[0]
        for requirement in (
            list(project["dependencies"])
            + [r for reqs in project["optional-dependencies"].values() for r in reqs]
        )
    ]

    assert len(names) == len(set(names))


def test_scipy_is_a_core_dependency() -> None:
    # Research modules and Lewis annotations import SciPy on demand (ADR-0034).
    assert "scipy>=1.11" in _project()["project"]["dependencies"]


def test_default_development_environment_can_run_the_complete_test_suite() -> None:
    data = _project()
    groups = data["dependency-groups"]

    assert data["tool"]["uv"]["default-groups"] == ["dev"]
    assert groups["dev"] == [{"include-group": "test"}]
    assert set(groups["test"]) == {
        "pytest>=8,<9",
        *(r for reqs in data["project"]["optional-dependencies"].values() for r in reqs),
    }
