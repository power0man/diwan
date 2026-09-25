"""Independent synthetic backup checks; no existing private data is read."""
import base64
import hashlib
import json
import os

import pytest

from acceptance_m13 import _app, _snapshot, SyntheticProvider
from core.canonical import canonical_bytes
import workspace_tools.backup as backup


@pytest.mark.parametrize("alternate_spelling", [None, "case", "unicode"])
def test_restore_failure_after_mkdir_cannot_open_as_fresh_workspace(tmp_path, monkeypatch, alternate_spelling):
    source = tmp_path.resolve() / "source"
    app = _app(source, SyntheticProvider)
    app.close()
    archive = tmp_path / "copy.json"
    exported = backup.export_workspace(source, archive)
    destination = tmp_path / ("caf\u00e9" if alternate_spelling == "unicode" else "restored")
    original_fsync = os.fsync

    def fail_first_persist_after_target_creation(fd):
        # Actual mkdir succeeded; persistence of its parent then fails.
        if destination.exists():
            raise OSError("synthetic parent fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(backup.os, "fsync", fail_first_persist_after_target_creation)
    with pytest.raises(backup.BackupError):
        backup.restore_workspace(archive, destination, exported["sha256"])
    monkeypatch.undo()
    if destination.exists():
        before = _snapshot(destination)
        opened_path = (destination.with_name(destination.name.upper()) if alternate_spelling == "case"
                       else destination.with_name("cafe\u0301") if alternate_spelling == "unicode"
                       else destination)
        if alternate_spelling and not opened_path.exists():
            pytest.skip("filesystem is case sensitive")
        try:
            reopened = _app(opened_path, SyntheticProvider)
        except Exception as exc:
            assert getattr(exc, "code", None) == "restore_incomplete"
        else:
            reopened.close()
            pytest.fail("restore error left an openable, empty destination")
        assert _snapshot(destination) == before
        assert not (destination / "app.lock").exists()


def test_intent_before_failed_target_creation_blocks_ui_without_creating_root(tmp_path, monkeypatch):
    source = tmp_path.resolve() / "source"
    app = _app(source, SyntheticProvider)
    app.close()
    archive = tmp_path / "copy.json"
    exported = backup.export_workspace(source, archive)
    destination = tmp_path / "restored"
    real_mkdir = os.mkdir

    def fail_destination(path, *args, **kwargs):
        if path == destination.name and "dir_fd" in kwargs:
            raise OSError("synthetic target mkdir failure")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(backup.os, "mkdir", fail_destination)
    with pytest.raises(backup.BackupError):
        backup.restore_workspace(archive, destination, exported["sha256"])
    monkeypatch.undo()
    before = _snapshot(tmp_path)
    with pytest.raises(Exception) as caught:
        _app(destination, SyntheticProvider)
    assert getattr(caught.value, "code", None) == "restore_incomplete"
    assert not destination.exists()
    assert _snapshot(tmp_path) == before


def test_destination_creation_race_keeps_existing_tree_and_removes_only_own_intent(tmp_path, monkeypatch):
    source = tmp_path.resolve() / "source"
    app = _app(source, SyntheticProvider)
    app.close()
    archive = tmp_path / "copy.json"
    exported = backup.export_workspace(source, archive)
    destination = tmp_path / "restored"
    real_mkdir = os.mkdir

    def concurrent_mkdir(path, *args, **kwargs):
        if path == destination.name and "dir_fd" in kwargs:
            real_mkdir(path, *args, **kwargs)
            (destination / "sentinel").write_bytes(b"independent existing data")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(backup.os, "mkdir", concurrent_mkdir)
    with pytest.raises(backup.BackupError, match="backup_destination_exists"):
        backup.restore_workspace(archive, destination, exported["sha256"])
    monkeypatch.undo()
    assert list(destination.iterdir()) == [destination / "sentinel"]
    assert (destination / "sentinel").read_bytes() == b"independent existing data"
    assert not backup.restore_pending(destination)


@pytest.mark.parametrize("mutation", ["unicode_alias", "case_alias", "file_is_parent", "orphan_parent"])
def test_closed_archive_paths_fail_before_creating_destination(tmp_path, mutation):
    source = tmp_path.resolve() / "source"
    app = _app(source, SyntheticProvider)
    project = app.dispatch({"action": "create_project", "name": "fixture"})["id"]
    app.close()
    archive = tmp_path / "copy.json"
    backup.export_workspace(source, archive)
    bundle = json.loads(archive.read_bytes())
    folder = "projects/" + project + "/outputs"
    identity = {"device": "0", "inode": "0"}
    bundle["directories"].append({"path": folder, "identity": identity})

    def add_file(path):
        bundle["files"].append({"path": path, "identity": identity,
            "size_bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest(),
            "data_base64": base64.b64encode(b"x").decode("ascii")})

    if mutation == "unicode_alias":
        add_file(folder + "/caf\u00e9.txt")
        add_file(folder + "/cafe\u0301.txt")
    elif mutation == "case_alias":
        add_file(folder + "/File.txt")
        add_file(folder + "/file.txt")
    elif mutation == "file_is_parent":
        add_file(folder + "/file")
        add_file(folder + "/file/child.txt")
    else:
        add_file(folder + "/missing/child.txt")
    bundle["directories"].sort(key=lambda item: item["path"])
    bundle["files"].sort(key=lambda item: item["path"])
    raw = canonical_bytes(bundle)
    archive.write_bytes(raw)
    destination = tmp_path / "restored"
    with pytest.raises(backup.BackupError):
        backup.restore_workspace(archive, destination, hashlib.sha256(raw).hexdigest())
    assert not destination.exists()


def test_export_detects_source_mutation_during_read(tmp_path, monkeypatch):
    source = tmp_path.resolve() / "source"
    app = _app(source, SyntheticProvider)
    project = app.dispatch({"action": "create_project", "name": "fixture"})["id"]
    app.close()
    target = source / "projects" / project / "meta.json"
    real_read = backup._read_at
    changed = False

    def mutate_after_read(*args, **kwargs):
        nonlocal changed
        result = real_read(*args, **kwargs)
        if not changed:
            changed = True
            target.write_bytes(canonical_bytes({"id": project, "name": "new fixture"}))
        return result

    monkeypatch.setattr(backup, "_read_at", mutate_after_read)
    archive = tmp_path / "copy.json"
    with pytest.raises(backup.BackupError, match="backup_source_changed"):
        backup.export_workspace(source, archive)
    assert not archive.exists()
