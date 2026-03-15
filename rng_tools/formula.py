"""Formula parsing and mass-balance utilities used across analysis scripts."""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Tuple

MONOISOTOPIC_MASS = {
    "H": 1.00782503223,
    "C": 12.0,
    "N": 14.00307400443,
    "O": 15.99491461957,
    "F": 18.99840316273,
    "P": 30.97376199842,
    "S": 31.9720711744,
    "Cl": 34.968852682,
    "Br": 78.9183376,
    "I": 126.9044719,
    "Si": 27.97692653465,
}

NOMINAL_MASS = {
    "H": 1,
    "C": 12,
    "N": 14,
    "O": 16,
    "F": 19,
    "P": 31,
    "S": 32,
    "Cl": 35,
    "Br": 79,
    "I": 127,
    "Si": 28,
}


def parse_formula(formula: str) -> Dict[str, int]:
    """Parse a molecular formula into an element count dict.

    Examples:
        C6H5ClO -> {'C': 6, 'H': 5, 'Cl': 1, 'O': 1}
        C12H8Cl2O2 -> {'C': 12, 'H': 8, 'Cl': 2, 'O': 2}
    """

    if not formula or formula.strip() in {"", "∅"}:
        return {}

    pattern = r"([A-Z][a-z]?)(\d*)"
    matches = re.findall(pattern, formula)

    counts: Dict[str, int] = {}
    for elem, num in matches:
        if not elem:
            continue
        count = int(num) if num else 1
        counts[elem] = counts.get(elem, 0) + count

    return counts


def parse_formulas_list(formulas_str: str) -> Dict[str, int]:
    """Parse multiple formulas separated by "+" and aggregate element counts."""

    if not formulas_str or formulas_str.strip() == "∅":
        return {}

    total: Counter[str] = Counter()
    for formula in (f.strip() for f in formulas_str.split("+")):
        if formula and formula != "∅":
            total.update(parse_formula(formula))

    return dict(total)


def count_reactants(formulas_str: str) -> int:
    """Count non-empty, non-null reactants on the left side."""

    if not formulas_str or formulas_str.strip() == "∅":
        return 0

    return len([
        f for f in (s.strip() for s in formulas_str.split("+"))
        if f and f != "∅"
    ])


def check_mass_balance(left_formulas: str, right_formulas: str) -> Tuple[bool, str]:
    """Check atom conservation between two sides of a reaction."""

    left_counts = parse_formulas_list(left_formulas)
    right_counts = parse_formulas_list(right_formulas)

    all_elements = set(left_counts) | set(right_counts)
    unbalanced = []

    for elem in all_elements:
        l, r = left_counts.get(elem, 0), right_counts.get(elem, 0)
        if l != r:
            unbalanced.append(f"{elem}({l}→{r})")

    if unbalanced:
        return False, f"Unbalanced: {', '.join(unbalanced)}"

    return True, "OK"


def formula_exact_mass(formula: str) -> float | None:
    """Compute monoisotopic neutral mass from molecular formula.

    Returns None if any element in formula has no mass entry.
    """
    counts = parse_formula(formula)
    total = 0.0
    for elem, cnt in counts.items():
        mass = MONOISOTOPIC_MASS.get(elem)
        if mass is None:
            return None
        total += mass * cnt
    return total


def formula_nominal_mass(formula: str) -> int | None:
    """Compute nominal mass number from molecular formula.

    Returns None if any element in formula has no mass entry.
    """
    counts = parse_formula(formula)
    total = 0
    for elem, cnt in counts.items():
        mass = NOMINAL_MASS.get(elem)
        if mass is None:
            return None
        total += mass * cnt
    return total
