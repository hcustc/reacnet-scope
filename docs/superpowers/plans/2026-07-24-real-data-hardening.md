# Real-Data Acceptance and Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the complete evidence→pathway→network→trajectory workflow against the real `rng-test-rp3-0523` dataset, harden bounded I/O and deterministic behavior, and publish operator/interoperability documentation.

**Architecture:** Repository-local real data becomes a read-only integration oracle. Acceptance tests prepare indexes only under a temporary cache, exercise public Python/service/CLI interfaces, and verify source hashes are unchanged. Performance tests use structural budgets—SQLite query plans, byte-range guards, expansion caps, and payload limits—instead of machine-dependent wall-clock thresholds.

**Tech Stack:** Python 3.10+, pytest markers/fixtures, SQLite query-plan inspection, SHA-256 source snapshots, NetworkX, optional ASE, existing Dash/service/CLI layers.

## Global Constraints

- Complete Plans 1–4 first.
- Never edit, regenerate, normalize, or delete any file under `ref_data/rng-test-rp3-0523`.
- Real-data tests set `REACNET_SCOPE_CACHE_DIR` to `tmp_path`; no prepared artifact is written beside the source dataset.
- Do not install dependencies. If NetworkX, ASE, pytest, or the prepared `.venv` is missing, stop and request the human-owned commands from Plans 1 and 4.
- Do not launch OVITO in automated tests. ASE validates file syntax; OVITO opening is a documented human acceptance check.
- Use structural performance assertions, not flaky elapsed-time limits.
- Preserve the legacy static Web application. It may retain its private parser, but the new Dash workflow must not call it.
- No source file may be opened by an online indexed event query; trajectory requests may open only exact indexed ranges.
- Preserve unrelated working-tree changes and commit only task-scoped files.

## Human-Owned Final Environment Gate

Ask the human to confirm that the environment from Plans 1 and 4 is active. The worker may run:

```bash
.venv/bin/python -c "import networkx, ase, pytest; print(networkx.__version__, ase.__version__, pytest.__version__)"
```

Expected: NetworkX 3.x, ASE 3.x, and pytest 8.x (or a later compatible pytest chosen by the human). If imports fail, return these human-run commands and pause:

```bash
uv sync --extra web --extra trajectory
uv pip install "pytest>=8,<9"
```

---

### Task 1: Real-data fixtures, markers, and immutable-source guard

**Files:**
- Create: `tests/realdata/conftest.py`
- Create: `tests/realdata/test_real_event_acceptance.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces fixture: `rp3_dataset -> dict[str, Path]`
- Produces fixture: `rp3_prepared -> dict[str, str]`
- Adds marker: `realdata`
- Adds helper: source signature/hash snapshot assertion

- [ ] **Step 1: Write failing fixture and source-immutability test**

Resolve the dataset relative to the repository root:

```python
DATASET = Path(__file__).resolve().parents[2] / "ref_data" / "rng-test-rp3-0523"
BASE = DATASET / "rp3.lammpstrj"
```

Fixture artifacts:

```python
{
    "base": BASE,
    "reaction": Path(f"{BASE}.reactionabcd"),
    "table": Path(f"{BASE}.table"),
    "reactionevent": Path(f"{BASE}.reactionevent.csv"),
    "molecules": Path(f"{BASE}.molecules.csv"),
    "trajectory": BASE,
}
```

Snapshot every regular file as `(size, mtime_ns, sha256)`. Run event and trajectory preparation under `tmp_path / "cache"`, then assert the complete source snapshot is identical.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q -m realdata tests/realdata/test_real_event_acceptance.py
```

Expected: fixture/marker/test is incomplete until added; after the test file exists, failures identify any preparation API mismatch.

- [ ] **Step 3: Register markers and implement fixtures**

Add:

```toml
[tool.pytest.ini_options]
markers = [
  "realdata: exercises repository-local ReacNetGenerator reference data",
  "trajectory: requires the human-installed ASE trajectory extra",
]
```

`rp3_prepared` must:

