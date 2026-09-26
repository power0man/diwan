"""ك٥٥: الذاكرةُ المحكومة موصولةٌ بالجلسة والواجهة، والبنكُ المجمَّد يمرّ عبر الطريق الموصول.

الحارسُ الأول يشغّل بنك ك٤٨ نفسَه عبر `LocalApp.dispatch` (`evaluation/memory_runner.py`، `driver="wired"`)؛
وما بعده يثبت ما لا يقيسه البنك: أنّ عنصرًا نُسي لا يعود من تاريخ الجلسة، وأنّ الإيصالَ يعدّ الجولاتِ
التي رأته، وأنّ كتلةً عُدّلت بعد الجولة تُرفض.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from conversation.session import ConversationError
from core.canonical import canonical_bytes, digest
from evaluation.memory_runner import _Wired, _block_of, run_memory_bank
from memory.store import HEADER

ROOT = Path(__file__).resolve().parents[1]
BANK = json.loads((ROOT / "evaluation" / "suites" / "memory_v1.json").read_text(encoding="utf-8"))


@pytest.fixture
def wired(tmp_path):
    running = _Wired(tmp_path.resolve() / "ui")
    try:
        yield running
    finally:
        running.close()


def _ask(wired, name, kind, message):
    ids = wired.project(name)
    before = len(wired.provider.requests)
    result = wired.api("agent_ask" if kind == "agent" else "ask", project=ids["id"], session=wired.session(name, kind),
                       turn=uuid.uuid4().hex, message=message, files=[])
    (request,) = wired.provider.requests[before:]
    return result, request


def test_the_frozen_bank_passes_through_the_wired_path():
    report = run_memory_bank(BANK, driver="wired")
    failed = {r["id"]: r["failures"] for r in report["results"] if not r["passed"]}
    assert failed == {}
    assert report["driver"] == "wired" and report["passed"] == report["total"] == 30
    assert report["metrics"] == {"forget_rate": 1.0, "leakage": 0, "consent_violations": 0,
                                 "injection_unquarantined": 0}
    assert report["meets_thresholds"] is True


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_a_forgotten_item_never_returns_through_session_history(wired, kind):
    project = wired.project("A")["id"]
    item = wired.api("memory_remember", project=project, text="رمز البوابة ٤٤٢١")["item_id"]
    first, seen = _ask(wired, "A", kind, "ما رمز البوابة؟")
    assert "٤٤٢١" in _block_of(seen.messages[-1].content)
    assert first.get("memory_items") == 1
    wired.api("memory_forget", project=project, item_id=item)
    _, later = _ask(wired, "A", kind, "ذكّرني بالرمز")
    # الجولةُ السابقة في التاريخ بلا كتلتها، والكتلةُ الجديدة غائبة
    assert all(not m.content.startswith(HEADER) for m in later.messages)
    assert "٤٤٢١" not in "".join(m.content for m in later.messages)
    assert len(later.messages) > len(seen.messages)


def test_the_forget_receipt_names_every_turn_that_saw_the_item(wired):
    ids = wired.project("A")
    item = wired.api("memory_remember", project=ids["id"], text="موعد التسليم الثلاثاء")["item_id"]
    agent, _ = _ask(wired, "A", "agent", "متى التسليم؟")
    text, _ = _ask(wired, "A", "text", "متى التسليم؟")
    other = wired.api("memory_remember", project=ids["id"], text="لون الشعار أزرق")["item_id"]
    out = wired.api("memory_forget", project=ids["id"], item_id=item)
    assert out["receipt"]["references"] == sorted([f"agent:{ids['agent']}/{agent['turn_id']}",
                                                   f"text:{ids['text']}/{text['turn_id']}"])
    assert "الثلاثاء" not in json.dumps(out, ensure_ascii=False)
    # النسيانُ الثاني يعيد الإيصالَ نفسَه، والعنصرُ الآخر لم يُمسّ
    again = wired.api("memory_forget", project=ids["id"], item_id=item)
    assert again["receipt"] == out["receipt"] and again["replayed"] is True
    assert [i["item_id"] for i in wired.api("memory", project=ids["id"])["items"]] == [other]


def test_a_model_proposal_waits_for_the_owner_and_a_denial_saves_nothing(wired):
    project = wired.project("A")["id"]
    denied = wired.propose("A", "عنوان المستودع الجديد")
    assert denied["action"]["consent"] == "owner"
    assert wired.api("memory", project=project)["items"] == []
    assert wired.decide(denied, approve=False) is None
    assert wired.api("memory", project=project)["items"] == []
    approved = wired.decide(wired.propose("A", "عنوان المستودع الجديد"), approve=True)
    (item,) = wired.api("memory", project=project)["items"]
    assert item["item_id"] == approved and item["source"] == {"via": "propose_memory"}


def test_no_memory_directory_exists_until_the_owner_saves(wired):
    _, request = _ask(wired, "A", "agent", "مرحبًا")
    project_dir = wired.app.project(wired.project("A")["id"])
    assert not (project_dir / "memory").exists()
    assert not request.messages[-1].content.startswith(HEADER)
    assert wired.api("memory", project=wired.project("A")["id"]) == {"items": [], "receipts": []}


def test_the_owner_ui_refuses_a_malformed_source_by_name(wired):
    from webui.server import UIError
    project = wired.project("A")["id"]
    with pytest.raises(UIError) as err:
        wired.api("memory_remember", project=project, text="نص", source={"session": "../x"})
    assert err.value.code == "id_invalid"
    with pytest.raises(UIError) as err:
        wired.api("memory_remember", project=project, text="نص", source={"via": "model"})
    assert err.value.code == "memory_source_invalid"
    with pytest.raises(UIError) as err:
        wired.api("memory_remember", project=project, text="نص", consent="model")
    assert err.value.code == "request_invalid"


def _rewrite_state(path: Path, change):
    envelope = json.loads(path.read_text(encoding="utf-8"))
    change(envelope["state"])
    envelope["sha256"] = digest(envelope["state"])
    path.write_bytes(canonical_bytes(envelope))


def test_a_memory_block_changed_after_the_turn_is_refused(wired):
    ids = wired.project("A")
    wired.api("memory_remember", project=ids["id"], text="اسم المورد الأساسي نور")
    _ask(wired, "A", "agent", "من المورد؟")
    state = wired.app.project(ids["id"]) / "agent-control" / ids["agent"] / "state.json"

    def swap(value):
        memory = value["turns"][-1]["memory"]
        memory["block"] = memory["block"].replace("نور", "سهى")
    _rewrite_state(state, swap)
    with pytest.raises(ConversationError) as err:
        wired.app.agent_session(wired.app.project(ids["id"]), ids["agent"]).history()
    assert err.value.code == "state_corrupt"


def test_a_session_that_declared_web_search_reopens(tmp_path):
    """جلسةٌ أعلنت البحث كانت تُرفض عند إعادة فتحها (`agent_tool_contract_changed`)."""
    from tests.test_web_search_tool import FakeBackend
    from webui.server import LocalApp
    app = LocalApp(tmp_path.resolve() / "ui", model="m", model_version="0" * 64,
                   provider_factory=lambda: None, agent_provider_factory=lambda: None,
                   web_search=FakeBackend())
    try:
        project = app.dispatch({"action": "create_project", "name": "مشروع"})["id"]
        session = app.dispatch({"action": "create_session", "project": project, "name": "س",
                                "mode": "agent"})["id"]
        reopened = app.agent_session(app.project(project), session)
        assert "web_search" in {spec["name"] for spec in reopened.config["tools"]}
    finally:
        app.close()


def test_admission_counts_the_memory_block_before_attachments_are_staged(tmp_path):
    """`validate_turn` (قبل نسخ المرفقات) يحسب الكتلة، فلا يرفض `start_turn` بحدّ السياق بعد النسخ."""
    from agent.registry import ToolRegistry
    from conversation.agent_session import AgentSession, _messages, _user_message
    from memory.store import MemoryStore
    project, workspace = tmp_path / "project", tmp_path / "workspace"
    project.mkdir(), workspace.mkdir()
    store = MemoryStore(project)
    store.remember("س" * 1900, consent="owner")

    def session(name, limit, memory):
        return AgentSession(tmp_path / "control", name, workspace_root=workspace, project_id="p",
                            registry=ToolRegistry(), model="m", model_version="0" * 64,
                            max_context_chars=limit, memory=memory)
    probe = session("probe", 200000, None)
    plain = probe._request(_messages([{"role": "system", "content": probe.config["system"]}])
                           + (_user_message("سؤال"),))
    size = len(canonical_bytes(plain.fingerprint_payload()).decode("utf-8"))
    assert session("plain", size + 10, None).validate_turn(uuid.uuid4().hex, "سؤال") == {"status": "ready"}
    with pytest.raises(ConversationError) as err:
        session("remembering", size + 10, store).validate_turn(uuid.uuid4().hex, "سؤال")
    assert err.value.code == "context_limit"


class _Delegate:
    """مزوّدٌ حيٌّ مصطنع: يعدّ نداءاته ويجيب بلا ذاكرة."""
    name, is_local = "fake-live", True

    def __init__(self):
        self.calls = 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        from core.contracts import Response, Usage
        self.calls += 1
        return Response("حسنًا.", Usage(1, 1), "complete", 0, provider=self.name, model_version="1" * 64)


def test_the_live_driver_sends_every_unscripted_turn_to_the_real_provider():
    delegate = _Delegate()
    report = run_memory_bank(BANK, driver="live", delegate=delegate)
    assert report["driver"] == "live" and report["provider"] == "fake-live"
    assert report["passed"] == report["total"] == 30 and report["meets_thresholds"]
    assert delegate.calls > 0, "الطريقُ الحيّ لم يبلغ المزوّدَ الحقيقيّ"


def test_live_needs_a_provider_and_the_others_refuse_one():
    import pytest
    with pytest.raises(ValueError):
        run_memory_bank(BANK, driver="live")
    with pytest.raises(ValueError):
        run_memory_bank(BANK, driver="wired", delegate=_Delegate())



class _ProposingDelegate(_Delegate):
    """نموذجٌ حيٌّ يطلب propose_memory من تلقاء نفسه في كل جولةٍ وكيلة، فتقف الجولةُ تنتظر المالك."""
    name = "proposing-live"

    def complete(self, request):
        from core.contracts import Response, ToolCall, Usage
        self.calls += 1
        if request.tools:
            call = ToolCall("call_" + uuid.uuid4().hex[:8], "propose_memory", {"text": "ملاحظة"})
            return Response("", Usage(1, 1), "complete", 0, provider=self.name, model_version="1" * 64,
                            tool_calls=(call,))
        return Response("حسنًا.", Usage(1, 1), "complete", 0, provider=self.name, model_version="1" * 64)


def test_a_tool_the_live_model_asks_for_does_not_leave_a_turn_that_fails_the_next_probe():
    """ملاحظةُ Codex على #129: جولةٌ وكيلة تقف awaiting_owner كانت تُسقط فحصَ السياق التالي بـturn_unresolved."""
    report = run_memory_bank(BANK, driver="live", delegate=_ProposingDelegate())
    assert report["passed"] == report["total"] == 30, [r for r in report["results"] if not r["passed"]][:2]


def test_the_cli_validates_the_suite_and_names_the_runner_before_any_call(tmp_path, monkeypatch, capsys):
    """ملاحظتا Codex على #129: بنكٌ غير مفحوص كان يمرّ ١٠٠٪، والمعرّفُ كان مكتوبًا في الأداة."""
    import json
    import tools.evaluate_memory as cli
    monkeypatch.setattr(cli, "OllamaProvider", lambda **_: (_ for _ in ()).throw(AssertionError("نداءٌ قبل الفحص")))
    bad = tmp_path / "bank.json"
    broken = json.loads(json.dumps(BANK))
    broken["scenarios"][0]["steps"] = [{"project": broken["scenarios"][0]["steps"][0]["project"], "expect": "typo"}]
    bad.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["--model", "m", "--suite", str(bad), "--agent", "anthropic/claude-opus-5-5",
                     "--out", str(tmp_path / "r.json")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    with pytest.raises(SystemExit):
        cli.main(["--model", "m", "--agent", "someone/unknown", "--out", str(tmp_path / "r.json")])
