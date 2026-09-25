"""اختبارات بنك الجودة المقيس م١٤ (Three-Arm Benchmark Tests)."""
import json
import pytest
from pathlib import Path

from core.acquisitions import SourceRegister
from core.budget import Budget
from core.contracts import Response, Usage
from core.ledger import Ledger
from evaluation.benchmark_arms import ModelAloneArm, NaiveRagArm, DiwanFullArm
from evaluation.benchmark_metrics import (
    detect_contradictions,
    evaluate_factuality,
    evaluate_completeness,
    evaluate_attribution,
    evaluate_translation_fidelity,
    evaluate_case_response,
)
from evaluation.benchmark_runner import BenchmarkRunner
from tests.private_stores import needs_m14_suite

ROOT = Path(__file__).resolve().parent.parent


class MockBenchmarkProvider:
    model = "mock-benchmark-model"
    is_local = True

    def __init__(self, response_text="إجابة تجريبية"):
        self.response_text = response_text

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        return Response(
            self.response_text,
            Usage(20, 10),
            "complete",
            0,
            provider="mock",
            model_version="v1"
        )


@needs_m14_suite
def test_benchmark_m14_suite_schema_and_count():
    suite_path = ROOT / "evaluation" / "suites" / "benchmark_m14.json"
    assert suite_path.exists()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))

    assert suite["suite_id"] == "quality_benchmark_v1"
    assert suite["total_cases"] == 40
    assert len(suite["cases"]) == 40

    reg = SourceRegister(ROOT / "sources" / "acquisitions.jsonl")
    current_sources = reg.current()

    for c in suite["cases"]:
        assert c["case_id"].startswith("bm14_")
        assert c["domain"] in ("maritime", "lexicon", "translation")
        assert len(c["question"]) > 10
        gt = c["ground_truth"]
        assert len(gt["literal_text"]) > 10
        assert len(gt["golden_facts"]) >= 1
        assert len(gt["facets"]) >= 1
        # التحقق من أن المصدر مسجل في سجل الأصول
        assert gt["source_id"] in current_sources


def test_factuality_metric():
    golden_facts = ["30 طن", "20 متر", "المملكة"]
    
    # تطابق كامل
    ans_perfect = "الحد الأقصى هو 30 طن وطول 20 متر في المملكة."
    res = evaluate_factuality(ans_perfect, golden_facts)
    assert res["score"] == 1.0
    assert len(res["matched"]) == 3

    # تطابق جزئي
    ans_partial = "الحمولة 30 طن فقط."
    res_part = evaluate_factuality(ans_partial, golden_facts)
    assert res_part["score"] == round(1 / 3, 3)
    assert "20 متر" in res_part["missing"]

    # لا تطابق
    ans_none = "معلومات عامة لا علاقة لها بالسفن."
    res_none = evaluate_factuality(ans_none, golden_facts)
    assert res_none["score"] == 0.0


def test_completeness_metric():
    facets = ["حمولة وطول وحدة الصيد", "حمولة وطول سفينة الصيد"]
    
    ans = "وحدة الصيد محددة بالحمولة والطول، وكذلك سفينة الصيد لها حمولة وطول."
    res = evaluate_completeness(ans, facets)
    assert res["score"] == 1.0

    ans_empty = "جواب مقتضب جداً."
    res_empty = evaluate_completeness(ans_empty, facets)
    assert res_empty["score"] == 0.0


