# Event-Evidence Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish dependency contracts and replace online scans of ReacNetGenerator event CSVs with a resumable, read-only-at-runtime SQLite evidence index.

**Architecture:** ReacNetGenerator remains the only event detector. An offline builder streams its `reactionevent.csv` and `molecules.csv` in timestep order, joins adjacent molecule frames, and atomically publishes one dataset-local SQLite index. Dash, Python, and later pathway code consume a stable read-only store; they never fall back to scanning source CSVs.

**Tech Stack:** Python 3.10+, SQLite, NetworkX 3.2–3.x, pytest, existing ReacNet Scope cache/index infrastructure.

## Global Constraints

- Preserve all unrelated working-tree changes and never modify files under `ref_data/`.
- Do not run `pip install`, `uv sync`, Conda, Apt, or another installer. Dependency installation and lock refresh are human-owned gates.
- Do not infer bonds or reactions from trajectory coordinates. Consume only ReacNetGenerator-authored event and molecule outputs.
- Only `reacnet-scope-prepare` may build, rebuild, migrate, or clear the event index.
- Online services open SQLite with `mode=ro` and `PRAGMA query_only=ON`; missing/stale indexes return an actionable state and never scan source CSVs.
- Preserve reaction-side multiplicity and existing RNG zero-based to trajectory one-based atom-ID conversion.
- Commit only the files named by the current task.

## Human-Owned Environment Gate

Before Task 1, the implementation worker must pause and ask the human operator to prepare the environment. Suggested commands:

```bash
cd /home/huangchen/cal_proc/reacnet-scope
uv pip install "networkx>=3.2,<4" "pytest>=8,<9"
```

The worker may run only these read-only checks afterward:

```bash
.venv/bin/python -c "import networkx; major=int(networkx.__version__.split('.')[0]); assert 3 <= major < 4; print(networkx.__version__)"
.venv/bin/python -m pytest --version
```

Expected: both commands exit `0`, NetworkX reports a 3.x version, and pytest reports its version. If either command fails, stop and return the exact failed check to the human; do not install or repair the environment.

---

### Task 1: Dependency and canonical reaction-key contracts

**Files:**
- Create: `tests/test_dependency_contract.py`
- Modify: `pyproject.toml`
- Modify: `reacnet_scope/rng_events.py`
- Modify: `tests/test_rng_event_outputs.py`
- Human refreshes after the edit: `uv.lock`

**Interfaces:**
- Produces: required dependency `networkx>=3.2,<4`
- Produces: optional extra `trajectory = ["ase>=3.23,<4"]`
- Produces: `canonical_reaction_key(reactants, products) -> str`
- Preserves: `reaction_key(reactant, product) -> tuple[tuple[str, ...], tuple[str, ...]]`

- [ ] **Step 1: Write failing packaging and key tests**

```python
# tests/test_dependency_contract.py
from pathlib import Path
import tomllib


def test_graph_and_trajectory_dependency_contracts() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert "networkx>=3.2,<4" in data["project"]["dependencies"]
    assert data["project"]["optional-dependencies"]["trajectory"] == ["ase>=3.23,<4"]
```

```python
# append to tests/test_rng_event_outputs.py
from reacnet_scope.rng_events import canonical_reaction_key


def test_canonical_reaction_key_sorts_each_side_and_preserves_multiplicity() -> None:
    assert canonical_reaction_key(("[O]", "[H]", "[H]"), ("[H][O][H]",)) == (
        "[H]+[H]+[O]->[H][O][H]"
    )
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dependency_contract.py tests/test_rng_event_outputs.py
```

Expected: failures because NetworkX/trajectory declarations and `canonical_reaction_key` do not exist.

- [ ] **Step 3: Add the declarations and minimal canonicalizer**

Add NetworkX to `[project].dependencies` and create `[project.optional-dependencies].trajectory`. Implement:

```python
def canonical_reaction_key(
    reactants: Iterable[str],
    products: Iterable[str],
) -> str:
    left = "+".join(sorted(str(item).strip() for item in reactants if str(item).strip()))
    right = "+".join(sorted(str(item).strip() for item in products if str(item).strip()))
    return f"{left}->{right}"
```

Make cached event rows publish this string in `row["reaction_key_text"]` while retaining the existing tuple-valued `row["reaction_key"]` until Task 4 migrates its callers.

- [ ] **Step 4: Ask the human to refresh dependency metadata**

The worker must not run these commands. Ask the human to run:

```bash
cd /home/huangchen/cal_proc/reacnet-scope
uv lock
uv sync --extra web
```

After confirmation, run:

```bash
.venv/bin/python -c "import networkx; print(networkx.__version__)"
git diff --check -- pyproject.toml uv.lock reacnet_scope/rng_events.py tests/test_dependency_contract.py tests/test_rng_event_outputs.py
```

