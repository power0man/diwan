"""مدقّقُ الإسناد (ك٥٣): كلُّ ادّعاءٍ في الجواب مسنَدٌ إلى مصدرٍ أعاده البحثُ في الجولة نفسِها.

الصيغةُ التي يُطلب بها الجواب (`agent/research.py`):

    وُلدت سلافة في زبرجد [1]، وتأسّست زبرجد عام 1412 [2].

    المصادر:
    [1] https://mawsoua.example/person/sulafa
    [2] https://mawsoua.example/city/zabarjad

**ما يُرفض:**
- **`sources_missing`:** في الجواب ادّعاءٌ وليس فيه قائمةُ مصادر.
- **`source_malformed` و`source_duplicate`:** سطرٌ في القائمة ليس `[n] عنوان`، أو رقمٌ مكرَّر.
- **`marker_undefined`:** مرجعٌ في المتن لا مصدرَ له في القائمة.
- **`source_not_returned`:** مصدرٌ لم يُعِده البحثُ في هذه الجولة. وهو الاختلاق، أو الاستشهادُ من الذاكرة.
  ويُعدّ كذلك كلُّ رابطٍ في سطرٍ معطوبٍ من القائمة (`[1] رابط - وصف`)، فلا يُخفي العطبُ الاختلاق.
- **`uncited_claim`:** جملةُ ادّعاءٍ بلا مرجع.

**ما الادّعاء:** جملةٌ فيها رقم، أو فيها أربعُ كلماتٍ فأكثر. وتُستثنى جملُ الإقرار بالعجز («لم أجد في المصادر…»)
والعناوينُ المنتهية بنقطتين، ما لم يكن فيها رقم. وهذا الحدُّ معلن: جملةٌ قصيرة بلا رقمٍ تمرّ بلا مرجع. والمدقّقُ يحكم على الشكل،
لا على أن المصدر يقول ما نُسب إليه. ذاك يحكم عليه البنك بمطابقة الحقائق لمصادرها.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
SOURCES_HEADER = re.compile(r"^\s*(?:#+\s*)?\**\s*(?:المصادر|المراجع|Sources)\s*\**\s*[:：]?\s*\**\s*$",
                            re.IGNORECASE)
MARKER = re.compile(r"\[\s*([0-9٠-٩۰-۹]+(?:\s*[,،]\s*[0-9٠-٩۰-۹]+)*)\s*\]")
SOURCE_LINE = re.compile(r"^\s*[-*]?\s*\[\s*([0-9٠-٩۰-۹]+)\s*\]\s*[:：\-–—]?\s*<?(\S+?)>?\s*$")
URL = re.compile(r"https?://[^\s<>\[\]]+")
_URL_TAIL = ".,;:!?،؛。\"'»”"
_SENTENCE_END = re.compile(r"(?<=[.!؟?])\s+|\n+")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_DIGIT = re.compile(r"[0-9٠-٩۰-۹]")
# عباراتُ إقرارٍ بأن المصادر لا تجيب. وجملةٌ فيها رقمٌ ادّعاءٌ ولو أقرّت، فلا يمرّ تخمينٌ رقميّ بلا مرجع
INSUFFICIENT = ("لم أجد", "لم أعثر", "لا تذكر", "لم تذكر", "لا تتضمن", "لا تتضمّن", "لا يوجد في المصادر",
                "لا تتوفر", "لا تتوفّر", "لا تكفي", "لا أستطيع تأكيد", "لا يمكنني تأكيد")
FAILING = frozenset({"sources_missing", "source_malformed", "source_duplicate", "marker_undefined",
                     "source_not_returned", "uncited_claim"})


def numbers(text: str) -> list[int]:
    return [int(n) for group in MARKER.findall(text) for n in re.split(r"\s*[,،]\s*", group.translate(DIGITS))]


@dataclass(frozen=True)
class Sentence:
    text: str
    cites: tuple            # أرقامُ المراجع فيها
    claim: bool


@dataclass(frozen=True)
class CitationReport:
    sources: dict           # الرقمُ ← العنوان
    sentences: tuple
    findings: tuple = field(default_factory=tuple)   # (الرمز، التفصيل)

    @property
    def codes(self) -> tuple:
        return tuple(sorted({code for code, _ in self.findings}))

    @property
    def passed(self) -> bool:
        return not any(code in FAILING for code, _ in self.findings)

    def urls_of(self, sentence: Sentence) -> set:
        return {self.sources[n] for n in sentence.cites if n in self.sources}


def _url(raw: str) -> str:
    """الرابطُ بلا ما لصق به من ترقيم الجملة، وبلا قوسٍ يُغلق ما لم يفتحه الرابط (`(رابط)`)."""
    url = raw.rstrip(_URL_TAIL)
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(_URL_TAIL)
    return url


def insufficient(text: str) -> bool:
    return any(cue in text for cue in INSUFFICIENT)


def _split(answer: str) -> tuple[str, list[str]]:
    """المتنُ قبل ترويسة المصادر، وسطورُ القائمة بعدها."""
    lines = answer.splitlines()
    for index, line in enumerate(lines):
        if SOURCES_HEADER.match(line):
            return "\n".join(lines[:index]), lines[index + 1:]
    return answer, []


def sentences(body: str) -> list[Sentence]:
    out = []
    for raw in _SENTENCE_END.split(body):
        text = raw.strip().lstrip("-*•").strip()
        if not text:
            continue
        cites = tuple(numbers(text))
        bare = MARKER.sub(" ", text)
        claim = bool(_DIGIT.search(bare)) or (len(_WORD.findall(bare)) >= 4 and not insufficient(bare)
                                               and not bare.rstrip().endswith((":", "：")))
        out.append(Sentence(text, cites, claim))
    return out


def check(answer: str, returned_urls) -> CitationReport:
    returned = set(returned_urls)
    body, source_lines = _split(answer if isinstance(answer, str) else "")
    findings, sources = [], {}
    for line in source_lines:
        if not line.strip():
            continue
        match = SOURCE_LINE.match(line)
        if match is None:
            findings.append(("source_malformed", line.strip()[:120]))
            for url in map(_url, URL.findall(line)):
                if url not in returned:
                    findings.append(("source_not_returned", url[:200]))
            continue
        number, url = int(match.group(1).translate(DIGITS)), match.group(2)
        if number in sources:
            findings.append(("source_duplicate", str(number)))
            continue
        sources[number] = url
        if url not in returned:
            findings.append(("source_not_returned", url[:200]))
    parts = sentences(body)
    claims = [s for s in parts if s.claim]
    if claims and not sources:
        findings.append(("sources_missing", ""))
    cited = set()
    for sentence in parts:
        for number in sentence.cites:
            cited.add(number)
            if number not in sources:
                findings.append(("marker_undefined", str(number)))
        if sentence.claim and not sentence.cites:
            findings.append(("uncited_claim", sentence.text[:160]))
    for number in sorted(set(sources) - cited):
        findings.append(("source_unused", str(number)))
    return CitationReport(sources, tuple(parts), tuple(findings))
