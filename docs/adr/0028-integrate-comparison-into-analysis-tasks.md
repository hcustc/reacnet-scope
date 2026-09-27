---
status: accepted
date: 2026-09-25
---

# Integrate comparison into analysis tasks

Navigation placement is partially superseded by [ADR-0029](0029-open-analysis-from-species-reactions-evolution-and-events.md): comparison now appears within peer Evolution and Reaction entries. References below to a persistent research Species are superseded by [ADR-0030](0030-use-local-species-focus-in-dash.md). Per-source Species selection, independent comparison without changing Current Dataset, and return to the current-data trend remain accepted.

## Context

Single-source abundance and multi-source Species comparison shared a scientific
purpose but required separate pages. A “comparison and distribution” workspace
also grouped this task with element distribution without a shared workflow.
Placing element distribution under Species discovery while abundance trends
were under Species research split the temporal analyses across workspaces.

## Decision

Keep RNG Data independent and expose three analysis workspaces: Species discovery,
Species research, and evidence inspection. Species discovery holds exact Species
search. Place element distribution evolution alongside abundance trends in Species
research, retaining temporal distributions and exact Species drill-down. Element
distribution remains usable without a research anchor.

Use one abundance-trend page in Species research. It defaults to current data;
adding comparison sources opens the existing per-source selection and results in
that page. Each source keeps explicit Species selection, identity, revision,
preparation status, and time conversion. Comparison does not switch Current Dataset
or replace the research Species. Return to current data preserves single-source
inputs and results. Independent comparisons do not require a research anchor or
Current Dataset. Remove the separate multi-file overlay controls from the ordinary
Dash flow; the core/API compatibility options remain available.

Retire the Dash batch-compare page and restore its session ID to evolution's
comparison view without executing a query. Reaction/condition comparison remains
in Species research. The batch-compare CLI/API and condition/Replicate statistics
are unchanged.

## Supersession

This replaces only the navigation placement of abundance comparison and element
distribution in [ADR-0027](0027-center-dash-on-persistent-research-species.md)
and the four-analysis-workspace grouping in the design baseline. Research anchors,
Dataset switching, independent comparison sources, scientific identities, units,
and result/export contracts remain unchanged.
