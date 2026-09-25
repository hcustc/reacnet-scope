---
status: accepted
---

# Separate Candidate Path Discovery from Continuous MD Support

ADR-0026 supersedes the mandatory `max_steps` horizon for two-anchor search;
the discovery/continuous-support separation in this decision remains in force.

ADR-0016 refines the per-step carried branch: ordinary Candidate routes now
require event-local dominant atom-descendant evidence. This ADR's separation
from whole-route Continuous MD Support remains in force.

Candidate Path Discovery operates on the current dataset's MD-observed directed reaction hypergraph: a direction is eligible only when at least one normalized Reaction Occurrence and concrete Reaction Evidence support that direction. Adjacent steps connect through an explicit exact Carried Species, while co-reactants and other products retain the full reaction context. A Candidate Path is therefore a network-level route and does not claim that one sampled Molecule Instance or atom lineage traversed the whole sequence.

Continuous MD Support is a separate, selective validation stage applied after network filtering and ranking. It uses indexed Reaction Occurrences, Molecule Continuity Segments, first-consuming Transitions, and anchor provenance to determine whether a concrete molecular carrier chain realizes a selected Candidate. Validation evidence, status, retention policy, query rank, and dataset revision do not change the Candidate's structural identity; unevaluated validation is not negative evidence, and factual provenance results remain distinct from any explicitly requested binary retention-policy evaluation.

This separation replaces [ADR-0012](0012-discover-only-sampled-candidate-paths.md). Requiring a complete Event Path during discovery was rejected because it conflated step eligibility with sequence validation, excluded chemically useful routes assembled from observations across Replicates, and forced online searches toward a global occurrence graph. Allowing directions inferred only from an aggregate network or an assumed reverse was also rejected because a Candidate must remain grounded in Reaction Evidence from the current dataset. Pre-enumerating every Candidate was rejected because the number of paths is combinatorial and incompatible with bounded online queries.

## Consequences

- Offline preparation must publish a revisioned, MD-observed directed hypergraph, normalized occurrences, continuity segments, production/first-consumption links, and drill-down indexes through a staging revision with integrity checks and atomic activation.
- Online discovery performs deterministic, bounded local expansion around exact Carried Species and never scans raw event sources, loads all occurrences or continuity segments, builds a global occurrence graph, or persists every possible Candidate.
- Ordinary Candidates are Carried-Species-simple paths. A revisit is recorded as cycle-closure evidence; Reaction Cycle Candidate Discovery remains a separate future capability, and Fast Recrossing Episodes retain their occurrence-level temporal meaning.
- Candidate structural identity uses versioned canonical Species identities, ordered carried Species, and ordered directed Reaction Types with stoichiometric multiplicity. Dataset evidence identity, query-relative result metadata, and Continuous MD Support records remain separate.
- Target-constrained and exploratory discovery have different completion contracts. A declarative `max_steps` horizon is not an execution truncation; execution budgets and incomplete validation are reported independently.
- Step Evidence and Continuous Support Evidence are separate drill-down channels. The first explains each step independently; only the second may claim an ordered molecular provenance chain.
- Continuous MD Support defaults to factual, threshold-free provenance reporting and validates only selected Candidates. A binary support decision exists only when the query supplies an explicit retention policy.
- The current `reacnet_scope/event_paths.py` discovery path remains a prototype/compatibility implementation until the indexed production path and migration gates are complete; it is not the production architecture.
