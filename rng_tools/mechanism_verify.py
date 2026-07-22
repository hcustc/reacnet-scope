"""Literature mechanism evidence matrix builder.

Matches user-specified literature reactions against simulation data
and grades the evidence for each reaction with a standardised
confidence scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from rng_tools.formula import check_mass_balance, parse_formula
from rng_tools.network import ReactionNetwork, smiles_to_formula_fast


# ---------------------------------------------------------------------------
# Evidence level enum
# ---------------------------------------------------------------------------


class EvidenceLevel(Enum):
    """Standardised evidence grades for literature reactions."""

    NOT_DETECTED = "not_detected"
    DETECTED_SPECIES_ONLY = "detected_species_only"
    HAS_NET_FLUX = "has_net_flux"
    STRUCTURE_CONFIRMED = "structure_confirmed"
    ONLY_TRANSIENT = "only_transient"
    MULTIPLE_CONFIRMED = "multiple_confirmed"

    @property
    def label(self) -> str:
        _labels = {
            EvidenceLevel.NOT_DETECTED: "未检测到",
            EvidenceLevel.DETECTED_SPECIES_ONLY: "仅物种匹配",
            EvidenceLevel.HAS_NET_FLUX: "存在净通量",
            EvidenceLevel.STRUCTURE_CONFIRMED: "结构已确认",
            EvidenceLevel.ONLY_TRANSIENT: "仅瞬时出现",
            EvidenceLevel.MULTIPLE_CONFIRMED: "多次原子确认",
        }
        return _labels.get(self, self.value)

    @property
    def css_class(self) -> str:
        _classes = {
            EvidenceLevel.NOT_DETECTED: "rs-evidence-not-detected",
            EvidenceLevel.DETECTED_SPECIES_ONLY: "rs-evidence-detected",
            EvidenceLevel.HAS_NET_FLUX: "rs-evidence-net-flux",
            EvidenceLevel.STRUCTURE_CONFIRMED: "rs-evidence-confirmed",
            EvidenceLevel.ONLY_TRANSIENT: "rs-evidence-transient",
            EvidenceLevel.MULTIPLE_CONFIRMED: "rs-evidence-confirmed",
        }
        return _classes.get(self, "")

    @property
    def score(self) -> int:
        """Numeric score for sorting (higher = stronger evidence)."""
        _scores = {
            EvidenceLevel.NOT_DETECTED: 0,
            EvidenceLevel.ONLY_TRANSIENT: 1,
            EvidenceLevel.DETECTED_SPECIES_ONLY: 2,
            EvidenceLevel.HAS_NET_FLUX: 3,
            EvidenceLevel.STRUCTURE_CONFIRMED: 4,
            EvidenceLevel.MULTIPLE_CONFIRMED: 5,
        }
        return _scores.get(self, 0)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MechanismReaction:
    """A single literature reaction to verify."""

    index: int
    reaction_text: str             # original user input
    reactant_smiles: List[str] = field(default_factory=list)
    product_smiles: List[str] = field(default_factory=list)
    reactant_formulas: List[str] = field(default_factory=list)
    product_formulas: List[str] = field(default_factory=list)


@dataclass
class MechanismEvidence:
    """Evidence collected for one literature reaction."""

    reaction: MechanismReaction
    evidence_level: EvidenceLevel = EvidenceLevel.NOT_DETECTED
    detected: bool = False
    forward_tp: int = 0
    reverse_tp: int = 0
    net_tp: int = 0
    is_transient: bool = False
    net_event_count: int = 0
    dedup_event_count: int = 0
    atom_confirmed_count: int = 0
    avg_lifetime: float = 0.0
    matched_reaction_keys: List[str] = field(default_factory=list)
    notes: str = ""

    @property
    def evidence_label(self) -> str:
        return self.evidence_level.label


@dataclass
class EvidenceMatrixResult:
    """Complete evidence matrix for a set of literature reactions."""

    reactions: List[MechanismReaction] = field(default_factory=list)
    evidence_rows: List[MechanismEvidence] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.reactions)

    @property
    def detected_count(self) -> int:
        return sum(1 for e in self.evidence_rows if e.detected)

    @property
    def net_flux_count(self) -> int:
        return sum(
            1 for e in self.evidence_rows
            if e.evidence_level in (
                EvidenceLevel.HAS_NET_FLUX,
                EvidenceLevel.STRUCTURE_CONFIRMED,
                EvidenceLevel.MULTIPLE_CONFIRMED,
            )
        )

    @property
    def structure_confirmed_count(self) -> int:
        return sum(
            1 for e in self.evidence_rows
            if e.evidence_level in (
                EvidenceLevel.STRUCTURE_CONFIRMED,
                EvidenceLevel.MULTIPLE_CONFIRMED,
            )
        )

    @property
    def not_detected_count(self) -> int:
        return sum(1 for e in self.evidence_rows if not e.detected)


# ---------------------------------------------------------------------------
# Reaction text parser
# ---------------------------------------------------------------------------


def parse_literature_reaction_text(text: str) -> List[str]:
    """Split multi-line literature reaction input into individual lines.

    Handles newlines, semicolons, and blank lines.
    """
    raw_lines: List[str] = []
    for line in text.replace(";", "\n").split("\n"):
        stripped = line.strip()
        if stripped:
            raw_lines.append(stripped)
    return raw_lines


def _parse_single_reaction(reaction_str: str) -> Tuple[List[str], List[str]]:
    """Parse a reaction string like ``"A + B -> C + D"`` into
    (reactants, products) lists.
    """
    if "->" not in reaction_str and "→" not in reaction_str:
        raise ValueError(f"Cannot parse reaction (missing '->'): {reaction_str}")

    arrow = "->" if "->" in reaction_str else "→"
    left, right = reaction_str.split(arrow, 1)

    def _parse_side(side: str) -> List[str]:
        tokens: List[str] = []
        for token in side.split("+"):
            token = token.strip()
            if not token:
                continue
            # Handle stoichiometric coefficient like "2CP"
            coeff = 1
            rest = token
            if token and token[0].isdigit():
                i = 0
                while i < len(token) and token[i].isdigit():
                    i += 1
                coeff = int(token[:i])
                rest = token[i:].strip()
            tokens.extend([rest] * coeff)
        return tokens

    return _parse_side(left), _parse_side(right)


def _try_resolve_species_name(
    name: str,
    network: ReactionNetwork,
    aliases: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Try to resolve a named species (e.g. "CP", "R2") to a SMILES.

    1. Look up in aliases dict.
    2. If `name` looks like a SMILES (contains brackets), return as-is.
    3. Try formula look-up in the network.
    4. Return None if unresolvable.
    """
    if aliases and name in aliases:
        resolved = aliases[aliases[name]]
        if resolved:
            return resolved

    # If already looks like a SMILES
    if "[" in name or "(" in name or "=" in name:
        return name

    # Try as a formula
    try:
        parsed = parse_formula(name)
        if parsed:
            # Formula detected — look up known SMILES
            formula_str = name
            if formula_str in network._formula_to_smiles:
                candidates = network._formula_to_smiles[formula_str]
                # Return the one with highest throughput
                best = max(
                    candidates,
                    key=lambda s: network.species.get(s, type('', (), {'total_throughput': 0})()).total_throughput
                    if s in network.species else 0,
                )
                return best
            # Otherwise use formula for matching
            return name
    except (ValueError, Exception):
        pass

    return None


