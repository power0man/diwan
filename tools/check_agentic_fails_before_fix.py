"""كلُّ مهمّةٍ وكيلة تسقط قبل الحلّ — وإلا فهي لا تقيس شيئًا.

مهمّةٌ يمرّ معيارُها على المساحة الابتدائية تمنح الوكيلَ درجةً على لا شيء:
يكفيه ألّا يفعل. وهذه أخطرُ من مهمّةٍ صعبة، لأنها ترفع الرقمَ بلا عمل، ولا
يظهر خللُها في أي تشغيلة — فالمهمّةُ «تنجح» دائمًا.

والحكمُ هنا بدالّة المُشغِّل نفسِها (`evaluate_success`)، لا بنسخةٍ منها، حتى
لا يُثبَت شيءٌ عن مدقِّقٍ غيرِ الذي يُقاس به فعلًا.

**يُشغَّل في حاوية.** فالتسعُ مهامٍّ ذواتُ `command_exit_zero` تُنفِّذ سكربتاتٍ
ألّفها نموذجٌ خارجيّ، وتشغيلُها على جهاز المالك هو ما تعالجه المهمّة ج٣.

    docker run --rm --network none -v "$PWD":/workspace:ro -w /workspace \\
      <image> python tools/check_agentic_fails_before_fix.py <suite.json>
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# بناءُ المساحة بدالّة المُشغِّل نفسِها: نصٌّ وملفّاتٌ ثنائية (ك٥٠)، لا بنسخةٍ نصّية منها
from evaluation.agentic_runner import evaluate_success, materialize, qualified_task_id, validate_agentic_suite


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("الاستعمال: check_agentic_fails_before_fix.py <suite.json>", file=sys.stderr)
        return 2
    suite = validate_agentic_suite(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
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
        print(f"{mark}  {name}  [{kind}]")

    total = len(suite["tasks"])
    print(f"\nالمهامّ: {total} — تسقط قبل الحلّ "
          f"{total - len(already_passing) - len(unjudgeable)}، "
          f"تمرّ {len(already_passing)}، تعذَّر الحكم {len(unjudgeable)}")
    if already_passing:
        print("مهامٌّ لا تقيس شيئًا:", ", ".join(already_passing))
    if unjudgeable:
        print("مهامٌّ لم يُحكم عليها:", ", ".join(f"{t} ({c})" for t, c in unjudgeable))
    return 1 if already_passing or unjudgeable else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
