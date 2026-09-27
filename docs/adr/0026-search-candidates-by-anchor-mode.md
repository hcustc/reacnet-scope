---
status: accepted
date: 2026-09-24
---

# Search candidates by available Species anchors

ADR-0031 adds net-direction filtering and advances the indexed report to v5; anchor modes, route enumeration and adjacency version 5 remain in force.

The user approved three Candidate Path Discovery entries after reviewing the
search method: an exact start only, an exact target only, or both. This decision
supersedes ADR-0015's required start and shortest-first first-version display,
and ADR-0013's mandatory declarative `max_steps` for target-constrained search.
It does not change Candidate identity, Step Evidence, Continuous MD Support or
Path Verification.

## Decision

- Start-only and target-only searches expand one observed carried transfer at a
  time, with bounded adjacency pages. The user may continue from a selected
  result or browse another page. Target-only expansion
  reads predecessors of the recorded **forward** transfer; Reaction Types are
  never reversed. A target that is only a non-carried coproduct remains visible
  in Direct Reaction Channels, but does not create a Candidate predecessor.
- A target-only result is a network-level candidate precursor. Actual provenance
  of one target Molecule Instance requires occurrence/lineage evidence.
- Start-to-target search defaults to no path-length horizon. An explicit
  `max_steps` is an optional query constraint and must be reported. The finite
  simple-path rule prevents cycles but does not make complete route enumeration
  affordable; independent adjacency, target-probe, frontier, time, prefix and
  candidate-examination budgets remain mandatory.
- Reachability and alternative-route coverage are separate results. One
  discovered route establishes observed-network reachability in the selected
  evidence view. No connection may be reported only after the relevant graph
  was fully searched; a budget-limited miss is inconclusive. Hitting the display
  count does not end route examination. The display samples first-step branches
  in round-robin order and uses step count only within each branch; this is
  deterministic presentation, not chemical or mechanistic ranking.
- The prepared candidate index stores product-leading reverse adjacency and
  advances its version to 5. Existing event indexes need an explicit event
  rebuild for the new Candidate task; online analysis never rebuilds them.
  The indexed query report advances to `reacnet-scope/indexed-candidates/v4`.
  Route `candidate_signature` remains independent of mode and budgets.

## Consequences

Python, CLI and Dash use the same three modes. Exports include reachability,
route coverage, display truncation, query limits and source revision. The
`species_connectivity_only` degraded view remains separately labeled and does
not claim atom-supported material transfer. Selected-route continuous-history
checks remain independent of network discovery and display order.

This first implementation uses bounded local graph retrieval followed by
round-robin first-branch enumeration. It does not claim to rank chemical
importance or guarantee coverage when a budget is reached. Representative
large-data performance remains a separate release gate.
