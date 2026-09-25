"""مزوّد Apple MLX المحلي فائق السرعة على Apple Silicon (Metal).

يتيح الاستدلال المباشر للنماذج اللغوية على الذاكرة الموحدة (Unified Memory)
باستخدام محرك MLX ومعالج الرسوميات (Metal GPU)، مما يوفر:
1. انعدام تكلفة نقل البيانات بين المعالج وذاكرة الرسوميات.
2. سرعة قراءة سياق واستدلال تتجاوز الطرق التقليدية بأضعاف.
3. الالتزام الصارم بالهوية المحلية `is_local=True` والتكلفة الصفرية (0 ميكرو-دولار).
4. الهبوط الآمن (Graceful Degradation) والتوافقية مع بيئات الاختبار.
"""
from __future__ import annotations

import sys
from typing import Any

from core.contracts import Message, Request, Response, Usage
from core.validate import validated
from providers.base import ProviderError, require_text_only

CONTEXT_TOKENS = 32768
SAMPLING_SEED = 0


class MLXProvider:
    """مزود محلي معتمد على محرك Apple MLX وتسريع عتاد Metal."""

    def __init__(
        self,
        model_name: str = "mlx-community/Qwen2.5-7B-Instruct-4bit",
        model: Any = None,
        tokenizer: Any = None,
        context_tokens: int = CONTEXT_TOKENS,
        seed: int = SAMPLING_SEED,
        temp: float = 0.0,
    ) -> None:
        self.model_name = model_name
        self.name = f"mlx:{model_name}"
        self.is_local = True
        self.context_tokens = context_tokens
        self.seed = seed
        self.temp = temp
        self._model = model
        self._tokenizer = tokenizer

    @classmethod
    def is_hardware_accelerated(cls) -> bool:
        """فحص توفر تسريع Metal على عتاد Apple Silicon."""
        if sys.platform != "darwin":
            return False
        try:
            import mlx.core as mx
            return bool(mx.metal.is_available())
        except Exception:
            return False

    def estimate_micros(self, request: Request) -> int:
        """كلفة التشغيل المحلي صفر ميكرو-دولار دائماً."""
        return 0

    def _ensure_loaded(self) -> tuple[Any, Any]:
        """تحميل النموذج والمجزئ اللغوي مع التحقق من البيئة."""
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer

        try:
            import mlx.core as mx
            import mlx_lm
        except ImportError as exc:
            raise ProviderError(
                "mlx_unavailable",
                "محرك Apple MLX غير متوفر في بيئة التشغيل الحالية",
                retryable=False,
            ) from exc

        try:
            # تحميل النموذج والمجزئ
            model, tokenizer = mlx_lm.load(self.model_name)
            self._model = model
            self._tokenizer = tokenizer
            return model, tokenizer
        except Exception as exc:
            raise ProviderError(
                "mlx_load_failed",
                f"تعذر تحميل نموذج MLX ({self.model_name}): {exc}",
                retryable=False,
            ) from exc

    def format_prompt(self, messages: tuple[Message, ...] | list[Message]) -> str:
        """صياغة محث المحادثة بنمط ChatML القياسي."""
        if self._tokenizer is not None and hasattr(self._tokenizer, "apply_chat_template"):
            try:
                formatted = self._tokenizer.apply_chat_template(
                    [{"role": m.role, "content": m.content} for m in messages],
                    tokenize=False,
                    add_generation_prompt=True,
                )
                if isinstance(formatted, str):
                    return formatted
            except Exception:
                pass

        # الصياغة القياسية المتوافقة مع نماذج Qwen
        lines: list[str] = []
        for m in messages:
            lines.append(f"<|im_start|>{m.role}\n{m.content}<|im_end|>")
        lines.append("<|im_start|>assistant\n")
        return "\n".join(lines)

    def complete(self, request: Request) -> Response:
        """تنفيذ التوليد الحتمي عبر MLX واستخراج عدادات الاستهلاك."""
        request = validated(request)
        require_text_only(request)

        model, tokenizer = self._ensure_loaded()

        try:
            import mlx.core as mx
            import mlx_lm
        except ImportError as exc:
            raise ProviderError(
                "mlx_unavailable",
                "محرك Apple MLX غير متوفر في هذه البيئة",
                retryable=False,
            ) from exc

        prompt_str = self.format_prompt(request.messages)

        # حساب توكنات المدخل
        try:
            prompt_tokens = len(tokenizer.encode(prompt_str))
        except Exception:
            prompt_tokens = max(1, len(prompt_str) // 4)

        max_tokens = min(request.max_output, 4096) if request.max_output > 0 else 2048

        # ضبط بذرة التوليد الحتمي
        if hasattr(mx, "random") and hasattr(mx.random, "seed"):
            mx.random.seed(self.seed)

        try:
            response_text = mlx_lm.generate(
                model=model,
                tokenizer=tokenizer,
                prompt=prompt_str,
                max_tokens=max_tokens,
                verbose=False,
                temp=self.temp,
            )
        except Exception as exc:
            raise ProviderError(
                "mlx_generation_failed",
                f"فشل توليد الاستجابة من محرك MLX: {exc}",
                retryable=False,
            ) from exc

        # حساب توكنات المخرج
        try:
            output_tokens = len(tokenizer.encode(response_text))
        except Exception:
            output_tokens = max(1, len(response_text) // 4)

        usage = Usage(input_tokens=prompt_tokens, output_tokens=output_tokens)
        return Response(
            content=response_text,
            usage=usage,
            stop_reason="complete",
            cost_micros=0,
            provider=self.name,
            model_version=f"mlx-{getattr(model, '__class__', type(model)).__name__}",
        )
