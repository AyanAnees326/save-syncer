"""The hosted account server.

A second implementation of the same store protocol LocalDirRemote already serves
locally out of a folder - here it is a per-account, per-slug revision history backed
by SQLite plus a blob directory, reachable over HTTP instead of a shared filesystem.
Everything is scoped by the Bearer token's user id; there is no endpoint here that
returns or accepts data without that scope.

This is meant to be self-hosted: run it on a machine you control (a home server, a
small VPS, ...) and point desktops at its URL. Nothing here signs you up for hosting -
see the README for what running it actually involves.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..hashing import hash_bytes
from ..manifest import Manifest
from . import auth
from .db import Database

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
LOCK_TTL = 120.0


class RegisterIn(BaseModel):
    username: str
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    username: str
    password: str


class HeadIn(BaseModel):
    rev: int


class LockIn(BaseModel):
    machine: str


def create_server(db: Database | None = None) -> FastAPI:
    db = db or Database()
    app = FastAPI(title="Save Syncer Account Server", version="0.1.0")
    app.state.db = db

    def bad_request(detail: str) -> HTTPException:
        return HTTPException(status_code=400, detail=detail)

    def current_user_id(authorization: Optional[str] = Header(default=None)) -> int:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        user_id = auth.verify_token(authorization.removeprefix("Bearer "), db.secret_key)
        if user_id is None or db.get_user(user_id) is None:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        return user_id

    def require_slug(slug: str) -> str:
        if not SLUG_RE.match(slug):
            raise bad_request(f"invalid profile id: {slug!r}")
        return slug

    # -- auth -----------------------------------------------------------------

    @app.post("/api/account/register", status_code=201)
    def register(body: RegisterIn) -> dict[str, Any]:
        if not USERNAME_RE.match(body.username):
            raise bad_request(
                "username must be 3-32 characters: letters, numbers, underscore, dot or hyphen"
            )
        if db.get_user_by_username(body.username) is not None:
            raise HTTPException(status_code=409, detail="that username is taken")
        user_id = db.create_user(body.username, auth.hash_password(body.password))
        token = auth.make_token(user_id, db.secret_key)
        return {"token": token, "user": {"id": user_id, "username": body.username}}

    @app.post("/api/account/login")
    def login(body: LoginIn) -> dict[str, Any]:
        row = db.get_user_by_username(body.username)
        if row is None or not auth.verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="wrong username or password")
        token = auth.make_token(row["id"], db.secret_key)
        return {"token": token, "user": {"id": row["id"], "username": row["username"]}}

    @app.get("/api/account/me")
    def me(user_id: int = Depends(current_user_id)) -> dict[str, Any]:
        row = db.get_user(user_id)
        return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}

    @app.get("/api/account/profiles")
    def account_profiles(user_id: int = Depends(current_user_id)) -> list[dict[str, Any]]:
        """Every save history in this account - what the discovery screen shows a new device."""
        out = []
        for profile in db.list_profiles(user_id):
            if profile["head_rev"] is None:
                continue
            manifest_json = db.read_revision(profile["id"], profile["head_rev"])
            if not manifest_json:
                continue
            manifest = Manifest.from_dict(json.loads(manifest_json))
            out.append(
                {
                    "id": profile["slug"],
                    "name": manifest.profile_name or profile["slug"].replace("-", " ").title(),
                    "rev": profile["head_rev"],
                    "machine": manifest.machine,
                    "created_at": manifest.created_at,
                    "note": manifest.note,
                    "file_count": manifest.file_count,
                    "total_size": manifest.total_size,
                    "source_local_path": manifest.source_local_path,
                }
            )
        return out

    # -- store protocol ---------------------------------------------------------
    # Mirrors LocalDirRemote's operations one for one, so HttpRemote on the client
    # side can implement the exact same Remote interface the engine already uses.

    @app.get("/api/store/{slug}/head")
    def get_head(slug: str, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
        slug = require_slug(slug)
        profile = db.find_profile(user_id, slug)
        return {"rev": profile["head_rev"] if profile else None}

    @app.put("/api/store/{slug}/head")
    def put_head(slug: str, body: HeadIn, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
        slug = require_slug(slug)
        profile_id = db.get_or_create_profile(user_id, slug)
        db.set_head(profile_id, body.rev)
        return {"ok": True}

    @app.get("/api/store/{slug}/revisions")
    def get_revision_list(slug: str, user_id: int = Depends(current_user_id)) -> list[int]:
        slug = require_slug(slug)
        profile = db.find_profile(user_id, slug)
        return db.list_revisions(profile["id"]) if profile else []

    @app.get("/api/store/{slug}/revisions/{rev}")
    def get_revision(slug: str, rev: int, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
        slug = require_slug(slug)
        profile = db.find_profile(user_id, slug)
        manifest_json = db.read_revision(profile["id"], rev) if profile else None
        if manifest_json is None:
            raise HTTPException(status_code=404, detail=f"revision {rev} not found")
        return json.loads(manifest_json)

    @app.put("/api/store/{slug}/revisions/{rev}")
    def put_revision(
        slug: str, rev: int, body: dict[str, Any], user_id: int = Depends(current_user_id)
    ) -> dict[str, Any]:
        slug = require_slug(slug)
        profile_id = db.get_or_create_profile(user_id, slug)
        db.write_revision(profile_id, rev, json.dumps(body))
        return {"ok": True}

    @app.get("/api/store/{slug}/blobs/{file_hash}/exists")
    def blob_exists(
        slug: str, file_hash: str, user_id: int = Depends(current_user_id)
    ) -> dict[str, bool]:
        require_slug(slug)
        return {"exists": db.has_blob(user_id, file_hash)}

    @app.get("/api/store/{slug}/blobs/{file_hash}")
    def get_blob(slug: str, file_hash: str, user_id: int = Depends(current_user_id)) -> Response:
        require_slug(slug)
        path = db.blob_path_for_read(user_id, file_hash)
        if path is None:
            raise HTTPException(status_code=404, detail=f"blob {file_hash} not found")
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    @app.put("/api/store/{slug}/blobs/{file_hash}")
    async def put_blob(
        slug: str, file_hash: str, request: Request, user_id: int = Depends(current_user_id)
    ) -> dict[str, Any]:
        require_slug(slug)
        data = await request.body()
        actual = hash_bytes(data)
        if actual != file_hash:
            raise bad_request(f"uploaded bytes hash to {actual}, not the claimed {file_hash}")
        db.write_blob(user_id, file_hash, data)
        return {"ok": True}

    @app.post("/api/store/{slug}/lock")
    def acquire_lock(
        slug: str, body: LockIn, user_id: int = Depends(current_user_id)
    ) -> dict[str, Any]:
        require_slug(slug)
        profile_id = db.get_or_create_profile(user_id, slug)
        acquired, existing = db.acquire_lock(profile_id, body.machine, LOCK_TTL)
        if not acquired:
            assert existing is not None
            raise HTTPException(
                status_code=423,
                detail=f"locked by {existing['machine']} for another "
                f"{int(existing['expires_at'] - time.time())}s",
            )
        return {"ok": True, "expires_in": LOCK_TTL}

    @app.delete("/api/store/{slug}/lock")
    def release_lock(
        slug: str, body: LockIn, user_id: int = Depends(current_user_id)
    ) -> dict[str, Any]:
        require_slug(slug)
        profile = db.find_profile(user_id, slug)
        if profile:
            db.release_lock(profile["id"], body.machine)
        return {"ok": True}

    @app.exception_handler(HTTPException)
    async def http_exc_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_server()
