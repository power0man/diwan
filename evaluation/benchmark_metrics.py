"""مقاييس بنك الجودة المقيس م١٤ — الأبعاد الأربعة:
1. الصحة (Factuality): استدعاء الحقائق والأرقام الذهبية الحرفية.
2. الاكتمال (Completeness): استيفاء شقوق السؤال وشروطه.
3. الإسناد (Attribution): فحص نسبة الادعاءات المسندة بدقة وفق ق٢٦.
4. أمانة الترجمة والاصطلاح (Translation Fidelity): التزام مصطلحات المسرد المعتمد.
"""
from __future__ import annotations

import re
from typing import Any

from core.attribution import (
    DEFAULT_OVERLAP_FLOOR,
    bind_claims,
    content_tokens,
    normalize,
    numbers_in,
    unsupported,
)
from nodes.linguistics.node import term_in_text


def _norm(text: str) -> str:
    """تطبيع نصي خفيف للمطابقة (إزالة التشكيل وتوحيد الألفات)."""
    t = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)
    t = re.sub(r"[إأآٱ]", "ا", t)
    t = re.sub(r"[ة]", "ه", t)
    t = re.sub(r"[ى]", "ي", t)
    return t.strip()


# أدواتُ النفي التي تقلب حكمَ الجملة وهي باقيةٌ بحروفها كلِّها. وهذا هو
# بالضبط ما أعمى المقياسَ: المطابقةُ بالاحتواء (`fact in answer`) تبقى صادقةً
# إذا سبقتها أداةُ نفي، فنال «لا يجب على الربان الإبلاغ» درجةَ «يجب على الربان
# الإبلاغ» كاملةً — وهي الحالة المرصودة في docs/EVALUATION-20260923.md (العثرة ب).
# والصورُ الملتحمة («ولا» و«فلا» و«بلا») تُذكر صراحةً لأن التقطيعَ بالمسافات
# لا يفصلها عمّا بعدها.
NEGATION_PARTICLES = frozenset({
    "لا", "لم", "لن", "ليس", "ليست", "لست", "لسنا", "لسن",
    "غير", "عدم", "بدون", "دون", "سوي",
    "ولا", "فلا", "بلا", "ولم", "ولن", "وليس", "فليس",
})

_EDGE_PUNCTUATION = "،.:;!؟()[]{}\"'«»ـ-—"


def _negation_before(text: str, at: int) -> str | None:
    """أداةُ النفي الملاصقة لما قبل الموضع، أو None.

    «الملاصقة» قيدٌ مقصود: لا يُفتَّش عن نفيٍ في الجملة كلها، بل عن أداةٍ تسبق
    الجملةَ الذهبية مباشرةً. فالبحثُ الواسع يَعُدّ «لا» في شقٍّ آخر من الجواب
    قلبًا لحكمٍ لم تمسَّه، وهذا خطأٌ في الاتجاه المعاكس.
    """
    prefix = text[:at].rstrip()
    if not prefix:
        return None
    last = prefix.split()[-1].strip(_EDGE_PUNCTUATION)
    return last if last in NEGATION_PARTICLES else None


def _starts_negated(fact_norm: str) -> bool:
    """الجملةُ الذهبية المنفيّةُ أصلًا لا يُعَدّ نفيُها في الجواب قلبًا لها."""
    head = fact_norm.split()
    return bool(head) and head[0].strip(_EDGE_PUNCTUATION) in NEGATION_PARTICLES


def _inversion_particle(answer: str, fact: str) -> str | None:
    """الأداةُ التي قلبت الجملةَ الذهبية في الجواب، أو None.

    يُعَدّ الحكمُ مقلوبًا حين ترد الجملةُ الذهبية في الجواب و**كلُّ** مواضعها
    مسبوقةٌ بنفي. واشتراطُ «كلّها» مقصود: جوابٌ يقول «لا يجب كذا» ثم يستدرك
    «بل يجب كذا» قد أثبت الحكمَ في أحد مواضعه، فلا يُحاسَب حسابَ من نفاه.
    """
    for rendered_answer, rendered_fact in ((_norm(answer), _norm(fact)),
                                           (_clean_fact_text(answer), _clean_fact_text(fact))):
        if not rendered_fact or _starts_negated(rendered_fact):
            continue
        particles, start = [], 0
        while (at := rendered_answer.find(rendered_fact, start)) >= 0:
            particles.append(_negation_before(rendered_answer, at))
            start = at + 1
        if particles and all(particles):
            return particles[0]
    return None


