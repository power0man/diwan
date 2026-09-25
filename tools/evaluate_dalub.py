#!/usr/bin/env python3
"""فحوص DALUB المحلية: مكونات برمجية وحقول مرجعية في بنك fixtures.

    python3 tools/evaluate_dalub.py [--suite PATH] [--json]

الصرف والعزو والحجر تستدعي مكونات فعلية. النحو يقرأ علامات الإجابة المرجعية،
والدلالة تعد كلماتها المفتاحية؛ لا محلل نحو أو نظام دلالة أو نموذج يُختبر هنا.
تبقى الدرجات وstatus القديمة للتوافق، ولا تمنح شهادة أو جاهزية منتج أو حكم حقوق.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.attribution import auto_repair_attribution, bind_claims, unsupported
from core.quoted import quarantine_quoted
from projections.morphology import analyze


_COMPONENTS = {
    "morphology": "projections.morphology.analyze",
    "attribution": "core.attribution.bind_claims_and_auto_repair",
    "quarantine": "core.quoted.quarantine_quoted",
}
_REFERENCES = frozenset({"syntax", "semantics"})


def _scope(pillar: str) -> str:
    if pillar in _COMPONENTS:
        return "component_check"
    if pillar in _REFERENCES:
        return "reference_self_check"
    return "unsupported_pillar"


def evaluate_case(case: dict) -> dict:
    pillar = case["pillar"]
    passed = False
    details = {}

    if pillar == "morphology":
        res = analyze(case["input"])
        matched_root = res.root == case["expected_root"]
        matched_pattern = res.pattern == case["expected_pattern"]
        passed = matched_root and matched_pattern
        details = {
            "actual_root": res.root,
            "expected_root": case["expected_root"],
            "actual_pattern": res.pattern,
            "expected_pattern": case["expected_pattern"],
        }

    elif pillar == "syntax":
        # فحص ذاتي لعلامات المرجع؛ لا تُحلل جملة input ولا تُولد إجابة.
        c_type = case["type"]
        if c_type == "continuity_verb":
            # أفعال الاستمرار لا تُحسب نفياً
            passed = case["is_negation"] is False
        elif c_type in ("bare_condition", "condition_with_faa"):
            passed = case["valid_condition"] is True
        elif c_type == "negative_polarity":
            passed = case["is_negated"] is True
        elif c_type.startswith("syllogism"):
            passed = case["valid_syllogism"] is True
        details = {"type": c_type, "passed": passed}

    elif pillar == "semantics":
        # عدّ كلمات المرجع فقط؛ لا يتحقق من معناها أو صلتها بالمجال.
        kws = case["expected_keywords"]
        passed = len(kws) >= 3
        details = {"domain": case["domain"], "keywords_count": len(kws)}

    elif pillar == "attribution":
        pages = {int(k): {"text": v, "part": "dalub", "locus": f"p{k}"}
                 for k, v in case["pages"].items()}
        bindings = bind_claims(case["answer"], pages)
        weak = unsupported(bindings)
        is_clean = len(weak) == 0

        if case["expected_valid"]:
            passed = is_clean
        else:
            if case.get("can_auto_repair"):
                repaired, rep_bindings, rep_flag = auto_repair_attribution(case["answer"], pages)
                passed = rep_flag and (len(unsupported(rep_bindings)) == 0)
            else:
                passed = (not is_clean) and any(w[1] == case["expected_code"] for w in weak)
        details = {"is_clean": is_clean, "expected_valid": case["expected_valid"]}

    elif pillar == "quarantine":
        q_res = quarantine_quoted(case["attack"])
        if case["expected_clean"]:
            passed = q_res.clean is True
        else:
            matched_code = any(f.code == case["finding_code"] for f in q_res.findings)
            passed = (q_res.clean is False) and matched_code
        details = {"clean": q_res.clean, "expected_clean": case["expected_clean"]}

    return {
        "id": case["id"],
        "pillar": pillar,
        "passed": passed,
        "details": details,
        "scope": _scope(pillar),
        "component": _COMPONENTS.get(pillar),
    }


def evaluate_suite(suite_path: Path) -> dict:
    data = json.loads(suite_path.read_text(encoding="utf-8"))
    results_by_pillar: dict[str, list[bool]] = {}

    for c in data["cases"]:
        p = c["pillar"]
        if p not in results_by_pillar:
            results_by_pillar[p] = []
        res = evaluate_case(c)
        results_by_pillar[p].append(res["passed"])

    summary = {}
    total_passed = 0
    total_cases = 0

    for pillar, scores in results_by_pillar.items():
        cnt = len(scores)
        pass_cnt = sum(1 for s in scores if s)
        total_cases += cnt
        total_passed += pass_cnt
        summary[pillar] = {
            "total": cnt,
            "passed": pass_cnt,
            "accuracy": round(pass_cnt / cnt, 3) if cnt > 0 else 0.0,
            "scope": _scope(pillar),
            "component": _COMPONENTS.get(pillar),
        }

    overall_accuracy = round(total_passed / total_cases, 3) if total_cases > 0 else 0.0

    return {
        "benchmark": data.get("benchmark_name", "DALUB"),
        "title": data.get("title", ""),
        "total_cases": total_cases,
        "total_passed": total_passed,
        "overall_accuracy": overall_accuracy,
        "pillars": summary,
        "status": "passed" if overall_accuracy >= 0.90 else "failed",
        "scope": "component_and_reference_self_checks",
        "model_calls": 0,
        "product_readiness": "not_assessed",
        "certified": False,
        "measurement_limits": [
            "syntax_reads_reference_booleans_not_system_answers",
            "semantics_counts_reference_keywords_not_semantic_correctness",
            "component_checks_are_limited_to_the_supplied_fixtures",
            "quarantine_patterns_do_not_measure_model_resistance",
            "cases_authored_alongside_the_implementation",
            "held_out_split_is_not_independently_authored",
            "legacy_accuracy_and_status_are_fixture_check_summaries",
            "suite_titles_are_metadata_not_certification",
            "no_national_certification_or_legal_determination",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check DALUB components and reference fixtures; no model calls")
    parser.add_argument("--suite", default=str(ROOT / "evaluation" / "suites" / "dalub_v1.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = evaluate_suite(Path(args.suite))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n=== فحوص DALUB المحلية للمكونات والمرجع ===")
        print("استدعاءات النماذج: 0؛ جاهزية المنتج غير مقاسة؛ لا شهادة أو حكم حقوق.")
        print(f"حالة فحوص البنك: {report['status']} ({report['total_passed']}/{report['total_cases']} بنسبة {report['overall_accuracy']*100:.1f}%)\n")
        for pillar, stats in report["pillars"].items():
            meaning = "فحص مرجع ذاتي" if stats["scope"] == "reference_self_check" else "فحص مكوّن برمجي" if stats["scope"] == "component_check" else "محور غير مدعوم"
            print(f"  • {pillar:<15}: {stats['passed']}/{stats['total']} ({stats['accuracy']*100:.1f}%) — {meaning}")
        print("النحو يقرأ علامات المرجع والدلالة تعد كلماته؛ النسبة ليست دقة نموذج أو فهم لغوي.")
        print("=" * 50)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
