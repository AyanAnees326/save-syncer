"""Command line front end.

Exists mainly so the engine can be driven and debugged without the UI running - every
command here is the same call the HTTP API makes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from .apply import ApplyError
from .config import POLICIES, POLICY_ASK, Config, ConfigError, Settings
from .engine import Engine, EngineError
from .guard import GuardError
from .lock import LockError
from .scanner import ScanError
from .store import RemoteError

app = typer.Typer(add_completion=False, help="Sync a save folder between two desktops.")

STATE_LABELS = {
    "in_sync": "in sync",
    "local_ahead": "this desktop is ahead",
    "remote_ahead": "the relay is ahead",
    "conflict": "CONFLICT",
    "unlinked": "not linked yet",
    "no_remote": "relay is empty",
    "local_missing": "local folder is missing",
}

USER_ERRORS = (EngineError, ConfigError, RemoteError, LockError, GuardError, ScanError, ApplyError)


def get_engine() -> Engine:
    return Engine(Config())


def fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def only_profile(engine: Engine, profile_id: Optional[str]) -> str:
    """Let the user omit the profile id when there is exactly one."""
    if profile_id:
        return profile_id
    profiles = engine.config.list_profiles()
    if len(profiles) == 1:
        return profiles[0].id
    if not profiles:
        fail("No profiles yet. Add one with: savesync add NAME --local PATH --relay PATH")
    fail("Several profiles exist; name one of: " + ", ".join(p.id for p in profiles))
    raise AssertionError  # unreachable, keeps type checkers happy


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


@app.command("add")
def add_profile(
    name: str = typer.Argument(..., help="Display name, e.g. the game."),
    local: Path = typer.Option(..., "--local", help="The save folder on this desktop."),
    relay: Optional[Path] = typer.Option(
        None, "--relay", help="The shared folder both desktops can see. Omit with --cloud."
    ),
    cloud: bool = typer.Option(
        False, "--cloud", help="Store this profile in your signed-in account instead of a folder."
    ),
    policy: str = typer.Option(POLICY_ASK, "--policy", help=f"One of {', '.join(POLICIES)}."),
    guard: list[str] = typer.Option([], "--guard", help="Process to refuse syncing under."),
) -> None:
    """Add a profile."""
    engine = get_engine()
    if cloud and not engine.config.settings().signed_in:
        fail("Not signed in. Run: savesync account login <server-url>")
    try:
        profile = engine.config.add_profile(
            name,
            str(local),
            str(relay) if relay else "",
            policy=policy,
            guard_processes=list(guard),
            remote_kind="cloud" if cloud else "folder",
        )
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.echo(f"Added profile {profile.id} ({profile.name})")
    typer.echo(f"  local: {profile.local}")
    if cloud:
        settings = engine.config.settings()
        typer.echo(f"  cloud: {settings.account_username} on {settings.server_url}")
    else:
        typer.echo(f"  relay: {profile.relay}")


@app.command("profiles")
def list_profiles() -> None:
    """List profiles."""
    engine = get_engine()
    profiles = engine.config.list_profiles()
    if not profiles:
        typer.echo("No profiles yet.")
        return
    for profile in profiles:
        typer.echo(f"{profile.id:20} {profile.name}")
        typer.echo(f"{'':20} local {profile.local}")
        typer.echo(f"{'':20} relay {profile.relay}  policy={profile.policy}")


@app.command("rm")
def remove_profile(profile_id: str) -> None:
    """Remove a profile (leaves the relay history and local saves alone)."""
    engine = get_engine()
    try:
        engine.config.delete_profile(profile_id)
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.echo(f"Removed {profile_id}")


@app.command("status")
def status(profile_id: Optional[str] = typer.Argument(None)) -> None:
    """Show what the engine thinks the situation is."""
    engine = get_engine()
    targets = (
        [only_profile(engine, profile_id)]
        if profile_id
        else [p.id for p in engine.config.list_profiles()]
    )
    if not targets:
        typer.echo("No profiles yet.")
        return
    for pid in targets:
        try:
            report = engine.status(pid)
        except USER_ERRORS as exc:
            typer.secho(f"{pid}: {exc}", fg=typer.colors.RED)
            continue
        colour = {
            "in_sync": typer.colors.GREEN,
            "conflict": typer.colors.RED,
            "local_ahead": typer.colors.YELLOW,
            "remote_ahead": typer.colors.BLUE,
        }.get(report.state, typer.colors.WHITE)
        typer.secho(f"{pid}: {STATE_LABELS.get(report.state, report.state)}", fg=colour, bold=True)
        typer.echo(f"  {report.message}")
        if report.local:
            typer.echo(
                f"  local  {report.local.file_count} files, {human_size(report.local.total_size)}"
            )
        if report.remote:
            typer.echo(
                f"  relay  rev {report.remote_rev} from {report.remote.machine}, "
                f"{report.remote.file_count} files, {human_size(report.remote.total_size)}"
            )
        if report.diff and not report.diff.is_empty:
            d = report.diff
            typer.echo(
                f"  diff   +{len(d.added)} ~{len(d.changed)} -{len(d.removed)} (local vs relay)"
            )
        if report.blocking_processes:
            typer.secho(f"  blocked by {', '.join(report.blocking_processes)}", fg=typer.colors.RED)
        if report.actions:
            typer.echo(f"  can    {', '.join(report.actions)}")


@app.command("sync")
def sync(
    profile_id: Optional[str] = typer.Argument(None),
    note: str = typer.Option("", "-m", "--note"),
) -> None:
    """Do whatever is safe: push, pull, or stop and ask."""
    engine = get_engine()
    try:
        result = engine.sync(only_profile(engine, profile_id), note)
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(result.message, fg=typer.colors.GREEN)


@app.command("push")
def push(
    profile_id: Optional[str] = typer.Argument(None),
    note: str = typer.Option("", "-m", "--note"),
    force: bool = typer.Option(False, "--force", help="Publish over the relay head."),
) -> None:
    """Publish this desktop's save as a new revision."""
    engine = get_engine()
    try:
        result = engine.push(only_profile(engine, profile_id), note, force=force)
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(result.message, fg=typer.colors.GREEN)


