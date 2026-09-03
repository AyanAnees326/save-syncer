"""Save syncing through the hosted account server, exercised over real HTTP.

Mirrors the folder-based cases in test_engine.py: the point is not that a byte moved,
it's that the same three-way conflict logic, the same backups, the same "restore an
older revision" all work identically when the Remote is HttpRemote instead of
LocalDirRemote - Engine never knows which one it has.
"""

from __future__ import annotations

import pytest

from savesync import account_client
from savesync.config import REMOTE_CLOUD, Config, Settings
from savesync.engine import ConflictError, Engine, EngineError
from savesync.remote_http import HttpRemote


def _make_cloud_machine(tmp_path, name: str, server_url: str, token: str, profile_id: str | None = None):
    """A desktop signed into the account server, with one cloud-backed profile."""
    home = tmp_path / f"home-{name}"
    save = tmp_path / name / "Saves"
    save.mkdir(parents=True)
    config = Config(home)
    config.save_settings(
        Settings(machine=name, backup_retention=5, server_url=server_url, account_token=token)
    )
    engine = Engine(config)
    if profile_id:
        profile = config.add_profile("Elden Ring", str(save), "", adopt_id=profile_id, remote_kind=REMOTE_CLOUD)
    else:
        profile = config.add_profile("Elden Ring", str(save), "", remote_kind=REMOTE_CLOUD)
    return config, engine, save, profile.id


# -- HttpRemote against the real store protocol -------------------------------


def test_http_remote_round_trip(tmp_path, live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]

    remote = HttpRemote(live_server.url, token, "elden-ring")
    assert remote.read_head() is None
    assert remote.exists() is False

    src = tmp_path / "slot1.sav"
    src.write_text("chapter one", encoding="utf-8")
    from savesync.hashing import hash_file

    file_hash = hash_file(src)
    assert remote.has_blob(file_hash) is False
    remote.write_blob(file_hash, src)
    assert remote.has_blob(file_hash) is True

    from savesync.manifest import FileEntry, Manifest

    manifest = Manifest(
        files=(FileEntry(path="slot1.sav", hash=file_hash, size=src.stat().st_size, mtime=0),),
        rev=1,
        profile="elden-ring",
        profile_name="Elden Ring",
        machine="DESKTOP-A",
        note="first save",
    )
    remote.write_manifest(manifest)
    remote.write_head(1)

    assert remote.read_head() == 1
    assert remote.list_revisions() == [1]
    round_tripped = remote.read_manifest(1)
    assert round_tripped.note == "first save"
    assert round_tripped.files[0].hash == file_hash

    dst = tmp_path / "downloaded.sav"
    remote.read_blob_to(file_hash, dst)
    assert dst.read_text(encoding="utf-8") == "chapter one"


def test_http_remote_rejects_a_tampered_blob_claim(tmp_path, live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]
    remote = HttpRemote(live_server.url, token, "elden-ring")

    src = tmp_path / "slot1.sav"
    src.write_text("real content", encoding="utf-8")
    from savesync.store import RemoteError

    with pytest.raises(RemoteError):
        remote.write_blob("blake2b:0000notreal", src)


def test_http_remote_lock_blocks_a_second_writer(live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]

    remote_a = HttpRemote(live_server.url, token, "elden-ring")
    remote_b = HttpRemote(live_server.url, token, "elden-ring")

    from savesync.store import RemoteError

    lock_a = remote_a.lock("DESKTOP-A")
    lock_a.acquire()
    try:
        with pytest.raises(RemoteError):
            remote_b.lock("DESKTOP-B").acquire()
    finally:
        lock_a.release()

    # released - another machine can now take it
    remote_b.lock("DESKTOP-B").acquire()


# -- accounts are isolated ------------------------------------------------------


