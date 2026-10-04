"""سجلُّ الإنفاق، الشطرُ الأول (جديد-spend-ledger): توكناتُ كلِّ نداءٍ في المراجعة الخارجية ومجموعُها لكل نموذج.

كان `OllamaChat` يقرأ `message.content` وحده ويُسقط عدّادَي Ollama، فلا يحمل دليلُ مراجعةٍ على نموذجٍ سحابيّ
توكنًا واحدًا. هنا يُثبت أن كلَّ محاولةٍ صفٌّ في `provider_usage` بشكل الواجهات المجانية نفسِه، وأن المجموعَ صحيح،
وأنه يصل إلى دليل الدخان وإلى `SUMMARY.json`. **الحدُّ المعلَن:** لا نداءَ حيًّا هنا؛ الردودُ معلَّبة قبل الشبكة.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from evaluation.external_review import DEFAULT_REVIEWERS, smoke_bank
from evaluation.multi_system_review import AutomaticReviewError
from tools import external_review as cli


def _reply(**counts):
    judgments = [{"id": f"smoke_{i}", "reference": "incorrect" if i == 2 else "correct",
                  "rubric": "sufficient", "my_answer": "synthetic", "reason": "synthetic", "fix": None}
                 for i in (1, 2, 3)]
    return {"message": {"content": json.dumps({"judgments": judgments})}, **counts}


class _Opener:
    """يردّ ردودًا معلَّبة بالترتيب، أو خطأً، ويعدّ الطلبات."""

    def __init__(self, *bodies, error: Exception | None = None):
        self.bodies, self.error, self.requests = list(bodies), error, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return io.BytesIO(json.dumps(body).encode("utf-8"))


@pytest.mark.parametrize("body,expected", [
    ({"prompt_eval_count": 120, "eval_count": 30},
     {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}),
    ({"prompt_eval_count": 7}, {"prompt_tokens": 7}),
    ({"eval_count": 0}, {"completion_tokens": 0}),
    ({"prompt_eval_count": True, "eval_count": "30"}, None),
    ({"prompt_eval_count": -1, "eval_count": 2.0}, None),
    ({}, None),
    (["not", "a", "dict"], None),
    ({"prompt_eval_count": 2**53, "eval_count": 3}, {"completion_tokens": 3}),
    ({"prompt_eval_count": 2**53 - 1, "eval_count": 1}, {"prompt_tokens": 2**53 - 1, "completion_tokens": 1}),
], ids=["both", "prompt-only", "zero-completion", "bool-and-text", "negative-and-float", "none", "not-a-dict",
        "beyond-safe-integer", "total-beyond-safe-integer"])
def test_ollama_counters_map_to_the_shared_usage_names_and_only_whole_counts_pass(body, expected):
    assert cli.ollama_usage(body) == expected


def test_every_ollama_call_is_a_usage_row_with_its_tokens_and_no_assumed_zero_cost():
    chat = cli.OllamaChat()
    chat.opener = _Opener(_reply(prompt_eval_count=1200, eval_count=340))
    content = chat("deepseek-v4.1-flash:cloud", "system", "user text", {})
    assert json.loads(content)["judgments"]
    [row] = chat.provider_usage
    assert row["usage"] == {"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540}
    assert row["status"] == "succeeded" and row["request_sent"] is True and row["error"] is None
    # النموذجُ السحابيُّ عبر الخادم المحليّ سحابيٌّ باسمه؛ وOllama لا يُبلغ كلفةً فلا تُكتب صفرًا
    assert row["cloud"] is True and row["cost_usd"] is None and row["cost_status"] == "not_reported"
    assert row["provider"] == "ollama" and row["family"] == "deepseek"
    assert "user text" not in json.dumps(row) and "judgments" not in json.dumps(row)


def test_a_local_model_on_the_local_server_is_not_marked_cloud():
    chat = cli.OllamaChat()
    chat.opener = _Opener(_reply(prompt_eval_count=5, eval_count=6))
    chat("qwen3.5:9b", "s", "u", {})
    assert chat.provider_usage[0]["cloud"] is False


def test_a_failed_call_is_a_row_with_its_code_and_a_malformed_reply_keeps_its_counts():
    chat = cli.OllamaChat()
    chat.opener = _Opener(error=urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", 503, "x", {}, None))
    with pytest.raises(AutomaticReviewError):
        chat("deepseek-v4.1-flash:cloud", "s", "u", {})
    chat.opener = _Opener({"message": {"content": 7}, "prompt_eval_count": 9, "eval_count": 1})
    with pytest.raises(AutomaticReviewError):
        chat("deepseek-v4.1-flash:cloud", "s", "u", {})
    refused, malformed = chat.provider_usage
    assert refused["error"] == "http_503" and refused["request_sent"] is True and refused["usage"] is None
    assert malformed["error"] == "ollama_response_malformed"
    assert malformed["usage"] == {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}


def test_token_totals_sum_each_model_and_count_calls_with_incomplete_usage():
    rows = [
        {"model": "a:cloud", "request_sent": True, "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                                             "total_tokens": 12}},
        {"model": "a:cloud", "request_sent": True, "usage": {"prompt_tokens": 5, "completion_tokens": 1,
                                                             "total_tokens": 6}},
        {"model": "a:cloud", "request_sent": True, "usage": None},
        {"model": "b:cloud", "request_sent": True, "usage": {"completion_tokens": 4}},
        {"model": "b:cloud", "request_sent": False, "usage": None},
        {"kind": "catalog", "model": None, "request_sent": True, "usage": None},
    ]
    assert cli.token_totals(rows) == {
        "a:cloud": {"calls": 3, "calls_with_incomplete_usage": 1, "prompt_tokens": 15, "completion_tokens": 3,
                    "total_tokens": 18},
        # عدّادٌ واحد استهلاكٌ ناقص لا كامل، فلا يُقرأ مجموعُه صفرًا صادقًا (ملاحظة Codex على #298)
        "b:cloud": {"calls": 1, "calls_with_incomplete_usage": 1, "prompt_tokens": 0, "completion_tokens": 4,
                    "total_tokens": 0},
    }


def test_both_transports_report_token_totals():
    chat = cli.OllamaChat()
    chat.opener = _Opener(_reply(prompt_eval_count=3, eval_count=4))
    chat("deepseek-v4.1-flash:cloud", "s", "u", {})
    assert chat.spend_report() == {"token_totals": {"deepseek-v4.1-flash:cloud": {
        "calls": 1, "calls_with_incomplete_usage": 0, "prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}}
    free = cli.OpenAICompatChat("hf-router", "synthetic-key")
    free.provider_usage.append({"provider": "hf-router", "model": "m", "request_sent": True,
                                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                                "cost_status": "not_reported"})
    assert free.spend_report()["token_totals"]["m"]["total_tokens"] == 3


def _wire(monkeypatch, opener):
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)


def test_the_ollama_smoke_evidence_carries_every_call_and_the_totals(tmp_path, monkeypatch, capsys):
    opener = _Opener(_reply(prompt_eval_count=100, eval_count=20))
    _wire(monkeypatch, opener)
    out = tmp_path / "smoke.json"
    assert cli.main(["--smoke", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert len(report["provider_usage"]) == len(opener.requests) == len(DEFAULT_REVIEWERS)
    assert {model: totals["total_tokens"] for model, totals in report["token_totals"].items()} == {
        model: 120 for model in DEFAULT_REVIEWERS}


def test_the_ollama_bank_summary_carries_every_call_and_the_totals(tmp_path, monkeypatch, capsys):
    bank = smoke_bank(tmp_path)
    _wire(monkeypatch, _Opener(_reply(prompt_eval_count=50, eval_count=10)))
    assert cli.main([str(bank)]) == 0
    summary = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert len(summary["provider_usage"]) == len(DEFAULT_REVIEWERS)
    assert all(t == {"calls": 1, "calls_with_incomplete_usage": 0, "prompt_tokens": 50, "completion_tokens": 10,
                     "total_tokens": 60} for t in summary["token_totals"].values())


def test_a_rerun_on_a_reviewed_bank_keeps_the_earlier_ledger(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #298: إعادةُ التشغيل تتخطّى السجلّات فلا ترسل نداءً، ولا تمحو أدلّةَ إنفاق المراجعات القائمة."""
    bank = smoke_bank(tmp_path)
    _wire(monkeypatch, _Opener(_reply(prompt_eval_count=50, eval_count=10)))
    assert cli.main([str(bank)]) == 0
    first = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    again = _Opener(_reply(prompt_eval_count=999, eval_count=999))
    _wire(monkeypatch, again)
    assert cli.main([str(bank)]) == 0
    second = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert again.requests == [], "البنكُ مراجَعٌ فلا نداء"
    assert second["provider_usage"] == first["provider_usage"] and second["token_totals"] == first["token_totals"]
    ledger = json.loads((bank / "reviews" / cli.LEDGER_FILE).read_text(encoding="utf-8"))
    assert ledger["provider_usage"] == first["provider_usage"], "السجلُّ الدائم والخلاصةُ سجلٌّ واحد"


