"""Remote server directory browser helpers.

These functions are deliberately free of Dash / Flask / ``reacnet_scope``
imports so that they can be tested in CI without the full web dependency
graph.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class DirBrowserError(Exception):
    """User-facing directory browser error."""

    def __init__(self, message: str, *, reason: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


# ---------------------------------------------------------------------------
# Allowed roots
# ---------------------------------------------------------------------------


def get_allowed_roots() -> list[Path]:
    """Return the list of allowed browsing root directories.

    The default set covers common data mount points.  Set the environment
    variable ``REACNET_SCOPE_ALLOWED_ROOTS`` to a colon-separated list
    of paths to override the defaults.

    Only directories that actually exist are returned.
    """
    env_override = os.environ.get("REACNET_SCOPE_ALLOWED_ROOTS", "")
    if env_override:
        roots = [Path(p).expanduser().resolve() for p in env_override.split(":") if p.strip()]
        return [r for r in roots if r.exists() and r.is_dir()]

    home = Path.home()
    username = home.name
    candidates: list[Path] = [
        home,
        Path(f"/media/{username}"),
        Path("/mnt"),
        Path("/data"),
    ]
    return [c for c in candidates if c.exists() and c.is_dir()]


ALLOWED_ROOTS: list[Path] = get_allowed_roots()


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def validate_browse_path(path_str: str) -> Path:
    """Normalise *path_str* and verify it lies inside an allowed root.

    The path is expanded (``~`` → home), resolved (symlinks followed), and
    checked for containment within :data:`ALLOWED_ROOTS`.  Raises
    :class:`DirBrowserError` when the path escapes the allowed tree.
    """
    raw = (path_str or "").strip()
    if not raw:
        raise DirBrowserError("路径不能为空", reason="empty_path")

    try:
        resolved = Path(raw).expanduser().resolve()
    except (RuntimeError, OSError) as exc:
        raise DirBrowserError(f"无法解析路径: {raw}", reason="invalid_path") from exc

    existing_roots = [r for r in ALLOWED_ROOTS if r.exists()]
    if not existing_roots:
        raise DirBrowserError("没有可用的允许根目录", reason="no_roots")

    within = any(resolved.is_relative_to(root) for root in existing_roots)
    if not within:
        root_list = ", ".join(str(r) for r in existing_roots)
        raise DirBrowserError(
            f"路径超出允许范围。允许的根目录: {root_list}",
            reason="path_out_of_bounds",
        )
    return resolved


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def list_directory(path_str: str) -> dict[str, Any]:
    """Enumerate subdirectories in *path_str* for the directory browser.

    Returns a dict with keys ``current_path``, ``parent_path``,
    ``can_go_up``, and ``subdirs`` (each a ``{name, path, accessible}``
    dict).  Directories are sorted case-insensitively.  Hidden entries,
    macOS metadata folders, and Windows system directories are skipped.
    """
    path = validate_browse_path(path_str)

    if not path.exists():
        raise DirBrowserError(f"目录不存在: {path}", reason="not_found")
    if not path.is_dir():
        raise DirBrowserError(f"路径不是目录: {path}", reason="not_directory")
    if not os.access(path, os.R_OK):
        raise DirBrowserError(f"没有读取权限: {path}", reason="permission_denied")

    # Determine whether the parent is still within an allowed root.
    parent_path = path.parent
    can_go_up = False
    try:
        validate_browse_path(str(parent_path))
        can_go_up = True
    except DirBrowserError:
        pass

    # Names that are always hidden from the directory listing.
    _SKIP_EXACT = {
        ".Spotlight-V100",
        ".Trashes",
        ".TemporaryItems",
        "System Volume Information",
    }

    subdirs: list[dict[str, Any]] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                name = entry.name

                # Hidden entries (start with ".")
                if name.startswith("."):
                    if name.startswith("._"):       # macOS AppleDouble
                        continue
                    if name in _SKIP_EXACT:
                        continue
                    if name.startswith(".Trash"):    # .Trash, .Trash-*, …
                        continue
                    continue  # all other dotfiles

                # Non-dot macOS / Windows metadata
                if name in _SKIP_EXACT:
                    continue

                # Per-entry error isolation: a single vanished or
                # inaccessible entry must not break the whole listing.
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    accessible = os.access(entry.path, os.R_OK)
                except (FileNotFoundError, OSError):
                    continue

                subdirs.append(
                    {
                        "name": name,
                        "path": str(entry.path),
                        "accessible": accessible,
                    }
                )
    except PermissionError as exc:
        raise DirBrowserError(
            f"没有读取权限: {path}", reason="permission_denied"
        ) from exc
    except OSError as exc:
        raise DirBrowserError(f"读取目录失败: {exc}", reason="read_error") from exc

    # Stable, case-insensitive sort.
    subdirs.sort(key=lambda d: d["name"].casefold())

    return {
        "current_path": str(path),
        "parent_path": str(parent_path) if can_go_up else None,
        "can_go_up": can_go_up,
        "subdirs": subdirs,
    }
