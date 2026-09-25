"""مشغّل بنك الجودة المقيس م١٤:
- تشغيل الأذرع الثلاثة: (النموذج وحده، استرجاع ساذج، ديوان كاملاً).
- قياس الأبعاد الأربعة: (الصحة، الاكتمال، الإسناد، أمانة الاصطلاح).
- حفظ قياس غير معتمد؛ المراجعة الآلية المتعددة منفصلة ومرتبطة بالمخرجات نفسها (ق٤٩).
"""
from __future__ import annotations

import json
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from core.budget import Budget
from core.canonical import PayloadRejected
from core.ledger import Ledger
from evaluation.benchmark_arms import DiwanFullArm, ModelAloneArm, NaiveRagArm
from evaluation.benchmark_metrics import evaluate_case_response
from evaluation.multi_system_review import parse_json, sha256

DEFAULT_SUITE_PATH = Path(__file__).resolve().parent / "suites" / "benchmark_m14.json"


STANDARD_ARMS = ("model_alone", "naive_rag", "diwan_full")


class BenchmarkIncompleteError(PayloadRejected):
    """يُرمى عند محاولة نشر نتيجة قياس دون استيفاء شروط المراجعة أو خرق ق٤٢."""


def summarize_scores(scores_by_arm: dict[str, list[dict]]) -> dict[str, Any]:
    """Derive denominators and aggregates from per-case metrics, never stored totals.

    المقامُ ما قِيس لا ما حُوول (ك٢١): العطبُ (`errored`) يُعدّ ويُنشر رقمًا مستقلًّا
    ويخرج من مقامَي الإجابة والامتناع، فلا يرفع انقطاعُ مزوّدٍ «حكمةَ» الامتناع.
    """
    # حساب المتوسطات العامة لكل ذراع مع فصل الامتناع (ف٠) والعطب (ك٢١)
    summary: dict[str, Any] = {}
    for arm_name, metrics_list in scores_by_arm.items():
        if not metrics_list:
            continue
        n_total = len(metrics_list)
        errored_cases = [m for m in metrics_list if m.get("errored")]
        measured_cases = [m for m in metrics_list if not m.get("errored")]
        answered_cases = [m for m in measured_cases if not m["abstained"]]
        abstained_cases = [m for m in measured_cases if m["abstained"]]

        n_errors = len(errored_cases)
        n_measured = len(measured_cases)
        n_answered = len(answered_cases)
        n_abstained = len(abstained_cases)
        answered_rate = round(n_answered / n_measured, 3) if n_measured else 0.0
        refusal_rate = round(n_abstained / n_measured, 3) if n_measured else 0.0

        if n_answered > 0:
            mean_factuality = sum(m["factuality"]["score"] for m in answered_cases) / n_answered
            mean_completeness = sum(m["completeness"]["score"] for m in answered_cases) / n_answered
            mean_attribution = sum(m["attribution"]["score"] for m in answered_cases) / n_answered
            mean_hallucination = sum(m["hallucination_penalty"] for m in answered_cases) / n_answered
            mean_overall = sum(m["overall_score"] for m in answered_cases) / n_answered

            trans_scores = [
                m["translation"]["score"]
                for m in answered_cases
                if m["translation"] and m["translation"]["applicable"] and m["translation"]["score"] is not None
            ]
            mean_translation = round(sum(trans_scores) / len(trans_scores), 3) if trans_scores else None
        else:
            mean_factuality = 0.0
            mean_completeness = 0.0
            mean_attribution = 0.0
            mean_hallucination = 0.0
            mean_overall = 0.0
            mean_translation = None

        effective_overall = round(mean_overall * answered_rate, 3)

        summary[arm_name] = {
            "total_cases": n_total,
            "measured_count": n_measured,
            "error_count": n_errors,
            "error_rate": round(n_errors / n_total, 3),
            "answered_count": n_answered,
            "abstained_count": n_abstained,
            "answered_rate": answered_rate,
            "refusal_rate": refusal_rate,
            "mean_factuality": round(mean_factuality, 3),
            "mean_completeness": round(mean_completeness, 3),
            "mean_attribution": round(mean_attribution, 3),
            "mean_hallucination_penalty": round(mean_hallucination, 3),
            "mean_translation": mean_translation,
            "mean_overall": round(mean_overall, 3),
            "effective_overall": effective_overall,
        }

    return summary


