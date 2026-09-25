"""محوّل وسائط م١٢ إلى Ollama المحلي المثبت، دون تنزيل أو إعادة نداء.

البايتات والسياسة مجمدة في محتوى Request وتدخل بصمته قبل هذا التحويل.
صور PNG وصوت PCM WAV يصلان إلى حقل images في API الإصدار المحدد.
الخادم المحلي حد ثقة؛ هذا المحوّل لا يعزل عملية Ollama عن الشبكة، ولا
يثبت جودة إدراك الوسائط أو يمنع تبديل alias بين الفحص والتوليد.
"""
from __future__ import annotations

import time

from core.canonical import canonical_bytes
from core.contracts import Request, Response, Usage
from core.validate import validated
from multimodal.codec import (
    MEDIA_SYSTEM, POLICY, MediaError, decode_request, validate_context,
)
from providers.base import ProviderError, require_text_only
from providers.local_chat import (
    LocalChatProvider, MAX_DEADLINE_S, MAX_OUTPUT_TOKENS,
    _count, _fail, _no_remote,
)

RUNTIME_VERSION = "0.34.2"


class LocalMediaProvider(LocalChatProvider):
    """يُستدعى عبر core.run.execute؛ العدادات الموروثة تعد محاولات HTTP."""

    __slots__ = ()
    request_byte_limit = 2 * 1024 * 1024

    @property
    def name(self) -> str:
        return "ollama-local-media"

    @staticmethod
    def _messages(request: Request) -> tuple[list[dict], tuple[str, ...]]:
        source = request.messages
        try:
            validate_context(source)
        except MediaError as exc:
            code = "local_" + exc.code if exc.code in {
                "media_message_order", "media_context_limit", "media_text_limit"
            } else exc.code
            raise ProviderError(code, exc.reason, retryable=False) from exc
        messages = [{"role": "system", "content": MEDIA_SYSTEM}]
        capabilities = set()
        for index, message in enumerate(source[1:], start=1):
            expected = "user" if index % 2 else "assistant"
            if expected == "assistant":
                content = message.content
                outgoing = {"role": "assistant", "content": content}
            else:
                try:
                    envelope = decode_request(message.content)
                except MediaError as exc:
                    raise ProviderError(exc.code, exc.reason, retryable=False) from exc
                media = envelope["media"]
                public = {**envelope, "media": [
                    {key: value for key, value in item.items() if key != "data_base64"}
                    for item in media
                ]}
                content = canonical_bytes(public).decode("utf-8")
                outgoing = {"role": "user", "content": content}
                if media:
                    outgoing["images"] = [item["data_base64"] for item in media]
                    capabilities.update("vision" if item["kind"] == "image" else "audio"
                                        for item in media)
            messages.append(outgoing)
        return messages, tuple(sorted(capabilities))

    def complete(self, request: Request) -> Response:
        request = validated(request)
        if request.model != self.model or request.model_version != self.model_version:
            _fail("local_chat_request_identity", "هوية الطلب لا تطابق المزوّد")
        require_text_only(request, code="local_media_tools_unsupported",
                          reason="مسار الوسائط لا ينفذ أدوات ولا يسقط تاريخها")
        if request.max_output > MAX_OUTPUT_TOKENS:
            _fail("local_chat_output_limit", "سقف الإخراج أعلى من الحد المحلي")
        if request.deadline_s > MAX_DEADLINE_S:
            _fail("local_media_deadline_limit", "مهلة الوسائط أعلى من الحد المحلي")
        messages, required = self._messages(request)
        deadline = time.monotonic() + request.deadline_s
        runtime = self._json("GET", "/api/version", None, deadline)
        _no_remote(runtime)
        if runtime.get("version") != RUNTIME_VERSION:
            _fail("local_media_runtime_unsupported", "إصدار Ollama مختلف عن العقد المثبت")
        thinking = self._preflight(deadline, required_capabilities=required,
                                  context_tokens=POLICY["num_ctx"])
        payload = {
            "model": self.model, "messages": messages,
            "stream": False, "truncate": False, "shift": False,
            "options": {"num_predict": request.max_output, "temperature": 0,
                        "num_ctx": POLICY["num_ctx"]},
        }
        if thinking:
            payload["think"] = False
        out = self._json("POST", "/api/chat", payload, deadline)
        _no_remote(out)
        message = out.get("message")
        if (out.get("model") != self.model or out.get("done") is not True
                or not isinstance(message, dict) or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str)
                or out.get("done_reason") not in ("stop", "length")
                or message.get("tool_calls") not in (None, [])
                or message.get("images") not in (None, [])
                or message.get("audio") not in (None, [])
                or message.get("thinking") not in (None, "")
                or out.get("audio") not in (None, [])):
            _fail("local_media_malformed", "استجابة الوسائط يجب أن تكون نصًا فقط")
        return Response(
            content=message["content"],
            usage=Usage(_count(out.get("prompt_eval_count")), _count(out.get("eval_count"))),
            stop_reason="complete" if out["done_reason"] == "stop" else "max_output",
            cost_micros=0, provider=self.name, model_version=self.model_version,
        )
