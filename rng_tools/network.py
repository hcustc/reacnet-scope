"""Reaction network graph construction, key intermediate identification,
and multi-step pathway tracing from ReacNetGenerator output.

This module provides a high-level workflow:
    1.  Parse .reactionabcd → build species inventory + reaction list
    2.  Construct a directed reaction network (species-level)
    3.  Score & rank key intermediates (throughput, hub, bottleneck)
    4.  Trace multi-step downstream/upstream cascades
    5.  Detect reversible pairs and compute net flux
    6.  Generate structured reports (ASCII tree, tables, CSV/JSON)

All SMILES handling uses a lightweight bracket-atom parser so that
RDKit is **not required** at runtime (though it will be used for
canonical SMILES if available).
"""

from __future__ import annotations

import re
import json
import csv
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from typing import (
    Dict, List, Set, Tuple, Optional, Sequence, Generator, Any,
)


# ═══════════════════════════════════════════════════════════
#  0.  Lightweight SMILES atom counter (no RDKit needed)
# ═══════════════════════════════════════════════════════════

_BRACKET_RE = re.compile(r'\[([A-Z][a-z]?)')
_BARE_ATOM_RE = re.compile(r'[A-Z][a-z]?')


def count_atoms_fast(smi: str) -> Dict[str, int]:
    """Count element occurrences in a *RNG-style* SMILES string.

    Works reliably for the bracket-heavy notation produced by
    ReacNetGenerator (``[H][O][C]1...``).  Does **not** handle
    implicit hydrogens — which is fine because RNG always writes
    them explicitly.
    """
    counts: Dict[str, int] = {}
    # 1. atoms inside brackets  [H], [Cl], [O], ...
    for m in _BRACKET_RE.finditer(smi):
        a = m.group(1)
        counts[a] = counts.get(a, 0) + 1
    # 2. bare atoms outside brackets (rare in RNG output)
    stripped = re.sub(r'\[.*?\]', '', smi)
    for m in _BARE_ATOM_RE.finditer(stripped):
        a = m.group()
        counts[a] = counts.get(a, 0) + 1
    return counts


def formula_from_counts(counts: Dict[str, int]) -> str:
    """Element-count dict → Hill-order molecular formula string."""
    parts: list[str] = []
    # Hill order: C first, H second, then alphabetical
    for elem in ['C', 'H']:
        if elem in counts:
            parts.append(elem if counts[elem] == 1 else f"{elem}{counts[elem]}")
    for elem in sorted(counts):
        if elem not in ('C', 'H'):
            parts.append(elem if counts[elem] == 1 else f"{elem}{counts[elem]}")
    return ''.join(parts)


def smiles_to_formula_fast(smi: str) -> str:
    """SMILES → molecular formula (lightweight, no RDKit)."""
    return formula_from_counts(count_atoms_fast(smi))


def has_ring(smi: str) -> bool:
    """Quick check: does the SMILES contain a ring closure digit?"""
    return bool(re.search(r'(?<!\[)\d', smi))


def count_ring_atoms(smi: str) -> int:
    """Estimate ring size from closure digits (heuristic)."""
    digits = set(re.findall(r'(?<!\[)(\d)', smi))
    return len(digits) * 2  # very rough: each digit = 2 atoms in ring


# ═══════════════════════════════════════════════════════════
#  1.  Data classes
# ═══════════════════════════════════════════════════════════

@dataclass
class Reaction:
    """A single parsed reaction from *reactionabcd*."""
    reactant_smiles: Tuple[str, ...]
    product_smiles: Tuple[str, ...]
    tp: int  # total passages (event count)

    # Lazy-computed
    _reactant_formulas: Optional[Tuple[str, ...]] = field(
        default=None, repr=False, compare=False)
    _product_formulas: Optional[Tuple[str, ...]] = field(
        default=None, repr=False, compare=False)

    @property
    def reactant_formulas(self) -> Tuple[str, ...]:
        if self._reactant_formulas is None:
            self._reactant_formulas = tuple(
                smiles_to_formula_fast(s) for s in self.reactant_smiles)
        return self._reactant_formulas

    @property
    def product_formulas(self) -> Tuple[str, ...]:
        if self._product_formulas is None:
            self._product_formulas = tuple(
                smiles_to_formula_fast(s) for s in self.product_smiles)
        return self._product_formulas

    @property
    def key(self) -> str:
        """Canonical string key (sorted sides)."""
        r = '+'.join(sorted(self.reactant_smiles))
        p = '+'.join(sorted(self.product_smiles))
        return f"{r}->{p}"

    @property
    def formula_key(self) -> str:
        r = '+'.join(sorted(self.reactant_formulas))
        p = '+'.join(sorted(self.product_formulas))
        return f"{r}->{p}"

    def involves_smiles(self, smi: str) -> bool:
        return smi in self.reactant_smiles or smi in self.product_smiles

    def involves_formula(self, formula: str) -> bool:
        return formula in self.reactant_formulas or formula in self.product_formulas


