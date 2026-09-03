"""Native folder picker and cloud-storage detection.

This service only ever binds to 127.0.0.1, so "show a dialog" and "the user sees it"
are the same machine - there is no remote-desktop case to worry about. The picker runs
server-side rather than through a pywebview JS bridge so it behaves identically
whether the app is opened in the desktop window, a plain browser tab, or the dev
server: one implementation, three shells.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# tkinter's own dialog is not reentrant across threads on Windows; FastAPI dispatches
# each sync route to a fresh worker thread, so without this a double-click could open
# two native dialogs at once and wedge one of them.
_dialog_lock = threading.Lock()


class DialogError(Exception):
    pass


def pick_folder(initial: str | None = None, title: str = "Choose a folder") -> str | None:
    """Show a native Windows folder picker. Returns the chosen path, or None if cancelled."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise DialogError(
            "This Python install does not include tkinter, so there is no folder picker "
            "available. Type the path instead."
        ) from exc

    start = Path.home()
    if initial:
        candidate = Path(os.path.expandvars(initial)).expanduser()
        if candidate.is_dir():
            start = candidate
        elif candidate.parent.is_dir():
            start = candidate.parent

    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)  # otherwise it can open behind the app window
        try:
            selected = filedialog.askdirectory(initialdir=str(start), title=title, parent=root)
        finally:
            root.destroy()
    return selected or None


def _dropbox_roots() -> list[dict[str, str]]:
    """Dropbox writes its real sync folder(s) to info.json - more reliable than guessing."""
    info_file = Path(os.environ.get("LOCALAPPDATA", "")) / "Dropbox" / "info.json"
    roots: list[dict[str, str]] = []
    try:
        data = json.loads(info_file.read_text(encoding="utf-8"))
        for kind, entry in data.items():
            path = entry.get("path")
            if path and Path(path).is_dir():
                label = "Dropbox" if kind == "personal" else f"Dropbox ({kind})"
                roots.append({"label": label, "path": path})
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    if not roots:
        guess = Path.home() / "Dropbox"
        if guess.is_dir():
            roots.append({"label": "Dropbox", "path": str(guess)})
    return roots


def _onedrive_roots() -> list[dict[str, str]]:
    """OneDrive exposes its sync folder(s) through environment variables it sets itself."""
    roots: list[dict[str, str]] = []
    seen: set[str] = set()
    for var, label in (
        ("OneDriveConsumer", "OneDrive (Personal)"),
        ("OneDriveCommercial", "OneDrive (Work/School)"),
        ("OneDrive", "OneDrive"),
    ):
        value = os.environ.get(var)
        if value and value not in seen and Path(value).is_dir():
            roots.append({"label": label, "path": value})
            seen.add(value)
    return roots


def _google_drive_roots() -> list[dict[str, str]]:
    for guess in (Path.home() / "Google Drive", Path.home() / "GoogleDrive", Path("G:/My Drive")):
        if guess.is_dir():
            return [{"label": "Google Drive", "path": str(guess)}]
    return []


def _icloud_roots() -> list[dict[str, str]]:
    guess = Path.home() / "iCloudDrive"
    if guess.is_dir():
        return [{"label": "iCloud Drive", "path": str(guess)}]
    return []


def detect_cloud_roots() -> list[dict[str, str]]:
    """Cloud-sync folders already set up on this machine, so relay setup is a click."""
    roots = [*_dropbox_roots(), *_onedrive_roots(), *_google_drive_roots(), *_icloud_roots()]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        if root["path"] not in seen:
            out.append(root)
            seen.add(root["path"])
    return out
