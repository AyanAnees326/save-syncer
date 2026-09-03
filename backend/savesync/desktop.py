"""Desktop shell: a local server plus a native window pointed at it.

The server binds to 127.0.0.1 on a random free port and requires a token that is
minted per launch and handed to the page in its URL, so nothing else on the machine
can drive the sync engine through it.
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .api import STATIC_DIR, create_app
from .config import Config

WINDOW_TITLE = "Save Syncer"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_up(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def launch(port: int = 0, use_browser: bool = False, config: Config | None = None) -> None:
    if not STATIC_DIR.is_dir():
        raise SystemExit(
            f"The UI has not been built yet.\n"
            f"  cd frontend && npm install && npm run build\n"
            f"(that writes into {STATIC_DIR})"
        )

    port = port or free_port()
    token = secrets.token_urlsafe(24)
    app = create_app(config or Config(), token=token)
    url = f"http://127.0.0.1:{port}/?token={token}"

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not _wait_until_up(port):
        raise SystemExit("the local server did not start")

    if use_browser:
        webbrowser.open(url)
        print(f"Save Syncer is running at {url}\nPress Ctrl+C to stop.")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    try:
        import webview  # pywebview
    except ImportError:
        webbrowser.open(url)
        print(
            "pywebview is not installed, so the UI opened in your browser instead.\n"
            "  pip install pywebview\n"
            f"Running at {url}. Press Ctrl+C to stop."
        )
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        return

    webview.create_window(WINDOW_TITLE, url, width=1100, height=760, min_size=(820, 560))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    launch()
