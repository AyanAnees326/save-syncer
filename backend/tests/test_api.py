"""API tests - that the routes expose the engine faithfully and fail usefully."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from savesync import dialogs
from savesync.api import create_app


@pytest.fixture
def client(world):
    """An API bound to machine B, with machine A available to change the relay."""
    with TestClient(create_app(world.b.config)) as client:
        client.world = world
        yield client


def test_health_reports_the_machine(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["machine"] == "DESKTOP-B"


def test_profile_list_carries_status(client):
    client.world.a.write("slot1.sav", "v1")
    client.world.a.push()

    body = client.get("/api/profiles").json()
    assert len(body) == 1
    assert body[0]["profile"]["name"] == "Test Game"
    assert body[0]["status"]["state"] == "unlinked"
    assert set(body[0]["status"]["actions"]) == {"use_local", "use_remote"}


def test_full_round_trip_through_the_api(client):
    a, b = client.world.a, client.world.b
    a.write("slot1.sav", "from A")
    a.push("chapter one")
    pid = b.profile_id

    assert client.post(f"/api/profiles/{pid}/resolve", json={"choice": "use_remote"}).status_code == 200
    assert b.read("slot1.sav") == "from A"

    b.write("slot1.sav", "from B")
    assert client.get(f"/api/profiles/{pid}/status").json()["state"] == "local_ahead"

    pushed = client.post(f"/api/profiles/{pid}/push", json={"note": "chapter two"}).json()
    assert pushed["rev"] == 2

    revisions = client.get(f"/api/profiles/{pid}/revisions").json()
    assert [r["rev"] for r in revisions] == [2, 1]
    assert revisions[0]["note"] == "chapter two"
    assert revisions[0]["from_this_machine"] is True
    assert revisions[1]["from_this_machine"] is False


def test_restore_an_older_revision_over_http(client):
    a, b = client.world.a, client.world.b
    a.write("slot1.sav", "v1")
    a.push()
    a.write("slot1.sav", "v2")
    a.push()
    pid = b.profile_id
    client.post(f"/api/profiles/{pid}/resolve", json={"choice": "use_remote"})

    detail = client.get(f"/api/profiles/{pid}/revisions/1").json()
    assert detail["diff_vs_disk"]["changed"] == ["slot1.sav"]

    result = client.post(f"/api/profiles/{pid}/restore", json={"rev": 1}).json()
    assert result["rev"] == 1
    assert b.read("slot1.sav") == "v1"
    assert client.get(f"/api/profiles/{pid}/status").json()["state"] == "local_ahead"


def test_conflict_is_a_409_with_a_kind(client):
    a, b = client.world.a, client.world.b
    a.write("slot1.sav", "v1")
    a.push()
    pid = b.profile_id
    client.post(f"/api/profiles/{pid}/resolve", json={"choice": "use_remote"})

    a.write("slot1.sav", "a played")
    a.push()
    b.write("slot1.sav", "b played")

    assert client.get(f"/api/profiles/{pid}/status").json()["state"] == "conflict"
    response = client.post(f"/api/profiles/{pid}/sync")
    assert response.status_code == 409
    assert response.json()["kind"] == "conflict"


def test_unknown_profile_is_a_404(client):
    response = client.get("/api/profiles/nope/status")
    assert response.status_code == 404
    assert response.json()["kind"] == "config"


def test_backup_listing_and_restore(client):
    a, b = client.world.a, client.world.b
    a.write("slot1.sav", "from A")
    a.push()
    b.write("slot1.sav", "B had this")
    pid = b.profile_id
    client.post(f"/api/profiles/{pid}/resolve", json={"choice": "use_remote"})

    backups = client.get(f"/api/profiles/{pid}/backups").json()
    assert len(backups) == 1
    client.post(f"/api/profiles/{pid}/backups/{backups[0]['id']}/restore")
    assert b.read("slot1.sav") == "B had this"


def test_path_check_helps_the_add_form(client):
    body = client.get("/api/fs/check", params={"path": str(client.world.a.save)}).json()
    assert body["exists"] and body["is_dir"]
    missing = client.get("/api/fs/check", params={"path": str(client.world.a.save / "nope")}).json()
    assert missing["exists"] is False


def test_cloud_roots_endpoint_returns_a_list(client):
    body = client.get("/api/fs/cloud-roots").json()
    assert isinstance(body, list)


def test_pick_folder_returns_the_chosen_path(client, monkeypatch):
    monkeypatch.setattr(dialogs, "pick_folder", lambda initial, title: r"C:\Users\you\Dropbox\SaveSyncer")
    response = client.post("/api/fs/pick-folder", json={"title": "Pick one"})
    assert response.json() == {"path": r"C:\Users\you\Dropbox\SaveSyncer"}


def test_pick_folder_reports_cancellation_as_no_path(client, monkeypatch):
    monkeypatch.setattr(dialogs, "pick_folder", lambda initial, title: None)
    response = client.post("/api/fs/pick-folder", json={})
    assert response.json() == {"path": None}


def test_discover_endpoint_lists_relay_profiles(client):
    a, b = client.world.a, client.world.b
    a.write("slot1.sav", "v1")
    a.push()

    body = client.post("/api/relay/discover", json={"relay_path": str(client.world.relay)}).json()
    assert len(body) == 1
    entry = body[0]
    assert entry["id"] == b.profile_id
    assert entry["name"] == "Test Game"
    assert entry["source_local_path"] == str(a.save)
    # b already has this profile configured locally, from the fixture setup
    assert entry["already_added"] is True


def test_adopt_endpoint_creates_a_profile_with_the_exact_given_id(client, tmp_path):
    """Full linkage to an existing relay history is covered in test_discovery.py -
    this checks the route wires the id through untouched rather than slugifying it,
    which is what would otherwise happen through the normal create-profile endpoint."""
    new_local = tmp_path / "adopted-local"
    new_local.mkdir()
    response = client.post(
        "/api/profiles/adopt",
        json={
            "id": "some-other-fresh-id",
            "name": "Adopted Game",
            "local_path": str(new_local),
            "relay_path": str(client.world.relay),
        },
    )
    assert response.status_code == 201
    profile = response.json()
    assert profile["id"] == "some-other-fresh-id"
    assert profile["name"] == "Adopted Game"


def test_adopt_endpoint_refuses_a_taken_id(client, tmp_path):
    response = client.post(
        "/api/profiles/adopt",
        json={
            "id": client.world.b.profile_id,
            "name": "Whatever",
            "local_path": str(tmp_path),
            "relay_path": str(client.world.relay),
        },
    )
    assert response.status_code == 404
    assert response.json()["kind"] == "config"


def test_settings_round_trip(client):
    client.patch("/api/settings", json={"machine": "LIVING-ROOM", "backup_retention": 3})
    body = client.get("/api/settings").json()
    assert body["machine"] == "LIVING-ROOM"
    assert body["backup_retention"] == 3


def test_token_is_enforced_when_set(world):
    with TestClient(create_app(world.b.config, token="secret")) as client:
        assert client.get("/api/profiles").status_code == 401
        ok = client.get("/api/profiles", headers={"X-Savesync-Token": "secret"})
        assert ok.status_code == 200


def test_events_stream_announces_actions(client):
    a, b = client.world.a, client.world.b
    a.write("slot1.sav", "v1")
    a.push()
    pid = b.profile_id
    with client.websocket_connect("/api/events") as ws:
        client.post(f"/api/profiles/{pid}/resolve", json={"choice": "use_remote"})
        event = ws.receive_json()
        assert event["type"] == "changed"
        assert event["profile_id"] == pid
        assert event["action"] == "resolve"