def test_the_prior_ledger_is_empty_without_a_summary_or_with_a_malformed_one(tmp_path):
    assert cli.prior_provider_usage(tmp_path) == []
    (tmp_path / "reviews").mkdir()
    for raw in ("{", "[]", '{"provider_usage": "x"}', '{"provider_usage": [1, {"model": "m"}]}'):
        (tmp_path / "reviews" / "SUMMARY.json").write_text(raw, encoding="utf-8")
        assert cli.prior_provider_usage(tmp_path) == ([{"model": "m"}] if "model" in raw else [])


def test_a_refused_ollama_run_still_prints_the_calls_that_went_out(tmp_path, monkeypatch, capsys):
    bank = smoke_bank(tmp_path)

    def refuse(*args, **kwargs):
        raise AutomaticReviewError("brief_missing")

    _wire(monkeypatch, _Opener(_reply(prompt_eval_count=1, eval_count=1)))
    real = cli.build_transport

    def transport_then_refuse(base_url):
        chat = real(base_url)
        chat("deepseek-v4.1-flash:cloud", "s", "u", {})
        return chat

    monkeypatch.setattr(cli, "build_transport", transport_then_refuse)
    monkeypatch.setattr(cli, "review_bank", refuse)
    assert cli.main([str(bank)]) == 2
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["code"] == "brief_missing" and len(printed["provider_usage"]) == 1


