"""Bounded Lewis candidates on a fixed, complete atom-and-connectivity graph.

This is a deliberately limited neutral main-group model, not electronic-state
identification. It never changes a Species identity or an RNG evidence record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from itertools import combinations
import json
import math
import time

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from rdkit import Chem, rdBase


LEWIS_SCHEMA = "reacnet-scope/lewis-annotation/v2"
LEWIS_METHOD = "neutral-main-group-milp/v4"
_OBJECTIVES = ("charged_shell_deficit", "hetero_shell_deficit", "small_ring_unsaturation",
               "unpaired_electrons", "shell_deficit", "formal_charge_count",
               "expanded_shell_excess", "charge_electronegativity")
# Pauling electronegativities x100, integer-scaled for exact lexicographic locks.
# Source: https://periodic-table.rsc.org/ (element atomic data).
_ELECTRONEGATIVITY = {"H": 220, "B": 204, "C": 255, "N": 304, "O": 344, "F": 398,
                      "Si": 190, "P": 219, "S": 258, "Cl": 316, "Br": 296, "I": 266}
_HALOGENS = frozenset({"F", "Cl", "Br", "I"})
# Valence electrons and closed-shell bond-order sums by formal charge.
# Lower sums retain unpaired electrons; P/S additionally permit expanded shells.
_STATES = {
    "H": (1, {-1: (0,), 0: (1,), 1: (0,)}),
    "B": (3, {-1: (4,), 0: (3,), 1: (2,)}),
    "C": (4, {-1: (3,), 0: (4,), 1: (3,)}),
    "N": (5, {-1: (2,), 0: (3,), 1: (4,)}),
    "O": (6, {-1: (1,), 0: (2,), 1: (3,)}),
    "F": (7, {-1: (0,), 0: (1,)}),
    "Si": (4, {-1: (3,), 0: (4,), 1: (3,)}),
    "P": (5, {-1: (2, 4, 6), 0: (3, 5), 1: (4, 6)}),
    "S": (6, {-1: (1, 3, 5), 0: (2, 4, 6), 1: (3, 5)}),
    "Cl": (7, {-1: (0,), 0: (1,)}),
    "Br": (7, {-1: (0,), 0: (1,)}),
    "I": (7, {-1: (0,), 0: (1,)}),
}


@dataclass(frozen=True)
class LewisOptions:
    assume_neutral: bool = False
    net_charge: int | None = None
    multiplicity: int | None = None
    max_atoms: int = 150
    max_seconds: float = 5.0
    max_nodes: int = 10_000

    def __post_init__(self):
        if not isinstance(self.assume_neutral, bool):
            raise ValueError("assume_neutral must be a boolean")
        for name in ("net_charge", "multiplicity", "max_atoms", "max_nodes"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} must be an integer")
        if not 1 <= self.max_atoms <= 500 or not 1 <= self.max_nodes <= 1_000_000:
            raise ValueError("Lewis atom/node budget is out of range")
        if not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 300:
            raise ValueError("max_seconds must be finite and in (0, 300]")
        if self.multiplicity is not None and self.multiplicity < 1:
            raise ValueError("multiplicity must be positive")


def method_metadata() -> dict:
    return {"method": LEWIS_METHOD, "rdkit_version": rdBase.rdkitVersion,
            "scipy_version": scipy.__version__, "elements": sorted(_STATES),
            "fragment_charge": 0, "spin_model": "unspecified",
            "objective_order": list(_OBJECTIVES),
            "charge_policy": {"positive_halogen": "forbidden",
                              "negative_sites": "N_O_halogen_or_C_with_one_heavy_neighbor",
                              "pairing": "one_to_one_opposite_charges_on_existing_edges"},
            "small_ring_policy": {"sizes": [3, 4],
                                  "objective": "triple_bonds_plus_adjacent_double_bond_pairs",
                                  "counting": "unique_bonds_and_pairs_in_any_small_cycle",
                                  "claim": "naming_preference_not_strain_energy"},
            "charge_location_policy": {"objective": "min_sum_formal_charge_times_electronegativity",
                                       "scale": "Pauling_x100", "values": dict(_ELECTRONEGATIVITY)},
            "radical_electrons_basis": "RDKit_representation_not_electronic_state",
            "neutral_carbon_pairing": "all_allowed_unpaired_lone_pair_partitions",
            "electron_assignment_claim": "one_model_assignment_not_spin_identification",
            "candidate_policy": "one_model_optimum_not_exhaustive_enumeration"}


class _InputProblem(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _complete_graph(smiles: str, max_atoms: int):
    if not smiles or len(smiles) > 30_000 or "_unknownSMILES" in smiles:
        raise _InputProblem("input_incomplete", "missing_or_unknown_structure")
    params = Chem.SmilesParserParams()
    params.sanitize = False
    params.removeHs = False
    params.parseName = False
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(smiles, params)
    if molecule is None or not molecule.GetNumAtoms():
        raise _InputProblem("input_incomplete", "smiles_parse_failed")
    if molecule.GetNumAtoms() > max_atoms:
        raise _InputProblem("unsupported", "atom_budget")
    unsupported = sorted({a.GetSymbol() for a in molecule.GetAtoms()} - _STATES.keys())
    if unsupported:
        raise _InputProblem("unsupported", "unsupported_elements:" + ",".join(unsupported))
    # Only SMILES-defined hydrogens are materialized. An invalid original
    # valence cannot be used to guess an implicit hydrogen count.
    try:
        with rdBase.BlockLogs():
            for atom in molecule.GetAtoms():
                atom.UpdatePropertyCache(strict=not atom.GetNoImplicit())
            hydrogen_counts = [a.GetNumExplicitHs() + a.GetNumImplicitHs()
                               for a in molecule.GetAtoms()]
    except (ValueError, RuntimeError) as exc:
        raise _InputProblem("input_incomplete", "hydrogen_count_unresolved") from exc
    if molecule.GetNumAtoms() + sum(hydrogen_counts) > max_atoms:
        raise _InputProblem("unsupported", "atom_budget")
    fragments = Chem.GetMolFrags(molecule)
    charges = [sum(molecule.GetAtomWithIdx(i).GetFormalCharge() for i in fragment)
               for fragment in fragments]
    graph = Chem.RWMol()
    for atom in molecule.GetAtoms():
        copied = Chem.Atom(atom.GetAtomicNum())
        copied.SetIsotope(atom.GetIsotope())
        copied.SetNoImplicit(True)
        graph.AddAtom(copied)
    for bond in molecule.GetBonds():
        graph.AddBond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx(), Chem.BondType.SINGLE)
    for i, count in enumerate(hydrogen_counts):
        for _ in range(count):
            hydrogen = Chem.Atom(1)
            hydrogen.SetNoImplicit(True)
            graph.AddBond(i, graph.AddAtom(hydrogen), Chem.BondType.SINGLE)
    graph.UpdatePropertyCache(strict=False)
    canonical = Chem.MolToSmiles(graph, canonical=True, allHsExplicit=True)
    # Reparse the canonical connectivity for stable solver ordering, including
    # when the original representative differs only in bond orders.
    ordered = Chem.MolFromSmiles(canonical, params)
    ordered.UpdatePropertyCache(strict=False)
    return ordered, canonical, charges


def _atom_states(atom):
    element, degree = atom.GetSymbol(), atom.GetDegree()
    electrons, targets = _STATES[element]
    states = set()
    heavy_degree = sum(neighbor.GetAtomicNum() != 1 for neighbor in atom.GetNeighbors())
    for charge, closed_sums in targets.items():
        # Conservative model domain, not universal chemistry: e.g. borate,
        # phosphide and chloronium representations require a different model.
        if charge < 0 and not (element in {"N", "O"} | _HALOGENS
                               or element == "C" and heavy_degree == 1):
            continue
        for closed_sum in closed_sums:
            for bond_sum in range(degree, min(closed_sum, 3 * degree) + 1):
                nonbonding = electrons - charge - bond_sum
                candidates = range(nonbonding % 2, nonbonding + 1, 2) if element == "C" and charge == 0 else (closed_sum - bond_sum,)
                for radicals in candidates:
                    if nonbonding < radicals or (nonbonding - radicals) % 2:
                        continue
                    occupancy = 2 * bond_sum + nonbonding
                    shell = 2 if element == "H" else 6 if element == "B" and charge == 0 else 8
                    if element in {"P", "S"}:
                        shell = max(8, 2 * math.ceil(occupancy / 2))
                    if occupancy > shell or shell > 12:
                        continue
                    states.add((bond_sum, charge, radicals, shell - occupancy, max(0, shell - 8)))
    return sorted(states)


def _optimize(objectives, matrix, rhs, lower, upper, options, seconds):
    """Prove each integer objective optimal before considering the next one.

    All passes share the same time/node budget. An incumbent, or even a proven
    optimum for only a prefix of objectives, is never a finished annotation.
    """
    deadline = time.monotonic() + seconds
    used_nodes, scores, values = 0, {}, None
    fields = {"optimization_complete": False, "model_objectives": scores,
              "optimization_stages_completed": 0, "solver_nodes_used": 0}
    for name, objective in zip(_OBJECTIVES, objectives, strict=True):
        if values is not None and not np.any(objective):
            scores[name] = 0
            fields["optimization_stages_completed"] += 1
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0 or used_nodes >= options.max_nodes:
            return None, {**fields, "status": "timeout" if remaining <= 0 else "truncated",
                          "reason": "solver_budget", "truncated": True}
        result = milp(objective, integrality=np.ones(len(objective)), bounds=Bounds(lower, upper),
                      constraints=LinearConstraint(matrix, rhs, rhs),
                      options={"time_limit": remaining, "node_limit": options.max_nodes - used_nodes,
                               "mip_rel_gap": 0.0})
        used_nodes += int(getattr(result, "mip_node_count", 0) or 0)
        fields.update(solver_status=int(result.status), solver_message=str(result.message),
                      solver_nodes_used=used_nodes)
        if result.status == 1:
            return None, {**fields, "status": "timeout" if "time" in str(result.message).lower() else "truncated",
                          "reason": "solver_budget", "truncated": True}
        if result.status != 0 or result.x is None:
            infeasible = result.status == 2 and not scores
            return None, {**fields, "optimization_complete": infeasible,
                          "status": "no_candidate" if infeasible else "solver_failed",
                          "reason": "model_infeasible" if infeasible else "solver_failed"}
        values = np.rint(result.x)
        if (not np.allclose(values, result.x, atol=1e-5)
                or not np.allclose(matrix @ values, rhs)
                or np.any(values < lower) or np.any(values > upper)):
            return None, {**fields, "status": "solver_failed", "reason": "invalid_integer_solution"}
        optimum = int(round(float(objective @ values)))
        scores[name] = optimum
        fields["optimization_stages_completed"] += 1
        # Exact integer equality prevents lower-priority improvements from
        # buying even one unit of a higher-priority objective.
        matrix = np.vstack((matrix, objective))
        rhs = np.append(rhs, optimum)
    return values, {**fields, "optimization_complete": True}


def _charge_policy_holds(molecule):
    """Recheck the emitted representation, since sanitization may change it."""
    positives, negatives = [], set()
    for atom in molecule.GetAtoms():
        charge, element = atom.GetFormalCharge(), atom.GetSymbol()
        if charge not in (-1, 0, 1) or (charge > 0 and element in _HALOGENS):
            return False
        if charge < 0:
            heavy_degree = sum(n.GetAtomicNum() != 1 for n in atom.GetNeighbors())
            if not (element in {"N", "O"} | _HALOGENS or element == "C" and heavy_degree == 1):
                return False
            negatives.add(atom.GetIdx())
        if charge > 0:
            positives.append(atom.GetIdx())
    matched = {}
    def pair(positive, seen):
        for neighbor in molecule.GetAtomWithIdx(positive).GetNeighbors():
            negative = neighbor.GetIdx()
            if negative not in negatives or negative in seen:
                continue
            seen.add(negative)
            if negative not in matched or pair(matched[negative], seen):
                matched[negative] = positive
                return True
        return False
    return len(positives) == len(negatives) and all(pair(i, set()) for i in positives)


def _small_ring_bond_pairs(graph):
    """Find every adjacent edge pair in a triangle or quadrilateral.

    Detect local 3/4-cycles directly: a ring basis can omit a chorded or fused
    small cycle. Deduplicate pairs so shared rings do not multiply a penalty.
    This never enumerates longer cycles or uses the input bond orders.
    """
    neighbors = [{n.GetIdx() for n in a.GetNeighbors()} for a in graph.GetAtoms()]
    pairs = set()
    for center, adjacent in enumerate(neighbors):
        for left, right in combinations(sorted(adjacent), 2):
            triangle = right in neighbors[left]
            quadrilateral = bool((neighbors[left] & neighbors[right]) - {center})
            if triangle or quadrilateral:
                pairs.add(tuple(sorted((graph.GetBondBetweenAtoms(center, left).GetIdx(),
                                        graph.GetBondBetweenAtoms(center, right).GetIdx()))))
    return sorted(pairs)


def _solve(graph, options: LewisOptions, seconds: float) -> dict:
    started = time.monotonic()
    atoms = list(graph.GetAtoms())
    bonds = list(graph.GetBonds())
    states = [_atom_states(atom) for atom in atoms]
    if any(not items for items in states):
        return {"status": "no_candidate", "reason": "no_allowed_atom_state"}
    offsets, width = [], len(bonds)
    for items in states:
        offsets.append(width)
        width += len(items)
    pairing_offset = width
    width += 2 * len(bonds)
    ring_pairs = _small_ring_bond_pairs(graph)
    ring_bonds = sorted({j for pair in ring_pairs for j in pair})
    ring_offsets = {j: width + 3 * k for k, j in enumerate(ring_bonds)}
    width += 3 * len(ring_bonds)
    cumulene_offset = width
    width += 2 * len(ring_pairs)
    fragments = Chem.GetMolFrags(graph)
    pairing_row = 2 * len(atoms) + len(fragments)
    ring_row = pairing_row + 2 * len(atoms)
    matrix = np.zeros((ring_row + 2 * len(ring_bonds) + len(ring_pairs), width))
    rhs = np.zeros(len(matrix))
    objectives = np.zeros((len(_OBJECTIVES), width))
    lower, upper = np.zeros(width), np.ones(width)
    lower[:len(bonds)], upper[:len(bonds)] = 1, 3
    for j, bond in enumerate(bonds):
        left, right = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        matrix[left, j] = 1
        matrix[right, j] = 1
        # Each directed pairing binary contributes once to a positive atom's
        # row and once to an adjacent negative atom's row.
        for direction, (positive, negative) in enumerate(((left, right), (right, left))):
            column = pairing_offset + 2 * j + direction
            matrix[pairing_row + positive, column] = 1
            matrix[pairing_row + len(atoms) + negative, column] = 1
        if bond.GetBeginAtom().GetSymbol() == "H" or bond.GetEndAtom().GetSymbol() == "H":
            upper[j] = 1
    fragment_for = {i: j for j, fragment in enumerate(fragments) for i in fragment}
    for i, items in enumerate(states):
        rhs[len(atoms) + i] = 1
        for k, (bond_sum, charge, radicals, deficit, expanded) in enumerate(items):
            j = offsets[i] + k
            matrix[i, j] = -bond_sum
            matrix[len(atoms) + i, j] = 1
            matrix[2 * len(atoms) + fragment_for[i], j] = charge
            if charge:
                matrix[pairing_row + (0 if charge > 0 else len(atoms)) + i, j] = -1
            objectives[:, j] = (deficit if charge else 0,
                                 deficit if atoms[i].GetSymbol() != "C" else 0,
                                 0, radicals, deficit, abs(charge), expanded,
                                 charge * _ELECTRONEGATIVITY[atoms[i].GetSymbol()])
    ring_objective = _OBJECTIVES.index("small_ring_unsaturation")
    for k, j in enumerate(ring_bonds):
        row, column = ring_row + 2 * k, ring_offsets[j]
        # Exactly one single/double/triple indicator, tied to the bond order.
        matrix[row, column:column + 3] = 1
        rhs[row] = 1
        matrix[row + 1, j] = 1
        matrix[row + 1, column:column + 3] = (-1, -2, -3)
        objectives[ring_objective, column + 2] = 1
    for k, (left, right) in enumerate(ring_pairs):
        row = ring_row + 2 * len(ring_bonds) + k
        column = cumulene_offset + 2 * k
        matrix[row, ring_offsets[left] + 1] = 1
        matrix[row, ring_offsets[right] + 1] = 1
        # d_left + d_right = 2 * both + exactly_one (all binary).
        matrix[row, column:column + 2] = (-2, -1)
        objectives[ring_objective, column] = 1
    values, fields = _optimize(objectives, matrix, rhs, lower, upper, options,
                               seconds - (time.monotonic() - started))
    if values is None:
        return fields
    molecule = Chem.RWMol(graph)
    for j, bond in enumerate(molecule.GetBonds()):
        bond.SetBondType({1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}[int(values[j])])
    model_atoms = []
    for i, items in enumerate(states):
        _b, charge, radicals, _deficit, _expanded = items[int(np.argmax(values[offsets[i]:offsets[i] + len(items)]))]
        atom = molecule.GetAtomWithIdx(i)
        atom.SetFormalCharge(charge)
        atom.SetNumRadicalElectrons(radicals)
        atom.SetNoImplicit(True)
        nonbonding = _STATES[atom.GetSymbol()][0] - charge - _b
        model_atoms.append({"index": i, "element": atom.GetSymbol(), "isotope": atom.GetIsotope(),
                            "formal_charge": charge, "unpaired_electrons": radicals,
                            "lone_pairs": (nonbonding - radicals) // 2})
    model_graph = {"index_basis": "local_zero_based_not_RNG_atom_ids", "atoms": model_atoms,
                   "bonds": [[b.GetBeginAtomIdx(), b.GetEndAtomIdx(), int(b.GetBondTypeAsDouble())]
                             for b in molecule.GetBonds()]}
    try:
        with rdBase.BlockLogs():
            checked = Chem.Mol(molecule)
            flag = Chem.SanitizeMol(checked, catchErrors=True)
            if flag != Chem.SanitizeFlags.SANITIZE_NONE:
                return {**fields, "status": "candidate_rejected", "reason": "candidate_rdkit_check_failed"}
            candidate = Chem.MolToSmiles(checked, canonical=True)
            if not _charge_policy_holds(checked):
                return {**fields, "status": "candidate_rejected", "reason": "candidate_charge_policy_failed"}
    except (ValueError, RuntimeError):
        return {**fields, "status": "solver_failed", "reason": "candidate_serialization_failed"}
    # Sanitization is performed on a copy and may standardize charges/bonds.
    # Verify its output still has exactly the supplied atoms and connectivity.
    _candidate_graph, candidate_key, candidate_charges = _complete_graph(candidate, options.max_atoms)
    original_key = Chem.MolToSmiles(graph, canonical=True, allHsExplicit=True)
    if candidate_key != original_key or any(candidate_charges):
        return {**fields, "status": "solver_failed", "reason": "candidate_graph_or_charge_changed"}
    return {**fields, "status": "generated", "reason": "one_heuristic_candidate",
            "candidate_smiles": candidate, "candidate_count": 1,
            "model_graph": model_graph,
            "model_unpaired_electrons": sum(a["unpaired_electrons"] for a in model_atoms),
            "formal_charge": sum(a.GetFormalCharge() for a in checked.GetAtoms()),
            "radical_electrons": sum(a.GetNumRadicalElectrons() for a in checked.GetAtoms())}


def annotate_lewis(smiles: str, *, options: LewisOptions | None = None,
                   miso: int | None = None, source_revision: dict | None = None,
                   input_source: str = "representative_smiles", _cache=None) -> dict:
    """Return one auditable annotation; never use its name as Species identity.

    Net charge zero must be explicitly supplied or assumed. Known nonzero
    fragment charges and any requested spin multiplicity are unsupported.
    """
    options = options or LewisOptions()
    if miso is not None and (type(miso) is not int or miso not in (0, 1, 2)):
        raise ValueError("miso must be 0, 1, 2 or unknown")
    started = time.monotonic()
    result = {"schema_version": LEWIS_SCHEMA, "original_species": smiles,
              "original_smiles": smiles, "candidate_smiles": None,
              "candidate_count": 0, "candidate_search_complete": False,
              "optimization_complete": False, "truncated": False,
              "input_graph_id": None, "input_source": input_source,
              "coverage": "selected_representative_only" if miso == 2 else
                          "representative_scope_unknown" if miso is None else "representative_connectivity",
              "miso": miso, "source_revision": source_revision or {},
              "method": method_metadata(), "assumptions": asdict(options),
              "chemical_validity": "not_established", "unique_electronic_state": "not_established"}
    try:
        graph, canonical, charges = _complete_graph(smiles, options.max_atoms)
        result["input_graph_id"] = hashlib.sha256(canonical.encode()).hexdigest()
        result["input_connectivity"] = canonical
        if options.net_charge not in (None, 0) or any(charges):
            raise _InputProblem("unsupported", "nonzero_fragment_charge")
        if options.multiplicity is not None:
            raise _InputProblem("unsupported", "spin_constraint")
        if options.net_charge is None and not options.assume_neutral:
            raise _InputProblem("needs_input", "neutral_assumption_required")
        seconds = options.max_seconds - (time.monotonic() - started)
        if seconds <= 0:
            raise _InputProblem("timeout", "input_processing_budget")
        key = json.dumps([canonical, asdict(options), method_metadata()], sort_keys=True)
        solved = _cache.get(key) if _cache is not None else None
        if solved is None:
            solved = _solve(graph, options, seconds)
            if _cache is not None and solved["status"] not in {"timeout", "truncated", "solver_failed"}:
                _cache[key] = solved
        result.update(solved)
    except _InputProblem as exc:
        result.update(status=exc.status, reason=exc.reason, truncated=exc.status == "timeout")
    result["elapsed_seconds"] = time.monotonic() - started
    return result


def lewis_alias_key(smiles: str) -> str:
    """Canonical search key only; never replaces an original RNG identity."""
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(molecule, canonical=True) if molecule is not None else smiles


def lewis_model_molecule(annotation: dict):
    """Reconstruct the stored model assignment without reparsing lossy SMILES.

    Atom indices here are local diagram indices, never RNG/trajectory Atom IDs.
    The graph preserves one model u/p assignment, not a determined spin state.
    """
    graph = annotation.get("model_graph") or {}
    if not isinstance(graph, dict):
        raise ValueError("invalid Lewis model graph")
    atoms, bonds = graph.get("atoms"), graph.get("bonds")
    if (graph.get("index_basis") != "local_zero_based_not_RNG_atom_ids"
            or not isinstance(atoms, list) or not 1 <= len(atoms) <= 500
            or not isinstance(bonds, list) or len(bonds) > 1500):
        raise ValueError("invalid Lewis model graph")
    molecule = Chem.RWMol()
    for index, row in enumerate(atoms):
        if not isinstance(row, dict) or type(row.get("index")) is not int or row["index"] != index or row.get("element") not in _STATES:
            raise ValueError("invalid Lewis model atom")
        if any(type(row.get(k)) is not int for k in ("isotope", "formal_charge", "unpaired_electrons", "lone_pairs")):
            raise ValueError("invalid Lewis model electron fields")
        if not 0 <= row["isotope"] <= 300 or row["formal_charge"] not in (-1, 0, 1) or not 0 <= row["unpaired_electrons"] <= 8 or not 0 <= row["lone_pairs"] <= 6:
            raise ValueError("invalid Lewis model electron values")
        atom = Chem.Atom(row["element"])
        atom.SetNoImplicit(True)
        atom.SetIsotope(row["isotope"])
        atom.SetFormalCharge(row["formal_charge"])
        atom.SetNumRadicalElectrons(row["unpaired_electrons"])
        atom.SetIntProp("model_lone_pairs", row["lone_pairs"])
        molecule.AddAtom(atom)
    seen = set()
    for bond in bonds:
        if not isinstance(bond, list) or len(bond) != 3 or any(type(v) is not int for v in bond):
            raise ValueError("invalid Lewis model bond")
        left, right, order = bond
        pair = tuple(sorted((left, right)))
        if not 0 <= left < len(atoms) or not 0 <= right < len(atoms) or left == right or pair in seen or order not in (1, 2, 3):
            raise ValueError("invalid Lewis model bond")
        seen.add(pair)
        molecule.AddBond(left, right, {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}[order])
    molecule.UpdatePropertyCache(strict=False)
    for atom in molecule.GetAtoms():
        assigned = (sum(b.GetBondTypeAsDouble() for b in atom.GetBonds()) + atom.GetFormalCharge()
                    + atom.GetNumRadicalElectrons() + 2 * atom.GetIntProp("model_lone_pairs"))
        if assigned != _STATES[atom.GetSymbol()][0]:
            raise ValueError("inconsistent Lewis electron bookkeeping")
    if not _charge_policy_holds(molecule):
        raise ValueError("inconsistent Lewis charge policy")
    return molecule.GetMol()
