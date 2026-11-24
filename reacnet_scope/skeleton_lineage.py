"""Skeleton-level atom-anchored lineage tracking over published segment indexes.

Unlike ``lineage_explorer``'s strict exact-bond-state expansion, this module
provides explicit, auditable continuity modes:

- ``strict``: preserves exact ``(species, atom_ids, bonds)`` segments.
- ``skeleton``: merges contiguous segments that share the exact same
  bond-order-free connectivity graph (eliminating high-temperature bond-order
  flip fragmentation such as C=O single/double oscillation), and optionally
  folds brief ``A -> B -> A`` oscillation excursions while preserving all
  folded species and frame counts for audit.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .event_index import EVENT_EVIDENCE_STORE
from .indexes import IndexNotReadyError
from .lineage_segments import VERSION as LINEAGE_SEGMENTS_VERSION
from .provenance import collect_runtime_provenance
from .service_types import ServiceError
from .workspace_services import _event_artifact_paths


SKELETON_LINEAGE_SCHEMA = "reacnet-scope/skeleton-lineage/v1"
VALID_MODES = ("strict", "skeleton")
EVENT_PLATEAU_MAX_PADDING = 100


def strip_bond_orders(bonds: Iterable[str]) -> tuple[tuple[int, int], ...]:
    """Convert ``a-b-order`` bond strings into canonical undirected pairs."""
    edges: set[tuple[int, int]] = set()
    for raw in bonds:
        parts = str(raw).split("-", 2)
        if len(parts) < 2:
            continue
        a, b = int(parts[0]), int(parts[1])
        edges.add((a, b) if a <= b else (b, a))
    return tuple(sorted(edges))


def connectivity_signature(
    atom_ids: Iterable[int],
    bonds: Iterable[str],
) -> tuple[str, tuple[int, ...], tuple[tuple[int, int], ...]]:
    """Return a deterministic bond-order-free connectivity hash and canonical graph."""
    atoms = tuple(sorted({int(a) for a in atom_ids}))
    edges = strip_bond_orders(bonds)
    payload = json.dumps(
        {"atoms": atoms, "edges": edges},
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"conn:v1:{digest}", atoms, edges


def cyclomatic_ring_count(
    atoms: Sequence[int],
    edges: Sequence[tuple[int, int]],
) -> int:
    """Return the circuit rank (E - V + C) of a molecular connectivity graph."""
    if not atoms:
        return 0
    adj: dict[int, set[int]] = {a: set() for a in atoms}
    edge_count = 0
    for a, b in edges:
        if a in adj and b in adj and a != b:
            if b not in adj[a]:
                adj[a].add(b)
                adj[b].add(a)
                edge_count += 1
    components = 0
    unseen = set(atoms)
    while unseen:
        components += 1
        stack = [next(iter(unseen))]
        while stack:
            node = stack.pop()
            if node not in unseen:
                continue
            unseen.remove(node)
            stack.extend(adj[node] & unseen)
    return max(0, edge_count - len(atoms) + components)


def _normalize_segment(raw: Mapping[str, Any], anchors: set[int]) -> dict[str, Any]:
    atom_ids = sorted({int(a) for a in raw["atom_ids"]})
    bonds = [str(b) for b in raw.get("bonds", ())]
    conn_sig, canon_atoms, edges = connectivity_signature(atom_ids, bonds)
    start_frame = int(raw["start_frame"])
    end_frame = int(raw["end_frame"])
    frame_count = max(1, end_frame - start_frame + 1)
    species = str(raw["species"])
    return {
        "segment_ids": [str(raw["segment_id"])],
        "start_frame": start_frame,
        "end_frame": end_frame,
        "start_timestep": raw.get("start_timestep"),
        "end_timestep": raw.get("end_timestep"),
        "source_id": raw.get("source_id"),
        "frame_count": frame_count,
        "atom_ids": list(canon_atoms),
        "connectivity_edges": [list(pair) for pair in edges],
        "connectivity_signature": conn_sig,
        "ring_count": cyclomatic_ring_count(canon_atoms, edges),
        "retained_anchors": sorted(anchors & set(canon_atoms)),
        "lost_anchors": sorted(anchors - set(canon_atoms)),
        "gained_atoms": sorted(set(canon_atoms) - anchors),
        "species_frame_counts": {species: frame_count},
        "bond_order_flips_merged": 0,
        "oscillation_excursions_folded": 0,
        "folded_frame_count": 0,
        "folded_species": [],
        "raw_segment_count": 1,
    }


def _merge_platform(base: dict[str, Any], nxt: dict[str, Any], *, is_flip: bool) -> dict[str, Any]:
    counts = Counter(base["species_frame_counts"])
    counts.update(nxt["species_frame_counts"])
    merged = {
        **base,
        "segment_ids": [*base["segment_ids"], *nxt["segment_ids"]],
        "end_frame": max(base["end_frame"], nxt["end_frame"]),
        "end_timestep": nxt["end_timestep"] if nxt["end_frame"] >= base["end_frame"] else base["end_timestep"],
        "frame_count": max(base["end_frame"], nxt["end_frame"]) - base["start_frame"] + 1,
        "species_frame_counts": dict(sorted(counts.items())),
        "bond_order_flips_merged": base["bond_order_flips_merged"] + (1 if is_flip else 0),
        "oscillation_excursions_folded": base["oscillation_excursions_folded"] + nxt["oscillation_excursions_folded"],
        "folded_frame_count": base["folded_frame_count"] + nxt["folded_frame_count"],
        "folded_species": sorted(set(base["folded_species"]) | set(nxt["folded_species"])),
        "raw_segment_count": base["raw_segment_count"] + nxt["raw_segment_count"],
    }
    return merged


def _fold_excursion(
    left: dict[str, Any],
    middle: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    counts = Counter(left["species_frame_counts"])
    counts.update(right["species_frame_counts"])
    folded_species = sorted(
        set(left["folded_species"])
        | set(right["folded_species"])
        | set(middle["species_frame_counts"].keys())
        | set(middle["folded_species"])
    )
    return {
        **left,
        "segment_ids": [*left["segment_ids"], *middle["segment_ids"], *right["segment_ids"]],
        "end_frame": right["end_frame"],
        "end_timestep": right["end_timestep"],
        "frame_count": right["end_frame"] - left["start_frame"] + 1,
        "species_frame_counts": dict(sorted(counts.items())),
        "bond_order_flips_merged": left["bond_order_flips_merged"] + right["bond_order_flips_merged"],
        "oscillation_excursions_folded": (
            left["oscillation_excursions_folded"]
            + right["oscillation_excursions_folded"]
            + 1
        ),
        "folded_frame_count": (
            left["folded_frame_count"]
            + right["folded_frame_count"]
            + middle["frame_count"]
        ),
        "folded_species": folded_species,
        "raw_segment_count": (
            left["raw_segment_count"]
            + middle["raw_segment_count"]
            + right["raw_segment_count"]
        ),
    }


def _finalize_platform(
    platform: dict[str, Any],
    *,
    mode: str,
    timestep_ps: float | None,
) -> dict[str, Any]:
    counts: dict[str, int] = platform["species_frame_counts"]
    representative = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    duration_ps = None
    if (
        timestep_ps is not None
        and platform["start_timestep"] is not None
        and platform["end_timestep"] is not None
    ):
        duration_ps = round(
            (int(platform["end_timestep"]) - int(platform["start_timestep"])) * timestep_ps,
            6,
        )
    continuity_basis = "strict_exact_bond_state"
    if mode == "skeleton":
        if platform["oscillation_excursions_folded"] > 0:
            continuity_basis = "skeleton_connectivity_with_folded_oscillations"
        elif platform["bond_order_flips_merged"] > 0:
            continuity_basis = "skeleton_connectivity_bond_order_merged"
        else:
            continuity_basis = "skeleton_connectivity_exact"
    return {
        "segment_ids": platform["segment_ids"],
        "start_frame": platform["start_frame"],
        "end_frame": platform["end_frame"],
        "start_timestep": platform["start_timestep"],
        "end_timestep": platform["end_timestep"],
        "source_id": platform["source_id"],
        "frame_count": platform["frame_count"],
        "duration_ps": duration_ps,
        "representative_species": representative,
        "raw_species_variants": sorted(counts.keys()),
        "species_frame_counts": counts,
        "connectivity_signature": platform["connectivity_signature"],
        "atom_ids": platform["atom_ids"],
        "ring_count": platform["ring_count"],
        "retained_anchors": platform["retained_anchors"],
        "lost_anchors": platform["lost_anchors"],
        "gained_atoms": platform["gained_atoms"],
        "continuity_basis": continuity_basis,
        "raw_segment_count": platform["raw_segment_count"],
        "bond_order_flips_merged": platform["bond_order_flips_merged"],
        "oscillation_excursions_folded": platform["oscillation_excursions_folded"],
        "folded_frame_count": platform["folded_frame_count"],
        "folded_species": platform["folded_species"],
        "_edges": platform["connectivity_edges"],
    }


def build_skeleton_timeline(
    segments: Sequence[Mapping[str, Any]],
    *,
    anchor_atom_ids: Iterable[int],
    mode: str = "skeleton",
    oscillation_max_frames: int = 2,
    timestep_ps: float | None = None,
) -> dict[str, Any]:
    """Assemble an auditable strict or skeleton-level timeline from segment records.

    Parameters
    ----------
    segments:
        Ordered or unordered segment dicts containing ``segment_id``, ``species``,
        ``atom_ids``, ``bonds``, ``start_frame``, ``end_frame``, and optional
        ``start_timestep`` / ``end_timestep``.
    anchor_atom_ids:
        Heavy-atom (e.g., carbon backbone) IDs tracked across the trajectory.
    mode:
        ``"strict"`` (exact bond-state segments) or ``"skeleton"`` (merge
        contiguous bond-order flips and optional brief A->B->A excursions).
    oscillation_max_frames:
        Maximum excursion length (in frames) folded when ``mode == "skeleton"``
        and flanked by identical ``connectivity_signature`` plateaus. Set to 0
        to disable excursion folding while still merging pure bond-order flips.
    timestep_ps:
        Optional confirmed conversion factor (ps per source timestep).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    if oscillation_max_frames < 0:
        raise ValueError("oscillation_max_frames must be >= 0")

    anchors = {int(a) for a in anchor_atom_ids}
    if not anchors:
        raise ValueError("anchor_atom_ids must contain at least one atom ID")

    ordered = sorted(
        (
            _normalize_segment(seg, anchors)
            for seg in segments
            if anchors & {int(a) for a in seg.get("atom_ids", ())}
        ),
        key=lambda item: (
            item["start_frame"],
            -len(item["retained_anchors"]),
            item["end_frame"],
            item["segment_ids"][0],
        ),
    )

    if mode == "strict":
        plateaus = ordered
    else:
        # Pass 1: merge contiguous segments with identical bond-order-free connectivity.
        merged: list[dict[str, Any]] = []
        for item in ordered:
            if (
                merged
                and merged[-1]["end_frame"] + 1 == item["start_frame"]
                and merged[-1]["source_id"] == item["source_id"]
                and merged[-1]["connectivity_signature"] == item["connectivity_signature"]
            ):
                is_flip = (
                    set(merged[-1]["species_frame_counts"].keys())
                    != set(item["species_frame_counts"].keys())
                )
                merged[-1] = _merge_platform(merged[-1], item, is_flip=is_flip)
            else:
                merged.append(item)

        # Pass 2: fold brief contiguous A -> B -> A oscillation excursions.
        if oscillation_max_frames > 0 and len(merged) >= 3:
            changed = True
            while changed and len(merged) >= 3:
                changed = False
                folded_list: list[dict[str, Any]] = []
                idx = 0
                while idx < len(merged):
                    if (
                        idx + 2 < len(merged)
                        and merged[idx]["end_frame"] + 1 == merged[idx + 1]["start_frame"]
                        and merged[idx + 1]["end_frame"] + 1 == merged[idx + 2]["start_frame"]
                        and merged[idx]["source_id"] == merged[idx + 1]["source_id"] == merged[idx + 2]["source_id"]
                        and merged[idx]["connectivity_signature"]
                        == merged[idx + 2]["connectivity_signature"]
                        and merged[idx + 1]["frame_count"] <= oscillation_max_frames
                    ):
                        folded_list.append(
                            _fold_excursion(merged[idx], merged[idx + 1], merged[idx + 2])
                        )
                        idx += 3
                        changed = True
                    else:
                        folded_list.append(merged[idx])
                        idx += 1
                merged = folded_list
        plateaus = merged

    finalized = [
        _finalize_platform(item, mode=mode, timestep_ps=timestep_ps)
        for item in plateaus
    ]

    transitions: list[dict[str, Any]] = []
    for prev, curr in zip(finalized, finalized[1:]):
        prev_atoms = set(prev["atom_ids"])
        curr_atoms = set(curr["atom_ids"])
        prev_edges = {tuple(edge) for edge in prev["_edges"]}
        curr_edges = {tuple(edge) for edge in curr["_edges"]}
        frame_gap = curr["start_frame"] - prev["end_frame"] - 1
        transitions.append(
            {
                "from_frame": prev["end_frame"],
                "to_frame": curr["start_frame"],
                "from_timestep": prev["end_timestep"],
                "to_timestep": curr["start_timestep"],
                "frame_gap": frame_gap,
                "contiguous": frame_gap == 0 and prev["source_id"] == curr["source_id"],
                "from_species": prev["representative_species"],
                "to_species": curr["representative_species"],
                "from_connectivity": prev["connectivity_signature"],
                "to_connectivity": curr["connectivity_signature"],
                "shared_atoms": sorted(prev_atoms & curr_atoms),
                "lost_atoms": sorted(prev_atoms - curr_atoms),
                "gained_atoms": sorted(curr_atoms - prev_atoms),
                "broken_edges": [list(edge) for edge in sorted(prev_edges - curr_edges)],
                "formed_edges": [list(edge) for edge in sorted(curr_edges - prev_edges)],
            }
        )

    for item in finalized:
        item.pop("_edges", None)

    return {
        "schema_version": SKELETON_LINEAGE_SCHEMA,
        "mode": mode,
        "anchor_atom_ids": sorted(anchors),
        "oscillation_max_frames": oscillation_max_frames if mode == "skeleton" else 0,
        "timestep_ps": timestep_ps,
        "raw_segment_count": len(ordered),
        "plateau_count": len(finalized),
        "total_bond_order_flips_merged": sum(
            item["bond_order_flips_merged"] for item in finalized
        ),
        "total_oscillation_excursions_folded": sum(
            item["oscillation_excursions_folded"] for item in finalized
        ),
        "timeline": finalized,
        "transitions": transitions,
    }


