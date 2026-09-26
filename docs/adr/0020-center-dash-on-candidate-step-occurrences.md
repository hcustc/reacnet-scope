---
status: accepted
date: 2026-09-21
---

# Center the Dash workflow on candidate steps and concrete occurrences

The separate evidence-inspection navigation placement is superseded by [ADR-0029](0029-open-analysis-from-species-reactions-evolution-and-events.md). Candidate-step evidence, concrete occurrence selection, stable handoff, return, and export decisions remain accepted.

## Context

The ordinary research task starts with a target Species and asks which observed
reactions can generate or consume it, which multi-step routes they can form,
and what evidence supports one selected step. The former product priority made
Molecular Lineage Explorer the development mainline. That ordering required
users to understand a continuity model before they could inspect the multiple
independent Reaction Occurrences supporting a candidate step.

A Candidate Path and a concrete molecule history answer different questions.
Candidate steps may be supported by different Molecule Instances, while a
lineage follows one selected instance and its atoms through recorded changes.
Failure to find a continuous multi-step history therefore does not invalidate
the Candidate.

## Decision

The ordinary Dash mainline is:

> exact target Species → Direct Reaction Channel or Candidate Path → selected
> reaction step → supporting Reaction Occurrences → structure/trajectory and
> evidence or DFT Initial Geometry export

Candidate Path remains a task in the reaction workspace. Its merged graph uses
Species cards and reaction diamonds; clicking a reaction diamond or either
adjacent edge selects the same complete Candidate step. The step view exposes
its independently observed occurrences, keeps stable `event_id` identity, and
allows previous/next navigation across bounded evidence pages. List order does
not infer order among occurrences in one Transition.

Opening an occurrence hands the selected `event_id`, Candidate query identity,
signature, step and source context to the structure workspace. The receiver
re-reads the occurrence from the published evidence index and clears previews
bound to an earlier occurrence. It preserves a return path to the Candidate
step. Missing coordinates disable only coordinate-dependent actions; matched
molecular evidence remains inspectable.

Molecular Lineage Explorer is presented in the interface as **分子变化追踪**.
It is an optional action on a selected occurrence participant and can operate
without coordinates when its segment evidence is available. Continuous MD
Support remains an optional Candidate check. Neither function determines
Candidate existence, step support counts, structure inspection, or geometry
export eligibility.

DFT Initial Geometry continues to bind both sides to one matched occurrence's
exact before/after frames and existing unit, element, PBC and completeness
checks. This decision adds no transition-state or mechanism claim.

The five-workspace architecture is retained. The former “轨迹与谱系” display
name becomes “结构与轨迹”; the lineage domain object, schemas, API names and
the scientific rules in ADR-0018 and ADR-0019 do not change.

## Supersession

[ADR-0029](0029-open-analysis-from-species-reactions-evolution-and-events.md)
subsequently replaces this decision's navigation placement of the Candidate task.
The step, occurrence, return and evidence rules below remain accepted.

This ADR supersedes only ADR-0018's statement that Molecular Lineage recovery
is the product development mainline. ADR-0018's prepared substrate, traversal,
identity, limits and evidence semantics remain accepted, as do ADR-0019's
anchor and provenance rules. ADR-0013 and ADR-0017 continue to define the
separation between Candidate discovery, step evidence and continuous support.

## Consequences

The common workflow exposes multiple occurrences before optional continuity
analysis. Candidate and lineage graphs stay visibly distinct, so independent
step evidence is not drawn as one sampled molecular history. Cross-page state
must bind the selected occurrence and source revision, and automated tests must
cover non-first occurrence selection, evidence-page boundaries, stale-preview
clearing, return navigation and missing-coordinate behavior.
