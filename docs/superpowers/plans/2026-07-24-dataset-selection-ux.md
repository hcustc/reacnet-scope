# Dataset-First Selection UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the directory/base/scan/apply sequence with a dataset-first server browser and guarantee that every new species search clears stale workflow selections.

**Architecture:** A new Dash-independent discovery module groups ReacNetGenerator artifacts by their shared output prefix without opening source data. The existing legacy status builder and a new Dash browser facade consume this one contract. Dash keeps browser candidates separate from the applied dataset, hides the internal `base`, stores at most ten recent datasets locally, and resets workflow selection explicitly when a new species search starts.

**Tech Stack:** Python 3.10+, pathlib/os.scandir, Dash 4, dash-bootstrap-components, pytest, existing ReacNet Scope index/status services.

## Global Constraints

- Preserve unrelated working-tree changes and never modify files under `ref_data/`.
- Do not install or update dependencies; this feature uses the existing environment.
- Keep all server paths inside `REACNET_SCOPE_ALLOWED_ROOTS`.
- Dataset discovery may inspect names, existence, stat metadata and prepared SQLite metadata; it must not open `.reactionevent.csv`, `.molecules.csv`, `.species`, `.route`, or trajectory source files.
- Keep the applied dataset unchanged until the user explicitly clicks `加载数据集`.
- A directory with one candidate selects it automatically; a directory with multiple candidates must not silently choose the first.
- Do not expose `base` or “运行组” as a required user concept.
- Recent datasets are browser-local, limited to ten records, and contain only folder, base, label and load time.
- Do not run offline index builders from Dash.

---

### Task 1: Create the shared dataset-discovery contract

**Files:**
- Create: `reacnet_scope/datasets.py`
- Create: `tests/test_dataset_discovery.py`
- Modify: `scripts/webapp/server.py:712-778`
- Modify: `tests/test_transition_table.py`

**Interfaces:**
- Produces: `discover_dataset_candidates(directory: str | Path) -> list[dict[str, Any]]`
- Produces: `choose_dataset_candidate(candidates: Iterable[dict[str, Any]], preferred_base: str = "") -> dict[str, Any] | None`
- Candidate keys: `folder`, `base`, `label`, `kinds`, `artifact_paths`, `score`, `mtime`
- Preserves: `build_dataset_status_payload(params) -> dict[str, Any]`

- [ ] **Step 1: Write failing discovery tests**

Create fixtures with one RP3-style prefix, two prefixes, unrelated files, and a guarded `open`:

```python
def test_discovery_groups_artifacts_without_opening_sources(tmp_path, monkeypatch):
    base = tmp_path / "rp3.lammpstrj"
    Path(f"{base}.reactionabcd").write_text("1 A->B\n")
    Path(f"{base}.species").write_text("Timestep 0: A 1\n")
    Path(f"{base}.reactionevent.csv").write_text(
        "Timestep_Index,Reactant,Product\n0,A,B\n"
    )
    Path(f"{base}.molecules.csv").write_text(
        "Timestep,Species,AtomIDs,BondIDs\n0,A,0,\n1,B,0,\n"
    )
    protected = {
        str(Path(f"{base}.reactionabcd")),
        str(Path(f"{base}.species")),
        str(Path(f"{base}.reactionevent.csv")),
        str(Path(f"{base}.molecules.csv")),
    }
    real_open = Path.open

    def forbidden_open(path, *args, **kwargs):
        if str(path) in protected:
            raise AssertionError("discovery opened source data")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden_open)
    candidates = discover_dataset_candidates(tmp_path)

    assert [item["label"] for item in candidates] == ["rp3.lammpstrj"]
    assert candidates[0]["kinds"] == [
        "molecules", "reaction", "reactionevent", "species"
    ]
```

Also assert that two prefixes remain separate, unknown files are ignored, candidates sort by completeness then modification time, and `choose_dataset_candidate` returns `None` for ambiguous candidates without a preferred base.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dataset_discovery.py
```

Expected: import failure because `reacnet_scope.datasets` does not exist.

- [ ] **Step 3: Implement the pure discovery module**

Use longest-suffix-first matching and `os.scandir`:

```python
ARTIFACT_SUFFIXES = (
    (".reactionevent.csv", "reactionevent"),
    (".molecules.csv", "molecules"),
    (".reactionabcd", "reaction"),
    (".lammpstrj", "trajectory"),
    (".species", "species"),
    (".moname", "moname"),
    (".route", "route"),
    (".table", "table"),
)


