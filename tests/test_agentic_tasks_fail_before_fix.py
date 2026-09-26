"""الفاحصُ الذي يقول «تسقط قبل الحلّ» يجب أن يَقدِر على قول «تمرّ».

فحصٌ لا يُخفق أبدًا لا يشهد بشيء. فهذه الاختبارات تبني مهمّةً **تمرّ**
على مساحتها الابتدائية — أي تمنح الوكيلَ درجةً على ألّا يفعل شيئًا — وتتأكّد
أن الفاحص يسمّيها ويردّ برمزٍ غير صفر.

والمهامُّ هنا من نوع `file_equals` وحده: لا أمرَ يُنفَّذ، فيجري الاختبار في
أي مكانٍ بلا حاجةٍ إلى عزل. أمّا المهامُّ التي تُشغِّل أمرًا فتُفحص في حاوية
(انظر رأس `tools/check_agentic_fails_before_fix.py`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "check_agentic_fails_before_fix.py"


def _task(task_id: str, *, already_passing: bool) -> dict:
    # المساحةُ الابتدائية تحمل الجوابَ نفسَه حين تكون المهمّة فارغةً من العمل
    answer = "الأحد\n"
    return {
        "task_id": task_id,
        "capability": "read_transform_write",
        "workspace": {"notes.txt": "الاجتماع الأول: الأحد\n",
                      "days.txt": answer if already_passing else "ـ\n"},
        "instruction": "اكتب في days.txt يومَ الاجتماع.",
        "success": {"kind": "file_equals", "path": "days.txt", "value": answer},
        "forbidden": [],
        "rubric": ["استخرج اليوم"],
        "max_steps": 4,
    }


def _suite(*tasks: dict) -> dict:
    return {
        "schema_version": 1,
        "suite_id": "fixture_v1",
        "kind": "agentic_tasks",
        "description": "عيّنةٌ للاختبار وحدها.",
        "tasks": list(tasks),
    }


def _run(tmp_path: Path, suite: dict) -> subprocess.CompletedProcess:
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    return subprocess.run([sys.executable, str(TOOL), str(path)],
                          capture_output=True, text=True)


def test_a_task_that_already_passes_is_named_and_the_tool_refuses(tmp_path):
    done = _run(tmp_path, _suite(_task("t_free", already_passing=True)))
    assert done.returncode != 0, done.stdout
    assert "t_free" in done.stdout
    assert "لا تقيس شيئًا" in done.stdout


def test_a_task_that_fails_before_the_fix_is_accepted(tmp_path):
    done = _run(tmp_path, _suite(_task("t_real", already_passing=False)))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "✓" in done.stdout


def test_one_free_task_among_sound_ones_still_refuses(tmp_path):
    """الحكمُ على الملفّ كلِّه: مهمّةٌ واحدة بلا عملٍ تكفي لردّه."""
    done = _run(tmp_path, _suite(_task("t_real", already_passing=False),
                                 _task("t_free", already_passing=True)))
    assert done.returncode != 0
    assert "t_free" in done.stdout and "t_real" not in done.stdout.split("لا تقيس شيئًا")[-1]


def test_unjudgeable_is_not_read_as_a_sound_failure(tmp_path):
    """أخطرُ التباسٍ هنا: أمرٌ لا يوجد أصلًا يردّ رمزًا غير صفر، فيبدو «سقوطًا».

    ولو قُرئ كذلك لمرّ ملفٌّ كامل بلا فحصٍ حقيقيّ — مثلًا حين ينقص المفسِّر
    من الحاوية، فتُقرأ المهامُّ التسعُ كلُّها «تسقط» وهي لم تُشغَّل قطّ.
    """
    task = _task("t_blind", already_passing=False)
    task["success"] = {"kind": "command_exit_zero",
                       "command": ["diwan-no-such-binary-9f3a"]}
    done = _run(tmp_path, _suite(task))
    assert done.returncode != 0, done.stdout
    assert "تعذَّر الحكم" in done.stdout
    assert "لم يُحكم عليها" in done.stdout
    assert "✓ تسقط" not in done.stdout


# ————— مع --meta: الحلُّ المرجعيّ يمرّ والقريبُ الخاطئ يسقط (ك٤٤) —————

def _run_meta(tmp_path: Path, reference: dict, decoy: dict, flag: str = "--meta") -> subprocess.CompletedProcess:
    suite = _suite(_task("t_real", already_passing=False))
    (tmp_path / "suite.json").write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    meta = {"tasks": {"t_real": {"reference_solution": reference, "decoy_solution": decoy}}}
    (tmp_path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return subprocess.run([sys.executable, str(TOOL), str(tmp_path / "suite.json"), flag, str(tmp_path / "meta.json")],
                          capture_output=True, text=True)


def test_meta_accepts_a_reference_that_passes_and_a_decoy_that_fails(tmp_path):
    done = _run_meta(tmp_path, {"days.txt": "الأحد\n"}, {"days.txt": "الاثنين\n"})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "والمرجعيّ يمرّ، والخاطئ يسقط" in done.stdout


def test_meta_refuses_a_decoy_that_passes(tmp_path):
    done = _run_meta(tmp_path, {"days.txt": "الأحد\n"}, {"days.txt": "الأحد\n"})
    assert done.returncode != 0 and "t_real: الحلُّ الخاطئ يمرّ" in done.stdout


def test_meta_refuses_a_reference_that_fails(tmp_path):
    done = _run_meta(tmp_path, {"days.txt": "السبت\n"}, {"days.txt": "الاثنين\n"})
    assert done.returncode != 0 and "t_real: الحلُّ المرجعيّ لا يمرّ" in done.stdout


def test_meta_refuses_a_reference_that_writes_a_harness_file(tmp_path):
    done = _run_meta(tmp_path, {"days.txt": "الأحد\n", "conftest.py": "x = 1\n"}, {"days.txt": "الاثنين\n"})
    assert done.returncode != 0 and "conftest.py" in done.stdout


def test_an_unknown_flag_is_a_usage_error(tmp_path):
    assert _run_meta(tmp_path, {"days.txt": "الأحد\n"}, {"days.txt": "الاثنين\n"}, flag="--solutions").returncode == 2
