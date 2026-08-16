# Derive DFT initial geometries from matched occurrences

ReacNet Scope may derive a DFT Initial Geometry only from a `matched` Reaction
Occurrence with exact Molecular Evidence. Reactants use the occurrence's exact
before timestep and products use its exact after timestep; intermediate viewer
frames are not assigned molecular identities or presented as transition states.

The exporter reconstructs each selected complete Molecule Instance from its
side-specific bond graph using minimum-image displacements, places multiple
participants in a consistent reaction-contact image, and translates the
resulting non-periodic cluster without rotation or optimization. Elements and
the source Å unit must be explicitly confirmed. Charge and multiplicity are
optional user-supplied metadata and are never inferred.

DFT geometry packages are derived calculation artifacts with their own schema
and provenance. They do not change or replace the reproducible event evidence
package.
