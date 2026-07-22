"""Reaction event deduplication and recrossing filtering.

Analyses atom-level transitions from ``.route`` files to:
1.  Merge short-timescale bond-breaking/re-forming oscillations into
    single candidate events.
2.  Distinguish instantaneous bond oscillations, reversible reactions,
    and net reactions that produce stable products.
3.  Assign each deduplicated event an ``event_id``, atom membership,
    lifetime, recrossing count, and confidence score.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AtomTransition:
    """A single atom-level species change extracted from a route file."""

    atom_id: int
    start_frame: int
    end_frame: int
    from_label: str
    to_label: str
    from_canonical: str
    to_canonical: str
    from_formula: str
    to_formula: str
    direction: str = ""  # "reactant_to_product" | "product_to_reactant" | ""


@dataclass
class RecrossingAtomHistory:
    """Full chronological timeline of one atom's species transitions."""

    atom_id: int
    transitions: List[AtomTransition] = field(default_factory=list)

    @property
    def n_transitions(self) -> int:
        return len(self.transitions)


@dataclass
class RecrossedAtomEvent:
    """A single atom's effective event after recrossing filtering.

    A single raw transition A→B may be split into multiple
    RecrossedAtomEvent entries if the atom oscillates multiple times.
    """

    atom_id: int
    start_frame: int          # first frame in product (or reactant) state
    end_frame: int            # last frame in that state before switching back
    lifetime: int             # end_frame - start_frame
    recrossing_count: int     # how many A→B→A oscillations were detected
    total_bounce_count: int   # raw transition count contributing to this event
    is_net_event: bool        # lifetime >= threshold, stays in new state
    net_direction: str        # "forward", "reverse", or "oscillating"
    from_label: str           # starting SMILES before this event
    to_label: str             # ending SMILES after this event


@dataclass
class DeduplicatedReactionEvent:
    """A merged reaction event spanning a group of atoms in time.

    Multiple atoms whose transition windows overlap are grouped into
    a single molecular-scale event.
    """

    event_id: str
    reaction_signature: str   # canonical reaction key
    atom_ids: Set[int]
    start_frame: int          # earliest atom transition
    end_frame: int            # latest atom transition
    lifetime: int             # end_frame - start_frame
    recrossing_count: int     # total recrossing bounces across all atoms
    total_atom_events: int    # number of raw atom transitions
    net_atom_events: int      # number of net (non-oscillating) atom events
    is_net_event: bool        # enough net atom events qualify
    confidence: float         # 0.0 - 1.0
    avg_lifetime: float       # mean atom lifetime in frames


# ---------------------------------------------------------------------------
# RecrossingAnalyzer
# ---------------------------------------------------------------------------


