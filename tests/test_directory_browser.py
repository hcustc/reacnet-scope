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

from scripts.webapp_dash.services import (
    ALLOWED_ROOTS,
    ServiceError,
    _get_allowed_roots,
    list_directory,
    validate_browse_path,
)


# ======================================================================
# validate_browse_path
# ======================================================================


class ValidateBrowsePathTests(unittest.TestCase):
    def test_rejects_empty_path(self):
        with self.assertRaisesRegex(ServiceError, "路径不能为空"):
            validate_browse_path("")

    def test_resolves_tilde_to_home(self):
        resolved = validate_browse_path("~")
        self.assertEqual(resolved, Path.home())

    def test_rejects_paths_outside_allowed_roots(self):
        with self.assertRaisesRegex(ServiceError, "路径超出允许范围"):
            validate_browse_path("/tmp")

    def test_rejects_dotdot_traversal(self):
        with self.assertRaisesRegex(ServiceError, "路径超出允许范围"):
            validate_browse_path(str(Path.home() / ".." / ".." / ".." / "etc"))

    def test_allows_paths_inside_home(self):
        p = validate_browse_path(str(Path.home()))
        self.assertEqual(p, Path.home())

    def test_rejects_symlink_escaping_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            link = Path.home() / f".reacnet_test_link_{os.getpid()}"
            try:
                link.symlink_to(target)
                with self.assertRaisesRegex(ServiceError, "路径超出允许范围"):
                    validate_browse_path(str(link))
            finally:
                link.unlink(missing_ok=True)


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
        with self.assertRaisesRegex(ServiceError, "目录不存在"):
            list_directory(bad)

    def test_skips_hidden_directories(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "visible_dir").mkdir()
            (tmp_path / ".hidden_dir").mkdir()
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("visible_dir", names)
                self.assertNotIn(".hidden_dir", names)
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_skips_macos_metadata_dirs(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            blocked_names = {"._macos_double", ".Spotlight-V100", ".Trashes",
                             ".Trash-1000", ".TemporaryItems"}
            for name in blocked_names:
                (tmp_path / name).mkdir(exist_ok=True)
            (tmp_path / "normal_dir").mkdir(exist_ok=True)
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("normal_dir", names)
                self.assertTrue(names.isdisjoint(blocked_names))
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_skips_windows_system_volume_information(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "System Volume Information").mkdir(exist_ok=True)
            (tmp_path / "real_data").mkdir(exist_ok=True)
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertNotIn("System Volume Information", names)
                self.assertIn("real_data", names)
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_handles_permission_denied_on_scandir(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.scandir", side_effect=PermissionError("denied")):
                    with self.assertRaisesRegex(ServiceError, "没有读取权限"):
                        list_directory(str(tmp_path))
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_handles_os_error_on_scandir(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.scandir", side_effect=OSError("I/O error")):
                    with self.assertRaisesRegex(ServiceError, "读取目录失败"):
                        list_directory(str(tmp_path))
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_isolates_single_subdir_permission_error(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "ok_dir").mkdir()
            (tmp_path / "bad_dir").mkdir()

            def _mock_access(p, mode):
                return "bad_dir" not in str(p)

            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.access", side_effect=_mock_access):
                    data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("ok_dir", names)
                bad = [d for d in data["subdirs"] if d["name"] == "bad_dir"]
                self.assertEqual(len(bad), 1)
                self.assertFalse(bad[0]["accessible"])
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_reports_unmounted_path(self):
        with self.assertRaisesRegex(ServiceError, "目录不存在"):
            list_directory("/media/huangchen/T3000_nonexistent")

    def test_reports_not_a_directory(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = tmp_path / "regular_file.txt"
            f.write_text("data")
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                with self.assertRaisesRegex(ServiceError, "路径不是目录"):
                    list_directory(str(f))
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_cannot_go_up_beyond_allowed_root(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                self.assertFalse(data["can_go_up"])
                self.assertIsNone(data["parent_path"])
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_skips_regular_files(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "adir").mkdir()
            (tmp_path / "afile.txt").write_text("hello")
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(tmp_path))
                names = {d["name"] for d in data["subdirs"]}
                self.assertIn("adir", names)
                self.assertNotIn("afile.txt", names)
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_can_go_up_to_parent_within_roots(self):
        import scripts.webapp_dash.services as _svc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            child = tmp_path / "child"
            child.mkdir()
            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                data = list_directory(str(child))
                self.assertTrue(data["can_go_up"])
                self.assertIsNotNone(data["parent_path"])
            finally:
                _svc.ALLOWED_ROOTS = old_roots

    def test_isolates_single_subdir_deleted_during_scan(self):
        import scripts.webapp_dash.services as _svc

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

            old_roots = list(_svc.ALLOWED_ROOTS)
            _svc.ALLOWED_ROOTS = [tmp_path]
            try:
                with mock.patch("os.scandir", side_effect=_mock_scandir):
                    data = list_directory(str(tmp_path))
                    self.assertEqual(data["subdirs"], [])
            finally:
                _svc.ALLOWED_ROOTS = old_roots


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
                roots = _get_allowed_roots()
                self.assertIn(d, roots)
                self.assertNotIn(Path.home(), roots)

    def test_skips_nonexistent_default_roots(self):
        roots = _get_allowed_roots()
        for r in roots:
            self.assertTrue(r.exists(), f"Root {r} should exist")