def test_attribution_metric_q26():
    # النموذج وحده: بلا شواهد => إسناد 0.0
    res_alone = evaluate_attribution("ادعاء غير مسند إطلاقاً", pages_by_ref=None)
    assert res_alone["score"] == 0.0

    # شواهد مع إسناد سليم
    pages = {
        1: {"text": "كل إنسان فان لا محالة وفق قوانين الطبيعة.", "part": "كتاب", "locus": "ص1"}
    }
    ans_supported = "كل إنسان فان لا محالة [ش1]."
    res_sup = evaluate_attribution(ans_supported, pages)
    assert res_sup["score"] == 1.0
    assert res_sup["supported_claims"] == 1

    # شواهد مع رقم غير مسند في الشاهد
    ans_unsupported = "هذا ينطبق على 99 حالة مختلفة [ش1]."
    res_unsup = evaluate_attribution(ans_unsupported, pages)
    assert res_unsup["score"] == 0.0
    assert res_unsup["unsupported_claims"] == 1


def test_translation_fidelity_metric():
    terms = [
        {"ar": "السفينة", "foreign": "ship"},
        {"ar": "الرحلة الدولية", "foreign": "international voyage"}
    ]

    # التزام تام بالمصطلحات
    ans_good = "أبحرت السفينة في رحلة دولية جديدة."
    res_good = evaluate_translation_fidelity(ans_good, terms)
    assert res_good["score"] == 1.0

    # إخفاق في مصطلح
    ans_bad = "أبحرت الباخرة في رحلة خارج البلاد."
    res_bad = evaluate_translation_fidelity(ans_bad, terms)
    assert res_bad["score"] == 0.0


@needs_m14_suite
def test_benchmark_producer_cannot_certify_or_accept_legacy_human_file(tmp_path):
    from evaluation.benchmark_runner import BenchmarkIncompleteError
    provider = MockBenchmarkProvider("جواب تجريبي")
    runner = BenchmarkRunner(ROOT, provider, out_dir=tmp_path / "benchmarks")
    res = runner.run_benchmark(limit=2)
    assert res["status"] == "pending_automated_review"
    assert res["human_review_verified"] is False
    assert len(res["reference_cases"]) == len(res["results"]) == 2
    review_file = tmp_path / "human_review.json"
    review_file.write_text(json.dumps({"reviewer": "حسين", "attestation": True,
        "reviews": {c["case_id"]: {"verdict": "pass"} for c in res["results"]}}))
    with pytest.raises(BenchmarkIncompleteError, match="legacy_human_review_rejected"):
        runner.run_benchmark(limit=2, human_review_file=review_file)
    diagnostic = runner.run_benchmark(limit=1, require_human_review=False)
    assert diagnostic["status"] == "diagnostic_only"
    assert diagnostic["human_review_verified"] is False


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"offset": -1}, {"domain": "nonexistent"},
    {"arms": ("diwan_full", "diwan_full"), "diagnostic_single_arm": True},
    {"arms": ("invented",), "diagnostic_single_arm": True},
])
@needs_m14_suite
def test_invalid_comparison_cannot_be_reported(tmp_path, kwargs):
    from evaluation.benchmark_runner import BenchmarkIncompleteError
    runner = BenchmarkRunner(ROOT, MockBenchmarkProvider("x"), out_dir=tmp_path)
    with pytest.raises(BenchmarkIncompleteError):
        runner.run_benchmark(**kwargs)


def test_hallucination_penalty_metric():
    golden_facts = ["200 ميل", "المملكة"]
    
    # إجابة تذكر رقماً مناقضاً (3 أميال بدل 200 ميل)
    ans_hallucinated = "يسمح بالتفريغ على بعد 3 أميال من ساحل المملكة."
    res = evaluate_factuality(ans_hallucinated, golden_facts)
    # يجب أن تُرصد كعقوبة هلوسة
    assert res["hallucination_penalty"] > 0.0
    assert len(res["contradictions"]) >= 1
    assert "تناقض عددي" in res["contradictions"][0]
    # النتيجة الصافية تُخصم منها العقوبة
    assert res["score"] < res["raw_score"]


