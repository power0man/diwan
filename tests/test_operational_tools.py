"""اختبارات الأدوات التشغيلية الحقيقية (Operational Tools): التحقق الفعلي من الأدوات وحمايتها.

الفحوصات:
1. أداة search_regulations تسترجع اللوائح والتعريفات البحرية الفعلية.
2. أداة analyze_arabic_morphology تعيد الجذر والوزن الصحيحين.
3. أداة write_workspace_document تكتب داخل مساحة العمل وترفض الهروب وتتيح التراجع.
4. أداة execute_isolated_command تتوقف بلا إذن المالك أو منفذ حاوية موثوق؛ إعلان البيئة لا يرخّص التنفيذ.
"""
from __future__ import annotations

import os
from pathlib import Path
import pathlib
import pytest

from agent.actions import ActionStore
from agent.journal import Journal
from agent.registry import ToolContext
from core.action_loop import ActionControl, GovernedActionLoop
from core.canonical import PayloadRejected
from core.contracts import ToolCall
from core.ledger import Ledger
from core.tools_registry import create_default_action_loop, default_tools_registry
from tests.private_stores import needs_corpus

ROOT = Path(__file__).resolve().parent.parent


@needs_corpus
def test_search_regulations_tool_returns_real_results():
    loop = create_default_action_loop()
    call = ToolCall(call_id="call-search", name="search_regulations", arguments={"query": "مياه الصابورة", "limit": 3})
    res = loop.dispatch_call(call)
    assert res.success is True
    assert res.consent_grade == "auto"
    assert isinstance(res.output, list)
    assert len(res.output) > 0
    assert "doc_id" in res.output[0]


def test_analyze_arabic_morphology_tool():
    loop = create_default_action_loop()
    call = ToolCall(call_id="call-morph", name="analyze_arabic_morphology", arguments={"word": "المستكشفون"})
    res = loop.dispatch_call(call)
    assert res.success is True
    assert res.output["root"] == "كشف"
    assert res.output["pattern"] == "مستفعل"


def test_write_workspace_document_writes_and_reverts(tmp_path: Path):
    ledger = Ledger(tmp_path / "doc_ledger.jsonl")
    loop = create_default_action_loop(ledger=ledger)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_rel = "reports/summary.txt"

    control = ActionControl(ActionStore(tmp_path / "control", workspace),
        ToolContext(workspace, Journal(workspace), frozenset({"auto", "logged"})), "test", "write")

    call = ToolCall(
        call_id="call-write",
        name="write_workspace_document",
        arguments={"path": target_rel, "content": "مسودة تقرير ديوان الحوكمي"},
    )

    res = loop.dispatch_call(call, control=control)
    assert res.success is True
    assert res.consent_grade == "logged"
    assert (workspace / target_rel).read_text(encoding="utf-8") == "مسودة تقرير ديوان الحوكمي"

    # The old callback is not a restoration authority.
    reverted = loop.rollback(res, control=control, request_id="undo-write")
    assert reverted is True
    assert not (workspace / target_rel).exists()


def test_write_workspace_document_blocks_directory_escape(tmp_path: Path):
    loop = create_default_action_loop()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    control = ActionControl(ActionStore(tmp_path / "control", workspace),
        ToolContext(workspace, Journal(workspace), frozenset({"auto", "logged"})), "test", "escape")

    call = ToolCall(
        call_id="call-escape",
        name="write_workspace_document",
        arguments={"path": "../../etc/shadow", "content": "اختراق"},
    )
    res = loop.dispatch_call(call, control=control)
    assert res.success is False
    # يُؤكَّد على الرمز وعلى **انعدام الأثر**، لا على نصِّ الرسالة: الرسالةُ
    # تغيّرت حين صار الحرسُ أضيق (دفترُ الرجوع بـ`_relative`)، والسلوكُ
    # المقصود هو الردُّ قبل وقوع الأثر.
    assert "path_invalid" in res.error or "path_escape" in res.error
    assert not (tmp_path / "etc").exists() and not pathlib.Path("/etc/shadow-probe").exists()
    assert sorted(p.name for p in workspace.rglob("*")) == []


