"""العطبُ لا يُقرأ امتناعًا في مقياس م١٤ (ك٢١).

كان أيُّ `error_code` يصير `abstained=True` فيدخل انقطاعُ المزوّد ومهلتُه وعطبُ الفهرس
معدّلَ الامتناع «حكمةً»، والملخّصُ بلا حقلِ أخطاء: ثلاثةُ أعطالٍ وجوابٌ صحيح = امتناعٌ ٠٫٧٥.
وكان عطبُ FTS في الذراع الساذج يعود «لا مستندات» بلا رمز فيُسأل النموذجُ بلا استرجاع.
وهنا ثلاثُ حالات: جوابٌ يُقاس، وامتناعٌ قرارُ النظام، وعطبٌ يخرج من المقامَين.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.budget import Budget
from core.contracts import Response, Usage
from core.ledger import Ledger
from evaluation import benchmark_arms
from evaluation.benchmark_arms import DiwanFullArm, ModelAloneArm, NaiveRagArm, NaiveSearchFailed
from evaluation.benchmark_metrics import evaluate_case_response
from evaluation.benchmark_runner import STANDARD_ARMS, summarize_scores
from evaluation.benchmark_review import validate_benchmark_artifact
from providers.base import ProviderError
from tests.test_benchmark_review_binding import reference_case, synthetic_report

ROOT = Path(__file__).resolve().parents[1]
CASE = {"case_id": "bm14_probe", "domain": "maritime",
        "ground_truth": {"golden_facts": ["30 طن"], "facets": ["حمولة"], "glossary_terms": []}}


class Scripted:
    model, is_local, name = "scripted", True, "scripted"

    def __init__(self, text="الحمولة 30 طن.", error=None):
        self.text, self.error, self.calls = text, error, 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Response(self.text, Usage(1, 1), "complete", 0, provider="scripted", model_version="v1")


def fresh(tmp_path):
    return Budget(1000, 1000), Ledger(tmp_path / "ledger.jsonl")


# ————— المقياس: ثلاثُ حالات —————

def test_an_error_code_alone_is_an_error_not_an_abstention():
    metrics = evaluate_case_response("", CASE, None, error_code="timeout")
    assert (metrics["errored"], metrics["abstained"], metrics["error_code"]) == (True, False, "timeout")
    assert metrics["overall_score"] is None and metrics["factuality"] is None


def test_the_abstained_flag_keeps_its_code_as_a_declared_reason():
    metrics = evaluate_case_response("", CASE, None, abstained=True, error_code="insufficient_evidence")
    assert (metrics["errored"], metrics["abstained"], metrics["error_code"]) == (False, True, "insufficient_evidence")


@pytest.mark.parametrize("answer", ["", "الشواهد غير كافية للجواب", "تعذر إكمال طلب ديوان: كذا"])
def test_refusal_text_without_a_code_is_an_abstention(answer):
    metrics = evaluate_case_response(answer, CASE, None)
    assert (metrics["errored"], metrics["abstained"], metrics["error_code"]) == (False, True, "refusal")


def test_a_measured_answer_is_neither():
    metrics = evaluate_case_response("الحمولة 30 طن.", CASE, None)
    assert (metrics["errored"], metrics["abstained"]) == (False, False) and metrics["overall_score"] > 0


# ————— الملخّص: المقامُ ما قِيس —————

def test_three_errors_and_one_correct_answer_are_not_a_seventy_five_percent_refusal():
    metrics = [evaluate_case_response("", CASE, None, error_code="timeout") for _ in range(3)]
    metrics.append(evaluate_case_response("الحمولة 30 طن.", CASE, None))
    [arm] = summarize_scores({"diwan_full": metrics}).values()
    assert (arm["total_cases"], arm["error_count"], arm["measured_count"]) == (4, 3, 1)
    assert (arm["answered_count"], arm["abstained_count"]) == (1, 0)
    assert (arm["answered_rate"], arm["refusal_rate"], arm["error_rate"]) == (1.0, 0.0, 0.75)
    assert arm["effective_overall"] == arm["mean_overall"]


def test_all_errors_leave_the_rates_at_zero_not_a_division_by_zero():
    [arm] = summarize_scores({"x": [evaluate_case_response("", CASE, None, error_code="down")]}).values()
    assert (arm["measured_count"], arm["answered_rate"], arm["refusal_rate"], arm["error_rate"]) == (0, 0.0, 0.0, 1.0)


def test_legacy_metrics_without_the_errored_key_still_summarize():
    legacy = evaluate_case_response("الحمولة 30 طن.", CASE, None)
    legacy.pop("errored")
    [arm] = summarize_scores({"x": [legacy]}).values()
    assert (arm["error_count"], arm["answered_count"]) == (0, 1)


def test_a_report_with_an_errored_arm_output_validates_and_recomputes_consistently():
    report = synthetic_report()
    output = report["results"][0]["arms"]["naive_rag"]
    output.update(answer="", abstained=False, error_code="naive_index_missing")
    output["metrics"] = evaluate_case_response("", report["reference_cases"][0], {},
                                               abstained=False, error_code="naive_index_missing")
    report["summary"] = summarize_scores({arm: [report["results"][0]["arms"][arm]["metrics"]]
                                          for arm in STANDARD_ARMS})
    assert report["summary"]["naive_rag"]["error_count"] == 1
    assert report["summary"]["naive_rag"]["abstained_count"] == 0
    validate_benchmark_artifact(report)


# ————— الأذرع: العطبُ رمزٌ والامتناعُ قرار —————

def test_a_provider_outage_in_model_alone_is_an_error_with_no_abstention(tmp_path):
    budget, ledger = fresh(tmp_path)
    arm = ModelAloneArm(Scripted(error=ProviderError("unreachable", "لا يستجيب", retryable=False)), budget, ledger)
    out = arm.run("ما الحمولة؟", CASE)
    assert out["error_code"] and out["abstained"] is False


def test_an_empty_model_answer_is_an_abstention_not_an_error(tmp_path):
    budget, ledger = fresh(tmp_path)
    out = ModelAloneArm(Scripted(text=""), budget, ledger).run("ما الحمولة؟", CASE)
    assert (out["error_code"], out["abstained"]) == (None, True)


def test_a_missing_naive_index_is_a_named_error_and_the_model_is_not_asked(tmp_path):
    budget, ledger = fresh(tmp_path)
    provider = Scripted()
    arm = NaiveRagArm(tmp_path, provider, budget, ledger)        # جذرٌ بلا إسقاطات
    with pytest.raises(NaiveSearchFailed) as exc:
        arm._raw_search("شروط السلامة للسفن", "maritime")
    assert exc.value.code == "naive_index_missing"
    out = arm.run("شروط السلامة للسفن", CASE)
    assert (out["error_code"], out["abstained"], out["answer"], provider.calls) == ("naive_index_missing", False, "", 0)


def test_a_corrupt_naive_index_is_a_named_error_not_no_documents(tmp_path):
    budget, ledger = fresh(tmp_path)
    (tmp_path / "projections").mkdir()
    (tmp_path / "projections/maritime-fts.sqlite").write_bytes(b"not a database")
    arm = NaiveRagArm(tmp_path, Scripted(), budget, ledger)
    with pytest.raises(NaiveSearchFailed) as exc:
        arm._raw_search("شروط السلامة للسفن", "maritime")
    assert exc.value.code.startswith("naive_search_failed:")


def test_a_real_empty_result_is_still_no_documents(tmp_path):
    budget, ledger = fresh(tmp_path)
    (tmp_path / "projections").mkdir()
    con = sqlite3.connect(tmp_path / "projections/maritime-fts.sqlite")
    con.execute("CREATE VIRTUAL TABLE pages USING fts5(norm, doc_id UNINDEXED, part UNINDEXED, locus UNINDEXED, item_digest UNINDEXED)")
    con.commit(); con.close()
    arm = NaiveRagArm(tmp_path, Scripted(), budget, ledger)
    assert arm._raw_search("كلمات لا توجد في الفهرس", "maritime") == []
    out = arm.run("كلمات لا توجد في الفهرس", CASE)
    assert out["error_code"] is None and out["abstained"] is False


def test_diwan_full_separates_infrastructure_failure_from_declared_insufficiency(tmp_path, monkeypatch):
    budget, ledger = fresh(tmp_path)
    arm = DiwanFullArm(ROOT, Scripted(), budget, ledger)

    class Broken:
        def answer(self, question, data_policy):
            raise RuntimeError("فهرسٌ فاسد")

    class Empty:
        def answer(self, question, data_policy):
            raise LookupError("لا شواهد في المخزن")

    monkeypatch.setattr(arm, "maritime_node", Broken())
    broken = arm.run("ما الحمولة؟", CASE)
    assert (broken["error_code"], broken["abstained"]) == ("RuntimeError", False)
    monkeypatch.setattr(arm, "maritime_node", Empty())
    empty = arm.run("ما الحمولة؟", CASE)
    assert (empty["error_code"], empty["abstained"]) == (None, True)
    assert benchmark_arms.INSUFFICIENT in empty["answer"]
