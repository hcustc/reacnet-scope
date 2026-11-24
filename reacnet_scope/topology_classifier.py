"""Topology and isomer classification for reactive MD species.

Prevents formula-only misassignments encountered in complex oxidation networks
by explicitly distinguishing:

- Ring topology classes: ``acyclic``, ``monocyclic-<n>``, ``fused-<sizes>``
  (e.g. ``fused-6-5-6`` dibenzofuran core vs ``fused-6-6-6`` dioxin core vs
  ``fused-6-6`` naphthalene vs ``fused-6-5`` indene), ``spiro-<sizes>``,
  and ``linked-<sizes>`` (e.g. ``linked-5-5`` linked cyclopentadienyl dimers).
- Six-membered ring disubstitution patterns: ``1,2-ortho``, ``1,3-meta``,
  and ``1,4-para`` (e.g. distinguishing 1,3-meta non-Kekule diradical quinone
  precursors from 1,4-para benzoquinones).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from rdkit import Chem, RDLogger


TOPOLOGY_CLASSIFIER_SCHEMA = "reacnet-scope/topology-classifier/v1"


def _parse_mol_permissive(smiles: str) -> Chem.Mol | None:
    token = str(smiles or "").strip()
    if not token:
        return None
    RDLogger.DisableLog("rdApp.*")
    try:
        mol = Chem.MolFromSmiles(token)
        if mol is not None:
            return mol
        mol = Chem.MolFromSmiles(token, sanitize=False)
        if mol is not None:
            mol.UpdatePropertyCache(strict=False)
            Chem.GetSymmSSSR(mol)
        return mol
    finally:
        RDLogger.EnableLog("rdApp.*")


def _ring_system_signature(mol: Chem.Mol) -> dict[str, Any]:
    ring_info = mol.GetRingInfo()
    atom_rings = [tuple(int(i) for i in ring) for ring in ring_info.AtomRings()]
    if not atom_rings:
        return {
            "ring_count": 0,
            "ring_sizes": [],
            "ring_system": "acyclic",
            "hetero_ring_tags": [],
            "core_motif": "acyclic",
        }

    sizes = sorted((len(r) for r in atom_rings), reverse=True)
    hetero_tags: list[str] = []
    for ring in atom_rings:
        heteros = sorted(
            mol.GetAtomWithIdx(idx).GetSymbol()
            for idx in ring
            if mol.GetAtomWithIdx(idx).GetSymbol() != "C"
        )
        if heteros:
            hetero_tags.append(f"{len(ring)}({''.join(heteros)})")
        else:
            hetero_tags.append(f"{len(ring)}(C)")
    hetero_tags.sort()

    if len(atom_rings) == 1:
        ring_system = f"monocyclic-{sizes[0]}"
        core_motif = ring_system
        return {
            "ring_count": 1,
            "ring_sizes": sizes,
            "ring_system": ring_system,
            "hetero_ring_tags": hetero_tags,
            "core_motif": core_motif,
        }

    # Build ring-overlap graph to distinguish fused (>=2 shared atoms),
    # spiro (1 shared atom), and linked (0 shared atoms).
    max_overlap = 0
    fused_adj: dict[int, set[int]] = {i: set() for i in range(len(atom_rings))}
    spiro_pairs = 0
    for i in range(len(atom_rings)):
        set_i = set(atom_rings[i])
        for j in range(i + 1, len(atom_rings)):
            overlap = len(set_i & set(atom_rings[j]))
            if overlap > max_overlap:
                max_overlap = overlap
            if overlap >= 2:
                fused_adj[i].add(j)
                fused_adj[j].add(i)
            elif overlap == 1:
                spiro_pairs += 1

    # Connected components of fused rings
    visited: set[int] = set()
    fused_components: list[list[int]] = []
    for i in range(len(atom_rings)):
        if i in visited:
            continue
        stack = [i]
        comp: list[int] = []
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            comp.append(node)
            stack.extend(fused_adj[node] - visited)
        fused_components.append(comp)

    if len(fused_components) == 1 and max_overlap >= 2:
        # Order fused ring sizes with central bridge ring in the middle for 3-ring systems
        if len(atom_rings) == 3:
            degrees = {i: len(fused_adj[i]) for i in range(3)}
            center = [i for i, deg in degrees.items() if deg == 2]
            ends = [i for i, deg in degrees.items() if deg == 1]
            if len(center) == 1 and len(ends) == 2:
                ordered = [
                    len(atom_rings[ends[0]]),
                    len(atom_rings[center[0]]),
                    len(atom_rings[ends[1]]),
                ]
                ring_system = f"fused-{'-'.join(map(str, ordered))}"
                center_heteros = [
                    mol.GetAtomWithIdx(idx).GetSymbol()
                    for idx in atom_rings[center[0]]
                    if mol.GetAtomWithIdx(idx).GetSymbol() != "C"
                ]
                if ordered == [6, 5, 6] and center_heteros == ["O"]:
                    core_motif = "dibenzofuran_6_5_6_core"
                elif ordered == [6, 6, 6] and sorted(center_heteros) == ["O", "O"]:
                    core_motif = "dibenzodioxin_6_6_6_core"
                else:
                    core_motif = ring_system
                return {
                    "ring_count": len(atom_rings),
                    "ring_sizes": sizes,
                    "ring_system": ring_system,
                    "hetero_ring_tags": hetero_tags,
                    "core_motif": core_motif,
                }
        ring_system = f"fused-{'-'.join(map(str, sizes))}"
        core_motif = ring_system
    elif max_overlap == 0:
        ring_system = f"linked-{'-'.join(map(str, sizes))}"
        core_motif = ring_system
    elif spiro_pairs > 0 and max_overlap == 1:
        ring_system = f"spiro-{'-'.join(map(str, sizes))}"
        core_motif = ring_system
    else:
        ring_system = f"mixed-{'-'.join(map(str, sizes))}"
        core_motif = ring_system

    return {
        "ring_count": len(atom_rings),
        "ring_sizes": sizes,
        "ring_system": ring_system,
        "hetero_ring_tags": hetero_tags,
        "core_motif": core_motif,
    }


def _six_ring_oxygen_substitution(mol: Chem.Mol) -> list[str]:
    """Identify ortho/meta/para oxygen-substitution patterns on 6-membered carbon rings."""
    patterns: list[str] = []
    atom_rings = [tuple(int(i) for i in ring) for ring in mol.GetRingInfo().AtomRings()]
    for ring in atom_rings:
        if len(ring) != 6:
            continue
        if any(mol.GetAtomWithIdx(idx).GetSymbol() != "C" for idx in ring):
            continue
        ring_set = set(ring)
        o_positions: list[int] = []
        for pos, idx in enumerate(ring):
            atom = mol.GetAtomWithIdx(idx)
            has_exocyclic_o = any(
                nbr.GetIdx() not in ring_set and nbr.GetSymbol() == "O"
                for nbr in atom.GetNeighbors()
            )
            if has_exocyclic_o:
                o_positions.append(pos)
        if len(o_positions) == 2:
            diff = abs(o_positions[1] - o_positions[0])
            step = min(diff, 6 - diff)
            if step == 1:
                patterns.append("1,2-ortho-dioxy")
            elif step == 2:
                patterns.append("1,3-meta-dioxy")
            elif step == 3:
                patterns.append("1,4-para-dioxy")
        elif len(o_positions) >= 3:
            patterns.append(f"poly-oxy({len(o_positions)})")
        elif len(o_positions) == 1:
            patterns.append("mono-oxy")
    return sorted(set(patterns))


def _hill_formula(mol: Chem.Mol) -> str:
    counts = Counter(atom.GetSymbol() for atom in mol.GetAtoms())
    # Account for implicit hydrogens when RDKit parsed a Kekule/aromatic SMILES.
    implicit_h = sum(atom.GetTotalNumHs(includeNeighbors=False) for atom in mol.GetAtoms())
    if implicit_h:
        counts["H"] += implicit_h
    parts: list[str] = []
    if "C" in counts:
        c = counts.pop("C")
        parts.append(f"C{c if c > 1 else ''}")
        if "H" in counts:
            h = counts.pop("H")
            parts.append(f"H{h if h > 1 else ''}")
    for elem in sorted(counts):
        n = counts[elem]
        parts.append(f"{elem}{n if n > 1 else ''}")
    return "".join(parts)


def classify_species_topology(smiles: str) -> dict[str, Any]:
    """Return structured ring-system and substitution-pattern descriptors for one SMILES."""
    mol = _parse_mol_permissive(smiles)
    if mol is None:
        return {
            "schema_version": TOPOLOGY_CLASSIFIER_SCHEMA,
            "smiles": str(smiles),
            "parsed": False,
            "formula": "",
            "ring_count": 0,
            "ring_sizes": [],
            "ring_system": "unparsed",
            "hetero_ring_tags": [],
            "core_motif": "unparsed",
            "oxygen_substitution_patterns": [],
        }

    ring_info = _ring_system_signature(mol)
    o_patterns = _six_ring_oxygen_substitution(mol)
    return {
        "schema_version": TOPOLOGY_CLASSIFIER_SCHEMA,
        "smiles": str(smiles),
        "parsed": True,
        "formula": _hill_formula(mol),
        **ring_info,
        "oxygen_substitution_patterns": o_patterns,
    }


def classify_species_collection(
    smiles_list: Iterable[str],
    *,
    formula_filter: str = "",
) -> dict[str, Any]:
    """Classify a collection of species SMILES and group them by topological class."""
    target_formula = str(formula_filter or "").strip()
    records: list[dict[str, Any]] = []
    grouped: dict[str, list[str]] = {}
    for smi in sorted({str(s).strip() for s in smiles_list if str(s).strip()}):
        info = classify_species_topology(smi)
        if target_formula and info["formula"] != target_formula:
            continue
        records.append(info)
        key_parts = [info["core_motif"]]
        if info["oxygen_substitution_patterns"]:
            key_parts.append("+".join(info["oxygen_substitution_patterns"]))
        group_key = " | ".join(key_parts)
        grouped.setdefault(group_key, []).append(smi)
    return {
        "schema_version": TOPOLOGY_CLASSIFIER_SCHEMA,
        "formula_filter": target_formula or None,
        "species_count": len(records),
        "group_counts": {k: len(v) for k, v in sorted(grouped.items())},
        "groups": dict(sorted(grouped.items())),
        "records": records,
    }


__all__ = [
    "TOPOLOGY_CLASSIFIER_SCHEMA",
    "classify_species_collection",
    "classify_species_topology",
]
