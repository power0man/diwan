"""الحلقة: يفعل ← يرى الأثر ← يصحّح (الحلقة — الشريحة ٣).

المزوّدُ مكتوبٌ سلفًا، لا شبكةَ ولا نموذج: تُقاس الحلقةُ نفسُها لا النموذج.
"""
from __future__ import annotations

import json

import pytest

from core.budget import Budget, BudgetRefused
from core.contracts import Response, ToolCall, ToolSpec, Usage
from core.ledger import Ledger
from agent.builtin_tools import DEFAULT_TOOLS
from agent.journal import Journal
from agent.actions import ActionStore
from agent.loop import MAX_STEPS, Run, run_agent
from agent.registry import Tool, ToolContext, ToolRegistry


class Scripted:
    name = "scripted"
    is_local = True

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.requests.append(request)
        if not self.queue:
            raise AssertionError("نودي المزوّد أكثر مما كُتب له")
        return self.queue.pop(0)


def says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="scripted",
                    model_version="v1", tool_calls=tuple(calls))


@pytest.fixture
def space(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return workspace


@pytest.fixture
def full(space):
    return ToolContext(root=space, journal=Journal(space),
                       allowed_consents=frozenset({"auto", "logged"}))


def drive(provider, context, space, **kw):
    return run_agent(kw.pop("task", "أصلح الخطأ في app.py"), provider,
                     kw.pop("registry", ToolRegistry(*DEFAULT_TOOLS)), context,
                     ledger=Ledger(space / "ledger.jsonl"), budget=Budget(0, 0),
                     action_store=ActionStore(space.parent / "actions", space),
                     session_id="fixture-session", turn_id="fixture-turn",
                     model="fixture", model_version="v1", **kw)


def owner_registry():
    def effect(arguments, context):
        (context.root / "ran.txt").write_text("executed")
        return {"content": "done"}
    return ToolRegistry(*DEFAULT_TOOLS, Tool(ToolSpec("owner_action", "أثر مصطنع يحتاج قرارًا", {}, consent="owner"), effect))


# ————— المخرجُ الأول: أنجز —————

def test_the_loop_reads_then_fixes_then_answers(space, full):
    provider = Scripted(
        says("سأقرأ الملف", ToolCall("c1", "read_file", {"path": "app.py"})),
        says("العيبُ في العلامة", ToolCall("c2", "write_file",
             {"path": "app.py", "content": "def add(a, b):\n    return a + b\n"})),
        says("أُصلح العيب: كان يطرح بدل أن يجمع."))
    run = drive(provider, full, space)
    assert run.status == "complete" and run.code is None
    assert run.answer == "أُصلح العيب: كان يطرح بدل أن يجمع."
    assert len(run.steps) == 3
    # الأثرُ وقع فعلًا — لا وصفًا له
    assert (space / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_the_tool_result_reaches_the_model_verbatim(space, full):
    provider = Scripted(says("اقرأ", ToolCall("c1", "read_file", {"path": "app.py"})),
                        says("انتهيت"))
    drive(provider, full, space)
    second = provider.requests[1]
    tool_messages = [m for m in second.messages if m.role == "tool"]
    assistant = [m for m in second.messages if m.role == "assistant"]
    assert assistant[0].tool_calls == (ToolCall("c1", "read_file", {"path": "app.py"}),)
    assert len(tool_messages) == 1 and tool_messages[0].tool_call_id == "c1"
    payload = json.loads(tool_messages[0].content)
    assert payload["status"] == "ok" and "return a - b" in payload["content"]


def test_a_refusal_goes_back_to_the_model_and_the_loop_continues(space, full):
    """الرفضُ تعليمٌ لا نهاية: النموذج يصحّح نداءه."""
    provider = Scripted(
        says("سأقرأ", ToolCall("c1", "read_file", {"path": "ghost.txt"})),
        says("أعيد بالمسار الصحيح", ToolCall("c2", "read_file", {"path": "app.py"})),
        says("تمّ"))
    run = drive(provider, full, space)
    assert run.status == "complete"
    assert run.steps[0].tool_results[0]["status"] == "refused"
    assert run.steps[0].tool_results[0]["code"] == "file_not_found"
    payload = json.loads([m for m in provider.requests[1].messages
                          if m.role == "tool"][0].content)
    assert payload["code"] == "file_not_found"


# ————— المخرجُ الثاني: ينتظر إذنك —————

def test_an_owner_grade_call_halts_the_loop_without_running_it(space, full):
    marker = space / "ran.txt"
    provider = Scripted(says("سأشغّل أمرًا",
                             ToolCall("c1", "owner_action", {})),
                        says("لن يُنادى"))
    run = drive(provider, full, space, registry=owner_registry())
    assert run.status == "awaiting_owner" and run.code == "consent_required"
    assert [p["name"] for p in run.pending] == ["owner_action"]
    assert not marker.exists()
    assert len(provider.requests) == 1, "لا يُستأنف الاستدلالُ على نتيجةٍ لم تقع"


def test_work_done_before_the_pause_is_kept_and_reported(space, full):
    """ما نُفِّذ يُقيَّد، وما انتظر يُعلَن — فلا يضيع عملٌ ولا يُدَّعى ما لم يقع."""
    provider = Scripted(says("اثنان معًا",
                             ToolCall("c1", "read_file", {"path": "app.py"}),
                             ToolCall("c2", "owner_action", {})))
    run = drive(provider, full, space, registry=owner_registry())
    assert run.status == "awaiting_owner"
    statuses = {r["name"]: r["status"] for r in run.steps[0].tool_results}
    assert statuses == {"read_file": "ok", "owner_action": "awaiting_owner"}


def test_a_charter_of_auto_alone_pauses_on_writing(space):
    read_only = ToolContext(root=space, journal=Journal(space))
    provider = Scripted(says("سأكتب", ToolCall("c1", "write_file",
                                               {"path": "new.txt", "content": "x"})))
    run = drive(provider, read_only, space)
    assert run.status == "awaiting_owner"
    assert not (space / "new.txt").exists()


# ————— المخرجُ الثالث: عجزٌ مُعلَن —————

def test_the_step_limit_is_neither_success_nor_failure(space, full):
    provider = Scripted(*[says(f"خطوة {i}", ToolCall(f"c{i}", "read_file",
                                                     {"path": "app.py"}))
                          for i in range(3)])
    run = drive(provider, full, space, max_steps=3)
    assert run.status == "step_limit" and run.code == "max_steps_exhausted"
    assert len(run.steps) == 3
    assert run.answer == "خطوة 2", "يُعرض ما أُنجز، لا فراغ"


def test_a_provider_failure_is_reported_not_swallowed(space, full):
    class Broken(Scripted):
        def complete(self, request):
            from providers.base import ProviderError
            raise ProviderError("provider_down", "المزوّد لا يستجيب", retryable=False)
    run = drive(Broken(), full, space)
    assert run.status == "failed" and run.code == "provider_down"


def test_a_budget_refusal_stops_the_loop_by_name(space, full):
    provider = Scripted(says("لن يُنادى"))
    run = run_agent("مهمّة", provider, ToolRegistry(*DEFAULT_TOOLS), full,
                    ledger=Ledger(space / "l.jsonl"), budget=Budget(0, 0, kill_switch=True),
                    model="fixture", model_version="v1")
    assert run.status == "refused" and run.code == "kill_switch"
    assert provider.requests == []


# ————— ما تكسبه الحلقةُ من النواة —————

def test_every_step_is_recorded_in_the_governed_ledger(space, full):
    provider = Scripted(says("اقرأ", ToolCall("c1", "read_file", {"path": "app.py"})),
                        says("تمّ"))
    run = drive(provider, full, space)
    ledger = Ledger(space / "ledger.jsonl")
    records = [e["record"] for e in ledger.entries() if e["record"]["kind"] == "ok"]
    assert len(records) == 2 == len(run.steps)
    assert records[0]["response"]["tool_calls"][0]["name"] == "read_file"
    assert [s.ledger_digest for s in run.steps] == [e["digest"] for e in ledger.entries()]


def test_rerunning_the_same_history_replays_without_paying_twice(space, full):
    def script():
        return Scripted(says("اقرأ", ToolCall("c1", "read_file", {"path": "app.py"})),
                        says("تمّ"))
    ledger = Ledger(space / "shared.jsonl")
    common = dict(registry=ToolRegistry(*DEFAULT_TOOLS), context=full, ledger=ledger,
                  action_store=ActionStore(space.parent / "actions", space),
                  session_id="fixture-session", turn_id="fixture-turn",
                  budget=Budget(0, 0), model="fixture", model_version="v1",
                  idempotency_prefix="run-a")
    first_provider, second_provider = script(), script()
    first = run_agent("مهمّة", first_provider, **common)
    second = run_agent("مهمّة", second_provider, **common)
    assert first.status == second.status == "complete"
    assert [s.replayed for s in first.steps] == [False, False]
    assert [s.replayed for s in second.steps] == [True, True]
    assert second_provider.requests == [], "لم يُنادَ النموذج مرّةً ثانية"
    assert second.answer == first.answer


def test_a_different_history_takes_a_different_key_and_never_conflicts(space, full):
    ledger = Ledger(space / "shared.jsonl")
    common = dict(registry=ToolRegistry(*DEFAULT_TOOLS), context=full, ledger=ledger,
                  budget=Budget(0, 0), model="fixture", model_version="v1",
                  idempotency_prefix="run-a")
    run_agent("مهمّة أولى", Scripted(says("تمّ")), **common)
    other = run_agent("مهمّة ثانية", Scripted(says("تمّ أيضًا")), **common)
    assert other.status == "complete" and other.steps[0].replayed is False


# ————— الخاصيّةُ التي تشتري الاستقلال —————

def test_everything_the_loop_wrote_can_be_reverted(space, full):
    before = (space / "app.py").read_bytes()
    provider = Scripted(
        says("أكتب", ToolCall("c1", "write_file",
             {"path": "app.py", "content": "def add(a, b):\n    return a + b\n"})),
        says("وأضيف ملفًّا", ToolCall("c2", "write_file",
             {"path": "notes.md", "content": "ملاحظة"})),
        says("تمّ"))
    run = drive(provider, full, space)
    assert run.status == "complete"
    journal = Journal(space)
    actions = journal.actions()
    assert [a.path for a in actions] == ["app.py", "notes.md"]
    for action in reversed(actions):
        journal.revert(action.action_id)
    assert (space / "app.py").read_bytes() == before
    assert not (space / "notes.md").exists()


def test_the_declared_tools_reach_the_model_with_their_consent_grades(space, full):
    provider = Scripted(says("تمّ"))
    drive(provider, full, space)
    declared = {t.name: t.consent for t in provider.requests[0].tools}
    assert declared == {"read_file": "auto", "search_files": "auto", "list_files": "auto",
                        "run_tests": "auto", "write_file": "logged", "edit_file": "logged",
                        "run_command": "owner"}
