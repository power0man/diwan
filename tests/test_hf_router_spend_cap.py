"""موجّهُ HF مدفوعٌ بالتوكن: لا نداءَ بلا سعرٍ مقروء ولا فوق سقف التشغيل (جديد-spend-ledger، البندان ٢ و٣ من #295).

كلُّ نقلٍ هنا محقون، فلا شبكةَ ولا مفتاحَ حقيقيّ. والسعرُ بالدولار لكل مليون توكن كما في `providers[].pricing` من
`/v1/models`، وهو نفسُه ميكرو-دولار لكل توكن.
"""
from __future__ import annotations

import io
import json
import urllib.error
from decimal import Decimal

import pytest

from evaluation.external_review import BUDGET_TERMINAL_ERRORS, review_bank, smoke_bank
from evaluation.multi_system_review import AutomaticReviewError
from tools import external_review as cli

KEY = "synthetic-key-not-a-secret"
MODEL = "deepseek-ai/DeepSeek-V3-0324"
OTHER = "meta-llama/Llama-3.3-70B-Instruct"
SYSTEM, USER = "s" * 100, "u" * 100          # مئتا بايت: التقديرُ يعدّ كلَّ بايتٍ توكنًا، ومعها ٥١٢ توكنًا للتأطير


def _offer(name, input_price, output_price, status="live"):
    return {"provider": name, "status": status, "pricing": {"input": input_price, "output": output_price}}


def _catalog(*entries):
    return {"object": "list", "data": [{"id": model, "providers": providers} for model, providers in entries]}


