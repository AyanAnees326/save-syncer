"""Turn a local save folder into a Manifest."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterable, Sequence

from .hashing import hash_file
from .manifest import FileEntry, Manifest

# Junk that Windows and cloud clients sprinkle into folders. Syncing these creates
# spurious conflicts between machines that are otherwise identical.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "desktop.ini",
    "Thumbs.db",
    ".DS_Store",
    "*.tmp",
    ".savesync-stage-*/**",
)


class ScanError(Exception):
    pass


def _matches(rel_path: str, patterns: Sequence[str]) -> bool:
    name = rel_path.rsplit("/", 1)[-1]
    for pat in patterns:
        if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat):
            return True
        # a "dir/**" pattern should also exclude the directory itself
        if pat.endswith("/**") and (rel_path == pat[:-3] or rel_path.startswith(pat[:-2])):
            return True
    return False


def scan_files(root: Path, excludes: Iterable[str] | None = None) -> tuple[FileEntry, ...]:
    """Hash every non-excluded regular file under root.

    Symlinks and reparse points are skipped rather than followed - a save folder that
    contains a junction to somewhere else should not silently pull that target into
    the sync set.
    """
    root = Path(root)
    if not root.exists():
        raise ScanError(f"local path does not exist: {root}")
    if not root.is_dir():
        raise ScanError(f"local path is not a directory: {root}")

    patterns = tuple(excludes) if excludes is not None else DEFAULT_EXCLUDES
    entries: list[FileEntry] = []

    for path in sorted(root.rglob("*")):
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        if _matches(rel, patterns):
            continue
        try:
            stat = path.stat()
            entries.append(
                FileEntry(path=rel, hash=hash_file(path), size=stat.st_size, mtime=stat.st_mtime)
            )
        except OSError as exc:
            raise ScanError(f"cannot read {path}: {exc}") from exc

    return tuple(entries)


def scan(
    root: Path,
    excludes: Iterable[str] | None = None,
    *,
    profile: str = "",
    machine: str = "",
) -> Manifest:
    """Scan root into an unnumbered manifest describing what is on disk right now."""
    return Manifest(files=scan_files(root, excludes), rev=None, profile=profile, machine=machine)
