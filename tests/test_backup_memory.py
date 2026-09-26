"""ك٥٥ (الجزء ٢): النسخةُ الاحتياطية للمساحة تحمل ذاكرةَ المشاريع، واستعادتُها تمرّ بـ`MemoryStore.restore`.

فما نُسي بعد النسخة لا يعود باستعادتها ما دامت إيصالاتُ المساحة الحيّة معها (§٣.٣). ونسخةٌ فيها
ذاكرة لا تُستعاد بلا اختيارٍ صريح لتلك الإيصالات.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from core import filelock
from core.canonical import canonical_bytes
from evaluation.memory_runner import _Wired
from memory.store import LOCK_NAME, MemoryStore
from workspace_tools.backup import BackupError, export_workspace, inspect_archive, restore_workspace


@pytest.fixture
def workspace(tmp_path):
    """مساحةٌ حيّة بمشروعٍ نصّيّ فيه عنصران محفوظان، والواجهةُ مغلقة (النسخُ يشترط ذلك)."""
    base = tmp_path.resolve()
    base.chmod(0o700)
    wired = _Wired(base / "live")
    project = wired.project("A")["id"]
    wired.session("A", "text")
    kept = wired.api("memory_remember", project=project, text="اسم المورد نور")["item_id"]
    gone = wired.api("memory_remember", project=project, text="رمز الخزنة ٩٩٣١")["item_id"]
    wired.close()
    return {"base": base, "root": base / "live", "project": project, "kept": kept, "gone": gone}


def _store(root, project):
    return MemoryStore(root / "projects" / project)


def _export(ws):
    archive = ws["base"] / "backup.json"
    return archive, export_workspace(ws["root"], archive)["sha256"]


def test_an_item_forgotten_after_the_backup_does_not_return_with_it(workspace):
    archive, sha = _export(workspace)
    _store(workspace["root"], workspace["project"]).forget(workspace["gone"])
    out = restore_workspace(archive, workspace["base"] / "restored", sha, tombstones_from=workspace["root"])
    assert out["memory"] == {"projects": 1, "items_in_archive": 2, "items_restored": 1,
                             "live_receipts_applied": True}
    restored = _store(workspace["base"] / "restored", workspace["project"])
    assert [item["item_id"] for item in restored.items()] == [workspace["kept"]]
    assert [r["item_id"] for r in restored.receipts()] == [workspace["gone"]]
    residue = b"".join(p.read_bytes() for p in (workspace["base"] / "restored").rglob("*") if p.is_file())
    assert "٩٩٣١".encode("utf-8") not in residue


def test_an_archive_with_memory_needs_an_explicit_choice_of_receipts(workspace):
    archive, sha = _export(workspace)
    with pytest.raises(BackupError) as err:
        restore_workspace(archive, workspace["base"] / "restored", sha)
    assert err.value.code == "backup_tombstones_required"
    assert not (workspace["base"] / "restored").exists()


def test_without_the_live_workspace_the_archive_receipts_alone_apply(workspace):
    """اختيارٌ صريح معلَن: إن فُقدت المساحةُ الحيّة فلا إيصالاتَ لنسيانٍ بعد النسخة."""
    store = _store(workspace["root"], workspace["project"])
    store.forget(workspace["gone"])
    archive, sha = _export(workspace)            # النسيانُ قبل النسخة: إيصالُه فيها
    out = restore_workspace(archive, workspace["base"] / "restored", sha, tombstones_from=None)
    assert out["memory"]["live_receipts_applied"] is False and out["memory"]["items_restored"] == 1
    restored = _store(workspace["base"] / "restored", workspace["project"])
    assert [item["item_id"] for item in restored.items()] == [workspace["kept"]]


def _tamper(archive: Path, change) -> str:
    bundle = json.loads(archive.read_bytes())
    for item in bundle["files"]:
        if change(item):
            raw = base64.b64decode(item["data_base64"])
            item["size_bytes"], item["sha256"] = len(raw), hashlib.sha256(raw).hexdigest()
            item["data_base64"] = base64.b64encode(raw).decode("ascii")
    raw = canonical_bytes(bundle)
    archive.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_a_memory_item_that_does_not_match_its_digest_is_refused_on_inspection(workspace):
    archive, _ = _export(workspace)

    def swap(item):
        if "/memory/items/" not in item["path"]:
            return False
        value = json.loads(base64.b64decode(item["data_base64"]))
        value["text"] = value["text"] + " معدَّل"
        item["data_base64"] = base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()
        return True
    sha = _tamper(archive, swap)
    with pytest.raises(BackupError) as err:
        inspect_archive(archive, sha)
    assert err.value.code == "backup_memory_invalid"


def test_an_unexpected_file_in_a_memory_store_is_refused(workspace):
    memory = workspace["root"] / "projects" / workspace["project"] / "memory"
    stray = memory / "notes.txt"
    stray.write_text("x")
    stray.chmod(0o600)
    with pytest.raises(BackupError) as err:
        _export(workspace)
    assert err.value.code == "backup_tree_invalid"


def test_the_backup_waits_for_a_memory_write_in_progress(workspace):
    memory = workspace["root"] / "projects" / workspace["project"] / "memory"
    fd = os.open(memory / LOCK_NAME, os.O_RDWR)
    try:
        filelock.lock(fd)
        with pytest.raises(BackupError) as err:
            _export(workspace)
        assert err.value.code == "backup_busy"
    finally:
        filelock.unlock(fd)
        os.close(fd)


def test_the_memory_directories_are_private(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    store = MemoryStore(project)
    for path in (store.root, store.root / "items"):
        assert stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0


def test_the_cli_asks_for_the_receipt_choice(workspace, capsys):
    from tools.workspace_backup import main
    archive, sha = _export(workspace)
    assert main(["restore", "--archive", str(archive), "--destination", str(workspace["base"] / "r1"),
                 "--sha256", sha]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "backup_tombstones_required"
    assert main(["restore", "--archive", str(archive), "--destination", str(workspace["base"] / "r2"),
                 "--sha256", sha, "--tombstones-from", str(workspace["root"])]) == 0
    assert json.loads(capsys.readouterr().out)["memory"]["live_receipts_applied"] is True
