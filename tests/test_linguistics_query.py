"""اختبارات البحث الذكي والاستخراج المعجمي والشرح المسند في عقدة اللغويات."""
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nodes.linguistics.node import (
    LinguisticsNode,
    extract_headwords,
    extract_lexicon_entry,
)
from tests.private_stores import needs_lexicons


def test_extract_headwords():
    # استخراج الكلمات بعد حذف الأدوات والأسئلة الشائعة
    q1 = "ما معنى الصابورة في المعجم الوسيط؟"
    hws1 = extract_headwords(q1)
    assert "الصابورة" in hws1
    assert "معنى" not in hws1
    assert "المعجم" not in hws1
    assert "الوسيط" not in hws1

    q2 = "ما أصل كلمة الربان ومعناها في القاموس المحيط؟"
    hws2 = extract_headwords(q2)
    assert "الربان" in hws2
    assert "أصل" not in hws2
    assert "كلمة" not in hws2


def test_extract_lexicon_entry():
    text = (
        "(التصبيرة) يتَنَاوَلهُ الجائع يَسْتَعِين بِهِ على الصَّبْر.\n"
        "(الصابورة) مَا يوضع فِي بطن السَّفِينَة من الثفل لِئَلَّا تميد.\n"
        "(الصبارة) صمام القارورة."
    )
    entry = extract_lexicon_entry(text, "الصابورة")
    assert entry == "(الصابورة) مَا يوضع فِي بطن السَّفِينَة من الثفل لِئَلَّا تميد."

    # في حال عدم وجود الكلمة يعاد صدر النص
    entry_fallback = extract_lexicon_entry(text, "كلمة_غير_موجودة")
    assert entry_fallback.startswith("(التصبيرة)")


@needs_lexicons
def test_lookup_query_real_lexicon():
    node = LinguisticsNode(ROOT)
    # فحص كلمة مفردة
    hits_single = node.lookup_query("الصابورة")
    assert len(hits_single) > 0
    assert any("بطن السَّفِينَة" in h["item"]["text"] for h in hits_single)

    # فحص سؤال طبيعي مركب
    hits_question = node.lookup_query("ما معنى الصابورة في المعجم الوسيط؟")
    assert len(hits_question) > 0
    assert any("بطن السَّفِينَة" in h["item"]["text"] for h in hits_question)


@needs_lexicons
def test_explain_real_lexicon():
    node = LinguisticsNode(ROOT)
    res = node.explain("ما معنى الصابورة في المعجم الوسيط؟")
    assert "answer_text" in res
    assert "[ش1]" in res["answer_text"]
    assert "الصابورة" in res["answer_text"]
    assert len(res["evidence"]) == 1
    assert res["evidence"][0]["item"]["domain"] == "lexicon"
    assert "ج1 ص506" in res["locus"]


def test_lookup_query_empty_and_unknown():
    node = LinguisticsNode(ROOT)
    with pytest.raises(LookupError, match="فارغ"):
        node.lookup_query("   ")

    with pytest.raises(LookupError, match="لا مدخل"):
        node.lookup_query("كلمة_خيالية_جدا_لا_وجود_لها_بتاتا_123")
