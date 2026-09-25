"""Real durable files and synthetic barriers: cancellation never kills an effect."""
import json
import os
import shutil
import threading

import pytest

from agent.registry import Tool, ToolRegistry
from conversation.agent_session import AgentSession, TERMINAL
from conversation.session import ConversationError
from core.contracts import Response, ToolCall, ToolSpec, Usage


def answer(*calls, text="done", stop="complete"):
    return Response(text, Usage(1, 1), stop, 0, tool_calls=tuple(calls))


def call(identity="one", path="one.txt"):
    return ToolCall(identity, "write", {"path": path, "content": identity})


class Provider:
    is_local, name = True, "stop-fixture"
    def __init__(self, *outputs): self.outputs, self.calls = list(outputs), []
    def estimate_micros(self, request): return 0
    def complete(self, request):
        self.calls.append(request)
        assert self.outputs, "unexpected provider call"
        value = self.outputs.pop(0)
        if isinstance(value, BaseException): raise value
        return value(request) if callable(value) else value


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    effects = []
    def write(args, context):
        action = context.journal.write_file(args["path"], args["content"])
        effects.append(args["path"])
        return {"content": "written", "action_id": action.action_id}
    def make(*, session_id="session", owner=False, handler=write):
        return AgentSession(tmp_path / "control", session_id, workspace_root=workspace,
            project_id="project", registry=ToolRegistry(Tool(ToolSpec("write", "write", {},
            "owner" if owner else "logged", True), handler)), model="synthetic", model_version="v1")
    return make, workspace, effects, write


def threaded(callback):
    results, errors = [], []
    def run():
        try: results.append(callback())
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    return thread, results, errors


def finish(thread, errors):
    thread.join(5)
    assert not thread.is_alive()
    assert not errors, errors


def test_stop_during_model_keeps_lock_and_prevents_tools(setup):
    make, workspace, effects, _ = setup
    session = make(); entered, release = threading.Event(), threading.Event()
    def delayed(request):
        entered.set(); assert release.wait(5)
        return answer(call())
    provider = Provider(delayed)
    thread, results, errors = threaded(lambda: session.start_turn("t1", "write", provider))
    try:
        assert entered.wait(3)
        active_fd = session._fd
        stopped = session.request_stop("t1")
        assert stopped == {"turn_id":"t1", "status":"stop_requested", "error_code":None}
        assert session._fd == active_fd and active_fd is not None
        assert thread.is_alive() and not effects
        assert session.request_stop("t1") == stopped
    finally:
        release.set(); finish(thread, errors)
    result = results[0]
    assert result["status"] == "cancelled" and len(result["steps"]) == 1
    assert result["steps"][0]["tool_calls"] and len(provider.calls) == 1
    assert not effects and not (workspace / "one.txt").exists()
    assert not list((session.root / "actions").glob("action-*.json"))
    reopened = make()
    assert reopened.resume("t1", Provider()) == result
    assert reopened.start_turn("t1", "write", Provider()) == result
    following = Provider(answer(text="new"))
    assert reopened.start_turn("t2", "new", following)["status"] == "complete"
    assert [m.role for m in following.calls[0].messages] == ["system", "user"]


def test_stop_during_first_tool_preserves_receipt_and_skips_second(setup):
    make, workspace, effects, write = setup
    entered, release = threading.Event(), threading.Event()
    def delayed(args, context):
        result = write(args, context); entered.set(); assert release.wait(5)
        return result
    session = make(handler=delayed)
    provider = Provider(answer(call(), call("two", "two.txt")))
    thread, results, errors = threaded(lambda: session.start_turn("t1", "two writes", provider))
    try:
        assert entered.wait(3) and (workspace / "one.txt").read_text() == "one"
        assert session.request_stop("t1")["status"] == "stop_requested"
        assert thread.is_alive()
    finally:
        release.set(); finish(thread, errors)
    result = results[0]
    assert result["status"] == "cancelled" and len(provider.calls) == 1
    assert effects == ["one.txt"] and not (workspace / "two.txt").exists()
    one = result["steps"][0]["tool_results"]
    assert len(one) == 1 and one[0]["status"] == "ok"
    assert session.action_store.completed_result(one[0]["action_id"]) == one[0]
    assert make(handler=delayed).resume("t1", Provider()) == result
    assert effects == ["one.txt"]


def test_stop_after_tool_prevents_next_model_step(setup):
    make, workspace, effects, write = setup; holder = {}
    def stop_in_tool(args, context):
        result = write(args, context)
        assert holder["session"].request_stop("t1")["status"] == "stop_requested"
        return result
    session = holder["session"] = make(handler=stop_in_tool)
    provider = Provider(answer(call()))
    result = session.start_turn("t1", "write", provider)
    assert result["status"] == "cancelled" and effects == ["one.txt"] and len(provider.calls) == 1
    assert result["steps"][0]["tool_results"][0]["status"] == "ok"
    assert (workspace / "one.txt").read_text() == "one"