def discover_dataset_candidates(directory: str | Path) -> list[dict[str, Any]]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"dataset folder not found: {root}")
    groups: dict[str, dict[str, Path]] = defaultdict(dict)
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            lower_name = entry.name.lower()
            for suffix, kind in ARTIFACT_SUFFIXES:
                if lower_name.endswith(suffix):
                    base = str(root / entry.name[: -len(suffix)])
                    if kind == "trajectory":
                        base = str(root / entry.name)
                    groups[base][kind] = Path(entry.path)
                    break
    candidates = [
        {
            "folder": str(root),
            "base": base,
            "label": Path(base).name,
            "kinds": sorted(paths),
            "artifact_paths": {
                kind: str(path) for kind, path in paths.items()
            },
            "score": len(paths),
            "mtime": max(path.stat().st_mtime for path in paths.values()),
        }
        for base, paths in groups.items()
    ]
    return sorted(
        candidates,
        key=lambda item: (
            -int(item["score"]),
            -float(item["mtime"]),
            str(item["label"]).casefold(),
        ),
    )
```

Implement `choose_dataset_candidate` so one candidate is automatic, an exact absolute `preferred_base` wins, and multiple candidates without a preference return `None`.

- [ ] **Step 4: Replace legacy duplicate discovery**

Change `_scan_rng_dataset_directory` to call the shared functions, preserve the existing tuple result, and mark only an explicitly or uniquely chosen candidate as `selected`. Do not retain a second suffix table in `server.py`.

- [ ] **Step 5: Run focused and legacy tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dataset_discovery.py \
  tests/test_transition_table.py
```

Expected: all tests pass, including ambiguous-directory behavior.

- [ ] **Step 6: Commit**

```bash
git add \
  reacnet_scope/datasets.py \
  scripts/webapp/server.py \
  tests/test_dataset_discovery.py \
  tests/test_transition_table.py
git commit -m "refactor: centralize dataset discovery"
```

---

### Task 2: Add the dataset-browser service facade

**Files:**
- Modify: `scripts/webapp_dash/services.py:90-180`
- Modify: `rng_tools/dir_browser.py`
- Modify: `tests/test_directory_browser.py`
- Modify: `tests/test_online_index_contract.py`

**Interfaces:**
- Consumes: `discover_dataset_candidates(directory)`
- Produces: `browse_dataset_location(path: str) -> dict[str, Any]`
- Produces: `resolve_dataset_input(path: str) -> dict[str, str]`
- Produces: `normalise_recent_datasets(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]`
- Browser snapshot keys: `current_path`, `parent_path`, `can_go_up`, `breadcrumbs`, `subdirs`, `datasets`

- [ ] **Step 1: Write failing browser-facade tests**

```python
def test_browser_snapshot_exposes_breadcrumbs_and_one_dataset(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])
    data_dir = tmp_path / "case"
    data_dir.mkdir()
    Path(f"{data_dir / 'rp3.lammpstrj'}.reactionabcd").touch()
    Path(f"{data_dir / 'rp3.lammpstrj'}.species").touch()

    snapshot = svc.browse_dataset_location(str(data_dir))

    assert snapshot["current_path"] == str(data_dir)
    assert snapshot["breadcrumbs"][-1] == {
        "label": "case",
        "path": str(data_dir),
    }
    assert snapshot["datasets"][0]["auto_selected"] is True
```

Add cases for zero datasets, multiple datasets with no auto-selection, invalid/out-of-root paths, inaccessible subdirectories, and breadcrumb paths never escaping the allowed root.

Test `resolve_dataset_input` with both a directory and a full common prefix such as
`/data/case/rp3.lammpstrj`; the latter must return its parent as `folder` and the
full prefix as `preferred_base`.

Add a guarded-I/O test that patches both `builtins.open` and `Path.open` for all ReacNet source artifacts, then calls `browse_dataset_location`; it must not open them.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_directory_browser.py \
  tests/test_online_index_contract.py