1. set a temporary cache with `monkeypatch.setenv`;
2. call `EVENT_EVIDENCE_STORE.build`;
3. call `TRAJECTORY_INDEX_STORE.build`;
4. return string artifact paths and index statuses;
5. register no finalizer that touches source data.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q -m realdata tests/realdata/test_real_event_acceptance.py
git add pyproject.toml tests/realdata/conftest.py tests/realdata/test_real_event_acceptance.py
git commit -m "test: add immutable real ReacNetGenerator fixture"
```

Expected: source snapshot remains byte-identical.

---

### Task 2: Event-index and local-trajectory acceptance oracle

**Files:**
- Modify: `tests/realdata/test_real_event_acceptance.py`
- Modify only if a test exposes a defect: `reacnet_scope/event_index.py`, `reacnet_scope/event_view.py`, `reacnet_scope/trajectory.py`

**Interfaces:**
- Validates: 263 reaction types, 3,406 events
- Validates: H2O dissociation query returns 100 rows, 99 atom-associated
- Validates: selected event exposes five frames and one broken O–H bond

- [ ] **Step 1: Add failing real event-count/query assertions**

```python
@pytest.mark.realdata
def test_rp3_event_oracle(rp3_prepared) -> None:
    status = EVENT_EVIDENCE_STORE.status(
        rp3_prepared["reactionevent"], rp3_prepared["molecules"]
    )
    assert status["reaction_types"] == 263
    assert status["event_count"] == 3406

    result = EVENT_EVIDENCE_STORE.query_events(
        rp3_prepared["reactionevent"],
        rp3_prepared["molecules"],
        "[H][O][H]->[H]+[H][O]",
        limit=100,
    )
    assert result["total"] == 100
    assert len(result["rows"]) == 100
    assert sum(row["association_status"] == "matched" for row in result["rows"]) == 99
```

The 100-row expectation refers to the complete matching set in this fixture, not only a page truncation. Assert `result["has_more"] is False`.

- [ ] **Step 2: Add failing five-frame/broken-bond assertion**

Use the first matched event in stable `(timestep_index, source_row, event_id)` order and mapping:

```python
mapping = {"1": "C", "2": "H", "3": "O"}
view = build_event_view(
    rp3_prepared["trajectory"],
    selected,
    scope="participants",
    before_frames=3,
    after_frames=3,
    atom_type_map=mapping,
)
assert [frame["timestep"] for frame in view["frames"]] == [
    20000000, 20010000, 20020000, 20030000, 20040000
]
assert any(
    {bond["element1"], bond["element2"]} == {"O", "H"}
    for bond in view["bond_changes"]["broken"]
)
```

Also build the ZIP, read local LAMMPS/ExtXYZ members with ASE, and assert both have five frames.

- [ ] **Step 3: Verify RED and fix only demonstrated defects**

```bash
.venv/bin/python -m pytest -q -m realdata tests/realdata/test_real_event_acceptance.py
```

Expected before fixes: any mismatch is a concrete defect in normalization, summary counts, frame selection, mapping, or bond labeling. Apply the smallest production change needed; do not change oracle values to match incorrect output.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q -m realdata tests/realdata/test_real_event_acceptance.py
git add tests/realdata/test_real_event_acceptance.py
git add reacnet_scope/event_index.py reacnet_scope/event_view.py reacnet_scope/trajectory.py
git commit -m "test: validate real event and trajectory evidence"
```

Before staging production files, omit any that did not change.

---

### Task 3: Candidate-path and dual-network real-data acceptance

**Files:**
- Create: `tests/realdata/test_real_pathway_network.py`
- Modify only if tests expose a defect: `rng_tools/pathways.py`, `rng_tools/mechanism_graph.py`, `scripts/webapp_dash/services.py`

**Interfaces:**
- Validates chain: `C2H2 -> C2H3 -> C2H4`
- Validates mechanism and observation schema separation
- Validates deterministic repeated results

- [ ] **Step 1: Write the failing hydrogen-addition chain test**

Use exact fixture SMILES:

```python
acetylene = "[H][C][C][H]"
vinyl = "[H][C][C]([H])[H]"
ethylene = "[H][C]([H])[C]([H])[H]"

result = svc.find_pathways(
    artifacts,
    acetylene,
    direction="downstream",
    max_depth=2,
    max_branches=20,
    max_paths=200,
    max_expansions=5000,
    min_net_tp=1,
    min_directionality=0.05,
)
assert any(path["species"] == [acetylene, vinyl, ethylene] for path in result["paths"])
```

