"""Bounded Lewis candidates on a fixed, complete atom-and-connectivity graph.

This is a deliberately limited neutral main-group model, not electronic-state
identification. It never changes a Species identity or an RNG evidence record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import time

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from rdkit import Chem, rdBase


LEWIS_SCHEMA = "reacnet-scope/lewis-annotation/v1"
LEWIS_METHOD = "neutral-main-group-milp/v2"
_SCORE_WEIGHTS = {"unpaired_electrons": 100, "absolute_formal_charge": 10,
                  "shell_deficit": 10, "expanded_shell_excess": 1}
# Valence electrons and closed-shell bond-order sums by formal charge.
# Lower sums retain unpaired electrons; P/S additionally permit expanded shells.
# Positive halogen states are outside this naming model. This restriction is
# a conservative domain choice, not a universal statement about halogen chemistry.
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
            "charge_policy": {"positive_halogen": "forbidden"},
            "objective_policy": "weighted_heuristic",
            "score_weights": dict(_SCORE_WEIGHTS),
            "radical_electrons_basis": "RDKit_representation_not_electronic_state",
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
    for charge, closed_sums in targets.items():
        for closed_sum in closed_sums:
            for bond_sum in range(degree, min(closed_sum, 3 * degree) + 1):
                radicals = closed_sum - bond_sum
                nonbonding = electrons - charge - bond_sum
                if nonbonding < radicals or (nonbonding - radicals) % 2:
                    continue
                occupancy = 2 * bond_sum + nonbonding
                shell = 2 if element == "H" else 6 if element == "B" and charge == 0 else 8
                if element in {"P", "S"}:
                    shell = max(8, 2 * math.ceil(occupancy / 2))
                if occupancy > shell or shell > 12:
                    continue
                penalty = (_SCORE_WEIGHTS["unpaired_electrons"] * radicals
                           + _SCORE_WEIGHTS["absolute_formal_charge"] * abs(charge)
                           + _SCORE_WEIGHTS["shell_deficit"] * (shell - occupancy)
                           + _SCORE_WEIGHTS["expanded_shell_excess"] * max(0, shell - 8))
                states.add((bond_sum, charge, radicals, penalty))
    return sorted(states)


def _solve(graph, options: LewisOptions, seconds: float) -> dict:
    atoms = list(graph.GetAtoms())
    bonds = list(graph.GetBonds())
    states = [_atom_states(atom) for atom in atoms]
    if any(not items for items in states):
        return {"status": "no_candidate", "reason": "no_allowed_atom_state"}
    offsets, width = [], len(bonds)
    for items in states:
        offsets.append(width)
        width += len(items)
    fragments = Chem.GetMolFrags(graph)
    matrix = np.zeros((2 * len(atoms) + len(fragments), width))
    rhs = np.zeros(len(matrix))
    objective = np.zeros(width)
    lower, upper = np.zeros(width), np.ones(width)
    lower[:len(bonds)], upper[:len(bonds)] = 1, 3
    for j, bond in enumerate(bonds):
        matrix[bond.GetBeginAtomIdx(), j] = 1
        matrix[bond.GetEndAtomIdx(), j] = 1
        if bond.GetBeginAtom().GetSymbol() == "H" or bond.GetEndAtom().GetSymbol() == "H":
            upper[j] = 1
    fragment_for = {i: j for j, fragment in enumerate(fragments) for i in fragment}
    for i, items in enumerate(states):
        rhs[len(atoms) + i] = 1
        for k, (bond_sum, charge, _radicals, penalty) in enumerate(items):
            j = offsets[i] + k
            matrix[i, j] = -bond_sum
            matrix[len(atoms) + i, j] = 1
            matrix[2 * len(atoms) + fragment_for[i], j] = charge
            objective[j] = penalty
    result = milp(objective, integrality=np.ones(width), bounds=Bounds(lower, upper),
                  constraints=LinearConstraint(matrix, rhs, rhs),
                  options={"time_limit": seconds, "node_limit": options.max_nodes, "mip_rel_gap": 0.0})
    fields = {"optimization_complete": result.status in (0, 2),
              "solver_status": int(result.status), "solver_message": str(result.message)}
    if result.status == 1:
        # Keep no incumbent: a bounded search cannot silently replace the name
        # with a candidate dependent on when the solver happened to stop.
        return {**fields, "status": "timeout" if "time" in str(result.message).lower() else "truncated",
                "reason": "solver_budget", "truncated": True}
    if result.status != 0 or result.x is None:
        return {**fields, "status": "no_candidate" if result.status == 2 else "solver_failed",
                "reason": "model_infeasible" if result.status == 2 else "solver_failed"}
    values = np.rint(result.x)
    if not np.allclose(values, result.x, atol=1e-5) or not np.allclose(matrix @ values, rhs):
        return {**fields, "status": "solver_failed", "reason": "invalid_integer_solution"}
    molecule = Chem.RWMol(graph)
    for j, bond in enumerate(molecule.GetBonds()):
        bond.SetBondType({1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}[int(values[j])])
    for i, items in enumerate(states):
        _b, charge, radicals, _p = items[int(np.argmax(values[offsets[i]:offsets[i] + len(items)]))]
        atom = molecule.GetAtomWithIdx(i)
        atom.SetFormalCharge(charge)
        atom.SetNumRadicalElectrons(radicals)
        atom.SetNoImplicit(True)
    try:
        with rdBase.BlockLogs():
            checked = Chem.Mol(molecule)
            flag = Chem.SanitizeMol(checked, catchErrors=True)
            if flag != Chem.SanitizeFlags.SANITIZE_NONE:
                return {**fields, "status": "candidate_rejected", "reason": "candidate_rdkit_check_failed"}
            candidate = Chem.MolToSmiles(checked, canonical=True)
            if any(atom.GetSymbol() in {"F", "Cl", "Br", "I"} and atom.GetFormalCharge() > 0
                   for atom in checked.GetAtoms()):
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
            "model_score": float(result.fun),
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
