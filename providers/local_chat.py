"""مزوّد م٩ النصي: فحص artifact محلي قبل إرسال أي رسالة.

النقل إلى 127.0.0.1:11434 حرفيًا؛ لا proxy ولا تحويل HTTP ولا إعادة
نداء تلقائية. البصمة من /api/tags، ومعلومات /api/show تمنع الوكيل
السحابي المعلن وتثبت صيغة محلية وسعة سياق معلنة. الخادم المحلي حد
ثقة: لا يعزل هذا الكود عملية Ollama عن الشبكة، ولا يمنع تغيير alias
بالتزامن بين الفحص والتوليد، ولا يثبت معالجة كل توكن داخل النموذج.
"""
from __future__ import annotations

import http.client
import json
import math
import re
import socket
import threading
import time

from core.contracts import Request, Response, Usage
from core.canonical import SAFE_INT
from core.validate import validated
from providers.base import ProviderError, require_text_only

CONTEXT_TOKENS = 32768
# temperature=0 وحدها لا تكفي للحتمية؛ البذرة معلنة ليكون التشغيلان متطابقين.
SAMPLING_SEED = 0
MAX_OUTPUT_TOKENS = 4096
MAX_CONTEXT_CHARS = 24000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 512 * 1024
MAX_DEADLINE_S = 300
METADATA_DEADLINE_S = 10
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _fail(code: str, reason: str) -> None:
    raise ProviderError(code, reason, retryable=False)


def _object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value: str):
    raise ValueError("non-finite number")


