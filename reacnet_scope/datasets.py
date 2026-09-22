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
)


def discover_dataset_candidates(directory: str | Path) -> list[dict[str, Any]]:
    """Group recognized dataset artifacts without reading their contents."""

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"dataset folder not found: {root}")
    from .file_collections import collection_root, read_collection, COLLECTION_SUFFIX
    if root == collection_root():
        candidates = []
        for number, reference in enumerate(root.glob(f"*{COLLECTION_SUFFIX}")):
            if number >= 500:
                break
            record = read_collection(str(reference))
            if record:
                candidates.append({"folder": str(root), "base": str(reference),
                    "label": record["label"], "collection_id": record["dataset_id"],
                    "artifact_paths": record["artifact_paths"], "kinds": sorted(record["artifact_paths"]),
                    "score": len(record["artifact_paths"]), "mtime": reference.stat().st_mtime})
        return sorted(candidates, key=lambda item: item["label"])
    groups: dict[str, dict[str, tuple[Path, float]]] = defaultdict(dict)
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                mtime = entry.stat(follow_symlinks=False).st_mtime
            except (FileNotFoundError, OSError):
                # Source artifacts can disappear while a live simulation or
                # cleanup job is updating the directory.  One vanished entry
                # must not invalidate every other Dataset Candidate.
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
                    groups[base][kind] = (Path(entry.path), mtime)
                    break
    candidates = [
        {
            "folder": str(root),
            "base": base,
            "label": Path(base).name,
            "kinds": sorted(artifacts),
            "artifact_paths": {
                kind: str(path)
                for kind, (path, _mtime) in artifacts.items()
            },
            "score": len(artifacts),
            "mtime": max(mtime for _path, mtime in artifacts.values()),
        }
        for base, artifacts in groups.items()
    ]
    return sorted(
        candidates,
        key=lambda item: (
            str(item["label"]).casefold(),
            str(item["label"]),
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
