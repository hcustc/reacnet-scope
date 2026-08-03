"""Indexed LAMMPS-frame helpers for event trajectory visualization.

The online Dash workflow passes individual byte ranges obtained from the
prepared trajectory index to this module.  ASE parses only that one frame and
provides cell/PBC handling; this module never scans a complete trajectory.
"""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Mapping

from reacnet_scope.indexes import resolve_dataset_paths


class TrajectoryDependencyError(RuntimeError):
    """Raised when the optional ASE trajectory dependency is unavailable."""


class TrajectoryFrameError(ValueError):
    """Raised when an indexed LAMMPS frame cannot be interpreted safely."""


def _ase_api() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        from ase.data import atomic_numbers
        from ase.geometry import find_mic
        from ase.io import read
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise TrajectoryDependencyError(
            "事件轨迹查看需要 ASE；请运行 "
            "uv sync --extra web --extra trajectory"
        ) from exc
    return np, atomic_numbers, find_mic, read


def _normalized_element(value: Any, atomic_numbers: Mapping[str, int]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text[0].upper() + text[1:].lower()
    return normalized if normalized in atomic_numbers else ""


def normalize_type_element_map(
    values: Mapping[Any, Any] | None,
) -> dict[str, str]:
    """Validate and canonicalize a LAMMPS ``type -> element`` mapping."""
    _np, atomic_numbers, _find_mic, _read = _ase_api()
    normalized: dict[str, str] = {}
    for raw_type, raw_element in (values or {}).items():
        type_text = str(raw_type or "").strip()
        try:
            atom_type = str(int(type_text))
        except (TypeError, ValueError) as exc:
            raise TrajectoryFrameError(
                f"无效的 LAMMPS atom type: {raw_type!r}"
            ) from exc
        if int(atom_type) <= 0:
            raise TrajectoryFrameError(
                f"LAMMPS atom type 必须为正整数: {raw_type!r}"
            )
        element = _normalized_element(raw_element, atomic_numbers)
        if not element:
            raise TrajectoryFrameError(
                f"无效的元素符号: {raw_element!r}"
            )
        normalized[atom_type] = element
    return dict(sorted(normalized.items(), key=lambda item: int(item[0])))


def dataset_settings_path(
    trajectory_file: str,
    *,
    persist_identity: bool = False,
) -> Path:
    """Return the Dataset Workspace settings path for one trajectory."""
    return (
        resolve_dataset_paths(
            trajectory_file,
            persist_identity=persist_identity,
        ).workspace_dir
        / "dataset-settings.json"
    )


def load_type_element_map(trajectory_file: str) -> dict[str, str]:
    """Load a previously confirmed mapping without mutating cache state."""
    path = dataset_settings_path(trajectory_file)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrajectoryFrameError(f"数据集元素映射文件无效: {path}") from exc
    raw = ((payload.get("trajectory") or {}).get("type_element_map") or {})
    if not isinstance(raw, dict):
        raise TrajectoryFrameError(f"数据集元素映射格式无效: {path}")
    return normalize_type_element_map(raw)


def load_timestep_ps(source_file: str) -> float | None:
    """Load a user-confirmed timestep-to-ps conversion without writing."""
    path = dataset_settings_path(source_file)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = (payload.get("time_axis") or {}).get("timestep_ps")
        parsed = float(value)
    except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if parsed > 0 else None


def save_timestep_ps(source_file: str, value: float) -> Path:
    """Persist an explicitly confirmed timestep-to-ps conversion."""
    try:
        timestep_ps = float(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryFrameError("timestep 到 ps 的换算必须是正数") from exc
    if timestep_ps <= 0:
        raise TrajectoryFrameError("timestep 到 ps 的换算必须是正数")
    path = dataset_settings_path(source_file, persist_identity=True)
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrajectoryFrameError(f"数据集设置文件无效: {path}") from exc
        if not isinstance(existing, dict):
            raise TrajectoryFrameError(f"数据集设置格式无效: {path}")
        payload.update(existing)
    payload["time_axis"] = {
        "timestep_ps": timestep_ps,
        "confirmed": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def save_type_element_map(
    trajectory_file: str,
    values: Mapping[Any, Any],
) -> Path:
    """Atomically persist a user-confirmed dataset-specific mapping."""
    mapping = normalize_type_element_map(values)
    path = dataset_settings_path(trajectory_file, persist_identity=True)
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrajectoryFrameError(f"数据集设置文件无效: {path}") from exc
        if not isinstance(existing, dict):
            raise TrajectoryFrameError(f"数据集设置格式无效: {path}")
        payload.update(existing)
    trajectory = payload.get("trajectory")
    if not isinstance(trajectory, dict):
        trajectory = {}
    trajectory["type_element_map"] = mapping
    payload["trajectory"] = trajectory
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _raw_frame_metadata(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    timestep: int | None = None
    atom_count: int | None = None
    box_header = ""
    box_lines: list[str] = []
    atom_columns: list[str] = []
    atom_rows: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line == "ITEM: TIMESTEP" and index + 1 < len(lines):
            try:
                timestep = int(lines[index + 1].split()[0])
            except (IndexError, ValueError) as exc:
                raise TrajectoryFrameError("LAMMPS 帧 timestep 无效") from exc
            index += 2
            continue
        if line.startswith("ITEM: NUMBER OF ATOMS") and index + 1 < len(lines):
            try:
                atom_count = int(lines[index + 1].split()[0])
            except (IndexError, ValueError) as exc:
                raise TrajectoryFrameError("LAMMPS 帧原子数无效") from exc
            index += 2
            continue
        if line.startswith("ITEM: BOX BOUNDS"):
            box_header = line
            box_lines = [
                lines[index + offset].strip()
                for offset in range(1, 4)
                if index + offset < len(lines)
            ]
            if len(box_lines) != 3:
                raise TrajectoryFrameError("LAMMPS 帧晶胞信息不完整")
            index += 4
            continue
        if line.startswith("ITEM: ATOMS"):
            atom_columns = line.split()[2:]
            if "id" not in atom_columns:
                raise TrajectoryFrameError("LAMMPS 轨迹缺少 id 列，无法跨帧定位原子")
            if atom_count is None or atom_count < 0:
                raise TrajectoryFrameError("LAMMPS 帧缺少有效原子数")
            start = index + 1
            stop = start + atom_count
            if stop > len(lines):
                raise TrajectoryFrameError("LAMMPS 帧原子数据不完整")
            for raw_line in lines[start:stop]:
                parts = raw_line.split()
                if len(parts) < len(atom_columns):
                    raise TrajectoryFrameError("LAMMPS 帧原子列数不完整")
                atom_rows.append(dict(zip(atom_columns, parts)))
            index = stop
            continue
        index += 1
    if timestep is None or atom_count is None or not atom_columns:
        raise TrajectoryFrameError("无法识别索引中的 LAMMPS 轨迹帧")
    if len(atom_rows) != atom_count:
        raise TrajectoryFrameError("LAMMPS 帧原子数与数据行不一致")
    return {
        "frame": timestep,
        "box_header": box_header,
        "box_lines": box_lines,
        "atom_columns": atom_columns,
        "atom_rows": atom_rows,
    }


def read_lammps_frame_block(
    block: bytes,
    *,
    type_element_map: Mapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """Parse one indexed text-dump frame with ASE cell/PBC semantics."""
    np, atomic_numbers, _find_mic, ase_read = _ase_api()
    text = block.decode("utf-8", errors="strict")
    raw = _raw_frame_metadata(text)
    mapping = normalize_type_element_map(type_element_map)
    atom_rows = raw["atom_rows"]
    atom_columns = set(raw["atom_columns"])

    specorder: list[str] | None = None
    if "element" not in atom_columns and "type" in atom_columns:
        try:
            max_type = max(int(row["type"]) for row in atom_rows)
        except (KeyError, ValueError) as exc:
            raise TrajectoryFrameError("LAMMPS atom type 必须为整数") from exc
        if max_type <= 0:
            raise TrajectoryFrameError("LAMMPS atom type 必须为正整数")
        specorder = [mapping.get(str(value), "X") for value in range(1, max_type + 1)]

    try:
        ase_atoms = ase_read(
            StringIO(text),
            format="lammps-dump-text",
            index=0,
            order=False,
            specorder=specorder,
        )
    except Exception as exc:
        raise TrajectoryFrameError(f"ASE 无法解析 LAMMPS 帧: {exc}") from exc
    if len(ase_atoms) != len(atom_rows):
        raise TrajectoryFrameError("ASE 帧原子数与原始 LAMMPS 数据不一致")

    positions = np.asarray(ase_atoms.get_positions(), dtype=float)
    atoms: dict[int, dict[str, Any]] = {}
    for row_index, (row, position) in enumerate(zip(atom_rows, positions)):
        try:
            atom_id = int(float(row["id"]))
        except (KeyError, ValueError) as exc:
            raise TrajectoryFrameError("LAMMPS atom id 必须为整数") from exc
        if atom_id in atoms:
            raise TrajectoryFrameError(f"LAMMPS 帧包含重复 atom id: {atom_id}")
        atom_type = str(row.get("type") or "").strip()
        raw_element = _normalized_element(row.get("element"), atomic_numbers)
        element = raw_element or mapping.get(atom_type, "")
        atoms[atom_id] = {
            "id": atom_id,
            "type": atom_type,
            "element": element,
            "label": element or (f"T{atom_type}" if atom_type else f"A{atom_id}"),
            "x": float(position[0]),
            "y": float(position[1]),
            "z": float(position[2]),
            "source_index": row_index,
        }

    cell = np.asarray(ase_atoms.cell.array, dtype=float)
    pbc = np.asarray(ase_atoms.pbc, dtype=bool)
    celldisp = np.asarray(ase_atoms.get_celldisp(), dtype=float).reshape(-1)
    bounds: list[tuple[float, float]] = []
    for line in raw["box_lines"]:
        parts = line.split()
        try:
            bounds.append((float(parts[0]), float(parts[1])))
        except (IndexError, ValueError):
            bounds = []
            break
    return {
        "frame": int(raw["frame"]),
        "box": bounds,
        "box_header": raw["box_header"],
        "box_lines": raw["box_lines"],
        "cell": cell.tolist(),
        "cell_origin": celldisp[:3].tolist(),
        "pbc": pbc.tolist(),
        "atoms": atoms,
        "atom_columns": list(raw["atom_columns"]),
    }


def _mic(
    vectors: Any,
    *,
    cell: Any,
    pbc: Any,
) -> tuple[Any, Any]:
    np, _atomic_numbers, find_mic, _read = _ase_api()
    values = np.asarray(vectors, dtype=float)
    periodic = np.asarray(pbc, dtype=bool)
    if not periodic.any():
        return values, np.linalg.norm(values, axis=-1)
    matrix = np.asarray(cell, dtype=float)
    if matrix.shape != (3, 3) or abs(float(np.linalg.det(matrix))) < 1e-12:
        raise TrajectoryFrameError("周期轨迹帧缺少有效晶胞，无法计算局部环境")
    try:
        return find_mic(values, matrix, pbc=periodic)
    except Exception as exc:
        raise TrajectoryFrameError(f"无法应用最小镜像约定: {exc}") from exc


def select_local_environment(
    frame: Mapping[str, Any],
    participant_ids: Iterable[int],
    *,
    radius: float = 4.0,
    max_environment_atoms: int = 500,
) -> dict[str, Any]:
    """Select a deterministic PBC-aware neighborhood around participants."""
    np, _atomic_numbers, _find_mic, _read = _ase_api()
    atoms = frame.get("atoms") or {}
    participant_set = {
        int(value) for value in participant_ids if int(value) in atoms
    }
    if not participant_set:
        raise TrajectoryFrameError("事件参与原子不在锚点轨迹帧中")
    radius_value = max(0.0, float(radius))
    cap = max(0, int(max_environment_atoms))
    ordered_ids = sorted(int(value) for value in atoms)
    positions = np.asarray(
        [[atoms[value][axis] for axis in ("x", "y", "z")] for value in ordered_ids],
        dtype=float,
    )
    minimum = np.full(len(ordered_ids), np.inf, dtype=float)
    by_id = {atom_id: index for index, atom_id in enumerate(ordered_ids)}
    for participant_id in sorted(participant_set):
        origin = positions[by_id[participant_id]]
        _vectors, lengths = _mic(
            positions - origin,
            cell=frame.get("cell"),
            pbc=frame.get("pbc"),
        )
        minimum = np.minimum(minimum, lengths)
    candidates = [
        (float(minimum[index]), atom_id)
        for index, atom_id in enumerate(ordered_ids)
        if atom_id not in participant_set and minimum[index] <= radius_value
    ]
    candidates.sort(key=lambda item: (item[0], item[1]))
    selected = candidates[:cap]
    return {
        "participant_ids": sorted(participant_set),
        "environment_ids": [atom_id for _distance, atom_id in selected],
        "radius": radius_value,
        "raw_environment_count": len(candidates),
        "selected_environment_count": len(selected),
        "truncated": len(selected) < len(candidates),
    }


def recentered_positions(
    frame: Mapping[str, Any],
    selected_ids: Iterable[int],
    core_ids: Iterable[int],
) -> dict[int, tuple[float, float, float]]:
    """Unwrap selected atoms around the reaction core and center the core."""
    np, _atomic_numbers, _find_mic, _read = _ase_api()
    atoms = frame.get("atoms") or {}
    selected = sorted({int(value) for value in selected_ids if int(value) in atoms})
    if not selected:
        return {}
    references = sorted({int(value) for value in core_ids if int(value) in atoms})
    if not references:
        references = [selected[0]]

    def position(atom_id: int) -> Any:
        atom = atoms[atom_id]
        return np.asarray([atom["x"], atom["y"], atom["z"]], dtype=float)

    reference_origin = position(references[0])
    unwrapped_references: dict[int, Any] = {}
    for atom_id in references:
        vector, _length = _mic(
            position(atom_id) - reference_origin,
            cell=frame.get("cell"),
            pbc=frame.get("pbc"),
        )
        unwrapped_references[atom_id] = reference_origin + np.asarray(vector)
    center = np.mean(list(unwrapped_references.values()), axis=0)

    result: dict[int, tuple[float, float, float]] = {}
    for atom_id in selected:
        source = position(atom_id)
        candidates: list[tuple[float, Any]] = []
        for reference_id in references:
            vector, length = _mic(
                source - position(reference_id),
                cell=frame.get("cell"),
                pbc=frame.get("pbc"),
            )
            candidates.append(
                (
                    float(length),
                    unwrapped_references[reference_id] + np.asarray(vector),
                )
            )
        _distance, unwrapped = min(candidates, key=lambda item: item[0])
        display = unwrapped - center
        result[atom_id] = tuple(float(value) for value in display)
    return result
