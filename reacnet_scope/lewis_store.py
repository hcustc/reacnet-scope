"""Revision-bound Lewis annotations, prepared offline and queried by identity."""

from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import closing
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .indexes import (IndexInvalidError, IndexNotReadyError, IndexStaleError,
                      _exclusive_build_lock, _readonly_connection, resolve_dataset_paths)
from .lewis import LewisOptions, annotate_lewis, lewis_alias_key, method_metadata, LEWIS_SCHEMA


_ROLES = ("reaction", "species", "timeline", "reactionevent", "molecules")


def _source(artifacts):
    for role in ("reaction", "species"):
        path = str(artifacts.get(role) or "")
        if path and Path(path).is_file():
            return path
    raise IndexNotReadyError("Lewis 注释需要 .reactionabcd 或 .species 代表结构来源")


def annotation_path(artifacts, *, create=False):
    return resolve_dataset_paths(_source(artifacts), persist_identity=create).workspace_dir / "lewis.sqlite3"


def annotation_revision(artifacts):
    paths = {role: str(Path(artifacts[role]).resolve()) for role in _ROLES
             if artifacts.get(role) and Path(artifacts[role]).is_file()}
    for directory in sorted({str(Path(p).parent) for p in paths.values()}):
        for name in ("rng_run.json", "timed_output_validation.json"):
            paths[f"metadata:{directory}/{name}"] = str(Path(directory) / name)
    sources = {}
    for role, path in paths.items():
        try:
            stat = Path(path).stat()
            sources[role] = {"path": path, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        except FileNotFoundError:
            sources[role] = {"path": path, "missing": True}
    return {"fingerprint": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
            "artifacts": sources}


def _meta(connection):
    try:
        return {key: json.loads(value) for key, value in connection.execute("SELECT key,value FROM meta")}
    except (sqlite3.Error, ValueError) as exc:
        raise IndexInvalidError("Lewis 注释元数据无效，请重建") from exc


def annotation_status(artifacts, *, options=None):
    try:
        path = annotation_path(artifacts)
    except IndexNotReadyError as exc:
        return {"state": "missing_source", "message": str(exc)}
    if not path.is_file():
        return {"state": "missing", "message": "Lewis 注释尚未准备"}
    try:
        with closing(_readonly_connection(path)) as connection:
            meta = _meta(connection)
            connection.execute("SELECT original_species,alias_key,status,payload FROM annotations LIMIT 0")
        if meta.get("schema") != LEWIS_SCHEMA or meta.get("state") != "ready":
            return {"state": "invalid", "message": "Lewis 注释未完整发布或版本不兼容"}
        if (not isinstance(meta.get("publication_id"), str) or not meta["publication_id"]
                or not isinstance(meta.get("options"), dict) or not isinstance(meta.get("statuses"), dict)
                or not isinstance(meta.get("count"), int) or meta["count"] < 0):
            return {"state": "invalid", "message": "Lewis 注释元数据不完整，请重建"}
        if meta.get("revision") != annotation_revision(artifacts) or meta.get("method") != method_metadata():
            return {"state": "stale", "message": "来源或方法版本已变化，请重新准备 Lewis 注释"}
        if options is not None and meta.get("options") != asdict(options):
            return {"state": "stale", "message": "Lewis 模型假设或预算已变化，请重新准备"}
        return {**meta, "state": "ready", "index_path": str(path)}
    except (sqlite3.Error, IndexInvalidError, OSError) as exc:
        return {"state": "invalid", "message": str(exc)}


def _required(artifacts, options=None):
    status = annotation_status(artifacts, options=options)
    if status["state"] == "stale":
        raise IndexStaleError(status["message"])
    if status["state"] == "invalid":
        raise IndexInvalidError(status["message"])
    if status["state"] != "ready":
        raise IndexNotReadyError(status["message"])
    return status


def _annotation_record(payload, original_species):
    try:
        record = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise IndexInvalidError("Lewis 注释记录无效，请重建") from exc
    if (not isinstance(record, dict) or record.get("schema_version") != LEWIS_SCHEMA
            or record.get("original_species") != original_species or not record.get("status")):
        raise IndexInvalidError("Lewis 注释记录的身份或版本无效，请重建")
    return record


def _representatives(artifacts, tick):
    """Preparation-only streaming; no full network or trajectory materialization."""
    from .composition import _parse_species_line
    from .network import parse_reactionabcd
    for role in ("reaction", "species"):
        path = str(artifacts.get(role) or "")
        if not path or not Path(path).is_file():
            continue
        with open(path, "rb") as source:
            while True:
                raw = source.readline(1_048_577)
                if not raw:
                    break
                tick()
                if len(raw) > 1_048_576:
                    raise ValueError("代表结构来源单行超过 1 MiB；本次注释未发布")
                if role == "species":
                    parsed = _parse_species_line(raw)
                    if parsed:
                        for smiles, _count in parsed[1]:
                            yield smiles, f"{role}:{path}"
                else:
                    for reaction in parse_reactionabcd(io.StringIO(raw.decode("utf-8")), min_tp=0):
                        for smiles in (*reaction.reactant_smiles, *reaction.product_smiles):
                            yield smiles, f"{role}:{path}"


def prepare_lewis_annotations(artifacts: dict, *, options: LewisOptions,
                              max_task_seconds: float = 300, max_species: int = 10_000,
                              progress_callback=None, rebuild=False, expected_revision="") -> dict:
    """Atomically publish a complete catalogue of outcomes, including failures.

    Whole-task interruption/budget exhaustion never replaces a published
    catalogue. Per-structure unsupported/timeout outcomes remain inspectable.
    Incomplete builds restart; they are not published or used by readers.
    """
    if not options.assume_neutral and options.net_charge is None:
        raise ValueError("请明确选择中性片段假设，或提供已知净电荷")
    if not math.isfinite(max_task_seconds) or not 0 < max_task_seconds <= 86_400:
        raise ValueError("任务时间预算须在 (0, 86400] 秒范围内")
    if isinstance(max_species, bool) or not isinstance(max_species, int) or not 1 <= max_species <= 1_000_000:
        raise ValueError("物种预算须为 1–1000000 的整数")
    target = annotation_path(artifacts, create=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_build_lock(target):
        if expected_revision and annotation_revision(artifacts)["fingerprint"] != expected_revision:
            raise IndexStaleError("提交后 Lewis 来源发生变化，请重新准备")
        existing = annotation_status(artifacts, options=options)
        if existing["state"] == "ready" and not rebuild:
            return existing
        revision = annotation_revision(artifacts)
        from .analysis_services import rng_processing_metadata
        processing = rng_processing_metadata(artifacts)
        raw_miso = processing["fields"]["miso"]["value"]
        miso = raw_miso if type(raw_miso) is int and raw_miso in (0, 1, 2) else None
        staging = target.with_name(f".lewis.{os.getpid()}.building.sqlite3")
        staging.unlink(missing_ok=True)
        connection = sqlite3.connect(staging)
        started = time.monotonic()
        last_report = started - 1
        count = 0
        def tick(force=False, phase="collecting"):
            nonlocal last_report
            now = time.monotonic()
            if now - started >= max_task_seconds:
                raise TimeoutError("Lewis 整体任务达到时间预算；本次结果未发布")
            if force or now - last_report >= 0.25:
                if annotation_revision(artifacts) != revision:
                    raise IndexStaleError("Lewis 准备期间来源变化；本次结果未发布")
                if progress_callback:
                    progress_callback({"phase": phase, "message": f"Lewis 注释：{count} 个物种"})
                last_report = now
        try:
            connection.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE inputs(species TEXT PRIMARY KEY,source TEXT NOT NULL);
                CREATE TABLE annotations(original_species TEXT PRIMARY KEY,alias_key TEXT,
                                         status TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX aliases ON annotations(alias_key,original_species);
            """)
            for smiles, source in _representatives(artifacts, tick):
                inserted = connection.execute("INSERT OR IGNORE INTO inputs VALUES (?,?)", (smiles, source)).rowcount
                count += inserted
                if count > max_species:
                    raise ValueError("Lewis 物种总数超过任务预算；本次结果未发布")
            total, count = count, 0
            cache = {}
            statuses = {}
            for smiles, source in connection.execute("SELECT species,source FROM inputs ORDER BY species"):
                tick(force=True, phase="annotating")
                remaining = max_task_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Lewis 整体任务达到时间预算；本次结果未发布")
                effective = replace(options, max_seconds=min(options.max_seconds, remaining))
                result = annotate_lewis(smiles, options=effective, miso=miso,
                                        source_revision=revision, input_source=source, _cache=cache)
                # Preserve submitted options separately from a smaller remaining
                # task budget used for the final solve.
                result["requested_options"] = asdict(options)
                alias = lewis_alias_key(result["candidate_smiles"]) if result["candidate_smiles"] else None
                connection.execute("INSERT INTO annotations VALUES (?,?,?,?)",
                                   (smiles, alias, result["status"], json.dumps(result, ensure_ascii=False)))
                statuses[result["status"]] = statuses.get(result["status"], 0) + 1
                count += 1
                if len(cache) > 4096:
                    cache.clear()
            meta = {"schema": LEWIS_SCHEMA, "state": "ready", "revision": revision,
                    "publication_id": uuid.uuid4().hex,
                    "method": method_metadata(), "options": asdict(options), "count": total,
                    "statuses": statuses, "processing": processing, "catalog_complete": True,
                    "task_budget": {"max_seconds": max_task_seconds, "max_species": max_species},
                    "input_scope": "representative_catalogue_not_molecular_instance_audit"}
            connection.executemany("INSERT INTO meta VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
            connection.execute("DROP TABLE inputs")
            connection.commit()
            tick(force=True, phase="publishing")
            connection.close()
            os.replace(staging, target)
            return {**meta, "index_path": str(target)}
        finally:
            connection.close()
            staging.unlink(missing_ok=True)


def get_lewis_annotation(artifacts: dict, species: str, *, options=None) -> dict:
    meta = _required(artifacts, options)
    with closing(_readonly_connection(Path(meta["index_path"]))) as connection:
        connection.execute("BEGIN")
        if _meta(connection).get("publication_id") != meta["publication_id"]:
            raise IndexStaleError("读取期间 Lewis 注释已重新发布，请重试")
        row = connection.execute("SELECT payload FROM annotations WHERE original_species=?", (species,)).fetchone()
    return _annotation_record(row[0], species) if row else {"status": "not_evaluated", "reason": "species_not_in_prepared_catalogue",
                                          "original_species": species, "source_revision": meta["revision"]}


def search_lewis_annotations(artifacts: dict, alias: str = "", *, limit=50, offset=0, options=None) -> dict:
    meta = _required(artifacts, options)
    limit, offset = min(100, max(1, int(limit))), max(0, int(offset))
    where, parameters = ("alias_key=?", [lewis_alias_key(alias)]) if alias else ("1", [])
    with closing(_readonly_connection(Path(meta["index_path"]))) as connection:
        connection.execute("BEGIN")
        if _meta(connection).get("publication_id") != meta["publication_id"]:
            raise IndexStaleError("读取期间 Lewis 注释已重新发布，请重试")
        rows = connection.execute("SELECT original_species,payload FROM annotations WHERE " + where +
                                  " ORDER BY original_species LIMIT ? OFFSET ?",
                                  [*parameters, limit + 1, offset]).fetchall()
    return {"schema_version": LEWIS_SCHEMA, "rows": [_annotation_record(row[1], row[0]) for row in rows[:limit]],
            "has_more": len(rows) > limit, "limit": limit, "offset": offset,
            "query": {"alias": alias}, "source_revision": meta["revision"],
            "publication_id": meta["publication_id"],
            "method": meta["method"], "options": meta["options"]}


def export_lewis_annotations(artifacts: dict, *, format="json", alias="") -> str:
    """Export prepared annotations with identity and provenance, never solving."""
    if format not in {"json", "csv"}:
        raise ValueError("Lewis export format must be json or csv")
    first = search_lewis_annotations(artifacts, alias, limit=100)
    rows, page = list(first["rows"]), first
    while page["has_more"]:
        page = search_lewis_annotations(artifacts, alias, limit=100, offset=page["offset"] + 100)
        if page["publication_id"] != first["publication_id"]:
            raise IndexStaleError("导出期间 Lewis 注释发生变化")
        rows.extend(page["rows"])
    payload = {**first, "rows": rows, "has_more": False, "export_scope": "all_matching_annotations"}
    if format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    stream = io.StringIO()
    fields = ("original_species", "candidate_smiles", "status", "reason", "coverage", "annotation_json")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({**{key: row.get(key) for key in fields[:-1]},
                         "annotation_json": json.dumps(row, ensure_ascii=False, sort_keys=True)})
    return stream.getvalue()
