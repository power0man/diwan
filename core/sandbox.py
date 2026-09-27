"""Run evaluation code only through the explicitly configured container backend.

The old interpreter patches and DIWAN_DISPOSABLE_HOST declaration did not isolate
the host. Their successful escape probes are historical evidence, not a behavior
to preserve. No candidate Python is written, imported or executed on the host.
The trusted bootstrap must configure a reviewed receipt and a dedicated workspace;
the candidate and its harness are sent as data with an empty file snapshot.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import secrets
import time

from core.execution import DockerExecutionBackend, ExecutionRefused

# الحكمُ حكمُ المدقّق لا رمزُ خروج العملية (ك١٣): لا يُطبع هذا السطرُ إلا إذا بلغ
# التنفيذُ نهايةَ المدقّق بلا استثناء. فجوابٌ ينهي العملية بحالة 0 قبل التحقّق لا
# يبلغه فيسقط، وصوابٌ يُخرج رمزًا غير صفر لسببٍ جانبيّ بعد التحقّق يبقى ناجحًا.
# والحدُّ معلَن: المرشّحُ والمدقّقُ في مفسّرٍ واحد، فمرشّحٌ يتعمّد قراءةَ ثوابت
# الوحدة أو /proc/self/cmdline يستطيع تزوير السطر؛ هذا يمنع السقوطَ العَرَضيّ
# لا الغشَّ المتعمَّد، كما تعلن وثيقةُ الوحدة.
VERDICT_PREFIX = "__DIWAN_VERDICT__"


def verdict_marker(nonce: str) -> str:
    return f"{VERDICT_PREFIX} {nonce} PASS"


def verdict_reached(stdout: str, marker: str) -> bool:
    """آخرُ سطرٍ غيرِ فارغ في المخرَج هو علامةُ الحكم بعينها."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    return bool(lines) and lines[-1].strip() == marker


@dataclass(frozen=True)
class SandboxResult:
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    witness_digest: str
    elapsed_ms: int
    error_code: str | None = None
    boundary: str | None = None


_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_SANDBOX_BACKEND: DockerExecutionBackend | None = None
# Compatibility names remain importable; this variable no longer grants execution.
DISPOSABLE_HOST_ENV = "DIWAN_DISPOSABLE_HOST"
NOT_A_BOUNDARY = "environment_declaration_is_not_a_security_boundary"
REFUSAL_NO_HOST = "execution_backend_unavailable"


def configure_sandbox_backend(receipt_path: Path, workspace_root: Path) -> DockerExecutionBackend:
    """Trusted bootstrap only. No environment fallback or model-tool exposure."""
    global _SANDBOX_BACKEND
    backend = DockerExecutionBackend(receipt_path, workspace_root, ())
    _SANDBOX_BACKEND = backend
    return backend


def sandbox_configuration() -> dict | None:
    """Configuration identity, not a claim that a container has run successfully."""
    if _SANDBOX_BACKEND is None:
        return None
    return {"backend": "docker", **_SANDBOX_BACKEND.receipt, "snapshot_files": []}


def declared_host() -> str | None:
    """Legacy display API: returns configured image identity, never an env flag."""
    config = sandbox_configuration()
    return None if config is None else "docker:" + config["image_id"]


def extract_code(text: str) -> str:
    matches = _CODE_BLOCK_RE.findall(text)
    return "\n\n".join(match.strip() for match in matches) if matches else text.strip()


