"""عميلُ قياسٍ صغير لنماذج الرؤية في Ollama المحلي (غ٨، غ١١): صورةٌ وسؤالٌ، ونصٌّ يعود.

يقيس النموذجَ لا طريقَ المنتج: لا يمرّ بعقد `providers/local_media.py` (إصدارٌ مثبَّت وغلافُ وسائط)،
فرقمُه رقمُ النموذج على البنك، لا شهادةٌ بأن المنتج يصل إليه.
- **محلّيٌّ افتراضًا:** العنوانُ localhost، ولا يُرسل إلى غيره إلا بعنوانٍ صريح.
- **هويّةُ النموذج مسجَّلة:** بصمتُه من `/api/tags` وإصدارُ Ollama، ونموذجٌ بلا قدرة `vision` يُرفض باسمه.
- **الإعداداتُ ثابتة:** `temperature=0` و`seed=0`، ونموذجُ التفكير يُطلب بلا تفكير (`think=false`).
- **النقلُ قابلٌ للحقن،** فالاختباراتُ كلُّها بلا شبكة.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

DEFAULT_URL = "http://127.0.0.1:11434"
TIMEOUT_S = 600
OPTIONS = {"temperature": 0, "seed": 0, "num_predict": 2048}
_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)

Transport = Callable[[str, str, dict | None], dict]


class VisionRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


def http_transport(base_url: str = DEFAULT_URL, timeout_s: float = TIMEOUT_S) -> Transport:
    def call(method: str, path: str, payload: dict | None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise VisionRefused("ollama_http_error", f"{method} {path}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VisionRefused("ollama_unreachable", f"{base_url}: {type(exc).__name__}") from None
    return call


class OllamaVision:
    def __init__(self, model: str, *, base_url: str = DEFAULT_URL, transport: Transport | None = None,
                 allow_remote: bool = False):
        if not allow_remote and urlparse(base_url).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise VisionRefused("remote_not_allowed", "القياسُ على Ollama المحلي وحده ما لم يُطلب غيرُه صراحةً")
        self.model = model
        self._call = transport or http_transport(base_url)
        shown = self._call("POST", "/api/show", {"model": model})
        capabilities = shown.get("capabilities") or []
        if "vision" not in capabilities:
            raise VisionRefused("model_not_vision", f"«{model}» بلا قدرة رؤية ({', '.join(capabilities) or '—'})")
        self.thinking = "thinking" in capabilities
        tags = self._call("GET", "/api/tags", None).get("models") or []
        entry = next((m for m in tags if m.get("name") == model or m.get("model") == model), None)
        if entry is None:
            raise VisionRefused("model_missing", f"«{model}» غيرُ مثبَّت (ollama pull {model})")
        self.digest = entry.get("digest", "")
        self.runtime = self._call("GET", "/api/version", None).get("version", "")

    @property
    def settings(self) -> dict:
        return {"options": OPTIONS, "think": False if self.thinking else None, "digest": self.digest,
                "ollama": self.runtime}

    def ask(self, image: Path, prompt: str) -> str:
        payload = {"model": self.model, "stream": False, "options": OPTIONS,
                   "messages": [{"role": "user", "content": prompt,
                                 "images": [base64.b64encode(Path(image).read_bytes()).decode("ascii")]}]}
        if self.thinking:
            payload["think"] = False
        out = self._call("POST", "/api/chat", payload)
        message = out.get("message") or {}
        if out.get("done") is not True or not isinstance(message.get("content"), str):
            raise VisionRefused("ollama_malformed", "ردٌّ بلا نصٍّ تامّ")
        return _THINK.sub("", message["content"])
