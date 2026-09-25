from __future__ import annotations

from dataclasses import replace

import pytest

from core.canonical import digest
from core.contracts import Message, Request, ToolCall, ToolSpec
from providers.base import ProviderError
from providers.ollama import OllamaProvider


READ = ToolSpec("read_file", "Read a workspace file", {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}, consent="auto")


def request(*, tools=()):
    return Request(
        messages=(Message("user", "Read README.md"),),
        model="fixture",
        model_version="v1",
        max_output=128,
        deadline_s=5,
        data_policy="local_only",
        idempotency_key=None,
        tools=tuple(tools),
    )


def answer(*, calls=None):
    return {
        "model": "fixture",
        "message": {
            "role": "assistant",
            "content": "",
            **({"tool_calls": calls} if calls is not None else {}),
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 1,
        "eval_count": 1,
    }


def test_declared_tools_use_ollama_function_schema(monkeypatch):
    seen = {}
    provider = OllamaProvider("fixture")

    def post(payload, timeout):
        seen.update(payload)
        return answer()

    monkeypatch.setattr(provider, "_post", post)
    provider.complete(request(tools=(READ,)))

    assert seen["tools"] == [{
        "type": "function",
        "function": {
            "name": READ.name,
            "description": READ.description,
            "parameters": READ.parameters,
        },
    }]


def test_tools_are_omitted_when_none_are_declared(monkeypatch):
    seen = {}
    provider = OllamaProvider("fixture")
    monkeypatch.setattr(provider, "_post",
                        lambda payload, timeout: (seen.update(payload) or answer()))

    provider.complete(request())

    assert "tools" not in seen


def test_valid_ollama_tool_call_is_mapped_to_contract():
    response = OllamaProvider("fixture")._to_response(
        answer(calls=[{"function": {"name": "read_file",
                                    "arguments": {"path": "README.md"}}}]),
        request=request(tools=(READ,)),
    )

    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
    assert response.tool_calls[0].call_id == f"ollama-{digest(request(tools=(READ,)).fingerprint_payload())[:40]}-0"


@pytest.mark.parametrize("calls", [
    [{}],
    [{"function": {"name": "Read_File", "arguments": {}}}],
    [{"function": {"name": "read_file", "arguments": "not-json"}}],
    [{"id": "../escape", "function": {"name": "read_file", "arguments": {}}}],
])
def test_malformed_ollama_tool_calls_fail_closed(calls):
    with pytest.raises(ProviderError) as exc:
        OllamaProvider("fixture")._to_response(
            answer(calls=calls), request=request(tools=(READ,)))
    assert exc.value.code == "malformed"


def test_unknown_ollama_tool_call_fails_closed():
    with pytest.raises(ProviderError) as exc:
        OllamaProvider("fixture")._to_response(
            answer(calls=[{"function": {"name": "delete_everything",
                                         "arguments": {}}}]),
            request=request(tools=(READ,)))
    assert exc.value.code == "malformed"


def test_complete_transcript_reaches_ollama_with_ids_names_and_arguments(monkeypatch):
    calls = (ToolCall("c1", "read_file", {"path": "a.txt"}),
             ToolCall("c2", "read_file", {"path": "b.txt"}))
    history = (Message("user", "اقرأ الملفين"), Message("assistant", "أقرأ", tool_calls=calls),
               Message("tool", "B", tool_call_id="c2"), Message("tool", "A", tool_call_id="c1"))
    provider, seen = OllamaProvider("fixture"), []
    monkeypatch.setattr(provider, "_post", lambda payload, timeout: (seen.append(payload) or answer()))
    provider.complete(replace(request(tools=(READ,)), messages=history))
    assert seen[0]["messages"] == [
        {"role": "user", "content": "اقرأ الملفين"},
        {"role": "assistant", "content": "أقرأ", "tool_calls": [
            {"id": "c1", "function": {"index": 0, "name": "read_file", "arguments": {"path": "a.txt"}}},
            {"id": "c2", "function": {"index": 1, "name": "read_file", "arguments": {"path": "b.txt"}}}]},
        {"role": "tool", "content": "B", "tool_call_id": "c2", "tool_name": "read_file"},
        {"role": "tool", "content": "A", "tool_call_id": "c1", "tool_name": "read_file"}]


def test_missing_ids_are_stable_for_replay_and_distinct_between_turns():
    provider = OllamaProvider("fixture")
    raw = answer(calls=[{"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}])
    initial = request(tools=(READ,))
    first = provider._to_response(raw, request=initial).tool_calls[0]
    repeated = provider._to_response(raw, request=replace(initial, deadline_s=10, idempotency_key="other")).tool_calls[0]
    next_request = replace(initial, messages=(*initial.messages, Message("assistant", "", tool_calls=(first,)),
                                             Message("tool", "result", tool_call_id=first.call_id)))
    second = provider._to_response(raw, request=next_request).tool_calls[0]
    assert first.call_id == repeated.call_id
    assert first.call_id != second.call_id
    assert len(first.call_id) <= 64


def test_explicit_id_cannot_reuse_a_historical_call_id():
    old = ToolCall("old-id", "read_file", {"path": "a.txt"})
    req = replace(request(tools=(READ,)), messages=(Message("assistant", "", tool_calls=(old,)),
                                                    Message("tool", "done", tool_call_id=old.call_id)))
    with pytest.raises(ProviderError, match="malformed"):
        OllamaProvider("fixture")._to_response(answer(calls=[
            {"id": old.call_id, "function": {"name": "read_file", "arguments": {"path": "b.txt"}}}]), request=req)


def test_response_preserves_explicit_call_id():
    raw = answer(calls=[{"id": "native-id", "function": {"name": "read_file", "arguments": {}}}])
    response = OllamaProvider("fixture")._to_response(raw, request=request(tools=(READ,)))
    assert response.tool_calls[0].call_id == "native-id"


def test_idless_tool_response_without_request_context_is_refused():
    with pytest.raises(ProviderError, match="malformed"):
        OllamaProvider("fixture")._to_response(answer(calls=[
            {"function": {"name": "read_file", "arguments": {}}}]))


def test_orphan_history_is_refused_before_network(monkeypatch):
    from core.canonical import PayloadRejected
    provider = OllamaProvider("fixture")
    monkeypatch.setattr(provider, "_post", lambda *a: pytest.fail("network reached"))
    req = replace(request(), messages=(Message("tool", "orphan", tool_call_id="c1"),))
    with pytest.raises(PayloadRejected, match="tool_result_orphan"):
        provider.complete(req)


def test_noncanonical_response_arguments_are_refused():
    with pytest.raises(ProviderError, match="malformed"):
        OllamaProvider("fixture")._to_response(answer(calls=[
            {"function": {"name": "read_file", "arguments": {"path": float("nan")}}}]),
            request=request(tools=(READ,)))
