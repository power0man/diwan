"""الجوابُ الصحيحُ المُعادُ صياغتُه يُقبل — ولو خلت الحقيقةُ الذهبية من رقم.

كان مسارُ التداخل اللفظي (‎0.5) مُعشّشًا داخل شرط الأرقام، فالحقيقةُ الخاليةُ
من رقمٍ لا يُطابقُها إلا النصُّ الحرفيّ. والنماذجُ تُجيب بصياغتها هي، فكان
الجوابُ الصحيحُ يسقط إلى صفر لأنه لم يَنسخ — ويَنجو متى حوت الحقيقةُ رقمًا
يرد في الجواب. فنجاتُه **مصادفةٌ في صياغة الحقيقة، لا حكمٌ على صحّته**، وقياسُ
أي محرّكٍ بهذا يقيس تطابقَ الألفاظ لا الصحّة (المهمّة ك١٠).

وتوسيعُ القبول لا يجوز أن يصير قبولًا للخطأ، فهذه الاختبارات تشدّ الطرفين:
الصحيحُ المُعادُ صياغتُه يمرّ، والمقلوبُ والأجنبيُّ وناقصُ الرقم يسقطون.
"""
from __future__ import annotations

import pytest

from evaluation.benchmark_metrics import evaluate_factuality

# حقيقةٌ بلا رقم — هي موضعُ العطب
FACT_NO_NUMBER = "يجب على الربان إبلاغ السلطة البحرية قبل دخول الميناء"
# حقيقةٌ فيها رقم — كانت تنجو بالمصادفة
FACT_WITH_NUMBER = "تسري الشهادة مدة 5 سنوات من تاريخ إصدارها"


def score(answer: str, facts: list[str]) -> float:
    return evaluate_factuality(answer, facts, ground_truth=None)["score"]


def test_correct_paraphrase_of_a_numberless_fact_is_credited():
    """جوهرُ ك١٠: المعنى نفسُه بألفاظٍ أخرى."""
    answer = "على الربان أن يُبلغ السلطة البحرية قبل أن يدخل الميناء."
    assert score(answer, [FACT_NO_NUMBER]) == 1.0


def test_a_numbered_fact_still_requires_its_number():
    """التوسيعُ لا يرفع شرطَ الرقم: مدّةٌ محذوفة تغيّر الحكم."""
    assert score("تسري الشهادة مدة سنوات من تاريخ إصدارها", [FACT_WITH_NUMBER]) == 0.0
    assert score("تسري الشهادة مدة 5 سنوات من تاريخ إصدارها", [FACT_WITH_NUMBER]) == 1.0


def test_a_wrong_number_is_not_credited():
    assert score("تسري الشهادة مدة 9 سنوات من تاريخ إصدارها", [FACT_WITH_NUMBER]) == 0.0


def test_an_unrelated_answer_is_not_credited():
    """لولا هذا لكان التوسيعُ بابًا لقبول أي نصّ."""
    assert score("الطقس اليوم صحوٌ ومناسب للإبحار في البحر.", [FACT_NO_NUMBER]) == 0.0


def test_a_verbatim_inversion_is_still_refused():
    """القلبُ الحرفيّ (الحقيقةُ بنصّها مسبوقةً بأداة نفي) يسبق التداخلَ في الحكم.

    كان اسمُ هذا الاختبار «inverted paraphrase» ومحتواه الحقيقةَ الحرفيةَ بـ«لا»،
    فكان يحرس أقلَّ مما يدّعي. والقلبُ المُعادُ صياغتُه حدٌّ معلَنٌ في الاختبار التالي.
    """
    answer = "لا يجب على الربان إبلاغ السلطة البحرية قبل دخول الميناء."
    assert score(answer, [FACT_NO_NUMBER]) == 0.0


@pytest.mark.parametrize("answer", [
    "يُحظر على الربان إبلاغ السلطة البحرية قبل دخول الميناء.",
    "الربان غير ملزم بإبلاغ السلطة البحرية قبل دخول الميناء.",
    "لا يجب على الربان أن يُبلغ السلطة البحرية قبل دخول الميناء.",
])
def test_a_paraphrased_inversion_is_refused(answer):
    """كان حدًّا معلَنًا (`test_limit_…`، ك٢٦) وأُغلق: القطبيةُ الحُكمية تُقرأ في إعادة الصياغة.

    كاشفُ القلب (ك٤) لا يرى إلا الحقيقةَ بنصّها مسبوقةً بأداة نفي، ومسارُ التداخل ≥0.5
    (ك١٠) كان يمرّر «يُحظر» و«غير ملزم» و«لا يجب … أن يُبلغ» بدرجةٍ كاملة. صار الحكمُ
    الحُكميّ (وجوب/حظر/إباحة ونفيُها) يُقرأ في الحقيقة وفي جُمل الجواب المتداخلة معها.
    """
    assert score(answer, [FACT_NO_NUMBER]) == 0.0


@pytest.mark.parametrize("fact", [FACT_NO_NUMBER, FACT_WITH_NUMBER])
def test_the_verbatim_answer_stays_correct(fact):
    """ما كان يمرّ بحقٍّ يبقى يمرّ."""
    assert score(fact, [fact]) == 1.0


def test_survival_no_longer_depends_on_whether_the_fact_has_a_number():
    """الحكمُ الجوهريّ: صياغةُ الحقيقة لم تعد تقرّر مصيرَ الجواب الصحيح.

    عبارتان بالمعنى نفسه، إحداهما بلا رقم — ولا يجوز أن تفترق درجتاهما.
    """
    numberless = score("على الربان أن يُبلغ السلطة البحرية قبل أن يدخل الميناء.",
                       [FACT_NO_NUMBER])
    numbered = score("تبقى الشهادة سارية 5 سنوات ابتداءً من تاريخ إصدارها.",
                     [FACT_WITH_NUMBER])
    assert numberless == numbered == 1.0
