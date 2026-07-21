"""Reusable helpers for analysis scripts.

Modules:
- formula: parse/count helpers for chemical formulas and mass balance.
- io: lightweight loaders for CSV/Excel inputs with simple validation.
- carbon_plot: carbon-number aggregation, summary, and plotting helpers.
- reaction: RDKit-based SMILES tools, reactionabcd parsing, reaction classification.
- network: Reaction network graph, key intermediate identification, pathway tracing.
"""

__all__ = [
    # formula
    "parse_formula",
    "parse_formulas_list",
    "count_reactants",
    "check_mass_balance",
    "formula_exact_mass",
    "formula_nominal_mass",
    # io
    "load_table",
    "load_transition_table",
    # carbon plot
    "parse_formula_to_atom_counts",
    "aggregate_counts_by_carbon_number",
    "species_file_to_tidy_table",
    "parse_carbon_range_specs",
    "summarize_carbon_evolution",
    "plot_carbon_number_evolution",
]

from .formula import (
    parse_formula,
    parse_formulas_list,
    count_reactants,
    check_mass_balance,
    formula_exact_mass,
    formula_nominal_mass,
)
from .io import load_table, load_transition_table
from .carbon_plot import (
    parse_formula_to_atom_counts,
    aggregate_counts_by_carbon_number,
    species_file_to_tidy_table,
    parse_carbon_range_specs,
    summarize_carbon_evolution,
    plot_carbon_number_evolution,
)

_REACTION_EXPORTS = [
    "smiles_to_formula",
    "canonical_smiles",
    "collect_smiles_for_formulas",
    "build_canonical_map",
    "build_formula_cache",
    "iter_reactions",
    "filter_reactions_by_formula",
    "merge_reactions",
    "compute_net_flux",
    "make_radical_classifier",
    "parse_species_file",
    "locate_reaction_frames",
    "save_species_timeseries_csv",
    "save_pathway_csv",
    "save_smiles_csv",
    "print_ranked_table",
    "ParsedReaction",
]

try:
    from .reaction import (
        smiles_to_formula,
        canonical_smiles,
        collect_smiles_for_formulas,
        build_canonical_map,
        build_formula_cache,
        iter_reactions,
        filter_reactions_by_formula,
        merge_reactions,
        compute_net_flux,
        make_radical_classifier,
        parse_species_file,
        locate_reaction_frames,
        save_species_timeseries_csv,
        save_pathway_csv,
        save_smiles_csv,
        print_ranked_table,
        ParsedReaction,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"rdkit", "rdkit.Chem", "rdkit.Chem.rdMolDescriptors", "rdkit.RDLogger"}:
        raise
else:
    __all__.extend(_REACTION_EXPORTS)
