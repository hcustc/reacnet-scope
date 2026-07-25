"""Shared, lossless export protocol for candidate pathway results."""

from __future__ import annotations

import csv
import io
import json
from typing import Any


PATHWAY_SCHEMA_VERSION = "reacnet-scope/pathways/v1"
PATHWAY_CSV_FIELDS = [
    "path_rank",
    "step_index",
    "path_species",
    "reaction_key",
    "traversal_direction",
    "focal_input",
    "focal_output",
    "reactants",
    "products",
    "forward_tp",
    "reverse_tp",
    "net_tp",
    "net_share",
    "directionality",
    "event_coverage",
    "time_coverage",
    "event_total",
    "matched_event_total",
    "distinct_intervals",
    "path_score",
    "step_score",
    "evidence_status",
    "score_version",
    "source_references",
]


def pathway_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the versioned JSON document without mutating the store payload."""
    document = dict(payload)
    document["schema_version"] = PATHWAY_SCHEMA_VERSION
    return document


def _json_list(value: object) -> str:
    return json.dumps(
        value if value is not None else [],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def pathway_csv_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every pathway step into the public CSV row schema."""
    rows: list[dict[str, Any]] = []
    for path in payload.get("paths", []):
        path_rank = path.get("rank")
        path_score = path.get("score")
        path_species = _json_list(path.get("species", []))
        for step_index, step in enumerate(path.get("steps", []), 1):
            rows.append(
                {
                    "path_rank": path_rank,
                    "step_index": step_index,
                    "path_species": path_species,
                    "reaction_key": step.get("reaction_key"),
                    "traversal_direction": step.get("traversal_direction"),
                    "focal_input": step.get("focal_input"),
                    "focal_output": step.get("focal_output"),
                    "reactants": _json_list(step.get("reactants", [])),
                    "products": _json_list(step.get("products", [])),
                    "forward_tp": step.get("forward_tp"),
                    "reverse_tp": step.get("reverse_tp"),
                    "net_tp": step.get("net_tp"),
                    "net_share": step.get("net_share"),
                    "directionality": step.get("directionality"),
                    "event_coverage": step.get("event_coverage"),
                    "time_coverage": step.get("time_coverage"),
                    "event_total": step.get("event_total"),
                    "matched_event_total": step.get("matched_event_total"),
                    "distinct_intervals": step.get("distinct_intervals"),
                    "path_score": path_score,
                    "step_score": step.get("score"),
                    "evidence_status": step.get(
                        "evidence_status", path.get("evidence_status")
                    ),
                    "score_version": step.get(
                        "score_version",
                        path.get("score_version", payload.get("score_version")),
                    ),
                    "source_references": _json_list(
                        step.get("source_references", [])
                    ),
                }
            )
    return rows


def pathway_csv_text(payload: dict[str, Any]) -> str:
    """Serialize the same header and rows used by the CLI CSV export."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=PATHWAY_CSV_FIELDS)
    writer.writeheader()
    writer.writerows(pathway_csv_rows(payload))
    return buffer.getvalue()
