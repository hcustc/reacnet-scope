# ReacNet Scope

ReacNet Scope organizes ReacNetGenerator outputs into evidence that can be queried for reaction, molecule, and pathway analysis.

## Language

**Timed Evidence Source**:
An artifact that supplies frame-indexed reaction occurrences and molecule occurrences for event and pathway analysis.
_Avoid_: Event file, timeline file

**Analyzed Frame**:
A position in the trajectory sequence selected for analysis, with a mapping to its source timestep.
_Avoid_: Timestep, time index

**Transition**:
The interval from one analyzed frame to the next, within which reaction occurrences are detected without an internal ordering.
_Avoid_: Timestep, frame

**Species**:
A molecular structure identified within a dataset by its exact ReacNetGenerator SMILES; molecular formula and mass are searchable attributes rather than identity.
_Avoid_: Molecular formula, formula group

**Reaction Type**:
A directed, stoichiometry-preserving equation of exact species identities shared by any number of reaction occurrences; ordering within each reaction side is not significant.
_Avoid_: Reaction event, event row

**Direct Reaction Channel**:
A reaction type viewed relative to one focal species and classified as a production channel when that species is on the product side or a consumption channel when it is on the reactant side; it is a one-step query and does not recursively expand a path.
_Avoid_: Path Verification, Event Path

**Apparent Rate Constant Estimate**:
A dataset- and window-bound estimate for one directed Reaction Type under an explicitly reported stoichiometric mass-action model, calculated from exact Reaction Occurrence count divided by physical-time reactant exposure and, for second-order models, confirmed simulation-box volume. It is an auditable model-dependent estimate, not an intrinsic rate constant inferred from TP or net flux.
_Avoid_: TP rate constant, intrinsic rate constant, net-TP rate

**Aggregated Reaction Record**:
The count of one reaction type detected within one transition.
_Avoid_: Reaction occurrence, event

**Reaction Occurrence**:
One independently detected atom-connected change within a transition, whether or not its molecular participants can be resolved.
_Avoid_: Aggregated reaction, reaction type

**DFT Initial Geometry**:
A non-periodic, atom-mapped cluster derived from the exact before or after frame of one matched Reaction Occurrence by reconstructing selected complete Molecule Instances through periodic boundaries. It is an auditable starting geometry, not an optimized structure, transition state, reaction path, or complete quantum-chemistry job.
_Avoid_: DFT input, transition state, optimized geometry

**Occurrence Identity**:
A stable identity for a reaction occurrence derived from its transition, reaction type, and molecular participants rather than its source artifact layout.
_Avoid_: Source row, HDF5 row ID

**Reaction Evidence**:
Transition-level reaction types and occurrence counts sufficient for chronological event search and statistics.
_Avoid_: Reaction network, molecular evidence

**Species Abundance Evidence**:
Analyzed-frame species identities and their abundance counts, sufficient for species lookup, time evolution, and abundance-based screening.
_Avoid_: Molecular evidence, species index

**Event Path**:
A temporally ordered sequence of reaction occurrences linked by continuity of a molecular instance and its atom lineage in the available evidence; it does not establish causality or a unique mechanism.
_Avoid_: Confirmed mechanism, mechanistic proof

**Path Verification**:
The evidence check of one user-supplied sequence of exact reaction types against strict Event Path continuity rules; it does not discover, complete, score, or rank paths.
_Avoid_: Path search, mechanism prediction, automatic pathway analysis

**Molecular Evidence**:
Frame-specific species, atom membership, and bond structure used to associate reaction occurrences with molecular participants.
_Avoid_: Reaction evidence, trajectory coordinates

**Molecule Instance**:
A concrete molecular participant identified by one analyzed frame, exact Species, atom-ID set, and intramolecular bond set. The same stored molecule definition may recur in disjoint frame ranges, so a Species or molecule-definition ID alone is not an instance identity.
_Avoid_: Species, molecule definition, abundance trace

**Molecule Lineage**:
A bounded, bidirectional evidence graph that starts from one Molecule Instance and follows anchor atoms through their nearest resolvable Reaction Occurrences. It preserves co-participants and leaving fragments as context and does not establish a mechanism, causality, or a unique molecular history.
_Avoid_: Mechanism network, automatic pathway discovery, Species evolution

**Fast Recrossing Episode**:
A sequence in which the same anchor atom set returns within a configured number of analyzed frames to the exact same Species, atom-ID set, and intramolecular bond set. A persistent view may fold the sequence, but the raw Reaction Occurrences remain available.
_Avoid_: Reversible reaction type, equilibrium proof

