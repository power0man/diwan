"""الإصلاحُ الآليّ للإسناد لا يصنع إسنادًا لنفي شاهده (غ٦، #25).

كان `auto_repair_attribution` يُلحق `[ش1]` بادعاءٍ ينفي شاهدَه، لأن ألفاظهما متطابقة إلا أداةَ
النفي (تقييم ٢٣ سبتمبر §٥-أ). فصار يوازن قطبيّةَ الادعاء بقطبيّة نافذة الشاهد، أي زوجيّةَ أدوات
النفي والحظر فيهما. فلا يُصلح إلا ما وافقت قطبيّتُه قطبيّةَ الشاهد.

الحالاتُ مزروعةٌ بأدوات النفي كلِّها، وفي الاتجاهين، ومعها ضابطان موجبان.
"""
from __future__ import annotations

import pytest

from core.attribution import auto_repair_attribution, polarity, unsupported

AFFIRMED = "يجب على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث"
FORBIDDEN = "لا يجوز للسفينة مغادرة الميناء قبل إتمام التفتيش الفني من الجهة المختصة"


def _pages(text):
    return {1: {"text": text, "part": "لائحة", "locus": "المادة (٥)"}}


@pytest.mark.parametrize("claim", [
    "لا يجب على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث",
    "ليس على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث",
    "لم يُلزَم الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث",
    "لن يجب على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث",
    "إبلاغ السلطة البحرية غير واجب على الربان خلال أربع وعشرين ساعة من وقوع الحادث",
    "عدم إبلاغ السلطة البحرية جائز للربان خلال أربع وعشرين ساعة من وقوع الحادث",
    "يُحظر على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث",
    "ممنوع على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث",
])
def test_a_claim_that_negates_an_affirming_witness_is_not_repaired(claim):
    repaired, bindings, changed = auto_repair_attribution(claim, _pages(AFFIRMED))
    assert changed is False and repaired == claim
    assert unsupported(bindings), "صار الادعاءُ المنفيّ مسنَدًا"


def test_a_claim_that_affirms_what_the_witness_forbids_is_not_repaired():
    claim = "يجوز للسفينة مغادرة الميناء قبل إتمام التفتيش الفني من الجهة المختصة"
    _, _, changed = auto_repair_attribution(claim, _pages(FORBIDDEN))
    assert changed is False


def test_an_affirmed_claim_with_an_affirming_witness_is_still_repaired():
    repaired, bindings, changed = auto_repair_attribution(AFFIRMED, _pages(AFFIRMED))
    assert changed is True and repaired.endswith("[ش1]") and unsupported(bindings) == []


def test_a_negated_claim_with_a_negating_witness_is_still_repaired():
    repaired, _, changed = auto_repair_attribution(FORBIDDEN, _pages(FORBIDDEN))
    assert changed is True and repaired.endswith("[ش1]")


@pytest.mark.parametrize("text,expected", [
    ("يجب الإبلاغ", 0), ("لا يجب الإبلاغ", 1), ("لا يجوز عدم الإبلاغ", 0), ("ما يلزم من وثائق", 0),
])
def test_polarity_is_the_parity_of_negators(text, expected):
    assert polarity(text) == expected
