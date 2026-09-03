"""Manifests describe one complete snapshot of a save folder.

A manifest is the unit of sync. The engine never reasons about individual files
crossing between machines - it moves whole manifests, because a save folder assembled
from the newest of each file is a state that never existed on either machine.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .hashing import ALGO, DIGEST_SIZE


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str  # relative to the save root, forward slashes
    hash: str
    size: int
    mtime: float

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "hash": self.hash, "size": self.size, "mtime": self.mtime}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FileEntry":
        return cls(path=d["path"], hash=d["hash"], size=int(d["size"]), mtime=float(d["mtime"]))


def content_id(files: Iterable[FileEntry]) -> str:
    """Identity of a tree: paths and contents only.

    Deliberately excludes mtime, revision number and machine, so the same bytes in the
    same layout produce the same id on both machines regardless of clock or history.
    """
    h = hashlib.blake2b(digest_size=DIGEST_SIZE)
    for entry in sorted(files, key=lambda f: f.path):
        h.update(entry.path.encode("utf-8"))
        h.update(b"\0")
        h.update(entry.hash.encode("ascii"))
        h.update(b"\n")
    return f"{ALGO}:{h.hexdigest()}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Manifest:
    files: tuple[FileEntry, ...]
    rev: int | None = None
    parent: int | None = None
    profile: str = ""
    profile_name: str = ""
    machine: str = ""
    created_at: str = field(default_factory=utc_now)
    note: str = ""
    # The absolute local path on the machine that pushed this revision. Purely
    # informational - it lets a desktop discovering this profile for the first time
    # see where the save lived elsewhere, since the exact path (Windows username,
    # Steam id) is rarely identical between two machines.
    source_local_path: str = ""

    def __post_init__(self) -> None:
        self.files = tuple(sorted(self.files, key=lambda f: f.path))

    @property
    def content_id(self) -> str:
        return content_id(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def newest_mtime(self) -> float | None:
        return max((f.mtime for f in self.files), default=None)

    def by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}

    def to_dict(self) -> dict[str, Any]:
        return {
            "rev": self.rev,
            "parent": self.parent,
            "profile": self.profile,
            "profile_name": self.profile_name,
            "machine": self.machine,
            "created_at": self.created_at,
            "note": self.note,
            "source_local_path": self.source_local_path,
            "content_id": self.content_id,
            "files": [f.to_dict() for f in self.files],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Manifest":
        return cls(
            files=tuple(FileEntry.from_dict(f) for f in d.get("files", [])),
            rev=d.get("rev"),
            parent=d.get("parent"),
            profile=d.get("profile", ""),
            profile_name=d.get("profile_name", ""),
            machine=d.get("machine", ""),
            created_at=d.get("created_at", ""),
            note=d.get("note", ""),
            source_local_path=d.get("source_local_path", ""),
        )

    def summary(self) -> dict[str, Any]:
        """Compact shape for lists and status panels."""
        return {
            "rev": self.rev,
            "parent": self.parent,
            "machine": self.machine,
            "created_at": self.created_at,
            "note": self.note,
            "source_local_path": self.source_local_path,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "newest_mtime": self.newest_mtime,
            "content_id": self.content_id,
        }


@dataclass(slots=True)
class Diff:
    added: list[str]
    changed: list[str]
    removed: list[str]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "changed": self.changed,
            "removed": self.removed,
            "is_empty": self.is_empty,
        }


def diff_manifests(base: Manifest | None, other: Manifest | None) -> Diff:
    """What `other` did relative to `base`."""
    a = base.by_path() if base else {}
    b = other.by_path() if other else {}
    added = sorted(p for p in b if p not in a)
    removed = sorted(p for p in a if p not in b)
    changed = sorted(p for p in a.keys() & b.keys() if a[p].hash != b[p].hash)
    return Diff(added=added, changed=changed, removed=removed)


def newest_mtime_of(paths: Sequence[str], manifest: Manifest) -> float | None:
    entries = manifest.by_path()
    return max((entries[p].mtime for p in paths if p in entries), default=None)
