"""Compare user-confirmed exact Species across prepared abundance indexes."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from reacnet_scope.comparison_sources import (
    build_comparison_source_record,
    comparison_metadata_text,
)
from reacnet_scope.evidence_services import build_species_evolution
from reacnet_scope.indexes import (
    IndexBuildInProgressError,
    IndexInvalidError,
    IndexNotReadyError,
    IndexStaleError,
)
from reacnet_scope.queries import collect_species_totals
from reacnet_scope.service_types import ServiceError


_INDEX_ERRORS = (IndexNotReadyError, IndexBuildInProgressError, IndexStaleError, IndexInvalidError)
_STATUS_LABELS = {
    "ready": "有结果", "zero": "结果为零", "target_not_found": "未找到目标",
    "missing_index": "缺少所需索引", "missing_source": "缺少来源文件",
    "time_conversion_missing": "缺少时间换算",
}


def species_compare_catalog(species_file: str) -> dict[str, Any]:
    """List exact identities from one published index for manual selection."""
    path = str(Path(species_file).expanduser().resolve())
    if not Path(path).is_file():
        return {"status": "missing_source", "options": [], "message": "缺少 Species 文件"}
    try:
        totals = collect_species_totals(path)
    except _INDEX_ERRORS as exc:
        return {"status": "missing_index", "options": [], "message": f"Species Abundance Index 未就绪：{exc}"}
    return {
        "status": "ready",
        "options": [
            {"label": f"{smiles} · 总数 {count}", "value": smiles}
            for smiles, count in sorted(totals.items())
        ],
        "message": f"{len(totals)} 个精确物种",
    }


def compare_species_sources(sources: list[dict[str, Any]], *, x_axis: str = "step") -> dict[str, Any]:
    """Return independent raw curves and summaries; never infer target equivalence."""
    if len(sources) < 2:
        raise ServiceError("请至少添加两个数据来源", reason="too_few_sources")
    if x_axis not in {"step", "ps", "ns"}:
        raise ServiceError("时间轴无效", reason="invalid_axis")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        path_text = str(source.get("species_file") or "").strip()
        target = str(source.get("target_smiles") or "").strip()
        label = str(source.get("label") or "").strip()
        if not path_text or not target:
            raise ServiceError("每个来源都必须选择 Species 文件和精确目标", reason="incomplete_source")
        path = str(Path(path_text).expanduser().resolve())
        if path in seen:
            raise ServiceError("同一 Species 文件不能重复添加", reason="duplicate_source")
        seen.add(path)
        normalized.append(
            {
                **source,
                "species_file": path,
                "label": label or Path(path).stem,
                "target_smiles": target,
            }
        )

    rows: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    source_records = [
        build_comparison_source_record(source, primary_kind="species")
        for source in normalized
    ]
    physical_time_ready = all(
        (record.get("time_basis") or {}).get("physical_time_status")
        == "confirmed"
        for record in source_records
    )
    for source, source_record in zip(normalized, source_records):
        path = source["species_file"]
        row: dict[str, Any] = {
            **source,
            **comparison_metadata_text(source_record),
            "dataset_id": source_record["dataset_id"],
            "source_revision": (source_record.get("source_revision") or {}).get(
                "fingerprint", ""
            ),
            "status": "ready",
            "message": "",
            "initial": None,
            "final": None,
            "peak": None,
            "peak_time": None,
            "timestep_ps": (source_record.get("time_basis") or {}).get(
                "timestep_ps"
            ),
        }
        if not Path(path).is_file():
            row.update(status="missing_source", message="缺少 Species 文件")
            row["status_text"] = _STATUS_LABELS[row["status"]]
            rows.append(row)
            continue
        try:
            totals = collect_species_totals(path)
            if source["target_smiles"] not in totals:
                row.update(status="target_not_found", message="此来源未找到所选精确物种")
                row["status_text"] = _STATUS_LABELS[row["status"]]
                rows.append(row)
                continue
            conversion = (source_record.get("time_basis") or {}).get("timestep_ps")
            if x_axis != "step" and conversion is None:
                conversion_status = str(
                    (source_record.get("time_basis") or {}).get(
                        "physical_time_status"
                    )
                    or "unknown"
                )
                row.update(
                    status="time_conversion_missing",
                    message=(
                        "已保存的 timestep → ps 换算无效，请重新确认"
                        if conversion_status == "invalid"
                        else "请先为此来源确认 timestep → ps 换算"
                    ),
                )
                row["status_text"] = _STATUS_LABELS[row["status"]]
                rows.append(row)
                continue
            evolution = build_species_evolution(
                {"species": path},
                [f"smiles:{source['target_smiles']}"],
                x_axis=x_axis,
                normalize="none",
                smooth_window=1,
                downsample=0,
                max_curves=1,
            )
        except _INDEX_ERRORS as exc:
            row.update(status="missing_index", message=f"Species Abundance Index 未就绪：{exc}")
            row["status_text"] = _STATUS_LABELS[row["status"]]
            rows.append(row)
            continue
        except ServiceError as exc:
            if exc.reason not in {"species_index_not_ready", "missing_file"}:
                raise
            row.update(status="missing_index", message=exc.message)
            row["status_text"] = _STATUS_LABELS[row["status"]]
            rows.append(row)
            continue
        evolution_curves = evolution.get("curves") or []
        if not evolution_curves:
            row.update(status="missing_index", message="Species Abundance Index 没有时间点")
            row["status_text"] = _STATUS_LABELS[row["status"]]
            rows.append(row)
            continue
        evolution_curve = evolution_curves[0]
        timesteps = [int(value) for value in evolution_curve["source_timesteps"]]
        values = [
            int(value) if float(value).is_integer() else float(value)
            for value in evolution_curve["raw_values"]
        ]
        x_values = [
            int(value) if x_axis == "step" and float(value).is_integer() else float(value)
            for value in evolution_curve["raw_x_values"]
        ]
        peak_index = max(range(len(values)), key=lambda index: values[index])
        row.update(status="zero" if not any(values) else "ready",
                   message="结果为零" if not any(values) else "",
                   initial=values[0], final=values[-1], peak=values[peak_index],
                   peak_time=x_values[peak_index], timestep_ps=conversion)
        row["status_text"] = _STATUS_LABELS[row["status"]]
        rows.append(row)
        curves.append(
            {
                "species_file": path,
                "label": source["label"],
                "target_smiles": source["target_smiles"],
                "dataset_id": source_record["dataset_id"],
                "source_revision": (source_record.get("source_revision") or {}).get(
                    "fingerprint", ""
                ),
                "analyzed_frames": list(range(len(timesteps))),
                "source_timesteps": timesteps,
                "x_values": x_values,
                "values": values,
            }
        )

    if x_axis != "step" and not physical_time_ready:
        curves = []
        comparability = {
            "status": "not_comparable",
            "physical_time": False,
            "message": (
                "至少一个来源缺少已确认的 timestep → ps 换算；"
                "本次不绘制部分物理时间曲线。"
            ),
        }
    else:
        comparability = {
            "status": "comparable" if x_axis != "step" else "independent_raw_timelines",
            "physical_time": x_axis != "step",
            "message": (
                "所有来源均有已确认的物理时间换算。"
                if x_axis != "step"
                else "各来源按独立原始 timestep 展示，不声明物理时间对齐。"
            ),
        }
    query_sources = [
        {
            key: value
            for key, value in source.items()
            if key
            in {
                "species_file",
                "label",
                "target_smiles",
                "model_iteration",
                "model_iteration_status",
                "simulation_condition",
                "simulation_condition_status",
                "replicate",
                "replicate_status",
                "dataset_id",
                "source_revision",
            }
        }
        for source in normalized
    ]
    return {
        "schema_version": 2,
        "query": {"x_axis": x_axis, "time_align": "raw", "sources": query_sources},
        "x_name": {"step": "source_timestep", "ps": "time_ps", "ns": "time_ns"}[x_axis],
        "identity_basis": "exact_rng_species_per_source",
        "comparability": comparability,
        "source_records": source_records,
        "summary": rows,
        "curves": curves,
    }


def species_comparison_zip(payload: dict[str, Any]) -> bytes:
    """Export the displayed raw series, summaries and exact query as one archive."""
    x_name = str(payload["x_name"])
    series = io.StringIO()
    writer = csv.writer(series)
    include_physical_time = x_name != "source_timestep"
    header = ["species_file", "label", "target_smiles", "analyzed_frame", "source_timestep"]
    if include_physical_time:
        header.append(x_name)
    writer.writerow([*header, "abundance"])
    for curve in payload["curves"]:
        for frame, (step, x, value) in enumerate(zip(curve["source_timesteps"], curve["x_values"], curve["values"])):
            row = [curve["species_file"], curve["label"], curve["target_smiles"], frame, step]
            if include_physical_time:
                row.append(x)
            writer.writerow([*row, value])
    summary = io.StringIO()
    fields = [
        "species_file", "label", "dataset_id", "source_revision",
        "target_smiles", "model_iteration_display",
        "simulation_condition_display", "replicate_display",
        "identity_basis_text", "processing_summary", "evidence_scope_text",
        "time_basis_text", "status", "status_text", "message", "initial",
        "final", "peak", "peak_time", "timestep_ps",
    ]
    summary_writer = csv.DictWriter(summary, fieldnames=fields, extrasaction="ignore")
    summary_writer.writeheader()
    summary_writer.writerows(payload["summary"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("curves.csv", series.getvalue())
        archive.writestr("summary.csv", summary.getvalue())
        archive.writestr("query.json", json.dumps(payload["query"], ensure_ascii=False, indent=2))
        archive.writestr(
            "sources.json",
            json.dumps(payload.get("source_records") or [], ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "comparability.json",
            json.dumps(payload.get("comparability") or {}, ensure_ascii=False, indent=2),
        )
    return buffer.getvalue()
