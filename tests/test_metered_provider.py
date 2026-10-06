"""النداءُ السحابيُّ عبر `core.run` بمزوّدٍ مزيَّف (جديد-spend-ledger، البند ٣ من #295): الجمعُ صحيح، و`Budget` يرفض ما فوق السقف،
وردٌّ بلا توكناتٍ لا يُحسب، ونموذجٌ بلا سعرٍ يُرفض قبل الشبكة. **الحدُّ المعلَن:** لا نداءَ حيًّا؛ النقلُ معلَّب."""
from __future__ import annotations

import pytest

from core import prices
from core.budget import Budget
from core.ledger import Ledger
from evaluation.metered import FRAMING_TOKENS, MeteredTransport
from evaluation.multi_system_review import AutomaticReviewError
from tools import external_review as cli

TABLE = {"schema_version": 1, "unit": prices.UNIT, "entries": {
    "fake/priced": {"basis": "priced", "input": 1000, "output": 2000, "read_at": "2026-10-07",
                    "source": "https://example.test/pricing", "source_kind": "provider_page"}}}


class FakeTransport:
    def __init__(self, *replies):
        self.replies, self.provider_usage, self.calls = list(replies), [], 0

    def __call__(self, model, system, user, schema):
        self.calls += 1
        reply = self.replies.pop(0)
        self.provider_usage.append({"provider": "fake", "model": model, "request_sent": True,
                                    "usage": reply.get("usage"), "cost_status": reply.get("cost_status", "not_reported"),
                                    "cost_usd": reply.get("cost_usd")})
        return reply["content"]


def _metered(tmp_path, transport, cap_micros=10 ** 6):
    budget = Budget(day_remaining_micros=cap_micros, month_remaining_micros=cap_micros)
    return MeteredTransport(transport, provider_key="fake", budget=budget, ledger=Ledger(tmp_path / "ledger.jsonl"),
                            prices=TABLE, max_output=10)


def test_sum_of_tokens_and_cost_is_ledgered(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
                              {"content": "b", "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    metered = _metered(tmp_path, transport)
    assert [metered("priced", "s", "u", {}) for _ in (1, 2)] == ["a", "b"]
    report = metered.spend_report()["core_run"]
    assert [(c["prompt_tokens"], c["completion_tokens"], c["cost_micros"]) for c in report["calls"]] \
        == [(100, 50, 200), (10, 5, 20)]
    assert report["settled_usd"] == "0.00022" and report["outstanding_micros"] == 0
    records = [e["record"] for e in metered.ledger.entries()]
    assert [r["kind"] for r in records] == ["ok", "ok"]
    assert [r["settled_micros"] for r in records] == [200, 20]
    assert records[0]["response"]["usage"] == {"input_tokens": 100, "output_tokens": 50}


def test_budget_refuses_call_over_cap_before_network(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    estimate = (2 + FRAMING_TOKENS) * 1000 // 1000 + 10 * 2000 // 1000
    metered = _metered(tmp_path, transport, cap_micros=estimate - 1)
    with pytest.raises(AutomaticReviewError) as refused:
        metered("priced", "s", "u", {})
    assert refused.value.code == "spend_cap_reached" and transport.calls == 0
    assert metered.refusals == [{"model": "priced", "code": "spend_cap_reached", "estimate_micros": estimate}]
    assert [e["record"]["kind"] for e in metered.ledger.entries()] == ["refused"]
    assert _metered(tmp_path / "ok", transport, cap_micros=estimate)("priced", "s", "u", {}) == "a"


def test_reply_without_tokens_is_settled_by_reservation_and_refused(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 7}})
    metered = _metered(tmp_path, transport)
    with pytest.raises(AutomaticReviewError) as refused:
        metered("priced", "s", "u", {})
    assert refused.value.code == "usage_missing" and metered.calls == []
    (record,) = [e["record"] for e in metered.ledger.entries()]
    assert record["kind"] == "error" and record["settled_micros"] == metered.refusals[0]["estimate_micros"] > 0


def test_unpriced_model_is_refused_before_network(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    with pytest.raises(AutomaticReviewError) as refused:
        _metered(tmp_path, transport)("unlisted", "s", "u", {})
    assert refused.value.code == "price_unknown" and transport.calls == 0


def test_reported_cost_settles_over_the_price_estimate(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                               "cost_status": "reported", "cost_usd": "0.0005"})
    metered = _metered(tmp_path, transport)
    metered("priced", "s", "u", {})
    (call,) = metered.spend_report()["core_run"]["calls"]
    assert (call["cost_micros"], call["cost_basis"]) == (500, "reported_by_provider")


def test_cli_wraps_the_cloud_ollama_transport_only(tmp_path):
    cloud = cli.build_transport(cli.CLOUD_ENDPOINT, {cli.CLOUD_KEY_ENV: "k"}, ledger_path=tmp_path / "l.jsonl")
    assert isinstance(cloud, MeteredTransport) and cloud.provider_key == "ollama" and cloud.cloud is True
    assert cloud.budget.outstanding_micros == 0
    local = cli.build_transport("http://127.0.0.1:11434", {}, ledger_path=tmp_path / "l.jsonl")
    assert isinstance(local, cli.OllamaChat)
