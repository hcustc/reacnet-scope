"""Normalized observation-network payloads for ReacNetGenerator outputs.

The current ``.lammpstrj.table`` artifact is an aggregate species transition
matrix.  It cannot identify individual reaction events, so this module keeps
that limitation explicit while exposing a Species--Reaction graph shape that
can later be populated from Route/ReactionABCD evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


SCHEMA_VERSION = "observation-network/v1"


def _formula(smiles: str, formula_resolver: Any) -> str:
    try:
        return str(formula_resolver(smiles) or "?")
    except Exception:
        return "?"


def build_observation_network(
    parsed: dict[str, Any],
    *,
    table_path: str | Path,
    min_count: int = 1,
    max_species: int = 60,
    top_edges: int = 40,
    formula_resolver: Any = None,
) -> dict[str, Any]:
    """Build a traceable, bipartite network from an aggregate table.

    Each non-zero table cell becomes an ``observed_transition`` reaction node.
    This is an aggregate observation node, not a claim that a single chemical
    reaction event was recovered from the table.
    """

    if formula_resolver is None:
        formula_resolver = lambda _smiles: "?"
    labels = list(parsed["labels"])
    matrix = parsed["matrix"]
    incoming = [sum(int(row[index]) for row in matrix) for index in range(len(labels))]
    outgoing = [sum(int(value) for value in matrix[index]) for index in range(len(labels))]
    ranking = sorted(
        range(len(labels)),
        key=lambda index: (-incoming[index] - outgoing[index], -incoming[index], labels[index]),
    )
    selected = ranking if max_species <= 0 else ranking[: min(max_species, len(ranking))]
    selected_set = set(selected)

    species_nodes: list[dict[str, Any]] = []
    for rank, index in enumerate(selected, 1):
        smiles = labels[index]
        species_nodes.append(
            {
                "id": f"species:{index}",
                "kind": "species",
                "label": _formula(smiles, formula_resolver),
                "smiles": smiles,
                "formula": _formula(smiles, formula_resolver),
                "rank": rank,
                "incoming": int(incoming[index]),
                "outgoing": int(outgoing[index]),
                "total": int(incoming[index] + outgoing[index]),
            }
        )

    all_edges: list[dict[str, Any]] = []
    for source_index in selected:
        for target_index in selected:
            count = int(matrix[source_index][target_index])
            if count < min_count:
                continue
            reverse = int(matrix[target_index][source_index])
            all_edges.append(
                {
                    "source_index": int(source_index),
                    "target_index": int(target_index),
                    "source": labels[source_index],
                    "target": labels[target_index],
                    "source_formula": _formula(labels[source_index], formula_resolver),
                    "target_formula": _formula(labels[target_index], formula_resolver),
                    "count": count,
                    "event_count": count,
                    "net_event_count": count - reverse,
                    "atom_transfer_count": None,
                    "carbon_flux": None,
                    "chlorine_flux": None,
                    "time_weighted_flux": None,
                    "evidence_level": "aggregate_observation",
                }
            )
    all_edges.sort(key=lambda edge: (-int(edge["count"]), edge["source"], edge["target"]))
    displayed_edges = all_edges[: max(1, min(500, top_edges))]

    reaction_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    for ordinal, edge in enumerate(displayed_edges, 1):
        reaction_id = f"reaction:observed:{edge['source_index']}:{edge['target_index']}"
        reaction_nodes.append(
            {
                "id": reaction_id,
                "kind": "reaction",
                "label": f"{edge['source_formula']} -> {edge['target_formula']}",
                "reaction_type": "observed_transition",
                "evidence_level": "aggregate_observation",
                "event_count": int(edge["event_count"]),
                "net_event_count": int(edge["net_event_count"]),
                "source_artifact": str(Path(table_path).expanduser().resolve()),
                "ordinal": ordinal,
            }
        )
        graph_edges.extend(
            [
                {
                    "id": f"edge:{reaction_id}:reactant",
                    "source": f"species:{edge['source_index']}",
                    "target": reaction_id,
                    "kind": "reactant_of",
                    "event_count": int(edge["event_count"]),
                },
                {
                    "id": f"edge:{reaction_id}:product",
                    "source": reaction_id,
                    "target": f"species:{edge['target_index']}",
                    "kind": "produces",
                    "event_count": int(edge["event_count"]),
                },
            ]
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "model": "species_reaction_bipartite",
        "source": {
            "kind": "reacnetgenerator",
            "artifact_type": "lammpstrj.table",
            "path": str(Path(table_path).expanduser().resolve()),
            "evidence_level": "aggregate_observation",
        },
        "audit": {
            "status": "not_available",
            "reason": "The aggregate Table has no raw event IDs or atom-overlap pairs.",
            "raw_event_count": None,
            "transfer_edge_count": None,
        },
        "weights": [
            "event_count",
            "net_event_count",
            "atom_transfer_count",
            "carbon_flux",
            "chlorine_flux",
            "time_weighted_flux",
        ],
        "species": species_nodes,
        "reactions": reaction_nodes,
        "edges": graph_edges,
        "observed_transitions": displayed_edges,
        "legacy": {
            "labels": [labels[index] for index in selected],
            "matrix": [[int(matrix[row][col]) for col in selected] for row in selected],
            "species": species_nodes,
            "edges": displayed_edges,
        },
    }
