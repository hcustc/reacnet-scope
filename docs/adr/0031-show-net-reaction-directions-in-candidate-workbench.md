---
status: accepted
date: 2026-09-26
---

# Show net reaction directions in the candidate workbench

The user requested that the pathway graph show net conversion after comparing
111 forward occurrences with 110 reverse occurrences. Keeping both arrows in
the default graph obscures the net contribution. The user also rejected treating
a repeated Species in a graph as evidence that a concrete molecule returned.

This decision supersedes the Dash default return-folding view in ADR-0017.
Its occurrence-level return records and opt-in folding remain available.
It extends ADR-0013/0016/0026 with an explicit direction filter; per-step atom
transfer, candidate identity and separate continuity validation remain in force.

## Direction and counts

- Dash defaults to `direction_view=net`. `observed` displays the recorded
  directions. Python and `candidate-search` retain `observed` as the compatible
  default and accept the same explicit `direction_view` / `--direction-view`.
- A strict reverse swaps the complete canonical reactant and product Species
  multisets, preserving multiplicity. Carried endpoints, formula, shared
  participants or layout position cannot establish a reverse pair.
- `forward_count` is the Reaction Type's original occurrence count;
  `reverse_count` is its strict reverse's count, or zero when absent;
  `net_count = forward_count - reverse_count`. Net view expands only positive
  net directions. A self-reverse equation has net zero. This is not an operation
  that selects surviving occurrences or modifies raw evidence.
- Counts cover all recorded transitions in the current published revision,
  including unresolved occurrences. This scope is explicit as
  `count_scope=published_revision_all_transitions`; no time-window selection,
  physical-time rate, or future direction is inferred. Per-carrier supporting
  occurrences remain separate from full Reaction Type counts.
- Net view always uses raw counts and effective `quality_view=raw`. Temporal
  return folding is offered only with the observed-direction view, and remains
  independent of net counts. No additional significance threshold is applied.

## Search, graph and evidence

Filtering belongs in indexed local adjacency queries before pagination and
search budgets. Reverse counts use the published reaction primary key even
when the reverse direction lies outside the displayed page or lacks matched
carrier transfers. No index rebuild, raw-source scan or global graph is needed.
Forward exploration, predecessor exploration and two-anchor search share this
rule. A negative direction is omitted, never reversed into an inferred transfer.

The net graph labels reaction diamonds with net counts. Step details and
JSON/CSV retain forward, reverse, net and original carrier-support counts;
event drill-down continues to expose all original supporting occurrences.
Continuing, paging and returning through browsing history retain the submitted
query's semantics. Changing controls takes effect on a new search.

A graph cycle, including A → B → C → A, is not occurrence-level return evidence.
Net filtering does not guarantee an acyclic network and does not hide multi-step
cycles. Ordinary route enumeration retains its existing simple-path constraint
as a search boundary, not a chemistry claim. Single-step browsing may revisit a
Species and display that browsing history.

The indexed report advances from v4 to v5. Candidate structural identities and
adjacency version 5 remain unchanged. Existing reports lacking `direction_view`
keep their observed-direction meaning; they are not silently relabeled net.
