"""Finding save histories that already exist inside a relay folder.

Without this, the second desktop has to already know the exact profile name the first
desktop typed - get it wrong and it creates a second, unrelated history next to the
first instead of linking to it. This scans a relay root for existing revision stores
and surfaces enough to recognise one and adopt it, id and all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import LocalDirRemote, RemoteError


def _humanize(profile_id: str) -> str:
    return profile_id.replace("-", " ").replace("_", " ").title()


def discover(relay_root: Path, known_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Every existing revision store directly inside relay_root, newest first."""
    relay_root = Path(relay_root)
    if not relay_root.is_dir():
        return []
    known_ids = known_ids or set()

    found: list[dict[str, Any]] = []
    for child in sorted(relay_root.iterdir()):
        if not child.is_dir():
            continue
        remote = LocalDirRemote(child)
        try:
            head = remote.read_head()
            if head is None:
                continue  # a folder with no HEAD.json is not a save history at all
            manifest = remote.read_manifest(head)
        except RemoteError:
            continue  # a store mid-upload or corrupted is skipped, not fatal to the list

        found.append(
            {
                "id": child.name,
                "name": manifest.profile_name or _humanize(child.name),
                "rev": head,
                "machine": manifest.machine,
                "created_at": manifest.created_at,
                "note": manifest.note,
                "file_count": manifest.file_count,
                "total_size": manifest.total_size,
                "source_local_path": manifest.source_local_path,
                "already_added": child.name in known_ids,
            }
        )
    found.sort(key=lambda entry: entry["created_at"], reverse=True)
    return found