Expected: NetworkX imports and `git diff --check` prints nothing.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_dependency_contract.py tests/test_rng_event_outputs.py
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock reacnet_scope/rng_events.py tests/test_dependency_contract.py tests/test_rng_event_outputs.py
git commit -m "build: declare graph and trajectory dependencies"
```

---

### Task 2: Dataset-local event-index path and read-only store

**Files:**
- Create: `reacnet_scope/event_index.py`
- Create: `tests/test_event_evidence_index.py`
- Modify: `reacnet_scope/indexes.py`

**Interfaces:**
- Produces: `EVENT_EVIDENCE_SCHEMA_VERSION = 1`
- Produces: `DatasetPaths.event_index`
- Produces: `event_evidence_index_path(reactionevent_file) -> Path`
- Produces:
  `EventEvidenceStore.status(reactionevent_file, molecules_file) -> dict[str, Any]`
- Produces:
  `EventEvidenceStore.open_required(reactionevent_file, molecules_file) -> dict[str, Any]`
- Produces:
  `EventEvidenceStore.query_events(reactionevent_file, molecules_file, reaction_key, *, limit, offset=0) -> dict[str, Any]`
- Produces:
  `EventEvidenceStore.reaction_summary(reactionevent_file, molecules_file, reaction_keys) -> dict[str, dict[str, Any]]`
- Produces singleton: `EVENT_EVIDENCE_STORE`

- [ ] **Step 1: Write failing path, state, pagination, and read-only tests**

Use a fixture with two event rows for the same reaction and three molecule timesteps. The core assertions are:

```python
def test_event_store_publishes_dataset_local_index_and_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)

    built = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    assert built["state"] == "ready"
    assert Path(built["index_path"]).name == "events.sqlite3"

    first = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent), str(molecules), "[H]+[O]->[H][O]", limit=1
    )
    second = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent), str(molecules), "[H]+[O]->[H][O]", limit=1, offset=1
    )
    assert first["total"] == 2
    assert first["rows"][0]["event_id"] != second["rows"][0]["event_id"]
    assert first["rows"][0]["atom_id_list"] == [1, 2]
```

Also assert:

- `status` distinguishes `missing_source`, `missing`, `building`, `stale`, `invalid`, and `ready`;
- `open_required` connects with `PRAGMA query_only == 1`;
- a source size/mtime change produces `stale`;
- deleting the `reaction_summary` table produces `invalid`;
- `reaction_summary([known, unknown])` returns the known row and omits the unknown key.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_event_evidence_index.py
```

Expected: import errors because `event_index.py`, `DatasetPaths.event_index`, and the store do not exist.

- [ ] **Step 3: Extend the cache-layout contract**

In `indexes.py`, centralize suffix stripping and include, longest first:

```python
DATASET_SUFFIXES = (
    ".reactionevent.csv",
    ".molecules.csv",
    ".reactionabcd",
    ".species",
    ".route",
    ".table",
)
```

Add `event_index: Path` to `DatasetPaths` and resolve it as `cache_dir / "events.sqlite3"`. Do not change existing route or trajectory paths.

- [ ] **Step 4: Implement the schema and strict readers**

Create these tables:

```sql
CREATE TABLE meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE events(
    event_id TEXT PRIMARY KEY,
    reaction_key TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    timestep_index INTEGER NOT NULL,
    before_timestep INTEGER NOT NULL,
    after_timestep INTEGER NOT NULL,
    reactant_text TEXT NOT NULL,
    product_text TEXT NOT NULL,
    atom_ids_json TEXT NOT NULL,
    reactant_bonds_json TEXT NOT NULL,
    product_bonds_json TEXT NOT NULL,
    association_status TEXT NOT NULL
);
CREATE INDEX events_by_reaction
    ON events(reaction_key, timestep_index, source_row, event_id);
CREATE TABLE reaction_summary(
    reaction_key TEXT PRIMARY KEY,
    total_events INTEGER NOT NULL,
    matched_events INTEGER NOT NULL,
    distinct_intervals INTEGER NOT NULL
);
```

Metadata must include both source paths/sizes/mtimes, schema version, dataset
ID, build state, source offsets, completed interval, event count, reaction
type count, molecule frame count,
`available_intervals=max(molecule_frame_count-1, 0)`, and update time.

