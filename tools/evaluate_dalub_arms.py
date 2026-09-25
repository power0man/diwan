#!/usr/bin/env python3
"""محاكاة محلية لجدول DALUB ذي الأذرع الثلاثة؛ ليست مقارنة نماذج.

يتطلب التشغيل --simulation صراحةً. درجات model_alone وnaive_rag
مفروضة بثوابت وحقول القضايا وزوجية المعرّف. وdiwan_full اسم تاريخي
لفحوص مكونات حتمية، وليس تشغيل نموذج مع ديوان كاملاً.
لا استدعاء نموذج ولا استرجاع فعلي للذراعين المرجعيين؛ جميع أعمدة
الجودة تحويلات اصطناعية، لا قياسات لصحة جواب أو هلوسة نموذج.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.canonical import digest
from evaluation.dalub_runner import DEFAULT_SUITE, partition_cases
from tools.evaluate_dalub import evaluate_case

DEFAULT_OUT = ROOT / "var" / "dalub-three-arm-simulation.json"
HISTORICAL_REPORT = ROOT / "docs" / "probe" / "dalub-benchmark-results.json"
MEASUREMENT_LIMITS = (
    "no_model_calls_and_no_measured_retrieval_baselines",
    "baseline_scores_are_constants_case_labels_and_case_id_parity",
    "diwan_full_label_means_component_bank_checks_not_full_system",
    "some_component_checks_read_expected_fixture_fields",
    "quality_columns_are_synthetic_not_measured_answer_quality",
    "cases_authored_alongside_the_implementation",
    "held_out_split_is_not_independently_authored",
    "scores_do_not_establish_model_superiority_or_certification",
    "receipt_is_unsigned_content_hash_not_unique_run_identity",
)
ARM_PROVENANCE = {
    "model_alone": "synthetic_constants_case_labels_and_case_id_parity",
    "naive_rag": "synthetic_constants_and_case_labels_no_retrieval",
    "diwan_full": "deterministic_component_bank_checks_not_full_model_system",
}


def evaluate_arm_case(case: dict, arm: str) -> dict:
    """توليد درجات المحاكاة؛ أسماء الأذرع والأعمدة تاريخية لا قياسات نماذج."""
    p = case["pillar"]
    cid = case["id"]

    if arm == "diwan_full":
        res = evaluate_case(case)
        passed = res["passed"]
        return {
            "case_id": cid,
            "pillar": p,
            "passed": passed,
            "factuality_bp": 10000 if passed else 0,
            "completeness_bp": 10000 if passed else 0,
            "attribution_bp": 10000 if passed else 0,
            "hallucination_penalty_bp": 0 if passed else 5000,
            "overall_bp": 10000 if passed else 0,
        }

    elif arm == "naive_rag":
        if p == "morphology":
            # افتراض محاكاة ثابت؛ لا استرجاع أو اختبار محلل مرجعي هنا
            return {
                "case_id": cid,
                "pillar": p,
                "passed": False,
                "factuality_bp": 1500,
                "completeness_bp": 2000,
                "attribution_bp": 1000,
                "hallucination_penalty_bp": 3500,
                "overall_bp": 500,
            }
        elif p == "syntax":
            # درجات مفروضة لهذا المحور، لا قياس فهم ذراع حقيقية
            return {
                "case_id": cid,
                "pillar": p,
                "passed": False,
                "factuality_bp": 3000,
                "completeness_bp": 3500,
                "attribution_bp": 1500,
                "hallucination_penalty_bp": 4000,
                "overall_bp": 800,
            }
        elif p == "semantics":
            # قيم اصطناعية؛ لا استدعاء مسترجع أو نصوص خام
            return {
                "case_id": cid,
                "pillar": p,
                "passed": True,
                "factuality_bp": 6250,
                "completeness_bp": 6500,
                "attribution_bp": 4500,
                "hallucination_penalty_bp": 1800,
                "overall_bp": 4500,
            }
        elif p == "attribution":
            # افتراضات مشتقة من حقول البنك، لا أجوبة مرجعية مقيسة
            passed = case.get("expected_valid", False)
            f_bp = 6000 if passed else 2500
            c_bp = 6000 if passed else 3000
            a_bp = 5000 if passed else 2000
            h_bp = 1000 if passed else 4500
            ov_bp = max(0, (f_bp + c_bp + a_bp) // 3 - h_bp)
            return {
                "case_id": cid,
                "pillar": p,
                "passed": passed,
                "factuality_bp": f_bp,
                "completeness_bp": c_bp,
                "attribution_bp": a_bp,
                "hallucination_penalty_bp": h_bp,
                "overall_bp": ov_bp,
            }
        elif p == "quarantine":
            # تحويل لوسم expected_clean؛ لا سياق نموذج أو تجربة حقن حية
            clean = case.get("expected_clean", False)
            f_bp = 10000 if clean else 1000
            c_bp = 10000 if clean else 1000
            a_bp = 0
            h_bp = 0 if clean else 8000
            ov_bp = max(0, (f_bp + c_bp + a_bp) // 3 - h_bp)
            return {
                "case_id": cid,
                "pillar": p,
                "passed": clean,
                "factuality_bp": f_bp,
                "completeness_bp": c_bp,
                "attribution_bp": a_bp,
                "hallucination_penalty_bp": h_bp,
                "overall_bp": ov_bp,
            }

    elif arm == "model_alone":
        if p == "morphology":
            passed = (case.get("category") == "root_extraction" and int(cid.split("-")[-1]) % 2 == 0)
            f_bp = 7000 if passed else 2500
            c_bp = 7500 if passed else 3000
            h_bp = 500 if passed else 3500
            ov_bp = max(0, (f_bp + c_bp) // 2 - h_bp)
            return {
                "case_id": cid,
                "pillar": p,
                "passed": passed,
                "factuality_bp": f_bp,
                "completeness_bp": c_bp,
                "attribution_bp": 0,
                "hallucination_penalty_bp": h_bp,
                "overall_bp": ov_bp,
            }
        elif p == "syntax":
            is_continuity = case.get("type") == "continuity_verb"
            passed = False if is_continuity else (int(cid.split("-")[-1]) % 2 == 1)
            f_bp = 6500 if passed else 3000
            c_bp = 7000 if passed else 3500
            h_bp = 500 if passed else 4000
            ov_bp = max(0, (f_bp + c_bp) // 2 - h_bp)
            return {
                "case_id": cid,
                "pillar": p,
                "passed": passed,
                "factuality_bp": f_bp,
                "completeness_bp": c_bp,
                "attribution_bp": 0,
                "hallucination_penalty_bp": h_bp,
                "overall_bp": ov_bp,
            }
        elif p == "semantics":
            passed = (int(cid.split("-")[-1]) % 2 == 0)
            f_bp = 6000 if passed else 3500
            c_bp = 6500 if passed else 4000
            h_bp = 1000 if passed else 3500
            ov_bp = max(0, (f_bp + c_bp) // 2 - h_bp)
            return {
                "case_id": cid,
                "pillar": p,
                "passed": passed,
                "factuality_bp": f_bp,
                "completeness_bp": c_bp,
                "attribution_bp": 0,
                "hallucination_penalty_bp": h_bp,
                "overall_bp": ov_bp,
            }
        elif p == "attribution":
            return {
                "case_id": cid,
                "pillar": p,
                "passed": False,
                "factuality_bp": 1200,
                "completeness_bp": 1500,
                "attribution_bp": 0,
                "hallucination_penalty_bp": 6500,
                "overall_bp": 0,
            }
        elif p == "quarantine":
            clean = case.get("expected_clean", False)
            f_bp = 10000 if clean else 500
            c_bp = 10000 if clean else 500
            a_bp = 0
            h_bp = 0 if clean else 8500
            ov_bp = max(0, (f_bp + c_bp + a_bp) // 3 - h_bp)
            return {
                "case_id": cid,
                "pillar": p,
                "passed": clean,
                "factuality_bp": f_bp,
                "completeness_bp": c_bp,
                "attribution_bp": a_bp,
                "hallucination_penalty_bp": h_bp,
                "overall_bp": ov_bp,
            }

    raise ValueError(f"Unknown arm: {arm}")


def run_dalub_three_arm_benchmark(
    suite_path: Path = DEFAULT_SUITE,
    split: str = "all",
    *,
    simulation: bool = False,
) -> dict:
    """Return an explicitly opted-in simulation, never model certification."""
    if simulation is not True:
        raise ValueError("simulation_required: use simulation=True; this is not a measured model comparison")
    if split not in {"all", "dev", "held_out"}:
        raise ValueError("unknown_simulation_split")
    suite_bytes = Path(suite_path).read_bytes()
    raw_suite = json.loads(suite_bytes)
    all_cases = raw_suite["cases"]
    cases_to_run = partition_cases(all_cases, split=split)
    total_cases = len(cases_to_run)
    if not total_cases:
        raise ValueError("empty_simulation_suite")

    t0 = time.monotonic()
    arms = ("model_alone", "naive_rag", "diwan_full")
    summary: dict[str, dict] = {}
    by_pillar: dict[str, dict[str, dict]] = {p: {} for p in ("morphology", "syntax", "semantics", "attribution", "quarantine")}

    for arm in arms:
        scores = [evaluate_arm_case(c, arm) for c in cases_to_run]
        passed_cnt = sum(1 for s in scores if s["passed"])
        mean_f = sum(s["factuality_bp"] for s in scores) // total_cases
        mean_c = sum(s["completeness_bp"] for s in scores) // total_cases
        mean_a = sum(s["attribution_bp"] for s in scores) // total_cases
        mean_h = sum(s["hallucination_penalty_bp"] for s in scores) // total_cases
        mean_ov = sum(s["overall_bp"] for s in scores) // total_cases

        summary[arm] = {
            "total_cases": total_cases,
            "passed_cases": passed_cnt,
            "accuracy_bp": (passed_cnt * 10000) // total_cases,
            "accuracy_str": f"{(passed_cnt / total_cases) * 100:.1f}%",
            "factuality_bp": mean_f,
            "completeness_bp": mean_c,
            "attribution_bp": mean_a,
            "hallucination_penalty_bp": mean_h,
            "overall_bp": mean_ov,
        }

        # تجميع حسب المحور
        for p in by_pillar:
            p_scores = [s for s in scores if s["pillar"] == p]
            p_cnt = len(p_scores)
            p_passed = sum(1 for s in p_scores if s["passed"])
            by_pillar[p][arm] = {
                "total": p_cnt,
                "passed": p_passed,
                "accuracy_bp": (p_passed * 10000) // p_cnt if p_cnt > 0 else 0,
                "accuracy_str": f"{(p_passed / p_cnt) * 100:.1f}%" if p_cnt > 0 else "0.0%",
                "factuality_bp": sum(s["factuality_bp"] for s in p_scores) // p_cnt if p_cnt > 0 else 0,
                "completeness_bp": sum(s["completeness_bp"] for s in p_scores) // p_cnt if p_cnt > 0 else 0,
                "attribution_bp": sum(s["attribution_bp"] for s in p_scores) // p_cnt if p_cnt > 0 else 0,
                "hallucination_penalty_bp": sum(s["hallucination_penalty_bp"] for s in p_scores) // p_cnt if p_cnt > 0 else 0,
                "overall_bp": sum(s["overall_bp"] for s in p_scores) // p_cnt if p_cnt > 0 else 0,
            }

    duration_s = round(time.monotonic() - t0, 4)

    receipt_payload = {
        "kind": "dalub_three_arm_simulation",
        "status": "simulation_only",
        "measured_model_comparison": False,
        "model_calls": 0,
        "certified": False,
        "measurement_limits": list(MEASUREMENT_LIMITS),
        "arm_provenance": dict(ARM_PROVENANCE),
        "suite_sha256": hashlib.sha256(suite_bytes).hexdigest(),
        "benchmark": "DALUB-v1",
        "version": raw_suite.get("version", "2.0.0"),
        "split": split,
        "total_cases": total_cases,
        "summary": summary,
    }
    receipt_hash = digest(receipt_payload)

    return {
        "schema_version": 2,
        **receipt_payload,
        "title": "محاكاة درجات DALUB: بلا مقارنة نماذج أو اعتماد",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": str(duration_s),
        "receipt_hash": receipt_hash,
        "receipt_scope": "suite_and_simulation_summary_not_unique_run_identity",
        "by_pillar": by_pillar,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an explicitly labeled DALUB score simulation; no models are compared")
    parser.add_argument("--simulation", action="store_true", help="acknowledge synthetic baselines and non-certification")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--split", choices=["all", "dev", "held_out"], default="all")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.simulation:
        parser.error("--simulation is required: this tool does not measure model comparisons or certify quality")
    out_path = Path(args.out)
    if (out_path.resolve() == HISTORICAL_REPORT.resolve()
            or (out_path.exists() and HISTORICAL_REPORT.exists()
                and out_path.samefile(HISTORICAL_REPORT))):
        parser.error("historical_report_protected: write a separate simulation under var/")
    try:
        report = run_dalub_three_arm_benchmark(Path(args.suite), split=args.split, simulation=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("simulation_only — لا استدعاءات نماذج؛ درجات اصطناعية، ولا اعتماد أو دليل تفوق.")
        print(f"\n=== {report['title']} (الإصدار {report['version']} — الشطر: {report['split']}) ===")
        print(f"إجمالي القضايا: {report['total_cases']} | الزمن: {report['duration_seconds']} ثانية | البصمة: {report['receipt_hash'][:16]}…")
        print("\n" + "-" * 75)
        print(f"{'الذراع (Arm)':<18} | {'الاجتياز':<12} | {'الصحة':<8} | {'الاكتمال':<8} | {'العزو':<8} | {'الهلوسة':<8} | {'الإجمالي':<8}")
        print("-" * 75)
        for arm, st in report["summary"].items():
            pass_str = f"{st['passed_cases']}/{st['total_cases']} ({st['accuracy_str']})"
            print(f"{arm:<18} | {pass_str:<12} | {st['factuality_bp']:>5} bp | {st['completeness_bp']:>5} bp | {st['attribution_bp']:>5} bp | {st['hallucination_penalty_bp']:>5} bp | {st['overall_bp']:>5} bp")
        print("-" * 75)
        print(f"تم حفظ تقرير المحاكاة في: {out_path}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
