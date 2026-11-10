---
status: accepted
date: 2026-11-09
---

# Deprecate v3 Candidate ranking and split frozen capabilities by purpose

## Context

Two Candidate Path entry points coexist. `candidate-search` and
`search_candidate_paths` return `indexed-candidates/v6` results without a
chemical-importance score and back the Dash tool. The older `candidate-paths`
command, `rank_candidate_paths` and the energy-CSV loader return `v3` results
ranked by the `candidate-path/network-v1` weighted score. ADR-0014 already
removed aggregate scoring and energy-CSV ranking from the ordinary surface;
keeping two schemas and ranking rules for one capability confuses users and
doubles maintenance.

The design baseline froze Species Fate Analysis, Apparent Rate Constant
Estimate, Path Verification and QC Handoff Readiness extensions without
saying where frozen code lives or how long it is carried. These capabilities
are not equally close to the mainline adopted in
[ADR-0033](0033-make-direct-channels-the-mainline-and-candidates-optional.md).
Path Verification and QC Handoff Readiness reuse the event, molecular and
geometry evidence of the mainline and support hypothesis testing of Candidate
steps, including DFT Initial Geometry export. Species Fate Analysis and
Apparent Rate Constant Estimate are the largest frozen modules, are farthest
from the mainline and carry known incomplete parts (initial left-censor
detection for Fate; time, volume and unit preconditions for rates).

## Decision

**v3 Candidate ranking is deprecated and is removed in 0.2.0.** The
`candidate-paths` CLI command prints a deprecation notice that names
`candidate-search`; `rank_candidate_paths` raises `DeprecationWarning`. The
`v3` result schema, `candidate-path/network-v1` score and energy-CSV ranking
receive no further changes and are removed with the command.
`candidate-search` / `search_candidate_paths` is the single Candidate Path
Discovery entry for CLI, Python and Dash. Removal does not change Candidate
structural identity, Step Evidence or any prepared index.

**Frozen capabilities are split by purpose:**

- Path Verification and QC Handoff Readiness stay in the core package. They
  remain frozen for expansion, keep their existing contracts and public
  CLI/API, and must keep working when shared evidence contracts change.
- Species Fate Analysis and Apparent Rate Constant Estimate move out of the
  core package into the optional subpackage `reacnet_scope.research`.
  Core modules and the ordinary Dash mainline must not import them; the CLI
  imports them only when their commands run and reports explicitly
  (`optional_dependency_missing`) when their dependencies cannot be imported.
  Their third-party dependency, SciPy, is a core dependency rather than a
  separate extra (see Amendment). Their scientific contracts, result schemas and exports are unchanged;
  during migration the old import paths remain as deprecated aliases. The
  package name, extra name and alias removal version are settled in the
  implementing change.
- Shared substrate stays in the core: molecular continuity preparation used by
  Molecular Lineage, physical-time conversion, and per-frame box volume and PBC
  data used by trajectory inspection and geometry export are not removed with
  the optional modules.

## Supersession

This refines the frozen-capability paragraph of the design baseline and the
compatibility retention of `candidate-paths` and Candidate scoring described in
[ADR-0014](0014-converge-dash-around-five-workspaces.md). ADR-0014's surface
decisions otherwise remain accepted. Fate and rate decisions in
[ADR-0011](0011-prepare-molecular-continuity-for-query-time-fate.md) and
[`0012-estimate-channel-rates-from-prepared-exposure.md`](0012-estimate-channel-rates-from-prepared-exposure.md)
keep their scientific semantics; only their packaging changes.

## Amendment (2026-11-09)

The first implementation shipped SciPy through a `research` extra. That extra
is removed and SciPy joins the core dependencies: every documented install
(development `uv sync --locked` and deployment with the `web` and `trajectory`
extras, where ASE already requires SciPy) contained it, and the Lewis candidate
annotation preparation, a core feature, needs SciPy's MILP solver. The
separation is kept at the import level only: core, CLI and Dash still never
import `reacnet_scope.research.*` or SciPy at load time. Extras are reduced to
one purpose each: `web` (Dash runtime), `trajectory` (ASE) and `plot`
(matplotlib for CLI PNG export).

## Consequences

Users of `candidate-paths` see a notice and have until 0.2.0 to switch.
Core contract changes no longer have to carry Fate and rate code paths once the
split is implemented; those modules must then be tested against the core
through their own optional test selection. Until the split lands, the baseline
lists it as a known deviation.
