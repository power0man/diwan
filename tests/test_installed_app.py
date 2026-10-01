"""Exercise the distribution, not imports accidentally satisfied by the checkout."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
from urllib.parse import quote
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def distribution(tmp_path_factory):
    source = tmp_path_factory.mktemp("package-source")
    # Read only paths tracked in the public repository.  In particular, never walk
    # an owner's corpus/glossary/source/projection/key stores to prepare this test.
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True,
                             capture_output=True).stdout.split(b"\0")
    for raw in tracked:
        if not raw:
            continue
        relative = raw.decode("utf-8")
        if relative == ".gitignore":
            continue  # The packaging assertion must not inherit VCS exclusions.
        original, target = ROOT / relative, source / relative
        assert original.is_file() and not original.is_symlink(), relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    # Synthetic sentinels exercise the owner's excluded shapes without reading them.
    for path in ("var/private.txt", "corpus/maritime/private.txt", "glossaries/private.json",
                 "sources/private.md", "publish/private.json", "projections/private.json",
                 "keys/private.pem", "evaluation/banks/sealed/secret.json",
                 "webui/static/private.txt", "tools/seed_acquisitions.py",
                 "tools/build_benchmark_suite.py"):
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic-owner-sentinel", encoding="utf-8")
    output = tmp_path_factory.mktemp("distributions")
    subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-t", "sdist",
                    "-d", str(output)], cwd=source, check=True, capture_output=True, text=True)
    return output


def test_wheel_and_sdist_exclude_owner_stores_and_private_keys(distribution):
    with zipfile.ZipFile(next(distribution.glob("*.whl"))) as archive:
        names = archive.namelist()
        contents = [archive.read(n) for n in names]
        required = {"diwan/cli.py", "diwan/_runtime/core/run.py",
                    "diwan/_runtime/webui/static/index.html", "diwan/_runtime/uv.lock",
                    "diwan/_runtime/keys/anchor-ed25519.pub", "diwan/_runtime/keys/anchor-policy.json"}
        assert required <= set(names)
        assert any(n.endswith("entry_points.txt") and b"diwan = diwan.cli:main" in c
                   for n, c in zip(names, contents))
    with tarfile.open(next(distribution.glob("*.tar.gz"))) as archive:
        contents += [archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()]
    assert all(b"synthetic-owner-sentinel" not in content for content in contents)


def test_wheel_runs_outside_the_checkout_with_real_resources(distribution, tmp_path):
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next(distribution.glob("*.whl"))) as archive:
        archive.extractall(installed)
    # -I excludes both cwd and PYTHONPATH; only the wheel is added, not the checkout.
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from diwan.cli import runtime_root, main
root = runtime_root()
sys.path.insert(0, str(root))
from core.signing import load_signing_policy
from webui.server import STATIC
assert (STATIC / 'index.html').is_file()
load_signing_policy()
main(['serve', '--help'])
"""
    # argparse --help exits 0; the separate check below exercises the whole loop.
    result = subprocess.run([sys.executable, "-I", "-c", script, str(installed)], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from diwan.cli import main
raise SystemExit(main(['check', '--json', '--base-url', 'http://127.0.0.1:1']))
"""
    result = subprocess.run([sys.executable, "-I", "-c", script, str(installed)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 3, result.stderr + result.stdout
    report = json.loads(result.stdout)
    steps = {s["step"]: s for s in report["steps"]}
    assert steps["runtime"]["status"] == "ok"
    assert steps["ui"]["status"] == "ok"
    assert steps["agent_turn"]["code"] == "mechanism_only"
    assert steps["policies"]["code"] == "corpus_missing"
    assert not any(s["status"] == "failed" for s in report["steps"])


def test_installed_ui_keeps_state_outside_the_installation(tmp_path, monkeypatch):
    from diwan import cli
    monkeypatch.setenv("DIWAN_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import serve_ui
    calls = []
    monkeypatch.setattr(serve_ui, "main", lambda args, **kw: calls.append((args, kw)) or 0)
    assert cli.main(["serve", "--port", "8766"]) == 0
    assert calls == [(["--port", "8766"], {"default_root": (tmp_path / "data" / "daily-ui").resolve()})]
    monkeypatch.delenv("DIWAN_DATA_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert cli.data_root() == (tmp_path / "xdg" / "diwan").resolve()


def test_non_browser_peer_cannot_steal_csrf_with_forged_routing_headers():
    from webui.server import Server

    class RemotePeerServer(Server):
        """Keep a real TCP exchange while presenting the Docker-side peer address."""

        def get_request(self):
            request, _ = super().get_request()
            return request, ("192.0.2.2", 40000)

    class RecordingApp:
        def __init__(self):
            self.calls = []

        def dispatch(self, request):
            self.calls.append(request)
            return {"projects": []}

    app = RecordingApp()
    with RemotePeerServer(app, 0, listen="0.0.0.0") as server:
        bootstrap = server.bootstrap_secret
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        address = "127.0.0.1"

        def request(method, path, *, headers=None, body=None):
            connection = http.client.HTTPConnection(address, port, timeout=5)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                connection.close()

        forged = {"Host": f"127.0.0.1:{port}"}
        status, body, _ = request("GET", "/", headers=forged)
        assert status == 403 and server.token.encode() not in body and bootstrap.encode() not in body

        payload = json.dumps({"action": "projects"}).encode("utf-8")
        forged_post = {**forged, "Origin": server.origin, "X-Diwan-CSRF": server.token,
                       "Content-Type": "application/json", "Content-Length": str(len(payload))}
        assert request("POST", "/api", headers=forged_post, body=payload)[0] == 403
        assert app.calls == []

        # Invalid Unicode capabilities must be a named HTTP refusal, not a dropped
        # connection, and must leave the real one-use capability available.
        assert request("GET", "/?bootstrap=%D8%B3", headers=forged)[0] == 403
        bad_cookie = {**forged, "Cookie": 'Diwan-Bootstrap="\\351"'}
        assert request("GET", "/", headers=bad_cookie)[0] == 403
        assert server.bootstrap_secret == bootstrap

        status, body, response_headers = request(
            "GET", f"/?bootstrap={quote(bootstrap, safe='')}", headers=forged)
        assert status == 200 and server.token.encode() in body
        cookie = response_headers["Set-Cookie"].split(";", 1)[0]
        assert "HttpOnly" in response_headers["Set-Cookie"] and "SameSite=Strict" in response_headers["Set-Cookie"]
        # The printed bootstrap capability is one-use; a second peer cannot replay it.
        assert request("GET", f"/?bootstrap={quote(bootstrap, safe='')}", headers=forged)[0] == 403
        authorised = {**forged_post, "Cookie": cookie}
        assert request("POST", "/api", headers={**authorised, "X-Diwan-CSRF": "\u00e9"}, body=payload)[0] == 403
        assert app.calls == []
        assert request("POST", "/api", headers=authorised, body=payload)[0] == 200
        assert app.calls == [{"action": "projects"}]

        server.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_container_binding_uses_a_one_time_bootstrap_capability():
    from webui.server import Server
    with Server(None, 0, listen="0.0.0.0") as server:
        assert server.server_address[0] == "0.0.0.0"
        assert server.origin == f"http://127.0.0.1:{server.server_port}"
        assert server.bootstrap_url.startswith(server.origin + "/?bootstrap=")
    with pytest.raises(ValueError, match="invalid_listen_address"):
        Server(None, 0, listen="192.0.2.1")


def test_nonloopback_cli_prints_the_secret_bootstrap_url(monkeypatch, capsys, tmp_path):
    import tools.serve_ui as cli

    monkeypatch.setenv("DIWAN_CHAT_MODEL", "synthetic")
    monkeypatch.setenv("DIWAN_CHAT_DIGEST", "a" * 64)
    monkeypatch.setattr(cli, "LocalChatProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "LocalToolProvider", lambda *args, **kwargs: object())

    class App:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    class FakeServer:
        origin = "http://127.0.0.1:8765"
        bootstrap_url = origin + "/?bootstrap=synthetic-secret"

        def __init__(self, *args, **kwargs):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(cli, "LocalApp", App)
    monkeypatch.setattr(cli, "Server", FakeServer)
    assert cli.main(["--root", str(tmp_path), "--listen", "0.0.0.0"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ديوان المحلي: http://127.0.0.1:8765/?bootstrap=synthetic-secret"
    assert "أحادي الاستخدام" in lines[1]


def test_container_state_uses_the_installed_runtime_and_data_root(distribution, tmp_path):
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next(distribution.glob("*.whl"))) as archive:
        archive.extractall(installed)
    script = tmp_path / "container_state.py"
    shutil.copy2(ROOT / "ci" / "container_state.py", script)
    data = tmp_path / "state"
    # -I still processes site .pth files, including an editable checkout in CI.
    # A successful turn alone therefore does not prove that the wheel supplied it.
    runner = (
        "import pathlib,runpy,sys; "
        "installed=pathlib.Path(sys.argv[1]).resolve(); "
        "sys.path.insert(0, str(installed)); "
        "sys.argv=[sys.argv[2],sys.argv[3]]; "
        "runpy.run_path(sys.argv[0],run_name='__main__'); "
        "assert all(pathlib.Path(sys.modules[name].__file__).resolve().is_relative_to(installed) "
        "for name in ('diwan.cli','core.contracts','webui.server')), 'imports escaped installed wheel'"
    )
    env = {"DIWAN_DATA_HOME": str(data)}
    for mode in ("write", "read"):
        result = subprocess.run([sys.executable, "-I", "-c", runner, str(installed), str(script), mode],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr + result.stdout
    assert (data / "smoke-context.json").is_file()