```

Expected: failures because `browse_dataset_location` is missing.

- [ ] **Step 3: Implement the browser snapshot**

Validate first, reuse `list_directory`, then attach discovery results:

```python
def browse_dataset_location(path: str) -> dict[str, Any]:
    current = validate_browse_path(path)
    listing = _core_list_directory(str(current))
    candidates = discover_dataset_candidates(current)
    breadcrumbs = _breadcrumbs_within_allowed_root(current)
    datasets = []
    for candidate in candidates:
        preparation = dataset_preparation_status(
            candidate["folder"],
            base=candidate["base"],
        )
        datasets.append(
            {
                **candidate,
                "auto_selected": len(candidates) == 1,
                "completeness": (
                    f"{candidate['score']}/{len(ARTIFACT_SUFFIXES)}"
                ),
                "index_states": {
                    "event": preparation["events"]["state"],
                    "trajectory": preparation["trajectory"]["state"],
                    "composition": preparation["composition"]["state"],
                },
            }
        )
    return {
        **listing,
        "breadcrumbs": breadcrumbs,
        "datasets": datasets,
}
```

Implement breadcrumbs from the containing allowed root rather than from `/`:

```python
def _breadcrumbs_within_allowed_root(current: Path) -> list[dict[str, str]]:
    containing = [
        root.resolve()
        for root in ALLOWED_ROOTS
        if current.is_relative_to(root.resolve())
    ]
    if not containing:
        raise ServiceError("路径超出允许范围", reason="path_out_of_bounds")
    root = max(containing, key=lambda item: len(item.parts))
    crumbs = [{"label": root.name or str(root), "path": str(root)}]
    cursor = root
    for part in current.relative_to(root).parts:
        cursor = cursor / part
        crumbs.append({"label": part, "path": str(cursor)})
    return crumbs
```

Resolve manual directory/prefix input without allowing a parent outside the permitted
roots:

```python
def resolve_dataset_input(path: str) -> dict[str, str]:
    raw = Path(str(path or "").strip()).expanduser()
    if raw.is_dir():
        folder = validate_browse_path(str(raw))
        return {"folder": str(folder), "preferred_base": ""}
    folder = validate_browse_path(str(raw.parent))
    if not folder.is_dir():
        raise ServiceError("数据集父目录不存在", reason="missing_folder")
    return {
        "folder": str(folder),
        "preferred_base": str(raw.resolve()),
    }
```

- [ ] **Step 4: Implement deterministic recent-record normalization**

```python
def normalise_recent_datasets(records):
    deduped = {}
    for raw in records or []:
        folder = str(raw.get("folder") or "").strip()
        base = str(raw.get("base") or "").strip()
        if not folder or not base:
            continue
        key = (os.path.abspath(folder), os.path.abspath(base))
        deduped[key] = {
            "folder": key[0],
            "base": key[1],
            "label": str(raw.get("label") or Path(base).name),
            "loaded_at": int(raw.get("loaded_at") or 0),
        }
    return sorted(
        deduped.values(),
        key=lambda item: -item["loaded_at"],
    )[:10]
```

Do not stat recent records in this function; availability is evaluated when the browser renders them.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_directory_browser.py \
  tests/test_online_index_contract.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add \
  scripts/webapp_dash/services.py \
  rng_tools/dir_browser.py \
  tests/test_directory_browser.py \
  tests/test_online_index_contract.py
git commit -m "feat: expose dataset-aware browser snapshots"
```

---

### Task 3: Redesign the server browser layout and navigation

**Files:**
- Modify: `scripts/webapp_dash/app.py:1345-1585`
- Modify: `scripts/webapp_dash/callbacks.py:577-650,2168-2350`
- Modify: `scripts/webapp_dash/assets/app.css`
- Modify: `tests/test_dash_smoke.py`

**Interfaces:**
- Consumes: `browse_dataset_location(path)`
- Adds components: `dir-browser-path-input`, `dir-browser-breadcrumbs`, `dir-browser-datasets`, `dir-browser-recent`
- Adds stores: `dataset-browser-candidate` (memory), `recent-datasets` (local)
- Pattern IDs: `dir-browser-crumb`, `dir-browser-entry`, `dir-browser-dataset`, `dir-browser-recent-entry`