For the matching path assert formula chain `["C2H2", "C2H3", "C2H4"]`, step net TP `[1, 1]`, score version, and nonnegative evidence metrics.

- [ ] **Step 2: Write failing network-semantics assertions**

```python
mechanism = svc.build_mechanism_elements(
    artifacts, anchor_smiles=acetylene, direction="both", max_depth=2
)
observation = svc.build_observation_elements(artifacts, min_count=1)

assert mechanism["schema_version"] == "reacnet-scope/mechanism-network/v1"
assert mechanism["network_semantics"] == "mechanism"
assert observation["network_semantics"] == "event_transfer"
assert observation["evidence_level"] == "aggregate_observation"
assert mechanism["elements"] != observation["elements"]
```

Run each service twice and compare canonical JSON bytes to prove deterministic order.

- [ ] **Step 3: Verify RED and fix only demonstrated defects**

```bash
.venv/bin/python -m pytest -q -m realdata tests/realdata/test_real_pathway_network.py
```

Expected before fixes: any failure identifies a deterministic search, reversible-pair, evidence-key, or schema-label defect.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q -m realdata tests/realdata/test_real_pathway_network.py
git add tests/realdata/test_real_pathway_network.py
git add rng_tools/pathways.py rng_tools/mechanism_graph.py scripts/webapp_dash/services.py
git commit -m "test: validate real candidate path and network semantics"
```

Omit unchanged production files before committing.

---

### Task 4: Structural performance and guarded-I/O contracts

**Files:**
- Create: `tests/test_evidence_performance_contract.py`
- Modify: `tests/test_online_index_contract.py`
- Modify only if tests expose a defect: `reacnet_scope/event_index.py`, `reacnet_scope/trajectory.py`, `rng_tools/pathways.py`

**Interfaces:**
- Validates: indexed event query uses the reaction index
- Validates: online source CSVs are never opened
- Validates: trajectory bytes read equal requested frame ranges
- Validates: path expansions and mechanism nodes honor hard caps

- [ ] **Step 1: Write failing SQLite query-plan tests**

Add the read-only diagnostic
`EventEvidenceStore.explain_query_events(reactionevent_file, molecules_file, reaction_key) -> dict[str, list[tuple]]`.
It returns separate `count_plan` and `page_plan` rows. Assert:

```python
detail = " ".join(row[-1] for row in plan_rows).upper()
assert "EVENTS_BY_REACTION" in detail
assert "SCAN EVENTS" not in detail
```

Test both the count query and paginated row query. The production SQL must be:

```sql
SELECT COUNT(*) FROM events WHERE reaction_key = ?
SELECT event_id,reaction_key,source_row,timestep_index,before_timestep,
       after_timestep,reactant_text,product_text,atom_ids_json,
       reactant_bonds_json,product_bonds_json,association_status
  FROM events
 WHERE reaction_key = ?
 ORDER BY timestep_index, source_row, event_id
 LIMIT ? OFFSET ?
```

- [ ] **Step 2: Add source-open and exact-byte-budget tests**

After indexes are built:

- patch `Path.open`, `builtins.open`, and `sqlite3.connect` recording targets;
- allow the prepared SQLite database;
- fail any event/molecule source CSV open from service/path/network queries;
- wrap trajectory reads and assert total bytes equal the sum of selected `(end - start)` ranges;
- fail any `read(-1)` on the trajectory;
- assert an event view never requests an unindexed timestep range.

- [ ] **Step 3: Add deterministic computation caps**

Synthetic high-branch graphs assert:

```python
assert result.expansions <= max_expansions
assert len(result.paths) <= max_paths
assert payload["meta"]["node_count"] <= max_nodes
assert result.truncated is True
assert payload["meta"]["truncated"] is True
```

Run with reversed input order and require byte-identical canonical JSON.

- [ ] **Step 4: Verify RED and implement required indexes/guards**

```bash
.venv/bin/python -m pytest -q tests/test_evidence_performance_contract.py tests/test_online_index_contract.py
```

If SQLite does not choose the intended index, adjust schema/query rather than weakening the test. If a read exceeds bounds, fix the caller to slice through `offsets_for`; do not increase a byte allowance.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_evidence_performance_contract.py tests/test_online_index_contract.py tests/test_candidate_pathways.py tests/test_mechanism_graph.py
git add tests/test_evidence_performance_contract.py tests/test_online_index_contract.py
git add reacnet_scope/event_index.py reacnet_scope/trajectory.py rng_tools/pathways.py
git commit -m "test: enforce bounded evidence queries"
```

