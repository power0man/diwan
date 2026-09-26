"""بنكُ البحث المعمّق (ك٥٣): متنٌ ثابت، ومحرّكُ بحثٍ ثابتٌ عليه، ومقياسٌ يحكم على الجواب بحقائقه ومصادره.

- **المتن** صفحاتٌ عربيةٌ مولَّدة، عن مدنٍ وأعلامٍ وأنهارٍ متخيَّلة، بعناوين تحت النطاق المحجوز `.example`.
  فلا يجيب نموذجٌ من ذاكرته، ولا يصل البحثُ إلى الشبكة.
- **المحرّكُ الثابت `FixtureSearch`** يسترجع من المتن بـBM25 على نصٍّ مطبَّع، ويعيد الصفحةَ مقتطفًا. وعقدُه عقدُ
  `SearxngBackend` نفسُه، فيمرّ بأداة `web_search` المحكومة كما هي: الحَجر، والسقف، والمصدر لكل نتيجة.
- **المقياس** (`score_item`):
  - ينجح الجوابُ إن اجتاز مدقّقَ الإسناد (`agent/citations.py`)، وأسند كلَّ حقيقةٍ مطلوبةٍ في جملةٍ تُحيل إلى
    صفحةٍ تحويها.
  - وفي التعارض يُسمّي الطرفين ويقول إنهما يتعارضان.
  - وفي ما لا جوابَ له يُقرّ بذلك، ولا يَنسب الفخَّ إلى المسؤول عنه.
- **مستقلٌّ عن المنتج:** التطبيعُ هنا مجمَّدٌ مع البنك، فلا يتغيّر الرقمُ إن تغيّر تطبيعُ النواة.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from agent.citations import check, insufficient

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evaluation" / "suites" / "research_v1.json"
CORPUS = ROOT / "evaluation" / "suites" / "research_v1.corpus.json"
META = ROOT / "evaluation" / "suites" / "research_v1.meta.json"
CATEGORIES = ("two_hop", "compare", "aggregate", "conflict", "unanswerable")
CONFLICT_CUES = ("تختلف", "تتعارض", "تعارض", "اختلاف", "خلاف", "تضارب", "متضارب", "بينما", "في حين")
TOP_K = 5

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه",
                       **dict(zip("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))})
_SEPARATED = re.compile(r"(?<=\d)[,،٬٬](?=\d{3}(?!\d))")
_NONWORD = re.compile(r"[^\w]+", re.UNICODE)
_PREFIXES = ("وبال", "وال", "بال", "كال", "فال", "لل", "ال", "و")
_SUFFIXES = ("هما", "ها", "هم", "هن", "كم", "نا")
# مطبَّعةٌ كما يطبّعها fold: «على» تصير «علي»
_STOPWORDS = frozenset({"في", "من", "علي", "عن", "الي", "ما", "لا", "هل", "كم", "اي", "التي", "الذي", "عام",
                        "هو", "هي", "ان", "او", "ثم", "قد", "مع", "بين", "بعد", "قبل", "كل", "يبلغ"})


def fold(text: str) -> str:
    """حروفٌ بلا تشكيل ولا تطويل، وألفٌ وياءٌ وتاءٌ موحّدة، وأرقامٌ غربية بلا فواصل آلاف."""
    text = _DIACRITICS.sub("", str(text)).translate(_FOLD)
    return _SEPARATED.sub("", text).lower()


def tokens(text: str) -> list[str]:
    """كلماتٌ مطبَّعة بلا أدوات، وبجذعٍ خفيف: تُنزع سابقةٌ واحدة ولاحقةُ ضميرٍ واحدة إن بقي ثلاثةُ أحرف."""
    out = []
    for word in _NONWORD.sub(" ", fold(text)).split():
        for prefix in _PREFIXES:
            if word.startswith(prefix) and len(word) - len(prefix) >= 3:
                word = word[len(prefix):]
                break
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[:-len(suffix)]
                break
        if word not in _STOPWORDS:
            out.append(word)
    return out


class FixtureSearch:
    """بحثٌ حتميّ على المتن الثابت: BM25، والتعادلُ يُحسم بالعنوان."""
    name = "fixture"

    def __init__(self, corpus: list[dict], k1: float = 1.2, b: float = 0.75):
        self.docs = [{"title": d["title"], "url": d["url"], "content": d["body"]} for d in corpus]
        self.terms = [tokens(d["title"] + " " + d["body"]) for d in corpus]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.terms)) / max(1, len(self.terms))
        self.df: dict[str, int] = {}
        for terms in self.terms:
            for term in set(terms):
                self.df[term] = self.df.get(term, 0) + 1
        self.digest = hashlib.sha256(json.dumps(corpus, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def identity(self) -> dict:
        return {"backend": self.name, "corpus_sha256": self.digest}

    def search(self, query: str, *, timeout_s: float | None = None) -> list[dict]:
        wanted, n, scored = set(tokens(query)), len(self.docs), []
        for doc, terms in zip(self.docs, self.terms):
            score = 0.0
            for term in wanted:
                tf = terms.count(term)
                if tf:
                    idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                    score += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(terms) / self.avg))
            if score > 0:
                scored.append((-score, doc["url"], doc))
        return [dict(doc) for _, _, doc in sorted(scored)[:TOP_K]]


def load() -> tuple[dict, list[dict], dict]:
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["pages"]
    meta = json.loads(META.read_text(encoding="utf-8"))
    return suite, corpus, meta


def _has(sentence: str, value) -> bool:
    folded = fold(sentence)
    if isinstance(value, int):
        return re.search(rf"(?<!\d){value}(?!\d)", folded) is not None
    return fold(value) in folded


def score_item(item: dict, answer: str, returned_urls) -> dict:
    report = check(answer, returned_urls)
    body = [s for s in report.sentences]
    facts = []
    for fact in item["facts"]:
        holding = [s for s in body if _has(s.text, fact["value"])]
        facts.append({"value": fact["value"], "found": bool(holding),
                      "supported": any(report.urls_of(s) & set(fact["sources"]) for s in holding)})
    trapped = [trap["value"] for trap in item["traps"]
               if any(_has(s.text, trap["value"]) and _has(s.text, trap["context"]) and not insufficient(s.text)
                      for s in body)]
    if item["category"] == "unanswerable":
        abstained = any(insufficient(s.text) for s in body)
        content_ok = abstained and not trapped
    else:
        abstained = False
        content_ok = all(f["supported"] for f in facts)
        if item["category"] == "conflict":
            content_ok = content_ok and any(cue in answer for cue in CONFLICT_CUES)
    return {"passed": report.passed and content_ok, "citation_passed": report.passed,
            "citation_codes": list(report.codes), "facts": facts, "trapped": trapped,
            "abstained": abstained,
            "fabricated_sources": sum(code == "source_not_returned" for code, _ in report.findings)}


def summarize(results: list[dict], thresholds: dict) -> dict:
    measured = [r for r in results if r.get("status") != "error"]
    passed = sum(r["passed"] for r in measured)
    by_category = {}
    for category in CATEGORIES:
        rows = [r for r in measured if r["category"] == category]
        by_category[category] = {"measured": len(rows), "passed": sum(r["passed"] for r in rows),
                                 "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 4) if rows else None}
    rate = round(passed / len(measured), 4) if measured else None
    citation_rate = round(sum(r["citation_passed"] for r in measured) / len(measured), 4) if measured else None
    fabricated = sum(r["fabricated_sources"] for r in measured)
    rates = [c["pass_rate"] for c in by_category.values() if c["pass_rate"] is not None]
    meets = (rate is not None and rate >= thresholds["pass_rate"]
             and all(r >= thresholds["min_category_pass_rate"] for r in rates)
             and citation_rate >= thresholds["citation_valid_rate"]
             and fabricated <= thresholds["fabricated_sources"]
             and len(measured) == len(results))
    return {"attempted": len(results), "measured": len(measured), "errors": len(results) - len(measured),
            "passed": passed, "pass_rate": rate, "citation_valid_rate": citation_rate,
            "fabricated_sources": fabricated, "by_category": by_category, "thresholds": thresholds,
            "meets": meets}