def test_execute_isolated_command_halts_without_owner_approval():
    loop = create_default_action_loop()
    call = ToolCall(call_id="call-cmd-1", name="execute_isolated_command", arguments={"command": "echo test"})
    res = loop.dispatch_call(call, owner_approved=False)
    assert res.success is False
    assert res.pending_owner is False
    assert res.error == "action_control_required"


def test_execute_isolated_command_refuses_without_a_configured_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("DIWAN_DISPOSABLE_HOST", raising=False)
    loop = create_default_action_loop()
    call = ToolCall(call_id="call-cmd-2", name="execute_isolated_command", arguments={"command": "echo test"})
    workspace = tmp_path / "work"
    workspace.mkdir()
    control = ActionControl(ActionStore(tmp_path / "control", workspace),
        ToolContext(workspace, Journal(workspace)), "test", "command")
    res = loop.dispatch_call(call, owner_approved=True, control=control)
    assert res.success is False
    assert "execution_backend_unavailable" in res.error


def test_execute_isolated_command_declaration_and_approval_do_not_create_a_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "test-ephemeral-sandbox")
    loop = create_default_action_loop()
    call = ToolCall(call_id="call-cmd-3", name="execute_isolated_command", arguments={"command": "echo 'مرحبا من ديوان'"})
    workspace = tmp_path / "work"
    workspace.mkdir()
    control = ActionControl(ActionStore(tmp_path / "control", workspace),
        ToolContext(workspace, Journal(workspace)), "test", "command")
    res = loop.dispatch_call(call, owner_approved=True, control=control)
    assert res.success is False
    assert "execution_backend_unavailable" in res.error


def test_export_agent_tools_with_tool_registry(tmp_path: Path):
    from agent.journal import Journal
    from agent.registry import ToolContext, ToolRegistry
    from core.tools_registry import export_agent_tools

    agent_tools = export_agent_tools()
    assert len(agent_tools) == 6
    names = {t.spec.name for t in agent_tools}
    assert "search_regulations" in names
    assert "analyze_arabic_morphology" in names
    assert "evaluate_governance" in names
    assert "check_mlx_hardware" in names

    ctx = ToolContext(root=tmp_path, journal=Journal(tmp_path), allowed_consents=frozenset({"auto", "logged"}))
    reg = ToolRegistry(*agent_tools)

    res = reg.invoke(ToolCall("c-agent", "analyze_arabic_morphology", {"word": "المستكشفون"}), ctx)
    assert res["status"] == "ok"
    assert "كشف" in res["content"]
    assert "مستفعل" in res["content"]


def test_exported_write_preserves_journal_identity_in_durable_receipt_and_reverts(tmp_path):
    import json
    from agent.action_revert import revert_prepared
    from agent.registry import ToolRegistry
    from core.canonical import digest
    from core.tools_registry import export_agent_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "document.md"
    target.write_text("owner original")
    context = ToolContext(workspace, Journal(workspace), frozenset({"auto", "logged"}))
    store = ActionStore(tmp_path / "control", workspace)
    registry = ToolRegistry(*export_agent_tools())
    call = ToolCall("write-1", "write_workspace_document",
                    {"path": "document.md", "content": "agent draft"})
    position = dict(session_id="session", turn_id="turn", step_index=0,
                    request_digest=digest(call.declared()))
    store.register_step(**position, calls=(call,), specs=registry.specs())
    result = registry.invoke_prepared(call, context, store=store, **position, call_index=0)
    assert result["status"] == "ok"
    assert result["action_id"].startswith("action-")
    assert result["journal_action_id"] == json.loads(result["content"])["action_id"]
    assert store.completed_result(result["action_id"])["journal_action_id"] == result["journal_action_id"]
    assert target.read_text() == "agent draft"
    undo = revert_prepared(store, context, result["action_id"], session_id="session", request_id="undo-1")
    assert undo["status"] == "ok" and json.loads(undo["content"])["result"] == "restored"
    assert target.read_text() == "owner original"
    target.write_text("later owner edit")
    assert revert_prepared(store, context, result["action_id"], session_id="session", request_id="undo-1") == undo
    assert target.read_text() == "later owner edit"
