"""SQLite storage for the hosted account server.

One file, no separate database process, no ORM - the schema is small enough that raw
SQL is more legible than the abstraction would be. Every table beyond `users` is
scoped by `user_id` (directly or via `profiles`), which is the entire access-control
model: a query that forgets the `user_id` filter is the one bug that matters here, so
every accessor takes it as a required argument rather than a query builder that could
be called without one.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    slug TEXT NOT NULL,
    head_rev INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, slug)
);

CREATE TABLE IF NOT EXISTS revisions (
    profile_id INTEGER NOT NULL REFERENCES profiles(id),
    rev INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    PRIMARY KEY (profile_id, rev)
);

CREATE TABLE IF NOT EXISTS blobs (
    user_id INTEGER NOT NULL REFERENCES users(id),
    hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, hash)
);

CREATE TABLE IF NOT EXISTS locks (
    profile_id INTEGER PRIMARY KEY REFERENCES profiles(id),
    machine TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""


def default_server_home() -> Path:
    override = os.environ.get("SAVESYNC_SERVER_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "savesync-server"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "savesync-server"


class Database:
    """Owns the SQLite connection and the blob directory alongside it."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home) if home else default_server_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.blobs_dir = self.home / "blobs"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "db.sqlite3"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @property
    def secret_key(self) -> bytes:
        """The HMAC key for session tokens - generated once, persisted alongside the DB."""
        path = self.home / "secret.key"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
        return path.read_bytes()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()

    # -- users ---------------------------------------------------------------

    def create_user(self, username: str, password_hash: str) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, _now_iso()),
            )
            return int(cur.lastrowid)

    def get_user_by_username(self, username: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    # -- profiles --------------------------------------------------------------

    def get_or_create_profile(self, user_id: int, slug: str) -> int:
        row = self._conn.execute(
            "SELECT id FROM profiles WHERE user_id = ? AND slug = ?", (user_id, slug)
        ).fetchone()
        if row:
            return int(row["id"])
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO profiles (user_id, slug, head_rev, created_at) VALUES (?, ?, NULL, ?)",
                (user_id, slug, _now_iso()),
            )
            return int(cur.lastrowid)

    def find_profile(self, user_id: int, slug: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM profiles WHERE user_id = ? AND slug = ?", (user_id, slug)
        ).fetchone()

    def list_profiles(self, user_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM profiles WHERE user_id = ? ORDER BY created_at", (user_id,)
        ).fetchall()

    def set_head(self, profile_id: int, rev: int) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE profiles SET head_rev = ? WHERE id = ?", (rev, profile_id))

    # -- revisions ---------------------------------------------------------------

    def write_revision(self, profile_id: int, rev: int, manifest_json: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO revisions (profile_id, rev, manifest_json) VALUES (?, ?, ?)",
                (profile_id, rev, manifest_json),
            )

    def read_revision(self, profile_id: int, rev: int) -> str | None:
        row = self._conn.execute(
            "SELECT manifest_json FROM revisions WHERE profile_id = ? AND rev = ?",
            (profile_id, rev),
        ).fetchone()
        return row["manifest_json"] if row else None

    def list_revisions(self, profile_id: int) -> list[int]:
        rows = self._conn.execute(
            "SELECT rev FROM revisions WHERE profile_id = ? ORDER BY rev", (profile_id,)
        ).fetchall()
        return [int(r["rev"]) for r in rows]

    # -- blobs -----------------------------------------------------------------
    # Bytes live on disk under blobs_dir; this table only tracks existence/size so
    # has_blob() is a DB lookup rather than a filesystem stat on every check.

    def _blob_path(self, user_id: int, file_hash: str) -> Path:
        digest = file_hash.split(":", 1)[-1]
        shard = digest[:2] if len(digest) >= 2 else "xx"
        return self.blobs_dir / str(user_id) / shard / digest

    def has_blob(self, user_id: int, file_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM blobs WHERE user_id = ? AND hash = ?", (user_id, file_hash)
        ).fetchone()
        return row is not None

    def blob_path_for_read(self, user_id: int, file_hash: str) -> Path | None:
        return self._blob_path(user_id, file_hash) if self.has_blob(user_id, file_hash) else None

    def write_blob(self, user_id: int, file_hash: str, data: bytes) -> None:
        path = self._blob_path(user_id, file_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO blobs (user_id, hash, size, created_at) VALUES (?, ?, ?, ?)",
                (user_id, file_hash, len(data), _now_iso()),
            )

    # -- locks -------------------------------------------------------------------

    def read_lock(self, profile_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM locks WHERE profile_id = ?", (profile_id,)).fetchone()

    def acquire_lock(self, profile_id: int, machine: str, ttl: float) -> tuple[bool, sqlite3.Row | None]:
        """Atomically take the lock if free or expired. Returns (acquired, current_row)."""
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM locks WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            now = time.time()
            if existing is not None and existing["expires_at"] > now and existing["machine"] != machine:
                return False, existing
            conn.execute(
                "INSERT OR REPLACE INTO locks (profile_id, machine, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (profile_id, machine, now, now + ttl),
            )
            return True, None

    def release_lock(self, profile_id: int, machine: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM locks WHERE profile_id = ? AND machine = ?", (profile_id, machine)
            )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