- [ ] **Step 1: Write failing layout and navigation tests**

Extend the layout smoke test:

```python
assert "dir-browser-path-input" in layout_ids
assert "dir-browser-breadcrumbs" in layout_ids
assert "dir-browser-datasets" in layout_ids
assert "dir-browser-recent" in layout_ids
assert "dataset-browser-candidate" in layout_ids
assert "recent-datasets" in layout_ids
```

Post Dash callback payloads that verify:

- opening starts at the current dataset directory;
- clicking a breadcrumb navigates directly;
- entering a valid path and pressing Enter navigates;
- one discovered dataset populates `dataset-browser-candidate`;
- multiple datasets leave the candidate empty until one card is clicked;
- cancel leaves `data-folder-input` and the applied `app-store` unchanged.

- [ ] **Step 2: Run the callback tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py
```

Expected: missing-component assertions and callback expectation failures.

- [ ] **Step 3: Add the browser components and stores**

In `_dir_browser_modal`, replace the code-only path display with:

```python
dbc.InputGroup(
    [
        dbc.Input(
            id="dir-browser-path-input",
            debounce=True,
            placeholder="输入服务器目录后按 Enter",
        ),
        dbc.Button("前往", id="dir-browser-go-btn", color="secondary"),
    ]
),
html.Div(id="dir-browser-breadcrumbs", className="rs-browser-breadcrumbs"),
html.Div(id="dir-browser-recent"),
html.Div(id="dir-browser-datasets"),
html.Div(id="dir-browser-body"),
```

Add to `build_layout`:

```python
dcc.Store(id="dataset-browser-candidate", storage_type="memory"),
dcc.Store(
    id="recent-datasets",
    storage_type="local",
    data=[],
),
```

Change the primary footer action text from `选择当前目录` to `使用所选数据集`, disabled until a candidate exists.

- [ ] **Step 4: Render breadcrumbs, dataset cards and recent cards**

Create focused render helpers:

```python
def _render_dataset_cards(datasets):
    if not datasets:
        return html.Div(
            "当前目录未发现 ReacNetGenerator 数据集，可继续进入子目录。",
            className="rs-empty",
        )
    return [
        dbc.Button(
            [
                html.Strong(item["label"]),
                html.Span(
                    f"文件完整度 {item['completeness']}",
                    className="rs-dataset-meta",
                ),
                html.Span(
                    " · ".join(
                        f"{key}: {value}"
                        for key, value in item["index_states"].items()
                    ),
                    className="rs-dataset-index-states",
                ),
            ],
            id={
                "type": "dir-browser-dataset",
                "base": item["base"],
            },
            className="rs-dataset-card",
        )
        for item in datasets
    ]
```

Render recent entries separately and mark missing paths unavailable. Do not mutate the stored list while rendering it.

- [ ] **Step 5: Refactor the consolidated browser callback**

The callback dispatch order is:

1. cancel;
2. open;
3. path input / go;
4. breadcrumb;
5. subdirectory;
6. parent;
7. dataset card;
8. recent dataset;
9. use selected dataset.

Every navigation response replaces the browser snapshot and sets the candidate only when exactly one dataset is present. Selecting a candidate does not mutate `app-store`.

- [ ] **Step 6: Add responsive styles**

Add CSS for a two-section modal (`发现的数据集` above `子目录`), compact dataset cards, wrapping breadcrumbs, and a scrollable directory list. At widths below 768 px, cards and path controls stack vertically.

- [ ] **Step 7: Run Dash smoke tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py
```

Expected: all browser layout and navigation tests pass.

- [ ] **Step 8: Commit**

```bash
git add \
  scripts/webapp_dash/app.py \
  scripts/webapp_dash/callbacks.py \
  scripts/webapp_dash/assets/app.css \
  tests/test_dash_smoke.py
git commit -m "feat: add dataset-first server browser"
```

---

### Task 4: Replace scan/base/apply with one candidate load

**Files:**
- Modify: `scripts/webapp_dash/app.py:1345-1468`
- Modify: `scripts/webapp_dash/callbacks.py:397-575,650-730,2168-2210`
- Modify: `tests/test_dash_smoke.py`
- Modify: `tests/test_preparation_management.py`

