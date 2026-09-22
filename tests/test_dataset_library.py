from pathlib import Path

import pytest
from reacnet_scope import services as svc, dir_browser


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(dir_browser, 'ALLOWED_ROOTS', [tmp_path])
    monkeypatch.setenv('REACNET_SCOPE_CACHE_DIR', str(tmp_path / 'cache'))
    paths = []
    for name in ('A', 'B'):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'run.reactionabcd').write_text('1 CCO->COC\n')
        (folder / 'run.species').write_text('Timestep 0: CCO 1\n')
        paths.append(str(folder))
    return paths


def test_import_folders_keeps_independent_identities_and_reports_partial_failures(folders):
    empty = Path(folders[0]).parent / 'empty'
    empty.mkdir()
    report = svc.inspect_dataset_folders([folders[0], folders[1], folders[0], str(empty), '/outside'])
    assert [e['label'] for e in report['entries']] == ['A', 'B']
    assert len({e['dataset_id'] for e in report['entries']}) == 2
    assert len(report['errors']) == 2
    for path in folders:
        assert (Path(path) / 'run.reactionabcd').read_text() == '1 CCO->COC\n'


def test_ambiguous_folder_is_not_silently_merged(folders):
    (Path(folders[0]) / 'second.reactionabcd').write_text('1 O->O\n')
    report = svc.inspect_dataset_folders([folders[0]])
    assert report['entries'] == []
    assert len(report['errors']) == 1


def test_import_does_not_evict_older_catalog_entries_at_recent_history_limit():
    records = [{'folder': f'/data/{i}', 'base': f'/data/{i}/run', 'label': str(i)} for i in range(25)]
    normalized = svc.normalise_dataset_library([*records, records[0]])
    assert len(normalized) == 25
