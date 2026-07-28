# Event Trajectory and Evidence Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the event viewer’s custom coordinate parser with indexed ASE-backed frame handling, add reaction-core/participant/environment scopes, and export a reproducible OVITO-compatible evidence package.

**Architecture:** The existing SQLite trajectory index remains the sole frame locator. A lazy ASE adapter parses only indexed byte ranges into `Atoms`, handles cells/PBC/minimum-image geometry, and produces JSON-safe views. Dataset-specific atom-type mappings live in a separate atomic settings file. A package layer combines event metadata, raw-column-preserving local LAMMPS frames, optional ExtXYZ, bond changes, and provenance into a deterministic ZIP.

**Tech Stack:** Python 3.10+, ASE 3.23–3.x optional extra, SQLite trajectory offsets, NumPy through ASE, `zipfile`, Dash/Plotly, pytest.

## Global Constraints

- Complete Plans 1–3 first.
- ASE is an optional human-installed dependency. The implementation worker must never install it.
- Every online frame read must seek to offsets returned by `TRAJECTORY_INDEX_STORE`; do not call ASE on the complete trajectory path.
- Do not perform bond perception or reaction detection from coordinates.
- Environment selection uses PBC minimum-image distances; malformed/unsupported cells fail explicitly.
- Core/participant views may still work when environment geometry fails.
- Original raw trajectory bytes and source files are immutable.
- Type mappings are dataset-specific, user-confirmed, and stored outside index databases.
- Partial mappings retain visible `T<type>` labels and LAMMPS export; ExtXYZ is withheld.
- No GIF/MP4 encoder, OVITO runtime, or VMD runtime is added.
- Preserve unrelated changes and commit only task-scoped files.

## Human-Owned ASE Environment Gate

Before Task 1, pause and ask the human operator to run:

```bash
cd /home/huangchen/cal_proc/reacnet-scope
uv sync --extra web --extra trajectory
```

The implementation worker may then run only these checks:

```bash
.venv/bin/python -c "import ase; major=int(ase.__version__.split('.')[0]); assert 3 <= major < 4; print(ase.__version__)"
.venv/bin/python -c "from ase.io import read, write; from ase.geometry import find_mic; print('ASE trajectory API ready')"
```

Expected: both commands exit `0`. If either fails, stop and return the exact manual install command and failed verification command. Do not change environments automatically.

---

### Task 1: Dataset settings and validated atom-type maps

**Files:**
- Create: `reacnet_scope/dataset_settings.py`
- Create: `tests/test_dataset_settings.py`
- Modify: `reacnet_scope/indexes.py`
- Modify: `reacnet_scope/prepare.py`

**Interfaces:**
- Adds: `DatasetPaths.settings`
- Produces: `DatasetSettingsStore.load(dataset_paths) -> dict`
- Produces: `DatasetSettingsStore.save_type_map(dataset_paths, mapping) -> dict`
- Produces singleton: `DATASET_SETTINGS_STORE`

- [ ] **Step 1: Write failing default, validation, and atomic-write tests**

```python
def test_settings_default_and_partial_mapping_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REACNET_SCOPE_CACHE_DIR", str(tmp_path / "cache"))
    paths = resolve_dataset_paths(tmp_path, "run.lammpstrj")
    assert DATASET_SETTINGS_STORE.load(paths)["atom_type_map"] == {}

    saved = DATASET_SETTINGS_STORE.save_type_map(paths, {"1": "C", 2: "H"})
    assert saved["atom_type_map"] == {"1": "C", "2": "H"}
    assert DATASET_SETTINGS_STORE.load(paths) == saved
```

Also assert:

- invalid type keys (`0`, negative, noninteger) are rejected;
- invalid element symbols and fallback labels such as `T3` are rejected;
- `os.replace` publishes a complete JSON document;
- a corrupt settings file returns a typed `DatasetSettingsError`, not silent defaults;
- rebuilding trajectory/event indexes does not modify settings bytes;
- manifest version 2 references the settings path and reports whether it exists.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_dataset_settings.py tests/test_preparation_management.py
```

Expected: settings module/path do not exist.

- [ ] **Step 3: Implement the independent settings store**

Use schema:

```json
{
  "schema_version": 1,
  "dataset_id": "0123456789abcdefabcd",
  "atom_type_map": {"1": "C", "2": "H"},
  "updated_at_epoch": 0
}
```

Validate element symbols against a small immutable periodic-symbol set in this module so settings inspection does not require importing ASE. Normalize keys to positive decimal strings and symbols to canonical case.

Write `dataset-settings.json.tmp`, `flush`, `os.fsync`, then `os.replace`. Settings writes are user-requested application state and must not share index build/clear code.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_dataset_settings.py tests/test_preparation_management.py
git add reacnet_scope/dataset_settings.py reacnet_scope/indexes.py reacnet_scope/prepare.py tests/test_dataset_settings.py
git commit -m "feat: persist dataset atom type mappings"
```

---

### Task 2: Lazy ASE adapter for indexed LAMMPS frame blocks

**Files:**
- Create: `reacnet_scope/trajectory.py`
- Create: `tests/test_trajectory_frames.py`
- Modify: `reacnet_scope/indexes.py`

**Interfaces:**
- Produces: `TrajectoryDependencyError`
- Produces: `IndexedFrame(raw: bytes, atoms: ase.Atoms, timestep: int, atom_ids: tuple[int, ...], atom_types: tuple[str, ...], coordinate_columns: tuple[str, ...])`
- Produces: `read_indexed_frames(trajectory_file, index, frames, *, atom_type_map=None) -> tuple[IndexedFrame, ...]`
- Produces: `select_raw_lammps_atoms(frame_raw, atom_ids) -> bytes`

- [ ] **Step 1: Write failing coordinate/cell and bounded-I/O tests**

Fixtures must cover:

- orthogonal `x y z`;
- orthogonal unwrapped `xu yu zu`;
- scaled `xs ys zs`;
- scaled unwrapped `xsu ysu zsu`;
- restricted triclinic `xy xz yz`;
- general triclinic `abc origin`;
- periodic flags from `ITEM: BOX BOUNDS`;
- `element` column precedence over type mapping;
- numeric type mapped to a symbol and unmapped fallback metadata;
- malformed coordinate columns and malformed box records.

Bounded I/O test:

```python
class GuardedReader:
    def read(self, size=-1):
        assert 0 <= size <= largest_requested_frame_bytes
        return super().read(size)

def test_reader_seeks_only_requested_index_ranges(indexed_trajectory):
    path, index, opened_ranges = indexed_trajectory
    frames = read_indexed_frames(str(path), index, [10, 30])
    assert [item.timestep for item in frames] == [10, 30]
    assert opened_ranges == [index.frame_offsets[10], index.frame_offsets[30]]
```

Missing-extra test monkeypatches the lazy import helper to raise `ModuleNotFoundError` and asserts:

```python
assert error.value.install_command == "uv sync --extra web --extra trajectory"
assert ".venv/bin/python -c \"import ase" in error.value.verify_command
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_trajectory_frames.py
```

Expected: trajectory module/interfaces are missing.

- [ ] **Step 3: Implement lazy imports and indexed range reads**

Do not import ASE at module import time. Use:

```python
def _require_ase():
    try:
        from ase.io import read, write
        from ase.geometry import find_mic
    except ModuleNotFoundError as exc:
        raise TrajectoryDependencyError(
            install_command="uv sync --extra web --extra trajectory",
            verify_command=(
                '.venv/bin/python -c "import ase; print(ase.__version__)"'
            ),
        ) from exc
    return read, write, find_mic
```

For each requested timestep:

1. resolve `(byte_start, byte_end)` with `index.offsets_for`;
2. seek and read exactly `byte_end - byte_start`;
3. decode UTF-8 strictly;
4. parse the one-frame string with
   `ase.io.read(StringIO(text), format="lammps-dump-text", index=0, specorder=specorder)`;
