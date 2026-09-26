"""كلُّ مهمّةٍ وكيلة تسقط قبل الحلّ — وإلا فهي لا تقيس شيئًا.

مهمّةٌ يمرّ معيارُها على المساحة الابتدائية تمنح الوكيلَ درجةً على لا شيء:
يكفيه ألّا يفعل. وهذه أخطرُ من مهمّةٍ صعبة، لأنها ترفع الرقمَ بلا عمل، ولا
يظهر خللُها في أي تشغيلة — فالمهمّةُ «تنجح» دائمًا.

والحكمُ هنا بدالّة المُشغِّل نفسِها (`evaluate_success`)، لا بنسخةٍ منها، حتى
لا يُثبَت شيءٌ عن مدقِّقٍ غيرِ الذي يُقاس به فعلًا.

**يُشغَّل في حاوية.** فالتسعُ مهامٍّ ذواتُ `command_exit_zero` تُنفِّذ سكربتاتٍ
ألّفها نموذجٌ خارجيّ، وتشغيلُها على جهاز المالك هو ما تعالجه المهمّة ج٣.

    docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \\
      <image> python tools/check_agentic_fails_before_fix.py <suite.json> [--meta <suite.meta.json>]

ومع `--meta` (بنكٌ له حلولٌ مرجعية، ك٤٤) يُفحص في البيئة نفسِها أن كلَّ مهمّةٍ تمرّ بحلّها المرجعيّ ولا
يمسّ حلُّها ملفّاتِ الحكم، وأن حلَّها القريبَ الخاطئ (`decoy_solution`) يسقط: فالمدقّقُ يميّز لا يرفض كلَّ شيء.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# بناءُ المساحة بدالّة المُشغِّل نفسِها: نصٌّ وملفّاتٌ ثنائية (ك٥٠)، لا بنسخةٍ نصّية منها
from evaluation.agentic_runner import (evaluate_success, harness_tampering, materialize, qualified_task_id,
                                      validate_agentic_suite, workspace_bytes)


def _judge(task: dict, overlay: dict | None) -> tuple[dict, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        materialize(task, root)
        for name, content in (overlay or {}).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(workspace_bytes(content))
        return evaluate_success(task, root), harness_tampering(task, root)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 4) or (len(argv) == 4 and argv[2] != "--meta"):
        print("الاستعمال: check_agentic_fails_before_fix.py <suite.json> [--meta <suite.meta.json>]", file=sys.stderr)
        return 2
    suite = validate_agentic_suite(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    meta = json.loads(Path(argv[3]).read_text(encoding="utf-8"))["tasks"] if len(argv) == 4 else None
    solutions = []
    already_passing = []
    unjudgeable = []
    for task in suite["tasks"]:
        # المعرّفُ المؤهَّل: task_id وحده يتكرّر بين حزم البنك (ك٤٢)
        name = qualified_task_id(suite["suite_id"], task["task_id"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            materialize(task, root)
            verdict = evaluate_success(task, root)
        kind = task["success"]["kind"]
        if verdict["passed"]:
            already_passing.append(name)
            mark = "✗ تمرّ قبل الحلّ"
        elif verdict.get("code") in ("success_command_unavailable", "success_command_timeout"):
            # تعذُّرُ الحكم ليس سقوطًا: لا يُقرأ دليلًا على أن المهمّة تقيس
            unjudgeable.append((name, verdict["code"]))
            mark = "؟ تعذَّر الحكم"
        else:
            mark = "✓ تسقط"
        if meta is not None:
            reference, tampered = _judge(task, meta[task["task_id"]]["reference_solution"])
            decoy, _ = _judge(task, meta[task["task_id"]]["decoy_solution"])
            if not reference["passed"] or tampered:
                solutions.append(f"{name}: الحلُّ المرجعيّ لا يمرّ ({reference.get('code')}{', ' + ', '.join(tampered) if tampered else ''})")
            if decoy["passed"]:
                solutions.append(f"{name}: الحلُّ الخاطئ يمرّ")
            mark += "، والمرجعيّ يمرّ" if reference["passed"] and not tampered else "، والمرجعيّ لا يمرّ"
            mark += "، والخاطئ يسقط" if not decoy["passed"] else "، والخاطئ يمرّ"
        print(f"{mark}  {name}  [{kind}]")

    total = len(suite["tasks"])
    print(f"\nالمهامّ: {total} — تسقط قبل الحلّ "
          f"{total - len(already_passing) - len(unjudgeable)}، "
          f"تمرّ {len(already_passing)}، تعذَّر الحكم {len(unjudgeable)}")
    if already_passing:
        print("مهامٌّ لا تقيس شيئًا:", ", ".join(already_passing))
    if unjudgeable:
        print("مهامٌّ لم يُحكم عليها:", ", ".join(f"{t} ({c})" for t, c in unjudgeable))
    if meta is not None:
        print(f"الحلول: {total - len(solutions)} مهمّةً بحلٍّ مرجعيٍّ يمرّ وحلٍّ خاطئٍ يسقط" if not solutions
              else "حلولٌ لا تميّز:\n  " + "\n  ".join(solutions))
    return 1 if already_passing or unjudgeable or solutions else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
