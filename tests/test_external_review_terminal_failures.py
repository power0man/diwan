"""Permanent refusal must not repeat a request; incomplete replies still retry."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from evaluation.external_review import review_file, smoke_bank
from evaluation.multi_system_review import AutomaticReviewError
from tools.external_review import OllamaChat, OpenAICompatChat


def _review(tmp_path, transport):
    bank = smoke_bank(tmp_path)
    return review_file(bank / "open" / "smoke.json", bank,
                       "deepseek-v4-flash:cloud", "deepseek", transport,
                       brief="Synthetic review only", brief_sha="0" * 64)


def _valid_reply():
    return json.dumps({"judgments": [
        {"id": f"smoke_{i}", "reference": "incorrect" if i == 2 else "correct",
         "rubric": "sufficient", "my_answer": "synthetic", "reason": "synthetic", "fix": None}
        for i in (1, 2, 3)
    ]})


@pytest.mark.parametrize("code", [
    "unauthorized", "forbidden", "not_found", "request_too_large", "redirected",
    "quota_exhausted", "key_missing", "cloud_key_missing", "endpoint_not_allowed",
    "free_model_required", "free_price_unverified", "free_tier_unverified",
    "zero_spend_breach", "usage_unavailable", "usage_cost_unavailable", "usage_cost_invalid",
    "http_400", "http_401", "http_402", "http_403", "http_404", "http_405",
    "http_410", "http_413", "http_415", "http_422", "http_429",
])
def test_terminal_refusal_is_recorded_once_without_accepting_a_later_answer(tmp_path, code):
    calls = []

    def transport(*args):
        calls.append(args)
        if len(calls) == 1:
            raise AutomaticReviewError(code)
        return _valid_reply()

    record = _review(tmp_path, transport)
    assert len(calls) == 1
    assert record["judgments"] is None and record["error"] == code
    assert record["attempts"] == [{"attempt": 1, "raw_output": None, "error": code}]


@pytest.mark.parametrize("code", ["transport_timeout", "transport_error", "http_503",
                                  "reply_empty", "reply_incomplete"])
def test_transient_or_incomplete_response_keeps_one_recovery_attempt(tmp_path, code):
    calls = []

    def transport(*args):
        calls.append(args)
        if len(calls) == 1:
            raise AutomaticReviewError(code)
        return _valid_reply()

    record = _review(tmp_path, transport)
    assert len(calls) == 2
    assert len(record["attempts"]) == 2
    assert record["error"] is None and len(record["judgments"]) == 3


@pytest.mark.parametrize("backend,status,expected", [
    ("ollama", 403, "http_403"), ("ollama", 410, "http_410"),
    ("hf-router", 403, "forbidden"), ("hf-router", 410, "http_410"),
])
def test_real_transport_http_refusal_reaches_retry_guard_without_body_leak(
        tmp_path, backend, status, expected):
    chat = OllamaChat() if backend == "ollama" else OpenAICompatChat(backend, "synthetic-key")
    requests = []

    class Reject:
        def open(self, request, timeout=None):
            requests.append(request)
            raise urllib.error.HTTPError(request.full_url, status, "synthetic failure", {},
                                         io.BytesIO(b'{"error":"private-body-marker"}'))

    chat.opener = Reject()
    record = _review(tmp_path, chat)
    assert len(requests) == 1 and len(record["attempts"]) == 1
    assert record["error"] == expected and record["judgments"] is None
    assert "private-body-marker" not in json.dumps(record)
    assert "synthetic-key" not in json.dumps(record)
