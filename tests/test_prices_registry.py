"""جدولُ الأسعار `registry/prices.json` (جديد-spend-ledger، البند ٢ من #295): شكلُه، والبحثُ فيه، والتقريبُ إلى أعلى، ومزوّدُ Ollama
يسعِّر النموذجَ السحابيَّ منه ولا يفترض صفرًا. **الحدُّ المعلَن:** لا نداءَ حيًّا؛ ولا يُحكم هنا على صدق سعرٍ بل على شكله ومصدره."""
from __future__ import annotations

import pytest

from core import prices
from core.contracts import Message, Request
from evaluation.external_review import DEFAULT_REVIEWERS
from providers.base import ProviderError
from providers.ollama import OllamaProvider


def _entry(**over) -> dict:
    return {"basis": "priced", "input": 1, "output": 1, "read_at": "2026-10-07",
            "source": "https://example.test/pricing", "source_kind": "provider_page", **over}


def _table(**entries) -> dict:
    return {"schema_version": 1, "unit": prices.UNIT, "entries": entries}


def _request(model: str) -> Request:
    return Request(messages=(Message(role="user", content="x" * 10),), model=model, model_version="v",
                   max_output=10, deadline_s=5.0, data_policy="public", idempotency_key=None)


def test_repo_prices_registry_is_well_formed():
    table = prices.load()
    assert table["unit"] == prices.UNIT
    assert prices.lookup(table, "ollama", "deepseek-v4.1-flash:cloud")["basis"] == "subscription_flat"
    # وكلُّ مراجِعٍ افتراضيّ مسعَّرٌ بالجدول، ومنه وسمُ «-cloud» (`mistral-large-3:675b-cloud`) الذي لا يطابقه نمطُ «:cloud» (#352)
    assert {prices.lookup(table, "ollama", model)["basis"] for model in DEFAULT_REVIEWERS} == {"subscription_flat"}


def test_priced_entry_requires_provider_page_source():
    assert prices.findings(_table(**{"p/m": _entry()})) == []
    assert "priced_source:p/m" in prices.findings(_table(**{"p/m": _entry(source_kind="search_excerpt")}))
    assert "priced_source:p/m" in prices.findings(_table(**{"p/m": _entry(source="http://example.test")}))


def test_prices_are_non_negative_integers():
    # ملاحظة Codex على #352: حارسُ `_valid_price` بلا إثباتٍ بالطفرة؛ فسعرٌ سالبٌ أو `True` أو كسرٌ يُردّ باسمه
    assert prices.findings(_table(**{"p/m": _entry()})) == []
    for bad in (-1, True, 1.5, "1", 2 ** 63):
        assert f"price:p/m:input" in prices.findings(_table(**{"p/m": _entry(input=bad)})), bad


def test_cloud_model_without_entry_is_price_unknown():
    table = _table(**{"ollama/*:cloud": _entry(basis="subscription_flat", input=0, output=0,
                                                source_kind="search_excerpt", source="docs/x")})
    assert prices.lookup(table, "ollama", "a:cloud")["key"] == "ollama/*:cloud"
    with pytest.raises(prices.PriceUnknown):
        prices.lookup(table, "ollama", "qwen3.5:9b")


def test_micros_round_up_per_side():
    entry = {"key": "p/m", **_entry(input=1, output=1)}
    assert prices.micros(entry, 1, 1) == 2
    assert prices.micros(entry, 1000, 0) == 1
    assert prices.micros({"key": "s", "basis": "subscription_flat"}, 10 ** 6, 10 ** 6) == 0


def test_ollama_provider_prices_cloud_model_from_registry():
    assert OllamaProvider("qwen3.5:9b").estimate_micros(_request("qwen3.5:9b")) == 0
    assert OllamaProvider("deepseek-v4.1-flash:cloud").estimate_micros(_request("deepseek-v4.1-flash:cloud")) == 0
    with pytest.raises(ProviderError) as refused:
        OllamaProvider("cloud-unlisted:7b").estimate_micros(_request("cloud-unlisted:7b"))
    assert refused.value.code == "price_unknown"
    # المسعَّرُ النموذجُ الذي يُرسل (`self.model`)، لا اسمُ الطلب الذي لا يُرسل: فمحليٌّ يبقى صفرًا ولو بلا طلب، وسحابيٌّ بلا سعرٍ
    # يُرفض ولو حمل الطلبُ اسمًا مسعَّرًا (سقوطُ `test_node` على `estimate_micros(None)` في #352)
    assert OllamaProvider("qwen3.5:9b").estimate_micros(_request("cloud-unlisted:7b")) == 0
    with pytest.raises(ProviderError) as sent_model:
        OllamaProvider("cloud-unlisted:7b").estimate_micros(_request("deepseek-v4.1-flash:cloud"))
    assert sent_model.value.code == "price_unknown"


def test_ollama_estimate_without_request_is_zero_only_for_a_local_provider():
    assert OllamaProvider("m").estimate_micros(None) == 0
    assert OllamaProvider("m", "http://localhost:11434").estimate_micros(None) == 0
    for provider in (OllamaProvider("deepseek-v4.1-flash:cloud"), OllamaProvider("unlisted-cloud"),
                     OllamaProvider("m", "https://ollama.com")):
        with pytest.raises(ProviderError) as refused:
            provider.estimate_micros(None)
        assert refused.value.code == "estimate_request_required" and refused.value.retryable is False


def test_ollama_estimate_rejects_a_non_request_for_a_cloud_provider_before_network(monkeypatch):
    provider = OllamaProvider("unlisted-cloud")
    monkeypatch.setattr(provider, "_post", lambda *a, **k: pytest.fail("network reached"))
    with pytest.raises(ProviderError) as refused:
        provider.estimate_micros({"model": "unlisted-cloud"})
    assert refused.value.code == "estimate_request_required"
    # والطلبُ المكتمل يُسعَّر بالنموذج الذي يُرسل: سحابيٌّ بلا سعرٍ يُرفض ولو حمل الطلبُ اسمًا محليًّا (ملاحظة Codex على #352)
    with pytest.raises(ProviderError) as unpriced:
        OllamaProvider("cloud-unlisted:7b").estimate_micros(_request("m"))
    assert unpriced.value.code == "price_unknown"
