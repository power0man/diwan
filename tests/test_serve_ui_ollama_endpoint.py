import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from providers.base import ProviderError
from providers.local_chat import local_ollama_endpoint
from tools.serve_ui import _discover_ollama


def test_discovery_reaches_the_explicit_ephemeral_ollama_endpoint():
    digest = "d" * 64

    class Ollama(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/tags"
            body = json.dumps({"models": [{"name": "fixture:1b", "digest": digest,
                                            "size": 1}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert _discover_ollama(url) == ("fixture:1b", digest, None, None, None)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("url", [
    "http://example.com:11434", "http://203.0.113.7:11434", "https://127.0.0.1:11434",
    "http://127.0.0.1", "http://127.0.0.1:11434/api", "not a url",
])
def test_non_loopback_or_ambiguous_ollama_endpoint_is_named(url):
    with pytest.raises(ProviderError) as exc:
        local_ollama_endpoint(url)
    assert exc.value.code == "local_chat_endpoint_invalid"


def test_serve_ui_passes_environment_endpoint_to_every_local_provider(monkeypatch, tmp_path):
    import tools.serve_ui as cli

    url = "http://127.0.0.1:49152"
    made = []
    monkeypatch.setenv("DIWAN_OLLAMA_URL", url)
    monkeypatch.setenv("DIWAN_CHAT_MODEL", "fixture:1b")
    monkeypatch.setenv("DIWAN_CHAT_DIGEST", "a" * 64)
    monkeypatch.setenv("DIWAN_MEDIA_MODEL", "vision:1b")
    monkeypatch.setenv("DIWAN_MEDIA_DIGEST", "b" * 64)
    monkeypatch.setattr(sys, "argv", ["serve_ui", "--root", str(tmp_path), "--port", "0"])
    monkeypatch.setattr(cli, "LocalChatProvider", lambda *a, **kw: made.append(("chat", kw["base_url"])))
    monkeypatch.setattr(cli, "LocalToolProvider", lambda *a, **kw: made.append(("tools", kw["base_url"])))
    monkeypatch.setattr(cli, "LocalMediaProvider", lambda *a, **kw: made.append(("media", kw["base_url"])))

    class App:
        def __init__(self, *args, **kwargs):
            self.factories = (kwargs["provider_factory"], kwargs["agent_provider_factory"],
                              kwargs["media_provider_factory"])

        def close(self):
            pass

    class Server:
        origin = "http://127.0.0.1:0"

        def __init__(self, app, port, **kwargs):
            self.app = app

        def serve_forever(self):
            for factory in self.app.factories:
                factory()
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(cli, "LocalApp", App)
    monkeypatch.setattr(cli, "Server", Server)
    assert cli.main() == 0
    assert made == [("chat", url), ("tools", url), ("media", url),
                    ("chat", url), ("tools", url), ("media", url)]