def _occurs_unnegated(text_norm: str, token: str) -> int | None:
    """موضعُ أوّلِ ورودٍ للّفظ غيرِ مسبوقٍ بنفي، أو None.

    «يجوز» داخلَ «لا يجوز» ليست إباحة، و«يحظر» داخلَ «لا يحظر» ليست منعًا.
    وكان عدُّ اللفظ بمجرّد وروده يجعل الجوابَ الذي ينقل المنعَ بحروفه الصحيحة
    متناقضًا مع نفسه — ستَّ مرّاتٍ في جملةٍ واحدة — فيسقط إلى صفر وهو مصيب.
    """
    start = 0
    while (at := text_norm.find(token, start)) >= 0:
        if _negation_before(text_norm, at) is None:
            return at
        start = at + 1
    return None


# ————— ك٢٦: القطبيةُ في إعادة الصياغة —————
#
# كاشفُ القلب أعلاه (ك٤) لا يرى إلا الحقيقةَ بنصّها مسبوقةً بأداة نفي، ومسارُ
# التداخل ≥٠٫٥ (ك١٠) كان يمرّر كلَّ قلبٍ مُعاد الصياغة: «يُحظر على الربان الإبلاغ»
# و«الربان غير ملزم بالإبلاغ» و«لا يجب على الربان أن يُبلغ» تنال ١٫٠ مقابل حقيقةٍ
# توجب الإبلاغ. فتُقرأ **القطبيةُ الحُكمية** (وجوب/حظر/إباحة، ونفيُ كلٍّ منها) في
# الحقيقة وفي جُمل الجواب التي تتداخل معها، ويُعدّ الحكمُ مقلوبًا إذا لم ترد قطبيةُ
# الحقيقة في تلك الجمل ووردت قطبيةٌ تناقضها. والحدُّ معلَن: حقيقةٌ بلا علامةِ حكمٍ
# (كالأرقام) خارج هذا الكشف، وجملةٌ تحمل الحكمَ بلا علامةٍ صريحة («على الربان أن…»)
# لا تُقرأ لها قطبية فلا تُتَّهم.
_DEONTIC_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("obligation", re.compile(r"(?<!\w)(?:يجب|يتعين|يلزم|ينبغي|ملزم\w*|ملزمه|الزامي|وجوب|واجب)(?!\w)")),
    ("prohibition", re.compile(r"(?<!\w)(?:يحظر|حظر|محظور|يمنع|ممنوع|يحرم|محرم)(?!\w)")),
    ("permission", re.compile(r"(?<!\w)(?:يجوز|جائز|يسمح|مسموح|يصرح|مصرح|يحق)(?!\w)")),
)
_NEGATED_POLARITY = {"obligation": "not_obliged", "prohibition": "not_prohibited",
                     "permission": "prohibition"}
_CONFLICTING_POLARITY = {
    "obligation": {"not_obliged", "prohibition"},
    "prohibition": {"permission", "not_prohibited", "obligation"},
    "permission": {"prohibition"},
    "not_obliged": {"obligation"},
    "not_prohibited": {"prohibition"},
}
_SENTENCE_BREAK = re.compile(r"[.!?؟؛\n]+")
# «ليس الربانُ ملزمًا»: أداةُ «ليس» تسبق الاسمَ لا الخبرَ، فيُلتمس لها نافذةُ ثلاث كلمات
# قبل العلامة الاسمية وحدها (ملزم، مسموح، محظور…)، لا قبل الفعل.
_LAYSA = frozenset({"ليس", "ليست", "لست", "لسنا", "لسن", "وليس", "فليس"})
_NOMINAL_MARKER = re.compile(r"^(?:ملزم|مسموح|مصرح|جائز|محظور|ممنوع|محرم|واجب|الزامي)")


