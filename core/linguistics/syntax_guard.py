# -*- coding: utf-8 -*-
"""المدقق النحوي والتركيبي لمخرجات اللغة العربية (core/linguistics/syntax_guard.py).

يقوم هذا المكون بفحص الجمل والتراكيب آلياً لكشف اللحن الإعرابي والأخطاء التركيبية
الشائعة في مخرجات النماذج اللغوية (مثل خرم الجزم، ثبوت النون بعد النواصب، أو
خلط المرفوع بالمنصوب في أسماء إنّ وأخواتها وأخبار كان وأخواتها وحروف الجر).
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from core.linguistics.tashkeel import strip_tashkeel


@dataclass(frozen=True)
class SyntaxIssue:
    """وصف العلة النحوية وموضعها والتصويب المقترح."""
    rule: str
    span: str
    message: str
    severity: str  # "error" أو "warning"
    suggested_fix: str | None


@dataclass(frozen=True)
class SyntaxVerdict:
    """نتيجة التدقيق النحوي الشاملة بنقاط الأساس."""
    valid: bool
    score_bp: int
    issues: tuple[SyntaxIssue, ...]


# أدوات الجزم
_JAZM_PARTICLES = r"(?:لم|لما|لا\s+الناهية|لـ)"
# أدوات النصب
_NASB_PARTICLES = r"(?:لن|كي|لكي|حتى|أن)"
# إن وأخواتها
_INNA_PARTICLES = r"(?:إنّ?|أنّ?|كأنّ?|ليت|لعلّ)"
# كان وأخواتها
_KANA_VERBS = r"(?:كان|كانت|كانوا|أصبح|أصبحت|أصبحوا|أمسى|أمسوا|صار|صارت|صاروا|ليس|ليست)"
# حروف الجر
_PREPOSITIONS = r"(?:في|من|إلى|الى|على|عن|مع|بـ|لـ|لدى|عند)"

# أفعال خمسة بثبوت النون (يفعلون/تفعلون/تفعلين)
_FIVE_VERBS_MARFOO = re.compile(r"\b([يتا][ء-ي]{2,}(?:ون|ين))\b")

# جمع مذكر سالم مرفوع بالواو والنون
_MASC_PLURAL_MARFOO = re.compile(r"\b([ء-ي]{3,}ون)\b")
# جمع مذكر سالم مجرور/منصوب بالياء والنون
_MASC_PLURAL_MANSOOB = re.compile(r"\b([ء-ي]{3,}ين)\b")

# مثنى مرفوع بالألف والنون
_DUAL_MARFOO = re.compile(r"\b([ء-ي]{3,}ان)\b")
# مثنى مجرور/منصوب بالياء والنون
_DUAL_MANSOOB = re.compile(r"\b([ء-ي]{3,}ين)\b")


def check_syntax(text: str) -> SyntaxVerdict:
    """تدقيق النص نحويّاً واستخراج العيوب التركيبية الظاهرة."""
    clean = text.strip()
    if not clean:
        return SyntaxVerdict(valid=True, score_bp=10000, issues=())

    issues: list[SyntaxIssue] = []

    # 1. فحص أدوات الجزم مع الأفعال الخمسة (ثبوت النون خطأ جلي)
    # مثال: "لم يفعلون" -> خطأ
    jazm_pattern = re.compile(r"\b(لم|لما|لا)\s+([يت][ء-ي]{2,}ون)\b")
    for match in jazm_pattern.finditer(clean):
        particle = match.group(1)
        verb = match.group(2)
        # استثناء الأسماء الشبيهة (مثل "لم تدوين")
        if verb.endswith("ون") and len(verb) >= 4:
            fix = verb[:-1] + "ا"  # يفعلون -> يفعلوا
            issues.append(SyntaxIssue(
                rule="jazm_five_verbs",
                span=match.group(0),
                message=f"دخول أداة الجزم «{particle}» على فعل مع ثبوت النون «{verb}» وحقها الحذف.",
                severity="error",
                suggested_fix=f"{particle} {fix}",
            ))

    # 2. فحص أدوات النصب مع الأفعال الخمسة
    # مثال: "لن يفعلون" -> خطأ
    nasb_pattern = re.compile(r"\b(لن|كي|لكي|حتى|أن|ان)\s+([يت][ء-ي]{2,}ون)\b")
    for match in nasb_pattern.finditer(clean):
        particle = match.group(1)
        verb = match.group(2)
        fix = verb[:-1] + "ا"
        issues.append(SyntaxIssue(
            rule="nasb_five_verbs",
            span=match.group(0),
            message=f"دخول أداة النصب «{particle}» على فعل مع ثبوت النون «{verb}» وحقها الحذف.",
            severity="error",
            suggested_fix=f"{particle} {fix}",
        ))

    # 3. فحص حروف الجر يليها جمع مذكر سالم أو مثنى مرفوع بالواو أو الألف
    # مثال: "في المهندسون" -> خطأ، الصواب "في المهندسين"
    prep_plural_pattern = re.compile(r"\b(في|من|إلى|الى|على|عن|بين)\s+(ال[ء-ي]{2,}ون)\b")
    for match in prep_plural_pattern.finditer(clean):
        prep = match.group(1)
        noun = match.group(2)
        fix = noun[:-2] + "ين"
        issues.append(SyntaxIssue(
            rule="preposition_governs_mansoob_majroor",
            span=match.group(0),
            message=f"حرف الجر «{prep}» يقتضي جر الاسم بعده «{noun}» بالياء لا بالواو.",
            severity="error",
            suggested_fix=f"{prep} {fix}",
        ))

    prep_dual_pattern = re.compile(r"\b(في|من|إلى|الى|على|عن|بين)\s+(ال[ء-ي]{2,}ان)\b")
    for match in prep_dual_pattern.finditer(clean):
        prep = match.group(1)
        noun = match.group(2)
        # استثناء الألف والنون الأصلية كـ "السلطان" و"القرآن" و"الإنسان" و"البيان"
        if noun in ("السلطان", "القران", "القرآن", "الإنسان", "الانسان", "البيان", "العنوان", "المكان", "الزمان", "الطوفان", "الرهان", "الميدان", "الجريان"):
            continue
        fix = noun[:-2] + "ين"
        issues.append(SyntaxIssue(
            rule="preposition_governs_dual",
            span=match.group(0),
            message=f"حرف الجر «{prep}» يقتضي جر المثنى بعده «{noun}» بالياء لا بالألف.",
            severity="warning",
            suggested_fix=f"{prep} {fix}",
        ))

    # 4. فحص اسم إنّ وأخواتها (حقه النصب بالياء في الجمع والمثنى)
    # مثال: "إن المعلمون حاضرون" -> خطأ
    inna_pattern = re.compile(r"\b(إن|ان|إنّ|أنّ|كأن|ليت|لعل)\s+(ال[ء-ي]{2,}ون)\b")
    for match in inna_pattern.finditer(clean):
        particle = match.group(1)
        noun = match.group(2)
        fix = noun[:-2] + "ين"
        issues.append(SyntaxIssue(
            rule="inna_ism_must_be_mansoob",
            span=match.group(0),
            message=f"اسم «{particle}» المباشر «{noun}» حقه النصب بالياء في جمع المذكر السالم.",
            severity="error",
            suggested_fix=f"{particle} {fix}",
        ))

    # 5. فحص خبر كان وأخواتها المباشر بعد الاسم المعرف
    # مثال: "كان المهندسون حاضرون" -> خطأ، الصواب "كان المهندسون حاضرين"
    kana_pattern = re.compile(r"\b(كان|أصبح|أمسى|صار|ليس)\s+(ال[ء-ي]{2,}ون)\s+([ء-ي]{2,}ون)\b")
    for match in kana_pattern.finditer(clean):
        verb = match.group(1)
        ism = match.group(2)
        khabar = match.group(3)
        fix = khabar[:-2] + "ين"
        issues.append(SyntaxIssue(
            rule="kana_khabar_must_be_mansoob",
            span=match.group(0),
            message=f"خبر الفعل الناسخ «{verb}» وهو «{khabar}» حقه النصب بالياء في جمع المذكر السالم.",
            severity="error",
            suggested_fix=f"{verb} {ism} {fix}",
        ))

    # احتساب النتيجة بنقاط الأساس: خصم 2000 نقطة لكل خطأ و1000 لكل تحذير
    penalty_bp = sum(2000 if i.severity == "error" else 1000 for i in issues)
    score_bp = max(0, 10000 - penalty_bp)
    valid = len(issues) == 0

    return SyntaxVerdict(
        valid=valid,
        score_bp=score_bp,
        issues=tuple(issues)
    )
