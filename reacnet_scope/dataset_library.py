"""Validate independent RNG folders for a browser's reusable dataset catalog."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .service_types import ServiceError

MAX_IMPORT_FOLDERS = 100


def normalise_dataset_library(records: Any) -> list[dict]:
    """Keep references only; saved records never authorize filesystem access."""
    result = {}
    for item in records if isinstance(records, list) else []:
        if not isinstance(item, dict) or not item.get('folder') or not item.get('base'):
            continue
        folder, base = str(item['folder']), str(item['base'])
        if not os.path.isabs(folder) or not os.path.isabs(base):
            continue
        result[base] = {'folder': folder, 'base': base,
                        'label': str(item.get('label') or Path(folder).name),
                        'dataset_id': str(item.get('dataset_id') or '')}
    return list(result.values())


def inspect_dataset_folders(paths: list[str]) -> dict:
    """Check each explicitly selected folder without merging runs or building indexes."""
    from .workspace_services import resolve_dataset_folder_candidate
    from .dataset_context import validate_dataset_candidate

    if not isinstance(paths, list) or not paths or len(paths) > MAX_IMPORT_FOLDERS:
        raise ServiceError(f'每次请选择 1–{MAX_IMPORT_FOLDERS} 个 RNG 文件夹。', reason='invalid_folder_count')
    entries, errors, seen = [], [], set()
    for path in paths:
        try:
            candidate = resolve_dataset_folder_candidate(str(path))
            validation = validate_dataset_candidate(candidate['folder'], candidate['base'])
            if candidate['base'] in seen:
                continue
            seen.add(candidate['base'])
            entries.append({**candidate, 'dataset_id': validation['dataset_id']})
        except (ServiceError, OSError, ValueError) as exc:
            errors.append({'path': str(path), 'message': str(exc)})
    return {'entries': normalise_dataset_library(entries), 'errors': errors}