def _refusal(code_or_answer: str, test_harness: str, start_ns: int, *,
             error_code: str, stderr: str, exit_code: int = -3) -> SandboxResult:
    witness = f"{extract_code(code_or_answer)}\n---HARNESS---\n{test_harness}\n---REFUSED---\n{error_code}"
    return SandboxResult(False, exit_code, "", stderr,
        hashlib.sha256(witness.encode("utf-8")).hexdigest(),
        (time.monotonic_ns() - start_ns) // 1_000_000, error_code, None)


def run_in_sandbox(code_or_answer: str, test_harness: str, *,
                   timeout_s: float = 5.0, memory_limit_mb: int = 256) -> SandboxResult:
    """Preserve the evaluation result contract without any host execution fallback.

    Process exit comes from Docker's inspection, not candidate stdout. This contains
    execution; it does not certify that arbitrary candidate code cannot game a harness
    running in the same interpreter. Such grading claims need independent evidence.
    """
    start_ns = time.monotonic_ns()
    backend = _SANDBOX_BACKEND
    if backend is None:
        return _refusal(code_or_answer, test_harness, start_ns,
            error_code=REFUSAL_NO_HOST,
            stderr="لا منفذ حاوية موثوق مضبوط؛ " + NOT_A_BOUNDARY)
    if (type(memory_limit_mb) is not int or not 1 <= memory_limit_mb <= 1024
            or type(timeout_s) not in (int, float) or not 0 < timeout_s <= 900):
        return _refusal(code_or_answer, test_harness, start_ns,
            error_code="execution_arguments_invalid", stderr="حدود تنفيذ غير صالحة")
    code = extract_code(code_or_answer)
    try:
        ast.parse(code)
        valid_syntax = True
    except SyntaxError:
        valid_syntax = False
    # These limits are applied inside the inspected container. The outer backend
    # independently enforces its memory, PID, CPU and wall-clock limits.
    memory_bytes = memory_limit_mb * 1024 * 1024
    cpu_seconds = max(1, math.ceil(timeout_s))
    marker = verdict_marker(secrets.token_hex(16))
    script = (
        "import resource\n"
        f"resource.setrlimit(resource.RLIMIT_AS, ({memory_bytes}, {memory_bytes}))\n"
        f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds}))\n"
        f"__model_raw_answer__ = {code_or_answer!r}\n"
        f"__model_code__ = {code!r}\n"
        f"__model_syntax_valid__ = {valid_syntax!r}\n"
        + (code + "\n" if valid_syntax else "# Model answer is not valid Python.\n")
        + "# Evaluation harness\n" + test_harness + "\n"
        # الحكم: يُبلَغ فقط إن أكمل المدقّقُ بلا استثناء، فلا يحكم رمزُ الخروج وحده
        + "# Verdict: reached only when the harness completed without raising\n"
        + f"__import__('sys').stdout.write('\\n' + {marker!r} + '\\n')\n"
        + "__import__('sys').stdout.flush()\n"
    )
    try:
        result = backend.run(("/opt/venv/bin/python", "-I", "-c", script), timeout_s=timeout_s)
    except ExecutionRefused as exc:
        if exc.code == "execution_timeout":
            return _refusal(code_or_answer, test_harness, start_ns,
                error_code=exc.code, stderr=exc.reason, exit_code=-1)
        return _refusal(code_or_answer, test_harness, start_ns,
                        error_code=exc.code, stderr=exc.reason)
    if not re.fullmatch(r"docker:[0-9a-f]{64}", result.boundary):
        return _refusal(code_or_answer, test_harness, start_ns,
            error_code="execution_boundary_unverified", stderr="هوية حاوية التنفيذ غير صالحة")
    boundary = "docker:" + backend.receipt["image_id"] + "@" + result.boundary.removeprefix("docker:")
    # الخُلفيّةُ تُبقي ذيلَ المخرَج، فعلامةُ الحكم في آخره تنجو من القصّ.
    passed = verdict_reached(result.stdout, marker)
    stdout, stderr = result.stdout[:16384], result.stderr[:16384]
    code_value = result.exit_code
    witness = (f"{code}\n---HARNESS---\n{test_harness}\n---EXIT---\n{code_value}"
               f"\n---VERDICT---\n{passed}"
               f"\n---STDOUT---\n{stdout}\n---STDERR---\n{stderr}\n---BOUNDARY---\n{boundary}")
    error_code = (None if passed
                  else "verdict_missing" if code_value == 0 else f"exit_{code_value}")
    return SandboxResult(passed, code_value, stdout, stderr,
        hashlib.sha256(witness.encode("utf-8")).hexdigest(),
        (time.monotonic_ns() - start_ns) // 1_000_000, error_code, boundary)
