"""Writing a revision onto the local disk, safely.

Order matters and is the whole point of this module:

  1. stage   - materialise the target revision into a temp dir on the same volume,
               verifying every blob hash as it lands
  2. backup  - snapshot whatever is currently in the save folder
  3. swap    - move staged files into place, then remove local files the revision
               does not contain

Nothing touches the real save folder until the full target state exists and has been
verified, so a missing blob or a half-synced relay aborts with the local save
untouched. And because step 2 always runs, every destructive path has an undo.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .manifest import Manifest
from .store import Remote


class ApplyError(Exception):
    pass


@dataclass(slots=True)
class ApplyResult:
    backup_path: Path | None
    written: int
    removed: int


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def snapshot_backup(local_path: Path, dest_root: Path, label: str = "") -> Path | None:
    """Copy the current save folder into the backup area. Returns the backup path."""
    local_path = Path(local_path)
    if not local_path.exists():
        return None
    name = f"{_timestamp()}{'-' + label if label else ''}"
    dest = Path(dest_root) / name
    n = 2
    while dest.exists():
        dest = Path(dest_root) / f"{name}-{n}"
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(local_path, dest, dirs_exist_ok=False, symlinks=True)
    return dest


def list_backups(dest_root: Path) -> list[dict[str, object]]:
    root = Path(dest_root)
    if not root.exists():
        return []
    out = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        files = [p for p in path.rglob("*") if p.is_file()]
        out.append(
            {
                "id": path.name,
                "path": str(path),
                "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                    timespec="seconds"
                ),
                "file_count": len(files),
                "total_size": sum(p.stat().st_size for p in files),
            }
        )
    return out


def prune_backups(dest_root: Path, keep: int) -> int:
    """Drop all but the newest `keep` backups. Returns how many were removed."""
    root = Path(dest_root)
    if not root.exists() or keep < 0:
        return 0
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    removed = 0
    for path in dirs[keep:]:
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


def materialize(remote: Remote, manifest: Manifest, dest: Path) -> None:
    """Write a revision's full contents into `dest`, verifying every blob."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for entry in manifest.files:
        target = dest / Path(entry.path)
        remote.read_blob_to(entry.hash, target)
        try:
            os.utime(target, (entry.mtime, entry.mtime))
        except OSError:
            pass  # a preserved mtime is a nicety, not a correctness requirement


def _prune_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:
            continue


def apply_manifest(
    remote: Remote,
    manifest: Manifest,
    local_path: Path,
    backup_root: Path,
    *,
    retention: int = 10,
    backup_label: str = "",
) -> ApplyResult:
    local_path = Path(local_path)
    stage = local_path.parent / f".savesync-stage-{uuid.uuid4().hex[:8]}"

    try:
        # 1. stage + verify, before anything local is touched
        materialize(remote, manifest, stage)

        # 2. backup whatever is there now
        backup = snapshot_backup(local_path, backup_root, backup_label)
        local_path.mkdir(parents=True, exist_ok=True)

        # 3. swap
        wanted = set()
        written = 0
        for entry in manifest.files:
            src = stage / Path(entry.path)
            dst = local_path / Path(entry.path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            wanted.add(dst.resolve())
            written += 1

        removed = 0
        for path in list(local_path.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.resolve() in wanted:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                raise ApplyError(f"cannot remove {path}: {exc}") from exc
        _prune_empty_dirs(local_path)

        if retention >= 0:
            prune_backups(backup_root, retention)
        return ApplyResult(backup_path=backup, written=written, removed=removed)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def restore_backup(backup_path: Path, local_path: Path, backup_root: Path) -> Path | None:
    """Put a previous backup back, snapshotting the current state first."""
    backup_path = Path(backup_path)
    local_path = Path(local_path)
    if not backup_path.is_dir():
        raise ApplyError(f"no such backup: {backup_path}")
    safety = snapshot_backup(local_path, backup_root, "pre-restore")
    shutil.rmtree(local_path, ignore_errors=True)
    shutil.copytree(backup_path, local_path, symlinks=True)
    return safety
