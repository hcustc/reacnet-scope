# ADR-0022: Server files and resumable browser uploads

Status: Superseded by [ADR-0023](0023-read-data-on-the-application-host.md). Previously accepted (2026-09-22).

## Decision

The user accepted explicit server/local sources and reuse of mature open-source
components. The RNG importer therefore exposes two source tabs in one page:
server files (default), and local browser upload. Files and folders remain
selection mechanisms; a directory is not a dataset boundary.

Server files retain ADR-0021's original-path, read-only behavior. Browser files
use locally bundled Uppy Dashboard/Tus and the official tusd service. The app
proxies bounded chunks to a loopback-only tusd filesystem store, checking a
browser capability token on upload creation, mutation and publication. Existing
LAN/VPN or reverse-proxy authentication remains required; this is not a new
multi-user account or authorization system.

The upload storage is explicit and separate from the disposable index workspace.
Incomplete bytes remain in `incoming/`. Completed files are atomically moved to
`ready/<upload-id>/<original-name>` after size and ownership validation. That
ready directory must be in the configured allowed roots. Same names cannot
replace another upload; publication is idempotent. A completed input is source
data and cannot be deleted through the upload cancel endpoint or index cleanup.
Local file selection never becomes a server path before publication.

The application retains the existing RNG role, conflict, identity, source
revision and two-phase Current Dataset switch contracts. "Upload and analyse"
captures the current draft revision. A matching, single valid group can begin
analysis after publication. Multiple groups or conflicts require an explicit
choice. Changed drafts, leaving the page or returning cancel the automatic
continuation; uploaded sources remain available. Browser transfer progress and
analysis preparation status are separate.

## Reuse and deployment

Uppy dependencies and build tools are pinned by npm lockfile. Generated JS/CSS
and dependency licenses ship with the Python application; production does not
fetch a CDN or need Node to serve the interface. tusd remains an independently
installed, supervised service; its protocol and file-locking implementation are
not duplicated in Python. FileLock protects gateway publication against gateway
PATCH/DELETE across processes.

Uploads are enabled by explicit `REACNET_SCOPE_UPLOAD_DIR` and
`REACNET_SCOPE_TUSD_URL`. The UI reports an unavailable upload service without
blocking server selection. Transfer blocks are 8 MiB, two concurrent uploads;
per-file limits are configurable and are not performance promises. Production
reverse proxies must allow those request sizes and disable request buffering.

## Supersession and limits

This extends ADR-0021's browser-upload exclusion and design baseline §5. Its
"do not copy source files" rule continues to apply to server sources; browser
uploads necessarily transfer a copy into managed input storage. It does not
introduce automatic cloud uploads, multi-tenant permissions, remote URL fetching,
or object-store readers.

Closing a browser stops local transfer; tus fingerprints allow reselecting the
same original file to resume. The application does not promise that browser
memory/files survive a reload. Explicit cancel removes unfinished bytes through
tusd. Abandoned upload retention and storage quotas are deployment policy;
completed input retention must not be tied to cache expiry.
