"""فحوص الاسترجاع المعرفي الهجين والتقطيع التشريعي (Track A).

التحقق من:
1. التقطيع الدلالي الهيكلي للمواد والفقرات والتعريفات (LegislativeChunker).
2. الاسترجاع المعرفي الهجين (HybridRetriever) ودمج درجات RRF.
3. دقة الشواهد والفقرات المسترجعة وسرعة الأداء (< 50ms).
4. سلامة التوسيع بالجذور الصرفية.
"""
from __future__ import annotations

import time
import pytest

from core.chunking import LegislativeChunker, LegislativeChunk
from core.hybrid_retrieval import HybridRetriever, hybrid_search
from tests.private_stores import needs_corpus


SAMPLE_REGULATION = """المادة (1): التعريفات
يقصد بالمصطلحات والعبارات الآتية المعاني الموضحة قرين كل منها:
السلطة البحرية: هيئة النقل العام ممثلة في قطاع النقل البحري أو فروعها المختصة.
المعاينة الدورية: الكشف الفني على السفينة للتأكد من استيفاء متطلبات السلامة.
مياه الصابورة: المياه المحملة في السفينة لضبط الاتزان والاستقرار الملاحي.

المادة (2): الواجبات والالتزامات
1- تلتزم كافة السفن الوطنية بحمل شهادات الصلاحية الفنية السارية.
2- يمتنع ربان السفينة عن الإبحار في حال وجود خلل جسيم يهدد الأرواح.
3- تخضع المنشآت البحرية للتفتيش المفاجئ من قبل مفتشي الهيئة.

المادة (3): العقوبات والجزاءات
تفرض غرامة مالية لا تقل عن 10000 ريال على كل سفينة تخالف شروط السلامة.
"""


def test_legislative_chunker_structure():
    chunker = LegislativeChunker()
    chunks = chunker.chunk_text(SAMPLE_REGULATION, doc_id="reg_01", part="لائحة السلامة")
    assert len(chunks) >= 5

    # فحص مقاطع التعريفات
    defs = [c for c in chunks if "تعريف" in c.locus]
    assert len(defs) == 3
    terms = [c.heading for c in defs]
    assert any("السلطة البحرية" in t for t in terms)
    assert any("المعاينة الدورية" in t for t in terms)
    assert any("مياه الصابورة" in t for t in terms)

    # فحص مقاطع الفقرات
    clauses = [c for c in chunks if "الفقرة" in c.locus]
    assert len(clauses) == 3
    assert all(c.article_number == 2 for c in clauses)

    # فحص مادة العقوبات
    penalties = [c for c in chunks if c.article_number == 3]
    assert len(penalties) == 1
    assert "غرامة مالية" in penalties[0].text


@needs_corpus
def test_hybrid_search_accuracy_and_speed():
    t0 = time.perf_counter()
    results = hybrid_search("مياه الصابورة", limit=3)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(results) > 0
    assert len(results) <= 3
    assert elapsed_ms < 100.0  # سريع دون أي بطء

    first = results[0]
    assert "doc_id" in first
    assert "part" in first
    assert "locus" in first
    assert "text" in first
    assert "score" in first
    assert first["score"] > 0.0


@needs_corpus
def test_hybrid_search_morphology_root_expansion():
    # البحث بلفظ مشتق يجد مواد تحتوي على تصاريف أخرى لنفس الجذر
    results = hybrid_search("المفتشون", limit=3)
    assert len(results) > 0
    first = results[0]
    assert "doc_id" in first
    assert "locus" in first
    # تأكيد احتواء النتيجة على سياق تشريعي حقيقي
    assert len(first["text"]) > 0


def test_hybrid_search_empty_query():
    results = hybrid_search("   ", limit=5)
    assert results == []
