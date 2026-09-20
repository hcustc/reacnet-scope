#!/usr/bin/env python3
"""Unified terminal query tool for ReacNetGenerator reaction datasets.

Common use cases:
1) Query all SMILES for a molecular formula
2) Query next-step reactions for a given SMILES (consumption/production)
3) Query reaction channels by formula equation (e.g. C6H4O2+C6H4->C12H8O2)
4) Compute TOP-N share from a CSV metric column
5) Export one indexed RNG event as a reproducible evidence ZIP
6) Verify an explicit time-ordered, exact-molecule, atom-continuous event path
7) Export one matched occurrence as auditable DFT initial geometries
8) Discover bounded, Step-Evidence-backed candidate routes from start Species
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = TOOL_ROOT.parent

if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from reacnet_scope.network import (  # noqa: E402
    Reaction,
    ReactionNetwork,
    parse_reactionabcd,
    smiles_to_formula_fast,
)


def detect_default_reaction_file() -> Path:
    env_path = os.getenv("RNG_REACTION_FILE", "").strip()
    candidates: List[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            PROJECT_ROOT / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
            TOOL_ROOT / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
            Path.cwd() / "datas" / "1ER_2500K" / "rng_data" / "2CP_O2_1ER.lammpstrj.reactionabcd",
        ]
    )
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


DEFAULT_REACTION_FILE = detect_default_reaction_file()

@dataclass
class MatchedReaction:
    role: str
    reaction: Reaction
    forward_tp: int
    reverse_tp: int
    net_tp: int
    ratio_pct: float


def reaction_formula_str(rxn: Reaction) -> str:
    return " + ".join(rxn.reactant_formulas) + " -> " + " + ".join(rxn.product_formulas)


def reaction_smiles_str(rxn: Reaction) -> str:
    return " + ".join(rxn.reactant_smiles) + " -> " + " + ".join(rxn.product_smiles)


def build_network(reac_file: str, min_tp: int) -> ReactionNetwork:
    if not os.path.exists(reac_file):
        raise FileNotFoundError(f"reactionabcd not found: {reac_file}")
    reactions = parse_reactionabcd(reac_file, min_tp=min_tp)
    if not reactions:
        raise RuntimeError(f"No reactions loaded from: {reac_file}")
    return ReactionNetwork(reactions)


def reverse_key(rxn: Reaction) -> str:
    return "+".join(sorted(rxn.product_smiles)) + "->" + "+".join(sorted(rxn.reactant_smiles))


def net_flux(rxn: Reaction, tp_map: dict[str, int]) -> tuple[int, int, int]:
    fwd = rxn.tp
    rev = tp_map.get(reverse_key(rxn), 0)
    return fwd, rev, fwd - rev


def write_csv(path: str, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def cmd_prepare(args: argparse.Namespace) -> int:
    """Run offline preparation through the supported unified CLI surface."""
    from reacnet_scope.prepare import run_preparation

    return run_preparation(
        action=str(args.prepare_action),
        capability=str(getattr(args, "capability", "all")),
        case=str(args.case),
        base=str(args.base or ""),
    )


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the supported Dash application from the unified CLI."""
    from scripts.webapp_dash.app import run_server

    return run_server(
        host=str(args.host),
        port=int(args.port),
        debug=bool(args.debug),
    )


def _element_filter(value: str) -> tuple[str, dict[str, int | str]]:
    """Parse ELEMENT=present|absent|range[:MIN[:MAX]]."""
    try:
        element, expression = value.split("=", 1)
        parts = expression.split(":")
        mode = parts[0]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "filter must use ELEMENT=present|absent|range[:MIN[:MAX]]"
        ) from exc
    if not re.fullmatch(r"[A-Z][a-z]?", element):
        raise argparse.ArgumentTypeError("filter element must be a chemical symbol")
    if mode not in {"present", "absent", "range"}:
        raise argparse.ArgumentTypeError("filter mode must be present, absent, or range")
    rule: dict[str, int | str] = {"mode": mode}
    if mode == "range":
        try:
            if len(parts) > 1 and parts[1] != "":
                rule["min"] = int(parts[1])
            if len(parts) > 2 and parts[2] != "":
                rule["max"] = int(parts[2])
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "range bounds must be integers"
            ) from exc
        if len(parts) > 3:
            raise argparse.ArgumentTypeError("range accepts at most MIN and MAX")
    elif len(parts) != 1:
        raise argparse.ArgumentTypeError(
            "present and absent filters do not accept bounds"
        )
    return element, rule


def _element_symbol(value: str) -> str:
    symbol = str(value).strip()
    if not re.fullmatch(r"[A-Z][a-z]?", symbol):
        raise argparse.ArgumentTypeError(
            "element must be a chemical symbol such as C, N, or Cl"
        )
    return symbol


