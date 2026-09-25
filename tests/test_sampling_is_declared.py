"""كل شرطٍ يغيّر الجواب يُعلَن في الحمولة، ولا يُترك لإعداد خادم.

قياسٌ بنافذةِ سياقٍ غير معلنة ليس قابلًا لإعادة الإنتاج: يتغيّر بإعداد
ollama على الجهاز. و`temperature=0` وحدها لا تضمن تطابق تشغيلين — رُصد
اختلافُ `ag06_order_constraints` بين قياسَي ٢١ و٢٢ سبتمبر بنفس النموذج.
فالنافذة والبذرة يُفرضان في المزوّدَين، ويتطابقان بينهما حتى لا يختلف
مسارُ القياس عن مسار الحوار.
"""
from __future__ import annotations

import pytest

from core.contracts import Message, Request
from providers import local_chat, ollama


def request(text: str = "سؤال") -> Request:
    return Request(model="m", model_version="v",
                   messages=(Message(role="user", content=text),),
                   max_output=32, deadline_s=5,
                   data_policy="local_only", idempotency_key=None)


def _captured_options(monkeypatch, provider, sender_attr: str) -> dict:
    seen: dict = {}

    def capture(payload, timeout):
        seen.update(payload)
        raise ollama.ProviderError("captured", "لا نداء فعليّ في الاختبار")

    monkeypatch.setattr(provider, sender_attr, capture)
    with pytest.raises(Exception):
        provider.complete(request())
    assert seen, "لم تُبنَ حمولةٌ قبل الإرسال"
    return seen.get("options", {})


def test_context_and_seed_agree_between_providers():
    assert ollama.CONTEXT_TOKENS == local_chat.CONTEXT_TOKENS, \
        "نافذة القياس تخالف نافذة الحوار فيختلف الجواب لسببٍ غير مقصود"
    assert ollama.SAMPLING_SEED == local_chat.SAMPLING_SEED


@pytest.mark.parametrize("module", [ollama, local_chat])
def test_both_providers_pin_temperature_context_and_seed(module):
    src = (module.__file__ or "")
    text = open(src, encoding="utf-8").read()
    for key in ('"temperature": 0', '"num_ctx"', '"seed"'):
        assert key in text, f"{module.__name__} لا يُعلن {key} في الحمولة"


def test_ollama_payload_carries_the_declared_options(monkeypatch):
    provider = ollama.OllamaProvider("m")
    options = _captured_options(monkeypatch, provider, "_post")
    assert options.get("temperature") == 0
    assert options.get("num_ctx") == ollama.CONTEXT_TOKENS
    assert options.get("seed") == ollama.SAMPLING_SEED
