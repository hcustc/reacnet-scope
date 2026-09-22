# ADR-0023: Read data on the application host

Status: Accepted (2026-09-22). Supersedes ADR-0022.

## Context

The required deployments keep data and ReacNet Scope on the same machine:
server data with a server deployment, or local data with a local deployment.
Browser uploads added unnecessary transfer, storage and service administration.

## Decision

Use one “选择数据” workflow that browses files accessible to the application
process. Preserve allowed-root validation, multi-file selection, cross-directory
collections, grouping and explicit “开始分析”. Directory membership is not a
dataset boundary. Keep the existing transactional switch and stale-result guards.

Remove the upload UI, gateway, Uppy assets, tusd deployment configuration and
upload-only dependencies. No upload service or input storage configuration is
required. Existing source files, including previously uploaded files, are not
deleted; they can still be selected when accessible under configured roots.

## Consequences

Local data is analysed by running the application locally. A browser connected
to a remote deployment browses the remote machine. Transferring local data to a
remote deployment is outside the current product scope.
