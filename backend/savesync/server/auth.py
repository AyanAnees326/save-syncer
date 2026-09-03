"""Password hashing and session tokens for the hosted account server.

Tokens are a lightweight signed-and-expiring scheme (HMAC over a JSON payload)
rather than a JWT library, since the payload here is trivial (one user id) and this
keeps the server's dependency list small.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import bcrypt

TOKEN_TTL_SECONDS = 365 * 24 * 3600  # a year - this is a personal tool, not a bank


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False  # a malformed stored hash should fail closed, not raise


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def make_token(user_id: int, secret: bytes) -> str:
    payload = json.dumps({"uid": user_id, "exp": time.time() + TOKEN_TTL_SECONDS}).encode("utf-8")
    payload_b64 = _b64encode(payload)
    signature = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64encode(signature)}"


def verify_token(token: str, secret: bytes) -> int | None:
    """Returns the user id if the token is well-formed, correctly signed and unexpired."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(sig_b64)):
            return None
        payload = json.loads(_b64decode(payload_b64))
        if payload["exp"] < time.time():
            return None
        return int(payload["uid"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
