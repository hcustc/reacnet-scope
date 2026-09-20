"""Indexed timing queries for exact directed Reaction Types."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from .event_index import EVENT_EVIDENCE_STORE, EVENT_EVIDENCE_SCHEMA_VERSION
from .indexes import IndexInvalidError, IndexNotReadyError, IndexStaleError
from .rng_events import canonical_reaction_key
from .service_types import ServiceError
from .trajectory import load_timestep_ps
from .workspace_services import _event_artifact_paths


def _source(artifacts: Mapping[str, Any]) -> tuple[str, str]:
    source, molecules = _event_artifact_paths(artifacts)
    if not source or not Path(source).is_file():
        raise ServiceError("缺少事件源", reason="missing_reactionevent")
    return source, molecules


def _conversion(artifacts: Mapping[str, Any], source: str, basis: str) -> float | None:
    if basis != "physical_timestep":
        return None
    for key in ("species", "trajectory"):
        candidate = str(artifacts.get(key) or "")
        if candidate:
            value = load_timestep_ps(candidate)
            if value is not None:
                return value
    return load_timestep_ps(source)


def _unit(basis: str, conversion: float | None) -> str:
    return "ps" if conversion is not None else (
        "source_timestep" if basis == "physical_timestep" else "analyzed_frame"
    )


def _scaled(value: float | int | None, conversion: float | None) -> float | int | None:
    if value is None:
        return None
    return round(float(value) * conversion, 9) if conversion is not None else value


def _error(exc: Exception) -> ServiceError:
    if isinstance(exc, IndexStaleError):
        return ServiceError(f"事件索引已过期；请重建事件索引：{exc}", reason="event_index_stale")
    if isinstance(exc, IndexInvalidError):
        return ServiceError(f"事件索引无效；请重建事件索引：{exc}", reason="event_index_invalid")
    if isinstance(exc, IndexNotReadyError):
        return ServiceError(f"事件索引未准备；请构建事件索引：{exc}", reason="event_index_not_ready")
    return ServiceError(str(exc), reason="invalid_time_query")


def reaction_timing_summaries(
    artifacts: Mapping[str, Any], rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach complete-index timing to displayed directed reaction rows."""
    try:
        source, molecules = _source(artifacts)
        opened = EVENT_EVIDENCE_STORE.open_required(source, molecules)
        basis = str(opened["time_basis"])
        conversion = _conversion(artifacts, source, basis)
        keys = [canonical_reaction_key(row["reactant_smiles"], row["product_smiles"])
                for row in rows]
        summaries = EVENT_EVIDENCE_STORE.reaction_time_summary(source, molecules, keys)
    except (ServiceError, IndexNotReadyError, IndexStaleError, IndexInvalidError, ValueError) as exc:
        error = exc if isinstance(exc, ServiceError) else _error(exc)
        for row in rows:
            row.update(timing_status=error.reason, timing_unit="",
                       first_time=None, last_time=None)
        return {"status": error.reason, "message": error.message}
    unit = _unit(basis, conversion)
    for row, key in zip(rows, keys):
        summary = summaries[key]
        first = summary["first_after_timestep"]
        last = summary["last_after_timestep"]
        row.update(
            timing_status="ok" if conversion is not None else "time_conversion_missing"
            if basis == "physical_timestep" else "analyzed_frame_only",
            timing_unit=unit,
            time_basis=basis,
            timestep_ps=conversion,
            timing_event_count=summary["total"],
            first_after_timestep=first,
            last_after_timestep=last,
            first_time=_scaled(first, conversion),
            last_time=_scaled(last, conversion),
            first_time_ps=_scaled(first, conversion) if conversion is not None else None,
            last_time_ps=_scaled(last, conversion) if conversion is not None else None,
        )
    return {"status": "ok" if conversion is not None else "time_conversion_missing"
            if basis == "physical_timestep" else "analyzed_frame_only", "unit": unit}


