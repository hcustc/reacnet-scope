# Prepare molecular continuity for query-time Species Fate Analysis

Species Fate Analysis uses a two-stage architecture: `prepare event` builds a
revision-bound, query-independent molecular continuity substrate, while each
Fate Query applies its target, fixed anchors, endpoint categories, observation
window, and limits at query time. This avoids repeatedly reconstructing the
same first-participation relationships for every formation episode without
locking user-defined endpoints into permanent precomputed fate indexes; older
event indexes remain valid for existing event tools but expose Species Fate as
`REBUILD_REQUIRED`.
