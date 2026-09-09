from itertools import permutations

import pytest

from reacnet_scope.candidate_identity import DirectedReactionKey, SpeciesKey


def test_reaction_identity_preserves_exact_species_direction_and_stoichiometry():
    expected = DirectedReactionKey.from_sides(["[H]", "[H]", "[O]"], ["[H][O][H]"])
    for terms in permutations(["[O]", "[H]", "[H]"]):
        assert DirectedReactionKey.from_sides(terms, ["[H][O][H]"]) == expected
    assert expected.reactants == (SpeciesKey("[H]"), SpeciesKey("[H]"), SpeciesKey("[O]"))
    assert DirectedReactionKey.from_sides(["[H]", "[O]"], ["[H][O][H]"]) != expected
    assert DirectedReactionKey.from_sides(["[H][O][H]"], ["[H]", "[H]", "[O]"]) != expected
    assert SpeciesKey("CCO") != SpeciesKey("COC")
    assert SpeciesKey(" CCO ") == SpeciesKey("CCO")
    charged = DirectedReactionKey.from_sides(["[NH4+]", "[OH-]"], ["N", "O"])
    assert charged.reactants == (SpeciesKey("[NH4+]"), SpeciesKey("[OH-]"))


def test_candidate_signature_uses_explicit_carried_chain_and_versioned_serialization():
    from reacnet_scope.candidate_identity import CandidateIdentity

    identity = CandidateIdentity(
        SpeciesKey("[C]"),
        (SpeciesKey("[C][O]"), SpeciesKey("[C]=O")),
        (
            DirectedReactionKey.from_sides(["[O]", "[C]"], ["[C][O]"]),
            DirectedReactionKey.from_sides(["[C][O]"], ["[C]=O"]),
        ),
    )
    assert identity.canonical_json() == (
        '{"anchor_species":"[C]","carried_species":["[C][O]","[C]=O"],'
        '"reaction_identity_semantic_version":"directed-reaction/v1",'
        '"reaction_types":[{"products":["[C][O]"],"reactants":["[C]","[O]"]},'
        '{"products":["[C]=O"],"reactants":["[C][O]"]}],'
        '"signature_semantic_version":"candidate/v1",'
        '"species_identity_semantic_version":"rng-exact-species/v1"}'
    )
    assert identity.signature == (
        "candidate:v1:0cb658ed6dba5ca51525654f0ed7939e4b48348b90971340ae2022af4ead79e6"
    )
    reactions = (
        DirectedReactionKey.from_sides(["A"], ["B", "C"]),
        DirectedReactionKey.from_sides(["B", "C"], ["D"]),
    )
    via_b = CandidateIdentity(SpeciesKey("A"), (SpeciesKey("B"), SpeciesKey("D")), reactions)
    via_c = CandidateIdentity(SpeciesKey("A"), (SpeciesKey("C"), SpeciesKey("D")), reactions)
    assert via_b.signature != via_c.signature


@pytest.mark.parametrize("species", ["", "  ", "C O", "C\x1fO", None, 5])
def test_invalid_species_never_produce_an_identity(species):
    with pytest.raises(ValueError):
        SpeciesKey(species)


@pytest.mark.parametrize(
    "chain, reactions",
    [
        ((), ()),
        (("B", "C"), ((["A"], ["B"]),)),
        (("B",), ((["C"], ["B"]),)),
        (("C",), ((["A"], ["B"]),)),
        (("B", "C"), ((["A"], ["B"]), (["D"], ["C"]))),
        (("B", "A"), ((["A"], ["B"]), (["B"], ["A"]))),
    ],
)
def test_invalid_or_cyclic_carried_chain_is_rejected(chain, reactions):
    from reacnet_scope.candidate_identity import CandidateIdentity

    with pytest.raises(ValueError):
        CandidateIdentity(
            SpeciesKey("A"), tuple(SpeciesKey(value) for value in chain),
            tuple(DirectedReactionKey.from_sides(*sides) for sides in reactions),
        )


def test_evidence_binding_changes_with_revision_without_changing_structure():
    from reacnet_scope.candidate_identity import CandidateIdentity

    identity = CandidateIdentity(
        SpeciesKey("A"), (SpeciesKey("B"),),
        (DirectedReactionKey.from_sides(["A"], ["B"]),),
    )
    first = identity.as_dict(dataset_revision="dataset-1:revision-1")
    second = identity.as_dict(dataset_revision="dataset-1:revision-2")
    other_dataset = identity.as_dict(dataset_revision="dataset-2:revision-1")
    assert first["candidate_signature"] == second["candidate_signature"] == other_dataset["candidate_signature"]
    assert len({first["candidate_evidence_key"], second["candidate_evidence_key"], other_dataset["candidate_evidence_key"]}) == 3
    assert first["schema_version"] == "reacnet-scope/candidate-identity/v1"
    assert first["dataset_revision"] == "dataset-1:revision-1"
    assert "candidate_evidence_key" not in identity.as_dict()
    assert "signature_id" not in first
    for bad_revision in ("", "  ", 1):
        with pytest.raises(ValueError):
            identity.as_dict(dataset_revision=bad_revision)
