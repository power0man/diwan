"""Backup/restore tests use synthetic sessions and private temporary trees only."""
import base64
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer
from acceptance_m12 import png_fixture, wav_fixture
from core.canonical import canonical_bytes, digest
from webui.server import LocalApp
from workspace_tools import backup
from workspace_tools.files import TextWorkspace, WorkspaceError


def call(app, action, **values):
    return app.dispatch({"action": action, **values})


class Provider:
    is_local = True
    model = SYNTHETIC_MODEL
    name = "synthetic-backup"
    def __init__(self):
        self.calls = 0
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.calls += 1
        return _answer("جواب مصطنع محفوظ")


def poison():
    raise AssertionError("Backup/replay must not construct a provider")


def opened(root, factory):
    return LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
        provider_factory=factory, media_model=SYNTHETIC_MODEL,
        media_model_version=SYNTHETIC_VERSION, media_provider_factory=factory)


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None,
                                     p.stat().st_mtime_ns, p.stat().st_ino)
            for p in (root, *sorted(root.rglob("*")))}


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path.resolve() / "source"
    provider = Provider()
    app = opened(root, lambda: provider)
    project = call(app, "create_project", name="مشروع الاختبار")["id"]
    uploaded = call(app, "upload", project=project, upload=uuid.uuid4().hex,
                    name="مرفق.txt", content="مرفق عربي\u200f")
    pref = call(app, "set_preference", project=project, key="verbosity", value="concise", revision=0)
    turns = []
    for mode, raw, name in (("text", None, None), ("media", png_fixture(), "صورة.png"),
                            ("media", wav_fixture(), "صوت.wav")):
        session = call(app, "create_session", project=project, name="جلسة", mode=mode)["id"]
        turn = uuid.uuid4().hex
        ctx = {"project": project, "session": session, "turn": turn}
        if mode == "text":
            result = call(app, "ask", **ctx, message="اقرأ", files=[uploaded["path"]])
        else:
            result = call(app, "ask_media", **ctx, message="اقرأ", media=[{
                "name": name, "data_base64": base64.b64encode(raw).decode("ascii")}])
        turns.append((ctx, result, call(app, "inspect", **ctx)))
    applied = call(app, "propose", **turns[0][0], name="جواب.txt", request=uuid.uuid4().hex)
    call(app, "apply", project=project, proposal=applied["proposal_id"], sha256=applied["sha256"])
    proposed = call(app, "propose", **turns[0][0], name="لم-يطبق.txt", request=uuid.uuid4().hex)
    app.close()
    empty = root / "projects" / project / "outputs" / "empty"
    empty.mkdir(mode=0o700)
    archive = tmp_path / "backup.json"
    return {"root": root, "archive": archive, "project": project, "provider": provider,
            "turns": turns, "uploaded": uploaded, "pref": pref,
            "applied": applied, "proposed": proposed}


def export(fixture):
    return backup.export_workspace(fixture["root"], fixture["archive"])


def rewrite(path, value):
    path.write_bytes(canonical_bytes(value))
    path.chmod(0o600)


def edit_bundle(fixture, mutate):
    report = export(fixture)
    bundle = json.loads(fixture["archive"].read_bytes())
    mutate(bundle)
    rewrite(fixture["archive"], bundle)
    return hashlib.sha256(fixture["archive"].read_bytes()).hexdigest()


def test_full_roundtrip_preserves_frozen_data_and_only_rebinds_own_created_files(fixture, tmp_path):
    original = snapshot(fixture["root"])
    report = export(fixture)
    assert report["status"] == "exported"
    assert snapshot(fixture["root"]) == original
    assert set(report) == {"status", "sha256", "file_count", "directory_count", "total_bytes"}
    verified = backup.inspect_archive(fixture["archive"], report["sha256"])
    assert verified == {**report, "status": "verified"}
    dest = tmp_path / "restored"
    assert backup.restore_workspace(fixture["archive"], dest, report["sha256"])["status"] == "restored"
    assert not (dest / backup.INCOMPLETE).exists()
    assert (dest / backup.PROVENANCE).is_file()
    assert snapshot(fixture["root"]) == original
    app = opened(dest, poison)
    try:
        for ctx, old, inspection in fixture["turns"]:
            assert call(app, "replay", **ctx)["content"] == old["content"]
            assert call(app, "inspect", **ctx) == inspection
        assert call(app, "preferences", project=fixture["project"]) == fixture["pref"]
        files = call(app, "files", project=fixture["project"])
        assert files["files"][0]["sha256"] == fixture["uploaded"]["sha256"]
        assert files["unavailable"] == []
        outputs = dest / "projects" / fixture["project"] / "outputs"
        assert (outputs / "empty").is_dir()
        before = snapshot(outputs)
        result = call(app, "apply", project=fixture["project"],
                      proposal=fixture["applied"]["proposal_id"], sha256=fixture["applied"]["sha256"])
        assert result["replayed"] and result["status"] == "applied"
        assert snapshot(outputs) == before
        assert call(app, "review", project=fixture["project"],
                    proposal=fixture["proposed"]["proposal_id"])["status"] == "proposed"
        assert not (outputs / "لم-يطبق.txt").exists()
    finally:
        app.close()
    assert fixture["provider"].calls == 3
    assert fixture["archive"].stat().st_mode & 0o777 == 0o600
    assert dest.stat().st_mode & 0o777 == 0o700


