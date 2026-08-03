# Index species abundance evidence offline

ReacNet Scope prepares Species Abundance Evidence into a persistent, revision-bound index in the Dataset Workspace before ordinary interactive use. Species lookup, time evolution, Intermediate Candidate screening, and Element Distribution Evolution read that index instead of rescanning `.species` in Dash requests; this spends storage and preparation time to keep large-dataset queries bounded, reproducible, and recoverable.