@dataclass
class SpeciesInfo:
    """Aggregated statistics for a single SMILES species."""
    smiles: str
    formula: str
    atom_counts: Dict[str, int]

    tp_as_reactant: int = 0       # sum of tp where this appears as reactant
    tp_as_product: int = 0        # sum of tp where this appears as product
    n_consume_rxns: int = 0       # number of unique reactions consuming it
    n_produce_rxns: int = 0       # number of unique reactions producing it

    @property
    def total_throughput(self) -> int:
        return self.tp_as_reactant + self.tp_as_product

    @property
    def net_production(self) -> int:
        """Positive = net source; negative = net sink."""
        return self.tp_as_product - self.tp_as_reactant

    @property
    def hub_score(self) -> int:
        return self.n_consume_rxns + self.n_produce_rxns

    @property
    def n_heavy(self) -> int:
        return sum(v for k, v in self.atom_counts.items() if k != 'H')


@dataclass
class PathNode:
    """A node in a pathway cascade tree."""
    smiles: str
    formula: str
    reaction_smiles: str   # the reaction string that produced this node
    tp: int                # event count for this branch
    fraction: float        # fraction of parent's total consumption
    net_tp: int = 0        # net flux (forward - reverse)
    is_reversible: bool = False
    children: List['PathNode'] = field(default_factory=list)
    depth: int = 0


@dataclass
class InitiationChannel:
    """Aggregated initiation channel for a starting species."""
    reaction_key: str
    reaction_formulas: str
    example_reaction_smiles: str

    forward_tp: int
    reverse_tp: int
    net_events: int
    net_start_loss: int

    share_of_positive_loss: float = 0.0
    share_of_species_net_loss: float = 0.0
    smiles_branches: List[dict] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
#  2.  Reaction file parser
# ═══════════════════════════════════════════════════════════

def parse_reactionabcd(filepath: str,
                       *,
                       min_tp: int = 1) -> List[Reaction]:
    """Read a .reactionabcd file and return a list of Reaction objects.

    Args:
        filepath:  Path to the reactionabcd file.
        min_tp:    Minimum tp to keep (default 1).

    Returns:
        List of Reaction objects (not yet deduplicated).
    """
    reactions: List[Reaction] = []
    with open(filepath) as fh:
        for line in fh:
            line = line.strip()
            if not line or '->' not in line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                tp = int(parts[0])
            except ValueError:
                continue
            if tp < min_tp:
                continue
            lr = parts[1].split('->')
            if len(lr) != 2:
                continue
            r_smi = tuple(s.strip() for s in lr[0].split('+') if s.strip())
            p_smi = tuple(s.strip() for s in lr[1].split('+') if s.strip())
            reactions.append(Reaction(r_smi, p_smi, tp))
    return reactions


# ═══════════════════════════════════════════════════════════
#  3.  ReactionNetwork — the central data structure
# ═══════════════════════════════════════════════════════════