def _laysa_before(text_norm: str, at: int) -> str | None:
    tokens = text_norm[:at].split()[-3:]
    for token in tokens:
        if token.strip(_EDGE_PUNCTUATION) in _LAYSA:
            return token.strip(_EDGE_PUNCTUATION)
    return None


def deontic_polarities(text: str) -> list[tuple[str, str]]:
    """قطبياتُ الحكم في نصٍّ مع صورتها: [(«obligation», «يجب»), («not_obliged», «غير ملزم»), …]."""
    text_norm = _norm(text)
    found = []
    for polarity, pattern in _DEONTIC_MARKERS:
        for match in pattern.finditer(text_norm):
            particle = _negation_before(text_norm, match.start())
            if particle is None and _NOMINAL_MARKER.match(match.group(0)):
                particle = _laysa_before(text_norm, match.start())
            if particle:
                found.append((_NEGATED_POLARITY[polarity], f"{particle} {match.group(0)}"))
            else:
                found.append((polarity, match.group(0)))
    return found


def _fact_polarity(fact: str) -> str | None:
    """قطبيةُ الحقيقة إن كانت واحدةً واضحة، وإلا None فلا كشفَ عليها."""
    polarities = {polarity for polarity, _ in deontic_polarities(fact)}
    return next(iter(polarities)) if len(polarities) == 1 else None


def paraphrased_inversions(answer: str, golden_facts: list[str]) -> list[tuple[str, str]]:
    """حقائقُ ذهبية قُلب حكمُها في جوابٍ مُعاد الصياغة، مع صورة العلامة القالبة."""
    if not answer or not golden_facts:
        return []
    found = []
    for fact in golden_facts:
        polarity = _fact_polarity(fact)
        if polarity is None:
            continue
        marker_words = {w for _, surface in deontic_polarities(fact) for w in surface.split()}
        fact_toks = [_norm_unit(t) for t in content_tokens(fact) if t not in marker_words]
        if not fact_toks:
            continue
        seen: list[tuple[str, str]] = []
        for sentence in _SENTENCE_BREAK.split(answer):
            sentence_toks = set(_clean_fact_text(sentence).split())
            overlap = sum(1 for t in fact_toks if t in sentence_toks) / len(fact_toks)
            if overlap >= 0.5:
                seen.extend(deontic_polarities(sentence))
        if not seen or any(p == polarity for p, _ in seen):
            continue
        conflicting = [surface for p, surface in seen if p in _CONFLICTING_POLARITY[polarity]]
        if conflicting:
            found.append((fact, conflicting[0]))
    return found


def inverted_facts(answer: str, golden_facts: list[str]) -> list[tuple[str, str]]:
    """الحقائقُ الذهبية المقلوبُ معناها: بنصّها مسبوقةً بنفي (ك٤)، أو مُعادةَ الصياغة بقطبيةٍ مضادّة (ك٢٦)."""
    if not answer or not golden_facts:
        return []
    found = []
    for fact in golden_facts:
        particle = _inversion_particle(answer, fact)
        if particle:
            found.append((fact, particle))
    verbatim = {fact for fact, _ in found}
    found.extend((fact, surface) for fact, surface in paraphrased_inversions(answer, golden_facts)
                 if fact not in verbatim)
    return found


PROHIBITION_TOKENS = frozenset({"يحظر", "حظر", "لا يجوز", "يمنع", "ممنوع", "دون", "خارج", "ليس", "غير"})
PERMISSION_TOKENS = frozenset({"يجوز", "يصرح", "مسموح", "داخل", "مصرح"})

UNIT_MAP = {
    "اميال": "ميل", "ميلا": "ميل",
    "امتار": "متر", "مترا": "متر",
    "اطنان": "طن", "طنا": "طن",
    "ايام": "يوم", "يوما": "يوم",
    "اشهر": "شهر", "شهرا": "شهر",
    "سنوات": "سنة", "اعوام": "عام",
}