5. extract original atom IDs/types/coordinate column names from the one frame’s header for stable selection and export;
6. attach IDs/types as arrays if ASE did not preserve them.

An existing trajectory `element` column wins. Without that column, derive
`specorder` for every type from `1..max_type`, using the confirmed symbol for
mapped types and ASE's neutral `X` symbol for unmapped types. Viewer labels
for those `X` atoms remain `T<type>`. Do not let ASE's default numeric
type-to-atomic-number behavior turn an unmapped LAMMPS type into a fictitious
element.

- [ ] **Step 4: Implement narrow raw-frame filtering**

`select_raw_lammps_atoms` is not a coordinate parser. It:

- validates one `ITEM: TIMESTEP` block;
- locates `ITEM: NUMBER OF ATOMS` and validates that the
  `ITEM: ATOMS` column list contains `id`;
- retains original timestep, box bounds, atom-column header, and selected atom rows byte-for-byte;
- replaces only the atom count line;
- sorts selected rows by their original file order;
- rejects duplicate/missing `id` columns.

This preserves `x/xu/xs/xsu`, image flags, velocities, charges, and any extra columns for OVITO without reimplementing their meaning.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_trajectory_frames.py tests/test_trajectory_index_persistence.py
git add reacnet_scope/trajectory.py reacnet_scope/indexes.py tests/test_trajectory_frames.py
git commit -m "feat: parse indexed trajectory frames with ASE"
```

---

### Task 3: Core, participant, and periodic environment views

**Files:**
- Modify: `reacnet_scope/trajectory.py`
- Create: `reacnet_scope/event_view.py`
- Create: `tests/test_event_view.py`
- Modify: `scripts/webapp_dash/services.py`
- Modify: `tests/test_rng_event_outputs.py`

**Interfaces:**
- Produces: `build_event_view(trajectory_file, event, *, scope="participants", before_frames=3, after_frames=3, environment_radius=4.0, max_environment_atoms=500, atom_type_map=None) -> dict`
- Replaces service internals: `build_rng_event_visualization(...)`

- [ ] **Step 1: Write failing scope, MIC, cap, and recenter tests**

Create a periodic 10 Å box where participant atom 1 is at `x=0.2` and a neighbor is at `x=9.8`. Assert the neighbor is selected at MIC distance `0.4`, not `9.6`.

Test:

```python
participants = build_event_view(
    str(trajectory), event, scope="participants", atom_type_map=type_map
)
assert participants["atom_groups"]["selected"] == event["atom_id_list"]

core = build_event_view(
    str(trajectory), event, scope="core", atom_type_map=type_map
)
assert core["atom_groups"]["selected"] == [1, 2]  # atoms in broken/formed bonds

environment = build_event_view(
    str(trajectory),
    event,
    scope="environment",
    environment_radius=4.0,
    max_environment_atoms=50,
    atom_type_map=type_map,
)
assert environment["selection"]["raw_environment_matches"] == 57
assert environment["selection"]["selected_count"] == 50
assert environment["selection"]["truncated"] is True
```

Across a boundary-crossing two-frame fixture, assert recentered core coordinates change continuously while `original_positions` and cell metadata remain unchanged.

Also assert:

- radius outside `2.0–10.0` and cap outside `50–2,000` are rejected at public boundary;
- participants exceeding the cap produce `participants_exceed_environment_cap`;
- no core bonds falls back to all participants with `core_fallback=True`;
- unresolved event atom association is rejected;
- environment failure from invalid PBC metadata does not prevent core/participant requests.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_event_view.py tests/test_rng_event_outputs.py
```

Expected: event view module and new scopes are missing.

- [ ] **Step 3: Select frames only through the trajectory index**

Open `TRAJECTORY_INDEX_STORE.open_required`, find the closest indexed frames to event `before_timestep` and `after_timestep`, and include the requested surrounding frame counts. The anchor frame is the closest indexed frame to `before_timestep`.

