"""Generic tidy-table adapters for Species Abundance Evidence."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

import pandas as pd


FormulaParser = Callable[[str], Mapping[str, int]]
SpeciesResolver = Callable[[str], str]
ProgressCallback = Callable[[Mapping[str, Any]], None]
_FORMULA_PATTERN = re.compile(r"([A-Z][a-z]?)(\d*)")


def parse_formula_to_atom_counts(
    species: str,
    parser: FormulaParser | None = None,
) -> dict[str, int]:
    """Parse and strictly validate one molecular formula."""
    formula = str(species or "").strip()
    if not formula:
        raise ValueError("Encountered an empty molecular formula")
    if parser is None:
        matches = list(_FORMULA_PATTERN.finditer(formula))
        if not matches or "".join(match.group(0) for match in matches) != formula:
            raise ValueError(f"Invalid molecular formula: {formula!r}")
        raw: Mapping[str, int] = {
            element: sum(
                int(match.group(2) or 1)
                for match in matches
                if match.group(1) == element
            )
            for element in {match.group(1) for match in matches}
        }
    else:
        raw = parser(formula)
    if not isinstance(raw, Mapping):
        raise TypeError("Formula parser must return a mapping")
    counts: dict[str, int] = {}
    for element, value in raw.items():
        symbol = str(element)
        if not re.fullmatch(r"[A-Z][a-z]?", symbol):
            raise ValueError(f"Invalid element symbol: {symbol!r}")
        if isinstance(value, bool) or int(value) != value or int(value) < 0:
            raise ValueError(f"Invalid atom count for {symbol}: {value!r}")
        if int(value) > 0:
            counts[symbol] = int(value)
    if not counts:
        raise ValueError(f"Formula has no atoms: {formula!r}")
    return counts


def _parse_species_line(text: str) -> tuple[int, list[tuple[str, int]]] | None:
    stripped = text.strip()
    if not stripped.startswith("Timestep ") or ":" not in stripped:
        return None
    prefix, body = stripped.split(":", 1)
    try:
        timestep = int(prefix.split()[1])
    except (IndexError, ValueError):
        return None
    tokens = body.split()
    pairs: list[tuple[str, int]] = []
    for index in range(0, len(tokens) - 1, 2):
        try:
            pairs.append((tokens[index], int(tokens[index + 1])))
        except ValueError:
            continue
    return timestep, pairs


def species_file_to_tidy_table(
    species_file: str | Path,
    *,
    time_axis: Literal["step", "ps", "ns"] = "step",
    timestep_ps: float | None = None,
    species_resolver: SpeciesResolver | None = None,
    system: str | None = None,
    replicate: str | int | None = None,
    time_col: str = "time",
    species_col: str = "species",
    count_col: str = "count",
    progress_callback: ProgressCallback | None = None,
) -> pd.DataFrame:
    """Stream one RNG ``.species`` source into a generic tidy table."""
    path = Path(species_file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if time_axis not in {"step", "ps", "ns"}:
        raise ValueError("time_axis must be step, ps, or ns")
    if time_axis != "step" and (timestep_ps is None or float(timestep_ps) <= 0):
        raise ValueError("physical time requires a positive timestep_ps conversion")
    resolver = species_resolver or (lambda value: value)
    cache: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    parsed_timesteps = 0
    file_size = max(path.stat().st_size, 1)
    bytes_read = 0
    last_emit = 0.0
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            bytes_read += len(raw_line.encode("utf-8"))
            parsed = _parse_species_line(raw_line)
            if parsed is None:
                continue
            timestep, pairs = parsed
            parsed_timesteps += 1
            merged: dict[str, int] = {}
            for raw_species, count in pairs:
                if raw_species not in cache:
                    cache[raw_species] = str(resolver(raw_species)).strip()
                species = cache[raw_species]
                if species:
                    merged[species] = merged.get(species, 0) + int(count)
            if time_axis == "step":
                time_value: int | float = timestep
            else:
                time_value = float(timestep) * float(timestep_ps)
                if time_axis == "ns":
                    time_value /= 1000.0
            for species, count in merged.items():
                row: dict[str, Any] = {
                    time_col: time_value,
                    species_col: species,
                    count_col: count,
                    "frame": timestep,
                }
                if system is not None:
                    row["system"] = system
                if replicate is not None:
                    row["replicate"] = replicate
                rows.append(row)
            now = time.monotonic()
            if progress_callback and now - last_emit >= 1.0:
                progress_callback(
                    {
                        "progress": min(bytes_read / file_size, 1.0),
                        "phase": "reading_species",
                        "message": f"Reading {path.name}",
                        "timesteps": parsed_timesteps,
                        "rows": len(rows),
                        "frame": timestep,
                    }
                )
                last_emit = now
    if parsed_timesteps == 0:
        raise ValueError(f"No valid timestep rows found in {path}")
    table = pd.DataFrame.from_records(rows)
    if table.empty:
        raise ValueError(f"No species rows found in {path}")
    return table.sort_values([time_col, "frame", species_col]).reset_index(drop=True)
