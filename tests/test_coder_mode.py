"""ج٩ (#45) الشطر الأول: جلسةُ المبرمج، وفرقُ الجولة، والتراجعُ عن الجولة كلِّها.

- جلسةُ المبرمج وكيلةٌ بتعليماتها المسجَّلة ببصمتها وبأدوات الشيفرة وحدها. والتنفيذُ منها حيث ضُبطت خُلفيّتُه.
- فرقُ الجولة ملفًّا ملفًّا، من حال الملفّ قبل أول فعلٍ فيها إلى حاله بعد آخر فعل.
- التراجعُ عن الجولة يُرجع كلَّ ملفٍّ مسَّته، بعكس ترتيب أفعالها، ولكلّ فعلٍ إيصال.
  - إن تغيّر ملفٌّ بعدها رُفض الطلبُ كلُّه قبل أن يُرجع عن شيء.
  - وكذلك إن رُدّ بعضُها وحده، أو تغيّر ملفٌّ بين فعلين فيها.
- الجلسةُ تُنسخ وتُستعاد، ولا تُسأل بطريق النصّ.
- `tools/evaluate_agentic.py --mode coder` يقيس بنكَ ك٤٤ بتعليمات المبرمج وأدواته نفسِها.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

from agent.builtin_tools import DEFAULT_TOOLS
from agent.coder import CODER_SYSTEM, CODER_TOOLS, coder_tools, file_diff
from agent.journal import Journal
from core.contracts import Response, ToolCall, Usage
from webui.server import LocalApp, UIError
from workspace_tools.backup import export_workspace, restore_workspace

ROOT = Path(__file__).resolve().parents[1]
CALC = "def area(w, h):\n    return w + h\n"


def _says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="scripted", model_version="v1",
                    tool_calls=tuple(calls))


class Scripted:
    """يؤدّي ما كُتب له جولةً جولة: قائمةُ خطواتٍ لكل جولة، وكلُّ خطوةٍ نداءاتٌ أو جواب، وقد تسبقها يدٌ على القرص."""
    name, is_local = "scripted", True

    def __init__(self, *turns):
        self.turns = [list(t) for t in turns]

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        step = self.turns[0].pop(0)
        if not self.turns[0]:
            self.turns.pop(0)
        if callable(step):
            step = step()
        return step


def edit(path, old, new, n=0):
    return ToolCall(f"edit-{n}-{uuid.uuid4().hex[:6]}", "edit_file", {"path": path, "old_text": old, "new_text": new})


def write(path, content, n=0):
    return ToolCall(f"write-{n}-{uuid.uuid4().hex[:6]}", "write_file", {"path": path, "content": content})


def _app(root, provider=None):
    app = LocalApp(root, model="scripted", model_version="v" * 64, provider_factory=lambda: None,
                   agent_provider_factory=lambda: provider)
    app.api = lambda action, **values: app.dispatch({"action": action, **values})
    return app


@pytest.fixture
def base(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    return root


def _coder(app, mode="coder"):
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="برمجة", mode=mode)["id"]
    workspace = app.root / "projects" / project / "agent-workspace"
    (workspace / "src").mkdir(mode=0o700, exist_ok=True)
    (workspace / "src" / "calc.py").write_text(CALC, encoding="utf-8")
    (workspace / "src" / "calc.py").chmod(0o600)        # كما يكتب الدفتر: النسخُ الاحتياطيّ يرفض ما يقرؤه الآخرون
    return project, session, workspace


def _ask(app, project, session, message="أصلح area"):
    return app.api("agent_ask", project=project, session=session, turn=uuid.uuid4().hex, message=message, files=[])


def _code(call):
    with pytest.raises(Exception) as err:
        call()
    return getattr(err.value, "code", None)


# ————— الجلسة —————

def test_the_instructions_are_registered_by_digest():
    assert hashlib.sha256(CODER_SYSTEM.encode("utf-8")).hexdigest() == \
        "0fe0cef4f87749aec321380eface3e9004554eb384c1d250c91217d4dc2da2c4"


def test_a_coder_session_has_its_instructions_and_the_code_tools_alone(base):
    app = _app(base / "root", Scripted())
    project, session, _ = _coder(app)
    manifest = json.loads((app.root / "projects" / project / "agent-control" / session / "manifest.json").read_bytes())
    assert manifest["system"] == CODER_SYSTEM
    assert sorted(tool["name"] for tool in manifest["tools"]) == ["edit_file", "list_files", "read_file",
                                                                   "search_files", "write_file"]
    app.close()


def test_execution_tools_join_only_where_execution_is_configured():
    assert [t.spec.name for t in coder_tools(DEFAULT_TOOLS, execution=True)] == list(CODER_TOOLS)
    assert {t.spec.name for t in coder_tools(DEFAULT_TOOLS, execution=False)} == set(CODER_TOOLS) - {"run_tests",
                                                                                                    "run_command"}


def test_a_coder_session_is_not_asked_through_the_text_path(base):
    app = _app(base / "root", Scripted())
    project, session, _ = _coder(app)
    with pytest.raises(UIError) as err:
        app.api("ask", project=project, session=session, turn=uuid.uuid4().hex, message="سؤال", files=[])
    assert err.value.code == "session_mode_mismatch"
    app.close()


def test_coder_needs_the_agent_path(base):
    app = LocalApp(base / "plain", model="scripted", model_version="v" * 64, provider_factory=lambda: None)
    project = app.dispatch({"action": "create_project", "name": "أ"})["id"]
    with pytest.raises(UIError) as err:
        app.dispatch({"action": "create_session", "project": project, "name": "ب", "mode": "coder"})
    assert err.value.code == "coder_unavailable"
    app.close()


# ————— فرقُ الجولة —————

def test_a_turn_shows_its_changes_file_by_file(base):
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h"), write("NOTES.md", "ضربٌ لا جمع\n")),
                                        _says("أصلحتُ area.")]))
    project, session, workspace = _coder(app)
    result = _ask(app, project, session)
    assert result["status"] == "complete" and result["mode"] == "coder"
    changes = app.api("agent_turn_changes", project=project, session=session, turn=result["turn_id"])
    files = {f["path"]: f for f in changes["files"]}
    assert set(files) == {"src/calc.py", "NOTES.md"} and changes["complete"]
    assert files["src/calc.py"]["status"] == "modified" and files["src/calc.py"]["now"] == "as_after"
    assert "-    return w + h" in files["src/calc.py"]["diff"] and "+    return w * h" in files["src/calc.py"]["diff"]
    assert files["NOTES.md"]["status"] == "added" and "+ضربٌ لا جمع" in files["NOTES.md"]["diff"]
    app.close()


def test_two_edits_of_one_file_show_one_diff_from_the_first_state_to_the_last(base):
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h")),
                                        _says("", edit("src/calc.py", "w * h", "h * w", 1)), _says("تم.")]))
    project, session, _ = _coder(app)
    turn = _ask(app, project, session)["turn_id"]
    (only,) = app.api("agent_turn_changes", project=project, session=session, turn=turn)["files"]
    assert only["actions"] == 2 and "-    return w + h" in only["diff"] and "+    return h * w" in only["diff"]
    assert "w * h" not in only["diff"]
    app.close()


def test_a_turn_without_writes_has_nothing_to_revert(base):
    app = _app(base / "root", Scripted([_says("لا حاجة لتعديل.")]))
    project, session, _ = _coder(app)
    turn = _ask(app, project, session)["turn_id"]
    assert app.api("agent_turn_changes", project=project, session=session, turn=turn)["files"] == []
    assert _code(lambda: app.api("agent_revert_turn", project=project, session=session, turn=turn,
                                 request=uuid.uuid4().hex)) == "turn_has_no_changes"
    assert _code(lambda: app.api("agent_turn_changes", project=project, session=session,
                                 turn=uuid.uuid4().hex)) == "turn_unknown"
    app.close()


def test_a_turn_awaiting_the_owner_is_neither_diffed_nor_reverted(base):
    call = ToolCall("mem-1", "propose_memory", {"text": "المشروعُ بالعربية"})
    app = _app(base / "root", Scripted([_says("", write("a.txt", "أ\n")), _says("", call)]))
    project, session, _ = _coder(app, mode="agent")
    result = _ask(app, project, session)
    assert result["status"] == "awaiting_owner"
    for action, extra in (("agent_turn_changes", {}), ("agent_revert_turn", {"request": uuid.uuid4().hex})):
        assert _code(lambda: app.api(action, project=project, session=session, turn=result["turn_id"],
                                     **extra)) == "turn_unresolved"
    app.close()


# ————— التراجعُ عن الجولة —————

def test_reverting_a_turn_restores_every_file_then_reports_already_reverted(base):
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h")),
                                        _says("", edit("src/calc.py", "w * h", "h * w", 1), write("NOTES.md", "ملاحظة\n")),
                                        _says("تم.")]))
    project, session, workspace = _coder(app)
    turn = _ask(app, project, session)["turn_id"]
    out = app.api("agent_revert_turn", project=project, session=session, turn=turn, request=uuid.uuid4().hex)
    assert out["status"] == "reverted" and out["files"] == ["NOTES.md", "src/calc.py"] and len(out["receipts"]) == 3
    assert (workspace / "src" / "calc.py").read_text(encoding="utf-8") == CALC
    assert not (workspace / "NOTES.md").exists()
    again = app.api("agent_revert_turn", project=project, session=session, turn=turn, request=uuid.uuid4().hex)
    assert again["status"] == "already_reverted" and again["receipts"] == []
    files = app.api("agent_turn_changes", project=project, session=session, turn=turn)["files"]
    assert {f["now"] for f in files} == {"as_before"}
    app.close()


def test_a_file_changed_after_the_turn_blocks_the_whole_revert(base):
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h"), write("NOTES.md", "ملاحظة\n")),
                                        _says("تم.")]))
    project, session, workspace = _coder(app)
    turn = _ask(app, project, session)["turn_id"]
    (workspace / "NOTES.md").write_text("ملاحظةُ المالك بعد الجولة\n", encoding="utf-8")
    assert _code(lambda: app.api("agent_revert_turn", project=project, session=session, turn=turn,
                                 request=uuid.uuid4().hex)) == "changed_since_turn"
    assert "w * h" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")       # لم يُرجع عن شيء
    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == "ملاحظةُ المالك بعد الجولة\n"
    app.close()


def test_a_partially_reverted_turn_is_refused_by_name(base):
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h"), write("NOTES.md", "ملاحظة\n")),
                                        _says("تم.")]))
    project, session, workspace = _coder(app)
    result = _ask(app, project, session)
    (edit_result,) = [r for s in result["steps"] for r in s["tool_results"] if r["name"] == "edit_file"]
    single = app.api("agent_revert", project=project, session=session, action_id=edit_result["action_id"],
                     request=uuid.uuid4().hex)
    assert single["status"] == "reverted"
    assert _code(lambda: app.api("agent_revert_turn", project=project, session=session, turn=result["turn_id"],
                                 request=uuid.uuid4().hex)) == "turn_partially_reverted"
    assert (workspace / "NOTES.md").exists()
    app.close()


def test_a_file_changed_between_two_actions_of_the_turn_is_not_reverted_at_once(base):
    holder = {}

    def meddle():
        path = holder["workspace"] / "src" / "calc.py"
        path.write_text(path.read_text(encoding="utf-8") + "# يدٌ بين فعلين\n", encoding="utf-8")
        return _says("", edit("src/calc.py", "w * h", "h * w", 1))
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h")), meddle, _says("تم.")]))
    project, session, workspace = _coder(app)
    holder["workspace"] = workspace
    turn = _ask(app, project, session)["turn_id"]
    assert _code(lambda: app.api("agent_revert_turn", project=project, session=session, turn=turn,
                                 request=uuid.uuid4().hex)) == "turn_not_revertible"
    assert "h * w" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")
    app.close()


# ————— النسخُ والقياس —————

def test_a_coder_session_is_backed_up_restored_and_reverted_there(base):
    app = _app(base / "root", Scripted([_says("", edit("src/calc.py", "w + h", "w * h")), _says("تم.")]))
    project, session, _ = _coder(app)
    turn = _ask(app, project, session)["turn_id"]
    app.close()
    archive = base / "backup.json"
    sha = export_workspace(base / "root", archive)["sha256"]
    out = restore_workspace(archive, base / "restored", sha)
    assert out["agent_sessions"] == {"sessions": 1, "archived": []}
    restored = _app(base / "restored", Scripted())
    history = restored.api("history", project=project, session=session, before=None)
    assert history["turns"][0]["mode"] == "coder"
    reverted = restored.api("agent_revert_turn", project=project, session=session, turn=turn, request=uuid.uuid4().hex)
    assert reverted["status"] == "reverted"
    workspace = base / "restored" / "projects" / project / "agent-workspace"
    assert (workspace / "src" / "calc.py").read_text(encoding="utf-8") == CALC
    restored.close()


def test_the_bank_measures_coder_mode_with_its_instructions_and_tools(tmp_path, monkeypatch):
    import providers.ollama
    sys.path.insert(0, str(ROOT))
    from tools import evaluate_agentic
    suite_path = ROOT / "evaluation" / "suites" / "agentic_v2.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    meta = json.loads(suite_path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    for task in suite["tasks"]:
        if task["success"]["kind"] == "command_exit_zero":
            task["success"]["command"][0] = sys.executable
    path = tmp_path / "agentic_v2.json"
    path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    path.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    by_instruction = {t["instruction"]: meta["tasks"][t["task_id"]]["reference_solution"] for t in suite["tasks"]}
    seen_systems = set()

    class Player:
        name, is_local = "player", True

        def estimate_micros(self, request):
            return 0

        def complete(self, request):
            seen_systems.update(m.content for m in request.messages if m.role == "system")
            if any(m.role == "tool" for m in request.messages):
                return _says("تم.")
            text = "\n".join(m.content for m in request.messages if m.role == "user")
            (files,) = [f for instruction, f in by_instruction.items() if instruction in text]
            return _says("", *(write(name, content, i) for i, (name, content) in enumerate(sorted(files.items()))))

    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "pytest-host")
    monkeypatch.setattr(providers.ollama, "OllamaProvider", lambda model: Player())
    out = tmp_path / "report.json"
    assert evaluate_agentic.main(["--suite", str(path), "--model", "player", "--mode", "coder", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["config"]["system_sha256"] == hashlib.sha256(CODER_SYSTEM.encode("utf-8")).hexdigest()
    assert sorted(report["config"]["tools"]) == ["edit_file", "list_files", "read_file", "search_files", "write_file"]
    assert seen_systems == {CODER_SYSTEM}
    assert report["summary"]["passed"] == 30 and report["thresholds"]["meets_thresholds"]


# ————— الأجزاء —————

def test_file_diff_names_binary_and_added_files():
    assert file_diff("a.bin", b"\x00\xff", b"\x01\xfe")["binary"] is True
    added = file_diff("n.txt", None, "سطر\n".encode())
    assert added["status"] == "added" and "+سطر" in added["diff"]


def test_journal_content_is_returned_only_under_its_own_digest(tmp_path):
    root = tmp_path.resolve()
    journal = Journal(root)
    first = journal.write_file("f.txt", "أول\n")
    second = journal.write_file("f.txt", "ثانٍ\n")
    assert journal.content(first.after_sha256, "f.txt") == "أول\n".encode()      # نسخةُ ما قبل الفعل الثاني
    assert journal.content(second.after_sha256, "f.txt") == "ثانٍ\n".encode()   # الملفُّ الآن
    assert journal.content(hashlib.sha256(b"x").hexdigest(), "f.txt") is None
    assert journal.current_sha256("f.txt") == second.after_sha256 and journal.current_sha256("g.txt") is None
