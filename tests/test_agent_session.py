"""AgentSession v2 acceptance: real durable ledger/store/journal, synthetic provider."""
import json
import os
from pathlib import Path

import pytest

from agent.actions import ActionRefused
from agent.registry import Tool, ToolRegistry
from conversation.agent_session import AgentSession
from conversation.session import ConversationError
from core.canonical import digest
from core.contracts import Response, ToolCall, ToolSpec, Usage
from providers.base import ProviderError


def response(content="تم", calls=(), stop="complete"):
    return Response(content, Usage(1, 1), stop, 0, tool_calls=calls)


class Provider:
    is_local = True
    name = "synthetic"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.estimates = 0

    def estimate_micros(self, request):
        self.estimates += 1
        return 0

    def complete(self, request):
        self.requests.append(request)
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def registry(effects, *, owner=False, crash=False):
    def write(args, ctx):
        effects.append(args.copy())
        action = ctx.journal.write_file(args["path"], args["content"])
        if crash:
            raise KeyboardInterrupt()
        return {"content": "written", "action_id": action.action_id}
    return ToolRegistry(Tool(ToolSpec("write_file", "write", {"type": "object"},
                                      "owner" if owner else "logged", True), write))


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    effects = []
    tools = registry(effects)
    def create(**overrides):
        kwargs = dict(workspace_root=workspace, project_id="project", registry=tools,
                      model="test", model_version="v1")
        kwargs.update(overrides)
        return AgentSession(tmp_path / "control", "session", **kwargs)
    return create, workspace, effects


def call(call_id="c1", path="a.txt", content="hello"):
    return ToolCall(call_id, "write_file", {"path": path, "content": content})


def test_multi_call_turn_reopens_and_keeps_typed_history(setup):
    create, workspace, effects = setup
    session = create()
    provider = Provider(response(calls=(call(), call("c2", "b.txt"))), response("done"))
    result = session.start_turn("t1", "نفذ", provider)
    assert result["status"] == "complete", result
    assert len(result["steps"]) == 2
    assert len(effects) == 2
    assert len(provider.requests) == 2
    next_provider = Provider(response("next"))
    reopened = create()
    assert reopened.start_turn("t1", "نفذ", Provider()) == result
    second = reopened.start_turn("t2", "راجع", next_provider)
    assert second["status"] == "complete"
    messages = next_provider.requests[0].messages
    assert [m.role for m in messages] == ["system", "user", "assistant", "tool", "tool", "assistant", "user"]
    assert messages[2].tool_calls == (call(), call("c2", "b.txt"))
    assert messages[3].tool_call_id == "c1"
    assert len(reopened.history()["turns"]) == 2


def test_owner_decision_binds_digest_revision_and_resume_without_repeating(setup):
    create, workspace, effects = setup
    tools = registry(effects, owner=True)
    session = create(registry=tools)
    provider = Provider(response(calls=(call(), call("c2", "b.txt"))), response("done"))
    pending = session.start_turn("t1", "نفذ", provider)
    assert pending["status"] == "awaiting_owner"
    assert effects == []
    with pytest.raises(ConversationError, match="turn_unresolved"):
        session.start_turn("t2", "new", provider)
    first = pending["pending"][0]
    with pytest.raises(ActionRefused, match="action_binding_conflict"):
        session.decide(first["action_id"], "0" * 64, True, first["revision"])
    session.decide(first["action_id"], first["call_digest"], True, first["revision"])
    pending2 = create(registry=tools).resume("t1", provider)
    assert pending2["status"] == "awaiting_owner"
    assert len(effects) == 1 and len(provider.requests) == 1
    second = pending2["pending"][0]
    session.decide(second["action_id"], second["call_digest"], False, second["revision"])
    result = session.resume("t1", provider)
    assert result["status"] == "complete"
    assert len(effects) == 1 and len(provider.requests) == 2
    assert not (workspace / "b.txt").exists()
    assert session.resume("t1", Provider()) == result


def test_started_tool_is_unknown_and_never_retried(setup):
    create, workspace, effects = setup
    tools = registry(effects, crash=True)
    session = create(registry=tools)
    with pytest.raises(KeyboardInterrupt):
        session.start_turn("t1", "write", Provider(response(calls=(call(),))))
    assert (workspace / "a.txt").read_text() == "hello"
    result = create(registry=tools).resume("t1", Provider())
    assert result["status"] == "outcome_unknown"
    assert result["error_code"] == "action_outcome_unknown"
    assert len(effects) == 1