@app.command("pull")
def pull(profile_id: Optional[str] = typer.Argument(None)) -> None:
    """Bring the relay head onto this desktop."""
    engine = get_engine()
    try:
        result = engine.pull(only_profile(engine, profile_id))
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(result.message, fg=typer.colors.GREEN)
    if result.backup_path:
        typer.echo(f"  previous state backed up to {result.backup_path}")


@app.command("log")
def log(profile_id: Optional[str] = typer.Argument(None), limit: int = typer.Option(20)) -> None:
    """Show the revision timeline."""
    engine = get_engine()
    try:
        revisions = engine.revisions(only_profile(engine, profile_id))
    except USER_ERRORS as exc:
        fail(str(exc))
    if not revisions:
        typer.echo("No revisions on the relay yet.")
        return
    for rev in revisions[:limit]:
        marks = "".join(
            [
                "*" if rev["is_head"] else " ",
                "b" if rev["is_base"] else " ",
                "=" if rev["matches_disk"] else " ",
            ]
        )
        typer.echo(
            f"{marks} {rev['rev']:>4}  {rev['created_at']}  {rev['machine']:<16} "
            f"{rev['file_count']} files  {human_size(rev['total_size'])}  {rev['note']}"
        )
    typer.echo("\n  * relay head   b this desktop's base   = matches what is on disk now")


@app.command("restore")
def restore(
    rev: int = typer.Argument(..., help="Revision number from `savesync log`."),
    profile_id: Optional[str] = typer.Argument(None),
) -> None:
    """Put any past revision onto this desktop."""
    engine = get_engine()
    try:
        result = engine.restore(only_profile(engine, profile_id), rev)
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(result.message, fg=typer.colors.GREEN)
    if result.backup_path:
        typer.echo(f"  previous state backed up to {result.backup_path}")


@app.command("resolve")
def resolve(
    choice: str = typer.Argument(..., help="use_local, use_remote or keep_both"),
    profile_id: Optional[str] = typer.Argument(None),
) -> None:
    """Settle a conflict."""
    engine = get_engine()
    try:
        result = engine.resolve(only_profile(engine, profile_id), choice)
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(result.message, fg=typer.colors.GREEN)


@app.command("backups")
def backups(profile_id: Optional[str] = typer.Argument(None)) -> None:
    """List the local safety copies taken before each overwrite."""
    engine = get_engine()
    try:
        entries = engine.backups(only_profile(engine, profile_id))
    except USER_ERRORS as exc:
        fail(str(exc))
    if not entries:
        typer.echo("No backups yet.")
        return
    for entry in entries:
        typer.echo(
            f"{entry['id']:<32} {entry['file_count']} files  {human_size(int(entry['total_size']))}"
        )


@app.command("undo")
def undo(
    backup_id: str = typer.Argument(..., help="Id from `savesync backups`."),
    profile_id: Optional[str] = typer.Argument(None),
) -> None:
    """Put a backup back onto this desktop."""
    engine = get_engine()
    try:
        result = engine.restore_backup(only_profile(engine, profile_id), backup_id)
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(result.message, fg=typer.colors.GREEN)


@app.command("settings")
def settings_cmd(
    machine: Optional[str] = typer.Option(None, "--machine", help="Name shown in the timeline."),
    retention: Optional[int] = typer.Option(None, "--retention", help="How many backups to keep."),
) -> None:
    """Show or change app settings."""
    config = Config()
    current = config.settings()
    if machine is not None or retention is not None:
        current = Settings(
            machine=machine or current.machine,
            backup_retention=retention if retention is not None else current.backup_retention,
        )
        config.save_settings(current)
    typer.echo(f"home        {config.home}")
    typer.echo(f"machine     {current.machine}")
    typer.echo(f"retention   {current.backup_retention} backups")


