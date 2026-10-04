"""Real session/provider boundary, synthetic payloads, no model or SDK required."""
import json

import pytest

from agent.registry import ToolRegistry
from conversation.agent_session import AgentSession
from conversation.session import ChatSession, ConversationError
from conversation.observability import observed, safe_metadata, FIELDS, LocalLangfuseObserver
from core.contracts import Request, Message, Response, Usage
from providers.base import ProviderError

CANARY = "SYNTHETIC_PRIVATE_CANARY_DO_NOT_EXPORT"


class Provider:
    is_local = True
    name = "synthetic"

    def __init__(self, failure=None):
        self.calls = 0
        self.failure = failure
        self.result = Response(CANARY, Usage(7, 3), "complete", 0)

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.result


def request():
    return Request((Message("user", CANARY),), CANARY, CANARY, 100, 30, "local_only", "key")


def broken(event):
    raise RuntimeError(CANARY)


def test_disabled_preserves_provider_identity():
    p = Provider()
    assert observed(p, None) is p


def test_allowlist_rejects_payloads_and_invalid_numbers():
    event = safe_metadata({"model": [CANARY], "error_code": {"detail": CANARY},
        "duration_ms": 10 ** 400, "input_tokens": True, "output_tokens": float("nan"),
        "prompt": CANARY, "exception": CANARY})
    assert set(event) == set(FIELDS)
    assert event == {"duration_ms": None, "model": "unknown_model", "error_code": "other_error",
                     "input_tokens": None, "output_tokens": None}
    assert CANARY not in json.dumps(event)


def test_provider_result_and_usage_preserved_without_payload():
    events, p = [], Provider()
    wrapper = observed(p, events.append)
    assert wrapper.estimate_micros(request()) == 0
    assert wrapper.complete(request()) is p.result
    assert p.calls == 1 and wrapper.is_local is True
    assert len(events) == 1 and events[0]["input_tokens"] == 7
    assert events[0]["output_tokens"] == 3 and events[0]["duration_ms"] >= 0
    assert events[0]["error_code"] == "none"
    assert CANARY not in json.dumps(events)


def test_provider_error_identity_and_named_code_preserved():
    events = []
    original = ProviderError("provider_timeout", CANARY, False)
    with pytest.raises(ProviderError) as exc:
        observed(Provider(original), events.append).complete(request())
    assert exc.value is original
    assert events[0]["error_code"] == "provider_timeout"
    assert events[0]["input_tokens"] is None
    assert CANARY not in json.dumps(events)


def test_observer_failure_keeps_success_result():
    p = Provider()
    assert observed(p, broken).complete(request()) is p.result


def test_observer_failure_keeps_original_exception():
    error = ValueError(CANARY)
    with pytest.raises(ValueError) as exc:
        observed(Provider(error), broken).complete(request())
    assert exc.value is error


def test_chat_replay_does_not_emit_or_generate_again(tmp_path):
    events, p = [], Provider()
    s = ChatSession(tmp_path.resolve() / "chat", "s", model="synthetic", model_version="v1",
                    observer=events.append)
    first = s.turn("t1", CANARY, p)
    assert first["status"] == "complete" and len(events) == 1
    reopened = ChatSession(tmp_path.resolve() / "chat", "s", model="synthetic", model_version="v1",
                           observer=events.append)
    assert reopened.turn("t1", CANARY, p) == {**first, "replayed": True}
    assert p.calls == 1 and len(events) == 1
    assert CANARY not in json.dumps(events)


def test_chat_observer_failure_keeps_durable_result(tmp_path):
    s = ChatSession(tmp_path.resolve() / "chat", "s", model="synthetic", model_version="v1", observer=broken)
    result = s.turn("t1", CANARY, Provider())
    assert result["status"] == "complete" and result["content"] == CANARY
    assert s.history()[0]["content"] == CANARY


def test_observation_cannot_make_remote_provider_local(tmp_path):
    p, events = Provider(), []
    p.is_local = False
    assert observed(p, events.append).is_local is False
    s = ChatSession(tmp_path.resolve() / "chat", "s", model="synthetic", model_version="v1",
                    observer=events.append)
    with pytest.raises(ConversationError, match="policy_requires_local"):
        s.turn("t1", CANARY, p)
    assert p.calls == 0 and events == []


