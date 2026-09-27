"""Exercise the distribution, not imports accidentally satisfied by the checkout."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def distribution(tmp_path_factory):
    source = tmp_path_factory.mktemp("package-source")
    # Build from an owner-shaped tree without reading any real owner stores.
    shutil.copytree(ROOT, source, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", "var", "dist"))
    for path in ("var/private.txt", "corpus/maritime/private.txt", "keys/private.pem",
                 "evaluation/banks/sealed/secret.json", "webui/static/private.txt",
                 "tools/seed_acquisitions.py", "tools/build_benchmark_suite.py"):
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


def test_container_binding_keeps_local_host_and_origin_checks():
    from webui.server import Server
    with Server(None, 0, listen="0.0.0.0") as server:
        assert server.server_address[0] == "0.0.0.0"
        assert server.origin == f"http://127.0.0.1:{server.server_port}"
    with pytest.raises(ValueError, match="invalid_listen_address"):
        Server(None, 0, listen="192.0.2.1")
