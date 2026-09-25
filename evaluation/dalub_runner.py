"""مشغّل فحوص DALUB المحلية للمكونات وحقول المرجع؛ لا يستدعي نموذجًا.

    python3 evaluation/dalub_runner.py [--suite PATH] [--split all|dev|held_out] [--json]

يدعم:
1. فحص الحزمة وشطريها؛ held_out اسم شطر تاريخي مكشوف، لا بنك محجوب مستقل.
2. فصل استدعاءات المكونات عن فحص النحو والدلالة الذاتي لحقول المرجع.
3. بصمة محتوى لحقول الدرجات القديمة؛ ليست توقيعًا أو هوية تشغيل فريدة.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.canonical import canonical_bytes, digest
from tools.evaluate_dalub import evaluate_case

DEFAULT_SUITE = ROOT / "evaluation" / "suites" / "dalub_v1.json"


def partition_cases(cases: list[dict], split: str = "all") -> list[dict]:
    """تقسيم تاريخي للحالات؛ الاسم held_out لا يثبت استقلال تأليفها أو حجبها."""
    if split == "all":
        return cases

    # تجميع الحالات حسب المحور
    by_pillar: dict[str, list[dict]] = {}
    for c in cases:
        by_pillar.setdefault(c["pillar"], []).append(c)

    selected: list[dict] = []
    for pillar, p_cases in by_pillar.items():
        # آخر 20% للشطر المسمى held_out؛ لا يُخفى أو يؤلّف مستقلًا هنا.
        split_idx = int(len(p_cases) * 0.8)
        if split == "dev":
            selected.extend(p_cases[:split_idx])
        elif split == "held_out":
            selected.extend(p_cases[split_idx:])
        else:
            raise ValueError(f"Unknown split: {split}")

    return selected


def run_benchmark(suite_path: Path = DEFAULT_SUITE, split: str = "all") -> dict:
    raw_data = json.loads(suite_path.read_text(encoding="utf-8"))
    all_cases = raw_data["cases"]
    cases_to_run = partition_cases(all_cases, split=split)

    start_time = time.monotonic()
    results_by_pillar: dict[str, list[dict]] = {}

    for c in cases_to_run:
        p = c["pillar"]
        if p not in results_by_pillar:
            results_by_pillar[p] = []
        res = evaluate_case(c)
        results_by_pillar[p].append(res)

    duration_s = round(time.monotonic() - start_time, 4)

    summary = {}
    total_passed = 0
    total_cases = len(cases_to_run)

    for pillar, res_list in results_by_pillar.items():
        cnt = len(res_list)
        pass_cnt = sum(1 for r in res_list if r["passed"])
        total_passed += pass_cnt
        summary[pillar] = {
            "total": cnt,
            "passed": pass_cnt,
            "accuracy": round(pass_cnt / cnt, 4) if cnt > 0 else 0.0,
            "scope": res_list[0]["scope"],
            "component": res_list[0]["component"],
        }

    overall_accuracy = round(total_passed / total_cases, 4) if total_cases > 0 else 0.0

    # تحفظ البصمة حقول الدرجات القديمة للتوافق؛ لا توقّع الحدود أو زمن التشغيل.
    receipt_payload = {
        "suite_version": raw_data.get("version", "1.0.0"),
        "split": split,
        "total_cases": total_cases,
        "total_passed": total_passed,
        "overall_accuracy": str(overall_accuracy),
        "pillars": {k: f"{v['passed']}/{v['total']}" for k, v in sorted(summary.items())},
    }
    receipt_hash = digest(receipt_payload)

    return {
        "benchmark": raw_data.get("benchmark_name", "DALUB"),
        "title": raw_data.get("title", ""),
        "version": raw_data.get("version", "1.0.0"),
        "split": split,
        "total_cases": total_cases,
        "total_passed": total_passed,
        "overall_accuracy": overall_accuracy,
        "duration_seconds": duration_s,
        "pillars": summary,
        "receipt_hash": receipt_hash,
        "status": "passed" if overall_accuracy >= 0.90 else "failed",
        "scope": "component_and_reference_self_checks",
        "model_calls": 0,
        "product_readiness": "not_assessed",
        "certified": False,
        "receipt_scope": "legacy_score_fields_only_unsigned_content_hash_not_unique_run_identity",
        # حدودُ صلاحية القياس، تُنشر مع الرقم لا بعده. قُيست في فحص
        # ٢٠٢٦-٠٩-٢٣ ٠٢:٢٣ غرينتش: dev و«held_out» و«all» كلُّها ١٠٠٪،
        # لأن القضايا الأربعمئة أُلّفت في الدفعة نفسها التي أُلّفت فيها
        # الشيفرة. و«شطرٌ محجوب» كُتب مع الشيفرة ليس محجوبًا — هو تسميةٌ
        # أخرى لشطر التطوير. وثماني صياغاتٍ عربيةٍ جديدةٍ خارج البنك عبرت
        # الحَجرَ كلُّها (docs/probe/dalub-validity-20260923.json).
        # فالرقمُ يقيس تغطيةَ البنك لا قوّةَ الحارس.
        "measurement_limits": [
            "cases_authored_alongside_the_implementation",
            "held_out_split_is_not_independently_authored",
            "score_bounds_bank_coverage_not_guard_strength",
            "syntax_reads_reference_booleans_not_system_answers",
            "semantics_counts_reference_keywords_not_semantic_correctness",
            "quarantine_patterns_do_not_measure_model_resistance",
            "no_national_certification_or_legal_determination",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DALUB local component and reference checks; no model calls")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--split", choices=["all", "dev", "held_out"], default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = run_benchmark(Path(args.suite), split=args.split)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== فحوص DALUB المحلية (الإصدار {report['version']} — الشطر: {report['split']}) ===")
        print("استدعاءات النماذج: 0؛ جاهزية المنتج غير مقاسة؛ لا شهادة أو حكم حقوق.")
        print("held_out تسمية تاريخية لشطر مكشوف؛ النحو والدلالة يفحصان المرجع نفسه.")
        print(f"حالة فحوص البنك: {report['status']} ({report['total_passed']}/{report['total_cases']} بنسبة {report['overall_accuracy']*100:.1f}%)")
        print(f"الزمن المستغرق: {report['duration_seconds']} ثانية | بصمة درجات غير موقعة: {report['receipt_hash'][:16]}…\n")
        for pillar, stats in report["pillars"].items():
            print(f"  • {pillar:<15}: {stats['passed']}/{stats['total']} ({stats['accuracy']*100:.1f}%) — {stats['scope']}")
        print("=" * 60)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
