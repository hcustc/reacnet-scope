"""Unified offline data preparation command for ReacNet Scope."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .indexes import (
    clear_index,
    resolve_dataset_paths,
    ROUTE_INDEX_STORE,
    TRAJECTORY_INDEX_STORE,
)
from .rng_events import event_output_status


def discover_dataset(case: str, base: str = "") -> dict[str, str]:
    root = Path(case).expanduser().resolve()
    if root.is_file():
        stem = str(root)
    elif root.is_dir():
        candidates = sorted(root.glob("*.reactionabcd"))
        if base:
            stem = str((root / base).resolve()) if not os.path.isabs(base) else str(Path(base).resolve())
        elif len(candidates) == 1:
            stem = str(candidates[0])[: -len(".reactionabcd")]
        else:
            routes = sorted(root.glob("*.route"))
            stems = {str(path)[: -len(".route")] for path in routes}
            if len(stems) != 1:
                raise RuntimeError("dataset directory is ambiguous; pass --base")
            stem = stems.pop()
    else:
        raise FileNotFoundError(f"dataset path not found: {root}")
    if stem.endswith(".reactionabcd"):
        stem = stem[: -len(".reactionabcd")]
    if stem.endswith(".route"):
        stem = stem[: -len(".route")]
    return {
        "base": stem,
        "reaction": f"{stem}.reactionabcd",
        "species": f"{stem}.species",
        "table": f"{stem}.table",
        "route": f"{stem}.route",
        "trajectory": stem,
        "reactionevent": f"{stem}.reactionevent.csv",
        "molecules": f"{stem}.molecules.csv",
    }


def _manifest_path(dataset: dict[str, str]) -> Path:
    paths = resolve_dataset_paths(Path(dataset["base"]).parent, Path(dataset["base"]).name)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    return paths.manifest


def build_manifest(dataset: dict[str, str]) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for kind in ("reaction", "species", "table", "route", "trajectory", "reactionevent", "molecules"):
        path = Path(dataset[kind])
        item: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            stat = path.stat()
            item.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        artifacts[kind] = item
    route_status = ROUTE_INDEX_STORE.status(dataset["route"]) if artifacts["route"]["exists"] else {"state": "missing"}
    trajectory_status = (
        TRAJECTORY_INDEX_STORE.status(dataset["trajectory"])
        if artifacts["trajectory"]["exists"]
        else {"state": "missing"}
    )
    return {
        "manifest_version": 1,
        "dataset_id": resolve_dataset_paths(Path(dataset["base"]).parent, Path(dataset["base"]).name).dataset_id,
        "base": dataset["base"],
        "updated_at_epoch": int(time.time()),
        "artifacts": artifacts,
        "indexes": {
            "route": route_status,
            "trajectory": trajectory_status,
            "rng_events": event_output_status(dataset["reactionevent"], dataset["molecules"]),
        },
    }


def write_manifest(dataset: dict[str, str]) -> Path:
    target = _manifest_path(dataset)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(build_manifest(dataset), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def _capacity_check(
    dataset: dict[str, str],
    *,
    include_route: bool,
    include_trajectory: bool,
) -> None:
    cache = Path(os.environ["REACNET_SCOPE_CACHE_DIR"]).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(cache).free
    required = 1024**3
    if include_route and Path(dataset["route"]).is_file():
        # Building and rebuilding may temporarily coexist with the published
        # database. SQLite secondary indexes can be larger than raw rows.
        required += int(Path(dataset["route"]).stat().st_size * 2.5)
    if include_trajectory and Path(dataset["trajectory"]).is_file():
        required += max(256 * 1024**2, Path(dataset["trajectory"]).stat().st_size // 100)
    if free < required:
        raise RuntimeError(
            f"insufficient cache capacity: need about {required / 1024**3:.1f} GiB, "
            f"have {free / 1024**3:.1f} GiB at {cache}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare persistent ReacNet Scope indexes offline")
    parser.add_argument("case", help="Dataset directory or dataset base path")
    parser.add_argument("--base", default="", help="Dataset base name when a directory contains multiple runs")
    parser.add_argument("--status", action="store_true", help="Inspect state without building")
    parser.add_argument("--route-only", action="store_true")
    parser.add_argument("--trajectory-only", action="store_true")
    parser.add_argument("--rebuild", choices=("route", "trajectory", "all"))
    parser.add_argument("--clear", choices=("route", "trajectory", "all"))
    args = parser.parse_args(argv)
    if not os.environ.get("REACNET_SCOPE_CACHE_DIR", "").strip():
        parser.error("REACNET_SCOPE_CACHE_DIR must be set")
    dataset = discover_dataset(args.case, args.base)
    # RNG-authored event files replace Route reconstruction in the normal
    # workflow.  Route preparation remains explicit for compatibility only.
    selected_route = bool(args.route_only or args.rebuild in {"route", "all"})
    selected_trajectory = bool(not args.route_only or args.rebuild in {"trajectory", "all"})
    route_needs_build = (
        selected_route
        and Path(dataset["route"]).is_file()
        and ROUTE_INDEX_STORE.status(dataset["route"])["state"] != "ready"
    )
    trajectory_needs_build = (
        selected_trajectory
        and Path(dataset["trajectory"]).is_file()
        and TRAJECTORY_INDEX_STORE.status(dataset["trajectory"])["state"] != "ready"
    )
    if not args.status and not args.clear:
        _capacity_check(
            dataset,
            include_route=route_needs_build,
            include_trajectory=trajectory_needs_build,
        )

    def report(update: dict[str, Any]) -> None:
        print(f"[{float(update.get('progress', 0.0)) * 100:6.2f}%] {update.get('message', '')}", flush=True)

    if args.clear or args.rebuild:
        target = args.clear or args.rebuild
        if target in {"route", "all"} and Path(dataset["route"]).is_file():
            clear_index(dataset["route"], kind="route")
        if target in {"trajectory", "all"} and Path(dataset["trajectory"]).is_file():
            clear_index(dataset["trajectory"], kind="trajectory")
        if args.clear:
            print(f"Manifest: {write_manifest(dataset)}")
            return 0
    if not args.status:
        try:
            if selected_route:
                if not Path(dataset["route"]).is_file():
                    raise FileNotFoundError(f"Route file not found: {dataset['route']}")
                ROUTE_INDEX_STORE.build(dataset["route"], progress_callback=report)
            if selected_trajectory:
                if not Path(dataset["trajectory"]).is_file():
                    raise FileNotFoundError(f"trajectory file not found: {dataset['trajectory']}")
                TRAJECTORY_INDEX_STORE.build(dataset["trajectory"], progress_callback=report)
        except KeyboardInterrupt:
            print("Preparation canceled; committed checkpoints were preserved.")
            write_manifest(dataset)
            return 130
    manifest = write_manifest(dataset)
    print(json.dumps(build_manifest(dataset)["indexes"], ensure_ascii=False, indent=2))
    print(f"Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
