"""Local tool provider uses synthetic HTTP only; no model or live network."""
from dataclasses import replace
import json

import pytest

from core.budget import Budget
from core.contracts import Message, ToolCall, ToolSpec
from core.ledger import Ledger
from core.run import execute
from providers.base import ProviderError
from providers.local_chat import LocalChatProvider
from providers.local_tools import LocalToolProvider
import providers.local_chat as local

# Reuse the existing transport fixture, including bounded reads and socket expiry.
from tests.test_local_chat_provider import MODEL, VERSION, Reply, Transport, answer, request, shown, tags

READ = ToolSpec("read_file", "Read a workspace file", {
    "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, consent="auto")


def tool_metadata():
    info = shown()
    info["capabilities"].append("tools")
    return info


def tool_answer():
    out = answer()
    out["message"].update(content="", tool_calls=[{
        "function": {"name": "read_file", "arguments": {"path": "a.txt"}}}])
    return out


def replies(out=None):
    return [Reply(tags()), Reply(tool_metadata()), Reply(tool_answer() if out is None else out)]


def test_two_turn_wire_roundtrip_uses_artifact_preflight_and_preserves_calls(monkeypatch):
    transport = Transport(monkeypatch, replies() + replies(answer()))
    provider = LocalToolProvider(MODEL, VERSION)
    initial = request(tools=(READ,))
    first = provider.complete(initial)
    assert first.provider == "ollama-local-tools" and first.model_version == VERSION
    call = first.tool_calls[0]
    second = provider.complete(replace(initial, idempotency_key="second", messages=(
        *initial.messages, Message("assistant", first.content, tool_calls=first.tool_calls),
        Message("tool", "file contents", tool_call_id=call.call_id))))
    assert second.stop_reason == "complete" and second.tool_calls == ()
    assert (provider.metadata_calls, provider.chat_calls) == (4, 2)
    assert [item[1] for item in transport.calls] == ["/api/tags", "/api/show", "/api/chat"] * 2
    payload = json.loads(transport.calls[-1][2])
    assert payload["messages"][-2]["tool_calls"][0]["id"] == call.call_id
    assert payload["messages"][-1] == {"role": "tool", "content": "file contents",
                                       "tool_call_id": call.call_id, "tool_name": "read_file"}
    assert payload["tools"][0]["function"]["name"] == "read_file"
    assert payload["think"] is False and payload["stream"] is False
    assert payload["truncate"] is False and payload["shift"] is False
    assert all(connection.closed for connection in transport.connections)


def test_missing_tool_capability_refuses_before_chat(monkeypatch):
    transport = Transport(monkeypatch, [Reply(tags()), Reply(shown())])
    provider = LocalToolProvider(MODEL, VERSION)
    with pytest.raises(ProviderError, match="local_tools_capability_missing"):
        provider.complete(request(tools=(READ,)))
    assert (provider.metadata_calls, provider.chat_calls) == (2, 0)
    assert len(transport.calls) == 2


@pytest.mark.parametrize("where", ["tags", "show", "answer"])
def test_remote_artifact_or_response_is_rejected(monkeypatch, where):
    listing, metadata, out = tags(), tool_metadata(), tool_answer()
    target = {"tags": listing["models"][0], "show": metadata, "answer": out}[where]
    target["remote_host"] = "unexpected.invalid"
    transport = Transport(monkeypatch, [Reply(listing), Reply(metadata), Reply(out)])
    provider = LocalToolProvider(MODEL, VERSION)
    with pytest.raises(ProviderError, match="local_chat_remote_model"):
        provider.complete(request(tools=(READ,)))
    assert len(transport.calls) == {"tags": 1, "show": 2, "answer": 3}[where]


def test_changed_artifact_never_receives_prompt(monkeypatch):
    listing = tags()
    listing["models"][0]["digest"] = "b" * 64
    transport = Transport(monkeypatch, [Reply(listing)])
    provider = LocalToolProvider(MODEL, VERSION)
    with pytest.raises(ProviderError, match="local_chat_artifact_mismatch"):
        provider.complete(request(tools=(READ,)))
    assert provider.chat_calls == 0 and len(transport.calls) == 1


@pytest.mark.parametrize("location", ["definition", "historical_arguments", "result"])
def test_tool_material_counts_towards_context_before_network(monkeypatch, location):
    req = request(tools=(READ,))
    if location == "definition":
        req = replace(req, tools=(replace(READ, description="x" * 24001),))
    else:
        call = ToolCall("c1", "read_file", {"path": "x" * 24001 if location == "historical_arguments" else "a.txt"})
        req = replace(req, messages=(Message("assistant", "", tool_calls=(call,)),
            Message("tool", "x" * 24001 if location == "result" else "short", tool_call_id="c1")))
    transport = Transport(monkeypatch, replies())
    with pytest.raises(ProviderError, match="local_chat_context_limit"):
        LocalToolProvider(MODEL, VERSION).complete(req)
    assert transport.calls == []


@pytest.mark.parametrize("field,value,code", [("model", "other", "local_chat_request_identity"),
    ("model_version", "b" * 64, "local_chat_request_identity"),
    ("max_output", 4097, "local_chat_output_limit")])
def test_identity_and_output_limits_precede_transport(monkeypatch, field, value, code):
    transport = Transport(monkeypatch, replies())
    with pytest.raises(ProviderError, match=code):
        LocalToolProvider(MODEL, VERSION).complete(request(**{field: value}))
    assert transport.calls == []


@pytest.mark.parametrize("position", [0, 1, 2])
def test_redirect_never_followed_or_retried(monkeypatch, position):
    responses = replies()
    responses[position] = Reply({}, status=307, headers={"Location": "https://unexpected.invalid"})
    transport = Transport(monkeypatch, responses)
    with pytest.raises(ProviderError, match="local_chat_redirect"):
        LocalToolProvider(MODEL, VERSION).complete(request(tools=(READ,)))
    assert len(transport.calls) == position + 1


def test_endpoint_and_proxy_behavior_are_inherited_without_override(monkeypatch):
    assert LocalToolProvider._json is LocalChatProvider._json
    assert LocalToolProvider._preflight is LocalChatProvider._preflight
    monkeypatch.setenv("HTTP_PROXY", "http://unexpected.invalid")
    monkeypatch.setenv("ALL_PROXY", "http://unexpected.invalid")
    monkeypatch.setenv("OLLAMA_HOST", "http://unexpected.invalid")
    transport = Transport(monkeypatch, replies())
    provider = LocalToolProvider(MODEL, VERSION)
    with pytest.raises(TypeError):
        LocalToolProvider(MODEL, VERSION, base_url="https://unexpected.invalid")
    with pytest.raises(AttributeError):
        provider.base_url = "https://unexpected.invalid"
    provider.complete(request(tools=(READ,)))
    assert len(transport.calls) == 3
    assert "Authorization" not in transport.calls[-1][3]


@pytest.mark.parametrize("reason", ["stop", "length"])
def test_thinking_is_never_returned_or_recorded_and_length_stays_truncated(monkeypatch, tmp_path, reason):
    out = answer()
    out["done_reason"] = reason
    out["message"]["thinking"] = "synthetic-private-reasoning-marker"
    Transport(monkeypatch, replies(out))
    ledger = Ledger(tmp_path / "calls.jsonl")
    result = execute(request(), LocalToolProvider(MODEL, VERSION), Budget(0, 0), ledger)
    assert result.response.stop_reason == ("complete" if reason == "stop" else "max_output")
    assert "synthetic-private-reasoning-marker" not in repr(result.response)
    assert "synthetic-private-reasoning-marker" not in ledger.path.read_text()


def test_tools_with_hidden_thinking_do_not_erase_length_stop(monkeypatch):
    out = tool_answer()
    out["done_reason"] = "length"
    out["message"]["thinking"] = "synthetic-thinking-not-for-display"
    Transport(monkeypatch, replies(out))
    response = LocalToolProvider(MODEL, VERSION).complete(request(tools=(READ,)))
    assert response.stop_reason == "max_output"
    assert len(response.tool_calls) == 1
    assert "synthetic-thinking-not-for-display" not in repr(response)


def test_unsolicited_call_is_refused_when_no_tool_is_currently_declared(monkeypatch):
    transport = Transport(monkeypatch, replies())
    with pytest.raises(ProviderError, match="local_tools_malformed"):
        LocalToolProvider(MODEL, VERSION).complete(request())
    assert len(transport.calls) == 3


@pytest.mark.parametrize("change", [
    {"model": "other"}, {"done": False}, {"done_reason": "unknown"},
    {"message": {"role": "assistant", "content": "", "thinking": []}},
    {"message": {"role": "assistant", "content": "", "audio": ["data"]}},
    {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "not_declared", "arguments": {}}}]}},
    {"prompt_eval_count": None}, {"eval_count": True}, {"images": ["data"]},
])
def test_malformed_generated_answers_never_become_success(monkeypatch, change):
    out = tool_answer()
    out.update(change)
    transport = Transport(monkeypatch, replies(out))
    with pytest.raises(ProviderError, match="local_tools_malformed"):
        LocalToolProvider(MODEL, VERSION).complete(request(tools=(READ,)))
    assert len(transport.calls) == 3


