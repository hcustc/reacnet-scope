"""Explicit RNG artifact collections, independent of source directory layout.

Discovery produces suggestions, never silently merges conflicting sources. The
small collection definition is published separately from derived index manifests.
Source artifacts remain read-only and keep their existing source-index workspaces.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Iterable

from .datasets import ARTIFACT_SUFFIXES
from .dir_browser import validate_browse_path, DirBrowserError
from .service_types import ServiceError

MAX_FILES = 500
MAX_ENTRIES = 10000
COLLECTION_SUFFIX = ".rng-dataset.json"
ROLES = dict((role, suffix) for suffix, role in ARTIFACT_SUFFIXES)
ROLE_LABELS = {
    "timeline": "反应与分子证据", "reactionevent": "反应事件",
    "molecules": "分子证据", "reaction": "聚合反应",
    "species": "物种丰度", "trajectory": "坐标轨迹",
}


def collection_root() -> Path:
    from .indexes import _cache_root
    return _cache_root() / "collections"


def is_collection_path(value: str | Path) -> bool:
    path = Path(value).expanduser().absolute()
    return bool(
        path.parent == collection_root()
        and re.fullmatch(r"[0-9a-f]{20}\.rng-dataset\.json", path.name)
    )


def artifact_role(path: str | Path) -> tuple[str, str]:
    name = Path(path).name
    if name.startswith("._"):
        return "", ""
    for suffix, role in ARTIFACT_SUFFIXES:
        if name.lower().endswith(suffix):
            stem = name[:-len(suffix)]
            if stem.lower().endswith(".lammpstrj"):
                stem = stem[:-len(".lammpstrj")]
            return role, stem or name
    return "", ""


def _source_path(value: str) -> Path:
    try:
        return validate_browse_path(value)
    except DirBrowserError as exc:
        raise ServiceError(exc.message, reason=exc.reason) from exc


def collect_files(inputs: Iterable[str], *, recursive: bool = False) -> dict[str, Any]:
    """Bounded, permission-checked expansion; truncation and rejected inputs remain visible."""
    paths: set[str] = set()
    errors: list[dict[str, str]] = []
    visited: set[str] = set()
    pending = list(inputs)
    if len(pending) > MAX_FILES:
        raise ServiceError(f"一次最多添加 {MAX_FILES} 个输入位置", reason="selection_limit")
    entries = 0
    truncated = False
    while pending:
        raw = str(pending.pop(0) or "").strip()
        if not raw:
            continue
        try:
            path = _source_path(raw)
            if str(path) in visited:
                continue
            visited.add(str(path))
            if path.is_file():
                role, _ = artifact_role(path)
                if not role:
                    raise ServiceError("不支持的 RNG 证据文件", reason="unsupported_artifact")
                paths.add(str(path))
            elif path.is_dir():
                with os.scandir(path) as children:
                    for child in children:
                        entries += 1
                        if entries > MAX_ENTRIES or len(paths) + len(pending) >= MAX_FILES:
                            truncated = True
                            break
                        if child.name.startswith("."):
                            continue
                        if child.is_file() and artifact_role(child.name)[0]:
                            pending.append(child.path)
                        elif recursive and child.is_dir(follow_symlinks=False):
                            pending.append(child.path)
            else:
                raise ServiceError("文件或目录不存在", reason="not_found")
        except (ServiceError, OSError) as exc:
            errors.append({"path": raw, "message": str(exc), "reason": getattr(exc, "reason", "read_error")})
        if len(paths) >= MAX_FILES:
            truncated = truncated or bool(pending)
            break
    return {"paths": sorted(paths), "errors": errors, "truncated": truncated}


def browse_import_files(location: str) -> dict[str, Any]:
    """One bounded server-side file picker, shared by multi-file and folder addition."""
    root = _source_path(location)
    if not root.is_dir():
        raise ServiceError("请选择要浏览的文件夹", reason="not_directory")
    directories, files = [], []
    truncated = False
    with os.scandir(root) as entries:
        for number, item in enumerate(entries):
            if number >= MAX_ENTRIES:
                truncated = True
                break
            if item.name.startswith("."):
                continue
            try:
                path = _source_path(item.path)
                if item.is_dir(follow_symlinks=False):
                    directories.append({"label": item.name, "value": str(path)})
                elif path.is_file() and artifact_role(path)[0]:
                    files.append({"label": item.name, "value": str(path), "size": path.stat().st_size})
            except (ServiceError, OSError):
                continue
            if len(directories) + len(files) >= MAX_FILES:
                truncated = True
                break
    try:
        parent = str(_source_path(str(root.parent)))
    except ServiceError:
        parent = ""
    return {"path": str(root), "parent": parent, "directories": sorted(directories, key=lambda x: x["label"]),
            "files": sorted(files, key=lambda x: x["label"]), "truncated": truncated}


def preview_file_collection(paths: Iterable[str], assignments: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Suggest groups using names; report conflicting roles instead of choosing a winner."""
    values = list(dict.fromkeys(str(p) for p in paths))
    if len(values) > MAX_FILES:
        raise ServiceError(f"一次最多选择 {MAX_FILES} 个文件", reason="selection_limit")
    rows: list[dict[str, Any]] = []
    assigned = dict(assignments or {})
    for value in values:
        role, stem = artifact_role(value)
        row: dict[str, Any] = {"path": value, "role": role, "stem": stem,
                               "label": Path(value).name, "message": ""}
        try:
            path = _source_path(value)
            if not path.is_file():
                raise ServiceError("文件不存在或已不可用", reason="missing_source")
            if not role:
                raise ServiceError("不支持的证据文件", reason="unsupported_artifact")
            stat = path.stat()
            row.update(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        except (ServiceError, OSError) as exc:
            row["message"] = str(exc)
        rows.append(row)
    # Repeated roles under one basename usually denote independent runs.
    duplicates: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["stem"], row["role"])
        if key in seen:
            duplicates.add(row["stem"])
        seen.add(key)
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        suggested = row["stem"]
        if suggested in duplicates:
            suggested = f"{suggested} · {Path(row['path']).parent}"
        group = str(assigned.get(row["path"], suggested) or "").strip()[:240]
        row["group"] = group
        if not group:
            continue
        target = groups.setdefault(group, {"key": group, "label": group, "files": [], "artifact_paths": {}, "errors": []})
        target["files"].append(row)
        if row["message"]:
            target["errors"].append(row["message"])
        elif row["role"] in target["artifact_paths"]:
            target["errors"].append(f"{ROLE_LABELS[row['role']]}存在多个来源，请移除冲突项或分别归组。")
        else:
            target["artifact_paths"][row["role"]] = row["path"]
    for group in groups.values():
        artifacts = group["artifact_paths"]
        if not any(key in artifacts for key in ("species", "reaction", "timeline", "reactionevent")):
            group["errors"].append("需要物种或反应证据；分子文件和坐标不能独立建立分析数据集。")
        group["association_notice"] = "按文件名建议归组；请核对运行归属。来源一致性仍由证据读取与准备验证。"
        group["valid"] = not group["errors"]
    encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
    return {"files": rows, "groups": list(groups.values()), "revision": hashlib.sha256(encoded).hexdigest()}


