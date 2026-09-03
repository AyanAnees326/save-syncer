"""The correctness cases from the plan.

Everything here runs two simulated desktops against one relay folder. The point of
most of these tests is not that a file copied, but that the engine refused to do the
wrong thing.
"""

from __future__ import annotations

import json
import time

import pytest

from savesync import engine as eng
from savesync.config import POLICY_LATEST_WINS
from savesync.engine import ConflictError, EngineError
from savesync.lock import LockError
from savesync.store import RemoteError


# -- the happy path ---------------------------------------------------------


def test_push_then_pull_gives_identical_folders(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "chapter one")
    a.write("meta/index.dat", "index")

    assert a.state() == eng.NO_REMOTE
    result = a.push("first save")
    assert result.rev == 1

    assert b.state() == eng.UNLINKED  # B has an empty folder, relay has a save
    b.resolve("use_remote")

    assert b.tree() == a.tree()
    assert b.state() == eng.IN_SYNC
    assert a.state() == eng.IN_SYNC


def test_one_sided_edit_is_never_a_conflict(world):
    """The case that naive two-way comparison gets wrong."""
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")

    a.write("slot1.sav", "v2")
    a.push()

    # B changed nothing, so this must be a clean pull, not a conflict.
    assert b.state() == eng.REMOTE_AHEAD
    b.pull()
    assert b.read("slot1.sav") == "v2"
    assert b.state() == eng.IN_SYNC

    # And the reverse direction, with the roles swapped.
    b.write("slot1.sav", "v3")
    b.push()
    assert a.state() == eng.REMOTE_AHEAD
    a.pull()
    assert a.read("slot1.sav") == "v3"


