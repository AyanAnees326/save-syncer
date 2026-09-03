"""All sync decisions live here.

The API and the CLI are both thin shells over this class, so there is exactly one
implementation of "what state are we in and what is safe to do" and the UI cannot
invent its own rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import apply as apply_mod
from . import guard
from .config import POLICY_LATEST_WINS, REMOTE_CLOUD, REMOTE_FOLDER, Config, Profile
from .hashing import hash_file
from .manifest import Diff, Manifest, diff_manifests, newest_mtime_of, utc_now
from .remote_http import HttpRemote
from .scanner import ScanError, scan
from .state import StateStore, SyncState
from .store import LocalDirRemote, Remote, RemoteError

# Sync states
NO_REMOTE = "no_remote"        # the relay has no store yet - this machine seeds it
UNLINKED = "unlinked"          # a store exists but this machine has never synced it
IN_SYNC = "in_sync"
LOCAL_AHEAD = "local_ahead"
REMOTE_AHEAD = "remote_ahead"
CONFLICT = "conflict"
LOCAL_MISSING = "local_missing"


class EngineError(Exception):
    pass


class ConflictError(EngineError):
    pass


@dataclass(slots=True)
class StatusReport:
    profile: Profile
    state: str
    message: str
    base_rev: int | None
    remote_rev: int | None
    local: Manifest | None
    remote: Manifest | None
    diff: Diff | None
    last_sync_at: str | None
    blocking_processes: list[str]

    @property
    def actions(self) -> list[str]:
        """What the UI is allowed to offer in this state."""
        if self.blocking_processes:
            return []
        if self.state == NO_REMOTE:
            return ["push"] if self.local and self.local.file_count else []
        if self.state == UNLINKED:
            return ["use_local", "use_remote"]
        if self.state == LOCAL_AHEAD:
            return ["push"]
        if self.state == REMOTE_AHEAD:
            return ["pull"]
        if self.state == CONFLICT:
            return ["use_local", "use_remote", "keep_both"]
        if self.state == LOCAL_MISSING:
            return ["pull"] if self.remote_rev is not None else []
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile.id,
            "profile_name": self.profile.name,
            "local_path": str(self.profile.local),
            "remote_kind": self.profile.remote_kind,
            "relay_path": str(self.profile.relay) if self.profile.remote_kind == REMOTE_FOLDER else "",
            "policy": self.profile.policy,
            "state": self.state,
            "message": self.message,
            "actions": self.actions,
            "base_rev": self.base_rev,
            "remote_rev": self.remote_rev,
            "local": self.local.summary() if self.local else None,
            "remote": self.remote.summary() if self.remote else None,
            "diff": self.diff.to_dict() if self.diff else None,
            "last_sync_at": self.last_sync_at,
            "blocking_processes": self.blocking_processes,
        }


@dataclass(slots=True)
class ActionResult:
    action: str
    message: str
    rev: int | None = None
    backup_path: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "message": self.message,
            "rev": self.rev,
            "backup_path": self.backup_path,
            **(self.extra or {}),
        }


class Engine:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.state = StateStore(config.state_dir)

    # -- helpers -----------------------------------------------------------

    @property
    def machine(self) -> str:
        return self.config.settings().machine

    def remote_for(self, profile: Profile) -> Remote:
        if profile.remote_kind == REMOTE_CLOUD:
            settings = self.config.settings()
            if not settings.signed_in:
                raise EngineError(
                    "This profile is stored in your account, but no account is signed in on "
                    "this desktop. Sign in from Settings first."
                )
            return HttpRemote(settings.server_url, settings.account_token, profile.id)
        return LocalDirRemote(profile.relay)

    def backup_root(self, profile: Profile) -> Path:
        return self.config.backup_root / profile.id

    def local_manifest(self, profile: Profile) -> Manifest | None:
        """The save folder as it is on disk right now, or None if it is not there."""
        try:
            return scan(profile.local, profile.excludes, profile=profile.id, machine=self.machine)
        except ScanError:
            return None

    def _record(
        self, profile: Profile, rev: int | None, content_id: str | None, action: str
    ) -> None:
        self.state.set(
            profile.id,
            SyncState(
                base_rev=rev,
                base_content_id=content_id,
                last_sync_at=utc_now(),
                last_action=action,
            ),
        )

    # -- status ------------------------------------------------------------

    def status(self, profile_id: str) -> StatusReport:
        profile = self.config.get_profile(profile_id)
        st = self.state.get(profile_id)
        remote = self.remote_for(profile)
        blocking = guard.blocking_processes(profile.guard_processes)

        head = remote.read_head()
        remote_manifest = remote.read_manifest(head) if head is not None else None
        local_manifest = self.local_manifest(profile)

        def report(state: str, message: str) -> StatusReport:
            return StatusReport(
                profile=profile,
                state=state,
                message=message,
                base_rev=st.base_rev,
                remote_rev=head,
                local=local_manifest,
                remote=remote_manifest,
                diff=diff_manifests(remote_manifest, local_manifest),
                last_sync_at=st.last_sync_at,
                blocking_processes=blocking,
            )

        if local_manifest is None:
            if head is None:
                return report(
                    LOCAL_MISSING, f"{profile.local} does not exist, and the relay is empty."
                )
            return report(
                LOCAL_MISSING,
                f"{profile.local} does not exist. Pull to create it from revision {head}.",
            )

        if head is None:
            if local_manifest.file_count == 0:
                return report(
                    NO_REMOTE, "Nothing here yet - no saves locally and nothing on the relay."
                )
            return report(NO_REMOTE, "First sync: push to seed the relay from this desktop.")

        assert remote_manifest is not None
        local_cid = local_manifest.content_id

        if not st.linked:
            if local_cid == remote_manifest.content_id:
                # Identical bytes on both sides - nothing worth asking the user about.
                self._record(profile, head, remote_manifest.content_id, "link")
                st = self.state.get(profile_id)
                return report(IN_SYNC, f"Linked to revision {head}; both sides already match.")
            return report(
                UNLINKED,
                f"This desktop has not synced this profile before, and its save differs from "
                f"revision {head}. Choose which copy to keep.",
            )

        local_changed = local_cid != st.base_content_id
        remote_changed = head != st.base_rev

        if not local_changed and not remote_changed:
            return report(IN_SYNC, f"Up to date with revision {head}.")
        if local_changed and not remote_changed:
            return report(
                LOCAL_AHEAD,
                f"This desktop has changes that are not on the relay yet (base {st.base_rev}).",
            )
        if remote_changed and not local_changed:
            return report(
                REMOTE_AHEAD,
                f"Revision {head} is newer than this desktop (base {st.base_rev}).",
            )
        return report(
            CONFLICT,
            f"Both sides changed since revision {st.base_rev}. "
            "Choose which copy this desktop keeps.",
        )

    # -- revisions ---------------------------------------------------------

    def revisions(self, profile_id: str) -> list[dict[str, Any]]:
        """The timeline, newest first. This is what makes any past state selectable."""
        profile = self.config.get_profile(profile_id)
        st = self.state.get(profile_id)
        remote = self.remote_for(profile)
        head = remote.read_head()
        local = self.local_manifest(profile)
        local_cid = local.content_id if local else None
        machine = self.machine

        out: list[dict[str, Any]] = []
        for rev in sorted(remote.list_revisions(), reverse=True):
            try:
                manifest = remote.read_manifest(rev)
            except RemoteError:
                continue
            summary = manifest.summary()
            summary.update(
                {
                    "is_head": rev == head,
                    "is_base": rev == st.base_rev,
                    "from_this_machine": manifest.machine == machine,
                    "matches_disk": local_cid is not None and manifest.content_id == local_cid,
                }
            )
            out.append(summary)
        return out

    def revision_detail(self, profile_id: str, rev: int) -> dict[str, Any]:
        profile = self.config.get_profile(profile_id)
        remote = self.remote_for(profile)
        manifest = remote.read_manifest(rev)
        local = self.local_manifest(profile)
        return {
            **manifest.to_dict(),
            "diff_vs_disk": diff_manifests(manifest, local).to_dict(),
            "matches_disk": bool(local and local.content_id == manifest.content_id),
        }

    # -- actions -----------------------------------------------------------

    def push(self, profile_id: str, note: str = "", *, force: bool = False) -> ActionResult:
        profile = self.config.get_profile(profile_id)
        guard.assert_clear(profile.guard_processes)
        local = self.local_manifest(profile)
        if local is None:
            raise EngineError(f"{profile.local} does not exist, so there is nothing to push.")
        if local.file_count == 0:
            raise EngineError(
                "The save folder is empty. Refusing to push an empty revision, since that "
                "would wipe the save on the other desktop."
            )

        remote = self.remote_for(profile)
        remote.initialize()
        st = self.state.get(profile_id)

        with remote.lock(self.machine) as lock:
            head = remote.read_head()
            if not force and st.linked and head != st.base_rev:
                raise ConflictError(
                    f"The relay moved to revision {head} while you were working "
                    f"(this desktop branched from {st.base_rev}). "
                    "Re-check status and resolve the conflict."
                )
            new_rev = (head or 0) + 1

            for entry in local.files:
                src = profile.local / Path(entry.path)
                # Re-hash before upload: if the game wrote to the folder between the
                # scan and now, the manifest would describe bytes that no longer exist.
                if hash_file(src) != entry.hash:
                    raise EngineError(
                        f"{entry.path} changed while the sync was running. "
                        "Close whatever is writing to the save folder and try again."
                    )
                if not remote.has_blob(entry.hash):
                    remote.write_blob(entry.hash, src)
                lock.heartbeat()

            manifest = Manifest(
                files=local.files,
                rev=new_rev,
                parent=head,
                profile=profile.id,
                profile_name=profile.name,
                machine=self.machine,
                created_at=utc_now(),
                note=note,
                source_local_path=str(profile.local),
            )
            remote.write_manifest(manifest)
            remote.write_head(new_rev)

        self._record(profile, new_rev, manifest.content_id, "push")
        return ActionResult(
            "push", f"Pushed revision {new_rev} ({manifest.file_count} files).", rev=new_rev
        )

    def pull(self, profile_id: str) -> ActionResult:
        profile = self.config.get_profile(profile_id)
        guard.assert_clear(profile.guard_processes)
        remote = self.remote_for(profile)
        head = remote.read_head()
        if head is None:
            raise EngineError("The relay has no revisions yet, so there is nothing to pull.")
        manifest = remote.read_manifest(head)
        result = apply_mod.apply_manifest(
            remote,
            manifest,
            profile.local,
            self.backup_root(profile),
            retention=self.config.settings().backup_retention,
            backup_label="pre-pull",
        )
        self._record(profile, head, manifest.content_id, "pull")
        return ActionResult(
            "pull",
            f"Pulled revision {head} ({result.written} files written, {result.removed} removed).",
            rev=head,
            backup_path=str(result.backup_path) if result.backup_path else None,
        )

    def restore(self, profile_id: str, rev: int) -> ActionResult:
        """Put any past revision onto this desktop.

        base_rev deliberately stays at the relay head. The restored state genuinely
        differs from head, so the profile then reads as "local ahead" and a push
        publishes the rollback as a new revision instead of rewriting history.
        """
        profile = self.config.get_profile(profile_id)
        guard.assert_clear(profile.guard_processes)
        remote = self.remote_for(profile)
        manifest = remote.read_manifest(rev)
        head = remote.read_head()
        head_manifest = remote.read_manifest(head) if head is not None else None

        result = apply_mod.apply_manifest(
            remote,
            manifest,
            profile.local,
            self.backup_root(profile),
            retention=self.config.settings().backup_retention,
            backup_label=f"pre-restore-{rev}",
        )
        self._record(
            profile,
            head,
            head_manifest.content_id if head_manifest else None,
            f"restore:{rev}",
        )
        tail = "" if rev == head else " Push to publish it to the other desktop."
        return ActionResult(
            "restore",
            f"Restored revision {rev} to this desktop ({result.written} files).{tail}",
            rev=rev,
            backup_path=str(result.backup_path) if result.backup_path else None,
        )

    def resolve(self, profile_id: str, choice: str, note: str = "") -> ActionResult:
        """Settle a conflict, or link a desktop that has never synced this profile.

        Every branch is non-destructive in the way that matters: the losing copy is
        either already in relay history or gets written to the backup area first.
        """
        profile = self.config.get_profile(profile_id)

        if choice == "use_remote":
            return self.pull(profile_id)

        if choice == "use_local":
            result = self.push(profile_id, note or "resolved: kept this desktop", force=True)
            result.message = f"Kept this desktop's save. {result.message}"
            return result

        if choice == "keep_both":
            guard.assert_clear(profile.guard_processes)
            remote = self.remote_for(profile)
            head = remote.read_head()
            saved_to = None
            if head is not None:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                saved_to = self.backup_root(profile) / f"{stamp}-remote-rev{head}"
                apply_mod.materialize(remote, remote.read_manifest(head), saved_to)
            result = self.push(profile_id, note or "resolved: kept both", force=True)
            result.action = "keep_both"
            if saved_to is not None:
                result.message = (
                    f"Kept this desktop's save as revision {result.rev}. The other copy is "
                    f"still in history as revision {head}, and was written to {saved_to} "
                    "so you can look at it."
                )
            else:
                result.message = f"Kept this desktop's save as revision {result.rev}."
            result.extra = {"remote_copy_path": str(saved_to) if saved_to else None}
            return result

        raise EngineError(
            f"unknown resolution {choice!r} (expected use_local, use_remote or keep_both)"
        )

    def auto_resolve(self, profile_id: str) -> ActionResult:
        """Latest-wins, applied only after the three-way check already said conflict.

        This is the single place where mtime decides anything, which is why it is
        opt-in per profile: across two machines a wrong clock silently picks the wrong
        save. The losing side stays recoverable from history and from the backups.
        """
        report = self.status(profile_id)
        if report.state != CONFLICT:
            raise EngineError(f"Latest-wins only applies to conflicts (state is {report.state}).")
        assert report.local is not None and report.remote is not None and report.diff is not None

        touched = report.diff.added + report.diff.changed + report.diff.removed
        local_time = newest_mtime_of(touched, report.local)
        remote_time = newest_mtime_of(touched, report.remote)
        if (local_time or -1.0) >= (remote_time or -1.0):
            result = self.resolve(profile_id, "use_local", "auto-resolved: this desktop was newer")
        else:
            result = self.resolve(profile_id, "use_remote")
        result.message = f"Latest-wins: {result.message}"
        return result

    def sync(self, profile_id: str, note: str = "") -> ActionResult:
        """Do whatever the status says is safe. The one-button path."""
        report = self.status(profile_id)
        if report.blocking_processes:
            raise guard.GuardError(
                f"{', '.join(report.blocking_processes)} is running. Close it before syncing."
            )
        if report.state == IN_SYNC:
            return ActionResult("none", report.message, rev=report.remote_rev)
        if report.state in (LOCAL_AHEAD, NO_REMOTE):
            return self.push(profile_id, note)
        if report.state in (REMOTE_AHEAD, LOCAL_MISSING):
            return self.pull(profile_id)
        if report.state == CONFLICT and self.config.get_profile(profile_id).policy == POLICY_LATEST_WINS:
            return self.auto_resolve(profile_id)
        raise ConflictError(report.message)

    # -- backups -----------------------------------------------------------

    def backups(self, profile_id: str) -> list[dict[str, Any]]:
        profile = self.config.get_profile(profile_id)
        return apply_mod.list_backups(self.backup_root(profile))

    def restore_backup(self, profile_id: str, backup_id: str) -> ActionResult:
        profile = self.config.get_profile(profile_id)
        guard.assert_clear(profile.guard_processes)
        root = self.backup_root(profile)
        target = root / backup_id
        if not target.is_dir() or target.parent != root:
            raise EngineError(f"no such backup: {backup_id}")
        safety = apply_mod.restore_backup(target, profile.local, root)
        # base_rev is unchanged: the disk now differs from it, which is exactly what
        # should make the next status read as "local ahead".
        st = self.state.get(profile_id)
        self._record(profile, st.base_rev, st.base_content_id, f"restore_backup:{backup_id}")
        return ActionResult(
            "restore_backup",
            f"Restored backup {backup_id} to {profile.local}.",
            backup_path=str(safety) if safety else None,
        )
