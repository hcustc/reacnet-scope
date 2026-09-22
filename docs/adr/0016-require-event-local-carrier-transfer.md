---
status: accepted
date: 2026-09-21
---

# Require event-local atom transfer for ordinary Candidate routes

ADR-0017 adds return-evidence qualification, raw/persistent views, selected
history checks and candidate schema/index version 3; this local-transfer rule
remains in force.

Connecting Reaction Types only because one produces and another consumes the
same Species can create misleading shortcuts through ubiquitous H, Cl, or O2.
In the reported C6H5ClO case, the selected H product inherited no atoms from
the focal C6H5ClO reactant; it came from a co-reactant. Presenting that graph
connection as a generation route was therefore unacceptable.

For every matched Reaction Occurrence and focal reactant Molecule Instance,
Candidate preparation compares its atom IDs with every product participant.
Products with the greatest positive intersection are the event-local dominant
atom descendants; ties remain separate carried branches. A Candidate edge is
eligible only when at least one occurrence supports that exact focal Species,
product Species, and Reaction Type transfer. Step Evidence pages contain only
those supporting occurrences and report their shared-atom count.

This rule is local to each step. Adjacent steps still connect through exact
Species identity and do not need to use the same concrete Molecule Instance,
time-adjacent events, or one continuous atom lineage. Continuous MD Support
therefore remains a separate validation as decided by ADR-0013.

Evidence without molecular participant atom IDs may still expose explicitly
labelled Species-connectivity exploration, but it must not claim atom transfer
or be presented as a material-conversion route. The indexed Candidate schema
and signature algorithm advance to version 2, and version 1 event indexes need
an explicit rebuild. This decision refines ADR-0013 and ADR-0015 wherever they
allowed arbitrary product Species to become the carried branch.