def test_awaiting_owner_cancels_without_provider_and_rejects_old_decision(setup):
    make, workspace, effects, _ = setup; session = make(owner=True)
    provider = Provider(answer(call(), call("two", "two.txt")))
    pending = session.start_turn("t1", "write", provider)["pending"][0]
    stopped = session.request_stop("t1")
    assert stopped == {"turn_id":"t1", "status":"cancelled", "error_code":"stop_requested"}
    assert session.request_stop("t1") == stopped and session.history()["pending"] == []
    with pytest.raises(ConversationError, match="action_not_pending"):
        session.decide(pending["action_id"], pending["call_digest"], True, pending["revision"])
    reopened = make(owner=True)
    assert reopened.resume("t1", Provider())["status"] == "cancelled"
    assert reopened.start_turn("t1", "write", Provider())["status"] == "cancelled"
    assert not effects and len(provider.calls) == 1 and not (workspace / "one.txt").exists()


def test_previously_approved_action_does_not_execute_after_stop(setup):
    make, _, effects, _ = setup; session = make(owner=True)
    pending = session.start_turn("t1", "write", Provider(answer(call())))["pending"][0]
    session.decide(pending["action_id"], pending["call_digest"], True, pending["revision"])
    assert session.request_stop("t1")["status"] == "cancelled"
    assert make(owner=True).resume("t1", Provider())["status"] == "cancelled" and not effects


def test_published_stop_survives_interruption_before_settlement(setup):
    make, _, effects, _ = setup; session = make(owner=True)
    session.start_turn("t1", "write", Provider(answer(call())))
    # Inject interruption after publishing and before the session-lock step.
    # This is not a claim of an OS process kill or power-loss test.
    assert session._stop_signals.request("t1", TERMINAL)["status"] == "stop_requested"
    state = json.loads((session.root / "state.json").read_text())["state"]
    assert state["turns"][0]["result"]["status"] == "awaiting_owner"
    reopened = make(owner=True)
    assert reopened.history()["turns"][0]["result"]["status"] == "cancelled"
    assert reopened.resume("t1", Provider())["status"] == "cancelled" and not effects


def test_provider_unknown_cannot_be_hidden_by_cancellation(setup):
    make, _, _, _ = setup; session = make()
    assert session.start_turn("t1", "write", Provider(RuntimeError("injected loss")))["status"] == "outcome_unknown"
    stopped = session.request_stop("t1")
    assert stopped["status"] == "outcome_unknown" and stopped["error_code"] == "provider_outcome_unknown"
    assert make().resume("t1", Provider())["status"] == "outcome_unknown"


def test_tool_unknown_wins_over_stop_and_never_repeats(setup):
    make, workspace, effects, write = setup; holder = {}
    class ToolLoss(BaseException): pass
    def lost(args, context):
        write(args, context); holder["session"].request_stop("t1")
        raise ToolLoss("injected after effect")
    session = holder["session"] = make(handler=lost)
    result = session.start_turn("t1", "write", Provider(answer(call())))
    assert result["status"] == "outcome_unknown" and result["error_code"] == "action_outcome_unknown"
    assert session.request_stop("t1")["error_code"] == "action_outcome_unknown"
    assert make(handler=lost).resume("t1", Provider())["status"] == "outcome_unknown"
    assert effects == ["one.txt"] and (workspace / "one.txt").read_text() == "one"


def test_completed_stop_does_not_write_or_create_marker(setup):
    make, _, _, _ = setup; session = make()
    result = session.start_turn("t1", "answer", Provider(answer()))
    before = {p.name:p.read_bytes() for p in session.root.iterdir() if p.is_file()}
    assert session.request_stop("t1") == {"turn_id":"t1", "status":"not_running", "turn_status":"complete", "error_code":None}
    assert {p.name:p.read_bytes() for p in session.root.iterdir() if p.is_file()} == before
    assert session.resume("t1", Provider()) == result


def test_unknown_and_cross_session_turns_cannot_publish_stop(setup):
    make, _, _, _ = setup
    first, other = make(owner=True), make(session_id="other", owner=True)
    first.start_turn("t1", "write", Provider(answer(call())))
    with pytest.raises(ConversationError, match="turn_unknown"): other.request_stop("t1")
    with pytest.raises(ConversationError, match="turn_id_invalid"): first.request_stop("../escape")
    assert not list(other.root.glob("stop-*.json")) and not list(first.root.glob("stop-*.json"))


def test_marker_copied_from_other_session_is_rejected(setup):
    make, _, _, _ = setup
    first, other = make(owner=True), make(session_id="other", owner=True)
    for session in (first, other): session.start_turn("t1", "write", Provider(answer(call())))
    first.request_stop("t1"); marker = next(first.root.glob("stop-*.json"))
    shutil.copyfile(marker, other.root / marker.name); os.chmod(other.root / marker.name, 0o600)
    with pytest.raises(ConversationError, match="stop_binding_conflict"): other.request_stop("t1")
    with pytest.raises(ConversationError, match="stop_binding_conflict"): other.resume("t1", Provider())