def test_provider_interrupt_never_recalled(setup):
    create, _, _ = setup
    session = create()
    provider = Provider(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        session.start_turn("t1", "hi", provider)
    other = Provider(response("should not happen"))
    result = create().resume("t1", other)
    assert result["status"] == "outcome_unknown"
    assert other.estimates == 0 and not other.requests


def test_intent_is_fsynced_before_estimate_and_missing_ledger_prevents_retry(setup):
    create, _, _ = setup
    session = create()
    class Crash(Provider):
        def estimate_micros(self, request):
            state = json.loads((session.root / "state.json").read_text())["state"]
            assert state["turns"][0]["calls"][0]["request_digest"] == digest(request.fingerprint_payload())
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        session.start_turn("t1", "hi", Crash())
    assert (session.root / "calls.jsonl").read_text() == ""
    provider = Provider()
    assert create().resume("t1", provider)["status"] == "outcome_unknown"
    assert provider.estimates == 0


def test_truncated_calls_never_execute_or_enter_next_context(setup):
    create, _, effects = setup
    session = create()
    result = session.start_turn("t1", "hi", Provider(response("partial", (call(),), "max_output")))
    assert result["status"] == "truncated" and effects == []
    provider = Provider(response())
    session.start_turn("t2", "again", provider)
    assert [message.role for message in provider.requests[0].messages] == ["system", "user"]


def test_retryable_provider_error_is_cached_per_turn(setup):
    create, _, _ = setup
    session = create()
    result = session.start_turn("t1", "hi", Provider(ProviderError("offline", "no server", retryable=True)))
    assert result["status"] == "failed"
    assert create().resume("t1", Provider()) == result


def test_identity_and_text_conflicts_fail_before_provider(setup):
    create, _, _ = setup
    session = create()
    session.start_turn("t1", "hi", Provider(response()))
    with pytest.raises(ConversationError, match="turn_id_conflict"):
        session.start_turn("t1", "changed", Provider())
    with pytest.raises(ConversationError, match="configuration_conflict"):
        create(project_id="other")
    with pytest.raises(ConversationError, match="configuration_conflict"):
        create(model_version="other")


@pytest.mark.parametrize("filename", ["manifest.json", "state.json", "calls.jsonl"])
def test_deleted_control_file_is_not_reinitialized(setup, filename):
    create, _, _ = setup
    session = create()
    path = session.root / filename
    path.unlink()
    with pytest.raises(ConversationError):
        create()
    assert not path.exists()


def test_tamper_and_ledger_tail_deletion_rejected(setup):
    create, _, _ = setup
    session = create()
    session.start_turn("t1", "hi", Provider(response()))
    (session.root / "calls.jsonl").write_text("")
    with pytest.raises(ConversationError, match="ledger_binding_invalid"):
        create()


def test_state_checksum_corruption_rejected(setup):
    create, _, _ = setup
    session = create()
    path = session.root / "state.json"
    state = json.loads(path.read_text())
    state["state"]["turns"].append({})
    path.write_text(json.dumps(state))
    with pytest.raises(ConversationError, match="state_corrupt"):
        create()


def test_process_lock_prevents_concurrent_session(setup):
    create, _, _ = setup
    session = create()
    with session._lock():
        with pytest.raises(ConversationError, match="session_busy"):
            create()


def test_symlink_hardlink_and_inside_workspace_control_rejected(setup, tmp_path):
    create, workspace, _ = setup
    with pytest.raises(ConversationError, match="control_root_in_workspace"):
        AgentSession(workspace / "control", "session", workspace_root=workspace, project_id="p",
                     registry=ToolRegistry(), model="test", model_version="v1")
    session = create()
    path = session.root / "state.json"
    saved = path.read_bytes()
    path.unlink()
    other = tmp_path / "other"
    other.write_bytes(saved)
    other.chmod(0o600)
    path.symlink_to(other)
    with pytest.raises(ConversationError):
        create()
    path.unlink()
    os.link(other, path)
    with pytest.raises(ConversationError):
        create()


def test_readonly_history_does_not_rewrite_control_state(setup):
    create, _, _ = setup
    session = create()
    session.start_turn("t1", "hi", Provider(response()))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in session.root.rglob("*") if p.is_file()}
    assert session.history()["turns"][0]["result"]["status"] == "complete"
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in session.root.rglob("*") if p.is_file()}
    assert after == before


