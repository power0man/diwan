"""Synthetic setup/daemon failures retain their cause and never enable execution."""
import json
import subprocess

import pytest

from core import execution
from tests.test_execution_boundary import FakeDocker
from webui import server


@pytest.mark.parametrize("backend", ["execution", "analysis"])
@pytest.mark.parametrize("failure,suffix", [
    (OSError("private socket path must not leak"), "backend_unavailable"),
    (subprocess.CalledProcessError(1, "private command"), "backend_unavailable"),
    (subprocess.TimeoutExpired("private command", 3), "backend_unavailable"),
    (ValueError("private receipt content must not leak"), "configuration_error"),
], ids=["os", "process", "timeout", "internal"])
def test_setup_failure_classifies_cause_without_leaking_exception(tmp_path, monkeypatch, backend,
                                                                 failure, suffix):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(server, f"configure_{backend}_backend", fail)
    options = {"runtime_receipt" if backend == "execution" else "analysis_receipt": tmp_path / "receipt"}
    app = server.LocalApp(tmp_path.resolve() / "ui", model="fixture", model_version="a" * 64,
        provider_factory=lambda: None, agent_provider_factory=lambda: None, **options)
    try:
        project = app.dispatch({"action": "create_project", "name": "اختبار"})["id"]
        result = app.dispatch({"action": "agent_capabilities", "project": project})
        code_key = "error_code" if backend == "execution" else "analysis_error_code"
        detail_key = "detail" if backend == "execution" else "analysis_detail"
        assert result[code_key] == f"{backend}_{suffix}"
        assert result[detail_key] == type(failure).__name__
        assert "private" not in json.dumps(result)
        assert result[f"{backend}_enabled"] is False
        assert result[f"{backend}_status"] == "unavailable"
        unavailable = {"run_command", "run_tests"} if backend == "execution" else {"analyze_data"}
        assert not unavailable.intersection(tool["name"] for tool in result["tools"])
    finally:
        app.close()


@pytest.mark.parametrize("backend", ["execution", "analysis"])
def test_setup_failure_preserves_named_refusal(tmp_path, monkeypatch, backend):
    failure = execution.ExecutionRefused("execution_receipt_invalid", "private receipt")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(server, f"configure_{backend}_backend", fail)
    options = {"runtime_receipt" if backend == "execution" else "analysis_receipt": tmp_path / "receipt"}
    app = server.LocalApp(tmp_path.resolve() / "ui", model="fixture", model_version="a" * 64,
        provider_factory=lambda: None, agent_provider_factory=lambda: None, **options)
    try:
        project = app.dispatch({"action": "create_project", "name": "اختبار"})["id"]
        result = app.dispatch({"action": "agent_capabilities", "project": project})
        key = "error_code" if backend == "execution" else "analysis_error_code"
        assert result[key] == "execution_receipt_invalid"
        assert "private" not in json.dumps(result)
        assert result[f"{backend}_enabled"] is False
    finally:
        app.close()


@pytest.mark.parametrize("phase", ["image", "boundary", "exit"])
def test_inspect_failure_is_named_bounded_and_closed(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(execution, "_BACKENDS", {})
    root = tmp_path.resolve() / "workspace"
    root.mkdir()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64,
        "lock_sha256": "b" * 64, "python_version": "3.14.7"}))
    receipt.chmod(0o600)
    backend = execution.configure_execution_backend(receipt, root, ())
    fake = FakeDocker(backend)
    stderr = b"omitted-prefix:" + b"x" * 4096 + b"\xff daemon disconnected"
    def daemon(*args, **kwargs):
        failed = (args[:2] == ("image", "inspect") if phase == "image" else
                  args[0] == "inspect" and fake.started == (phase == "exit"))
        if failed:
            fake.calls.append((args, kwargs.get("data", b""), kwargs.get("timeout", 20)))
            return 1, b"not valid JSON", stderr
        return fake(*args, **kwargs)
    monkeypatch.setattr(backend, "_docker", daemon)
    with pytest.raises(execution.ExecutionRefused) as caught:
        backend.run(("/bin/echo", "candidate-must-not-arrive"), timeout_s=5)
    assert caught.value.code == "execution_inspect_failed"
    assert caught.value.reason == stderr[-2048:].decode("utf-8", "replace")
    assert "candidate-must-not-arrive" not in str(fake.calls)
    starts = [data for args, data, _ in fake.calls if args[0] == "start"]
    assert starts == ([] if phase != "exit" else [json.dumps({"runtime": backend.receipt}, ensure_ascii=True).encode() + b"\n"])
    if phase != "image":
        assert fake.calls[-1][0] == ("rm", "--force", fake.name)