def trace_skeleton_lineage(
    artifacts: Mapping[str, Any],
    *,
    anchor_atom_ids: Iterable[int],
    mode: str = "skeleton",
    oscillation_max_frames: int = 2,
    start_frame: int | None = None,
    end_frame: int | None = None,
    min_anchor_overlap: int = 1,
    timestep_ps: float | None = None,
    max_segments: int = 20000,
    max_seconds: float = 10.0,
) -> dict[str, Any]:
    """Trace an atom-anchored skeleton timeline from a published event index."""
    anchors = sorted({int(a) for a in anchor_atom_ids})
    if not anchors:
        raise ServiceError("至少需要指定一个锚定原子 ID。", reason="invalid_anchor")
    if mode not in VALID_MODES:
        raise ServiceError(
            f"不支持的追踪模式 {mode!r}；可选值为 {', '.join(VALID_MODES)}。",
            reason="invalid_mode",
        )

    source, molecules = _event_artifact_paths(artifacts)
    if not source:
        raise ServiceError(
            "缺少反应事件源（.timeline.h5 或兼容 CSV）。",
            reason="missing_source",
        )
    try:
        opened = EVENT_EVIDENCE_STORE.open_required(source, molecules)
    except (IndexNotReadyError, OSError, ValueError) as exc:
        raise ServiceError(str(exc), reason="lineage_index_unavailable") from exc

    index_path = Path(opened["index_path"])
    con = sqlite3.connect(index_path.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    deadline = time.monotonic() + max_seconds
    con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    truncated = False
    try:
        version_row = con.execute(
            "SELECT value FROM meta WHERE key='lineage_segments_version'"
        ).fetchone()
        if not version_row or version_row[0] != LINEAGE_SEGMENTS_VERSION:
            raise ServiceError(
                "skeleton 谱系追踪需要连续分子段索引；请在数据集准备中重建事件索引。",
                reason="lineage_rebuild_required",
            )
        placeholders = ",".join("?" for _ in anchors)
        params: list[Any] = list(anchors)
        where = [f"a.atom_id IN ({placeholders})"]
        if start_frame is not None:
            where.append("s.end_frame >= ?")
            params.append(int(start_frame))
        if end_frame is not None:
            where.append("s.start_frame <= ?")
            params.append(int(end_frame))
        params.append(int(min_anchor_overlap))
        params.append(int(max_segments) + 1)

        sql = f"""
            SELECT s.segment_id, s.start_frame, s.end_frame,
                   q.species, q.atoms, q.bonds,
                   fa.timestep AS start_timestep, fb.timestep AS end_timestep, fa.source_id,
                   COUNT(DISTINCT a.atom_id) AS anchor_overlap
            FROM lineage_segment_atoms a
            JOIN lineage_segments s ON s.segment_id = a.segment_id
            JOIN lineage_states q ON q.state_key = s.state_key
            LEFT JOIN lineage_frames fa ON fa.frame = s.start_frame
            LEFT JOIN lineage_frames fb ON fb.frame = s.end_frame
            WHERE {' AND '.join(where)}
            GROUP BY s.segment_id
            HAVING COUNT(DISTINCT a.atom_id) >= ?
            ORDER BY s.start_frame ASC, anchor_overlap DESC, s.end_frame ASC
            LIMIT ?
        """
        rows = con.execute(sql, params).fetchall()
        if len(rows) > max_segments:
            truncated = True
            rows = rows[:max_segments]
        segments = [
            {
                "segment_id": row["segment_id"],
                "species": row["species"],
                "atom_ids": json.loads(row["atoms"]),
                "bonds": json.loads(row["bonds"]),
                "start_frame": row["start_frame"],
                "end_frame": row["end_frame"],
                "start_timestep": row["start_timestep"],
                "end_timestep": row["end_timestep"],
                "source_id": row["source_id"],
            }
            for row in rows
        ]
    except sqlite3.OperationalError as exc:
        raise ServiceError("谱系查询未完成：" + str(exc), reason="lineage_read_incomplete") from exc
    finally:
        con.close()

    report = build_skeleton_timeline(
        segments,
        anchor_atom_ids=anchors,
        mode=mode,
        oscillation_max_frames=oscillation_max_frames,
        timestep_ps=timestep_ps,
    )
    report["truncated"] = truncated
    report["provenance"] = collect_runtime_provenance(
        extra={
            "source": str(Path(source).resolve()),
            "index_path": str(index_path.resolve()),
        }
    )
    return report


def find_event_plateau_window(
    artifacts: Mapping[str, str], event: Mapping[str, Any]
) -> dict[str, Any]:
    """Find contiguous skeleton bounds at a matched event's exact endpoints.

    The carrier is the participant pair with the most shared atoms. Tied pairs
    contribute their common window. Other participants remain in the export.
    """
    if event.get("association_status") != "matched":
        raise ServiceError("Plateau mode requires a matched occurrence.", reason="unmatched_event")
    sides = [
        [set(participant["atom_ids"]) for participant in event.get(f"{side}_participants", ())]
        for side in ("reactant", "product")
    ]
    overlaps = [(len(left & right), left, right) for left in sides[0] for right in sides[1]]
    maximum = max((overlap for overlap, _, _ in overlaps), default=0)
    if not maximum:
        raise ServiceError("Plateau mode requires shared participant atoms.", reason="missing_carrier")
    carriers = [
        sorted({tuple(sorted(pair[side])) for pair in overlaps if pair[0] == maximum})
        for side in (1, 2)
    ]
    transition = int(event["timestep_index"])
    before, after = int(event["before_timestep"]), int(event["after_timestep"])
    if before >= after:
        raise ServiceError("Plateau mode requires increasing endpoint timesteps.", reason="invalid_event_time")

    endpoints = []
    for side, frame, timestep, atom_sets in zip(
        ("reactant", "product"), (transition, transition + 1), (before, after), carriers
    ):
        bonds = event.get(f"{side}_bonds") or ()
        if isinstance(bonds, str):
            bonds = bonds.split(";")
        edges = strip_bond_orders(bonds)
        platforms = []
        for atoms in atom_sets:
            # Topology returns are retained in this window, including short excursions.
            lineage = trace_skeleton_lineage(
                artifacts,
                anchor_atom_ids=atoms,
                oscillation_max_frames=0,
                start_frame=max(0, frame - EVENT_PLATEAU_MAX_PADDING),
                end_frame=frame + EVENT_PLATEAU_MAX_PADDING,
                min_anchor_overlap=len(atoms),
            )
            expected_signature, _, _ = connectivity_signature(
                atoms, (f"{a}-{b}" for a, b in edges if a in atoms and b in atoms)
            )
            matches = [
                plateau for plateau in lineage["timeline"]
                if plateau["atom_ids"] == list(atoms)
                and plateau["connectivity_signature"] == expected_signature
                and plateau["start_frame"] <= frame <= plateau["end_frame"]
                and plateau["start_timestep"] is not None
                and plateau["end_timestep"] is not None
                and plateau["start_timestep"] <= timestep <= plateau["end_timestep"]
            ]
            if lineage["truncated"] or len(matches) != 1:
                raise ServiceError(
                    f"Could not establish a unique {side} skeleton plateau for atoms {list(atoms)}.",
                    reason="incomplete_event_plateau",
                )
            platforms.append(matches[0])
        endpoints.append(platforms)

    source_ids = {item["source_id"] for platforms in endpoints for item in platforms}
    if len(source_ids) != 1 or None in source_ids:
        raise ServiceError("Plateau export requires one trajectory source.", reason="ambiguous_plateau_source")
    return {
        "start_timestep": max(item["start_timestep"] for item in endpoints[0]),
        "end_timestep": min(item["end_timestep"] for item in endpoints[1]),
        "reactant_carrier_atom_ids": carriers[0],
        "product_carrier_atom_ids": carriers[1],
        "shared_carrier_atom_count": maximum,
        "source_id": source_ids.pop(),
    }


__all__ = [
    "SKELETON_LINEAGE_SCHEMA",
    "VALID_MODES",
    "build_skeleton_timeline",
    "connectivity_signature",
    "cyclomatic_ring_count",
    "find_event_plateau_window",
    "strip_bond_orders",
    "trace_skeleton_lineage",
]
