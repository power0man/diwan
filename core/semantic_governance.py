"""محرك الحوكمة التوليدية المعززة بالاستلزام الدلالي والاشتقاق الحسابي (م١٦).

يرتقي بالحوكمة من أسلوب «الامتناع الدفاعي الميكانيكي» (الذي يرفض ~35% من الأجوبة
لاشتراط التطابق اللفظي الحرفي بنسبة 34%، وحظر الأرقام المحسوبة) إلى حوكمة فاهمة
تتحقق من:
1. الاستلزام الدلالي (Semantic Entailment) عبر الجذور الصرفية والمترادفات النظامية.
2. التدقيق الجبري للأرقام المشتقة بحساب سليم (الجمع، الفرق، النسب).
3. استثناء الهياكل التنسيقية والمقدمات من الإسقاط العشوائي بـ citation_missing.
4. تقديم مقترحات التنقيح التفاعلي عند نقص الإسناد بدلاً من الصمت والإلغاء الكامل.
5. صيانة الدستور الجبري وانعدام الكسور العشرية (Basis Points 0..10000).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.attribution import (
    cite_pattern,
    content_tokens,
    normalize,
    numbers_in,
    refs_in,
    split_claims,
)
from projections.morphology import analyze

# نقاط الأساس (Basis Points): 10,000 = 100.0%
DEFAULT_ENTAILMENT_FLOOR_BP = 6000   # 60.0% ثقة في الاستلزام الدلالي
DEFAULT_LEXICAL_FLOOR_BP = 3400      # 34.0% أرضية التطابق المعجمي الكلاسيكي

# قواميس التكافؤ الدلالي للمصطلحات النظامية والبحرية الشائعة
SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"حظر", "منع", "حظرها", "منعها", "محظور", "ممنوع"}),
    frozenset({"الزام", "وجوب", "تعين", "فرض", "يلزم", "يجب", "يتعين", "ينبغي"}),
    frozenset({"سفينة", "سفينه", "مركب", "باخرة", "باخره", "قارب", "سفن"}),
    frozenset({"ربان", "قبطان", "نوخذة", "نوخذه"}),
    frozenset({"معاينة", "معاينه", "تفتيش", "فحص", "كشف", "معاينتها", "تفتيشها"}),
    frozenset({"ترخيص", "تصريح", "اذن", "موافقة", "موافقه"}),
    frozenset({"عقوبة", "عقوبه", "جزاء", "غرامة", "غرامه", "مخالفة", "مخالفه"}),
    frozenset({"صلاحية", "صلاحيه", "اهلية", "اهليه", "كفاءة", "كفاءه"}),
    frozenset({"طوارئ", "اخلاء", "انقاذ", "نجدة", "نجده"}),
    frozenset({"نظام", "لائحة", "لائحه", "قانون", "تنظيم", "احكام"}),
)

# ألفاظ الحساب والاستنتاج المالي والتنظيمي
CALC_TOKENS: frozenset[str] = frozenset({
    "اجمالي", "مجموع", "مستحقات", "مبلغ", "قدره", "قدرها", "تكلفه",
    "تكلفة", "فارق", "زياده", "زيادة", "تصل", "يصل", "يبلغ", "تبلغ",
    "ريال", "ريالا", "مجموعها", "اجماليها",
})

# تعابير العبارات الإنشائية والتنسيقية المعفاة من الإحالة الإلزامية
_STRUCTURAL_PREFIXES = (
    "اهلا", "مرحبا", "السلام عليكم", "بناء على", "وفقا لما ورد", "وفقا للائحة",
    "فيما يلي", "الخلاصة", "ملخص", "وتجدر الاشارة", "علما بان", "ملاحظة",
    "الشروط هي", "الضوابط هي", "الاحكام هي", "المستندات المطلوبة", "تفاصيل",
)


def _strip_al(w: str) -> str:
    """تجريد السوابق المعرفة والمعدية بأمان."""
    if w.startswith(("وال", "فال", "بال", "كال")) and len(w) > 4:
        return w[3:]
    if w.startswith(("ال", "لل")) and len(w) > 3:
        return w[2:]
    return w


def is_structural_phrase(text: str) -> bool:
    """تمييز النصوص التنسيقية والمقدمات الحوارية غير المتضمنة لادعاء موضوعي."""
    clean = text.strip()
    if not clean:
        return True

    # عناوين الماركداون
    if clean.startswith("#"):
        return True

    # علامات التعداد النقطي المجردة أو المرقمة
    if re.match(r"^[-*•]\s*$", clean) or re.match(r"^\d+[\.-]\s*$", clean):
        return True

    normalized = normalize(clean)
    words = normalized.split()
    if len(words) <= 5:
        for prefix in _STRUCTURAL_PREFIXES:
            if normalized.startswith(prefix):
                return True

    return False


def _get_root(word: str) -> str:
    """استخراج الجذر الصرفي الحتمي للكلمة."""
    try:
        res = analyze(word)
        return res.root or word
    except Exception:
        return word


def semantic_match_basis_points(
    claim_tokens: list[str],
    haystack_text: str,
    has_derived_nums: bool = False,
) -> int:
    """حساب درجة الاستلزام والتطابق الدلالي بنقاط الأساس (0..10000)."""
    if not claim_tokens:
        return 10000

    haystack_norm = normalize(haystack_text)
    haystack_tokens = content_tokens(haystack_norm)
    if not haystack_tokens and not has_derived_nums:
        return 0

    matched_weight = 0
    total_weight = len(claim_tokens) * 100

    haystack_set = set(haystack_tokens)
    haystack_stripped = {_strip_al(w) for w in haystack_tokens}
    haystack_roots = {
        _get_root(_strip_al(w)) for w in haystack_tokens
    } | {_get_root(w) for w in haystack_tokens}

    for token in claim_tokens:
        stripped_token = _strip_al(token)
        token_root = _get_root(stripped_token)

        # 1. إجازة مفردات الحساب عند وجود أرقام مشتقة جبرياً
        if has_derived_nums and (token in CALC_TOKENS or stripped_token in CALC_TOKENS):
            matched_weight += 100
            continue

        # 2. تطابق حرفي تام مع أو بدون السوابق
        if token in haystack_set or stripped_token in haystack_stripped:
            matched_weight += 100
            continue

        # 3. تطابق بالجذر الصرفي
        if token_root and token_root in haystack_roots and len(token_root) >= 3:
            matched_weight += 85
            continue

        # 4. فحص الترادف النظامي
        found_synonym = False
        for group in SYNONYM_GROUPS:
            if token in group or stripped_token in group or token_root in group:
                if any(
                    hw in group or _strip_al(hw) in group or _get_root(hw) in group
                    for hw in haystack_tokens
                ):
                    matched_weight += 80
                    found_synonym = True
                    break
        if found_synonym:
            continue

    score = int(round((matched_weight / total_weight) * 10000))
    return min(score, 10000)


def verify_numerical_derivation(
    claim_nums: list[str],
    citation_nums: list[str],
) -> tuple[list[str], list[str]]:
    """فحص الأرقام المشتقة جبرياً وتفريقها عن الأرقام المختلقة دون سند.

    يعيد: (الأرقام المجازة بالاشتقاق أو المطابقة، الأرقام غير المسندة).
    """
    if not claim_nums:
        return [], []

    source_ints: list[int] = []
    for s in citation_nums:
        try:
            source_ints.append(int(s))
        except ValueError:
            pass

    supported: list[str] = []
    unsupported: list[str] = []

    source_set = set(citation_nums)

    for num_str in claim_nums:
        # إذا كان الرقم موجوداً حرفياً في الشاهد
        if num_str in source_set:
            supported.append(num_str)
            continue

        try:
            target = int(num_str)
        except ValueError:
            unsupported.append(num_str)
            continue

        # البحث عن اشتقاق جبري بسيط بين أرقام الشاهد
        derived = False

        # 1. الجمع: n1 + n2 == target
        for i, a in enumerate(source_ints):
            for j, b in enumerate(source_ints):
                if i != j and a + b == target:
                    derived = True
                    break
            if derived:
                break

        # 2. الطرح أو الفارق: abs(a - b) == target
        if not derived:
            for i, a in enumerate(source_ints):
                for j, b in enumerate(source_ints):
                    if i != j and abs(a - b) == target:
                        derived = True
                        break
                if derived:
                    break

        # 3. النسبة المئوية: target == int(round(a * (b / 100)))
        if not derived:
            for a in source_ints:
                for b in source_ints:
                    if a > 0 and 0 < b <= 100:
                        if int(round(a * (b / 100))) == target:
                            derived = True
                            break
                if derived:
                    break

        if derived:
            supported.append(num_str)
        else:
            unsupported.append(num_str)

    return supported, unsupported


@dataclass(frozen=True)
class SemanticBinding:
    claim: str
    refs: tuple[int, ...]
    is_structural: bool
    semantic_bp: int
    derived_numbers: tuple[str, ...]
    unsupported_numbers: tuple[str, ...]
    passed: bool
    diagnostic_code: str | None


@dataclass(frozen=True)
class SemanticGovernanceResult:
    all_passed: bool
    refusal_required: bool
    bindings: tuple[SemanticBinding, ...]
    average_semantic_bp: int
    unsupported_claims: tuple[str, ...]
    diagnostic_suggestions: tuple[str, ...]


def evaluate_semantic_governance(
    answer: str,
    pages_by_ref: dict[int, dict],
    min_entailment_bp: int = DEFAULT_ENTAILMENT_FLOOR_BP,
    marker: str = "ش",
) -> SemanticGovernanceResult:
    """تقييم الجواب بالحوكمة التوليدية المعززة بالاستلزام الدلالي والجبري."""
    cite = cite_pattern(marker)
    claims = split_claims(answer, marker)

    bindings: list[SemanticBinding] = []
    unsupported_claims: list[str] = []
    suggestions: list[str] = []
    total_bp = 0
    substantive_count = 0

    for claim_text in claims:
        stripped = claim_text.strip()
        if not stripped:
            continue

        refs = refs_in(claim_text, marker)
        bare = cite.sub(" ", claim_text).strip()
        is_struct = is_structural_phrase(bare)

        # استثناء الهياكل والمقدمات التنسيقية
        if is_struct:
            bindings.append(SemanticBinding(
                claim=stripped,
                refs=refs,
                is_structural=True,
                semantic_bp=10000,
                derived_numbers=(),
                unsupported_numbers=(),
                passed=True,
                diagnostic_code=None,
            ))
            continue

        # إذا خلا مقطع موضوعي من الإسناد
        if not refs:
            bindings.append(SemanticBinding(
                claim=stripped,
                refs=(),
                is_structural=False,
                semantic_bp=0,
                derived_numbers=(),
                unsupported_numbers=tuple(numbers_in(bare)),
                passed=False,
                diagnostic_code="citation_missing",
            ))
            unsupported_claims.append(stripped)
            suggestions.append(f"المقطع «{stripped[:60]}...» يحتاج إلى إسناده لرقم الشاهد المناسب.")
            continue

        # جمع نصوص الشواهد المشار إليها
        ref_text_parts: list[str] = []
        citation_numbers: list[str] = []
        for r in refs:
            p = pages_by_ref.get(r, {})
            body = p.get("text", "")
            meta = " ".join(str(p.get(k, "")) for k in ("part", "locus"))
            ref_text_parts.append(body)
            ref_text_parts.append(meta)
            citation_numbers.extend(numbers_in(body))
            citation_numbers.extend(numbers_in(meta))

        combined_haystack = " ".join(ref_text_parts)
        claim_toks = content_tokens(normalize(bare))
        claim_nums = numbers_in(bare)

        # فحص الأرقام والاشتقاق الجبري أولاً
        supported_nums, unsupp_nums = verify_numerical_derivation(claim_nums, citation_numbers)
        derived_nums = [n for n in supported_nums if n not in set(citation_numbers)]

        # فحص الاستلزام الدلالي مع مراعاة الأرقام المشتقة
        sem_bp = semantic_match_basis_points(
            claim_toks,
            combined_haystack,
            has_derived_nums=bool(derived_nums),
        )
        total_bp += sem_bp
        substantive_count += 1

        # الحكم النهائي على المقطع
        passed = True
        diag_code = None

        if unsupp_nums:
            passed = False
            diag_code = "citation_number_unsupported"
            unsupported_claims.append(stripped)
            suggestions.append(f"الرقم {unsupp_nums} غير موجود في الشاهد {refs} ولا يمكن اشتقاقه منه.")
        elif sem_bp < min_entailment_bp:
            passed = False
            diag_code = "citation_entailment_low"
            unsupported_claims.append(stripped)
            suggestions.append(f"درجة الاستلزام الدلالي ({sem_bp} bp) دون العتبة المطلوبة ({min_entailment_bp} bp).")

        bindings.append(SemanticBinding(
            claim=stripped,
            refs=refs,
            is_structural=False,
            semantic_bp=sem_bp,
            derived_numbers=tuple(derived_nums),
            unsupported_numbers=tuple(unsupp_nums),
            passed=passed,
            diagnostic_code=diag_code,
        ))

    avg_bp = total_bp // max(1, substantive_count) if substantive_count > 0 else 10000
    all_passed = len(unsupported_claims) == 0
    refusal_required = not all_passed and any(b.diagnostic_code == "citation_number_unsupported" for b in bindings)

    return SemanticGovernanceResult(
        all_passed=all_passed,
        refusal_required=refusal_required,
        bindings=tuple(bindings),
        average_semantic_bp=avg_bp,
        unsupported_claims=tuple(unsupported_claims),
        diagnostic_suggestions=tuple(suggestions),
    )
