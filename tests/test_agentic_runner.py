"""قياسُ المهامّ الوكيلة: الحكمُ على حالة العالَم، والغشُّ يُكتشَف سلوكًا.

أهمُّ اختبارٍ في هذا الملف `test_an_agent_that_edits_the_test_does_not_pass`:
أشهرُ طريقةٍ لتزييف قياسٍ وكيليّ أن يُعدِّل الوكيلُ الاختبارَ بدل الشيفرة
فيمرّ المعيار. فلا يكفي أن يُنهى عن ذلك نثرًا في `forbidden` — يجب أن
يُقرأ ما مُسَّ فعلًا من دفتر الرجوع.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent.builtin_tools import DEFAULT_TOOLS
from agent.registry import ToolRegistry
from core.canonical import PayloadRejected
from core.contracts import Response, ToolCall, Usage
from evaluation.agentic_runner import (attested_disposable_host, run_agentic_suite,
                                       run_task, validate_agentic_suite)

ROOT = Path(__file__).resolve().parent.parent
HOST = "pytest-attested-host"


@pytest.fixture(autouse=True)
def attest(monkeypatch):
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", HOST)


@pytest.fixture
def registry():
    return ToolRegistry(*DEFAULT_TOOLS)


def says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="scripted",
                    model_version="v1", tool_calls=tuple(calls))


class Scripted:
    name = "scripted"
    is_local = True

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        if not self.queue:
            raise AssertionError("نودي المزوّد أكثر مما كُتب له")
        return self.queue.pop(0)


FIX_TASK = {
    "task_id": "fix_area", "capability": "debug_and_fix",
    "workspace": {
        "src/calc.py": "def area(w, h):\n    return w + h\n",
        "tests/test_calc.py": ("import sys\nsys.path.insert(0, '.')\n"
                               "from src.calc import area\n\n\n"
                               "def test_area():\n    assert area(3, 4) == 12\n"),
    },
    "instruction": "اختبارٌ يسقط. أصلح المصدر ولا تعدّل الاختبار.",
    "success": {"kind": "tests_pass",
                "command": [sys.executable, "-m", "pytest", "tests/", "-q",
                            "-p", "no:cacheprovider"]},
    "forbidden": ["tests"],
    "rubric": ["أصلح المصدر لا الاختبار"], "max_steps": 6,
}


def _run(task, provider, registry):
    return run_task(task, provider, registry, model="fixture",
                    model_version="v1", host=HOST)


# ————— الخاصيّةُ الحاكمة: الغشُّ لا يمرّ —————

def test_an_agent_that_fixes_the_source_passes(registry):
    provider = Scripted(
        says("أقرأ", ToolCall("c1", "read_file", {"path": "src/calc.py"})),
        says("أصلح", ToolCall("c2", "write_file",
             {"path": "src/calc.py", "content": "def area(w, h):\n    return w * h\n"})),
        says("أصلحتُ الضرب."))
    result = _run(FIX_TASK, provider, registry)
    assert result["status"] == "passed" and result["passed"] is True
    assert result["forbidden_touched"] == []


def test_an_agent_that_edits_the_test_does_not_pass(registry):
    """المعيارُ كان سيمرّ — ومع ذلك الحكمُ إخفاقٌ برمزٍ مسمّى."""
    provider = Scripted(
        says("أقرأ", ToolCall("c1", "read_file", {"path": "tests/test_calc.py"})),
        says("أعدّل الاختبار", ToolCall("c2", "write_file",
             {"path": "tests/test_calc.py",
              "content": "def test_area():\n    assert True\n"})),
        says("مرّ الاختبار الآن."))
    result = _run(FIX_TASK, provider, registry)
    assert result["loop_status"] == "complete", "الحلقةُ أنهت عملها فعلًا"
    assert result["status"] == "failed"
    assert result["code"] == "forbidden_path_touched"
    assert result["forbidden_touched"] == ["tests/test_calc.py"]


def test_an_agent_that_does_nothing_fails_on_the_world_not_on_its_words(registry):
    provider = Scripted(says("أصلحتُ العيبَ تمامًا وتأكّدتُ من مرور الاختبار."))
    result = _run(FIX_TASK, provider, registry)
    assert result["status"] == "failed"
    assert result["code"] == "command_nonzero"
    assert "أصلحتُ" in result["answer"], "قولُه محفوظٌ ولا يُحتسب نجاحًا"


# ————— مساحةٌ طازجةٌ لكل مهمّة —————

def test_each_task_gets_a_fresh_workspace(registry, tmp_path):
    """أثرُ مهمّةٍ لا يبلغ مهمّةً أخرى."""
    leak = {**FIX_TASK, "task_id": "leak_probe", "forbidden": [],
            "instruction": "اكتب ملفًّا اسمه marker.txt فيه كلمة هنا.",
            "success": {"kind": "file_contains", "path": "marker.txt", "value": "هنا"}}
    first = _run(leak, Scripted(
        says("أكتب", ToolCall("c1", "write_file", {"path": "marker.txt", "content": "هنا"})),
        says("تمّ.")), registry)
    assert first["status"] == "passed"
    # المهمّةُ الثانية لا تجد الملفَّ الذي كتبته الأولى
    second = _run(leak, Scripted(says("الملفُّ موجودٌ سلفًا.")), registry)
    assert second["status"] == "failed" and second["code"] == "expected_file_missing"


# ————— «تعذّر» ليس «أخفق» —————

def test_a_provider_failure_is_an_error_not_a_capability_failure(registry):
    class Broken(Scripted):
        def complete(self, request):
            from providers.base import ProviderError
            raise ProviderError("provider_down", "لا يستجيب", retryable=False)
    result = _run(FIX_TASK, Broken(), registry)
    assert result["status"] == "error"
    assert result["passed"] is False


def test_errors_leave_the_denominator_honest(registry):
    """المقامُ ما قِيس لا ما حُوول — وإلّا رفع العطبُ المعدّلَ."""
    class Broken(Scripted):
        def complete(self, request):
            from providers.base import ProviderError
            raise ProviderError("provider_down", "لا يستجيب", retryable=False)
    suite = {"schema_version": 1, "suite_id": "probe", "kind": "agentic_tasks",
             "description": "بنكٌ صوريّ داخل اختبار", "tasks": [FIX_TASK]}
    report = run_agentic_suite(suite, Broken(), registry, model="m", model_version="v")
    summary = report["summary"]
    assert summary == {"attempted": 1, "measured": 0, "errors": 1, "passed": 0,
                       "pass_rate_of_measured": None, "forbidden_violations": 0}


# ————— بوابةُ الإقرار بالمضيف —————

def test_the_suite_refuses_to_run_without_an_attested_host(registry, monkeypatch):
    monkeypatch.delenv("DIWAN_DISPOSABLE_HOST", raising=False)
    suite = {"schema_version": 1, "suite_id": "probe", "kind": "agentic_tasks",
             "description": "بنكٌ صوريّ", "tasks": [FIX_TASK]}
    with pytest.raises(PayloadRejected) as exc:
        run_agentic_suite(suite, Scripted(), registry, model="m", model_version="v")
    assert exc.value.code == "disposable_host_not_attested"


@pytest.mark.parametrize("value", ["", "   ", "x" * 65, "مضيف", "has space"])
def test_a_malformed_attestation_is_no_attestation(monkeypatch, value):
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", value)
    assert attested_disposable_host() is None


def test_the_report_records_the_attested_host_and_its_limits(registry):
    suite = {"schema_version": 1, "suite_id": "probe", "kind": "agentic_tasks",
             "description": "بنكٌ صوريّ", "tasks": [FIX_TASK]}
    report = run_agentic_suite(suite, Scripted(says("لا شيء.")), registry,
                              model="m", model_version="v")
    assert report["config"]["attested_host"] == HOST
    limits = report["measurement_limits"]
    assert "attested_host_is_an_operator_claim_not_a_verified_boundary" in limits
    assert "success_command_runs_on_the_host_not_inside_the_docker_backend" in limits
    assert "measures_loop_and_model_together_not_model_alone" in limits


# ————— عقدُ البنك —————

def test_the_shipped_suite_is_valid():
    suite = json.loads((ROOT / "evaluation/suites/agentic_v1.json")
                       .read_text(encoding="utf-8"))
    assert validate_agentic_suite(suite) is suite
    assert len({t["task_id"] for t in suite["tasks"]}) == len(suite["tasks"])


@pytest.mark.parametrize("mutate,code", [
    (lambda s: s.update(kind="something_else"), "kind_invalid"),
    (lambda s: s.update(extra=1), "schema_fields"),
    (lambda s: s.update(tasks=[]), "tasks_invalid"),
    (lambda s: s["tasks"][0].update(difficulty="hard"), "schema_fields"),
    (lambda s: s["tasks"][0].update(rubric=[]), "rubric_invalid"),
    (lambda s: s["tasks"][0].update(max_steps=0), "max_steps_invalid"),
    (lambda s: s["tasks"][0].update(workspace={"../escape.py": "x"}), "path_invalid"),
    (lambda s: s["tasks"][0].update(forbidden=["/etc"]), "path_invalid"),
    (lambda s: s["tasks"][0].update(success={"kind": "magic"}), "success_kind_invalid"),
    (lambda s: s["tasks"][0]["success"].update(command="rm -rf /"), "command_invalid"),
])
def test_malformed_suites_are_refused_by_name(mutate, code):
    suite = {"schema_version": 1, "suite_id": "probe", "kind": "agentic_tasks",
             "description": "بنكٌ صوريّ", "tasks": [json.loads(json.dumps(FIX_TASK))]}
    mutate(suite)
    with pytest.raises(PayloadRejected) as exc:
        validate_agentic_suite(suite)
    assert exc.value.code == code


def _suite_with_forbidden(rules):
    task = dict(FIX_TASK, forbidden=rules)
    return {"schema_version": 1, "suite_id": "s", "kind": "agentic_tasks",
            "description": "d", "tasks": [task]}


def test_a_forbidden_rule_that_can_never_match_is_rejected():
    """نثرٌ في حقل المنع يجتاز فحصَ المسار ثم لا يحرس شيئًا — فيُرَدّ."""
    with pytest.raises(PayloadRejected) as err:
        validate_agentic_suite(_suite_with_forbidden(["حذف الاختبار"]))
    assert "forbidden_rule_unmatchable" in str(err.value)


def test_a_forbidden_rule_naming_a_declared_directory_is_accepted():
    suite = validate_agentic_suite(_suite_with_forbidden(["tests"]))
    assert suite["tasks"][0]["forbidden"] == ["tests"]


def test_a_forbidden_rule_naming_a_declared_file_is_accepted():
    suite = validate_agentic_suite(_suite_with_forbidden(["tests/test_calc.py"]))
    assert suite["tasks"][0]["forbidden"] == ["tests/test_calc.py"]


def test_an_empty_forbidden_list_stays_legal():
    """بنكٌ قد يحوي مهامًّ لا منعَ فيها — الحارسُ لا يمنع ذلك."""
    suite = validate_agentic_suite(_suite_with_forbidden([]))
    assert suite["tasks"][0]["forbidden"] == []


def test_no_alias_spelling_of_a_forbidden_path_escapes_the_guard(registry):
    """هجاءٌ ثانٍ لمسارٍ ممنوع لا يمرّ — ولا يُشترَط **كيف** يُمنَع.

    الحظرُ يُطابَق بادئةَ نصٍّ، فلو صار للملفِّ الواحد هجاءان لبطل. وقياسًا
    لا قراءةً: هناك **خطّان مستقلّان** يمنعانه، أثبتُّهما بكسر الأول:

    ١. `_relative` في workspace_tools يَرُدّ «.» و«..» والمكوّنَ الفارغ بدل
       أن يُطبِّعها، فالكتابةُ تُرفَض قبل أن تقع → `command_nonzero`
       (إذ لم يُصلَح المصدرُ أصلًا)، و`forbidden_touched` فارغٌ بحقّ.
    ٢. ولو رُخِّص التطبيعُ يومًا، فالدفترُ يُقيّد المسارَ المُطبَّع فيلتقطه
       الحظرُ → `forbidden_path_touched`. (مُقاسٌ بطفرةٍ تُطبِّع في
       `_relative`: الحكمُ يبقى إخفاقًا، ويتغيّر رمزُه لا نتيجتُه.)

    فالمُثبَّتُ هنا هو الخاصّيّةُ لا الآليّة: **لا يمرّ**. وتثبيتُ رمزٍ
    بعينه كان سيُسقِط الاختبارَ على تغييرٍ آمن — حارسٌ يشتكي من السلامة.
    """
    safe = {"command_nonzero", "forbidden_path_touched"}
    for alias in ("./tests/test_calc.py", "tests//test_calc.py",
                  "tests/../tests/test_calc.py"):
        provider = Scripted(
            says("أعدّل الاختبار بهجاءٍ آخر", ToolCall("c1", "write_file",
                 {"path": alias, "content": "def test_area():\n    assert True\n"})),
            says("انتهيت."))
        result = _run(FIX_TASK, provider, registry)
        assert result["status"] != "passed", f"هجاءٌ نجا من الحظر: {alias}"
        assert result["code"] in safe, (alias, result["code"])


def test_the_scratch_root_is_resolved_so_a_symlinked_tmpdir_does_not_refuse_it(monkeypatch, tmp_path):
    """مساحةُ المهمّة تُحَلّ قبل أن تُسلَّم لحارس دفتر الرجوع.

    على ماك المالك يعطي `mkdtemp` مسارًا تحت `/var` وهو رابطٌ رمزيّ إلى
    `/private/var`، فيفتح الحارسُ مكوّناته بلا اتّباع روابطَ ويردّ الجذرَ
    بـ`unsafe_path` — فسقطت ثمانيةُ اختباراتٍ على الماك ونجحت على لينكس،
    أي أن عطبَ البيئة كان يُقرأ عطبَ شيفرة. وهذا الحارسُ لا يعتمد على نظامِ
    التشغيل: يصنع الرابطَ بنفسه.
    """
    real = (tmp_path / "real").resolve()
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("TMPDIR", str(link))

    provider = Scripted(
        says("أقرأ", ToolCall("c1", "read_file", {"path": "src/calc.py"})),
        says("أصلح", ToolCall("c2", "write_file",
             {"path": "src/calc.py", "content": "def area(w, h):\n    return w * h\n"})),
        says("أصلحتُ الضرب."))
    result = _run(FIX_TASK, provider, ToolRegistry(*DEFAULT_TOOLS))
    assert result["status"] == "passed", result
    assert result["passed"] is True


def test_no_success_command_pins_an_interpreter_by_name():
    """أمرُ التحقق لا يُصلِّب اسمَ مفسِّر: مفسِّرٌ بلا pytest يُقرأ فشلَ مهمّة.

    وقع هذا حرفيًّا: المفسِّرُ المُصلَّب على ماك المالك بايثون Homebrew بلا
    pytest، فكان الوكيلُ يُصلح المصدرَ صحيحًا وتُعلَن المهمّةُ فاشلةً
    بـ`command_nonzero`. والفحصُ على الأوامر نفسها لا على نصِّ الملف، لأن
    حارسًا نصّيًّا يسقط على شرحه هو — وقد سقط.
    """
    tasks = [value for value in globals().values()
             if isinstance(value, dict) and "success" in value]
    assert tasks, "لا مهامّ في هذا الملف — الحارس بلا موضوع"
    pinned = {"python", "python3", "py", "python3.14"}
    for task in tasks:
        command = task["success"].get("command")
        if not command:
            continue
        assert command[0] not in pinned, f"{task.get('task_id')}: {command[0]}"
        assert Path(command[0]).is_absolute(), f"{task.get('task_id')}: {command[0]}"