Call `read_indexed_frames` with exactly this list. Missing/stale trajectory index returns the existing typed readiness error and exact preparation command; never scan.

- [ ] **Step 4: Implement atom scopes**

Parse atom IDs from RNG bond IDs:

```text
core = atoms present in product_bonds Δ reactant_bonds
participants = event.atom_id_list
environment = participants + nearest nonparticipants within radius
```

Use ASE minimum-image distances from each candidate to all participants. Sort environment candidates by `(minimum_distance, atom_id)` before applying the cap. The cap counts participants plus environment atoms; never silently drop participants.

Return requested radius, effective radius, raw matches, selected count, cap, and truncation.

- [ ] **Step 5: Implement PBC-stable viewer coordinates**

For every frame:

1. use the lowest core atom ID as an origin;
2. calculate MIC displacement vectors from that origin;
3. calculate the core centroid in the unwrapped local vectors;
4. subtract the core centroid from all selected local positions.

Return both `viewer_position` and `original_position` per atom, plus cell, PBC, source coordinate columns, labels, types, and mapping status. Bonds remain RNG evidence: reactant before, product after, and empty/unknown for intermediate frames.

The internal view also retains `raw_frame_blocks: tuple[bytes, ...]` for the
package builder. Add `event_view_payload(view) -> dict` to remove those bytes
before a result is placed in a Dash `dcc.Store`; package downloads rebuild
the bounded internal view from the stored event ID and parameters.

- [ ] **Step 6: Migrate the Dash service wrapper**

Keep `build_rng_event_visualization` as a compatibility wrapper calling `build_event_view(scope="participants")`. Delete its dependency on `parse_lammpstrj_frame_block`; do not delete the legacy helper until repository-wide callers are removed in Plan 5.

- [ ] **Step 7: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_event_view.py tests/test_rng_event_outputs.py tests/test_workflow_services.py
git add reacnet_scope/trajectory.py reacnet_scope/event_view.py scripts/webapp_dash/services.py tests/test_event_view.py tests/test_rng_event_outputs.py
git commit -m "feat: add periodic local event trajectory views"
```

---

### Task 4: Deterministic event evidence ZIP

**Files:**
- Create: `reacnet_scope/event_package.py`
- Create: `tests/test_event_package.py`
- Modify: `reacnet_scope/event_view.py`

**Interfaces:**
- Produces: `build_event_package(view, *, source_signatures, atom_type_map) -> bytes`
- ZIP members: `event.json`, `trajectory.lammpstrj`, optional `trajectory.extxyz`, `bonds.csv`, `README.txt`

- [ ] **Step 1: Write failing member, provenance, and determinism tests**

For a complete mapping:

```python
first = build_event_package(view, source_signatures=signatures, atom_type_map={"1": "C", "2": "H"})
second = build_event_package(view, source_signatures=signatures, atom_type_map={"1": "C", "2": "H"})
assert first == second
with ZipFile(BytesIO(first)) as archive:
    assert archive.namelist() == [
        "event.json", "trajectory.lammpstrj", "trajectory.extxyz", "bonds.csv", "README.txt"
    ]
```

Assert:

- `event.json` records identity, complete reaction sides, scopes, selected IDs, bond changes, frame list, source signatures, original/viewer coordinate treatment, mapping, radius/cap/truncation, schema version;
- raw local LAMMPS blocks open with ASE and retain original atom columns;
- ExtXYZ opens with ASE, has cell/PBC, mapped symbols, and viewer coordinates;
- `bonds.csv` has `state,atom1,atom2,bond_order,change`;
- partial mapping omits ExtXYZ, sets `extxyz_included=false`, and explains why in README;
- ZIP paths contain no absolute path or `..`;
- deterministic `ZipInfo` timestamps and member ordering make identical inputs byte-identical.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_event_package.py
```

Expected: package module is missing.

