"""Read-only access to ReacNetGenerator event and molecule timeline outputs.

The event catalogue is intentionally derived from RNG's compact CSV outputs.
It never opens ``.route`` and never reconstructs reactions from a trajectory.
"""

from __future__ import annotations

import csv
import hashlib
import os
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


class RngEventDataError(RuntimeError):
    """RNG event outputs are missing, incompatible, or incomplete."""


def _signature(path_text: str) -> tuple[str, int, int]:
    path = os.path.abspath(path_text)
    stat = os.stat(path)
    return path, int(stat.st_size), int(stat.st_mtime_ns)


def _terms(text: str) -> tuple[str, ...]:
    # RNG currently joins canonical species with '+'.  Keep multiplicity: it
    # is essential for reactions such as H2O + O -> 2 OH.
    return tuple(sorted(item.strip() for item in str(text or "").split("+") if item.strip()))


def reaction_key(reactant: str, product: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _terms(reactant), _terms(product)


def _trajectory_bond_id(rng_bond: str) -> str:
    parts = str(rng_bond or "").split("-")
    if len(parts) < 3:
        return str(rng_bond or "")
    try:
        return "-".join((str(int(parts[0]) + 1), str(int(parts[1]) + 1), *parts[2:]))
    except ValueError:
        return str(rng_bond or "")


@dataclass(frozen=True)
class MoleculeRow:
    species: str
    atom_ids: frozenset[int]
    bond_ids: tuple[str, ...]


@dataclass(frozen=True)
class MoleculeComponent:
    reactants: tuple[str, ...]
    products: tuple[str, ...]
    atom_ids: tuple[int, ...]
    reactant_bonds: tuple[str, ...]
    product_bonds: tuple[str, ...]

    @property
    def key(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return self.reactants, self.products


def _changed_components(before: tuple[MoleculeRow, ...], after: tuple[MoleculeRow, ...]) -> list[MoleculeComponent]:
    before_by_atom = {atom_id: idx for idx, molecule in enumerate(before) for atom_id in molecule.atom_ids}
    after_by_atom = {atom_id: idx for idx, molecule in enumerate(after) for atom_id in molecule.atom_ids}
    graph: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    for atom_id, before_idx in before_by_atom.items():
        after_idx = after_by_atom.get(atom_id)
        if after_idx is None:
            continue
        left = (0, before_idx)
        right = (1, after_idx)
        graph[left].add(right)
        graph[right].add(left)

    components: list[MoleculeComponent] = []
    seen: set[tuple[int, int]] = set()
    for start in graph:
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        nodes: list[tuple[int, int]] = []
        while queue:
            node = queue.pop()
            nodes.append(node)
            for neighbor in graph[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        left_rows = [before[index] for side, index in nodes if side == 0]
        right_rows = [after[index] for side, index in nodes if side == 1]
        reactants = tuple(sorted(row.species for row in left_rows))
        products = tuple(sorted(row.species for row in right_rows))
        if reactants == products:
            continue
        atom_ids = tuple(sorted({atom for row in (*left_rows, *right_rows) for atom in row.atom_ids}))
        components.append(
            MoleculeComponent(
                reactants=reactants,
                products=products,
                atom_ids=atom_ids,
                reactant_bonds=tuple(sorted({bond for row in left_rows for bond in row.bond_ids})),
                product_bonds=tuple(sorted({bond for row in right_rows for bond in row.bond_ids})),
            )
        )
    components.sort(key=lambda item: (item.key, item.atom_ids))
    return components


@lru_cache(maxsize=8)
def _load_molecule_timeline_cached(path: str, size: int, mtime_ns: int) -> tuple[tuple[int, ...], dict[int, tuple[MoleculeRow, ...]]]:
    del size, mtime_ns
    rows_by_timestep: dict[int, list[MoleculeRow]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"Timestep", "Species", "AtomIDs", "BondIDs"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RngEventDataError(f"molecules CSV columns are incompatible: {path}")
        for row in reader:
            timestep = int(row["Timestep"])
            atom_ids = frozenset(int(value) for value in str(row["AtomIDs"] or "").split(";") if value)
            bonds = tuple(value for value in str(row["BondIDs"] or "").split(";") if value)
            rows_by_timestep[timestep].append(MoleculeRow(str(row["Species"] or ""), atom_ids, bonds))
    timesteps = tuple(sorted(rows_by_timestep))
    frozen = {key: tuple(value) for key, value in rows_by_timestep.items()}
    return timesteps, frozen


def load_molecule_timeline(path_text: str) -> tuple[tuple[int, ...], dict[int, tuple[MoleculeRow, ...]]]:
    return _load_molecule_timeline_cached(*_signature(path_text))


@lru_cache(maxsize=32)
def _load_molecule_frame_indices_cached(
    path: str,
    size: int,
    mtime_ns: int,
    wanted_indices: tuple[int, ...],
) -> tuple[dict[int, int], dict[int, tuple[MoleculeRow, ...]]]:
    """Stream a sorted molecules CSV and retain only requested frame indices."""
    del size, mtime_ns
    wanted = set(wanted_indices)
    if not wanted:
        return {}, {}
    maximum = max(wanted)
    timesteps: dict[int, int] = {}
    rows: dict[int, list[MoleculeRow]] = defaultdict(list)
    current_timestep: int | None = None
    frame_index = -1
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"Timestep", "Species", "AtomIDs", "BondIDs"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RngEventDataError(f"molecules CSV columns are incompatible: {path}")
        for row in reader:
            timestep = int(row["Timestep"])
            if timestep != current_timestep:
                current_timestep = timestep
                frame_index += 1
                if frame_index > maximum:
                    break
                if frame_index in wanted:
                    timesteps[frame_index] = timestep
            if frame_index not in wanted:
                continue
            atom_ids = frozenset(int(value) for value in str(row["AtomIDs"] or "").split(";") if value)
            bonds = tuple(value for value in str(row["BondIDs"] or "").split(";") if value)
            rows[frame_index].append(MoleculeRow(str(row["Species"] or ""), atom_ids, bonds))
    missing = wanted.difference(timesteps)
    if missing:
        raise RngEventDataError(
            "molecules timeline does not cover reaction-event frame index(es): "
            + ",".join(str(value) for value in sorted(missing))
        )
    return timesteps, {key: tuple(value) for key, value in rows.items()}


def load_molecule_frame_indices(
    path_text: str,
    wanted_indices: set[int],
) -> tuple[dict[int, int], dict[int, tuple[MoleculeRow, ...]]]:
    path, size, mtime_ns = _signature(path_text)
    return _load_molecule_frame_indices_cached(
        path, size, mtime_ns, tuple(sorted(int(value) for value in wanted_indices))
    )


@lru_cache(maxsize=8)
def _load_event_rows_cached(path: str, size: int, mtime_ns: int) -> tuple[dict[str, Any], ...]:
    del size, mtime_ns
    rows: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"Timestep_Index", "Reactant", "Product"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RngEventDataError(f"reactionevent CSV columns are incompatible: {path}")
        for source_row, row in enumerate(reader, 1):
            reactant = str(row["Reactant"] or "").strip()
            product = str(row["Product"] or "").strip()
            rows.append(
                {
                    "source_row": source_row,
                    "timestep_index": int(row["Timestep_Index"]),
                    "reactant": reactant,
                    "product": product,
                    "reaction_smiles": f"{reactant} -> {product}",
                    "reaction_key": reaction_key(reactant, product),
                }
            )
    return tuple(rows)


def load_event_rows(path_text: str) -> tuple[dict[str, Any], ...]:
    return _load_event_rows_cached(*_signature(path_text))


def event_output_status(reactionevent_file: str, molecules_file: str) -> dict[str, Any]:
    reaction_path = Path(reactionevent_file)
    molecules_path = Path(molecules_file)
    if not reaction_path.is_file() or not molecules_path.is_file():
        return {
            "state": "missing",
            "ready": False,
            "reactionevent_file": str(reaction_path),
            "molecules_file": str(molecules_path),
            "message": "需要 ReacNetGenerator --reaction-event 与 --show-molecule-time 输出",
        }
    try:
        with reaction_path.open(newline="", encoding="utf-8") as handle:
            reaction_fields = set(csv.DictReader(handle).fieldnames or [])
        with molecules_path.open(newline="", encoding="utf-8") as handle:
            molecule_fields = set(csv.DictReader(handle).fieldnames or [])
        if not {"Timestep_Index", "Reactant", "Product"}.issubset(reaction_fields):
            raise RngEventDataError("reactionevent CSV columns are incompatible")
        if not {"Timestep", "Species", "AtomIDs", "BondIDs"}.issubset(molecule_fields):
            raise RngEventDataError("molecules CSV columns are incompatible")
    except (OSError, ValueError, RngEventDataError) as exc:
        return {
            "state": "invalid", "ready": False,
            "reactionevent_file": str(reaction_path), "molecules_file": str(molecules_path),
            "message": str(exc),
        }
    return {
        "state": "ready", "ready": True,
        "reactionevent_file": str(reaction_path), "molecules_file": str(molecules_path),
        "source_size": reaction_path.stat().st_size + molecules_path.stat().st_size,
    }


def query_rng_events(
    reactionevent_file: str,
    molecules_file: str,
    reaction_text: str,
    *,
    max_events: int = 100,
) -> dict[str, Any]:
    if "->" not in str(reaction_text or ""):
        raise RngEventDataError("请输入完整反应式，例如 A + B -> C + D")
    query_left, query_right = str(reaction_text).split("->", 1)
    wanted = reaction_key(query_left, query_right)
    events = load_event_rows(reactionevent_file)

    by_interval: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        if row["reaction_key"] == wanted:
            by_interval[int(row["timestep_index"])].append(dict(row))

    limit = max(1, min(int(max_events), 10_000))
    selected_intervals: list[int] = []
    selected_count = 0
    for interval in sorted(by_interval):
        selected_intervals.append(interval)
        selected_count += len(by_interval[interval])
        if selected_count >= limit:
            break
    wanted_frame_indices = {value for interval in selected_intervals for value in (interval, interval + 1)}
    timesteps, molecules = load_molecule_frame_indices(molecules_file, wanted_frame_indices)

    output: list[dict[str, Any]] = []
    association_counts = Counter()
    for interval in selected_intervals:
        if interval < 0 or interval not in timesteps or interval + 1 not in timesteps:
            continue
        before_timestep = timesteps[interval]
        after_timestep = timesteps[interval + 1]
        pools: dict[tuple[tuple[str, ...], tuple[str, ...]], deque[MoleculeComponent]] = defaultdict(deque)
        for component in _changed_components(molecules[interval], molecules[interval + 1]):
            pools[component.key].append(component)
        for occurrence, row in enumerate(by_interval[interval], 1):
            component = pools[row["reaction_key"]].popleft() if pools[row["reaction_key"]] else None
            status = "matched" if component is not None else "unresolved_hmm_timeline"
            association_counts[status] += 1
            rng_atom_ids = list(component.atom_ids) if component else []
            atom_ids = [atom_id + 1 for atom_id in rng_atom_ids]
            digest = hashlib.sha1(
                f"{interval}|{row['source_row']}|{','.join(map(str, atom_ids))}".encode("utf-8")
            ).hexdigest()[:12]
            output.append(
                {
                    "event_index": len(output) + 1,
                    "event_id": f"rngevt_{interval}_{digest}",
                    "source_row": row["source_row"],
                    "timestep_index": interval,
                    "before_timestep": before_timestep,
                    "after_timestep": after_timestep,
                    "anchor_frame": after_timestep,
                    "reactant": row["reactant"],
                    "product": row["product"],
                    "reaction_smiles": row["reaction_smiles"],
                    "occurrence": occurrence,
                    "atom_ids": ",".join(map(str, atom_ids)),
                    "atom_id_list": atom_ids,
                    "rng_atom_ids": ",".join(map(str, rng_atom_ids)),
                    "atom_count": len(atom_ids),
                    "reactant_bonds": ";".join(_trajectory_bond_id(bond) for bond in component.reactant_bonds) if component else "",
                    "product_bonds": ";".join(_trajectory_bond_id(bond) for bond in component.product_bonds) if component else "",
                    "association_status": status,
                    "event_class": "RNG 事件" if component else "RNG 事件（原子关联不确定）",
                }
            )
            if len(output) >= limit:
                break
        if len(output) >= limit:
            break
    return {
        "rows": output,
        "meta": {
            "status": "ok", "message": f"从 RNG 事件输出中找到 {len(output)} 条记录",
            "matched_atoms": int(association_counts["matched"]),
            "unresolved_atoms": int(association_counts["unresolved_hmm_timeline"]),
            "reactionevent_file": os.path.abspath(reactionevent_file),
            "molecules_file": os.path.abspath(molecules_file),
        },
    }
