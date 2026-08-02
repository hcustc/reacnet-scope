"""Deterministic evidence-package exports for one indexed RNG event.

The package builder consumes an already extracted viewer payload.  It never
opens a trajectory or an event source, so Dash and CLI callers retain the
bounded-I/O guarantees of the prepared indexes.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .trajectory import TrajectoryDependencyError, normalize_type_element_map


EVENT_PACKAGE_SCHEMA_VERSION = "reacnet-scope/event-package/v1"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MEMBER_ORDER = (
    "event.json",
    "trajectory.lammpstrj",
    "trajectory.extxyz",
    "bonds.csv",
    "README.txt",
)


class EventPackageError(ValueError):
    """Raised when a viewer payload cannot produce a valid event package."""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _normalized_scope(scope: str) -> str:
    normalized = str(scope or "participants").strip().lower()
    if normalized == "context":
        normalized = "environment"
    if normalized not in {"core", "participants", "environment"}:
        raise EventPackageError(
            "event package scope must be core, participants, or environment"
        )
    return normalized


def _scope_atom_ids(view: Mapping[str, Any], scope: str) -> list[int]:
    groups = view.get("atom_groups") or {}
    if scope == "core":
        values = groups.get("core") or []
    elif scope == "participants":
        values = (
            groups.get("participants")
            or [
                *(groups.get("reactant") or []),
                *(groups.get("product") or []),
            ]
        )
    else:
        values = (
            groups.get("context")
            or [
                *(groups.get("participants") or []),
                *(groups.get("environment") or []),
            ]
        )
    selected = sorted({int(value) for value in values})
    if not selected:
        raise EventPackageError(f"event package scope {scope!r} has no atoms")
    return selected


def _effective_type_map(
    view: Mapping[str, Any],
    override: Mapping[Any, Any] | None,
) -> dict[str, str]:
    stored = ((view.get("meta") or {}).get("type_element_map") or {})
    combined = {str(key): str(value) for key, value in stored.items()}
    if override is not None:
        combined.update({str(key): str(value) for key, value in override.items()})
    return normalize_type_element_map(combined)


def _selected_frames(
    view: Mapping[str, Any],
    *,
    atom_ids: list[int],
    type_element_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    selected = set(atom_ids)
    frames: list[dict[str, Any]] = []
    for source_frame in sorted(
        view.get("frames") or [], key=lambda item: int(item.get("frame") or 0)
    ):
        atoms: list[dict[str, Any]] = []
        for source_atom in sorted(
            source_frame.get("atoms") or [],
            key=lambda item: int(item.get("id") or 0),
        ):
            atom_id = int(source_atom.get("id") or 0)
            if atom_id not in selected:
                continue
            atom = dict(source_atom)
            if not str(atom.get("element") or "").strip():
                atom["element"] = type_element_map.get(
                    str(atom.get("type") or "").strip(), ""
                )
            atoms.append(atom)
        if atoms:
            frames.append({**dict(source_frame), "atoms": atoms})
    if not frames:
        raise EventPackageError("event package contains no selected trajectory frames")
    return frames


def event_trajectory_text(
    view: Mapping[str, Any],
    *,
    scope: str = "environment",
    atom_type_map: Mapping[Any, Any] | None = None,
) -> str:
    """Serialize selected original Cartesian coordinates as a LAMMPS dump."""
    normalized_scope = _normalized_scope(scope)
    mapping = _effective_type_map(view, atom_type_map)
    frames = _selected_frames(
        view,
        atom_ids=_scope_atom_ids(view, normalized_scope),
        type_element_map=mapping,
    )
    chunks: list[str] = []
    for frame in frames:
        atoms = frame["atoms"]
        box_header = str(frame.get("box_header") or "").strip()
        box_lines = [str(value).strip() for value in frame.get("box_lines") or []]
        if not box_header or len(box_lines) != 3:
            bounds = list(frame.get("box") or [])
            while len(bounds) < 3:
                bounds.append((0.0, 1.0))
            box_header = "ITEM: BOX BOUNDS pp pp pp"
            box_lines = [
                f"{float(pair[0]):.10g} {float(pair[1]):.10g}"
                for pair in bounds[:3]
            ]
        complete_elements = all(
            str(atom.get("element") or "").strip() for atom in atoms
        )
        atom_header = (
            "ITEM: ATOMS id type element x y z"
            if complete_elements
            else "ITEM: ATOMS id type x y z"
        )
        chunks.extend(
            [
                "ITEM: TIMESTEP",
                str(int(frame.get("frame") or 0)),
                "ITEM: NUMBER OF ATOMS",
                str(len(atoms)),
                box_header,
                *box_lines,
                atom_header,
            ]
        )
        for atom in atoms:
            fields = [
                str(int(atom.get("id") or 0)),
                str(atom.get("type") or "0"),
            ]
            if complete_elements:
                fields.append(str(atom.get("element") or "X"))
            fields.extend(
                f"{float(atom.get(axis, 0.0)):.10g}" for axis in ("x", "y", "z")
            )
            chunks.append(" ".join(fields))
    return "\n".join(chunks) + "\n"


def _extxyz_text(frames: list[dict[str, Any]], event_id: str) -> str:
    try:
        import numpy as np
        from ase import Atoms
        from ase.io import write as ase_write
    except ImportError as exc:  # pragma: no cover - optional dependency gate
        raise TrajectoryDependencyError(
            "事件 ExtXYZ 导出需要 ASE；请运行 "
            "uv sync --extra web --extra trajectory"
        ) from exc

    images = []
    for frame in frames:
        atoms_data = frame["atoms"]
        symbols = [str(atom["element"]) for atom in atoms_data]
        positions = [
            [
                float(atom.get(f"display_{axis}", atom.get(axis, 0.0)))
                for axis in ("x", "y", "z")
            ]
            for atom in atoms_data
        ]
        cell = frame.get("cell") or [[0.0, 0.0, 0.0]] * 3
        pbc = frame.get("pbc") or [False, False, False]
        image = Atoms(symbols=symbols, positions=positions, cell=cell, pbc=pbc)
        image.new_array(
            "original_id",
            np.asarray([int(atom.get("id") or 0) for atom in atoms_data], dtype=int),
        )
        image.new_array(
            "original_type",
            np.asarray(
                [str(atom.get("type") or "") for atom in atoms_data], dtype="U32"
            ),
        )
        image.new_array(
            "event_group",
            np.asarray(
                [str(atom.get("group") or "") for atom in atoms_data], dtype="U32"
            ),
        )
        image.info["timestep"] = int(frame.get("frame") or 0)
        image.info["bond_state"] = str(frame.get("bond_state") or "unknown")
        image.info["event_id"] = event_id
        images.append(image)
    output = io.StringIO()
    try:
        ase_write(output, images, format="extxyz")
    except Exception as exc:
        raise EventPackageError(f"ASE could not serialize event ExtXYZ: {exc}") from exc
    return output.getvalue()


def _parse_bond(value: Any) -> tuple[int, int, str]:
    parts = str(value or "").split("-")
    if len(parts) < 3:
        raise EventPackageError(f"invalid RNG bond identifier: {value!r}")
    try:
        atom1, atom2 = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise EventPackageError(f"invalid RNG bond identifier: {value!r}") from exc
    return min(atom1, atom2), max(atom1, atom2), "-".join(parts[2:])


def _bonds_csv(view: Mapping[str, Any]) -> str:
    evidence = view.get("bond_evidence") or {}
    broken = {str(value) for value in evidence.get("broken") or []}
    formed = {str(value) for value in evidence.get("formed") or []}
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["state", "atom1", "atom2", "bond_order", "change"])
    for state in ("reactant", "product"):
        values = sorted(
            {str(value) for value in evidence.get(state) or []},
            key=_parse_bond,
        )
        for value in values:
            atom1, atom2, order = _parse_bond(value)
            change = (
                "broken"
                if state == "reactant" and value in broken
                else "formed"
                if state == "product" and value in formed
                else "unchanged"
            )
            writer.writerow([state, atom1, atom2, order, change])
    return output.getvalue()


def _readme_text(
    *,
    event_id: str,
    scope: str,
    extxyz_included: bool,
) -> str:
    extxyz_note = (
        "trajectory.extxyz is included and uses reaction-core-centered, "
        "minimum-image coordinates."
        if extxyz_included
        else "trajectory.extxyz is omitted because at least one selected atom "
        "does not have a confirmed element mapping."
    )
    return (
        "ReacNet Scope event evidence package\n"
        "===================================\n\n"
        f"Event: {event_id}\n"
        f"Scope: {scope}\n\n"
        "trajectory.lammpstrj contains ASE-parsed Cartesian coordinates before "
        "viewer re-centering and retains the source cell bounds.\n"
        f"{extxyz_note}\n"
        "Bond changes in bonds.csv come from ReacNetGenerator event evidence; "
        "they are not guessed from coordinates.\n"
        "event.json records source signatures, selection parameters, coordinate "
        "treatment, and atom IDs for audit.\n\n"
        "Open the LAMMPS subset:\n"
        "  ovito trajectory.lammpstrj\n"
        "Inspect the mapped ExtXYZ when present:\n"
        "  python -c \"from ase.io import read; print(len(read('trajectory.extxyz', ':')))\"\n"
    )


def _zip_member(name: str, payload: bytes) -> tuple[ZipInfo, bytes]:
    info = ZipInfo(filename=name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, payload


def build_event_package(
    view: Mapping[str, Any],
    *,
    scope: str = "participants",
    source_signatures: Mapping[str, Any] | None = None,
    atom_type_map: Mapping[Any, Any] | None = None,
) -> bytes:
    """Build a reproducible ZIP without reopening any source data."""
    normalized_scope = _normalized_scope(scope)
    event_id = str(view.get("event_id") or "").strip()
    if not event_id:
        raise EventPackageError("event package requires an event_id")
    mapping = _effective_type_map(view, atom_type_map)
    atom_ids = _scope_atom_ids(view, normalized_scope)
    frames = _selected_frames(
        view,
        atom_ids=atom_ids,
        type_element_map=mapping,
    )
    complete_elements = all(
        str(atom.get("element") or "").strip()
        for frame in frames
        for atom in frame["atoms"]
    )
    extxyz = _extxyz_text(frames, event_id) if complete_elements else ""
    lammps = event_trajectory_text(
        view,
        scope=normalized_scope,
        atom_type_map=mapping,
    )
    signatures = (
        source_signatures
        if source_signatures is not None
        else (view.get("source_signatures") or {})
    )
    meta = view.get("meta") or {}
    event = dict(view.get("event") or {})
    event.setdefault("event_id", event_id)
    event.setdefault("reaction_smiles", str(meta.get("reaction_smiles") or ""))
    event.setdefault(
        "association_status", str(meta.get("verification_status") or "")
    )
    document = {
        "schema_version": EVENT_PACKAGE_SCHEMA_VERSION,
        "event": _json_safe(event),
        "selection": {
            "scope": normalized_scope,
            "atom_ids": atom_ids,
            "atom_groups": _json_safe(view.get("atom_groups") or {}),
            "extraction_parameters": _json_safe(meta.get("extraction") or {}),
            "environment": _json_safe(meta.get("environment") or {}),
        },
        "frames": [
            {
                "timestep": int(frame.get("frame") or 0),
                "atom_count": len(frame["atoms"]),
                "bond_state": str(frame.get("bond_state") or ""),
            }
            for frame in frames
        ],
        "bond_changes": _json_safe(view.get("bond_evidence") or {}),
        "atom_mapping": {
            "rng_atom_id_base": 0,
            "trajectory_atom_id_base": 1,
            "rng_to_trajectory": [
                {
                    "rng_atom_id": atom_id - 1,
                    "trajectory_atom_id": atom_id,
                }
                for atom_id in atom_ids
            ],
            "type_to_element": mapping,
        },
        "source_signatures": _json_safe(signatures),
        "coordinate_treatment": {
            "trajectory_lammpstrj": (
                "ASE-parsed Cartesian source positions before viewer re-centering"
            ),
            "trajectory_extxyz": (
                "minimum-image, reaction-core-centered viewer positions"
                if complete_elements
                else "omitted: incomplete element mapping"
            ),
        },
        "type_element_map": mapping,
        "extxyz_included": complete_elements,
    }
    payloads: dict[str, bytes] = {
        "event.json": (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
        "trajectory.lammpstrj": lammps.encode("utf-8"),
        "bonds.csv": _bonds_csv(view).encode("utf-8"),
        "README.txt": _readme_text(
            event_id=event_id,
            scope=normalized_scope,
            extxyz_included=complete_elements,
        ).encode("utf-8"),
    }
    if complete_elements:
        payloads["trajectory.extxyz"] = extxyz.encode("utf-8")

    output = io.BytesIO()
    with ZipFile(output, mode="w") as archive:
        for name in _MEMBER_ORDER:
            if name not in payloads:
                continue
            info, payload = _zip_member(name, payloads[name])
            archive.writestr(info, payload)
    return output.getvalue()


__all__ = [
    "EVENT_PACKAGE_SCHEMA_VERSION",
    "EventPackageError",
    "build_event_package",
    "event_trajectory_text",
]
