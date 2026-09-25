"""Agent execution evidence: actual tmp file effects, durable replay and safe stops."""
from dataclasses import replace

import pytest

from agent.actions import ActionStore
from agent.journal import Journal
from agent.loop import SYSTEM, run_agent
from agent.registry import Tool, ToolContext, ToolRegistry
from core.budget import Budget
from core.canonical import digest
from core.contracts import Message, Request, Response, ToolCall, ToolSpec, Usage
from core.ledger import Ledger
from core.run import RouteRefused, execute


class Scripted:
    name = "scripted"
    is_local = True

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.requests.append(request)
        assert self.responses, "unexpected new model request"
        return self.responses.pop(0)


def answer(*calls, stop="complete", content="fixture answer"):
    return Response(content, Usage(1, 1), stop, 0, provider="scripted",
                    model_version="v1", tool_calls=tuple(calls))


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    journal = Journal(root)
    context = ToolContext(root, journal, frozenset({"auto", "logged"}))
    invoked = []

    def write(arguments, context):
        invoked.append(dict(arguments))
        action = context.journal.write_file(arguments["path"], arguments["content"])
        return {"content": "written", "action_id": action.action_id}

    tool = Tool(ToolSpec("write_note", "Write a reversible note", {
        "path": {"type": "string"}, "content": {"type": "string"}
    }, consent="logged", reversible=True), write)
    return {
        "root": root, "control": control, "context": context,
        "registry": ToolRegistry(tool), "tool": tool, "invoked": invoked,
        "ledger": Ledger(control / "model.jsonl"),
        "store": ActionStore(control / "actions", root),
    }


def run(env, provider, **kwargs):
    options = dict(ledger=env["ledger"], budget=Budget(0, 0),
                   model="fixture", model_version="v1",
                   action_store=env["store"], session_id="session", turn_id="turn")
    options.update(kwargs)
    return run_agent("write the requested note", provider, env["registry"],
                     env["context"], **options)


def call(call_id="c1", path="note.txt", content="original"):
    return ToolCall(call_id, "write_note", {"path": path, "content": content})


@pytest.mark.parametrize("stop,status", [
    ("max_output", "truncated"), ("deadline", "timed_out"),
    ("error", "failed"), ("refused", "refused"),
])
@pytest.mark.parametrize("with_call", [False, True])
def test_noncomplete_response_never_executes_in_live_or_replay(env, stop, status, with_call):
    provider = Scripted(answer(*(call(),) if with_call else (), stop=stop, content="partial"))
    first = run(env, provider)
    replay = run(env, Scripted())
    for result in (first, replay):
        assert result.status == status
        assert result.code == "response_" + stop
        assert result.answer == "partial"
        assert result.steps[0].stop_reason == stop
        assert result.steps[0].tool_results == ()
        assert not any(message.tool_calls for message in result.transcript)
    assert replay.steps[0].replayed
    assert len(provider.requests) == 1
    assert env["invoked"] == []
    assert not (env["root"] / "note.txt").exists()
    assert not list((env["control"] / "actions").glob("step-*.json"))


def test_tools_require_a_durable_store_even_with_charter_permission(env):
    result = run(env, Scripted(answer(call())), action_store=None)
    assert result.status == "refused"
    assert result.code == "action_store_required"
    assert env["invoked"] == []


def test_complete_receipts_replay_verbatim_without_overwriting_a_later_owner_edit(env):
    provider = Scripted(answer(call()), answer(content="done"))
    first = run(env, provider)
    assert first.status == "complete"
    effect = first.steps[0].tool_results[0]
    assert effect["status"] == "ok"
    assert effect["action_id"].startswith("action-")
    assert effect["journal_action_id"].startswith("act-")
    (env["root"] / "note.txt").write_text("owner edit")
    replay = run(env, Scripted())
    assert replay.status == "complete"
    assert replay.steps[0].tool_results == first.steps[0].tool_results
    assert replay.transcript == first.transcript
    assert all(step.replayed for step in replay.steps)
    assert len(env["invoked"]) == 1
    assert (env["root"] / "note.txt").read_text() == "owner edit"