def test_context_and_turn_limits_before_provider(setup):
    create, _, _ = setup
    session = create(max_turns=1)
    provider = Provider(response())
    with pytest.raises(ConversationError, match="context_limit"):
        session.start_turn("too-big", "x" * 25000, provider)
    assert provider.estimates == 0
    session.start_turn("t1", "ok", provider)
    with pytest.raises(ConversationError, match="turn_limit"):
        session.start_turn("t2", "ok", Provider())


def test_revert_is_bound_and_http_repeat_does_not_repeat_effect(setup):
    create, workspace, _ = setup
    session = create()
    result = session.start_turn("t1", "write", Provider(response(calls=(call(),)), response()))
    action_id = result["steps"][0]["tool_results"][0]["action_id"]
    reverted = session.revert(action_id, "undo1")
    assert reverted["status"] == "reverted"
    assert not (workspace / "a.txt").exists()
    (workspace / "a.txt").write_text("owner's new file")
    assert create().revert(action_id, "undo1") == reverted
    assert (workspace / "a.txt").read_text() == "owner's new file"


def test_revert_preserves_owner_change(setup):
    create, workspace, _ = setup
    session = create()
    result = session.start_turn("t1", "write", Provider(response(calls=(call(),)), response()))
    action_id = result["steps"][0]["tool_results"][0]["action_id"]
    (workspace / "a.txt").write_text("owner")
    refused = session.revert(action_id, "undo1")
    assert refused["status"] == "refused" and refused["error_code"] == "changed_since_action"
    assert (workspace / "a.txt").read_text() == "owner"


def test_unowned_action_cannot_be_reverted(setup):
    create, workspace, _ = setup
    session = create()
    with pytest.raises((ActionRefused, ConversationError)):
        session.revert("action-" + "0" * 64, "undo1")


def test_completed_provider_call_recovers_after_result_save_crash_without_calling_again(setup, monkeypatch):
    create, _, _ = setup
    session = create()
    original = session._save
    def crash_after_result(state):
        if state["turns"] and state["turns"][-1]["result"] is not None:
            raise KeyboardInterrupt()
        return original(state)
    monkeypatch.setattr(session, "_save", crash_after_result)
    provider = Provider(response("durable answer"))
    with pytest.raises(KeyboardInterrupt):
        session.start_turn("t1", "hi", provider)
    reopened = create()
    assert reopened.history()["turns"][0]["result"]["status"] == "outcome_unknown"
    other = Provider()
    result = reopened.resume("t1", other)
    assert result["status"] == "complete" and result["content"] == "durable answer"
    assert other.estimates == 0 and other.requests == []


