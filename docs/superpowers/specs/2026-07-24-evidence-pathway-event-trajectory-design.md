# Evidence-Linked Pathway, Event, and Trajectory Design

> **Scope update (2026-08-01):** Event evidence, bounded candidate paths, local
> trajectory review, and deterministic event ZIP/ExtXYZ export are the complete
> scope of this design.

## Goal

Turn ReacNet Scope's existing one-step reaction queries, aggregate observation
network, and event viewer into an evidence-linked workflow that can:

- rank auditable multi-step candidate pathways;
- inspect reaction cores, participating molecules, and local environments; and
- export a reproducible local trajectory evidence package.

The result is an evidence-linked candidate-path analysis tool. It must not
describe a ranked path as an automatically confirmed reaction mechanism.

## Reuse and Dependency Policy

ReacNetGenerator remains the authoritative producer of molecule identities,
HMM-filtered reaction summaries, and time-resolved reaction events.
ReacNet Scope consumes those artifacts and must not run a second bond
perception or reaction-detection pipeline over the trajectory. In particular,
ReaxTools is an interoperability target and an audit-design reference, not a
replacement event source for a ReacNetGenerator dataset.

The implementation uses established libraries at the following boundaries:

- `ase>=3.23,<4` is an optional `trajectory` dependency for parsing indexed
  LAMMPS frame blocks, cell/PBC handling, minimum-image calculations, and
  XYZ/ExtXYZ writing.
- OVITO 3.15 or later is an external interoperability target. It is not a
  runtime dependency; exported local trajectories must open in OVITO.
- NOCTIS, its Neo4j route-miner, ReNView, Materials Project
  `reaction-network`, RMD_Digging, LUNAR, SCINE, RMG-Py, and Cantera are not
  runtime dependencies for this version.

ReaxTools' current GitHub repository does not publish a clear license file,
even though an older Gitee mirror describes an MIT license. No ReaxTools
source is copied unless upstream licensing is clarified. Its raw-event,
event-pair, transfer-flow, and manifest layout may be supported later through
an independently implemented importer.

Dependency installation is a user-owned prerequisite. Development tasks may
edit dependency declarations and lock metadata, but must not run `pip
install`, `uv sync`, Conda, Apt, or other package/system installers. Each
dependency gate supplies the exact manual command and a read-only import
verification command, then waits for the user to confirm that the environment
is ready.

## Product Scope

The current work is delivered in independently testable milestones:

1. offline event-evidence indexing;
2. pathway query, ranking, export, and Dash integration;
3. ASE-backed local-environment visualization, atom-type mapping, and
   OVITO-compatible event-package export;
4. real-data acceptance, performance hardening, interoperability
   documentation, and release checks.

Each milestone receives a separate implementation plan and produces a
reviewable, tested deliverable.

New user-facing work targets the recommended Dash application plus reusable
Python and CLI interfaces. The legacy static Web application remains
compatible but does not receive equivalent new pages. The design targets a
large single dataset: millions of event rows, tens of millions of species
records, and trajectories up to hundreds of GB. Cluster scheduling,
cross-node execution, GIF/MP4 rendering, and atom-continuous multi-step
mechanism proof are outside this version.

## Architecture

### Offline event-evidence index

Add a dataset-local SQLite event-evidence index under the existing
`REACNET_SCOPE_CACHE_DIR/datasets/<dataset_id>/` layout. The builder streams
`reactionevent.csv` and `molecules.csv`, normalizes reaction sides while
preserving multiplicity, and records:

- stable event ID derived from interval, source row, and associated atom IDs
  using the current RNG-event ID scheme, plus the source row;
- normalized reaction key and original reaction text;
- timestep interval and before/after trajectory timesteps;
- associated atom IDs;
- reactant and product bonds;
- association status; and
- per-reaction totals, matched-event count, and distinct interval count.

The build is checkpointed at completed timestep intervals and resumes without
duplicating published rows. Publication is atomic. The index records source
paths, sizes, mtimes, schema version, dataset ID, build state, and progress.

Only the offline preparation command may create, rebuild, migrate, or clear
the index. Dash readers open it read-only. Missing, building, stale, invalid,
and ready states remain distinct. An unavailable index never causes an online
scan of either source CSV.

The unified preparation command builds this index by default when both RNG
event outputs exist and exposes an `--event-only` mode. The dataset manifest
is upgraded while retaining read compatibility with old manifests and the
existing trajectory, route, and composition indexes.

### Candidate-pathway domain

Create a focused pathway module rather than extending the existing `PathNode`
tree. A pathway step contains:

- the focal input and output species;
- the complete stoichiometric reaction sides;
- forward, reverse, and positive net passage counts;
- the focal-species outgoing net-share;
- directionality, event-association coverage, and temporal coverage;
- evidence status and source references; and
- the versioned step score.

Reactions remain hyperedges. When a reaction produces multiple new species,
each can become a candidate focal continuation, but every step retains all
reactants and products. A path may not revisit a focal species.

