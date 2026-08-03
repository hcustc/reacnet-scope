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

**Aggregated Reaction Record**:
The count of one reaction type detected within one transition.
_Avoid_: Reaction occurrence, event

**Reaction Occurrence**:
One independently detected atom-connected change within a transition, whether or not its molecular participants can be resolved.
_Avoid_: Aggregated reaction, reaction type

**Occurrence Identity**:
A stable identity for a reaction occurrence derived from its transition, reaction type, and molecular participants rather than its source artifact layout.
_Avoid_: Source row, HDF5 row ID

**Reaction Evidence**:
Transition-level reaction types and occurrence counts sufficient for chronological event search and statistics.
_Avoid_: Reaction network, molecular evidence

**Species Abundance Evidence**:
Analyzed-frame species identities and their abundance counts, sufficient for species lookup, time evolution, and abundance-based screening.
_Avoid_: Molecular evidence, species index

**Intermediate Candidate**:
A species selected by explicit abundance-shape and lifetime criteria for further evidence review; it is not a confirmed mechanistic intermediate.
_Avoid_: Intermediate, confirmed intermediate

**Candidate Path**:
A bounded sequence of reaction types that is reachable in aggregated reaction evidence and remains a hypothesis until occurrence evidence is reviewed.
_Avoid_: Confirmed pathway, reaction mechanism

**Event Path**:
A temporally ordered sequence of reaction occurrences linked by continuity of a molecular instance and its atom lineage in the available evidence; it does not establish causality or a unique mechanism.
_Avoid_: Confirmed mechanism, mechanistic proof

**Molecular Evidence**:
Frame-specific species, atom membership, and bond structure used to associate reaction occurrences with molecular participants.
_Avoid_: Reaction evidence, trajectory coordinates

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