def test_restored_tree_can_be_backed_up_again(fixture, tmp_path):
    report = export(fixture)
    dest = tmp_path / "restored"
    backup.restore_workspace(fixture["archive"], dest, report["sha256"])
    report2 = backup.export_workspace(dest, tmp_path / "second.json")
    assert backup.inspect_archive(tmp_path / "second.json", report2["sha256"])["status"] == "verified"


def test_naive_copy_does_not_satisfy_existing_receipt_contract(fixture, tmp_path):
    source = fixture["root"] / "projects" / fixture["project"] / "outputs"
    copied = tmp_path / "naive"
    shutil.copytree(source, copied)
    with pytest.raises(WorkspaceError, match="config_conflict"):
        TextWorkspace(None, copied)


def test_identical_replacement_after_restore_is_still_rejected(fixture, tmp_path):
    report = export(fixture)
    dest = tmp_path / "restored"
    backup.restore_workspace(fixture["archive"], dest, report["sha256"])
    outputs = dest / "projects" / fixture["project"] / "outputs"
    target = outputs / "جواب.txt"
    old = target.read_bytes()
    target.rename(outputs / "old-owned-file")
    target.write_bytes(old); target.chmod(0o600)
    workspace = TextWorkspace(None, outputs)
    with pytest.raises(WorkspaceError, match="applied_output_changed"):
        workspace.apply(fixture["applied"]["proposal_id"], fixture["applied"]["sha256"])
    assert target.read_bytes() == old


@pytest.mark.parametrize("which", ["app", "session", "preferences", "store"])
def test_busy_locks_are_refused_without_source_changes(fixture, which):
    root = fixture["root"]
    suffix = {"app": "app.lock", "session": "session.lock", "preferences": "preferences.lock", "store": "store.lock"}[which]
    path = next(root.rglob(suffix))
    before = snapshot(root)
    with path.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(backup.BackupError, match="backup_busy"):
            export(fixture)
    assert snapshot(root) == before
    assert not fixture["archive"].exists()


@pytest.mark.parametrize("which", ["turn", "write"])
def test_pending_source_refused_not_recovered(fixture, which):
    root = fixture["root"]
    if which == "turn":
        session = fixture["turns"][0][0]["session"]
        path = next(root.rglob(session + "/state.json"))
        envelope = json.loads(path.read_bytes())
        envelope["state"]["turns"][-1]["result"] = None
    else:
        path = root / "projects" / fixture["project"] / "outputs/.diwan-tools/writes/state.json"
        envelope = json.loads(path.read_bytes())
        envelope["state"]["proposals"][-1]["status"] = "pending"
    envelope["sha256"] = digest(envelope["state"])
    rewrite(path, envelope)
    before = snapshot(root)
    with pytest.raises(backup.BackupError, match="backup_pending"):
        export(fixture)
    assert snapshot(root) == before
    assert not fixture["archive"].exists()


@pytest.mark.parametrize("change", ["missing", "bytes", "same-bytes-new-inode"])
def test_changed_applied_output_not_regenerated_or_adopted(fixture, change):
    path = fixture["root"] / "projects" / fixture["project"] / "outputs/جواب.txt"
    raw = path.read_bytes()
    if change == "missing":
        path.unlink()
    elif change == "bytes":
        path.write_bytes(b"changed")
    else:
        path.rename(path.with_name("unowned"))
        path.write_bytes(raw); path.chmod(0o600)
    before = snapshot(fixture["root"])
    with pytest.raises(backup.BackupError, match="backup_applied_changed"):
        export(fixture)
    assert snapshot(fixture["root"]) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "unknown", "staging", "missing-lock"])
def test_unsafe_or_incomplete_source_refused(fixture, tmp_path, kind):
    root = fixture["root"]
    if kind == "symlink":
        (root / "other").symlink_to(tmp_path)
    elif kind == "hardlink":
        os.link(root / "app.lock", root / "other")
    elif kind == "public":
        (root / "app.lock").chmod(0o644)
    elif kind == "unknown":
        path = root / "other"; path.write_bytes(b"x"); path.chmod(0o600)
    elif kind == "staging":
        (root / "staging" / "incomplete").mkdir(mode=0o700)
    else:
        (root / "app.lock").unlink()
    with pytest.raises(backup.BackupError):
        export(fixture)
    assert not fixture["archive"].exists()


def test_missing_optional_stores_stay_absent(tmp_path):
    root = tmp_path.resolve() / "source"
    app = opened(root, poison)
    pid = call(app, "create_project", name="فارغ")["id"]
    app.close()
    archive = tmp_path / "backup.json"
    result = backup.export_workspace(root, archive)
    dest = tmp_path / "restored"
    backup.restore_workspace(archive, dest, result["sha256"])
    assert set(p.name for p in (dest / "projects" / pid).iterdir()) == {"meta.json"}