Omit unchanged production files.

---

### Task 5: Dash/service compatibility and legacy-parser isolation

**Files:**
- Modify: `tests/test_dash_smoke.py`
- Modify: `tests/test_workflow_services.py`
- Modify: `scripts/webapp_dash/services.py`
- Modify only if necessary: `scripts/webapp_dash/callbacks.py`

**Interfaces:**
- Validates: focused workflow, event page, pathway page, and network page handoffs
- Validates: Dash services do not import/call legacy LAMMPS parser
- Preserves: `scripts/webapp/server.py` legacy behavior

- [ ] **Step 1: Write failing end-to-end handoff tests**

Exercise callback functions directly:

1. select exact species → pathway start;
2. select pathway step → event reaction text;
3. select pathway → mechanism-network anchor and highlight;
4. select mechanism reaction node → event reaction text;
5. select event → participant view → package download.

Assert every handoff uses exact SMILES/reaction keys, not formula-only matching.

- [ ] **Step 2: Add an import/call isolation test**

Remove the Dash service import of `parse_lammpstrj_frame_block`. Monkeypatch every legacy parser entry point in `scripts.webapp.server` to raise, then exercise Dash event view/package services successfully.

Do not delete the legacy parser: the static Web application remains outside the new feature scope.

- [ ] **Step 3: Verify RED and make the minimal isolation change**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_workflow_services.py
```

Expected before cleanup: Dash may still import the legacy parser.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_workflow_services.py tests/test_rng_event_outputs.py
git add tests/test_dash_smoke.py tests/test_workflow_services.py scripts/webapp_dash/services.py scripts/webapp_dash/callbacks.py
git commit -m "refactor: isolate Dash evidence workflow from legacy parser"
```

Omit unchanged callback files.

---

### Task 6: Open-source attribution and operator documentation

**Files:**
- Create: `docs/open-source-components.md`
- Create: `docs/real-data-acceptance.md`
- Modify: `README.md`

- [ ] **Step 1: Document reuse boundaries**

Record:

- ReacNetGenerator is the authoritative reaction/event source;
- NetworkX handles graph containers, algorithms, and graph formats;
- ASE handles bounded LAMMPS frame parsing, PBC/MIC geometry, and ExtXYZ;
- OVITO 3.15+ is an external viewer target, not a runtime dependency;
- ReaxTools source was not copied because current repository licensing is unclear;
- NOCTIS, ReNView, Materials Project reaction-network, RMG-Py, Cantera, SCINE, LUNAR, and RMD_Digging are reference/deferred integrations, not bundled dependencies.

Link upstream project documentation and licenses. Do not claim a license for a repository whose current license file was not verified.

- [ ] **Step 2: Write the human operator runbook**

Include exact sequence:

```bash
export REACNET_SCOPE_CACHE_DIR=/path/to/fast-cache
reacnet-scope-prepare ref_data/rng-test-rp3-0523
reacnet-scope pathway \
  --reac ref_data/rng-test-rp3-0523/rp3.lammpstrj.reactionabcd \
  --start-smiles '[H][C][C][H]' \
  --max-depth 2 --max-branches 20 --max-paths 200
export EVENT_ID_FROM_PREPARED_QUERY="$(
  .venv/bin/python -c \
  'from reacnet_scope.event_index import EVENT_EVIDENCE_STORE as S; b="ref_data/rng-test-rp3-0523/rp3.lammpstrj"; print(S.query_events(b+".reactionevent.csv", b+".molecules.csv", "[H][O][H]->[H]+[H][O]", limit=1)["rows"][0]["event_id"])'
)"
reacnet-scope export-event \
  --case ref_data/rng-test-rp3-0523 \
  --event-id "$EVENT_ID_FROM_PREPARED_QUERY" \
  --type-map '1=C,2=H,3=O' \
  --out rp3-event.zip
```

