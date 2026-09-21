---
status: accepted
date: 2026-09-21
---

# Add an indexed Candidate task to Reactions & Events

The user approved a first-version Web workflow for selecting exact Species,
searching target-constrained or exploratory routes, comparing routes, inspecting
Step Evidence, and exporting results. Add this as a task within Reactions &
Events, preserving the five primary workspaces. Species details can hand off
an exact start or target. Missing preparation remains visible.

This supersedes only ADR-0014's prohibition on promoting Candidate Discovery
in ordinary Dash. Manual Path Verification and Species Fate remain outside
ordinary Dash. Legacy page IDs still redirect without rerunning analysis.
ADR-0013's separation of discovery and Continuous MD Support remains binding.

## First-version scope

- One current dataset, one exact start, and optionally one exact target. Formula
  queries return separate exact identities and require selection when ambiguous.
- Only positive-count, normalized event directions are eligible. Event-index
  preparation materializes reaction adjacency, complete stoichiometry and a
  species lookup inside the unpublished build. Publication uses the existing
  source checks and atomic activation. Existing event indexes without this
  extension stay usable for old queries; candidate search explicitly requires
  an event-index rebuild. Online discovery never creates the extension.
- Breadth-first enumeration displays shorter routes first with deterministic
  identity ordering. This is presentation, not mechanistic ranking. Search,
  frontier, local adjacency, time and result limits report incomplete results;
  the maximum path length is a separate scientific query horizon.
- Target queries check an exact final step for each breadth-first prefix before
  expanding other products. Filtering uses the indexed reactant neighborhood;
  target matches are not restricted to its first adjacency page. Pending prefixes
  remain eligible for target checks after the expansion budget is spent. Target
  probes have their own bounded count (the `max_expansions` limit), and SQLite
  work shares the query deadline. Exports report probe and matching-row counts.
  These are execution changes and do not require rebuilding the version 1 index.
- Return each carried-product branch separately. Repeated carried Species are
  excluded from ordinary paths; bounded cycle-closure records remain in JSON.
- Step counts are individual event counts, never a full-route occurrence count.
  Side participants and repeated stoichiometric terms remain in every step.
- Known miso settings are sourced explicitly. With miso=1, labels are presented
  as RNG representatives; exact bond-state compatibility is not claimed.
- Continuous MD Support is `not_evaluated`; no unfinished verification control
  or composite/energy score is exposed. Event and trajectory inspection reuse
  existing tools with stable identities and revision checks.
- The new schema `reacnet-scope/indexed-candidates/v1`, Python service and
  `candidate-search` CLI share one implementation. Existing `candidate-paths`
  CLI/schema remain compatibility surfaces; their semantics are not relabeled.
- Requests and results carry dataset and request identity. Cancelled or
  superseded work cannot replace current results; changing datasets clears
  selections, results and event pages. Exports include source revision and limits.

Full multi-dataset comparisons, automated bond-state reconstruction, Continuous
MD Support and mechanism/kinetic ranking are outside this first version.
