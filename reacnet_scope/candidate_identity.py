"""Versioned structural identities, independent of evidence and query ranking.

Species strings are the exact identities supplied by RNG. No chemistry toolkit
re-canonicalizes them here. Canonicalization only orders each reaction side;
direction, duplicate participants and spectator species are preserved.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


SPECIES_IDENTITY_VERSION = "rng-exact-species/v1"
REACTION_IDENTITY_VERSION = "directed-reaction/v1"
CANDIDATE_SIGNATURE_VERSION = "candidate/v1"
CANDIDATE_IDENTITY_SCHEMA_VERSION = "reacnet-scope/candidate-identity/v1"
CANDIDATE_EVIDENCE_VERSION = "candidate-evidence/v1"


@dataclass(frozen=True, order=True)
class SpeciesKey:
    smiles: str

    def __post_init__(self) -> None:
        if not isinstance(self.smiles, str):
            raise ValueError("Species must be an exact RNG string")
        value = self.smiles.strip()
        if not value or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("Species must be a nonempty exact RNG token")
        object.__setattr__(self, "smiles", value)


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
            raise ValueError("reaction sides must be sequences of Species, not equations")
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
    """One ordinary path: anchor, then one carried output per reaction step."""

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
        """Export identity, optionally bound to a dataset-scoped published revision.

        The caller supplies the publisher's revision identity, not a bare local
        revision counter. This function neither publishes nor verifies evidence.
        Omit the revision when only structural identity is available.
        """
        result = {
            "schema_version": CANDIDATE_IDENTITY_SCHEMA_VERSION,
            **self._payload(),
            "candidate_signature": self.signature,
        }
        if dataset_revision is not None:
            if not isinstance(dataset_revision, str) or not dataset_revision.strip():
                raise ValueError("dataset_revision must identify a published dataset revision")
            payload = json.dumps(
                [CANDIDATE_EVIDENCE_VERSION, dataset_revision, self.signature],
                ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            result.update(
                dataset_revision=dataset_revision,
                evidence_identity_semantic_version=CANDIDATE_EVIDENCE_VERSION,
                candidate_evidence_key=f"candidate-evidence:v1:{digest}",
            )
        return result
