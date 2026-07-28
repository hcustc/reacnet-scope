# Generic Reference Species Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hard-coded parent handling with an optional user-selected reference species and fix the three related correctness defects.

**Architecture:** Keep the compact composition index and resolve an optional exact-SMILES series through indexed timestep offsets. Pass reference SMILES and timestep settings from Dash into the service, derive reference metadata from chemistry helpers, and leave reference-specific curves absent when no reference is selected.

**Tech Stack:** Python 3.10+, SQLite, Dash, pytest, RDKit-backed chemistry helpers.

## Global Constraints

- Preserve all unrelated working-tree changes.
- Do not create a full species-by-timestep SQLite table.
- Do not infer a reference species from abundance or a hard-coded formula.
- Do not commit without an explicit user request.

---

### Task 1: Generic reference-series service

**Files:**
- Modify: `tests/test_composition_index.py`
- Modify: `reacnet_scope/composition.py`
- Modify: `scripts/webapp_dash/services.py`

**Interfaces:**
- Consumes: `SpeciesCompositionStore.species_count_series(species_file, timesteps, smiles)`
- Produces: `build_elemental_composition_evolution(..., reference_smiles: str = "")`

- [x] **Step 1: Write failing tests**

Add tests asserting that no reference curves exist for an empty reference,
that an explicit reference is not replaced by a more abundant C1 species, and
that `timestep_ps=0.002` is reflected in plot and drilldown times.

- [x] **Step 2: Verify RED**

Run: `uv run --with pytest pytest -q tests/test_composition_index.py`

Expected: failures caused by the missing `reference_smiles` behavior and the
current inferred parent.

- [x] **Step 3: Implement the minimal service behavior**

Remove parent inference and stored `parent_count` dependence. When
`reference_smiles` is non-empty, resolve its sampled series with
`species_count_series`; otherwise emit only carbon-number totals. Return
`reference_smiles`, formula, and carbon count in the summary.

- [x] **Step 4: Verify GREEN**

Run: `uv run --with pytest pytest -q tests/test_composition_index.py`

Expected: all composition tests pass.

### Task 2: Dash reference and timestep controls

**Files:**
- Modify: `scripts/webapp_dash/app.py`
- Modify: `scripts/webapp_dash/callbacks.py`
- Modify: `tests/test_dash_smoke.py`

**Interfaces:**
- Consumes: `carbon-reference-smiles`, `carbon-timestep`
- Produces: arguments passed to `build_elemental_composition_evolution`

- [x] **Step 1: Write failing callback/layout tests**

Assert that the layout contains editable reference-SMILES and timestep inputs,
and that a callback invocation forwards their exact values.

- [x] **Step 2: Verify RED**

Run: `uv run --with pytest pytest -q tests/test_dash_smoke.py`

Expected: missing component IDs or callback arguments.

- [x] **Step 3: Implement controls and callback wiring**

Replace the disabled hard-coded parent input with an optional exact-SMILES
input, add a positive timestep-ps input, and pass both values unchanged.

- [x] **Step 4: Verify GREEN**

Run: `uv run --with pytest pytest -q tests/test_dash_smoke.py`

Expected: all Dash smoke tests pass.

### Task 3: Real moname syntax and zero tolerance

**Files:**
- Modify: `tests/test_workflow_services.py`
- Modify: `scripts/webapp_dash/services.py`
- Modify: `scripts/webapp_dash/callbacks.py`

**Interfaces:**
- Produces: accurate `moname_bond_count`; exact zero mass tolerance

- [x] **Step 1: Write failing tests**

Use `.moname` data containing `0,1,1;1,2,2` and assert two bonds. Exercise the
workflow search callback with `tolerance=0` and assert the service receives
zero.

- [x] **Step 2: Verify RED**

Run: `uv run --with pytest pytest -q tests/test_workflow_services.py tests/test_dash_smoke.py`

Expected: bond count is zero and callback tolerance is `0.5`.

- [x] **Step 3: Implement minimal fixes**

Split bonds on semicolons, validate each comma triple, and replace truthiness
defaulting with an explicit `None` check.

- [x] **Step 4: Verify GREEN**

Run: `uv run --with pytest pytest -q tests/test_workflow_services.py tests/test_dash_smoke.py`

Expected: focused tests pass.

### Task 4: Full verification and documentation alignment

**Files:**
- Modify: `README.md`

- [x] **Step 1: Update user documentation**

Describe the optional reference species, reference/other-same-carbon curves,
and configurable timestep.

- [x] **Step 2: Run full verification**

Run: `uv run --with pytest pytest -q`

Expected: all tests pass.

Run: `python -m compileall -q reacnet_scope scripts rng_tools tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no output.