class ReactionNetwork:
    """Weighted directed reaction network built from reactionabcd data.

    Attributes:
        reactions:   list of all Reaction objects (deduplicated, tp summed)
        species:     dict  smiles → SpeciesInfo
        consume_idx: dict  smiles → list of (Reaction, tp) consuming it
        produce_idx: dict  smiles → list of (Reaction, tp) producing it
    """

    def __init__(self, reactions: List[Reaction]):
        # Deduplicate reactions (sum tp for identical key)
        merged: Dict[str, Reaction] = {}
        for rxn in reactions:
            k = rxn.key
            if k in merged:
                merged[k] = Reaction(
                    rxn.reactant_smiles, rxn.product_smiles,
                    merged[k].tp + rxn.tp)
            else:
                merged[k] = Reaction(rxn.reactant_smiles, rxn.product_smiles, rxn.tp)
        self.reactions = list(merged.values())
        # Reverse-reaction lookups are used while building reports and
        # tracing paths.  Keeping this index avoids an O(number of reactions)
        # scan for every queried channel.
        self._reaction_by_key: Dict[str, Reaction] = merged

        # Build species inventory
        self.species: Dict[str, SpeciesInfo] = {}
        self.consume_idx: Dict[str, List[Reaction]] = defaultdict(list)
        self.produce_idx: Dict[str, List[Reaction]] = defaultdict(list)

        for rxn in self.reactions:
            for smi in rxn.reactant_smiles:
                self._ensure_species(smi)
                self.species[smi].tp_as_reactant += rxn.tp
                self.consume_idx[smi].append(rxn)
            for smi in rxn.product_smiles:
                self._ensure_species(smi)
                self.species[smi].tp_as_product += rxn.tp
                self.produce_idx[smi].append(rxn)

        # Count unique reactions
        for smi, sp in self.species.items():
            sp.n_consume_rxns = len(self.consume_idx[smi])
            sp.n_produce_rxns = len(self.produce_idx[smi])

        # Formula-level index
        self._formula_to_smiles: Dict[str, Set[str]] = defaultdict(set)
        for smi, sp in self.species.items():
            self._formula_to_smiles[sp.formula].add(smi)

    def _ensure_species(self, smi: str):
        if smi not in self.species:
            ac = count_atoms_fast(smi)
            self.species[smi] = SpeciesInfo(
                smiles=smi,
                formula=formula_from_counts(ac),
                atom_counts=ac,
            )

    # ── Lookups ────────────────────────────────────────

    def smiles_by_formula(self, formula: str) -> Set[str]:
        """All SMILES matching a given molecular formula."""
        return set(self._formula_to_smiles.get(formula, set()))

    def consumption_of(self, smi: str, *, top_n: int = 0) -> List[Reaction]:
        """Reactions consuming *smi*, sorted by tp desc."""
        rxns = sorted(self.consume_idx.get(smi, []),
                       key=lambda r: r.tp, reverse=True)
        return rxns[:top_n] if top_n else rxns

    def production_of(self, smi: str, *, top_n: int = 0) -> List[Reaction]:
        """Reactions producing *smi*, sorted by tp desc."""
        rxns = sorted(self.produce_idx.get(smi, []),
                       key=lambda r: r.tp, reverse=True)
        return rxns[:top_n] if top_n else rxns

    def total_consume_tp(self, smi: str) -> int:
        return sum(r.tp for r in self.consume_idx.get(smi, []))

    def total_produce_tp(self, smi: str) -> int:
        return sum(r.tp for r in self.produce_idx.get(smi, []))

    # ── Reversible pair detection ─────────────────────

    def find_reverse(self, rxn: Reaction) -> Optional[Reaction]:
        """Find the reverse reaction (products→reactants) if it exists."""
        rev_key = '+'.join(sorted(rxn.product_smiles)) + '->' + \
                  '+'.join(sorted(rxn.reactant_smiles))
        return self._reaction_by_key.get(rev_key)

    def net_flux(self, rxn: Reaction) -> Tuple[int, int, int, bool]:
        """Compute net flux for a reaction.

        Returns:
            (forward_tp, reverse_tp, net_tp, is_reversible)
        """
        rev = self.find_reverse(rxn)
        fwd = rxn.tp
        back = rev.tp if rev else 0
        return fwd, back, fwd - back, rev is not None

    def species_stoich_delta(self, rxn: Reaction, smi: str) -> int:
        """Stoichiometric change of species in one reaction event.

        Returns:
            n_products(smi) - n_reactants(smi)
        """
        return rxn.product_smiles.count(smi) - rxn.reactant_smiles.count(smi)

    def extract_initiation_channels(
        self,
        start_smiles: str,
        *,
        aggregate_by: str = 'formula',
        include_formula_preserving: bool = False,
        min_net_start_loss: int = 1,
        top_n: int = 20,
    ) -> Tuple[List[InitiationChannel], Dict[str, Any]]:
        """Extract net initiation channels that consume the starting species.

        Compared with raw top-consumption ranking, this method explicitly
        accounts for reversibility and species stoichiometry:
            net_start_loss = (-Δstart) * (forward_tp - reverse_tp)

        Only channels with positive net consumption of start_smiles are kept.

        Args:
            start_smiles: Starting species (SMILES).
            aggregate_by: 'formula' (default) or 'smiles'.
            include_formula_preserving:
                If False (default), drop channels where the starting
                molecular formula is conserved (e.g. C6H5ClO -> C6H5ClO).
            min_net_start_loss: Minimum channel strength to keep.
            top_n: Maximum number of returned channels. Set <=0 for all.

        Returns:
            (channels, meta)
            channels: list[InitiationChannel], sorted by net_start_loss desc.
            meta: summary dict with totals and normalization denominators.
        """
        if aggregate_by not in {'formula', 'smiles'}:
            raise ValueError("aggregate_by must be 'formula' or 'smiles'")

        if start_smiles not in self.species:
            return [], {
                'start_smiles': start_smiles,
                'start_formula': smiles_to_formula_fast(start_smiles),
                'aggregate_by': aggregate_by,
                'include_formula_preserving': include_formula_preserving,
                'species_net_loss': 0,
                'positive_loss_total': 0,
                'channels_total': 0,
                'channels_returned': 0,
            }

        species_net_loss = max(
            self.total_consume_tp(start_smiles) - self.total_produce_tp(start_smiles), 0)
        start_formula = self.species[start_smiles].formula
        buckets: Dict[str, InitiationChannel] = {}
        branch_buckets: Dict[str, Dict[str, dict]] = defaultdict(dict)

        for rxn in self.consumption_of(start_smiles):
            delta_start = self.species_stoich_delta(rxn, start_smiles)
            if delta_start >= 0:
                continue
            if not include_formula_preserving:
                delta_formula = (
                    rxn.product_formulas.count(start_formula)
                    - rxn.reactant_formulas.count(start_formula)
                )
                if delta_formula >= 0:
                    continue

            fwd, back, net, _ = self.net_flux(rxn)
            if net <= 0:
                continue

            net_start_loss = (-delta_start) * net
            if net_start_loss < min_net_start_loss:
                continue

            if aggregate_by == 'formula':
                r_side = tuple(sorted(rxn.reactant_formulas))
                p_side = tuple(sorted(rxn.product_formulas))
            else:
                r_side = tuple(sorted(rxn.reactant_smiles))
                p_side = tuple(sorted(rxn.product_smiles))
            key = '+'.join(r_side) + '->' + '+'.join(p_side)

            formula_str = (
                ' + '.join(sorted(rxn.reactant_formulas)) + ' -> '
                + ' + '.join(sorted(rxn.product_formulas))
            )
            smiles_str = (
                ' + '.join(sorted(rxn.reactant_smiles)) + ' -> '
                + ' + '.join(sorted(rxn.product_smiles))
            )

            ch = buckets.get(key)
            if ch is None:
                ch = InitiationChannel(
                    reaction_key=key,
                    reaction_formulas=formula_str,
                    example_reaction_smiles=smiles_str,
                    forward_tp=0,
                    reverse_tp=0,
                    net_events=0,
                    net_start_loss=0,
                )
                buckets[key] = ch

            ch.forward_tp += fwd
            ch.reverse_tp += back
            ch.net_events += net
            ch.net_start_loss += net_start_loss

            # Keep full SMILES-level branches under each aggregated channel.
            branch_key = (
                '+'.join(sorted(rxn.reactant_smiles)) + '->'
                + '+'.join(sorted(rxn.product_smiles))
            )
            br = branch_buckets[key].get(branch_key)
            if br is None:
                br = {
                    'reaction_key': branch_key,
                    'reaction_formulas': formula_str,
                    'reaction_smiles': smiles_str,
                    'forward_tp': 0,
                    'reverse_tp': 0,
                    'net_events': 0,
                    'net_start_loss': 0,
                    'share_of_channel_loss': 0.0,
                }
                branch_buckets[key][branch_key] = br
            br['forward_tp'] += fwd
            br['reverse_tp'] += back
            br['net_events'] += net
            br['net_start_loss'] += net_start_loss

        channels = sorted(
            buckets.values(),
            key=lambda c: (c.net_start_loss, c.net_events, c.forward_tp),
            reverse=True,
        )

        positive_loss_total = sum(c.net_start_loss for c in channels)
        for c in channels:
            c.share_of_positive_loss = (
                c.net_start_loss / positive_loss_total if positive_loss_total else 0.0
            )
            c.share_of_species_net_loss = (
                c.net_start_loss / species_net_loss if species_net_loss else 0.0
            )
            branches = sorted(
                branch_buckets.get(c.reaction_key, {}).values(),
                key=lambda b: (b['net_start_loss'], b['net_events'], b['forward_tp']),
                reverse=True,
            )
            denom = c.net_start_loss
            for b in branches:
                b['share_of_channel_loss'] = (
                    b['net_start_loss'] / denom if denom else 0.0
                )
            c.smiles_branches = branches

        if top_n and top_n > 0:
            channels = channels[:top_n]

        meta = {
            'start_smiles': start_smiles,
            'start_formula': start_formula,
            'aggregate_by': aggregate_by,
            'include_formula_preserving': include_formula_preserving,
            'species_net_loss': species_net_loss,
            'positive_loss_total': positive_loss_total,
            'channels_total': len(buckets),
            'channels_returned': len(channels),
        }
        return channels, meta

    # ── Key intermediate ranking ──────────────────────

    def rank_intermediates(
        self,
        *,
        min_heavy: int = 2,
        min_throughput: int = 10,
        exclude_formulas: Optional[Set[str]] = None,
    ) -> List[SpeciesInfo]:
        """Rank species by "intermediate importance score".

        Score = throughput × log2(hub_score + 1), filtered by heavy-atom
        count and optional formula exclusion (e.g. small radicals).
        """
        import math
        exclude = exclude_formulas or set()
        candidates = []
        for sp in self.species.values():
            if sp.n_heavy < min_heavy:
                continue
            if sp.total_throughput < min_throughput:
                continue
            if sp.formula in exclude:
                continue
            score = sp.total_throughput * math.log2(sp.hub_score + 1)
            candidates.append((score, sp))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [sp for _, sp in candidates]

    # ── Multi-step pathway tracing ────────────────────

    def trace_downstream(
        self,
        start_smiles: str,
        *,
        max_depth: int = 5,
        top_n_branches: int = 5,
        min_tp: int = 1,
        min_fraction: float = 0.01,
        _visited: Optional[Set[str]] = None,
        _depth: int = 0,
    ) -> List[PathNode]:
        """BFS/DFS trace of downstream products from *start_smiles*.

        At each level, takes the top-N consumption channels (by tp),
        computes net flux & reversibility, and recurses into the
        **major product** of each channel.

        Returns:
            List of PathNode trees (one per top-N channel).
        """
        if _visited is None:
            _visited = set()
        if _depth >= max_depth or start_smiles in _visited:
            return []
        _visited = _visited | {start_smiles}

        total_consume = self.total_consume_tp(start_smiles)
        if total_consume == 0:
            return []

        consume_rxns = self.consumption_of(start_smiles, top_n=top_n_branches * 3)
        nodes: List[PathNode] = []
        seen_products: Set[str] = set()

        for rxn in consume_rxns:
            if len(nodes) >= top_n_branches:
                break

            fwd, back, net, is_rev = self.net_flux(rxn)
            if net < min_tp:
                continue

            frac = rxn.tp / total_consume if total_consume else 0
            if frac < min_fraction:
                continue

            # Identify the "major product" to recurse into
            # = the heaviest new product (not the start species itself)
            new_products = [s for s in rxn.product_smiles
                           if s != start_smiles and s not in _visited]
            if not new_products:
                # All products already visited or is self → leaf
                major = rxn.product_smiles[0] if rxn.product_smiles else start_smiles
            else:
                major = max(new_products,
                           key=lambda s: self.species[s].n_heavy
                           if s in self.species else 0)

            if major in seen_products:
                continue
            seen_products.add(major)

            rxn_str = ' + '.join(rxn.reactant_smiles) + ' -> ' + \
                      ' + '.join(rxn.product_smiles)

            node = PathNode(
                smiles=major,
                formula=smiles_to_formula_fast(major),
                reaction_smiles=rxn_str,
                tp=rxn.tp,
                fraction=frac,
                net_tp=net,
                is_reversible=is_rev,
                depth=_depth + 1,
            )

            # Recurse
            if major != start_smiles:
                node.children = self.trace_downstream(
                    major,
                    max_depth=max_depth,
                    top_n_branches=top_n_branches,
                    min_tp=min_tp,
                    min_fraction=min_fraction,
                    _visited=_visited,
                    _depth=_depth + 1,
                )

            nodes.append(node)

        return nodes

    def trace_upstream(
        self,
        target_smiles: str,
        *,
        max_depth: int = 3,
        top_n_branches: int = 5,
        min_tp: int = 1,
        _visited: Optional[Set[str]] = None,
        _depth: int = 0,
    ) -> List[PathNode]:
        """Trace formation pathways of *target_smiles* (upstream)."""
        if _visited is None:
            _visited = set()
        if _depth >= max_depth or target_smiles in _visited:
            return []
        _visited = _visited | {target_smiles}

        total_produce = self.total_produce_tp(target_smiles)
        if total_produce == 0:
            return []

        produce_rxns = self.production_of(target_smiles, top_n=top_n_branches * 3)
        nodes: List[PathNode] = []

        for rxn in produce_rxns:
            if len(nodes) >= top_n_branches:
                break
            if rxn.tp < min_tp:
                continue

            frac = rxn.tp / total_produce if total_produce else 0

            # Major reactant = heaviest non-small-molecule reactant
            new_reactants = [s for s in rxn.reactant_smiles
                            if s != target_smiles and s not in _visited]
            if new_reactants:
                major = max(new_reactants,
                           key=lambda s: self.species[s].n_heavy
                           if s in self.species else 0)
            else:
                major = rxn.reactant_smiles[0] if rxn.reactant_smiles else target_smiles

            rxn_str = ' + '.join(rxn.reactant_smiles) + ' -> ' + \
                      ' + '.join(rxn.product_smiles)

            node = PathNode(
                smiles=major,
                formula=smiles_to_formula_fast(major),
                reaction_smiles=rxn_str,
                tp=rxn.tp,
                fraction=frac,
                net_tp=rxn.tp,
                depth=_depth + 1,
            )

            if major != target_smiles:
                node.children = self.trace_upstream(
                    major,
                    max_depth=max_depth,
                    top_n_branches=top_n_branches,
                    min_tp=min_tp,
                    _visited=_visited,
                    _depth=_depth + 1,
                )

            nodes.append(node)

        return nodes

    # ── Element-tracking helpers ──────────────────────

    def element_fate(
        self,
        start_smiles: str,
        element: str = 'Cl',
        *,
        top_n: int = 10,
    ) -> List[dict]:
        """Track where a specific element goes when *start_smiles* is consumed.

        For each consumption reaction, check if the element count in the
        major product differs from the starting species → classify as
        'retained', 'lost', or 'gained'.

        Returns:
            List of dicts with reaction info + element fate.
        """
        start_el = count_atoms_fast(start_smiles).get(element, 0)
        results = []

        for rxn in self.consumption_of(start_smiles, top_n=top_n):
            for psmi in rxn.product_smiles:
                prod_el = count_atoms_fast(psmi).get(element, 0)
                if prod_el < start_el:
                    fate = 'lost'
                elif prod_el > start_el:
                    fate = 'gained'
                else:
                    fate = 'retained'

                results.append({
                    'reaction': rxn.key,
                    'tp': rxn.tp,
                    'product': psmi,
                    'product_formula': smiles_to_formula_fast(psmi),
                    f'{element}_in_start': start_el,
                    f'{element}_in_product': prod_el,
                    'fate': fate,
                })

        return results


