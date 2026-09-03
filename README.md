# Save Syncer

Keeps a save folder in sync across your desktops - and doubles as a database of every
version you've ever pushed, so you can pull up an old save on the same PC or a laptop
you haven't touched it from before. Two storage backends, chosen per profile:

- **A shared folder** (Dropbox / OneDrive / Drive / a network share) - no account, no
  server, works even when the two machines are never online at the same time.
- **Your account** on a hosted server (self-run - see [Account server](#account-server-optional))
  - sign in on any machine and every save you've pushed is right there to browse,
  restore, or adopt, the same as it would be from the folder.

Either way, you can put **any** past version onto the desktop you are sitting at.

- Python 3.12 engine + FastAPI service (`backend/`)
- React + Tailwind UI, shipped inside the Python package (`frontend/`)
- One desktop window via pywebview, or a plain browser tab
- Optional account server (`savesync serve`) - SQLite + a blob directory, no external
  database to stand up

## Why it is built this way

**The save folder is one atomic unit.** A folder assembled from the newest of each file
across two machines is a state that never existed on either, and may not load. Sync
moves whole snapshots.

**mtime never decides anything.** Clock skew, DST and games touching files without
changing them make "newest" unreliable. Decisions use a monotonic revision counter;
timestamps are shown to you for judgement, never branched on. (The opt-in
`latest_wins` policy is the single exception, and only after a conflict is already
confirmed.)

**Conflicts are detected, not guessed.** Each machine records the revision it last
agreed on. Comparing local / base / remote distinguishes "they changed and I did not"
(a safe pull) from "we both changed" (a real conflict). Comparing only the two current
versions cannot, which is how sync tools quietly eat a save.

**Nothing is overwritten without an undo.** Every write to the save folder is staged
and hash-verified first, then the current state is snapshotted into a local backup
folder, and only then are files swapped into place. A missing or half-downloaded blob
on the relay aborts with your save untouched.

## Install and run

```bash
cd backend && python -m pip install -e ".[desktop]"
```

```bash
cd frontend && npm install && npm run build
```

The build writes into `backend/savesync/static/`, which the service serves. Then:

```bash
savesync ui
```

That opens a native window on a random localhost port with a per-launch token, so
nothing else on the machine can drive the engine through it. `savesync ui --browser`
opens a normal browser tab instead.

## Building a standalone .exe

To hand someone a single file that runs without a Python install:

```bash
cd frontend && npm install && npm run build
cd ../backend && python -m pip install -e ".[desktop,build]"
python -m PyInstaller --name SaveSyncer --onefile --windowed \
  --add-data "savesync/static;savesync/static" \
  --collect-submodules savesync \
  run_desktop.py
```

The frontend build has to happen first - PyInstaller bundles whatever is already in
`savesync/static/` at build time, it does not run `npm` itself. The result is
`backend/dist/SaveSyncer.exe`, roughly 22 MB, self-contained (no Python or Node needed
on the machine you send it to - it does need the WebView2 Runtime, which ships with
Windows 10 21H2+/11 and with Edge, so almost every Windows machine already has it).

Each launch still uses that machine's own `%APPDATA%\savesync` for profiles and
settings, same as running from source - the exe is just a different way to start the
same app, not a separate install. Add `--icon path\to\icon.ico` for a custom taskbar
icon; without it Windows uses a generic one.

This packages the desktop client, not the account server - that keeps running from
source (`savesync serve`) on whatever machine hosts it. The client exe can still sign
into and use cloud profiles; there's just no reason to package the server as a
one-off desktop app.

## First-time setup

On **both** desktops, add the same profile name pointing at that machine's save folder
and the shared relay folder:

```bash
savesync add "Elden Ring" --local "%APPDATA%\EldenRing\76561..." --relay "%USERPROFILE%\Dropbox\savesync" --guard eldenring.exe
```

The first desktop pushes. The second sees "not linked yet" and picks a side once; from
then on it is push, pull, or a conflict you resolve.

Name each machine so revisions are attributable:

```bash
savesync settings --machine DESKTOP-A
```

## Account server (optional)

An alternative to the shared folder: a small self-hosted server with real user
accounts and a SQLite-backed save database, so signing in from any machine is enough -
no relay folder to set up. **Self-hosted means you run it** - `savesync serve` starts
the server, but getting a URL that both your desktop and laptop can reach (a home
server, a Raspberry Pi, a small VPS, or just your LAN if both machines are on the same
network) is on you. Nothing here signs up hosting on your behalf.

Run it once, wherever you're hosting it:

```bash
savesync serve --host 0.0.0.0 --port 8420
```

`--host 127.0.0.1` (the default) only accepts connections from the same machine -
use `0.0.0.0` to accept them from your network, and put it behind a reverse proxy
with TLS before exposing it past your LAN. Account data lives in
`%APPDATA%\savesync-server\` (`db.sqlite3` + a `blobs/` directory); override with
`SAVESYNC_SERVER_HOME`.

On each desktop, sign into it:

```bash
savesync account register http://your-server:8420 --username you
savesync add "Elden Ring" --local "%APPDATA%\EldenRing\76561..." --cloud
```

On a second machine, sign in and see what's already there instead of retyping names:

```bash
savesync account login http://your-server:8420 --username you
savesync account discover
savesync account adopt elden-ring --local "D:\Games\EldenRing\76561..."
```

From the desktop app, this is the "Your account" option on the Add-profile screen
(sign in from Settings first) - it shows the exact same "found saves" list the CLI's
`discover` prints, with a **Use this save** button instead of a second command.

Passwords are hashed with bcrypt; nothing else about the account (server URL,
session token, username) is treated as secret beyond living in the same unencrypted
JSON as the rest of this app's local settings.

## CLI

Same engine as the UI, useful for scripting and debugging.

| Command | What it does |
| --- | --- |
| `savesync status` | What state each profile is in and what is safe to do |
| `savesync sync` | Push, pull, or stop and ask - whichever is correct |
| `savesync push -m "beat margit"` | Publish this desktop as a new revision |
| `savesync pull` | Bring the relay head onto this desktop |
| `savesync log` | The revision timeline |
| `savesync restore 7` | Put revision 7 onto this desktop |
| `savesync resolve use_local\|use_remote\|keep_both` | Settle a conflict |
| `savesync backups` / `savesync undo <id>` | List and restore local safety copies |
| `savesync account register\|login\|logout\|whoami` | Manage the signed-in account |
| `savesync account discover` / `adopt <id>` | List and link to saves already in your account |
| `savesync serve` | Run the account server |

With one profile the id can be omitted; otherwise pass it.

## Conflict choices

All three are non-destructive - the losing copy is either already in relay history or
written to your backups first.

- **Keep this desktop** - publishes your copy over the relay head. Theirs stays in history.
- **Take the relay copy** - overwrites your folder, after backing it up.
- **Keep both** - publishes your copy *and* writes the relay version into your backups
  folder so you can open it.

## Where things live

```
<relay>/<profile-id>/
  HEAD.json          {"rev": 17}
  revs/000017.json   one manifest per revision
  blobs/ab/cdef...   file contents, content-addressed, append-only
  lock.json          TTL write lock so two machines cannot interleave pushes

%APPDATA%\savesync\
  profiles.json  settings.json   (settings.json holds the account token, if signed in)
  state/<profile>.json   the revision this desktop last agreed on
  backups/<profile>/     pre-overwrite snapshots (retention configurable)

%APPDATA%\savesync-server\        (only on whatever machine runs `savesync serve`)
  db.sqlite3    users, profiles, revision manifests, locks
  blobs/<user-id>/ab/cdef...   file contents, per account, content-addressed
  secret.key    signs session tokens - generated once, keep it out of backups you'd share
```

`SAVESYNC_HOME` overrides the app home and `SAVESYNC_SERVER_HOME` the server's - handy
for a portable install, and how the tests run several machines (and a real server) in
one process.

## Development

```bash
cd backend && python -m uvicorn savesync.api:app --port 8765 --reload
```

```bash
cd frontend && npm run dev
```

Vite serves on 5173 and proxies `/api` to 8765.

## Tests

```bash
cd backend && python -m pytest
```

Most of the folder-relay cases run two simulated desktops against one relay directory
and assert the engine *refused* to do the wrong thing: a one-sided edit is never
reported as a conflict, a backwards clock does not flip the decision, a corrupt relay
blob aborts the pull with the local folder untouched, an empty folder is never pushed
over a good save, and every overwrite leaves a restorable backup.

`test_cloud.py` and `test_api_account.py` run the same kind of scenarios against a
real account server (a live uvicorn instance in a background thread, reached over
actual HTTP, not an in-process test transport) - the point being that Engine behaves
identically whether the Remote underneath it is a folder or an account, plus the
account-isolation case that matters most for a multi-user server: two accounts using
the same profile slug must never see each other's data.
