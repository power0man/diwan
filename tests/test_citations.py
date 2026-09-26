"""ك٥٣: مدقّقُ الإسناد — كلُّ ادّعاءٍ بمرجع، وكلُّ مرجعٍ مصدرٌ أعاده البحثُ في الجولة نفسِها."""
from __future__ import annotations

import pytest

from agent.citations import check

A, B = "https://mawsoua.example/person/sulafa", "https://mawsoua.example/city/zabarjad"
GOOD = f"وُلدت سلافة في زبرجد [1]، وتأسّست زبرجد عام 1412 [2].\n\nالمصادر:\n[1] {A}\n[2] {B}\n"


def test_a_fully_cited_answer_from_returned_sources_passes():
    report = check(GOOD, {A, B})
    assert report.passed and report.codes == () and report.sources == {1: A, 2: B}


@pytest.mark.parametrize("answer", [
    GOOD.replace("[1]", "[١]").replace("[2]", "[٢]"),                             # أرقامٌ عربية
    GOOD.replace("المصادر:", "**المصادر:**"),
    GOOD.replace("المصادر:", "## المراجع"),
    f"وُلدت سلافة في زبرجد التي تأسّست عام 1412 [1، 2].\n\nالمصادر:\n[1] {A}\n[2] <{B}>",
    f"وُلدت سلافة في زبرجد التي تأسّست عام 1412 [1,2].\n\nالمصادر:\n- [1] {A}\n- [2]: {B}",
])
def test_common_citation_forms_are_read(answer):
    assert check(answer, {A, B}).passed


@pytest.mark.parametrize("answer, returned, code", [
    (GOOD, {A}, "source_not_returned"),                                           # مصدرٌ لم يُعِده البحث
    (GOOD.split("\n\nالمصادر")[0], {A, B}, "sources_missing"),
    (GOOD.replace(" [1]", "").replace(" [2]", ""), {A, B}, "uncited_claim"),
    (GOOD.replace("[2].", "[3]."), {A, B}, "marker_undefined"),
    (GOOD + f"[2] {A}\n", {A, B}, "source_duplicate"),
    (GOOD + "انظر الموسوعة\n", {A, B}, "source_malformed"),
    ("زبرجد 1412.", set(), "uncited_claim"),                                      # قصيرةٌ لكن فيها رقم
    ("لم أجد السنة، لكنها على الأرجح 1415.", set(), "uncited_claim"),              # إقرارٌ فيه تخمينٌ رقميّ
], ids=["not_returned", "no_sources", "uncited", "undefined", "duplicate", "malformed", "short_number",
        "hedged_number"])
def test_each_failure_is_named(answer, returned, code):
    report = check(answer, returned)
    assert not report.passed and code in report.codes


def test_an_admission_of_no_answer_needs_no_source():
    report = check("لم أجد في المصادر المتاحة عددَ سكّان مدينة فيروزة.", set())
    assert report.passed and report.sources == {}


def test_an_unused_source_is_noted_but_not_a_failure():
    report = check(GOOD + f"[3] {A}#x\n", {A, B, A + "#x"})
    assert report.passed and "source_unused" in report.codes


def test_a_heading_is_not_a_claim_but_a_long_sentence_is():
    assert check("الجواب:\nلم أجد ذلك في المصادر.", set()).passed
    assert "uncited_claim" in check("تقع هذه المدينة على ساحل البحر الشمالي.", set()).codes


def test_limit_one_marker_covers_its_sentence_and_the_bank_catches_the_misattribution():
    """الحدُّ المعلن: المرجعُ يغطّي جملتَه كلَّها. فعزوُ رقمٍ إلى غير مصدره يمرّ من المدقّق، ويسقطه البنك."""
    from evaluation.research_bank import score_item
    answer = GOOD.replace(" [2]", "")
    assert check(answer, {A, B}).passed
    item = {"category": "two_hop", "facts": [{"value": "زبرجد", "sources": [A]}, {"value": 1412, "sources": [B]}],
            "traps": []}
    scored = score_item(item, answer, {A, B})
    assert not scored["passed"] and [f["supported"] for f in scored["facts"]] == [True, False]


MEMORY = "https://en.wikipedia.org/wiki/Nile_(river)"
DESCRIBED = f"وُلدت سلافة في زبرجد [1].\n\nالمصادر:\n[1] {A} - صفحةُ سلافة\n[2] من الذاكرة: {MEMORY}.\n[3] صفحةُ المدينة ({B})\n"


def test_a_malformed_source_line_still_counts_its_unreturned_link_as_fabricated():
    """كشفه ك٤٦ (#119): `[n] رابط - وصف` سطرٌ معطوب، فكان يُتخطّى قبل مقارنة رابطه بما أعاده البحث،
    فلا يُعدّ الاستشهادُ من الذاكرة اختلاقًا. والرابطُ يُقرأ بقوسيه ولا يلصق به ترقيمُ الجملة."""
    report = check(DESCRIBED, {A, B})
    assert "source_malformed" in report.codes and not report.passed
    assert [url for code, url in report.findings if code == "source_not_returned"] == [MEMORY]


def test_a_malformed_line_whose_link_was_returned_is_malformed_but_not_fabricated():
    report = check(DESCRIBED, {A, B, MEMORY})
    assert "source_malformed" in report.codes and "source_not_returned" not in report.codes
