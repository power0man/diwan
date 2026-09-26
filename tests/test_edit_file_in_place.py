"""ج١١: التحريرُ في موضع الملف — فرقٌ يُعرض، ويُطبَّق بالدفتر، ويُرجع عنه.

`edit_file` يستبدل مقطعًا يرد مرّةً واحدة في ملفٍّ قائم. والتعديلُ يُحسب من اللقطة نفسِها التي
يُودَع ما قبلها ويُستبدل عليها ذرّيًّا، فلا يمحو تغييرًا وقع بين القراءة والكتابة.
"""
from __future__ import annotations

import uuid

import pytest

import agent.journal as journal_module
from agent.builtin_tools import DEFAULT_TOOLS
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from core.contracts import ToolCall
from tests.test_agent_webui import AgentRunning, ask, response
from core import execution

ORIGINAL = "def add(a, b):\n    return a - b\n"


@pytest.fixture
def space(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text(ORIGINAL, encoding="utf-8")
    return tmp_path


@pytest.fixture
def context(space):
    return ToolContext(root=space, journal=Journal(space), allowed_consents=frozenset({"auto", "logged"}))


def edit(context, **arguments):
    return ToolRegistry(*DEFAULT_TOOLS).invoke(ToolCall("e1", "edit_file", arguments), context)


def test_an_edit_changes_only_the_passage_shows_the_diff_and_reverts(space, context):
    result = edit(context, path="src/app.py", old_text="return a - b", new_text="return a + b")
    assert result["status"] == "ok"
    assert (space / "src/app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert "-    return a - b" in result["diff"] and "+    return a + b" in result["diff"]
    assert result["diff"] in result["content"]
    (action,) = context.journal.actions()
    assert action.action_id == result["action_id"] and action.path == "src/app.py"
    assert context.journal.revert(action.action_id)["status"] == "reverted"
    assert (space / "src/app.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.parametrize("old, code", [("return a * b", "edit_match_missing"), ("a", "edit_match_ambiguous")])
def test_a_passage_that_is_absent_or_repeated_is_refused_by_name(space, context, old, code):
    result = edit(context, path="src/app.py", old_text=old, new_text="x")
    assert result["status"] == "refused" and result["code"] == code
    assert (space / "src/app.py").read_text(encoding="utf-8") == ORIGINAL
    assert context.journal.actions() == []


def test_editing_a_missing_file_creates_nothing(space, context):
    result = edit(context, path="new/dir/app.py", old_text="x", new_text="y")
    assert result["status"] == "refused" and result["code"] == "file_not_found"
    assert not (space / "new").exists()
    result = edit(context, path="src/other.py", old_text="x", new_text="y")
    assert result["status"] == "refused" and result["code"] == "file_not_found"
    assert not (space / "src/other.py").exists()


def test_a_binary_file_is_not_edited(space, context):
    (space / "src/blob.bin").write_bytes(b"\xff\xfe\x00a")
    result = edit(context, path="src/blob.bin", old_text="a", new_text="b")
    assert result["status"] == "refused" and result["code"] == "file_not_text"


def test_a_change_made_while_editing_is_never_overwritten(space, context, monkeypatch):
    """تغييرٌ يقع بين قراءة الملف وكتابته: التعديلُ يُحسب من لقطة القفل، فلا يمحوه."""
    concurrent = "def add(a, b):\n    return b + a  # المالك\n"
    original_read, fired = journal_module._read, []

    def racing_read(fd, name, limit):
        if name == "app.py" and not fired:
            fired.append(True)
            (space / "src/app.py").write_text(concurrent, encoding="utf-8")
        return original_read(fd, name, limit)
    monkeypatch.setattr(journal_module, "_read", racing_read)
    result = edit(context, path="src/app.py", old_text="return a - b", new_text="return a + b")
    assert fired and result["status"] == "refused" and result["code"] == "edit_match_missing"
    assert (space / "src/app.py").read_text(encoding="utf-8") == concurrent


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "_BACKENDS", {})
    running = AgentRunning(tmp_path.resolve() / "ui")
    yield running
    running.close()


def test_the_owner_reverts_an_in_place_edit_from_the_ui_path(live):
    ctx = live.agent()
    target = live.app.project(ctx["project"]) / "agent-workspace/notes.txt"
    target.write_text("الموعد يوم الأحد\n", encoding="utf-8")
    live.provider.responses = [
        response("أعدّل", ToolCall("edit1", "edit_file", {"path": "notes.txt", "old_text": "الأحد",
                                                          "new_text": "الإثنين"})),
        response("عُدّل")]
    result = ask(live, ctx)
    (tool,) = result["steps"][0]["tool_results"]
    assert tool["status"] == "ok" and "+الموعد يوم الإثنين" in tool["content"]
    assert target.read_text(encoding="utf-8") == "الموعد يوم الإثنين\n"
    reverted = live.api("agent_revert", **ctx, action_id=tool["action_id"], request=uuid.uuid4().hex)
    assert reverted["status"] in {"ok", "reverted"}
    assert target.read_text(encoding="utf-8") == "الموعد يوم الأحد\n"
