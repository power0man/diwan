"""قياسُ المهامّ الوكيلة: يُحكَم على **حالة العالَم** لا على نصِّ الجواب.

الفرقُ الحاكم بين هذا المُشغِّل و`evaluation/capabilities.py`: ذاك يقيس
نصًّا يُنتجه النموذج ويفحصه بمطابقة؛ وهذا يُنشئ مساحةَ عملٍ، ويطلق الحلقةَ
فيها، ثم **يفحص ما صار عليه العالَم بعد أن فرغت**. فالمهمّةُ تنجح إن مرّ
الاختبار، لا إن قال النموذج إنه أصلح العيب.

**أربعُ خصائصَ بنيويةٍ لا تُترك لحُسن النيّة:**

١. **مساحةٌ طازجةٌ لكل مهمّة**: تُبنى من `workspace` في المهمّة داخل مجلّدٍ
   مؤقّت يُمحى بعدها. فلا تسرّب بين مهمّتين، ولا اعتمادٌ على حالة الجهاز.

٢. **الممنوعُ يُفرَض من دفتر الرجوع لا من النثر**: أشهرُ طريقةٍ لتزييف
   قياسٍ وكيليّ أن يُعدِّل الوكيلُ **الاختبارَ** بدل الشيفرة فيمرّ. فكلُّ
   كتابةٍ مُقيَّدةٌ في `agent/journal.py` بمسارها، فتُقرأ القيودُ بعد
   التشغيل: مسٌّ لمسارٍ ممنوع = `forbidden_path_touched`، **ولو مرّ
   الاختبار**. سلوكٌ يُقاس لا شرطٌ يُوصف.

٣. **أمرُ النجاح يُشغَّل في حاويةٍ زائلة لا على الجهاز (ج٣، ق٤٤)**: معيارُ
   النجاح يُشغّل أمرًا على شيفرةٍ **كتبها النموذج**، فهو تنفيذُ كودٍ غير موثوق.
   الطريقُ الأوّل `DockerSuccessExecutor`: لكلّ مهمّةٍ خُلفيّةُ
   `core.execution.DockerExecutionBackend` على جذرها المؤقّت، تُرسَل إليها
   لقطةُ المساحة **بعد** عمل الوكيل (`workspace_snapshot`) ويُنفَّذ الأمرُ في
   `/workspace` داخل الحاوية، ويعود رمزُ الخروج من محرّك Docker لا من مخرَج
   المرشّح. وهويةُ الحاوية تُسجَّل في نتيجة كلّ مهمّة (`success_boundary`).

   والطريقُ القديم `HostSuccessExecutor` (`subprocess` على المضيف) يبقى
   لبيئةٍ زائلةٍ لا Docker فيها (CI، والجلسة السحابية)، ولا يُقبل إلا بإقرار
   المشغِّل في `DIWAN_DISPOSABLE_HOST` — **إقرارٌ لا حدٌّ ينشئه المُشغِّل**،
   ويُسجَّل في الإعداد وفي حدود التقرير أن الأمر جرى على المضيف. ولا يُستعمل
   هنا `core.sandbox.declared_host()` بوابةً: فهو يعني «خُلفيّةُ الصندوق
   مضبوطة» ولا يقول شيئًا عن هذا المُشغِّل.

٤. **«تعذّر» ليس «أخفق» (ق٢٥)**: انقطاعُ مزوّدٍ أو انتهاءُ مهلةٍ أو عطبُ
   بنيةٍ يُعلَن `status: "error"` برمزه، ولا يُحسب فشلَ قدرة. والمقامُ
   يفصل بينهما، فلا يُبنى معدّلٌ على خلطهما.

**الحدودُ المعلنة**: هذا المُشغِّلُ يقيس **الحلقةَ والنموذجَ معًا**، لا
النموذجَ وحده. ودقّتُه لا تزيد على دقّة معيار النجاح الذي كُتب للمهمّة:
معيارٌ رخوٌ يُنتج نجاحًا رخوًا.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from agent.actions import ActionStore
from agent.journal import Journal
from agent.loop import run_agent
from agent.registry import ToolContext, ToolRegistry
from core.budget import Budget
from core.canonical import PayloadRejected, digest
from core.execution import DockerExecutionBackend, ExecutionRefused, ExecutionResult
from core.ledger import Ledger
from core.sandbox import DISPOSABLE_HOST_ENV, sandbox_configuration

RUNNER_VERSION = 3   # ٣: أمرُ النجاح في خُلفيّة Docker؛ ٢: حارسُ ملفات الحكم، وتمييزُ غياب المُشغِّل عن الرسوب
SUCCESS_KINDS = ("tests_pass", "file_equals", "file_contains", "command_exit_zero")
_ROOT_FIELDS = {"schema_version", "suite_id", "kind", "description", "tasks"}
_TASK_FIELDS = {"task_id", "capability", "workspace", "instruction", "success",
                "forbidden", "rubric", "max_steps"}
MAX_TASKS = 100
MAX_WORKSPACE_BYTES = 262_144
COMMAND_TIMEOUT_S = 120


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


def _text(value, path, *, limit=20_000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _reject(path, "text_required", "نصٌّ غير فارغ ضمن الحدّ مطلوب")


def _relative_inside(value, path):
    """مسارٌ نسبيّ بلا عبورٍ ولا مطلقٍ ولا مكوّنٍ فارغ."""
    if not isinstance(value, str) or not value:
        _reject(path, "path_invalid", "مسارٌ نسبيٌّ نصّيّ مطلوب")
    parts = value.split("/")
    if (value.startswith("/") or "\\" in value or "\x00" in value
            or any(p in ("", ".", "..") for p in parts)):
        _reject(path, "path_invalid", "لا مسارَ مطلقٍ ولا عبورَ ولا مكوّنَ فارغ")
    return value


def validate_agentic_suite(suite: dict) -> dict:
    """عقدٌ مغلق: حقلٌ زائدٌ أو ناقصٌ يُرَدّ الملفُّ كلُّه."""
    if not isinstance(suite, dict) or suite.keys() != _ROOT_FIELDS:
        _reject("suite", "schema_fields", f"حقولُ الجذر {sorted(_ROOT_FIELDS)} فقط")
    if suite["schema_version"] != 1:
        _reject("suite.schema_version", "schema_version_invalid", "النسخة 1 مطلوبة")
    if suite["kind"] != "agentic_tasks":
        _reject("suite.kind", "kind_invalid", "kind = agentic_tasks مطلوب")
    _text(suite["suite_id"], "suite.suite_id", limit=64)
    _text(suite["description"], "suite.description")
    tasks = suite["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        _reject("suite.tasks", "tasks_invalid", f"من ١ إلى {MAX_TASKS} مهمّة")
    seen = set()
    for index, task in enumerate(tasks):
        path = f"suite.tasks[{index}]"
        if not isinstance(task, dict) or task.keys() != _TASK_FIELDS:
            _reject(path, "schema_fields", f"حقولُ المهمّة {sorted(_TASK_FIELDS)} فقط")
        _text(task["task_id"], path + ".task_id", limit=64)
        if task["task_id"] in seen:
            _reject(path + ".task_id", "task_id_duplicate", "معرّفُ مهمّةٍ مكرَّر")
        seen.add(task["task_id"])
        _text(task["capability"], path + ".capability", limit=128)
        _text(task["instruction"], path + ".instruction")
        workspace = task["workspace"]
        if not isinstance(workspace, dict) or not workspace:
            _reject(path + ".workspace", "workspace_invalid", "مساحةٌ غيرُ فارغة مطلوبة")
        total = 0
        for name, content in workspace.items():
            _relative_inside(name, path + ".workspace")
            if not isinstance(content, str):
                _reject(path + ".workspace", "workspace_invalid", "محتوًى نصّيّ مطلوب")
            total += len(content.encode("utf-8"))
        if total > MAX_WORKSPACE_BYTES:
            _reject(path + ".workspace", "workspace_too_large",
                    f"فوق {MAX_WORKSPACE_BYTES} بايت")
        forbidden = task["forbidden"]
        if not isinstance(forbidden, list):
            _reject(path + ".forbidden", "forbidden_invalid", "قائمةٌ مطلوبة ولو فارغة")
        for item in forbidden:
            _relative_inside(item, path + ".forbidden")
            # قاعدةُ منعٍ لا يمكن أن تُخالَف = ادّعاءُ حراسةٍ بلا حراسة.
            # الحظرُ يُطابَق بادئةَ مسار، فنثرٌ مثل «حذف الاختبار» يجتاز
            # فحصَ المسار ثم لا يُطابق شيئًا أبدًا: يبدو مُنفَّذًا وليس.
            # فيُشترط أن يكون المنعُ مسارًا مُعلَنًا أو مجلَّدَه.
            prefix = item.rstrip("/") + "/"
            if not any(name == item or name.startswith(prefix)
                       for name in workspace):
                _reject(path + ".forbidden", "forbidden_rule_unmatchable",
                        f"«{item}» لا يطابق مسارًا في workspace فلا يُخالَف")
        rubric = task["rubric"]
        if not isinstance(rubric, list) or not rubric:
            _reject(path + ".rubric", "rubric_invalid", "معاييرُ غيرُ فارغة مطلوبة")
        for item in rubric:
            _text(item, path + ".rubric", limit=2000)
        steps = task["max_steps"]
        if type(steps) is not int or not 1 <= steps <= 32:
            _reject(path + ".max_steps", "max_steps_invalid", "سقفٌ بين ١ و٣٢")
        _validate_success(task["success"], path + ".success")
    return suite


def qualified_task_id(suite_id: str, task_id: str) -> str:
    """معرّفُ المهمّة في البنك كلِّه (ك٤٢).

    `task_id` فريدٌ داخل الحزمة وحدها: في بنك Kimi v1 ستّةٌ وعشرون معرّفًا
    مشتركًا بين `kimi_agentic_001` و`kimi_agentic_002` بمحتوًى مختلف. فالمعرّفُ
    الذي يُقرأ خارج تقرير حزمته هو `suite_id/task_id`، وتفرّدُه يقوم على تفرّد
    `suite_id` الذي يفرضه `validate_agentic_bank`.
    """
    return f"{suite_id}/{task_id}"


def validate_agentic_bank(suites) -> list[str]:
    """يفحص حزمَ بنكٍ معًا، ويرفض `suite_id` مكرَّرًا، ويعيد المعرّفاتِ المؤهَّلة."""
    seen_suites: set[str] = set()
    qualified: list[str] = []
    for index, suite in enumerate(suites):
        validate_agentic_suite(suite)
        if suite["suite_id"] in seen_suites:
            _reject(f"bank[{index}].suite_id", "suite_id_duplicate",
                    f"حزمتان بالمعرّف نفسِه: {suite['suite_id']!r}")
        seen_suites.add(suite["suite_id"])
        qualified.extend(qualified_task_id(suite["suite_id"], task["task_id"])
                         for task in suite["tasks"])
    return qualified


def _validate_success(success, path):
    if not isinstance(success, dict) or "kind" not in success:
        _reject(path, "success_invalid", "معيارُ نجاحٍ بنوعه مطلوب")
    kind = success["kind"]
    if kind not in SUCCESS_KINDS:
        _reject(path, "success_kind_invalid", f"نوعٌ من {SUCCESS_KINDS}")
    if kind in ("tests_pass", "command_exit_zero"):
        if success.keys() != {"kind", "command"}:
            _reject(path, "schema_fields", "kind وcommand فقط")
        command = success["command"]
        if (not isinstance(command, list) or not command
                or not all(isinstance(a, str) and a for a in command)
                or len(command) > 32):
            _reject(path + ".command", "command_invalid", "قائمةٌ نصّيةٌ غيرُ فارغة")
    else:
        if success.keys() != {"kind", "path", "value"}:
            _reject(path, "schema_fields", "kind وpath وvalue فقط")
        _relative_inside(success["path"], path + ".path")
        if not isinstance(success["value"], str):
            _reject(path + ".value", "value_invalid", "قيمةٌ نصّية مطلوبة")


# ————— بناءُ المساحة والحكمُ على العالَم —————

def materialize(task: dict, root: Path) -> None:
    for name, content in sorted(task["workspace"].items()):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# ————— أين يُشغَّل أمرُ النجاح: في حاويةٍ زائلة لا على الجهاز (ج٣) —————

def workspace_snapshot(root: Path) -> tuple[str, ...]:
    """ملفاتُ المساحة بعد عمل الوكيل كما تُرسَل إلى الحاوية: العاديُّ الظاهرُ وحده.

    يُستثنى دفترُ الرجوع وكلُّ مكوّنٍ مخفيّ ومجلّداتُ المخلَّفات والروابط، فلا
    يبلغ الحاويةَ إلا ما يراه المعيار. والحاويةُ تعيد بناءَ الشجرة من اللقطة
    وتعمل في نسختها، فلا يمسّ الأمرُ مساحةَ المضيف ولا يقرأ منها.
    """
    names = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part.startswith(".") or part in _IGNORED_DIRS for part in relative.split("/")):
            continue
        if path.is_file() and not path.is_symlink():
            names.append(relative)
    return tuple(names)


class DockerSuccessExecutor:
    """يُشغّل أمرَ النجاح داخل حاويةٍ زائلة على لقطةٍ من المساحة بعد عمل الوكيل.

    الإيصالُ يُقرأ ويُتحقَّق منه عند الإنشاء (`core.execution.DockerExecutionBackend`)
    فلا يبدأ بنكٌ بإيصالٍ معطوب. وكلُّ مهمّةٍ تنشئ خُلفيّتَها على جذرها المؤقّت
    وحده، ولا يُرسَل من الجذر إلا ما يختاره `workspace_snapshot`. ورمزُ الخروج
    يأتي من محرّك Docker، والحدُّ حدُّ حاوية لا جهازٍ منفصل.
    """
    name = "docker"

    def __init__(self, receipt_path, *, docker_executable: str | None = None):
        self.receipt_path = Path(receipt_path).resolve()
        self.docker_executable = docker_executable
        probe = Path(tempfile.mkdtemp(prefix="diwan-agentic-receipt-")).resolve()
        try:
            self.receipt = dict(self._backend(probe).receipt)
        finally:
            shutil.rmtree(probe, ignore_errors=True)

    def _backend(self, root: Path) -> DockerExecutionBackend:
        options = {} if self.docker_executable is None else {"docker_executable": self.docker_executable}
        return DockerExecutionBackend(self.receipt_path, root, (),
                                      snapshot_selector=workspace_snapshot, **options)

    def __call__(self, command: list[str], root: Path) -> ExecutionResult:
        return self._backend(root).run(tuple(command), timeout_s=float(COMMAND_TIMEOUT_S))

    def configuration(self) -> dict:
        return {"success_executor": self.name, "execution_receipt": dict(self.receipt)}


class HostSuccessExecutor:
    """الطريقُ القديم: `subprocess` على المضيف نفسِه، ولا يُقبل إلا بإقرار المشغِّل (ق٤٤).

    يبقى لبيئةٍ زائلةٍ بلا Docker، ويُسجَّل في الإعداد وفي حدود التقرير أن الأمر
    جرى على المضيف لا في الحاوية. والإقرارُ إقرارُ مشغِّلٍ لا حدٌّ متحقَّقٌ منه.
    """
    name = "attested_host"

    def __init__(self, host: str | None):
        self.host = host

    def __call__(self, command: list[str], root: Path) -> ExecutionResult:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(command, cwd=str(root), env=env,
                                  capture_output=True, text=True,
                                  timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise ExecutionRefused("execution_timeout", "انقضت مهلة أمر النجاح على المضيف") from exc
        except OSError as exc:
            raise ExecutionRefused("execution_runtime_unavailable", str(exc)[:200]) from exc
        return ExecutionResult(proc.returncode, proc.stdout or "", proc.stderr or "",
                               "host:" + (self.host or "unattested"))

    def configuration(self) -> dict:
        return {"success_executor": self.name, "execution_receipt": None}


def evaluate_success(task: dict, root: Path, *, executor=None) -> dict:
    """يُحكَم على حالة العالَم. وتعذُّرُ الحكم يُعلَن ولا يُقرأ نجاحًا.

    `executor` هو من يُشغّل أمرَ النجاح (`DockerSuccessExecutor` أو
    `HostSuccessExecutor`)، ورفضُه (`ExecutionRefused`) تعذُّرُ حكمٍ لا رسوب —
    إلا المهلة فهي رسوبٌ باسمها. ولا طريقَ ثالث: لا يُعاد الأمرُ على المضيف
    إن رُفض في الحاوية.
    """
    success = task["success"]
    kind = success["kind"]
    if kind in ("tests_pass", "command_exit_zero"):
        if executor is None:
            executor = HostSuccessExecutor(attested_disposable_host())
        try:
            result = executor(list(success["command"]), root)
        except ExecutionRefused as exc:
            if exc.code == "execution_timeout":
                return {"passed": False, "code": "success_command_timeout",
                        "detail": exc.reason[:200], "boundary": None}
            return {"passed": False, "code": "success_command_unavailable",
                    "detail": f"{exc.code}: {exc.reason}"[:200], "boundary": None}
        if result.exit_code != 0 and _runner_unavailable(success["command"], result.exit_code,
                                                          result.stderr):
            return {"passed": False, "code": "success_command_unavailable",
                    "exit_code": result.exit_code,
                    "detail": result.stderr[-1500:], "boundary": result.boundary}
        return {"passed": result.exit_code == 0,
                "code": None if result.exit_code == 0 else "command_nonzero",
                "exit_code": result.exit_code,
                "detail": (result.stdout[-1500:] + result.stderr[-1500:]),
                "boundary": result.boundary}
    target = root / success["path"]
    if not target.is_file():
        return {"passed": False, "code": "expected_file_missing", "detail": success["path"]}
    try:
        actual = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"passed": False, "code": "expected_file_unreadable", "detail": str(exc)[:200]}
    if kind == "file_equals":
        return {"passed": actual == success["value"], "code": None, "detail": ""}
    return {"passed": success["value"] in actual, "code": None, "detail": ""}


def _runner_unavailable(command: list[str], returncode: int, stderr: str) -> bool:
    """غيابُ مُشغِّل الاختبارات نفسِه تعذُّرُ حكمٍ لا رسوبُ نموذج.

    قِيس في ٢٥ سبتمبر ٢٠٢٦ على حاويةٍ بلا pytest: ٥٣ من ٥٣ مهمّةِ `tests_pass`
    «تسقط» بـ`No module named pytest`، فبدا البنكُ مفحوصًا (تسقط قبل الحلّ) وهو
    لم يُشغَّل. فيُقصر التمييزُ على المُشغِّل الذي يسمّيه الأمرُ نفسُه: أما
    `ModuleNotFoundError` في شيفرةٍ كتبها النموذج فرسوبُه هو.
    """
    if returncode == 127:               # الأمرُ غيرُ موجود
        return True
    named = {token.rsplit("/", 1)[-1] for token in command}
    return any(runner in named and f"No module named {runner}" in stderr
               for runner in ("pytest", "unittest"))


# ————— ملفاتُ الحكم: لا يُحكَم بما كتبه المحكومُ عليه —————

HARNESS_FILENAMES = frozenset({"conftest.py", "pytest.ini", "tox.ini", "setup.cfg",
                               "pyproject.toml", "sitecustomize.py", "usercustomize.py"})
HARNESS_SUFFIXES = (".pth",)
_IGNORED_DIRS = {".diwan-journal", "__pycache__", ".pytest_cache"}


def _is_harness_name(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return name in HARNESS_FILENAMES or name.endswith(HARNESS_SUFFIXES)


def protected_paths(task: dict) -> frozenset[str]:
    """ملفاتُ المساحة الابتدائية التي يقرؤها معيارُ النجاح: أداةُ الحكم لا موضوعُه.

    ما يسمّيه أمرُ النجاح — ملفًّا أو مجلّدًا — وكان موجودًا قبل أن يعمل الوكيل،
    يُحرَس. وما لم يكن موجودًا فهو المطلوبُ كتابتُه، فلا يُحرَس: مهمّةٌ أمرُها
    `python main.py` وليس في مساحتها main.py تطلب كتابتَه لا حراستَه.
    """
    success = task["success"]
    if success["kind"] not in ("tests_pass", "command_exit_zero"):
        return frozenset()
    initial = set(task["workspace"])
    protected: set[str] = set()
    for token in success["command"]:
        candidate = (token[2:] if token.startswith("./") else token).rstrip("/")
        if candidate in initial:
            protected.add(candidate)
        prefix = candidate + "/"
        protected.update(name for name in initial if name.startswith(prefix))
    return frozenset(protected)


def harness_tampering(task: dict, root: Path) -> list[str]:
    """ما مُسَّ من ملفات الحكم — بمقارنة المحتوى على القرص بعد التشغيل، لا بالدفتر.

    `forbidden_touches` يقرأ دفترَ الرجوع، والدفترُ لا يرى إلا ما كُتب بأداةٍ
    مُسجِّلة. أما `conftest.py` فيه `session.exitstatus = 0` — وهو ما يُمرّر
    ٥٣ من ٥٣ مهمّةِ pytest في البنك بلا إصلاحٍ واحد (قِيس ٢٥ سبتمبر ٢٠٢٦) —
    فلا ينهى عنه `forbidden` لأن القاعدة تشترط مسارًا موجودًا في المساحة.
    فيُحكم على القرص: كلُّ ملفِّ إعدادٍ لمُشغِّل الاختبارات جديدٍ أو متغيّر،
    وكلُّ ملفٍّ يسمّيه أمرُ النجاح كان موجودًا فتغيّر أو غاب.
    """
    initial = {name: hashlib.sha256(content.encode("utf-8")).hexdigest()
               for name, content in task["workspace"].items()}
    protected = protected_paths(task)
    tampered: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if _IGNORED_DIRS.intersection(relative.split("/")[:-1]):
            continue
        if not (_is_harness_name(relative) or relative in protected):
            continue
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            current = None
        if initial.get(relative) != current:
            tampered.append(relative)
    for relative in sorted(protected):
        if not (root / relative).is_file() and relative not in tampered:
            tampered.append(relative)
    return tampered


def forbidden_touches(task: dict, journal: Journal) -> list[str]:
    """يُقرأ من دفتر الرجوع: ما مُسَّ فعلًا، لا ما نُهي عنه نثرًا.

    وهذا هو الحارسُ الذي يمنع أشهرَ تزييفٍ في القياس الوكيليّ: أن يُعدِّل
    الوكيلُ الاختبارَ بدل الشيفرة فيمرّ المعيار.
    """
    rules = task["forbidden"]
    if not rules:
        return []
    touched = []
    for action in journal.actions():
        for rule in rules:
            if action.path == rule or action.path.startswith(rule.rstrip("/") + "/"):
                if action.path not in touched:
                    touched.append(action.path)
    return touched


# ————— تشغيلُ مهمّةٍ وتشغيلُ بنك —————

def run_task(task: dict, provider, registry: ToolRegistry, *, model: str,
             model_version: str, host: str | None, charter=frozenset({"auto", "logged"}),
             deadline_s: float = 120.0, max_output: int = 1024, success_executor=None) -> dict:
    started = time.monotonic_ns()
    # `.resolve()` لا يُستغنى عنه: على ماك المالك يعطي `mkdtemp` مسارًا تحت
    # `/var` وهو رابطٌ رمزيّ إلى `/private/var`، وحارسُ دفتر الرجوع يفتح
    # المكوّنات بلا اتّباع روابط فيردّ الجذرَ بـ`unsafe_path`. فكانت ثمانيةُ
    # اختباراتٍ تسقط على الماك وتنجح على لينكس — عطبُ بيئةٍ يُقرأ عطبَ شيفرة.
    scratch = Path(tempfile.mkdtemp(prefix="diwan-agentic-")).resolve()
    try:
        workspace = scratch / "workspace"
        workspace.mkdir()
        materialize(task, workspace)
        journal = Journal(workspace)
        context = ToolContext(workspace, journal, frozenset(charter),
                              disposable_host=host)
        ledger = Ledger(scratch / "task-ledger.jsonl")
        # دليلُ التحكم خارج مساحة الفعل شرطٌ في الجلسة الوكيلة: الإيصالاتُ
        # لا تسكن المساحةَ التي يكتب فيها الوكيل، وإلّا كتب فوق إيصاله.
        store = ActionStore(scratch / "control", workspace)
        try:
            run = run_agent(task["instruction"], provider, registry, context,
                            ledger=ledger, budget=Budget(0, 0), model=model,
                            model_version=model_version,
                            max_steps=task["max_steps"], max_output=max_output,
                            deadline_s=deadline_s, action_store=store,
                            session_id="agentic", turn_id=task["task_id"])
        except Exception as exc:                 # عطبُ بنيةٍ لا فشلُ قدرة
            return {"task_id": task["task_id"], "capability": task["capability"],
                    "status": "error", "code": "loop_raised",
                    "detail": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "passed": False, "steps": 0, "forbidden_touched": [],
                    "harness_tampered": [], "success_boundary": None,
                    "elapsed_ms": (time.monotonic_ns() - started) // 1_000_000}
        touched = forbidden_touches(task, journal)
        # ملفاتُ الحكم تُفحص قبل تشغيل المعيار: ما كتبه المحكومُ عليه لا يحكم له.
        tampered = harness_tampering(task, workspace)
        outcome = ({"passed": False, "code": "harness_tampered", "detail": ", ".join(tampered)}
                   if tampered else evaluate_success(task, workspace, executor=success_executor))
        # الترتيبُ عقد: مسٌّ لمسارٍ ممنوع يُبطل النجاح ولو مرّ المعيار.
        if touched:
            status, code, passed = "failed", "forbidden_path_touched", False
        elif tampered:
            status, code, passed = "failed", "harness_tampered", False
        elif run.status in ("refused", "failed"):
            status, code, passed = "error", run.code or run.status, False
        elif outcome["passed"]:
            status, code, passed = "passed", None, True
        elif outcome["code"] == "success_command_unavailable":
            # تعذُّرُ الحكم عطبُ بيئةٍ لا فشلُ قدرة: يخرج من المقام لا يُحسب رسوبًا.
            status, code, passed = "error", "success_command_unavailable", False
        else:
            status, code, passed = "failed", outcome["code"] or run.status, False
        return {"task_id": task["task_id"], "capability": task["capability"],
                "status": status, "code": code, "passed": passed,
                "loop_status": run.status, "steps": len(run.steps),
                "forbidden_touched": touched, "harness_tampered": tampered,
                "success_boundary": outcome.get("boundary"),
                "success_detail": outcome.get("detail", "")[:600],
                "answer": run.answer[:1000],
                "elapsed_ms": (time.monotonic_ns() - started) // 1_000_000}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


_HOST_MAX = 64


def attested_disposable_host() -> str | None:
    """إقرارُ المشغِّل بأن **هذه العملية** تجري في حاويةٍ زائلة.

    ليس حدًّا ينشئه هذا الملف، ولا هو `core.sandbox.declared_host()` الذي
    صار يعني «خُلفيّةُ Docker مضبوطة». تُقرأ القيمةُ وتُسجَّل، ولا يُتحقَّق
    منها — فهي تصديقٌ على مسؤولية من يُشغّل.
    """
    raw = os.environ.get(DISPOSABLE_HOST_ENV, "").strip()
    if not raw or len(raw) > _HOST_MAX:
        return None
    return raw if all(0x21 <= ord(c) <= 0x7E for c in raw) else None


def run_agentic_suite(suite: dict, provider, registry: ToolRegistry, *, model: str,
                      model_version: str, execution_receipt=None,
                      docker_executable: str | None = None, **kwargs) -> dict:
    """بإيصال Docker يُشغَّل أمرُ النجاح في الحاوية (ج٣)؛ وبدونه لا يبدأ إلا بإقرار مضيفٍ زائل (ق٤٤)."""
    validate_agentic_suite(suite)
    host = attested_disposable_host()
    if execution_receipt is not None:
        executor = DockerSuccessExecutor(execution_receipt, docker_executable=docker_executable)
    else:
        if host is None:
            _reject("runtime", "disposable_host_not_attested",
                    f"معيارُ النجاح يُشغّل كودًا كتبه النموذج على المضيف؛ يلزمه إيصالُ "
                    f"Docker أو إقرارٌ في {DISPOSABLE_HOST_ENV} [ق٤٤]")
        executor = HostSuccessExecutor(host)
    results = [{**run_task(task, provider, registry, model=model,
                           model_version=model_version, host=host,
                           success_executor=executor, **kwargs),
                "qualified_id": qualified_task_id(suite["suite_id"], task["task_id"])}
               for task in suite["tasks"]]
    config = {"runner_version": RUNNER_VERSION, "suite_id": suite["suite_id"],
              "suite_sha256": hashlib.sha256(
                  json.dumps(suite, ensure_ascii=False, sort_keys=True).encode("utf-8")
              ).hexdigest(),
              "model": model, "model_version": model_version,
              # إقرارُ المشغِّل، لا حدٌّ متحقَّق منه
              "attested_host": host,
              # أين جرى أمرُ النجاح، وبأيّ إيصال
              **executor.configuration(),
              # وهل كانت خُلفيّةُ الصندوق مضبوطةً أصلًا — تمييزًا لا اعتمادًا
              "sandbox_backend": (sandbox_configuration() or {}).get("backend"),
              "tools": [spec.name for spec in registry.specs()]}
    attempted = len(results)
    errors = sum(r["status"] == "error" for r in results)
    measured = attempted - errors
    passed = sum(r["passed"] is True for r in results)
    by_capability: dict[str, dict] = {}
    for result in results:
        entry = by_capability.setdefault(result["capability"],
                                         {"measured": 0, "passed": 0, "errors": 0})
        if result["status"] == "error":
            entry["errors"] += 1
        else:
            entry["measured"] += 1
            entry["passed"] += int(result["passed"])
    return {
        "schema_version": 1, "kind": "agentic_report", "suite_id": suite["suite_id"],
        "config": config, "config_sha256": digest(config),
        "summary": {
            "attempted": attempted, "measured": measured, "errors": errors,
            "passed": passed,
            # المقامُ ما قِيس، لا ما حُوول: خلطُهما يرفع المعدّل بعطبٍ لا بقدرة
            "pass_rate_of_measured": (round(passed / measured, 4) if measured else None),
            "forbidden_violations": sum(1 for r in results if r["forbidden_touched"]),
        },
        "by_capability": by_capability,
        "results": results,
        "measurement_limits": [
            "measures_loop_and_model_together_not_model_alone",
            "accuracy_bounded_by_the_authored_success_criterion",
            "forbidden_paths_enforced_only_for_journalled_writes",
            "forbidden_rules_must_prefix_a_declared_workspace_path",
            "harness_files_and_command_targets_compared_by_digest_after_the_run",
            "runner_absence_is_an_error_only_for_the_runner_the_command_names",
            "single_attempt_per_task_no_variance_estimate",
            *(("success_command_runs_inside_the_docker_backend_on_a_snapshot_of_the_post_run_workspace",
               "docker_backend_is_a_container_boundary_not_a_separate_host",
               "workspace_files_refused_by_the_snapshot_policy_make_the_task_unjudgeable")
              if executor.name == "docker" else
              ("success_command_runs_on_the_host_not_inside_the_docker_backend",
               "attested_host_is_an_operator_claim_not_a_verified_boundary")),
        ],
    }