def _norm_unit(token: str) -> str:
    return UNIT_MAP.get(token, token)


def _clean_fact_text(t: str) -> str:
    """تنظيف نصي وتوحيد الوحدات للمطابقة الحقيقية للحقائق الذهبية."""
    norm = _norm(t)
    norm = re.sub(r"[^\w\s]", " ", norm)
    toks = [_norm_unit(w) for w in norm.split()]
    return " ".join(toks)


def _clean_arabic_stem(token: str) -> str:
    """تجريد خفيف للواحق والسوابق الشائعة لمطابقة الجذور والاشتقاقات."""
    t = _norm(token)
    if t.startswith("ال") and len(t) >= 5:
        t = t[2:]
    for p in ("و", "ف", "ب", "ل"):
        if t.startswith(p) and len(t) >= 4:
            t = t[1:]
            break
    if t.startswith("ال") and len(t) >= 5:
        t = t[2:]
    for s in ("تها", "تهم", "تهن", "تهما", "تك", "تكم", "تنا", "ها", "هم", "هن", "هما", "كم", "نا"):
        if t.endswith(s) and len(t) >= 5:
            t = t[:-len(s)]
            break
    if t.endswith("ه") or t.endswith("ت") or t.endswith("ة"):
        if len(t) >= 4:
            t = t[:-1]
    return t


def detect_contradictions(answer: str, golden_facts: list[str], ground_truth: dict | None = None) -> list[str]:
    """كشف التناقضات الصريحة والهلوَسَات المناقضة للنص الذهبي:
    ١. التناقضات العددية: ذكر أرقام متضاربة لوحدات ومفاهيم الحقيقة الذهبية دون ذكر الرقم الذهبي.
    ٢. تناقضات الإباحة والحظر: قلب المنع إلى إباحة أو العكس في سياق الحقائق الذهبية.
    """
    if not golden_facts or not answer:
        return []

    contradictions = []
    for fact, particle in inverted_facts(answer, golden_facts):
        contradictions.append(
            f"قلبُ معنى في ({fact[:40]}…): النصّ يُثبت الحكم والجوابُ ينفيه بـ«{particle}»")
    ans_norm = _norm(answer)
    ans_nums = set(numbers_in(answer))
    ans_tokens = set(_norm_unit(t) for t in content_tokens(answer))

    for fact in golden_facts:
        fact_nums = numbers_in(fact)
        fact_tokens = [_norm_unit(t) for t in content_tokens(fact)]

        # ١. التناقض العددي الصريح
        if fact_nums:
            keywords = [t for t in fact_tokens if t not in fact_nums and len(t) >= 2]
            overlap = [k for k in keywords if k in ans_tokens]
            if overlap and ans_nums:
                has_golden_num = any(fn in ans_nums for fn in fact_nums)
                if not has_golden_num:
                    conflicting = [n for n in ans_nums if n not in fact_nums]
                    if conflicting:
                        contradictions.append(
                            f"تناقض عددي في ({' '.join(overlap)}): المطلوب {fact_nums} ووردت أرقام مغايرة {conflicting[:2]}"
                        )

        # ٢. تناقض الإباحة والحظر
        has_prohib = any(p in fact for p in PROHIBITION_TOKENS)
        has_perm = any(p in fact for p in PERMISSION_TOKENS)
        if has_prohib:
            fact_kw = [t for t in fact_tokens if t not in PROHIBITION_TOKENS and len(t) >= 3]
            for kw in fact_kw:
                if kw in ans_norm:
                    # لفظُ الإباحة يُعتدّ به إن لم يكن هو نفسُه منفيًّا.
                    for perm in PERMISSION_TOKENS:
                        at = _occurs_unnegated(ans_norm, perm)
                        if at is not None and abs(at - ans_norm.find(kw)) < 40:
                            contradictions.append(f"تناقض إباحة/حظر في ({kw}): النص يقضي بالمنع وورد «{perm}»")
                            break
        elif has_perm:
            fact_kw = [t for t in fact_tokens if t not in PERMISSION_TOKENS and len(t) >= 3]
            for kw in fact_kw:
                if kw in ans_norm:
                    # والمنعُ كذلك: «لا يحظر» ليست منعًا.
                    for prohib in PROHIBITION_TOKENS:
                        at = _occurs_unnegated(ans_norm, prohib)
                        if at is not None and abs(at - ans_norm.find(kw)) < 40:
                            contradictions.append(f"تناقض إباحة/حظر في ({kw}): النص يقضي بالجواز وورد «{prohib}»")
                            break

    return contradictions