The domain objects and ranking engine remain independent of graph libraries so
their serialized schema is stable. Static additive edge weights cannot express
evidence availability, co-reactants, or query-time limits.

The default query is loopless best-first enumeration with:

- direction `downstream`;
- maximum depth `3`;
- maximum branches per expansion `5`;
- maximum returned paths `20`;
- maximum total expansions `5,000`;
- minimum positive net passage count `1`; and
- minimum directionality `0.05`.

Upstream queries use the symmetric producer traversal. All limits are exposed
through the Python and CLI interfaces; the Dash page exposes depth, branch
limit, result count, minimum net count, and minimum directionality.

### Ranking semantics

Each step publishes four normalized metrics:

1. `net_share`: positive net passage count divided by all positive outgoing
   net passages for the focal species;
2. `directionality`: positive net passage count divided by forward passages;
3. `event_coverage`: atom-associated RNG events divided by RNG events for the
   reaction; and
4. `time_coverage`: distinct supporting event intervals divided by the total
   adjacent-timestep intervals available in the dataset.

The versioned `candidate-path/v1` step score is:

```text
0.40 * net_share
+ 0.25 * directionality
+ 0.20 * event_coverage
+ 0.15 * time_coverage
```

If the event-evidence index is unavailable, only the first two terms are used
and their weights are renormalized to one. The result is explicitly marked
`network_only`.

For a path with step scores `s1..sn`, the path score is:

```text
0.70 * geometric_mean(s1..sn) + 0.30 * min(s1..sn)
```

The UI displays the composite score and every input metric. Users may sort by
any raw metric. JSON and CSV exports include the score version, query
parameters, source signatures, evidence status, and unrounded metrics.

## User Experience

### Pathway page

Add a dedicated “关键路径” Dash page. A user may start from the selected
species or enter an exact SMILES, choose upstream or downstream traversal,
adjust the bounded query controls, and run the search.

Results include:

- a ranked pathway table;
- a Cytoscape pathway graph;
- per-step formula, SMILES, forward/reverse/net values, and evidence metrics;
- a clear `evidence_linked` or `network_only` badge;
- a control to open supporting events for a selected step; and
- JSON and CSV exports.

Empty results distinguish “species absent”, “no positive-net continuation”,
and “filtered by current thresholds”.

### Event viewer and reproducible package

The event viewer exposes three atom scopes:

- `core`: atoms participating in broken or formed bonds;
- `participants`: all atoms in the RNG-associated reaction component; and
- `environment`: participants plus atoms within `4.0 Å` of any participant in
  the anchor frame.

Environment selection uses periodic minimum-image distances. It is capped at
`500` atoms by default; nearer atoms win and the response records whether
truncation occurred. The radius and cap are user-adjustable within server
limits of `2.0–10.0 Å` and `50–2,000` atoms.

The existing SQLite trajectory index remains responsible only for locating
bounded byte ranges. Indexed LAMMPS frame blocks are parsed into ASE `Atoms`
objects. ASE supplies `x`, `xu`, `xs`, and `xsu` coordinate handling,
orthogonal and restricted/general triclinic cells, PBC flags,
minimum-image calculations, wrapping, and output writers. ReacNet Scope does
not maintain a second general-purpose LAMMPS parser.

Viewer coordinates are re-centered on the reaction core per frame to avoid
periodic jumps. Original coordinates and cell metadata remain available for
faithful LAMMPS and ExtXYZ export. The reader must seek only to offsets
returned by the trajectory index; using ASE does not authorize scanning the
complete trajectory during a Dash request.

Element names follow this precedence:

1. the trajectory `element` column;
2. a user-confirmed dataset-specific `type -> element` mapping; and
3. the visible fallback label `T<type>`.

The mapping is stored in a separate dataset settings file under the cache
directory and referenced by the generated manifest, so rebuilding indexes
does not overwrite user settings. Partial mappings are allowed for viewing
and LAMMPS export. ExtXYZ export is disabled if any selected atom type is
unmapped.

An event download produces a ZIP containing:

- `event.json` with event identity, reaction, atom scopes, bond changes,
  source signatures, frame list, coordinate treatment, mapping, and selection
  parameters;
- `trajectory.lammpstrj` with the selected local atoms and original coordinate
  convention;
- `trajectory.extxyz` with mapped elements, cell/PBC metadata, and
  re-centered coordinates when the mapping is complete;
- `bonds.csv`; and
- `README.txt` explaining provenance, units, transformations, limitations,
  and commands for opening the trajectory in ASE or OVITO.

No GIF or MP4 encoder is added in this version.

### Interoperability adapters

This version exports stable event and trajectory data rather than embedding
larger chemistry platforms:

- ExtXYZ and a local LAMMPS dump are the trajectory interchange formats.
- A future ReaxTools importer may consume `reaction_events.csv`,
  `reaction_event_pairs.csv`, `transfer_flow.csv`, `molecules.json`, and its
  manifest, but imported datasets must carry
  `event_source="reax_tools_geometry"` and cannot be merged silently with RNG
  events.
- NOCTIS/Neo4j, ReNView/Cantera rate models, and Chemkin export are explicitly
  outside this version.

## Public Interfaces

The implementation adds reusable interfaces equivalent to:

```python
EVENT_EVIDENCE_STORE.status(reactionevent_file, molecules_file) -> dict
EVENT_EVIDENCE_STORE.build(reactionevent_file, molecules_file, *, progress_callback=None) -> dict
EVENT_EVIDENCE_STORE.query_events(reaction_key, *, limit, offset=0) -> dict
EVENT_EVIDENCE_STORE.reaction_summary(reaction_keys) -> dict[str, dict]

find_candidate_paths(
    network,
    start_smiles,
    *,
    direction="downstream",
    max_depth=3,
    max_branches=5,
    max_paths=20,
    max_expansions=5000,
    min_net_tp=1,
    min_directionality=0.05,
    evidence_provider=None,
) -> CandidatePathResult

build_event_view(
    trajectory_file,
    event,
    *,
    scope="participants",
    before_frames=3,
    after_frames=3,
    environment_radius=4.0,
    max_environment_atoms=500,
    atom_type_map=None,
) -> dict
```

The CLI adds `pathway` and `export-event` commands. Existing commands and
observation-network payloads remain compatible.

## Failure and Degradation Rules

- Missing event outputs disable event evidence but do not disable
  reactionabcd-only pathway queries.
- Missing or stale event index returns `network_only` paths and an exact
  preparation command.
- Unresolved atom association remains visible and cannot be opened as a
  trajectory event.
- A missing trajectory index disables the viewer and package export without
  scanning the trajectory.
- A missing ASE trajectory extra disables frame parsing and package export,
  preserves event metadata inspection, and returns the exact manual install
  and import-verification commands.
- A partial element map preserves `T<type>` labels; only ExtXYZ is withheld.
- An environment cap records the requested radius, selected count, raw match
  count, and `truncated=true`.
- Unsupported or malformed box/coordinate metadata fails the environment
  calculation explicitly; core and participant views may still use valid
  coordinates.
- Source signature mismatches mark affected indexes stale and require offline
  rebuilding.
- Reaching the path expansion limit returns deterministic partial results with
  `truncated=true`; it never silently changes thresholds.

## Validation

### Unit and contract tests

Cover:

- event-index normalization, multiplicity, pagination, checkpoint resume,
  atomic publication, and stale/corrupt detection;
- deterministic scoring, missing-evidence renormalization, hyperedge
  branching, upstream symmetry, cycle prevention, thresholding, and expansion
  truncation;
- orthogonal and triclinic coordinate parsing, scaled coordinates,
  minimum-image neighbor selection, re-centering, and environment caps;
- complete and partial type mappings and ZIP member contents; and
- guarded I/O proving online path/event queries do not open source event CSVs
  and trajectory extraction reads only indexed frame ranges.

ASE-backed tests run in the manually prepared `trajectory` environment. A
separate missing-extra test verifies graceful degradation without importing
ASE.

### Dash and CLI tests

Cover page layout, callback dependencies, selected-species handoff, pathway
filters, event drilldown, mapping validation, download behavior, and CLI
JSON/CSV/ZIP output.

### Real-data acceptance

Use `ref_data/rng-test-rp3-0523` as an integration fixture and assert:

- 263 reaction types and 3,406 RNG events are indexed;
- the H2O dissociation query returns 100 events with atom association;
- a selected event exposes five trajectory frames and an O-H broken bond;
- the candidate search includes the
  `C2H2 -> C2H3 -> C2H4` hydrogen-addition chain under documented query
  settings;
- rebuilding or querying never modifies the RNG source files.

Full validation runs focused tests, the complete test suite, `compileall`, and
`git diff --check`.

## Open-Source Decision Record

The reuse decisions are based on the following upstream interfaces:

- ReacNetGenerator generated files and event CLI:
  <https://docs.deepmodeling.com/projects/reacnetgenerator/en/latest/guide/report.html>
- ReaxTools audited event/transfer outputs:
  <https://github.com/tgraphite/reax_tools>
- ASE LAMMPS frame, cell, and coordinate handling:
  <https://docs.ase-lib.org/_modules/ase/io/lammpsrun.html>
- OVITO file I/O and Python-module license:
  <https://www.ovito.org/docs/current/python/introduction/file_io.html>
  and <https://www.ovito.org/manual/licenses/index.html>
- NOCTIS graph model and Neo4j route-miner:
  <https://github.com/syngenta/noctis> and
  <https://github.com/syngenta/noctis-route-miner>
- ReNView rate/flux inputs:
  <https://github.com/VlachosGroup/renview>