def test_local_edit_reads_as_local_ahead(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")

    b.write("slot1.sav", "b played")
    assert b.state() == eng.LOCAL_AHEAD
    assert a.state() == eng.IN_SYNC  # A does not see B until B pushes

    b.push()
    assert a.state() == eng.REMOTE_AHEAD


def test_sync_picks_the_safe_action(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    assert a.sync().action == "push"
    b.resolve("use_remote")

    a.write("slot1.sav", "v2")
    assert a.sync().action == "push"
    assert b.sync().action == "pull"
    assert b.sync().action == "none"


# -- conflicts --------------------------------------------------------------


def test_both_sides_changed_is_a_conflict(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")

    a.write("slot1.sav", "a played")
    a.push()
    b.write("slot1.sav", "b played")

    assert b.state() == eng.CONFLICT
    with pytest.raises(ConflictError):
        b.sync()
    # A plain push must also refuse: the relay moved out from under B.
    with pytest.raises(ConflictError):
        b.push()


def test_conflict_use_local_keeps_this_desktop(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")
    a.write("slot1.sav", "a played")
    a.push()
    b.write("slot1.sav", "b played")

    b.resolve("use_local")
    assert b.read("slot1.sav") == "b played"
    assert b.state() == eng.IN_SYNC

    a.pull()
    assert a.read("slot1.sav") == "b played"
    # A's version is not gone - it is still a revision in the timeline.
    assert any(r["rev"] == 2 for r in a.revisions())


def test_conflict_use_remote_takes_the_other_desktop(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")
    a.write("slot1.sav", "a played")
    a.push()
    b.write("slot1.sav", "b played")

    b.resolve("use_remote")
    assert b.read("slot1.sav") == "a played"
    assert b.state() == eng.IN_SYNC
    # B's overwritten work is recoverable from the pre-pull backup.
    assert b.backups(), "a backup must exist after an overwrite"


def test_conflict_keep_both_preserves_the_other_copy_on_disk(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")
    a.write("slot1.sav", "a played")
    a.push()
    b.write("slot1.sav", "b played")

    result = b.resolve("keep_both")
    assert b.read("slot1.sav") == "b played"
    copy_path = result.extra["remote_copy_path"]
    assert (b.backup_root() / "..").exists()
    from pathlib import Path

    assert (Path(copy_path) / "slot1.sav").read_text(encoding="utf-8") == "a played"


def test_latest_wins_policy_resolves_without_asking(world, tmp_path):
    a, b = world.a, world.b
    a.config.update_profile(a.profile_id, policy=POLICY_LATEST_WINS)
    b.config.update_profile(b.profile_id, policy=POLICY_LATEST_WINS)

    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")

    now = time.time()
    a.write("slot1.sav", "a played", mtime=now - 600)
    a.push()
    b.write("slot1.sav", "b played", mtime=now)

    assert b.state() == eng.CONFLICT
    result = b.sync()
    assert "Latest-wins" in result.message
    assert b.read("slot1.sav") == "b played"


# -- mtime must never be the decider ----------------------------------------


def test_backwards_clock_does_not_flip_the_decision(world):
    """The newer save carries an older timestamp; revisions must still win."""
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")

    # A's genuinely newer save is stamped ten years in the past.
    a.write("slot1.sav", "the real progress", mtime=time.time() - 10 * 365 * 86400)
    a.push()

    assert b.state() == eng.REMOTE_AHEAD
    b.pull()
    assert b.read("slot1.sav") == "the real progress"


# -- deletions --------------------------------------------------------------


def test_deleted_file_propagates_instead_of_resurrecting(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.write("slot2.sav", "second slot")
    a.push()
    b.resolve("use_remote")
    assert "slot2.sav" in b.tree()

    a.remove("slot2.sav")
    a.push()
    b.pull()

    assert "slot2.sav" not in b.tree()
    assert b.tree() == a.tree()


def test_empty_directories_do_not_survive_a_pull(world):
    a, b = world.a, world.b
    a.write("deep/nested/slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")
    a.remove("deep/nested/slot1.sav")
    a.write("slot1.sav", "moved")
    a.push()
    b.pull()

    assert b.tree() == {"slot1.sav": "moved"}
    assert not (b.save / "deep").exists()


# -- history and manual selection -------------------------------------------


def test_restore_an_older_revision_then_publish_it(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push("start")
    b.resolve("use_remote")
    a.write("slot1.sav", "v2")
    a.push("boss fight")
    a.write("slot1.sav", "v3")
    a.push("ruined it")
    b.pull()

    revs = a.revisions()
    assert [r["rev"] for r in revs] == [3, 2, 1]
    assert revs[0]["is_head"] and revs[0]["from_this_machine"]

    a.restore(1)
    assert a.read("slot1.sav") == "v1"
    # The rollback differs from head, so it is this desktop's pending change.
    assert a.state() == eng.LOCAL_AHEAD

    result = a.push("rolled back")
    assert result.rev == 4
    b.pull()
    assert b.read("slot1.sav") == "v1"


def test_restore_head_leaves_the_profile_in_sync(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push()
    a.write("slot1.sav", "v2")
    a.push()

    a.restore(2)
    assert a.state() == eng.IN_SYNC


def test_revision_detail_reports_the_diff_against_disk(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push()
    a.write("slot1.sav", "v2")
    a.write("new.sav", "extra")
    a.push()

    detail = a.engine.revision_detail(a.profile_id, 1)
    assert detail["diff_vs_disk"]["added"] == ["new.sav"]
    assert detail["diff_vs_disk"]["changed"] == ["slot1.sav"]
    assert detail["matches_disk"] is False


# -- refusing to do damage --------------------------------------------------


def test_corrupt_relay_blob_aborts_the_pull_and_leaves_local_alone(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()

    b.write("slot1.sav", "b untouched original")
    before = b.tree()

    # Simulate a cloud relay that has not finished downloading a blob.
    blob = next(p for p in (world.relay / a.profile_id / "blobs").rglob("*") if p.is_file())
    blob.write_text("", encoding="utf-8")

    with pytest.raises(RemoteError):
        b.resolve("use_remote")
    assert b.tree() == before


def test_missing_relay_blob_aborts_the_pull(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.write("slot1.sav", "b untouched original")
    before = b.tree()

    next(p for p in (world.relay / a.profile_id / "blobs").rglob("*") if p.is_file()).unlink()

    with pytest.raises(RemoteError):
        b.resolve("use_remote")
    assert b.tree() == before


def test_push_refuses_while_another_machine_holds_the_lock(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push()

    lock_file = world.relay / a.profile_id / "lock.json"
    lock_file.write_text(
        json.dumps(
            {"machine": "DESKTOP-B", "acquired_at": time.time(), "expires_at": time.time() + 300}
        ),
        encoding="utf-8",
    )
    a.write("slot1.sav", "v2")
    with pytest.raises(LockError):
        a.push()


def test_expired_lock_is_stolen(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push()

    lock_file = world.relay / a.profile_id / "lock.json"
    lock_file.write_text(
        json.dumps(
            {"machine": "DESKTOP-B", "acquired_at": time.time() - 600, "expires_at": time.time() - 1}
        ),
        encoding="utf-8",
    )
    a.write("slot1.sav", "v2")
    assert a.push().rev == 2
    assert not lock_file.exists()


def test_pushing_an_empty_folder_is_refused(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")

    b.remove("slot1.sav")
    with pytest.raises(EngineError, match="empty"):
        b.push()


def test_pull_with_no_revisions_is_refused(world):
    with pytest.raises(EngineError):
        world.b.pull()


# -- backups ----------------------------------------------------------------


def test_every_overwrite_leaves_a_restorable_backup(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "from A")
    a.push()
    b.write("slot1.sav", "B had this")
    b.resolve("use_remote")
    assert b.read("slot1.sav") == "from A"

    backups = b.backups()
    assert len(backups) == 1
    b.engine.restore_backup(b.profile_id, backups[0]["id"])
    assert b.read("slot1.sav") == "B had this"


def test_backups_are_pruned_to_the_retention_limit(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v0")
    a.push()
    b.resolve("use_remote")
    for i in range(8):
        a.write("slot1.sav", f"v{i}")
        a.push()
        b.pull()
    assert len(b.backups()) <= b.config.settings().backup_retention


# -- linking and scanning ---------------------------------------------------


def test_identical_sides_link_silently(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "same bytes")
    a.push()
    b.write("slot1.sav", "same bytes")

    assert b.state() == eng.IN_SYNC  # no question worth asking the user
    assert b.engine.state.get(b.profile_id).base_rev == 1


def test_excluded_junk_never_reaches_the_relay(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.write("desktop.ini", "windows junk")
    a.write("cache.tmp", "scratch")
    a.push()
    b.resolve("use_remote")

    assert b.tree() == {"slot1.sav": "v1"}
    assert a.state() == eng.IN_SYNC  # the junk does not read as a pending change


def test_content_identity_ignores_timestamps(world):
    a = world.a
    a.write("slot1.sav", "v1", mtime=1000)
    a.push()
    a.write("slot1.sav", "v1", mtime=999_999_999)
    assert a.state() == eng.IN_SYNC
