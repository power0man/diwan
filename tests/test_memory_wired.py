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

from conversation.agent_session import _scrub_agent_turns
from conversation.session import ConversationError, FORGOTTEN
from core.canonical import canonical_bytes, digest
from evaluation.memory_runner import _Wired, _block_of, run_memory_bank
from memory.store import HEADER, MemoryStore
from webui.server import UIError
from workspace_tools.backup import BackupError

ROOT = Path(__file__).resolve().parents[1]
BANK = json.loads((ROOT / "evaluation" / "suites" / "memory_v1.json").read_text(encoding="utf-8"))


class _ScrubEchoingDelegate:
    """Deterministic local provider that repeats the sensitive value in its own answer."""
    name, is_local = "echoing-memory", True

    def __init__(self, value, response=None):
        self.trigger, self.value, self.requests = value, value if response is None else response, []

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        from core.contracts import Response, Usage
        self.requests.append(request)
        return Response(self.value if any(self.trigger in m.content for m in request.messages) else "لا أتذكره.",
                        Usage(1, 1), "complete", 0, provider=self.name, model_version="0" * 64)


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


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_pr179_model_echo_is_removed_from_reused_session_history(wired, kind):
    value = "رمز الخزنة ٨١٩٣"
    delegate = _ScrubEchoingDelegate(value)
    wired.provider = delegate
    project = wired.project("echo")["id"]
    item = wired.api("memory_remember", project=project, text=value)["item_id"]
    first, _ = _ask(wired, "echo", kind, "ما رمز الخزنة؟")
    assert first["content"] == value
    session_id = wired.project("echo")[kind]
    project_root = wired.app.project(project)
    ledger = (project_root / "agent-control" / session_id / "calls.jsonl" if kind == "agent" else
              project_root / "sessions" / session_id / "chat" / session_id / "calls.jsonl")
    sealed_before = ledger.read_bytes()

    forgotten = wired.api("memory_forget", project=project, item_id=item)
    session_key = f"{kind}:{session_id}"
    assert forgotten["receipt"]["scrubbed"][session_key] >= 2
    assert ledger.read_bytes() == sealed_before
    _, later = _ask(wired, "echo", kind, "هل تتذكر الرمز؟")
    assert value not in "".join(message.content for message in later.messages)
    assert later.messages[-2].content == "‹نُسي›"


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_pr179_owner_remembered_source_turn_is_scrubbed_without_a_memory_hash(wired, kind):
    value = "رمز المصدر ٤٢٠٧"
    wired.provider = _ScrubEchoingDelegate(value)
    ids = wired.project("source")
    first, _ = _ask(wired, "source", kind, f"ردّد {value}")
    session_id = wired.project("source")[kind]
    item = wired.api("memory_remember", project=ids["id"], text=value,
                     source={"session": session_id, "turn": first["turn_id"]})["item_id"]

    forgotten = wired.api("memory_forget", project=ids["id"], item_id=item)
    assert forgotten["receipt"]["references"] == []
    assert forgotten["receipt"]["scrubbed"][f"{kind}:{session_id}"] >= 2
    _, later = _ask(wired, "source", kind, "ماذا قلت؟")
    assert value not in "".join(message.content for message in later.messages)


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_pr179_json_escaped_echoes_are_removed(wired, kind):
    value = 'رمز "زيتون"\nسطر'
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    wired.provider = _ScrubEchoingDelegate(value, escaped)
    project = wired.project(f"escaped-{kind}-{len(value)}")["id"]
    item = wired.api("memory_remember", project=project, text=value)["item_id"]
    _ask(wired, f"escaped-{kind}-{len(value)}", kind, "ما الرمز؟")
    wired.api("memory_forget", project=project, item_id=item)
    _, later = _ask(wired, f"escaped-{kind}-{len(value)}", kind, "هل تتذكر؟")
    joined = "".join(message.content for message in later.messages)
    assert value not in joined
    assert escaped not in joined


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_pr179_marker_colliding_echoes_are_removed(wired, kind):
    value = FORGOTTEN
    wired.provider = _ScrubEchoingDelegate(value)
    project = wired.project(f"marker-{kind}")["id"]
    item = wired.api("memory_remember", project=project, text=value)["item_id"]
    _ask(wired, f"marker-{kind}", kind, "ما العلامة؟")
    wired.api("memory_forget", project=project, item_id=item)
    _, later = _ask(wired, f"marker-{kind}", kind, "هل تتذكر؟")
    assert value not in "".join(message.content for message in later.messages)


