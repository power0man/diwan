"""اختبارات الإصلاح الآلي للإسناد (م١٥): الانتقال من الرفض الأعمى إلى الحوكمة التعزيزية.

الفحص يثبت:
1. إصلاح الإحالة الخاطئة إلى الشاهد الصحيح الذي يحمل الأرقام والنص.
2. إصلاح غياب الإحالة عن المقطع الموثوق بإلحاق رقم شاهده المثبت.
3. الامتناع الصارم (الفشل المغلق) عند اختلاق أرقام أو وقائع لا سند لها.
4. بقاء النص السليم دون تبديل.
"""
from __future__ import annotations

import pytest

from core.attribution import auto_repair_attribution, bind_claims, unsupported

PAGE_1 = {
    "text": "تلتزم السفينة بالحصول على شهادة إدارة مياه الصابورة الدولية المعمول بها.",
    "part": "لائحة الصابورة",
    "locus": "المادة (3)",
}

PAGE_2 = {
    "text": "يعاقب كل من يخالف أحكام هذه اللائحة بغرامة مالية قدرها 500 ريال وتضاعف عند التكرار.",
    "part": "لائحة المخالفات",
    "locus": "المادة (10)",
}

PAGES = {1: PAGE_1, 2: PAGE_2}


def test_clean_answer_is_not_modified():
    """الجواب السليم المنسوب بدقة لا يُمس ولا يُعدل."""
    text = "تلتزم السفينة بالحصول على شهادة إدارة مياه الصابورة [ش1]."
    repaired, bindings, changed = auto_repair_attribution(text, PAGES)
    assert changed is False
    assert repaired == text
    assert not unsupported(bindings)


def test_missing_citation_on_grounded_clause_is_repaired():
    """المقطع الذي يطابق الشاهد تماماً ولكنه نسي الإحالة يُصلح بإلحاق رقم الشاهد."""
    text = "تلتزم السفينة بالحصول على شهادة إدارة مياه الصابورة الدولية"
    repaired, bindings, changed = auto_repair_attribution(text, PAGES)
    assert changed is True
    assert "[ش1]" in repaired
    assert not unsupported(bindings)


def test_wrong_citation_number_is_repaired_to_actual_source():
    """إذا أخطأ النموذج ونسب الغرامة إلى شاهد الصابورة [ش1]، تُصلح الإحالة إلى [ش2]."""
    text = "يعاقب المخالف بغرامة مالية قدرها 500 ريال [ش1]."
    # في الأصل: ش1 لا يحتوي على الرقم 500، فيفشل الإسناد
    orig_bindings = bind_claims(text, PAGES)
    assert any(code == "citation_number_unsupported" for _, code, _ in unsupported(orig_bindings))

    # بعد الإصلاح: ش2 يحمل الرقم 500 ونفس النص، فتتحول الإحالة إلى [ش2]
    repaired, bindings, changed = auto_repair_attribution(text, PAGES)
    assert changed is True
    assert "[ش2]" in repaired
    assert "[ش1]" not in repaired
    assert not unsupported(bindings)


def test_fabricated_number_fails_closed_without_repair():
    """الأرقام المختلقة التي لا توجد في أي شاهد لا تُصلح أبداً، ويفشل الإسناد مغلقاً."""
    text = "يعاقب المخالف بغرامة قدرها 99999 ريال [ش1]."
    repaired, bindings, changed = auto_repair_attribution(text, PAGES)
    assert changed is False
    assert repaired == text
    assert any(code == "citation_number_unsupported" for _, code, _ in unsupported(bindings))


def test_completely_hallucinated_claim_fails_closed():
    """الادعاء المختلق بالكامل (لا تداخل معجمي في أي شاهد) يبقى مرفوضاً ولا يُزوّر له إسناد."""
    text = "تفتح السينما أبوابها للركاب مجاناً في عطلة نهاية الأسبوع [ش1]."
    repaired, bindings, changed = auto_repair_attribution(text, PAGES)
    assert changed is False
    assert any(code == "citation_overlap_low" for _, code, _ in unsupported(bindings))