class RecrossingAnalyzer:
    """Detect and filter recrossing events from atom-level route transitions.

    Parameters
    ----------
    recrossing_threshold_frames:
        Maximum frame gap to consider as an oscillation (A→B→A within
        this many frames is counted as one recrossing).
    net_event_min_lifetime:
        Minimum frame lifetime for an atom to be considered "net"
        (i.e. actually switched to a new species).
    min_atom_participation:
        Fraction of participating atoms that must qualify as net for
        the whole reaction event to be considered ``is_net_event``.
    """

    def __init__(
        self,
        recrossing_threshold_frames: int = 10,
        net_event_min_lifetime: int = 50,
        min_atom_participation: float = 0.5,
    ) -> None:
        self.recrossing_threshold_frames = recrossing_threshold_frames
        self.net_event_min_lifetime = net_event_min_lifetime
        self.min_atom_participation = min_atom_participation

    # -- atom histories ---------------------------------------------------

    def build_atom_histories(
        self,
        transitions: List[AtomTransition],
    ) -> Dict[int, RecrossingAtomHistory]:
        """Group transitions by atom_id and sort by frame."""
        groups: Dict[int, List[AtomTransition]] = defaultdict(list)
        for t in transitions:
            groups[t.atom_id].append(t)

        histories: Dict[int, RecrossingAtomHistory] = {}
        for atom_id, tlist in groups.items():
            tlist.sort(key=lambda x: (x.start_frame, x.end_frame))
            histories[atom_id] = RecrossingAtomHistory(
                atom_id=atom_id,
                transitions=tlist,
            )
        return histories

    # -- per-atom recrossing detection ------------------------------------

    def detect_atom_recrossing(
        self,
        history: RecrossingAtomHistory,
    ) -> List[RecrossedAtomEvent]:
        """Scan one atom's timeline for oscillation patterns.

        Algorithm
        ---------
        1. Walk transitions chronologically.
        2. If the atom returns to a previous species within
           ``recrossing_threshold_frames``, count it as a recrossing.
        3. If the atom stays in a new state for at least
           ``net_event_min_lifetime``, mark as ``is_net_event=True``.
        4. Each contiguous period in a new state becomes a
           ``RecrossedAtomEvent``.
        """
        if not history.transitions:
            return []

        events: List[RecrossedAtomEvent] = []
        tlist = history.transitions

        # Find the "initial" species (first from_label)
        initial_species = tlist[0].from_label

        i = 0
        while i < len(tlist):
            t = tlist[i]
            # Which direction is this transition?
            going_to_new = (t.to_label != initial_species)
            direction = "forward" if going_to_new else "reverse"

            # Collect a contiguous block of transitions
            block_start = t.start_frame
            block_end = t.end_frame
            block_labels_visited: Set[str] = {t.to_label}
            recrossing_count = 0
            total_bounce = 1

            j = i + 1
            while j < len(tlist):
                next_t = tlist[j]
                gap = next_t.start_frame - tlist[j - 1].end_frame

                if gap <= self.recrossing_threshold_frames:
                    # Still in the same oscillation cluster
                    block_end = next_t.end_frame
                    if next_t.to_label in block_labels_visited:
                        recrossing_count += 1
                    block_labels_visited.add(next_t.to_label)
                    total_bounce += 1
                    j += 1
                else:
                    break

            lifetime = block_end - block_start
            is_net = (
                lifetime >= self.net_event_min_lifetime
                and recrossing_count == 0
            )

            net_dir = direction
            if recrossing_count > 0:
                net_dir = "oscillating"

            events.append(
                RecrossedAtomEvent(
                    atom_id=history.atom_id,
                    start_frame=block_start,
                    end_frame=block_end,
                    lifetime=lifetime,
                    recrossing_count=recrossing_count,
                    total_bounce_count=total_bounce,
                    is_net_event=is_net,
                    net_direction=net_dir,
                    from_label=t.from_label,
                    to_label=t.to_label,
                )
            )
            i = j

        return events

    # -- cross-atom deduplication -----------------------------------------

    def deduplicate_events(
        self,
        atom_events: List[RecrossedAtomEvent],
        reaction_signature: str = "",
    ) -> List[DeduplicatedReactionEvent]:
        """Merge overlapping atom-level events into reaction-level events.

        Atoms whose transition time windows overlap are grouped into a
        single molecular event.  Non-overlapping time windows produce
        separate events.

        Parameters
        ----------
        atom_events:
            Filtered per-atom events (output of
            :meth:`detect_atom_recrossing`).
        reaction_signature:
            Canonical reaction key (e.g. ``"A+B->C+D"``) shared by all
            these atom events.
        """
        if not atom_events:
            return []

        # Sort by start_frame for time-window clustering
        sorted_events = sorted(atom_events, key=lambda e: e.start_frame)

        clusters: List[List[RecrossedAtomEvent]] = []
        current_cluster: List[RecrossedAtomEvent] = [sorted_events[0]]
        current_max_end = sorted_events[0].end_frame

        for evt in sorted_events[1:]:
            # Overlap if start_frame <= current_max_end (with small tolerance)
            if evt.start_frame <= current_max_end + self.recrossing_threshold_frames:
                current_cluster.append(evt)
                current_max_end = max(current_max_end, evt.end_frame)
            else:
                clusters.append(current_cluster)
                current_cluster = [evt]
                current_max_end = evt.end_frame
        clusters.append(current_cluster)

        dedup_events: List[DeduplicatedReactionEvent] = []
        for cluster in clusters:
            atom_ids = {e.atom_id for e in cluster}
            start_frame = min(e.start_frame for e in cluster)
            end_frame = max(e.end_frame for e in cluster)
            lifetime = end_frame - start_frame
            total_recrossing = sum(e.recrossing_count for e in cluster)
            total_atom = sum(e.total_bounce_count for e in cluster)
            net_atom = sum(1 for e in cluster if e.is_net_event)
            avg_lifetime = sum(e.lifetime for e in cluster) / len(cluster) if cluster else 0.0

            confidence = net_atom / max(total_atom, 1)
            is_net = (
                len(cluster) >= 1
                and net_atom >= max(1, len(cluster) * self.min_atom_participation)
                and lifetime >= self.net_event_min_lifetime
            )

            dedup_events.append(
                DeduplicatedReactionEvent(
                    event_id=self._make_event_id(
                        reaction_signature, start_frame, atom_ids
                    ),
                    reaction_signature=reaction_signature,
                    atom_ids=atom_ids,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    lifetime=lifetime,
                    recrossing_count=total_recrossing,
                    total_atom_events=total_atom,
                    net_atom_events=net_atom,
                    is_net_event=is_net,
                    confidence=round(confidence, 4),
                    avg_lifetime=round(avg_lifetime, 1),
                )
            )

        return dedup_events

    # -- full pipeline ----------------------------------------------------

    def analyze(
        self,
        transitions: List[AtomTransition],
        reaction_signature: str = "",
    ) -> Tuple[List[DeduplicatedReactionEvent], Dict[str, Any]]:
        """Run the full recrossing → deduplication pipeline.

        Returns
        -------
        (events, stats)
            ``events`` — deduplicated reaction events sorted by
            confidence (descending) then start_frame.
            ``stats`` — summary dictionary with keys:
            ``total_raw_atom_transitions``,
            ``total_net_atom_events``,
            ``total_recrossing_count``,
            ``total_deduplicated_events``,
            ``recrossing_rate`` (fraction of atom events with recrossing).
        """
        histories = self.build_atom_histories(transitions)

        all_atom_events: List[RecrossedAtomEvent] = []
        for history in histories.values():
            all_atom_events.extend(self.detect_atom_recrossing(history))

        dedup_events = self.deduplicate_events(all_atom_events, reaction_signature)

        total_atom = len(all_atom_events)
        total_recrossing = sum(e.recrossing_count for e in all_atom_events)
        total_net = sum(1 for e in all_atom_events if e.is_net_event)

        stats: Dict[str, Any] = {
            "total_raw_atom_transitions": len(transitions),
            "total_atom_events": total_atom,
            "total_net_atom_events": total_net,
            "total_recrossing_count": total_recrossing,
            "total_deduplicated_events": len(dedup_events),
            "net_deduplicated_events": sum(1 for e in dedup_events if e.is_net_event),
            "recrossing_rate": (
                round(total_recrossing / max(total_atom, 1), 4)
            ),
        }

        return dedup_events, stats

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _make_event_id(
        reaction_signature: str,
        start_frame: int,
        atom_ids: Set[int],
    ) -> str:
        sorted_ids = ",".join(str(a) for a in sorted(atom_ids))
        raw = f"{reaction_signature}|{start_frame}|{sorted_ids}"
        digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
        return f"rxevt_{start_frame}_{digest}"


