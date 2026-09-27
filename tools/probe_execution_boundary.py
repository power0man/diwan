#!/usr/bin/env python3
"""مجسّا حدّ التنفيذ (ج٥): يعيدان دليلَي execution-boundary وsandbox-container عبر الشيفرة نفسها.

    python3 tools/probe_execution_boundary.py --receipt <إيصال التشغيل الخاص> \\
        --out-boundary docs/probe/execution-boundary-<التاريخ>.json \\
        --out-sandbox docs/probe/sandbox-container-<التاريخ>.json

- كلُّ حالةٍ تمرّ بـ`core.execution.DockerExecutionBackend` أو `core.sandbox.run_in_sandbox`، لا بـDocker مباشرةً.
- والدليلُ مربوطٌ ببصمة الملفّين وبصمةِ المجسّ نفسِه، تُؤخذ قبل أولى الحالات ويُرفض التقريرُ إن تغيّرت قبل كتابته
  (`source_sha256` و`probe_sha256`).
- ولا يُطبع معرّفُ الحاوية: الحدُّ يُسجَّل بشكله (`docker:<64 hex>`) لا بقيمته.
"""
from __future__ import annotations

import argparse
import atexit
import datetime
import hashlib
import inspect
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
SOURCES = ("core/execution.py", "core/sandbox.py", "tools/probe_execution_boundary.py")
NOT_SOURCE = frozenset({"tests", "__pycache__", "site-packages", "venv"})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree() -> dict[str, str]:
    """بصماتُ كلّ وحدةٍ من المستودع، بلا الاختبارات والحزم المثبَّتة والمجلّدات المخفيّة."""
    out = {}
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in NOT_SOURCE and not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                path = Path(folder) / name
                out[path.relative_to(ROOT).as_posix()] = _sha(path)
    return out


# والشيفرةُ المقيسة تُترجم من مصدرها المبصوم في مخبأ بايتاتٍ جديدٍ فارغ، لا من `.pyc` في `__pycache__`: بايتاتٌ قديمة
# يطابق وقتُها وحجمُها مصدرًا عُدّل تُنفَّذ والبصمةُ للجديد (ملاحظة Codex على #144). ويُضبط حين يُشغَّل المجسُّ وحده،
# فاستيرادُه في الاختبارات لا يغيّر مخبأ العملية، ولا يُقبل تشغيلٌ بغيره (`_cached_elsewhere`)
PYCACHE = None
if __name__ == "__main__":
    PYCACHE = tempfile.mkdtemp(prefix="diwan-probe-pycache-")
    sys.pycache_prefix = PYCACHE
    atexit.register(shutil.rmtree, PYCACHE, True)

# بصماتُ المستودع قبل تحميل الشيفرة المقيسة: وحدةٌ استُبدلت بين تحميلها وأول بصمةٍ تُنفَّذ قديمةً ويُسجَّل جديدُها، ويُبقيه
# الفحصُ بعد التشغيل؛ فكلُّ بصمةٍ تُسجَّل تساوي ما قبل التحميل (ملاحظة Codex على #144)
BEFORE_IMPORT = _tree()

from core import sandbox  # noqa: E402
from core.execution import DockerExecutionBackend, ExecutionRefused, _clean_env  # noqa: E402