class BenchmarkRunner:
    """مشغّل المقارنة الثلاثية الشامل لبنك الجودة المقيس م١٤ وفق ق٤٢."""

    def __init__(
        self,
        root: Path,
        provider,
        suite_path: Path | str | None = None,
        out_dir: Path | str | None = None,
    ):
        self.root = root
        self.provider = provider
        self.suite_path = Path(suite_path or DEFAULT_SUITE_PATH)
        self.out_dir = Path(out_dir or (self.root / "var" / "benchmarks" / "m14"))
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # One read fixes the measured bank even if its file changes during a run.
        self._suite_bytes = self.suite_path.read_bytes()
        self._suite_sha256 = hashlib.sha256(self._suite_bytes).hexdigest()
        self.suite = parse_json(self._suite_bytes)
        self.cases = self.suite.get("cases", [])

    def run_benchmark(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        domain: str | None = None,
        arms: tuple[str, ...] = ("model_alone", "naive_rag", "diwan_full"),
        human_review_file: Path | str | None = None,
        require_human_review: bool = True,
        diagnostic_single_arm: bool = False,
    ) -> dict[str, Any]:
        """تشغيل المقارنة عبر الأذرع المطلوبة لحالات البنك وفق ق٤٢."""
        # Legacy files cannot assert a person's review or certify a new run.
        if human_review_file is not None:
            raise BenchmarkIncompleteError("benchmark.review", "legacy_human_review_rejected",
                                           "ق٤٩: استخدم المراجعة الآلية على التقرير المحفوظ نفسه.")
        if (not arms or len(arms) != len(set(arms)) or
                not set(arms) <= set(STANDARD_ARMS)):
            raise BenchmarkIncompleteError("benchmark.arms", "invalid_arms", "أذرع مجهولة أو مكررة")
        if type(offset) is not int or offset < 0 or (limit is not None and
                (type(limit) is not int or limit < 1)):
            raise BenchmarkIncompleteError("benchmark.cases", "invalid_slice", "نطاق قضايا غير صالح")
        # فرض ق٤٢: منع المقارنة الناقصة للأذرع
        if not diagnostic_single_arm and set(arms) != set(STANDARD_ARMS):
            raise BenchmarkIncompleteError(
                "benchmark.arms",
                "incomplete_arms_q42",
                "ق٤٢ الحاكمة: يُرفض إصدار تقرير مقارنة ما لم تعمل الأذرع الثلاثة كاملة "
                "(model_alone, naive_rag, diwan_full) على المجموعة نفسها في التشغيلة نفسها. "
                "للتشغيل الفردي التشخيصي، مرر diagnostic_single_arm=True مع حجب توثيق تقرير المقارنة الرسمي."
            )

        # Reparse the frozen bytes: callers cannot mutate self.cases behind its hash.
        frozen_cases = parse_json(self._suite_bytes)["cases"]
        cases = [c for c in frozen_cases if domain is None or c.get("domain") == domain]
        cases_to_run = cases[offset : offset + limit] if limit is not None else cases[offset:]
        if not cases_to_run:
            raise BenchmarkIncompleteError("benchmark.cases", "empty_benchmark", "لا قضايا للقياس")
        run_id = f"run-m14-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

        # تهيئة دفاتر السجلات والميزانية لكل ذراع
        budget = Budget(100_000_000, 1_000_000_000)
        ledger = Ledger(self.out_dir / f"{run_id}-ledger.jsonl")

        arm_instances = {}
        if "model_alone" in arms:
            arm_instances["model_alone"] = ModelAloneArm(self.provider, budget, ledger)
        if "naive_rag" in arms:
            arm_instances["naive_rag"] = NaiveRagArm(self.root, self.provider, budget, ledger)
        if "diwan_full" in arms:
            arm_instances["diwan_full"] = DiwanFullArm(self.root, self.provider, budget, ledger)

        results_by_case = []
        scores_by_arm: dict[str, list[dict]] = {arm: [] for arm in arm_instances}

        for idx, case in enumerate(cases_to_run, 1):
            cid = case["case_id"]
            question = case["question"]
            print(f"[{idx}/{len(cases_to_run)}] تشغيل القضية {cid}: {question[:50]}...", flush=True)
            case_res: dict[str, Any] = {"case_id": cid, "question": question, "arms": {}}

            for arm_name, arm_runner in arm_instances.items():
                arm_out = arm_runner.run(question, case)
                eval_metrics = evaluate_case_response(
                    arm_out["answer"],
                    case,
                    arm_out["pages_by_ref"],
                    abstained=arm_out.get("abstained", False),
                    error_code=arm_out.get("error_code"),
                )
                case_res["arms"][arm_name] = {
                    "answer": arm_out["answer"],
                    "pages_by_ref": {str(k): v for k, v in (arm_out["pages_by_ref"] or {}).items()},
                    "latency_s": arm_out["latency_s"],
                    "error_code": arm_out.get("error_code"),
                    "abstained": arm_out.get("abstained", False),
                    "metrics": eval_metrics,
                }
                scores_by_arm[arm_name].append(eval_metrics)

            results_by_case.append(case_res)

        summary = summarize_scores(scores_by_arm)

        # Never certify in the producer. Review the frozen report in a later step.
        final_report = {
            "schema_version": 2,
            "run_id": run_id,
            "status": "diagnostic_only" if diagnostic_single_arm or not require_human_review else "pending_automated_review",
            "review_mode": "automated_multi_system",
            "human_review_verified": False,
            "product_readiness": "not_assessed",
            "provenance": {
                "suite_sha256": self._suite_sha256,
                "reference_cases_sha256": sha256(cases_to_run),
                "provider": getattr(self.provider, "name", type(self.provider).__name__),
                "model": getattr(self.provider, "model", "unknown"),
                "arms": list(arms), "offset": offset, "limit": limit, "domain": domain,
                "reference_exposure": "development_visible",
            },
            "measurement_limits": [
                "Lexical metrics are diagnostics, not proof of factual entailment.",
                "This development suite is not an independent held-out acceptance set.",
                "Model agreement cannot establish source truth or product readiness.",
            ],
            "summary": summary,
            "reference_cases": cases_to_run,
            "results": results_by_case,
        }
        res_path = self.out_dir / f"{run_id}-results.json"
        res_path.write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
        return final_report