Document the exact expected oracle values and explain that the event ID is resolved from the prepared index rather than hard-coding an implementation-dependent digest in user docs.

- [ ] **Step 3: Add OVITO human acceptance checklist**

The human manually extracts the ZIP and verifies in OVITO 3.15+:

- `trajectory.lammpstrj` opens as five frames;
- atom count equals the selected scope;
- cell/PBC display correctly;
- mapped types display C/H/O;
- the documented broken O–H bond corresponds to `bonds.csv`.

No automated step installs or launches OVITO.

- [ ] **Step 4: Verify documentation links and commit**

```bash
rg -n "ReacNetGenerator|NetworkX|ASE|OVITO|ReaxTools|kinetic_flux" README.md docs/open-source-components.md docs/real-data-acceptance.md
git diff --check
git add README.md docs/open-source-components.md docs/real-data-acceptance.md
git commit -m "docs: publish evidence workflow acceptance runbook"
```

Expected: all required boundaries appear and `git diff --check` prints nothing.

---

### Task 7: Full release verification and acceptance report

**Files:**
- Create: `scripts/acceptance_real_dataset.py`
- Create: `tests/test_acceptance_report.py`

**Interfaces:**
- Produces read-only report command: `python -m scripts.acceptance_real_dataset`
- Produces JSON report to stdout or explicit `--out`
- Does not build missing indexes unless `--prepare` is explicitly supplied

- [ ] **Step 1: Write failing report-command tests**

Report schema:

```json
{
  "schema_version": "reacnet-scope/acceptance-report/v1",
  "dataset_id": "0123456789abcdefabcd",
  "source_signatures": {},
  "indexes": {},
  "event_oracle": {},
  "pathway_oracle": {},
  "network_semantics": {},
  "trajectory_oracle": {},
  "checks": [],
  "passed": true
}
```

Without ready indexes, default mode exits `2`, reports preparation commands, and does not build. `--prepare` invokes public offline stores; it is an explicit operator action, not an installer. `--out` writes atomically.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_acceptance_report.py
```

Expected: report module is missing.

- [ ] **Step 3: Implement the report through public interfaces**

Do not duplicate chemistry/index logic. Call the same stores, pathway service, mechanism/observation services, event view, and package validation used by tests. Include exact observed versus expected values and source signatures.

- [ ] **Step 4: Run focused, complete, and real-data verification**

```bash
.venv/bin/python -m pytest -q tests/test_acceptance_report.py tests/test_evidence_performance_contract.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q -m realdata tests/realdata
.venv/bin/python -m compileall -q reacnet_scope rng_tools scripts tests
git diff --check
```

Expected: all tests pass, all real-data oracle assertions pass, compileall exits `0`, and `git diff --check` prints nothing.

- [ ] **Step 5: Run the real acceptance report against a temporary cache**

The worker may create a temporary cache, but may not install anything:

```bash
tmp_cache="$(mktemp -d)"
REACNET_SCOPE_CACHE_DIR="$tmp_cache" \
  .venv/bin/python -m scripts.acceptance_real_dataset \
  ref_data/rng-test-rp3-0523 --prepare
```

Expected: JSON has `"passed": true`, 263 reaction types, 3,406 events, 100/99 H2O results, the C2 hydrogenation chain, distinct network semantics, and five selected frames. Remove only the exact temporary directory after validating it; never target a workspace or environment directory.

- [ ] **Step 6: Commit**

```bash
git add scripts/acceptance_real_dataset.py tests/test_acceptance_report.py
git commit -m "test: add reproducible real-data acceptance report"
```

## Final Acceptance

- All five plans’ focused tests and the complete suite pass in the human-prepared environment.
- The RP3 oracle matches 263 reaction types, 3,406 events, 100 H2O dissociation events with 99 associations, a five-frame broken O–H view, and the `C2H2 -> C2H3 -> C2H4` candidate path.
- Mechanism and observation networks retain different schemas/evidence labels.
- Source hashes and mtimes under `ref_data/rng-test-rp3-0523` are unchanged.
- Online event/path/network queries do not open event source CSVs; trajectory reads equal requested indexed ranges.
- ASE validates generated LAMMPS/ExtXYZ members; OVITO validation remains an explicit human checklist.
