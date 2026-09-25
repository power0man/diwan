"""فحوص محرك الحوكمة التوليدية المعززة بالاستلزام الدلالي والاشتقاق الحسابي."""
from __future__ import annotations

import pytest

from core.semantic_governance import (
    DEFAULT_ENTAILMENT_FLOOR_BP,
    evaluate_semantic_governance,
    is_structural_phrase,
    semantic_match_basis_points,
    verify_numerical_derivation,
)

PAGE_1 = {
    "text": (
        "المادة (5): تفرض غرامة مالية قدرها 200 ريال عند التأخير، "
        "مع رسوم إضافية قدرها 50 ريالاً للفحص الفني، ويجب معاينة السفينة قبل الإبحار."
    ),
    "part": "لائحة السلامة البحرية",
    "locus": "المادة 5",
}


def test_structural_phrase_detection():
    assert is_structural_phrase("### شروط السلامة البحرية") is True
    assert is_structural_phrase("أهلاً بك") is True
    assert is_structural_phrase("فيما يلي بيان الضوابط:") is True
    assert is_structural_phrase("- ") is True
    # جملة موضوعية تحمل ادعاءً
    assert is_structural_phrase("تفرض الهيئة غرامة قدرها مائتا ريال على المخالفين") is False


def test_semantic_match_with_roots_and_synonyms():
    # الشاهد يحتوي: "معاينة السفينة" و "غرامة"
    # الادعاء يحتوي: "تفتيش الباخرة" و "جزاء"
    claim_tokens = ["تفتيش", "الباخره", "جزاء"]
    score_bp = semantic_match_basis_points(claim_tokens, PAGE_1["text"])

    # يجب أن تتجاوز نقاط الأساس عتبة الاستلزام (6000 bp) بفضل الجذور والمترادفات
    assert score_bp >= DEFAULT_ENTAILMENT_FLOOR_BP
    assert score_bp <= 10000


def test_semantic_match_fails_for_unrelated_claim():
    claim_tokens = ["زراعه", "القمح", "والشعير", "في", "الحقول"]
    score_bp = semantic_match_basis_points(claim_tokens, PAGE_1["text"])
    assert score_bp < 3000


def test_numerical_derivation_sum():
    citation_nums = ["200", "50"]
    # 250 مشتقة من 200 + 50
    claim_nums = ["250"]
    supported, unsupported = verify_numerical_derivation(claim_nums, citation_nums)
    assert "250" in supported
    assert unsupported == []


def test_numerical_derivation_difference():
    citation_nums = ["2026", "2020"]
    # 6 مشتقة من 2026 - 2020
    claim_nums = ["6"]
    supported, unsupported = verify_numerical_derivation(claim_nums, citation_nums)
    assert "6" in supported
    assert unsupported == []


def test_numerical_derivation_catches_hallucination():
    citation_nums = ["200", "50"]
    claim_nums = ["9999"]
    supported, unsupported = verify_numerical_derivation(claim_nums, citation_nums)
    assert supported == []
    assert "9999" in unsupported


def test_evaluate_semantic_governance_full_success():
    answer = (
        "### تفاصيل الرسوم [ش1]. "
        "يجب تفتيش الباخرة قبل الإبحار [ش1]. "
        "ويبلغ إجمالي المستحقات 250 ريالاً [ش1]."
    )
    pages = {1: PAGE_1}

    result = evaluate_semantic_governance(answer, pages)
    assert result.all_passed is True
    assert result.refusal_required is False
    assert result.unsupported_claims == ()
    assert result.average_semantic_bp >= DEFAULT_ENTAILMENT_FLOOR_BP


def test_evaluate_semantic_governance_catches_missing_citation_on_claim():
    answer = (
        "### تفاصيل الرسوم. "
        "هذا ادعاء موضوعي غير مسند بأي شاهد مطلقاً."
    )
    pages = {1: PAGE_1}

    result = evaluate_semantic_governance(answer, pages)
    assert result.all_passed is False
    assert len(result.unsupported_claims) == 1
    assert any(b.diagnostic_code == "citation_missing" for b in result.bindings)


def test_evaluate_semantic_governance_catches_unsupported_numbers():
    answer = "تفرض غرامة خيالية قدرها 7777 ريالاً [ش1]."
    pages = {1: PAGE_1}

    result = evaluate_semantic_governance(answer, pages)
    assert result.all_passed is False
    assert result.refusal_required is True
    assert any(b.diagnostic_code == "citation_number_unsupported" for b in result.bindings)
