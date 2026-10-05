#!/usr/bin/env python3
"""لا يضع قالبُ مسألةٍ وسمَ عائلةٍ عاملة تلقائيًّا (البندان ٤ و٦ من #296؛ docs/AGENT-INTAKE.md).

وسمُ `family:<عائلة>` إذنُ البدء. ومن لا صلاحيةَ له في مستودعٍ عام لا يضع وسمًا إلا ما تضعه القوالبُ تلقائيًّا، فالقوالبُ
حدُّ الاستلام. والوسومُ تُقرأ بصورتها المضمَّنة وحدها (`labels: [...]`)، في نموذج YAML أو في رأس قالب Markdown؛ وصورةٌ
غيرُها تُرفض باسمها: يُغلق عند الشكّ.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
LABELS_LINE = re.compile(r"^labels:[ \t]*\[(?P<items>[^\]\n]*)\][ \t]*(?:#.*)?$")
NON_WORKING = frozenset({"family:owner"})


# مفتاحُ `labels` في أيّ موضعٍ من السطر: بإزاحةٍ، أو داخل خريطةٍ مضمَّنة `--- {labels: [...]}` (ملاحظة Codex على #304)؛
# وسطرٌ يبدأ خريطةً مضمَّنة أو مفتاحًا مركّبًا (`? labels`) صورةٌ لا تُقرأ سطرًا سطرًا.
LABELS_KEY = re.compile(r"""(?:^|[\s{,])['"]?labels['"]?[ \t]*:""")
UNREADABLE_STARTS = ("{", "?")
# مفتاحٌ مقتبسٌ بتهريبٍ (`"labe\u006cs":`) يُفكّ إلى labels ولا يُرى في نصّه، والمرساةُ (`&k`) تجعل اسمَها المستعار
# (`*k :`) مفتاحًا لا يُرى؛ فكلاهما صورةٌ لا تُقرأ (ملاحظة Codex على #304).
ESCAPED_KEY = re.compile(r'"(?:[^"\\\n]|\\.)*\\.(?:[^"\\\n]|\\.)*"[ \t]*:')
ANCHOR = re.compile(r"(?:^|[\s\[{,])&[^\s,\[\]{}]")
BLANK_KEY = re.compile(r"""(?:^|[\s{,])['"]?blank_issues_enabled['"]?[ \t]*:""")
BLANK_DISABLED = re.compile(r"^blank_issues_enabled:[ \t]*false[ \t]*$")


def unreadable_line(line: str) -> bool:
    """سطرٌ لا يُقرأ سطرًا سطرًا: خريطةٌ مضمَّنة أو مفتاحٌ مركّب، أو مقتبسٌ بتهريبٍ يليه «:»، أو مرساة."""
    return line.lstrip().startswith(UNREADABLE_STARTS) or bool(ESCAPED_KEY.search(line)) or bool(ANCHOR.search(line))


def template_labels(text: str) -> list[str] | None:
    """وسومُ القالب من سطرها المضمَّن في العمود الأول؛ وكلُّ ذكرٍ آخر لمفتاح `labels` (مُزاحًا أو في خريطةٍ مضمَّنة)، وكلُّ
    خريطةٍ مضمَّنة أو مفتاحٍ مركّب (`?`)، يعيد None: صورةٌ لا تُقرأ، فيُغلق عند الشكّ."""
    found = None
    for line in text.splitlines():
        if unreadable_line(line):
            return None
        if not LABELS_KEY.search(line):
            continue
        match = LABELS_LINE.match(line)
        if match is None or found is not None:
            return None
        found = [json.loads(item) for item in re.findall(r'"[^"\n]*"', match["items"])]
        if len(found) != len([part for part in match["items"].split(",") if part.strip()]):
            return None
    return found or []


def blank_issues_disabled(config: str) -> bool:
    """المسائلُ الفارغة معطّلةٌ بسطرٍ واحدٍ في العمود الأول هو كلُّ ذكرٍ لمفتاحها، وقيمتُه `false`. فسطرٌ داخل نصٍّ مقتبسٍ
    متعدّد الأسطر لا يغلب المفتاحَ الفعليّ، ولا تُقبل صورةٌ لا تُقرأ سطرًا سطرًا (ملاحظة Codex على #304)."""
    found = []
    for line in config.removeprefix("\ufeff").splitlines():
        if unreadable_line(line):
            return False
        if BLANK_KEY.search(line):
            found.append(line)
    return len(found) == 1 and bool(BLANK_DISABLED.match(found[0]))


def front_matter(text: str) -> str | None:
    """رأسُ قالب Markdown بين سطرَي `---` في أوّله، ومنه وحده تضع GitHub الوسوم؛ وبلا رأسٍ فلا وسوم. ورأسٌ لا يُغلق
    None (صورةٌ لا تُقرأ)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index, line in enumerate(lines[1:], 1):
        # الفاصلُ الخاتم في العمود الأول وحده؛ و`---` مُزاحٌ نصٌّ داخل قيمةٍ كتليّة (ملاحظة Codex على #304)
        if line.rstrip() == "---":
            return "\n".join(lines[1:index])
    return None


def intake_findings(templates: dict[str, str], config: str) -> list[str]:
    problems = []
    for name, text in sorted(templates.items()):
        text = text.removeprefix("\ufeff")   # علامةُ ترتيب البايتات يُسقطها مفسّرُ YAML، فلا تُخفي ما بعدها
        if name.endswith(".md"):
            text = front_matter(text)
        labels = None if text is None else template_labels(text)
        if labels is None:
            problems.append(f"labels_unreadable:{name}")
            continue
        # GitHub تطابق أسماءَ الوسوم بلا اعتبارٍ لحالة الأحرف، فـ`Family:OpenAI` يضع `family:openai` (ملاحظة Codex على #304)
        problems += [f"working_family_label:{name}:{label}" for label in labels
                     if label.strip().casefold().startswith(("family:", "ready:"))
                     and label.strip().casefold() not in NON_WORKING]
    if not blank_issues_disabled(config):
        problems.append("blank_issues_enabled")
    return problems


def published() -> tuple[dict[str, str], str]:
    """كلُّ ملفٍّ في مجلّد القوالب سوى إعداده: نماذجُ YAML (`.yml` و`.yaml`) وقوالبُ Markdown (`.md`) كلّها تضع وسومًا، فلا
    يُفلت امتدادٌ من الفحص (ملاحظة Codex على #304)."""
    templates = {p.name: p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.iterdir())
                 if p.is_file() and p.name not in {"config.yml", "config.yaml"}}
    return templates, (TEMPLATES / "config.yml").read_text(encoding="utf-8")



def main() -> int:
    problems = intake_findings(*published())
    print(json.dumps({"status": "failed" if problems else "passed", "findings": problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