# ---------------------------------------------------------------------------
# High-level convenience function
# ---------------------------------------------------------------------------


def convert_route_hits_to_transitions(
    hits: List[Dict[str, Any]],
) -> List[AtomTransition]:
    """Convert route query hit dicts (from RouteTransitionIndexStore) into
    :class:`AtomTransition` objects.

    Each hit dict is expected to have keys: ``atom_id``, ``start_frame``,
    ``end_frame``, ``from_label``, ``to_label``, ``from_token``,
    ``to_token``, ``direction``.
    """
    transitions: List[AtomTransition] = []
    for h in hits:
        transitions.append(
            AtomTransition(
                atom_id=int(h["atom_id"]),
                start_frame=int(h["start_frame"]),
                end_frame=int(h["end_frame"]),
                from_label=str(h.get("from_label", "")),
                to_label=str(h.get("to_label", "")),
                from_canonical=str(h.get("from_token", "")),
                to_canonical=str(h.get("to_token", "")),
                from_formula="",
                to_formula="",
                direction=str(h.get("direction", "")),
            )
        )
    return transitions


def dedup_events_to_rows(
    events: List[DeduplicatedReactionEvent],
) -> List[Dict[str, Any]]:
    """Convert :class:`DeduplicatedReactionEvent` list to flat dict rows
    suitable for a DataTable.
    """
    rows: List[Dict[str, Any]] = []
    for i, evt in enumerate(events):
        rows.append(
            {
                "event_index": i + 1,
                "event_id": evt.event_id,
                "reaction_signature": evt.reaction_signature,
                "atom_ids": ",".join(str(a) for a in sorted(evt.atom_ids)),
                "atom_count": len(evt.atom_ids),
                "start_frame": evt.start_frame,
                "end_frame": evt.end_frame,
                "lifetime": evt.lifetime,
                "recrossing_count": evt.recrossing_count,
                "total_atom_events": evt.total_atom_events,
                "net_atom_events": evt.net_atom_events,
                "is_net_event": "Yes" if evt.is_net_event else "No",
                "confidence": evt.confidence,
                "avg_lifetime": evt.avg_lifetime,
            }
        )
    return rows


