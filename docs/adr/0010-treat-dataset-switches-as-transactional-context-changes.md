# Treat dataset switches as transactional context changes

ReacNet Scope keeps a tab-local Dataset Candidate as a non-destructive draft and changes the Current Dataset only through an explicit, revision-validated atomic commit; the previous context remains active until that commit succeeds, and cancelled, abandoned, or superseded results can never commit later. Analysis Capability readiness and Preparation Task progress remain orthogonal, dataset-and-revision-scoped state so that partial readiness and background work cannot be mistaken for, or silently trigger, a global context switch.
