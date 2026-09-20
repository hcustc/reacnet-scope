"""Stable application-service facade for CLI and Dash consumers.

Implementations are grouped by workflow so each module has one reason to
change.  This facade deliberately preserves the historical import surface.
"""

from __future__ import annotations

import subprocess
from typing import Any

from reacnet_scope import analysis_services as _analysis
from reacnet_scope.dft_geometry import (
    DFT_GEOMETRY_SCHEMA_VERSION,
    DftGeometryBundle,
    DftGeometryError,
    DftGeometryRequest,
    build_dft_geometry_bundle,
)
from reacnet_scope.reaction_readiness import (
    REACTION_READINESS_SCHEMA_VERSION,
    ReactionReadinessRequest,
    ReactionReadinessResult,
    evaluate_reaction_readiness as _evaluate_reaction_readiness,
)
from reacnet_scope.event_package import build_event_package
from reacnet_scope.event_paths import verify_event_path
from reacnet_scope.queries import build_dataset_status_payload
from reacnet_scope.service_types import ServiceError
from reacnet_scope.reaction_timing import (
    reaction_time_distribution,
    reaction_time_events,
    reaction_timing_summaries,
)
from reacnet_scope.trajectory import (
    load_coordinate_length_unit,
    load_timestep_ps,
    save_coordinate_length_unit,
    save_timestep_ps,
)
from reacnet_scope.workspace_services import (
    ALLOWED_ROOTS,
    artifacts_from_status,
    browse_dataset_location,
    cancel_dataset_preparation,
    candidates_from_status,
    clear_dataset_index,
    dataset_analysis_capabilities,
    dataset_capabilities,
    dataset_label,
    dataset_preparation_status,
    dataset_readiness,
    dataset_ready_count,
    dismiss_dataset_preparation_task,
    list_directory,
    list_preparation_tasks,
    normalise_recent_datasets,
    prepare_dataset_workspace,
    resolve_dataset_input,
    resolve_dataset_folder_candidate,
    scan_dataset,
    validate_browse_path,
)
from reacnet_scope.analysis_services import (
    build_channel_structure_detail,
    build_event_path_occurrence_elements,
    build_species_structure_items,
    channel_volume_evidence,
    collect_species_channels,
    configure_channel_volume_source,
    confirm_channel_timestep_ps,
    compose_continuous_reaction_pair,
    detect_query_kind,
    discover_candidate_paths_for_dash,
    event_path_occurrence_rows,
    event_path_occurrences_for_signature,
    event_path_signature_rows,
    event_path_signature_time_rows,
    find_continuous_reactions,
    rank_representative_events,
    render_reaction_svg,
    render_species_svg,
    search_reactions_by_formula,
    search_species,
    search_species_catalog,
    channel_timestep_ps,
    species_detail,
    validate_event_path_sources_for_dash,
)
from reacnet_scope.evidence_services import (
    EVENT_BOOKMARK_SCHEMA_VERSION,
    batch_comparison_package,
    batch_comparison_to_csv,
    build_element_distribution_species_drilldown,
    build_elemental_composition_evolution,
    build_molecule_lineage_analysis,
    build_species_fate_analysis,
    build_rng_event_visualization,
    build_species_evolution,
    composition_index_status,
    continue_molecule_lineage_analysis,
    create_event_bookmark,
    event_viewer_atom_ids,
    event_viewer_changed_bond_distances_csv,
    event_viewer_frames_csv,
    event_viewer_ovito_expression,
    event_viewer_ovito_script,
    event_viewer_trajectory_text,
    event_viewer_vmd_script,
    evolution_to_csv,
    launch_event_in_ovito,
    locate_rng_events,
    molecule_lineage_to_csv,
    ovito_launch_capability,
    parse_event_type_element_map,
    restore_event_bookmark,
    rows_to_csv,
    species_evolution_catalog,
    species_fate_catalog_for_dataset,
    species_fate_tables_zip,
    species_fate_to_json,
    validate_pathway_step_occurrences,
)
from reacnet_scope.batch_services import (
    run_batch_comparison,
    run_grouped_batch_comparison,
    scan_batch_conditions,
)
from reacnet_scope.species_compare import (
    compare_species_sources,
    species_compare_catalog,
    species_comparison_zip,
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


def verify_event_path_for_dash(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Call explicit path verification through patchable dependencies."""
    previous_analyzer = _analysis.verify_event_path
    previous_validator = _analysis.validate_browse_path
    _analysis.verify_event_path = verify_event_path
    _analysis.validate_browse_path = validate_browse_path
    try:
        return _analysis.verify_event_path_for_dash(*args, **kwargs)
    finally:
        _analysis.verify_event_path = previous_analyzer
        _analysis.validate_browse_path = previous_validator


def evaluate_reaction_readiness(*args: Any, **kwargs: Any) -> ReactionReadinessResult:
    """Evaluate through the facade's patchable DFT geometry builder."""

    return _evaluate_reaction_readiness(
        *args,
        geometry_builder=build_dft_geometry_bundle,
        **kwargs,
    )


__all__ = [
    "ALLOWED_ROOTS",
    "ServiceError",
    "EVENT_BOOKMARK_SCHEMA_VERSION",
    "DFT_GEOMETRY_SCHEMA_VERSION",
    "DftGeometryBundle",
    "DftGeometryError",
    "DftGeometryRequest",
    "build_dft_geometry_bundle",
    "REACTION_READINESS_SCHEMA_VERSION",
    "ReactionReadinessRequest",
    "ReactionReadinessResult",
    "evaluate_reaction_readiness",
    "build_dataset_status_payload",
    "load_coordinate_length_unit",
    "load_timestep_ps",
    "save_coordinate_length_unit",
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
    "dataset_analysis_capabilities",
    "dataset_preparation_status",
    "list_preparation_tasks",
    "prepare_dataset_workspace",
    "cancel_dataset_preparation",
    "dismiss_dataset_preparation_task",
    "clear_dataset_index",
    "candidates_from_status",
    "detect_query_kind",
    "discover_candidate_paths_for_dash",
    "validate_event_path_sources_for_dash",
    "verify_event_path_for_dash",
    "event_path_signature_rows",
    "event_path_occurrences_for_signature",
    "event_path_signature_time_rows",
    "event_path_occurrence_rows",
    "build_event_path_occurrence_elements",
    "search_species_catalog",
    "search_species",
    "species_detail",
    "render_reaction_svg",
    "render_species_svg",
    "collect_species_channels",
    "channel_volume_evidence",
    "configure_channel_volume_source",
    "confirm_channel_timestep_ps",
    "channel_timestep_ps",
    "build_species_structure_items",
    "build_channel_structure_detail",
    "search_reactions_by_formula",
    "build_species_evolution",
    "species_evolution_catalog",
    "evolution_to_csv",
    "species_compare_catalog",
    "compare_species_sources",
    "species_comparison_zip",
    "build_elemental_composition_evolution",
    "composition_index_status",
    "create_event_bookmark",
    "build_element_distribution_species_drilldown",
    "build_molecule_lineage_analysis",
    "continue_molecule_lineage_analysis",
    "build_species_fate_analysis",
    "locate_rng_events",
    "molecule_lineage_to_csv",
    "species_fate_catalog_for_dataset",
    "species_fate_tables_zip",
    "species_fate_to_json",
    "validate_pathway_step_occurrences",
    "rank_representative_events",
    "find_continuous_reactions",
    "compose_continuous_reaction_pair",
    "parse_event_type_element_map",
    "restore_event_bookmark",
    "build_rng_event_visualization",
    "event_viewer_frames_csv",
    "event_viewer_changed_bond_distances_csv",
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
    "batch_comparison_package",
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