**Interfaces:**
- Consumes: `dataset-browser-candidate = {folder, base, label}`
- Preserves internal `scan_dataset(folder, base=base)`
- Produces recent record: `{folder, base, label, loaded_at}`
- Removes visible copy: `运行组 (base)` and footer action `扫描`
- Renames primary action: `加载数据集`

- [ ] **Step 1: Write failing one-click-load tests**

Assert the rendered modal has no visible `运行组 (base)` label and no `data-scan-btn`. Exercise the apply callback with a selected candidate:

```python
candidate = {
    "folder": str(tmp_path),
    "base": str(tmp_path / "rp3.lammpstrj"),
    "label": "rp3.lammpstrj",
}
```

Expected results:

- one click updates `app-store`;
- the modal closes only after a successful scan;
- the recent list contains the loaded candidate first;
- cancelling or a vanished candidate preserves the prior `app-store`;
- a single candidate from manual path input is selected automatically;
- a pasted full dataset prefix resolves to its parent directory and exact candidate;
- multiple candidates require explicit selection.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dash_smoke.py \
  tests/test_preparation_management.py
```

Expected: failures because the old scan/base controls remain.

- [ ] **Step 3: Simplify the management modal**

Replace the top form with:

```python
html.Div(id="data-candidate-summary"),
dbc.Button(
    "选择其他数据集",
    id="data-pick-btn",
    color="secondary",
),
html.Details(
    [
        html.Summary("手动输入服务器路径"),
        dbc.Input(
            id="data-folder-input",
            debounce=True,
            placeholder="输入目录或数据集公共前缀",
        ),
    ]
),
```

Keep `data-rungroup` only as a hidden internal component until all legacy callback dependencies are migrated. Remove its visible label and remove `data-scan-btn`. Change `data-apply-btn` text to `加载数据集`.

For the advanced manual path input, call `svc.resolve_dataset_input(value)`, browse
the returned folder, and select only the returned exact `preferred_base`. A directory
with multiple candidates remains unselected.

- [ ] **Step 4: Drive status from the candidate**

When `dataset-browser-candidate` changes, call:

```python
status = svc.scan_dataset(
    candidate["folder"],
    base=candidate["base"],
)
```

Render artifacts and preparation status immediately, but do not update `app-store`. If the candidate vanishes, show an error and keep the load button disabled.

- [ ] **Step 5: Apply atomically and update recents**

On `data-apply-btn`, rescan the candidate immediately before applying. Only on success:

```python
record = {
    "folder": candidate["folder"],
    "base": dataset["selected_base"],
    "label": dataset["label"],
    "loaded_at": int(time.time()),
}
recent = svc.normalise_recent_datasets(
    [record, *(recent_records or [])]
)
```

Return the new `app-store`, topbar labels, recent records and closed modal in one successful callback response. On error, return the previous store and keep the modal open.

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dash_smoke.py \
  tests/test_preparation_management.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add \
  scripts/webapp_dash/app.py \
  scripts/webapp_dash/callbacks.py \
  tests/test_dash_smoke.py \
  tests/test_preparation_management.py
git commit -m "refactor: load discovered datasets in one step"
```

---

### Task 5: Reset stale species workflow selections

**Files:**
- Modify: `scripts/webapp_dash/callbacks.py:727-840`
- Modify: `tests/test_dash_smoke.py`
- Modify: `tests/test_workflow_services.py`

**Interfaces:**
- Preserves: `initial_workflow_store()`
- New search behavior: `workflow-species-search` clears `species`, `channel`, `event` and sets `current_step=1`
- New grid behavior: every search returns `workflow-species-grid.selected_rows=[]`

- [ ] **Step 1: Write a failing H2-to-CH3 callback regression test**

Start with:

```python
workflow = {
    "dataset_key": "rp3",
    "current_step": 2,
    "species": {"formula": "H2", "smiles": "[H][H]"},
    "channel": {"reaction_smiles": "[H]+[H]->[H][H]"},
    "event": {"event_id": "old"},
    "validations": [],
}
```

Trigger `workflow-species-search` with query `CH3`. Assert:

```python
assert response["workflow-species-grid"]["selected_rows"] == []
assert response["workflow-store"]["data"]["species"] is None
assert response["workflow-store"]["data"]["channel"] is None
assert response["workflow-store"]["data"]["event"] is None
assert response["workflow-store"]["data"]["current_step"] == 1
```

