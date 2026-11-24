"""Deterministic runtime provenance and explicit LAMMPS unit parsing."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any


_LAMMPS_UNITS: dict[str, tuple[str | None, float | None, str | None]] = {
    # style -> (time_unit, ps_per_time_unit, length_unit)
    "metal": ("ps", 1.0, "angstrom"),
    "real": ("fs", 1e-3, "angstrom"),
    "electron": ("fs", 1e-3, "bohr"),
    "micro": ("us", 1e6, "micrometer"),
    "nano": ("ns", 1e3, "nanometer"),
    "si": ("s", 1e12, "meter"),
    "cgs": ("s", 1e12, "centimeter"),
    "lj": (None, None, None),
}


def sha256_file(path: str | Path) -> str:
    """Return the lowercase hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_lammps_input_units(input_path: str | Path) -> dict[str, Any]:
    """Parse explicit ``units`` and ``timestep`` directives from a LAMMPS script.

    This helper never guesses physical units: if ``units`` or ``timestep`` is
    absent or uses an unresolved variable (e.g. ``${DT}``), the corresponding
    converted field remains ``None`` and is explicitly reported in ``status``.
    """
    path = Path(input_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"LAMMPS input file not found: {path}")

    units_style: str | None = None
    raw_timestep: float | None = None
    raw_timestep_token: str | None = None

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        keyword = tokens[0].lower()
        if keyword == "units":
            units_style = tokens[1].lower()
        elif keyword == "timestep":
            raw_timestep_token = tokens[1]
            try:
                parsed = float(tokens[1])
            except ValueError:
                raw_timestep = None
            else:
                raw_timestep = parsed if parsed > 0 else None

    time_unit, ps_factor, length_unit = _LAMMPS_UNITS.get(
        units_style or "", (None, None, None)
    )
    timestep_ps = (
        raw_timestep * ps_factor
        if raw_timestep is not None and ps_factor is not None
        else None
    )

    if timestep_ps is not None and length_unit is not None:
        status = "confirmed_from_lammps_input"
    elif units_style is not None or raw_timestep_token is not None:
        status = "partial_from_lammps_input"
    else:
        status = "unparsed_lammps_input"

    return {
        "status": status,
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "units_style": units_style,
        "raw_timestep": raw_timestep,
        "raw_timestep_token": raw_timestep_token,
        "time_unit": time_unit,
        "timestep_ps": timestep_ps,
        "coordinate_length_unit": length_unit,
    }


@lru_cache(maxsize=1)
def _git_provenance() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / ".git").exists():
        return {"git_head": None, "git_dirty": None}
    try:
        head = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return {"git_head": None, "git_dirty": None}
    return {
        "git_head": head or None,
        "git_dirty": bool(status.strip()),
    }


def collect_runtime_provenance(
    *, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return deterministic software provenance metadata for reproducible exports."""
    try:
        version = pkg_version("reacnet-scope")
    except PackageNotFoundError:
        version = "0.1.0"
    payload: dict[str, Any] = {
        "software": "reacnet-scope",
        "package_version": version,
        **_git_provenance(),
    }
    if extra:
        payload.update({str(k): v for k, v in extra.items()})
    return payload


__all__ = [
    "collect_runtime_provenance",
    "parse_lammps_input_units",
    "sha256_file",
]
