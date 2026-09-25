"""فحوص مزود Apple MLX للاستدلال المحلي وتسريع Metal."""
from __future__ import annotations

import pytest

from core.contracts import Message, Request, ToolSpec
from providers.base import Provider, ProviderError
from providers.mlx_provider import MLXProvider


class DummyTokenizer:
    def encode(self, text: str) -> list[int]:
        return [1] * max(1, len(text.split()))

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        body = "\n".join(f"<{m['role']}>{m['content']}</{m['role']}>" for m in messages)
        return body + "\n<assistant>"


class DummyModel:
    pass


def _sample_request(messages: list[Message], tools=()) -> Request:
    return Request(
        messages=tuple(messages),
        model="qwen-mlx",
        model_version="1",
        max_output=256,
        deadline_s=30.0,
        data_policy="local_only",
        idempotency_key="key-mlx-1",
        tools=tuple(tools),
    )


def test_mlx_provider_protocol_conformance():
    provider = MLXProvider("test-model")
    assert provider.name == "mlx:test-model"
    assert provider.is_local is True
    assert provider.estimate_micros(_sample_request([])) == 0


def test_mlx_provider_hardware_check():
    is_accel = MLXProvider.is_hardware_accelerated()
    assert isinstance(is_accel, bool)


def test_mlx_provider_prompt_formatting():
    p = MLXProvider("test-model")
    msgs = (
        Message(role="system", content="أنت مساعد ديوان"),
        Message(role="user", content="ما شروط التسجيل؟"),
    )
    formatted = p.format_prompt(msgs)
    assert "<|im_start|>system\nأنت مساعد ديوان<|im_end|>" in formatted
    assert "<|im_start|>user\nما شروط التسجيل؟<|im_end|>" in formatted
    assert formatted.endswith("<|im_start|>assistant\n")


def test_mlx_provider_rejects_tools():
    p = MLXProvider("test-model")
    tool = ToolSpec(name="fetch", description="جلب", parameters={})
    req = _sample_request([Message(role="user", content="اختبر")], tools=[tool])
    with pytest.raises(ProviderError) as exc_info:
        p.complete(req)
    assert exc_info.value.code == "tools_unsupported"


def test_mlx_provider_complete_with_injected_model(monkeypatch):
    try:
        import mlx_lm
    except ImportError:
        pytest.skip("محرك Apple MLX غير متوفر في بيئة الاختبار الحالية")

    tokenizer = DummyTokenizer()
    model = DummyModel()
    p = MLXProvider("mock-qwen", model=model, tokenizer=tokenizer)

    # محاكاة توليد mlx_lm.generate
    def mock_generate(model, tokenizer, prompt, max_tokens, verbose, temp):
        return "تم تسجيل السفينة بنجاح."

    monkeypatch.setattr(mlx_lm, "generate", mock_generate)

    req = _sample_request([
        Message(role="system", content="نظام ديوان"),
        Message(role="user", content="سجل السفينة"),
    ])
    res = p.complete(req)

    assert res.content == "تم تسجيل السفينة بنجاح."
    assert res.cost_micros == 0
    assert res.stop_reason == "complete"
    assert res.usage.input_tokens > 0
    assert res.usage.output_tokens > 0
    assert res.provider == "mlx:mock-qwen"


def test_mlx_provider_unavailable_error(monkeypatch):
    p = MLXProvider("unloaded-model")
    # محاكاة عدم توفر المكتبة عند التحميل
    monkeypatch.setattr(p, "_ensure_loaded", lambda: (_ for _ in ()).throw(
        ProviderError("mlx_unavailable", "غير متوفر", retryable=False)
    ))
    req = _sample_request([Message(role="user", content="سؤال")])
    with pytest.raises(ProviderError) as exc:
        p.complete(req)
    assert exc.value.code == "mlx_unavailable"
