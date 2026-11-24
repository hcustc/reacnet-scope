"""Render saved Scope evidence as a standalone, read-only HTML document."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from html import escape
from typing import Any

from .geometry_audit import GEOMETRY_AUDIT_SCHEMA
from .skeleton_lineage import SKELETON_LINEAGE_SCHEMA
from .topology_classifier import TOPOLOGY_CLASSIFIER_SCHEMA


def _cell(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, list):
        return escape(", ".join(str(item) for item in value))
    return escape(str(value))


def _table(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    head = "".join(f'<th scope="col">{escape(label)}</th>' for label in columns)
    body = "".join("<tr>" + "".join(f"<td>{_cell(value)}</td>" for value in row) + "</tr>" for row in rows)
    if not body:
        return "<p>No records in this result.</p>"
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _records(document: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    values = document.get(key)
    if not isinstance(values, list) or not all(isinstance(item, Mapping) for item in values):
        raise ValueError(f"Evidence report requires an object array for {key!r}.")
    return values


def _section(document: Mapping[str, Any]) -> tuple[str, str]:
    audit = document.get("geometry_audit", document)
    schema = document.get("schema_version")
    if schema == SKELETON_LINEAGE_SCHEMA:
        rows = _records(document, "timeline")
        notice = " <strong>Result truncated.</strong>" if document.get("truncated") else ""
        summary = f'<p>Mode {_cell(document.get("mode"))} · {len(rows)} plateaus{notice}</p>'
        table = _table(
            ("First timestep", "Last timestep", "Analyzed frames", "Duration (ps)", "Representative species"),
            ((row.get("start_timestep"), row.get("end_timestep"), row.get("frame_count"),
              row.get("duration_ps"), row.get("representative_species")) for row in rows),
        )
        return "Skeleton lineage", summary + table
    if isinstance(audit, Mapping) and audit.get("schema_version") == GEOMETRY_AUDIT_SCHEMA:
        rows = _records(audit, "pairs")
        verdict = _cell(audit.get("verdict"))
        event = _cell(audit.get("event_id"))
        summary = f"<p><strong>{verdict}</strong> · <code>{event}</code></p>"
        reasons = "".join(f"<li>{_cell(reason)}</li>" for reason in audit.get("reasons", []))
        qc = document.get("qc_manifest") or {}
        if isinstance(qc, Mapping) and qc.get("participant_coverage"):
            coverage = qc["participant_coverage"]
            if isinstance(coverage, Mapping):
                summary += (
                    f'<p>Participant coverage {_cell(coverage.get("status"))} · '
                    f'Cluster formula {_cell(qc.get("formula"))} · '
                    f'Electrons {_cell(qc.get("total_electrons"))}</p>'
                )
        table = _table(
            ("Atom IDs", "Change", "Start (Å)", "End (Å)", "Cutoff crossings"),
            ((row.get("atoms"), row.get("role"), row.get("start_distance_ang"),
              row.get("end_distance_ang"), row.get("cutoff_crossings")) for row in rows),
        )
        return "Event geometry audit", summary + (f"<ul>{reasons}</ul>" if reasons else "") + table
    if schema == TOPOLOGY_CLASSIFIER_SCHEMA:
        rows = _records(document, "records")
        formula_filter = document.get("formula_filter")
        formula = f" · Formula {_cell(formula_filter)}" if formula_filter else ""
        return "Isomer classification", f"<p>{len(rows)} species{formula}</p>" + _table(
            ("SMILES", "Formula", "Core motif", "Ring sizes", "Oxygen substitution"),
            ((row.get("smiles"), row.get("formula"), row.get("core_motif"),
              row.get("ring_sizes"), row.get("oxygen_substitution_patterns")) for row in rows),
        )
    raise ValueError(f"Unsupported evidence report schema: {schema!r}.")


def render_report(documents: Iterable[Mapping[str, Any]], *, title: str = "ReacNet Scope evidence review") -> str:
    """Return one offline HTML page with summaries and the original evidence JSON."""
    sections = []
    navigation = []
    for index, document in enumerate(documents, start=1):
        if not isinstance(document, Mapping):
            raise ValueError(f"Evidence input {index} must be a JSON object.")
        heading, body = _section(document)
        anchor = f"evidence-{index}"
        raw = escape(json.dumps(document, ensure_ascii=False, indent=2))
        navigation.append(f'<a href="#{anchor}">{heading}</a>')
        sections.append(
            f'<section id="{anchor}"><h2>{heading}</h2>'
            f'<details><summary>Source JSON</summary><pre>{raw}</pre></details>{body}</section>'
        )
    if not sections:
        raise ValueError("Provide at least one evidence JSON object.")
    page_title = escape(title)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<link rel="icon" href="data:,">
<style>
  :root {{ font: 16px/1.55 system-ui, sans-serif; color: #183137; background: #f1f5f3; }}
  body {{ max-width: 74rem; margin: auto; padding: 2rem clamp(1rem, 4vw, 3rem); }}
  header {{ border-top: .3rem solid #296257; padding: .75rem 0 1.25rem; }}
  h1 {{ font-size: clamp(1.75rem, 4vw, 2.75rem); margin: .5rem 0; }}
  h2 {{ font-size: 1.25rem; margin: 0; }}
  nav {{ display: flex; flex-wrap: wrap; gap: .3rem 1.2rem; }}
  a {{ color: #21584e; text-underline-offset: .2em; }}
  section {{ background: #fff; border: 1px solid #ccdcd6; border-radius: .5rem;
            padding: clamp(.75rem, 3vw, 1.5rem); margin-bottom: 1rem; overflow-wrap: anywhere; }}
  .scroll, pre {{ overflow: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .94rem; }}
  th, td {{ padding: .55rem .7rem; text-align: left; vertical-align: top;
            border-bottom: 1px solid #dce7e2; }}
  th, pre {{ background: #edf4f1; }}
  details {{ margin: .5rem 0 1rem; }}
  summary {{ cursor: pointer; color: #21584e; width: fit-content; padding-block: .25rem; }}
  pre {{ max-height: 32rem; padding: 1rem; }}
  @media print {{ section {{ border-color: #aaa; }} }}
</style>
<header><h1>{page_title}</h1><nav aria-label="Evidence sections">{"".join(navigation)}</nav></header>
<main>{"".join(sections)}</main>
</html>
"""


__all__ = ["render_report"]
