# ADR-0024: Import RNG output folders

Status: Accepted (2026-09-22). Changes the primary picker workflow from ADR-0021/0023.

## Context

The user clarified that importing means selecting the folder containing an RNG
run's output. Individual file selection had become an unnecessary primary task.

## Decision

Opening a folder identifies supported direct-child artifacts and previews its
runs. A single run is selected automatically; multiple runs require a choice.
Only “开始分析” validates and commits the Current Dataset. Switching folders
replaces the preview, including clearing the prior candidate for an empty folder.
Stale previews are revision-guarded. A truncated folder scan cannot be analysed
as though its file set were complete.

The ordinary list shows folders and read-only detected file rows. File toggling,
additional paths and recursive collection remain advanced options. Group editing
is shown only when multiple groups or errors need attention. Source artifacts
stay read-only; explicit role-to-path collections and two-phase commit remain.

Folder membership is not proof of a common run. No automatic recursive merge,
uploading, copying or source rewriting is introduced. Local and server deployments
use the same workflow on their application host.

## Verification

656 tests passed. Firefox exercised opening a folder, automatic file detection,
analysis and species search without individual file selection, then returning to
the picker and checking desktop/narrow viewport action visibility. Regression
coverage includes the four conventional trajectory/RNG artifact names, empty
folders, replacing a previous folder, stale previews and truncated listings.
Large scientific-data performance and cross-platform acceptance are not implied.
