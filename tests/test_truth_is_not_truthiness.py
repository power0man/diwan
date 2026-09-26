"""الصدقُ `True` بعينه لا ما يُقرأ صدقًا (تقييم ٢٥ سبتمبر §٥، متوسط).

النواةُ تفرض `is True` (`core/run.py::_is_local`)، والحلقةُ والجلسةُ كانتا تصفانه ولا يحرسه اختبار: طفرةُ الصدق
(`bool(...)` مكان `is True`) نجت في المواضع الأربعة. فمزوّدٌ يعلن `is_local = 1`، أو فحصُ إيقافٍ يعيد `1`، كان
يُقرأ صدقًا لو ضعف الشرط. وهنا يُثبت كلُّ موضعٍ بقيمةٍ صادقةٍ غيرِ True.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.journal import Journal
from agent.loop import run_agent
from agent.registry import Tool, ToolContext, ToolRegistry
from conversation.agent_session import AgentSession, _ProviderGuard
from conversation.session import ConversationError
from core.budget import Budget
from core.contracts import Response, ToolCall, ToolSpec, Usage
from core.ledger import Ledger


def _say(text="تم", calls=()):
    return Response(text, Usage(1, 1), "complete", 0, tool_calls=tuple(calls))


class Provider:
    is_local, name = True, "truth-fixture"

    def __init__(self, *outputs):
        self.outputs, self.requests, self.estimates = list(outputs), [], 0

    def estimate_micros(self, request):
        self.estimates += 1
        return 0

    def complete(self, request):
        self.requests.append(request)
        return self.outputs.pop(0)


class Truthy(Provider):
    is_local = 1                        # يُقرأ صدقًا، وليس True


def test_a_stop_check_that_returns_a_truthy_value_does_not_stop_the_loop(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = run_agent("قل تم", Provider(_say("تم")), ToolRegistry(), ToolContext(workspace, Journal(workspace)),
                    ledger=Ledger(tmp_path / "ledger.jsonl"), budget=Budget(0, 0), model="m", model_version="v",
                    stop_check=lambda: 1)
    assert run.status == "complete" and run.answer == "تم"


@pytest.fixture
def make(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def write(args, context):
        action = context.journal.write_file(args["path"], args["content"])
        return {"content": "written", "action_id": action.action_id}
    tools = ToolRegistry(Tool(ToolSpec("write", "write", {}, "owner", True), write))

    def create():
        return AgentSession(tmp_path / "control", "session", workspace_root=workspace, project_id="project",
                            registry=tools, model="m", model_version="v")
    return create


def test_a_truthy_is_local_never_starts_a_turn(make):
    provider = Truthy(_say("لا يصل"))
    with pytest.raises(ConversationError, match="policy_requires_local"):
        make().start_turn("t1", "سرّ", provider)
    assert provider.estimates == 0 and not provider.requests


def test_a_truthy_is_local_never_resumes_a_turn(make):
    session = make()
    pending = session.start_turn("t1", "اكتب", Provider(_say(calls=(ToolCall("c1", "write",
                                                                             {"path": "a.txt", "content": "x"}),))))
    assert pending["status"] == "awaiting_owner"
    first = pending["pending"][0]
    session.decide(first["action_id"], first["call_digest"], True, first["revision"])
    truthy = Truthy(_say("لا يصل"))
    with pytest.raises(ConversationError, match="policy_requires_local"):
        make().resume("t1", truthy)
    assert truthy.estimates == 0 and not truthy.requests


def test_the_provider_guard_reads_is_local_as_true_or_nothing():
    assert _ProviderGuard(None, None, None, Truthy()).is_local is False
    assert _ProviderGuard(None, None, None, Provider()).is_local is True
