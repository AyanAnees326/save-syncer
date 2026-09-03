"""The revision store that lives in the shared relay folder.

Layout, per profile:

    <relay>/<profile-id>/
      HEAD.json            {"rev": 17}
      revs/000017.json     manifest for revision 17
      blobs/ab/cdef1234    file contents, keyed by hash, sharded by first 2 chars
      lock.json            TTL write lock

Content-addressed, so an unchanged file is uploaded once no matter how many revisions
reference it, and full history costs almost nothing for save-sized files. blobs/ is
append-only; nothing is ever deleted without an explicit gc.

Remote is an ABC purely so an SFTP or S3 backend can drop in later without the engine
learning anything new.
"""

from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

from .hashing import blob_key, hash_file
from .lock import RemoteLock
from .manifest import Manifest


class RemoteError(Exception):
    pass


class LockLike(Protocol):
    """What Engine actually needs from a lock: acquire/release as a context manager,
    plus a heartbeat during long pushes. RemoteLock (file-based) and HttpRemoteLock
    (server-based) both satisfy this without one inheriting from the other - their
    underlying I/O has nothing in common beyond this shape."""

    def __enter__(self) -> "LockLike": ...
    def __exit__(self, *exc: object) -> None: ...
    def heartbeat(self) -> None: ...


class Remote(ABC):
    def initialize(self) -> None:
        """Prepare the store for its first write, if the backend needs that at all.

        A concrete no-op by default rather than abstract: most backends (a server
        that provisions a profile row on first write, same as this one already does)
        have nothing to do here. LocalDirRemote overrides it because a plain
        directory does need its subfolders created before anything can be written.
        """

    @abstractmethod
    def exists(self) -> bool: ...

    @abstractmethod
    def read_head(self) -> int | None: ...

    @abstractmethod
    def write_head(self, rev: int) -> None: ...

    @abstractmethod
    def list_revisions(self) -> list[int]: ...

    @abstractmethod
    def read_manifest(self, rev: int) -> Manifest: ...

    @abstractmethod
    def write_manifest(self, manifest: Manifest) -> None: ...

    @abstractmethod
    def has_blob(self, file_hash: str) -> bool: ...

    @abstractmethod
    def write_blob(self, file_hash: str, src: Path) -> None: ...

    @abstractmethod
    def read_blob_to(self, file_hash: str, dst: Path) -> None: ...

    @abstractmethod
    def lock(self, machine: str) -> LockLike: ...


class LocalDirRemote(Remote):
    """A store backed by a plain directory.

    This covers every case the app actually needs: a Dropbox/OneDrive/Drive folder, a
    mapped drive, or a UNC share. No networking code, and it works when the two
    machines are never online at the same time.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def head_file(self) -> Path:
        return self.root / "HEAD.json"

    @property
    def revs_dir(self) -> Path:
        return self.root / "revs"

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def lock_file(self) -> Path:
        return self.root / "lock.json"

    def _rev_file(self, rev: int) -> Path:
        return self.revs_dir / f"{rev:06d}.json"

    def _blob_path(self, file_hash: str) -> Path:
        shard, name = blob_key(file_hash)
        return self.blobs_dir / shard / name

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # -- required interface ------------------------------------------------

    def exists(self) -> bool:
        return self.head_file.exists()

    def initialize(self) -> None:
        self.revs_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)

    def read_head(self) -> int | None:
        try:
            data = json.loads(self.head_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise RemoteError(f"relay HEAD is unreadable ({exc}); is the relay fully synced?")
        rev = data.get("rev")
        if rev is None:
            raise RemoteError("relay HEAD has no revision")
        return int(rev)

    def write_head(self, rev: int) -> None:
        self._write_json(self.head_file, {"rev": int(rev)})

    def list_revisions(self) -> list[int]:
        if not self.revs_dir.exists():
            return []
        revs = []
        for path in self.revs_dir.glob("*.json"):
            try:
                revs.append(int(path.stem))
            except ValueError:
                continue
        return sorted(revs)

    def read_manifest(self, rev: int) -> Manifest:
        path = self._rev_file(rev)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RemoteError(f"revision {rev} is missing from the relay") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RemoteError(f"revision {rev} is unreadable ({exc})") from exc
        manifest = Manifest.from_dict(data)
        if manifest.rev is None:
            manifest.rev = rev
        return manifest

    def write_manifest(self, manifest: Manifest) -> None:
        if manifest.rev is None:
            raise RemoteError("cannot write a manifest with no revision number")
        self._write_json(self._rev_file(manifest.rev), manifest.to_dict())

    def has_blob(self, file_hash: str) -> bool:
        return self._blob_path(file_hash).exists()

    def write_blob(self, file_hash: str, src: Path) -> None:
        dst = self._blob_path(file_hash)
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + f".{os.getpid()}.tmp")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise RemoteError(f"cannot upload {src} to the relay: {exc}") from exc

    def read_blob_to(self, file_hash: str, dst: Path) -> None:
        """Copy a blob out and verify it.

        The verification is not paranoia: with a cloud-backed relay a blob file can
        exist as a zero-length placeholder that has not downloaded yet, and applying
        that would silently blank a save file.
        """
        src = self._blob_path(file_hash)
        if not src.exists():
            raise RemoteError(
                f"blob {file_hash} is missing from the relay; "
                "the relay may not have finished syncing"
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(src, dst)
        except OSError as exc:
            raise RemoteError(f"cannot read blob {file_hash} from the relay: {exc}") from exc
        actual = hash_file(dst)
        if actual != file_hash:
            dst.unlink(missing_ok=True)
            raise RemoteError(
                f"blob {file_hash} failed verification (got {actual}); "
                "the relay copy is corrupt or incomplete"
            )

    def lock(self, machine: str) -> RemoteLock:
        return RemoteLock(self.lock_file, machine)
