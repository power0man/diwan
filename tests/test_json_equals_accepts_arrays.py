"""مرجعُ `json_equals` يكون كائنًا أو قائمة — والمدقِّقُ لا يكون أصرمَ من مقارِنه.

`_json_equal` يقارن القوائم صراحةً، وكان `validate_suite` يردّها. فحالةُ
استخراجٍ مرجعُها قائمة — وهو أكثرُ ما يُطلب في الاستخراج — تُردّ بلا شرطٍ
في التكليف يسندها. وقِيس أثرُه: ٣٠ حالةً في بنك Kimi v1.1 رُدّت لهذا وحده.

والتوسيعُ محدود: القيمةُ وعاءُ JSON، لا نصٌّ ولا رقمٌ ولا null — فتلك أنواعٌ
لها فحوصُها (`exact` و`contains`).
"""
from __future__ import annotations

import pytest

from evaluation.capabilities import CapabilityError, validate_suite


def _suite(value) -> dict:
    return {
        "schema_version": 1, "suite_id": "fixture_v1", "split": "development",
        "description": "عيّنةٌ للاختبار.",
        "cases": [{
            "case_id": "c1", "capability": "استخراج", "reference": "قائمة",
            "critical": False, "rubric": ["صحيح"],
            "messages": [{"role": "user", "content": "استخرج الأسماء."}],
            "checks": [{"kind": "json_equals", "value": value}],
        }],
    }


def test_an_array_reference_is_accepted():
    assert validate_suite(_suite(["أحمد", "سارة"]))


def test_an_object_reference_is_still_accepted():
    assert validate_suite(_suite({"الأسماء": ["أحمد"]}))


@pytest.mark.parametrize("value", ["نصّ", 7, None, True])
def test_a_scalar_reference_is_still_refused(value):
    """الحدُّ باقٍ: النصُّ والرقمُ لهما فحوصُهما، فلا يُقبلان هنا."""
    with pytest.raises(CapabilityError):
        validate_suite(_suite(value))


# ——— الجانبُ الذي فات: جوابُ النموذج لا المرجع وحده ———
#
# وُسِّع `validate_suite` ليقبل المرجعَ قائمة، ولم يُوسَّع تقييمُ الفحص. فكان
# الجوابُ القائمة يُردّ قبل أن يُقارن: رسوبٌ مضمونٌ لكل محرّك في كل حالةٍ
# مرجعُها قائمة — وهي حالاتُ الاستخراج التي تطلب المصفوفة صراحةً. ولا يُسجَّل
# خطأً بل «جوابًا خاطئًا»، فلا يظهر في أي عدّاد. وحذفُ الشرط كان لا يُسقط
# اختبارًا واحدًا في المستودع كلِّه، فلم يكن على العقد حارسٌ أصلًا.

from evaluation.capabilities import _checks


def _one(answer: str, value):
    return _checks(answer, [{"kind": "json_equals", "value": value}])[0]["passed"]


def test_a_list_answer_matching_a_list_reference_passes():
    assert _one('[{"الاسم": "سالم"}]', [{"الاسم": "سالم"}]) is True


def test_a_list_answer_that_differs_still_fails():
    assert _one('[{"الاسم": "خالد"}]', [{"الاسم": "سالم"}]) is False


def test_order_still_matters_in_a_list():
    assert _one('["ب", "أ"]', ["أ", "ب"]) is False


def test_a_shorter_list_fails():
    assert _one('["أ"]', ["أ", "ب"]) is False


def test_an_object_answer_still_matches_an_object_reference():
    assert _one('{"أ": 1}', {"أ": 1}) is True


@pytest.mark.parametrize("answer,value", [
    ('[1, 2]', {"أ": 1}),      # قائمةٌ مقابل كائن
    ('{"أ": 1}', [{"أ": 1}]),  # كائنٌ مقابل قائمة
])
def test_a_container_of_the_wrong_kind_fails(answer, value):
    """التوسيعُ لا يخلط الوعاءين: القائمةُ ليست كائنًا."""
    assert _one(answer, value) is False


def test_a_scalar_answer_fails_against_a_container():
    assert _one('"نصّ"', [{"أ": 1}]) is False
