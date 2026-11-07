"""Offline, inspectable Lewis comparison against literal RMG library entries.

RMG files are parsed as AST data, never imported or executed. Full inputs and
per-entry results belong outside the repository; commit only a concise report.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time

from rdkit import Chem, rdBase
from reacnet_scope import lewis


_BONDS = {"S": Chem.BondType.SINGLE, "D": Chem.BondType.DOUBLE,
          "T": Chem.BondType.TRIPLE, "B": Chem.BondType.AROMATIC}
_VALENCE = {"H": 1, "B": 3, "C": 4, "N": 5, "O": 6, "F": 7, "Si": 4,
            "P": 5, "S": 6, "Cl": 7, "Br": 7, "I": 7}


def library_entries(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    for statement in tree.body:
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name) and statement.value.func.id == "entry"):
            continue
        keywords = {kw.arg: kw.value for kw in statement.value.keywords}
        # Deliberately do not evaluate ThermoData/NASA calls, imports, assignments
        # or any other executable content in the downloaded library.
        try:
            yield {name: ast.literal_eval(keywords[name]) for name in ("index", "label", "molecule")}
        except (KeyError, ValueError, TypeError):
            yield {"index": None, "label": f"line:{statement.lineno}", "molecule": None}


def adjacency_molecule(text):
    """Decode explicit u/p/c atom data; check both directions of every edge."""
    if not isinstance(text, str):
        raise ValueError("nonliteral_adjacency")
    atoms, edges, multiplicity = {}, {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("multiplicity "):
            multiplicity = int(line.split()[1])
            continue
        parts = line.split()
        if not parts[0].isdigit():
            raise ValueError("unsupported_adjacency_header")
        index = int(parts[0])
        if index in atoms:
            raise ValueError("duplicate_atom_id")
        cursor = 2 if parts[1].startswith("*") else 1
        element = parts[cursor]
        if element not in _VALENCE:
            raise ValueError("unsupported_reference_element:" + element)
        fields = {}
        for part in parts[cursor + 1:]:
            if part[:1] in {"u", "p", "c"}:
                fields[part[0]] = int(part[1:])
            elif not part.startswith("{"):
                raise ValueError("unsupported_atom_field")
        if not {"u", "p", "c"} <= fields.keys():
            raise ValueError("incomplete_electron_fields")
        atoms[index] = (element, fields["u"], fields["p"], fields["c"])
        for other, order in re.findall(r"\{(\d+),([^}]+)\}", line):
            if order not in _BONDS:
                raise ValueError("unsupported_bond_type:" + order)
            edge = index, int(other)
            if edge in edges:
                raise ValueError("duplicate_bond")
            edges[edge] = order
    if not atoms:
        raise ValueError("empty_adjacency")
    for (left, right), order in edges.items():
        if left == right or right not in atoms or edges.get((right, left)) != order:
            raise ValueError("inconsistent_bond_pair")
    molecule = Chem.RWMol()
    positions = {}
    for index, (element, radicals, pairs, charge) in sorted(atoms.items()):
        if radicals < 0 or pairs < 0:
            raise ValueError("negative_electron_count")
        atom = Chem.Atom(element)
        atom.SetNoImplicit(True)
        atom.SetFormalCharge(charge)
        atom.SetNumRadicalElectrons(radicals)
        atom.SetIntProp("rmg_lone_pairs", pairs)
        positions[index] = molecule.AddAtom(atom)
    for (left, right), order in edges.items():
        if left < right:
            molecule.AddBond(positions[left], positions[right], _BONDS[order])
    molecule.UpdatePropertyCache(strict=False)
    with rdBase.BlockLogs():
        try:
            Chem.Kekulize(molecule, clearAromaticFlags=True)
        except (ValueError, RuntimeError) as exc:
            raise ValueError("reference_kekulization_failed") from exc
    # RMG permits omitted H only when u/p/c and bond orders specify them. This
    # calculation uses those reference annotations, not the inferred candidate.
    inferred_hydrogens = 0
    for atom in list(molecule.GetAtoms()):
        bond_sum = sum(b.GetBondTypeAsDouble() for b in atom.GetBonds())
        missing = (_VALENCE[atom.GetSymbol()] - atom.GetFormalCharge()
                   - atom.GetNumRadicalElectrons() - 2 * atom.GetIntProp("rmg_lone_pairs") - bond_sum)
        if missing < 0 or missing != int(missing):
            raise ValueError("reference_electron_bookkeeping_inconsistent")
        for _ in range(int(missing)):
            hydrogen = Chem.Atom("H")
            hydrogen.SetNoImplicit(True)
            hydrogen.SetIntProp("rmg_lone_pairs", 0)
            molecule.AddBond(atom.GetIdx(), molecule.AddAtom(hydrogen), Chem.BondType.SINGLE)
            inferred_hydrogens += 1
    molecule.UpdatePropertyCache(strict=False)
    return molecule.GetMol(), multiplicity, inferred_hydrogens


def comparison_keys(molecule, *, reference=False):
    """Separate normalized bond/charge equality from local u/p equality.

    Neither metric compares spin multiplicity, thermodynamics or real MD truth.
    Aromatic equivalence uses RDKit; other resonance alternatives are not pooled.
    """
    localized = Chem.Mol(molecule)
    Chem.Kekulize(localized, clearAromaticFlags=True)
    colors = []
    for atom in localized.GetAtoms():
        radicals = atom.GetNumRadicalElectrons()
        if reference:
            pairs = atom.GetIntProp("rmg_lone_pairs")
        else:
            remaining = (_VALENCE[atom.GetSymbol()] - atom.GetFormalCharge() - radicals
                         - sum(b.GetBondTypeAsDouble() for b in atom.GetBonds()))
            if remaining < 0 or remaining % 2:
                raise ValueError("candidate_electron_bookkeeping_inconsistent")
            pairs = int(remaining // 2)
        colors.append(1 + 16 * radicals + pairs)
    normalized = Chem.Mol(molecule)
    Chem.RemoveStereochemistry(normalized)
    # Remove u before aromaticity perception as well as serialization, so the
    # bond/charge metric cannot accidentally depend on a radical assignment.
    # The independent colors above still retain u/p for the second metric.
    for atom in normalized.GetAtoms():
        atom.SetNumRadicalElectrons(0)
        atom.SetAtomMapNum(0)
    with rdBase.BlockLogs():
        flag = Chem.SanitizeMol(normalized, sanitizeOps=(Chem.SanitizeFlags.SANITIZE_ALL
                                ^ Chem.SanitizeFlags.SANITIZE_FINDRADICALS), catchErrors=True)
    if flag != Chem.SanitizeFlags.SANITIZE_NONE:
        raise ValueError("reference_rdkit_incompatible" if reference else "candidate_rdkit_incompatible")
    structure = Chem.MolToSmiles(normalized, canonical=True, allHsExplicit=True)
    for atom, color in zip(normalized.GetAtoms(), colors, strict=True):
        atom.SetAtomMapNum(color)
    electrons = Chem.MolToSmiles(normalized, canonical=True, allHsExplicit=True)
    return structure, electrons


def evaluate_entry(entry, backend, options):
    result = {"index": entry["index"], "label": entry["label"]}
    try:
        reference, multiplicity, added_h = adjacency_molecule(entry["molecule"])
        ref_structure, ref_electrons = comparison_keys(reference, reference=True)
    except (ValueError, RuntimeError, IndexError) as exc:
        return {**result, "status": "reference_excluded", "reason": str(exc)}
    input_smiles = Chem.MolToSmiles(reference, canonical=True, allHsExplicit=True)
    annotation = backend.annotate_lewis(input_smiles, options=options)
    result.update(status=annotation["status"], reason=annotation["reason"], input_smiles=input_smiles,
                  candidate_smiles=annotation.get("candidate_smiles"),
                  input_graph_id=annotation.get("input_graph_id"), reference_multiplicity=multiplicity,
                  reference_hydrogens_materialized=added_h,
                  reference_unpaired_electrons=sum(a.GetNumRadicalElectrons() for a in reference.GetAtoms()),
                  spin_comparison="not_evaluated")
    if annotation["status"] != "generated":
        return result
    params = Chem.SmilesParserParams()
    params.removeHs = False
    candidate = Chem.MolFromSmiles(annotation["candidate_smiles"], params)
    try:
        structure, electrons = comparison_keys(candidate)
    except (ValueError, RuntimeError) as exc:
        return {**result, "comparison_error": str(exc)}
    result.update(structure_match=structure == ref_structure, local_electrons_match=electrons == ref_electrons,
                  positive_halogen=any(a.GetFormalCharge() > 0 and a.GetSymbol() in {"F", "Cl", "Br", "I"}
                                       for a in candidate.GetAtoms()))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("libraries", nargs="+", type=Path)
    parser.add_argument("--revision", required=True, help="Pinned RMG-database commit")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--implementation-file", type=Path, help="Trusted Scope source for a baseline, never an RMG library")
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--solve-seconds", type=float, default=5)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision) or not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        parser.error("Provide a full pinned commit and a positive benchmark budget")
    if args.output_dir.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("Keep full benchmark results outside the source repository")
    backend = lewis
    if args.implementation_file:
        spec = importlib.util.spec_from_file_location("lewis_benchmark_baseline", args.implementation_file)
        backend = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = backend
        spec.loader.exec_module(backend)
    options = backend.LewisOptions(assume_neutral=True, max_seconds=args.solve_seconds)
    deadline = time.monotonic() + args.max_seconds
    manifest, results = [], []
    for library in args.libraries:
        data = library.read_bytes()
        manifest.append({"name": library.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                         "url": f"https://github.com/ReactionMechanismGenerator/RMG-database/blob/{args.revision}/input/thermo/libraries/{library.name}"})
        for entry in library_entries(library):
            if time.monotonic() >= deadline:
                row = {"index": entry["index"], "label": entry["label"], "status": "not_evaluated", "reason": "benchmark_budget"}
            else:
                row = evaluate_entry(entry, backend, options)
            results.append({"library": library.name, **row})
        print(library.name, "processed", flush=True)
    summaries = {}
    for library in manifest:
        rows = [row for row in results if row["library"] == library["name"]]
        counts = Counter(row["status"] for row in rows)
        summaries[library["name"]] = {"entries": len(rows), "status_counts": dict(counts),
            "exclusion_reasons": dict(Counter(row["reason"] for row in rows if row["status"] == "reference_excluded")),
            "structure_matches": sum(bool(row.get("structure_match")) for row in rows),
            "local_electrons_matches": sum(bool(row.get("local_electrons_match")) for row in rows),
            "comparison_errors": sum(bool(row.get("comparison_error")) for row in rows),
            "positive_halogen_candidates": sum(bool(row.get("positive_halogen")) for row in rows),
            "unique_input_graphs": len({row["input_graph_id"] for row in rows if row.get("input_graph_id")})}
    summary = {"reference_revision": args.revision, "sources": manifest, "method": backend.method_metadata(),
               "implementation_sha256": hashlib.sha256(Path(backend.__file__).read_bytes()).hexdigest(),
               "complete": not any(row["status"] == "not_evaluated" for row in results), "libraries": summaries,
               "denominators": "all declared entries; generated-only rates must separately report coverage",
               "spin_comparison": "not_evaluated", "real_trajectory_validation": "not_evaluated"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (args.output_dir / "entries.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
