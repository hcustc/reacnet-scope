"""Build a durable frame-offset index for a LAMMPS trajectory."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.webapp.server import TRAJECTORY_INDEX_STORE, trajectory_frame_index_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a persistent ReacNet Scope trajectory frame index")
    parser.add_argument("trajectory", help="Path to a .lammpstrj trajectory")
    args = parser.parse_args()
    trajectory = str(Path(args.trajectory).expanduser().resolve())

    def report(update: dict) -> None:
        progress = float(update.get("progress", 0.0) or 0.0) * 100.0
        print(f"[{progress:5.1f}%] {update.get('message', '')}", flush=True)

    index = TRAJECTORY_INDEX_STORE.get(trajectory, progress_callback=report)
    print(
        f"Persistent index ready: {trajectory_frame_index_path(trajectory, mtime=index.mtime, size=index.size)} "
        f"({len(index.frames)} frames)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
