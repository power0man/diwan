"""الكتابة عبر جسر الأدوات تستخدم مساحة الوكيل ودفتر رجوعه نفسه."""
from __future__ import annotations

import json
import pytest

from agent.journal import Journal, JournalRefused
from agent.registry import ToolContext, ToolRegistry
from core.canonical import PayloadRejected
from core.contracts import ToolCall
from core.tools_registry import default_tools_registry, export_agent_tools


@pytest.fixture
def agent_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    journal = Journal(root)
    context = ToolContext(root, journal, allowed_consents=frozenset({"auto", "logged"}))
    return ToolRegistry(*export_agent_tools()), context


def _write(registry, context, text, path="report.md", call_id="write-1"):
    return registry.invoke(ToolCall(call_id, "write_workspace_document",
                                    {"path": path, "content": text}), context)


def _action_id(result):
    assert result["status"] == "ok", result
    return json.loads(result["content"])["action_id"]


def test_agent_writes_changes_and_reverts_through_the_supplied_journal(agent_workspace, monkeypatch):
    registry, context = agent_workspace
    journal = context.journal
    original_write = journal.write_file
    recorded = []

    def observe_write(path, content):
        recorded.append((path, content))
        return original_write(path, content)

    monkeypatch.setattr(journal, "write_file", observe_write)
    first = _action_id(_write(registry, context, "مسودة أولى"))
    second = _action_id(_write(registry, context, "مسودة منقحة", call_id="write-2"))

    assert recorded == [("report.md", "مسودة أولى"), ("report.md", "مسودة منقحة")]
    assert [action.action_id for action in journal.actions()] == [first, second]
    target = context.root / "report.md"
    assert target.read_text(encoding="utf-8") == "مسودة منقحة"
    assert journal.revert(second)["result"] == "restored"
    assert target.read_text(encoding="utf-8") == "مسودة أولى"
    assert journal.revert(first)["result"] == "removed"
    assert not target.exists()


def test_agent_revert_preserves_a_later_owners_edit(agent_workspace):
    registry, context = agent_workspace
    target = context.root / "report.md"
    target.write_text("أصل المالك", encoding="utf-8")
    action_id = _action_id(_write(registry, context, "اقتراح الوكيل"))
    target.write_text("تعديل لاحق للمالك", encoding="utf-8")

    with pytest.raises(JournalRefused) as exc:
        context.journal.revert(action_id)
    assert exc.value.code == "changed_since_action"
    assert target.read_text(encoding="utf-8") == "تعديل لاحق للمالك"


@pytest.mark.parametrize("path", ["../workspace-other/report.md", "absolute",
                                  ".git/config", ".diwan-journal/journal.jsonl"])
def test_agent_write_refuses_escaping_or_protected_paths_before_effect(agent_workspace, path):
    registry, context = agent_workspace
    if path == "absolute":
        path = str(context.root.parent / "absolute-report.md")
    before = set(context.root.parent.rglob("*"))
    result = _write(registry, context, "لا ينبغي كتابته", path=path)
    assert result["status"] == "refused"
    assert set(context.root.parent.rglob("*")) == before
    assert context.journal.actions() == []


@pytest.mark.parametrize("suffix", ["report.md", "new/nested/report.md"])
def test_agent_write_cannot_cross_a_symlinked_parent(agent_workspace, tmp_path, suffix):
    registry, context = agent_workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (context.root / "link").symlink_to(outside, target_is_directory=True)
    result = _write(registry, context, "لا ينبغي كتابته", path=f"link/{suffix}")
    assert result["status"] == "refused"
    assert result["code"] == "path_escapes_root"
    assert list(outside.rglob("*")) == []
    assert context.journal.actions() == []


@pytest.mark.parametrize("context", [None, {}, {"root": None}, {"workspace_root": None},
                                    {"root": ""}, {"workspace_root": "relative"}])
def test_missing_or_relative_workspace_never_falls_back_to_cwd(tmp_path, monkeypatch, context):
    monkeypatch.chdir(tmp_path)
    _, handlers = default_tools_registry()
    with pytest.raises(PayloadRejected) as exc:
        handlers["write_workspace_document"]({"path": "report.md", "content": "نص"}, context)
    assert exc.value.code in {"workspace_context_missing", "workspace_context_invalid"}
    assert list(tmp_path.rglob("*")) == []


def test_agent_write_refuses_a_missing_journal(agent_workspace):
    registry, original = agent_workspace
    context = ToolContext(original.root, None, allowed_consents=frozenset({"logged"}))
    result = _write(registry, context, "نص")
    assert result["status"] == "refused"
    assert result["code"] == "workspace_journal_missing"
    assert list(original.root.rglob("*")) == []


def test_agent_write_refuses_a_missing_root(agent_workspace):
    registry, original = agent_workspace
    context = ToolContext(None, original.journal, allowed_consents=frozenset({"logged"}))
    result = _write(registry, context, "نص")
    assert result["status"] == "refused"
    assert result["code"] == "workspace_context_missing"
    assert original.journal.actions() == []


def test_agent_write_refuses_a_journal_from_another_workspace(agent_workspace, tmp_path):
    registry, original = agent_workspace
    context = ToolContext(original.root, Journal(tmp_path / "other"),
                          allowed_consents=frozenset({"logged"}))
    result = _write(registry, context, "نص")
    assert result["status"] == "refused"
    assert result["code"] == "workspace_context_mismatch"
    assert list(original.root.rglob("*")) == []
    assert not (tmp_path / "other").exists()


@pytest.mark.parametrize("root_key", ["root", "workspace_root"])
def test_explicit_legacy_root_still_writes_and_reverts(tmp_path, root_key):
    _, handlers = default_tools_registry()
    result = handlers["write_workspace_document"](
        {"path": "report.md", "content": "نص"}, {root_key: tmp_path})
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "نص"
    assert Journal(tmp_path).revert(result["action_id"])["result"] == "removed"


def test_conflicting_legacy_root_aliases_are_refused(tmp_path):
    _, handlers = default_tools_registry()
    with pytest.raises(PayloadRejected) as exc:
        handlers["write_workspace_document"](
            {"path": "report.md", "content": "نص"},
            {"root": tmp_path, "workspace_root": tmp_path / "other"})
    assert exc.value.code == "workspace_context_mismatch"
    assert list(tmp_path.rglob("*")) == []
