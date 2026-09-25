"""القطبيةُ الحُكمية في إعادة الصياغة (ك٢٦): القلبُ المُعادُ صياغتُه لا يمرّ، والصحيحُ لا يُتَّهم.

الحقيقةُ الذهبية تحمل حكمًا (وجوب/حظر/إباحة)، والجوابُ قد يقلبه بغير نصّها: «يُحظر»
بدل «يجب»، «غير ملزم»، «لا يجب … أن». يُقرأ الحكمُ في الحقيقة وفي جُمل الجواب التي
تتداخل معها؛ فإن غابت قطبيةُ الحقيقة وحضرت قطبيةٌ تناقضها فالحكمُ مقلوب. والحدود
معلَنة اختبارًا: قلبٌ بلا علامةِ حكمٍ يفلت، وحقيقةٌ بلا حكمٍ خارج الكشف.
"""
from __future__ import annotations

import pytest

from evaluation.benchmark_metrics import (deontic_polarities, detect_contradictions, evaluate_case_response,
                                          evaluate_factuality, inverted_facts, paraphrased_inversions)

DUTY = "يجب على الربان إبلاغ السلطة البحرية قبل دخول الميناء"
BAN = "لا يجوز للربان مغادرة الميناء قبل التفتيش"
CASE = {"ground_truth": {"golden_facts": [DUTY], "facets": ["إبلاغ السلطة البحرية"], "glossary_terms": []}}


def score(answer, facts):
    return evaluate_factuality(answer, facts)["score"]


# ————— القلبُ المُعادُ صياغتُه يسقط، ويُسمّى —————

@pytest.mark.parametrize("answer,marker", [
    ("يُحظر على الربان إبلاغ السلطة البحرية قبل دخول الميناء.", "يحظر"),
    ("الربان غير ملزم بإبلاغ السلطة البحرية قبل دخول الميناء.", "غير ملزم"),
    ("لا يجب على الربان أن يُبلغ السلطة البحرية قبل دخول الميناء.", "لا يجب"),
    ("ليس الربان ملزماً بإبلاغ السلطة البحرية قبل دخول الميناء.", "ليس ملزما"),
    ("يُمنع على الربان إبلاغ السلطة البحرية قبل دخول الميناء.", "يمنع"),
])
def test_a_paraphrased_inversion_of_an_obligation_is_named_and_zeroed(answer, marker):
    assert paraphrased_inversions(answer, [DUTY]) == [(DUTY, marker)]
    assert inverted_facts(answer, [DUTY]) == [(DUTY, marker)]
    assert score(answer, [DUTY]) == 0.0
    assert any("قلبُ معنى" in c and f"«{marker}»" in c for c in detect_contradictions(answer, [DUTY]))
    result = evaluate_case_response(answer, CASE, None)
    assert result["overall_score"] == 0.0 and result["meaning_inverted"] == [DUTY]


@pytest.mark.parametrize("answer", [
    "يجوز للربان مغادرة الميناء قبل التفتيش.",
    "مسموح للربان بمغادرة الميناء قبل التفتيش.",
    "لا يُحظر على الربان مغادرة الميناء قبل التفتيش.",
])
def test_a_prohibition_inverted_into_a_permission_is_refused(answer):
    assert score(answer, [BAN]) == 0.0 and inverted_facts(answer, [BAN])


# ————— الصحيحُ لا يُتَّهم —————

@pytest.mark.parametrize("answer", [
    "على الربان أن يُبلغ السلطة البحرية قبل أن يدخل الميناء.",                       # بلا علامةٍ صريحة
    "يتعين على الربان إبلاغ السلطة البحرية قبل دخول الميناء.",                        # مرادفُ الوجوب
    "يجب على الربان إبلاغ السلطة البحرية قبل دخول الميناء؛ ولا يجوز له الرسو قبل ذلك.",  # حظرٌ لفعلٍ آخر
    f"لا {DUTY} — هذا خطأ؛ بل {DUTY}",                                                # يستدرك
    f"لا شكّ أنه {DUTY}",
    "لا يُعفى الربان من شيء. " + DUTY,
    # حظرٌ في جملةٍ لا تتداخل مع الحقيقة لا يُقرأ عليها
    "على الربان أن يُبلغ السلطة البحرية قبل أن يدخل الميناء. ولا يجوز التدخين على المتن.",
])
def test_a_correct_answer_keeps_its_score_whatever_its_wording(answer):
    assert inverted_facts(answer, [DUTY]) == [] and score(answer, [DUTY]) == 1.0


@pytest.mark.parametrize("answer", [
    f"وفق النظام، {BAN}.",
    "يُمنع الربان من مغادرة الميناء قبل التفتيش.",
    "محظور على الربان مغادرة الميناء قبل التفتيش.",
])
def test_a_negative_fact_restated_as_a_prohibition_is_not_inverted(answer):
    assert inverted_facts(answer, [BAN]) == [] and score(answer, [BAN]) == 1.0


def test_numeric_facts_without_a_deontic_marker_are_outside_this_detector():
    facts = ["30 طن", "20 متر", "لا تزيد"]
    answer = "سفينة الصيد تزيد عن 30 طناً، ووحدة الصيد لا تزيد عن 30 طناً."
    assert paraphrased_inversions(answer, facts) == []
    assert evaluate_factuality(answer, facts)["hallucination_penalty"] == 0.0


# ————— الحدودُ المعلَنة —————

def test_limit_an_inversion_without_any_deontic_marker_escapes():
    """«معفًى من الإبلاغ» يقلب الحكمَ بلا علامةِ وجوبٍ ولا حظر؛ فلا قطبيةَ تُقرأ له، فيفلت.
    إغلاقُه يسقط هذا الاختبار عمدًا فيُنقل إلى المُغلَق."""
    answer = "الربان معفى من إبلاغ السلطة البحرية قبل دخول الميناء."
    assert paraphrased_inversions(answer, [DUTY]) == []
    assert score(answer, [DUTY]) == 1.0


def test_limit_a_fact_with_mixed_polarities_is_not_judged():
    fact = "يجب الإبلاغ ولا يجوز التأخير عن 24 ساعة"
    assert paraphrased_inversions("لا يجب الإبلاغ ويجوز التأخير عن 24 ساعة", [fact]) == []


# ————— القارئُ الحُكمي —————

def test_deontic_polarities_read_negation_immediately_before_the_marker():
    assert deontic_polarities("الربان غير ملزم بالإبلاغ") == [("not_obliged", "غير ملزم")]
    assert deontic_polarities(BAN) == [("prohibition", "لا يجوز")]
    assert deontic_polarities("يجب الإبلاغ ولا يحظر الدخول") == [("obligation", "يجب"), ("not_prohibited", "ولا يحظر")]
    assert deontic_polarities("ليس الربان ملزماً بذلك") == [("not_obliged", "ليس ملزما")]
    assert deontic_polarities("ليس هذا هو الحال المعتاد، يجب الإبلاغ") == [("obligation", "يجب")]
    assert deontic_polarities("الطقس صحو") == []