# ═══════════════════════════════════════════════════════════
#  4.  Report generation
# ═══════════════════════════════════════════════════════════

def format_pathway_tree(
    nodes: List[PathNode],
    *,
    indent: str = '',
    is_last: bool = True,
    show_smiles: bool = True,
) -> List[str]:
    """Render a PathNode tree as ASCII text lines."""
    lines: List[str] = []
    for i, node in enumerate(nodes):
        last = (i == len(nodes) - 1)
        prefix = indent + ('└── ' if last else '├── ')
        cont = indent + ('    ' if last else '│   ')

        rev_tag = ' ⇄' if node.is_reversible else ''
        net_tag = f' (net={node.net_tp})' if node.is_reversible else ''
        frac_str = f'{node.fraction*100:.1f}%'

        line = (f"{prefix}[tp={node.tp}, {frac_str}{rev_tag}{net_tag}] "
                f"{node.formula}")
        if show_smiles:
            line += f"  {node.smiles}"
        lines.append(line)

        if node.children:
            lines.extend(format_pathway_tree(
                node.children, indent=cont, is_last=last,
                show_smiles=show_smiles))

    return lines


def format_species_table(
    species_list: Sequence[SpeciesInfo],
    *,
    top_n: int = 30,
    show_smiles: bool = True,
) -> List[str]:
    """Render a ranked species table as text lines."""
    lines = []
    hdr = f"{'Rank':>4}  {'Throughput':>10}  {'Produce':>8}  {'Consume':>8}" \
          f"  {'Net':>8}  {'Hub':>4}  {'Formula':<16}"
    if show_smiles:
        hdr += '  SMILES'
    lines.append(hdr)
    lines.append('─' * len(hdr))

    for i, sp in enumerate(species_list[:top_n], 1):
        row = (f"{i:>4}  {sp.total_throughput:>10}  {sp.tp_as_product:>8}"
               f"  {sp.tp_as_reactant:>8}  {sp.net_production:>+8}"
               f"  {sp.hub_score:>4}  {sp.formula:<16}")
        if show_smiles:
            row += f'  {sp.smiles}'
        lines.append(row)

    return lines


