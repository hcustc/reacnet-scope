"""Build a durable frame-offset index for a LAMMPS trajectory."""

from __future__ import annotations

import argparse
from pathlib import Path

from reacnet_scope.indexes import TRAJECTORY_INDEX_STORE, trajectory_index_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a persistent ReacNet Scope trajectory frame index")
    parser.add_argument("trajectory", help="Path to a .lammpstrj trajectory")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true", help="Show index state without building")
    action.add_argument("--clear", action="store_true", help="Remove the current index/checkpoint and exit")
    action.add_argument("--rebuild", action="store_true", help="Remove the current index/checkpoint before building")
    args = parser.parse_args()
    trajectory = str(Path(args.trajectory).expanduser().resolve())

    def show_status() -> None:
        status = TRAJECTORY_INDEX_STORE.status(trajectory)
        print(f"State: {status['state']}")
        print(f"Trajectory: {status['trajectory_file']}")
        print(f"Cache: {status['cache_dir']}")
        print(f"Index: {status['index_path']}")
        print(f"Progress: {float(status['progress']) * 100:.2f}%")
        print(f"Frames: {status['frames']}")

    if args.status:
        show_status()
        return 0
    if args.clear or args.rebuild:
        removed = TRAJECTORY_INDEX_STORE.clear(trajectory)
        print(f"Removed {len(removed)} index file(s)")
        if args.clear:
            return 0

    def report(update: dict) -> None:
        progress = float(update.get("progress", 0.0) or 0.0) * 100.0
        print(f"[{progress:5.1f}%] {update.get('message', '')}", flush=True)

    try:
        index = TRAJECTORY_INDEX_STORE.build(trajectory, progress_callback=report)
    except KeyboardInterrupt:
        print("\nBuild canceled; the latest checkpoint was preserved.")
        show_status()
        return 130
    print(
        f"Persistent index ready: {trajectory_index_path(trajectory)} "
        f"({len(index.frames)} frames)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
