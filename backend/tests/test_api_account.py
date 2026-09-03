"""The local app's account endpoints - thin proxies to the hosted server, exercised
through the same TestClient the rest of the API tests use, but talking to a real
account server (the live_server fixture) rather than mocking account_client."""

from __future__ import annotations

from fastapi.testclient import TestClient

from savesync.api import create_app
from savesync.config import REMOTE_CLOUD
from savesync.engine import Engine


def make_client(config):
    return TestClient(create_app(config))


def test_register_signs_in_and_persists_the_token(world, live_server):
    with make_client(world.b.config) as client:
        response = client.post(
            "/api/account/register",
            json={"server_url": live_server.url, "username": "alice", "password": "hunter22222"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["signed_in"] is True
        assert body["username"] == "alice"

        status = client.get("/api/account").json()
        assert status["signed_in"] is True
        assert status["server_url"] == live_server.url

    settings = world.b.config.settings()
    assert settings.account_token  # actually persisted to disk, not just in-memory


def test_duplicate_registration_is_a_clean_error(world, live_server):
    with make_client(world.b.config) as client:
        client.post(
            "/api/account/register",
            json={"server_url": live_server.url, "username": "alice", "password": "hunter22222"},
        )
        response = client.post(
            "/api/account/register",
            json={"server_url": live_server.url, "username": "alice", "password": "hunter22222"},
        )
        assert response.status_code == 502
        assert response.json()["kind"] == "account"


def test_login_with_wrong_password_leaves_prior_session_untouched(world, live_server):
    with make_client(world.b.config) as client:
        client.post(
            "/api/account/register",
            json={"server_url": live_server.url, "username": "alice", "password": "hunter22222"},
        )
        token_before = world.b.config.settings().account_token

        response = client.post(
            "/api/account/login",
            json={"server_url": live_server.url, "username": "alice", "password": "nope"},
        )
        assert response.status_code == 502
        assert response.json()["kind"] == "account"
        # a failed login attempt must not clobber whatever session was already valid
        assert world.b.config.settings().account_token == token_before
        assert client.get("/api/account").json()["signed_in"] is True


def test_login_with_wrong_password_never_signs_in_from_scratch(world, live_server):
    with make_client(world.b.config) as client:
        response = client.post(
            "/api/account/login",
            json={"server_url": live_server.url, "username": "nobody", "password": "nope"},
        )
        assert response.status_code == 502
        assert client.get("/api/account").json()["signed_in"] is False


def test_logout_clears_the_stored_token(world, live_server):
    with make_client(world.b.config) as client:
        client.post(
            "/api/account/register",
            json={"server_url": live_server.url, "username": "alice", "password": "hunter22222"},
        )
        client.post("/api/account/logout")
        assert client.get("/api/account").json()["signed_in"] is False
        assert world.b.config.settings().account_token == ""


def test_discover_and_adopt_a_cloud_profile_end_to_end(world, live_server, tmp_path):
    # Machine A pushes a cloud-backed profile directly through the engine, exactly
    # like test_cloud.py does - the point here is that the *API* layer's discovery
    # and adopt routes see the same thing.
    import savesync.account_client as account_client
    from savesync.config import Config, Settings

    account_client.register(live_server.url, "alice", "hunter22222")
    token = account_client.login(live_server.url, "alice", "hunter22222")["token"]

    a_config = Config(tmp_path / "home-A")
    a_config.save_settings(
        Settings(machine="DESKTOP-A", server_url=live_server.url, account_token=token)
    )
    a_save = tmp_path / "A-saves"
    a_save.mkdir()
    a_profile = a_config.add_profile("Hollow Knight", str(a_save), "", remote_kind=REMOTE_CLOUD)
    (a_save / "user.dat").write_text("silksong when", encoding="utf-8")
    Engine(a_config).push(a_profile.id, "start")

    # Machine B (the api.py `client`) signs into the same account and discovers it.
    with make_client(world.b.config) as client:
        client.post(
            "/api/account/register",
            json={"server_url": live_server.url, "username": "bob", "password": "hunter22222"},
        )
        # bob has no data yet - switch to alice's account instead, same server.
        client.post(
            "/api/account/login",
            json={"server_url": live_server.url, "username": "alice", "password": "hunter22222"},
        )

        found = client.get("/api/account/discover").json()
        assert len(found) == 1
        entry = found[0]
        assert entry["name"] == "Hollow Knight"
        assert entry["already_added"] is False

        b_save = tmp_path / "B-saves"
        b_save.mkdir()
        response = client.post(
            "/api/profiles/adopt",
            json={
                "id": entry["id"],
                "name": entry["name"],
                "local_path": str(b_save),
                "remote_kind": "cloud",
            },
        )
        assert response.status_code == 201
        adopted_id = response.json()["id"]
        assert adopted_id == a_profile.id

        status = client.get(f"/api/profiles/{adopted_id}/status").json()
        assert status["state"] == "unlinked"
        assert status["remote_kind"] == "cloud"

        client.post(f"/api/profiles/{adopted_id}/resolve", json={"choice": "use_remote"})
        assert (b_save / "user.dat").read_text(encoding="utf-8") == "silksong when"