def format_initiation_table(
    channels: Sequence[InitiationChannel],
    *,
    top_n: int = 20,
    show_smiles: bool = True,
) -> List[str]:
    """Render initiation channels as text lines."""
    lines = []
    hdr = (
        f"{'Rank':>4}  {'NetLoss':>8}  {'Share+':>7}  {'ShareNet':>8}  "
        f"{'NetEvt':>7}  {'Fwd':>7}  {'Rev':>7}  Initiation Channel (formula)"
    )
    lines.append(hdr)
    lines.append('─' * len(hdr))

    for i, ch in enumerate(channels[:top_n], 1):
        row = (
            f"{i:>4}  {ch.net_start_loss:>8}  {ch.share_of_positive_loss*100:>6.2f}%"
            f"  {ch.share_of_species_net_loss*100:>7.2f}%  {ch.net_events:>7}"
            f"  {ch.forward_tp:>7}  {ch.reverse_tp:>7}  {ch.reaction_formulas}"
        )
        lines.append(row)
        if show_smiles:
            lines.append(f"      SMILES: {ch.example_reaction_smiles}")

    return lines


def generate_full_report(
    net: ReactionNetwork,
    start_smiles: str,
    *,
    max_depth: int = 5,
    top_n_branches: int = 5,
    top_intermediates: int = 20,
    element_track: Optional[str] = None,
    show_smiles: bool = True,
) -> str:
    """Generate a complete pathway analysis report.

    Args:
        net:              The reaction network.
        start_smiles:     SMILES of the starting material.
        max_depth:        Max cascade depth.
        top_n_branches:   Branches per level.
        top_intermediates: How many intermediates to list.
        element_track:    Element to track fate of (e.g. 'Cl').
        show_smiles:      Whether to include raw SMILES in output.

    Returns:
        Multi-section text report.
    """
    lines: List[str] = []
    sep = '═' * 72

    # Header
    lines.append(sep)
    lines.append('  ReacNetGenerator Pathway Explorer — Automated Report')
    lines.append(sep)
    lines.append(f'  Starting material: {start_smiles}')
    lines.append(f'  Formula: {smiles_to_formula_fast(start_smiles)}')
    lines.append(f'  Total species: {len(net.species)}')
    lines.append(f'  Total reactions: {len(net.reactions)}')
    lines.append('')

    # §1: Starting material summary
    lines.append(f'§1  Starting Material Consumption Summary')
    lines.append('─' * 72)
    sp = net.species.get(start_smiles)
    if sp:
        lines.append(f'  Total consume tp: {sp.tp_as_reactant}')
        lines.append(f'  Total produce tp: {sp.tp_as_product}')
        lines.append(f'  Net: {sp.net_production:+d}')
        lines.append(f'  Consume channels: {sp.n_consume_rxns}')
        lines.append(f'  Produce channels: {sp.n_produce_rxns}')
    lines.append('')

    # §2: Top consumption channels
    lines.append(f'§2  Top Consumption Channels (branching ratios)')
    lines.append('─' * 72)
    total_c = net.total_consume_tp(start_smiles)
    for i, rxn in enumerate(net.consumption_of(start_smiles, top_n=15), 1):
        fwd, back, nf, is_rev = net.net_flux(rxn)
        pct = rxn.tp / total_c * 100 if total_c else 0
        rev_tag = f'  ⇄ rev={back}, net={nf}' if is_rev else ''
        rxn_str = ' + '.join(rxn.reactant_smiles) + ' -> ' + \
                  ' + '.join(rxn.product_smiles)
        lines.append(f'  {i:>3}. tp={rxn.tp:>6} ({pct:5.1f}%){rev_tag}')
        lines.append(f'       {rxn_str}')
        # formula-level
        f_str = ' + '.join(rxn.reactant_formulas) + ' -> ' + \
                ' + '.join(rxn.product_formulas)
        lines.append(f'       [{f_str}]')
        lines.append('')
    lines.append('')

    # §3: Downstream cascade tree
    lines.append(f'§3  Downstream Cascade (max_depth={max_depth})')
    lines.append('─' * 72)
    lines.append(f'  {smiles_to_formula_fast(start_smiles)}  {start_smiles}')
    tree = net.trace_downstream(
        start_smiles,
        max_depth=max_depth,
        top_n_branches=top_n_branches,
    )
    lines.extend(format_pathway_tree(tree, indent='  ',
                                      show_smiles=show_smiles))
    lines.append('')

    # §4: Upstream formation pathways
    lines.append(f'§4  Upstream Formation Pathways (max_depth=3)')
    lines.append('─' * 72)
    lines.append(f'  {smiles_to_formula_fast(start_smiles)}  {start_smiles}')
    up_tree = net.trace_upstream(start_smiles, max_depth=3,
                                 top_n_branches=top_n_branches)
    lines.extend(format_pathway_tree(up_tree, indent='  ',
                                      show_smiles=show_smiles))
    lines.append('')

    # §5: Key intermediates
    lines.append(f'§5  Key Intermediates (top {top_intermediates})')
    lines.append('─' * 72)
    intermediates = net.rank_intermediates(
        min_heavy=2,
        exclude_formulas={'O2', 'H2O', 'H2', 'HCl', 'CO', 'CO2', 'Cl2', 'H2O2'},
    )
    lines.extend(format_species_table(intermediates, top_n=top_intermediates,
                                       show_smiles=show_smiles))
    lines.append('')

    # §6: Element fate (optional)
    if element_track:
        lines.append(f'§6  {element_track} Fate Analysis')
        lines.append('─' * 72)
        fate = net.element_fate(start_smiles, element_track, top_n=15)
        lost = [f for f in fate if f['fate'] == 'lost']
        retained = [f for f in fate if f['fate'] == 'retained']
        gained = [f for f in fate if f['fate'] == 'gained']

        lines.append(f'  {element_track}-loss channels: {len(lost)}')
        for f in lost[:10]:
            lines.append(f"    tp={f['tp']:>5}  {f['product_formula']:<16}  "
                        f"{f['product']}")
        lines.append(f'  {element_track}-retain channels: {len(retained)}')
        for f in retained[:10]:
            lines.append(f"    tp={f['tp']:>5}  {f['product_formula']:<16}  "
                        f"{f['product']}")
        lines.append('')

    # §7: Per-intermediate cascade
    lines.append(f'§7  Per-Intermediate Downstream Cascades (top 5)')
    lines.append('─' * 72)
    for sp in intermediates[:5]:
        lines.append(f'\n  ── {sp.formula}  (throughput={sp.total_throughput}) ──')
        lines.append(f'     {sp.smiles}')
        sub_tree = net.trace_downstream(
            sp.smiles, max_depth=3, top_n_branches=4, min_tp=2,
        )
        lines.extend(format_pathway_tree(sub_tree, indent='     ',
                                          show_smiles=show_smiles))

    lines.append('')
    lines.append(sep)
    lines.append('  End of Report')
    lines.append(sep)

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════
#  5.  CSV / JSON export helpers
# ═══════════════════════════════════════════════════════════

