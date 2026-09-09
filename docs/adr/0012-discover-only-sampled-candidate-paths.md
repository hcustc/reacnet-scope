---
status: superseded by ADR-0013
---

# Discover candidate paths only from sampled Event Paths

> Superseded by [ADR-0013](0013-separate-candidate-discovery-from-continuous-md-support.md).

Candidate Path Discovery starts from one or more exact Species and enumerates only Event Paths supported by prepared Molecular Evidence: events are strictly ordered, adjacent events share the same exact Molecule Instance, and a non-empty atom lineage persists across the path. Aggregate Reaction Type reachability may supply frequency and directionality metrics, but it cannot create a candidate by itself. This keeps automatic discovery distinct from Path Verification and prevents a graph-theoretic connection from being presented as something sampled by the DeePMD trajectory.

Ranking publishes versioned frequency, structure-similarity, temporal-association, atom-continuity/replicate, and optional user-normalized energy metrics. Missing energy evidence is explicit and its weight is renormalized away; partial energy coverage is reported and penalized by coverage. A Sampled Candidate Path remains a candidate for follow-up and never establishes causality, uniqueness, a transition state, or a complete reaction mechanism.