def test_the_prior_zero_spend_evidence_keeps_only_named_entries(tmp_path):
    assert cli.prior_zero_spend_evidence(tmp_path) == {}
    (tmp_path / "reviews").mkdir()
    for raw, expected in (("[]", {}), ('{"zero_spend_evidence": "x"}', {}),
                          ('{"zero_spend_evidence": {"m": {"proof": "p"}, "n": "bad"}}', {"m": {"proof": "p"}})):
        (tmp_path / "reviews" / "SUMMARY.json").write_text(raw, encoding="utf-8")
        assert cli.prior_zero_spend_evidence(tmp_path) == expected


def test_calls_sent_before_a_refusal_survive_a_rerun_that_skips_them(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #298: نجح ملفٌّ ثم رُفض الثاني، فخرج التشغيلُ ٢ بلا خلاصة؛ والإعادةُ بعد إزالة الثاني تتخطّى
    المراجعتين فلا ترسل شيئًا. فنداءاتُ التشغيل المرفوض في السجلّ الدائم، والإعادةُ تقرؤها."""
    bank = smoke_bank(tmp_path)
    (bank / "open" / "z.json").write_text("{}", encoding="utf-8")
    first = _Opener(_reply(prompt_eval_count=40, eval_count=4))
    _wire(monkeypatch, first)
    assert cli.main([str(bank)]) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert len(refused["provider_usage"]) == len(first.requests) == len(DEFAULT_REVIEWERS)
    assert not (bank / "reviews" / "SUMMARY.json").exists()
    ledger = json.loads((bank / "reviews" / cli.LEDGER_FILE).read_text(encoding="utf-8"))
    assert ledger["provider_usage"] == refused["provider_usage"]
    (bank / "open" / "z.json").unlink()
    again = _Opener(_reply(prompt_eval_count=999, eval_count=999))
    _wire(monkeypatch, again)
    assert cli.main([str(bank)]) == 0
    summary = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert again.requests == [] and summary["provider_usage"] == refused["provider_usage"]
    assert all(t["total_tokens"] == 44 for t in summary["token_totals"].values())


def test_a_total_beyond_the_safe_integer_is_named_not_written():
    """ملاحظة Codex على #298: كلُّ نداءٍ في الحدّ، ومجموعُهما فوقه؛ فلا يُكتب رقمًا يُقرَّب بل يُعلَن اسمُه."""
    rows = [{"model": "m", "request_sent": True, "usage": {"prompt_tokens": 2**53 - 1, "completion_tokens": 0,
                                                           "total_tokens": 2**53 - 1}},
            {"model": "m", "request_sent": True, "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}}]
    assert cli.token_totals(rows) == {"m": {"calls": 2, "calls_with_incomplete_usage": 0, "prompt_tokens": None,
                                            "completion_tokens": 0, "total_tokens": None,
                                            "fields_beyond_safe_integer": ["prompt_tokens", "total_tokens"]}}


def test_a_truncated_reply_is_a_recorded_transport_error():
    """ملاحظة Codex على #298: IncompleteRead لا يرث OSError، فكان يفلت بلا رمزٍ ولا صفّ."""
    import http.client

    class Truncated:
        def open(self, request, timeout=None):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            raise http.client.IncompleteRead(b"partial")

    chat = cli.OllamaChat()
    chat.opener = Truncated()
    with pytest.raises(AutomaticReviewError) as failed:
        chat("deepseek-v4.1-flash:cloud", "s", "u", {})
    assert failed.value.code == "transport_error"
    [row] = chat.provider_usage
    assert row["error"] == "transport_error" and row["request_sent"] is True and row["usage"] is None
