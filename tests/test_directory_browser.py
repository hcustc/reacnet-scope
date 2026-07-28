"""Tests for the remote directory browser: path validation and listing.

Run with::

    python -m unittest tests.test_directory_browser
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

import rng_tools.dir_browser as dir_browser
from rng_tools.dir_browser import (
    ALLOWED_ROOTS,
    DirBrowserError,
    get_allowed_roots,
    list_directory,
    validate_browse_path,
)
from scripts.webapp_dash import services as svc


# ======================================================================
# validate_browse_path
# ======================================================================


class ValidateBrowsePathTests(unittest.TestCase):
    def test_rejects_empty_path(self):
        with self.assertRaisesRegex(DirBrowserError, "路径不能为空"):
            validate_browse_path("")

    def test_resolves_tilde_to_home(self):
        resolved = validate_browse_path("~")
        self.assertEqual(resolved, Path.home())

    def test_rejects_paths_outside_allowed_roots(self):
        with self.assertRaisesRegex(DirBrowserError, "路径超出允许范围"):
            validate_browse_path("/tmp")

    def test_rejects_dotdot_traversal(self):
        with self.assertRaisesRegex(DirBrowserError, "路径超出允许范围"):
            validate_browse_path(str(Path.home() / ".." / ".." / ".." / "etc"))

    def test_allows_paths_inside_home(self):
        p = validate_browse_path(str(Path.home()))
        self.assertEqual(p, Path.home())

    def test_rejects_symlink_escaping_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "allowed"
            target = tmp_path / "target"
            root.mkdir()
            target.mkdir()
            link = root / f"escape_{os.getpid()}"
            import rng_tools.dir_browser as _db

            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [root]
            try:
                link.symlink_to(target)
                with self.assertRaisesRegex(DirBrowserError, "路径超出允许范围"):
                    validate_browse_path(str(link))
            finally:
                link.unlink(missing_ok=True)
                _db.ALLOWED_ROOTS = old_roots


# ======================================================================
# list_directory
# ======================================================================


class ListDirectoryTests(unittest.TestCase):
    def test_lists_subdirectories_sorted(self):
        data = list_directory(str(Path.home()))
        self.assertIn("current_path", data)
        self.assertIn("subdirs", data)
        names = [d["name"] for d in data["subdirs"]]
        self.assertEqual(names, sorted(names, key=str.casefold))

    def test_rejects_nonexistent_path(self):
        bad = str(Path.home() / "nonexistent_dir_xyz789")
        with self.assertRaisesRegex(DirBrowserError, "目录不存在"):
            list_directory(bad)

    def test_skips_hidden_directories(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "visible_dir").mkdir()
            (tmp_path / ".hidden_dir").mkdir()
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("visible_dir", names)
                self.assertNotIn(".hidden_dir", names)
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_skips_macos_metadata_dirs(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blocked_names = {"._macos_double", ".Spotlight-V100", ".Trashes",
                             ".Trash-1000", ".TemporaryItems"}
            for name in blocked_names:
                (tmp_path / name).mkdir(exist_ok=True)
            (tmp_path / "normal_dir").mkdir(exist_ok=True)
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("normal_dir", names)
                self.assertTrue(names.isdisjoint(blocked_names))
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_skips_windows_system_volume_information(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "System Volume Information").mkdir(exist_ok=True)
            (tmp_path / "real_data").mkdir(exist_ok=True)
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertNotIn("System Volume Information", names)
                self.assertIn("real_data", names)
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_handles_permission_denied_on_scandir(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.scandir", side_effect=PermissionError("denied")):
                    with self.assertRaisesRegex(DirBrowserError, "没有读取权限"):
                        list_directory(str(tmp_path))
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_handles_os_error_on_scandir(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.scandir", side_effect=OSError("I/O error")):
                    with self.assertRaisesRegex(DirBrowserError, "读取目录失败"):
                        list_directory(str(tmp_path))
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_isolates_single_subdir_permission_error(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "ok_dir").mkdir()
            (tmp_path / "bad_dir").mkdir()

            def _mock_access(p, mode):
                return "bad_dir" not in str(p)

            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.access", side_effect=_mock_access):
                    data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("ok_dir", names)
                bad = [d for d in data["subdirs"] if d["name"] == "bad_dir"]
                self.assertEqual(len(bad), 1)
                self.assertFalse(bad[0]["accessible"])
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_reports_unmounted_path(self):
        """Path inside allowed roots that does not exist → '目录不存在'."""
        bad = str(Path.home() / "nonexistent_mount_xyz")
        with self.assertRaisesRegex(DirBrowserError, "目录不存在"):
            list_directory(bad)

    def test_reports_not_a_directory(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "regular_file.txt"
            f.write_text("data")
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                with self.assertRaisesRegex(DirBrowserError, "路径不是目录"):
                    list_directory(str(f))
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_cannot_go_up_beyond_allowed_root(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                self.assertFalse(data["can_go_up"])
                self.assertIsNone(data["parent_path"])
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_skips_regular_files(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "adir").mkdir()
            (tmp_path / "afile.txt").write_text("hello")
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("adir", names)
                self.assertNotIn("afile.txt", names)
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_can_go_up_to_parent_within_roots(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            child = tmp_path / "child"
            child.mkdir()
            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(child))
                self.assertTrue(data["can_go_up"])
                self.assertIsNotNone(data["parent_path"])
            finally:
                _db.ALLOWED_ROOTS = old_roots

    def test_isolates_single_subdir_deleted_during_scan(self):
        import rng_tools.dir_browser as _db

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            class _DisappearingEntry:
                def __init__(self, name, path):
                    self.name = name
                    self.path = path

                def is_dir(self, follow_symlinks=False):
                    raise FileNotFoundError(f"{self.name} vanished")

            class _MockScandirCtx:
                def __init__(self, entries):
                    self._entries = entries

                def __enter__(self):
                    return iter(self._entries)

                def __exit__(self, *a):
                    pass

            def _mock_scandir(path):
                return _MockScandirCtx(
                    [_DisappearingEntry("vanished", str(tmp_path / "vanished"))]
                )

            old_roots = list(_db.ALLOWED_ROOTS)
            _db.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.scandir", side_effect=_mock_scandir):
                    data = list_directory(str(tmp_path))
                    self.assertEqual(data["subdirs"], [])
            finally:
                _db.ALLOWED_ROOTS = old_roots


# ======================================================================
# ALLOWED_ROOTS configuration
# ======================================================================


class AllowedRootsTests(unittest.TestCase):
    def test_includes_home_directory(self):
        self.assertIn(Path.home(), ALLOWED_ROOTS)

    def test_respects_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "custom_root"
            d.mkdir()
            with mock.patch.dict(os.environ, {"REACNET_SCOPE_ALLOWED_ROOTS": str(d)}):
                roots = get_allowed_roots()
                self.assertIn(d, roots)
                self.assertNotIn(Path.home(), roots)

    def test_skips_nonexistent_default_roots(self):
        roots = get_allowed_roots()
        for r in roots:
            self.assertTrue(r.exists(), f"Root {r} should exist")


# ======================================================================
# Dataset-aware browser facade
# ======================================================================


def _allow_only(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "ALLOWED_ROOTS", [tmp_path])
    monkeypatch.setattr(dir_browser, "ALLOWED_ROOTS", [tmp_path])


def test_browser_snapshot_exposes_breadcrumbs_and_one_dataset(tmp_path, monkeypatch):
    _allow_only(tmp_path, monkeypatch)
    data_dir = tmp_path / "case"
    data_dir.mkdir()
    Path(f"{data_dir / 'rp3.lammpstrj'}.reactionabcd").touch()
    Path(f"{data_dir / 'rp3.lammpstrj'}.species").touch()

    snapshot = svc.browse_dataset_location(str(data_dir))

    assert snapshot["current_path"] == str(data_dir)
    assert snapshot["breadcrumbs"][-1] == {
        "label": "case",
        "path": str(data_dir),
    }
    assert snapshot["datasets"][0]["auto_selected"] is True
    assert snapshot["datasets"][0]["completeness"] == "2/7"
    assert set(snapshot["datasets"][0]["index_states"]) == {
        "event",
        "trajectory",
        "composition",
    }


def test_browser_snapshot_exposes_empty_and_ambiguous_dataset_states(
    tmp_path, monkeypatch
):
    _allow_only(tmp_path, monkeypatch)
    empty_dir = tmp_path / "empty"
    data_dir = tmp_path / "case"
    empty_dir.mkdir()
    data_dir.mkdir()
    for name in ("first.lammpstrj", "second.lammpstrj"):
        Path(f"{data_dir / name}.species").touch()

    assert svc.browse_dataset_location(str(empty_dir))["datasets"] == []
    datasets = svc.browse_dataset_location(str(data_dir))["datasets"]
    assert len(datasets) == 2
    assert {item["auto_selected"] for item in datasets} == {False}


def test_browser_snapshot_rejects_invalid_paths_and_keeps_breadcrumbs_in_root(
    tmp_path, monkeypatch
):
    _allow_only(tmp_path, monkeypatch)
    nested = tmp_path / "case" / "nested"
    nested.mkdir(parents=True)

    snapshot = svc.browse_dataset_location(str(nested))

    assert snapshot["breadcrumbs"] == [
        {"label": tmp_path.name, "path": str(tmp_path)},
        {"label": "case", "path": str(tmp_path / "case")},
        {"label": "nested", "path": str(nested)},
    ]
    assert all(Path(item["path"]).is_relative_to(tmp_path) for item in snapshot["breadcrumbs"])
    with pytest.raises(svc.ServiceError, match="路径超出允许范围"):
        svc.browse_dataset_location(str(tmp_path.parent))


def test_browser_snapshot_marks_inaccessible_subdirectories(tmp_path, monkeypatch):
    _allow_only(tmp_path, monkeypatch)
    (tmp_path / "available").mkdir()
    (tmp_path / "denied").mkdir()

    monkeypatch.setattr(
        dir_browser.os,
        "access",
        lambda path, _mode: Path(path).name != "denied",
    )

    subdirs = svc.browse_dataset_location(str(tmp_path))["subdirs"]

    assert {item["name"] for item in subdirs} == {"available", "denied"}
    assert next(item for item in subdirs if item["name"] == "denied")["accessible"] is False


def test_resolve_dataset_input_accepts_directories_and_dataset_prefixes(
    tmp_path, monkeypatch
):
    _allow_only(tmp_path, monkeypatch)
    data_dir = tmp_path / "case"
    data_dir.mkdir()
    base = data_dir / "rp3.lammpstrj"

    assert svc.resolve_dataset_input(str(data_dir)) == {
        "folder": str(data_dir),
        "preferred_base": "",
    }
    assert svc.resolve_dataset_input(str(base)) == {
        "folder": str(data_dir),
        "preferred_base": str(base),
    }
    with pytest.raises(svc.ServiceError, match="路径超出允许范围"):
        svc.resolve_dataset_input(str(tmp_path.parent / "outside" / "rp3.lammpstrj"))


def test_normalise_recent_datasets_deduplicates_sorts_and_limits():
    records = [
        {"folder": "/data/case", "base": "/data/case/old", "loaded_at": 1},
        {"folder": "/data/case", "base": "/data/case/new", "loaded_at": 5},
        {
            "folder": "/data/case",
            "base": "/data/case/old",
            "label": "newer copy",
            "loaded_at": 3,
        },
        {"folder": "/data/case", "loaded_at": 9},
    ]

    result = svc.normalise_recent_datasets(records)

    assert result == [
        {
            "folder": "/data/case",
            "base": "/data/case/new",
            "label": "new",
            "loaded_at": 5,
        },
        {
            "folder": "/data/case",
            "base": "/data/case/old",
            "label": "newer copy",
            "loaded_at": 3,
        },
    ]