def test_crash_before_action_plan_does_not_create_plan_from_replayed_model(setup, monkeypatch):
    create, _, effects = setup
    session = create()
    def crash(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(session.action_store, "register_step", crash)
    with pytest.raises(KeyboardInterrupt):
        session.start_turn("t1", "write", Provider(response(calls=(call(),))))
    provider = Provider()
    result = create().resume("t1", provider)
    assert result["status"] == "refused" and result["error_code"] == "action_receipt_missing"
    assert effects == [] and provider.estimates == 0


def test_unknown_intent_history_recover_is_explicit_and_readonly_by_default(setup, monkeypatch):
    create, _, _ = setup
    session = create()
    original = session._save
    def crash_after_result(state):
        if state["turns"] and state["turns"][-1]["result"] is not None:
            raise KeyboardInterrupt()
        return original(state)
    monkeypatch.setattr(session, "_save", crash_after_result)
    class CrashEstimate(Provider):
        def estimate_micros(self, request):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        session.start_turn("t1", "hi", CrashEstimate())
    reopened = create()
    path = reopened.root / "state.json"
    before = path.read_bytes()
    reopened.history()
    assert path.read_bytes() == before
    reopened.history(recover=True)
    assert path.read_bytes() != before
    assert reopened.resume("t1", Provider())["status"] == "outcome_unknown"


def test_rehashed_tool_history_injection_is_rejected_against_ledger(setup):
    create, _, _ = setup
    session = create()
    session.start_turn("t1", "write", Provider(response(calls=(call(),)), response()))
    path = session.root / "state.json"
    envelope = json.loads(path.read_text())
    envelope["state"]["turns"][0]["transcript"][3]["content"] = "forged result"
    envelope["sha256"] = digest(envelope["state"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(ConversationError, match="state_corrupt"):
        create()


def test_missing_completed_action_receipt_is_not_hidden_by_cached_turn(setup):
    create, _, _ = setup
    session = create()
    result = session.start_turn("t1", "write", Provider(response(calls=(call(),)), response()))
    action_id = result["steps"][0]["tool_results"][0]["action_id"]
    (session.root / "actions" / (action_id + ".json")).unlink()
    with pytest.raises(ConversationError):
        create()


def test_entire_deleted_action_store_is_not_recreated(setup):
    import shutil
    create, _, _ = setup
    session = create()
    shutil.rmtree(session.root / "actions")
    with pytest.raises(ConversationError, match="state_missing"):
        create()
    assert not (session.root / "actions").exists()


def test_workspace_replacement_and_float_deadline(setup):
    create, workspace, _ = setup
    session = create(deadline_s=120.5)
    session.start_turn("t1", "hi", Provider(response()))
    assert create(deadline_s=120.5).history()["turns"]
    workspace.rename(workspace.with_name("old-workspace"))
    workspace.mkdir()
    with pytest.raises(ConversationError, match="configuration_conflict"):
        create(deadline_s=120.5)


def test_stale_decision_and_cross_session_source_are_rejected(setup):
    create, workspace, effects = setup
    tools = registry(effects, owner=True)
    session = create(registry=tools)
    result = session.start_turn("t1", "write", Provider(response(calls=(call(),))))
    pending = result["pending"][0]
    session.decide(pending["action_id"], pending["call_digest"], True, pending["revision"])
    with pytest.raises(ActionRefused, match="action_revision_conflict"):
        session.decide(pending["action_id"], pending["call_digest"], False, pending["revision"])
    second = AgentSession(session.root.parent, "second", workspace_root=workspace, project_id="project",
                          registry=tools, model="test", model_version="v1")
    with pytest.raises((ConversationError, ActionRefused)):
        second.revert(pending["action_id"], "undo1")


def test_remote_provider_never_receives_context(setup):
    create, _, _ = setup
    provider = Provider()
    provider.is_local = False
    with pytest.raises(ConversationError, match="policy_requires_local"):
        create().start_turn("t1", "secret", provider)
    assert provider.estimates == 0


def test_same_object_losing_thread_cannot_clear_owner_descriptor(setup, monkeypatch):
    import threading
    import conversation.agent_session as module
    create, _, _ = setup
    session = create()
    a_waiting, b_entered_or_rejected = threading.Event(), threading.Event()
    a_inside, b_done = threading.Event(), threading.Event()
    original = module._open_directory
    first = {"owner": True, "contender": True}
    failures = []
    def interleaved(path, **kwargs):
        name = threading.current_thread().name
        if path == session.root and name in first and first[name]:
            first[name] = False
            if name == "owner":
                a_waiting.set()
                assert b_entered_or_rejected.wait(3)
            else:
                b_entered_or_rejected.set()
                assert a_inside.wait(3)
        return original(path, **kwargs)
    monkeypatch.setattr(module, "_open_directory", interleaved)
    def owner():
        try:
            with session._lock():
                a_inside.set()
                assert b_done.wait(3)
                session._check()
                assert session._fd is not None
        except BaseException as exc:
            failures.append(exc)
    def contender():
        try:
            assert a_waiting.wait(3)
            with pytest.raises(ConversationError, match="session_busy"):
                with session._lock():
                    pytest.fail("second owner entered")
        except BaseException as exc:
            failures.append(exc)
        finally:
            b_entered_or_rejected.set()
            b_done.set()
    one = threading.Thread(target=owner, name="owner")
    two = threading.Thread(target=contender, name="contender")
    one.start(); two.start()
    one.join(5); two.join(5)
    assert not one.is_alive() and not two.is_alive()
    assert failures == []
    assert session.history()["turns"] == []


def test_validate_turn_is_readonly_and_uses_same_admission_as_start(setup):
    create, _, _ = setup
    session = create(max_turns=1)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in session.root.rglob("*") if p.is_file()}
    assert session.validate_turn("t1", "hello") == {"status": "ready"}
    with pytest.raises(ConversationError, match="context_limit"):
        session.validate_turn("large", "x" * 25000)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in session.root.rglob("*") if p.is_file()}
    assert after == before
    result = session.start_turn("t1", "hello", Provider(response()))
    assert session.validate_turn("t1", "hello") == {"status": "replayed", "result": result}
    with pytest.raises(ConversationError, match="turn_id_conflict"):
        session.validate_turn("t1", "changed")
    with pytest.raises(ConversationError, match="turn_limit"):
        session.validate_turn("t2", "new")
