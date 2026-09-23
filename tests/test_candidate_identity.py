from itertools import permutations

import pytest

from reacnet_scope import (
    CandidateIdentity,
    DirectedReactionKey,
    SpeciesKey,
    candidate_identity_from_route,
)


def test_reaction_identity_preserves_exact_species_direction_and_stoichiometry():
    expected = DirectedReactionKey.from_sides(["[H]", "[H]", "[O]"], ["[H][O][H]"])
    for terms in permutations(["[O]", "[H]", "[H]"]):
        assert DirectedReactionKey.from_sides(terms, ["[H][O][H]"]) == expected
    assert expected.reactants == (SpeciesKey("[H]"), SpeciesKey("[H]"), SpeciesKey("[O]"))
    assert DirectedReactionKey.from_sides(["[H]", "[O]"], ["[H][O][H]"]) != expected
    assert DirectedReactionKey.from_sides(["[H][O][H]"], ["[H]", "[H]", "[O]"]) != expected
    assert SpeciesKey("CCO") != SpeciesKey("COC")
    charged = DirectedReactionKey.from_sides(["[NH4+]", "[OH-]"], ["N", "O"])
    assert charged.reactants == (SpeciesKey("[NH4+]"), SpeciesKey("[OH-]"))
    assert DirectedReactionKey.from_sides(["A", "X"], ["B", "X"]) != (
        DirectedReactionKey.from_sides(["A"], ["B"])
    )


def test_candidate_signature_has_frozen_utf8_canonical_json_and_explicit_branch():
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


@pytest.mark.parametrize("species", ["", "  ", " CCO", "CCO ", "C O", "C\x1fO", None, 5])
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
    with pytest.raises(ValueError):
        CandidateIdentity(
            SpeciesKey("A"), tuple(SpeciesKey(value) for value in chain),
            tuple(DirectedReactionKey.from_sides(*sides) for sides in reactions),
        )


def test_evidence_binding_changes_with_revision_without_changing_structure():
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
    assert "candidate_evidence_key" not in identity.as_dict()
    for revision in ("", "  ", " dataset-1:revision-1", 1):
        with pytest.raises(ValueError):
            identity.as_dict(dataset_revision=revision)


def test_route_adapter_accepts_explicit_modern_or_legacy_chain_without_guessing():
    modern = {
        "species": ["[NH4+]", "N"],
        "steps": [{"carried_from": "[NH4+]", "carried_to": "N",
                   "reaction_key": "[NH4+]+[OH-]->N+O",
                   "reactants": ["[OH-]", "[NH4+]"], "products": ["O", "N"]}],
        "signature_id": "legacy", "rank": 1, "source_revision": {"changed": False},
    }
    identity = candidate_identity_from_route(modern)
    assert identity == candidate_identity_from_route({
        "species": modern["species"], "reaction_keys": [modern["steps"][0]["reaction_key"]],
    })
    assert identity.signature == candidate_identity_from_route({**modern, "rank": 99}).signature
    assert "candidate_evidence_key" not in identity.as_dict()


@pytest.mark.parametrize("route", [
    {"signature_id": "legacy"},
    {"species": ["A", "B"], "reaction_keys": ["A++B->B"]},
    {"species": ["A", "B"], "steps": [{"reactants": ["A"], "products": ["B"]}]},
    {"species": ["A", "B"], "steps": [{"carried_from": "A", "carried_to": "B",
                                             "reactants": ["A"], "products": ["C"]}]},
    {"species": ["A", "B"], "steps": [{"carried_from": "A", "carried_to": "B",
                                             "reaction_key": "A->C", "reactants": ["A"], "products": ["B"]}]},
])
def test_route_adapter_rejects_missing_or_conflicting_identity(route):
    with pytest.raises(ValueError):
        candidate_identity_from_route(route)
