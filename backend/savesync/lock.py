"""A TTL lock file on the relay, so two machines cannot write overlapping revisions.

The lock is advisory and stealable. A machine that crashes mid-push must not wedge the
store forever, so an expired lock is taken over rather than respected.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TTL = 120.0


class LockError(Exception):
    pass


@dataclass(slots=True)
class LockInfo:
    machine: str
    acquired_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class RemoteLock:
    """Context manager around <store>/lock.json."""

    def __init__(self, path: Path, machine: str, ttl: float = DEFAULT_TTL) -> None:
        self.path = Path(path)
        self.machine = machine
        self.ttl = ttl
        self._held = False

    def read(self) -> LockInfo | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            # An unreadable or half-written lock is treated as stale rather than fatal;
            # a cloud relay can briefly expose a partial file.
            return None
        try:
            return LockInfo(
                machine=data["machine"],
                acquired_at=float(data["acquired_at"]),
                expires_at=float(data["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _write(self) -> None:
        now = time.time()
        payload = {
            "machine": self.machine,
            "acquired_at": now,
            "expires_at": now + self.ttl,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)

    def acquire(self) -> None:
        existing = self.read()
        if existing is not None and not existing.expired and existing.machine != self.machine:
            remaining = int(existing.expires_at - time.time())
            raise LockError(
                f"relay is locked by {existing.machine} for another {remaining}s; "
                "wait for that sync to finish or retry once it expires"
            )
        self._write()
        self._held = True

    def heartbeat(self) -> None:
        if self._held:
            self._write()

    def release(self) -> None:
        if not self._held:
            return
        current = self.read()
        if current is None or current.machine == self.machine:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
        self._held = False

    def __enter__(self) -> "RemoteLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
