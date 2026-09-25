"""عقود المزود المحلي بوسيط HTTP مصطنع؛ لا اتصال حي في الاختبارات."""
from __future__ import annotations

from dataclasses import replace
import http.client
import json
import socket
import threading

import pytest

from core.budget import Budget
from core.contracts import Message, Request, ToolSpec
from core.ledger import Ledger
from core.run import execute
from providers.base import ProviderError
from providers.local_chat import LocalChatProvider
import providers.local_chat as local


MODEL = "synthetic-local:1"
VERSION = "a" * 64


def request(**changes):
    return replace(Request((Message("user", "رسالة عربية خاصة"),), MODEL, VERSION,
                           800, 30, "local_only", "synthetic-turn"), **changes)


def tags():
    return {"models": [{"name": MODEL, "model": MODEL, "digest": VERSION,
                        "size": 1234, "details": {"format": "gguf"}}]}


def shown():
    return {"details": {"format": "gguf"},
            "model_info": {"general.architecture": "synthetic", "synthetic.context_length": 32768},
            "capabilities": ["completion", "thinking"]}


def answer():
    return {"model": MODEL, "message": {"role": "assistant", "content": "جواب محلي"},
            "done": True, "done_reason": "stop", "prompt_eval_count": 8, "eval_count": 3}


class FakeSocket:
    def __init__(self):
        self.shut = threading.Event()

    def shutdown(self, direction):
        assert direction == socket.SHUT_RDWR
        self.shut.set()


class Reply:
    def __init__(self, payload=None, *, raw=None, status=200, headers=None, hang=False):
        self.raw = raw if raw is not None else json.dumps(payload).encode()
        self.status = status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.closed = False
        self.hang = hang
        self.sock = None

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, size):
        assert size == local.MAX_RESPONSE_BYTES + 1
        if self.hang:
            assert self.sock.shut.wait(2), "deadline did not shut down socket"
            raise OSError("socket shut down")
        return self.raw[:size]

    def close(self):
        self.closed = True


class Transport:
    def __init__(self, monkeypatch, replies=None):
        self.replies = list(replies if replies is not None else
                            [Reply(tags()), Reply(shown()), Reply(answer())])
        self.calls = []
        self.connections = []
        outer = self

        class Connection:
            def __init__(self, host, port, *, timeout):
                assert host == "127.0.0.1"
                assert port == 11434
                assert 0 < timeout <= local.MAX_DEADLINE_S
                self.timeout = timeout
                self.sock = FakeSocket()
                self.closed = False
                outer.connections.append(self)

            def connect(self):
                pass

            def request(self, method, path, *, body, headers):
                outer.calls.append((method, path, body, headers))

            def getresponse(self):
                reply = outer.replies.pop(0)
                if isinstance(reply, BaseException):
                    raise reply
                reply.sock = self.sock
                # Match HTTPConnection behavior for a closing response.
                self.sock = None
                return reply

            def close(self):
                self.closed = True

        monkeypatch.setattr(local.http.client, "HTTPConnection", Connection)


def assert_error(provider, req, code):
    with pytest.raises(ProviderError) as exc:
        provider.complete(req)
    assert exc.value.code == code
    assert exc.value.retryable is False
    return exc.value


def test_governed_call_preflights_before_private_messages_and_replay_has_no_http(monkeypatch, tmp_path):
    transport = Transport(monkeypatch)
    provider = LocalChatProvider(MODEL, VERSION)
    ledger = Ledger(tmp_path / "calls.jsonl")
    outcome = execute(request(), provider, Budget(0, 0), ledger)
    assert outcome.response.content == "جواب محلي"
    assert outcome.response.provider == "ollama-local-chat"
    assert outcome.response.model_version == VERSION
    assert outcome.response.usage.input_tokens == 8
    assert (provider.chat_calls, provider.metadata_calls) == (1, 2)
    assert [c[1] for c in transport.calls] == ["/api/tags", "/api/show", "/api/chat"]
    assert transport.calls[0][2] is None
    assert json.loads(transport.calls[1][2]) == {"model": MODEL}
    payload = json.loads(transport.calls[2][2])
    assert payload["messages"] == [{"role": "user", "content": "رسالة عربية خاصة"}]
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["truncate"] is False
    assert payload["shift"] is False
    assert payload["options"] == {"num_predict": 800, "temperature": 0,
                                  "num_ctx": 32768, "seed": 0}
    replay = execute(request(), provider, Budget(0, 0), ledger)
    assert replay.replayed and replay.response == outcome.response
    assert (provider.chat_calls, provider.metadata_calls) == (1, 2)
    assert all(c.closed for c in transport.connections)


