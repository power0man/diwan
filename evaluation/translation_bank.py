"""بنكُ الترجمة (غ٤): الحكمُ على ترجمةٍ واحدة، وملخّصُ البنك بعتباته.

- **تمرّ الحالةُ بثلاثة شروطٍ معًا:**
  - يجتازها المدقّقُ الحتميّ (`agent/translation.py::check`)، ومعه مسردُ الحالة.
  - وفيها من كل مجموعةِ معنًى بديلٌ واحدٌ على الأقل.
  - ولا يرد فيها ممنوع.
- **الملخّص:** نسبةُ النجاح للمقيس، والنسبةُ لكل فئة، ونسبةُ اجتياز المدقّق وحده.
  - العطبُ يخرج من المقام، ويمنع الاستيفاء ما بقي.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.translation import _WORD, _contains, check

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evaluation" / "suites" / "translation_v1.json"
META = SUITE.with_suffix(".meta.json")


def load():
    return (json.loads(SUITE.read_text(encoding="utf-8")), json.loads(META.read_text(encoding="utf-8")))


# المدقّقُ لا يقيس الطولَ لمصدرٍ دون ستِّ كلمات، فرفضٌ أو شرحٌ طويلٌ لنصّ واجهةٍ قصير كان يُحسب ترجمة
# (tr55 «Save changes» وtr57 في قياس غ٤ الأول؛ ملاحظة Codex على #131). فما زاد على max(8، أربعة أضعاف) لا يمرّ.
SHORT_SOURCE_WORDS = 6


def overlong_for_short_source(source: str, translation: str) -> bool:
    words = len(_WORD.findall(source))
    return words < SHORT_SOURCE_WORDS and len(_WORD.findall(translation)) > max(8, 4 * words)


def score_item(item: dict, translation: str) -> dict:
    report = check(item["source"], translation, target=item["target"],
                   glossary=[tuple(pair) for pair in item["glossary"]])
    codes = list(report.codes)
    if overlong_for_short_source(item["source"], translation):
        codes.append("overlong_for_short_source")
    check_passed = report.passed and "overlong_for_short_source" not in codes
    missing = [group for group in item["must_include"] if not any(_contains(translation, alt) for alt in group)]
    forbidden = [phrase for phrase in item["must_not_include"] if _contains(translation, phrase)]
    return {"passed": check_passed and not missing and not forbidden, "check_passed": check_passed,
            "codes": codes, "missing_meaning": [group[0] for group in missing], "forbidden_found": forbidden}


def summarize(results: list[dict], thresholds: dict) -> dict:
    measured = [r for r in results if r["status"] == "measured"]
    errors = len(results) - len(measured)
    rate = round(sum(r["passed"] for r in measured) / len(measured), 4) if measured else None
    check_rate = round(sum(r["check_passed"] for r in measured) / len(measured), 4) if measured else None
    categories = {}
    for result in results:
        entry = categories.setdefault(result["category"], {"measured": 0, "passed": 0, "errors": 0})
        if result["status"] == "measured":
            entry["measured"] += 1
            entry["passed"] += int(result["passed"])
        else:
            entry["errors"] += 1
    rates = []
    for entry in categories.values():
        entry["rate"] = round(entry["passed"] / entry["measured"], 4) if entry["measured"] else None
        rates.append(entry["rate"])
    meets = (rate is not None and rate >= thresholds["pass_rate"]
             and check_rate >= thresholds["check_pass_rate"]
             and all(r is not None and r >= thresholds["min_category_pass_rate"] for r in rates)
             and errors == 0)
    return {"attempted": len(results), "measured": len(measured), "errors": errors,
            "passed": sum(r["passed"] for r in measured), "pass_rate": rate, "check_pass_rate": check_rate,
            "categories": categories, "thresholds": thresholds, "meets_thresholds": meets}
