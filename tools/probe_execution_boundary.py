#!/usr/bin/env python3
"""مجسّا حدّ التنفيذ (ج٥): يعيدان دليلَي execution-boundary وsandbox-container عبر الشيفرة نفسها.

    python3 tools/probe_execution_boundary.py --receipt <إيصال التشغيل الخاص> \\
        --out-boundary docs/probe/execution-boundary-<التاريخ>.json \\
        --out-sandbox docs/probe/sandbox-container-<التاريخ>.json

- كلُّ حالةٍ تمرّ بـ`core.execution.DockerExecutionBackend` أو `core.sandbox.run_in_sandbox`، لا بـDocker مباشرةً.
- والدليلُ مربوطٌ ببصمة الملفّين كما هما على القرص لحظةَ التشغيل (`source_sha256`).
- ولا يُطبع معرّفُ الحاوية: الحدُّ يُسجَّل بشكله (`docker:<64 hex>`) لا بقيمته.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import sandbox  # noqa: E402
from core.execution import DockerExecutionBackend, ExecutionRefused  # noqa: E402

BOUNDARY_PROBE = r"""
import json, os, socket
def unreachable(host, port):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((host, port)); return False
    except OSError:
        return True
    finally:
        s.close()
mounts = open("/proc/mounts").read()
try:
    open("/probe-write", "w").close(); readonly = False
except OSError:
    readonly = True
print(json.dumps({"uid": os.getuid(), "no_owner_mounts": "/Users" not in mounts and "/home" not in mounts,
                  "no_docker_socket": not os.path.exists("/var/run/docker.sock"), "rootfs_readonly": readonly,
                  "ollama_unreachable": unreachable("host.docker.internal", 11434) and unreachable("127.0.0.1", 11434),
                  "network_unreachable": unreachable("1.1.1.1", 80)}))