def test_ordered_batch_pauses_at_first_pending_and_resumes_remaining_calls(env):
    owner_effects = []

    def owner(arguments, context):
        owner_effects.append("ran")
        (context.root / "owner.txt").write_text("approved")
        return {"content": "owner effect"}

    env["registry"] = ToolRegistry(env["tool"], Tool(
        ToolSpec("owner_action", "Needs owner decision", {}, consent="owner"), owner))
    calls = (call("c1", "before.txt"), ToolCall("c2", "owner_action", {}),
             call("c3", "after.txt"))
    provider = Scripted(answer(*calls), answer(content="done"))
    first = run(env, provider)
    assert first.status == "awaiting_owner"
    assert [result["call_id"] for result in first.steps[0].tool_results] == ["c1", "c2"]
    assert (env["root"] / "before.txt").exists()
    assert not (env["root"] / "after.txt").exists()
    assert not (env["root"] / "owner.txt").exists()
    assert len(provider.requests) == 1
    pending = first.pending[0]
    env["store"].decide(pending["action_id"], pending["call_digest"],
                        pending["revision"], approve=True)
    resumed = run(env, provider)
    assert resumed.status == "complete"
    assert resumed.steps[0].replayed
    assert len(env["invoked"]) == 2
    assert owner_effects == ["ran"]
    assert (env["root"] / "after.txt").exists()
    assert provider.requests[0].idempotency_key
    messages = provider.requests[1].messages
    assert [message.tool_calls for message in messages if message.tool_calls] == [calls]
    assert [message.tool_call_id for message in messages if message.role == "tool"] == ["c1", "c2", "c3"]


def test_crash_after_real_file_effect_is_outcome_unknown_and_never_repeated(env):
    class ProcessLost(BaseException):
        pass

    invocations = []

    def crash(arguments, context):
        invocations.append("ran")
        (context.root / "effect.txt").write_text("effect before process loss")
        raise ProcessLost()

    env["registry"] = ToolRegistry(Tool(
        ToolSpec("crash_after_write", "Crash fixture", {}, consent="auto"), crash))
    with pytest.raises(ProcessLost):
        run(env, Scripted(answer(ToolCall("c1", "crash_after_write", {}))))
    assert (env["root"] / "effect.txt").exists()
    env["store"] = ActionStore(env["control"] / "actions", env["root"])
    resumed = run(env, Scripted())
    assert resumed.status == "outcome_unknown"
    assert resumed.code == "action_outcome_unknown"
    assert resumed.steps[0].replayed
    assert invocations == ["ran"]
    assert env["store"].pending()[0]["state"] == "outcome_unknown"


@pytest.mark.parametrize("same_batch", [False, True])
def test_duplicate_tool_ids_are_refused_before_the_duplicate_effect(env, same_batch):
    first, second = call("same-id", "one.txt"), call("same-id", "two.txt")
    provider = Scripted(answer(first, second)) if same_batch else Scripted(answer(first), answer(second))
    result = run(env, provider)
    assert result.status == "refused"
    assert result.code == ("tool_call_id_duplicate" if same_batch else "tool_call_id_reused")
    assert len(env["invoked"]) == (0 if same_batch else 1)
    assert not (env["root"] / "two.txt").exists()


def initial_request(env):
    return Request((Message("system", SYSTEM), Message("user", "write the requested note")),
                   "fixture", "v1", 1024, 120.0, "local_only", None,
                   tools=env["registry"].specs())


def historical_request(env):
    request = initial_request(env)
    key = "historical-" + digest([[m.role, m.content] for m in request.messages])[:24]
    return replace(request, idempotency_key=key)


