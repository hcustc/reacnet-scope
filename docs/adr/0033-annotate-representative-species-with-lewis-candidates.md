---
status: accepted
date: 2026-11-07
---

# Annotate representative Species with bounded Lewis candidates

## Context

[Issue #72](https://github.com/hcustc/reacnet-scope/issues/72) requests optional
Lewis candidate naming before a later structure-screening feature. A representative
RNG SMILES can retain a different bond-order assignment from a selected molecular
instance. Replacing its label globally would change Species and reaction identities.

## Decision

- Preserve every original Species, Reaction Type, Occurrence, count and raw file.
  A Lewis Candidate Annotation is a separate, revision-bound inferred representation.
  Alias search returns the original members, including multiple Species with the
  same candidate. Opening a member passes its original identity to analysis.
- The first delivery processes complete representative SMILES from the reaction
  and species catalogues. It does not enumerate Molecular Evidence or audit every
  instance. `miso=0/1` use representative connectivity; `miso=2` explicitly covers
  only the selected representative, and unknown `miso` has unknown group coverage.
  No group-level claim follows from a representative result.
- Preserve all SMILES-defined atoms and hydrogen counts. Materialize implicit
  hydrogens only when the original SMILES valence rules resolve their counts;
  otherwise report incomplete input. Ignore original bond orders and stereochemical
  annotations for this inference, but retain the original label and its provenance.
- Use the independent `neutral-main-group-milp/v2` core with the existing SciPy and
  RDKit dependencies. The RNG prototype mentioned in the issue was unavailable in
  the inspected local checkout; this implementation does not import an unpublished
  RNG interface or claim numerical equivalence with that prototype.
- The model supports H/B/C/N/O/F/Si/P/S/Cl/Br/I. Each connected fragment must be
  neutral. Unknown charge requires an explicit neutral assumption; known nonzero
  fragment or requested net charge is unsupported. Any specified spin multiplicity
  is unsupported. Formal charges are restricted to -1, 0, +1; atom-state rules,
  expanded P/S shells and heuristic weights are versioned in the core.
- Positive F/Cl/Br/I formal charges are excluded from the v2 candidate domain,
  including a check after RDKit sanitization. This is a conservative model
  restriction, not a universal statement about halogen chemistry. Retain the
  weighted objective and expose its weights in method metadata.
- Solve for one minimum-penalty assignment of single/double/triple bond orders,
  formal charges and radical electrons. Check the serialized candidate with RDKit
  and verify composition/connectivity/fragment charge are preserved. This is a
  representation check, not scientific validation. One optimum neither enumerates
  all alternatives nor establishes a unique electronic state or resonance class.
- Record optimization completion separately from candidate enumeration.
  `candidate_search_complete=false` explicitly means alternatives were not
  exhaustively enumerated, even when optimization completed. Do not publish a
  time/node-limited incumbent as a generated name. All unsupported, incomplete,
  infeasible and budget-limited outcomes preserve the original name.
- Preparation streams representative sources into a bounded catalogue and reuses
  equal canonical connectivity under the same options. Default limits are 150
  atoms, 5 seconds and 10,000 solver nodes per structure; 10,000 Species and 300
  seconds for the whole task. These are limits, not claims of constant runtime.
- Publish annotations atomically in Dataset Workspace. Whole-task failure,
  cancellation, source change or budget exhaustion keeps the previous publication.
  Incomplete builds restart on retry. Per-structure failure outcomes are published
  as outcomes, never interpreted as fake Species. Annotation preparation is explicit
  and is not included in automatic or `prepare build all` preparation.
- Bind publication and lookup to source signatures, bounded processing metadata,
  method/RDKit/SciPy versions and submitted options. Each publication has an identity
  so paged exports cannot mix rebuilds. Online search/detail/export read prepared
  rows; they never solve or scan raw sources.
- Python, CLI and Dash share the result and export contract. Dash requires the
  neutral assumption before submission, keeps tasks attached to their original
  dataset, and rejects late query results from other datasets or newer requests.

## Deferred

Group expansion for `miso=2`, supplied raw molecular-graph input, richer ion/spin
models, automatic species filtering and scientific validation against a curated
real-trajectory set remain separate work. This annotation feature does not
constitute a validated filter or change candidate-route eligibility.

## PR review validation and v2 selection

The [PR #73 feedback](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6702833099)
reproduced positive-halogen candidates for small halocarbon fragments in v1.
Increasing the charge penalty alone also changed the intended CO representation.
We therefore tested the positive-halogen restriction independently, as well as a
larger exploratory combination of negative-charge-site restrictions, adjacent
charge pairing and lexicographic objectives. The latter did not establish better
strict reference agreement and is not enabled by this revision.

The selected minimal change removes the observed positive-halogen candidates and
improves strict bond/formal-charge agreement on the pinned reference collection;
it still disagrees with some reference representations and cannot identify spin
or carbene electronic states. Local unpaired-electron/lone-pair agreement is a
separate, weaker result than establishing the electronic state. See the
[reference validation](../research/lewis-reference-validation.md) for denominators,
exclusions, sources and reproduction. The result schema remains v1; the method
changes to v2, invalidating v1 prepared annotations through the existing version
check. No original Species or reaction identity is migrated.
