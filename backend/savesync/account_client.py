"""Thin client for the account-level calls that aren't tied to one profile's Remote:
register, log in, and list what an account already has stored - the cloud equivalent
of scanning a relay folder in discovery.py.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT = 15.0


class AccountError(Exception):
    pass


def _call(method: str, server_url: str, path: str, **kw: Any) -> Any:
    try:
        response = httpx.request(method, f"{server_url.rstrip('/')}{path}", timeout=TIMEOUT, **kw)
    except httpx.HTTPError as exc:
        raise AccountError(f"cannot reach {server_url}: {exc}") from exc
    if not response.is_success:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise AccountError(str(detail))
    return response.json()


def register(server_url: str, username: str, password: str) -> dict[str, Any]:
    return _call("POST", server_url, "/api/account/register", json={"username": username, "password": password})


def login(server_url: str, username: str, password: str) -> dict[str, Any]:
    return _call("POST", server_url, "/api/account/login", json={"username": username, "password": password})


def whoami(server_url: str, token: str) -> dict[str, Any]:
    return _call("GET", server_url, "/api/account/me", headers={"Authorization": f"Bearer {token}"})


def list_profiles(server_url: str, token: str, known_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Every save history in the account, shaped exactly like discovery.discover()'s
    output so the frontend can reuse the same "found saves" screen for both."""
    known_ids = known_ids or set()
    entries = _call(
        "GET", server_url, "/api/account/profiles", headers={"Authorization": f"Bearer {token}"}
    )
    for entry in entries:
        entry["already_added"] = entry["id"] in known_ids
    return entries
