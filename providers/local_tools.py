"""Tool-capable local provider on the unchanged LocalChat transport boundary.

Every fresh call checks the local artifact digest, GGUF format, context capacity
and tools capability before sending messages. The inherited transport fixes the
loopback endpoint and rejects redirects, proxies, oversized/invalid JSON and
uncertain retries. The local server remains a trust boundary; alias replacement
between metadata checks and generation is not prevented by this adapter.
"""
from __future__ import annotations

import json
import time

from core.contracts import Request, Response
from core.validate import validated
from providers.base import ProviderError
from providers.local_chat import (CONTEXT_TOKENS, MAX_CONTEXT_CHARS, MAX_DEADLINE_S,
                                  MAX_OUTPUT_TOKENS, SAMPLING_SEED, LocalChatProvider,
                                  _fail, _no_remote)
from providers.ollama_codec import parse_response, serialize_messages, serialize_tools


class LocalToolProvider(LocalChatProvider):
    """Explicit tools adapter; LocalChatProvider remains text-only."""

    __slots__ = ()

    @property
    def name(self) -> str:
        return "ollama-local-tools"

    def complete(self, request: Request) -> Response:
        request = validated(request)
        if request.model != self.model or request.model_version != self.model_version:
            _fail("local_chat_request_identity", "هوية الطلب لا تطابق المزوّد")
        if request.max_output > MAX_OUTPUT_TOKENS:
            _fail("local_chat_output_limit", "سقف الإخراج أعلى من الحد المحلي")
        messages = serialize_messages(request)
        tools = serialize_tools(request.tools)
        # Definitions, historical arguments and results all consume context.
        context = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"))
        if len(context) > MAX_CONTEXT_CHARS:
            _fail("local_chat_context_limit", "سياق الأدوات والرسائل أكبر من حد المحارف المحلي")
        deadline = time.monotonic() + min(request.deadline_s, MAX_DEADLINE_S)
        try:
            thinking = self._preflight(deadline, required_capabilities=("tools",))
        except ProviderError as exc:
            if exc.code == "local_media_capability_missing":
                _fail("local_tools_capability_missing", "قدرة الأدوات غير معلنة في النموذج المحلي")
            raise
        payload = {"model": self.model, "messages": messages,
                   "stream": False, "truncate": False, "shift": False,
                   "options": {"num_predict": request.max_output, "temperature": 0,
                               "num_ctx": CONTEXT_TOKENS, "seed": SAMPLING_SEED}}
        if tools:
            payload["tools"] = tools
        if thinking:
            payload["think"] = False
        out = self._json("POST", "/api/chat", payload, deadline)
        _no_remote(out)
        if out.get("model") != self.model:
            _fail("local_tools_malformed", "هوية جواب الأدوات لا تطابق النموذج المحلي")
        if out.get("audio") not in (None, []) or out.get("images") not in (None, []):
            _fail("local_tools_malformed", "وسائط غير متوقعة في جواب الأدوات")
        try:
            # Some local models emit a separate thinking field despite think=false.
            # The pure parser discards it; done_reason still controls truncation.
            return parse_response(out, request=request, provider_name=self.name,
                                  model_version=self.model_version, allow_thinking=True)
        except ProviderError as exc:
            raise ProviderError("local_tools_malformed", "استجابة أدوات محلية خارج العقد",
                                retryable=False) from exc