# `core.sandbox.configure_sandbox_backend` يبني خلفيّته بمسار Docker الافتراضي في `DockerExecutionBackend`، ولا يقبل غيرَه.
# وتمريرُ المسار إليه من مسار openai (ق٦٦، #142)، فالمجسُّ يرفض مسارًا آخر قبل أن يسمّي حدًّا لم يختبره (ملاحظة Codex على #136).
# ويُقرأ الافتراضيُّ من الشيفرة نفسها لا من نسخةٍ هنا، فإن تغيّر هناك تبعه المجسّ
SANDBOX_DOCKER = inspect.signature(DockerExecutionBackend).parameters["docker_executable"].default
# ما يجب أن ينتهي إليه كلُّ برنامجٍ من الثلاثة بعينه، لا نجاحُه أو سقوطُه وحده: سقوطٌ لرفض الخلفية أو خطأِ صياغة
# لا يشهد بأن المدقّق حكم ولا بأن الخروج المبكّر رُدّ (ملاحظة Codex على #136)
SANDBOX_EXPECTED = {
    "correct_program": {"passed": True, "exit_code": 0, "error_code": None},
    "incorrect_program": {"passed": False, "exit_code": 1, "error_code": "exit_1"},
    "exit_zero_before_the_harness": {"passed": False, "exit_code": 0, "error_code": "verdict_missing"},
}

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
# علَمُ التركيب نفسُه لا محاولةُ كتابة: مستخدمٌ غيرُ الجذر لا يكتب في `/` ولو كان الجذرُ قابلًا للكتابة
readonly = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
print(json.dumps({"uid": os.getuid(), "no_owner_mounts": "/Users" not in mounts and "/home" not in mounts,
                  "no_docker_socket": not os.path.exists("/var/run/docker.sock"), "rootfs_readonly": readonly,
                  "ollama_unreachable": unreachable("host.docker.internal", 11434) and unreachable("127.0.0.1", 11434),
                  "network_unreachable": unreachable("1.1.1.1", 80)}))
