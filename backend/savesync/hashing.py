"""Content hashing.

Every decision the engine makes about "are these the same bytes" runs through here.
Hashes are prefixed with the algorithm so the on-disk format can change later without
ambiguity.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ALGO = "blake2b"
DIGEST_SIZE = 32
CHUNK = 1024 * 1024


def _new():
    return hashlib.blake2b(digest_size=DIGEST_SIZE)


def hash_file(path: Path) -> str:
    """Hash a file's contents. Returns "blake2b:<hex>"."""
    h = _new()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return f"{ALGO}:{h.hexdigest()}"


def hash_bytes(data: bytes) -> str:
    h = _new()
    h.update(data)
    return f"{ALGO}:{h.hexdigest()}"


def blob_key(file_hash: str) -> tuple[str, str]:
    """Split a hash into (shard, name) for the sharded blob directory layout."""
    digest = file_hash.split(":", 1)[-1]
    if len(digest) < 4:
        raise ValueError(f"malformed hash: {file_hash!r}")
    return digest[:2], digest[2:]
