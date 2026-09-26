---
status: accepted
date: 2026-09-26
---

# Use local exact Species focus in Dash

## Context

The ordinary Dash workflow now has one Species detail and peer Reaction,
Evolution, and Event entries. Selecting an exact Species previously also
created a persistent session research anchor. This duplicated the detail's
identity and required a second "set as research Species" action when browsing
another Species. It also risked making a result appear to follow a new anchor
instead of retaining the identity with which it was computed.

## Decision

Remove the global research Species state, automatic assignment, overview,
banner, and "set as research Species" actions. A Species detail's exact RNG
structure identity, Dataset Identity, and source revision define its local
target. Explicit actions from that detail pass this target to route, reaction,
and evolution tools. Independent searches use their own submitted identities;
each result retains its own target and source context when another Species is
viewed.

The bounded cross-entry return chain restores the originating query, selection,
Dataset Identity, and source revision without reinterpreting or rerunning the
result. An expired source or revision cannot restore old evidence. Comparison
sources keep independent exact Species identities and never change Current
Dataset implicitly. An explicit handoff from a Species detail uses that
currently viewed Species, subject to the normal source validation.

This changes Dash interaction state only. Scientific Species identity, candidate
and event evidence rules, result schemas, CLI/API behavior, and preparation
contracts do not change.

## Supersession

This supersedes the persistent research anchor, automatic assignment, and
research-replacement actions in [ADR-0027](0027-center-dash-on-persistent-research-species.md),
and the references to that anchor in [ADR-0028](0028-integrate-comparison-into-analysis-tasks.md)
and [ADR-0029](0029-open-analysis-from-species-reactions-evolution-and-events.md).
The five-entry navigation, independent comparison, stable event handoff, and
return context decisions remain accepted.