def test_proxy_environment_is_not_a_transport_configuration(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:9999")
    monkeypatch.setenv("ALL_PROXY", "http://example.invalid:9999")
    monkeypatch.setenv("OLLAMA_HOST", "http://example.invalid:9999")
    transport = Transport(monkeypatch)
    LocalChatProvider(MODEL, VERSION).complete(request())
    assert len(transport.calls) == 3
    assert "Authorization" not in transport.calls[-1][3]


@pytest.mark.parametrize("model", [None, "", " spaced", "https://example.invalid/a", "x\n", "../x", "x/../y", "x/", "cloud", "synthetic:cloud", "synthetic-cloud"])
def test_bad_or_remote_model_refused_without_network(model):
    with pytest.raises(ProviderError):
        LocalChatProvider(model, VERSION)


@pytest.mark.parametrize("version", [None, "", "a" * 63, "a" * 65, "A" * 64, "z" * 64, "sha256:" + VERSION])
def test_complete_lowercase_artifact_digest_required(version):
    with pytest.raises(ProviderError) as exc:
        LocalChatProvider(MODEL, version)
    assert exc.value.code == "local_chat_artifact_invalid"


def test_endpoint_not_configurable():
    with pytest.raises(TypeError):
        LocalChatProvider(MODEL, VERSION, base_url="http://example.invalid")
    provider = LocalChatProvider(MODEL, VERSION)
    for key, value in [("base_url", "http://example.invalid"), ("model", "different"),
                       ("model_version", "b" * 64), ("is_local", False)]:
        with pytest.raises(AttributeError):
            setattr(provider, key, value)


@pytest.mark.parametrize("changes,code", [
    ({"model": "different"}, "local_chat_request_identity"),
    ({"model_version": "b" * 64}, "local_chat_request_identity"),
    ({"tools": (ToolSpec("probe", "أداةٌ صوريّة داخل اختبار", {"type": "object"},
                consent="auto"),)}, "local_chat_tools_unsupported"),
    ({"max_output": 4097}, "local_chat_output_limit"),
    ({"messages": (Message("user", "x" * 24001),)}, "local_chat_context_limit"),
])
def test_request_limits_apply_before_network(monkeypatch, changes, code):
    transport = Transport(monkeypatch)
    assert_error(LocalChatProvider(MODEL, VERSION), request(**changes), code)
    assert not transport.calls


@pytest.mark.parametrize("change,code", [
    ({"name": "other"}, "local_chat_model_missing"),
    ({"model": "other"}, "local_chat_model_mismatch"),
    ({"digest": "b" * 64}, "local_chat_artifact_mismatch"),
    ({"size": 0}, "local_chat_artifact_invalid"),
    ({"size": True}, "local_chat_artifact_invalid"),
    ({"details": {}}, "local_chat_artifact_invalid"),
    ({"remote_host": ""}, "local_chat_remote_model"),
    ({"remote_model": None}, "local_chat_remote_model"),
    ({"cloud": False}, "local_chat_remote_model"),
    ({"details": {"format": "gguf", "Remote-Host": "remote"}}, "local_chat_remote_model"),
])
def test_invalid_selected_artifact_cannot_receive_prompt(monkeypatch, change, code):
    listing = tags()
    listing["models"][0].update(change)
    transport = Transport(monkeypatch, [Reply(listing)])
    provider = LocalChatProvider(MODEL, VERSION)
    assert_error(provider, request(), code)
    assert (provider.chat_calls, provider.metadata_calls) == (0, 1)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("listing,code", [
    ({}, "local_chat_malformed"), ({"models": {}}, "local_chat_malformed"),
    ({"models": [None]}, "local_chat_malformed"), ({"models": []}, "local_chat_model_missing"),
    ({"models": tags()["models"] * 2}, "local_chat_model_missing"),
])
def test_bad_or_ambiguous_tags_closed(monkeypatch, listing, code):
    transport = Transport(monkeypatch, [Reply(listing)])
    assert_error(LocalChatProvider(MODEL, VERSION), request(), code)
    assert len(transport.calls) == 1


def test_unrelated_installed_cloud_model_does_not_block_local_artifact(monkeypatch):
    listing = tags()
    listing["models"].append({"name": "unrelated:cloud", "remote_host": "cloud.invalid"})
    transport = Transport(monkeypatch, [Reply(listing), Reply(shown()), Reply(answer())])
    LocalChatProvider(MODEL, VERSION).complete(request())
    assert len(transport.calls) == 3


@pytest.mark.parametrize("change,code", [
    ({"remote_model": "remote"}, "local_chat_remote_model"),
    ({"remote_host": ""}, "local_chat_remote_model"),
    ({"capabilities": ["completion", {"cloud": True}]}, "local_chat_remote_model"),
    ({"capabilities": ["embedding"]}, "local_chat_artifact_invalid"),
    ({"details": {"format": "other"}}, "local_chat_artifact_invalid"),
    ({"model_info": {}}, "local_chat_artifact_invalid"),
    ({"model_info": {"general.architecture": "synthetic", "synthetic.context_length": True}}, "local_chat_context_unsupported"),
    ({"model_info": {"general.architecture": "synthetic", "synthetic.context_length": 32767}}, "local_chat_context_unsupported"),
])
def test_show_is_checked_before_private_chat(monkeypatch, change, code):
    details = shown()
    details.update(change)
    transport = Transport(monkeypatch, [Reply(tags()), Reply(details)])
    provider = LocalChatProvider(MODEL, VERSION)
    assert_error(provider, request(), code)
    assert (provider.chat_calls, provider.metadata_calls) == (0, 2)
    assert len(transport.calls) == 2


def test_unsupported_thinking_omitted_without_retry(monkeypatch):
    details = shown()
    details["capabilities"] = ["completion"]
    transport = Transport(monkeypatch, [Reply(tags()), Reply(details), Reply(answer())])
    LocalChatProvider(MODEL, VERSION).complete(request())
    assert "think" not in json.loads(transport.calls[-1][2])


@pytest.mark.parametrize("where", [0, 1, 2])
@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_never_followed(monkeypatch, where, status):
    replies = [Reply(tags()), Reply(shown()), Reply(answer())]
    replies[where] = Reply({}, status=status, headers={"Location": "https://example.invalid"})
    transport = Transport(monkeypatch, replies)
    provider = LocalChatProvider(MODEL, VERSION)
    assert_error(provider, request(), "local_chat_redirect")
    assert len(transport.calls) == where + 1


@pytest.mark.parametrize("reply,code", [
    (Reply({}, status=400), "local_chat_http_400"),
    (Reply({}, status=500), "local_chat_http_500"),
    (Reply({}, status=204), "local_chat_http_204"),
    (Reply(raw=b'{"x":1,"x":2}'), "local_chat_malformed"),
    (Reply(raw=b'{"x":NaN}'), "local_chat_malformed"),
    (Reply(raw=b'{"x":1e999}'), "local_chat_malformed"),
    (Reply(raw=b'{"x":"\\ud800"}'), "local_chat_malformed"),
    (Reply(raw=b'\xff'), "local_chat_malformed"),
    (Reply(raw=b'[]'), "local_chat_malformed"),
    (Reply(raw=b'{} trailing'), "local_chat_malformed"),
    (Reply({}, headers={"Content-Encoding": "gzip"}), "local_chat_malformed"),
    (Reply({}, headers={"Content-Type": "text/html"}), "local_chat_malformed"),
    (Reply({}, headers={"Content-Length": "100"}), "local_chat_malformed"),
    (Reply({}, headers={"Content-Length": "-1"}), "local_chat_response_large"),
    (Reply({}, headers={"Content-Length": "9" * 5000}), "local_chat_response_large"),
    (Reply({}, headers={"Content-Length": str(local.MAX_RESPONSE_BYTES + 1)}), "local_chat_response_large"),
    (Reply(raw=b" " * (local.MAX_RESPONSE_BYTES + 1)), "local_chat_response_large"),
    (TimeoutError(), "local_chat_timeout"),
    (http.client.IncompleteRead(b"part"), "local_chat_transport"),
    (OSError("private server details"), "local_chat_transport"),
])
def test_transport_failures_are_named_bounded_and_not_retried(monkeypatch, reply, code):
    transport = Transport(monkeypatch, [Reply(tags()), Reply(shown()), reply])
    provider = LocalChatProvider(MODEL, VERSION)
    error = assert_error(provider, request(), code)
    assert "private server details" not in error.reason
    assert len(transport.calls) == 3
    assert provider.chat_calls == 1
    assert all(c.closed for c in transport.connections)


def test_wall_deadline_shuts_down_slow_body_even_after_connection_close(monkeypatch):
    reply = Reply({}, hang=True)
    transport = Transport(monkeypatch, [reply])
    assert_error(LocalChatProvider(MODEL, VERSION), request(deadline_s=0.03), "local_chat_timeout")
    assert reply.sock.shut.is_set()
    assert reply.closed and transport.connections[0].closed


@pytest.mark.parametrize("change", [
    {"model": "other"}, {"done": False}, {"done": 1}, {"done_reason": "unknown"},
    {"message": {}}, {"message": {"role": "user", "content": "x"}},
    {"message": {"role": "assistant", "content": 0}},
    {"message": {"role": "assistant", "content": "x", "tool_calls": [{"function": {}}]}},
    {"message": {"role": "assistant", "content": "x", "images": ["image"]}},
    {"prompt_eval_count": None}, {"eval_count": True}, {"eval_count": -1},
    {"eval_count": 1.5}, {"eval_count": 2**53}, {"eval_count": 2**53 + 1},
])
def test_malformed_generated_answer_not_accepted(monkeypatch, change):
    generated = answer()
    generated.update(change)
    Transport(monkeypatch, [Reply(tags()), Reply(shown()), Reply(generated)])
    assert_error(LocalChatProvider(MODEL, VERSION), request(), "local_chat_malformed")


def test_remote_response_rejected_even_after_preflight(monkeypatch):
    generated = answer()
    generated["remote_host"] = "remote.invalid"
    Transport(monkeypatch, [Reply(tags()), Reply(shown()), Reply(generated)])
    assert_error(LocalChatProvider(MODEL, VERSION), request(), "local_chat_remote_model")


def test_length_limit_is_not_mislabeled_complete(monkeypatch):
    generated = answer()
    generated["done_reason"] = "length"
    Transport(monkeypatch, [Reply(tags()), Reply(shown()), Reply(generated)])
    assert LocalChatProvider(MODEL, VERSION).complete(request()).stop_reason == "max_output"


def test_every_fresh_turn_rechecks_artifact(monkeypatch):
    changed = tags()
    changed["models"][0]["digest"] = "b" * 64
    transport = Transport(monkeypatch, [Reply(tags()), Reply(shown()), Reply(answer()), Reply(changed)])
    provider = LocalChatProvider(MODEL, VERSION)
    provider.complete(request())
    assert_error(provider, request(idempotency_key="next-turn"), "local_chat_artifact_mismatch")
    assert (provider.chat_calls, provider.metadata_calls) == (1, 3)
    assert len(transport.calls) == 4


def test_after_uncertain_http_error_core_replays_error_without_retry(monkeypatch, tmp_path):
    transport = Transport(monkeypatch, [Reply(tags()), Reply(shown()), OSError("dropped")])
    provider = LocalChatProvider(MODEL, VERSION)
    ledger = Ledger(tmp_path / "calls.jsonl")
    first = execute(request(), provider, Budget(0, 0), ledger)
    assert first.error_code == "local_chat_transport"
    replay = execute(request(), provider, Budget(0, 0), ledger)
    assert replay.replayed and replay.error_code == first.error_code
    assert provider.chat_calls == 1 and len(transport.calls) == 3
