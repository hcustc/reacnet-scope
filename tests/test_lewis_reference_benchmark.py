"""The reference benchmark must not turn representation equality into spin agreement."""
import pytest
from rdkit import Chem

from scripts.benchmark_lewis_rmg import adjacency_molecule, comparison_keys, library_entries


def test_carbene_bond_graph_agreement_does_not_count_as_local_electron_agreement():
    reference, spin, added = adjacency_molecule("""
multiplicity 1
1 C u0 p1 c0 {2,S} {3,S}
2 F u0 p3 c0 {1,S}
3 F u0 p3 c0 {1,S}
""")
    structure, electrons = comparison_keys(reference, reference=True)
    candidate_structure, candidate_electrons = comparison_keys(Chem.MolFromSmiles("F[C]F"))
    assert structure == candidate_structure
    assert electrons != candidate_electrons
    assert spin == 1 and added == 0


def test_reference_hydrogen_inference_uses_declared_electron_bookkeeping():
    molecule, _, added = adjacency_molecule("1 C u1 p0 c0")
    assert added == 3
    assert molecule.GetNumAtoms() == 4
    assert sum(a.GetNumRadicalElectrons() for a in molecule.GetAtoms()) == 1


def test_reference_library_is_parsed_without_executing_python(tmp_path):
    path = tmp_path / "reference.py"
    path.write_text('raise AssertionError("must never execute")\n'
                    'entry(index=1,label="atom",molecule="1 H u1 p0 c0",thermo=UnknownFunction())\n')
    assert list(library_entries(path)) == [{"index": 1, "label": "atom", "molecule": "1 H u1 p0 c0"}]


def test_reference_bond_asymmetry_is_reported_instead_of_silently_repaired():
    with pytest.raises(ValueError, match="inconsistent_bond_pair"):
        adjacency_molecule("1 H u0 p0 c0 {2,S}\n2 H u0 p0 c0")
