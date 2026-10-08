"""النداءُ السحابيُّ عبر `core.run` بمزوّدٍ مزيَّف (جديد-spend-ledger، البند ٣ من #295): الجمعُ صحيح، و`Budget` يرفض ما فوق السقف،
وردٌّ بلا توكناتٍ لا يُحسب، ونموذجٌ بلا سعرٍ يُرفض قبل الشبكة. **الحدُّ المعلَن:** لا نداءَ حيًّا؛ النقلُ معلَّب."""
from __future__ import annotations

import io
import json
from argparse import Namespace
from decimal import Decimal

import pytest

from core import prices
from core.budget import Budget
from core.ledger import Ledger, LedgerCorrupt
from evaluation.metered import FRAMING_TOKENS, MeteredTransport
from evaluation.multi_system_review import AutomaticReviewError
from tools import external_review as cli

TABLE = {"schema_version": 1, "unit": prices.UNIT, "entries": {
    "fake/priced": {"basis": "priced", "input": 1000, "output": 2000, "read_at": "2026-10-07",
                    "source": "https://example.test/pricing", "source_kind": "provider_page"}}}


class FakeTransport:
    def __init__(self, *replies, max_tokens=10):
        self.replies, self.provider_usage, self.calls = list(replies), [], 0
        self.max_tokens = max_tokens      # الحدُّ الذي «يفرضه» النقلُ على السلك، وعليه يُحجز

    def __call__(self, model, system, user, schema):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        self.provider_usage.append({"provider": "fake", "model": model, "request_sent": True,
                                    "usage": reply.get("usage"), "cost_status": reply.get("cost_status", "not_reported"),
                                    "cost_usd": reply.get("cost_usd")})
        return reply["content"]


def _metered(tmp_path, transport, cap_micros=10 ** 6, **kw):
    budget = Budget(day_remaining_micros=cap_micros, month_remaining_micros=cap_micros)
    return MeteredTransport(transport, provider_key="fake", budget=budget, ledger=Ledger(tmp_path / "ledger.jsonl"),
                            prices=TABLE, **kw)


def _charged(metered, cap_micros=10 ** 6) -> int:
    return cap_micros - metered.budget.day_remaining_micros


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
    assert metered.refusals == [{"model": "priced", "code": "spend_cap_reached", "estimate_micros": estimate,
                                 "settled_micros": 0}]
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


def test_the_report_counts_what_the_budget_charged_on_errors(tmp_path):
    """ملاحظة Codex على #295: ردٌّ بلا توكناتٍ خُصم بالمحجوز (٥٣٤ ميكرو) والتقريرُ قال صفرًا. الجمعُ يساوي ما خصمته الميزانيةُ
    ويطابق السجلَّ، على عطلٍ مصنَّف وعطلٍ غيرِ مصنَّف ونداءٍ ناجحٍ بعدهما."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 7}}, RuntimeError("wire fell"),
                              {"content": "b", "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    metered = _metered(tmp_path, transport)
    with pytest.raises(AutomaticReviewError) as refused:
        metered("priced", "s", "u", {})
    assert refused.value.code == "usage_missing"
    with pytest.raises(RuntimeError):
        metered("priced", "s", "u", {})
    assert metered("priced", "s", "u", {}) == "b"
    estimate = (2 + FRAMING_TOKENS) * 1000 // 1000 + 10 * 2000 // 1000
    assert estimate == 534 and _charged(metered) == 534 + 534 + 20
    assert [(r["code"], r["settled_micros"]) for r in metered.refusals] \
        == [("usage_missing", 534), ("aborted_unclassified", 534)]
    report = metered.spend_report()["core_run"]
    assert report["settled_on_errors_micros"] == 1068 and report["outstanding_micros"] == 0
    assert Decimal(report["settled_usd"]) * 10 ** 6 == _charged(metered)
    assert sum(e["record"]["settled_micros"] for e in metered.ledger.entries()) == _charged(metered)


def test_the_reservation_bound_is_the_limit_the_transport_enforces(tmp_path):
    """ملاحظة Codex على #295: حجزٌ على `max_output` لا يمرّ إلى النقل يسمح للمخرج بتجاوز ما حُجز عليه. الحدُّ يُقرأ من
    `max_tokens` النقل نفسِه، ونقلٌ بلا حدٍّ أو حدٌّ مطلوبٌ يخالفه لا يُغلَّف."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, max_tokens=25)
    metered = _metered(tmp_path, transport)
    assert metered.max_output == 25
    metered("priced", "s", "u", {})
    assert metered.refusals == [] and metered.calls[0]["estimate_micros"] == (2 + FRAMING_TOKENS) + 25 * 2
    assert _metered(tmp_path, transport, max_output=25).max_output == 25
    with pytest.raises(ValueError, match="max_output_mismatch"):
        _metered(tmp_path, transport, max_output=10)
    for unenforced in (None, 0, True, "25"):
        transport.max_tokens = unenforced
        with pytest.raises(ValueError, match="max_output_unenforced"):
            _metered(tmp_path, transport)
    del transport.max_tokens
    with pytest.raises(ValueError, match="max_output_unenforced"):
        _metered(tmp_path, transport)


