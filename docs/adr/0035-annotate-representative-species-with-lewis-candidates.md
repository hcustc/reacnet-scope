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
- Use the independent `neutral-main-group-milp/v5` core with the existing SciPy and
  RDKit dependencies. The RNG prototype mentioned in the issue was unavailable in
  the inspected local checkout; this implementation does not import an unpublished
  RNG interface or claim numerical equivalence with that prototype.
- The model supports H/B/C/N/O/F/Si/P/S/Cl/Br/I. Each connected fragment must be
  neutral. Unknown charge requires an explicit neutral assumption; known nonzero
  fragment or requested net charge is unsupported. Any specified spin multiplicity
  is unsupported. Formal charges are restricted to -1, 0, +1. Neutral carbon can
  partition its nonbonding electrons between lone pairs and unpaired electrons;
  the optimizer selects one assignment, without identifying spin or ground state.
- Exclude positive F/Cl/Br/I charges. Permit negative charges only on N/O/halogens
  or carbon with exactly one heavy-atom neighbor. Each charged atom must participate
  in a one-to-one opposite-charge pairing along an existing edge. Recheck these
  restrictions after RDKit sanitization. These are deliberately narrow model-domain
  assumptions, not universal chemistry; they exclude valid ionic representations.
- Replace weighted penalties with lexicographic integer minimization, in this order:
  charged-atom shell deficit, small-ring unsaturation, model unpaired electrons,
  total shell deficit, number of charged atoms, unmatched eligible aromatic six-rings,
  expanded-shell excess, charge-location electronegativity. There is no separate
  non-carbon shell objective: neutral C/N/O shell deficits share one total. Shell targets
  are a duet for H, a sextet for neutral B, otherwise an octet; allowed P/S expanded
  states remain versioned in the core. Lock each optimum before solving the next
  objective, sharing the remaining wall-time/node budget across all stages.
- For 3/4-member cycles, count each triple bond and each adjacent pair of double
  bonds once, including cycles omitted by a ring basis. Minimize this count before
  unpaired electrons; isolated ring double bonds, exocyclic double bonds and acyclic
  multiple bonds are not penalized. This is a naming preference, not strain energy
  or a blanket prohibition: the higher-priority charged-shell objective can still prevail.
- After minimizing charge count, prefer alternating single/double assignments on
  eligible chordless six-membered C/N cycles. With all hydrogens explicit, eligible
  carbon has degree three and nitrogen degree two; each selected ring atom must be
  neutral, closed-shell and octet-complete. This bounded six-pi naming preference
  is not a general aromaticity detector or Clar-sextet model. Input bond orders and
  aromatic flags do not decide eligibility. Hydrogen counts, valence states and
  earlier objectives can prevent a ring from receiving the preference.
- Cycle discovery shares the structure wall-time budget and admits at most 64
  eligible cycles. Exceeding either bound returns an explicit timeout/truncation
  without a candidate. `eligible_six_ring_count` reports the number considered;
  schema remains v2 and method metadata publishes the rule and cap.
- As the final tie-break, minimize the sum of formal charge times integer-scaled
  Pauling electronegativity. At fixed earlier objectives this favors negative
  charge on more electronegative atoms and positive charge on less electronegative
  atoms. Publish the table and scale in method metadata. It cannot trade shell
  satisfaction or introduce additional charges to improve this last objective.
- Preserve the selected atom-level charge, unpaired electrons, lone pairs, isotope
  and integer bond orders in `model_graph`. Indices are local zero-based diagram
  indices, never RNG Atom IDs. Draw from this graph without reinferring radicals
  from SMILES; show neutral bonded carbon lone pairs as an LP annotation.
  `candidate_smiles` remains a searchable serialization that may lose electron
  pairing, and `radical_electrons` remains its RDKit representation property.
  `model_unpaired_electrons` is the selected model assignment, not a spin measurement.
- Check the serialized candidate with RDKit and verify composition, connectivity
  and fragment charge are preserved. This is a representation check, not scientific
  validation. One optimum neither enumerates all alternatives nor establishes a
  unique electronic state or resonance class. Export named `model_objectives`
  instead of the old weighted `model_score`.
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

## PR review validation and v3 selection

v2 addressed [positive-halogen candidates](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6702833099)
while retaining v1's weighted objective. The [next review](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6703323546)
showed that the radical penalty still favored carbon cations with incomplete shells,
and that the earlier reference set overrepresented halogenated closed-shell species.

