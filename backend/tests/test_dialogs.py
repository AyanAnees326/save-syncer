"""dialogs.detect_cloud_roots must never touch tkinter or error out on a machine
with no cloud clients installed - it runs on every render of the add-profile form.
"""

from __future__ import annotations

from savesync import dialogs


def test_detect_cloud_roots_returns_a_list_without_crashing():
    roots = dialogs.detect_cloud_roots()
    assert isinstance(roots, list)
    for root in roots:
        assert set(root) == {"label", "path"}


def test_detect_cloud_roots_deduplicates_by_path(monkeypatch):
    monkeypatch.setattr(dialogs, "_dropbox_roots", lambda: [{"label": "Dropbox", "path": "C:\\Shared"}])
    monkeypatch.setattr(dialogs, "_onedrive_roots", lambda: [{"label": "OneDrive", "path": "C:\\Shared"}])
    monkeypatch.setattr(dialogs, "_google_drive_roots", lambda: [])
    monkeypatch.setattr(dialogs, "_icloud_roots", lambda: [])

    roots = dialogs.detect_cloud_roots()
    assert roots == [{"label": "Dropbox", "path": "C:\\Shared"}]


def test_dropbox_roots_survives_missing_info_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(dialogs.Path, "home", classmethod(lambda cls: tmp_path / "nope"))
    assert dialogs._dropbox_roots() == []


def test_dropbox_roots_reads_info_json(tmp_path, monkeypatch):
    import json

    appdata = tmp_path / "AppData"
    dropbox_dir = tmp_path / "Dropbox"
    dropbox_dir.mkdir()
    info = appdata / "Dropbox"
    info.mkdir(parents=True)
    (info / "info.json").write_text(
        json.dumps({"personal": {"path": str(dropbox_dir)}}), encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    roots = dialogs._dropbox_roots()
    assert roots == [{"label": "Dropbox", "path": str(dropbox_dir)}]