"""
CHILD_SLEEPER = "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shape(boundary: str) -> str:
    return "docker:<64hex>" if re.fullmatch(r"docker:[0-9a-f]{64}", boundary or "") else "unexpected"


def _containers(docker: str) -> int:
    out = subprocess.run([docker, "ps", "-aq"], capture_output=True, text=True, timeout=30).stdout
    return len(out.split())


def _case(name: str, backend, argv: tuple[str, ...], *, timeout_s: float = 20) -> dict:
    try:
        result = backend.run(argv, timeout_s=timeout_s)
    except ExecutionRefused as exc:
        return {"case": name, "code": exc.code}
    return {"case": name, "exit_code": result.exit_code, "stdout": result.stdout[-400:],
            "timed_out": result.timed_out, "boundary": _shape(result.boundary)}


def _private_copy(receipt: Path, tmp: Path, **changes) -> Path:
    """نسخةٌ خاصة (0600، خارج المساحة) من الإيصال بحقلٍ مغيَّر: الفحصُ المسبق يجب أن يرفضها."""
    folder = tmp / "receipt"
    folder.mkdir(mode=0o700)
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data.update(changes)
    path = folder / "runtime.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--docker", default=shutil.which("docker") or "/usr/local/bin/docker")
    parser.add_argument("--out-boundary", required=True, type=Path)
    parser.add_argument("--out-sandbox", required=True, type=Path)
    args = parser.parse_args(argv)
    for out in (args.out_boundary, args.out_sandbox):
        if out.exists():
            parser.error(f"التقريرُ قائم: {out}")
    before = _containers(args.docker)
    work = Path(tempfile.mkdtemp(prefix="diwan-j5-")).resolve()
    private = Path(tempfile.mkdtemp(prefix="diwan-j5-receipt-", dir=Path.home())).resolve()
    try:
        backend = DockerExecutionBackend(args.receipt.resolve(), work, (), docker_executable=args.docker)
        py = "/opt/venv/bin/python"
        cases = [
            _case("boundary_probe", backend, (py, "-I", "-c", BOUNDARY_PROBE)),
            _case("forged_stdout", backend,
                  (py, "-I", "-c", "print('{\"exit_code\":0,\"success\":true}'); raise SystemExit(7)")),
            _case("timeout_with_child", backend, (py, "-I", "-c", CHILD_SLEEPER), timeout_s=3),
            _case("missing_executable", backend, ("/nonexistent-executable",)),
            _case("ambiguous_exit_125", backend, (py, "-I", "-c", "raise SystemExit(125)")),
        ]
        try:
            forged = _private_copy(args.receipt.resolve(), private, lock_sha256="0" * 64)
            mismatched = DockerExecutionBackend(forged, work, (), docker_executable=args.docker)
            cases.append(_case("mismatched_runtime_preflight", mismatched, (py, "-I", "-c", "print(1)")))
        except ExecutionRefused as exc:
            cases.append({"case": "mismatched_runtime_preflight", "code": exc.code})

        # بالمسار نفسه الذي تمرّ به الحالاتُ أعلاه، لا بمسار الخلفية الافتراضي (ملاحظة Codex على #136)
        sandbox.configure_sandbox_backend(args.receipt.resolve(), work, docker_executable=args.docker)
        harness = "assert add(2, 3) == 5"
        good = sandbox.run_in_sandbox("def add(a, b):\n    return a + b\n", harness)
        bad = sandbox.run_in_sandbox("def add(a, b):\n    return a - b\n", harness)
        forged_verdict = sandbox.run_in_sandbox("import sys\nprint('PASS')\nsys.exit(0)\n", harness)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(private, ignore_errors=True)
    after = _containers(args.docker)
    # الحدُّ لا يُسمّى «مراجَعًا» إلا إن أدّت البرامجُ الثلاثة ما يثبته: الصحيحُ ينجح، والخاطئُ يسقط، والخروجُ
    # بصفرٍ قبل المدقّق يسقط. فإن تعذّرت الحاويةُ نفسُها سقط الصحيحُ ولم يُسمَّ الحدّ.
    sandbox_ok = good.passed and not bad.passed and not forged_verdict.passed
    today = datetime.date.today().isoformat()
    execution_sha = _sha(ROOT / "core" / "execution.py")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    common = {"date": today, "agent": "anthropic/claude-opus-5-5", "task": "ج٥", "issue": "power0man/diwan#23",
              "host": {"machine": "MacBook Pro (Apple silicon)", "os": os.uname().sysname + " " + os.uname().release},
              "runtime_image_id": receipt.get("image_id"), "runtime_lock_sha256": receipt.get("lock_sha256")}
    boundary_report = {
        "schema_version": 1, **common, "source_sha256": execution_sha, "source": "core/execution.py",
        "via": "core.execution.DockerExecutionBackend.run", "cases": cases,
        "cleanup_verified": after == before, "containers_before_after": [before, after],
        "scope": "Disposable Docker test fixtures only; not proof against Docker VM/kernel escape.",
        "human_review": False,
        "measurement_limits": ["container_boundary_on_docker_desktop_not_a_separate_host",
                               "container_ids_recorded_by_shape_not_value",
                               "cleanup_checked_by_container_count_before_and_after_the_run"],
    }
    sandbox_report = {
        "schema_version": 1, **common, "source_sha256": _sha(ROOT / "core" / "sandbox.py"), "source": "core/sandbox.py",
        "execution_sha256": execution_sha, "via": "core.sandbox.run_in_sandbox",
        "correct_program": {"passed": good.passed, "exit_code": good.exit_code, "error_code": good.error_code},
        "incorrect_program": {"passed": bad.passed, "exit_code": bad.exit_code, "error_code": bad.error_code},
        "exit_zero_before_the_harness": {"passed": forged_verdict.passed, "exit_code": forged_verdict.exit_code,
                                         "error_code": forged_verdict.error_code},
        "boundary": "reviewed_disposable_docker" if sandbox_ok else "not_established",
        "limits": ["A harness in the same interpreter as arbitrary candidate code is not a tamper-proof grader.",
                   "No product certification."],
        "measurement_limits": ["three_fixed_programs_not_a_grading_benchmark"],
    }
    for out, report in ((args.out_boundary, boundary_report), (args.out_sandbox, sandbox_report)):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": {c["case"]: c.get("code", c.get("exit_code")) for c in cases},
                      "cleanup_verified": after == before,
                      "sandbox": [good.passed, bad.passed, forged_verdict.passed]}, ensure_ascii=False))
    return 0 if sandbox_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