def evaluate_factuality(answer: str, golden_facts: list[str], ground_truth: dict | None = None) -> dict[str, Any]:
    """قياس بُعد الصحة: نسبة الحقائق الذهبية الحاضرة مع خصم عقوبة الهلوسة المناقضة."""
    if not golden_facts:
        return {
            "score": 1.0,
            "raw_score": 1.0,
            "hallucination_penalty": 0.0,
            "matched": [],
            "missing": [],
            "inverted": [],
            "contradictions": [],
            "total": 0,
        }

    ans_norm = _norm(answer)
    ans_clean = _clean_fact_text(answer)
    ans_toks = set(ans_clean.split())
    ans_nums = set(numbers_in(answer))
    inverted = dict(inverted_facts(answer, golden_facts))
    matched = []
    missing = []

    for fact in golden_facts:
        # الحقيقةُ المقلوبُ معناها غائبةٌ لا حاضرة، وإن وردت بحروفها كلِّها.
        # فعَدُّها حاضرةً هو الذي أعطى الجوابَ المقلوب درجةً كاملة.
        if fact in inverted:
            missing.append(fact)
            continue
        fact_norm = _norm(fact)
        fact_clean = _clean_fact_text(fact)
        if fact_norm in ans_norm or fact_clean in ans_clean:
            matched.append(fact)
        else:
            # إعادةُ الصياغة الصحيحة تُقبل بالتداخل اللفظيّ، ولا يُشترط لها رقم.
            # وكان هذا المسارُ مُعشّشًا داخل شرط الأرقام، فالحقيقةُ الخاليةُ من رقم
            # لا يُطابقُها إلا النصُّ الحرفيّ، فيُحرم الجوابُ الصحيح درجتَه لمّا صاغه بلفظه.
            # ونجاتُه حين تحوي الحقيقةُ رقمًا مصادفةٌ في صياغة الحقيقة، لا حكمٌ على صحّته.
            fact_nums = numbers_in(fact)
            # والرقمُ يبقى شرطًا لازمًا متى وُجد: حقيقةٌ فيها «ستّة أشهر» لا يُجزئُها
            # جوابٌ يذكر الأشهر بلا عدد، وإلّا مرّ تغييرُ المدّة بلا عقاب.
            if fact_nums and not all(n in ans_nums for n in fact_nums):
                missing.append(fact)
                continue
            fact_toks = [_norm_unit(t) for t in content_tokens(fact)]
            if not fact_toks or all(t in ans_toks for t in fact_toks):
                matched.append(fact)
                continue
            overlap = sum(1 for t in fact_toks if t in ans_toks) / max(len(fact_toks), 1)
            if overlap >= 0.5:
                matched.append(fact)
                continue
            missing.append(fact)

    raw_score = len(matched) / len(golden_facts)
    contradictions = detect_contradictions(answer, golden_facts, ground_truth)
    penalty = min(1.0, len(contradictions) / len(golden_facts))
    net_score = max(0.0, round(raw_score - penalty, 3))

    return {
        "score": net_score,
        "raw_score": round(raw_score, 3),
        "hallucination_penalty": round(penalty, 3),
        "contradictions": contradictions,
        "matched": matched,
        "missing": missing,
        "inverted": sorted(inverted),
        "total": len(golden_facts),
    }