**Species Fate Analysis**:
A bounded evidence analysis that follows the fixed anchor atoms of every eligible formation of one exact Species to user-defined absorbing endpoints or explicit censoring. It reports observed descendant outcomes and timing, not a final product, causal mechanism, or chemical rate.
_Avoid_: Automatic mechanism discovery, final-product prediction, kinetic analysis

**Formation Episode**:
The evidence record that begins when one matched product-side Molecule Instance newly forms the target Species with a fixed anchor set and ends after all anchor descendants are terminal or censored. A return with the same fixed anchor set remains inside the active episode.
_Avoid_: Reaction occurrence, independent experiment, abundance peak

**Active Descendant**:
A currently traceable Molecule Instance carrying a non-empty subset of one Formation Episode's fixed anchor set and not yet terminal or censored.
_Avoid_: Product Species, pathway node

**Endpoint Category**:
A user-named, mutually exclusive set of exact Species identities that acts as an absorbing outcome for matching Active Descendants.
_Avoid_: Formula group, SMARTS class, predicted product class

**Terminal Instance**:
The first Molecule Instance on one anchor-descendant branch that matches an Endpoint Category, together with its inherited anchors and first-passage evidence.
_Avoid_: Final product, terminal Species

**Descendant-complete Fate**:
The unordered multiset of Terminal Instances whose disjoint inherited anchor subsets exactly cover a Formation Episode's fixed anchor set.
_Avoid_: First hit, final mechanism, single product

**Fate Signature**:
The canonical cross-episode identity of a Descendant-complete Fate, derived from Endpoint Categories and their multiplicities rather than replicate-local Atom IDs.
_Avoid_: Event path, reaction mechanism

**Partial Fate**:
The observed Terminal Instances and unresolved anchor subsets of a Formation Episode that is not fully resolved.
_Avoid_: Completed fate, inferred fate

**Evidence Censoring**:
The explicit termination of an anchor-descendant trace because the available evidence, observation window, or declared analysis limit cannot support further continuity.
_Avoid_: No reaction, terminal product, negative result

**First-hit Fate**:
The Endpoint Categories first reached by any descendants of a Formation Episode in the earliest matching Transition; it is an auxiliary first-exit observation rather than the episode's Descendant-complete Fate.
_Avoid_: Episode fate, fastest mechanism

**Observed Fate Path**:
An atom-continuous route through recorded Reaction Occurrences from a Formation Episode toward a Terminal Instance or censoring point. It is observed evidence and does not establish causality, uniqueness, or an unobserved mechanism.
_Avoid_: Mechanistic pathway, predicted pathway

**Fate Query**:
A normalized Species Fate Analysis question consisting of a target, anchor policy, endpoint definition, observation window, and declared limits.
_Avoid_: Fate result, dataset revision

**Fate Result**:
A revision- and algorithm-bound report produced for one Fate Query, including complete statistics when available and explicit partial evidence otherwise.
_Avoid_: Fate query, timeless conclusion

**Current Dataset**:
The one ReacNetGenerator dataset whose evidence is available to the ordinary analysis tools at a time. Selecting datasets for a cross-condition comparison does not make them current.
_Avoid_: Loaded dataset, managed dataset

**Dataset Candidate**:
A ReacNetGenerator dataset identified and inspected in the dataset selector before the user explicitly makes it current. Inspecting a candidate does not change any analysis context.
_Avoid_: Pending dataset, loaded dataset

**Simulation Condition**:
A defined set of simulation inputs under which one or more independent datasets are compared as a statistical group.
_Avoid_: Dataset group, folder group

**Replicate**:
One independently generated dataset belonging to a simulation condition and serving as the unit for detection and variability statistics.
_Avoid_: File, run label

**Analysis Capability**:
A user-visible analysis operation that the current dataset can support with its available evidence and prepared indexes. Capabilities become available independently, so a dataset has no single "all ready" state; file and index states explain capability availability rather than replace it.
_Avoid_: File completeness, index readiness

**Element Distribution Evolution**:
A time-varying distribution of species grouped by atom count for a user-selected element, optionally filtered by other elements discovered in the dataset. Carbon may be offered as a preset when present, but it is not a domain boundary.
_Avoid_: Carbon-number evolution, C/O/Cl composition evolution

**Preparation Task**:
A long-running process that derives the index for one analysis capability from a specific dataset revision. It remains attached to that dataset and source revision even when the user makes another dataset current.
_Avoid_: Page task, current-dataset task

**Dataset Workspace**:
The recoverable state that ReacNet Scope derives or records for one dataset, kept separate from the ReacNetGenerator source artifacts and removable without deleting them.
_Avoid_: Dataset folder, source data, cache