def export_species_csv(net: ReactionNetwork, path: str, *, top_n: int = 100):
    """Export ranked species to CSV."""
    intermediates = net.rank_intermediates(min_heavy=1)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['rank', 'formula', 'smiles', 'throughput',
                    'produce_tp', 'consume_tp', 'net', 'hub_score',
                    'n_heavy', 'has_ring'])
        for i, sp in enumerate(intermediates[:top_n], 1):
            w.writerow([i, sp.formula, sp.smiles, sp.total_throughput,
                       sp.tp_as_product, sp.tp_as_reactant,
                       sp.net_production, sp.hub_score,
                       sp.n_heavy, has_ring(sp.smiles)])
    print(f'  ✓ Species CSV: {path}')


def export_reactions_csv(net: ReactionNetwork, path: str, *, top_n: int = 200):
    """Export top reactions to CSV."""
    rxns = sorted(net.reactions, key=lambda r: r.tp, reverse=True)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['rank', 'tp', 'reactant_formulas', 'product_formulas',
                    'reactant_smiles', 'product_smiles'])
        for i, rxn in enumerate(rxns[:top_n], 1):
            w.writerow([
                i, rxn.tp,
                ' + '.join(rxn.reactant_formulas),
                ' + '.join(rxn.product_formulas),
                ' + '.join(rxn.reactant_smiles),
                ' + '.join(rxn.product_smiles),
            ])
    print(f'  ✓ Reactions CSV: {path}')