def test_two_accounts_with_the_same_slug_do_not_see_each_other(live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token_a = account_client.login(live_server.url, "alice", "hunter22222")["token"]
    account_client.register(live_server.url, "bob", "hunter22222")
    token_b = account_client.login(live_server.url, "bob", "hunter22222")["token"]

    remote_a = HttpRemote(live_server.url, token_a, "elden-ring")

    from savesync.hashing import hash_bytes
    from savesync.manifest import FileEntry, Manifest

    file_hash = hash_bytes(b"alice's save")
    manifest = Manifest(
        files=(FileEntry(path="a.sav", hash=file_hash, size=12, mtime=0),),
        rev=1,
        profile="elden-ring",
    )
    remote_a.write_manifest(manifest)
    remote_a.write_head(1)

    remote_b = HttpRemote(live_server.url, token_b, "elden-ring")
    assert remote_b.read_head() is None
    assert remote_b.list_revisions() == []

    known_a = account_client.list_profiles(live_server.url, token_a)
    known_b = account_client.list_profiles(live_server.url, token_b)
    assert len(known_a) == 1 and known_a[0]["id"] == "elden-ring"
    assert known_b == []


def test_wrong_password_is_rejected(live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    with pytest.raises(account_client.AccountError):
        account_client.login(live_server.url, "alice", "wrong-password")


def test_duplicate_username_is_rejected(live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    with pytest.raises(account_client.AccountError):
        account_client.register(live_server.url, "alice", "another-password")


# -- through the engine, like two real desktops -------------------------------


def test_push_and_pull_through_the_engine(tmp_path, live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]

    _, engine_a, save_a, profile_id = _make_cloud_machine(tmp_path, "DESKTOP-A", live_server.url, token)
    (save_a / "slot1.sav").write_text("chapter one", encoding="utf-8")
    result = engine_a.push(profile_id, "first save")
    assert result.rev == 1

    # A laptop, previously unconfigured, signs into the same account and discovers it.
    laptop_config = Config(tmp_path / "home-laptop")
    laptop_config.save_settings(
        Settings(machine="LAPTOP", backup_retention=5, server_url=live_server.url, account_token=token)
    )
    found = account_client.list_profiles(
        live_server.url, token, known_ids={p.id for p in laptop_config.list_profiles()}
    )
    assert len(found) == 1
    entry = found[0]
    assert entry["name"] == "Elden Ring"
    assert entry["already_added"] is False

    laptop_save = tmp_path / "laptop-saves"
    laptop_save.mkdir()
    adopted = laptop_config.add_profile(
        entry["name"], str(laptop_save), "", adopt_id=entry["id"], remote_kind=REMOTE_CLOUD
    )
    assert adopted.id == profile_id

    laptop_engine = Engine(laptop_config)
    report = laptop_engine.status(adopted.id)
    assert report.state == "unlinked"
    laptop_engine.resolve(adopted.id, "use_remote")
    assert (laptop_save / "slot1.sav").read_text(encoding="utf-8") == "chapter one"
    assert laptop_engine.status(adopted.id).state == "in_sync"


def test_conflict_detection_works_the_same_way_over_cloud(tmp_path, live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]

    _, engine_a, save_a, pid = _make_cloud_machine(tmp_path, "DESKTOP-A", live_server.url, token)
    (save_a / "slot1.sav").write_text("v1", encoding="utf-8")
    engine_a.push(pid, "start")

    _, engine_b, save_b, _ = _make_cloud_machine(tmp_path, "DESKTOP-B", live_server.url, token, profile_id=pid)
    engine_b.resolve(pid, "use_remote")

    (save_a / "slot1.sav").write_text("a played", encoding="utf-8")
    engine_a.push(pid, "a played")
    (save_b / "slot1.sav").write_text("b played", encoding="utf-8")

    assert engine_b.status(pid).state == "conflict"
    with pytest.raises(ConflictError):
        engine_b.sync(pid)

    engine_b.resolve(pid, "use_local")
    assert (save_b / "slot1.sav").read_text(encoding="utf-8") == "b played"

    engine_a.pull(pid)
    assert (save_a / "slot1.sav").read_text(encoding="utf-8") == "b played"


def test_restore_an_older_revision_over_cloud(tmp_path, live_server):
    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]
    _, engine_a, save_a, pid = _make_cloud_machine(tmp_path, "DESKTOP-A", live_server.url, token)

    (save_a / "slot1.sav").write_text("v1", encoding="utf-8")
    engine_a.push(pid, "start")
    (save_a / "slot1.sav").write_text("v2", encoding="utf-8")
    engine_a.push(pid, "boss fight")

    revs = engine_a.revisions(pid)
    assert [r["rev"] for r in revs] == [2, 1]

    engine_a.restore(pid, 1)
    assert (save_a / "slot1.sav").read_text(encoding="utf-8") == "v1"
    assert engine_a.status(pid).state == "local_ahead"
    assert engine_a.backups(pid), "restoring over cloud must still leave a local backup"


def test_pushing_without_being_signed_in_fails_clearly(tmp_path):
    config = Config(tmp_path / "home")
    config.save_settings(Settings(machine="DESKTOP-A"))  # no server_url/token
    save = tmp_path / "Saves"
    save.mkdir()
    profile = config.add_profile("Elden Ring", str(save), "", remote_kind=REMOTE_CLOUD)
    (save / "slot1.sav").write_text("v1", encoding="utf-8")

    engine = Engine(config)
    with pytest.raises(EngineError, match="[Ss]ign in"):
        engine.push(profile.id)