- [ ] **Step 3: Implement canonical metadata and raw LAMMPS member**

Schema:

```text
reacnet-scope/event-package/v1
```

Serialize JSON with sorted keys, UTF-8, and stable separators/indentation. Concatenate the view’s already filtered raw frame blocks in timestep order. Never reopen the trajectory during packaging.

- [ ] **Step 4: Implement ExtXYZ through ASE**

Only when every selected atom has a valid element:

1. copy each selected `Atoms`;
2. replace positions with recentered viewer coordinates;
3. retain cell and PBC;
4. add `original_id`, `original_type`, and `event_group` arrays;
5. call `ase.io.write(StringIO(), images, format="extxyz")`.

If mapping is incomplete, do not invent symbols and do not emit the member.

- [ ] **Step 5: Implement bond CSV, README, and deterministic ZIP**

README includes provenance, units inherited from LAMMPS/ASE, coordinate transformations, event-source limitations, missing-map warning, and:

```bash
ovito trajectory.lammpstrj
python -c "from ase.io import read; print(len(read('trajectory.extxyz', ':')))"
```

Use fixed ZIP timestamps `(1980, 1, 1, 0, 0, 0)`, fixed permissions, `ZIP_DEFLATED`, and explicit member order.

- [ ] **Step 6: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_event_package.py tests/test_event_view.py
git add reacnet_scope/event_package.py reacnet_scope/event_view.py tests/test_event_package.py
git commit -m "feat: export reproducible event evidence packages"
```

---

### Task 5: `export-event` CLI

**Files:**
- Modify: `reacnet_scope/event_index.py`
- Modify: `scripts/rng_query_cli.py`
- Create: `tests/test_event_export_cli.py`

**Interfaces:**
- Produces:
  `EVENT_EVIDENCE_STORE.get_event(reactionevent_file, molecules_file, event_id) -> dict`
- Produces CLI: `reacnet-scope export-event`

- [ ] **Step 1: Write failing parser and end-to-end tests**

Required arguments/options:

```text
--case
--base
--event-id
--scope core|participants|environment
--before-frames
--after-frames
--environment-radius
--max-environment-atoms
--type-map "1=C,2=H"
--out
```

Test a prepared fixture and assert output ZIP members and metadata. Test failures for missing event index, missing trajectory index, unknown event ID, unresolved atoms, missing ASE, invalid mapping, and an existing output path without `--force`.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_event_export_cli.py
```

Expected: parser rejects `export-event`.

- [ ] **Step 3: Add event lookup and command implementation**

`get_event` validates both event sources and performs a primary-key SQLite query. The CLI:

1. resolves dataset artifacts with `discover_dataset`;
2. opens both prepared indexes;
3. loads stored type mapping and overlays explicit `--type-map` for this export only;
4. builds the event view;
5. builds package bytes;
6. writes `Path(f"{output_path}.tmp")`, fsyncs, and atomically replaces the
   target.

It must not save the command-line mapping to dataset settings unless a separate explicit future option is designed.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_event_export_cli.py tests/test_event_package.py
git add reacnet_scope/event_index.py scripts/rng_query_cli.py tests/test_event_export_cli.py
git commit -m "feat: add event package export CLI"
```

---

### Task 6: Dash scopes, type-map confirmation, and ZIP download

**Files:**
- Modify: `scripts/webapp_dash/app.py`
- Modify: `scripts/webapp_dash/callbacks.py`
- Modify: `scripts/webapp_dash/services.py`
- Modify: `scripts/webapp_dash/assets/app.css`
- Modify: `tests/test_dash_smoke.py`
- Modify: `tests/test_event_view.py`

**Interfaces:**
- Replaces event scopes with: `core`, `participants`, `environment`
- Adds: radius/cap inputs, type-map editor/save confirmation, ZIP download
- Preserves: current event selection and storyboard workflow

- [ ] **Step 1: Write failing layout and callback tests**

Assert IDs:

```text
event-view-scope
event-environment-radius
event-environment-cap
event-type-map-editor
event-type-map-save-btn
event-type-map-confirm
event-package-btn
event-package-download
```

Test:

- default scope is `participants`;
- choosing environment forwards exact radius/cap;
- radius `2.0` is not replaced by a truthiness default;
- saving requires explicit confirmation and updates only dataset settings;
- partial mappings show `T<type>` and disable only ExtXYZ, not ZIP;
- package download uses the exact event ID and selection parameters from
  `event-viewer-store`; any reread is limited to the same prepared frame
  offsets and never scans the trajectory;
- missing ASE shows exact manual install/verify commands while event metadata remains visible.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_event_view.py
```