def test_the_cloud_ollama_transport_sends_its_limit_as_num_predict(tmp_path):
    chat = cli.OllamaChat(cli.CLOUD_ENDPOINT, api_key="test-key-not-a-secret-0123456789", max_tokens=321)
    sent = []

    class _Opener:
        def open(self, request, timeout=None):
            sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"message": {"content": "{}"}, "prompt_eval_count": 3, "eval_count": 2})
                              .encode("utf-8"))

    chat.opener = _Opener()
    metered = cli.metered_transport(chat, "ollama", tmp_path / "l.jsonl", cap_micros=0)
    assert metered.max_output == 321
    assert metered("x:cloud", "s", "u", {}) == "{}"
    assert sent[0]["options"]["num_predict"] == 321
    assert cli.OllamaChat().max_tokens == 4000


def test_unpriced_model_is_refused_before_network(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    metered = _metered(tmp_path, transport)
    with pytest.raises(AutomaticReviewError) as refused:
        metered("unlisted", "s", "u", {})
    assert refused.value.code == "price_unknown" and transport.calls == 0
    # ملاحظة Codex على #352: كان رفضُ التقدير يسبق كلَّ قيدٍ في `core.run`، فلا يبقى منه في السجلّ الدائم شيء؛ والآن يُقيَّد رفضًا
    (record,) = [e["record"] for e in metered.ledger.entries()]
    assert (record["kind"], record["error_code"], record["model"]) == ("refused", "price_unknown", "unlisted")
    assert metered.ledger.verify_chain(strict=True) and _charged(metered) == 0


def test_reported_cost_settles_over_the_price_estimate(tmp_path):
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                               "cost_status": "reported", "cost_usd": "0.0005"})
    metered = _metered(tmp_path, transport)
    metered("priced", "s", "u", {})
    (call,) = metered.spend_report()["core_run"]["calls"]
    assert (call["cost_micros"], call["cost_basis"]) == (500, "reported_by_provider")


class _Server:
    """خادمُ Ollama معلَّب: يجيب كلَّ نداءٍ بتوكناتٍ كاملة ويحفظ النموذجَ المطلوب."""

    def __init__(self):
        self.models = []

    def open(self, request, timeout=None):
        self.models.append(json.loads(request.data)["model"])
        return io.BytesIO(json.dumps({"message": {"content": "{}"}, "prompt_eval_count": 3, "eval_count": 2}).encode())


def test_cli_meters_every_cloud_model_call_and_only_those(tmp_path):
    cloud = cli.build_transport(cli.CLOUD_ENDPOINT, {cli.CLOUD_KEY_ENV: "k"}, ledger_path=tmp_path / "cloud.jsonl")
    assert isinstance(cloud, MeteredTransport) and cloud.provider_key == "ollama" and cloud.cloud is True
    assert cloud.budget.outstanding_micros == 0 and cloud.meters is None
    # ملاحظة Codex على #352: الخادمُ المحليّ الافتراضيّ يمرّر المراجِعين `:cloud` إلى الحساب السحابيّ، وكان نقلُه يخرج بلا
    # `core.run`. فالآن يُغلَّف، ويُحاسَب منه نداءُ النموذج السحابيّ وحده؛ والمحليُّ يجيبه الخادمُ بلا حجزٍ ولا قيد
    local = cli.build_transport("http://127.0.0.1:11434", {}, ledger_path=tmp_path / "local.jsonl")
    assert isinstance(local, MeteredTransport) and local.cloud is False
    local.transport.opener = server = _Server()
    assert local("qwen3.5:9b", "s", "u", {}) == "{}"
    assert local.calls == [] and list(local.ledger.entries()) == []
    assert local("deepseek-v4.1-flash:cloud", "s", "u", {}) == "{}"
    assert server.models == ["qwen3.5:9b", "deepseek-v4.1-flash:cloud"]
    assert [(c["model"], c["cost_basis"], c["cost_micros"]) for c in local.calls] == [
        ("deepseek-v4.1-flash:cloud", "subscription_flat", 0)]
    assert [e["record"]["kind"] for e in local.ledger.entries()] == ["ok"]
    assert [row["model"] for row in local.provider_usage] == ["qwen3.5:9b", "deepseek-v4.1-flash:cloud"]
    assert isinstance(cli.build_transport("http://127.0.0.1:11434", {}), cli.OllamaChat)


