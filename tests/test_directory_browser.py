"""Tests for the remote directory browser: path validation and listing."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

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


class TestValidateBrowsePath:
    def test_rejects_empty_path(self):
        with pytest.raises(ServiceError, match="路径不能为空"):
            validate_browse_path("")

    def test_resolves_tilde_to_home(self):
        resolved = validate_browse_path("~")
        assert resolved == Path.home()

    def test_rejects_paths_outside_allowed_roots(self):
        with pytest.raises(ServiceError, match="路径超出允许范围"):
            validate_browse_path("/tmp")

    def test_rejects_dotdot_traversal(self):
        with pytest.raises(ServiceError, match="路径超出允许范围"):
            validate_browse_path(str(Path.home() / ".." / ".." / ".." / "etc"))

    def test_allows_paths_inside_home(self):
        p = validate_browse_path(str(Path.home()))
        assert p == Path.home()

    def test_allows_root_when_root_directory_is_explicitly_configured(self, monkeypatch):
        from scripts.webapp_dash import services

        original = services.ALLOWED_ROOTS
        services.ALLOWED_ROOTS = [Path("/")]
        try:
            assert validate_browse_path("/tmp") == Path("/tmp")
        finally:
            services.ALLOWED_ROOTS = original

    def test_rejects_symlink_escaping_roots(self, tmp_path):
        from scripts.webapp_dash import services

        allowed = tmp_path / "allowed"
        target = tmp_path / "outside" / "target"
        allowed.mkdir()
        target.mkdir(parents=True)
        link = allowed / f"test_link_{os.getpid()}"
        original = services.ALLOWED_ROOTS
        services.ALLOWED_ROOTS = [allowed]
        try:
            link.symlink_to(target, target_is_directory=True)
            with pytest.raises(ServiceError, match="路径超出允许范围"):
                validate_browse_path(str(link))
        finally:
            link.unlink(missing_ok=True)
            services.ALLOWED_ROOTS = original


# ======================================================================
# list_directory
# ======================================================================


class TestListDirectory:
    def test_lists_subdirectories_sorted(self):
        data = list_directory(str(Path.home()))
        assert "current_path" in data
        assert "subdirs" in data
        names = [d["name"] for d in data["subdirs"]]
        assert names == sorted(names, key=str.casefold)

    def test_rejects_nonexistent_path(self):
        """A path inside allowed roots that does not exist is rejected."""
        bad = str(Path.home() / "nonexistent_dir_xyz789")
        with pytest.raises(ServiceError, match="目录不存在"):
            list_directory(bad)

    def test_skips_hidden_directories(self, tmp_path, monkeypatch):
        visible = tmp_path / "visible_dir"
        hidden = tmp_path / ".hidden_dir"
        visible.mkdir()
        hidden.mkdir()
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            data = list_directory(str(tmp_path))
            names = {d["name"] for d in data["subdirs"]}
            assert "visible_dir" in names
            assert ".hidden_dir" not in names
        finally:
            _restore_allowed_roots()

    def test_skips_macos_metadata_dirs(self, tmp_path, monkeypatch):
        for name in ("._macos_double", ".Spotlight-V100", ".Trashes",
                     ".Trash-1000", ".TemporaryItems"):
            (tmp_path / name).mkdir(exist_ok=True)
        normal = tmp_path / "normal_dir"
        normal.mkdir(exist_ok=True)
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            data = list_directory(str(tmp_path))
            names = {d["name"] for d in data["subdirs"]}
            assert "normal_dir" in names
            blocked = {"._macos_double", ".Spotlight-V100", ".Trashes",
                       ".Trash-1000", ".TemporaryItems"}
            assert names.isdisjoint(blocked)
        finally:
            _restore_allowed_roots()

    def test_skips_windows_system_volume_information(self, tmp_path, monkeypatch):
        (tmp_path / "System Volume Information").mkdir(exist_ok=True)
        (tmp_path / "real_data").mkdir(exist_ok=True)
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            data = list_directory(str(tmp_path))
            names = {d["name"] for d in data["subdirs"]}
            assert "System Volume Information" not in names
            assert "real_data" in names
        finally:
            _restore_allowed_roots()

    def test_handles_permission_denied_on_scandir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            with mock.patch("os.scandir", side_effect=PermissionError("denied")):
                with pytest.raises(ServiceError, match="没有读取权限"):
                    list_directory(str(tmp_path))
        finally:
            _restore_allowed_roots()

    def test_handles_os_error_on_scandir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            with mock.patch("os.scandir", side_effect=OSError("I/O error")):
                with pytest.raises(ServiceError, match="读取目录失败"):
                    list_directory(str(tmp_path))
        finally:
            _restore_allowed_roots()

    def test_isolates_single_subdir_permission_error(self, tmp_path, monkeypatch):
        (tmp_path / "ok_dir").mkdir()
        (tmp_path / "bad_dir").mkdir()

        def _mock_access(p, mode):
            return "bad_dir" not in str(p)

        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            with mock.patch("os.access", side_effect=_mock_access):
                data = list_directory(str(tmp_path))
            names = {d["name"] for d in data["subdirs"]}
            assert "ok_dir" in names
            bad = [d for d in data["subdirs"] if d["name"] == "bad_dir"]
            assert len(bad) == 1
            assert bad[0]["accessible"] is False
        finally:
            _restore_allowed_roots()

    def test_reports_unmounted_path(self):
        with pytest.raises(ServiceError, match="目录不存在"):
            list_directory("/media/huangchen/T3000_nonexistent")

    def test_reports_not_a_directory(self, tmp_path, monkeypatch):
        f = tmp_path / "regular_file.txt"
        f.write_text("data")
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            with pytest.raises(ServiceError, match="路径不是目录"):
                list_directory(str(f))
        finally:
            _restore_allowed_roots()

    def test_cannot_go_up_beyond_allowed_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            data = list_directory(str(tmp_path))
            assert data["can_go_up"] is False
            assert data["parent_path"] is None
        finally:
            _restore_allowed_roots()

    def test_skips_regular_files(self, tmp_path, monkeypatch):
        (tmp_path / "adir").mkdir()
        (tmp_path / "afile.txt").write_text("hello")
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            data = list_directory(str(tmp_path))
            names = {d["name"] for d in data["subdirs"]}
            assert "adir" in names
            assert "afile.txt" not in names
        finally:
            _restore_allowed_roots()

    def test_can_go_up_to_parent_within_roots(self, tmp_path, monkeypatch):
        child = tmp_path / "child"
        child.mkdir()
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            data = list_directory(str(child))
            assert data["can_go_up"] is True
            assert data["parent_path"] is not None
        finally:
            _restore_allowed_roots()

    def test_isolates_single_subdir_deleted_during_scan(self, tmp_path, monkeypatch):
        """A subdirectory that disappears mid-scan is silently skipped."""

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

        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(tmp_path))
        _reload_allowed_roots(tmp_path)
        try:
            with mock.patch("os.scandir", side_effect=_mock_scandir):
                data = list_directory(str(tmp_path))
                assert data["subdirs"] == []
        finally:
            _restore_allowed_roots()


# ======================================================================
# ALLOWED_ROOTS configuration
# ======================================================================


class TestAllowedRoots:
    def test_includes_home_directory(self):
        assert Path.home() in ALLOWED_ROOTS

    def test_respects_env_override(self, tmp_path, monkeypatch):
        d = tmp_path / "custom_root"
        d.mkdir()
        monkeypatch.setenv("REACNET_SCOPE_ALLOWED_ROOTS", str(d))
        roots = _get_allowed_roots()
        assert d in roots
        assert Path.home() not in roots

    def test_skips_nonexistent_default_roots(self):
        roots = _get_allowed_roots()
        for r in roots:
            assert r.exists(), f"Root {r} should exist"


# ======================================================================
# helpers
# ======================================================================

_OLD_ALLOWED_ROOTS = None


def _reload_allowed_roots(extra_root):
    """Temporarily set ALLOWED_ROOTS to just *extra_root* for testing."""
    global _OLD_ALLOWED_ROOTS
    from scripts.webapp_dash import services
    _OLD_ALLOWED_ROOTS = list(services.ALLOWED_ROOTS)
    services.ALLOWED_ROOTS = [extra_root]


def _restore_allowed_roots():
    global _OLD_ALLOWED_ROOTS
    from scripts.webapp_dash import services
    if _OLD_ALLOWED_ROOTS is not None:
        services.ALLOWED_ROOTS = _OLD_ALLOWED_ROOTS
        _OLD_ALLOWED_ROOTS = None
