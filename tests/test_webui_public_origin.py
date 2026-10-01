"""عقدُ الأصل العام الصريح؛ مزوّد مصطنع وجذر مؤقت بلا شبكة خارجية."""
import http.client
import json
import sys
import threading

import pytest

from acceptance_m9 import SyntheticProvider, SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer
from webui.server import LocalApp, Server, UIError


class PublicServer:
    def __init__(self, root, *, public_origin=None, bind="127.0.0.1", server_type=Server):
        self.app = LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
                            provider_factory=lambda: SyntheticProvider([_answer("مصطنع")]))
        self.server = server_type(self.app, 0, bind, public_origin)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()

    def request(self, method, *, host, origin=None, forwarded_host=None,
                path=None, metadata=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            headers = {"Host": host}
            headers.update(metadata or {})
            body = b""
            path = path or "/"
            if forwarded_host is not None:
                headers["X-Forwarded-Host"] = forwarded_host
                headers["X-Forwarded-Proto"] = "https"
            if method == "POST":
                body = json.dumps({"action": "projects"}).encode()
                path = "/api"
                headers.update({"Origin": origin, "X-Diwan-CSRF": self.server.token,
                                "Content-Type": "application/json"})
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, payload
        finally:
            connection.close()


def test_declared_public_origin_accepts_matching_get_and_post_and_announces_status(tmp_path):
    live = PublicServer(tmp_path / "public", public_origin="https://demo.example")
    try:
        assert live.request("GET", host="demo.example")[0] == 200
        status, raw = live.request("POST", host="demo.example", origin="https://demo.example")
        assert status == 200
        assert json.loads(raw)["public_origin"] == "https://demo.example"
    finally:
        live.close()


def test_declared_origin_rejects_other_host_even_when_proxy_headers_claim_it(tmp_path):
    live = PublicServer(tmp_path / "public", public_origin="https://demo.example")
    try:
        status, raw = live.request("GET", host="attacker.invalid",
                                   forwarded_host="demo.example")
        assert status == 403 and json.loads(raw)["error_code"] == "http_refused"
    finally:
        live.close()


def test_declared_origin_rejects_other_post_origin_before_dispatch(tmp_path):
    live = PublicServer(tmp_path / "public", public_origin="https://demo.example")
    try:
        status, raw = live.request("POST", host="demo.example", origin="https://other.invalid")
        assert status == 403 and json.loads(raw)["error_code"] == "http_refused"
        assert live.app.dispatch({"action": "projects"})["projects"] == []
    finally:
        live.close()


def test_default_server_stays_loopback_only_and_rejects_public_host(tmp_path):
    live = PublicServer(tmp_path / "local")
    try:
        status, raw = live.request("GET", host="demo.example")
        assert status == 403 and json.loads(raw)["error_code"] == "http_refused"
        assert live.app.dispatch({"action": "projects"})["public_origin"] is None
    finally:
        live.close()


def test_public_document_navigation_accepts_cross_site_entry(tmp_path):
    live = PublicServer(tmp_path / "navigation", public_origin="https://demo.example")
    try:
        for site in ("cross-site", "same-site"):
            status, body = live.request("GET", host="demo.example", metadata={
                "Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document"})
            assert status == 200 and b"<!doctype html>" in body.lower()
    finally:
        live.close()


def test_navigation_exception_is_limited_to_public_root_get(tmp_path):
    live = PublicServer(tmp_path / "navigation-bounds", public_origin="https://demo.example")
    local = PublicServer(tmp_path / "local-navigation")
    navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                  "Sec-Fetch-Dest": "document"}
    try:
        assert live.request("POST", host="demo.example", origin="https://demo.example",
                            metadata=navigation)[0] == 403
        assert live.request("GET", host="demo.example", path="/app.js", metadata=navigation)[0] == 403
        assert live.request("GET", host="demo.example", metadata={
            **navigation, "Sec-Fetch-Mode": "cors"})[0] == 403
        assert live.request("GET", host="demo.example", metadata={
            **navigation, "Sec-Fetch-Dest": "iframe"})[0] == 403
        assert local.request("GET", host=local.server.origin_host, metadata=navigation)[0] == 403
    finally:
        live.close()
        local.close()