v3 admits neutral-carbon lone-pair assignments and explicitly prioritizes avoiding
charged shell deficits and preserving heteroatom shells. It returns neutral cumulene
representations for the four reported carbon-chain examples while retaining the
charge-separated CO representation. This is a naming convention under declared
assumptions, not evidence that the selected carbene arrangement is a ground state.
The reference collection includes triplet C2/C2O assignments that v3 does not match.

The [reference validation](../lewis-annotations.md#参考验证) now publishes
v1, v2, the previously unreported combined experiment, and v3 on the same denominators,
plus predeclared small-C/N/O and reactive-fragment strata. v3 has lower aggregate
strict agreement than v2 (7449 versus 7487 of 7775), although it removes the observed
carbon-charge pathologies and preserves some previously lost lone-pair assignments.
This tradeoff prevents claiming a general accuracy improvement or validated filter.
The historical combined experiment (its patch is kept in the git history of PR #73,
commit `53ba837`) remains a comparison, not another selectable production method.

Result schema v2 introduces the model graph and named objectives. Method v3 and
schema v2 invalidate older prepared annotations; users explicitly rebuild them.
Original Species and reaction identities are never migrated. Real-trajectory and
spin-resolved validation remain deferred.

## Charge-location and small-ring review (v4)

The [remaining review examples](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6703813400)
reproduced two omissions: no tie-break for equal-charge-count representations, and
no small-ring multiple-bond penalty. The two charge-location cases, one N2O naming convention and four ring
regressions pass with the two added priorities. CO and earlier acyclic carbon
examples retain their intended representations. These preferences do not identify
an electronic ground state.

A charge-only ablation and the full v4 model were replayed on the same 7775 entries.
Strict agreement is 7449 for both v3 and charge-only, and 7439 for v4. The cited
42-entry drop from v2 to v3 in CHOF/CHOCl is a net difference: 200 losses and 158
gains. None of the 200 losses is an equal-charge-count location error; 188 v3
candidates are uncharged, and 12 introduce charges absent from the reference.
Ring penalties also change tied bond/radical assignments, including some that
already had zero small-ring penalty. Keep these regressions visible in the
[validation report](../lewis-annotations.md#参考验证), rather than treating
the two fixes as a general reference-accuracy improvement.

Method v4 invalidates v1/v2/v3 annotations. The result schema stays v2; its named
objectives and method metadata expose the new rules. No source identity changes.

## Oxygen radicals and resonance interpretation (v5)

The [next review](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6705484722)
identifies an element-dependent bias: satisfying neutral O shells before counting
charges moves peroxy radicals onto carbon and can select non-aromatic phenoxyl
representations. It also corrects the earlier N2O example: both octet-complete
charge-separated representations are valid resonance drawings, not a right/wrong pair.

Four fixed-input comparisons isolate v4, removal of the non-carbon preference,
aromatic preference alone, and their combination. Removing the element bias restores
all 12 reported peroxy references; the combination additionally selects the readable
aromatic phenoxyl representations. Existing CO, carbene, halogen and small-ring
regressions pass. Neutral carbon lone-pair states and the charged-shell priority remain.

Strict reference agreement is 7439/7775 for v4 and aromatic-only, and 7371/7775 for
unbiased-only and v5. Of 93 newly non-matching entries, 91 have a single two-bond
radical shift with unchanged atom-level charge and lone-pair counts. Two more complex
electron assignments remain unclassified. These are reported without erasing strict
mismatches or claiming identical energies/electronic states. The benchmark separately
labels the two explicitly accepted N2O resonance forms; other mismatches are not
called chemical errors. See the [report](../lewis-annotations.md#参考验证)
for denominators, every peroxy entry, remaining differences and reproducible ablations.
A separately reported pinned SABIC aromatic library improves from 263/365 to 281/365,
including its phenoxyl and phenylperoxy references. It is not pooled with the original
7775-entry denominator. Two fused-radical entries expose limitations of the comparator
that clears radical counts; they agree after ordinary normalization retaining the
assigned radicals. The report retains both discrepancies explicitly.

Method v5 invalidates earlier prepared annotations. Original Species identities,
reaction evidence and alias-to-original handoff remain unchanged. The result is one
readable model candidate, not a unique electronic-state determination or a filter.
