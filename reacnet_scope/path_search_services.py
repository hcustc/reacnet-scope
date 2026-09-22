"""Application boundary for the candidate workbench and Python clients."""
from __future__ import annotations

from contextlib import contextmanager
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .event_index import EVENT_EVIDENCE_STORE
from .indexes import IndexNotReadyError
from .path_search import (
    ATOM_TRANSFER_POLICY,
    CandidateReader,
    discover_indexed_candidates,
)
from .service_types import ServiceError
from .workspace_services import _event_artifact_paths
from .candidate_evidence import event_details, validate_carrier_chain


@contextmanager
def _reader(artifacts: Mapping[str, Any]):
    source, molecules = _event_artifact_paths(artifacts)
    if not source:
        raise ServiceError('缺少反应事件源；仅有聚合反应网络不能提供逐步事件证据。', reason='missing_event_source')
    reader = None
    try:
        opened = EVENT_EVIDENCE_STORE.open_required(source, molecules)
        reader = CandidateReader(opened)
        yield reader
    except (IndexNotReadyError, OSError, sqlite3.Error, ValueError) as exc:
        raise ServiceError(str(exc), reason='candidate_query_unavailable') from exc
    finally:
        if reader is not None:
            reader.close()


def candidate_search_status(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    try:
        with _reader(artifacts) as reader:
            if reader.carrier_policy == ATOM_TRANSFER_POLICY:
                message = '候选路径已就绪；每步主线产物均有局部原子继承证据。'
            else:
                message = ('仅有物种网络连通性，缺少分子原子关联；'
                           '结果可用于网络探索，不能解读为物质转化路线。')
            return {'available': True, 'message': message,
                    'carrier_policy': reader.carrier_policy,
                    'degraded': reader.carrier_policy != ATOM_TRANSFER_POLICY}
    except ServiceError as exc:
        return {'available': False, 'message': exc.message}


def search_candidate_species(artifacts: Mapping[str, Any], query: str) -> dict[str, Any]:
    with _reader(artifacts) as reader:
        return reader.species(query)


def candidate_source_revision(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    source, molecules = _event_artifact_paths(artifacts)
    with _reader(artifacts) as reader:
        paths = [source, molecules, str(reader.opened['index_path'])]
        return {str(Path(p).resolve()): {'size': Path(p).stat().st_size,
                'mtime_ns': str(Path(p).stat().st_mtime_ns)} for p in paths if p}


def search_candidate_paths(artifacts: Mapping[str, Any], start: str, **query: Any) -> dict[str, Any]:
    # Local import keeps the event-index build independent of services.
    from .analysis_services import rng_processing_metadata
    revision = candidate_source_revision(artifacts)
    with _reader(artifacts) as reader:
        result = discover_indexed_candidates(reader, start, **query)
    if candidate_source_revision(artifacts) != revision:
        raise ServiceError('查询期间数据版本已变化，请重新搜索。', reason='source_changed')
    result['source_revision'] = revision
    result['processing'] = rng_processing_metadata(artifacts)
    result['evidence_key'] = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
    return result


def candidate_step_events(artifacts: Mapping[str, Any], report: Mapping[str, Any],
                          signature: str, step_index: int, offset: int = 0) -> dict[str, Any]:
    if candidate_source_revision(artifacts) != report.get('source_revision'):
        raise ServiceError('来源版本已变化，请重新搜索候选路径。', reason='source_changed')
    path = next((p for p in report.get('paths', []) if p['signature_id'] == signature), None)
    if path is None or not 0 <= step_index < len(path['steps']) or offset < 0:
        raise ServiceError('所选路线或步骤已失效。', reason='bad_candidate_selection')
    source, molecules = _event_artifact_paths(artifacts)
    step = path['steps'][step_index]
    key = step['reaction_key']
    try:
        if step.get('transfer_basis') == ATOM_TRANSFER_POLICY:
            payload = EVENT_EVIDENCE_STORE.query_candidate_transfer_events(
                source, molecules, key, step['carried_from'], step['carried_to'],
                limit=25, offset=offset)
        else:
            payload = EVENT_EVIDENCE_STORE.query_events(
                source, molecules, key, limit=25, offset=offset)
    except (IndexNotReadyError, sqlite3.Error, OSError, ValueError) as exc:
        raise ServiceError(str(exc), reason='candidate_events_unavailable') from exc
    if candidate_source_revision(artifacts) != report.get('source_revision'):
        raise ServiceError('事件查询期间来源版本已变化，请重新搜索。', reason='source_changed')
    payload['reaction_key'] = key
    payload['signature_id'] = signature
    payload['step_index'] = step_index
    with _reader(artifacts) as reader:
        for row in payload['rows']:
            row['candidate_evidence'] = event_details(reader.connection, row['event_id'])
    if candidate_source_revision(artifacts) != report.get('source_revision'):
        raise ServiceError('事件查询期间来源版本已变化，请重新搜索。', reason='source_changed')
    return payload


def check_candidate_continuity(artifacts: Mapping[str, Any], report: Mapping[str, Any],
                               signature: str, *, max_states: int = 1000,
                               max_seconds: float = 5) -> dict[str, Any]:
    """Validate one selected route without changing identity or network order."""
    if candidate_source_revision(artifacts) != report.get('source_revision'):
        raise ServiceError('来源版本已变化，请重新搜索后检查。', reason='source_changed')
    path = next((p for p in report.get('paths', []) if p['signature_id'] == signature), None)
    if path is None:
        raise ServiceError('所选路线不存在。', reason='bad_candidate_selection')
    with _reader(artifacts) as reader:
        result = validate_carrier_chain(reader.connection, path, max_states=max_states, max_seconds=max_seconds)
    if candidate_source_revision(artifacts) != report.get('source_revision'):
        raise ServiceError('检查期间来源版本已变化，请重新搜索。', reason='source_changed')
    return dict(result, signature_id=signature, source_revision=report['source_revision'])


def candidate_paths_csv(report: Mapping[str, Any]) -> str:
    output = io.StringIO()
    fields = ['signature_id', 'step', 'carried_from', 'carried_to', 'reaction_key',
              'reactants', 'products', 'event_count', 'transfer_event_count',
              'max_shared_atoms', 'transfer_basis', 'continuous_md', 'query_complete',
              'quality', 'continuous_support', 'query', 'source_revision', 'truncation_reasons',
              'search_algorithm', 'path_prefix_budget', 'path_prefixes_examined']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for path in report.get('paths', []):
        for index, step in enumerate(path['steps'], 1):
            writer.writerow(dict(signature_id=path['signature_id'], step=index,
                carried_from=step['carried_from'], carried_to=step['carried_to'],
                reaction_key=step['reaction_key'], reactants=json.dumps(step['reactants']),
                products=json.dumps(step['products']), event_count=step['event_count'],
                transfer_event_count=step.get('transfer_event_count'),
                max_shared_atoms=step.get('max_shared_atoms'),
                transfer_basis=step.get('transfer_basis'),
                continuous_md=path['continuous_md'], query_complete=report['query_complete'],
                quality=json.dumps(step.get('quality', {})),
                continuous_support=json.dumps(path.get('continuous_support')),
                query=json.dumps(report['query']), source_revision=json.dumps(report['source_revision']),
                truncation_reasons=json.dumps(report['truncation_reasons']),
                search_algorithm=report.get('search_algorithm'),
                path_prefix_budget=report.get('path_prefix_budget'),
                path_prefixes_examined=report.get('path_prefixes_examined')))
    return output.getvalue()
