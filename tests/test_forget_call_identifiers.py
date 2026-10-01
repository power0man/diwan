"""Forgotten model-supplied IDs leave reusable context, not immutable audit receipts."""
import json
import uuid

import pytest

from core.contracts import CALL_ID
from conversation import agent_session
from conversation.session import ConversationError
from evaluation.memory_runner import EXPOSURE_QUESTION, _Wired
from providers.ollama import OllamaProvider


class WireEcho(OllamaProvider):
    """Use complete/payload/codec unchanged; replace only the HTTP transport."""
    def __init__(self, value):
        super().__init__(model="0" * 64)
        self.value, self.payloads = value, []
        self.emitted = False

    def _post(self, payload, timeout):
        self.payloads.append(json.loads(json.dumps(payload)))
        message = {"role": "assistant", "content": "تم."}
        if not self.emitted:
            assert self.value in json.dumps(payload, ensure_ascii=False)
            self.emitted = True
            message["tool_calls"] = [{"id": ident, "function": {"name": "list_files", "arguments": {}}}
                for ident in ("first-" + self.value, "second-" + self.value, "unchanged-call")]
        return {"message": message, "done": True, "done_reason": "stop", "model": self.model,
                "prompt_eval_count": 1, "eval_count": 1}


@pytest.mark.parametrize("restored", [False, True], ids=["reopen", "restore"])
def test_forget_rekeys_real_wire_ids_but_preserves_audit_and_replay(tmp_path, restored):
    value = "passport-secret-445566"
    delegate = WireEcho(value)
    wired = _Wired(tmp_path.resolve() / "ui", delegate=delegate)
    try:
        ids = wired.project("A")
        sid = wired.session("A", "agent")
        ctx = {"project": ids["id"], "session": sid}
        item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
        turn = uuid.uuid4().hex
        first = wired.api("agent_ask", **ctx, turn=turn, message=EXPOSURE_QUESTION, files=[])
        assert first["status"] == "complete"
        control = wired.app.project(ids["id"]) / "agent-control" / sid
        audit = {str(path.relative_to(control)): path.read_bytes()
                 for path in [control / "calls.jsonl", *sorted((control / "actions").rglob("*.json"))]}
        assert value.encode() in audit["calls.jsonl"]
        before = wired.backup() if restored else None
        wired.api("memory_forget", project=ids["id"], item_id=item)
        assert all((control / name).read_bytes() == raw for name, raw in audit.items())
        if restored:
            wired.restore(before)
        else:
            wired.close()
            wired._open()
        count = len(delegate.payloads)
        replay = wired.api("agent_resume", **ctx, turn=turn)
        assert replay["steps"][0]["tool_calls"] == first["steps"][0]["tool_calls"]
        assert replay["steps"][0]["tool_results"] == first["steps"][0]["tool_results"]
        assert len(delegate.payloads) == count
        later = wired.api("agent_ask", **ctx, turn=uuid.uuid4().hex, message="أكمل.", files=[])
        assert later["status"] == "complete"
        payload = delegate.payloads[-1]
        assert value not in json.dumps(payload, ensure_ascii=False)
        calls = [call["id"] for message in payload["messages"] for call in message.get("tool_calls", [])]
        results = [message["tool_call_id"] for message in payload["messages"] if message["role"] == "tool"]
        assert len(calls) == len(set(calls)) == 3
        assert calls == results
        assert "unchanged-call" in calls
        assert all(CALL_ID.fullmatch(ident) for ident in calls)
    finally:
        wired.close()


def _context(identifiers):
    return [{"initial_messages": [], "calls": [], "transcript": [
        {"role": "assistant", "content": "", "tool_calls": [
            {"call_id": ident, "name": "list_files", "arguments": {}} for ident in identifiers]},
        *[{"role": "tool", "content": "{}", "tool_call_id": ident} for ident in identifiers]]}]


@pytest.mark.parametrize("value", ["a", "0", "secret", "-"])
def test_context_aliases_are_valid_unique_and_leave_unrelated_ids(value):
    identifiers = ["first-" + value, "second-" + value, "KEEPZ"]
    context = _context(identifiers)
    aliases = agent_session._context_call_aliases(context, value)
    assert set(aliases) == set(identifiers[:2])
    assert len(set(aliases.values())) == 2
    assert all(CALL_ID.fullmatch(alias) and value not in alias for alias in aliases.values())
    assert not set(aliases.values()).intersection(identifiers)
    assert aliases == agent_session._context_call_aliases(context, value)


def test_context_alias_namespace_uses_original_id_not_forgotten_text():
    context = _context(["passport-secret-445566"])
    passport = agent_session._context_call_aliases(context, "passport")
    secret = agent_session._context_call_aliases(context, "secret")
    assert passport == secret and len(passport) == 1


def test_context_alias_collision_uses_a_new_id_without_changing_reserved_ids(monkeypatch):
    actual = agent_session.digest
    collisions = []
    def collide(value):
        if value.get("namespace") == "diwan-forgotten-context-call-v1" and value["counter"] == 0:
            collisions.append(value["original_call_id"])
            return "KEEPZ"
        if value.get("namespace") == "diwan-forgotten-context-call-v1" and value["counter"] == 1:
            return "SHARED"
        return actual(value)
    monkeypatch.setattr(agent_session, "digest", collide)
    aliases = agent_session._context_call_aliases(_context(["first-secret", "second-secret", "KEEPZ"]), "secret")
    assert len(collisions) == len(aliases) == len(set(aliases.values())) == 2
    assert "KEEPZ" not in aliases and "KEEPZ" not in aliases.values()
    assert "SHARED" in aliases.values()


def test_context_alias_exhaustion_refuses_instead_of_reusing_a_conflicting_id(monkeypatch):
    monkeypatch.setattr(agent_session, "digest", lambda value: "KEEPZ")
    with pytest.raises(ConversationError) as refused:
        agent_session._context_call_aliases(_context(["first-secret", "KEEPZ"]), "secret")
    assert refused.value.code == "state_corrupt"
