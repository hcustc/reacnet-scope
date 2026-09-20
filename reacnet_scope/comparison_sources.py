"""Versioned source records shared by comparison workflows.

The record deliberately separates explicit metadata from filename suggestions.
It reads only bounded file metadata and small sidecar JSON documents; comparison
queries never scan a large RNG artifact to fill an unknown field.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from reacnet_scope.analysis_services import rng_processing_metadata
from reacnet_scope.datasets import ARTIFACT_SUFFIXES
from reacnet_scope.indexes import dataset_id_for_source
from reacnet_scope.trajectory import TrajectoryFrameError, load_timestep_ps


COMPARISON_SOURCE_SCHEMA_VERSION = "reacnet-scope/comparison-source/v1"
_METADATA_STATES = {"confirmed", "suggested", "unknown"}


def _dataset_base(path: Path) -> Path:
    text = str(path)
    for suffix, _kind in ARTIFACT_SUFFIXES:
        if text.lower().endswith(suffix):
            return Path(text[: -len(suffix)])
    return path


def comparison_artifacts(primary_path: str) -> dict[str, str]:
    """Discover sibling artifacts without reading their contents."""

    primary = Path(primary_path).expanduser().resolve(strict=False)
    base = _dataset_base(primary)
    artifacts: dict[str, str] = {}
    if base.is_file():
        artifacts["trajectory"] = str(base.resolve())
    for suffix, kind in ARTIFACT_SUFFIXES:
        if kind == "trajectory":
            continue
        candidate = Path(f"{base}{suffix}")
        if candidate.is_file():
            artifacts[kind] = str(candidate.resolve())
    return artifacts


def _source_revision(artifacts: Mapping[str, str]) -> dict[str, Any]:
    descriptors: list[dict[str, Any]] = []
    for kind, path_text in sorted(artifacts.items()):
        path = Path(path_text)
        try:
            stat = path.stat()
        except OSError:
            continue
        descriptors.append(
            {
                "kind": str(kind),
                "path": str(path.resolve()),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )
    encoded = json.dumps(
        [
            {
                "kind": item["kind"],
                "size": item["size"],
                "mtime_ns": item["mtime_ns"],
            }
            for item in descriptors
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "artifacts": descriptors,
    }


def _metadata_field(
    source: Mapping[str, Any],
    name: str,
    *,
    aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    raw: Any = None
    for key in (name, *aliases):
        candidate = source.get(key)
        if candidate is not None and candidate != "":
            raw = candidate
            break
    if isinstance(raw, Mapping):
        value = raw.get("value")
        status = str(raw.get("status") or "unknown")
        origin = str(raw.get("source") or "")
    else:
        value = raw
        status = str(source.get(f"{name}_status") or "")
        origin = str(source.get(f"{name}_source") or "")
        if not status:
            status = "confirmed" if value is not None and value != "" else "unknown"
    if status not in _METADATA_STATES:
        status = "unknown"
    if value is None or value == "":
        value = None
        status = "unknown"
    elif not origin:
        origin = (
            "comparison_request"
            if status == "confirmed"
            else "directory_name"
            if status == "suggested"
            else ""
        )
    return {"value": value, "status": status, "source": origin}


def build_comparison_source_record(
    source: Mapping[str, Any],
    *,
    primary_kind: str,
) -> dict[str, Any]:
    """Build one auditable source record for Species or Reaction comparison."""

    primary_key = f"{primary_kind}_file"
    primary_text = str(
        source.get(primary_key)
        or source.get(primary_kind)
        or source.get("primary_path")
        or ""
    ).strip()
    primary = Path(primary_text).expanduser().resolve(strict=False)
    artifacts = comparison_artifacts(str(primary)) if primary_text else {}
    if primary_text and primary.is_file():
        artifacts.setdefault(primary_kind, str(primary))

    dataset_id = str(source.get("dataset_id") or "").strip()
    if not dataset_id and primary_text:
        dataset_id = dataset_id_for_source(str(primary))

    model_iteration = _metadata_field(
        source,
        "model_iteration",
        aliases=("model", "iteration"),
    )
    simulation_condition = _metadata_field(
        source,
        "simulation_condition",
        aliases=("condition", "group_name"),
    )
    replicate = _metadata_field(source, "replicate")
    try:
        conversion = (
            load_timestep_ps(str(primary))
            if primary_text and primary.is_file()
            else None
        )
        physical_time_status = "confirmed" if conversion is not None else "unknown"
    except TrajectoryFrameError:
        conversion = None
        physical_time_status = "invalid"
    timeline = str(artifacts.get("timeline") or "")
    reactionevent = str(artifacts.get("reactionevent") or "")
    molecules = str(artifacts.get("molecules") or "")
    molecular_available = bool(timeline or (reactionevent and molecules))
    identity_basis = (
        "exact_rng_species"
        if primary_kind == "species"
        else "exact_directed_reaction_type_with_stoichiometric_multiplicity"
    )
    supplied_revision = source.get("source_revision")
    source_revision = (
        dict(supplied_revision)
        if isinstance(supplied_revision, Mapping)
        and str(supplied_revision.get("fingerprint") or "").strip()
        else _source_revision(artifacts)
    )
    return {
        "schema_version": COMPARISON_SOURCE_SCHEMA_VERSION,
        "source_id": dataset_id or str(primary),
        "label": str(source.get("label") or source.get("name") or primary.stem),
        "dataset_id": dataset_id,
        "source_revision": source_revision,
        "primary_kind": primary_kind,
        "primary_path": str(primary) if primary_text else "",
        "artifacts": artifacts,
        "metadata": {
            "model_iteration": model_iteration,
            "simulation_condition": simulation_condition,
            "replicate": replicate,
        },
        "processing": rng_processing_metadata(artifacts),
        "identity_basis": identity_basis,
        "time_basis": {
            "source_timestep": "available" if primary_text and primary.is_file() else "missing",
            "timestep_ps": conversion,
            "physical_time_status": physical_time_status,
        },
        "evidence": {
            "species_abundance": bool(artifacts.get("species")),
            "reaction_network": bool(artifacts.get("reaction")),
            "timed_molecular_evidence": molecular_available,
            "timed_molecular_evidence_kind": (
                "native_timeline_hdf5"
                if timeline
                else "compatible_csv_pair"
                if reactionevent and molecules
                else "none"
            ),
        },
    }


def comparison_metadata_text(record: Mapping[str, Any]) -> dict[str, str]:
    """Return compact display strings without turning unknowns into facts."""

    metadata = record.get("metadata") or {}

    def display(name: str) -> str:
        field = metadata.get(name) or {}
        value = field.get("value")
        status = str(field.get("status") or "unknown")
        if value is None or value == "":
            return "未知"
        suffix = "（待确认）" if status == "suggested" else ""
        return f"{value}{suffix}"

    processing = record.get("processing") or {}
    fields = processing.get("fields") or {}
    processing_values = [
        f"{name}={item.get('value')}"
        for name, item in fields.items()
        if isinstance(item, Mapping) and item.get("value") is not None
    ]
    evidence = record.get("evidence") or {}
    evidence_values = ["物种丰度"] if evidence.get("species_abundance") else []
    if evidence.get("reaction_network"):
        evidence_values.append("反应网络")
    if evidence.get("timed_molecular_evidence"):
        evidence_values.append("分子时序")
    return {
        "model_iteration_display": display("model_iteration"),
        "simulation_condition_display": display("simulation_condition"),
        "replicate_display": display("replicate"),
        "processing_summary": "；".join(processing_values) or "未知",
        "identity_basis_text": str(record.get("identity_basis") or "未知"),
        "evidence_scope_text": "、".join(evidence_values) or "无可用证据",
        "time_basis_text": (
            f"1 timestep = {(record.get('time_basis') or {}).get('timestep_ps')} ps"
            if (record.get("time_basis") or {}).get("physical_time_status") == "confirmed"
            else "仅原始 timestep；物理时间未知"
        ),
    }
