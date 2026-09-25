# -*- coding: utf-8 -*-
"""المحلل الصرفي العربي الأصيل لنواة ديوان (core/linguistics/morphology.py).

يقوم هذا المحلل بربط المشتقات بجذورها (ثلاثية ومضاعفة رباعية)، وتحديد الأوزان الصرفية،
وتفكيك الزوائد والضمائر المتصلة، ورد جموع التكسير لمفرداتها.

مستقل ومحلي بالكامل:
- لا يعتمد على نماذج خارجية ولا استدعاءات شبكية.
- حتمي ويعلن أسباب التعذر ولا يخمن (القرار ق23 وق41).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterator

# التطبيع المعلن: حذفُ الحركات والتطويل، وتوحيدُ الألف والياء والتاء
_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
_ARABIC_ONLY = re.compile(r"\A[ء-ي]+\Z")
_WEAK_LETTERS = frozenset("اوي")

UNDETERMINED = "root_undetermined"
NOT_ARABIC = "not_arabic_script"
TOO_SHORT = "stem_too_short"

# السوابق في مجموعتين، والفرقُ بينهما أمانُ الاقتطاع لا طولُه:
DEFINITE_PREFIXES: tuple[str, ...] = (
    "وبال", "فبال", "وكال", "فكال", "بال", "كال", "وال", "فال", "لل", "ال",
)
SINGLE_PREFIXES: tuple[str, ...] = ("و", "ف", "ب", "ك", "ل", "س")
PREFIXES: tuple[str, ...] = DEFINITE_PREFIXES + SINGLE_PREFIXES

# اللواحق المتّصلة، أطولُها أوّلًا
SUFFIXES: tuple[str, ...] = (
    "هما", "كما", "هم", "هن", "كم", "كن", "ها", "نا", "ني", "ات", "ان",
    "ون", "ين", "وا", "تم", "تن", "ه", "ك", "ي",
)

MIN_STEM = 3

# القوالب: (الاسم، النمط). الترتيبُ من الأخصّ إلى الأعمّ، فأوّلُ مطابقٍ يفوز
TEMPLATES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern)) for name, pattern in (
        ("استفعال", r"\Aاست(.)(.)ا(.)\Z"),
        ("استفعل", r"\Aاست(.)(.)(.)\Z"),
        ("مستفعل", r"\Aمست(.)(.)(.)\Z"),
        ("انفعال", r"\Aان(.)(.)ا(.)\Z"),
        ("انفعل", r"\Aان(.)(.)(.)\Z"),
        ("افتعال", r"\Aا(.)ت(.)ا(.)\Z"),
        ("افتعل", r"\Aا(.)ت(.)(.)\Z"),
        ("تفعيل", r"\Aت(.)(.)ي(.)\Z"),
        ("تفاعل", r"\Aت(.)ا(.)(.)\Z"),
        ("مفعول", r"\Aم(.)(.)و(.)\Z"),
        ("مفاعل", r"\Aم(.)ا(.)(.)\Z"),   # جمعُ تكسير: مكاتب ← كتب
        ("مفعله", r"\Aم(.)(.)(.)ه\Z"),
        ("مفعل", r"\Aم(.)(.)(.)\Z"),
        ("فواعل", r"\A(.)وا(.)(.)\Z"),   # جمعُ تكسير: شواهد ← شهد
        ("فعائل", r"\A(.)(.)ا[ئي](.)\Z"),  # جمعُ تكسير: رسائل ← رسل
        ("افعال", r"\Aا(.)(.)ا(.)\Z"),   # جمعُ تكسير: اقلام ← قلم
        ("افعل", r"\Aا(.)(.)(.)\Z"),
        ("فعال", r"\A(.)(.)ا(.)\Z"),
        ("فعيل", r"\A(.)(.)ي(.)\Z"),
        # الرباعيُّ المضاعف وحدَه (فعفع: زلزل، وسوس، زقزق) يسبق فعول لحماية وسوس
        ("فعفع", r"\A(?P<root>(.)(.)\2\3)\Z"),
        ("فعول", r"\A(.)(.)و(.)\Z"),
        ("فاعل", r"\A(.)ا(.)(.)\Z"),
        ("فعله", r"\A(.)(.)(.)ه\Z"),
        ("فعل", r"\A(.)(.)(.)\Z"),
    )
)

BARE_TEMPLATES = frozenset({"فعل", "فعله"})

# معجم جموع التكسير الشائعة وردها إلى المفرد (Lemmatization of Broken Plurals)
_RAW_BROKEN_PLURALS: dict[str, str] = {
    "سفن": "سفينة",
    "موانئ": "ميناء",
    "مواني": "ميناء",
    "أنظمة": "نظام",
    "انظمة": "نظام",
    "انظمه": "نظام",
    "لوائح": "لائحة",
    "أحكام": "حكم",
    "احكام": "حكم",
    "مراكب": "مركب",
    "شواهد": "شاهد",
    "وثائق": "وثيقة",
    "بحار": "بحر",
    "بحور": "بحر",
    "عقود": "عقد",
    "قوانين": "قانون",
    "مكاتب": "مكتب",
    "رسائل": "رسالة",
    "أقلام": "قلم",
    "اقلام": "قلم",
    "أعضاء": "عضو",
    "اعضاء": "عضو",
    "أطراف": "طرف",
    "اطراف": "طرف",
    "حقوق": "حق",
    "شروط": "شرط",
    "مواد": "مادة",
    "رجال": "رجل",
    "كتب": "كتاب",
    "علوم": "علم",
    "أعمال": "عمل",
    "اعمال": "عمل",
    "أموال": "مال",
    "اموال": "مال",
    "أصول": "اصل",
    "اصول": "اصل",
    "فروع": "فرع",
    "قواعد": "قاعدة",
    "مفاهيم": "مفهوم",
    "مشاريع": "مشروع",
    "معاجم": "معجم",
    "أوزان": "وزن",
    "اوزان": "وزن",
    "جذور": "جذر",
    "أفعال": "فعل",
    "افعال": "فعل",
    "أسماء": "اسم",
    "اسماء": "اسم",
    "حروف": "حرف",
    "دول": "دولة",
    "طرق": "طريق",
    "صناع": "صانع",
    "تجار": "تاجر",
    "ركاب": "راكب",
    "سكان": "ساكن",
    "قضاة": "قاضي",
    "ولاة": "والي",
    "دعاوى": "دعوى",
    "معايير": "معيار",
    "خرائط": "خريطة",
    "بضائع": "بضاعة",
    "موانع": "مانع",
    "مخاطر": "خطر",
    "أرباح": "ربح",
    "ارباح": "ربح",
    "خسائر": "خسارة",
    "ديون": "دين",
    "إجراءات": "إجراء",
    "اجراءات": "اجراء",
    "مسؤوليات": "مسؤولية",
    "صلاحيات": "صلاحية",
}


def normalize(text: str) -> str:
    """التطبيع المعلن: حذفُ الحركات والتطويل، وتوحيدُ الألف والياء والتاء."""
    return _DIACRITICS.sub("", text).translate(_ALEF)


BROKEN_PLURALS_MAP: dict[str, str] = {
    normalize(k): v for k, v in _RAW_BROKEN_PLURALS.items()
}


@dataclass(frozen=True)
class MorphologicalAnalysis:
    """تحليلُ كلمةٍ واحدة. `root is None` يعني: لم يُحدَّد، والسببُ في `reason`."""
    word: str
    normalized: str
    prefixes: tuple[str, ...]
    stem: str
    suffixes: tuple[str, ...]
    root: str | None
    pattern: str | None
    lemma: str
    is_plural: bool
    weak: bool
    reason: str | None


# الاسم التوافقي للإصدارات السابقة
Analysis = MorphologicalAnalysis


def _strip_suffixes(stem: str) -> tuple[str, tuple[str, ...]]:
    """اقتطاعُ اللواحق المتكرّرة، بلا أن ينزل الجذع دون ثلاثة أحرف."""
    removed: list[str] = []
    changed = True
    while changed:
        changed = False
        for suffix in SUFFIXES:
            if stem.endswith(suffix) and len(stem) - len(suffix) >= MIN_STEM:
                removed.append(suffix)
                stem = stem[:-len(suffix)]
                changed = True
                break
    return stem, tuple(removed)


def _candidates(normalized: str) -> Iterator[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    """قراءاتُ الكلمة مرتّبةً من الأسلم إلى الأخطر؛ أوّلُ ما يطابق قالبًا يفوز."""
    yield (), normalized, ()
    stem, suffixes = _strip_suffixes(normalized)
    if stem != normalized:
        yield (), stem, suffixes
    for group in (DEFINITE_PREFIXES, SINGLE_PREFIXES):
        for prefix in group:
            if not normalized.startswith(prefix):
                continue
            rest = normalized[len(prefix):]
            if len(rest) < MIN_STEM:
                continue
            yield (prefix,), rest, ()
            stem, suffixes = _strip_suffixes(rest)
            if stem != rest:
                yield (prefix,), stem, suffixes


def singularize_broken_plural(word: str) -> str:
    """رد جمع التكسير إلى مفرده المعجمي إن وجد."""
    norm = normalize(word)
    # فحص الكلمة كما هي
    if norm in BROKEN_PLURALS_MAP:
        return BROKEN_PLURALS_MAP[norm]

    # فحص الكلمة بعد تجريد أل التعريف
    if norm.startswith("ال") and len(norm) > 4:
        bare = norm[2:]
        if bare in BROKEN_PLURALS_MAP:
            return "ال" + BROKEN_PLURALS_MAP[bare]

    return norm


def is_plural(word: str, pattern: str | None, suffixes: tuple[str, ...]) -> bool:
    """التحقق مما إذا كانت اللفظة جمعاً (تكسير أو سالم)."""
    norm = normalize(word)
    bare = norm[2:] if norm.startswith("ال") else norm
    if bare in BROKEN_PLURALS_MAP:
        return True
    if (word.endswith("ة") or norm.endswith("ه")) and "ين" in suffixes:
        return False
    if any(s in ("ون", "ين", "ات") for s in suffixes):
        return True
    if pattern in ("مفاعل", "فواعل", "فعائل", "افعال", "فعول"):
        return True
    return False


def segment(word: str) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    """القراءةُ المختارة للكلمة: السوابقُ والجذعُ واللواحق."""
    result = analyze(word)
    return result.prefixes, result.stem, result.suffixes


def analyze(word: str) -> MorphologicalAnalysis:
    """تحليلُ كلمةٍ إلى (سوابق، جذع، لواحق، جذر، وزن، مفرد) — أو سببِ تعذُّرٍ مسمّى."""
    normalized = normalize(word)
    if not _ARABIC_ONLY.match(normalized):
        return MorphologicalAnalysis(
            word, normalized, (), normalized, (), None, None, normalized, False, False, NOT_ARABIC
        )
    if len(normalized) < MIN_STEM:
        return MorphologicalAnalysis(
            word, normalized, (), normalized, (), None, None, normalized, False, False, TOO_SHORT
        )

    for prefixes, stem, suffixes in _candidates(normalized):
        if len(stem) < MIN_STEM:
            continue
        for name, pattern in TEMPLATES:
            match = pattern.match(stem)
            if match:
                # قالبٌ يسمّي جذرَه يُؤخذ منه كاملًا؛ وما سواه جذرُه حروفُ مجموعاته
                named = match.groupdict().get("root")
                root = named if named is not None else "".join(match.groups())
                weak = bool(_WEAK_LETTERS & set(root)) or (
                    name in BARE_TEMPLATES
                    and any(p in SINGLE_PREFIXES for p in prefixes)
                )

                plural_flag = is_plural(word, name, suffixes)
                lemma = singularize_broken_plural(stem) if plural_flag else stem

                return MorphologicalAnalysis(
                    word=word,
                    normalized=normalized,
                    prefixes=prefixes,
                    stem=stem,
                    suffixes=suffixes,
                    root=root,
                    pattern=name,
                    lemma=lemma,
                    is_plural=plural_flag,
                    weak=weak,
                    reason=None,
                )

    _, fallback_suffixes = _strip_suffixes(normalized)
    fallback_plural = any(s in ("ون", "ين", "ات") for s in fallback_suffixes) and not normalized.endswith("ه")
    return MorphologicalAnalysis(
        word, normalized, (), normalized, fallback_suffixes, None, None, normalized, fallback_plural, False, UNDETERMINED
    )


def extract_root(word: str) -> str | None:
    """استخراج الجذر الصافي المجرد للكلمة."""
    return analyze(word).root


def extract_pattern(word: str) -> str | None:
    """استخراج وزن الكلمة الصرفي."""
    return analyze(word).pattern
