"""الخطةُ الحاكمة سليمةُ الإحالات، وتأجيلُها لا يعطّل ما لم يُؤجَّل (ق٦٨، ملاحظتا Codex السادسة عشرة والسابعة عشرة على #161).

كانت ح٢ تعدّ جلسةَ Nitro من مُخرجها («جلستا الماك وNitro مأذونتان») وخطوةُ المالك ٤ (G4 ١٧–١٨) التي تفتحها مؤجَّلةٌ بق٦٨، والخطةُ
بلا حالةِ إنجازٍ جزئيّ: فإمّا تُعلَن ح٢ منجزةً بلا مُخرجها، وإمّا يبقى كلُّ ما يعتمد عليها من عمل الماك والسحابة معطَّلًا حتى يعود
Nitro — عكسُ ما أراده القرار. فُصلت جلسةُ Nitro في ح٢-ن المؤجَّلة، وهذه الحرّاسُ تجعل هذا الصنفَ فشلَ CI لا ملاحظةَ مراجعة.
والتأجيلُ يُقرأ بتعريف الخزنة نفسِه (`tools/obsidian_vault.py`: تأجيلُ المهمّة أو تأجيلُ دورها، والأدوارُ المؤجَّلة التي يسمّيها نصُّها).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("obsidian_vault_for_plan", ROOT / "tools/obsidian_vault.py")
ov = importlib.util.module_from_spec(spec)
sys.modules["obsidian_vault_for_plan"] = ov
spec.loader.exec_module(ov)

PLAN = json.loads((ROOT / ov.PLAN).read_text(encoding="utf-8"))
TASKS = {t["id"]: t for t in PLAN["tasks"]}
AGENTS = {a["name"]: a for a in PLAN.get("agents", [])}


def _deferred(task_id: str) -> bool:
    return ov.task_deferral(TASKS[task_id], AGENTS) is not None


def test_plan_ids_are_unique_and_every_reference_resolves():
    """معرّفُ كلِّ مهمّةٍ فريد، وكلُّ إحالةٍ إلى مهمّة (يعتمد على، ويفتح، ومهامُّ المرحلة، ومهامُّ الدور، وما تفتحه خطوةُ المالك) تبلغ
    مهمّةً موجودة؛ فمعرّفٌ جديد مثل ح٢-ن لا يُضاف في موضعٍ ويُنسى في آخر."""
    ids = [t["id"] for t in PLAN["tasks"]]
    assert sorted(i for i in set(ids) if ids.count(i) > 1) == []
    refs = ([(t["id"], d) for t in PLAN["tasks"] for d in t.get("depends_on", []) + t.get("unblocks", [])]
            + [(p["id"], d) for p in PLAN["phases"] for d in p["task_ids"]]
            + [(a["name"], d) for a in PLAN.get("agents", []) for d in a.get("task_ids", [])]
            + [(f"step {s['order']}", d) for s in PLAN["owner_steps"] for d in s.get("unblocks", [])])
    assert [(src, d) for src, d in refs if d not in TASKS] == []


def test_every_deferral_names_a_recorded_decision_a_date_and_a_reason():
    """كلُّ تأجيلٍ في الخطة (مهمّةٍ أو خطوةِ مالك أو دور) يسمّي قرارًا مسجَّلًا بعنوانه في `docs/DECISIONS.md` وتاريخَ عودةٍ وسببًا."""
    decisions = set(re.findall(r"^## (ق[٠-٩]+) — ", (ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8"), re.M))
    entries = ([("task", t["id"], t) for t in PLAN["tasks"]] + [("step", s["order"], s) for s in PLAN["owner_steps"]]
               + [("agent", a["name"], a) for a in PLAN.get("agents", [])])
    deferred = [(kind, key, e["deferred"]) for kind, key, e in entries if e.get("deferred")]
    assert deferred, "لا تأجيلَ في الخطة؟ ق٦٨ يؤجّل Nitro"
    for kind, key, d in deferred:
        assert d.get("by") in decisions, (kind, key, d.get("by"))
        assert date.fromisoformat(d.get("until", "")) and str(d.get("reason", "")).strip(), (kind, key)
    assert ("task", "ح٢-ن") in {(k, key) for k, key, _ in deferred}, "جلسةُ Nitro التي فُصلت من ح٢ مؤجَّلةٌ بق٦٨"


def test_no_active_task_waits_on_deferred_work():
    """مهمّةٌ غيرُ مؤجَّلة لا تعتمد اعتمادًا صلبًا (`depends_on`) على مهمّةٍ مؤجَّلة، ولا تفتحها خطوةُ مالكٍ مؤجَّلة. فعملُ الماك
    والسحابة لا ينتظر Nitro: كانت ح٢ نشطةً تفتحها خطوةُ المالك ٤ المؤجَّلة ومُخرجُها جلسةُ Nitro، فتعطّل كلُّ ما يعتمد عليها
    (الجولة السادسة عشرة). ولا استثناءَ للمهمّة المختلطة: جديد-v1-acceptance تقبل «مشغّل Nitro (ع٣) أو العقدة الثانية»، وكانت
    تعتمد على ع٣ صلبًا فتبقى معطَّلةً ولو توفّرت العقدةُ الثانية (الجولة السابعة عشرة). ومخطّطُ الخطة بلا اعتمادٍ بديل، فالبديلُ
    يُكتب في نصّ المهمّة لا في `depends_on`."""
    offending = [(t["id"], "يعتمد على", d) for t in PLAN["tasks"] if not _deferred(t["id"])
                 for d in t.get("depends_on", []) if _deferred(d)]
    offending += [(f"خطوة المالك {s['order']}", "تفتح", tid) for s in PLAN["owner_steps"] if s.get("deferred")
                  for tid in s.get("unblocks", []) if not _deferred(tid)]
    assert offending == []
    # والفصلُ نفسُه: ح٢ نشطةٌ لا تسمّي جلسةَ Nitro في مُخرجها، وما يحتاج Nitro بعينه يعتمد على ح٢-ن
    assert not _deferred("ح٢") and "Nitro" not in TASKS["ح٢"]["deliverable"]
    assert {t["id"] for t in PLAN["tasks"] if "ح٢-ن" in t.get("depends_on", [])} == {"ح٥", "ع٣"}
    assert "مشغّل Nitro (ع٣، claude-nitro) أو العقدة الثانية" in TASKS["جديد-v1-acceptance"]["description"], "البديلُ في نصّها"
