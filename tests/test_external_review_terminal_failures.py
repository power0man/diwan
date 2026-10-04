"""Permanent refusal must not repeat a request; incomplete replies still retry."""
from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from evaluation.external_review import review_bank, review_file, smoke_bank
from evaluation.multi_system_review import AutomaticReviewError
from tools.external_review import OllamaChat, OpenAICompatChat
from tools import external_review as cli


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
    "payment_required",
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


def _two_files(tmp_path):
    bank = smoke_bank(tmp_path)
    (bank / "open" / "second.json").write_bytes((bank / "open" / "smoke.json").read_bytes())
    brief = tmp_path / "brief.md"
    brief.write_text("## من أنت\nSynthetic reviewer.\n", encoding="utf-8")
    return bank, brief


def test_terminal_model_is_not_dispatched_to_later_files_but_can_retry_in_a_new_run(tmp_path):
    bank, brief = _two_files(tmp_path)
    reviewers = ["deepseek-v4-flash:cloud", "mistral-large-3:675b-cloud"]
    calls = []

    def transport(model, *args):
        calls.append(model)
        if model == reviewers[0]:
            raise AutomaticReviewError("http_410")
        return _valid_reply()

    result = review_bank(bank, reviewers, transport, brief_path=brief)
    assert result == {"reviewed": 2, "skipped": 0, "failed": 2}
    assert calls == [reviewers[0], reviewers[1], reviewers[1]]
    blocked = json.loads((bank / "reviews" / "deepseek-v4-flash_cloud" / "smoke.json").read_text())
    assert blocked["attempts"] == [] and blocked["error"] == "http_410"
    assert blocked["not_attempted_reason"] == "earlier_terminal_failure"
    recovered = []

    def repaired(model, *args):
        recovered.append(model)
        return _valid_reply()

    assert review_bank(bank, reviewers, repaired, brief_path=brief) == {
        "reviewed": 2, "skipped": 2, "failed": 0}
    assert recovered == [reviewers[0], reviewers[0]]


def test_budget_failure_stops_other_models_and_files_without_fabricating_attempts(tmp_path):
    bank, brief = _two_files(tmp_path)
    reviewers = ["deepseek-v4-flash:cloud", "mistral-large-3:675b-cloud"]
    calls = []

    def transport(model, *args):
        calls.append(model)
        raise AutomaticReviewError("zero_spend_breach")

    assert review_bank(bank, reviewers, transport, brief_path=brief) == {
        "reviewed": 0, "skipped": 0, "failed": 4}
    assert calls == [reviewers[0]]
    records = [json.loads(p.read_text()) for p in (bank / "reviews").rglob("*.json")]
    assert sum(len(r["attempts"]) for r in records) == 1
    assert sum(r.get("not_attempted_reason") == "earlier_terminal_failure" for r in records) == 3
    assert all(r["error"] == "zero_spend_breach" and r["judgments"] is None for r in records)


@pytest.mark.parametrize("code", ["request_too_large", "http_400", "http_413", "http_415", "http_422"])
def test_request_specific_refusal_keeps_other_files_reviewable(tmp_path, code):
    bank, brief = _two_files(tmp_path)
    reviewers = ["deepseek-v4-flash:cloud", "mistral-large-3:675b-cloud"]
    calls = []

    def transport(model, *args):
        calls.append(model)
        if len(calls) == 1:
            raise AutomaticReviewError(code)
        return _valid_reply()

    assert review_bank(bank, reviewers, transport, brief_path=brief) == {
        "reviewed": 3, "skipped": 0, "failed": 1}
    assert calls == reviewers + reviewers
    recovered = json.loads((bank / "reviews" / "deepseek-v4-flash_cloud" / "smoke.json").read_text())
    assert recovered["error"] is None and len(recovered["attempts"]) == 1


@pytest.mark.parametrize("mode", ["bank", "smoke", "every-family"])
@pytest.mark.parametrize("budget_code", [
    "free_model_required", "free_price_unverified", "free_tier_unverified",
    "zero_spend_breach", "usage_unavailable", "usage_cost_unavailable", "usage_cost_invalid",
    "payment_required",
])
def test_cli_never_resumes_after_budget_failure_through_fallback_or_another_pair(
        tmp_path, monkeypatch, capsys, mode, budget_code):
    models = ["deepseek-ai/DeepSeek-V3.1", "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
              "meta-llama/Llama-3.3-70B-Instruct"]
    calls = []

    class SyntheticChat:
        backend = "hf-router"
        endpoint_host = "router.huggingface.co"
        provider_usage = []
        failures = {}

        def describe(self):
            return {"name": self.backend, "endpoint_host": self.endpoint_host}

        def __call__(self, model, *args):
            calls.append(model)
            if len(calls) == 1:
                raise AutomaticReviewError("quota_exhausted")
            if len(calls) == 2:
                raise AutomaticReviewError(budget_code)
            return _valid_reply()

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "build_free_transport", lambda *a, **k: SyntheticChat())
    bank, brief = _two_files(tmp_path / "evaluation" / "banks")
    args = ["--backend", "hf-router", "--reviewer", models[0], "--reviewer", models[1],
            "--fallback", models[2], "--brief", str(brief)]
    out = tmp_path / "result.json"
    if mode == "bank":
        args.insert(0, str(bank))
    else:
        args.extend(["--smoke", str(out)])
        if mode == "every-family":
            args.append("--every-family")
    assert cli.main(args) == 1
    printed = json.loads(capsys.readouterr().out)
    report = printed if mode == "bank" else json.loads(out.read_text())
    assert report["status"] == "failed" and report["code"] == budget_code
    assert calls == models[:2], "No later reviewer request may hide a budget failure"
    if mode == "every-family":
        assert report["pairs"] == [models[:2]]
        assert report["not_attempted_pairs"] == [[models[2], models[0]]]
        assert len(report["runs"]) == 1
    else:
        assert report["fallbacks"] == []


def test_fallback_does_not_hide_unrelated_program_errors():
    candidates = [cli.resolve_reviewer("deepseek-ai/DeepSeek-V3.1"),
                  cli.resolve_reviewer("meta-llama/Llama-3.3-70B-Instruct")]

    def run(chosen):
        raise AutomaticReviewError("synthetic_unexpected_error")

    with pytest.raises(AutomaticReviewError) as raised:
        cli.with_fallback(candidates, 2, run)
    assert raised.value.code == "synthetic_unexpected_error"


def test_unattempted_reviewer_is_not_reported_reachable(tmp_path):
    models = ["deepseek-ai/DeepSeek-V3.1", "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
              "meta-llama/Llama-3.3-70B-Instruct"]
    calls = []

    class SyntheticChat:
        backend = "hf-router"
        provider_usage = []
        failures = {}

        def describe(self):
            return {"name": self.backend, "endpoint_host": "router.huggingface.co"}

        def __call__(self, model, *args):
            calls.append(model)
            raise AutomaticReviewError("zero_spend_breach")

    _, brief = _two_files(tmp_path)
    args = SimpleNamespace(reviewers=models[:2], fallbacks=models[2:], every_family=True,
                           backend="hf-router", brief=brief)
    report, exit_code = cli._free_smoke(args, SyntheticChat())
    assert exit_code == 1 and report["status"] == "failed"
    assert calls == models[:1]
    blocked = report["models"][models[1]]
    assert blocked["attempts"] == 0 and blocked["caught_planted_error"] is None
    assert blocked["reachable"] is False
