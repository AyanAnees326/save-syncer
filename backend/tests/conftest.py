"""Two simulated desktops sharing one relay folder, inside a single tmp_path.

Each machine gets its own SAVESYNC home (profiles, settings, sync state, backups) and
its own save folder, exactly as two real PCs would. The relay directory is the only
thing they share.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

from savesync.config import Config, Settings
from savesync.engine import Engine
from savesync.server.app import create_server
from savesync.server.db import Database
from savesync.store import LocalDirRemote

PROFILE_NAME = "Test Game"


@dataclass
class Machine:
    name: str
    home: Path
    save: Path
    config: Config
    engine: Engine
    profile_id: str

    # -- save folder helpers ------------------------------------------------

    def write(self, rel: str, text: str, mtime: float | None = None) -> Path:
        path = self.save / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def read(self, rel: str) -> str:
        return (self.save / rel).read_text(encoding="utf-8")

    def remove(self, rel: str) -> None:
        (self.save / rel).unlink()

    def tree(self) -> dict[str, str]:
        """Every file in the save folder, as {relative posix path: contents}."""
        out = {}
        for path in sorted(self.save.rglob("*")):
            if path.is_file():
                out[path.relative_to(self.save).as_posix()] = path.read_text(encoding="utf-8")
        return out

    # -- engine shortcuts ---------------------------------------------------

    def status(self):
        return self.engine.status(self.profile_id)

    def state(self) -> str:
        return self.engine.status(self.profile_id).state

    def push(self, note: str = ""):
        return self.engine.push(self.profile_id, note)

    def pull(self):
        return self.engine.pull(self.profile_id)

    def sync(self):
        return self.engine.sync(self.profile_id)

    def resolve(self, choice: str):
        return self.engine.resolve(self.profile_id, choice)

    def restore(self, rev: int):
        return self.engine.restore(self.profile_id, rev)

    def revisions(self):
        return self.engine.revisions(self.profile_id)

    def backups(self):
        return self.engine.backups(self.profile_id)

    def backup_root(self) -> Path:
        return self.engine.backup_root(self.config.get_profile(self.profile_id))


@dataclass
class World:
    relay: Path
    a: Machine
    b: Machine

    @property
    def store(self) -> LocalDirRemote:
        return LocalDirRemote(self.relay / self.a.profile_id)


@pytest.fixture
def world(tmp_path: Path) -> World:
    relay = tmp_path / "relay"
    relay.mkdir()

    def make(name: str, policy: str = "ask") -> Machine:
        home = tmp_path / f"home-{name}"
        save = tmp_path / name / "SaveGames"
        save.mkdir(parents=True)
        config = Config(home)
        config.save_settings(Settings(machine=name, backup_retention=5))
        profile = config.add_profile(PROFILE_NAME, str(save), str(relay), policy=policy)
        return Machine(
            name=name,
            home=home,
            save=save,
            config=config,
            engine=Engine(config),
            profile_id=profile.id,
        )

    a, b = make("DESKTOP-A"), make("DESKTOP-B")
    assert a.profile_id == b.profile_id, "both machines must address the same relay store"
    return World(relay=relay, a=a, b=b)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LiveServer:
    url: str
    db: Database


@pytest.fixture
def live_server(tmp_path: Path):
    """A real account server in a background thread, reached over actual HTTP - the
    same way a real desktop talks to it, not an in-process ASGI transport."""
    db = Database(tmp_path / "server-home")
    app = create_server(db)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    else:
        raise RuntimeError("test account server did not start in time")

    yield LiveServer(url=f"http://127.0.0.1:{port}", db=db)

    server.should_exit = True
    thread.join(timeout=5)