def test_pr179_agent_scrub_preserves_control_metadata_and_scrubs_tool_payloads():
    value = "complete"
    message = {"role": "assistant", "content": value,
               "tool_calls": [{"call_id": value, "name": value,
                                "arguments": {"secret": value}}]}
    tool_result = {"call_id": value, "name": value, "status": value,
                   "call_digest": value, "content": json.dumps(value)}
    turn = {"turn_id": value, "text": value, "initial_messages": [message],
            "input_digest": value,
            "calls": [{"idempotency_key": value, "request_digest": value,
                       "request": {"messages": [message]}}],
            "result": {"turn_id": value, "content": value, "status": value,
                       "error_code": value, "steps": [{"index": 0, "content": value,
                           "request_digest": value, "ledger_digest": value, "replayed": True,
                           "tool_results": [tool_result], "stop_reason": value,
                           "tool_calls": [message["tool_calls"][0]]}], "pending": []},
            "transcript": [message], "memory": {"block": value, "items": ["0" * 64]}}

    (scrubbed,), count = _scrub_agent_turns([turn], value)
    assert count >= 10
    assert scrubbed["turn_id"] == scrubbed["input_digest"] == value
    assert scrubbed["result"]["status"] == scrubbed["result"]["error_code"] == value
    call = scrubbed["initial_messages"][0]["tool_calls"][0]
    assert call["call_id"] == call["name"] == value
    assert call["arguments"]["secret"] != value
    result = scrubbed["result"]["steps"][0]["tool_results"][0]
    assert result["call_id"] == result["name"] == result["status"] == result["call_digest"] == value
    assert value not in result["content"]


def test_pr179_pending_owner_turn_blocks_forget_and_retry_succeeds_after_resolution(wired):
    from core.contracts import Response, ToolCall, Usage
    value = "رمز المعلّق ٥٥١"
    ids = wired.project("pending")
    item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
    call = ToolCall("pending-call", "propose_memory", {"text": value})
    wired.provider.responses.append(Response(value, Usage(1, 1), "complete", 0,
                                             provider="memory-bank", model_version="0" * 64,
                                             tool_calls=(call,)))
    turn_id = uuid.uuid4().hex
    result = wired.api("agent_ask", project=ids["id"], session=wired.session("pending", "agent"),
                       turn=turn_id, message="ما الرمز؟", files=[])
    assert result["status"] == "awaiting_owner"

    with pytest.raises(UIError) as err:
        wired.api("memory_forget", project=ids["id"], item_id=item)
    assert err.value.code == "memory_scrub_turn_unresolved"
    assert [found["item_id"] for found in wired.api("memory", project=ids["id"])["items"]] == [item]
    assert wired.store("pending").receipts(item) == []

    action = result["pending"][0]
    wired.api("agent_decide", project=ids["id"], session=ids["agent"],
              action_id=action["action_id"], call_digest=action["call_digest"],
              expected_revision=action["revision"], approve=False)
    wired.api("agent_resume", project=ids["id"], session=ids["agent"], turn=turn_id)
    assert wired.api("memory_forget", project=ids["id"], item_id=item)["status"] == "forgotten"


def test_pr179_busy_session_keeps_item_and_receipt_retryable(wired, monkeypatch):
    value = "موعد القفل الأحد"
    ids = wired.project("busy")
    item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
    _ask(wired, "busy", "text", "متى الموعد؟")
    session = wired.app.session(wired.app.project(ids["id"]), ids["text"])
    original = wired.app.session
    monkeypatch.setattr(wired.app, "session", lambda project, value:
                        session if value == ids["text"] else original(project, value))
    with session._lock():
        with pytest.raises(UIError) as err:
            wired.api("memory_forget", project=ids["id"], item_id=item)
    assert err.value.code == "memory_scrub_session_busy"
    assert wired.store("busy").find(item) is not None
    assert wired.store("busy").receipts(item) == []
    assert wired.api("memory_forget", project=ids["id"], item_id=item)["status"] == "forgotten"


def test_pr179_generation_lease_covers_store_delete_and_receipt(wired, monkeypatch):
    ids = wired.project("lease")
    item = wired.api("memory_remember", project=ids["id"], text="نص الإيجار")["item_id"]
    original = MemoryStore._apply_forget

    def guarded(store, *args, **kwargs):
        assert wired.app.generation.locked()
        return original(store, *args, **kwargs)

    monkeypatch.setattr(MemoryStore, "_apply_forget", guarded)
    assert wired.api("memory_forget", project=ids["id"], item_id=item)["status"] == "forgotten"


