#!/usr/bin/env python3
"""لا يضع قالبُ مسألةٍ وسمَ عائلةٍ عاملة تلقائيًّا (البندان ٤ و٦ من #296؛ docs/AGENT-INTAKE.md).

وسمُ `family:<عائلة>` إذنُ البدء. ومن لا صلاحيةَ له في مستودعٍ عام لا يضع وسمًا إلا ما تضعه القوالبُ تلقائيًّا، فالقوالبُ
حدُّ الاستلام. والوسومُ تُقرأ بصورتها المضمَّنة وحدها (`labels: [...]`)؛ وصورةٌ غيرُها تُرفض باسمها: يُغلق عند الشكّ.
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


def template_labels(text: str) -> list[str] | None:
    """وسومُ القالب من سطرها المضمَّن؛ وكلُّ سطرٍ آخر يذكر `labels` في المستوى الأعلى يعيد None (صورةٌ لا تُقرأ)."""
    found = None
    for line in text.splitlines():
        if not re.match(r"^['\"]?labels['\"]?[ \t]*:", line):
            continue
        match = LABELS_LINE.match(line)
        if match is None or found is not None:
            return None
        found = [json.loads(item) for item in re.findall(r'"[^"\n]*"', match["items"])]
        if len(found) != len([part for part in match["items"].split(",") if part.strip()]):
            return None
    return found or []


def intake_findings(templates: dict[str, str], config: str) -> list[str]:
    problems = []
    for name, text in sorted(templates.items()):
        labels = template_labels(text)
        if labels is None:
            problems.append(f"labels_unreadable:{name}")
            continue
        problems += [f"working_family_label:{name}:{label}" for label in labels
                     if label.startswith(("family:", "ready:")) and label not in NON_WORKING]
    if not re.search(r"^blank_issues_enabled:[ \t]*false[ \t]*$", config, re.MULTILINE):
        problems.append("blank_issues_enabled")
    return problems


def published() -> tuple[dict[str, str], str]:
    templates = {p.name: p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.glob("*.yml"))
                 if p.name != "config.yml"}
    return templates, (TEMPLATES / "config.yml").read_text(encoding="utf-8")



def main() -> int:
    problems = intake_findings(*published())
    print(json.dumps({"status": "failed" if problems else "passed", "findings": problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
