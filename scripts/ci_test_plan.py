"""Choose CI coverage from the complete PR/push diff, without extra dependencies."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


# Unknown Dash changes deliberately fall back to the complete browser suite.
AREA_PATHS = {
    "abundance": (
        "scripts/webapp_dash/evolution_workbench.py",
        "scripts/webapp_dash/assets/evolution_targets.js",
        "reacnet_scope/composition.py",
        "reacnet_scope/formula.py",
        "tests/test_abundance_workbench.py",
        "tests/browser/test_abundance_",
    ),
    "candidate": (
        "scripts/webapp_dash/candidate_",
        "scripts/webapp_dash/assets/candidate_",
        "reacnet_scope/path_search",
        "reacnet_scope/candidate_",
        "reacnet_scope/hypergraph_svg.py",
        "tests/browser/test_candidate_",
    ),
    "dataset": (
        "scripts/webapp_dash/dataset_library.py",
        "scripts/webapp_dash/file_import.py",
        "scripts/webapp_dash/assets/library_index_poll.js",
        "scripts/webapp_dash/assets/data_picker_layout.js",
        "tests/browser/test_dataset_",
    ),
    "events": (
        "scripts/webapp_dash/lineage_explorer.py",
        "scripts/webapp_dash/assets/event_viewer.js",
        "scripts/webapp_dash/assets/3Dmol-min.js",
        "tests/browser/test_event_",
    ),
    "species": ("reacnet_scope/composition.py", "reacnet_scope/formula.py",
                "reacnet_scope/lewis", "scripts/webapp_dash/lewis_workbench.py",
                "tests/browser/test_lewis_", "tests/test_lewis_",
                "tests/browser/test_species_"),
    "reactions": ("tests/browser/test_reaction_",),
}
# Callback modules whose owned components are only exercised by area-marked
# browser cases. Modules that write cross-workspace state (dataset_switch,
# page_navigation, preparation, species, ...) or whose components appear in
# unmarked navigation acceptance (tests/browser/test_workbench.py) stay
# unmapped, so they keep falling back to the complete suite.
CALLBACK_AREAS = {
    "batch.py": ("dataset", "reactions"),
    "dataset_overview.py": ("dataset",),
    "dft.py": ("events",),
    "element_distribution.py": ("abundance",),
    "event_exports.py": ("events",),
    "events.py": ("candidate", "events"),
    "evolution.py": ("abundance", "species"),
    "molecule_lineage.py": ("events",),
    "reaction_timing.py": ("events", "reactions"),
}
for _module, _areas in CALLBACK_AREAS.items():
    for _area in _areas:
        AREA_PATHS[_area] += (f"scripts/webapp_dash/callbacks/{_module}",)
SHARED_PATHS = (
    ".github/workflows/", "scripts/ci_test_plan.py", "pyproject.toml", "uv.lock",
    "tests/browser/conftest.py", "tests/browser/workbench.py", "tests/browser/test_workbench.py",
    "tests/test_ci_test_plan.py", "reacnet_scope/services.py",
    "reacnet_scope/analysis_services.py", "reacnet_scope/evidence_services.py",
    "reacnet_scope/queries.py",
)


def test_plan(paths: list[str], *, force_full: bool = False) -> dict:
    """Return a fail-closed plan; an unknown shared UI change runs everything."""
    code = [path for path in paths if not (
        path.endswith((".md", ".rst")) or path.startswith(("docs/", ".agents/"))
        or path == "LICENSE"
    )]
    if force_full:
        scopes = ["full"]
    elif not code:
        scopes = []
    else:
        areas = set()
        for path in code:
            matched = {area for area, prefixes in AREA_PATHS.items()
                       if path.startswith(prefixes)}
            if path.startswith(SHARED_PATHS) or (
                path.startswith(("scripts/webapp_dash/", "tests/browser/")) and not matched
            ):
                areas = {"full"}
                break
            areas.update(matched)
        scopes = ["full"] if "full" in areas else ["smoke", *sorted(areas)]
    return {
        "pytest": bool(code) or force_full,
        "browser_scope": ",".join(scopes) or "none",
        "shards": [1, 2, 3, 4] if scopes == ["full"] else (
            [1, 2] if len(scopes) > 1 else [1]
        ),
    }


def changed_paths(event_name: str, event: dict) -> list[str] | None:
    """None means no reliable diff: use full coverage instead of skipping."""
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
        separator = "..."
    elif event_name == "push":
        base, head, separator = event["before"], event["after"], ".."
    else:
        return None
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise ValueError("Expected full Git commit IDs")
    if base == "0" * 40:
        return None
    diff = subprocess.check_output([
        "git", "diff", "--name-only", "--no-renames", "-z", f"{base}{separator}{head}", "--",
    ])
    return [path for path in diff.decode().split("\0") if path]


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    paths = changed_paths(os.environ["GITHUB_EVENT_NAME"], event)
    plan = test_plan(paths or [], force_full=paths is None)
    print(json.dumps({"changed_files": paths, **plan}, ensure_ascii=False, indent=2))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for name, value in plan.items():
            encoded = value if isinstance(value, str) else json.dumps(value)
            output.write(f"{name}={encoded}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(f"pytest: **{plan['pytest']}** · browser: **{plan['browser_scope']}**\n")


if __name__ == "__main__":
    main()