def evaluate_completeness(answer: str, facets: list[str]) -> dict[str, Any]:
    """قياس بُعد الاكتمال: نسبة الشقوق والشروط المطلوبة التي تمت الإجابة عنها."""
    if not facets:
        return {"score": 1.0, "matched": [], "missing": [], "total": 0}

    ans_norm = _norm(answer)
    ans_toks = set(content_tokens(answer))
    ans_stems = set(_clean_arabic_stem(t) for t in ans_toks)
    matched = []
    missing = []

    for facet in facets:
        facet_norm = _norm(facet)
        if facet_norm in ans_norm:
            matched.append(facet)
            continue

        # نمط الترجمة: تعريب X إلى Y (المطلوب في الجواب العربي هو Y وليس X أو كلمة تعريب)
        m_tr = re.match(r"تعريب\s+(.*?)\s+إلى\s+(.*)", facet)
        if m_tr:
            target = _norm(m_tr.group(2))
            if target in ans_norm:
                matched.append(facet)
                continue
            t_toks = content_tokens(target)
            if t_toks and (sum(1 for t in t_toks if t in ans_toks or _clean_arabic_stem(t) in ans_stems) / len(t_toks)) >= 0.5:
                matched.append(facet)
                continue

        # نمط الأقواس التوضيحية: تصنيف (محتوى مطلوب)
        m_paren = re.search(r"\((.*?)\)", facet)
        if m_paren:
            inner = _norm(m_paren.group(1))
            if inner in ans_norm:
                matched.append(facet)
                continue
            in_toks = content_tokens(inner)
            if in_toks and (sum(1 for t in in_toks if t in ans_toks or _clean_arabic_stem(t) in ans_stems) / len(in_toks)) >= 0.5:
                matched.append(facet)
                continue

        facet_toks = content_tokens(facet)
        if not facet_toks:
            matched.append(facet)
            continue
        hits = sum(1 for t in facet_toks if t in ans_toks or _clean_arabic_stem(t) in ans_stems)
        coverage = hits / len(facet_toks)
        if coverage >= 0.5:
            matched.append(facet)
        else:
            missing.append(facet)

    score = len(matched) / len(facets)
    return {
        "score": round(score, 3),
        "matched": matched,
        "missing": missing,
        "total": len(facets),
    }


def evaluate_attribution(answer: str, pages_by_ref: dict[int, dict] | None) -> dict[str, Any]:
    """قياس بُعد الإسناد: نسبة الادعاءات المسندة إلى شواهدها بنجاح (ق٢٦)."""
    if not pages_by_ref:
        return {
            "score": 0.0,
            "total_claims": 0,
            "supported_claims": 0,
            "unsupported_claims": 0,
            "reasons": ["لا شواهد مسترجعة مسندة"],
        }

    bindings = bind_claims(answer, pages_by_ref)
    if not bindings:
        return {
            "score": 0.0,
            "total_claims": 0,
            "supported_claims": 0,
            "unsupported_claims": 0,
            "reasons": ["لم يتم استخراج أي ادعاء في الإجابة"],
        }

    bad = unsupported(bindings, floor=DEFAULT_OVERLAP_FLOOR)
    bad_claims = {b[0] for b in bad}
    total = len(bindings)
    supported_count = sum(1 for b in bindings if b.claim not in bad_claims)

    score = supported_count / total if total > 0 else 0.0
    return {
        "score": round(score, 3),
        "total_claims": total,
        "supported_claims": supported_count,
        "unsupported_claims": len(bad_claims),
        "reasons": [b[2] for b in bad],
    }


def evaluate_translation_fidelity(answer: str, glossary_terms: list[dict] | None) -> dict[str, Any]:
    """قياس بُعد أمانة الترجمة والاصطلاح: الالتزام بمصطلحات المسرد المعتمد.
    إذا كانت المهمة لا تحتوي مسرداً، يعاد applicable=False لعدم منح 100% وهمية للجميع."""
    if not glossary_terms:
        return {
            "applicable": False,
            "score": None,
            "matched": [],
            "missing": [],
            "total": 0,
        }

    matched = []
    missing = []

    for entry in glossary_terms:
        ar_term = entry.get("ar") or entry.get("term", "")
        if not ar_term:
            continue
        if term_in_text(ar_term, answer):
            matched.append(ar_term)
        else:
            missing.append(ar_term)

    total = len(matched) + len(missing)
    score = len(matched) / total if total > 0 else 1.0
    return {
        "applicable": True,
        "score": round(score, 3),
        "matched": matched,
        "missing": missing,
        "total": total,
    }


