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

**Reaction Type**:
A normalized reactant-to-product equation shared by any number of reaction occurrences.
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

**Molecular Evidence**:
Frame-specific species, atom membership, and bond structure used to associate reaction occurrences with molecular participants.
_Avoid_: Reaction evidence, trajectory coordinates

**Current Dataset**:
The one ReacNetGenerator dataset whose evidence is available to the ordinary analysis tools at a time. Selecting datasets for a cross-condition comparison does not make them current.
_Avoid_: Loaded dataset, managed dataset

**Dataset Candidate**:
A ReacNetGenerator dataset identified and inspected in the dataset selector before the user explicitly makes it current. Inspecting a candidate does not change any analysis context.
_Avoid_: Pending dataset, loaded dataset

**Analysis Capability**:
A user-visible analysis operation that the current dataset can support with its available evidence and prepared indexes. Capabilities become available independently, so a dataset has no single "all ready" state; file and index states explain capability availability rather than replace it.
_Avoid_: File completeness, index readiness

**Element Distribution Evolution**:
A time-varying distribution of species grouped by atom count for a user-selected element, optionally filtered by other elements discovered in the dataset. Carbon may be offered as a preset when present, but it is not a domain boundary.
_Avoid_: Carbon-number evolution, C/O/Cl composition evolution

**Preparation Task**:
A long-running process that derives the index for one analysis capability from a specific dataset revision. It remains attached to that dataset and source revision even when the user makes another dataset current.
_Avoid_: Page task, current-dataset task