def _inspect_json(value, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("deep JSON")
    if isinstance(value, str):
        value.encode("utf-8", "strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number")
    elif isinstance(value, dict):
        for key, child in value.items():
            _inspect_json(key, depth + 1)
            _inspect_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _inspect_json(child, depth + 1)


def _decode(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8", "strict"),
                           object_pairs_hook=_object, parse_constant=_constant)
        _inspect_json(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProviderError("local_chat_malformed", "JSON محلي معطوب",
                            retryable=False) from exc
    if not isinstance(value, dict):
        _fail("local_chat_malformed", "جذر الجواب ليس كائنًا")
    return value


def _no_remote(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized.startswith(("remote", "cloud")):
                _fail("local_chat_remote_model", "بيانات نموذج بعيد مرفوضة")
            _no_remote(child)
    elif isinstance(value, list):
        for child in value:
            _no_remote(child)


def _count(value) -> int:
    if type(value) is not int or not 0 <= value <= SAFE_INT:
        _fail("local_chat_malformed", "عداد الاستهلاك غائب أو غير صالح")
    return value


class LocalChatProvider:
    """واجهة Provider، تُستدعى عبر core.run.execute حصريًا في خدمة م٩.

    chat_calls وmetadata_calls يعدان محاولات HTTP بعد نجاح الاتصال؛
    لا يعني عد المحاولة ثبوت استلام الخادم لها عند انقطاع النقل.
    """

    __slots__ = ("_model", "_model_version", "_chat_calls", "_metadata_calls")
    request_byte_limit = MAX_REQUEST_BYTES

    def __init__(self, model: str, model_version: str):
        if (not isinstance(model, str) or not _MODEL.fullmatch(model)
                or "//" in model or model.endswith("/")
                or any(part in (".", "..") for part in model.split("/"))):
            _fail("local_chat_model_invalid", "اسم نموذج محلي صريح مطلوب")
        if re.search(r"(?:^|[-:/])cloud(?:$|[-:/])", model, re.IGNORECASE):
            _fail("local_chat_remote_model", "اسم نموذج سحابي مرفوض")
        if not isinstance(model_version, str) or not _SHA256.fullmatch(model_version):
            _fail("local_chat_artifact_invalid", "بصمة artifact كاملة مطلوبة")
        self._model = model
        self._model_version = model_version
        self._chat_calls = 0
        self._metadata_calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def name(self) -> str:
        return "ollama-local-chat"

    @property
    def is_local(self) -> bool:
        return True

    @property
    def chat_calls(self) -> int:
        return self._chat_calls

    @property
    def metadata_calls(self) -> int:
        return self._metadata_calls

    def estimate_micros(self, request: Request) -> int:
        return 0

    def _json(self, method: str, path: str, payload: dict | None,
              deadline: float) -> dict:
        if (method, path) not in (("GET", "/api/tags"), ("GET", "/api/version"), ("POST", "/api/show"),
                                  ("POST", "/api/chat")):
            _fail("local_chat_endpoint", "نقطة نهاية غير مسموحة")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail("local_chat_timeout", "انقضت المهلة؛ لا إعادة تلقائية")
        timeout = min(remaining, MAX_DEADLINE_S if path == "/api/chat"
                      else METADATA_DEADLINE_S)
        body = None if payload is None else json.dumps(
            payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if body is not None and len(body) > self.request_byte_limit:
            _fail("local_chat_request_large", "الطلب أكبر من حد النقل")
        # HTTPConnection لا يقرأ proxy من البيئة ولا يتبع Location.
        conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=timeout)
        expired = threading.Event()
        sockets = []

        def expire():
            expired.set()
            # يُحفظ المقبس قبل getresponse لأن Connection: close قد يفصله
            # عن conn؛ shutdown يقطع أيضًا انتظار رؤوس بطيئة أو جسم متقطر.
            for sock in sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        response = None
        timer.start()
        try:
            conn.connect()
            sockets.append(conn.sock)
            if expired.is_set():
                _fail("local_chat_timeout", "انقضت المهلة؛ لا إعادة تلقائية")
            if path == "/api/chat":
                self._chat_calls += 1
            else:
                self._metadata_calls += 1
            conn.request(method, path, body=body, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "Accept-Encoding": "identity", "Connection": "close",
            })
            response = conn.getresponse()
            if 300 <= response.status < 400:
                _fail("local_chat_redirect", "تحويل HTTP مرفوض")
            if response.status != 200:
                _fail(f"local_chat_http_{response.status}", "رفض الخادم المحلي الطلب")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                _fail("local_chat_malformed", "ترميز نقل غير مدعوم")
            content_type = response.getheader("Content-Type", "").split(";")[0].strip()
            if content_type.lower() != "application/json":
                _fail("local_chat_malformed", "نوع جواب غير JSON")
            size = response.getheader("Content-Length")
            if size is not None and (len(size) > 10 or not re.fullmatch(r"[0-9]+", size)
                                     or int(size) > MAX_RESPONSE_BYTES):
                _fail("local_chat_response_large", "حجم الجواب غير مقبول")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if expired.is_set() or time.monotonic() >= deadline:
                _fail("local_chat_timeout", "انقضت المهلة؛ لا إعادة تلقائية")
            if len(raw) > MAX_RESPONSE_BYTES:
                _fail("local_chat_response_large", "الجواب أكبر من حد النقل")
            if size is not None and len(raw) != int(size):
                _fail("local_chat_malformed", "جسم HTTP مبتور")
            return _decode(raw)
        except TimeoutError as exc:
            raise ProviderError("local_chat_timeout", "انقضت المهلة؛ لا إعادة تلقائية",
                                retryable=False) from exc
        except (OSError, http.client.HTTPException) as exc:
            code = "local_chat_timeout" if expired.is_set() else "local_chat_transport"
            raise ProviderError(code, "تعذر النقل المحلي؛ النتيجة غير مؤكدة",
                                retryable=False) from exc
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            conn.close()

    def _preflight(self, deadline: float, *, required_capabilities=(),
                   context_tokens=CONTEXT_TOKENS) -> bool:
        tags = self._json("GET", "/api/tags", None, deadline)
        models = tags.get("models")
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            _fail("local_chat_malformed", "قائمة النماذج غير صالحة")
        matching = [m for m in models if m.get("name") == self.model]
        if len(matching) != 1:
            _fail("local_chat_model_missing", "النموذج غير مثبت باسم مطابق وحيد")
        selected = matching[0]
        _no_remote(selected)
        if selected.get("model") != self.model:
            _fail("local_chat_model_mismatch", "هوية النموذج غير مطابقة")
        if selected.get("digest") != self.model_version:
            _fail("local_chat_artifact_mismatch", "بصمة النموذج المثبت غير مطابقة")
        if type(selected.get("size")) is not int or selected["size"] <= 0:
            _fail("local_chat_artifact_invalid", "حجم artifact المحلي غير صالح")
        details = selected.get("details")
        if not isinstance(details, dict) or details.get("format") != "gguf":
            _fail("local_chat_artifact_invalid", "صيغة artifact محلي غير مثبتة")
        shown = self._json("POST", "/api/show", {"model": self.model}, deadline)
        _no_remote(shown)
        details = shown.get("details")
        info = shown.get("model_info")
        capabilities = shown.get("capabilities")
        if (not isinstance(details, dict) or details.get("format") != "gguf"
                or not isinstance(info, dict)
                or not isinstance(capabilities, list)
                or any(not isinstance(c, str) for c in capabilities)
                or "completion" not in capabilities):
            _fail("local_chat_artifact_invalid", "بيانات النموذج المحلي غير مكتملة")
        if not set(required_capabilities) <= set(capabilities):
            _fail("local_media_capability_missing", "قدرة الوسيط المطلوب غير معلنة في المزود المحلي")
        architecture = info.get("general.architecture")
        if not isinstance(architecture, str) or not architecture:
            _fail("local_chat_artifact_invalid", "معمارية artifact غائبة")
        context = info.get(f"{architecture}.context_length")
        if type(context) is not int or context < context_tokens:
            _fail("local_chat_context_unsupported", "سعة السياق المعلنة أقل من المطلوبة")
        return "thinking" in capabilities

    def complete(self, request: Request) -> Response:
        request = validated(request)
        if request.model != self.model or request.model_version != self.model_version:
            _fail("local_chat_request_identity", "هوية الطلب لا تطابق المزوّد")
        require_text_only(request, code="local_chat_tools_unsupported",
                          reason="م٩ النصية لا تنفذ أدوات ولا تسقط تاريخها")
        if request.max_output > MAX_OUTPUT_TOKENS:
            _fail("local_chat_output_limit", "سقف الإخراج أعلى من الحد المحلي")
        if sum(len(m.content) for m in request.messages) > MAX_CONTEXT_CHARS:
            _fail("local_chat_context_limit", "السياق أكبر من حد المحارف المحلي")
        deadline = time.monotonic() + min(request.deadline_s, MAX_DEADLINE_S)
        thinking = self._preflight(deadline)
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": False, "truncate": False, "shift": False,
            "options": {"num_predict": request.max_output, "temperature": 0,
                        "num_ctx": CONTEXT_TOKENS, "seed": SAMPLING_SEED},
        }
        if thinking:
            payload["think"] = request.thinking
        out = self._json("POST", "/api/chat", payload, deadline)
        _no_remote(out)
        message = out.get("message")
        if (out.get("model") != self.model or out.get("done") is not True
                or not isinstance(message, dict) or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str)
                or out.get("done_reason") not in ("stop", "length")
                or message.get("tool_calls") not in (None, [])
                or message.get("images") not in (None, [])):
            _fail("local_chat_malformed", "استجابة المحادثة النصية غير صالحة")
        return Response(
            content=message["content"],
            usage=Usage(_count(out.get("prompt_eval_count")), _count(out.get("eval_count"))),
            stop_reason="complete" if out["done_reason"] == "stop" else "max_output",
            cost_micros=0, provider=self.name, model_version=self.model_version,
            # التفكيرُ حين طُلب وحده (ك٤٧)؛ والنواةُ تحجره قبل القيد
            thinking=(message.get("thinking") or "") if request.thinking
            and isinstance(message.get("thinking"), (str, type(None))) else "",
        )
