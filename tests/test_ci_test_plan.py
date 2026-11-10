"""CI selection must not silently omit shared changes or failed coverage."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.ci_test_plan import changed_paths, main, test_plan as plan_for_changes


@pytest.mark.parametrize("paths, scope, run_pytest", [
    (["README.md", "docs/ui-workbench.md"], "none", False),
    (["scripts/rng_query_cli.py"], "smoke", True),
    (["scripts/webapp_dash/evolution_workbench.py"], "smoke,abundance", True),
    (["reacnet_scope/composition.py"], "smoke,abundance,species", True),
    (["scripts/webapp_dash/candidate_graph.py", "scripts/webapp_dash/file_import.py"],
     "smoke,candidate,dataset", True),
    (["tests/browser/test_candidate_workbench.py"], "smoke,candidate", True),
    (["reacnet_scope/candidate_constraints.py"], "smoke,candidate", True),
    (["reacnet_scope/candidate_hypergraph.py"], "smoke,candidate", True),
    (["reacnet_scope/hypergraph_svg.py"], "smoke,candidate", True),
    (["tests/browser/test_abundance_workbench.py"], "smoke,abundance", True),
    (["tests/browser/test_species_workbench.py"], "smoke,species", True),
    (["scripts/webapp_dash/lewis_workbench.py"], "smoke,species", True),
    (["tests/browser/test_reaction_workbench.py"], "smoke,reactions", True),
    (["tests/browser/test_event_workbench.py"], "smoke,events", True),
    (["tests/browser/test_dataset_workbench.py"], "smoke,dataset", True),
    (["tests/browser/workbench.py"], "full", True),
    (["tests/browser/test_workbench.py"], "full", True),
    (["scripts/webapp_dash/new_page.py"], "full", True),
    (["scripts/webapp_dash/assets/workbench.css"], "full", True),
    (["scripts/webapp_dash/callbacks/events.py"], "full", True),
    (["tests/browser/new_workflow.py"], "full", True),
    (["tests/browser/conftest.py"], "full", True),
    (["reacnet_scope/services.py"], "full", True),
    (["uv.lock", "docs/ui-workbench.md"], "full", True),
    ([".github/workflows/test.yml"], "full", True),
])
def test_changed_files_choose_coverage(paths, scope, run_pytest):
    plan = plan_for_changes(paths)
    assert plan["browser_scope"] == scope
    assert plan["pytest"] is run_pytest
    assert len(plan["shards"]) == (4 if scope == "full" else 2 if "," in scope else 1)


@pytest.mark.parametrize("paths, scope", [
    (["README.md"], "none"),
    (["scripts/webapp_dash/evolution_workbench.py"], "smoke,abundance"),
    (["scripts/webapp_dash/callbacks/events.py"], "all"),
    (["reacnet_scope/services.py"], "all"),
    (["uv.lock"], "all"),
    ([".github/workflows/test.yml"], "all"),
])
def test_pull_requests_run_the_complete_suite_on_compact_navigation_only(paths, scope):
    plan = plan_for_changes(paths, pull_request=True)
    assert plan["browser_scope"] == scope
    assert plan["shards"] == ([1, 2, 3, 4] if scope == "all" else [1, 2] if "," in scope else [1])
    # Push, scheduled and manual runs keep classic navigation for shared changes.
    if scope == "all":
        assert plan_for_changes(paths)["browser_scope"] == "full"


def test_pull_request_event_is_the_only_one_that_drops_classic_navigation(tmp_path, monkeypatch):
    import scripts.ci_test_plan as ci_test_plan

    event = tmp_path / "event.json"
    event.write_text("{}")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(ci_test_plan, "changed_paths", lambda *_: ["reacnet_scope/services.py"])
    for event_name, scope in (("pull_request", "all"), ("push", "full")):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
        output.write_text("")
        main()
        assert f"browser_scope={scope}" in output.read_text().splitlines()
    # Without a reliable diff a pull request still falls back to the complete suite.
    monkeypatch.setattr(ci_test_plan, "changed_paths", lambda *_: None)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    output.write_text("")
    main()
    assert "browser_scope=full" in output.read_text().splitlines()


def test_manual_and_scheduled_runs_always_use_full_coverage(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text("{}")
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    for event_name in ("schedule", "workflow_dispatch"):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
        output.write_text("")
        main()
        assert output.read_text().splitlines() == [
            "pytest=true", "browser_scope=full", "shards=[1, 2, 3, 4]",
        ]


def test_pr_diff_includes_all_commits_and_removed_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output([
            "git", "-c", "user.name=CI Test", "-c", "user.email=ci@example.invalid", *args,
        ], stderr=subprocess.DEVNULL).decode().strip()

    git("init")
    Path("README.md").write_text("Initial documentation")
    git("add", "README.md")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    Path("uv.lock").write_text("dependency change")
    git("add", "uv.lock")
    git("commit", "-m", "dependency")
    Path("README.md").write_text("Latest commit only changes documentation")
    git("add", "README.md")
    git("commit", "-m", "docs")
    head = git("rev-parse", "HEAD")
    paths = changed_paths("pull_request", {"pull_request": {
        "base": {"sha": base}, "head": {"sha": head},
    }})
    assert set(paths) == {"README.md", "uv.lock"}
    assert plan_for_changes(paths)["browser_scope"] == "full"
    git("mv", "uv.lock", "dependency-notes.md")
    git("commit", "-m", "move code into docs")
    paths = changed_paths("push", {"before": head, "after": git("rev-parse", "HEAD")})
    assert set(paths) == {"uv.lock", "dependency-notes.md"}
    assert plan_for_changes(paths)["browser_scope"] == "full"
    assert changed_paths("push", {"before": "0" * 40, "after": head}) is None


@pytest.fixture
def collected_browser_cases(tmp_path):
    browser_dir = tmp_path / "tests" / "browser"
    browser_dir.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent / "browser" / "conftest.py", browser_dir / "conftest.py")
    (browser_dir / "test_cases.py").write_text('''
import pytest
@pytest.fixture(params=["0", "1"], ids=["classic", "compact"])
def workbench(request):
    return request.param
@pytest.mark.browser_smoke
def test_critical(workbench): pass
@pytest.mark.browser_area("abundance")
def test_abundance(workbench): pass
@pytest.mark.browser_area("events")
def test_events(workbench): pass
def test_full_only(workbench): pass
''')

    def collect(*options):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/browser", *options],
            cwd=tmp_path, text=True, capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return {line for line in result.stdout.splitlines() if "::test_" in line}

    return collect


def test_scoped_collection_and_sharding_cover_each_selected_case_once(collected_browser_cases):
    collect = collected_browser_cases
    full = collect()
    assert len(full) == 8
    smoke = collect("--browser-scope=smoke")
    assert smoke == {"tests/browser/test_cases.py::test_critical[compact]"}
    related = collect("--browser-scope=smoke,abundance")
    assert related == smoke | {"tests/browser/test_cases.py::test_abundance[compact]"}
    every_case_once = collect("--browser-scope=all")
    assert every_case_once == {
        f"tests/browser/test_cases.py::{name}[compact]"
        for name in ("test_critical", "test_abundance", "test_events", "test_full_only")
    }
    first = collect("--browser-scope=smoke,abundance", "--browser-shard=1/2")
    second = collect("--browser-scope=smoke,abundance", "--browser-shard=2/2")
    assert not first & second
    assert first | second == related
