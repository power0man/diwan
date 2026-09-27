"""اكتشافُ Ollama يسمّي سببَ تعذّره بدل أن يطوي كلَّ عطبٍ في «شغّل Ollama» (مسحُ الإخفاقات الصامتة)."""
from __future__ import annotations

import json
import urllib.request

from tools.serve_ui import _discover_ollama


class _Reply:
    def __init__(self, models):
        self.models = models

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps({"models": self.models}).encode()


def test_an_unreachable_ollama_is_named_by_its_exception_type(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise ConnectionRefusedError(111, "refused")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    assert _discover_ollama() == (None, None, None, None, "ollama_unreachable:ConnectionRefusedError")


def test_an_ollama_without_a_usable_local_chat_model_says_so(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            return _Reply([{"name": "qwen2.5:3b", "digest": "b" * 64, "size": 100, "remote_model": "remote"}])
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    assert _discover_ollama() == (None, None, None, None, "no_local_chat_model")
