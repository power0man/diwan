"""مزوّد MLX لا يبدّل القالبَ صامتًا ولا يلفّق عدَّ التوكنات: كلُّ عطبٍ باسمه (مسحُ الإخفاقات الصامتة، مسار google بتسليم)."""
from __future__ import annotations

import sys
import types

import pytest

from core.contracts import Message, Request
from providers.base import ProviderError
from providers.mlx_provider import MLXProvider


class _Tokenizer:
    def __init__(self, *, template_fails=False, fail_on=None):
        self.template_fails, self.fail_on = template_fails, fail_on

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        if self.template_fails:
            raise RuntimeError("template broken")
        return "prompt"

    def encode(self, text):
        if self.fail_on is not None and self.fail_on in text:
            raise ValueError("cannot encode")
        return [1] * max(1, len(text.split()))


def _request():
    return Request(messages=(Message(role="user", content="سؤال"),), model="qwen-mlx", model_version="1",
                   max_output=16, deadline_s=30.0, data_policy="local_only", idempotency_key="key-mlx-named")


def _fake_mlx(monkeypatch):
    mx = types.ModuleType("mlx.core")
    mx.random = types.SimpleNamespace(seed=lambda seed: None)
    mlx = types.ModuleType("mlx")
    mlx.core = mx
    mlx_lm = types.ModuleType("mlx_lm")
    mlx_lm.generate = lambda **kw: "ردٌّ مولَّد"
    for name, module in (("mlx", mlx), ("mlx.core", mx), ("mlx_lm", mlx_lm)):
        monkeypatch.setitem(sys.modules, name, module)


def test_a_failing_chat_template_is_a_named_error_not_a_silent_chatml_fallback():
    provider = MLXProvider("m", model=object(), tokenizer=_Tokenizer(template_fails=True))
    with pytest.raises(ProviderError) as exc:
        provider.format_prompt((Message(role="user", content="سؤال"),))
    assert exc.value.code == "mlx_chat_template_failed" and "RuntimeError" in exc.value.reason


def test_a_tokenizer_that_cannot_count_the_input_is_a_named_error_not_an_estimate(monkeypatch):
    _fake_mlx(monkeypatch)
    provider = MLXProvider("m", model=object(), tokenizer=_Tokenizer(fail_on="prompt"))
    with pytest.raises(ProviderError) as exc:
        provider.complete(_request())
    assert exc.value.code == "mlx_tokenizer_failed" and "المدخل" in exc.value.reason


def test_a_tokenizer_that_cannot_count_the_output_is_a_named_error_not_an_estimate(monkeypatch):
    _fake_mlx(monkeypatch)
    provider = MLXProvider("m", model=object(), tokenizer=_Tokenizer(fail_on="مولَّد"))
    with pytest.raises(ProviderError) as exc:
        provider.complete(_request())
    assert exc.value.code == "mlx_tokenizer_failed" and "المخرج" in exc.value.reason