def export_pathway_json(
    tree: List[PathNode],
    path: str,
):
    """Export pathway tree to JSON for visualization."""
    def node_to_dict(n: PathNode) -> dict:
        return {
            'smiles': n.smiles,
            'formula': n.formula,
            'reaction': n.reaction_smiles,
            'tp': n.tp,
            'fraction': round(n.fraction, 4),
            'net_tp': n.net_tp,
            'is_reversible': n.is_reversible,
            'depth': n.depth,
            'children': [node_to_dict(c) for c in n.children],
        }

    data = [node_to_dict(n) for n in tree]
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f'  ✓ Pathway JSON: {path}')


def export_initiation_csv(
    channels: Sequence[InitiationChannel],
    path: str,
):
    """Export initiation channels to CSV."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow([
            'rank', 'net_start_loss', 'share_positive_loss_pct',
            'share_species_net_loss_pct', 'net_events', 'forward_tp', 'reverse_tp',
            'reaction_formulas', 'example_reaction_smiles',
        ])
        for i, ch in enumerate(channels, 1):
            w.writerow([
                i,
                ch.net_start_loss,
                round(ch.share_of_positive_loss * 100, 3),
                round(ch.share_of_species_net_loss * 100, 3),
                ch.net_events,
                ch.forward_tp,
                ch.reverse_tp,
                ch.reaction_formulas,
                ch.example_reaction_smiles,
            ])
    print(f'  ✓ Initiation CSV: {path}')


def export_initiation_smiles_branches_csv(
    channels: Sequence[InitiationChannel],
    path: str,
):
    """Export per-channel SMILES branches with branch share.

    This is a companion export to ``export_initiation_csv``:
    - parent channel remains the formula/smiles aggregated pathway
    - each row here is one concrete SMILES branch within that parent
    - branch_share_in_channel_pct = branch_net_start_loss / parent_net_start_loss
    """
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow([
            'parent_rank', 'parent_reaction_formulas',
            'parent_net_start_loss', 'parent_net_events',
            'parent_forward_tp', 'parent_reverse_tp',
            'branch_rank', 'branch_share_in_channel_pct',
            'branch_net_start_loss', 'branch_net_events',
            'branch_forward_tp', 'branch_reverse_tp',
            'branch_reaction_formulas', 'branch_reaction_smiles',
        ])

        for i, ch in enumerate(channels, 1):
            branches = ch.smiles_branches or []
            for j, b in enumerate(branches, 1):
                w.writerow([
                    i,
                    ch.reaction_formulas,
                    ch.net_start_loss,
                    ch.net_events,
                    ch.forward_tp,
                    ch.reverse_tp,
                    j,
                    round(b.get('share_of_channel_loss', 0.0) * 100, 3),
                    b.get('net_start_loss', 0),
                    b.get('net_events', 0),
                    b.get('forward_tp', 0),
                    b.get('reverse_tp', 0),
                    b.get('reaction_formulas', ''),
                    b.get('reaction_smiles', ''),
                ])
    print(f'  ✓ Initiation SMILES branches CSV: {path}')
