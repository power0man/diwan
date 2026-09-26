"""تنفيذُ التحليل في حاويةٍ زائلة (ج٨): كودٌ ومدخلاتٌ مسمّاة، ومخرجاتٌ معلنة تعود بايتات.

يرث حدَّ `core.execution.DockerExecutionBackend` كما هو، ولا يضيف إليه ثقةً جديدة:
- **الصورة:** تُفحص هويّتُها بالإيصال، ثم يُفحص قفلُها في حاويةٍ مستقلّة قبل أن يُرسل كود.
- **الحاوية:** بلا شبكة، ونظامُ ملفّاتها للقراءة وحدها، ولا ربطَ فيها ولا مقبس، وقدراتُها مُسقطة. وتُفحص
  ضوابطُها قبل البدء، ويُثبت زوالُها بعده.
- **المدخلات:** نسخٌ من ملفّات المساحة التي سمّاها النداء، بقواعد لقطة التنفيذ نفسِها (لا مخفيّ، ولا رابط،
  ولا مسارُ اعتماد).
- **المخرجات:** المعلنةُ وحدها تعود، وكلٌّ منها محدودُ الحجم.

**ما يُقرأ من stdout بياناتٌ لا سلطة:** تقريرُ السائق سطرٌ واحد، يتحقّق منه المضيف كما يتحقّق من مدخلات أداة.
والمرشّحُ يستطيع التأثير فيه، لكنه لا يمنح أكثر ممّا يملكه المرشّح أصلًا: بايتاتِ مخرجاته المعلنة.
ورمزُ خروج الحاوية نفسِها يشهد به محرّكُ Docker وحده، ولا يُقبل تقريرٌ من حاويةٍ لم تخرج بصفر.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import unicodedata

from core.execution import (DockerExecutionBackend, ExecutionRefused, _refuse, _root,
                            _snapshot_files, _snapshot_name)

MAX_CODE_BYTES = 64 * 1024
MAX_INPUTS = 32
MAX_OUTPUTS = 8
# = agent.journal.MAX_BYTES: كلُّ مخرَجٍ يُكتب بدفتر الرجوع، فلا يعود ما لا يُكتب (اختبارٌ يثبت التساوي)
MAX_OUTPUT_FILE_BYTES = 256 * 1024
OUTPUT_SUFFIXES = frozenset({".csv", ".tsv", ".json", ".txt", ".md", ".xlsx", ".png"})
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 300
STDOUT_TAIL = 8000
STDERR_TAIL = 4000
# ثمانيةُ مخرجاتٍ بحدّها بترميز base64 مع ذيلَي المخرجين وهامش
REPORT_LIMIT = 4 * 1024 * 1024
_REPORT_KEYS = frozenset({"exit", "timed_out", "stdout", "stderr", "outputs", "missing", "oversized"})

# يعمل داخل الحاوية المفحوصة وحدها. المرشّحُ عمليةٌ ابنة مخرجاها ملفّان في /tmp، فلا يكتب في
# stdout السائق مباشرة؛ والتقريرُ سطرٌ واحد بعد أن تفرغ.
_ANALYSIS_DRIVER = r'''
import os
try:
    import base64, json, pathlib, resource, shutil, subprocess, sys
    payload = json.loads(sys.stdin.buffer.readline(100 * 1024 * 1024))
    root = pathlib.Path("/workspace")
    for item in payload["files"]:
        rel = pathlib.PurePosixPath(item["path"])
        if rel.is_absolute() or any(p in ("..", ".") for p in rel.parts):
            raise RuntimeError("snapshot_path_invalid")
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(base64.b64decode(item["data"], validate=True))
    pathlib.Path("/tmp/home").mkdir()
    shutil.copytree("/opt/mplconfig", "/tmp/mpl")
    script = pathlib.Path("/tmp/diwan-analysis.py")
    script.write_text(payload["code"], encoding="utf-8")
    env = {"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp/home",
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONHASHSEED": "0", "MPLCONFIGDIR": "/tmp/mpl", "MPLBACKEND": "Agg"}
    limit = payload["output_file_bytes"]
    def bounded():
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    with open("/tmp/diwan-out", "wb") as out, open("/tmp/diwan-err", "wb") as err:
        try:
            done = subprocess.run(["/opt/venv/bin/python", "-I", str(script)], cwd=root,
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env,
                                  timeout=payload["timeout_s"], preexec_fn=bounded)
            code, timed_out = done.returncode, False
        except subprocess.TimeoutExpired:
            code, timed_out = None, True
    def tail(name, size):
        raw = pathlib.Path(name).read_bytes()
        return raw[-size * 4:].decode("utf-8", "replace")[-size:]
    report = {"exit": code if code is None or 0 <= code <= 255 else 255, "timed_out": timed_out,
              "stdout": tail("/tmp/diwan-out", payload["stdout_tail"]),
              "stderr": tail("/tmp/diwan-err", payload["stderr_tail"]),
              "outputs": {}, "missing": [], "oversized": []}
    for rel in payload["outputs"]:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            report["missing"].append(rel)
        elif path.stat().st_size > limit:
            report["oversized"].append(rel)
        else:
            report["outputs"][rel] = base64.b64encode(path.read_bytes()).decode("ascii")
    sys.stdout.write(json.dumps(report, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    os._exit(0)
except BaseException:
    os._exit(125)
'''


@dataclass(frozen=True)
class AnalysisResult:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    outputs: dict           # المسارُ المعلن ← بايتاتُه، بترتيب الإعلان
    missing: tuple
    oversized: tuple
    boundary: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.missing and not self.oversized


def _output_names(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_OUTPUTS:
        _refuse("analysis_outputs_invalid", f"مخرجٌ واحد على الأقل و{MAX_OUTPUTS} على الأكثر")
    names = tuple(_snapshot_name(name) for name in value)
    if any(PurePosixPath(name).suffix.casefold() not in OUTPUT_SUFFIXES for name in names):
        _refuse("analysis_output_type", "امتدادُ المخرج أحدُ: " + "، ".join(sorted(OUTPUT_SUFFIXES)))
    folded = [unicodedata.normalize("NFC", name).casefold() for name in names]
    if len(set(folded)) != len(folded):
        _refuse("analysis_outputs_invalid", "تكرارٌ أو التباسٌ في أسماء المخرجات")
    return names


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _report(stdout: bytes, outputs: tuple[str, ...]) -> dict:
    """سطرٌ واحد بمفاتيحه، ولا مخرجَ غيرُ معلن، ولا مخرجَ بلا حكم."""
    try:
        if stdout.count(b"\n") != 1 or not stdout.endswith(b"\n"):
            raise ValueError("one line")
        report = json.loads(stdout, object_pairs_hook=_pairs)
        if not isinstance(report, dict) or set(report) != _REPORT_KEYS:
            raise ValueError("keys")
        code, timed_out = report["exit"], report["timed_out"]
        if (type(timed_out) is not bool or (code is None) != timed_out
                or (code is not None and (type(code) is not int or not 0 <= code <= 255))):
            raise ValueError("exit")
        if (not isinstance(report["stdout"], str) or len(report["stdout"]) > STDOUT_TAIL
                or not isinstance(report["stderr"], str) or len(report["stderr"]) > STDERR_TAIL):
            raise ValueError("tails")
        produced, missing, oversized = report["outputs"], report["missing"], report["oversized"]
        if (not isinstance(produced, dict) or not isinstance(missing, list) or not isinstance(oversized, list)
                or any(not isinstance(name, str) for name in (*missing, *oversized))):
            raise ValueError("outputs")
        accounted = [*produced, *missing, *oversized]
        if sorted(accounted) != sorted(outputs):
            raise ValueError("undeclared or unaccounted output")
        raw = {}
        for name in outputs:
            if name in produced:
                if not isinstance(produced[name], str):
                    raise ValueError("encoding")
                data = base64.b64decode(produced[name], validate=True)
                if len(data) > MAX_OUTPUT_FILE_BYTES:
                    raise ValueError("oversized output")
                raw[name] = data
        report["outputs"] = raw
        return report
    except (ValueError, TypeError, binascii.Error, UnicodeError, RecursionError):
        _refuse("analysis_report_invalid", "تقريرُ حاوية التحليل غير صالح؛ لا يُكتب شيء")


class AnalysisBackend(DockerExecutionBackend):
    lock_path = "/opt/analysis.lock"

    def __init__(self, receipt_path: Path, workspace_root: Path, *,
                 docker_executable: str = "/usr/local/bin/docker"):
        super().__init__(receipt_path, workspace_root, (), docker_executable=docker_executable)

    def analyze(self, code: str, inputs, outputs, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> AnalysisResult:
        if (not isinstance(code, str) or not code.strip() or "\x00" in code
                or len(code.encode("utf-8", "replace")) > MAX_CODE_BYTES):
            _refuse("analysis_code_invalid", f"كودُ بايثون نصٌّ غير فارغ حتى {MAX_CODE_BYTES} بايت")
        if not isinstance(inputs, (list, tuple)) or len(inputs) > MAX_INPUTS:
            _refuse("analysis_inputs_invalid", f"قائمةُ مدخلاتٍ حتى {MAX_INPUTS} ملفًّا")
        selected = _snapshot_files(tuple(inputs))
        declared = _output_names(outputs)
        if type(timeout_s) not in (int, float) or not 0 < timeout_s <= MAX_TIMEOUT_S:
            _refuse("analysis_timeout_invalid", f"مهلةٌ موجبة حتى {MAX_TIMEOUT_S} ثانية")
        files = self._snapshot(selected=selected)
        self._preflight()
        payload = {"code": code, "files": files, "outputs": list(declared), "timeout_s": timeout_s,
                   "output_file_bytes": MAX_OUTPUT_FILE_BYTES,
                   "stdout_tail": STDOUT_TAIL, "stderr_tail": STDERR_TAIL}
        exit_code, stdout, _, boundary = self._run_container_raw(
            _ANALYSIS_DRIVER, payload, timeout_s=timeout_s + 30, output_limit=REPORT_LIMIT)
        if exit_code != 0:
            _refuse("analysis_outcome_unverified", "لم تخرج حاويةُ التحليل بسلام؛ لا يُكتب شيء")
        report = _report(stdout, declared)
        return AnalysisResult(report["exit"], report["timed_out"], report["stdout"], report["stderr"],
                              report["outputs"], tuple(report["missing"]), tuple(report["oversized"]),
                              boundary)


_BACKENDS: dict[Path, AnalysisBackend] = {}


def configure_analysis_backend(receipt_path: Path, workspace_root: Path, **options) -> AnalysisBackend:
    """إقلاعٌ موثوق وحده؛ لا تُعرض أداةً للنموذج."""
    backend = AnalysisBackend(receipt_path, workspace_root, **options)
    _BACKENDS[backend.root] = backend
    return backend


def release_analysis_backend(workspace_root) -> None:
    _BACKENDS.pop(Path(workspace_root), None)


def analysis_backend(workspace_root) -> AnalysisBackend:
    if not _BACKENDS:
        _refuse("analysis_backend_unavailable", "لم تُضبط صورةُ التحليل؛ لا تحليلَ على المضيف")
    backend = _BACKENDS.get(_root(workspace_root))
    if backend is None:
        _refuse("analysis_backend_unavailable", "لا صورةَ تحليلٍ مضبوطةً لهذه المساحة")
    return backend


__all__ = ["AnalysisBackend", "AnalysisResult", "ExecutionRefused", "analysis_backend",
           "configure_analysis_backend", "release_analysis_backend"]
