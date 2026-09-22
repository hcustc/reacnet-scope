---
status: accepted
date: 2026-09-21
---

# Qualify candidate events and check selected history

The user authorized implementation following the chlorophenol case study.
This refines ADR-0013/15/16: discovery and continuous history remain separate;
candidate structural identity stays version 2 and report schema advances to
version 3. The prepared candidate adjacency advances to version 4 because
return gaps beyond the supported 100-frame window are grouped into bounded
buckets. Existing event indexes remain valid
for other tools; candidate discovery needs an explicit event-index rebuild.
Online requests never migrate or rebuild an index.

## Return evidence and views

Preparation identifies adjacent observed reverse occurrences only when complete
Species/atom partitions match in reverse and bond connectivity returns. It
distinguishes exact bond-order return from topology-only return. Intervening
events on any involved atom and same-transition ambiguity prevent association;
unresolved changes without atom IDs conservatively break preparation history.
These records do not classify noise or prove elementary chemistry. Topology
return is not relabeled as a strict Fast Recrossing Episode.

The default `persistent` view folds either side of a recorded return within
`return_window_frames=3`, with explicit `return_basis=topology`. Users can choose
exact bond-order return or another window of 1–100 analyzed-frame intervals.
No physical-time threshold is inferred. The raw view retains all occurrences.
A carried edge is omitted only when ALL its support is folded; mixed and
unclassified evidence remains eligible. Low frequency and short occupancy
alone do not remove an edge. Filtering precedes adjacency LIMIT.

Step evidence retains raw support and reports original, folded and retained
counts. Actual event graphs use recorded bonds; when atom-element mapping is
absent they label atom IDs instead of inventing elements from a representative.
No chemical score or frequency ranking is introduced.

## Selected history check

The API, CLI `--check-top` (0–10), and Dash selected-route button provide a
separate bounded check, default 1000 states/five seconds per route. Identity
and discovery order do not change. Follow only the first consuming occurrence,
retain local dominant-carrier ties, require increasing Transitions and exact
carrier compatibility, and report the continuing anchor intersection.

This first implementation explicitly selects `all_atoms`, reporting selected
anchors, retained count/fraction and intactness without a hidden retention
fraction. It does not claim to implement the broader configurable anchor and
retention-policy interface. Results are `chain_found` (one witness),
`not_observed_within_evidence` (seed branches exhausted without a witness or
barrier), and `inconclusive` (missing evidence, ambiguity, or budget).

Existing first-consumption indexes store event endpoints, not certificates for
every intervening frame. Matching exact states at the same analyzed frame can
establish an adjacent handoff. Longer gaps without a per-frame continuity
certificate are inconclusive even if endpoints match. This restriction prevents
miso-hidden changes or interrupted reappearance from being called continuous.
General gap-spanning segment certification remains a separate extension.

The interval to a recorded first consumer is sampled follow-up, not proven
uninterrupted residence; no successor does not mean a stable product.

JSON/CSV retain view, window, basis, source revision, budgets, quality and any
selected-history result. Dash commits require matching dataset, validation
request and parent search request; services recheck revision around reads.

Regression cases cover return-only routes, short-lived real continuations,
mixed support, windows, topology/exact distinctions, same-transition ambiguity,
missing follow-up, bond incompatibility, budgets, stale revisions, exports and
late results. Real-data acceptance uses the preserved case-study source.
