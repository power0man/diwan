"""مزوّد محليّ حتميّ — بلا شبكة وبلا مفاتيح.

غرضه أن يُثبت م٠ مسارًا كاملًا يعمل من الطرف إلى الطرف، ويُختبر السقف
والتسوية والسجل، **دون إنفاق ريال واحد ولا إرسال بايت خارج الجهاز**.
"""
from __future__ import annotations

from core.contracts import Request, Response, Usage
from core.validate import validated
from providers.base import require_text_only

# تعريفة وهمية معلنة بوحدات صحيحة: ميكرو-دولار لكل ألف رمز.
PRICE_INPUT_MICROS_PER_1K = 1
PRICE_OUTPUT_MICROS_PER_1K = 3


def _count_tokens(text: str) -> int:
    """عدٌّ تقريبيّ حتميّ — ولا يُقدَّم بوصفه مُجزّئًا حقيقيًّا."""
    return max(1, len(text.split()))


class EchoProvider:
    name = "echo"
    is_local = True

    def estimate_micros(self, request: Request) -> int:
        tin = sum(_count_tokens(m.content) for m in request.messages)
        tout = request.max_output
        return (tin * PRICE_INPUT_MICROS_PER_1K + tout * PRICE_OUTPUT_MICROS_PER_1K + 999) // 1000

    def complete(self, request: Request) -> Response:
        request = validated(request)
        require_text_only(request)
        last = request.messages[-1].content
        body = f"[{self.name}] {last}"
        words = body.split()
        truncated = len(words) > request.max_output
        if truncated:
            body = " ".join(words[: request.max_output])
        tin = sum(_count_tokens(m.content) for m in request.messages)
        tout = _count_tokens(body)
        cost = (tin * PRICE_INPUT_MICROS_PER_1K + tout * PRICE_OUTPUT_MICROS_PER_1K + 999) // 1000
        return Response(
            content=body,
            usage=Usage(input_tokens=tin, output_tokens=tout),
            stop_reason="max_output" if truncated else "complete",
            cost_micros=cost,
            provider=self.name,
            model_version=request.model_version,
        )
