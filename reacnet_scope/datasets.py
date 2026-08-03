"""Pure discovery helpers for ReacNetGenerator dataset artifacts."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ARTIFACT_SUFFIXES = (
    (".timeline.h5", "timeline"),
    (".reactionevent.csv", "reactionevent"),
    (".molecules.csv", "molecules"),
    (".reactionabcd", "reaction"),
    (".lammpstrj", "trajectory"),
    (".species", "species"),
    (".moname", "moname"),
    (".route", "route"),
)


def discover_dataset_candidates(directory: str | Path) -> list[dict[str, Any]]:
    """Group recognized dataset artifacts without reading their contents."""

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"dataset folder not found: {root}")
    groups: dict[str, dict[str, Path]] = defaultdict(dict)
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            # Removable media copied from macOS commonly contains AppleDouble
            # sidecars such as ``._run.lammpstrj.species``.  They mirror real
            # RNG suffixes but are metadata, not a second dataset.
            if entry.name.startswith("._"):
                continue
            lower_name = entry.name.lower()
            for suffix, kind in ARTIFACT_SUFFIXES:
                if lower_name.endswith(suffix):
                    base = str(root / entry.name[: -len(suffix)])
                    if kind == "trajectory":
                        base = str(root / entry.name)
                    groups[base][kind] = Path(entry.path)
                    break
    candidates = [
        {
            "folder": str(root),
            "base": base,
            "label": Path(base).name,
            "kinds": sorted(paths),
            "artifact_paths": {kind: str(path) for kind, path in paths.items()},
            "score": len(paths),
            "mtime": max(path.stat().st_mtime for path in paths.values()),
        }
        for base, paths in groups.items()
    ]
    return sorted(
        candidates,
        key=lambda item: (
            -int(item["score"]),
            -float(item["mtime"]),
            str(item["label"]).casefold(),
        ),
    )


def choose_dataset_candidate(
    candidates: Iterable[dict[str, Any]], preferred_base: str = ""
) -> dict[str, Any] | None:
    """Choose the only candidate or an explicitly preferred absolute base."""

    candidate_list = list(candidates)
    if len(candidate_list) == 1:
        return candidate_list[0]
    if not preferred_base:
        return None
    return next(
        (
            candidate
            for candidate in candidate_list
            if str(candidate.get("base", "")) == preferred_base
        ),
        None,
    )