def evaluate_case_response(
    answer: str,
    case: dict,
    pages_by_ref: dict[int, dict] | None,
    *,
    abstained: bool = False,
    error_code: str | None = None,
) -> dict[str, Any]:
    """تقييم إجابة واحدة عبر الأبعاد الأربعة مع فصل الامتناع والعطب عن حساب الجودة.

    ثلاثُ حالات (ك٢١): **جوابٌ** يُقاس؛ و**امتناعٌ** (`abstained`) قرارُ النظام يدخل
    معدّلَ الامتناع؛ و**عطبٌ** (`errored`) رمزُ خطأٍ بلا علامةِ امتناع — لم يُقَس فيخرج
    من المقامَين معًا. وعلامةُ الامتناع من الذراع تغلب الرمز: فهي تقول إن الرمز
    سببُ امتناعٍ مُعلَن (كـ`insufficient_evidence`) لا عطبَ تشغيل.
    """
    ans_clean = (answer or "").strip()
    if error_code and not abstained:
        return {
            "abstained": False,
            "errored": True,
            "error_code": error_code,
            "factuality": None,
            "completeness": None,
            "attribution": None,
            "translation": None,
            "hallucination_penalty": 0.0,
            "meaning_inverted": [],
            "overall_score": None,
        }
    is_refusal = (
        abstained
        or not ans_clean
        or ans_clean.startswith("تعذر إكمال طلب ديوان")
        or "الشواهد غير كافية" in ans_clean
    )

    if is_refusal:
        return {
            "abstained": True,
            "errored": False,
            "error_code": error_code or "refusal",
            "factuality": None,
            "completeness": None,
            "attribution": None,
            "translation": None,
            "hallucination_penalty": 0.0,
            "meaning_inverted": [],
            "overall_score": None,
        }

    gt = case.get("ground_truth", {})
    golden_facts = gt.get("golden_facts", [])
    facets = gt.get("facets", [])
    glossary_terms = gt.get("glossary_terms", [])

    factuality = evaluate_factuality(answer, golden_facts, gt)
    completeness = evaluate_completeness(answer, facets)
    attribution = evaluate_attribution(answer, pages_by_ref)
    translation = evaluate_translation_fidelity(answer, glossary_terms)

    # حساب الجودة الإجمالية عند الإجابة:
    if translation["applicable"] and translation["score"] is not None:
        overall = (
            factuality["score"] * 0.35 +
            completeness["score"] * 0.30 +
            attribution["score"] * 0.15 +
            translation["score"] * 0.20
        )
    else:
        overall = (
            factuality["score"] * 0.45 +
            completeness["score"] * 0.35 +
            attribution["score"] * 0.20
        )

    # قلبُ المعنى عيبٌ مُسقِط، لا خصمٌ من درجة. فالجوابُ الذي يقول للربان «لا
    # يجب الإبلاغ» والنظامُ يوجبه ليس جوابًا متوسّط الجودة: هو الضررُ نفسه في
    # ثوب الجواب. ولو تُرك خصمًا لبقي ينال درجةَ الاكتمال والإسناد كاملةً —
    # أي ٥٥٪ — لأنه يُحسن عرضَ ما يقلبه. والمستودعُ يسمّي هذا: «جوابٌ يحمل
    # زينةَ الإسناد بلا حقيقته أسوأ من جوابٍ بلا إسناد».
    inverted = [fact for fact, _ in inverted_facts(answer, golden_facts)]
    if inverted:
        overall = 0.0

    return {
        "abstained": False,
        "errored": False,
        "error_code": None,
        "meaning_inverted": inverted,
        "factuality": factuality,
        "completeness": completeness,
        "attribution": attribution,
        "translation": translation,
        "hallucination_penalty": factuality.get("hallucination_penalty", 0.0),
        "overall_score": round(overall, 3),
    }

