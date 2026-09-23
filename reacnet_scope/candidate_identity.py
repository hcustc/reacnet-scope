"""Versioned Candidate structure identities, separate from evidence and rank."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


SPECIES_IDENTITY_VERSION = "rng-exact-species/v1"
REACTION_IDENTITY_VERSION = "directed-reaction/v1"
CANDIDATE_SIGNATURE_VERSION = "candidate/v1"
CANDIDATE_IDENTITY_SCHEMA_VERSION = "reacnet-scope/candidate-identity/v1"
CANDIDATE_EVIDENCE_VERSION = "candidate-evidence/v1"


@dataclass(frozen=True, order=True)
class SpeciesKey:
    smiles: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.smiles, str)
            or not self.smiles
            or any(character.isspace() or ord(character) < 32 for character in self.smiles)
        ):
            raise ValueError("Species must be a nonempty exact RNG token")


@dataclass(frozen=True)
class DirectedReactionKey:
    reactants: tuple[SpeciesKey, ...]
    products: tuple[SpeciesKey, ...]

    def __post_init__(self) -> None:
        for side in ("reactants", "products"):
            values = tuple(getattr(self, side))
            if not values or any(not isinstance(value, SpeciesKey) for value in values):
                raise ValueError("each reaction side requires exact SpeciesKeys")
            object.__setattr__(self, side, tuple(sorted(values)))

    @classmethod
    def from_sides(cls, reactants: Iterable[str], products: Iterable[str]) -> "DirectedReactionKey":
        if isinstance(reactants, str) or isinstance(products, str):
            raise ValueError("reaction sides must be sequences of Species")
        return cls(
            tuple(SpeciesKey(value) for value in reactants),
            tuple(SpeciesKey(value) for value in products),
        )

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "reactants": [value.smiles for value in self.reactants],
            "products": [value.smiles for value in self.products],
        }


@dataclass(frozen=True)
class CandidateIdentity:
    anchor: SpeciesKey
    carried: tuple[SpeciesKey, ...]
    reactions: tuple[DirectedReactionKey, ...]

    def __post_init__(self) -> None:
        carried = tuple(self.carried)
        reactions = tuple(self.reactions)
        if not isinstance(self.anchor, SpeciesKey) or any(
            not isinstance(value, SpeciesKey) for value in carried
        ):
            raise ValueError("anchor and carried species require exact SpeciesKeys")
        if not reactions or len(carried) != len(reactions) or any(
            not isinstance(value, DirectedReactionKey) for value in reactions
        ):
            raise ValueError("one explicit carried Species is required per reaction step")
        visited = {self.anchor}
        previous = self.anchor
        for output, reaction in zip(carried, reactions):
            if previous not in reaction.reactants or output not in reaction.products:
                raise ValueError("carried Species must join the directed reaction sides")
            if output in visited:
                raise ValueError("ordinary Candidates cannot revisit a carried Species")
            visited.add(output)
            previous = output
        object.__setattr__(self, "carried", carried)
        object.__setattr__(self, "reactions", reactions)

    def _payload(self) -> dict[str, Any]:
        return {
            "signature_semantic_version": CANDIDATE_SIGNATURE_VERSION,
            "species_identity_semantic_version": SPECIES_IDENTITY_VERSION,
            "reaction_identity_semantic_version": REACTION_IDENTITY_VERSION,
            "anchor_species": self.anchor.smiles,
            "carried_species": [value.smiles for value in self.carried],
            "reaction_types": [value.as_dict() for value in self.reactions],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self._payload(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )

    @property
    def signature(self) -> str:
        digest = hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
        return f"candidate:v1:{digest}"

    def as_dict(self, *, dataset_revision: str | None = None) -> dict[str, Any]:
        result = {
            "schema_version": CANDIDATE_IDENTITY_SCHEMA_VERSION,
            **self._payload(),
            "candidate_signature": self.signature,
        }
        if dataset_revision is not None:
            if (
                not isinstance(dataset_revision, str)
                or not dataset_revision
                or dataset_revision != dataset_revision.strip()
            ):
                raise ValueError("dataset_revision must identify a published dataset revision")
            payload = json.dumps(
                [CANDIDATE_EVIDENCE_VERSION, dataset_revision, self.signature],
                ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
            result.update(
                dataset_revision=dataset_revision,
                evidence_identity_semantic_version=CANDIDATE_EVIDENCE_VERSION,
                candidate_evidence_key=(
                    "candidate-evidence:v1:"
                    + hashlib.sha256(payload.encode("utf-8")).hexdigest()
                ),
            )
        return result


def _reaction_from_text(key: str) -> DirectedReactionKey:
    if not isinstance(key, str) or key.count("->") != 1:
        raise ValueError("route requires directed RNG Reaction Type keys")
    sides: list[list[str]] = []
    for side in key.split("->"):
        terms: list[str] = []
        current: list[str] = []
        bracket_depth = 0
        for character in side:
            if character == "[":
                bracket_depth += 1
            elif character == "]":
                bracket_depth -= 1
                if bracket_depth < 0:
                    raise ValueError("reaction key contains unmatched brackets")
            if character == "+" and bracket_depth == 0:
                if not current:
                    raise ValueError("reaction key contains an empty participant")
                terms.append("".join(current))
                current = []
            else:
                current.append(character)
        if bracket_depth or not current:
            raise ValueError("reaction key has an incomplete participant")
        terms.append("".join(current))
        sides.append(terms)
    return DirectedReactionKey.from_sides(*sides)


def candidate_identity_from_route(route: Mapping[str, Any]) -> CandidateIdentity:
    """Adapt a route only when every carried branch is explicitly declared."""
    species = route.get("species")
    if not isinstance(species, (list, tuple)) or len(species) < 2:
        raise ValueError("route requires an anchor and explicit carried Species")
    steps = route.get("steps")
    reactions: list[DirectedReactionKey] = []
    if steps is not None:
        if not isinstance(steps, (list, tuple)) or len(steps) != len(species) - 1:
            raise ValueError("route requires one complete step per carried Species")
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping) or (
                step.get("carried_from") != species[index]
                or step.get("carried_to") != species[index + 1]
            ):
                raise ValueError("step carried chain does not match route Species")
            reactants, products = step.get("reactants"), step.get("products")
            if not isinstance(reactants, (list, tuple)) or not isinstance(products, (list, tuple)):
                raise ValueError("step requires complete directed Reaction Type sides")
            reaction = DirectedReactionKey.from_sides(reactants, products)
            if step.get("reaction_key") is not None and _reaction_from_text(step["reaction_key"]) != reaction:
                raise ValueError("step Reaction Type disagrees with its RNG key")
            reactions.append(reaction)
    else:
        keys = route.get("reaction_keys")
        if not isinstance(keys, (list, tuple)) or len(keys) != len(species) - 1:
            raise ValueError("route requires one complete Reaction Type per carried step")
        reactions = [_reaction_from_text(key) for key in keys]
    return CandidateIdentity(
        SpeciesKey(species[0]),
        tuple(SpeciesKey(value) for value in species[1:]),
        tuple(reactions),
    )
