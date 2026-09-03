"""HTTP API over the engine.

Every route is a thin call into Engine. No sync decisions are made here, so the UI
cannot end up with rules of its own that drift from the CLI.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import account_client, dialogs, discovery
from .account_client import AccountError
from .apply import ApplyError
from .config import POLICIES, POLICY_ASK, REMOTE_KINDS, Config, ConfigError
from .dialogs import DialogError
from .engine import ConflictError, Engine, EngineError
from .guard import GuardError
from .lock import LockError
from .scanner import ScanError, scan_files
from .store import RemoteError

STATIC_DIR = Path(__file__).parent / "static"

# Errors that describe something the user can act on, mapped to a status code and a
# machine-readable kind the UI switches on.
USER_ERRORS: tuple[tuple[type[Exception], int, str], ...] = (
    (ConflictError, 409, "conflict"),
    (LockError, 423, "locked"),
    (GuardError, 409, "guarded"),
    (RemoteError, 502, "relay"),
    (DialogError, 500, "dialog"),
    (ScanError, 400, "scan"),
    (ApplyError, 500, "apply"),
    (ConfigError, 404, "config"),
    (EngineError, 400, "engine"),
    (AccountError, 502, "account"),
)


class EventHub:
    """Fan-out of engine events to any connected UI.

    Engine calls run in FastAPI's worker threads, so publishing hops back onto the
    event loop rather than touching the queues directly.
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        for queue in list(self._queues):
            try:
                self._loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass


# -- request bodies ---------------------------------------------------------


class ProfileIn(BaseModel):
    name: str
    local_path: str
    relay_path: str = ""  # unused, and left blank, when remote_kind is "cloud"
    excludes: Optional[list[str]] = None
    policy: str = POLICY_ASK
    guard_processes: list[str] = Field(default_factory=list)
    remote_kind: str = "folder"


class ProfilePatch(BaseModel):
    name: Optional[str] = None
    local_path: Optional[str] = None
    relay_path: Optional[str] = None
    excludes: Optional[list[str]] = None
    policy: Optional[str] = None
    guard_processes: Optional[list[str]] = None


class PushIn(BaseModel):
    note: str = ""


class RestoreIn(BaseModel):
    rev: int


class ResolveIn(BaseModel):
    choice: str
    note: str = ""


class SettingsIn(BaseModel):
    machine: Optional[str] = None
    backup_retention: Optional[int] = None


class PickFolderIn(BaseModel):
    initial: Optional[str] = None
    title: str = "Choose a folder"


class DiscoverIn(BaseModel):
    relay_path: str


class AdoptIn(BaseModel):
    id: str  # the exact id discovery reported - must match the relay/account slug
    name: str
    local_path: str
    relay_path: str = ""
    excludes: Optional[list[str]] = None
    policy: str = POLICY_ASK
    guard_processes: list[str] = Field(default_factory=list)
    remote_kind: str = "folder"


class RegisterIn(BaseModel):
    server_url: str
    username: str
    password: str


class LoginIn(BaseModel):
    server_url: str
    username: str
    password: str