def collection_candidate(artifacts: Mapping[str, str], label: str, *, existing_base: str = "") -> dict[str, Any]:
    mapping = _validated_artifacts(artifacts)
    if existing_base and not is_collection_path(existing_base):
        raise ServiceError("只能补充已登记的文件集合", reason="invalid_collection")
    identity = Path(existing_base).name[:-len(COLLECTION_SUFFIX)] if existing_base else hashlib.sha256(
        json.dumps(mapping, sort_keys=True).encode()).hexdigest()[:20]
    base = collection_root() / f"{identity}{COLLECTION_SUFFIX}"
    old = read_collection(str(base))
    return {"folder": str(base.parent), "base": str(base), "label": str(label or "RNG 数据集")[:240],
            "artifact_paths": mapping, "collection_id": identity,
            "expected_definition": definition_revision(old) if old else ""}


def _validated_artifacts(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or not values or set(values) - set(ROLES):
        raise ServiceError("文件角色映射无效", reason="invalid_collection")
    result = {}
    for role, value in values.items():
        path = _source_path(str(value))
        if not path.is_file() or artifact_role(path)[0] != role:
            raise ServiceError(f"来源不存在或角色不符：{path}", reason="invalid_artifact")
        result[role] = str(path)
    if not set(result) & {"species", "reaction", "timeline", "reactionevent"}:
        raise ServiceError("请选择 RNG 物种或反应证据", reason="missing_evidence")
    return result


def definition_revision(record: Mapping[str, Any] | None) -> str:
    if not record:
        return ""
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_collection(base: str, *, validate_sources: bool = False) -> dict[str, Any] | None:
    if not is_collection_path(base):
        return None
    path = Path(base)
    if not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("invalid collection definition")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("schema_version") != 1 or record.get("dataset_id") != path.name[:-len(COLLECTION_SUFFIX)]:
            raise ValueError("incompatible collection definition")
        mapping = record.get("artifact_paths")
        if not isinstance(mapping, dict) or not mapping or set(mapping) - set(ROLES):
            raise ValueError("invalid artifact mapping")
        if any(not isinstance(value, str) or not Path(value).is_absolute() for value in mapping.values()):
            raise ValueError("artifact paths must be absolute")
        if validate_sources:
            _validated_artifacts(mapping)
        return record
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        raise ServiceError(f"数据集关联记录无法读取：{exc}", reason="invalid_collection") from exc


def _publish_collection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Compare-and-swap a validated selection after its tab transaction succeeds."""
    from .indexes import _workspace_identity_lock
    base = str(candidate.get("base") or "")
    if not is_collection_path(base):
        raise ServiceError("数据集引用无效", reason="invalid_collection")
    mapping = _validated_artifacts(candidate.get("artifact_paths") or {})
    target = Path(base)
    record = {"schema_version": 1, "dataset_id": target.name[:-len(COLLECTION_SUFFIX)],
              "label": str(candidate.get("label") or "RNG 数据集"), "artifact_paths": mapping}
    target.parent.mkdir(parents=True, exist_ok=True)
    with _workspace_identity_lock(target):
        current = read_collection(base)
        if current == record:
            return record
        if definition_revision(current) != str(candidate.get("expected_definition") or ""):
            raise ServiceError("文件关联已被另一请求更新，请重新检查。", reason="collection_changed")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(record, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return record


def publish_collection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Report recoverable workspace failures without losing the selection draft."""
    from .indexes import IndexBuildInProgressError
    try:
        return _publish_collection(candidate)
    except IndexBuildInProgressError as exc:
        raise ServiceError("数据集关联正在由另一请求保存，请稍后重试。", reason="collection_busy") from exc
    except OSError as exc:
        raise ServiceError(f"无法保存数据集关联，请检查工作区权限或磁盘空间：{exc}", reason="workspace_write_failed") from exc
