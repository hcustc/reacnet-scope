---
status: accepted
date: 2026-11-09
---

# Make direct channels the mainline and Candidate Path an optional hypothesis tool

## Context

The design baseline made "explore Candidate Path" a required step of the
release-critical path, and ADR-0020 listed Candidate Path as a co-equal entry
to the ordinary mainline. The questions researchers actually bring are narrower:
whether a target Species appears, when and how much; which observed reactions
produce or consume it; whether a concrete occurrence can be inspected in the
trajectory; and where selected atoms came from or went. The chain from an exact
Species through Direct Reaction Channels to concrete Reaction Occurrences and
then to trajectory, evidence export or Molecular Lineage answers these
questions, and every step resolves to recorded RNG evidence.

A Candidate Path is a network-level route whose steps are each supported by at
least one matched occurrence, but those occurrences may come from different
Replicates, times and Molecule Instances. It is a hypothesis grounded in
per-step evidence, not an observed molecular history. Making it a mandatory
gate couples the release of the evidence chain to the scope, budgets and scale
gates of route search. The current Dash Species detail already presents
"相关反应" as the primary action and "探索路线" as a secondary one.

## Decision

The release-critical ordinary mainline is:

> select RNG data → exact Species detail → Direct Reaction Channel and a
> selected Reaction Type → concrete Reaction Occurrence → structure / local
> trajectory, evidence package, DFT Initial Geometry, or Molecular Lineage →
> return to the originating position

Candidate Path Discovery remains a formal, maintained optional tool for
forming multi-step route hypotheses. It is not frozen. It is opened explicitly
from the Species detail or with independently entered exact endpoints, and it
is never a prerequisite of the mainline or of any other capability. Its
discovery, identity, step-evidence, net-direction, constraint and Continuous MD
Support contracts (ADR-0013, ADR-0016, ADR-0017, ADR-0026, ADR-0031,
ADR-0032) and its step-to-occurrence handoff, return and export rules
(ADR-0020) are unchanged.

A Candidate step's supporting occurrence opens the same shared event detail as
the mainline, so it can be inspected and exported as DFT Initial Geometry for
downstream quantum-chemistry checks. These checks answer different questions:

- DFT on one selected occurrence's before/after geometry assesses whether that
  step is chemically plausible as an elementary reaction in that local
  environment. Different occurrences of one Reaction Type may differ, so the
  chosen occurrence identity stays bound to the result.
- Continuous MD Support assesses whether a concrete molecular provenance
  realized the selected route in order within the recorded MD.

Neither substitutes for the other, and neither, nor both together, turns a
Candidate into a confirmed mechanism. QC Handoff Readiness keeps its existing
meaning: `ready` does not establish an elementary step or a validated
transition state, and its expansion remains frozen.

## Supersession

This supersedes only the statement in
[ADR-0020](0020-center-dash-on-candidate-step-occurrences.md) that Candidate
Path is a co-equal entry of the ordinary mainline, and the baseline's
release-critical path through Candidate Path. ADR-0020's step, occurrence,
handoff, return, Molecular Lineage and DFT export decisions remain accepted.
[ADR-0029](0029-open-analysis-from-species-reactions-evolution-and-events.md)
and [ADR-0030](0030-use-local-species-focus-in-dash.md) navigation and local
Species focus are unchanged. No scientific result schema, CLI/API identity,
index preparation rule or Candidate structural identity changes.

## Consequences

Release acceptance of the mainline no longer depends on Candidate Path
Discovery. Candidate Path keeps its own capability acceptance, including the
scale gates for Candidate Discovery and Continuous MD Support. Interfaces and
documentation must present Candidate results as route hypotheses with
independent step evidence, and must not place route exploration ahead of
direct channels in the Species detail.
