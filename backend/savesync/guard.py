"""Refuse to sync while the game is running.

A running game holds save files open and rewrites them on its own schedule, so both
pulling (overwrite under its feet) and pushing (capture a half-written save) are
unsafe. Implemented with the OS process list rather than a dependency, since this is
the only place the app needs to know about processes at all.
"""

from __future__ import annotations

import os
import subprocess
from typing import Iterable


class GuardError(Exception):
    pass


def running_processes() -> set[str]:
    """Lowercased executable names of currently running processes."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            names = set()
            for line in out.splitlines():
                line = line.strip()
                if line.startswith('"'):
                    names.add(line.split('","')[0].strip('"').lower())
            return names
        out = subprocess.run(
            ["ps", "-eo", "comm="], capture_output=True, text=True, timeout=15
        ).stdout
        return {line.strip().rsplit("/", 1)[-1].lower() for line in out.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        # If we cannot enumerate processes, do not block the user; the guard is a
        # convenience, and every write path is already backed up.
        return set()


def blocking_processes(guarded: Iterable[str]) -> list[str]:
    wanted = {name.strip().lower() for name in guarded if name.strip()}
    if not wanted:
        return []
    running = running_processes()
    return sorted(name for name in wanted if name in running)


def assert_clear(guarded: Iterable[str]) -> None:
    blocking = blocking_processes(guarded)
    if blocking:
        raise GuardError(
            f"{', '.join(blocking)} is running. Close it before syncing - a running game "
            "rewrites its save files and can corrupt either side."
        )
