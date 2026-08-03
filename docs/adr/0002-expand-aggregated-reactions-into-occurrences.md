# Expand aggregated reactions into occurrences

Timed evidence may combine repeated instances of one reaction type in a transition into an aggregated count. ReacNet Scope expands that count into distinct reaction occurrences while building its evidence index, associates each occurrence with a different atom-connected molecular change when possible, and retains unmatched occurrences as unresolved; this preserves total-event statistics and per-occurrence evidence without materializing legacy CSV files.
