"""Sandbox adapter contracts only; fake results do not certify live isolation.

Historical os.open/_socket/ctypes escapes disproved the interpreter-patch design.
Their payloads remain as refusal regressions, never as successful host escapes.
Live container probes are a separate acceptance gate.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

import pytest

from core import sandbox
from core.execution import ExecutionRefused, ExecutionResult

IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64


MARKER_RE = re.compile(r"__DIWAN_VERDICT__ [0-9a-f]{32} PASS")


def marker_in(script: str) -> str:
    """علامةُ الحكم كما زرعها الصندوق في ذيل السكربت (يعرفها الاختبار من السكربت، لا يخمّنها)."""
    found = MARKER_RE.findall(script)
    assert len(found) == 1, found
    return found[0]


class RecordingBackend:
    receipt = {"image_id": IMAGE, "lock_sha256": "c" * 64, "python_version": "3.14.7"}

    def __init__(self, *, result=None, error=None, exit_code=0):
        # بلا نتيجةٍ مثبَّتة: خُلفيّةٌ يبلغ فيها المدقّقُ نهايتَه فيطبع علامةَ الحكم
        self.result = result
        self.exit_code = exit_code
        self.error = error
        self.calls = []

    def run(self, argv, *, timeout_s):
        self.calls.append((argv, timeout_s))
        if self.error:
            raise ExecutionRefused(self.error, "synthetic backend refusal")
        if self.result is not None:
            return self.result
        return ExecutionResult(self.exit_code, "ok\n" + marker_in(argv[3]) + "\n", "",
                               "docker:" + CONTAINER)


@pytest.fixture(autouse=True)
def unconfigured(monkeypatch):
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", None)


@pytest.mark.parametrize("declaration", [None, "pytest-declared-host", "", "x" * 65, "مضيف", "has space"])
def test_environment_never_authorizes_host_execution(monkeypatch, tmp_path, declaration):
    if declaration is None:
        monkeypatch.delenv(sandbox.DISPOSABLE_HOST_ENV, raising=False)
    else:
        monkeypatch.setenv(sandbox.DISPOSABLE_HOST_ENV, declaration)
    marker = tmp_path / "must-not-run"
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("host process contacted"))
    result = sandbox.run_in_sandbox(f"open({str(marker)!r}, 'w').write('bad')", "pass")
    assert result.passed is False
    assert result.error_code == "execution_backend_unavailable"
    assert result.boundary is None
    assert sandbox.NOT_A_BOUNDARY in result.stderr
    assert len(result.witness_digest) == 64
    assert not marker.exists()
    assert sandbox.declared_host() is None
    assert sandbox.sandbox_configuration() is None


@pytest.mark.parametrize("payload", [
    "import os\nfd = os.open({marker!r}, os.O_WRONLY | os.O_CREAT, 0o600)\nos.close(fd)",
    "import _socket\ns = _socket.socket()\ns.close()",
    "import ctypes\nassert hasattr(ctypes.CDLL(None), 'system')",
])
def test_historical_escape_payloads_are_refused_before_execution(monkeypatch, tmp_path, payload):
    monkeypatch.setenv(sandbox.DISPOSABLE_HOST_ENV, "historical-declaration")
    marker = tmp_path / "historical-escape"
    result = sandbox.run_in_sandbox(payload.format(marker=str(marker)), "pass")
    assert result.error_code == "execution_backend_unavailable"
    assert not marker.exists()


def test_trusted_bootstrap_uses_empty_snapshot_and_explicit_configuration(monkeypatch, tmp_path):
    captured = []
    backend = RecordingBackend()
    def constructor(receipt, root, files):
        captured.append((receipt, root, files))
        return backend
    monkeypatch.setattr(sandbox, "DockerExecutionBackend", constructor)
    receipt, root = tmp_path / "receipt.json", tmp_path / "workspace"
    assert sandbox.configure_sandbox_backend(receipt, root) is backend
    assert captured == [(receipt, root, ())]
    assert sandbox.sandbox_configuration() == {"backend": "docker", **backend.receipt, "snapshot_files": []}
    assert sandbox.declared_host() == "docker:" + IMAGE


def test_code_and_harness_are_only_data_sent_to_configured_backend(monkeypatch, tmp_path):
    backend = RecordingBackend()
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    marker = tmp_path / "no-host-effect"
    code = f"open({str(marker)!r}, 'w').write('inside only')"
    harness = "assert 2 + 2 == 4"
    result = sandbox.run_in_sandbox(f"```python\n{code}\n```", harness, timeout_s=7)
    argv, timeout = backend.calls[0]
    assert argv[:3] == ("/opt/venv/bin/python", "-I", "-c")
    assert code in argv[3] and harness in argv[3]
    assert "resource.setrlimit(resource.RLIMIT_AS" in argv[3]
    ast.parse(argv[3])
    assert timeout == 7 and not marker.exists()
    assert result.passed and result.exit_code == 0
    assert result.boundary == "docker:" + IMAGE + "@" + CONTAINER
    assert len(result.witness_digest) == 64


def test_non_python_answer_is_available_to_harness_without_becoming_executable(monkeypatch):
    backend = RecordingBackend()
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    answer = 'الإجابة هي: {"order": ["ج", "أ", "ب", "د"]}'
    sandbox.run_in_sandbox(answer, "assert 'order' in __model_raw_answer__")
    tree = ast.parse(backend.calls[0][0][3])
    metadata = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    assert metadata["__model_raw_answer__"] == answer
    assert metadata["__model_code__"] == answer
    assert metadata["__model_syntax_valid__"] is False


def test_exit_comes_from_backend_not_forged_stdout(monkeypatch):
    backend = RecordingBackend(result=ExecutionResult(7, '{"exit_code":0,"passed":true}',
                                "AssertionError", "docker:" + CONTAINER))
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    result = sandbox.run_in_sandbox("x=1", "assert x == 2")
    assert result.exit_code == 7 and result.passed is False
    assert result.error_code == "exit_7"
    assert result.boundary == "docker:" + IMAGE + "@" + CONTAINER


@pytest.mark.parametrize("code", ["execution_timeout", "execution_cleanup_unverified",
                                   "execution_image_unavailable", "execution_boundary_unverified"])
def test_backend_refusals_are_named_unmeasured_results(monkeypatch, code):
    backend = RecordingBackend(error=code)
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    result = sandbox.run_in_sandbox("x=1", "assert x == 1")
    assert result.error_code == code
    assert result.passed is False and result.boundary is None
    assert result.exit_code == (-1 if code == "execution_timeout" else -3)


@pytest.mark.parametrize("kwargs", [{"memory_limit_mb": True}, {"memory_limit_mb": 1025},
    {"memory_limit_mb": 0}, {"timeout_s": 0}, {"timeout_s": float("nan")},
    {"timeout_s": float("inf")}, {"timeout_s": True}, {"timeout_s": 10 ** 400}])
def test_invalid_limits_are_refused_before_backend(monkeypatch, kwargs):
    backend = RecordingBackend()
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    assert sandbox.run_in_sandbox("x=1", "pass", **kwargs).error_code == "execution_arguments_invalid"
    assert backend.calls == []


def test_output_is_bounded_and_boundary_changes_witness(monkeypatch):
    backend = RecordingBackend(result=ExecutionResult(0, "x" * 20000, "y" * 20000,
                                                       "docker:" + CONTAINER))
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    first = sandbox.run_in_sandbox("x=1", "pass")
    backend.result = ExecutionResult(0, "x" * 20000, "y" * 20000, "docker:" + "d" * 64)
    second = sandbox.run_in_sandbox("x=1", "pass")
    assert len(first.stdout) == len(first.stderr) == 16384
    assert first.witness_digest != second.witness_digest


def test_extract_code_from_markdown():
    assert sandbox.extract_code("إليك:\n```python\nx = 1\n```\nشرح") == "x = 1"


def test_adapter_has_no_host_execution_imports_or_calls():
    tree = ast.parse(Path(sandbox.__file__).read_text())
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imports & {"subprocess", "os", "sys", "tempfile", "ctypes"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id in {"exec", "eval", "compile"}]


# ————— ك١٣: الحكمُ حكمُ المدقّق لا رمزُ خروج العملية —————

def test_exit_zero_without_reaching_the_verdict_is_not_a_pass(monkeypatch):
    """جوابٌ ينهي العملية بحالة 0 قبل التحقّق (os._exit(0)) كان يمرّ؛ لم يعد."""
    backend = RecordingBackend(result=ExecutionResult(0, "ok", "", "docker:" + CONTAINER))
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    result = sandbox.run_in_sandbox("import os\nos._exit(0)", "assert False")
    assert result.passed is False and result.exit_code == 0
    assert result.error_code == "verdict_missing"


def test_verdict_reached_survives_a_side_effect_nonzero_exit(monkeypatch):
    """صوابٌ بلغ نهايةَ المدقّق ثم خرجت العمليةُ بغير صفرٍ لسببٍ جانبيّ يبقى ناجحًا."""
    backend = RecordingBackend(exit_code=3)
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    result = sandbox.run_in_sandbox("x = 2", "assert x == 2")
    assert result.passed is True and result.exit_code == 3
    assert result.error_code is None


def test_a_forged_verdict_line_without_the_run_nonce_is_not_a_pass(monkeypatch):
    backend = RecordingBackend(result=ExecutionResult(
        0, "__DIWAN_VERDICT__ " + "0" * 32 + " PASS\n", "", "docker:" + CONTAINER))
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    result = sandbox.run_in_sandbox("print('__DIWAN_VERDICT__ ' + '0' * 32 + ' PASS')", "assert False")
    assert result.passed is False and result.error_code == "verdict_missing"


def test_a_verdict_followed_by_more_output_is_not_the_verdict(monkeypatch):
    """العلامةُ آخرُ سطرٍ أو لا شيء: ما يُطبع بعدها لم يمرّ بالمدقّق."""
    class Trailing(RecordingBackend):
        def run(self, argv, *, timeout_s):
            self.calls.append((argv, timeout_s))
            return ExecutionResult(0, marker_in(argv[3]) + "\nmore\n", "", "docker:" + CONTAINER)
    backend = Trailing()
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    assert sandbox.run_in_sandbox("x = 1", "pass").passed is False


def test_the_verdict_trailer_follows_the_harness_and_its_nonce_changes_per_run(monkeypatch):
    backend = RecordingBackend()
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", backend)
    sandbox.run_in_sandbox("x = 1", "assert x == 1  # HARNESS-END")
    sandbox.run_in_sandbox("x = 1", "assert x == 1  # HARNESS-END")
    first, second = (call[0][3] for call in backend.calls)
    assert first.index("# HARNESS-END") < first.index(marker_in(first))
    assert marker_in(first) != marker_in(second)
    ast.parse(first)


def test_verdict_reached_requires_the_exact_last_line():
    marker = sandbox.verdict_marker("ab" * 16)
    assert sandbox.verdict_reached("noise\n" + marker + "\n", marker)
    assert sandbox.verdict_reached(marker, marker)
    assert not sandbox.verdict_reached("", marker)
    assert not sandbox.verdict_reached(marker + " extra", marker)
    assert not sandbox.verdict_reached(marker + "\nlater", marker)
    assert not sandbox.verdict_reached(sandbox.verdict_marker("cd" * 16), marker)
