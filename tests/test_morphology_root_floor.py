"""أرضيةُ دقّة الجذور وقاعدةُ «يرفض بدل أن يخمّن» (غ١).

قِيس في ٢٥ سبتمبر ٢٠٢٦ على `evaluation/suites/morphology_roots_v1.json`: المحلّلُ القالبي
٦١/١٠٠ بخمسةٍ وثلاثين خطأً بثقة، وCAMeL باتّفاق التحليلات ٨٣/١٠٠ بخطأين. هذه الأرقامُ
أرضيةٌ لا هدف: انحدارُ أيٍّ منهما يُسقط الاختبار، والتحسّنُ يمرّ. واختبارُ الاتّفاق لا يحتاج
CAMeL: محلّلٌ مصطنع يُثبت أن اللبسَ رفضٌ لا تخمين.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.linguistics import roots
from core.linguistics.roots import RootVerdict, camel_available, camel_verdict, root_verdict, template_verdict
from tools.measure_morphology import matches

SUITE = json.loads((Path(__file__).resolve().parents[1] / "evaluation/suites/morphology_roots_v1.json")
                   .read_text(encoding="utf-8"))
TEMPLATE_FLOOR = {"correct": 61, "wrong_with_confidence": 35}
CAMEL_FLOOR = {"correct": 83, "wrong_with_confidence": 2}


def tally(verdict_of):
    correct = wrong = 0
    for item in SUITE["items"]:
        verdict = verdict_of(item["word"])
        hit = matches(verdict.root, item["gold_roots"])
        correct += hit
        wrong += verdict.root is not None and not hit
    return {"correct": correct, "wrong_with_confidence": wrong}


def test_the_suite_is_one_hundred_real_words_with_declared_gold():
    assert len(SUITE["items"]) == 100 and len({i["word"] for i in SUITE["items"]}) == 100
    assert all(isinstance(i["gold_roots"], list) for i in SUITE["items"])
    assert sum(1 for i in SUITE["items"] if i["stratum"] == "frequent") == 50


def test_the_template_analyzer_does_not_regress_below_its_measured_floor():
    scores = tally(template_verdict)
    assert scores["correct"] >= TEMPLATE_FLOOR["correct"], scores
    assert scores["wrong_with_confidence"] <= TEMPLATE_FLOOR["wrong_with_confidence"], scores


@pytest.mark.skipif(not camel_available(), reason="CAMeL Tools أو قاعدتُه غير مركَّبة")
def test_camel_agreement_does_not_regress_below_its_measured_floor():
    scores = tally(camel_verdict)
    assert scores["correct"] >= CAMEL_FLOOR["correct"], scores
    assert scores["wrong_with_confidence"] <= CAMEL_FLOOR["wrong_with_confidence"], scores


# ————— يرفض بدل أن يخمّن: بلا CAMeL، بمحلّلٍ مصطنع —————

class FakeAnalyzer:
    def __init__(self, table):
        self.table = table

    def analyze(self, word):
        return [{"root": r} for r in self.table.get(word, [])]


FAKE = FakeAnalyzer({
    "كاتب": ["ك.ت.ب", "ك.ت.ب"],
    "الحق": ["ح.ق.ق", "ل.ح.ق"],
    "xyz": ["FOREIGN"],
    "١٢٣": ["DIGIT", "NOAN"],
    "غريب": [],
})


def test_agreement_gives_the_root_and_disagreement_refuses_with_the_candidates():
    assert camel_verdict("كاتب", FAKE) == RootVerdict("كاتب", "كتب", "camel", None, ("كتب",))
    held = camel_verdict("الحق", FAKE)
    assert held.root is None and held.reason == "ambiguous" and held.candidates == ("حقق", "لحق")


@pytest.mark.parametrize("word,reason", [("xyz", "no_root"), ("١٢٣", "no_root"), ("غريب", "no_analysis")])
def test_non_roots_and_missing_analyses_are_named_refusals(word, reason):
    verdict = camel_verdict(word, FAKE)
    assert verdict.root is None and verdict.reason == reason and verdict.candidates == ()


def test_without_camel_the_template_path_is_taken_and_named(monkeypatch):
    monkeypatch.setattr(roots, "camel_available", lambda: False)
    verdict = root_verdict("كاتب")
    assert verdict.source == "template" and verdict.root == "كتب"


def test_the_template_path_can_be_asked_for_explicitly():
    assert root_verdict("كاتب", prefer="template").source == "template"


def test_the_sovereign_tool_reports_the_measured_root_and_keeps_the_template_root(monkeypatch):
    """«السفينة»: القالبيُّ يبتلع اللام (لسف)، والمقيسُ يعطي سفن — والأداةُ تقول من أين."""
    from core.tools_registry import _analyze_morphology_handler
    monkeypatch.setattr(roots, "camel_available", lambda: True)
    monkeypatch.setattr(roots, "camel_analyzer", lambda: FakeAnalyzer({"السفينة": ["س.ف.ن"]}))
    result = _analyze_morphology_handler({"word": "السفينة"})
    assert (result["root"], result["root_source"], result["root_reason"]) == ("سفن", "camel", None)
    assert result["template_root"] == "لسف" and result["root_candidates"] == ["سفن"]
    monkeypatch.setattr(roots, "camel_available", lambda: False)
    fallback = _analyze_morphology_handler({"word": "السفينة"})
    assert (fallback["root"], fallback["root_source"]) == ("لسف", "template")