Expected: controls and callbacks are missing.

- [ ] **Step 3: Update the event viewer layout**

Replace “完整上下文/仅反应核” with:

- 反应核 (`core`)
- 参与分子 (`participants`)
- 4 Å 邻域 (`environment`)

Show radius/cap only for environment. Add a compact type→element table derived from types visible in selected frames, with current mapping, fallback labels, validation state, and explicit save confirmation.

- [ ] **Step 4: Update rendering and degradation**

Plot `viewer_position`; retain original coordinates only in store/package metadata. Visually distinguish core, other participant, and environment atoms. Show formed/broken RNG bonds without inferring intermediate bonds.

Catch `TrajectoryDependencyError` separately and render:

```text
人工安装: uv sync --extra web --extra trajectory
只读验证: .venv/bin/python -c "import ase; print(ase.__version__)"
```

Do not hide the selected event metadata/table.

- [ ] **Step 5: Add mapping save and ZIP download callbacks**

Mapping save derives `DatasetPaths` from the current dataset and calls
`DATASET_SETTINGS_STORE.save_type_map` only after confirmation. ZIP download
re-resolves the selected event by stable event ID, rebuilds the exact view
using the selection parameters stored in `event-viewer-store`, and calls
`build_event_package`. The second read is allowed only through the same
trajectory-index offsets; source-path or whole-file reads remain forbidden.

- [ ] **Step 6: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_event_view.py tests/test_event_package.py tests/test_dataset_settings.py
git add scripts/webapp_dash/app.py scripts/webapp_dash/callbacks.py scripts/webapp_dash/services.py scripts/webapp_dash/assets/app.css tests/test_dash_smoke.py tests/test_event_view.py
git commit -m "feat: add local environment event viewer and download"
```

---

### Task 7: Documentation and milestone verification

**Files:**
- Modify: `README.md`
- Create: `docs/event-trajectory-evidence.md`

- [ ] **Step 1: Document the human prerequisite and workflow**

Cover:

- manual ASE installation and read-only verification;
- trajectory-index preparation;
- three atom scopes, PBC/MIC behavior, recentering, radius/cap limits;
- type-map precedence and partial-map behavior;
- ZIP schema and member provenance;
- OVITO and ASE opening commands;
- why intermediate bonds and confirmed mechanisms are not inferred.

- [ ] **Step 2: Run final verification**

```bash
.venv/bin/python -m pytest -q tests/test_dataset_settings.py tests/test_trajectory_frames.py tests/test_event_view.py tests/test_event_package.py tests/test_event_export_cli.py tests/test_dash_smoke.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q reacnet_scope rng_tools scripts tests
git diff --check
```

Expected: all tests pass, compileall exits `0`, and `git diff --check` prints nothing.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/event-trajectory-evidence.md
git commit -m "docs: explain local trajectory evidence packages"
```

## Milestone Acceptance

- ASE is used only after a human-owned environment gate and imports lazily.
- Every viewer/package read is bounded by prepared trajectory offsets.
- Core, participants, and periodic environment scopes are deterministic and auditable.
- Complete mappings produce LAMMPS and ExtXYZ; partial mappings produce valid LAMMPS plus an explicit ExtXYZ omission.
- Generated local LAMMPS trajectories open in ASE and are documented for OVITO 3.15+.
