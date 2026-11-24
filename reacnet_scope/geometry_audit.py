"""3D minimum-image geometry auditing for RNG reaction events.

Consumes an already extracted event viewer payload (never reopening large
trajectory files) to classify changed bond pairs into:

- ``VERIFIED_NET_TRANSFORMATION``: monotonic bond formation/cleavage that
  persists to the end of the audited window.
- ``BOND_OSCILLATION_ARTIFACT``: repeated cutoff crossings around an already
  bound motif (e.g., high-temperature ether bridge stretching).
- ``TRANSIENT_RECROSSING_COLLISION``: brief approach/stretch followed by
  rebound back to the initial state.
- ``INCONCLUSIVE_WINDOW``: insufficient frames or missing coordinates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


GEOMETRY_AUDIT_SCHEMA = "reacnet-scope/geometry-audit/v1"

VERIFIED_NET_TRANSFORMATION = "VERIFIED_NET_TRANSFORMATION"
BOND_OSCILLATION_ARTIFACT = "BOND_OSCILLATION_ARTIFACT"
TRANSIENT_RECROSSING_COLLISION = "TRANSIENT_RECROSSING_COLLISION"
INCONCLUSIVE_WINDOW = "INCONCLUSIVE_WINDOW"


def _parse_bond_pair(bond_token: str) -> tuple[int, int] | None:
    parts = str(bond_token or "").split("-")
    if len(parts) < 2:
        return None
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return (a, b) if a <= b else (b, a)


def _minimum_image_distance(
    pos_a: tuple[float, float, float],
    pos_b: tuple[float, float, float],
    *,
    box: Sequence[Sequence[float]] | None = None,
    pbc: Sequence[bool] | None = None,
) -> float:
    dx = pos_b[0] - pos_a[0]
    dy = pos_b[1] - pos_a[1]
    dz = pos_b[2] - pos_a[2]
    deltas = [dx, dy, dz]
    if box is not None and len(box) >= 3:
        flags = list(pbc) if pbc is not None and len(pbc) >= 3 else [True, True, True]
        for axis in range(3):
            if not flags[axis]:
                continue
            bounds = box[axis]
            if len(bounds) >= 2:
                length = float(bounds[1]) - float(bounds[0])
                if length > 0:
                    deltas[axis] -= length * round(deltas[axis] / length)
    return math.sqrt(
        deltas[0] * deltas[0] + deltas[1] * deltas[1] + deltas[2] * deltas[2]
    )


def _count_crossings(distances: Sequence[float], cutoff: float) -> int:
    if len(distances) < 2:
        return 0
    states = [d <= cutoff for d in distances]
    return sum(1 for a, b in zip(states, states[1:]) if a != b)


def _reference_cutoff(elem_a: str, elem_b: str) -> float:
    pair = tuple(sorted((elem_a.strip().capitalize(), elem_b.strip().capitalize())))
    if "H" in pair:
        if pair == ("H", "H"):
            return 1.10
        return 1.35
    if "Cl" in pair:
        return 2.15
    return 1.85


def audit_event_geometry(
    view: Mapping[str, Any],
    *,
    break_margin_ang: float = 0.25,
    form_margin_ang: float = 0.15,
    oscillation_min_crossings: int = 2,
) -> dict[str, Any]:
    """Audit changed bond trajectories in a viewer payload using MIC distances."""
    event_id = str(view.get("event_id") or "")
    frames = sorted(
        view.get("frames") or [],
        key=lambda item: int(item.get("frame") or 0),
    )
    bond_evidence = view.get("bond_evidence") or {}
    broken_raw = bond_evidence.get("broken") or []
    formed_raw = bond_evidence.get("formed") or []

    broken_pairs = {
        pair for token in broken_raw if (pair := _parse_bond_pair(token)) is not None
    }
    formed_pairs = {
        pair for token in formed_raw if (pair := _parse_bond_pair(token)) is not None
    }
    # Pairs appearing in both broken and formed are pure bond-order changes;
    # still track them as "reordered" so oscillation/stability is visible.
    pure_broken = sorted(broken_pairs - formed_pairs)
    pure_formed = sorted(formed_pairs - broken_pairs)
    reordered = sorted(broken_pairs & formed_pairs)

    all_tracked: list[tuple[tuple[int, int], str]] = [
        *[(pair, "broken") for pair in pure_broken],
        *[(pair, "formed") for pair in pure_formed],
        *[(pair, "bond_order_change") for pair in reordered],
    ]

    if len(frames) < 2 or not all_tracked:
        return {
            "schema_version": GEOMETRY_AUDIT_SCHEMA,
            "event_id": event_id,
            "frame_count": len(frames),
            "verdict": INCONCLUSIVE_WINDOW,
            "reasons": [
                "insufficient frames or no changed bond pairs available for geometry audit"
            ],
            "pairs": [],
        }

    pair_reports: list[dict[str, Any]] = []
    for (a, b), role in all_tracked:
        series: list[dict[str, Any]] = []
        elem_a = ""
        elem_b = ""
        for frame in frames:
            atom_map = {
                int(atom.get("id") or 0): atom for atom in (frame.get("atoms") or [])
            }
            if a not in atom_map or b not in atom_map:
                continue
            aa, bb = atom_map[a], atom_map[b]
            elem_a = elem_a or str(aa.get("element") or "")
            elem_b = elem_b or str(bb.get("element") or "")
            dist = _minimum_image_distance(
                (float(aa["x"]), float(aa["y"]), float(aa["z"])),
                (float(bb["x"]), float(bb["y"]), float(bb["z"])),
                box=frame.get("box"),
                pbc=frame.get("pbc"),
            )
            series.append(
                {
                    "frame": int(frame.get("frame") or 0),
                    "bond_state": str(frame.get("bond_state") or ""),
                    "distance_ang": round(dist, 4),
                }
            )
        if len(series) < 2:
            continue
        dists = [pt["distance_ang"] for pt in series]
        cutoff = _reference_cutoff(elem_a, elem_b)
        crossings = _count_crossings(dists, cutoff)
        start_d, end_d = dists[0], dists[-1]
        min_d, max_d = min(dists), max(dists)

        if role == "broken":
            persists = end_d >= cutoff + break_margin_ang and end_d > start_d
            rebounded = (
                max_d > start_d + 0.10
                and end_d <= cutoff
                and (max_d - end_d) >= 0.10
            )
            oscillating = crossings >= oscillation_min_crossings and end_d <= cutoff
        elif role == "formed":
            persists = end_d <= cutoff - form_margin_ang and end_d < start_d
            rebounded = (
                min_d < start_d - 0.10
                and end_d >= cutoff
                and (end_d - min_d) >= 0.10
            )
            oscillating = crossings >= oscillation_min_crossings and start_d <= cutoff and end_d <= cutoff
        else:
            persists = False
            rebounded = False
            oscillating = True

        pair_reports.append(
            {
                "atoms": [a, b],
                "elements": [elem_a, elem_b],
                "role": role,
                "reference_cutoff_ang": cutoff,
                "start_distance_ang": start_d,
                "end_distance_ang": end_d,
                "min_distance_ang": min_d,
                "max_distance_ang": max_d,
                "cutoff_crossings": crossings,
                "persists": persists,
                "rebounded": rebounded,
                "oscillating": oscillating,
                "distances": series,
            }
        )

    if not pair_reports:
        return {
            "schema_version": GEOMETRY_AUDIT_SCHEMA,
            "event_id": event_id,
            "frame_count": len(frames),
            "verdict": INCONCLUSIVE_WINDOW,
            "reasons": ["changed bond atoms were missing from frame coordinates"],
            "pairs": [],
        }

    structural_pairs = [p for p in pair_reports if p["role"] in {"broken", "formed"}]
    reasons: list[str] = []

    if not structural_pairs:
        verdict = BOND_OSCILLATION_ARTIFACT
        reasons.append(
            "only bond-order changes were detected without net connectivity change"
        )
    elif any(p["persists"] for p in structural_pairs) and not any(
        p["oscillating"] for p in structural_pairs
    ):
        verdict = VERIFIED_NET_TRANSFORMATION
        for p in structural_pairs:
            if p["persists"]:
                reasons.append(
                    f"{p['role']} pair {p['atoms'][0]}-{p['atoms'][1]} "
                    f"({p['start_distance_ang']} -> {p['end_distance_ang']} A) persisted"
                )
    elif any(p["oscillating"] for p in structural_pairs):
        verdict = BOND_OSCILLATION_ARTIFACT
        for p in structural_pairs:
            if p["oscillating"]:
                reasons.append(
                    f"pair {p['atoms'][0]}-{p['atoms'][1]} crossed cutoff "
                    f"{p['cutoff_crossings']} times ({p['min_distance_ang']}-{p['max_distance_ang']} A)"
                )
    elif any(p["rebounded"] for p in structural_pairs) or all(
        not p["persists"] for p in structural_pairs
    ):
        verdict = TRANSIENT_RECROSSING_COLLISION
        for p in structural_pairs:
            reasons.append(
                f"{p['role']} pair {p['atoms'][0]}-{p['atoms'][1]} did not persist "
                f"(start={p['start_distance_ang']}, min={p['min_distance_ang']}, "
                f"max={p['max_distance_ang']}, end={p['end_distance_ang']} A)"
            )
    else:
        verdict = INCONCLUSIVE_WINDOW
        reasons.append("window geometry did not match a decisive classification rule")

    return {
        "schema_version": GEOMETRY_AUDIT_SCHEMA,
        "event_id": event_id,
        "frame_count": len(frames),
        "verdict": verdict,
        "reasons": reasons,
        "pairs": pair_reports,
    }


__all__ = [
    "BOND_OSCILLATION_ARTIFACT",
    "GEOMETRY_AUDIT_SCHEMA",
    "INCONCLUSIVE_WINDOW",
    "TRANSIENT_RECROSSING_COLLISION",
    "VERIFIED_NET_TRANSFORMATION",
    "audit_event_geometry",
]