def test_pr179_a_mid_commit_failure_rolls_forward_before_history_can_be_reused(wired, monkeypatch):
    import webui.server as server
    value = "رمز المعاملة ٩٠٧"
    wired.provider = _ScrubEchoingDelegate(value)
    ids = wired.project("transaction")
    item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
    _ask(wired, "transaction", "agent", "ما الرمز؟")
    _ask(wired, "transaction", "text", "ما الرمز؟")
    original, state_writes = server._replace_private, 0

    def fail_second_state(path, payload):
        nonlocal state_writes
        if Path(path).name == "state.json":
            state_writes += 1
            if state_writes % 2 == 0:
                raise OSError("synthetic second-state failure")
        return original(path, payload)

    monkeypatch.setattr(server, "_replace_private", fail_second_state)
    with pytest.raises(UIError) as err:
        wired.api("memory_forget", project=ids["id"], item_id=item)
    assert err.value.code == "memory_forget_incomplete"
    project = wired.app.project(ids["id"])
    assert (project / server.MEMORY_FORGET_TRANSACTION).is_file()
    assert value not in (project / server.MEMORY_FORGET_TRANSACTION).read_text(encoding="utf-8")
    assert wired.store("transaction").find(item) is not None

    monkeypatch.setattr(server, "_replace_private", original)
    # A fresh process finishes the durable intent before it can serve a read.
    wired.close()
    wired._open()
    assert wired.api("memory", project=ids["id"])["items"] == []
    assert not (project / server.MEMORY_FORGET_TRANSACTION).exists()
    for kind in ("agent", "text"):
        _, later = _ask(wired, "transaction", kind, "هل تتذكر الرمز؟")
        assert value not in "".join(message.content for message in later.messages)


def test_pr179_a_late_preflight_failure_changes_no_earlier_session_or_receipt(wired, monkeypatch):
    from conversation.session import ChatSession
    value = "رمز الفحص ٢١٧"
    wired.provider = _ScrubEchoingDelegate(value)
    ids = wired.project("preflight")
    item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
    _ask(wired, "preflight", "agent", "ما الرمز؟")
    _ask(wired, "preflight", "text", "ما الرمز؟")
    project = wired.app.project(ids["id"])
    agent_state = project / "agent-control" / ids["agent"] / "state.json"
    before = agent_state.read_bytes()

    def fail_late(*args, **kwargs):
        raise ConversationError("synthetic_preflight_failure", "فشل مصطنع في الجلسة الثانية")

    monkeypatch.setattr(ChatSession, "_memory_forget_plan", fail_late)

    with pytest.raises(UIError) as err:
        wired.api("memory_forget", project=ids["id"], item_id=item)
    assert err.value.code == "memory_scrub_synthetic_preflight_failure"
    assert agent_state.read_bytes() == before
    assert wired.store("preflight").find(item) is not None
    assert wired.store("preflight").receipts(item) == []


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_pr179_backup_before_forget_cannot_restore_a_reusable_echo(wired, kind):
    value = "رمز النسخة ٦٠١"
    wired.provider = _ScrubEchoingDelegate(value)
    ids = wired.project(f"backup-{kind}")
    item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
    _ask(wired, f"backup-{kind}", kind, "ما الرمز؟")
    old = wired.backup()

    wired.api("memory_forget", project=ids["id"], item_id=item)
    wired.restore(old)
    assert wired.api("memory", project=ids["id"])["items"] == []
    _, later = _ask(wired, f"backup-{kind}", kind, "هل تتذكر الرمز؟")
    assert value not in "".join(message.content for message in later.messages)


def test_pr179_an_old_receipt_without_scrub_proof_blocks_reusable_history_restore(wired):
    value = "رمز نسخة قديمة ٣٨٨"
    wired.provider = _ScrubEchoingDelegate(value)
    ids = wired.project("old-receipt")
    item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
    _ask(wired, "old-receipt", "text", "ما الرمز؟")
    wired.store("old-receipt").forget(item)  # صيغة الإيصال السابقة لـ#179: لا دليل scrubbed
    old = wired.backup()

    with pytest.raises(BackupError) as err:
        wired.restore(old)
    assert err.value.code == "backup_memory_history_unverifiable"


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