def create_app(config: Config | None = None, token: str | None = None) -> FastAPI:
    config = config or Config()
    token = token if token is not None else os.environ.get("SAVESYNC_TOKEN") or None
    engine = Engine(config)
    hub = EventHub()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Save Syncer", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.hub = hub
    app.state.token = token

    # The Vite dev server runs on another origin during development only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(x_savesync_token: str | None = Header(default=None)) -> None:
        if token and x_savesync_token != token:
            raise HTTPException(status_code=401, detail="bad or missing session token")

    auth = [Depends(require_token)]

    for exc_type, status, kind in USER_ERRORS:

        def make_handler(status: int = status, kind: str = kind):
            async def handler(_request: Request, exc: Exception) -> JSONResponse:
                return JSONResponse(status_code=status, content={"detail": str(exc), "kind": kind})

            return handler

        app.add_exception_handler(exc_type, make_handler())

    def announce(profile_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
        hub.publish({"type": "changed", "profile_id": profile_id, "action": action, **(payload or {})})

    # -- meta ---------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "home": str(config.home), "machine": engine.machine}

    @app.get("/api/settings", dependencies=auth)
    def get_settings() -> dict[str, Any]:
        return {**config.settings().to_dict(), "home": str(config.home)}

    @app.patch("/api/settings", dependencies=auth)
    def patch_settings(body: SettingsIn) -> dict[str, Any]:
        current = config.settings()
        if body.machine is not None:
            current.machine = body.machine
        if body.backup_retention is not None:
            current.backup_retention = max(0, body.backup_retention)
        config.save_settings(current)
        return {**current.to_dict(), "home": str(config.home)}

    @app.get("/api/fs/check", dependencies=auth)
    def check_path(path: str = Query(...)) -> dict[str, Any]:
        """Used by the add-profile form to tell the user what it is pointing at."""
        target = Path(os.path.expandvars(path)).expanduser()
        if not target.exists():
            return {"path": str(target), "exists": False, "is_dir": False, "file_count": 0}
        if not target.is_dir():
            return {"path": str(target), "exists": True, "is_dir": False, "file_count": 0}
        try:
            files = scan_files(target)
        except ScanError:
            files = ()
        return {
            "path": str(target),
            "exists": True,
            "is_dir": True,
            "file_count": len(files),
            "total_size": sum(f.size for f in files),
        }

    @app.post("/api/fs/pick-folder", dependencies=auth)
    def pick_folder(body: PickFolderIn) -> dict[str, Any]:
        """Opens a native folder picker on this machine and blocks until it closes."""
        path = dialogs.pick_folder(body.initial, body.title)
        return {"path": path}

    @app.get("/api/fs/cloud-roots", dependencies=auth)
    def cloud_roots() -> list[dict[str, str]]:
        """Cloud-sync folders (Dropbox, OneDrive, ...) already set up on this machine."""
        return dialogs.detect_cloud_roots()

    @app.post("/api/relay/discover", dependencies=auth)
    def discover_relay(body: DiscoverIn) -> list[dict[str, Any]]:
        """Save histories already sitting in a relay folder, so the second desktop can
        pick one instead of having to retype the exact same profile name."""
        root = Path(os.path.expandvars(body.relay_path)).expanduser()
        known_ids = {p.id for p in config.list_profiles()}
        return discovery.discover(root, known_ids)

    @app.get("/api/account/discover", dependencies=auth)
    def discover_account() -> list[dict[str, Any]]:
        """The cloud equivalent of /api/relay/discover: every save already stored in
        the signed-in account, so a new device can adopt one instead of starting a
        fresh history under the same name by accident."""
        settings = config.settings()
        if not settings.signed_in:
            raise HTTPException(status_code=401, detail="not signed in")
        known_ids = {p.id for p in config.list_profiles()}
        return account_client.list_profiles(settings.server_url, settings.account_token, known_ids)

    @app.post("/api/profiles/adopt", dependencies=auth, status_code=201)
    def adopt_profile(body: AdoptIn) -> dict[str, Any]:
        """Link this desktop to an existing save history rather than starting a new one."""
        if body.policy not in POLICIES:
            raise HTTPException(status_code=400, detail=f"policy must be one of {POLICIES}")
        if body.remote_kind not in REMOTE_KINDS:
            raise HTTPException(status_code=400, detail=f"remote_kind must be one of {REMOTE_KINDS}")
        profile = config.add_profile(
            body.name,
            body.local_path,
            body.relay_path,
            excludes=body.excludes,
            policy=body.policy,
            guard_processes=body.guard_processes,
            adopt_id=body.id,
            remote_kind=body.remote_kind,
        )
        announce(profile.id, "created")
        return profile.to_dict()

    # -- account --------------------------------------------------------------

    @app.get("/api/account", dependencies=auth)
    def account_status() -> dict[str, Any]:
        settings = config.settings()
        return {
            "signed_in": settings.signed_in,
            "server_url": settings.server_url,
            "username": settings.account_username,
        }

    @app.post("/api/account/register", dependencies=auth)
    def account_register(body: RegisterIn) -> dict[str, Any]:
        result = account_client.register(body.server_url, body.username, body.password)
        settings = config.settings()
        settings.server_url = body.server_url
        settings.account_token = result["token"]
        settings.account_username = result["user"]["username"]
        config.save_settings(settings)
        return {"signed_in": True, "server_url": settings.server_url, "username": settings.account_username}

    @app.post("/api/account/login", dependencies=auth)
    def account_login(body: LoginIn) -> dict[str, Any]:
        result = account_client.login(body.server_url, body.username, body.password)
        settings = config.settings()
        settings.server_url = body.server_url
        settings.account_token = result["token"]
        settings.account_username = result["user"]["username"]
        config.save_settings(settings)
        return {"signed_in": True, "server_url": settings.server_url, "username": settings.account_username}

    @app.post("/api/account/logout", dependencies=auth)
    def account_logout() -> dict[str, Any]:
        settings = config.settings()
        settings.account_token = ""
        settings.account_username = ""
        config.save_settings(settings)
        return {"signed_in": False}

    # -- profiles -----------------------------------------------------------

    @app.get("/api/profiles", dependencies=auth)
    def list_profiles() -> list[dict[str, Any]]:
        out = []
        for profile in config.list_profiles():
            entry: dict[str, Any] = {"profile": profile.to_dict()}
            try:
                entry["status"] = engine.status(profile.id).to_dict()
            except Exception as exc:  # one broken relay must not blank the whole list
                entry["status"] = None
                entry["error"] = str(exc)
            out.append(entry)
        return out

    @app.post("/api/profiles", dependencies=auth, status_code=201)
    def create_profile(body: ProfileIn) -> dict[str, Any]:
        if body.policy not in POLICIES:
            raise HTTPException(status_code=400, detail=f"policy must be one of {POLICIES}")
        if body.remote_kind not in REMOTE_KINDS:
            raise HTTPException(status_code=400, detail=f"remote_kind must be one of {REMOTE_KINDS}")
        profile = config.add_profile(
            body.name,
            body.local_path,
            body.relay_path,
            excludes=body.excludes,
            policy=body.policy,
            guard_processes=body.guard_processes,
            remote_kind=body.remote_kind,
        )
        announce(profile.id, "created")
        return profile.to_dict()

    @app.patch("/api/profiles/{profile_id}", dependencies=auth)
    def patch_profile(profile_id: str, body: ProfilePatch) -> dict[str, Any]:
        profile = config.update_profile(profile_id, **body.model_dump(exclude_none=True))
        announce(profile_id, "updated")
        return profile.to_dict()

    @app.delete("/api/profiles/{profile_id}", dependencies=auth)
    def delete_profile(profile_id: str) -> dict[str, bool]:
        config.delete_profile(profile_id)
        announce(profile_id, "deleted")
        return {"ok": True}

    @app.get("/api/profiles/{profile_id}/status", dependencies=auth)
    def get_status(profile_id: str) -> dict[str, Any]:
        return engine.status(profile_id).to_dict()

    @app.get("/api/profiles/{profile_id}/revisions", dependencies=auth)
    def get_revisions(profile_id: str) -> list[dict[str, Any]]:
        return engine.revisions(profile_id)

    @app.get("/api/profiles/{profile_id}/revisions/{rev}", dependencies=auth)
    def get_revision(profile_id: str, rev: int) -> dict[str, Any]:
        return engine.revision_detail(profile_id, rev)

    # -- actions ------------------------------------------------------------

    @app.post("/api/profiles/{profile_id}/push", dependencies=auth)
    def do_push(profile_id: str, body: PushIn | None = None) -> dict[str, Any]:
        result = engine.push(profile_id, (body.note if body else ""))
        announce(profile_id, "push")
        return result.to_dict()

    @app.post("/api/profiles/{profile_id}/pull", dependencies=auth)
    def do_pull(profile_id: str) -> dict[str, Any]:
        result = engine.pull(profile_id)
        announce(profile_id, "pull")
        return result.to_dict()

    @app.post("/api/profiles/{profile_id}/sync", dependencies=auth)
    def do_sync(profile_id: str, body: PushIn | None = None) -> dict[str, Any]:
        result = engine.sync(profile_id, (body.note if body else ""))
        announce(profile_id, "sync")
        return result.to_dict()

    @app.post("/api/profiles/{profile_id}/restore", dependencies=auth)
    def do_restore(profile_id: str, body: RestoreIn) -> dict[str, Any]:
        result = engine.restore(profile_id, body.rev)
        announce(profile_id, "restore")
        return result.to_dict()

    @app.post("/api/profiles/{profile_id}/resolve", dependencies=auth)
    def do_resolve(profile_id: str, body: ResolveIn) -> dict[str, Any]:
        result = engine.resolve(profile_id, body.choice, body.note)
        announce(profile_id, "resolve")
        return result.to_dict()

    # -- backups ------------------------------------------------------------

    @app.get("/api/profiles/{profile_id}/backups", dependencies=auth)
    def get_backups(profile_id: str) -> list[dict[str, Any]]:
        return engine.backups(profile_id)

    @app.post("/api/profiles/{profile_id}/backups/{backup_id}/restore", dependencies=auth)
    def do_restore_backup(profile_id: str, backup_id: str) -> dict[str, Any]:
        result = engine.restore_backup(profile_id, backup_id)
        announce(profile_id, "restore_backup")
        return result.to_dict()

    # -- live updates -------------------------------------------------------

    @app.websocket("/api/events")
    async def events(websocket: WebSocket, token_q: str | None = Query(default=None, alias="token")) -> None:
        if token and token_q != token:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = hub.subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(queue)

    # -- built frontend -----------------------------------------------------

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    return app


app = create_app()
