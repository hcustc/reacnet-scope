"""DFT initial-geometry exports derived from one matched reaction occurrence.

The module reads only the two exact trajectory frames named by the occurrence,
reconstructs complete molecule instances through periodic boundaries, validates
the derived cluster, and returns both preview data and a deterministic ZIP.
It does not search for transition states or generate quantum-chemistry jobs.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .indexes import TRAJECTORY_INDEX_STORE, event_evidence_index_path
from .trajectory import (
    TrajectoryDependencyError,
    TrajectoryFrameError,
    load_coordinate_length_unit,
    load_type_element_map,
    normalize_type_element_map,
    read_lammps_frame_block,
)


DFT_GEOMETRY_SCHEMA_VERSION = "reacnet-scope/dft-geometry-package/v1"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SIDE_NAMES = {"reactant": "reactants", "product": "products"}


class DftGeometryError(ValueError):
    """Raised when trustworthy DFT initial geometry cannot be produced."""

    def __init__(self, message: str, *, reason: str = "invalid_geometry") -> None:
        super().__init__(message)
        self.reason = reason


def _strict_int(value: Any, label: str) -> int:
    """Parse an integer without accepting bools or truncating floats."""
    if isinstance(value, bool):
        raise DftGeometryError(f"{label} 必须是整数", reason="invalid_integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise DftGeometryError(f"{label} 必须是整数", reason="invalid_integer")
    text = str(value or "").strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        raise DftGeometryError(f"{label} 必须是整数", reason="invalid_integer")
    return int(text)


@dataclass(frozen=True)
class DftGeometryRequest:
    """One explicit selection and output policy for an occurrence."""

    include_reactants: bool = True
    include_products: bool = True
    reactant_indices: tuple[int, ...] | None = None
    product_indices: tuple[int, ...] | None = None
    layout: str = "combined"
    electronic_states: Mapping[str, Any] = field(default_factory=dict)
    atom_type_map: Mapping[Any, Any] | None = None
    source_length_unit: str | None = None
    warning_atom_count: int = 200
    max_atom_count: int = 5_000


@dataclass(frozen=True)
class DftGeometryBundle:
    """A complete, side-effect-free preview and deterministic archive."""

    manifest: dict[str, Any]
    geometries: dict[str, str]
    atom_map_csv: str
    readme: str
    readiness_report: dict[str, Any] = field(default_factory=dict)
    occurrence: dict[str, Any] = field(default_factory=dict)

    def preview_payload(self) -> dict[str, Any]:
        return {
            "schema_version": DFT_GEOMETRY_SCHEMA_VERSION,
            "manifest": self.manifest,
            "geometries": dict(self.geometries),
            "atom_map_csv": self.atom_map_csv,
            "readme": self.readme,
            "readiness_report": dict(self.readiness_report),
            "occurrence": dict(self.occurrence),
        }

    def to_zip(self) -> bytes:
        payloads: dict[str, bytes] = {
            "manifest.json": (
                json.dumps(
                    self.manifest,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
            "atom_map.csv": self.atom_map_csv.encode("utf-8"),
            "README.txt": self.readme.encode("utf-8"),
        }
        if self.occurrence:
            payloads["occurrence.json"] = (
                json.dumps(
                    self.occurrence,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        if self.readiness_report:
            payloads["reaction_readiness.json"] = (
                json.dumps(
                    self.readiness_report,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        payloads.update(
            {name: value.encode("utf-8") for name, value in self.geometries.items()}
        )
        output = io.BytesIO()
        with ZipFile(output, mode="w") as archive:
            names = ["manifest.json", "atom_map.csv", "README.txt"]
            names.extend(
                name
                for name in ("occurrence.json", "reaction_readiness.json")
                if name in payloads
            )
            names.extend(sorted(self.geometries))
            for name in names:
                info = ZipInfo(filename=name, date_time=_ZIP_TIMESTAMP)
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payloads[name])
        return output.getvalue()


def _geometry_api() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        from ase.data import atomic_numbers, covalent_radii
        from ase.geometry import find_mic
    except ImportError as exc:  # pragma: no cover - optional dependency gate
        raise TrajectoryDependencyError(
            "DFT 几何导出需要 ASE；请运行 uv sync --extra web --extra trajectory"
        ) from exc
    return np, atomic_numbers, covalent_radii, find_mic


def _bond_values(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [value for value in raw.split(";") if value]
    return [str(value) for value in (raw or []) if str(value)]


def _parse_bond(value: Any) -> tuple[int, int, str]:
    parts = str(value or "").split("-")
    if len(parts) < 3:
        raise DftGeometryError(
            f"事件包含无效键标识: {value!r}", reason="invalid_bond_evidence"
        )
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise DftGeometryError(
            f"事件包含无效键标识: {value!r}", reason="invalid_bond_evidence"
        ) from exc
    return min(first, second), max(first, second), "-".join(parts[2:])


def _participants(event: Mapping[str, Any], side: str) -> list[dict[str, Any]]:
    raw = event.get(f"{side}_participants") or []
    participants: list[dict[str, Any]] = []
    occupied: set[int] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, Mapping):
            raise DftGeometryError(
                f"{side} Molecule Instance 数据无效",
                reason="invalid_participants",
            )
        try:
            atom_ids = sorted(
                {
                    _strict_int(item, f"{side} Molecule Instance Atom ID")
                    for item in value.get("atom_ids") or []
                }
            )
        except DftGeometryError as exc:
            raise DftGeometryError(
                f"{side} Molecule Instance Atom IDs 无效",
                reason="invalid_participants",
            ) from exc
        if not atom_ids or any(atom_id <= 0 for atom_id in atom_ids):
            raise DftGeometryError(
                f"{side} Molecule Instance {index + 1} 缺少有效 Atom IDs",
                reason="invalid_participants",
            )
        overlap = occupied.intersection(atom_ids)
        if overlap:
            raise DftGeometryError(
                f"{side} Molecule Instance 重复使用 Atom IDs: {sorted(overlap)}",
                reason="invalid_participants",
            )
        occupied.update(atom_ids)
        participants.append(
            {
                "index": index,
                "species": str(value.get("species") or ""),
                "atom_ids": atom_ids,
            }
        )
    return participants


def _select_participants(
    values: list[dict[str, Any]],
    indices: tuple[int, ...] | None,
    *,
    side: str,
) -> list[dict[str, Any]]:
    if indices is None:
        selected = values
    else:
        normalized = sorted(
            {_strict_int(value, f"{side} Molecule Instance 序号") for value in indices}
        )
        invalid = [value for value in normalized if value < 0 or value >= len(values)]
        if invalid:
            raise DftGeometryError(
                f"{side} Molecule Instance 序号不存在: {invalid}",
                reason="invalid_selection",
            )
        selected = [values[index] for index in normalized]
    if not selected:
        raise DftGeometryError(
            f"{side} 侧没有选中 Molecule Instance", reason="empty_selection"
        )
    return selected


def _read_exact_frames(
    trajectory_file: str,
    timesteps: Iterable[int],
    *,
    atom_type_map: Mapping[str, str],
) -> dict[int, dict[str, Any]]:
    requested = sorted({int(value) for value in timesteps})
    index = TRAJECTORY_INDEX_STORE.open_required(trajectory_file)
    offsets = index.offsets_for(requested)
    missing = [value for value in requested if value not in offsets]
    if missing:
        raise DftGeometryError(
            f"轨迹索引缺少事件证据帧: {missing}", reason="missing_exact_frame"
        )
    frames: dict[int, dict[str, Any]] = {}
    try:
        with open(trajectory_file, "rb") as source:
            for timestep in requested:
                start, end = offsets[timestep]
                source.seek(int(start))
                block = source.read(int(end) - int(start))
                parsed = read_lammps_frame_block(
                    block,
                    type_element_map=atom_type_map,
                )
                if int(parsed.get("frame")) != timestep:
                    raise DftGeometryError(
                        f"轨迹索引帧与源 timestep 不一致: {timestep}",
                        reason="frame_identity_mismatch",
                    )
                frames[timestep] = parsed
    except (OSError, UnicodeError, TrajectoryFrameError) as exc:
        raise DftGeometryError(
            f"无法读取 DFT 几何证据帧: {exc}", reason="trajectory_frame_error"
        ) from exc
    return frames


def _mic_delta(first: Any, second: Any, frame: Mapping[str, Any]) -> Any:
    np, _numbers, _radii, find_mic = _geometry_api()
    vector = np.asarray(second, dtype=float) - np.asarray(first, dtype=float)
    pbc = np.asarray(frame.get("pbc") or [False, False, False], dtype=bool)
    if not pbc.any():
        return vector
    cell = np.asarray(frame.get("cell"), dtype=float)
    if cell.shape != (3, 3) or abs(float(np.linalg.det(cell))) < 1e-12:
        raise DftGeometryError(
            "周期轨迹帧缺少有效晶胞", reason="invalid_periodic_cell"
        )
    try:
        minimum, _length = find_mic(vector, cell, pbc=pbc)
    except Exception as exc:
        raise DftGeometryError(
            f"无法应用最小镜像约定: {exc}", reason="periodic_unwrap_failed"
        ) from exc
    return np.asarray(minimum, dtype=float)


def _lattice_shift(cartesian_shift: Any, frame: Mapping[str, Any]) -> list[int]:
    np, _numbers, _radii, _find_mic = _geometry_api()
    pbc = np.asarray(frame.get("pbc") or [False, False, False], dtype=bool)
    if not pbc.any():
        return [0, 0, 0]
    cell = np.asarray(frame.get("cell"), dtype=float)
    fractional = np.asarray(cartesian_shift, dtype=float) @ np.linalg.inv(cell)
    rounded = np.rint(fractional).astype(int)
    for index, periodic in enumerate(pbc):
        if not periodic:
            rounded[index] = 0
    residual = np.asarray(cartesian_shift, dtype=float) - rounded @ cell
    if float(np.linalg.norm(residual)) > 1e-5:
        raise DftGeometryError(
            "分子周期平移不是有效晶格向量", reason="periodic_unwrap_failed"
        )
    return [int(value) for value in rounded]


def _unwrap_participant(
    frame: Mapping[str, Any],
    participant: Mapping[str, Any],
    bonds: list[tuple[int, int, str]],
) -> dict[str, Any]:
    np, _numbers, _radii, _find_mic = _geometry_api()
    frame_atoms = frame.get("atoms") or {}
    atom_ids = list(participant["atom_ids"])
    missing = [atom_id for atom_id in atom_ids if atom_id not in frame_atoms]
    if missing:
        raise DftGeometryError(
            f"目标帧缺少 Atom IDs: {missing}", reason="missing_atoms"
        )
    raw = {
        atom_id: np.asarray(
            [frame_atoms[atom_id][axis] for axis in ("x", "y", "z")],
            dtype=float,
        )
        for atom_id in atom_ids
    }
    if any(not np.isfinite(position).all() for position in raw.values()):
        raise DftGeometryError("目标帧包含非有限坐标", reason="non_finite_coordinates")
    atom_set = set(atom_ids)
    edges = [(a, b, order) for a, b, order in bonds if a in atom_set and b in atom_set]
    adjacency = {atom_id: [] for atom_id in atom_ids}
    for first, second, _order in edges:
        adjacency[first].append(second)
        adjacency[second].append(first)
    if len(atom_ids) > 1 and not edges:
        raise DftGeometryError(
            f"Molecule Instance {participant['index'] + 1} 缺少分子内键图",
            reason="incomplete_molecular_topology",
        )
    unwrapped = {atom_ids[0]: raw[atom_ids[0]].copy()}
    pending = [atom_ids[0]]
    while pending:
        first = pending.pop(0)
        for second in sorted(adjacency[first]):
            candidate = unwrapped[first] + _mic_delta(raw[first], raw[second], frame)
            if second in unwrapped:
                if float(np.linalg.norm(unwrapped[second] - candidate)) > 1e-5:
                    raise DftGeometryError(
                        "分子环闭合与周期最小镜像不一致",
                        reason="periodic_cycle_inconsistent",
                    )
                continue
            unwrapped[second] = candidate
            pending.append(second)
    if len(unwrapped) != len(atom_ids):
        unresolved = sorted(atom_set.difference(unwrapped))
        raise DftGeometryError(
            f"Molecule Instance 键图不连通: {unresolved}",
            reason="incomplete_molecular_topology",
        )
    return {
        "participant": dict(participant),
        "raw": raw,
        "positions": unwrapped,
        "bonds": edges,
    }


def _place_components(
    frame: Mapping[str, Any],
    components: list[dict[str, Any]],
    contact_edges: list[tuple[int, int, str]],
) -> list[dict[str, Any]]:
    np, _numbers, _radii, _find_mic = _geometry_api()
    # Molecule placement is deterministic by the smallest original Atom ID.
    components.sort(key=lambda item: min(item["positions"].keys()))
    reports: list[dict[str, Any]] = []
    assigned = [components[0]]
    pending = components[1:]
    while pending:
        assigned_ids = {
            atom_id for component in assigned for atom_id in component["positions"]
        }
        contact_candidates: list[tuple[int, int, int, Any]] = []
        for component_index, component in enumerate(pending):
            component_ids = set(component["positions"])
            for first, second, _order in contact_edges:
                if first in assigned_ids and second in component_ids:
                    contact_candidates.append((first, second, component_index, component))
                elif second in assigned_ids and first in component_ids:
                    contact_candidates.append((second, first, component_index, component))
        if contact_candidates:
            first, second, component_index, component = min(
                contact_candidates, key=lambda item: (item[0], item[1], item[2])
            )
            assigned_position = next(
                item["positions"][first] for item in assigned if first in item["positions"]
            )
            raw_first = next(item["raw"][first] for item in assigned if first in item["raw"])
            target = assigned_position + _mic_delta(
                raw_first, component["raw"][second], frame
            )
            translation = target - component["positions"][second]
            placement_mode = "reaction_contact"
        else:
            nearest: list[tuple[float, int, int, int, dict[str, Any], Any]] = []
            for component_index, component in enumerate(pending):
                for left in assigned:
                    for first in sorted(left["positions"]):
                        for second in sorted(component["positions"]):
                            delta = _mic_delta(left["raw"][first], component["raw"][second], frame)
                            nearest.append(
                                (
                                    float(np.linalg.norm(delta)),
                                    first,
                                    second,
                                    component_index,
                                    component,
                                    left["positions"][first] + delta - component["positions"][second],
                                )
                            )
            _distance, _first, _second, component_index, component, translation = min(
                nearest, key=lambda item: (item[0], item[1], item[2], item[3])
            )
            placement_mode = "nearest_fallback"
        for atom_id in component["positions"]:
            component["positions"][atom_id] = (
                component["positions"][atom_id] + translation
            )
        component_ids = set(component["positions"])
        contact_checks: list[dict[str, Any]] = []
        for edge_first, edge_second, order in contact_edges:
            if edge_first in assigned_ids and edge_second in component_ids:
                assigned_atom, component_atom = edge_first, edge_second
            elif edge_second in assigned_ids and edge_first in component_ids:
                assigned_atom, component_atom = edge_second, edge_first
            else:
                continue
            assigned_component = next(
                item for item in assigned if assigned_atom in item["positions"]
            )
            expected = _mic_delta(
                assigned_component["raw"][assigned_atom],
                component["raw"][component_atom],
                frame,
            )
            actual = (
                component["positions"][component_atom]
                - assigned_component["positions"][assigned_atom]
            )
            residual = float(np.linalg.norm(actual - expected))
            check = {
                "atom_ids": [assigned_atom, component_atom],
                "bond_order": order,
                "residual_angstrom": residual,
            }
            contact_checks.append(check)
            if residual > 1e-5:
                raise DftGeometryError(
                    "多个反应接触边无法映射到一致的周期镜像；"
                    f"Atom IDs {assigned_atom}-{component_atom} 残差 {residual:.6g} Å",
                    reason="inconsistent_contact_image",
                )
        reports.append(
            {
                "participant_index": int(component["participant"]["index"]),
                "mode": placement_mode,
                "translation_lattice": _lattice_shift(translation, frame),
                "contact_checks": contact_checks,
                "max_contact_residual_angstrom": max(
                    (item["residual_angstrom"] for item in contact_checks),
                    default=None,
                ),
            }
        )
        assigned.append(component)
        pending.pop(component_index)
    return reports


def _electronic_state(request: DftGeometryRequest, stem: str) -> dict[str, Any]:
    raw = request.electronic_states.get(stem)
    if raw is None:
        return {"charge": None, "multiplicity": None, "status": "unspecified"}
    if isinstance(raw, Mapping):
        charge, multiplicity = raw.get("charge"), raw.get("multiplicity")
    else:
        try:
            charge, multiplicity = raw
        except (TypeError, ValueError) as exc:
            raise DftGeometryError(
                f"{stem} 的电荷/多重度格式无效", reason="invalid_electronic_state"
            ) from exc
    try:
        parsed_charge = _strict_int(charge, f"{stem} 的总电荷")
        parsed_multiplicity = _strict_int(
            multiplicity, f"{stem} 的自旋多重度"
        )
    except DftGeometryError as exc:
        raise DftGeometryError(
            f"{stem} 的电荷/多重度必须是整数", reason="invalid_electronic_state"
        ) from exc
    if parsed_multiplicity <= 0:
        raise DftGeometryError(
            f"{stem} 的自旋多重度必须为正整数", reason="invalid_electronic_state"
        )
    return {
        "charge": parsed_charge,
        "multiplicity": parsed_multiplicity,
        "status": "user_supplied",
    }


def _warnings_for_geometry(
    atoms: list[dict[str, Any]],
    bonds: list[tuple[int, int, str]],
    participant_atom_sets: list[set[int]],
    *,
    warning_atom_count: int,
) -> list[dict[str, str]]:
    np, atomic_numbers, covalent_radii, _find_mic = _geometry_api()
    warnings: list[dict[str, str]] = []
    if len(atoms) > warning_atom_count:
        warnings.append(
            {
                "code": "large_system",
                "message": f"体系包含 {len(atoms)} 个原子，常规 DFT 计算可能昂贵。",
            }
        )
    by_id = {atom["id"]: atom for atom in atoms}
    bond_pairs = {(first, second) for first, second, _order in bonds}
    for first, second, _order in bonds:
        if first not in by_id or second not in by_id:
            continue
        left, right = by_id[first], by_id[second]
        distance = float(np.linalg.norm(left["position"] - right["position"]))
        radius = float(
            covalent_radii[atomic_numbers[left["element"]]]
            + covalent_radii[atomic_numbers[right["element"]]]
        )
        if radius > 0 and distance > 1.8 * radius:
            warnings.append(
                {
                    "code": "stretched_evidence_bond",
                    "message": f"Molecular Evidence 键 {first}-{second} 距离为 {distance:.3f} Å。",
                }
            )
    if len(atoms) <= 1_000:
        for index, left in enumerate(atoms):
            for right in atoms[index + 1 :]:
                pair = (min(left["id"], right["id"]), max(left["id"], right["id"]))
                if pair in bond_pairs:
                    continue
                distance = float(np.linalg.norm(left["position"] - right["position"]))
                radius = float(
                    covalent_radii[atomic_numbers[left["element"]]]
                    + covalent_radii[atomic_numbers[right["element"]]]
                )
                if radius > 0 and distance < 0.55 * radius:
                    warnings.append(
                        {
                            "code": "short_nonbonded_contact",
                            "message": f"非键合原子 {left['id']}-{right['id']} 距离仅 {distance:.3f} Å。",
                        }
                    )
                    if len(warnings) >= 50:
                        return warnings
    else:
        warnings.append(
            {
                "code": "pair_check_limited",
                "message": "体系超过 1,000 个原子，跳过了全量非键合碰撞检查。",
            }
        )
    if len(participant_atom_sets) > 1 and len(atoms) <= 1_000:
        minimum = math.inf
        for left_index, left_ids in enumerate(participant_atom_sets):
            for right_ids in participant_atom_sets[left_index + 1 :]:
                for first in left_ids:
                    for second in right_ids:
                        minimum = min(
                            minimum,
                            float(np.linalg.norm(by_id[first]["position"] - by_id[second]["position"])),
                        )
        if math.isfinite(minimum) and minimum > 8.0:
            warnings.append(
                {
                    "code": "distant_fragments",
                    "message": f"所选分子之间的最近距离为 {minimum:.3f} Å。",
                }
            )
    return warnings


def _geometry_file_name(side: str, kind: str, participant: Mapping[str, Any] | None) -> str:
    if kind == "combined":
        return f"{_SIDE_NAMES[side]}.xyz"
    assert participant is not None
    atom_ids = participant["atom_ids"]
    return (
        f"{side}-{int(participant['index']) + 1:02d}-"
        f"atoms-{min(atom_ids)}-{max(atom_ids)}.xyz"
    )


def _format_number(value: float) -> str:
    normalized = 0.0 if abs(float(value)) < 5e-13 else float(value)
    return f"{normalized:.12f}"


def _build_geometry(
    *,
    event_id: str,
    side: str,
    timestep: int,
    frame: Mapping[str, Any],
    participants: list[dict[str, Any]],
    side_bonds: list[tuple[int, int, str]],
    contact_edges: list[tuple[int, int, str]],
    kind: str,
    request: DftGeometryRequest,
) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
    np, atomic_numbers, _radii, _find_mic = _geometry_api()
    owner_by_atom = {
        atom_id: int(participant["index"])
        for participant in participants
        for atom_id in participant["atom_ids"]
    }
    cross_molecule_bonds = [
        (first, second)
        for first, second, _order in side_bonds
        if first in owner_by_atom
        and second in owner_by_atom
        and owner_by_atom[first] != owner_by_atom[second]
    ]
    if cross_molecule_bonds:
        raise DftGeometryError(
            f"侧别键图跨越不同 Molecule Instance: {cross_molecule_bonds}",
            reason="inconsistent_molecular_topology",
        )
    components = [
        _unwrap_participant(frame, participant, side_bonds)
        for participant in participants
    ]
    placement_reports: list[dict[str, Any]] = []
    if len(components) > 1:
        placement_reports = _place_components(frame, components, contact_edges)
    all_positions = {
        atom_id: position
        for component in components
        for atom_id, position in component["positions"].items()
    }
    core_ids = sorted(
        {
            atom_id
            for first, second, _order in contact_edges
            for atom_id in (first, second)
            if atom_id in all_positions
        }
    )
    center_ids = core_ids or sorted(all_positions)
    center = np.mean([all_positions[atom_id] for atom_id in center_ids], axis=0)
    participant_by_atom = {
        atom_id: participant
        for participant in participants
        for atom_id in participant["atom_ids"]
    }
    frame_atoms = frame.get("atoms") or {}
    atoms: list[dict[str, Any]] = []
    for atom_id in sorted(all_positions):
        source_atom = frame_atoms[atom_id]
        element = str(source_atom.get("element") or "").strip()
        if not element or element not in atomic_numbers:
            raise DftGeometryError(
                f"Atom ID {atom_id} 缺少已确认元素映射",
                reason="incomplete_element_mapping",
            )
        raw = np.asarray([source_atom[axis] for axis in ("x", "y", "z")], dtype=float)
        unwrapped = all_positions[atom_id]
        participant = participant_by_atom[atom_id]
        atoms.append(
            {
                "id": atom_id,
                "type": str(source_atom.get("type") or ""),
                "element": element,
                "position": unwrapped - center,
                "original_position": raw,
                "unwrapped_position": unwrapped,
                "image_shift": _lattice_shift(unwrapped - raw, frame),
                "participant_index": int(participant["index"]),
                "species": str(participant["species"]),
            }
        )
    max_atom_count = _strict_int(request.max_atom_count, "DFT 几何原子数上限")
    warning_atom_count = _strict_int(
        request.warning_atom_count, "DFT 几何警告原子数"
    )
    if max_atom_count <= 0 or warning_atom_count <= 0:
        raise DftGeometryError(
            "DFT 几何原子数阈值必须为正整数", reason="invalid_atom_limit"
        )
    if len(atoms) > max_atom_count:
        raise DftGeometryError(
            f"所选几何包含 {len(atoms)} 个原子，超过上限 {request.max_atom_count}",
            reason="atom_limit_exceeded",
        )
    participant_sets = [set(value["atom_ids"]) for value in participants]
    selected_ids = set(all_positions)
    selected_bonds = [
        value for value in side_bonds if value[0] in selected_ids and value[1] in selected_ids
    ]
    warnings = _warnings_for_geometry(
        atoms,
        selected_bonds,
        participant_sets,
        warning_atom_count=warning_atom_count,
    )
    participant = participants[0] if kind == "separate" else None
    filename = _geometry_file_name(side, kind, participant)
    stem = filename.removesuffix(".xyz")
    electronic_state = _electronic_state(request, stem)
    charge = (
        str(electronic_state["charge"])
        if electronic_state["charge"] is not None
        else "unspecified"
    )
    multiplicity = (
        str(electronic_state["multiplicity"])
        if electronic_state["multiplicity"] is not None
        else "unspecified"
    )
    lines = [
        str(len(atoms)),
        (
            f"event_id={event_id} side={side} source_timestep={timestep} "
            f"charge={charge} multiplicity={multiplicity} units=angstrom"
        ),
    ]
    for atom in atoms:
        lines.append(
            " ".join(
                [
                    atom["element"],
                    *[_format_number(value) for value in atom["position"]],
                ]
            )
        )
    metadata = {
        "file": filename,
        "side": side,
        "kind": kind,
        "source_timestep": int(timestep),
        "atom_count": len(atoms),
        "atom_ids": [atom["id"] for atom in atoms],
        "element_counts": {
            element: sum(1 for atom in atoms if atom["element"] == element)
            for element in sorted({atom["element"] for atom in atoms})
        },
        "participants": [
            {
                "index": int(value["index"]),
                "number": int(value["index"]) + 1,
                "species": str(value["species"]),
                "atom_ids": list(value["atom_ids"]),
            }
            for value in participants
        ],
        "electronic_state": electronic_state,
        "centering": {
            "reference_atom_ids": center_ids,
            "translation_angstrom": [-float(value) for value in center],
            "rotation_applied": False,
        },
        "component_placement": placement_reports,
        "warnings": warnings,
    }
    atom_rows: list[dict[str, Any]] = []
    for row_number, atom in enumerate(atoms, 1):
        atom_rows.append(
            {
                "geometry_file": filename,
                "xyz_row": row_number,
                "side": side,
                "source_timestep": int(timestep),
                "participant_index": atom["participant_index"],
                "participant_number": atom["participant_index"] + 1,
                "species": atom["species"],
                "atom_id": atom["id"],
                "atom_type": atom["type"],
                "element": atom["element"],
                "source_x": float(atom["original_position"][0]),
                "source_y": float(atom["original_position"][1]),
                "source_z": float(atom["original_position"][2]),
                "image_a": atom["image_shift"][0],
                "image_b": atom["image_shift"][1],
                "image_c": atom["image_shift"][2],
                "export_x": float(atom["position"][0]),
                "export_y": float(atom["position"][1]),
                "export_z": float(atom["position"][2]),
            }
        )
    return filename, "\n".join(lines) + "\n", metadata, atom_rows


def _source_signatures(artifacts: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    signatures: dict[str, dict[str, Any]] = {}
    paths = {
        kind: Path(str(artifacts.get(kind) or ""))
        for kind in ("trajectory", "timeline", "reactionevent", "molecules")
    }
    trajectory = paths["trajectory"]
    if trajectory.is_file():
        try:
            index = TRAJECTORY_INDEX_STORE.open_required(str(trajectory))
            paths["trajectory_index"] = Path(index.index_path)
        except (OSError, ValueError):
            pass
    event_source = (
        paths["timeline"]
        if paths["timeline"].is_file()
        else paths["reactionevent"]
    )
    if event_source.is_file():
        paths["event_index"] = event_evidence_index_path(str(event_source))
    for kind, path in paths.items():
        if not path.is_file():
            continue
        stat = path.stat()
        signatures[kind] = {
            "path": str(path.resolve()),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    return signatures


def _atom_map_csv(rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    fields = [
        "geometry_file",
        "xyz_row",
        "side",
        "source_timestep",
        "participant_index",
        "participant_number",
        "species",
        "atom_id",
        "atom_type",
        "element",
        "source_x",
        "source_y",
        "source_z",
        "image_a",
        "image_b",
        "image_c",
        "export_x",
        "export_y",
        "export_z",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _readme(event_id: str) -> str:
    return (
        "ReacNet Scope DFT initial geometry package\n"
        "=========================================\n\n"
        f"Reaction occurrence: {event_id}\n\n"
        "The XYZ files are initial geometries derived from exact before/after "
        "molecular-evidence frames. They are not transition states and are not "
        "complete Gaussian, ORCA, CP2K, or other quantum-chemistry jobs.\n"
        "Molecules were reconstructed through periodic boundaries from the "
        "Molecular Evidence bond graph, then translated as a cluster and "
        "centered without rotation or optimization.\n"
        "Charge and multiplicity are user-supplied when present; unspecified "
        "values were not inferred. See manifest.json and atom_map.csv before "
        "starting a calculation. When reaction_readiness.json is present, "
        "qc_handoff.status=ready means only that this occurrence package can "
        "be handed to an external TS workflow. It does not mean that a rate "
        "can be calculated or that TST/RRKM is applicable.\n"
    )


def build_dft_geometry_bundle(
    artifacts: Mapping[str, str],
    event: Mapping[str, Any],
    request: DftGeometryRequest | None = None,
) -> DftGeometryBundle:
    """Build preview data and a reproducible DFT initial-geometry package."""
    request = request or DftGeometryRequest()
    if str(event.get("association_status") or "") != "matched":
        raise DftGeometryError(
            "只有具有精确 Molecular Evidence 的 matched 事件可以导出 DFT 几何",
            reason="unresolved_event",
        )
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        raise DftGeometryError("DFT 几何导出需要 event_id", reason="missing_event_id")
    layout = str(request.layout or "combined").strip().lower()
    if layout not in {"combined", "separate", "both"}:
        raise DftGeometryError(
            "DFT 几何布局必须是 combined、separate 或 both",
            reason="invalid_layout",
        )
    if not request.include_reactants and not request.include_products:
        raise DftGeometryError("至少选择一个反应侧", reason="empty_selection")
    trajectory_file = str(artifacts.get("trajectory") or "").strip()
    if not trajectory_file or not Path(trajectory_file).is_file():
        raise DftGeometryError("缺少原始轨迹文件", reason="missing_trajectory")
    source_unit = (
        str(request.source_length_unit or "").strip().lower()
        or load_coordinate_length_unit(trajectory_file)
    )
    if source_unit in {"å"}:
        source_unit = "angstrom"
    if source_unit != "angstrom":
        raise DftGeometryError(
            "导出前必须明确确认源轨迹坐标单位为 Å",
            reason="unconfirmed_length_unit",
        )
    mapping = load_type_element_map(trajectory_file)
    if request.atom_type_map is not None:
        mapping.update(normalize_type_element_map(request.atom_type_map))
    try:
        before_timestep = _strict_int(
            event.get("before_timestep"), "before_timestep"
        )
        after_timestep = _strict_int(
            event.get("after_timestep"), "after_timestep"
        )
    except DftGeometryError as exc:
        raise DftGeometryError(
            "事件缺少精确 before/after timestep", reason="missing_exact_frame"
        ) from exc
    frames = _read_exact_frames(
        trajectory_file,
        [before_timestep, after_timestep],
        atom_type_map=mapping,
    )
    reactant_bonds = [_parse_bond(value) for value in _bond_values(event.get("reactant_bonds"))]
    product_bonds = [_parse_bond(value) for value in _bond_values(event.get("product_bonds"))]
    reactant_set = set(reactant_bonds)
    product_set = set(product_bonds)
    side_specs: list[tuple[str, int, list[dict[str, Any]], list[tuple[int, int, str]], list[tuple[int, int, str]]]] = []
    if request.include_reactants:
        values = _participants(event, "reactant")
        selected = _select_participants(
            values, request.reactant_indices, side="reactant"
        )
        side_specs.append(
            (
                "reactant",
                before_timestep,
                selected,
                reactant_bonds,
                sorted(product_set.difference(reactant_set)),
            )
        )
    if request.include_products:
        values = _participants(event, "product")
        selected = _select_participants(
            values, request.product_indices, side="product"
        )
        side_specs.append(
            (
                "product",
                after_timestep,
                selected,
                product_bonds,
                sorted(reactant_set.difference(product_set)),
            )
        )
    geometries: dict[str, str] = {}
    geometry_metadata: list[dict[str, Any]] = []
    atom_rows: list[dict[str, Any]] = []
    for side, timestep, participants, bonds, contacts in side_specs:
        if layout in {"combined", "both"}:
            filename, xyz, metadata, rows = _build_geometry(
                event_id=event_id,
                side=side,
                timestep=timestep,
                frame=frames[timestep],
                participants=participants,
                side_bonds=bonds,
                contact_edges=contacts,
                kind="combined",
                request=request,
            )
            geometries[filename] = xyz
            geometry_metadata.append(metadata)
            atom_rows.extend(rows)
        if layout in {"separate", "both"}:
            for participant in participants:
                filename, xyz, metadata, rows = _build_geometry(
                    event_id=event_id,
                    side=side,
                    timestep=timestep,
                    frame=frames[timestep],
                    participants=[participant],
                    side_bonds=bonds,
                    contact_edges=contacts,
                    kind="separate",
                    request=request,
                )
                geometries[filename] = xyz
                geometry_metadata.append(metadata)
                atom_rows.extend(rows)
    output_stems = {name.removesuffix(".xyz") for name in geometries}
    if any(not isinstance(key, str) for key in request.electronic_states):
        raise DftGeometryError(
            "电子态键必须使用输出 XYZ 文件名（不含扩展名）",
            reason="unknown_electronic_state",
        )
    requested_state_keys = set(request.electronic_states)
    unknown_state_keys = sorted(requested_state_keys.difference(output_stems))
    if unknown_state_keys:
        raise DftGeometryError(
            "电子态键与本次输出不匹配: " + ", ".join(unknown_state_keys),
            reason="unknown_electronic_state",
        )
    requested_states = {
        str(item["file"]).removesuffix(".xyz"): item["electronic_state"]
        for item in geometry_metadata
        if str(item["file"]).removesuffix(".xyz") in requested_state_keys
    }
    reactant_ids = {
        atom_id
        for side, _time, participants, _bonds, _contacts in side_specs
        if side == "reactant"
        for participant in participants
        for atom_id in participant["atom_ids"]
    }
    product_ids = {
        atom_id
        for side, _time, participants, _bonds, _contacts in side_specs
        if side == "product"
        for participant in participants
        for atom_id in participant["atom_ids"]
    }
    manifest = {
        "schema_version": DFT_GEOMETRY_SCHEMA_VERSION,
        "event": {
            "event_id": event_id,
            "reaction_smiles": str(event.get("reaction_smiles") or ""),
            "association_status": "matched",
            "before_timestep": before_timestep,
            "after_timestep": after_timestep,
        },
        "selection": {
            "layout": layout,
            "participant_index_base": 0,
            "participant_number_base": 1,
            "include_reactants": bool(request.include_reactants),
            "include_products": bool(request.include_products),
            "reactant_indices": (
                None if request.reactant_indices is None else list(request.reactant_indices)
            ),
            "product_indices": (
                None if request.product_indices is None else list(request.product_indices)
            ),
        },
        "requested_electronic_states": requested_states,
        "coordinate_treatment": {
            "source_length_unit": "angstrom",
            "output_length_unit": "angstrom",
            "periodic_unwrap": "Molecular Evidence bond graph with ASE minimum image",
            "cluster_placement": "reaction-contact minimum image with deterministic nearest fallback",
            "centering": "reaction-contact atoms, otherwise all selected atoms",
            "rotation_applied": False,
            "optimization_applied": False,
        },
        "atom_order": "ascending original trajectory Atom ID",
        "cross_side_atom_ids_match": (
            reactant_ids == product_ids
            if request.include_reactants and request.include_products
            else None
        ),
        "limits": {
            "warning_atom_count": int(request.warning_atom_count),
            "max_atom_count": int(request.max_atom_count),
            "molecules_are_never_truncated": True,
        },
        "geometries": geometry_metadata,
        "source_signatures": _source_signatures(artifacts),
        "limitations": [
            "initial geometry only; not a transition state",
            "not a complete quantum-chemistry job input",
            "charge and multiplicity are never inferred",
        ],
    }
    return DftGeometryBundle(
        manifest=manifest,
        geometries=geometries,
        atom_map_csv=_atom_map_csv(atom_rows),
        readme=_readme(event_id),
    )


__all__ = [
    "DFT_GEOMETRY_SCHEMA_VERSION",
    "DftGeometryBundle",
    "DftGeometryError",
    "DftGeometryRequest",
    "build_dft_geometry_bundle",
]