class Opener:
    """الفهرسُ لطلب GET، ولكل نموذجٍ مثبَّتٍ ردودُه بالترتيب: قاموسُ استهلاكٍ، أو None (بلا usage)، أو رقمُ خطأ HTTP."""

    def __init__(self, catalog, replies=None):
        self.catalog = catalog
        self.replies = {model: list(queue) for model, queue in (replies or {}).items()}
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if request.data is None:
            return _Reply(self.catalog)
        reply = self.replies[json.loads(request.data)["model"]].pop(0)
        if isinstance(reply, int):
            raise urllib.error.HTTPError(request.full_url, reply, "refused", {}, io.BytesIO(b"{}"))
        body = {"choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]}
        if reply is not None:
            body["usage"] = reply
        return _Reply(body)

    def posted(self):
        return [json.loads(r.data)["model"] for r in self.requests if r.data is not None]


class _Reply(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode("utf-8"))
        self.headers = {"Content-Type": "application/json"}
        self.status = 200

    def geturl(self):
        return "https://router.huggingface.co/v1/x"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat(catalog, replies=None, cap="1", max_tokens=100):
    chat = cli.PricedRouterChat(KEY, spend_cap_usd=Decimal(cap), max_tokens=max_tokens)
    chat.opener = Opener(catalog, replies)
    return chat


def test_router_prices_keep_only_live_providers_that_state_both_prices():
    entry = {"id": MODEL, "providers": [
        _offer("cheap", 0.5, 0.5), _offer("dear", "1.5", 3),
        {"provider": "half", "status": "live", "pricing": {"input": 1}},
        _offer("down", 0.1, 0.1, status="error"),
        _offer("free-looking", -1, 0),
        _offer("bad name", 1, 1),
        {"provider": "unpriced", "status": "live"}]}
    assert cli.router_prices(entry) == {"cheap": {"input": Decimal("0.5"), "output": Decimal("0.5")},
                                        "dear": {"input": Decimal("1.5"), "output": Decimal("3")}}
    assert cli._catalog_entry(entry)["router_prices"] == cli.router_prices(entry)


def test_the_cheapest_live_priced_provider_is_pinned_in_the_payload():
    catalog = _catalog((MODEL, [_offer("dear", 2, 4), _offer("cheap", 1, 2), _offer("down", 0, 0, "error")]))
    chat = _chat(catalog, {f"{MODEL}:cheap": [{"prompt_tokens": 10, "completion_tokens": 5}]})
    assert chat(MODEL, SYSTEM, USER, {}) == "{}"
    assert chat.opener.posted() == [f"{MODEL}:cheap"], "المزوّدُ مثبَّتٌ في الحمولة فلا يختار الموجّهُ أغلى منه"
    pin = chat.spend_report()["spend_cap"]["prices"][MODEL]
    assert {k: pin[k] for k in ("provider", "input", "output", "unit")} == {
        "provider": "cheap", "input": "1", "output": "2", "unit": "usd_per_million_tokens"}
    assert pin["catalog"] == "https://router.huggingface.co/v1/models" and pin["read_at"]


@pytest.mark.parametrize("catalog", [
    pytest.param(_catalog((MODEL, [{"provider": "p", "status": "live"}])), id="no_price"),
    pytest.param(_catalog((OTHER, [_offer("p", 1, 1)])), id="model_absent"),
    pytest.param({"data": "not-a-list"}, id="catalog_unreadable"),
])
def test_a_model_without_a_router_price_is_refused_before_any_request(catalog):
    chat = _chat(catalog)
    with pytest.raises(AutomaticReviewError) as refused:
        chat(MODEL, SYSTEM, USER, {})
    assert refused.value.code == "price_unknown"
    assert chat.opener.posted() == [], "لا نداءَ نموذجٍ بلا سعر"
    assert not chat.budget.reservations


def test_a_call_whose_estimate_exceeds_the_cap_is_refused_before_the_network():
    # التقدير: (٢٠٠ بايت + ٥١٢ للتأطير) × ١ + ١٠٠ توكن × ٢ = ٩١٢ ميكرو-دولار، والسقف ٩١١
    chat = _chat(_catalog((MODEL, [_offer("p", 1, 2)])), {f"{MODEL}:p": [{"prompt_tokens": 1, "completion_tokens": 1}]},
                 cap="0.000911")
    with pytest.raises(AutomaticReviewError) as refused:
        chat(MODEL, SYSTEM, USER, {})
    assert refused.value.code == "spend_cap_reached"
    assert chat.opener.posted() == [] and chat.provider_usage[-1].get("kind") == "catalog"
    assert chat.spend_report()["spend_cap"]["spent_usd"] == "0"


def test_settlement_is_the_reply_tokens_at_the_pinned_price_and_the_cap_holds_across_calls():
    # كلُّ نداءٍ يُحجز له ٩١٢ ويُسوّى بـ ١٠٠×١ + ٥٠×٢ = ٢٠٠؛ فالسقفُ ١٢٠٠ يتّسع لاثنين ويردّ الثالث (يبقى ٨٠٠)
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    chat = _chat(_catalog((MODEL, [_offer("p", 1, 2)])), {f"{MODEL}:p": [usage, usage, usage]}, cap="0.0012")
    chat(MODEL, SYSTEM, USER, {})
    row = chat.provider_usage[-1]
    assert (row["cost_usd"], row["cost_status"]) == ("0.0002", "estimated_from_prices")
    assert row["price"]["provider"] == "p" and row["price"]["unit"] == "usd_per_million_tokens"
    chat(MODEL, SYSTEM, USER, {})
    assert chat.budget.day_remaining_micros == 800
    with pytest.raises(AutomaticReviewError) as refused:
        chat(MODEL, SYSTEM, USER, {})
    assert refused.value.code == "spend_cap_reached" and len(chat.opener.posted()) == 2
    assert chat.spend_report()["spend_cap"] | {"prices": None} == {
        "cap_usd": "0.0012", "spent_usd": "0.0004", "prices": None}


def test_the_reservation_covers_the_chat_framing_the_provider_bills():
    """ملاحظة Codex على #308: prompt_tokens تعدّ تأطيرَ المحادثة فوق بايتات التكليف، فيُحجز له قبل الإرسال ولا يُتجاوز السقف."""
    framed = {"prompt_tokens": 200 + 300, "completion_tokens": 100}   # ٣٠٠ توكنٍ للتأطير فوق البايتات
    tight = _chat(_catalog((MODEL, [_offer("p", 1, 2)])), {f"{MODEL}:p": [framed]}, cap="0.0009")
    with pytest.raises(AutomaticReviewError) as refused:
        tight(MODEL, SYSTEM, USER, {})
    assert refused.value.code == "spend_cap_reached" and tight.opener.posted() == []
    exact = _chat(_catalog((MODEL, [_offer("p", 1, 2)])), {f"{MODEL}:p": [framed]}, cap="0.000912")
    exact(MODEL, SYSTEM, USER, {})
    assert exact.provider_usage[-1]["cost_usd"] == "0.0007" and exact.budget.day_remaining_micros == 212


def test_a_cost_the_router_reports_is_settled_as_reported():
    usage = {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.00005}
    chat = _chat(_catalog((MODEL, [_offer("p", 1, 2)])), {f"{MODEL}:p": [usage]})
    chat(MODEL, SYSTEM, USER, {})
    row = chat.provider_usage[-1]
    assert (row["cost_usd"], row["cost_status"]) == ("0.00005", "reported")
    assert chat.spend_report()["spend_cap"]["spent_usd"] == "0.00005"


@pytest.mark.parametrize("reply", [pytest.param(None, id="no_usage"), pytest.param(500, id="http_500")])
def test_a_sent_call_without_tokens_is_settled_at_its_full_reservation(reply):
    chat = _chat(_catalog((MODEL, [_offer("p", 1, 2)])), {f"{MODEL}:p": [reply]})
    try:
        chat(MODEL, SYSTEM, USER, {})
    except AutomaticReviewError:
        pass
    row = chat.provider_usage[-1]
    assert (row["cost_usd"], row["cost_status"]) == ("0.000912", "reserved_upper_bound")
    assert not chat.budget.reservations and chat.budget.day_remaining_micros == 1_000_000 - 912


@pytest.mark.parametrize("cap,code", [
    pytest.param(None, "spend_cap_required", id="absent"),
    pytest.param(Decimal("0"), "spend_cap_invalid", id="zero"),
    pytest.param(Decimal("-1"), "spend_cap_invalid", id="negative"),
    pytest.param(Decimal("20.01"), "spend_cap_invalid", id="above_hfd2"),
    pytest.param(Decimal("NaN"), "spend_cap_invalid", id="not_a_number"),
    pytest.param(True, "spend_cap_invalid", id="boolean"),
])
def test_the_cap_is_required_positive_and_within_hfd2(cap, code):
    with pytest.raises(AutomaticReviewError) as refused:
        cli.build_free_transport("hf-router", environ={"HF_TOKEN": KEY}, spend_cap_usd=cap)
    assert refused.value.code == code
    assert cli.build_free_transport("hf-router", environ={"HF_TOKEN": KEY},
                                    spend_cap_usd=Decimal("20")).spend_cap_usd == Decimal("20")


def test_max_usd_is_refused_on_every_other_backend(monkeypatch, capsys):
    opener = Opener([])
    monkeypatch.setattr(cli.urllib.request, "build_opener", lambda *handlers: opener)
    monkeypatch.setenv("GITHUB_TOKEN", KEY)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    for args, code in ((["--backend", "github-models", "--max-usd", "1", "--smoke", "x.json"], "max_usd_is_hf_router_only"),
                       (["--max-usd", "1", "--smoke", "x.json"], "free_backend_option_without_free_backend")):
        assert cli.main(args) == 2
        assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["code"] == code
    monkeypatch.setenv("HF_TOKEN", KEY)
    assert cli.main(["--backend", "hf-router", "--smoke", "x.json"]) == 2
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["code"] == "spend_cap_required"
    assert opener.requests == [], "لا طلبَ قبل الرفض"


@pytest.mark.parametrize("code", [pytest.param("price_unknown", id="price_unknown"),
                                  pytest.param("spend_cap_reached", id="spend_cap_reached")])
def test_a_price_or_cap_refusal_stops_the_whole_run_under_its_name(tmp_path, code):
    assert code in BUDGET_TERMINAL_ERRORS
    bank = smoke_bank(tmp_path)
    (bank / "open" / "second.json").write_bytes((bank / "open" / "smoke.json").read_bytes())
    brief = tmp_path / "brief.md"
    brief.write_text("## من أنت\nSynthetic reviewer.\n", encoding="utf-8")
    calls = []

    def transport(model, *args):
        calls.append(model)
        raise AutomaticReviewError(code, model)

    review_bank(bank, [MODEL, OTHER], transport, brief_path=brief)
    assert calls == [MODEL], "لا إعادةَ ولا نداءَ لمراجعٍ آخر ولا لملفٍّ لاحق بعد رفض السعر أو السقف"
