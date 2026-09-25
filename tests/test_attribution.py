"""اختبارات ق٢٦: الإسنادُ يُفحص لا يُفترض.

الحارسان الدائمان هنا **مثالان حقيقيان** من دليل م٤ المدفوع: ادعاءان
حملا `[ش1]` وشاهدُهما لا يحتويهما — مرّا بالحارس القديم لأن رقم
الشاهد كان صالحًا في المدى.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.attribution import (DEFAULT_OVERLAP_FLOOR, bind_claims,
                              content_tokens, normalize, numbers_in,
                              refs_in, split_claims, unsupported)

# صفحةُ شاهدٍ تحاكي جدول المخالفة الجسيمة بصياغةٍ مصطنعة: نصُّ اللائحة نفسُه من
# ملفات المالك الخاصة فلا يُقتبس في المستودع (ك٢٧)، والبنيةُ والمعنى محفوظان.
PAGE_1 = {
    "text": "# جدول تعريفات «المخالفة الجسيمة» ومعاييرها "
            "**تعريف المخالفة الجسيمة:** كل فعلٍ يخالف أحكام النظام أو "
            "اللائحة ويمسّ صلاحية النشاط أو السلامة التشغيلية للسفينة أو "
            "أهلية طاقمها. "
            "- جاهزية الطوارئ والاستجابة لها: أي خلل يعيق قدرة السفينة على "
            "الاستجابة لحالات الطوارئ، كعدم جاهزية معدات الإخلاء أو نقص "
            "تدريب الطاقم. "
            "- حماية البيئة البحرية: أي تصريف أو تشغيل يُدخل ملوثات أو مواد "
            "ضارة إلى البيئة البحرية.",
    "part": "جدول 1 — تعريفات ومعايير المخالفة الجسيمة",
    "locus": "المقطع 1",
}

# — الأدوات الحتمية —

def test_normalization_unifies_digits_and_letters():
    assert numbers_in("الحمولة ٤٠٠ طن") == ["400"]
    assert numbers_in("مبلغ 1,000 ريال") == ["1000"]
    n = normalize("الإدارةُ البحريّة")
    assert "الاداره" in n and "ً" not in n


def test_claim_split_and_refs():
    text = "الأول [ش1]. والثاني [ش٢] [ش3]؟ وثالث بلا إحالة"
    claims = split_claims(text)
    assert len(claims) == 3
    assert refs_in(claims[0]) == (1,) and refs_in(claims[1]) == (2, 3)
    assert refs_in(claims[2]) == ()


def test_content_tokens_drop_stopwords_and_short():
    toks = content_tokens("في السفينة من أجل الطوارئ")
    assert "السفينه" in toks and "في" not in toks and "من" not in toks


# — الحارسان الدائمان: مثالا التدقيق الحقيقيان —

def test_fabricated_claim_about_tickets_is_caught():
    """«مزاولة بيع تذاكر السفر» أُسند إلى شاهد المخالفة الجسيمة ولا
    ذكرَ له فيه — مرّ بالحارس القديم (تغطيته 0.15)."""
    answer = ("- بيعُ تذاكر رحلاتٍ بحرية من غير عقدٍ مع ناقلٍ مرخّص [ش1]")
    bindings = bind_claims(answer, {1: PAGE_1})
    assert bindings[0].coverage < DEFAULT_OVERLAP_FLOOR
    bad = unsupported(bindings)
    assert bad and bad[0][1] == "citation_overlap_low"


def test_supported_claim_passes_with_excerpt():
    answer = ("أي خلل يعيق قدرة السفينة على الاستجابة لحالات الطوارئ "
              "كعدم جاهزية معدات الإخلاء [ش1].")
    b = bind_claims(answer, {1: PAGE_1})[0]
    assert b.coverage >= DEFAULT_OVERLAP_FLOOR and not unsupported([b])
    assert "الطوارئ" in b.excerpt          # المقتطف يُفتح ويُتحقق


def test_number_not_in_evidence_is_refused_outright():
    answer = "تلتزم السفن التي تبلغ حمولتها 400 طن بهذا الحكم [ش1]."
    bad = unsupported(bind_claims(answer, {1: PAGE_1}))
    assert bad and bad[0][1] == "citation_number_unsupported"
    assert "400" in bad[0][2]


def test_number_in_document_title_counts_as_supported():
    """هويةُ المرجع جزءٌ من قيده: «جدول 1» في عنوان الوثيقة."""
    answer = "ورد ذلك في الجدول 1 من التعريفات [ش1]."
    assert not any(b.missing_numbers for b in bind_claims(answer, {1: PAGE_1}))


def test_claim_citing_missing_page_is_unsupported():
    answer = "ادعاء يحيل إلى شاهد غير متاح [ش9]."
    bad = unsupported(bind_claims(answer, {1: PAGE_1}))
    assert bad and bad[0][1] == "citation_overlap_low"


def test_uncited_segments_are_bound_and_refused():
    answer = "تمهيد بلا إحالة. وادعاء مسند إلى الطوارئ والإخلاء [ش1]."
    bindings = bind_claims(answer, {1: PAGE_1})
    assert len(bindings) == 2
    assert unsupported(bindings)[0][1] == "citation_missing"


def test_marker_parameter_supports_research_citations():
    answer = "إدخال ملوثات أو مواد ضارة إلى البيئة البحرية [م1]."
    assert unsupported(bind_claims(answer, {1: PAGE_1}))[0][1] == "citation_missing"
    b = bind_claims(answer, {1: PAGE_1}, marker="م")
    assert b and b[0].refs == (1,) and not unsupported(b)


def test_floor_separates_real_golden_from_fabricated():
    """الأرضية مُعايَرةٌ على 43 ادعاءً حقيقيًّا: أدنى سليمٍ 0.39،
    والمختلَقان 0.29 و0.15 — فالأرضية بينهما لا بالتخمين."""
    assert 0.29 < DEFAULT_OVERLAP_FLOOR < 0.39
