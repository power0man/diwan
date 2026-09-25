"""اختبارات عدم تكرار عيب البروكسي (المهمة ٠ في PROMPT-CEILING-FLOOR.md).

يتحقق هذا الاختبار من أن مزود Ollama (providers/ollama.py) وأداة قياس
النماذج (tools/model_probe.py) لا يطيعان متغيرات بيئة البروكسي (HTTP_PROXY / ALL_PROXY)
ولا يسربان الحمولات المحلية (local_only) إلى خوادم وسيطة عند غياب no_proxy.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
from typing import Iterator

import pytest

from core.contracts import Message, Request
from providers.ollama import OllamaProvider
import tools.model_probe as probe


class MockOllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)
        data = json.loads(body.decode("utf-8"))

        if self.path == "/api/chat":
            resp = {
                "model": data.get("model", "test-model"),
                "message": {"role": "assistant", "content": "جواب محلي آمن"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 10,
                "eval_count": 5,
            }
        elif self.path == "/api/generate":
            resp = {
                "model": data.get("model", "test-model"),
                "response": "جواب توليد محلي",
                "done": True,
            }
        else:
            self.send_response(404)
            self.end_headers()
            return

        resp_bytes = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)


@pytest.fixture
def mock_ollama_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), MockOllamaHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ollama_provider_ignores_proxy_environment(mock_ollama_server, monkeypatch):
    """تحقق من أن OllamaProvider يتصل محلياً حتى مع وجود HTTP_PROXY خبيث وغياب no_proxy."""
    # تعيين بروكسي غير موجود ليفشل أي اتصال يمر عبره
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:59999")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:59999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:59999")
    monkeypatch.setenv("all_proxy", "http://127.0.0.1:59999")
    # مسح no_proxy لمحاكاة بيئة ماك الافتراضية
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    provider = OllamaProvider(model="test-model", base_url=mock_ollama_server)
    req = Request(
        messages=(Message("user", "سؤال سري محلي"),),
        model="test-model",
        model_version="1",
        max_output=100,
        deadline_s=10.0,
        data_policy="local_only",
        idempotency_key="key-proxy-test-1",
    )

    # النداء يجب أن ينجح مباشرة متجاوزاً البروكسي
    response = provider.complete(req)
    assert response.content == "جواب محلي آمن"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5


def test_model_probe_call_ignores_proxy_environment(mock_ollama_server, monkeypatch):
    """تحقق من أن model_probe.call يتصل محلياً حتى مع وجود HTTP_PROXY خبيث وغياب no_proxy."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:59999")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:59999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:59999")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    monkeypatch.setattr(probe, "BASE", mock_ollama_server)
    out = probe.call("test-model", "فحص النموذج", max_out=32, timeout=5)
    assert out.get("response") == "جواب توليد محلي"