def reaction_time_distribution(
    artifacts: Mapping[str, Any], reactants: list[str], products: list[str],
    *, start: float | None = None, end: float | None = None,
    width: float | None = None,
) -> dict[str, Any]:
    """Return forward/reverse counts on one after-frame axis."""
    try:
        source, molecules = _source(artifacts)
        opened = EVENT_EVIDENCE_STORE.open_required(source, molecules)
        basis = str(opened["time_basis"])
        conversion = _conversion(artifacts, source, basis)
        unit = _unit(basis, conversion)
        forward = canonical_reaction_key(reactants, products)
        reverse = canonical_reaction_key(products, reactants)
        keys = [forward] if forward == reverse else [forward, reverse]
        summaries = EVENT_EVIDENCE_STORE.reaction_time_summary(source, molecules, keys)
        observed = [summaries[key] for key in keys if summaries[key]["total"]]
        minimum = min((int(item["first_after_timestep"]) for item in observed), default=0)
        maximum = max((int(item["last_after_timestep"]) for item in observed), default=0)
        scale = conversion or 1.0
        raw_start = float(start) / scale if start is not None else float(minimum)
        raw_end = float(end) / scale if end is not None else float(maximum + 1)
        raw_width = float(width) / scale if width is not None else max(1.0, math.ceil((raw_end - raw_start) / 30))
        bins = EVENT_EVIDENCE_STORE.reaction_time_bins(
            source, molecules, keys, start=raw_start, end=raw_end, width=raw_width
        )
    except (ServiceError, IndexNotReadyError, IndexStaleError, IndexInvalidError, ValueError) as exc:
        raise exc if isinstance(exc, ServiceError) else _error(exc) from exc
    counts = bins["counts"]
    output_bins = []
    for index in range(bins["bin_count"]):
        left = raw_start + index * raw_width
        right = min(raw_end, left + raw_width)
        output_bins.append({"index": index, "start": round(left * scale, 9), "end": round(right * scale, 9),
                            "start_raw": left, "end_raw": right,
                            "forward": counts[forward][index],
                            "reverse": 0 if forward == reverse else counts[reverse][index]})
    return {
        "reaction_key": forward, "reverse_reaction_key": reverse,
        "self_reverse": forward == reverse, "unit": unit,
        "time_basis": basis, "timestep_ps": conversion,
        "start": round(raw_start * scale, 9), "end": round(raw_end * scale, 9),
        "width": round(raw_width * scale, 9), "bins": output_bins,
        "forward_total": bins["totals"][forward],
        "reverse_total": 0 if forward == reverse else bins["totals"][reverse],
        "forward_all": summaries[forward]["total"],
        "reverse_all": 0 if forward == reverse else summaries[reverse]["total"],
    }


def reaction_time_events(
    artifacts: Mapping[str, Any], reaction_key: str,
    *, start_raw: float, end_raw: float, offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Page the entire selected bin without choosing a representative event."""
    try:
        source, molecules = _source(artifacts)
        page = EVENT_EVIDENCE_STORE.query_events_window(
            source, molecules, reaction_key, start=start_raw, end=end_raw,
            offset=offset, limit=limit,
        )
        conversion = _conversion(artifacts, source, str(page["time_basis"]))
    except (ServiceError, IndexNotReadyError, IndexStaleError, IndexInvalidError, ValueError) as exc:
        raise exc if isinstance(exc, ServiceError) else _error(exc) from exc
    unit = _unit(str(page["time_basis"]), conversion)
    raw_unit = (
        "source_timestep" if page["time_basis"] == "physical_timestep"
        else "analyzed_frame"
    )
    source_stat = Path(source).stat()
    page["source_signature"] = {
        "path": str(Path(source).resolve()),
        "size": source_stat.st_size,
        "mtime_ns": source_stat.st_mtime_ns,
    }
    for row in page["rows"]:
        row["time_unit"] = unit
        row["raw_time_unit"] = raw_unit
        row["time_basis"] = page["time_basis"]
        row["timestep_ps"] = conversion
        row["dataset_source"] = page["source_signature"]["path"]
        row["source_size"] = source_stat.st_size
        row["source_mtime_ns"] = source_stat.st_mtime_ns
        row["event_index_schema_version"] = EVENT_EVIDENCE_SCHEMA_VERSION
        row["before_time_ps"] = (
            _scaled(row["before_timestep"], conversion)
            if conversion is not None else None
        )
        row["after_time_ps"] = (
            _scaled(row["after_timestep"], conversion)
            if conversion is not None else None
        )
    page["unit"] = unit
    page["timestep_ps"] = conversion
    return page
