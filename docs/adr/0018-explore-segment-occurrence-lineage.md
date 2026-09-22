---
status: accepted
date: 2026-09-21
---

# Explore concrete segment/occurrence lineage

The user selected Molecular Lineage recovery as the development mainline and
requested an interactive Explorer. This PR establishes the substrate,
traversal, identity and evidence contracts; the Dash entry point is delivered
separately.
Candidate species-network discovery cannot create the observed pathways shown here.
This adds a segment-backed interface alongside the existing endpoint-based
`molecule-lineage/v2` compatibility workflow. ADR-0013 still defines Candidate
identity and independent support checks; the Explorer does not relabel Candidates.

## Prepared substrate

Event preparation additionally publishes `lineage_segments_version=2`. Exact
state keys contain Species, source Atom IDs normalized to Scope's one-based
convention, and intramolecular bond orders. Native molecule ranges are streamed
without expansion into every molecule-frame record; CSV molecular frames use
the existing strict parser. Adjacent identical states coalesce into maximal
segments; interrupted reappearance and source-file boundaries remain distinct.
Conflicting atom assignments cannot certify a segment.

Native sources may contain hundreds of millions of genuinely disjoint ranges.
The complete exact-state occupancy is stored as compressed frame bitmaps with
logical segment counts. Explicit segment IDs/ranges are materialized for the
concrete occurrence participants that the Explorer can select. This avoids one
SQLite row per short source range while preserving every gap. Version 2 stores
the compressed bitmaps in bounded frame chunks, omitting all-zero chunks, and
requires rebuilding earlier draft indexes. Bitmaps are built in bounded range
blocks; disk-backed packed atom occupancy checks conflicting
assignments even outside occurrence endpoint frames. No temporal smoothing is
performed by this storage compression.

Occurrence ports map existing stable event participant identities to exact
segments covering their before/after frame. Inputs terminate at the transition,
outputs start on its after frame; participants unchanged across the event are
context. Incomplete mappings or ambiguous boundary events terminate traversal.
The Explorer neither invents an event for an unlinked state change nor orders
occurrences inside one Transition. No second bond detector or HMM is introduced.

All tables are built in the event index's unpublished staging database. Source
revision checks, locking and atomic publication remain owned by event preparation.
Interrupted derived-table work is rebuilt on resume; the event checkpoints are
preserved. Existing indexes keep their other capabilities and expose an explicit
Explorer rebuild requirement. Online reads never migrate or scan raw RNG files.

## Interactive lineage

The root is a concrete event-participant Molecule Instance. One action expands
its segment's previous/next boundary, preserving the occurrence's complete input
and output sets. All split successors and all merge sources remain visible.
The first version uses all root atoms as an explicit provenance reference,
reports local pairwise atom transfers, retained/lost/gained sets and connectivity,
and applies no skeletal-retention threshold or largest-descendant selection.

Each expansion is bounded to 20 occurrences/five seconds; a graph is bounded to
500 segments/250 occurrences. A whole occurrence is admitted or left unexpanded,
never partially published by clipping its participants. Independent request IDs
and dataset contexts reject late UI responses. Each service read checks the
published source revision before and after the operation.

Observed paths are extracted only from the currently expanded concrete lineage.
Every step is rehydrated from the published index, has strictly increasing time,
and uses compatible segment boundaries. A nonempty continuous atom intersection
must survive each path; split/remerge histories remain distinct projections.
The path result records its limited scope and enumeration truncation. Species
may recur in distinct segments. A path is an observed history, not a mechanism.

The UI provides separate Lineage/Observed Path views, segment and occurrence
details, incremental branch actions, root navigation, exact-frame audit and
revision-bound JSON export. Coordinates are read from one indexed original
frame only when its exact timestep exists; absent coordinate evidence does not
disable the segment graph.

## Validation

Fixtures cover split, merge, return to the same Species in a new segment,
long unchanged intervals, hidden bond-state changes, disappearance/reappearance,
retention through alternative split branches, complete hyperedge admission,
source changes, missing segment indexes, late responses and exact raw frames.
Native/CSV preparation and online no-source-read checks share the same service.
Real-data timings and scale limits are reported separately from fixture results.