def test_refusal_separation_metric():
    case = {
        "case_id": "bm14_test",
        "ground_truth": {
            "golden_facts": ["30 طن"],
            "facets": ["حمولة"],
            "glossary_terms": [],
        }
    }
    # حالة امتناع صريح
    ans_refusal = "تعذر إكمال طلب ديوان: العقدة أعلنت العجز: الشواهد غير كافية"
    res = evaluate_case_response(ans_refusal, case, None, abstained=True, error_code="insufficient_evidence")
    assert res["abstained"] is True
    assert res["factuality"] is None
    assert res["completeness"] is None
    assert res["overall_score"] is None


def test_glossary_applicability_fix():
    # عند غياب المسرد، لا يُمنح 100% وهمية
    res_empty = evaluate_translation_fidelity("أي كلام عربي", glossary_terms=[])
    assert res_empty["applicable"] is False
    assert res_empty["score"] is None

    # عند وجود المسرد، يُقاس الالتزام
    terms = [{"ar": "الربان", "foreign": "master"}]
    res_present = evaluate_translation_fidelity("قام الربان بتوجيه السفينة", glossary_terms=terms)
    assert res_present["applicable"] is True
    assert res_present["score"] == 1.0


@needs_m14_suite
def test_q42_triple_arm_lock_enforcement(tmp_path):
    from evaluation.benchmark_runner import BenchmarkIncompleteError
    provider = MockBenchmarkProvider("جواب تجريبي")
    runner = BenchmarkRunner(ROOT, provider, out_dir=tmp_path / "benchmarks")

    # محاولة تشغيل ذراعين فقط دون وضع التشخيص الفردي => خرق ق٤٢ ورمي استثناء
    with pytest.raises(BenchmarkIncompleteError):
        runner.run_benchmark(limit=1, arms=("model_alone", "naive_rag"), diagnostic_single_arm=False)

    # بالسماح الصريح بالتشخيص الفردي => يمر
    res_diag = runner.run_benchmark(limit=1, arms=("diwan_full",), diagnostic_single_arm=True, require_human_review=False)
    assert "diwan_full" in res_diag["summary"]


def test_translation_facet_evaluation():
    facets = [
        "تعريب ship إلى السفينة",
        "تعريب international voyage إلى الرحلة الدولية",
    ]
    ans = "غادرت السفينة في رحلة دولية."
    res = evaluate_completeness(ans, facets)
    assert res["score"] == 1.0
    assert len(res["matched"]) == 2


def test_parenthetical_facet_evaluation_with_affixes():
    facets = [
        "جمع التكسير (ربابنة)",
        "المكافئ الشائع (العنبر)",
        "حمولة وطول وحدة الصيد",
    ]
    ans = "وجمعه ربابنة، ومكافئه هو العنبر، وحمولتها وطولها لوحدة الصيد مطابقان للشروط."
    res = evaluate_completeness(ans, facets)
    assert res["score"] == 1.0


def test_factuality_number_unit_normalization():
    golden_facts = ["30 طن", "20 متر"]
    ans = "تزيد حمولتها عن ثلاثين (30) طناً ويزيد طولها عن عشرين (20) متراً."
    res = evaluate_factuality(ans, golden_facts)
    assert res["score"] == 1.0
    assert len(res["matched"]) == 2
    assert res["hallucination_penalty"] == 0.0


def test_comparison_not_penalized_as_contradiction():
    golden_facts = ["30 طن", "20 متر", "لا تزيد"]
    # الإجابة توضح أن سفينة الصيد تزيد ووحدة الصيد لا تزيد
    ans = "سفينة الصيد تزيد عن 30 طناً، ووحدة الصيد لا تزيد عن 30 طناً."
    res = evaluate_factuality(ans, golden_facts)
    assert res["hallucination_penalty"] == 0.0
    assert len(res["contradictions"]) == 0




# ————— ك٤: قلبُ المعنى لا يمرّ مرورَ الصدى اللفظي —————