def classify_event(
    forward_tp: int,
    reverse_tp: int,
    dedup_events: Optional[List[DeduplicatedReactionEvent]] = None,
) -> str:
    """Return a human-readable event classification label.

    Labels
    ------
    - ``"instantaneous_oscillation"`` — no net events, high recrossing
    - ``"reversible"`` — both forward and reverse net events exist
    - ``"net_forward"`` — only forward net events
    - ``"net_reverse"`` — only reverse net events
    - ``"no_event"`` — no events at all
    """
    if dedup_events is None:
        dedup_events = []

    net_events = [e for e in dedup_events if e.is_net_event]
    has_forward = any(e.reaction_signature and "->" in e.reaction_signature for e in net_events)
    has_reverse = False  # Determined by direction field if available

    if not dedup_events:
        if forward_tp == 0 and reverse_tp == 0:
            return "no_event"
        if forward_tp == reverse_tp:
            return "instantaneous_oscillation"
        return "reversible" if forward_tp > 0 and reverse_tp > 0 else "net_forward" if forward_tp > reverse_tp else "net_reverse"

    if not net_events:
        total_recrossing = sum(e.recrossing_count for e in dedup_events)
        if total_recrossing > 0:
            return "instantaneous_oscillation"
        return "only_transient"

    # Check for forward/reverse via direction in net events
    forward_net = sum(
        1 for e in net_events
        if getattr(e, 'net_direction', '') == 'forward'
    )
    reverse_net = sum(
        1 for e in net_events
        if getattr(e, 'net_direction', '') == 'reverse'
    )

    if forward_net > 0 and reverse_net > 0:
        return "reversible"
    elif forward_net > 0:
        return "net_forward"
    elif reverse_net > 0:
        return "net_reverse"

    return "net_forward" if forward_tp > reverse_tp else "reversible"
