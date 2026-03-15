"""Reusable helpers for analysis scripts.

Modules:
- formula: parse/count helpers for chemical formulas and mass balance.
- io: lightweight loaders for CSV/Excel inputs with simple validation.
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
    # reaction
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

from .formula import (
    parse_formula,
    parse_formulas_list,
    count_reactants,
    check_mass_balance,
    formula_exact_mass,
    formula_nominal_mass,
)
from .io import load_table
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
