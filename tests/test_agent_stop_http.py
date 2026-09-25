"""Stop requests use real HTTP concurrently with a held synthetic provider."""
import threading
import uuid

from core.contracts import ToolCall
from tests.test_agent_webui import ask, commands, live, response


def test_stop_during_provider_returns_before_provider_then_prevents_file_effect(live):
    ctx = live.agent()
    turn = uuid.uuid4().hex
    entered, release = threading.Event(), threading.Event()
    def held(request):
        entered.set()
        assert release.wait(8)
        return response("write", ToolCall("w", "write_file", {"path": "stop.txt", "content": "forbidden"}))
    live.provider.responses = [held]
    results = []
    worker = threading.Thread(target=lambda: results.append(ask(live, ctx, turn=turn)))
    worker.start()
    try:
        assert entered.wait(5)
        stopped = live.api("agent_stop", **ctx, turn=turn)
        assert stopped["status"] == "stop_requested"
        assert worker.is_alive(), "stop must return without waiting for the model"
        assert live.api("agent_stop", **ctx, turn=turn)["status"] == "stop_requested"
        assert live.api("history", **ctx, before=None)["status"] == "running"
    finally:
        release.set()
        worker.join(8)
    assert not worker.is_alive()
    assert results[0]["status"] == "cancelled"
    assert live.api("agent_files", project=ctx["project"])["files"] == []
    live.app.agent_provider_factory = lambda: (_ for _ in ()).throw(AssertionError("must not construct provider"))
    assert live.api("agent_resume", **ctx, turn=turn)["status"] == "cancelled"
    assert ask(live, ctx, turn=turn)["status"] == "cancelled"
    assert len(live.provider.requests) == 1


def test_stop_pending_owner_prevents_later_approval_or_resume(commands):
    ctx = commands.agent()
    commands.provider.responses = [response("run", ToolCall("r", "run_command", {"argv": ["python", "a.py"]}))]
    result = ask(commands, ctx)
    assert result["status"] == "awaiting_owner"
    action = result["pending"][0]
    stopped = commands.api("agent_stop", **ctx, turn=result["turn_id"])
    assert stopped["status"] == "cancelled"
    status, error, _ = commands.request({"action": "agent_decide", **ctx,
        "action_id": action["action_id"], "call_digest": action["call_digest"],
        "expected_revision": action["revision"], "approve": True})
    assert status == 409
    assert commands.api("agent_resume", **ctx, turn=result["turn_id"])["status"] == "cancelled"
    assert len(commands.provider.requests) == 1


def test_stop_guards_scope_and_pre_admission_race(live):
    ctx, other = live.agent(), live.agent()
    turn = uuid.uuid4().hex
    live.app.active = (ctx["project"], ctx["session"], turn)
    try:
        status, error, _ = live.request({"action": "agent_stop", **ctx, "turn": turn})
        assert status == 409 and error["error_code"] == "stop_not_ready"
        live.app.active_agent_session = live.app.agent_session(live.app.project(ctx["project"]), ctx["session"])
        status, error, _ = live.request({"action": "agent_stop", **ctx, "turn": turn})
        assert status == 409 and error["error_code"] == "stop_not_ready"
        status, error, _ = live.request({"action": "agent_stop", **ctx, "turn": uuid.uuid4().hex})
        assert status == 409 and error["error_code"] == "turn_conflict"
        status, error, _ = live.request({"action": "agent_stop", **other, "turn": turn})
        assert status == 409 and error["error_code"] == "turn_unknown"
    finally:
        live.app.active = live.app.active_agent_session = None


def test_stop_origin_csrf_and_completed_turn_do_not_change_effect(live):
    ctx = live.agent()
    live.provider.responses = [response("done")]
    result = ask(live, ctx)
    payload = {"action": "agent_stop", **ctx, "turn": result["turn_id"]}
    for key, value in (("Origin", "https://outside.invalid"), ("X-Diwan-CSRF", "wrong")):
        headers = live.headers()
        headers[key] = value
        assert live.request(payload, headers=headers)[0] == 403
    assert live.request(payload)[1]["status"] == "not_running"
    assert live.api("history", **ctx, before=None)["turns"][0]["status"] == "complete"
    live.provider.responses = [response("next")]
    assert ask(live, ctx)["content"] == "next"
