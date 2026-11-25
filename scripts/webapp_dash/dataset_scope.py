"""Browser-session state bound to one Current Dataset context.

``context_key`` identifies the dataset context (Dataset Identity, source
revision and published artifacts) that a request, result or handoff belongs
to. The Species focus records which exact Species the Species detail shows.
It lives in its own ``species-focus`` store rather than in ``app-store``:
selecting a Species must not rewrite the Current Dataset context, and a late
focus written for an earlier context is ignored instead of being applied.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SPECIES_FOCUS_FIELDS = ("selected_smiles", "selected_formula", "selected_species_source")


def context_key(store):
    store = store or {}
    return hashlib.sha256(json.dumps({k: store.get(k) for k in
        ('dataset_id', 'source_revision', 'artifacts')}, sort_keys=True).encode()).hexdigest()


def empty_species_focus() -> dict[str, str]:
    return {"context": "", **{field: "" for field in SPECIES_FOCUS_FIELDS}}


def species_focus_for(
    store: dict[str, Any] | None,
    smiles: str = "",
    formula: str = "",
    source: str = "",
) -> dict[str, str]:
    """Return a focus on ``smiles`` bound to the dataset context in ``store``."""

    if not smiles:
        return empty_species_focus()
    return {
        "context": context_key(store),
        "selected_smiles": smiles,
        "selected_formula": formula,
        "selected_species_source": source,
    }


def current_species_focus(
    focus: dict[str, Any] | None,
    store: dict[str, Any] | None,
) -> dict[str, str]:
    """Return ``focus`` if it belongs to the current dataset context, else empty."""

    if (
        isinstance(focus, dict)
        and focus.get("selected_smiles")
        and focus.get("context") == context_key(store)
    ):
        return {
            "context": str(focus["context"]),
            **{field: str(focus.get(field) or "") for field in SPECIES_FOCUS_FIELDS},
        }
    return empty_species_focus()
