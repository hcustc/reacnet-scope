"""The reference benchmark must not turn representation equality into spin agreement."""
import pytest
from rdkit import Chem

from reacnet_scope.lewis import LewisOptions, annotate_lewis, lewis_model_molecule

from scripts.benchmark_lewis_rmg import adjacency_molecule, comparison_keys, library_entries, reference_strata, representation_relation


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
    annotation = annotate_lewis("F[C]F", options=LewisOptions(assume_neutral=True))
    model_structure, model_electrons = comparison_keys(lewis_model_molecule(annotation))
    assert model_structure == structure
    assert model_electrons == electrons
    assert annotation["unique_electronic_state"] == "not_established"


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


def test_small_fragment_strata_use_reference_features_not_success():
    base = {"status": "generated", "elements": ["C", "H", "O"], "heavy_atoms": 3,
            "reference_unpaired_electrons": 0, "reference_carbon_lone_pairs": 0}
    rows = [dict(base, structure_match=True),
            dict(base, reference_carbon_lone_pairs=1, structure_match=False),
            dict(base, reference_unpaired_electrons=1, structure_match=False),
            dict(base, heavy_atoms=5), dict(base, elements=["C", "F"])]
    strata = reference_strata(rows)
    assert strata["small_CNO"]["entries"] == 3
    assert strata["small_CNO"]["structure_matches"] == 1
    assert strata["small_CNO_unpaired_or_carbon_lone_pair"]["entries"] == 2
    assert strata["small_CNO_carbon_lone_pair"]["entries"] == 1


def test_nitrous_oxide_resonance_is_not_counted_as_exact_agreement_or_error():
    reference = Chem.MolFromSmiles("[N-]=[N+]=O")
    candidate = Chem.MolFromSmiles("N#[N+][O-]")
    ref_key = comparison_keys(reference)[0]
    candidate_key = comparison_keys(candidate)[0]
    assert ref_key != candidate_key
    assert representation_relation(reference, candidate, ref_key, candidate_key) == "accepted_n2o_resonance"
    unrelated = Chem.MolFromSmiles("[C-]#[O+]")
    assert representation_relation(reference, unrelated, ref_key, comparison_keys(unrelated)[0]) == "unclassified_difference"