# ---------------------------------------------------------------------------
# MechanismVerifier
# ---------------------------------------------------------------------------


class MechanismVerifier:
    """Match literature reactions against simulation data and grade evidence.

    Parameters
    ----------
    network:
        A :class:`~rng_tools.network.ReactionNetwork` loaded from the
        simulation's ``.reactionabcd`` file.
    species_aliases:
        Optional mapping of human-readable species names to SMILES
        (e.g. ``{"CP": "[C]1...", "R2": "[C]1..."}``).
    """

    def __init__(
        self,
        network: ReactionNetwork,
        species_aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        self.network = network
        self.aliases = species_aliases or {}

    def parse_literature_reactions(
        self,
        reaction_texts: List[str],
    ) -> List[MechanismReaction]:
        """Parse a list of reaction strings into :class:`MechanismReaction`
        objects, resolving named species against the network where possible.
        """
        reactions: List[MechanismReaction] = []
        for idx, text in enumerate(reaction_texts):
            try:
                reactants, products = _parse_single_reaction(text)
            except ValueError:
                continue

            # Resolve names to SMILES/formulas
            resolved_reactants: List[str] = []
            resolved_products: List[str] = []
            reactant_formulas: List[str] = []
            product_formulas: List[str] = []

            for r in reactants:
                resolved = _try_resolve_species_name(r, self.network, self.aliases)
                resolved_reactants.append(resolved or r)

            for p in products:
                resolved = _try_resolve_species_name(p, self.network, self.aliases)
                resolved_products.append(resolved or p)

            # Compute formulas for resolved species
            for r in resolved_reactants:
                try:
                    reactant_formulas.append(smiles_to_formula_fast(r))
                except Exception:
                    reactant_formulas.append(r)

            for p in resolved_products:
                try:
                    product_formulas.append(smiles_to_formula_fast(p))
                except Exception:
                    product_formulas.append(p)

            reactions.append(
                MechanismReaction(
                    index=idx + 1,
                    reaction_text=text,
                    reactant_smiles=resolved_reactants,
                    product_smiles=resolved_products,
                    reactant_formulas=reactant_formulas,
                    product_formulas=product_formulas,
                )
            )
        return reactions

    def verify_reaction(
        self,
        mechanism_rxn: MechanismReaction,
    ) -> MechanismEvidence:
        """Check one literature reaction against the loaded
        :class:`ReactionNetwork`.

        Returns a :class:`MechanismEvidence` with the evidence level
        and supporting statistics.
        """
        evidence = MechanismEvidence(reaction=mechanism_rxn)

        # Build formula-level reaction key for lookup
        reactant_f_set = frozenset(mechanism_rxn.reactant_formulas)
        product_f_set = frozenset(mechanism_rxn.product_formulas)

        # Try to find matching reactions in the network
        matched_keys: List[str] = []
        total_forward_tp = 0
        total_reverse_tp = 0

        for reaction in self.network.reactions:
            r_f = frozenset(reaction.reactant_formulas)
            p_f = frozenset(reaction.product_formulas)

            # Forward match: reactant→product matches literature
            if r_f == reactant_f_set and p_f == product_f_set:
                matched_keys.append(reaction.key)
                total_forward_tp += reaction.tp

            # Reverse match: product→reactant matches literature
            elif p_f == reactant_f_set and r_f == product_f_set:
                matched_keys.append(reaction.key)
                total_reverse_tp += reaction.tp

        evidence.matched_reaction_keys = matched_keys
        evidence.forward_tp = total_forward_tp
        evidence.reverse_tp = total_reverse_tp
        evidence.net_tp = total_forward_tp - total_reverse_tp

        # Grade evidence
        if not matched_keys:
            evidence.evidence_level = EvidenceLevel.NOT_DETECTED
            evidence.detected = False
            evidence.notes = "在 reactionabcd 中未找到匹配反应"
        elif evidence.net_tp > 0:
            evidence.detected = True
            evidence.evidence_level = EvidenceLevel.HAS_NET_FLUX
            evidence.notes = "检测到净正向通量"
        elif evidence.net_tp < 0:
            evidence.detected = True
            evidence.evidence_level = EvidenceLevel.HAS_NET_FLUX
            evidence.notes = "净通量为反向（逆反应占优）"
        elif total_forward_tp == total_reverse_tp and total_forward_tp > 0:
            evidence.detected = True
            evidence.evidence_level = EvidenceLevel.DETECTED_SPECIES_ONLY
            evidence.notes = "正逆反应次数相等，可能为可逆过程或瞬时振荡"
        else:
            evidence.detected = True
            evidence.evidence_level = EvidenceLevel.DETECTED_SPECIES_ONLY
            evidence.notes = "在物种级别检测到反应"

        return evidence

    def build_matrix(
        self,
        mechanism_reactions: List[MechanismReaction],
    ) -> EvidenceMatrixResult:
        """Verify all literature reactions and build the evidence matrix."""
        evidence_rows = [
            self.verify_reaction(rxn) for rxn in mechanism_reactions
        ]

        meta = {
            "total": len(evidence_rows),
            "detected": sum(1 for e in evidence_rows if e.detected),
            "has_net_flux": sum(
                1 for e in evidence_rows
                if e.evidence_level in (
                    EvidenceLevel.HAS_NET_FLUX,
                    EvidenceLevel.STRUCTURE_CONFIRMED,
                    EvidenceLevel.MULTIPLE_CONFIRMED,
                )
            ),
            "not_detected": sum(1 for e in evidence_rows if not e.detected),
            "detection_rate": round(
                sum(1 for e in evidence_rows if e.detected) / max(len(evidence_rows), 1), 3
            ),
        }

        return EvidenceMatrixResult(
            reactions=mechanism_reactions,
            evidence_rows=evidence_rows,
            meta=meta,
        )

    def matrix_to_rows(
        self,
        matrix: EvidenceMatrixResult,
    ) -> List[Dict[str, Any]]:
        """Convert evidence matrix to flat dict rows for DataTable display."""
        rows: List[Dict[str, Any]] = []
        for evidence in matrix.evidence_rows:
            rows.append(
                {
                    "index": evidence.reaction.index,
                    "reaction_text": evidence.reaction.reaction_text,
                    "evidence_label": evidence.evidence_label,
                    "evidence_level": evidence.evidence_level.value,
                    "evidence_css": evidence.evidence_level.css_class,
                    "detected": "Yes" if evidence.detected else "No",
                    "forward_tp": evidence.forward_tp,
                    "reverse_tp": evidence.reverse_tp,
                    "net_tp": evidence.net_tp,
                    "is_transient": "Yes" if evidence.is_transient else "No",
                    "atom_confirmed_count": evidence.atom_confirmed_count,
                    "net_event_count": evidence.net_event_count,
                    "notes": evidence.notes,
                }
            )
        return rows

    def summary_to_dict(self, matrix: EvidenceMatrixResult) -> Dict[str, Any]:
        """Return a compact summary dict for the UI."""
        return {
            "total_reactions": matrix.total,
            "detected": matrix.detected_count,
            "has_net_flux": matrix.net_flux_count,
            "structure_confirmed": matrix.structure_confirmed_count,
            "not_detected": matrix.not_detected_count,
            "detection_rate": round(
                matrix.detected_count / max(matrix.total, 1), 3
            ),
            "only_transient": sum(
                1 for e in matrix.evidence_rows
                if e.evidence_level == EvidenceLevel.ONLY_TRANSIENT
            ),
        }
