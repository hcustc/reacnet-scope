---
status: accepted
date: 2026-08-18
---

# Prepare molecular continuity for query-time Species Fate Analysis

Species Fate Analysis moves to an optional extra
([ADR-0034](0034-deprecate-v3-candidate-ranking-and-split-frozen-capabilities.md));
the shared molecular continuity substrate stays in the core. The decision below
otherwise remains accepted.

Species Fate Analysis uses a two-stage architecture: `prepare event` builds a
revision-bound, query-independent molecular continuity substrate, while each
Fate Query applies its target, fixed anchors, endpoint categories, observation
window, and limits at query time. This avoids repeatedly reconstructing the
same first-participation relationships for every formation episode without
locking user-defined endpoints into permanent precomputed fate indexes; older
event indexes remain valid for existing event tools but expose Species Fate as
`REBUILD_REQUIRED`.
