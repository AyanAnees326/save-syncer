"""Discovering existing save histories in a relay folder, and adopting one.

This is what lets a second desktop browse what is already on the relay instead of
having to retype the first desktop's exact profile name to link to the same history.
"""

from __future__ import annotations

from savesync import discovery
from savesync.config import ConfigError


def test_discover_finds_a_pushed_profile_with_its_metadata(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push("first save")

    found = discovery.discover(world.relay)
    assert len(found) == 1
    entry = found[0]
    assert entry["id"] == a.profile_id
    assert entry["name"] == "Test Game"  # from the pushing machine's profile name
    assert entry["machine"] == "DESKTOP-A"
    assert entry["rev"] == 1
    assert entry["file_count"] == 1
    assert entry["already_added"] is False
    assert entry["source_local_path"] == str(a.save)


def test_discover_marks_profiles_this_machine_already_has(world):
    a, b = world.a, world.b
    a.write("slot1.sav", "v1")
    a.push()
    b.resolve("use_remote")  # now b also has this profile configured

    found = discovery.discover(world.relay, known_ids={b.profile_id})
    assert found[0]["already_added"] is True


def test_discover_ignores_folders_that_are_not_save_histories(world, tmp_path):
    junk = world.relay / "not-a-save-history"
    junk.mkdir()
    (junk / "random.txt").write_text("hello", encoding="utf-8")

    assert discovery.discover(world.relay) == []


def test_discover_skips_a_corrupt_store_without_crashing(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push()

    (world.relay / a.profile_id / "revs" / "000001.json").write_text("not json", encoding="utf-8")

    assert discovery.discover(world.relay) == []


def test_discover_on_a_missing_relay_root_returns_empty(tmp_path):
    assert discovery.discover(tmp_path / "does-not-exist") == []


def test_falls_back_to_a_humanized_id_when_manifest_has_no_name(world):
    a = world.a
    a.write("slot1.sav", "v1")
    a.push()
    # simulate a manifest pushed before profile_name existed
    manifest_file = world.relay / a.profile_id / "revs" / "000001.json"
    import json

    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["profile_name"] = ""
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    found = discovery.discover(world.relay)
    assert found[0]["name"] == a.profile_id.replace("-", " ").title()


def test_adopt_links_to_the_exact_existing_id(world, tmp_path):
    # A third, previously unconfigured desktop finds A's push and adopts it - unlike
    # world.b, it has never called add_profile for this name, so the id is free.
    from savesync.config import Config, Settings
    from savesync.engine import Engine

    a = world.a
    a.write("slot1.sav", "v1")
    a.push()

    c_config = Config(tmp_path / "home-C")
    c_config.save_settings(Settings(machine="DESKTOP-C", backup_retention=5))
    c_engine = Engine(c_config)

    found = discovery.discover(world.relay, {p.id for p in c_config.list_profiles()})
    assert len(found) == 1
    entry = found[0]
    assert entry["already_added"] is False

    new_local = tmp_path / "c-new-local"
    new_local.mkdir()  # the local save folder exists but is empty, like a fresh install
    adopted = c_config.add_profile(
        entry["name"], str(new_local), str(world.relay), adopt_id=entry["id"]
    )
    assert adopted.id == a.profile_id  # same id as the original -> same relay history

    report = c_engine.status(adopted.id)
    assert report.state == "unlinked"
    assert report.remote is not None and report.remote.file_count == 1


def test_adopt_refuses_a_locally_taken_id(world, tmp_path):
    b = world.b
    b.write("slot1.sav", "v1")
    b.push()  # b already has a profile using world.a.profile_id (same slug, shared world)

    try:
        b.config.add_profile("Different Name", str(tmp_path), str(world.relay), adopt_id=b.profile_id)
        assert False, "expected a ConfigError"
    except ConfigError as exc:
        assert "already set up" in str(exc)
