"""Inspect and explicitly prune ReacNet Scope persistent index files."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


INDEX_SUFFIXES = (
    ".sqlite3",
    ".sqlite3.building",
    ".trajectory-index.json",
    ".trajectory-index.json.building",
)


def _cache_root() -> Path:
    configured = os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip()
    if not configured:
        raise RuntimeError("REACNET_SCOPE_CACHE_DIR must be set for cache management")
    return Path(configured).expanduser().resolve()


def _entries(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.name.endswith(INDEX_SUFFIXES)
        ),
        key=lambda path: path.stat().st_mtime,
    )


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or prune persistent ReacNet Scope indexes")
    parser.add_argument("--prune-days", type=float, default=0.0, help="Select indexes older than this many days")
    parser.add_argument("--max-gb", type=float, default=0.0, help="Also prune oldest indexes until total size is below this limit")
    parser.add_argument("--apply", action="store_true", help="Actually delete selected files; default is a dry run")
    args = parser.parse_args()
    root = _cache_root()
    entries = _entries(root)
    sizes = {path: path.stat().st_size for path in entries}
    total = sum(sizes.values())
    print(f"Cache: {root}")
    print(f"Indexes: {len(entries)}")
    print(f"Total size: {_format_bytes(total)}")

    selected: set[Path] = set()
    now = time.time()
    if args.prune_days > 0:
        cutoff = now - args.prune_days * 86400.0
        selected.update(path for path in entries if path.stat().st_mtime < cutoff)
    remaining_total = total - sum(sizes[path] for path in selected)
    if args.max_gb > 0:
        limit = int(args.max_gb * 1024**3)
        for path in entries:
            if remaining_total <= limit:
                break
            if path in selected:
                continue
            selected.add(path)
            remaining_total -= sizes[path]

    if not selected:
        return 0
    action = "REMOVE" if args.apply else "WOULD REMOVE"
    for path in sorted(selected):
        print(f"{action}: {_format_bytes(sizes[path])} {path}")
    print(f"Selected: {len(selected)} file(s), {_format_bytes(sum(sizes[path] for path in selected))}")
    if args.apply:
        for path in selected:
            path.unlink(missing_ok=True)
    else:
        print("Dry run only; add --apply to delete the selected index files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
