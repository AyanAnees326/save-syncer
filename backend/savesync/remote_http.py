"""A Remote backed by the hosted account server instead of a shared folder.

Same contract as LocalDirRemote (store.py) - the engine talks to whichever one a
profile is configured with and never knows the difference. This one makes HTTP calls
instead of touching a filesystem; "the relay" here is an account on a server rather
than a Dropbox folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .hashing import hash_file
from .manifest import Manifest
from .store import LockLike, Remote, RemoteError

TIMEOUT = 30.0


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text
    raise RemoteError(f"account server error ({response.status_code}): {detail}")


class HttpRemoteLock:
    """Acquire/heartbeat/release against the server's lock endpoint for one profile.

    Structurally matches store.LockLike; does not subclass the file-based RemoteLock
    since the two share no implementation, only the shape Engine relies on.
    """

    def __init__(self, client: httpx.Client, slug: str, machine: str) -> None:
        self._client = client
        self._slug = slug
        self._machine = machine
        self._held = False

    def acquire(self) -> None:
        response = self._client.post(
            f"/api/store/{self._slug}/lock", json={"machine": self._machine}, timeout=TIMEOUT
        )
        if response.status_code == 423:
            detail = response.json().get("detail", "the account is locked by another device")
            raise RemoteError(detail)
        _raise_for(response)
        self._held = True

    def heartbeat(self) -> None:
        if self._held:
            self.acquire()

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._client.request(
                "DELETE",
                f"/api/store/{self._slug}/lock",
                json={"machine": self._machine},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError:
            pass  # best-effort - the TTL expires it either way
        self._held = False

    def __enter__(self) -> "HttpRemoteLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class HttpRemote(Remote):
    def __init__(self, server_url: str, token: str, slug: str) -> None:
        self.slug = slug
        self._client = httpx.Client(
            base_url=server_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **kw: Any) -> httpx.Response:
        try:
            response = self._client.get(path, timeout=TIMEOUT, **kw)
        except httpx.HTTPError as exc:
            raise RemoteError(f"cannot reach the account server: {exc}") from exc
        _raise_for(response)
        return response

    # -- required interface -----------------------------------------------

    def exists(self) -> bool:
        return self.read_head() is not None

    def read_head(self) -> int | None:
        return self._get(f"/api/store/{self.slug}/head").json()["rev"]

    def write_head(self, rev: int) -> None:
        try:
            response = self._client.put(
                f"/api/store/{self.slug}/head", json={"rev": rev}, timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise RemoteError(f"cannot reach the account server: {exc}") from exc
        _raise_for(response)

    def list_revisions(self) -> list[int]:
        return self._get(f"/api/store/{self.slug}/revisions").json()

    def read_manifest(self, rev: int) -> Manifest:
        try:
            response = self._client.get(f"/api/store/{self.slug}/revisions/{rev}", timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise RemoteError(f"cannot reach the account server: {exc}") from exc
        if response.status_code == 404:
            raise RemoteError(f"revision {rev} is missing from your account")
        _raise_for(response)
        manifest = Manifest.from_dict(response.json())
        if manifest.rev is None:
            manifest.rev = rev
        return manifest

    def write_manifest(self, manifest: Manifest) -> None:
        if manifest.rev is None:
            raise RemoteError("cannot write a manifest with no revision number")
        try:
            response = self._client.put(
                f"/api/store/{self.slug}/revisions/{manifest.rev}",
                json=manifest.to_dict(),
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise RemoteError(f"cannot reach the account server: {exc}") from exc
        _raise_for(response)

    def has_blob(self, file_hash: str) -> bool:
        return self._get(f"/api/store/{self.slug}/blobs/{file_hash}/exists").json()["exists"]

    def write_blob(self, file_hash: str, src: Path) -> None:
        if self.has_blob(file_hash):
            return
        try:
            response = self._client.put(
                f"/api/store/{self.slug}/blobs/{file_hash}",
                content=src.read_bytes(),
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise RemoteError(f"cannot reach the account server: {exc}") from exc
        _raise_for(response)

    def read_blob_to(self, file_hash: str, dst: Path) -> None:
        try:
            response = self._client.get(f"/api/store/{self.slug}/blobs/{file_hash}", timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise RemoteError(f"cannot reach the account server: {exc}") from exc
        if response.status_code == 404:
            raise RemoteError(
                f"blob {file_hash} is missing from your account; the upload may not have finished"
            )
        _raise_for(response)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(response.content)
        actual = hash_file(dst)
        if actual != file_hash:
            dst.unlink(missing_ok=True)
            raise RemoteError(
                f"blob {file_hash} failed verification (got {actual}); the download was corrupted"
            )

    def lock(self, machine: str) -> LockLike:
        return HttpRemoteLock(self._client, self.slug, machine)
