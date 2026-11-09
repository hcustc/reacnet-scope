"""Dash callback registration for the ReacNet Scope workbench.

Callbacks are grouped by workbench page or tool. Each module exposes
``register(app)``; ``register_callbacks`` registers them all. Each callback
delegates data operations to ``reacnet_scope.services`` and never
re-implements analysis logic.
"""

from __future__ import annotations

from typing import Any

from . import (
    batch,
    dataset_overview,
    dataset_switch,
    dft,
    element_distribution,
    event_exports,
    events,
    evolution,
    molecule_lineage,
    page_navigation,
    preparation,
    reaction_channels,
    reaction_search,
    reaction_timing,
    species,
    species_compare,
)
from .dataset_switch import initial_store

__all__ = ["initial_store", "register_callbacks"]

_MODULES = (
    reaction_channels,
    page_navigation,
    dataset_overview,
    preparation,
    dataset_switch,
    species,
    reaction_search,
    evolution,
    element_distribution,
    reaction_timing,
    events,
    dft,
    molecule_lineage,
    event_exports,
    species_compare,
    batch,
)


def register_callbacks(app: Any) -> None:
    """Register every workbench callback on ``app``."""
    for module in _MODULES:
        module.register(app)
