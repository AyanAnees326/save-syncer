"""Profiles and app settings, stored as JSON under the app home.

SAVESYNC_HOME overrides the location of everything. Tests use it to run two
independent machines inside one process; it is also how a portable install off a USB
stick would work.
"""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .manifest import utc_now
from .scanner import DEFAULT_EXCLUDES

POLICY_ASK = "ask"
POLICY_LATEST_WINS = "latest_wins"
POLICIES = (POLICY_ASK, POLICY_LATEST_WINS)

REMOTE_FOLDER = "folder"
REMOTE_CLOUD = "cloud"
REMOTE_KINDS = (REMOTE_FOLDER, REMOTE_CLOUD)


def default_home() -> Path:
    override = os.environ.get("SAVESYNC_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "savesync"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "savesync"


def default_machine_name() -> str:
    return os.environ.get("SAVESYNC_MACHINE") or platform.node() or "unknown-machine"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


@dataclass(slots=True)
class Profile:
    id: str
    name: str
    local_path: str
    relay_path: str
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    policy: str = POLICY_ASK
    guard_processes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    # "folder": relay_path is a shared folder, exactly as before. "cloud": storage is
    # the account signed into on this machine (Settings.server_url/account_token) and
    # relay_path is unused.
    remote_kind: str = REMOTE_FOLDER

    @property
    def local(self) -> Path:
        return Path(os.path.expandvars(self.local_path)).expanduser()

    @property
    def relay(self) -> Path:
        """The per-profile store directory inside the shared relay folder.

        Only meaningful when remote_kind is "folder" - a cloud profile has no
        filesystem relay at all, so callers must branch on remote_kind before using
        this rather than relying on it to return something sensible for both.
        """
        return Path(os.path.expandvars(self.relay_path)).expanduser() / self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        return cls(
            id=d["id"],
            name=d["name"],
            local_path=d["local_path"],
            relay_path=d["relay_path"],
            excludes=list(d.get("excludes", DEFAULT_EXCLUDES)),
            policy=d.get("policy", POLICY_ASK),
            guard_processes=list(d.get("guard_processes", [])),
            created_at=d.get("created_at", ""),
            remote_kind=d.get("remote_kind", REMOTE_FOLDER),
        )


@dataclass(slots=True)
class Settings:
    machine: str = field(default_factory=default_machine_name)
    backup_retention: int = 10
    # The signed-in account, if any. account_token is a long-lived bearer token
    # returned by the server on register/login - there is nothing more sensitive
    # (no password) stored here, but it is still a credential: this file lives
    # unencrypted under the app home like everything else Config owns.
    server_url: str = ""
    account_token: str = ""
    account_username: str = ""

    @property
    def signed_in(self) -> bool:
        return bool(self.server_url and self.account_token)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Settings":
        return cls(
            machine=d.get("machine") or default_machine_name(),
            backup_retention=int(d.get("backup_retention", 10)),
            server_url=d.get("server_url", ""),
            account_token=d.get("account_token", ""),
            account_username=d.get("account_username", ""),
        )


class ConfigError(Exception):
    pass


def write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


class Config:
    """Owns the app home: profiles, settings, sync state and backups."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home) if home else default_home()

    @property
    def profiles_file(self) -> Path:
        return self.home / "profiles.json"

    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def backup_root(self) -> Path:
        return self.home / "backups"

    def settings(self) -> Settings:
        return Settings.from_dict(read_json(self.settings_file, {}))

    def save_settings(self, settings: Settings) -> Settings:
        write_json(self.settings_file, settings.to_dict())
        return settings

    def list_profiles(self) -> list[Profile]:
        return [Profile.from_dict(d) for d in read_json(self.profiles_file, [])]

    def get_profile(self, profile_id: str) -> Profile:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        raise ConfigError(f"no such profile: {profile_id}")

    def _save_profiles(self, profiles: list[Profile]) -> None:
        write_json(self.profiles_file, [p.to_dict() for p in profiles])

    def add_profile(
        self,
        name: str,
        local_path: str,
        relay_path: str,
        *,
        excludes: list[str] | None = None,
        policy: str = POLICY_ASK,
        guard_processes: list[str] | None = None,
        adopt_id: str | None = None,
        remote_kind: str = REMOTE_FOLDER,
    ) -> Profile:
        """Register a profile.

        Normally the id is derived from `name`. Pass `adopt_id` to instead reuse an
        exact id that already exists on the relay - e.g. when this desktop discovered
        an existing save history there and is linking to it rather than starting a
        fresh one, where the id has to match the relay's subfolder name precisely.

        remote_kind "cloud" ignores relay_path entirely: storage is whichever account
        is signed into on this machine (see Settings), not a shared folder.
        """
        if policy not in POLICIES:
            raise ConfigError(f"unknown policy {policy!r}, expected one of {POLICIES}")
        if remote_kind not in REMOTE_KINDS:
            raise ConfigError(f"unknown remote_kind {remote_kind!r}, expected one of {REMOTE_KINDS}")
        if remote_kind == REMOTE_FOLDER and not relay_path.strip():
            raise ConfigError("relay_path is required for a folder-based profile")
        profiles = self.list_profiles()
        existing = {p.id for p in profiles}
        if adopt_id:
            if adopt_id in existing:
                raise ConfigError(f"a profile with id {adopt_id!r} is already set up here")
            profile_id = adopt_id
        else:
            base_id = slugify(name)
            profile_id, n = base_id, 2
            while profile_id in existing:
                profile_id, n = f"{base_id}-{n}", n + 1
        profile = Profile(
            id=profile_id,
            name=name,
            local_path=local_path,
            relay_path=relay_path if remote_kind == REMOTE_FOLDER else "",
            excludes=list(excludes) if excludes is not None else list(DEFAULT_EXCLUDES),
            policy=policy,
            guard_processes=list(guard_processes or []),
            remote_kind=remote_kind,
        )
        profiles.append(profile)
        self._save_profiles(profiles)
        return profile

    def update_profile(self, profile_id: str, **changes: Any) -> Profile:
        profiles = self.list_profiles()
        for i, profile in enumerate(profiles):
            if profile.id != profile_id:
                continue
            for key, value in changes.items():
                if value is None or key in ("id", "created_at"):
                    continue
                if not hasattr(profile, key):
                    raise ConfigError(f"unknown profile field: {key}")
                if key == "policy" and value not in POLICIES:
                    raise ConfigError(f"unknown policy {value!r}")
                if key == "remote_kind" and value not in REMOTE_KINDS:
                    raise ConfigError(f"unknown remote_kind {value!r}")
                setattr(profile, key, value)
            profiles[i] = profile
            self._save_profiles(profiles)
            return profile
        raise ConfigError(f"no such profile: {profile_id}")

    def delete_profile(self, profile_id: str) -> None:
        profiles = self.list_profiles()
        remaining = [p for p in profiles if p.id != profile_id]
        if len(remaining) == len(profiles):
            raise ConfigError(f"no such profile: {profile_id}")
        self._save_profiles(remaining)
        (self.state_dir / f"{profile_id}.json").unlink(missing_ok=True)