def test_a_reply_the_core_refuses_is_counted_once_as_a_refusal(tmp_path):
    """ملاحظة Codex على #352: كان النداءُ يُضاف إلى `calls` قبل أن تقبل النواةُ ردَّه، فكلفةٌ مُبلَّغة سالبةٌ بميكرو واحد يرفضها
    `core.run` ويسوّيها بالمحجوز (٥٣٤) كانت تُجمع مع المحجوز، فيعلن التقريرُ ٥٣٣ وما خُصم ٥٣٤."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                               "cost_status": "reported", "cost_usd": "-0.000001"})
    metered = _metered(tmp_path, transport)
    with pytest.raises(AutomaticReviewError) as refused:
        metered("priced", "s", "u", {})
    assert refused.value.code == "cost_negative" and transport.calls == 1
    assert metered.calls == [] and [(r["code"], r["settled_micros"]) for r in metered.refusals] == [("cost_negative", 534)]
    report = metered.spend_report()["core_run"]
    assert Decimal(report["settled_usd"]) * 10 ** 6 == _charged(metered) == 534
    assert sum(e["record"]["settled_micros"] for e in metered.ledger.entries()) == 534


def test_the_core_run_ledger_lives_beside_reviews_and_the_artifact_stays_json(tmp_path):
    """ملاحظة Codex على #352: كان السجلُّ في `reviews/`، و`check_artifact` يقرأ كلَّ ملفٍّ هناك وثيقةَ JSON واحدة، فيرفض سجلًّا
    من سطرين (`artifact_not_json`). فصار بجانبه."""
    bank = tmp_path / "bank"
    (bank / "reviews").mkdir(parents=True)
    (bank / "reviews" / "a.json").write_text(json.dumps({"file": "open/a.json"}))
    path = cli._ledger_path(Namespace(bank=bank, smoke=None))
    assert path == bank / "core-run-ledger.jsonl"
    transport = FakeTransport(*({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1}},) * 2)
    metered = MeteredTransport(transport, provider_key="fake", budget=Budget(day_remaining_micros=10 ** 6, month_remaining_micros=10 ** 6),
                               ledger=Ledger(path), prices=TABLE)
    metered("priced", "s", "u", {})
    metered("priced", "s", "u", {})
    assert len(path.read_text().splitlines()) == 2 and metered.ledger.verify_chain(strict=True)
    assert cli.check_artifact(bank / "reviews", environ={})["files"] == 1


class _LedgerFullAfterSettlement(Ledger):
    """سجلٌّ يمتلئ عند قيد الردّ المقبول، بعد أن سوّت الميزانيةُ كلفتَه."""

    def append(self, record):
        if record.get("kind") == "ok":
            raise OSError(28, "No space left on device")
        return super().append(record)


def test_a_ledger_failure_after_settlement_is_counted_once(tmp_path):
    """ملاحظة Codex على #352: كانت الكلفةُ تُضاف إلى `calls` قبل أن تكتمل `execute`؛ فإن سقطت كتابةُ القيد بعد التسوية أُضيفت
    الكلفةُ نفسُها إلى `refusals` أيضًا، فخُصم ٢٠٠ ميكرو وأعلن التقريرُ ٤٠٠."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    budget = Budget(day_remaining_micros=10 ** 6, month_remaining_micros=10 ** 6)
    metered = MeteredTransport(transport, provider_key="fake", budget=budget,
                               ledger=_LedgerFullAfterSettlement(tmp_path / "ledger.jsonl"), prices=TABLE)
    with pytest.raises(OSError):
        metered("priced", "s", "u", {})
    assert metered.calls == [] and [r["settled_micros"] for r in metered.refusals] == [200]
    assert Decimal(metered.spend_report()["core_run"]["settled_usd"]) * 10 ** 6 == _charged(metered) == 200