def test_public_navigation_keeps_declared_host_boundary(tmp_path):
    live = PublicServer(tmp_path / "navigation-host", public_origin="https://demo.example")
    try:
        assert live.request("GET", host="attacker.invalid", forwarded_host="demo.example",
                            metadata={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                                      "Sec-Fetch-Dest": "document"})[0] == 403
    finally:
        live.close()


@pytest.mark.parametrize("origin", [
    "http://demo.example", "https://demo.example/", "https://user@demo.example",
    "https://demo.example/path", "https://demo.example?x=1", "https://demo_example",
])
def test_public_origin_is_one_bare_https_origin(tmp_path, origin):
    app = LocalApp(tmp_path / origin.replace("/", "_").replace(":", "_"),
                   model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
                   provider_factory=lambda: SyntheticProvider([_answer("مصطنع")]))
    try:
        with pytest.raises(UIError, match="public_origin_invalid"):
            Server(app, 0, "127.0.0.1", origin)
    finally:
        app.close()


def test_non_loopback_bind_requires_declared_public_origin(tmp_path):
    app = LocalApp(tmp_path / "unsafe-bind", model=SYNTHETIC_MODEL,
                   model_version=SYNTHETIC_VERSION,
                   provider_factory=lambda: SyntheticProvider([_answer("مصطنع")]))
    try:
        with pytest.raises(UIError, match="public_origin_required"):
            Server(app, 0, "0.0.0.0")
    finally:
        app.close()


def test_serve_ui_passes_explicit_bind_and_public_origin(monkeypatch, tmp_path):
    import tools.serve_ui as cli

    captured = {}
    monkeypatch.setenv("DIWAN_CHAT_MODEL", "fixture")
    monkeypatch.setenv("DIWAN_CHAT_DIGEST", "a" * 64)
    monkeypatch.delenv("DIWAN_MEDIA_MODEL", raising=False)
    monkeypatch.delenv("DIWAN_MEDIA_DIGEST", raising=False)
    monkeypatch.setattr(sys, "argv", ["serve_ui", "--root", str(tmp_path / "ui"), "--port", "0",
                                      "--bind", "0.0.0.0", "--public-origin", "https://demo.example"])
    monkeypatch.setattr(cli, "LocalChatProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "LocalToolProvider", lambda *args, **kwargs: object())

    class App:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    class FakeServer:
        origin = "https://demo.example"
        public_origin = origin

        def __init__(self, app, port, bind, public_origin, *, listen=None):
            captured.update(port=port, bind=bind, public_origin=public_origin)

        def serve_forever(self):
            raise KeyboardInterrupt()

        def server_close(self):
            pass

    monkeypatch.setattr(cli, "LocalApp", App)
    monkeypatch.setattr(cli, "Server", FakeServer)
    assert cli.main() == 0
    assert captured == {"port": 0, "bind": "0.0.0.0", "public_origin": "https://demo.example"}


def test_public_origin_keeps_bootstrap_for_non_loopback_peers(tmp_path):
    class RemotePeerServer(Server):
        def finish_request(self, request, client_address):
            super().finish_request(request, ("198.51.100.3", client_address[1]))

    live = PublicServer(tmp_path / "public-bootstrap", bind="0.0.0.0",
                        public_origin="https://demo.example", server_type=RemotePeerServer)
    try:
        assert live.server.requires_bootstrap
        assert live.server.bootstrap_url.startswith("https://demo.example/?bootstrap=")
        assert live.request("GET", host="demo.example")[0] == 403
        assert live.request("GET", host="demo.example", path="/?bootstrap=" + live.server.bootstrap_secret)[0] == 200
    finally:
        live.close()


def test_conflicting_server_binding_options_are_refused():
    with pytest.raises(UIError, match="listen_bind_conflict"):
        with Server(None, 0, "127.0.0.2", "https://demo.example", listen="0.0.0.0"):
            pass
