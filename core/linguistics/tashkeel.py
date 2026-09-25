# -*- coding: utf-8 -*-
"""محرك التشكيل الصواتي وفك اللبس الدلالي (core/linguistics/tashkeel.py).

يقوم هذا المكون بـ:
1. تجريد التشكيل والتحقق من وجود الحركات.
2. قياس دقة التطابق التشكيلي بنقاط الأساس (0..10,000 bp) دون كسور عشرية (ق23).
3. فك اللبس بين المتشابهات رسماً بناءً على القرائن السياقية في المتون القانونية والمعجمية.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# علامات التشكيل العربية: الفتحة، الضمة، الكسرة، السكون، التنوينات، والشدة
_TASHKEEL_REGEX = re.compile(r"[ًٌٍَُِّْٰـ]")
_PUNCTUATION_REGEX = re.compile(r"[^\w\s\u0621-\u064A]")


class DisambiguationResult(NamedTuple):
    vocalized: str
    confidence_bp: int
    sense: str


def strip_tashkeel(text: str) -> str:
    """تجريد النص من الحركات والتشكيل."""
    return _TASHKEEL_REGEX.sub("", text)


def has_tashkeel(text: str) -> bool:
    """التحقق من اشتمال النص على أي علامة تشكيل."""
    return bool(_TASHKEEL_REGEX.search(text))


def tashkeel_similarity_bp(text1: str, text2: str) -> int:
    """قياس نسبة التطابق التشكيلي بين نصين بنقاط الأساس (0..10000 bp).

    إذا اختلف الرسم المجرد: النتيجة صفر.
    إذا تطابق الرسم المجرد: تُقاس نسبة تطابق الحركات على كل حرف.
    """
    stripped1 = strip_tashkeel(text1)
    stripped2 = strip_tashkeel(text2)

    if stripped1 != stripped2:
        return 0

    if not stripped1:
        return 10000

    # استخراج الحركات لكل حرف مجرد
    def extract_char_marks(text: str) -> list[tuple[str, str]]:
        marks = []
        curr_char = ""
        curr_marks: list[str] = []
        for ch in text:
            if _TASHKEEL_REGEX.match(ch):
                curr_marks.append(ch)
            else:
                if curr_char:
                    marks.append((curr_char, "".join(sorted(curr_marks))))
                curr_char = ch
                curr_marks = []
        if curr_char:
            marks.append((curr_char, "".join(sorted(curr_marks))))
        return marks

    marks1 = extract_char_marks(text1)
    marks2 = extract_char_marks(text2)

    if len(marks1) != len(marks2):
        return 0

    total_chars = len(marks1)
    matches = sum(1 for m1, m2 in zip(marks1, marks2) if m1[1] == m2[1])

    # حساب نقاط الأساس integer basis points (0..10,000)
    return (matches * 10000) // total_chars


# جدول القرائن الدلالية لفك اللبس بين الكلمات المشتركة رسماً
_HOMOGRAPHS_RULES: dict[str, list[tuple[str, tuple[str, ...], int, str]]] = {
    "عقد": [
        (
            "عَقْد",
            ("عمل", "ايجار", "إيجار", "نقل", "تامين", "تأمين", "شريعة", "متعاقد", "ابرام", "إبرام",
             "بنود", "شروط", "محرر", "وثيقة", "فسخ", "صحة", "بطلان", "التزام", "أطراف", "طرفي"),
            9800,
            "وثيقة_واتفاق_قانوني"
        ),
        (
            "عِقْد",
            ("عشرين", "عشرون", "ثلاثين", "ثلاثون", "اربعين", "أربعين", "خمسين", "خمسون",
             "سنوات", "سنة", "سنين", "قرن", "عقود", "زمان", "الماضي", "ماضي"),
            9500,
            "عقد_زمني_عشر_سنوات"
        ),
        (
            "عَقَدَ",
            ("اجتماع", "مؤتمر", "جلسة", "قران", "حاجبين", "نية", "عزم", "اتفاقا"),
            9000,
            "فعل_إبرام_أو_انعقاد"
        ),
    ],
    "حكم": [
        (
            "حُكْم",
            ("محكمة", "قضائي", "استئناف", "دستوري", "صادر", "تنفيذي", "براءة", "إدانة", "مادة",
             "نظام", "لائحة", "احكام", "أحكام", "قانوني", "نافذ", "قطعي"),
            9800,
            "قضاء_أو_قرار_نظامي"
        ),
        (
            "حَكَم",
            ("طرفين", "نزاع", "تحكيم", "فصل", "اختيار", "مباراة", "حكمان", "عدل", "محكم"),
            9200,
            "قاض_أو_مُحَكَّم"
        ),
        (
            "حَكَمَ",
            ("بينهم", "بالعدل", "بالبراءة", "بالإدانة", "على", "له"),
            9000,
            "فعل_قضائي"
        ),
    ],
    "سفن": [
        (
            "سُفُن",
            ("ملاحة", "بحرية", "ميناء", "حمولة", "ركاب", "صيد", "نزهة", "ناقلة", "سفينة", "بواخر",
             "مراكب", "مرفأ", "عرض", "البحر", "شحن"),
            9900,
            "مراكب_بحرية_جمع_سفينة"
        ),
        (
            "سَفَّان",
            ("ربان", "طاقم", "صانع", "قائد", "ملاح", "بحار"),
            9200,
            "ملاح_أو_صانع_سفن"
        ),
        (
            "سَفَنَ",
            ("قشر", "نحت", "سحل", "حك"),
            8500,
            "فعل_نحت_وقشر"
        ),
    ],
    "بحر": [
        (
            "بَحْر",
            ("مياه", "اقليمية", "إقليمية", "ساحل", "شاطئ", "احمر", "أحمر", "خليج", "عمق", "امواج",
             "أمواج", "ملاحة", "اعالي", "أعالي"),
            9800,
            "يم_أو_مياه_بحرية"
        ),
        (
            "بَحَّار",
            ("طاقم", "سفينة", "خدمة", "شهادة", "تأهيل", "ربان", "ملاح", "عامل", "سجل"),
            9200,
            "ملاح_عامل_في_البحر"
        ),
    ],
    "علم": [
        (
            "عَلَم",
            ("سعودي", "اجنبي", "أجنبي", "دولة", "سفينة", "رفع", "تسجيل", "جنسية", "راية", "سارية"),
            9800,
            "راية_أو_جنسية_السفينة"
        ),
        (
            "عِلْم",
            ("دراسة", "بحث", "معرفة", "لغويات", "اصول", "أصول", "فقه", "يقين", "معلومات"),
            9500,
            "معرفة_ودراية"
        ),
        (
            "عَلِمَ",
            ("ادرك", "أدرك", "عرف", "اخبر", "أخبر", "به"),
            9000,
            "فعل_دراية"
        ),
    ],
    "نقل": [
        (
            "نَقْل",
            ("بحري", "بري", "بضائع", "ركاب", "حمولة", "خدمات", "ترخيص", "وسائط", "شاحنات"),
            9800,
            "خدمة_المواصلات_والشحن"
        ),
        (
            "نَقَلَ",
            ("البضاعة", "الركاب", "الخبر", "الملكية", "الى", "إلى"),
            9000,
            "فعل_تحويل_المكان"
        ),
    ],
}


def disambiguate_homographs(word: str, context: str) -> DisambiguationResult:
    """فك اللبس بين المتشابهات رسماً استناداً إلى الكلمات المفتاحية في السياق المحيط."""
    norm_word = strip_tashkeel(word).replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    if norm_word.startswith("ال") and len(norm_word) > 4:
        norm_word = norm_word[2:]

    if norm_word not in _HOMOGRAPHS_RULES:
        # كلمة لا لبس مسجل فيها، تعاد بأعلى ثقة افتراضية
        return DisambiguationResult(vocalized=word, confidence_bp=6000, sense="معنى_عام")

    norm_context = strip_tashkeel(context).lower()
    rules = _HOMOGRAPHS_RULES[norm_word]

    best_match: tuple[str, int, str] | None = None
    max_hits = 0

    for vocalized, clues, confidence_bp, sense in rules:
        hits = sum(1 for clue in clues if clue in norm_context)
        if hits > max_hits:
            max_hits = hits
            best_match = (vocalized, confidence_bp, sense)

    if best_match and max_hits > 0:
        return DisambiguationResult(*best_match)

    # النتيجة الافتراضية الأولى في حال غياب القرائن
    default = rules[0]
    return DisambiguationResult(vocalized=default[0], confidence_bp=6500, sense=default[3])