def test_every_core_run_record_is_anchored_so_a_cut_tail_is_detected(tmp_path):
    """ملاحظة Codex على #352: لم يكن أحدٌ يرسّي سجلَّ الإنفاق بعد النداء إلا الاختبار، فلا يتحقّق `verify_chain(strict=True)`
    ولا يُكشف قصُّ آخر القيود. فالآن يُرسّى بعد كل ما قيّده `core.run`، نداءً مقبولًا ورفضًا فوق السقف."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    metered = _metered(tmp_path, transport, cap_micros=600)
    assert metered("priced", "s", "u", {}) == "a"
    assert metered.ledger.read_anchor() == {"head": metered.ledger.head(), "count": 1}
    with pytest.raises(AutomaticReviewError) as refused:
        metered("priced", "s", "u", {})
    assert refused.value.code == "spend_cap_reached" and transport.calls == 1
    assert metered.ledger.verify_chain(strict=True)
    assert metered.ledger.read_anchor() == {"head": metered.ledger.head(), "count": 2}
    path = metered.ledger.path
    path.write_text("".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[:-1]), encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        metered.ledger.verify_chain(strict=True)


class _AnchorFails(Ledger):
    """سجلٌّ تُرفض مرساتُه بعد أن قُيِّد النداءُ وسُوّيت كلفتُه (قرصٌ صار للقراءة وحدها)."""

    def anchor(self):
        raise OSError(30, "Read-only file system")


def test_an_anchor_failure_after_settlement_keeps_the_call_in_the_report(tmp_path):
    """ملاحظة Codex على #352: كانت المرساةُ في `finally` تسبق حسابَ النداء، فإن سقطت بعد التسوية خُصم ٢٠٠ ميكرو وقُيِّد `ok`
    وأعلن التقريرُ صفرًا."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
    budget = Budget(day_remaining_micros=10 ** 6, month_remaining_micros=10 ** 6)
    metered = MeteredTransport(transport, provider_key="fake", budget=budget,
                               ledger=_AnchorFails(tmp_path / "ledger.jsonl"), prices=TABLE)
    with pytest.raises(OSError):
        metered("priced", "s", "u", {})
    assert [call["cost_micros"] for call in metered.calls] == [200] and metered.refusals == []
    assert Decimal(metered.spend_report()["core_run"]["settled_usd"]) * 10 ** 6 == _charged(metered) == 200
    assert [e["record"]["kind"] for e in metered.ledger.entries()] == ["ok"]


def test_a_ledger_cut_between_runs_is_refused_before_the_next_call(tmp_path):
    """ملاحظة Codex على #352: كان النداءُ التالي يقيّد فوق سلسلةٍ قُصّ ذيلُها ثم يستبدل المرساة، فيمرّ التحقّقُ الصارم ويختفي القيدُ
    المحذوف. فالآن يُتحقَّق من السجلّ بمرساته قبل أي قيد، ويُرفض بلا نداءٍ ولا خصم؛ وسجلٌّ بقيودٍ بلا مرساةٍ كذلك."""
    transport = FakeTransport(*({"content": "a", "usage": {"prompt_tokens": 100, "completion_tokens": 50}},) * 3)
    metered = _metered(tmp_path, transport)
    assert [metered("priced", "s", "u", {}) for _ in (1, 2)] == ["a", "a"]
    path = metered.ledger.path
    path.write_text("".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[:-1]), encoding="utf-8")
    again = _metered(tmp_path, transport)
    with pytest.raises(AutomaticReviewError) as refused:
        again("priced", "s", "u", {})
    assert refused.value.code == "spend_ledger_corrupt" and transport.calls == 2 and _charged(again) == 0
    assert len(again.ledger.entries()) == 1 and again.ledger.read_anchor()["count"] == 2
    again.ledger.anchor_path.unlink()
    with pytest.raises(AutomaticReviewError) as unanchored:
        again("priced", "s", "u", {})
    assert unanchored.value.code == "spend_ledger_corrupt" and transport.calls == 2 and _charged(again) == 0


def test_a_malformed_unanchored_ledger_is_a_named_refusal(tmp_path):
    """ملاحظة Codex على #352: كان عدُّ القيود يقرأ السجلّ قبل التحويل، فسجلٌّ تالفٌ بلا مرساة (انهيارٌ وسط الكتابة) يُخرج
    `LedgerCorrupt` خامًا لا يلتقطه سطرُ الأوامر، بدل الرفض المسمّى."""
    transport = FakeTransport({"content": "a", "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    metered = _metered(tmp_path, transport)
    metered.ledger.path.write_text('{"digest": \n', encoding="utf-8")
    with pytest.raises(AutomaticReviewError) as refused:
        metered("priced", "s", "u", {})
    assert refused.value.code == "spend_ledger_corrupt" and transport.calls == 0 and _charged(metered) == 0
