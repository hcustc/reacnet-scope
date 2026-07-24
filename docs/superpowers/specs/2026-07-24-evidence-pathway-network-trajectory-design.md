# Evidence-Linked Pathway, Network, and Trajectory Design

## Goal

Turn ReacNet Scope's existing one-step reaction queries, aggregate observation
network, and event viewer into an evidence-linked workflow that can:

- rank auditable multi-step candidate pathways;
- display mechanism and observation networks without conflating their meaning;
- inspect reaction cores, participating molecules, and local environments; and
- export a reproducible local trajectory evidence package.

The result is a candidate-mechanism analysis tool. It must not describe a
ranked path as an automatically confirmed reaction mechanism.

## Product Scope

The work is delivered in independently testable milestones:

1. offline event-evidence indexing and the pathway domain layer;
2. pathway query, ranking, export, and Dash integration;
3. a reactionabcd mechanism-network view alongside the existing table view;
4. local-environment visualization, atom-type mapping, and event-package
   export;
5. real-data acceptance, performance hardening, and documentation.

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
- a control to highlight the path in the mechanism network;
- a control to open supporting events for a selected step; and
- JSON and CSV exports.

Empty results distinguish “species absent”, “no positive-net continuation”,
and “filtered by current thresholds”.

### Dual network page

The network page contains two semantically independent views with a shared
species selection:

- **Mechanism network** uses `.reactionabcd`. Each reaction is a bipartite
  reaction node with all reactant/product edges, forward/reverse/net passages,
  and event-evidence coverage when available.
- **Observation network** preserves the existing `.table` view and
  `aggregate_observation` evidence label.

The mechanism view defaults to a bounded neighborhood around the selected
species instead of rendering the entire dataset. It supports direction,
depth, minimum net passage, maximum node count, and evidence-status filters.
A reaction node opens its complete stoichiometry and metrics and can hand off
to event search. A pathway can be highlighted without changing the underlying
network semantics.

Both views retain PNG export. The mechanism view additionally exports
Cytoscape JSON, node CSV, and edge CSV. Exported schemas include a version and
evidence level.

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

Trajectory parsing moves into a reusable core module and supports `x`, `xu`,
`xs` coordinate variants, orthogonal boxes, and triclinic tilt factors.
Viewer coordinates are re-centered on the reaction core per frame to avoid
periodic jumps. Original wrapped coordinates remain available for faithful
LAMMPS export.

Element names follow this precedence:

1. the trajectory `element` column;
2. a user-confirmed dataset-specific `type -> element` mapping; and
3. the visible fallback label `T<type>`.

The mapping is stored in a separate dataset settings file under the cache
directory and referenced by the generated manifest, so rebuilding indexes
does not overwrite user settings. Partial mappings are allowed for viewing
and LAMMPS export. XYZ export is disabled if any selected atom type is
unmapped.

An event download produces a ZIP containing:

- `event.json` with event identity, reaction, atom scopes, bond changes,
  source signatures, frame list, coordinate treatment, mapping, and selection
  parameters;
- `trajectory.lammpstrj` with the selected local atoms and original coordinate
  convention;
- `trajectory.xyz` with mapped elements and re-centered coordinates when the
  mapping is complete;
- `bonds.csv`; and
- `README.txt` explaining provenance, units, transformations, and limitations.

No GIF or MP4 encoder is added in this version.

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

build_mechanism_network(
    network,
    *,
    anchor_smiles,
    direction="both",
    max_depth=2,
    min_net_tp=1,
    max_nodes=200,
    evidence_provider=None,
) -> dict

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
- A partial element map preserves `T<type>` labels; only XYZ is withheld.
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
- mechanism-network stoichiometry and strict separation from observation
  network evidence semantics;
- orthogonal and triclinic coordinate parsing, scaled coordinates,
  minimum-image neighbor selection, re-centering, and environment caps;
- complete and partial type mappings and ZIP member contents; and
- guarded I/O proving online path/event queries do not open source event CSVs
  and trajectory extraction reads only indexed frame ranges.

### Dash and CLI tests

Cover page layout, callback dependencies, selected-species handoff, pathway
filters, path highlighting, event drilldown, network view switching, mapping
validation, download behavior, and CLI JSON/CSV/ZIP output.

### Real-data acceptance

Use `ref_data/rng-test-rp3-0523` as an integration fixture and assert:

- 263 reaction types and 3,406 RNG events are indexed;
- the H2O dissociation query returns 100 events, 99 with atom association;
- a selected event exposes five trajectory frames and an O-H broken bond;
- the candidate search includes the
  `C2H2 -> C2H3 -> C2H4` hydrogen-addition chain under documented query
  settings;
- mechanism and observation payloads retain different schema/evidence labels;
  and
- rebuilding or querying never modifies the RNG source files.

Full validation runs focused tests, the complete test suite, `compileall`, and
`git diff --check`.
