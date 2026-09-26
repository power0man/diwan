"""ك٤٤ وك٥١: البنكُ الوكيل الثاني مجمَّدٌ بعتباته، ومدقّقُه يميّز، وحكمُ العتبات يُقرأ من ملفّه الجانبي.

- الملفّان المودَعان هما ما يولّده `tools/make_agentic_bank.py` بايتًا ببايت.
- كلُّ مهمّةٍ تسقط على مساحتها الابتدائية، وتمرّ بحلّها المرجعيّ بلا مسٍّ لملفّات الحكم، وتسقط بحلّها القريبِ الخاطئ.
- إعادةُ كتابة الاختبارات المرئية لا تُنجح مهمّةً: الفحصُ المخفيّ يسقطها، وحارسُ ملفّات الحكم يسمّيها.
- الحلولُ المرجعية عبر الحلقة الوكيلة نفسِها وأدواتها (`auto` و`logged`) تستوفي العتبات: فكلُّ مهمّةٍ تُحلّ بما يملكه الوكيل.
- الحكمُ بالعتبات يسمّي ما لم يُستوفَ، ويرفض تقريرًا على بنكٍ تغيّر.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.contracts import Response, ToolCall, Usage
from evaluation.agentic_bank import BankChanged, attach, judge, suite_sha256
from evaluation.agentic_runner import (HostSuccessExecutor, evaluate_success, harness_tampering, materialize,
                                       protected_paths, validate_agentic_suite, workspace_bytes)

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "evaluation" / "suites" / "agentic_v2.json"
SUITE = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
META = json.loads(SUITE_PATH.with_suffix(".meta.json").read_text(encoding="utf-8"))
TASKS = {t["task_id"]: t for t in SUITE["tasks"]}
WITH_TESTS = sorted(tid for tid, t in TASKS.items() if any(n.startswith("tests/") for n in t["workspace"]))
EXECUTOR = HostSuccessExecutor("pytest-host")


def _local(task: dict) -> dict:
    """في الحاوية python3؛ وهنا مفسّرُ الاختبار نفسُه."""
    task = copy.deepcopy(task)
    if task["success"]["kind"] == "command_exit_zero":
        task["success"]["command"][0] = sys.executable
    return task


def _judge(task: dict, root: Path, overlay: dict | None = None) -> dict:
    materialize(task, root)
    for name, content in (overlay or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(workspace_bytes(content))
    return evaluate_success(_local(task), root, executor=EXECUTOR)


# ————— البنك —————

def test_the_bank_is_valid_and_has_thirty_tasks_in_five_categories():
    validate_agentic_suite(SUITE)
    counts: dict[str, int] = {}
    for task_id, item in META["tasks"].items():
        assert TASKS[task_id]["capability"] == item["category"]
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    assert len(TASKS) == 30 and counts == {"coder_bug_fix": 8, "coder_feature": 7, "coder_refactor": 5,
                                           "coder_explain": 5, "workspace_files": 5}
    assert META["thresholds"] == {
        "pass_rate": 0.6,
        "min_category_pass_rate": {"coder_bug_fix": 0.5, "coder_feature": 0.5, "coder_refactor": 0.4,
                                    "coder_explain": 0.6, "workspace_files": 0.6},
        "forbidden_violations": 0, "harness_tampered": 0}


def test_the_committed_bank_is_exactly_what_the_generator_builds():
    sys.path.insert(0, str(ROOT))
    from tools.make_agentic_bank import THRESHOLDS, build
    tasks, meta = build()
    assert tasks == SUITE["tasks"] and meta == META["tasks"] and THRESHOLDS == META["thresholds"]


def test_the_checker_lives_in_the_command_and_names_what_it_reads():
    for task in SUITE["tasks"]:
        success = task["success"]
        if success["kind"] == "file_equals":
            continue
        command = success["command"]
        assert command[:2] == ["python3", "-c"] and "def visible(d):" in command[2]
        for token in command[3:]:        # ما يسمّيه الأمرُ بعد المدقّق مسارٌ في المساحة يُحرس بالبصمة
            assert any(name == token or name.startswith(token + "/") for name in task["workspace"])
        assert not any(name.endswith(("answer.json", "output.txt")) for name in task["workspace"])


@pytest.mark.parametrize("task_id", WITH_TESTS)
def test_the_visible_tests_are_harness_files_guarded_by_digest(task_id):
    task = TASKS[task_id]
    assert {n for n in task["workspace"] if n.startswith("tests/")} <= protected_paths(task)


# ————— المدقّقُ يميّز —————

@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_each_task_fails_before_passes_with_its_reference_and_fails_with_its_decoy(task_id, tmp_path):
    task = TASKS[task_id]
    assert not _judge(task, tmp_path / "before")["passed"]
    reference = _judge(task, tmp_path / "reference", META["tasks"][task_id]["reference_solution"])
    assert reference["passed"], reference
    assert harness_tampering(task, tmp_path / "reference") == []
    decoy = _judge(task, tmp_path / "decoy", META["tasks"][task_id]["decoy_solution"])
    assert not decoy["passed"], f"{task_id}: الحلُّ الخاطئ ({META['tasks'][task_id]['decoy']}) مرّ"


@pytest.mark.parametrize("task_id", [t for t in WITH_TESTS if TASKS[t]["capability"] != "coder_explain"])
def test_rewriting_the_visible_tests_passes_nothing(task_id, tmp_path):
    task = TASKS[task_id]
    blank = {name: "def test_nothing():\n    pass\n" for name in task["workspace"]
             if name.startswith("tests/test_")}
    assert not _judge(task, tmp_path, blank)["passed"]
    assert sorted(blank) == [p for p in harness_tampering(task, tmp_path) if p in blank]


@pytest.mark.parametrize("task_id", [t for t in WITH_TESTS if TASKS[t]["capability"] != "coder_explain"])
def test_the_visible_tests_pass_under_pytest_with_the_reference(task_id, tmp_path):
    """الوكيلُ يشغّلها بـrun_tests (pytest)، والمدقّقُ يشغّلها بلا pytest: فيتّفقان على الحلّ المرجعيّ."""
    materialize(TASKS[task_id], tmp_path)
    for name, content in META["tasks"][task_id]["reference_solution"].items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(workspace_bytes(content))
    done = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout[-2000:]


# ————— الحلولُ المرجعية عبر الحلقة الوكيلة —————

class ReferencePlayer:
    """يكتب الحلَّ المرجعيّ للمهمّة بأداة write_file في خطوةٍ واحدة، ثم يجيب."""
    name, is_local = "reference-player", True

    def __init__(self, suite, meta):
        self.by_instruction = {t["instruction"]: meta["tasks"][t["task_id"]]["reference_solution"]
                               for t in suite["tasks"]}

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        text = "\n".join(m.content for m in request.messages if m.role == "user")
        (files,) = [files for instruction, files in self.by_instruction.items() if instruction in text]
        if any(m.role == "tool" for m in request.messages):
            return Response("تمّ.", Usage(1, 1), "complete", 0, provider=self.name, model_version="v1")
        calls = tuple(ToolCall(f"call-{i}", "write_file", {"path": name, "content": content})
                      for i, (name, content) in enumerate(sorted(files.items())))
        return Response("", Usage(1, 1), "complete", 0, provider=self.name, model_version="v1", tool_calls=calls)


def test_reference_solutions_through_the_agent_loop_meet_the_thresholds(tmp_path, monkeypatch, capsys):
    import providers.ollama
    sys.path.insert(0, str(ROOT))
    from tools import evaluate_agentic
    suite = {**SUITE, "tasks": [_local(t) for t in SUITE["tasks"]]}
    path = tmp_path / "agentic_v2.json"
    path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    path.with_suffix(".meta.json").write_text(json.dumps(META, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "pytest-host")
    monkeypatch.setattr(providers.ollama, "OllamaProvider", lambda model: ReferencePlayer(suite, META))
    out = tmp_path / "report.json"
    assert evaluate_agentic.main(["--suite", str(path), "--model", "reference", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    failed = {r["task_id"]: r.get("code") for r in report["results"] if not r["passed"]}
    assert failed == {}
    assert report["thresholds"]["meets_thresholds"] is True and report["thresholds"]["unmet"] == []
    assert "العتبات: مستوفاة" in capsys.readouterr().out


# ————— الحكمُ بالعتبات —————

def _report(suite, passed=(), errors=(), forbidden=(), tampered=()):
    results = []
    for task in suite["tasks"]:
        tid = task["task_id"]
        results.append({"task_id": tid, "capability": task["capability"],
                        "status": "error" if tid in errors else "measured",
                        "passed": tid in passed and tid not in errors,
                        "forbidden_touched": ["tests/x.py"] if tid in forbidden else [],
                        "harness_tampered": ["conftest.py"] if tid in tampered else []})
    by_capability: dict[str, dict] = {}
    for r in results:
        entry = by_capability.setdefault(r["capability"], {"measured": 0, "passed": 0, "errors": 0})
        if r["status"] == "error":
            entry["errors"] += 1
        else:
            entry["measured"] += 1
            entry["passed"] += int(r["passed"])
    measured = sum(r["status"] != "error" for r in results)
    passed_count = sum(r["passed"] for r in results)
    return {"suite_id": suite["suite_id"], "config": {"suite_sha256": suite_sha256(suite)},
            "summary": {"measured": measured, "errors": len(results) - measured, "passed": passed_count,
                        "pass_rate_of_measured": round(passed_count / measured, 4) if measured else None,
                        "forbidden_violations": sum(1 for r in results if r["forbidden_touched"])},
            "by_capability": by_capability, "results": results}


ALL = set(TASKS)


def _without(category, keep):
    ids = sorted(t for t in TASKS if TASKS[t]["capability"] == category)
    return ALL - set(ids[keep:])


def test_a_clean_full_pass_meets_the_thresholds():
    verdict = judge(_report(SUITE, passed=ALL), SUITE, META)
    assert verdict["meets_thresholds"] and verdict["unmet"] == [] and verdict["pass_rate"] == 1.0


def test_a_category_exactly_at_its_threshold_meets_it():
    verdict = judge(_report(SUITE, passed=_without("coder_refactor", 2)), SUITE, META)   # ٢ من ٥ = ٠٫٤
    assert verdict["categories"]["coder_refactor"]["rate"] == 0.4 and verdict["meets_thresholds"]


def test_a_weak_category_fails_by_name_while_the_total_passes():
    verdict = judge(_report(SUITE, passed=_without("coder_refactor", 1)), SUITE, META)   # ١ من ٥
    assert verdict["pass_rate"] >= 0.6 and verdict["unmet"] == ["category:coder_refactor"]


def test_a_low_total_fails_by_name():
    passed = {t for t in TASKS if t.endswith(("01", "02", "03"))} | {"ag2_bf04"}   # ١٦ من ٣٠
    verdict = judge(_report(SUITE, passed=passed), SUITE, META)
    assert "pass_rate" in verdict["unmet"]


@pytest.mark.parametrize("kind,code", [("errors", "errors"), ("forbidden", "forbidden_violations"),
                                       ("tampered", "harness_tampered")])
def test_errors_forbidden_touches_and_tampering_each_block_the_thresholds(kind, code):
    verdict = judge(_report(SUITE, passed=ALL, **{kind: {"ag2_bf01"}}), SUITE, META)
    assert code in verdict["unmet"] and not verdict["meets_thresholds"]


def test_a_category_with_nothing_measured_is_not_met():
    ids = {t for t in TASKS if TASKS[t]["capability"] == "coder_explain"}
    verdict = judge(_report(SUITE, passed=ALL, errors=ids), SUITE, META)
    assert verdict["categories"]["coder_explain"]["rate"] is None
    assert "category:coder_explain" in verdict["unmet"]


def test_a_report_on_a_changed_bank_is_refused():
    report = _report(SUITE, passed=ALL)
    changed = copy.deepcopy(SUITE)
    changed["tasks"][0]["rubric"].append("معيارٌ أُضيف بعد القياس")
    with pytest.raises(BankChanged):
        judge(report, changed, META)


def test_a_single_floor_for_every_category_reads_the_analyst_sidecar():
    path = ROOT / "evaluation" / "suites" / "analyst_v1.json"
    suite = json.loads(path.read_text(encoding="utf-8"))
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    ids = sorted(t["task_id"] for t in suite["tasks"])
    verdict = judge(_report(suite, passed=set(ids)), suite, meta)
    assert verdict["meets_thresholds"] and set(verdict["categories"]) == {t["capability"] for t in suite["tasks"]}
    assert all(row["threshold"] == 0.5 for row in verdict["categories"].values())


def test_attach_adds_the_verdict_only_where_a_sidecar_has_thresholds():
    report = _report(SUITE, passed=ALL)
    assert attach(report, SUITE, SUITE_PATH)["thresholds"]["meets_thresholds"]
    v1_path = ROOT / "evaluation" / "suites" / "agentic_v1.json"
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    plain = _report(v1, passed={t["task_id"] for t in v1["tasks"]})
    assert attach(plain, v1, v1_path) is plain


def test_another_banks_sidecar_is_refused():
    analyst = json.loads((ROOT / "evaluation/suites/analyst_v1.meta.json").read_text(encoding="utf-8"))
    with pytest.raises(BankChanged):
        judge(_report(SUITE, passed=ALL), SUITE, analyst)


# ————— فحوصُ البنية في إعادة الهيكلة: كلُّ شرطٍ يُسقط حلًّا قريبًا وحده —————

def test_a_refactor_that_imports_but_keeps_a_duplicate_fails(tmp_path):
    reference = META["tasks"]["ag2_rf01"]["reference_solution"]
    kept = {**reference, "src/invoices.py": ("from src.text import clean\n\n\ndef _clean(text):\n    return clean(text)\n\n\n"
                                             "def invoice_title(raw):\n    return \"فاتورة: \" + clean(raw)\n")}
    assert not _judge(TASKS["ag2_rf01"], tmp_path, kept)["passed"]


def test_a_rate_table_moved_inside_cost_fails(tmp_path):
    local = {"src/shipping.py": (
        "RATES = {\"الوسطى\": 4, \"الغربية\": 5, \"الشرقية\": 5, \"الشمالية\": 7, \"الجنوبية\": 6}\n\n\n"
        "def cost(zone, weight_kg):\n"
        "    table = {\"الوسطى\": 4, \"الغربية\": 5, \"الشرقية\": 5, \"الشمالية\": 7, \"الجنوبية\": 6}\n"
        "    if zone not in table:\n        raise ValueError(f\"منطقةٌ غير معروفة: {zone}\")\n"
        "    return max(15, table[zone] * weight_kg)\n")}
    assert not _judge(TASKS["ag2_rf03"], tmp_path, local)["passed"]