"""
CHILD_SLEEPER = "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)"
# الحمولةُ المزوّرة التي تطبعها الحالةُ قبل خروجها بـ7؛ ولا تُقبل الحالةُ إلا إن رُئيت في stdout (ملاحظة Codex على #136)
FORGED_PAYLOAD = '{"exit_code":0,"success":true}'
def _repo_modules(modules=None):
    """وحداتُ المستودع المحمَّلة: (مسارُها النسبيّ، الوحدة)، بلا الاختبارات والحزم المثبَّتة والمجلّدات المخفيّة."""
    for module in list((sys.modules if modules is None else modules).values()):
        file = getattr(module, "__file__", None)
        if not isinstance(file, str) or not file.endswith(".py"):
            continue
        try:
            rel = Path(file).resolve().relative_to(ROOT)
        except ValueError:
            continue
        if not NOT_SOURCE.intersection(rel.parts) and not any(part.startswith(".") for part in rel.parts):
            yield rel.as_posix(), module


# ما يُبصم قبل التشغيل ويُعاد بصمُه قبل الكتابة: الشيفرةُ المقيسة، والمجسُّ الذي يقيسها (ملاحظة Codex على #136)؛ ومعها
# كلُّ وحدةٍ من المستودع محمَّلةٍ في العملية، لا قائمةٌ منتقاة: `ExecutionRefused` يرث `.code` من `core/canonical.py`
# (ملاحظة Codex على #144)
def _sources() -> dict[str, str]:
    paths = set(SOURCES) | {path for path, _ in _repo_modules()}
    return {path: _sha(ROOT / path) for path in sorted(paths)}


def _cached_elsewhere(modules=None) -> list[str]:
    """وحداتُ المستودع المحمَّلة التي لم تُترجم في مخبأ المجسّ الجديد (`PYCACHE`)، فقد تُنفَّذ من بايتاتٍ قديمة.
    والمجسُّ نفسُه (`__main__`) يُترجم من مصدره حين يُشغَّل."""
    fresh = lambda cached: (PYCACHE is not None and isinstance(cached, str)
                            and Path(cached).resolve().is_relative_to(Path(PYCACHE).resolve()))
    return sorted(path for path, module in _repo_modules(modules)
                  if module.__name__ != "__main__" and not fresh(getattr(module, "__cached__", None)))


def _shape(boundary: str) -> str:
    return "docker:<64hex>" if re.fullmatch(r"docker:[0-9a-f]{64}", boundary or "") else "unexpected"


def _containers(docker: str) -> set[str] | None:
    """معرّفاتُ الحاويات كاملةً لا عددُها: حاويةٌ غريبة تُحذف وحاويةُ مجسٍّ تبقى يتساوى بهما العدد (ملاحظة Codex على #136).
    والمعرّفاتُ لا تُكتب في التقرير، بل عددُها وعددُ ما بقي. وتعذُّرُ العدّ `None` لا مجموعةٌ فارغة، فلا يُقرأ خادمٌ
    غائب تنظيفًا تامًّا (ملاحظة Codex على #136). وبالبيئة النظيفة التي تشغّل بها الخلفيّةُ Docker، فلا يُعدّ خادمٌ غيرُ
    خادم الحالات (ملاحظة Codex على #144)."""
    try:
        done = subprocess.run([docker, "ps", "-aq", "--no-trunc"], capture_output=True, text=True, timeout=30,
                              env=_clean_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return set(done.stdout.split()) if done.returncode == 0 else None


def _case(name: str, backend, argv: tuple[str, ...], *, timeout_s: float = 20) -> dict:
    try:
        result = backend.run(argv, timeout_s=timeout_s)
    except ExecutionRefused as exc:
        return {"case": name, "code": exc.code}
    return {"case": name, "exit_code": result.exit_code, "stdout": result.stdout[-400:],
            "timed_out": result.timed_out, "boundary": _shape(result.boundary)}


# ما يجب أن تنتهي إليه كلُّ حالة؛ فالخروجُ بصفرٍ لا يكون إلا إن انتهت كلُّها إليه وتحقّق التنظيف (ملاحظة Codex على #136)
EXPECTED_CODES = {"timeout_with_child": "execution_timeout", "missing_executable": "execution_outcome_unverified",
                  "ambiguous_exit_125": "execution_outcome_unverified",
                  "mismatched_runtime_preflight": "execution_runtime_mismatch"}
PROBE_FLAGS = ("no_owner_mounts", "no_docker_socket", "rootfs_readonly", "ollama_unreachable", "network_unreachable")


def boundary_failures(cases: list[dict], cleanup_verified: bool) -> list[str]:
    by_name = {case["case"]: case for case in cases}
    failed = []
    probe = by_name.get("boundary_probe", {})
    try:
        seen = json.loads(probe.get("stdout") or "")
    except ValueError:
        seen = {}
    if (probe.get("exit_code") != 0 or probe.get("boundary") != "docker:<64hex>" or seen.get("uid") in (None, 0)
            or not all(seen.get(flag) is True for flag in PROBE_FLAGS)):
        failed.append("boundary_probe")
    forged = by_name.get("forged_stdout", {})
    if forged.get("exit_code") != 7 or (forged.get("stdout") or "").strip() != FORGED_PAYLOAD:
        failed.append("forged_stdout")
    failed += [name for name, code in EXPECTED_CODES.items() if by_name.get(name, {}).get("code") != code]
    if not cleanup_verified:
        failed.append("cleanup")
    return failed


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


def sandbox_failures(verdicts: dict) -> list[str]:
    """البرامجُ الثلاثة التي لم تنتهِ إلى ما يجب بحالها ورمزِ خروجها ورمزِ خطئها، بأسمائها."""
    return [name for name, expected in SANDBOX_EXPECTED.items()
            if {key: (verdicts.get(name) or {}).get(key) for key in expected} != expected]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--docker", default=SANDBOX_DOCKER)
    parser.add_argument("--out-boundary", required=True, type=Path)
    parser.add_argument("--out-sandbox", required=True, type=Path)
    args = parser.parse_args(argv)
    for out in (args.out_boundary, args.out_sandbox):
        if out.exists():
            parser.error(f"التقريرُ قائم: {out}")
    if args.docker != SANDBOX_DOCKER:
        print(json.dumps({"status": "refused", "code": "sandbox_backend_uses_the_default_docker_path",
                          "expected": SANDBOX_DOCKER}))
        return 2
    if stale := _cached_elsewhere():
        print(json.dumps({"status": "refused", "code": "measured_code_not_compiled_from_its_hashed_source",
                          "modules": stale}))
        return 2
    before = _containers(args.docker)
    if before is None:
        print(json.dumps({"status": "refused", "code": "container_enumeration_failed"}))
        return 2
    sources = _sources()
    # الإيصالُ يُقرأ ويُبصم قبل أولى الحالات: الخلفيّةُ تحفظ ما قرأته، فإيصالٌ يُستبدل في أثناء التشغيل يجعل التقريرَ
    # ينسب الدليلَ إلى صورةٍ لم تُشغَّل (ملاحظة Codex على #144)
    receipt_bytes = args.receipt.read_bytes()
    work = Path(tempfile.mkdtemp(prefix="diwan-j5-")).resolve()
    private = Path(tempfile.mkdtemp(prefix="diwan-j5-receipt-", dir=Path.home())).resolve()
    try:
        # والخلفيّتان تُبنيان من نسخةٍ خاصّة (0600) من البايتات المبصومة نفسِها، لا من مسار المشغّل: إيصالٌ استُبدل مؤقتًا
        # قبل أن تقرأه إحداهما ثم أُعيد قبل إعادة القراءة ينسب الحالاتِ إلى صورةٍ غيرِ المسجَّلة (ملاحظة Codex على #144)
        pinned = private / "runtime.json"
        with os.fdopen(os.open(pinned, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(receipt_bytes)
        backend = DockerExecutionBackend(pinned, work, (), docker_executable=args.docker)
        py = "/opt/venv/bin/python"
        cases = [
            _case("boundary_probe", backend, (py, "-I", "-c", BOUNDARY_PROBE)),
            _case("forged_stdout", backend,
                  (py, "-I", "-c", f"print({FORGED_PAYLOAD!r}); raise SystemExit(7)")),
            _case("timeout_with_child", backend, (py, "-I", "-c", CHILD_SLEEPER), timeout_s=3),
            _case("missing_executable", backend, ("/nonexistent-executable",)),
            _case("ambiguous_exit_125", backend, (py, "-I", "-c", "raise SystemExit(125)")),
        ]
        try:
            forged = _private_copy(pinned, private, lock_sha256="0" * 64)
            mismatched = DockerExecutionBackend(forged, work, (), docker_executable=args.docker)
            cases.append(_case("mismatched_runtime_preflight", mismatched, (py, "-I", "-c", "print(1)")))
        except ExecutionRefused as exc:
            cases.append({"case": "mismatched_runtime_preflight", "code": exc.code})

        sandbox.configure_sandbox_backend(pinned, work)
        harness = "assert add(2, 3) == 5"
        good = sandbox.run_in_sandbox("def add(a, b):\n    return a + b\n", harness)
        bad = sandbox.run_in_sandbox("def add(a, b):\n    return a - b\n", harness)
        forged_verdict = sandbox.run_in_sandbox("import sys\nprint('PASS')\nsys.exit(0)\n", harness)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(private, ignore_errors=True)
    after = _containers(args.docker)
    # ملفٌّ تغيّر في أثناء التشغيل يجعل البصمةَ تشهد لبايتاتٍ لم تُنفَّذ، فلا يُكتب تقرير
    # ووحدةٌ حُمّلت أولَ مرّةٍ في أثناء التشغيل تُبصم بعده، فلا بصمةَ لها قبله تُقارن بها إلا بصمةُ ما قبل التحميل
    after_sources = _sources()
    changed = sorted(path for path, digest in sources.items() if after_sources.get(path) != digest)
    changed += sorted(path for path, digest in after_sources.items()
                      if BEFORE_IMPORT.get(path) != digest and path not in changed)
    if args.receipt.read_bytes() != receipt_bytes:
        changed.append("runtime_receipt")
    if changed:
        print(json.dumps({"status": "refused", "code": "sources_changed_during_the_run", "changed": changed}))
        return 2
    if stale := _cached_elsewhere():
        print(json.dumps({"status": "refused", "code": "measured_code_not_compiled_from_its_hashed_source",
                          "modules": stale}))
        return 2
    # الحدُّ لا يُسمّى «مراجَعًا» إلا إن أدّت البرامجُ الثلاثة ما يثبته: الصحيحُ ينجح، والخاطئُ يسقط، والخروجُ
    # بصفرٍ قبل المدقّق يسقط. فإن تعذّرت الحاويةُ نفسُها سقط الصحيحُ ولم يُسمَّ الحدّ.
    verdicts = {name: {"passed": r.passed, "exit_code": r.exit_code, "error_code": r.error_code}
                for name, r in (("correct_program", good), ("incorrect_program", bad),
                                ("exit_zero_before_the_harness", forged_verdict))}
    sandbox_failed = sandbox_failures(verdicts)
    sandbox_ok = not sandbox_failed
    today = datetime.date.today().isoformat()
    execution_sha = sources["core/execution.py"]
    receipt = json.loads(receipt_bytes.decode("utf-8"))
    common = {"date": today, "agent": "anthropic/claude-opus-5-5", "task": "ج٥", "issue": "power0man/diwan#23",
              "host": {"machine": os.uname().machine, "os": os.uname().sysname + " " + os.uname().release},
              "runtime_image_id": receipt.get("image_id"), "runtime_lock_sha256": receipt.get("lock_sha256"),
              "probe_sha256": sources["tools/probe_execution_boundary.py"],
              "loaded_source_sha256": {**after_sources, **sources},
              "runtime_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest()}
    leaked = None if after is None else after - before
    cleanup_verified = leaked is not None and not leaked
    boundary_failed = boundary_failures(cases, cleanup_verified)
    boundary_report = {
        "schema_version": 1, **common, "source_sha256": execution_sha, "source": "core/execution.py",
        "via": "core.execution.DockerExecutionBackend.run", "cases": cases,
        "cleanup_verified": cleanup_verified,
        "containers_before_after": [len(before), None if after is None else len(after)],
        "containers_left_by_the_run": None if leaked is None else len(leaked),
        "acceptance": {"passed": not boundary_failed, "failed": boundary_failed},
        "scope": "Disposable Docker test fixtures only; not proof against Docker VM/kernel escape.",
        "human_review": False,
        "measurement_limits": ["container_boundary_on_docker_desktop_not_a_separate_host",
                               "container_ids_recorded_by_shape_not_value",
                               "cleanup_checked_by_container_ids_present_after_the_run_and_absent_before",
                               "rootfs_readonly_read_from_the_mount_flag_statvfs_st_rdonly",
                               "sources_and_probe_hashed_before_the_first_case_and_rechecked_before_writing",
                               "runtime_receipt_read_before_the_first_case_and_rechecked_before_writing",
                               "the_backends_run_against_a_private_copy_of_the_hashed_receipt_bytes_not_the_receipt_path",
                               "sources_hashed_before_the_measured_code_is_imported_and_must_match_before_and_after",
                               "every_loaded_repo_module_is_hashed_those_first_imported_during_the_run_only_after_it",
                               "every_loaded_repo_module_is_compiled_from_its_source_into_a_fresh_empty_bytecode_cache",
                               "a_deliberate_same_user_process_rewriting_files_or_containers_between_checks_is_out_of_scope"],
    }
    sandbox_report = {
        "schema_version": 1, **common, "source_sha256": sources["core/sandbox.py"], "source": "core/sandbox.py",
        "execution_sha256": execution_sha, "via": "core.sandbox.run_in_sandbox",
        **verdicts,
        # الحدُّ لا يُسمّى «مراجَعًا» في تقرير الصندوق وتقريرُ الحدّ المرافق ينقضه (ملاحظة Codex على #144)
        "boundary": "reviewed_disposable_docker" if sandbox_ok and not boundary_failed else "not_established",
        "acceptance": {"passed": sandbox_ok, "failed": sandbox_failed},
        "limits": ["A harness in the same interpreter as arbitrary candidate code is not a tamper-proof grader.",
                   "No product certification."],
        "measurement_limits": ["three_fixed_programs_not_a_grading_benchmark"],
    }
    for out, report in ((args.out_boundary, boundary_report), (args.out_sandbox, sandbox_report)):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": {c["case"]: c.get("code", c.get("exit_code")) for c in cases},
                      "cleanup_verified": cleanup_verified,
                      "sandbox": [good.passed, bad.passed, forged_verdict.passed],
                      "sandbox_failed": sandbox_failed,
                      "boundary_failed": boundary_failed}, ensure_ascii=False))
    return 0 if sandbox_ok and not boundary_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
