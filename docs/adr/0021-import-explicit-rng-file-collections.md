# ADR-0021: Import explicit RNG file collections

Status: Accepted (2026-09-22). Deployment scope clarified by
[ADR-0023](0023-read-data-on-the-application-host.md).

## Context

Users need to select RNG evidence and begin analysis without reorganizing source
folders. A directory-based dataset boundary cannot represent artifacts stored
across mounts or directories. Separate directory and file modes also duplicate
selection controls and obscure the current analysis context.

## Decision

Use one file collection draft. A server-side picker supports multi-selection,
additional locations and bounded, cancellable folder enumeration. Folder input
only adds recognized files. Filename stems suggest groups; the user confirms
which artifacts belong to one RNG run. Repeated roles are conflicts and never
silently select a winner. Multiple groups require an explicit current choice.

Persist explicit artifact roles and paths in a small versioned JSON definition
under the configured or standard user workspace's `collections/` directory. A
collection has a stable identity independent of a common parent directory. Initial
identity derives from its source mapping; supplementing an existing registered
collection keeps its identity and changes the source revision. Source revisions
include actual paths, sizes and exact nanosecond timestamps represented as strings
when crossing the browser boundary. No source files are copied, renamed or linked.

The existing two-phase Current Dataset transaction validates the draft without
publishing it. Only a live matching request may atomically publish the collection
using definition compare-and-swap and a fresh source-revision check. Cancelled,
superseded or failed requests preserve the current context. Source/index readers,
restoration and preparation resolve the same explicit mapping. Native timeline
precedence and evidence validation remain unchanged.

Collection task records and manifests use the collection workspace; existing
source index workspaces remain reusable. Deleting a shared source index affects
all collections using that source. A source rebind invalidates the collection
revision and prevents old preparation tasks from publishing for the new binding.

Successful import returns to the initiating analysis page, or selects an entry
page based on available evidence. A background coordinator prepares composition
or event evidence required by that page, without eagerly indexing coordinates.
Submitted abundance, element-distribution and event queries may wait for that
preparation and run once with their captured arguments; revision changes prevent
replay. Cancellation/failure requires an explicit retry. Published indexes remain
the only online evidence boundary.

## Compatibility and limits

- Existing directory/prefix API and CLI discovery remain available. Creating an
  explicit collection from an old dataset's files establishes a new collection
  identity; supplementing a registered collection preserves its identity.
- Only the selected group is registered; other groups remain browser drafts.
- Remote input references server-side files within allowed roots. Browser upload
  and automatic relocation of missing sources are separate capabilities.
- Supported file roles follow the existing input matrix. Units and atom-type
  mappings remain explicit; filename grouping does not establish scientific
  consistency, a common time axis or physical units.
- Collection and file discovery are bounded. Large real datasets, network mounts
  and cross-platform behavior need separate scale validation.

This replaces the directory-bound Dash selector and load-without-preparation
interaction in design baseline §§8.2 and 9, while retaining the offline preparation,
identity and raw-evidence constraints of prior ADRs.
