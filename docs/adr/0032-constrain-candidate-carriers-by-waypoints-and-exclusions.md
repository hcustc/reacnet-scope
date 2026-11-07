---
status: accepted
date: 2026-10-11
---

# Constrain Candidate carriers by ordered waypoints and exclusions

The user approved ordered intermediate Species and excluded main-route Species
as search controls. A shortest-only search mode is explicitly outside this change.
This extends ADR-0026's anchor modes and ADR-0031's query report; their existing
direction defaults and ADR-0016's atom-transfer rule remain in force.

## Decision

- `via_species` is an ordered list of exact intermediate Species for two-anchor
  search. Only the carried sequence satisfies it; co-participants do not. Reject
  duplicate waypoints, endpoint waypoints, and waypoints in single-anchor modes.
- `excluded_species` applies to carried Species in all three modes. It must not
  remove co-reactants, coproducts, stoichiometry, counts, or underlying evidence.
  Filter indexed adjacency before pagination and traversal, not returned paths.
- Each list is bounded to 100 entries and validated against the current published
  Candidate species index. Reject invalid/unknown labels and exclusions of an
  anchor or waypoint; normalize exclusions to a sorted unique list.
- Enumerate complete simple routes with a per-prefix waypoint position and visited
  Species set. Independently reachable legs are insufficient: concatenation can
  repeat Species or reach waypoints out of order. Relaxed distances to remaining
  stops may prune prefixes, but do not establish constrained reachability.
- A found complete route proves constrained reachability. An empty, budget-limited
  search remains inconclusive; only completed traversal can establish absence
  within the submitted constraints and optional horizon. Execution budgets still
  bound adjacency, frontier, prefixes, candidates and time.
- Constraints belong to the query, not Candidate identity. They survive JSON/CSV
  and hypergraph provenance export. Changing only constraints never renames an
  otherwise identical Candidate and does not imply Continuous MD Support.
  CSV includes `schema_version` and `record_type=step`; empty results carry one
  `record_type=query` row with query/provenance/status and blank route/step fields.
- Dash selects exact Species, shows waypoint order and applied constraints, and
  clears selections on dataset changes. Continuing, paging and going back reuse
  submitted queries; unsubmitted edits only take effect on a new search.

## Compatibility

The indexed report advances to `reacnet-scope/indexed-candidates/v6`. Missing
constraint fields in older reports mean no waypoint/exclusion constraints.
Adjacency remains version 5; no preparation rebuild, new dependency, or raw-source
scan is required. Python accepts `via_species` / `excluded_species`; CLI exposes
repeatable `--via-species` / `--exclude-species`. Empty lists preserve prior search
behavior. Species-connectivity-only exploration keeps its existing evidence limit.
