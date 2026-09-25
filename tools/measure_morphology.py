#!/usr/bin/env python3
"""قياسُ استخراج الجذر: المحلّلُ الحالي مقابل CAMeL Tools على عيّنةٍ ذهبيّةٍ واحدة (غ١).

    python3 tools/measure_morphology.py --out docs/probe/g1-camel-vs-morphology-<تاريخ>.json

العيّنةُ `evaluation/suites/morphology_roots_v1.json` (مئةُ كلمةِ متنٍ حقيقية بجذورها
الذهبية). يُقاس لكلّ محلّلٍ: **الإصابة** (الجذرُ المعطى صحيح)، و**الإجابة** (أعطى جذرًا)،
و**الخطأُ بثقة** (أعطى جذرًا خاطئًا) — فالمحلّلُ الذي يرفض حين لا يعلم أفضلُ من الذي
يخمّن، وهذا ما يفرّق بينه العمودان الأخيران. وCAMeL يُقاس بثلاث قراءات: **اتّفاق**
(كلُّ تحليلاته على جذرٍ واحد وإلا رفض — وهي القراءةُ المعتمَدة)، و**الأوّل** (أوّلُ
تحليلٍ بلا فكّ لبس)، و**أيّ** (سقفٌ أعلى: الجذرُ الذهبي بين تحليلاته).

**حدود**: الذهبُ بيد عميلٍ واحد لم يراجعه لسانيٌّ ثانٍ؛ وCAMeL يكتب الحرفَ المعتلّ
والهمزةَ «#» في r13 فتُقارَن بقاعدةٍ معلَنة؛ ولا فكَّ لبسٍ بالسياق (كلماتٌ مفردة).
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.linguistics.morphology import analyze  # noqa: E402

SUITE = ROOT / "evaluation/suites/morphology_roots_v1.json"
WEAK = set("وياأإآءؤئى")
NON_ROOTS = {"NOAN", "FOREIGN", "DIGIT", "PUNC", "NTWS", None, ""}


def camel_analyzer():
    """CAMeL إن رُكّب وقاعدتُه حاضرة، وإلا None برمز السبب."""
    try:
        from camel_tools.morphology.analyzer import Analyzer
        from camel_tools.morphology.database import MorphologyDB
    except ImportError:
        return None, "camel_tools_not_installed"
    try:
        return Analyzer(MorphologyDB.builtin_db("calima-msa-r13")), None
    except Exception as exc:                      # قاعدةٌ غائبة أو معطوبة
        return None, f"camel_db_unavailable:{type(exc).__name__}"


def letters(root: str) -> str:
    """جذرُ CAMeL «ك.ت.ب» أو الحالي «كتب» إلى حروفٍ متتالية؛ الهمزةُ الذهبية «ء»."""
    return root.replace(".", "").replace("_", "")


def matches(candidate: str | None, gold: list[str]) -> bool:
    """«#» في CAMeL يطابق حرفَ علّةٍ أو همزة؛ وما سواه حرفًا بحرف."""
    if candidate is None:
        return not gold
    if not gold:
        return False
    cand = letters(candidate)
    for root in gold:
        if len(root) != len(cand):
            continue
        if all(c == g or (c == "#" and g in WEAK) or ({c, g} <= WEAK and c != "#" and g in "ءأإؤئ" and c in "ءأإؤئ")
               for c, g in zip(cand, root)):
            return True
    return False


def current_verdict(word: str) -> dict:
    analysis = analyze(word)
    return {"root": analysis.root, "reason": analysis.reason, "pattern": analysis.pattern}


def camel_verdict(analyzer, word: str) -> dict:
    analyses = analyzer.analyze(word)
    roots = [a.get("root") for a in analyses]
    real = [r for r in roots if r not in NON_ROOTS]
    distinct = sorted(set(real))
    if not analyses or not real:
        agree, reason = None, ("no_analysis" if not analyses else "no_root")
    elif len(distinct) == 1:
        agree, reason = distinct[0], None
    else:
        agree, reason = None, "ambiguous"
    return {"analyses": len(analyses), "roots": distinct, "first": (real[0] if real else None),
            "agree": agree, "reason": reason}


def score(items, key):
    correct = sum(1 for it in items if it[key]["correct"])
    answered = sum(1 for it in items if it[key]["root"] is not None)
    wrong = sum(1 for it in items if it[key]["root"] is not None and not it[key]["correct"])
    refused_right = sum(1 for it in items if it[key]["root"] is None and it[key]["correct"])
    return {"correct": correct, "answered": answered, "wrong_with_confidence": wrong,
            "refused_correctly": refused_right, "total": len(items),
            "accuracy": round(correct / len(items), 4)}


def measure(suite: dict) -> dict:
    analyzer, camel_reason = camel_analyzer()
    items = []
    for item in suite["items"]:
        word, gold = item["word"], item["gold_roots"]
        cur = current_verdict(word)
        entry = {"word": word, "gold_roots": gold, "stratum": item["stratum"],
                 "current": {**cur, "correct": matches(cur["root"], gold)}}
        if analyzer is not None:
            cam = camel_verdict(analyzer, word)
            entry["camel_agree"] = {"root": cam["agree"], "reason": cam["reason"], "correct": matches(cam["agree"], gold)}
            entry["camel_first"] = {"root": cam["first"], "correct": matches(cam["first"], gold)}
            any_hit = any(matches(r, gold) for r in cam["roots"]) or (not cam["roots"] and not gold)
            entry["camel_any"] = {"root": cam["roots"], "correct": any_hit}
            entry["camel_detail"] = {"analyses": cam["analyses"], "roots": cam["roots"]}
        items.append(entry)
    scores = {"current": score(items, "current")}
    if analyzer is not None:
        for key in ("camel_agree", "camel_first"):
            scores[key] = score(items, key)
        scores["camel_any_upper_bound"] = {"correct": sum(1 for it in items if it["camel_any"]["correct"]),
                                           "total": len(items)}
    by_stratum = {}
    for stratum in ("frequent", "mid"):
        subset = [it for it in items if it["stratum"] == stratum]
        by_stratum[stratum] = {key: score(subset, key)["accuracy"] for key in scores if key != "camel_any_upper_bound"}
    return {"schema_version": 1, "kind": "morphology_root_measurement", "task": "غ١",
            "date": date.today().isoformat(), "suite_id": suite["suite_id"],
            "camel": ({"available": True, "db": "calima-msa-r13"} if analyzer is not None
                      else {"available": False, "reason": camel_reason}),
            "scores": scores, "by_stratum": by_stratum, "items": items,
            "measurement_limits": [
                "gold_roots_authored_by_one_agent_not_yet_reviewed_by_a_second_linguist",
                "single_words_without_context_no_disambiguation",
                "camel_r13_writes_weak_and_hamza_radicals_as_hash_compared_by_declared_rule",
                "sample_is_maritime_corpus_only_not_general_arabic",
                "current_analyzer_is_the_one_shipped_in_core_linguistics_morphology",
            ]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=SUITE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = measure(json.loads(args.suite.read_text(encoding="utf-8")))
    if args.out:
        if args.out.exists():
            print(json.dumps({"status": "refused", "code": "output_exists"}, ensure_ascii=False))
            return 1
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"camel": report["camel"], "scores": report["scores"], "by_stratum": report["by_stratum"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
