"""المراجعةُ الخارجية على واجهة ollama.com بمفتاحٍ من البيئة وحدها (ك٣١، ق٦٠).

نقطتان لا ثالثَ لهما: الخادمُ المحلي بلا ترويسة تفويض، و`https://ollama.com` بترويسة
`Authorization: Bearer` من `OLLAMA_API_KEY`. أيُّ نقطةٍ أخرى مرفوضة بمفتاحٍ أو بدونه؛ والسحابةُ
بلا مفتاح رفضٌ مسمًّى؛ والمفتاحُ لا يظهر في خطأٍ ولا تقريرٍ ولا سطرِ أوامر. **الحدُّ المعلَن:**
النداءُ الحيّ لا يُختبر هنا؛ الطلبُ يُلتقط قبل الشبكة.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import external_review as cli  # noqa: E402
from evaluation.multi_system_review import AutomaticReviewError  # noqa: E402

KEY = "sk-test-secret-key-0123456789"


class _Opener:
    """يلتقط الطلبَ قبل الشبكة ويردّ ردًّا معلَّبًا أو خطأً."""

    def __init__(self, content: str = "{}", error: Exception | None = None):
        self.requests: list[urllib.request.Request] = []
        self.content, self.error = content, error

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps({"message": {"content": self.content}}).encode("utf-8"))


def _capture(chat: cli.OllamaChat, **kw) -> _Opener:
    opener = _Opener(**kw)
    chat.opener = opener
    return opener


def test_the_local_endpoint_sends_no_authorization_header():
    chat = cli.OllamaChat()
    opener = _capture(chat)
    assert chat("m", "s", "u", {}) == "{}"
    request = opener.requests[0]
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert not request.has_header("Authorization") and chat.cloud is False


def test_the_cloud_endpoint_requires_the_key_and_sends_it_as_a_bearer_header():
    with pytest.raises(AutomaticReviewError) as refused:
        cli.OllamaChat(cli.CLOUD_ENDPOINT)
    assert refused.value.code == "cloud_key_missing" and KEY not in str(refused.value)
    chat = cli.OllamaChat(cli.CLOUD_ENDPOINT, api_key=KEY)
    opener = _capture(chat)
    chat("deepseek-v4-flash:cloud", "s", "u", {})
    request = opener.requests[0]
    assert request.full_url == "https://ollama.com/api/chat" and chat.cloud is True
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert request.get_header("Content-type") == "application/json"


@pytest.mark.parametrize("url", ["https://example.com", "http://10.0.0.5:11434", "https://ollama.com.evil.example",
                                 "http://ollama.com", "https://api.ollama.com", "https://ollama.com/v1"])
def test_any_other_endpoint_is_refused_with_or_without_a_key(url):
    for key in (None, KEY):
        with pytest.raises(AutomaticReviewError) as refused:
            cli.OllamaChat(url, api_key=key)
        assert refused.value.code == "local_endpoint_required"


def test_the_key_never_appears_in_a_transport_error():
    chat = cli.OllamaChat(cli.CLOUD_ENDPOINT, api_key=KEY)
    _capture(chat, error=urllib.error.HTTPError("https://ollama.com/api/chat", 401, "Unauthorized", {}, None))
    with pytest.raises(AutomaticReviewError) as failed:
        chat("deepseek-v4-flash:cloud", "s", "u", {})
    assert failed.value.code == "http_401" and KEY not in str(failed.value) and KEY not in repr(failed.value)


def test_the_key_comes_from_the_environment_only_and_not_from_the_command_line(monkeypatch):
    assert "--api-key" not in cli.main.__code__.co_consts and "api_key" not in " ".join(
        a for a in cli.main.__code__.co_consts if isinstance(a, str) and a.startswith("--"))
    with pytest.raises(AutomaticReviewError) as refused:
        cli.build_transport(cli.CLOUD_ENDPOINT, environ={})
    assert refused.value.code == "cloud_key_missing"
    assert cli.build_transport(cli.CLOUD_ENDPOINT, environ={cli.CLOUD_KEY_ENV: KEY}).cloud is True
    assert cli.build_transport("http://127.0.0.1:11434", environ={}).cloud is False


def test_main_wires_the_environment_key_into_the_cloud_request_and_never_prints_it(tmp_path, monkeypatch, capsys):
    """التجربةُ الحيّة بنقطةٍ سحابية: الطلبُ يحمل المفتاح، والتقريرُ والمخرجُ لا يحملانه."""
    opener = _Opener(error=urllib.error.HTTPError("https://ollama.com/api/chat", 503, "down", {}, None))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    monkeypatch.setenv(cli.CLOUD_KEY_ENV, KEY)
    out = tmp_path / "smoke.json"
    code = cli.main(["--smoke", str(out), "--base-url", cli.CLOUD_ENDPOINT])
    assert code == 1, "نداءٌ فاشل تجربةٌ فاشلة لا مرفوضة"
    assert opener.requests and all(r.full_url == "https://ollama.com/api/chat" for r in opener.requests)
    assert all(r.get_header("Authorization") == f"Bearer {KEY}" for r in opener.requests)
    printed = capsys.readouterr().out
    assert KEY not in printed and KEY not in out.read_text(encoding="utf-8")
    assert "http_503" in out.read_text(encoding="utf-8")


def test_main_without_the_key_refuses_the_cloud_endpoint_by_name(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(cli.CLOUD_KEY_ENV, raising=False)
    code = cli.main(["--smoke", str(tmp_path / "smoke.json"), "--base-url", cli.CLOUD_ENDPOINT])
    assert code == 2
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["code"] == "cloud_key_missing"
    assert not (tmp_path / "smoke.json").exists()