Then select row zero and assert the choice text changes to CH3 before the confirm button becomes enabled.

- [ ] **Step 2: Run the regression test to verify RED**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dash_smoke.py \
  tests/test_workflow_services.py
```

Expected: the workflow store still contains H2 after the CH3 search.

- [ ] **Step 3: Reset grid selection on every executed search**

Add `Output("workflow-species-grid", "selected_rows")` to `_search_workflow_catalog` and return `[]` on success, empty results and errors.

- [ ] **Step 4: Reset workflow state on the search button**

Add `Input("workflow-species-search", "n_clicks")` to `_advance_workflow` before the grid selection input. Handle it before `workflow-species-grid`:

```python
if triggered == "workflow-species-search":
    state.update(
        {
            "species": None,
            "channel": None,
            "event": None,
            "current_step": 1,
            "validation_message": "",
        }
    )
elif triggered == "workflow-species-grid":
    row = chosen(species_selected, species_rows)
    if row:
        state.update(
            {
                "species": row,
                "channel": None,
                "event": None,
                "current_step": 1,
            }
        )
```

Do not clear the current selection when the user merely edits the query input without executing a search.

- [ ] **Step 5: Run workflow tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dash_smoke.py \
  tests/test_workflow_services.py
```

Expected: all tests pass and the H2-to-CH3 regression is covered.

- [ ] **Step 6: Commit**

```bash
git add \
  scripts/webapp_dash/callbacks.py \
  tests/test_dash_smoke.py \
  tests/test_workflow_services.py
git commit -m "fix: reset workflow choices on species search"
```

---

### Task 6: Document and validate the second-stage UX

**Files:**
- Modify: `README.md`
- Modify: `tests/test_online_index_contract.py`

**Interfaces:**
- Documents: dataset-first browser, automatic base handling, recent datasets, manual path fallback
- Verifies: browser discovery remains source-read-free

- [ ] **Step 1: Add the user workflow documentation**

Replace the old directory/base explanation with:

```markdown
在“管理数据”中点击“选择其他数据集”。服务器浏览器会标记当前目录中的
ReacNetGenerator 数据集；目录中只有一个数据集时自动选中，存在多个数据集时
按文件名前缀列出候选。选择后点击一次“加载数据集”即可。

`base` 是同组 RNG 输出的内部公共前缀，通常无需手动填写。手动路径输入保留在
“手动输入服务器路径”中，所有路径仍受 `REACNET_SCOPE_ALLOWED_ROOTS` 限制。
```

- [ ] **Step 2: Run the milestone verification**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_dataset_discovery.py \
  tests/test_directory_browser.py \
  tests/test_dash_smoke.py \
  tests/test_preparation_management.py \
  tests/test_workflow_services.py \
  tests/test_online_index_contract.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q reacnet_scope rng_tools scripts tests
git diff --check
git status --short ref_data
```

Expected:

- all focused and full tests pass;
- compileall and `git diff --check` print nothing;
- `git status --short ref_data` prints nothing;
- guarded tests prove discovery and browsing never open large source artifacts.

- [ ] **Step 3: Perform the RP3 manual acceptance**

With Dash launched using the prepared cache:

1. Open `管理数据`.
2. Navigate from an allowed root to `ref_data/rng-test-rp3-0523`.
3. Confirm `rp3.lammpstrj` is auto-selected and “运行组” is absent.
4. Click `加载数据集` once.
5. Confirm event and trajectory status are ready.
6. Select H2, advance to channels, return to step 1, search CH3.
7. Confirm the choice footer is empty until CH3 is explicitly selected.
8. Confirm selecting CH3 updates the footer and opens CH3 channels.

- [ ] **Step 4: Commit**

```bash
git add README.md tests/test_online_index_contract.py
git commit -m "docs: explain dataset-first loading"
```

---

## Final Review Gate

After Task 6:

1. Request a read-only code review over the complete second-stage commit range.
2. Fix every Critical and Important finding with a failing regression test first.
3. Re-run the complete milestone verification after the final fix commit.
4. Do not merge, push or delete the branch without explicit user direction.
