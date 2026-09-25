"""Tool transcripts remain structural through fingerprints and text-provider gates."""
from dataclasses import replace

import pytest

from core.canonical import PayloadRejected, digest
from core.contracts import Message, Request, ToolCall
from core.validate import validated
from providers.base import ProviderError
from providers.echo import EchoProvider
from providers.local_chat import LocalChatProvider
from providers.local_media import LocalMediaProvider
from providers.mlx_provider import MLXProvider

MODEL, VERSION = "synthetic-local:1", "a" * 64
CALL = ToolCall("c1", "read_file", {"path": "a.txt"})


def request(messages):
    return Request(tuple(messages), MODEL, VERSION, 128, 5, "local_only", None)


def closed_history(*calls):
    return (Message("user", "اقرأ"), Message("assistant", "", tool_calls=calls),
            *(Message("tool", "نتيجة", tool_call_id=call.call_id) for call in reversed(calls)))


def test_text_history_has_exact_legacy_fingerprint_payload():
    req = request((Message("system", "نظام"), Message("user", "سؤال"), Message("assistant", "جواب")))
    expected = {"messages": [{"role": "system", "content": "نظام"},
                             {"role": "user", "content": "سؤال"},
                             {"role": "assistant", "content": "جواب"}],
        "model": MODEL, "model_version": VERSION, "max_output": 128,
        "data_policy": "local_only", "tools": [], "schema_version": 1}
    assert req.fingerprint_payload() == expected
    assert digest(req.fingerprint_payload()) == digest(expected)
    assert validated(req) is req


def test_tool_history_fingerprint_includes_call_arguments_and_ids():
    req = request(closed_history(CALL))
    assert validated(req) is req
    assert req.fingerprint_payload()["messages"][1]["tool_calls"] == [CALL.declared()]
    changed_args = request(closed_history(replace(CALL, arguments={"path": "b.txt"})))
    changed_id = request(closed_history(replace(CALL, call_id="c2")))
    assert len({digest(r.fingerprint_payload()) for r in (req, changed_args, changed_id)}) == 3


def test_parallel_results_can_arrive_in_different_order_and_old_tools_need_not_be_available():
    req = request((*closed_history(CALL, ToolCall("c2", "read_file", {"path": "b.txt"})),
                   Message("assistant", "قرأت الملفين"), Message("user", "تابع")))
    assert req.tools == ()
    assert validated(req) is req


@pytest.mark.parametrize("messages,code", [
    ((Message("tool", "orphan", tool_call_id="c1"),), "tool_result_orphan"),
    ((Message("assistant", "", tool_calls=(CALL,)),), "tool_results_pending"),
    ((Message("assistant", "", tool_calls=(CALL,)), Message("user", "next")), "tool_results_pending"),
    ((Message("assistant", "", tool_calls=(CALL,)), Message("assistant", "fake final")), "tool_results_pending"),
    ((Message("assistant", "", tool_calls=(CALL,)), Message("system", "next")), "tool_results_pending"),
    ((Message("assistant", "", tool_calls=(CALL,)), Message("tool", "wrong", tool_call_id="c2")), "tool_result_orphan"),
    ((*closed_history(CALL), Message("tool", "again", tool_call_id="c1")), "tool_result_duplicate"),
    ((*closed_history(CALL), Message("assistant", "", tool_calls=(CALL,))), "tool_call_id_duplicate"),
    ((Message("assistant", "", tool_calls=(CALL, CALL)),), "tool_call_id_duplicate"),
    ((Message("user", "", tool_calls=(CALL,)),), "tool_calls_unexpected"),
    ((Message("system", "", tool_calls=(CALL,)),), "tool_calls_unexpected"),
    ((Message("tool", "", tool_call_id="c1", tool_calls=(CALL,)),), "tool_calls_unexpected"),
    ((Message("assistant", "", tool_calls=[]),), "tool_calls_type"),
    ((Message("assistant", "", tool_calls=({},)),), "tool_call_type"),
    ((Message("assistant", "", tool_calls=(replace(CALL, call_id="../id"),)),), "tool_call_id_invalid"),
    ((Message("assistant", "", tool_calls=(replace(CALL, name="Bad-Name"),)),), "tool_name"),
    ((Message("assistant", "", tool_calls=(replace(CALL, arguments=[]),)),), "tool_call_arguments"),
    ((Message("assistant", "", tool_calls=(replace(CALL, arguments={"x": float("nan")}),)),), "float_not_allowed"),
])
def test_orphan_duplicate_incomplete_or_malformed_history_is_refused(messages, code):
    with pytest.raises(PayloadRejected) as raised:
        validated(request(messages))
    assert raised.value.code == code


@pytest.mark.parametrize("provider,code", [
    (LocalChatProvider(MODEL, VERSION), "local_chat_tools_unsupported"),
    (LocalMediaProvider(MODEL, VERSION), "local_media_tools_unsupported"),
    (MLXProvider("synthetic"), "tools_unsupported"),
    (EchoProvider(), "tools_unsupported"),
])
def test_text_only_provider_rejects_closed_tool_history_before_transport_or_loading(monkeypatch, provider, code):
    def forbidden(*args, **kwargs):
        pytest.fail("tool history reached text transport or model loader")
    monkeypatch.setattr(LocalChatProvider, "_json", forbidden)
    monkeypatch.setattr(MLXProvider, "_ensure_loaded", forbidden)
    req = request(closed_history(CALL))
    assert req.tools == ()
    with pytest.raises(ProviderError) as raised:
        provider.complete(req)
    assert raised.value.code == code
    assert raised.value.retryable is False
