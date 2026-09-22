---
status: accepted
date: 2026-09-19
---

# Converge the ordinary Dash surface around five workspaces

The ordinary Dash product surface is organized around five workspaces:
Dataset, Species & Trends, Reactions & Events, Trajectory & Lineage, and
Compare. Tool pages may retain their existing internal page identifiers, but
only these workspaces appear as primary navigation and dataset-overview
destinations.

Candidate Path Discovery, manual Path Verification, and Species Fate Analysis
are frozen as independent ordinary-Dash tools. Their page layouts and
page-specific callbacks were retained during the compatibility transition and
removed after the P5 reference audit and real-case acceptance. Their services,
CLI commands, Python APIs, and exports remain available, but the ordinary
navigation and task launchers do not promote them. Restoring an old session to
one of these page IDs resolves to the owning workspace and does not
automatically start analysis. Candidate aggregate scoring, energy-CSV ranking,
and incomplete Continuous MD Support controls are not shown on the ordinary
surface.

Ordinary Direct Reaction Channel queries do not calculate an Apparent Rate
Constant Estimate. Physical-time confirmation, event counts, observation
windows, and existing compatibility fields remain available, while default
rate setup and rate-column controls leave the ordinary workflow. Event-bound
DFT Initial Geometry export and the checks required to make that export
auditable remain in Trajectory & Lineage; this does not create a general QC,
TS/IRC, theoretical-rate, or job-scheduling platform.

## Consequences

- Current Dataset remains singular and session-scoped; Compare keeps its own
  source selection and never changes it.
- Missing evidence, preparation requirements, valid empty results, and
  unevaluated support remain distinct and visible within the five workspaces.
- Freezing a page does not remove shared event, continuity, time, box, PBC, or
  geometry evidence and does not alter accepted scientific identities or
  result schemas.
- Public `reacnet-scope` commands and the `reacnet_scope` Python API remain
  compatible by default. Removing them requires a separate migration and
  version-bound decision.
- The P5 reference audit removed hidden compatibility layouts and callbacks;
  legacy page IDs are handled only as redirects to the owning workspace.

This decision replaces the navigation and ordinary-product-surface portion of
the earlier software design baseline. It does not supersede the scientific
contracts in ADRs for occurrence identity, Candidate Path Discovery,
Continuous MD Support, apparent rates, Species Fate, or DFT geometry.

## Partial supersession

[ADR-0015](0015-add-indexed-candidate-task-to-reaction-workspace.md) reintroduces
Candidate Discovery as a task within Reactions & Events following explicit user
approval. The five-workspace organization and the other exclusions above remain.
