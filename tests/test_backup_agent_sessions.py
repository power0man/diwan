"""ج١٢ (#85، ق٦٣): الجلساتُ الوكيلة تُنسخ وتُستعاد إلى جذرٍ جديد عبر الطريق الموصول نفسِه.

- الجلسةُ الجديدة مربوطةٌ بهويّة مساحتها لا بموضعها: تُفتح بعد الاستعادة، وتُعرض جولاتُها بلا مزوّد،
  ويُرجع عن فعلٍ سابقٍ للنسخة بزرّ الرجوع نفسِه.
- الهويّةُ تُكتب مرّةً في المساحة، في دليلٍ مخفيّ لا تكتبه أداة، ولا تُفتح جلسةٌ على مساحةٍ أخرى.
- جولةٌ تنتظر قرار المالك تمنع النسخ باسمها.
- جلسةٌ من قبل الهويّة (مخطّط ٢) لا تُفتح في موضعٍ جديد: تُرفض استعادتُها بلا اختيار، وتُستعاد أرشيفًا
  للقراءة باختيارٍ صريح، ويُسجَّل ذلك في `restore-provenance.json`.
- والنسخةُ المعدَّلة في سجلّ جلسةٍ وكيلة تُرفض.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from agent.builtin_tools import DEFAULT_TOOLS
from agent.registry import ToolRegistry
from conversation.agent_session import AgentSession, WORKSPACE_ID_DIR, WORKSPACE_ID_FILE
from conversation.session import ConversationError
from core.canonical import canonical_bytes, digest
from core.contracts import Response, ToolCall, Usage
from evaluation.memory_runner import _Wired
from workspace_tools.backup import BackupError, export_workspace, inspect_archive, restore_workspace


def _reply(text="تم.", calls=()):
    return Response(text, Usage(1, 1), "complete", 0, provider="memory-bank", model_version="0" * 64,
                    tool_calls=tuple(calls))


def _write_turn(wired, project, session, path, content):
    """جولةٌ وكيلة تكتب ملفًّا بأداة write_file (درجةُ `logged`: تُنفَّذ وتُرجَع)، ثم تجيب."""
    call = ToolCall("call_" + uuid.uuid4().hex[:8], "write_file", {"path": path, "content": content})
    wired.provider.responses += [_reply("", (call,)), _reply("كُتب الملف.")]
    result = wired.api("agent_ask", project=project, session=session, turn=uuid.uuid4().hex,
                       message=f"اكتب {path}", files=[])
    assert result["status"] == "complete", result
    (write,) = [r for step in result["steps"] for r in step["tool_results"] if r["name"] == "write_file"]
    return write["action_id"]


@pytest.fixture
def live(tmp_path):
    base = tmp_path.resolve()
    base.chmod(0o700)
    wired = _Wired(base / "live")
    project = wired.project("A")["id"]
    session = wired.session("A", "agent")
    action = _write_turn(wired, project, session, "notes/plan.md", "الخطة الأولى")
    wired.close()
    return {"base": base, "root": base / "live", "project": project, "session": session, "action": action,
            "wired": wired}


def _export(ws, name="backup.json"):
    archive = ws["base"] / name
    return archive, export_workspace(ws["root"], archive)["sha256"]


def _reopen(root):
    wired = _Wired.__new__(_Wired)
    from evaluation.memory_runner import _ScriptedProvider
    wired.provider, wired.root, wired.generation, wired.projects = _ScriptedProvider(), root, 0, {}
    wired._open()
    return wired


def test_a_new_agent_session_is_bound_to_its_workspace_identity_not_its_place(live):
    project_dir = live["root"] / "projects" / live["project"]
    manifest = json.loads((project_dir / "agent-control" / live["session"] / "manifest.json").read_bytes())
    identity = json.loads((project_dir / "agent-workspace" / WORKSPACE_ID_DIR / WORKSPACE_ID_FILE).read_bytes())
    assert manifest["schema_version"] == 3 and manifest["workspace"] == {"workspace_id": identity["workspace_id"]}
    actions = project_dir / "agent-control" / live["session"] / "actions"
    receipt = json.loads((actions / (live["action"] + ".json")).read_bytes())["record"]
    assert receipt["binding"]["workspace"] == {"workspace_id": identity["workspace_id"]}
    assert str(live["root"]) not in json.dumps(receipt["binding"]["workspace"])


def test_an_agent_session_is_backed_up_restored_replayed_and_reverted_at_a_new_root(live):
    archive, sha = _export(live)
    assert inspect_archive(archive, sha)["status"] == "verified"
    target = live["base"] / "restored"
    out = restore_workspace(archive, target, sha)
    assert out["agent_sessions"] == {"sessions": 1, "archived": []}
    provenance = json.loads((target / "restore-provenance.json").read_bytes())
    assert provenance["agent_sessions"] == {"sessions": 1, "archived": []}
    wired = _reopen(target)
    try:
        project = wired.app.project(live["project"])
        written = project / "agent-workspace" / "notes" / "plan.md"
        assert written.read_text(encoding="utf-8") == "الخطة الأولى"
        before = len(wired.provider.requests)
        history = wired.app.agent_session(project, live["session"]).history()
        assert [turn["result"]["status"] for turn in history["turns"]] == ["complete"]
        assert len(wired.provider.requests) == before          # العرضُ بلا مزوّد
        reverted = wired.api("agent_revert", project=live["project"], session=live["session"],
                             action_id=live["action"], request=uuid.uuid4().hex)
        assert reverted["status"] == "reverted", reverted
        assert not written.exists()                            # الرجوعُ عن فعلٍ سابقٍ للنسخة
    finally:
        wired.close()


def test_a_restored_session_keeps_working_and_can_be_backed_up_again(live):
    archive, sha = _export(live)
    target = live["base"] / "restored"
    restore_workspace(archive, target, sha)
    wired = _reopen(target)
    try:
        _write_turn(wired, live["project"], live["session"], "notes/second.md", "بعد الاستعادة")
    finally:
        wired.close()
    again = live["base"] / "again.json"
    assert export_workspace(target, again)["status"] == "exported"


def test_the_workspace_identity_is_hidden_from_tools_and_snapshots(live):
    project_dir = live["root"] / "projects" / live["project"]
    wired = _reopen(live["root"])
    try:
        call = ToolCall("call_x", "write_file", {"path": f"{WORKSPACE_ID_DIR}/{WORKSPACE_ID_FILE}", "content": "{}"})
        wired.provider.responses += [_reply("", (call,)), _reply("حاولت.")]
        result = wired.api("agent_ask", project=live["project"], session=live["session"], turn=uuid.uuid4().hex,
                           message="اكتب الهوية", files=[])
        (attempt,) = [r for step in result["steps"] for r in step["tool_results"]]
        assert attempt["status"] == "refused"
        listed = wired.api("agent_files", project=live["project"])
        assert not any(WORKSPACE_ID_DIR in item["path"] for item in listed["files"])
    finally:
        wired.close()
    identity = json.loads((project_dir / "agent-workspace" / WORKSPACE_ID_DIR / WORKSPACE_ID_FILE).read_bytes())
    assert set(identity) == {"schema_version", "workspace_id"}


def test_a_session_does_not_open_on_another_workspace(live, tmp_path):
    project_dir = live["root"] / "projects" / live["project"]
    other = tmp_path / "other-workspace"
    other.mkdir(mode=0o700)
    manifest = json.loads((project_dir / "agent-control" / live["session"] / "manifest.json").read_bytes())
    with pytest.raises(ConversationError) as err:
        AgentSession(project_dir / "agent-control", live["session"], workspace_root=other,
                     project_id=live["project"], registry=ToolRegistry(*DEFAULT_TOOLS),
                     model=manifest["model"], model_version=manifest["model_version"])
    assert err.value.code == "configuration_conflict"
    assert not (other / WORKSPACE_ID_DIR).exists()            # لا تُنشأ هويّةٌ لمساحةِ جلسةٍ قائمة


def test_a_turn_awaiting_the_owner_blocks_the_backup_by_name(tmp_path):
    base = tmp_path.resolve()
    base.chmod(0o700)
    wired = _Wired(base / "live")
    wired.propose("A", "اسم المورد نور")                       # propose_memory ينتظر موافقة المالك
    wired.close()
    with pytest.raises(BackupError) as err:
        export_workspace(base / "live", base / "backup.json")
    assert err.value.code == "backup_pending"


def _legacy(ws):
    """يحوّل الجلسةَ إلى صورتها قبل ج١٢ كما كُتبت يومها: ربطٌ بالمسار والجهاز وinode في البيان والإيصالات."""
    project_dir = ws["root"] / "projects" / ws["project"]
    control = project_dir / "agent-control" / ws["session"]
    work = project_dir / "agent-workspace"
    info = work.stat()
    old = {"path": str(work), "device": str(info.st_dev), "inode": str(info.st_ino)}

    def rewrite(path, change):
        path.write_bytes(canonical_bytes(change(json.loads(path.read_bytes()))))
        path.chmod(0o600)

    manifest = json.loads((control / "manifest.json").read_bytes())
    manifest.update(schema_version=2, workspace=old)
    rewrite(control / "manifest.json", lambda _: manifest)

    def state(envelope):
        inner = envelope["state"]
        inner["config_digest"] = digest(manifest)
        return {"state": inner, "sha256": digest(inner)}
    rewrite(control / "state.json", state)

    def record(envelope):
        inner = envelope["record"]
        if "binding" in inner:
            inner["binding"]["workspace"] = old
            inner["call_digest"] = digest(inner["binding"])
        elif "plan" in inner:
            inner["plan"]["workspace"] = old
        else:
            inner["workspace"] = old
        return {"record": inner, "sha256": digest(inner)}
    for path in (control / "actions").glob("*.json"):
        rewrite(path, record)


def test_a_legacy_session_is_refused_as_a_live_session_and_archived_only_by_choice(live):
    _legacy(live)
    archive, sha = _export(live)                               # النسخُ يقبلها أرشيفًا مفحوصًا
    with pytest.raises(BackupError) as err:
        restore_workspace(archive, live["base"] / "refused", sha)
    assert err.value.code == "backup_agent_session_not_portable"
    assert not (live["base"] / "refused").exists()
    target = live["base"] / "restored"
    out = restore_workspace(archive, target, sha, legacy_agent_sessions="archive")
    shelf = f"projects/{live['project']}/agent-archive/{live['session']}"
    assert out["agent_sessions"] == {"sessions": 0, "archived": [shelf]}
    assert json.loads((target / "restore-provenance.json").read_bytes())["agent_sessions"]["archived"] == [shelf]
    project_dir = target / "projects" / live["project"]
    assert not (project_dir / "sessions" / live["session"]).exists()
    assert not (project_dir / "agent-control" / live["session"]).exists()
    assert (target / shelf / "control" / "calls.jsonl").is_file()
    assert (project_dir / "agent-workspace" / "notes" / "plan.md").read_text(encoding="utf-8") == "الخطة الأولى"
    again = live["base"] / "again.json"
    assert export_workspace(target, again)["status"] == "exported"   # والأرشيفُ يُنسخ ثانيةً


def _tamper(archive: Path, change) -> str:
    bundle = json.loads(archive.read_bytes())
    change(bundle)
    raw = canonical_bytes(bundle)
    archive.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _edit_file(bundle, suffix, edit):
    entry = next(f for f in bundle["files"] if f["path"].endswith(suffix))
    raw = edit(base64.b64decode(entry["data_base64"]))
    entry.update(data_base64=base64.b64encode(raw).decode("ascii"), size_bytes=len(raw),
                 sha256=hashlib.sha256(raw).hexdigest())


def test_a_tampered_agent_ledger_is_refused(live):
    archive, _ = _export(live)
    sha = _tamper(archive, lambda b: _edit_file(b, "/calls.jsonl", lambda raw: raw.replace(b'"seq":0', b'"seq":9', 1)))
    with pytest.raises(BackupError) as err:
        inspect_archive(archive, sha)
    assert err.value.code in {"backup_agent_session_invalid", "backup_invalid"}


def test_an_unknown_file_in_an_agent_control_directory_is_refused(live):
    archive, _ = _export(live)

    def add(bundle):
        path = f"projects/{live['project']}/agent-control/{live['session']}/extra.json"
        raw = b"{}"
        bundle["files"].append({"path": path, "identity": {"device": "1", "inode": "1"}, "size_bytes": 2,
                                "sha256": hashlib.sha256(raw).hexdigest(),
                                "data_base64": base64.b64encode(raw).decode("ascii")})
        bundle["files"].sort(key=lambda f: f["path"])
    sha = _tamper(archive, add)
    with pytest.raises(BackupError) as err:
        inspect_archive(archive, sha)
    assert err.value.code == "backup_tree_invalid"


def _unsealed(raw):
    lines = raw.splitlines(keepends=True)
    entry = json.loads(lines[0])
    entry["record"]["request_digest"] = "0" * 64           # قيدٌ عُدّل ولم تُعَد بصمتُه
    return canonical_bytes(entry) + b"\n" + b"".join(lines[1:])


def _relinked(raw):
    lines = raw.splitlines(keepends=True)
    entry = json.loads(lines[0])
    entry["prev"] = "f" * 64                               # قيدٌ متّسقٌ مع بصمته، والسلسلةُ بعده مقطوعة
    entry["digest"] = digest({"prev": entry["prev"], "seq": entry["seq"], "record": entry["record"]})
    return canonical_bytes(entry) + b"\n" + b"".join(lines[1:])


@pytest.mark.parametrize("tamper", [_unsealed, _relinked], ids=["unsealed_entry", "broken_link"])
def test_a_tampered_legacy_ledger_is_refused_by_its_structural_check(live, tamper):
    """الجلسةُ القديمة لا تُفتح في موضعٍ جديد، ففحصُها البنيويّ هو الحارس الوحيد لسلسلتها."""
    _legacy(live)
    archive, _ = _export(live)
    sha = _tamper(archive, lambda b: _edit_file(b, "/calls.jsonl", tamper))
    with pytest.raises(BackupError) as err:
        inspect_archive(archive, sha)
    assert err.value.code == "backup_agent_session_invalid"


def test_a_busy_journal_blocks_the_backup(live):
    """دفترُ الرجوع يُقفل أثناء الكتابة؛ والنسخُ لا يلتقط مساحةً دفترُها قيد عملية."""
    import os
    from core import filelock
    lock = live["root"] / "projects" / live["project"] / "agent-workspace" / ".diwan-journal" / "journal.lock"
    fd = os.open(lock, os.O_RDWR)
    try:
        filelock.lock(fd, blocking=False)
        with pytest.raises(BackupError) as err:
            export_workspace(live["root"], live["base"] / "busy.json")
        assert err.value.code == "backup_busy"
    finally:
        os.close(fd)
