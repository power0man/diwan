"""مزوّد Ollama المحلي — أول مزوّد نموذجٍ حيّ عبر بروتوكول م٠ (م٤، ق٢٠).

`is_local=True` بالهوية: بوابة الخصوصية تقبل له حمولات `local_only`
و`regulated`. والكلفة المالية صفرٌ معلَن (كهرباء الجهاز لا تُحاسَب):
الحجز والتسوية يعملان بصفرين، والاستهلاك الحقيقي (توكنات) يُقيَّد في
السجل من عدّادات Ollama نفسها.

«جواب المزود لا يُصدَّق» تبدأ هنا: جسدٌ ليس JSON، أو عدّاداتٌ ليست
أعدادًا صحيحة، أو غيابُ message — كلها `ProviderError("malformed")`
مصنَّفة لا استثناءً خامًا يسمّم مفتاح عدم التكرار (عيب تدقيق م٤/١).

تصنيف الأعطاب: رفضُ الاتصال «unreachable» قابلٌ للإعادة (المزوّد لم
يرَ الطلب قطعًا)؛ والمهلة «timeout» **غير** قابلة (نتيجة غير مؤكدة —
قد يكون الخادم نفّذ)؛ وأخطاء HTTP برمزها في المسارين كليهما.

حدٌّ معلن: مسار توافق النسخ القديمة (نداءٌ ثانٍ بلا حقل think عند رفضه)
يعني نداءين شبكيين تحت حجزٍ واحد — بلا أثر ماليًّا هنا لأن الكلفة صفر،
ويُنقل قرارُ think خارج `complete` قبل أي مزوّد مدفوع.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.contracts import Request, Response
from core.validate import validated
from providers.base import ProviderError
from providers.ollama_codec import parse_response, serialize_messages, tool_payload

BASE_URL = "http://127.0.0.1:11434"
# المحرّكُ الافتراضي لديوان، في موضعٍ واحد تستورده أدواتُ التشغيل والفحص (ق٥٤، ٢٥ سبتمبر ٢٠٢٦):
# `qwen3.5:9b` بالقياس على العيّنة الطبقية من بنك Kimi (ك٢: ٥٤٫٣٪ مقابل ٤٨٫١٪ لـqwen3:14b).
# تغييرُه بقاعدة ق٥٩ (فارقُ ≥٥ نقاط على العيّنة نفسِها)، وقاعدةُ التحكيم (AGENTS.md §٤) تتبعه.
DEFAULT_MODEL = "qwen3.5:9b"
# النافذة نفسها المعلنة في providers/local_chat.py حتى لا يختلف مسارُ القياس
# عن مسار الحوار في شرطٍ يغيّر الجواب.
CONTEXT_TOKENS = 32768
SAMPLING_SEED = 0
# تحصين النقل: ProxyHandler({}) يمنع قراءة HTTP_PROXY/ALL_PROXY من البيئة
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaProvider:
    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = BASE_URL,
                 allow_thinking: bool = False):
        self.model = model
        self.base_url = base_url
        self.name = f"ollama:{model}"
        self.is_local = True
        self.allow_thinking = allow_thinking

    def estimate_micros(self, request: Request) -> int:
        return 0  # محليّ: لا فاتورة مالية — انظر توثيق الوحدة

    _tool_payload = staticmethod(tool_payload)

    def _post(self, payload: dict, timeout: float) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with _LOCAL_OPENER.open(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise ProviderError(f"http_{e.code}",
                                f"خطأ خادم Ollama: {detail}",
                                retryable=e.code >= 500) from e
        except TimeoutError as e:
            raise ProviderError("timeout",
                                "مهلة انقضت — النتيجة غير مؤكدة فلا تُعاد آليًّا",
                                retryable=False) from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), TimeoutError):
                raise ProviderError("timeout",
                                    "مهلة انقضت — النتيجة غير مؤكدة",
                                    retryable=False) from e
            raise ProviderError("unreachable",
                                f"تعذّر بلوغ Ollama: {e}", retryable=True) from e
        except OSError as e:
            raise ProviderError("unreachable",
                                f"تعذّر بلوغ Ollama: {e}", retryable=True) from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ProviderError("malformed",
                                f"جسد الجواب ليس JSON: {raw[:120]!r}",
                                retryable=False) from e

    def complete(self, request: Request) -> Response:
        request = validated(request)
        payload = {
            "model": self.model,
            "messages": self._messages(request),
            "stream": False,
            "think": request.thinking,
            # نافذة السياق والبذرة معلنتان لا متروكتان لخادمٍ قد يغيّرهما:
            # بلا num_ctx يتغيّر السياق الفعليّ بإعداد الخادم فيصير القياس غير
            # قابلٍ لإعادة الإنتاج — وهو ما وثّقه tools/model_probe.py قبلًا.
            # وseed مع temperature=0 يزيل تقلّبًا رُصد في ag06 بين تشغيلين.
            "options": {"num_predict": request.max_output, "temperature": 0,
                        "num_ctx": CONTEXT_TOKENS, "seed": SAMPLING_SEED},
        }
        if request.tools:
            payload["tools"] = [self._tool_payload(tool) for tool in request.tools]
        try:
            out = self._post(payload, request.deadline_s)
        except ProviderError as e:
            if e.code == "http_400" and "does not support thinking" in e.reason:
                payload.pop("think")
                out = self._post(payload, request.deadline_s)
            else:
                raise
        return self._to_response(out, request=request)

    _messages = staticmethod(serialize_messages)

    def _to_response(self, out: dict, *, request: Request | None = None) -> Response:
        return parse_response(out, request=request, provider_name=self.name,
                              allow_thinking=self.allow_thinking)