@app.command("ui")
def ui(
    port: int = typer.Option(0, help="0 picks a free port."),
    browser: bool = typer.Option(False, "--browser", help="Open in the default browser instead."),
) -> None:
    """Launch the desktop app."""
    from .desktop import launch

    launch(port=port, use_browser=browser)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Use 0.0.0.0 to accept LAN/remote connections."),
    port: int = typer.Option(8420, help="Port to listen on."),
) -> None:
    """Run the account server: user accounts + a hosted save database, so a desktop
    can sync through your own server instead of a Dropbox/OneDrive folder.

    This is meant to be self-hosted - run it on a machine you control (a home server,
    a small VPS, ...) and point desktops at its address with `savesync account
    register`/`login`. Binding 0.0.0.0 exposes it to your whole network; put it behind
    a reverse proxy with TLS before exposing it to the internet."""
    import uvicorn

    from .server.app import create_server
    from .server.db import default_server_home

    typer.echo(f"Account data: {default_server_home()}")
    typer.echo(f"Listening on http://{host}:{port}")
    uvicorn.run(create_server(), host=host, port=port, log_level="info")


account_app = typer.Typer(add_completion=False, help="Manage the signed-in cloud account.")
app.add_typer(account_app, name="account")


@account_app.command("register")
def account_register_cmd(
    server_url: str = typer.Argument(..., help="e.g. http://homeserver:8420"),
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Create an account on a server and sign this desktop into it."""
    from . import account_client

    try:
        result = account_client.register(server_url, username, password)
    except account_client.AccountError as exc:
        fail(str(exc))
    config = get_engine().config
    settings = config.settings()
    settings.server_url = server_url
    settings.account_token = result["token"]
    settings.account_username = result["user"]["username"]
    config.save_settings(settings)
    typer.secho(f"Signed in as {settings.account_username} on {server_url}", fg=typer.colors.GREEN)


@account_app.command("login")
def account_login_cmd(
    server_url: str = typer.Argument(..., help="e.g. http://homeserver:8420"),
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
) -> None:
    """Sign this desktop into an existing account."""
    from . import account_client

    try:
        result = account_client.login(server_url, username, password)
    except account_client.AccountError as exc:
        fail(str(exc))
    config = get_engine().config
    settings = config.settings()
    settings.server_url = server_url
    settings.account_token = result["token"]
    settings.account_username = result["user"]["username"]
    config.save_settings(settings)
    typer.secho(f"Signed in as {settings.account_username} on {server_url}", fg=typer.colors.GREEN)


@account_app.command("logout")
def account_logout_cmd() -> None:
    """Forget the signed-in account on this desktop."""
    config = get_engine().config
    settings = config.settings()
    settings.account_token = ""
    settings.account_username = ""
    config.save_settings(settings)
    typer.echo("Signed out.")


@account_app.command("whoami")
def account_whoami_cmd() -> None:
    """Show which account (if any) this desktop is signed into."""
    settings = get_engine().config.settings()
    if not settings.signed_in:
        typer.echo("Not signed in.")
        return
    typer.echo(f"{settings.account_username} on {settings.server_url}")


@account_app.command("discover")
def account_discover_cmd() -> None:
    """List the saves already stored in the signed-in account."""
    from . import account_client

    engine = get_engine()
    settings = engine.config.settings()
    if not settings.signed_in:
        fail("Not signed in. Run: savesync account login <server-url>")
    known_ids = {p.id for p in engine.config.list_profiles()}
    try:
        found = account_client.list_profiles(settings.server_url, settings.account_token, known_ids)
    except account_client.AccountError as exc:
        fail(str(exc))
    if not found:
        typer.echo("Nothing in this account yet.")
        return
    for entry in found:
        tag = " (already set up here)" if entry["already_added"] else ""
        typer.echo(
            f"{entry['id']:<20} {entry['name']:<24} {entry['file_count']} files  "
            f"{human_size(int(entry['total_size']))}  from {entry['machine']}{tag}"
        )


@account_app.command("adopt")
def account_adopt_cmd(
    profile_id: str = typer.Argument(..., help="Id from `savesync account discover`."),
    local: Path = typer.Option(..., "--local", help="The save folder on this desktop."),
) -> None:
    """Link this desktop to a save already in the signed-in account."""
    engine = get_engine()
    settings = engine.config.settings()
    if not settings.signed_in:
        fail("Not signed in. Run: savesync account login <server-url>")
    from . import account_client

    try:
        found = account_client.list_profiles(settings.server_url, settings.account_token)
    except account_client.AccountError as exc:
        fail(str(exc))
    entry = next((e for e in found if e["id"] == profile_id), None)
    if entry is None:
        fail(f"no such saved profile in this account: {profile_id!r}")
    try:
        profile = engine.config.add_profile(
            entry["name"], str(local), "", adopt_id=entry["id"], remote_kind="cloud"
        )
    except USER_ERRORS as exc:
        fail(str(exc))
    typer.secho(f"Linked {profile.id} ({profile.name}) to this desktop.", fg=typer.colors.GREEN)
    typer.echo("Run `savesync status` to see whether to push or pull.")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
