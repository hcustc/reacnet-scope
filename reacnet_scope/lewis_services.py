"""Shared Lewis annotation queries for the CLI and Dash."""

from .indexes import IndexNotReadyError
from .lewis_store import (annotation_status, get_lewis_annotation,
                          search_lewis_annotations, export_lewis_annotations, annotation_revision)
from .service_types import ServiceError


lewis_status = annotation_status


def lewis_source_fingerprint(artifacts):
    return annotation_revision(artifacts)["fingerprint"]


def lewis_task_status(folder, base):
    from .workspace_services import validate_browse_path
    from .prepare import discover_dataset, _read_preparation_task
    folder_path, base_path = validate_browse_path(folder), validate_browse_path(base)
    dataset = discover_dataset(str(folder_path), str(base_path))
    return _read_preparation_task(dataset, "lewis")


def lewis_detail(artifacts, species):
    status = annotation_status(artifacts)
    if status["state"] != "ready":
        return {"status": "not_evaluated", "index_state": status["state"],
                "reason": status.get("message", ""), "original_species": species}
    try:
        return get_lewis_annotation(artifacts, species)
    except IndexNotReadyError as exc:
        return {"status": "not_evaluated", "index_state": "stale", "reason": str(exc),
                "original_species": species}


def lewis_search(artifacts, alias="", *, limit=50, offset=0):
    try:
        return search_lewis_annotations(artifacts, alias, limit=limit, offset=offset)
    except IndexNotReadyError as exc:
        raise ServiceError(str(exc), reason="lewis_index_not_ready") from exc


def lewis_export(artifacts, *, format="json", alias=""):
    try:
        return export_lewis_annotations(artifacts, format=format, alias=alias)
    except IndexNotReadyError as exc:
        raise ServiceError(str(exc), reason="lewis_index_not_ready") from exc
