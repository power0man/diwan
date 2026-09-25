"""ربطُ الادعاء بمقتطفه — الإسنادُ يُفحص لا يُفترض (ق٢٦).

حارسُ الاستشهاد قبل هذه الوحدة كان يسأل: «أرقمُ الشاهد صالحٌ في
المدى؟» — والسؤالُ الصحيح: «أيقولُ هذا الشاهدُ هذا الكلام؟». فجوابٌ
يحمل زينةَ الإسناد بلا حقيقته **أسوأ من جوابٍ بلا إسناد**: يشتري
ثقةً لا يستحقها، وينقض دعوى النظام كلَّها من داخلها.

**البنيةُ تفرض ولا تفهم**: لا نُحكِّم نموذجًا في إسنادِ نموذج (ذلك
نقلُ «ادعاءات أقوى من أدلتها» طبقةً أعلى وتسميتُه قياسًا). الفحصُ
هنا **حتميٌّ معجمي وعددي** وحده:

1. يُقطَّع الجواب مقاطعَ نصيةً، ولكل مقطعٍ إحالةٌ محلية؛ المقطع
   بلا إحالة يُردّ بـ `citation_missing`، بما فيه العنوان والتمهيد.
   الإحالة تختم المقطع ولا تُسند الكلام اللاحق لها.
2. **كلُّ رقمٍ في الادعاء يجب أن يرد في صفحةٍ من شواهده** (أو في
   عنوان الوثيقة/موضعها — فهويةُ المرجع جزءٌ من قيده). رقمٌ غائب =
   `citation_number_unsupported`، قطعًا بلا اجتهاد.
3. **أرضيةُ تداخلٍ معجمي** بين ألفاظ الادعاء وأقرب نافذةٍ في شواهده
   — دونها `citation_overlap_low`.
4. وتُسجَّل **الرابطة**: الادعاء ← المقتطف الأقرب ← درجتُه. وهذه
   منفعةٌ للمستخدم لا فحصٌ فقط: يفتح المقتطف ويتحقق في ثوانٍ بدل
   قراءة صفحةٍ كاملة.

**حدٌّ معلن**: اكتمال الإحالات بنيويٌّ وفق حدود المقاطع فقط، وليس
استخراجًا دلاليًّا لكل ادعاء. قد يحمل المقطع الواحد جملًا معطوفة أو
نفيًا لا تثبته شواهده رغم كفاية التداخل المعجمي. الفحص يلتقط بعض
الاختلاق العددي والمعجمي، ولا يثبت الاستلزام ولا يصطاد **سوءَ النسبة**
(أرقامٌ صحيحة نُسبت لموضوعٍ خطأ) ولا
الأعدادَ المكتوبةَ حروفًا («أربعمائة») ولا المحسوبةَ جمعًا. تلك
بابُ حكم الاستلزام وتحكيم البشر في بنك الجودة (م٨) — ولا يُدَّعى
لهذه الوحدة غيرُ ما تفعل.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_DIACRITICS = re.compile("[ً-ْٰـ]")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_UNIFY = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
                        "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"})
_SEPARATED = re.compile(r"(?<=\d)[,،](?=\d)")
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_NUM_SIGNS = str.maketrans({"−": "-", "﹣": "-", "－": "-",
                            "＋": "+", "﹢": "+", "٫": ".",
                            "⁄": "/", "∕": "/"})
_NUM_SPACE = r"[ \t\u00a0\u202f]*"
_DECIMAL_FORM = re.compile(
    r"([+-]?)(?:(\d{1,3}(?:[,،٬]\d{3})+|\d+)(?:\.(\d+))?|\.(\d+))"
    r"([eE][+-]?\d+)?\Z")
def cite_pattern(marker: str = "ش") -> re.Pattern:
    return re.compile(r"\[\s*" + re.escape(marker) + r"\s*([\d٠-٩]+)\s*\]")


_CITE = cite_pattern()
# النقطة العشرية (بما فيها .5) لا تفصل العدد؛ splitlines يحكم الأسطر.
_CLAIM_SPLIT = re.compile(r"(?<=[^\W\d])\.|\.(?!\d)|[؟?!؛;۔。．｡።]+")

# أدوات الربط والحروف — لا تحمل دلالةً تُسنَد
_STOP = frozenset({
    "في", "من", "على", "إلى", "الى", "عن", "مع", "أو", "او", "ثم",
    "التي", "الذي", "الذين", "اللاتي", "هذا", "هذه", "ذلك", "تلك",
    "كما", "كل", "بعض", "غير", "بين", "عند", "لدى", "حتى", "إذا",
    "اذا", "قد", "لا", "ما", "هو", "هي", "هم", "أن", "ان", "إن",
    "أنه", "انه", "وفق", "وفقا", "بموجب", "بحسب", "حسب", "يجب",
    "يكون", "تكون", "ويكون", "وتكون", "التالي", "التالية", "كذلك",
})

DEFAULT_OVERLAP_FLOOR = 0.34   # مُعايَرٌ على الأجوبة الذهبية القائمة
_WINDOW_SLACK = 3              # عرضُ النافذة = ألفاظ الادعاء × هذا


@dataclass(frozen=True)
class Binding:
    """رابطةُ ادعاءٍ واحد بأقرب مقتطفٍ في شواهده."""
    claim: str
    refs: tuple[int, ...]
    excerpt: str
    coverage: float
    missing_numbers: tuple[str, ...]

    @property
    def code(self) -> str | None:
        if not self.refs:
            return "citation_missing"
        if self.missing_numbers:
            return "citation_number_unsupported"
        return None

    def payload(self) -> dict:
        return {"claim": self.claim, "refs": list(self.refs),
                "excerpt": self.excerpt, "coverage": round(self.coverage, 3),
                "missing_numbers": list(self.missing_numbers)}


def normalize(text: str) -> str:
    text = _DIACRITICS.sub("", text).translate(_AR_DIGITS)
    text = _SEPARATED.sub("", text).translate(_UNIFY)
    return _NONWORD.sub(" ", text)


def content_tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split()
            if len(t) >= 3 and t not in _STOP]


def _numeric_notation(token: str) -> str:
    token = "".join(token.split()).translate(_NUM_SIGNS)
    form = _DECIMAL_FORM.fullmatch(token)
    if form is None:
        # الكسور والنطاقات والتمثيلات غير المعيارية وحدات حرفية؛ لا
        # تُفكّك أجزاءً تتبادل الإسناد ولا تُفسّر حسابيًّا.
        return token
    sign, integer, fraction, leading_fraction, exponent = form.groups()
    integer = re.sub("[,،٬]", "", integer or "0").lstrip("0") or "0"
    fraction = (fraction or leading_fraction or "").rstrip("0")
    value = ("-" if sign == "-" else "") + integer
    if fraction:
        value += "." + fraction
    if exponent:
        negative = exponent[1:].startswith("-")
        power = exponent[1:].lstrip("+-").lstrip("0") or "0"
        if power != "0":
            value += "e" + ("-" if negative else "") + power
    return value


def numbers_in(text: str) -> list[str]:
    """مطابقة تمثيل رقمي كامل بإشارته؛ ليست حكمًا على وحداته أو معناه.

    يوحد أرقام Unicode العشرية والإشارات والفواصل المعروفة. يحتفظ
    بالكسور والنطاقات والرموز العددية الأخرى كوحدة حرفية، ولا يحسبها.
    محارف التنسيق غير المرئية لا تفصل الإشارة عن العدد الذي يليها.
    """
    numeric = []
    for char in _DIACRITICS.sub("", text):
        if unicodedata.category(char) == "Cf":
            continue
        try:
            numeric.append(str(unicodedata.decimal(char)))
        except ValueError:
            numeric.append(char)
    numeric = "".join(numeric).translate(_NUM_SIGNS)
    # لا نتجاهل الكسور الجاهزة أو الأرقام الرومانية أو الأرقام المرتفعة؛
    # بقاؤها حرفية يمنع تحويل 1² إلى 12 أو إسقاط علامة -½.
    other = "".join(sorted({c for c in numeric if c.isnumeric() and not c.isdecimal()}))
    run = r"[\d" + re.escape(other) + r"]+"
    sign = r"[+\-±]"
    continuation = (rf"(?:{_NUM_SPACE}[.,،٬:/+\-]+{_NUM_SPACE}{run}"
                    rf"|[eE]{sign}?{run})*")
    pattern = re.compile(rf"(?:{sign}{_NUM_SPACE})?(?:\.(?=\d))?{run}{continuation}")
    return [_numeric_notation(m.group()) for m in pattern.finditer(numeric)]


def split_claims(text: str, marker: str = "ش") -> list[str]:
    """مقاطع بنيوية محافظة: لا استثناء لعناوين أو تمهيد مولّد.

    فواصل الجمل والأسطر تنهي المقطع. كذلك تنهي مجموعة الإحالات
    المتجاورة المقطعَ الذي قبلها، فلا ترث الكلمات التالية إسنادها.
    التنسيق الخالي من الحروف والأرقام ليس ادعاءً. لا يحاول هذا
    المحلل فصل الادعاءات الدلالية المتعددة داخل المقطع الواحد.
    """
    cite = cite_pattern(marker)
    out = []
    sentences = (part for line in text.splitlines()
                 for part in _CLAIM_SPLIT.split(line))
    for sentence in sentences:
        start = 0
        refs = list(cite.finditer(sentence))
        for i, ref in enumerate(refs):
            next_start = refs[i + 1].start() if i + 1 < len(refs) else len(sentence)
            between = sentence[ref.end():next_start]
            if not any(c.isalnum() for c in between):
                continue  # إحالات متجاورة أو تنسيق ختامي فقط
            out.append(sentence[start:ref.end()].strip())
            start = ref.end()
        remainder = sentence[start:].strip()
        if any(c.isalnum() for c in remainder):
            out.append(remainder)
    return out


def refs_in(claim: str, marker: str = "ش") -> tuple[int, ...]:
    return tuple(sorted({int(n.translate(_AR_DIGITS))
                         for n in cite_pattern(marker).findall(claim)}))


def _best_window(claim_tokens: list[str], page_raw: str) -> tuple[str, float]:
    """أقربُ نافذةٍ في الشاهد إلى ألفاظ الادعاء + نسبةُ تغطيتها."""
    raw_words = page_raw.split()
    norm_words = [normalize(w).strip() for w in raw_words]
    want = set(claim_tokens)
    if not want or not raw_words:
        return "", 0.0
    size = max(8, min(len(raw_words), len(claim_tokens) * _WINDOW_SLACK))
    step = max(1, size // 4)
    best_score, best_at = 0.0, 0
    for start in range(0, max(1, len(raw_words) - size + 1), step):
        window = {w for w in norm_words[start:start + size] if w}
        hit = sum(1 for t in want if t in window)
        score = hit / len(want)
        if score > best_score:
            best_score, best_at = score, start
    excerpt = " ".join(raw_words[best_at:best_at + size])
    return excerpt, best_score


def bind_claims(answer: str, pages_by_ref: dict[int, dict],
                marker: str = "ش") -> list[Binding]:
    """يعيد رابطةً لكل مقطع، حتى إن كانت إحالاته فارغة.

    `pages_by_ref`: {رقم الشاهد: {"text", "part", "locus"}}. وإحالةٌ
    إلى رقمٍ لا صفحة له تُعَدّ شاهدًا فارغًا فتسقط تغطيتُها — فالرقمُ
    خارج المدى يحكمه المستدعي برمزه القائم.
    """
    cite = cite_pattern(marker)
    out: list[Binding] = []
    for claim in split_claims(answer, marker):
        refs = refs_in(claim, marker)
        body = " ".join(pages_by_ref.get(r, {}).get("text", "")
                        for r in refs)
        meta = " ".join(str(pages_by_ref.get(r, {}).get(k, ""))
                        for r in refs for k in ("part", "locus"))
        bare = cite.sub(" ", claim)
        toks = content_tokens(bare)
        haystack = set(numbers_in(body)) | set(numbers_in(meta))
        missing = tuple(n for n in numbers_in(bare) if n not in haystack)
        excerpt, coverage = _best_window(toks, body)
        out.append(Binding(claim=claim.strip(), refs=refs, excerpt=excerpt,
                           coverage=coverage, missing_numbers=missing))
    return out


def unsupported(bindings: list[Binding],
                floor: float = DEFAULT_OVERLAP_FLOOR) -> list[tuple]:
    """(الادعاء، الرمز، التفصيل) لكل ادعاءٍ لم يجتز الإسناد الحتمي."""
    bad = []
    for b in bindings:
        if not b.refs:
            bad.append((b.claim, "citation_missing",
                        "مقطع بلا إحالة؛ كل مقطع، بما فيه العنوان والتمهيد، "
                        "يجب أن يختم برقم شاهده أو يحذف"))
        elif b.missing_numbers:
            bad.append((b.claim, "citation_number_unsupported",
                        f"أرقامٌ ليست في شواهدها: {list(b.missing_numbers)}"))
        elif b.coverage < floor:
            bad.append((b.claim, "citation_overlap_low",
                        f"تداخلٌ معجمي {b.coverage:.2f} دون الأرضية "
                        f"{floor:.2f}"))
    return bad


def _find_best_supporting_ref(claim_text: str, pages_by_ref: dict[int, dict],
                             floor: float = DEFAULT_OVERLAP_FLOOR,
                             marker: str = "ش") -> int | None:
    """البحث الحتمي عن أفضل شاهد يسند الادعاء بالأرقام والتغطية المعجمية."""
    cite = cite_pattern(marker)
    bare = cite.sub(" ", claim_text).strip()
    toks = content_tokens(bare)
    nums = numbers_in(bare)
    if not toks and not nums:
        return None

    best_ref = None
    best_score = 0.0

    for ref, page in sorted(pages_by_ref.items()):
        body = page.get("text", "")
        meta = " ".join(str(page.get(k, "")) for k in ("part", "locus"))
        haystack = set(numbers_in(body)) | set(numbers_in(meta))
        if any(n not in haystack for n in nums):
            continue
        _, coverage = _best_window(toks, body)
        if coverage >= floor and coverage > best_score:
            best_score = coverage
            best_ref = ref

    return best_ref


def auto_repair_attribution(answer: str, pages_by_ref: dict[int, dict],
                            floor: float = DEFAULT_OVERLAP_FLOOR,
                            marker: str = "ش") -> tuple[str, list[Binding], bool]:
    """الإصلاح الآلي للإسناد (م١٥ — حوكمة تعزيزية لا رفضٌ أعمى).

    يفحص الادعاءات التي تعثرت إحالتها (غياب رقم، نسبة مقلوبة، أو نقص تداخل)،
    ويبحث في الشواهد المتاحة عن شاهدٍ يسند المقطع نصًّا وعدديًّا بأمانة تامة.
    إن وُجد أصلح الإحالة؛ وإن كان اختلاقًا لم يمسسه وفشل الإسناد بحكمه المغلق.
    """
    cite = cite_pattern(marker)
    initial_bindings = bind_claims(answer, pages_by_ref, marker)
    initial_weak = unsupported(initial_bindings, floor)
    if not initial_weak:
        return answer, initial_bindings, False

    repaired_answer = answer
    repaired_any = False

    for claim in split_claims(answer, marker):
        c_bindings = bind_claims(claim, pages_by_ref, marker)
        if not unsupported(c_bindings, floor):
            continue

        best_ref = _find_best_supporting_ref(claim, pages_by_ref, floor, marker)
        if best_ref is None:
            continue

        refs = refs_in(claim, marker)
        if not refs:
            repaired_claim = f"{claim.strip()} [{marker}{best_ref}]"
        else:
            bare = cite.sub("", claim).strip()
            repaired_claim = f"{bare} [{marker}{best_ref}]"

        test_bindings = bind_claims(repaired_claim, pages_by_ref, marker)
        if not unsupported(test_bindings, floor):
            repaired_answer = repaired_answer.replace(claim, repaired_claim, 1)
            repaired_any = True

    final_bindings = bind_claims(repaired_answer, pages_by_ref, marker)
    final_weak = unsupported(final_bindings, floor)

    if repaired_any and len(final_weak) < len(initial_weak):
        return repaired_answer, final_bindings, True
    return answer, initial_bindings, False

