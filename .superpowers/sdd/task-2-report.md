# Task 2: dataset-browser service facade

## Delivered

- Added `browse_dataset_location(path)` in `scripts.webapp_dash.services`.
  It validates the requested directory, reuses the core directory listing,
  discovers dataset candidates, reports root-bounded breadcrumbs, and adds
  selection, completeness, and read-only event/trajectory/composition index
  states for each candidate.
- Added `resolve_dataset_input(path)` for either a directory selection or a
  manually entered common dataset prefix.
- Added `normalise_recent_datasets(records)` with absolute-path
  deduplication, deterministic newest-first ordering, a ten-record cap, and
  no availability I/O.
- Resolved configured allowed roots before directory-containment checks.
- Added browser-facade coverage for empty and ambiguous folders, breadcrumbs,
  invalid paths, inaccessible subdirectories, manual input resolution,
  recent-record normalization, and source-artifact guarded I/O.

## TDD evidence

After adding the contract tests, the focused command failed as expected:

```text
.venv/bin/python -m pytest -q tests/test_directory_browser.py tests/test_online_index_contract.py
9 failed, 29 passed
```

The new failures were missing public facade functions. Two pre-existing tests
also required fixture isolation because this execution environment makes the
home directory read-only and the Task 1 root validation intentionally rejects
the default `/tmp` pytest directory. Those tests were adjusted within the
Task 2 test files to use an allowed temporary root.

After implementation:

```text
.venv/bin/python -m pytest -q tests/test_directory_browser.py tests/test_online_index_contract.py
38 passed in 0.47s
```

## Coverage and full-suite evidence

Focused coverage could not be collected because the checked-in environment
does not include either `pytest-cov` or the `coverage` module:

```text
pytest: error: unrecognized arguments: --cov=...
python: No module named coverage
```

An initial full run using the default pytest temporary directory demonstrated
four expected allowed-root fixture failures in tests outside Task 2's allowed
file scope. Running pytest with its temporary base inside the workspace
(therefore under the default allowed home root) preserved all contracts:

```text
.venv/bin/python -m pytest -q --basetemp=/home/huangchen/cal_proc/reacnet-scope/.pytest-task2
112 passed, 60 warnings in 1.61s
```

The temporary test directory was removed after verification. The warnings are
existing Dash `dash_table.DataTable` deprecation warnings.

## Fix wave: browse index-state I/O boundary

The browser previously delegated each discovered candidate to
`dataset_preparation_status`, which can enter the scan/payload flow and inspect
prepared manifests. The browse path now derives index states directly from the
candidate's already-discovered artifact paths: event evidence uses the
reaction-event/molecules pair, trajectory uses the trajectory artifact, and
composition uses the species artifact. Missing artifacts return `missing`; a
missing cache configuration is represented consistently as `invalid` without
reading a source artifact or manifest.

### RED

Added a browser regression guard covering every ReacNet source artifact,
`Path.read_text` for manifests, and the forbidden scan/preparation facades.
Before the fix it failed at the old delegation:

```text
.venv/bin/python -m pytest -q tests/test_online_index_contract.py::OnlineIndexContractTests::test_browser_snapshot_never_opens_reacnet_source_artifacts
1 failed
AssertionError: browser snapshot invoked the preparation facade
```

### GREEN

After replacing the delegation with the direct status helper:

```text
.venv/bin/python -m pytest -q tests/test_online_index_contract.py::OnlineIndexContractTests::test_browser_snapshot_never_opens_reacnet_source_artifacts
1 passed in 0.33s

.venv/bin/python -m pytest -q tests/test_directory_browser.py tests/test_online_index_contract.py
38 passed in 0.45s
```