def test_wrong_digest_rejected_before_destination_created(fixture, tmp_path):
    export(fixture)
    dest = tmp_path / "restored"
    with pytest.raises(backup.BackupError, match="backup_digest_mismatch"):
        backup.restore_workspace(fixture["archive"], dest, "0" * 64)
    assert not dest.exists()


@pytest.mark.parametrize("target", ["archive", "destination"])
def test_existing_targets_never_overwritten(fixture, tmp_path, target):
    if target == "archive":
        fixture["archive"].write_bytes(b"sentinel"); fixture["archive"].chmod(0o600)
        with pytest.raises(backup.BackupError, match="backup_destination_exists"):
            export(fixture)
        assert fixture["archive"].read_bytes() == b"sentinel"
    else:
        report = export(fixture)
        dest = tmp_path / "restored"; dest.mkdir(mode=0o700)
        before = snapshot(dest)
        with pytest.raises(backup.BackupError, match="backup_destination_exists"):
            backup.restore_workspace(fixture["archive"], dest, report["sha256"])
        assert snapshot(dest) == before


@pytest.mark.parametrize("mutation", ["traversal", "absolute", "duplicate", "content", "unknown", "bool-size", "owner"])
def test_malformed_archive_refused_before_any_destination(fixture, tmp_path, mutation):
    def alter(bundle):
        if mutation == "traversal":
            bundle["files"][0]["path"] = "../outside"
        elif mutation == "absolute":
            bundle["files"][0]["path"] = "/outside"
        elif mutation == "duplicate":
            bundle["files"].append(copy.deepcopy(bundle["files"][0]))
        elif mutation == "content":
            bundle["files"][0]["data_base64"] = "AA=="
        elif mutation == "unknown":
            bundle["extra"] = True
        elif mutation == "bool-size":
            bundle["files"][0]["size_bytes"] = False
        else:
            entry = next(e for e in bundle["files"] if e["path"].endswith("outputs/جواب.txt"))
            entry["identity"]["inode"] = "0"
    sha = edit_bundle(fixture, alter)
    dest = tmp_path / "restored"
    with pytest.raises(backup.BackupError):
        backup.restore_workspace(fixture["archive"], dest, sha)
    assert not dest.exists()


@pytest.mark.parametrize("limit", ["raw", "entries", "encoded", "depth"])
def test_capacity_limits_fail_closed(fixture, monkeypatch, limit):
    monkeypatch.setattr(backup, {"raw": "MAX_RAW_BYTES", "entries": "MAX_ENTRIES",
                                "encoded": "MAX_ARCHIVE_BYTES", "depth": "MAX_DEPTH"}[limit], 1)
    before = snapshot(fixture["root"])
    with pytest.raises(backup.BackupError):
        export(fixture)
    assert snapshot(fixture["root"]) == before
    assert not fixture["archive"].exists()


def test_incomplete_destination_remains_marked_on_failure(fixture, tmp_path, monkeypatch):
    report = export(fixture)
    dest = tmp_path / "restored"
    original = backup._validate_tree
    def fail_destination(root, *args):
        if root == dest:
            raise backup.BackupError("injected", "synthetic interruption")
        return original(root, *args)
    monkeypatch.setattr(backup, "_validate_tree", fail_destination)
    with pytest.raises(backup.BackupError, match="injected"):
        backup.restore_workspace(fixture["archive"], dest, report["sha256"])
    assert (dest / backup.INCOMPLETE).is_file()
    with pytest.raises(backup.BackupError, match="backup_incomplete"):
        backup.export_workspace(dest, tmp_path / "must-not-export.json")


def test_archive_cannot_be_created_inside_source(fixture):
    with pytest.raises(backup.BackupError, match="backup_unsafe_path"):
        backup.export_workspace(fixture["root"], fixture["root"] / "backup.json")


def test_media_profile_mismatch_rejected_even_when_session_hashes_match(tmp_path):
    root = tmp_path.resolve() / "source"
    app = opened(root, poison)
    pid = call(app, "create_project", name="تجربة")["id"]
    sid = call(app, "create_session", project=pid, name="وسائط", mode="media")["id"]
    app.close()
    chat = root / "projects" / pid / "sessions" / sid / "chat" / sid
    manifest = json.loads((chat / "manifest.json").read_bytes())
    manifest["max_output"] = 401
    rewrite(chat / "manifest.json", manifest)
    envelope = json.loads((chat / "state.json").read_bytes())
    envelope["state"]["config_sha256"] = digest(manifest)
    envelope["sha256"] = digest(envelope["state"])
    rewrite(chat / "state.json", envelope)
    before = snapshot(root)
    with pytest.raises(backup.BackupError, match="backup_invalid"):
        backup.export_workspace(root, tmp_path / "backup.json")
    assert snapshot(root) == before
