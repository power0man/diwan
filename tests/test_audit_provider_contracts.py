"""A missing response field must never become a successful ledger entry."""
import copy

import pytest

from core.budget import Budget
from core.contracts import Message, Request
from core.ledger import Ledger
from core.run import execute
from providers.base import ProviderError
from providers.ollama import OllamaProvider


def response():
    return {"model": "fixture", "message": {"role": "assistant", "content": "answer"},
            "done": True, "done_reason": "stop", "prompt_eval_count": 1, "eval_count": 2}


@pytest.mark.parametrize("field", ["model", "message", "done", "done_reason",
                                    "prompt_eval_count", "eval_count"])
def test_missing_fields_rejected(field):
    raw = response()
    del raw[field]
    with pytest.raises(ProviderError, match="malformed") as raised:
        OllamaProvider("fixture")._to_response(raw)
    assert raised.value.retryable is False


@pytest.mark.parametrize("key,value", [
    ("content", None), ("content", 1), ("role", None), ("role", "user"),
    ("tool_calls", [{}]), ("thinking", "hidden"), ("audio", "bytes"), ("images", ["bytes"]),
])
def test_invalid_message_fields_rejected(key, value):
    raw = response()
    raw["message"][key] = value
    with pytest.raises(ProviderError, match="malformed"):
        OllamaProvider("fixture")._to_response(raw)


@pytest.mark.parametrize("key,value", [
    ("done", False), ("done", 1), ("done_reason", "unknown"), ("done_reason", None),
    ("model", None), ("model", ""), ("prompt_eval_count", None), ("eval_count", True),
    ("eval_count", -1), ("eval_count", 1.0), ("eval_count", 2**53),
])
def test_invalid_response_fields_rejected(key, value):
    raw = response()
    raw[key] = value
    with pytest.raises(ProviderError, match="malformed"):
        OllamaProvider("fixture")._to_response(raw)


def test_audit_empty_message_is_error_and_replay_stays_error(tmp_path):
    provider = OllamaProvider("fixture")
    calls = []
    def post(*args):
        calls.append(True)
        return {"message": {}}
    provider._post = post
    ledger = Ledger(tmp_path / "calls.jsonl")
    request = Request((Message("user", "request"),), "fixture", "v1", 10, 10,
                      "local_only", "fixed-key")
    first = execute(request, provider, Budget(0, 0), ledger)
    saved = copy.deepcopy(ledger.entries())
    second = execute(request, provider, Budget(0, 0), ledger)
    assert first.response is None and first.error_code == "malformed"
    assert second.replayed and second.response is None and second.error_code == "malformed"
    assert saved[0]["record"]["kind"] == "error"
    assert "response" not in saved[0]["record"] and saved[0]["record"]["retryable"] is False
    assert len(calls) == 1 and ledger.entries() == saved


def test_explicit_zero_and_empty_content_are_not_missing_data():
    raw = response()
    raw["message"]["content"] = ""
    raw.update(prompt_eval_count=0, eval_count=0)
    result = OllamaProvider("fixture")._to_response(raw)
    assert result.content == "" and result.stop_reason == "complete"
    assert result.usage.input_tokens == result.usage.output_tokens == 0