def cmd_element_distribution(args: argparse.Namespace) -> int:
    """Query the prepared generic Element Distribution index."""
    from reacnet_scope.composition import build_element_distribution_model
    from reacnet_scope.prepare import discover_dataset

    try:
        dataset = discover_dataset(str(args.case), str(args.base or ""))
        species_files = {"current": dataset["species"]}
        for label, path in args.species_file or []:
            species_files[str(label)] = str(path)
        result = build_element_distribution_model(
            species_files=species_files,
            tidy_table=str(args.tidy_table or ""),
            max_points=int(args.max_points),
            group_element=str(args.group_element),
            max_group_count=int(args.max_group_count),
            element_filters={
                element: rule for element, rule in (args.filter or [])
            },
            include_zero=bool(args.include_zero),
            bin_width=int(args.bin_width),
            group_ranges=list(args.group_range or []),
            smooth_window=int(args.smooth_window),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    document = {
        "schema_version": 1,
        "group_element": str(args.group_element),
        "element_filters": {
            element: rule for element, rule in (args.filter or [])
        },
        "include_zero": bool(args.include_zero),
        "rows": result.get("rows") or [],
        "raw_rows": result.get("raw_rows") or [],
        "sources": result.get("sources") or [],
        "transform": result.get("transform") or {},
    }
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def _labelled_path(value: str) -> tuple[str, str]:
    label, separator, path = str(value).partition("=")
    if not separator or not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError("value must use LABEL=PATH")
    return label.strip(), path.strip()


def _group_range(value: str) -> dict[str, int | str | None]:
    label, separator, bounds = str(value).partition(":")
    if not separator or not label.strip():
        raise argparse.ArgumentTypeError("range must use LABEL:MIN:MAX")
    parts = bounds.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("range must use LABEL:MIN:MAX")
    try:
        minimum = int(parts[0]) if parts[0] else None
        maximum = int(parts[1]) if parts[1] else None
    except ValueError as exc:
        raise argparse.ArgumentTypeError("range bounds must be integers") from exc
    return {"label": label.strip(), "min": minimum, "max": maximum}


def cmd_events(args: argparse.Namespace) -> int:
    from reacnet_scope.event_index import EVENT_EVIDENCE_STORE
    from reacnet_scope.prepare import discover_dataset
    from reacnet_scope.timed_evidence import select_timed_evidence

    try:
        dataset = discover_dataset(args.case, args.base)
        selection = select_timed_evidence(
            timeline_file=dataset["timeline"],
            reactionevent_file=dataset["reactionevent"],
            molecules_file=dataset["molecules"],
        )
        result = EVENT_EVIDENCE_STORE.query_events(
            selection.primary_file,
            selection.molecules_file,
            str(args.reaction_key),
            limit=int(args.limit),
            offset=int(args.offset),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _fate_endpoint(value: str) -> tuple[str, str]:
    try:
        category, species = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "endpoint must use CATEGORY=EXACT_SPECIES"
        ) from exc
    category = category.strip()
    species = species.strip()
    if not category or not species:
        raise argparse.ArgumentTypeError(
            "endpoint category and exact Species must not be empty"
        )
    return category, species


def _atom_element(value: str) -> tuple[int, str]:
    try:
        atom_id_text, element = value.split("=", 1)
        atom_id = int(atom_id_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "atom element must use ATOM_ID=ELEMENT"
        ) from exc
    element = element.strip()
    if atom_id < 0 or not re.fullmatch(r"[A-Z][a-z]?", element):
        raise argparse.ArgumentTypeError(
            "atom element must use a non-negative Atom ID and element symbol"
        )
    return atom_id, element


def cmd_species_fate(args: argparse.Namespace) -> int:
    from reacnet_scope.prepare import discover_dataset
    from reacnet_scope.services import (
        ServiceError,
        build_species_fate_analysis,
        species_fate_tables_zip,
        species_fate_to_json,
    )

    endpoint_categories: dict[str, list[str]] = {}
    for category, species in args.endpoint:
        endpoint_categories.setdefault(category, []).append(species)
    atom_elements = dict(args.atom_element)
    try:
        json_target = (
            Path(args.out_json).expanduser().resolve()
            if args.out_json
            else None
        )
        tables_target = (
            Path(args.out_tables).expanduser().resolve()
            if args.out_tables
            else None
        )
        if json_target is not None and json_target == tables_target:
            raise ValueError("--out-json and --out-tables must be different files")
        if tables_target is not None and tables_target.exists() and not args.force:
            raise FileExistsError(
                f"output already exists: {tables_target}; pass --force to replace it"
            )
        dataset = discover_dataset(args.case, args.base)
        result = build_species_fate_analysis(
            dataset,
            target_species=str(args.target),
            endpoint_categories=endpoint_categories,
            atom_elements=atom_elements,
            anchor_mode=str(args.anchor_mode),
            anchor_elements=args.anchor_element,
            anchor_atom_ids=args.anchor_atom_id,
            formation_start_frame=int(args.formation_start_frame),
            formation_end_frame=args.formation_end_frame,
            followup_end_frame=args.followup_end_frame,
            minimum_followup_frames=int(args.minimum_followup_frames),
            max_events_per_episode=int(args.max_events_per_episode),
            max_active_branches=int(args.max_active_branches),
            detail_retention_limit=int(args.detail_retention_limit),
            global_episode_limit=getattr(args, "global_episode_limit", None),
        )
        json_payload = species_fate_to_json(result)
        tables_payload = (
            species_fate_tables_zip(result) if args.out_tables else b""
        )
        if args.out_json:
            _write_text_atomic(args.out_json, json_payload)
        if args.out_tables:
            _write_bytes_atomic(
                args.out_tables,
                tables_payload,
                force=bool(args.force),
            )
    except (FileNotFoundError, FileExistsError, RuntimeError, ServiceError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    summary = dict(result.get("summary") or {})
    print(
        json.dumps(
            {
                "fate_result_id": result.get("fate_result_id"),
                "status": result.get("status"),
                "formation_count": summary.get("formation_count"),
                "main_cohort_size": summary.get("main_cohort_size"),
                "fully_resolved_count": summary.get("fully_resolved_count"),
                "censored_count": summary.get("censored_count"),
                "resolution_fraction": summary.get("resolution_fraction"),
                "out_json": str(args.out_json or ""),
                "out_tables": str(args.out_tables or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def cmd_batch_compare(args: argparse.Namespace) -> int:
    from reacnet_scope.services import run_grouped_batch_comparison

    groups: dict[str, list[str]] = {}
    for label, path in args.group:
        groups.setdefault(label, []).append(path)
    requests = [
        {
            "group_name": label,
            "conditions": [
                {
                    "name": f"{label}-{index}",
                    "folder": str(Path(path).expanduser().parent),
                    "reaction_file": str(Path(path).expanduser()),
                    "replicate": index,
                }
                for index, path in enumerate(paths, 1)
            ],
        }
        for label, paths in groups.items()
    ]
    try:
        result = run_grouped_batch_comparison(
            requests,
            min_detection_rate=float(args.min_detection_rate),
            top_n=int(args.top),
        )
    except (RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _reaction_base(reaction_path: str) -> str:
    suffix = ".reactionabcd"
    return reaction_path[: -len(suffix)] if reaction_path.endswith(suffix) else reaction_path


def _write_json_atomic(path: str, document: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{target}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_text_atomic(path: str, payload: str) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{target}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_bytes_atomic(
    path: str,
    payload: bytes,
    *,
    force: bool = False,
) -> Path:
    target = Path(path).expanduser()
    if target.exists() and not force:
        raise FileExistsError(
            f"output already exists: {target}; pass --force to replace it"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_path), str(target))
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
    return target


_EVENT_PATH_SOURCE_SUFFIXES = (
    ".timeline.h5",
    ".reactionevent.csv",
    ".molecules.csv",
    ".reactionabcd",
)


def _event_path_source_from_spec(spec: str):
    """Parse ``REPLICATE=COMMON_PREFIX`` without opening source files."""
    from reacnet_scope.event_paths import EventPathSource

    label, separator, raw_base = str(spec or "").partition("=")
    label = label.strip()
    raw_base = raw_base.strip()
    if not separator or not label or not raw_base:
        raise ValueError(
            "--source must use REPLICATE=COMMON_PREFIX, for example "
            "rep1=/data/rep1/run.lammpstrj"
        )
    base = os.path.abspath(os.path.expanduser(raw_base))
    for suffix in _EVENT_PATH_SOURCE_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    reaction_path = f"{base}.reactionabcd"
    timeline_path = f"{base}.timeline.h5"
    native_available = Path(timeline_path).is_file()
    return EventPathSource(
        replicate=label,
        reactionevent_file=(
            timeline_path
            if native_available
            else f"{base}.reactionevent.csv"
        ),
        molecules_file=("" if native_available else f"{base}.molecules.csv"),
        reaction_file=reaction_path if Path(reaction_path).is_file() else "",
    )


def _print_event_path_table(payload: dict[str, object], *, top: int) -> None:
    summary = dict(payload.get("summary", {}))
    verification = dict(payload.get("verification", {}))
    print(
        "# status={status}, actual_occurrences={actual}, signatures={signatures}, "
        "atom_lineages={lineages}, replicates={replicates}, complete={complete}".format(
            status=verification.get("status", "inconclusive"),
            actual=summary.get("actual_path_occurrence_count", 0),
            signatures=summary.get("actual_path_signature_count", 0),
            lineages=summary.get("independent_atom_lineage_support_count", 0),
            replicates=summary.get("replicate_count", 0),
            complete=summary.get("statistics_complete", False),
        )
    )
    print(
        "rank,signature_id,replicate_rate,lineage_support,occurrences,"
        "reaction_keys"
    )
    paths = list(payload.get("paths", []))
    for rank, path_value in enumerate(paths[:top], 1):
        path = dict(path_value)
        keys = " | ".join(str(key) for key in path.get("reaction_keys", []))
        if len(keys) > 240:
            keys = f"{keys[:237]}..."
        print(
            f"{rank},{path.get('signature_id')},"
            f"{path.get('replicate_reproduction_rate')},"
            f"{path.get('independent_atom_lineage_support_count')},"
            f"{path.get('occurrence_count')},{keys}"
        )


def cmd_verify_path(args: argparse.Namespace) -> int:
    """Verify one strict-time, exact-molecule, atom-continuous RNG path."""
    from reacnet_scope.event_paths import (
        EventPathAnalysisError,
        verify_event_path,
    )
    from reacnet_scope.indexes import (
        IndexInvalidError,
        IndexNotReadyError,
        IndexStaleError,
    )

    try:
        sources = [_event_path_source_from_spec(value) for value in args.source]
        payload = verify_event_path(
            sources,
            args.reaction,
            max_interval_gap=args.max_interval_gap,
            max_timestep_gap=args.max_timestep_gap,
            max_occurrence_details=args.max_occurrence_details,
            max_expansions=args.max_expansions,
        )
    except (
        EventPathAnalysisError,
        FileNotFoundError,
        IndexInvalidError,
        IndexNotReadyError,
        IndexStaleError,
        ValueError,
    ) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    _print_event_path_table(payload, top=args.top)
    if args.out_json:
        _write_json_atomic(args.out_json, payload)
        print(f"[OK] wrote: {args.out_json}")
    return 0


def cmd_candidate_search(args: argparse.Namespace) -> int:
    """Run the same indexed, single-source query as the Web workbench."""
    from reacnet_scope import services as svc
    artifacts = {"reactionevent": args.source, "molecules": args.molecules}
    if args.source.endswith(".timeline.h5"):
        artifacts["timeline"] = args.source
    try:
        result = svc.search_candidate_paths(
            artifacts, args.start, target=args.target, mode=args.mode,
            max_steps=args.max_steps, max_paths=args.max_paths,
        )
    except svc.ServiceError as exc:
        print(f"[ERROR] {exc.message}", file=sys.stderr)
        return 2
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_csv:
        Path(args.out_csv).write_text(svc.candidate_paths_csv(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_candidate_paths(args: argparse.Namespace) -> int:
    """Discover bounded network Candidates with indexed Step Evidence."""
    from reacnet_scope import services as svc

    try:
        sources = [_event_path_source_from_spec(value) for value in args.source]
        current = sources[0]
        artifacts = {
            "reaction": os.path.abspath(args.reac),
            "timeline": (
                current.reactionevent_file
                if current.reactionevent_file.endswith(".timeline.h5")
                else ""
            ),
            "reactionevent": (
                ""
                if current.reactionevent_file.endswith(".timeline.h5")
                else current.reactionevent_file
            ),
            "molecules": current.molecules_file,
        }
        payload = svc.discover_candidate_paths_for_dash(
            artifacts,
            args.start,
            current_replicate=current.replicate,
            additional_sources="\n".join(args.source[1:]),
            minimum_path_length=args.min_steps,
            maximum_path_length=args.max_steps,
            max_interval_gap=args.max_interval_gap,
            max_timestep_gap=args.max_timestep_gap,
            max_expansions=args.max_expansions,
            max_paths=args.top,
            minimum_occurrences=args.min_occurrences,
            energy_csv=args.energy_csv,
        )
    except (svc.ServiceError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(
        "# candidate_paths={count}, truncated={truncated}, "
        "energy={energy}".format(
            count=payload["path_count"],
            truncated=payload["truncated"],
            energy=payload["energy_status"],
        )
    )
    print("rank,score,min_step_events,start,species,reaction_keys")
    for path in payload["paths"]:
        print(
            f"{path['rank']},{path['score']},"
            f"{path['minimum_step_occurrence_count']},"
            f"{path['start_species']},{' -> '.join(path['species'])},"
            f"{' | '.join(path['reaction_keys'])}"
        )
    if args.out_json:
        _write_json_atomic(args.out_json, payload)
        print(f"[OK] wrote: {args.out_json}")
    return 0


def cmd_export_event(args: argparse.Namespace) -> int:
    """Export one prepared RNG event as a reproducible evidence ZIP."""
    from reacnet_scope.event_index import (
        EVENT_EVIDENCE_STORE,
        EventNotFoundError,
    )
    from reacnet_scope.event_package import (
        EventPackageError,
        build_event_package,
    )
    from reacnet_scope.indexes import (
        IndexInvalidError,
        IndexNotReadyError,
        IndexStaleError,
    )
    from reacnet_scope.prepare import discover_dataset
    from reacnet_scope.trajectory import (
        TrajectoryDependencyError,
        TrajectoryFrameError,
        load_type_element_map,
    )
    from reacnet_scope import services as svc

    try:
        dataset = discover_dataset(args.case, args.base)
        reactionevent = dataset["reactionevent"]
        molecules = (
            dataset["molecules"]
            if Path(dataset["molecules"]).is_file()
            else ""
        )
        event = EVENT_EVIDENCE_STORE.get_event(
            reactionevent,
            molecules,
            args.event_id,
        )
        explicit_mapping = svc.parse_event_type_element_map(args.type_map)
        atom_type_map = None
        if str(args.type_map or "").strip():
            atom_type_map = load_type_element_map(dataset["trajectory"])
            atom_type_map.update(explicit_mapping)
        viewer = svc.build_rng_event_visualization(
            dataset,
            event,
            before_frames=args.before_frames,
            after_frames=args.after_frames,
            environment_radius=args.environment_radius,
            max_environment_atoms=args.max_environment_atoms,
            atom_type_map=atom_type_map,
            persist_type_map=False,
        )
        package = build_event_package(viewer, scope=args.scope)
        target = _write_bytes_atomic(args.out, package, force=args.force)
    except (
        EventNotFoundError,
        EventPackageError,
        FileExistsError,
        FileNotFoundError,
        IndexInvalidError,
        IndexNotReadyError,
        IndexStaleError,
        RuntimeError,
        TrajectoryDependencyError,
        TrajectoryFrameError,
        svc.ServiceError,
        ValueError,
    ) as exc:
        message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
        print(f"[ERROR] {message}", file=sys.stderr)
        return 2
    print(f"[OK] wrote event package: {target} ({len(package)} bytes)")
    return 0


def _dft_participant_selection(value: str) -> tuple[bool, tuple[int, ...] | None]:
    text = str(value or "all").strip().lower()
    if text == "all":
        return True, None
    if text == "none":
        return False, ()
    try:
        values = sorted(
            {int(token.strip()) for token in text.split(",") if token.strip()}
        )
    except ValueError as exc:
        raise ValueError("Molecule Instance 选择必须是 all、none 或 1,2 等序号") from exc
    if not values or any(value <= 0 for value in values):
        raise ValueError("Molecule Instance 序号从 1 开始")
    return True, tuple(value - 1 for value in values)


def _dft_electronic_states(values: Iterable[str]) -> dict[str, tuple[int, int]]:
    states: dict[str, tuple[int, int]] = {}
    for raw in values:
        stem, separator, state = str(raw or "").partition("=")
        parts = [value.strip() for value in state.split(",")]
        if not separator or not stem.strip() or len(parts) != 2:
            raise ValueError(
                "--state 格式必须是 geometry=charge,multiplicity"
            )
        try:
            charge, multiplicity = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("--state 的电荷和多重度必须是整数") from exc
        if multiplicity <= 0:
            raise ValueError("--state 的自旋多重度必须为正整数")
        normalized_stem = stem.strip()
        if normalized_stem in states:
            raise ValueError(f"--state 重复指定了 {normalized_stem}")
        states[normalized_stem] = (charge, multiplicity)
    return states


def cmd_export_dft_geometry(args: argparse.Namespace) -> int:
    """Export one matched occurrence as DFT initial geometries."""
    from reacnet_scope import services as svc
    from reacnet_scope.event_index import (
        EVENT_EVIDENCE_STORE,
        EventNotFoundError,
    )
    from reacnet_scope.indexes import (
        IndexInvalidError,
        IndexNotReadyError,
        IndexStaleError,
        dataset_id_for_source,
    )
    from reacnet_scope.prepare import discover_dataset
    from reacnet_scope.trajectory import (
        TrajectoryDependencyError,
        TrajectoryFrameError,
    )

    try:
        dataset = discover_dataset(args.case, args.base)
        reactionevent = dataset["reactionevent"]
        molecules = (
            dataset["molecules"]
            if Path(dataset["molecules"]).is_file()
            else ""
        )
        event = EVENT_EVIDENCE_STORE.get_event(
            reactionevent,
            molecules,
            args.event_id,
        )
        include_reactants, reactant_indices = _dft_participant_selection(
            args.reactants
        )
        include_products, product_indices = _dft_participant_selection(
            args.products
        )
        explicit_mapping = svc.parse_event_type_element_map(args.type_map)
        request = svc.DftGeometryRequest(
            include_reactants=include_reactants,
            include_products=include_products,
            reactant_indices=reactant_indices,
            product_indices=product_indices,
            layout=args.layout,
            electronic_states=_dft_electronic_states(args.state),
            atom_type_map=(
                explicit_mapping if str(args.type_map or "").strip() else None
            ),
            source_length_unit=(
                "angstrom"
                if args.save_unit_confirmation
                else (args.source_unit or None)
            ),
            warning_atom_count=args.warning_atoms,
            max_atom_count=args.max_atoms,
        )
        evaluation = svc.evaluate_reaction_readiness(
            dataset,
            event,
            svc.ReactionReadinessRequest(
                geometry=request,
                isolated_cluster_confirmed=args.confirm_isolated_cluster,
            ),
            dataset_id=dataset_id_for_source(dataset["base"]),
            replicate=(args.replicate or Path(dataset["base"]).name),
        )
        status = str(evaluation.report["qc_handoff"]["status"])
        print(f"qc_handoff.status={status}")
        if evaluation.bundle is None:
            failures = [
                item
                for item in evaluation.report["qc_handoff"]["checks"]
                if item.get("status") != "pass"
            ]
            for item in failures:
                evidence_message = str(
                    (item.get("evidence") or {}).get("message") or ""
                )
                guidance = str(
                    item.get("remediation") or item.get("claim_limit") or ""
                )
                detail = "；".join(
                    value for value in (evidence_message, guidance) if value
                ) or "请查看检查报告"
                print(
                    f"[CHECK] {item.get('id')}: {item.get('status')} — {detail}",
                    file=sys.stderr,
                )
            return 2
        if status == "review_required" and not args.acknowledge_review:
            print(
                "[ERROR] readiness 为 review_required；逐项复核后使用 "
                "--acknowledge-review 导出。",
                file=sys.stderr,
            )
            return 2
        bundle = evaluation.bundle
        if args.save_unit_confirmation:
            svc.save_coordinate_length_unit(dataset["trajectory"], "angstrom")
        package = bundle.to_zip()
        target = _write_bytes_atomic(args.out, package, force=args.force)
    except (
        EventNotFoundError,
        FileExistsError,
        FileNotFoundError,
        IndexInvalidError,
        IndexNotReadyError,
        IndexStaleError,
        RuntimeError,
        TrajectoryDependencyError,
        TrajectoryFrameError,
        svc.DftGeometryError,
        svc.ServiceError,
        ValueError,
    ) as exc:
        message = exc.message if isinstance(exc, svc.ServiceError) else str(exc)
        print(f"[ERROR] {message}", file=sys.stderr)
        return 2
    print(
        f"[OK] wrote DFT initial geometry package: {target} "
        f"({len(package)} bytes)"
    )
    return 0


def cmd_species(args: argparse.Namespace) -> int:
    net = build_network(args.reac, args.min_tp)
    smiles_set = net.smiles_by_formula(args.formula)

    if not smiles_set:
        print(f"[INFO] No SMILES found for formula {args.formula}")
        return 0

    rows = []
    for smi in smiles_set:
        sp = net.species[smi]
        rows.append(
            {
                "smiles": smi,
                "formula": sp.formula,
                "tp_total": sp.total_throughput,
                "tp_consume": sp.tp_as_reactant,
                "tp_produce": sp.tp_as_product,
                "net_production": sp.net_production,
                "n_consume_rxns": sp.n_consume_rxns,
                "n_produce_rxns": sp.n_produce_rxns,
            }
        )
    rows.sort(key=lambda x: x["tp_total"], reverse=True)

    top = rows[: args.top] if args.top > 0 else rows
    total_tp = sum(x["tp_total"] for x in rows)

    print(f"# formula={args.formula}  n_smiles={len(rows)}  total_tp={total_tp}")
    print(
        "rank,tp_total,tp_consume,tp_produce,net_production,"
        "n_consume_rxns,n_produce_rxns,smiles"
    )
    for i, row in enumerate(top, 1):
        print(
            f"{i},{row['tp_total']},{row['tp_consume']},{row['tp_produce']},"
            f"{row['net_production']},{row['n_consume_rxns']},{row['n_produce_rxns']},"
            f"{row['smiles']}"
        )

    if args.out:
        out_rows = []
        for i, row in enumerate(top, 1):
            d = dict(row)
            d["rank"] = i
            d["share_in_formula_tp_pct"] = round(
                row["tp_total"] / total_tp * 100.0 if total_tp else 0.0, 3
            )
            out_rows.append(d)
        fieldnames = [
            "rank",
            "smiles",
            "formula",
            "tp_total",
            "tp_consume",
            "tp_produce",
            "net_production",
            "share_in_formula_tp_pct",
            "n_consume_rxns",
            "n_produce_rxns",
        ]
        write_csv(args.out, fieldnames, out_rows)
        print(f"[OK] wrote: {args.out}")
    return 0


def collect_next_reactions(net: ReactionNetwork, smi: str, role: str) -> List[MatchedReaction]:
    tp_map = {r.key: r.tp for r in net.reactions}
    rows: List[MatchedReaction] = []

    if role in {"consume", "both"}:
        total = net.total_consume_tp(smi)
        for rxn in net.consumption_of(smi):
            fwd, rev, net_tp = net_flux(rxn, tp_map)
            ratio = (rxn.tp / total * 100.0) if total else 0.0
            rows.append(MatchedReaction("consume", rxn, fwd, rev, net_tp, ratio))

    if role in {"produce", "both"}:
        total = net.total_produce_tp(smi)
        for rxn in net.production_of(smi):
            fwd, rev, net_tp = net_flux(rxn, tp_map)
            ratio = (rxn.tp / total * 100.0) if total else 0.0
            rows.append(MatchedReaction("produce", rxn, fwd, rev, net_tp, ratio))

    rows.sort(key=lambda x: (abs(x.net_tp), x.forward_tp), reverse=True)
    return rows


def cmd_next(args: argparse.Namespace) -> int:
    net = build_network(args.reac, args.min_tp)
    smi = args.smiles
    if smi not in net.species:
        print(f"[INFO] SMILES not found in reaction network: {smi}")
        return 0

    matched = collect_next_reactions(net, smi, args.role)
    if args.net_positive_only:
        matched = [x for x in matched if x.net_tp > 0]
    top = matched[: args.top] if args.top > 0 else matched

    sp = net.species[smi]
    print(
        f"# smiles={smi}\n"
        f"# formula={sp.formula}, tp_consume={sp.tp_as_reactant}, tp_produce={sp.tp_as_product}, "
        f"net={sp.net_production}\n"
        f"# matched={len(matched)} (show={len(top)})"
    )
    print(
        "rank,role,tp,reverse_tp,net_tp,ratio_pct,reaction_formulas,reaction_smiles"
    )
    out_rows = []
    for i, m in enumerate(top, 1):
        row = {
            "rank": i,
            "role": m.role,
            "tp": m.forward_tp,
            "reverse_tp": m.reverse_tp,
            "net_tp": m.net_tp,
            "ratio_pct": round(m.ratio_pct, 3),
            "reaction_formulas": reaction_formula_str(m.reaction),
            "reaction_smiles": reaction_smiles_str(m.reaction),
        }
        out_rows.append(row)
        print(
            f"{row['rank']},{row['role']},{row['tp']},{row['reverse_tp']},"
            f"{row['net_tp']},{row['ratio_pct']},{row['reaction_formulas']},"
            f"{row['reaction_smiles']}"
        )

    if args.out:
        write_csv(
            args.out,
            [
                "rank",
                "role",
                "tp",
                "reverse_tp",
                "net_tp",
                "ratio_pct",
                "reaction_formulas",
                "reaction_smiles",
            ],
            out_rows,
        )
        print(f"[OK] wrote: {args.out}")
    return 0


def match_formula_reaction(
    rxn: Reaction,
    need_r: Counter,
    need_p: Counter,
    mode: str,
) -> bool:
    have_r = Counter(rxn.reactant_formulas)
    have_p = Counter(rxn.product_formulas)
    if mode == "exact":
        if need_r and have_r != need_r:
            return False
        if need_p and have_p != need_p:
            return False
        return True
    if need_r and not multiset_contains(have_r, need_r):
        return False
    if need_p and not multiset_contains(have_p, need_p):
        return False
    return True


def cmd_rxn_formula(args: argparse.Namespace) -> int:
    reactants = split_terms(args.reactants)
    products = split_terms(args.products)
    if not reactants and not products:
        print("[ERROR] provide reactants and/or products.")
        return 2

    net = build_network(args.reac, args.min_tp)
    tp_map = {r.key: r.tp for r in net.reactions}
    need_r = Counter(reactants)
    need_p = Counter(products)

    matched = []
    for rxn in net.reactions:
        if not match_formula_reaction(rxn, need_r, need_p, args.mode):
            continue
        fwd, rev, net_tp = net_flux(rxn, tp_map)
        matched.append(
            {
                "tp": fwd,
                "reverse_tp": rev,
                "net_tp": net_tp,
                "reactant_formulas": " + ".join(rxn.reactant_formulas),
                "product_formulas": " + ".join(rxn.product_formulas),
                "reaction_formulas": reaction_formula_str(rxn),
                "reaction_smiles": reaction_smiles_str(rxn),
            }
        )

    matched.sort(key=lambda x: (x["tp"], abs(x["net_tp"])), reverse=True)
    top = matched[: args.top] if args.top > 0 else matched

    lhs = " + ".join(reactants) if reactants else "*"
    rhs = " + ".join(products) if products else "*"
    q = f"{lhs} -> {rhs}"
    print(f"# query={q}, mode={args.mode}, matches={len(matched)} (show={len(top)})")
    print("rank,tp,reverse_tp,net_tp,reaction_formulas,reaction_smiles")
    for i, row in enumerate(top, 1):
        print(
            f"{i},{row['tp']},{row['reverse_tp']},{row['net_tp']},"
            f"{row['reaction_formulas']},{row['reaction_smiles']}"
        )

    if args.out:
        out_rows = []
        for i, row in enumerate(top, 1):
            d = dict(row)
            d["rank"] = i
            out_rows.append(d)
        write_csv(
            args.out,
            [
                "rank",
                "tp",
                "reverse_tp",
                "net_tp",
                "reactant_formulas",
                "product_formulas",
                "reaction_formulas",
                "reaction_smiles",
            ],
            out_rows,
        )
        print(f"[OK] wrote: {args.out}")
    return 0


def parse_metric(value: str) -> float | None:
    s = value.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cmd_topshare(args: argparse.Namespace) -> int:
    if not os.path.exists(args.csv):
        print(f"[ERROR] file not found: {args.csv}")
        return 2

    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or args.metric not in reader.fieldnames:
            print(f"[ERROR] metric column not found: {args.metric}")
            print(f"       available: {', '.join(reader.fieldnames or [])}")
            return 2
        rows = []
        for row in reader:
            v = parse_metric(str(row.get(args.metric, "")))
            if v is None:
                continue
            if args.abs_metric:
                v = abs(v)
            if args.positive_only and v <= 0:
                continue
            rows.append((v, row))

    if not rows:
        print("[INFO] no valid rows after filtering.")
        return 0

    rows.sort(key=lambda x: x[0], reverse=True)
    top = rows[: args.top] if args.top > 0 else rows

    total = sum(v for v, _ in rows)
    top_sum = sum(v for v, _ in top)
    share = top_sum / total * 100.0 if total else 0.0

    print(
        f"# metric={args.metric}, rows={len(rows)}, top={len(top)}, "
        f"top_sum={top_sum:.6g}, total={total:.6g}, share={share:.3f}%"
    )
    print(f"rank,metric_value,share_pct,cumulative_pct")
    cum = 0.0
    out_rows = []
    for i, (v, row) in enumerate(top, 1):
        pct = v / total * 100.0 if total else 0.0
        cum += pct
        data = {
            "rank": i,
            "metric_value": v,
            "share_pct": round(pct, 3),
            "cumulative_pct": round(cum, 3),
        }
        out_rows.append(data)
        print(f"{i},{v:.6g},{data['share_pct']},{data['cumulative_pct']}")

    if args.out:
        write_csv(args.out, ["rank", "metric_value", "share_pct", "cumulative_pct"], out_rows)
        print(f"[OK] wrote: {args.out}")
    return 0



def split_target_args(raw_items: Sequence[str]) -> List[str]:
    out: List[str] = []
    for raw in raw_items:
        parts = [x.strip() for x in re.split(r"\s*,\s*|\s*;\s*", raw.strip()) if x.strip()]
        out.extend(parts)
    return out



def derive_species_path(reac_path: str) -> str:
    if reac_path.endswith(".reactionabcd"):
        return reac_path[: -len(".reactionabcd")] + ".species"
    return reac_path + ".species"



def cmd_species_evolution(args: argparse.Namespace) -> int:
    """Run the formal indexed Species Evolution workflow."""
    from reacnet_scope import services as svc

    species_file = args.species_file or derive_species_path(args.reac)
    if not os.path.exists(species_file):
        print(f"[ERROR] species file not found: {species_file}")
        return 2
    raw_targets = split_target_args(args.target)
    if not raw_targets:
        print("[ERROR] --target is required (support multiple).")
        return 2
    try:
        payload = svc.build_species_evolution(
            {"reaction": args.reac, "species": species_file},
            raw_targets,
            species_file=species_file,
            x_axis=args.x_axis,
            timestep_ps=args.timestep_ps,
            normalize=args.normalize,
            smooth_window=args.smooth_window,
            downsample=0,
            max_curves=1_000_000,
            formula_mode=args.formula_mode,
            max_smiles_per_formula=args.max_smiles_per_formula,
        )
    except svc.ServiceError as exc:
        print(f"[ERROR] {exc.message}")
        return 2

    mapping_rows = list(payload.get("mapping") or [])
    if args.list_only:
        print("# target mapping")
        print("series_name,query_type,query,formula,smiles,tp_total")
        for row in mapping_rows:
            print(
                f"{row.get('series_name', '')},{row.get('query_type', '')},"
                f"{row.get('query', '')},{row.get('formula', '')},"
                f"{row.get('smiles', '')},{row.get('tp_total', 0)}"
            )
    if args.out_map:
        write_csv(
            args.out_map,
            [
                "series_name",
                "query_type",
                "query",
                "formula",
                "smiles",
                "tp_total",
            ],
            mapping_rows,
        )
        print(f"[OK] wrote: {args.out_map}")
    if args.list_only:
        return 0

    curves = list(payload.get("curves") or [])
    x_values = list(payload.get("x_values") or [])
    x_name = str(payload.get("x_name") or "timestep")
    print(
        f"# species_file={species_file}\n"
        f"# source_mode={payload.get('meta', {}).get('source_mode', '')}, "
        f"timesteps={payload.get('meta', {}).get('n_timestep_full', 0)}, "
        f"x_axis={args.x_axis}, curves={len(curves)}\n"
        f"# normalize={args.normalize}, smooth_window={args.smooth_window}; "
        "CSV export=raw indexed abundance"
    )
    for index, curve in enumerate(curves, 1):
        values = list(curve.get("values") or [])
        print(
            f"  {index:>2}. {curve.get('name', '')}  "
            f"({curve.get('query_type', '')})  "
            f"members={curve.get('n_members', 0)}  "
            f"max={max(values) if values else 0.0:.6g}"
        )
    if args.out_csv:
        Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_csv).write_text(
            svc.evolution_to_csv(payload), encoding="utf-8"
        )
        print(f"[OK] wrote: {args.out_csv}")
    if args.out_png:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        figure, axis = plt.subplots(
            figsize=(args.fig_width, args.fig_height), dpi=args.dpi
        )
        for curve in curves:
            axis.plot(
                x_values,
                curve.get("values") or [],
                linewidth=1.8,
                label=curve.get("name") or curve.get("query") or "curve",
            )
        axis.set_xlabel(x_name)
        axis.set_ylabel(
            "normalized_count"
            if args.normalize in {"initial", "max"}
            else "count"
        )
        axis.set_title(args.title or "Species Time Series")
        if not args.no_grid:
            axis.grid(True, alpha=0.25)
        axis.legend(loc="best", fontsize=9)
        figure.tight_layout()
        Path(args.out_png).parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(args.out_png)
        plt.close(figure)
        print(f"[OK] wrote: {args.out_png}")
    return 0


def _bounded_int(name: str, minimum: int, maximum: int | None = None):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if parsed < minimum or (maximum is not None and parsed > maximum):
            upper = f" and <= {maximum}" if maximum is not None else ""
            raise argparse.ArgumentTypeError(
                f"{name} must be >= {minimum}{upper}"
            )
        return parsed

    return parse


def _unit_float(name: str):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
        if not 0.0 <= parsed <= 1.0:
            raise argparse.ArgumentTypeError(f"{name} must be in [0, 1]")
        return parsed

    return parse


def _bounded_float(name: str, minimum: float, maximum: float):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be in [{minimum}, {maximum}]"
            )
        return parsed

    return parse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "ReacNetGenerator 检索、明确路径验证、绘图与事件证据包导出工具。"
        )
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_serve = sub.add_parser("serve", help="启动 Dash Web 应用")
    sp_serve.add_argument("--host", default="127.0.0.1", help="监听地址")
    sp_serve.add_argument("--port", type=int, default=8060, help="监听端口")
    sp_serve.add_argument("--debug", action="store_true", help="启用 Dash 调试模式")
    sp_serve.set_defaults(func=cmd_serve)

    def add_reac_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--reac",
            default=str(DEFAULT_REACTION_FILE),
            help=f"reactionabcd 文件路径 (default: {DEFAULT_REACTION_FILE})",
        )
        sp.add_argument("--min-tp", type=int, default=1, help="最低 tp 过滤阈值")

    sp_prepare = sub.add_parser(
        "prepare",
        help="检查、建立、重建或清理 Dataset Workspace 索引",
    )
    from reacnet_scope.prepare import configure_parser as configure_prepare

    configure_prepare(sp_prepare, handler=cmd_prepare)

    sp_distribution = sub.add_parser(
        "element-distribution",
        help="查询预建的通用元素分布索引",
    )
    sp_distribution.add_argument("case", help="数据集目录或公共前缀")
    sp_distribution.add_argument(
        "--base",
        default="",
        help="多候选目录中的数据集名称",
    )
    sp_distribution.add_argument(
        "--group-element",
        required=True,
        type=_element_symbol,
        help="用于分组的元素符号，例如 C、N 或 Cl",
    )
    sp_distribution.add_argument(
        "--max-group-count",
        type=_bounded_int("max-group-count", 0, 10000),
        default=100,
    )
    sp_distribution.add_argument(
        "--filter",
        action="append",
        type=_element_filter,
        default=[],
        metavar="ELEMENT=MODE[:MIN[:MAX]]",
        help="可重复：S=present、Cl=absent 或 O=range:1:3",
    )
    sp_distribution.add_argument(
        "--max-points",
        type=_bounded_int("max-points", 2, 4000),
        default=1200,
    )
    sp_distribution.add_argument(
        "--include-zero",
        action="store_true",
        help="包含分组元素原子数为 0 的 E0 组",
    )
    sp_distribution.add_argument(
        "--species-file",
        action="append",
        type=_labelled_path,
        default=[],
        metavar="LABEL=PATH",
        help="加入另一个已准备的 .species 数据集，可重复",
    )
    sp_distribution.add_argument(
        "--tidy-table",
        default="",
        help="可选 tidy CSV/Excel（time,species,count[,dataset]）",
    )
    sp_distribution.add_argument(
        "--bin-width",
        type=_bounded_int("bin-width", 1),
        default=1,
    )
    sp_distribution.add_argument(
        "--group-range",
        action="append",
        type=_group_range,
        default=[],
        metavar="LABEL:MIN:MAX",
    )
    sp_distribution.add_argument(
        "--smooth-window",
        type=_bounded_int("smooth-window", 1),
        default=1,
    )
    sp_distribution.set_defaults(func=cmd_element_distribution)

    sp_species = sub.add_parser("species", help="按分子式列出所有 SMILES")
    add_reac_flags(sp_species)
    sp_species.add_argument("--formula", required=True, help="目标分子式, 例如 C6H4")
    sp_species.add_argument("--top", type=int, default=50, help="输出前 N 条, <=0 表示全部")
    sp_species.add_argument("--out", default="", help="可选输出 CSV 路径")
    sp_species.set_defaults(func=cmd_species)

    sp_next = sub.add_parser("reactions", help="查询某个 SMILES 的下一步(消耗/生成)反应")
    add_reac_flags(sp_next)
    sp_next.add_argument("--smiles", required=True, help="目标 SMILES")
    sp_next.add_argument(
        "--role",
        choices=["consume", "produce", "both"],
        default="consume",
        help="检索角色",
    )
    sp_next.add_argument(
        "--net-positive-only",
        action="store_true",
        help="仅保留 net_tp > 0 的通道",
    )
    sp_next.add_argument("--top", type=int, default=30, help="输出前 N 条, <=0 表示全部")
    sp_next.add_argument("--out", default="", help="可选输出 CSV 路径")
    sp_next.set_defaults(func=cmd_next)

    sp_events = sub.add_parser("events", help="查询预建的 Reaction Occurrence 索引")
    sp_events.add_argument("case", help="数据集目录或公共前缀")
    sp_events.add_argument("--base", default="")
    sp_events.add_argument("--reaction-key", required=True)
    sp_events.add_argument("--limit", type=_bounded_int("limit", 1, 10000), default=100)
    sp_events.add_argument("--offset", type=_bounded_int("offset", 0), default=0)
    sp_events.set_defaults(func=cmd_events)

    sp_fate = sub.add_parser(
        "species-fate",
        help="对一个精确 Species 运行 descendant-complete first-passage 命运分析",
    )
    sp_fate.add_argument("case", help="数据集目录或公共前缀")
    sp_fate.add_argument("--base", default="")
    sp_fate.add_argument("--target", required=True, help="目标精确 RNG SMILES")
    sp_fate.add_argument(
        "--endpoint",
        action="append",
        required=True,
        type=_fate_endpoint,
        metavar="CATEGORY=EXACT_SPECIES",
        help="命名终点类别及一个精确 Species；同类别可重复",
    )
    sp_fate.add_argument(
        "--anchor-mode",
        choices=["heavy_atoms", "all_atoms", "elements", "atom_ids"],
        default="heavy_atoms",
    )
    sp_fate.add_argument(
        "--atom-element",
        action="append",
        type=_atom_element,
        default=[],
        metavar="ATOM_ID=ELEMENT",
        help="heavy_atoms/elements 策略所需的可靠 Atom ID→Element 映射",
    )
    sp_fate.add_argument(
        "--anchor-element", action="append", default=[], metavar="ELEMENT"
    )
    sp_fate.add_argument(
        "--anchor-atom-id", action="append", type=int, default=[], metavar="ATOM_ID"
    )
    sp_fate.add_argument(
        "--formation-start-frame",
        type=_bounded_int("formation-start-frame", 0),
        default=0,
    )
    sp_fate.add_argument(
        "--formation-end-frame",
        type=_bounded_int("formation-end-frame", 0),
        default=None,
    )
    sp_fate.add_argument(
        "--followup-end-frame",
        type=_bounded_int("followup-end-frame", 0),
        default=None,
    )
    sp_fate.add_argument(
        "--minimum-followup-frames",
        type=_bounded_int("minimum-followup-frames", 0),
        default=0,
    )
    sp_fate.add_argument(
        "--max-events-per-episode",
        type=_bounded_int("max-events-per-episode", 1, 10_000_000),
        default=10_000,
    )
    sp_fate.add_argument(
        "--max-active-branches",
        type=_bounded_int("max-active-branches", 1, 1_000_000),
        default=1_000,
    )
    sp_fate.add_argument(
        "--detail-retention-limit",
        type=_bounded_int("detail-retention-limit", 0, 1_000_000),
        default=1_000,
    )
    sp_fate.add_argument(
        "--global-episode-limit",
        type=_bounded_int("global-episode-limit", 1, 10_000_000),
        default=None,
        help=(
            "可选全局停止边界；触发时 Result=incomplete，主统计为 NA"
        ),
    )
    sp_fate.add_argument("--out-json", default="", help="输出 fate-result.json")
    sp_fate.add_argument("--out-tables", default="", help="输出 fate-tables.zip")
    sp_fate.add_argument(
        "--force", action="store_true", help="允许覆盖已有 fate-tables.zip"
    )
    sp_fate.set_defaults(func=cmd_species_fate)

    sp_batch = sub.add_parser("batch-compare", help="按 Simulation Condition 对比 Replicate")
    sp_batch.add_argument(
        "--group",
        action="append",
        type=_labelled_path,
        required=True,
        metavar="CONDITION=REACTIONABCD",
    )
    sp_batch.add_argument(
        "--min-detection-rate",
        type=_unit_float("min-detection-rate"),
        default=0.0,
    )
    sp_batch.add_argument("--top", type=_bounded_int("top", 1, 500), default=50)
    sp_batch.set_defaults(func=cmd_batch_compare)

    sp_event_paths = sub.add_parser(
        "verify-path",
        help="验证明确给出的 Reaction Type 序列是否形成真实事件链",
    )
    sp_event_paths.add_argument(
        "--source",
        action="append",
        required=True,
        metavar="REPLICATE=COMMON_PREFIX",
        help=(
            "一个重复实验及其 RNG 公共文件前缀；可重复传入，例如 "
            "rep1=/data/rep1/run.lammpstrj"
        ),
    )
    sp_event_paths.add_argument(
        "--reaction",
        action="append",
        required=True,
        metavar="REACTANTS->PRODUCTS",
        help="按发生顺序给出完整 Reaction Type；重复 2–8 次",
    )
    sp_event_paths.add_argument(
        "--max-interval-gap",
        type=_bounded_int("max_interval_gap", 0),
        default=None,
        help="可选：相邻事件允许的最大 RNG 区间差",
    )
    sp_event_paths.add_argument(
        "--max-timestep-gap",
        type=_bounded_int("max_timestep_gap", 0),
        default=None,
        help="可选：前一事件结束到后一事件开始的最大物理 timestep 间隔",
    )
    sp_event_paths.add_argument(
        "--max-occurrence-details",
        type=_bounded_int("max_occurrence_details", 0),
        default=10_000,
        help="JSON 中保留的具体事件路径明细上限；不影响完整统计",
    )
    sp_event_paths.add_argument(
        "--max-expansions",
        type=_bounded_int("max_expansions", 1),
        default=1_000_000,
        help="每个重复实验的实际路径展开上限",
    )
    sp_event_paths.add_argument(
        "--top",
        type=_bounded_int("top", 1),
        default=20,
        help="终端显示的路径签名数量",
    )
    sp_event_paths.add_argument(
        "--out-json",
        default="",
        help="可选：完整、可审计 JSON 报告输出路径",
    )
    sp_event_paths.set_defaults(func=cmd_verify_path)

    sp_candidate_search = sub.add_parser(
        "candidate-search", help="查询已索引的候选路径（与 Web 第一版相同，无综合评分）",
    )
    sp_candidate_search.add_argument("--source", required=True, help="原生 .timeline.h5 或 .reactionevent.csv")
    sp_candidate_search.add_argument("--molecules", default="", help="可选兼容分子 CSV；须与准备索引时一致")
    sp_candidate_search.add_argument("--start", required=True, help="精确 RNG 起始 Species 标签")
    sp_candidate_search.add_argument("--target", default="", help="精确 RNG 目标 Species 标签")
    sp_candidate_search.add_argument("--mode", choices=["target", "explore"], default="target")
    sp_candidate_search.add_argument("--max-steps", type=_bounded_int("max-steps", 1, 8), default=4)
    sp_candidate_search.add_argument("--max-paths", type=_bounded_int("max-paths", 1, 100), default=20)
    sp_candidate_search.add_argument("--out-json", default="")
    sp_candidate_search.add_argument("--out-csv", default="")
    sp_candidate_search.set_defaults(func=cmd_candidate_search)

    sp_candidate_paths = sub.add_parser(
        "candidate-paths",
        help="从观测有向反应网络发现有界候选路径，并附加逐步事件证据",
    )
    sp_candidate_paths.add_argument(
        "--source",
        action="append",
        required=True,
        metavar="REPLICATE=COMMON_PREFIX",
        help="一个重复实验及其 RNG 公共文件前缀；可重复传入",
    )
    sp_candidate_paths.add_argument(
        "--reac",
        required=True,
        help="用于反应频次和方向性的 .reactionabcd 文件",
    )
    sp_candidate_paths.add_argument(
        "--start",
        action="append",
        required=True,
        metavar="EXACT_SMILES",
        help="感兴趣的起始 Species（精确 RNG SMILES）；可重复传入",
    )
    sp_candidate_paths.add_argument(
        "--min-steps",
        type=_bounded_int("min_steps", 2, 8),
        default=2,
        help="最短路径步数",
    )
    sp_candidate_paths.add_argument(
        "--max-steps",
        type=_bounded_int("max_steps", 2, 8),
        default=4,
        help="最长路径步数",
    )
    sp_candidate_paths.add_argument(
        "--max-interval-gap",
        type=_bounded_int("max_interval_gap", 0),
        default=None,
        help="连续支持验证预留参数；当前不参与候选发现",
    )
    sp_candidate_paths.add_argument(
        "--max-timestep-gap",
        type=_bounded_int("max_timestep_gap", 0),
        default=None,
        help="连续支持验证预留参数；当前不参与候选发现",
    )
    sp_candidate_paths.add_argument(
        "--max-expansions",
        type=_bounded_int("max_expansions", 1),
        default=5_000,
        help="有向反应网络的非终止状态展开上限",
    )
    sp_candidate_paths.add_argument(
        "--min-occurrences",
        type=_bounded_int("min_occurrences", 1),
        default=1,
        help="候选路径每一步的最小 Reaction Occurrence 数",
    )
    sp_candidate_paths.add_argument(
        "--energy-csv",
        default="",
        help="可选能量证据 CSV（reaction_key,score[,delta_energy,barrier,unit]）",
    )
    sp_candidate_paths.add_argument(
        "--top",
        type=_bounded_int("top", 1, 500),
        default=20,
        help="返回候选路径数量",
    )
    sp_candidate_paths.add_argument(
        "--out-json",
        default="",
        help="可选：完整、可审计 JSON 报告输出路径",
    )
    sp_candidate_paths.set_defaults(func=cmd_candidate_paths)

    sp_export_event = sub.add_parser(
        "export-event",
        help="把一个已索引 RNG 事件导出为可复核 ZIP",
    )
    sp_export_event.add_argument(
        "--case",
        required=True,
        help="数据集目录或公共前缀",
    )
    sp_export_event.add_argument(
        "--base",
        default="",
        help="目录包含多个数据集时指定公共前缀",
    )
    sp_export_event.add_argument(
        "--event-id",
        required=True,
        help="事件索引中的 event_id",
    )
    sp_export_event.add_argument(
        "--scope",
        choices=["core", "participants", "environment"],
        default="participants",
        help="导出的原子范围",
    )
    sp_export_event.add_argument(
        "--before-frames",
        type=_bounded_int("before_frames", 0, 100),
        default=3,
        help="反应前附加帧数",
    )
    sp_export_event.add_argument(
        "--after-frames",
        type=_bounded_int("after_frames", 0, 100),
        default=3,
        help="反应后附加帧数",
    )
    sp_export_event.add_argument(
        "--environment-radius",
        type=_bounded_float("environment_radius", 0.0, 20.0),
        default=4.0,
        help="局部环境半径（Å）",
    )
    sp_export_event.add_argument(
        "--max-environment-atoms",
        type=_bounded_int("max_environment_atoms", 0, 2000),
        default=500,
        help="环境原子数量上限",
    )
    sp_export_event.add_argument(
        "--type-map",
        default="",
        help="本次导出的 Type→Element 覆盖，例如 1=C,2=H；不会写入数据集设置",
    )
    sp_export_event.add_argument("--out", required=True, help="输出 ZIP 路径")
    sp_export_event.add_argument(
        "--force",
        action="store_true",
        help="原子替换已有输出文件",
    )
    sp_export_event.set_defaults(func=cmd_export_event)

    sp_export_dft = sub.add_parser(
        "export-dft-geometry",
        help="把一个 matched RNG 事件导出为 DFT 初始几何 ZIP",
    )
    sp_export_dft.add_argument(
        "--case",
        required=True,
        help="数据集目录或公共前缀",
    )
    sp_export_dft.add_argument(
        "--base",
        default="",
        help="目录包含多个数据集时指定公共前缀",
    )
    sp_export_dft.add_argument(
        "--event-id",
        required=True,
        help="事件索引中的 matched event_id",
    )
    sp_export_dft.add_argument(
        "--reactants",
        default="all",
        help="反应物 Molecule Instance：all、none 或从 1 开始的序号，如 1,2",
    )
    sp_export_dft.add_argument(
        "--products",
        default="all",
        help="产物 Molecule Instance：all、none 或从 1 开始的序号，如 1,2",
    )
    sp_export_dft.add_argument(
        "--layout",
        choices=["combined", "separate", "both"],
        default="combined",
        help="所选分子合并、分别或同时导出",
    )
    sp_export_dft.add_argument(
        "--type-map",
        default="",
        help="本次导出的 Type→Element 覆盖，例如 1=C,2=H；不会保存",
    )
    sp_export_dft.add_argument(
        "--source-unit",
        choices=["angstrom"],
        default="",
        help="本次显式确认源轨迹坐标为 Å；省略时读取数据集设置",
    )
    sp_export_dft.add_argument(
        "--save-unit-confirmation",
        action="store_true",
        help="把源轨迹为 Å 的确认保存到 Dataset Workspace",
    )
    sp_export_dft.add_argument(
        "--state",
        action="append",
        default=[],
        metavar="GEOMETRY=CHARGE,MULTIPLICITY",
        help="可重复指定输出几何的电荷/多重度，例如 reactants=0,1",
    )
    sp_export_dft.add_argument(
        "--confirm-isolated-cluster",
        action="store_true",
        help=(
            "确认本次外部 TS 交接按非周期孤立簇处理；"
            "不代表气相 TST/RRKM 适用"
        ),
    )
    sp_export_dft.add_argument(
        "--replicate",
        default="",
        help="写入检查报告的 Replicate 标签；默认使用数据集名称",
    )
    sp_export_dft.add_argument(
        "--acknowledge-review",
        action="store_true",
        help="readiness=review_required 时确认已人工复核全部警告",
    )
    sp_export_dft.add_argument(
        "--warning-atoms",
        type=_bounded_int("warning_atoms", 1),
        default=200,
        help="超过该原子数时写入大体系警告",
    )
    sp_export_dft.add_argument(
        "--max-atoms",
        type=_bounded_int("max_atoms", 1),
        default=5000,
        help="单个输出几何的硬上限；不会截断分子",
    )
    sp_export_dft.add_argument("--out", required=True, help="输出 ZIP 路径")
    sp_export_dft.add_argument(
        "--force",
        action="store_true",
        help="原子替换已有输出文件",
    )
    sp_export_dft.set_defaults(func=cmd_export_dft_geometry)

    sp_plot = sub.add_parser(
        "species-evolution",
        help="绘制物种随时间变化曲线 (支持 formula/SMILES 混合输入与多曲线)",
    )
    add_reac_flags(sp_plot)
    sp_plot.add_argument(
        "--species-file",
        default="",
        help="species 文件路径, 默认由 --reac 自动推导(.reactionabcd -> .species)",
    )
    sp_plot.add_argument(
        "--target",
        action="append",
        default=[],
        help=(
            "目标物种, 可重复传入或用逗号分隔; 支持 formula/SMILES 混输, "
            "支持 label::query, 支持前缀 formula:/f: 与 smiles:/smi:"
        ),
    )
    sp_plot.add_argument(
        "--formula-mode",
        choices=["sum", "split", "both"],
        default="sum",
        help="formula 输入如何展开: sum(聚合), split(拆分每个SMILES), both",
    )
    sp_plot.add_argument(
        "--max-smiles-per-formula",
        type=int,
        default=0,
        help="限制每个 formula 展开的 SMILES 数, 0 表示不限制",
    )
    sp_plot.add_argument(
        "--x-axis",
        choices=["step", "ps", "ns"],
        default="step",
        help="x 轴单位",
    )
    sp_plot.add_argument(
        "--timestep-ps",
        type=float,
        default=None,
        help="显式确认并保存 timestep 到 ps 的换算，用于 step->ps/ns 转换",
    )
    sp_plot.add_argument(
        "--normalize",
        choices=["none", "initial", "max"],
        default="none",
        help="曲线归一化方式",
    )
    sp_plot.add_argument(
        "--smooth-window",
        type=int,
        default=1,
        help="平滑窗口(移动平均), 1 表示不平滑",
    )
    sp_plot.add_argument("--title", default="", help="图标题")
    sp_plot.add_argument("--fig-width", type=float, default=10.5, help="图宽(英寸)")
    sp_plot.add_argument("--fig-height", type=float, default=6.0, help="图高(英寸)")
    sp_plot.add_argument("--dpi", type=int, default=180, help="输出 PNG 分辨率")
    sp_plot.add_argument("--no-grid", action="store_true", help="关闭网格")
    sp_plot.add_argument("--out-png", default="", help="输出 PNG 路径")
    sp_plot.add_argument("--out-csv", default="", help="输出曲线 CSV 路径(宽表)")
    sp_plot.add_argument("--out-map", default="", help="输出目标展开映射 CSV 路径")
    sp_plot.add_argument(
        "--list-only",
        action="store_true",
        help="仅输出目标展开映射(公式->SMILES), 不绘图",
    )
    sp_plot.set_defaults(func=cmd_species_evolution)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