def test_text_provider_stays_text_only(monkeypatch):
    transport = Transport(monkeypatch, replies())
    with pytest.raises(ProviderError, match="local_chat_tools_unsupported"):
        LocalChatProvider(MODEL, VERSION).complete(request(tools=(READ,)))
    assert transport.calls == []


def test_response_byte_limit_and_uncertain_error_are_preserved(monkeypatch, tmp_path):
    transport = Transport(monkeypatch, [Reply(tags()), Reply(tool_metadata()),
        Reply(raw=b"x" * (local.MAX_RESPONSE_BYTES + 1))])
    provider = LocalToolProvider(MODEL, VERSION)
    ledger = Ledger(tmp_path / "calls.jsonl")
    first = execute(request(tools=(READ,)), provider, Budget(0, 0), ledger)
    assert first.error_code == "local_chat_response_large"
    replay = execute(request(tools=(READ,)), provider, Budget(0, 0), ledger)
    assert replay.replayed and replay.error_code == first.error_code
    assert len(transport.calls) == 3 and provider.chat_calls == 1


def test_byte_limit_applies_before_chat_connection(monkeypatch):
    transport = Transport(monkeypatch, replies())
    monkeypatch.setattr(LocalToolProvider, "request_byte_limit", 100)
    provider = LocalToolProvider(MODEL, VERSION)
    with pytest.raises(ProviderError, match="local_chat_request_large"):
        provider.complete(request(tools=(READ,)))
    assert provider.chat_calls == 0 and len(transport.calls) == 2


def test_deadline_closes_slow_response_without_retry(monkeypatch):
    reply = Reply({}, hang=True)
    transport = Transport(monkeypatch, [reply])
    with pytest.raises(ProviderError, match="local_chat_timeout"):
        LocalToolProvider(MODEL, VERSION).complete(request(tools=(READ,), deadline_s=0.03))
    assert len(transport.calls) == 1
    assert reply.closed and transport.connections[0].closed
