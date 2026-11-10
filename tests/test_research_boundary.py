"""Optional research capabilities stay outside the core import graph (ADR-0034)."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import warnings

import pytest

from reacnet_scope import analysis_services
from reacnet_scope.research import kinetics, species_fate

RESEARCH_MODULES = (
    "reacnet_scope.research.species_fate",
    "reacnet_scope.research.kinetics",
)


def test_core_cli_and_dash_imports_do_not_load_research_modules() -> None:
    code = (
        "import json, sys\n"
        "import reacnet_scope, reacnet_scope.services, scripts.rng_query_cli\n"
        "import scripts.webapp_dash.app\n"
        "print(json.dumps(sorted(m for m in sys.modules\n"
        "    if m.startswith('reacnet_scope.research.') or m == 'scipy')))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []


@pytest.mark.parametrize(
    ("old_name", "module"),
    [
        ("reacnet_scope.species_fate", species_fate),
        ("reacnet_scope.kinetics", kinetics),
    ],
)
def test_old_import_paths_are_deprecated_aliases(old_name, module) -> None:
    sys.modules.pop(old_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alias = importlib.import_module(old_name)

    assert alias is module
    assert any(
        issubclass(item.category, DeprecationWarning)
        and "reacnet_scope.research" in str(item.message)
        for item in caught
    )


def test_missing_research_dependency_reports_unavailable_rate(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "reacnet_scope.research.kinetics", None)
    rows = [{"reaction_smiles": "A>>B"}]

    summary = analysis_services._enrich_channel_kinetics({}, rows)

    assert summary["status"] == "unavailable"
    assert summary["reason"] == "optional_dependency_missing"
    assert "SciPy" in summary["message"]
    assert rows[0]["k_app"] is None
    assert rows[0]["kinetics_reason"] == "optional_dependency_missing"