def test_historical_model_replay_without_action_plan_never_executes_tools(env):
    request = historical_request(env)
    execute(request, Scripted(answer(call())), Budget(0, 0), env["ledger"])
    result = run(env, Scripted(), idempotency_prefix="historical")
    assert result.status == "refused"
    assert result.code == "action_receipt_missing"
    assert result.steps[0].replayed
    assert env["invoked"] == []


def test_historical_plain_text_keeps_the_old_key_and_replays(env):
    execute(historical_request(env), Scripted(answer(content="historical text")), Budget(0, 0), env["ledger"])
    result = run(env, Scripted(), idempotency_prefix="historical", action_store=None)
    assert result.status == "complete"
    assert result.answer == "historical text"
    assert result.steps[0].replayed


@pytest.mark.parametrize("changed", [
    {"model": "other"}, {"model_version": "v2"}, {"max_output": 2000},
    {"data_policy": "public"}, {"system": "other system"},
])
def test_idempotency_covers_fixed_request_fields_beyond_plain_text(env, changed):
    first = Scripted(answer())
    second = Scripted(answer())
    assert run(env, first, action_store=None, idempotency_prefix="p").status == "complete"
    assert run(env, second, action_store=None, idempotency_prefix="p", **changed).status == "complete"
    assert first.requests[0].idempotency_key != second.requests[0].idempotency_key


def test_tool_ids_and_arguments_in_history_change_the_request_key(env):
    keys = []
    for call_id, path in [("old1", "a.txt"), ("old2", "a.txt"), ("old2", "b.txt")]:
        previous = call(call_id, path)
        history = (Message("system", SYSTEM), Message("user", "previous task"),
                   Message("assistant", "same text", tool_calls=(previous,)),
                   Message("tool", "same result", tool_call_id=call_id),
                   Message("assistant", "previous done"))
        provider = Scripted(answer())
        result = run(env, provider, action_store=None, initial_messages=history,
                     idempotency_prefix="p")
        assert result.status == "complete"
        assert result.transcript[:len(history)] == history
        keys.append(provider.requests[0].idempotency_key)
    assert len(set(keys)) == 3


def test_tool_declarations_are_part_of_the_request_key(env):
    first = Scripted(answer())
    assert run(env, first, action_store=None).status == "complete"
    env["registry"] = ToolRegistry(Tool(replace(env["tool"].spec, description="changed declaration"),
                                         env["tool"].run))
    second = Scripted(answer())
    assert run(env, second, action_store=None).status == "complete"
    assert first.requests[0].idempotency_key != second.requests[0].idempotency_key


def test_invalid_replayed_response_is_rejected_before_any_effect(env):
    request = replace(initial_request(env), idempotency_key="oldkey")
    env["ledger"].append({"kind": "ok", "idempotency_key": "oldkey",
        "request_digest": digest(request.fingerprint_payload()),
        "response": {"content": "", "usage": {"input_tokens": 0, "output_tokens": 0},
                     "stop_reason": "complete", "cost_micros": 0,
                     "tool_calls": [{"call_id": "c1"}]}})
    with pytest.raises(RouteRefused, match="replay_response_invalid"):
        execute(request, Scripted(), Budget(0, 0), env["ledger"])
    assert env["invoked"] == []


def test_missing_action_receipt_blocks_later_effects_in_a_saved_batch(env):
    provider = Scripted(answer(call("c1", "one.txt"), call("c2", "two.txt")), answer())
    first = run(env, provider)
    assert first.status == "complete"
    receipt = first.steps[0].tool_results[0]["action_id"]
    (env["control"] / "actions" / (receipt + ".json")).unlink()
    (env["root"] / "two.txt").write_text("owner edit")
    resumed = run(env, Scripted())
    assert resumed.status == "refused"
    assert resumed.code == "action_receipt_missing"
    assert [item["call_id"] for item in resumed.steps[0].tool_results] == ["c1"]
    assert len(env["invoked"]) == 2
    assert (env["root"] / "two.txt").read_text() == "owner edit"
