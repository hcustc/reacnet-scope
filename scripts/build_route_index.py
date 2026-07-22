"""Build, inspect, resume, or clear a persistent Route transition index."""

from __future__ import annotations

import argparse
from pathlib import Path

from reacnet_scope.indexes import ROUTE_INDEX_STORE


def _format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def _print_status(route: str) -> None:
    status = ROUTE_INDEX_STORE.status(route)
    print(f"State: {status['state']}")
    print(f"Route: {status['route_file']} ({_format_bytes(status['route_size'])})")
    print(f"Cache: {status['cache_dir']}")
    print(f"Index: {status['index_path']}")
    print(f"Index size: {_format_bytes(status['index_size'])}")
    print(f"Progress: {float(status['progress']) * 100:.2f}%")
    print(f"Scanned atoms: {status['scanned_atoms']}")
    print(f"Indexed transitions: {status['indexed_transitions']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a persistent, resumable ReacNet Scope Route index"
    )
    parser.add_argument("route", help="Path to a ReacNetGenerator .route file")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true", help="Show index state without building")
    action.add_argument("--clear", action="store_true", help="Remove the current index/checkpoint and exit")
    action.add_argument("--rebuild", action="store_true", help="Remove the current index/checkpoint before building")
    args = parser.parse_args()
    route = str(Path(args.route).expanduser().resolve())

    if args.status:
        _print_status(route)
        return 0
    if args.clear or args.rebuild:
        removed = ROUTE_INDEX_STORE.clear(route)
        print(f"Removed {len(removed)} index file(s)")
        if args.clear:
            return 0

    def report(update: dict) -> None:
        progress = float(update.get("progress", 0.0) or 0.0) * 100.0
        resumed = " [resumed]" if update.get("resumed") else ""
        print(f"[{progress:6.2f}%]{resumed} {update.get('message', '')}", flush=True)

    try:
        result = ROUTE_INDEX_STORE.build(route, progress_callback=report)
    except KeyboardInterrupt:
        print("\nBuild canceled; the latest committed checkpoint was preserved.")
        _print_status(route)
        return 130
    print(f"Persistent Route index ready: {result['index_path']}")
    _print_status(route)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