def test_replaced_control_root_refused_before_stop_write(setup):
    make, _, _, _ = setup; session = make(owner=True)
    session.start_turn("t1", "write", Provider(answer(call())))
    original = session.root.with_name("preserved-session")
    session.root.rename(original); shutil.copytree(original, session.root)
    with pytest.raises(ConversationError, match="workspace_changed"): session.request_stop("t1")
    assert not list(session.root.glob("stop-*.json")) and not list(original.glob("stop-*.json"))


def test_marker_links_are_rejected_without_touching_outside(setup, tmp_path):
    make, _, _, _ = setup; session = make(owner=True)
    session.start_turn("t1", "write", Provider(answer(call())))
    marker = session.root / session._stop_signals._name("t1")
    outside = tmp_path / "outside.json"; outside.write_text("untouched")
    marker.symlink_to(outside)
    with pytest.raises(ConversationError, match="unsafe_path"): session.request_stop("t1")
    marker.unlink(); os.link(outside, marker)
    with pytest.raises(ConversationError, match="unsafe_path"): session.request_stop("t1")
    assert outside.read_text() == "untouched"


def test_two_stop_threads_do_not_reset_running_session_descriptor(setup):
    make, _, effects, _ = setup; session = make()
    entered, release = threading.Event(), threading.Event()
    def delayed(request):
        entered.set(); assert release.wait(5)
        return answer(call())
    running, results, errors = threaded(lambda: session.start_turn("t1", "write", Provider(delayed)))
    try:
        assert entered.wait(3); oldfd = session._fd; gate = threading.Barrier(3)
        def request():
            gate.wait(3)
            return session.request_stop("t1")
        a, ar, ae = threaded(request); b, br, be = threaded(request); gate.wait(3)
        finish(a, ae); finish(b, be)
        assert ar[0] == br[0] and ar[0]["status"] == "stop_requested" and session._fd == oldfd
    finally:
        release.set(); finish(running, errors)
    assert results[0]["status"] == "cancelled" and not effects


def test_stop_before_first_model_call_has_no_provider_intent(setup, monkeypatch):
    make, _, effects, _ = setup; session = make(); original = session._run
    def stop_then_run(state, turn, provider):
        assert session.request_stop(turn["turn_id"])["status"] == "stop_requested"
        return original(state, turn, provider)
    monkeypatch.setattr(session, "_run", stop_then_run)
    provider = Provider()
    result = session.start_turn("t1", "write", provider)
    assert result["status"] == "cancelled" and result["steps"] == []
    assert provider.calls == [] and not effects
    assert (session.root / "calls.jsonl").read_bytes() == b""
    assert make().resume("t1", Provider()) == result


def test_stop_never_relabels_saved_unknown_even_when_model_record_is_replayable(setup, monkeypatch):
    make, _, _, _ = setup; session = make(); original = session._save; injected = []
    def fail_summary_once(state):
        if state["turns"] and (state["turns"][-1]["result"] or {}).get("status") == "complete" and not injected:
            injected.append(True)
            raise OSError("injected after durable model record, before final summary")
        return original(state)
    monkeypatch.setattr(session, "_save", fail_summary_once)
    result = session.start_turn("t1", "answer", Provider(answer()))
    assert result["status"] == "outcome_unknown" and injected
    assert session.request_stop("t1")["status"] == "outcome_unknown"
    reopened = make()
    assert reopened.resume("t1", Provider())["status"] == "outcome_unknown"


def test_restored_stop_keeps_completed_tool_receipt_visible_for_revert(setup, monkeypatch):
    make, workspace, effects, _ = setup; session = make(); original = session._save
    def lose_all_result_summaries(state):
        if state["turns"] and state["turns"][-1]["result"] is not None:
            raise OSError("injected process loss before any final turn result persistence")
        return original(state)
    monkeypatch.setattr(session, "_save", lose_all_result_summaries)
    # The second provider response is durable, but both attempts to save the
    # final/unknown summaries fail. This intentionally leaves result=None.
    with pytest.raises(ConversationError, match="unsafe_path"):
        session.start_turn("t1", "write", Provider(answer(call()), answer()))
    monkeypatch.setattr(session, "_save", original)
    assert effects == ["one.txt"] and (workspace / "one.txt").read_text() == "one"
    state = json.loads((session.root / "state.json").read_text())["state"]
    assert state["turns"][0]["result"] is None
    session._stop_signals.request("t1", TERMINAL)
    reopened = make()
    result = reopened.history()["turns"][0]["result"]
    assert result["status"] == "cancelled" and len(result["steps"]) == 2
    effect = result["steps"][0]["tool_results"][0]
    assert effect == reopened.action_store.completed_result(effect["action_id"])
    assert reopened.resume("t1", Provider()) == result and effects == ["one.txt"]
    assert reopened.revert(effect["action_id"], "undo")["status"] == "reverted"
    assert not (workspace / "one.txt").exists()