# الحالةُ المرصودة في docs/EVALUATION-20260923.md (العثرة ب): نصُّ النظام يوجب
# الإبلاغ، والجوابُ ينفيه بحرفٍ واحد — ونال بذلك ١٠٠٪ في الأبعاد الأربعة كلِّها،
# لأن المطابقةَ بالاحتواء لا ترى النفيَ الذي يسبق الجملة.
REPORTING_DUTY = "يجب على الربان إبلاغ السلطة البحرية عن أي حادث تصادم خلال 24 ساعة من وقوعه"


def test_an_answer_that_inverts_a_golden_fact_is_not_a_perfect_answer():
    """الجوابُ المقلوبُ معناه كان ينال درجةَ الجواب الصحيح بحذافيرها."""
    case = {"ground_truth": {"golden_facts": [REPORTING_DUTY],
                             "facets": ["إبلاغ السلطة البحرية", "خلال 24 ساعة"]}}
    pages = {1: {"text": REPORTING_DUTY}}

    right = evaluate_case_response(f"{REPORTING_DUTY} [ش1]", case, pages)
    wrong = evaluate_case_response(f"لا {REPORTING_DUTY} [ش1]", case, pages)

    assert right["overall_score"] == 1.0
    assert right["meaning_inverted"] == []
    # المقلوبُ عيبٌ مُسقِط لا خصمٌ من درجة: لا ٥٥٪ ولا ١٠٠٪.
    assert wrong["overall_score"] == 0.0
    assert wrong["meaning_inverted"] == [REPORTING_DUTY]
    assert wrong["factuality"]["score"] == 0.0
    assert REPORTING_DUTY in wrong["factuality"]["missing"]
    assert REPORTING_DUTY not in wrong["factuality"]["matched"]


@pytest.mark.parametrize("particle", ["لا", "ولا", "فلا", "ليس", "لم", "لن"])
def test_every_listed_negation_particle_flips_the_verdict(particle):
    res = evaluate_factuality(f"{particle} {REPORTING_DUTY}", [REPORTING_DUTY])
    assert res["score"] == 0.0
    assert res["inverted"] == [REPORTING_DUTY]


@pytest.mark.parametrize("answer", [
    # ١. الجوابُ الصحيح نفسُه.
    REPORTING_DUTY,
    # ٢. نفيٌ في شقٍّ آخر من الجواب لا يمسّ الجملةَ الذهبية.
    f"لا يُعفى الربان من شيء. {REPORTING_DUTY}",
    # ٣. جوابٌ ينفي ثم يستدرك: أثبت الحكمَ في أحد مواضعه، فلا يُحاسَب حسابَ من نفاه.
    f"لا {REPORTING_DUTY} — هذا خطأ؛ بل {REPORTING_DUTY}",
    # ٤. أداةُ النفي حاضرةٌ لكنها لا تلاصق الجملة.
    f"لا شكّ أنه {REPORTING_DUTY}",
])
def test_a_correct_answer_is_never_accused_of_inverting(answer):
    """الحارسُ يُخطئ في اتجاهين، وهذا يمنع اتجاهَه الثاني."""
    res = evaluate_factuality(answer, [REPORTING_DUTY])
    assert res["inverted"] == []
    assert res["score"] == 1.0


def test_a_golden_fact_that_is_itself_negative_is_not_read_as_inverted():
    """«لا يجوز» في النص الذهبي نفسِه ليس قلبًا حين يردّده الجواب."""
    fact = "لا يجوز للربان مغادرة الميناء قبل التفتيش"
    res = evaluate_factuality(f"وفق النظام، {fact}.", [fact])
    assert res["inverted"] == []
    assert res["score"] == 1.0


def test_inversion_is_named_in_the_contradictions_not_only_scored():
    """الرقمُ وحده لا يكفي: القارئ يحتاج أن يعرف أيَّ حكمٍ قُلب وبأيّ أداة."""
    found = detect_contradictions(f"لا {REPORTING_DUTY}", [REPORTING_DUTY])
    assert any("قلبُ معنى" in c and "«لا»" in c for c in found)
