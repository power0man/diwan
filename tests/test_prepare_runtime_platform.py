"""ج٥: the runtime image is built for the host's platform, and the arm64-only runner image is never built for another."""
import sys

import pytest

from ci import prepare_runtime as bootstrap

PYTHON = "python:3.14-slim-bookworm@sha256:" + "a" * 64
NODE = "node:24-bookworm-slim@sha256:" + "b" * 64
IMAGE = "sha256:" + "c" * 64


def _run_main(tmp_path, monkeypatch, *extra):
    lock = tmp_path / "requirements-ci.lock"
    lock.write_text("pytest==1\n")
    builds, receipts = [], []
    monkeypatch.setattr(bootstrap, "validate_trust", lambda path: "d" * 64)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda argv, check: builds.append(argv))
    monkeypatch.setattr(bootstrap, "build_image_id", lambda iid: IMAGE)
    monkeypatch.setattr(bootstrap, "probe", lambda image: {"python_version": "3.14.7"})
    monkeypatch.setattr(bootstrap, "write_receipt", lambda path, data: receipts.append(data))
    monkeypatch.setattr(sys, "argv", ["prepare_runtime.py", "--python-image", PYTHON, "--node-image", NODE,
                                      "--lock", str(lock), "--trust-json", str(tmp_path / "trust.json"),
                                      "--receipt", str(tmp_path / "runtime.json"), *extra])
    assert bootstrap.main() == 0
    return builds, receipts


def _platform(argv):
    return argv[argv.index("--platform") + 1]


def test_default_platform_stays_arm64(tmp_path, monkeypatch):
    builds, receipts = _run_main(tmp_path, monkeypatch)
    assert [_platform(argv) for argv in builds] == ["linux/arm64"]
    assert receipts[0]["image_id"] == IMAGE


def test_amd64_is_passed_to_the_runtime_build(tmp_path, monkeypatch):
    builds, _ = _run_main(tmp_path, monkeypatch, "--platform", "linux/amd64")
    assert [_platform(argv) for argv in builds] == ["linux/amd64"]


def test_runner_image_is_refused_off_arm64(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: pytest.fail("must refuse before building"))
    monkeypatch.setattr(sys, "argv", ["prepare_runtime.py", "--python-image", PYTHON, "--node-image", NODE,
                                      "--lock", str(tmp_path / "lock"), "--trust-json", str(tmp_path / "t"),
                                      "--receipt", str(tmp_path / "r.json"), "--runner-receipt",
                                      str(tmp_path / "runner.json"), "--platform", "linux/amd64"])
    with pytest.raises(SystemExit) as exc:
        bootstrap.main()
    assert exc.value.code == 2


def test_unknown_platform_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["prepare_runtime.py", "--python-image", PYTHON, "--node-image", NODE,
                                      "--lock", str(tmp_path / "lock"), "--trust-json", str(tmp_path / "t"),
                                      "--receipt", str(tmp_path / "r.json"), "--platform", "windows/amd64"])
    with pytest.raises(SystemExit) as exc:
        bootstrap.main()
    assert exc.value.code == 2
