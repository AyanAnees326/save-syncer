"""PyInstaller entry point - launches the desktop window.

Kept separate from savesync/cli.py so the frozen build has one obvious, minimal
entry script with no Typer/CLI machinery to bundle for something that is only ever
double-clicked.
"""

from savesync.desktop import launch

if __name__ == "__main__":
    launch()
