"""Stable application-service facade for CLI and Dash consumers.

Implementations are grouped by workflow so each module has one reason to
change.  This facade deliberately preserves the historical import surface.
"""

from __future__ import annotations

import subprocess
from typing import Any

from reacnet_scope import analysis_services as _analysis
from reacnet_scope.event_package import build_event_package
from reacnet_scope.event_paths import analyze_event_paths
from reacnet_scope.queries import STORE, build_dataset_status_payload
from reacnet_scope.service_types import ServiceError
from reacnet_scope.trajectory import load_timestep_ps, save_timestep_ps
from reacnet_scope.workspace_services import (
    ALLOWED_ROOTS,
    artifacts_from_status,
    browse_dataset_location,
    cancel_dataset_preparation,
    candidates_from_status,
    clear_dataset_index,
    dataset_capabilities,
    dataset_label,
    dataset_preparation_status,
    dataset_readiness,
    dataset_ready_count,
    list_directory,
    normalise_recent_datasets,
    prepare_dataset_workspace,
    resolve_dataset_input,
    scan_dataset,
    validate_browse_path,
)
from reacnet_scope.analysis_services import (
    build_channel_structure_detail,
    build_event_path_occurrence_elements,
    build_pathway_elements,
    build_species_structure_items,
    collect_species_channels,
    compose_continuous_reaction_pair,
    detect_query_kind,
    event_path_comparison_rows,
    event_path_comparison_signature_rows,
    event_path_occurrence_rows,
    event_path_occurrences_for_signature,
    event_path_signature_rows,
    event_path_signature_time_rows,
    find_continuous_reactions,
    rank_representative_events,
    render_species_svg,
    search_reactions_by_formula,
    search_species,
    search_species_catalog,
    species_detail,
    validate_event_path_sources_for_dash,
)
from reacnet_scope.evidence_services import (
    batch_comparison_to_csv,
    build_element_distribution_species_drilldown,
    build_elemental_composition_evolution,
    build_intermediate_candidates,
    build_rng_event_visualization,
    build_species_evolution,
    composition_index_status,
    event_viewer_atom_ids,
    event_viewer_frames_csv,
    event_viewer_ovito_expression,
    event_viewer_ovito_script,
    event_viewer_trajectory_text,
    event_viewer_vmd_script,
    evolution_to_csv,
    intermediate_candidates_to_csv,
    launch_event_in_ovito,
    locate_rng_events,
    ovito_launch_capability,
    parse_event_type_element_map,
    rows_to_csv,
    validate_pathway_step_occurrences,
)
from reacnet_scope.batch_services import (
    run_batch_comparison,
    run_grouped_batch_comparison,
    scan_batch_conditions,
)
from reacnet_scope.dataset_context import (
    begin_dataset_switch,
    current_dataset_from_validation,
    inspect_dataset_candidate,
    is_same_dataset_revision,
    resolve_dataset_switch,
    revalidate_current_dataset,
    supersede_dataset_switch,
    validate_dataset_candidate,
)


def find_pathways(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Call the pathway workflow while preserving the patchable legacy seam."""
    previous = _analysis.STORE
    _analysis.STORE = STORE
    try:
        return _analysis.find_pathways(*args, **kwargs)
    finally:
        _analysis.STORE = previous


def analyze_event_paths_for_dash(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Call event-path analysis through the patchable legacy dependencies."""
    previous_analyzer = _analysis.analyze_event_paths
    previous_validator = _analysis.validate_browse_path
    _analysis.analyze_event_paths = analyze_event_paths
    _analysis.validate_browse_path = validate_browse_path
    try:
        return _analysis.analyze_event_paths_for_dash(*args, **kwargs)
    finally:
        _analysis.analyze_event_paths = previous_analyzer
        _analysis.validate_browse_path = previous_validator


__all__ = [
    "ALLOWED_ROOTS",
    "ServiceError",
    "build_dataset_status_payload",
    "load_timestep_ps",
    "save_timestep_ps",
    "browse_dataset_location",
    "list_directory",
    "normalise_recent_datasets",
    "resolve_dataset_input",
    "scan_dataset",
    "validate_browse_path",
    "artifacts_from_status",
    "dataset_label",
    "dataset_ready_count",
    "dataset_capabilities",
    "dataset_readiness",
    "dataset_preparation_status",
    "prepare_dataset_workspace",
    "cancel_dataset_preparation",
    "clear_dataset_index",
    "candidates_from_status",
    "detect_query_kind",
    "find_pathways",
    "build_pathway_elements",
    "validate_event_path_sources_for_dash",
    "analyze_event_paths_for_dash",
    "event_path_signature_rows",
    "event_path_comparison_rows",
    "event_path_comparison_signature_rows",
    "event_path_occurrences_for_signature",
    "event_path_signature_time_rows",
    "event_path_occurrence_rows",
    "build_event_path_occurrence_elements",
    "search_species_catalog",
    "search_species",
    "species_detail",
    "render_species_svg",
    "collect_species_channels",
    "build_species_structure_items",
    "build_channel_structure_detail",
    "search_reactions_by_formula",
    "build_species_evolution",
    "evolution_to_csv",
    "intermediate_candidates_to_csv",
    "build_elemental_composition_evolution",
    "composition_index_status",
    "build_element_distribution_species_drilldown",
    "build_intermediate_candidates",
    "locate_rng_events",
    "validate_pathway_step_occurrences",
    "rank_representative_events",
    "find_continuous_reactions",
    "compose_continuous_reaction_pair",
    "parse_event_type_element_map",
    "build_rng_event_visualization",
    "event_viewer_frames_csv",
    "event_viewer_trajectory_text",
    "build_event_package",
    "event_viewer_atom_ids",
    "event_viewer_ovito_expression",
    "event_viewer_ovito_script",
    "ovito_launch_capability",
    "launch_event_in_ovito",
    "event_viewer_vmd_script",
    "rows_to_csv",
    "batch_comparison_to_csv",
    "scan_batch_conditions",
    "run_grouped_batch_comparison",
    "run_batch_comparison",
    "begin_dataset_switch",
    "current_dataset_from_validation",
    "inspect_dataset_candidate",
    "is_same_dataset_revision",
    "resolve_dataset_switch",
    "revalidate_current_dataset",
    "supersede_dataset_switch",
    "validate_dataset_candidate",
]
