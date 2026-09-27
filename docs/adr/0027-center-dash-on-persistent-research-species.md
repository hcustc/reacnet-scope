---
status: superseded
date: 2026-09-25
---

# Center the ordinary Dash workflow on a persistent research Species

The persistent research Species identity and its automatic assignment are superseded by [ADR-0030](0030-use-local-species-focus-in-dash.md). Navigation placement was superseded by [ADR-0029](0029-open-analysis-from-species-reactions-evolution-and-events.md). Exact Species identity, source invalidation, candidate evidence, and event handoff remain governed by the current design baseline and their respective ADRs.

## Context

The existing Dash Candidate and occurrence workbench already connects routes,
steps, concrete events, structure inspection and export. Its primary navigation
presented these as separate tools, while `selected_smiles` served both the
Species search selection and local focus when browsing a reaction participant.
That field could not represent a persistent research subject.

## Decision

Selecting an exact Species in a discovery result establishes the session research anchor bound to
the Current Dataset and source revision. Local Species, reaction and occurrence
focus do not replace it. Empty search selections do not clear it. Changing the
anchor or source revision invalidates dependent route, event and trend views.
Failed Dataset Candidate validation leaves the Current Dataset and research
anchor in place.

After selection, the researcher may open any applicable tool directly; the
Species research overview is an optional hub, not a required step. Candidate
route exploration is a primary action. It offers
target-only predecessor browsing, start-only continuation and a two-anchor
search with an explicit side for the research Species. Direct production and
consumption channels and single-source abundance remain nearby. Formula
reaction search, independent event search, element distribution and multi-source
comparisons retain tool entrances and do not require a research anchor.

Selecting a Candidate step opens its supporting occurrences beside the graph
when space permits. Opening a selected occurrence carries stable event, query,
step and source identities into evidence inspection. Returning preserves the
route and occurrence browser state. Narrow layouts stack graph and detail.
Candidate evidence, Continuous MD Support and mechanism validity remain
separate judgments.

## Supersession

This decision replaces only the navigation grouping and research-entry
placement in [ADR-0025](0025-imported-dataset-library.md), the placement of
Candidate Path under a separately named reaction workspace in
[ADR-0020](0020-center-dash-on-candidate-step-occurrences.md), and the earlier
target-Species plan's navigation and required-start instructions. ADR-0020's
step, occurrence and evidence handoff rules remain accepted. ADR-0025's Dataset
Library, two-phase switch and comparison-source independence remain accepted.
[ADR-0026](0026-search-candidates-by-anchor-mode.md) continues to define the
three Candidate search modes. No scientific result schema or public API changes.
