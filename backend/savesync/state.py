"""The sync journal: what revision this machine last agreed on.

This one small file per profile is what makes conflict detection possible. Without a
base revision you can only compare local against remote, which cannot distinguish
"they changed and I did not" from "we both changed" - and guessing wrong there is how
sync tools quietly destroy a save.

base_content_id is always the content id of base_rev. Keeping it locally means the
comparison works without reading the relay, which matters when the relay is a cloud
folder that has not finished downloading.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import read_json, write_json


@dataclass(slots=True)
class SyncState:
    base_rev: int | None = None
    base_content_id: str | None = None
    last_sync_at: str | None = None
    last_action: str | None = None

    @property
    def linked(self) -> bool:
        return self.base_rev is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SyncState":
        rev = d.get("base_rev")
        return cls(
            base_rev=int(rev) if rev is not None else None,
            base_content_id=d.get("base_content_id"),
            last_sync_at=d.get("last_sync_at"),
            last_action=d.get("last_action"),
        )


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)

    def _file(self, profile_id: str) -> Path:
        return self.state_dir / f"{profile_id}.json"

    def get(self, profile_id: str) -> SyncState:
        return SyncState.from_dict(read_json(self._file(profile_id), {}))

    def set(self, profile_id: str, state: SyncState) -> SyncState:
        write_json(self._file(profile_id), state.to_dict())
        return state

    def clear(self, profile_id: str) -> None:
        self._file(profile_id).unlink(missing_ok=True)
