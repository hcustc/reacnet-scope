---
status: accepted
date: 2026-09-25
---

# Open analysis from Species, Reaction, Evolution, and Event entries

References below to a persistent research Species are superseded by [ADR-0030](0030-use-local-species-focus-in-dash.md). The five entries, local detail target, comparison source identity, event handoff, and return chain remain accepted.

## Context

The prior three-workspace Dash navigation placed independent reaction search,
abundance trends, element distribution, and event search behind Species research
tasks. A researcher with an exact Reaction Type, an abundance question, or an
event question should be able to start there. The Species summary, research
overview, and task row also repeated the same identity and actions.

## Decision

Show five peer entries: RNG Data, Species, Reactions, Evolution, and Events.
Species search leads to one exact Species detail. From that detail, route
discovery, one-step production/consumption channels, and abundance trends use
the detail Species as their explicit local target. A route can also be started
with independently entered exact endpoints. Independent Reaction Type and Event
queries do not require a research Species. Evolution exposes bounded cumulative
sampled-abundance ranking, trends, element distribution, and per-source Species
comparison. Reaction comparison belongs to Reactions.

A selected Reaction Occurrence from a Candidate step, Reaction result, or Event
query uses one detail surface bound to its stable event ID, Dataset Identity,
and source revision. It re-reads the published index before displaying evidence.
The detail can expand into the existing full-width inspection tools; closing it
returns to the result and selection. A bounded session return chain preserves
cross-entry context without treating display text or table position as identity.
Internal Dash page IDs and single mounted controls may remain while their
visible entry and title change.

Browsing a Species from another entry does not replace the research Species.
Comparison sources retain independent identities and do not switch Current
Dataset. Analysing a Species from another source requires an explicit validated
switch, after which the target is handed off only if its revision still matches.
The ranking metric is the sum of sampled-frame abundance counts, with the
recorded frame scope and a visible display limit. It is not current abundance,
peak abundance, or event frequency.

## Supersession

This replaces ADR-0027's separate Species research workspace and overview
placement, and ADR-0028's three analysis workspaces and task row. Their research
identity, local focus, source invalidation, independent comparison, and trend
decisions remain accepted. It replaces only the separate inspection-navigation
placement in ADR-0020. Candidate step evidence, stable occurrence handoff,
return, and export requirements remain accepted. ADR-0025's Dataset Library and
two-stage switch remain accepted. Scientific result schemas, CLI/API identities,
and index preparation rules are unchanged.