`query_events` accepts source paths so it can validate signatures before opening. It returns the existing UI row shape plus `total`, `limit`, `offset`, `source_signatures`, and `evidence_status="evidence_linked"`. Convert JSON atom IDs and bonds at the boundary.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_event_evidence_index.py
```

Expected: path, state, read-only, pagination, and corruption tests pass.

- [ ] **Step 6: Commit**

```bash
git add reacnet_scope/indexes.py reacnet_scope/event_index.py tests/test_event_evidence_index.py
git commit -m "feat: add event evidence index contract"
```

---

### Task 3: Streaming, resumable event-index builder

**Files:**
- Modify: `reacnet_scope/event_index.py`
- Modify: `reacnet_scope/rng_events.py`
- Modify: `tests/test_event_evidence_index.py`

**Interfaces:**
- Consumes: sorted RNG `reactionevent.csv` and `molecules.csv`
- Reuses: `MoleculeRow`, `MoleculeComponent`, changed-component association
- Produces: checkpointed `EventEvidenceStore.build(...)`

- [ ] **Step 1: Write failing normalization, resume, and atomic-publication tests**

Add tests covering:

```python
def test_event_builder_resumes_after_a_committed_interval_without_duplicates(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    reactionevent, molecules = write_rng_fixture(tmp_path)

    def interrupt(update):
        if update.get("phase") == "checkpoint_event_index":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        EVENT_EVIDENCE_STORE.build(
            str(reactionevent), str(molecules), progress_callback=interrupt
        )
    assert EVENT_EVIDENCE_STORE.status(
        str(reactionevent), str(molecules)
    )["state"] == "building"

    result = EVENT_EVIDENCE_STORE.build(str(reactionevent), str(molecules))
    rows = EVENT_EVIDENCE_STORE.query_events(
        str(reactionevent), str(molecules), "A+A->B", limit=100
    )["rows"]
    assert result["resumed"] is True
    assert len({row["event_id"] for row in rows}) == len(rows)
```

Also assert:

- `A + A -> B` is stored as `A+A->B`;
- an unmatched complex component remains indexed with the compatibility value
  `association_status="unresolved_hmm_timeline"` and an empty atom list;
- the published `events.sqlite3` does not exist during the first checkpoint;
- `os.replace(building_path, published_path)` occurs only after `build_state=ready`;
- source event rows or molecule frames that move backward raise `RngEventDataError`.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_event_evidence_index.py
```

Expected: resume, atomic publication, and ordering tests fail.

- [ ] **Step 3: Expose association helpers without changing semantics**

Rename `_changed_components` to `changed_components` and retain `_changed_components = changed_components` as a compatibility alias. Add a helper that maps one event row and its adjacent molecule frames to the existing UI event record. Reuse the current stable ID formula:

```python
digest = hashlib.sha1(
    f"{timestep_index}|{source_row}|{','.join(map(str, atom_ids))}".encode("utf-8")
).hexdigest()[:12]
event_id = f"rngevt_{timestep_index}_{digest}"
```

- [ ] **Step 4: Implement a two-stream interval join**

Read both CSVs in binary mode so checkpoints use byte offsets. RNG CSV records are single-line records; validate and reject embedded-newline records instead of guessing.

For each event interval:

1. group all event rows with the same `Timestep_Index`;
2. advance the molecule stream to frame indices `i` and `i + 1`;
3. compute changed molecule components once;
4. associate each event row by normalized, multiplicity-preserving sides;
5. start a SQLite transaction, insert the interval rows, update summaries, and write the next event offset plus the byte offset of frame `i + 1`;
6. commit, then emit `phase="checkpoint_event_index"`.

On resume, seek to the committed offsets, re-read frame `i + 1` as the next before-frame, and continue. A callback exception or `KeyboardInterrupt` leaves the `.building` database intact. A source-signature mismatch discards only the incompatible `.building` index, never a published index or source CSV.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_event_evidence_index.py tests/test_rng_event_outputs.py
```

Expected: all event builder and compatibility tests pass.

- [ ] **Step 6: Commit**

```bash
git add reacnet_scope/event_index.py reacnet_scope/rng_events.py tests/test_event_evidence_index.py
git commit -m "feat: stream and resume event evidence builds"
```

---

### Task 4: Unified preparation, manifest, and safe clearing

**Files:**
- Modify: `reacnet_scope/prepare.py`
- Modify: `reacnet_scope/indexes.py`
- Modify: `tests/test_preparation_management.py`
- Modify: `tests/test_event_evidence_index.py`

**Interfaces:**
- Produces CLI form:
  `reacnet-scope-prepare /absolute/path/to/case --event-only`
- Extends: `--rebuild event|all`, `--clear event|all`
- Upgrades: manifest version `2`, `indexes.event`
- Preserves: read compatibility with manifest version `1`

- [ ] **Step 1: Write failing preparation tests**

Test default preparation with both RNG event outputs, explicit `--event-only`, and safe clear. Mock unrelated stores so the test remains focused:

```python
result = prepare.main([str(tmp_path), "--event-only"])
assert result == 0
manifest = json.loads(resolve_dataset_paths(tmp_path, "run.lammpstrj").manifest.read_text())
assert manifest["manifest_version"] == 2
assert manifest["indexes"]["event"]["state"] == "ready"

source_bytes = reactionevent.read_bytes(), molecules.read_bytes()
assert prepare.main([str(tmp_path), "--clear", "event"]) == 0
assert (reactionevent.read_bytes(), molecules.read_bytes()) == source_bytes
```

Assert default selection builds the event index only when both files exist, and that an old version-1 manifest remains readable by `scan_dataset`.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_preparation_management.py tests/test_event_evidence_index.py
```

Expected: parser rejects `event`, manifest lacks the event index, or clear rejects the kind.

- [ ] **Step 3: Wire the event store into preparation**

Add event capacity estimation based on both CSV sizes, add `--event-only`, include `event` in clear/rebuild choices, and select event preparation by default when both files exist. `--status` must not build.

Use a dedicated `EventEvidenceStore.clear(reactionevent_file, molecules_file)` that derives its target under the configured cache root and acquires the same build lock. Do not broaden generic `clear_index` to accept arbitrary paths.

Manifest version 2 must retain existing keys and add:

```python
{
  "indexes": {
    "event": {"state": "ready", "index_path": str(paths.event_index)}
  },
  "settings": {"path": str(paths.cache_dir / "dataset-settings.json")}
}
```

The settings path is reserved now and implemented in Plan 4; its absence is valid.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_preparation_management.py tests/test_event_evidence_index.py
```

Expected: event-only/default/status/clear and manifest compatibility tests pass.

- [ ] **Step 5: Commit**

```bash
git add reacnet_scope/prepare.py reacnet_scope/indexes.py tests/test_preparation_management.py tests/test_event_evidence_index.py
git commit -m "feat: prepare event evidence offline"
```

---

### Task 5: Migrate Dash services to indexed event reads

**Files:**
- Modify: `scripts/webapp_dash/services.py`
- Modify: `tests/test_rng_event_outputs.py`
- Modify: `tests/test_online_index_contract.py`
- Modify: `README.md`

**Interfaces:**
- Changes: `locate_rng_events(...)` reads `EVENT_EVIDENCE_STORE`
- Preserves: returned row fields consumed by current callbacks
- Produces degradation command:
  `f"reacnet-scope-prepare {shlex.quote(str(Path(reactionevent_file).parent))} --event-only"`

- [ ] **Step 1: Write failing no-source-scan and degradation tests**

Prepare an event index, then monkeypatch `builtins.open` to fail if either source CSV is opened during `locate_rng_events`. Assert the indexed query still succeeds.

Without an index, assert:

```python
with pytest.raises(svc.ServiceError) as error:
    svc.locate_rng_events(artifacts, "[H] + [O] -> [H][O]")
assert error.value.reason == "event_index_not_ready"
assert "reacnet-scope-prepare" in error.value.message
assert "--event-only" in error.value.message
```

Also retain an explicit unit test for the legacy in-memory `query_rng_events`; it remains a small-file/offline compatibility helper, not an online fallback.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_rng_event_outputs.py tests/test_online_index_contract.py
```

Expected: the service opens source CSVs or succeeds without the required index.

- [ ] **Step 3: Replace the online service path**

Normalize user input through `reaction_key(...)` plus `canonical_reaction_key(...)`, call `EVENT_EVIDENCE_STORE.query_events`, and translate `IndexNotReadyError`, `IndexStaleError`, and `IndexInvalidError` into distinct `ServiceError.reason` values.

Update readiness reporting so event search is ready only when the event index is ready. Source CSV presence alone means `needs_preparation`, not `ready`.

Document:

```bash
export REACNET_SCOPE_CACHE_DIR=/path/to/fast-cache
reacnet-scope-prepare /path/to/case --event-only
```

- [ ] **Step 4: Verify focused and complete suites**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_rng_event_outputs.py tests/test_online_index_contract.py tests/test_preparation_management.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q reacnet_scope rng_tools scripts tests
git diff --check
```

Expected: all tests pass, compileall exits `0`, and `git diff --check` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add scripts/webapp_dash/services.py tests/test_rng_event_outputs.py tests/test_online_index_contract.py README.md
git commit -m "refactor: serve reaction events from offline evidence index"
```

## Milestone Acceptance

- `networkx` and `trajectory` dependency boundaries are declared, but no automated installer was run.
- Event index publication is atomic, resumable, source-signature validated, and safely clearable.
- Default preparation builds the event index when both RNG event files exist.
- Dash event queries perform no source-CSV scan and retain the current event-row contract.
- The following command is green:

```bash
.venv/bin/python -m pytest -q tests/test_dependency_contract.py tests/test_event_evidence_index.py tests/test_rng_event_outputs.py tests/test_online_index_contract.py tests/test_preparation_management.py
```

Plan 2 may start only after this milestone is committed and the human confirms the NetworkX import check.
