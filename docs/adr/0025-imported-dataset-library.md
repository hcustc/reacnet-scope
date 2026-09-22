# ADR-0025: Reuse imported RNG folders across analysis and comparison

Status: Accepted (2026-09-22). Extends ADR-0024.

## Context

Users need to import several independent RNG output folders once, choose one in
ordinary analysis, and choose several for comparison. Recent history alone is
bounded and cannot serve as a reusable catalog.

## Decision

Maintain a browser-local Dataset Library, separate from recent history and the
single Current Dataset. The picker supports a draft folder list, adding the
current folder or child folders, and entering one folder path per line. Explicit
batch import checks up to 100 folders in a background task, reports each failure,
and registers successful independent references without changing Current Dataset.
A folder with multiple ambiguous runs requires resolution through the existing
single-run picker; batch import never silently merges runs. Cancelled or
superseded results do not register references.

The sidebar Dataset entry opens the imported library with add, use and remove-reference actions.
Comparison is an optional task within the Species and Reaction workspaces,
not a library action or a separate top-level workspace. Current dataset preparation is a separate
tab, reached directly by preparation shortcuts. Utility pages hide analysis task
navigation.

A global selector uses the existing two-phase switch with fresh permission and
source validation and retains the originating analysis page. Both species and
reaction/condition comparison selectors use the library; multi-selection remains
independent of Current Dataset. Existing current/recent references remain
available for compatibility. A successful ordinary import also enters the library.

Persistence belongs to this browser and origin, not a shared server catalog.
Removing a library entry removes only its reference, not source files or Dataset
Workspace state. Imported references do not authorize access and do not establish
index readiness. No source copying, automatic preparation, or cross-run identity
merging is introduced.

## Verification

Service and Dash callback tests cover independent identities, partial failures,
ambiguous folders, retention beyond recent-history limits, stale/cancelled results,
and switching while preserving the originating page. Browser acceptance exercises
batch import, ordinary selection and multiple comparison sources with isolated
small RNG fixtures; real-scale and cross-platform acceptance remain separate.

The UI calls these references “RNG 数据” and comparisons “多来源对比”; internal
Dataset identifiers and API names remain unchanged. An empty batch draft means
the explicit “导入当前文件夹” action imports the currently browsed folder. A nonempty
draft changes the button to show its count and imports only those listed folders.
An empty request without a browsed location is an action prompt, not a failed file.