def test_agent_session_real_boundary_and_replay(tmp_path):
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    events, p = [], Provider()
    s = AgentSession(tmp_path.resolve() / "control", "s", workspace_root=workspace,
        project_id="p", registry=ToolRegistry(), model="synthetic", model_version="v1", observer=events.append)
    result = s.start_turn("t1", CANARY, p)
    assert result["status"] == "complete" and result["content"] == CANARY
    assert len(events) == 1 and events[0]["model"] == "synthetic"
    assert s.start_turn("t1", CANARY, p) == result
    assert s.resume("t1", p) == result
    assert p.calls == 1 and len(events) == 1 and CANARY not in json.dumps(events)


def test_agent_session_observer_failure_keeps_success(tmp_path):
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    s = AgentSession(tmp_path.resolve() / "control", "s", workspace_root=workspace,
        project_id="p", registry=ToolRegistry(), model="synthetic", model_version="v1", observer=broken)
    result = s.start_turn("t1", CANARY, Provider())
    assert result["status"] == "complete" and result["content"] == CANARY


def test_response_error_is_distinct_from_success():
    p, events = Provider(), []
    p.result = Response("", Usage(0, 0), "error", 0, retryable_error="temporary")
    assert observed(p, events.append).complete(request()) is p.result
    assert events[0]["error_code"] == "response_error"


def test_local_collector_capacity_rejects_unbounded_or_boolean_values(monkeypatch):
    import builtins
    original_import = builtins.__import__
    def no_optional_sdk(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("opentelemetry"):
            pytest.fail("invalid capacity reached an optional SDK import")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_optional_sdk)
    for capacity in (0, 1025, True, 1.5):
        with pytest.raises(ValueError, match="observer_capacity_invalid"):
            LocalLangfuseObserver(capacity)


def test_exception_cannot_label_itself_success():
    events = []
    with pytest.raises(ProviderError):
        observed(Provider(ProviderError("none", CANARY, False)), events.append).complete(request())
    assert events[0]["error_code"] == "operation_error"


def test_error_metadata_getter_cannot_replace_original_exception():
    class OriginalError(Exception):
        @property
        def code(self):
            raise RuntimeError(CANARY)
    events, original = [], OriginalError("synthetic original")
    with pytest.raises(OriginalError) as caught:
        observed(Provider(original), events.append).complete(request())
    assert caught.value is original and events == []


def test_response_metadata_getter_cannot_replace_original_result():
    class FlagResponse(Response):
        def __getattribute__(self, name):
            if name == "retryable_error":
                raise RuntimeError(CANARY)
            return super().__getattribute__(name)
    events, provider = [], Provider()
    provider.result = FlagResponse(CANARY, Usage(7, 3), "complete", 0)
    assert observed(provider, events.append).complete(request()) is provider.result
    assert provider.calls == 1 and events == []


def test_clock_preparation_failure_keeps_provider_result_with_unknown_duration(monkeypatch):
    from types import SimpleNamespace
    import conversation.observability as module
    def fail():
        raise OSError(CANARY)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=fail))
    events, provider = [], Provider()
    assert observed(provider, events.append).complete(request()) is provider.result
    assert provider.calls == 1 and len(events) == 1
    assert events[0]["duration_ms"] is None
    assert CANARY not in json.dumps(events)


def test_wrapper_does_not_eagerly_read_provider_name():
    class LazyNameProvider(Provider):
        @property
        def name(self):
            raise RuntimeError(CANARY)
    provider, events = LazyNameProvider(), []
    assert observed(provider, events.append).complete(request()) is provider.result
    assert provider.calls == 1 and CANARY not in json.dumps(events)


def test_wrapper_checks_current_provider_locality_instead_of_cached_local_flag():
    provider = Provider()
    wrapper = observed(provider, lambda event: None)
    assert wrapper.is_local is True
    provider.is_local = False
    assert wrapper.is_local is False
