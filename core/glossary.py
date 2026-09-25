"""المسارد المحكومة — نسيج الربط بين العقد (م٣، ق٢٠).

المسرد **لوحٌ مقيَّد**: الكتابة محكومة (تحقق ← شاهد من المخزن ← بصم ←
قيد إضافي-فقط) والقراءة حرة. المدخل لا يُكتب فوقه — التعديل قيدُ نسخةٍ
جديد و«الحالي» آخرُ قيدٍ لكل (مسرد، مصطلح).

القاعدة التأسيسية: **لا مصطلح بلا شاهدٍ مبصوم** — بصمةُ صفحةٍ قائمةٍ في
المخزن القانوني (م٢ أساسُ م٣ حرفيًّا)، وعضويتُها تُفحص عند الكتابة
وعند الفحص العميق كليهما، فالكتابة الملتفّة مكشوفة.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from core.canonical import PayloadRejected, check_payload, digest
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append

SCHEMA_VERSION = 1

STATUSES = frozenset({"proposed", "approved", "conflicted"})
_HEX64 = re.compile(r"[0-9a-f]{64}")
_RECORD_KEYS = frozenset({"kind", "glossary", "term", "entry", "entry_digest"})
_ENTRY_FIELDS = frozenset({"glossary", "term", "definition", "evidence_digest",
                           "status", "foreign_equiv", "schema_version"})


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


@dataclass(frozen=True)
class GlossaryEntry:
    # الإلزاميّ أوّلًا
    glossary: str            # اسم المسرد (عقدته) — مثل "maritime"
    term: str                # المصطلح
    definition: str          # التعريف
    evidence_digest: str     # بصمة صفحة الشاهد في المخزن — لا مصطلح بلا شاهد
    status: str              # من STATUSES
    foreign_equiv: str = ""  # المقابل الأجنبي إن وُجد

    def fingerprint_payload(self) -> dict:
        return {
            "glossary": self.glossary,
            "term": self.term,
            "definition": self.definition,
            "evidence_digest": self.evidence_digest,
            "status": self.status,
            "foreign_equiv": self.foreign_equiv,
            "schema_version": SCHEMA_VERSION,
        }


def validated_entry(entry: GlossaryEntry) -> GlossaryEntry:
    """يعيد المدخل نفسه إن جاز، وإلا رمى PayloadRejected برمزٍ مُسمّى."""
    if not isinstance(entry, GlossaryEntry):
        _reject("entry", "not_an_entry", f"نوع غير مدعوم: {type(entry).__name__}")
    for f in ("glossary", "term"):
        v = getattr(entry, f)
        if not isinstance(v, str) or not v.strip():
            _reject(f"entry.{f}", f"{f}_missing", "قيمة نصّية غير فارغة مطلوبة")
        if v != v.strip():
            _reject(f"entry.{f}", f"{f}_whitespace", "مسافات طرفية في معرِّف")
    if not isinstance(entry.definition, str) or not entry.definition.strip():
        _reject("entry.definition", "definition_missing", "تعريف غير فارغ مطلوب")
    if not isinstance(entry.evidence_digest, str) \
            or not _HEX64.fullmatch(entry.evidence_digest):
        _reject("entry.evidence_digest", "evidence_invalid",
                "بصمة sha256 ست عشرية من 64 خانة مطلوبة")
    if not isinstance(entry.status, str) or entry.status not in STATUSES:
        _reject("entry.status", "status_unknown",
                f"حالة غير معروفة: {entry.status!r}")
    if not isinstance(entry.foreign_equiv, str) \
            or entry.foreign_equiv != entry.foreign_equiv.strip():
        _reject("entry.foreign_equiv", "foreign_equiv_invalid",
                "نصّ مشذَّب مطلوب (وقد يكون فارغًا)")
    check_payload(entry.fingerprint_payload(), "entry.fingerprint_payload")
    return entry


class Glossary:
    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self._ledger = Ledger(path, create=create)

    def add(self, entry: GlossaryEntry, corpus_digests: frozenset[str]) -> str:
        """كتابةٌ محكومة: تحققٌ، وشاهدٌ عضوٌ في المخزن، وقيدٌ مبصوم.

        المطابقُ للحالي لا يُقيَّد؛ والمختلف قيدُ نسخةٍ جديد.
        """
        e = validated_entry(entry)
        if e.evidence_digest not in corpus_digests:
            _reject("entry.evidence_digest", "evidence_unknown",
                    f"بصمة الشاهد ليست صفحةً في المخزن: {e.evidence_digest[:12]}…")
        payload = e.fingerprint_payload()
        entry_digest = digest(payload)
        cur = self.current().get((e.glossary, e.term))
        if cur is not None and cur["entry_digest"] == entry_digest:
            return entry_digest
        sealed_append(self._ledger, {
            "kind": "term",
            "glossary": e.glossary,
            "term": e.term,
            "entry": payload,
            "entry_digest": entry_digest,
        }, "المسرد")
        return entry_digest

    # — قراءة حرة (لا تفشل مفتوحة) —

    def _checked_records(self) -> list[dict]:
        require_seal(self._ledger, "المسرد")
        self._ledger.verify_chain()
        out = []
        for i, e in enumerate(self._ledger.entries()):
            rec = e.get("record", {})
            if rec.get("kind") != "term":
                continue
            if set(rec) != _RECORD_KEYS:
                raise LedgerCorrupt(
                    f"قيد term رقم {i}: حقوله لا تطابق العقد حصرًا")
            if frozenset(rec["entry"]) != _ENTRY_FIELDS:
                raise LedgerCorrupt(
                    f"قيد term رقم {i}: مفاتيح المدخل لا تطابق العقد حصرًا")
            out.append(rec)
        return out

    def current(self) -> dict[tuple[str, str], dict]:
        """آخر قيدٍ لكل (مسرد، مصطلح) — المدخل النافذ."""
        out: dict[tuple[str, str], dict] = {}
        for rec in self._checked_records():
            out[(rec["glossary"], rec["term"])] = rec
        return out

    def get(self, glossary: str, term: str) -> dict | None:
        return self.current().get((glossary, term))

    def approved(self, glossary: str) -> list[dict]:
        return [rec for (g, _), rec in sorted(self.current().items())
                if g == glossary and rec["entry"]["status"] == "approved"]

    # — حوكمة —

    def verify(self, corpus_digests: frozenset[str],
               strict: bool = False) -> bool:
        """السلسلة، وبصمة كل مدخل بإعادة بنائه، **وعضوية شاهده في المخزن**
        — والالتفافُ على add مكشوفٌ **بختم الاعتماد**: كل كتابةٍ محكومة
        تُختَم فورًا، وأي قيدٍ بعد الختم يُغلق السجل حتى اعتمادٍ جديد
        (والحدُّ المعلن: إعادةُ كتابة الختم مع السجل معًا خارج الكشف —
        حدُّ ثقة نظام الملفات، انظر core/seal.py)."""
        self._ledger.verify_chain(strict)
        for i, rec in enumerate(self._checked_records()):
            p = rec["entry"]
            try:
                e = GlossaryEntry(
                    glossary=p["glossary"], term=p["term"],
                    definition=p["definition"],
                    evidence_digest=p["evidence_digest"], status=p["status"],
                    foreign_equiv=p["foreign_equiv"],
                )
            except (KeyError, TypeError) as exc:
                raise LedgerCorrupt(f"قيد term رقم {i}: مدخل مشوه — {exc}") from exc
            if digest(e.fingerprint_payload()) != rec["entry_digest"]:
                raise LedgerCorrupt(
                    f"قيد term رقم {i}: بصمة المدخل لا تطابق إعادة بنائه")
            try:
                validated_entry(e)
            except PayloadRejected as exc:
                raise LedgerCorrupt(
                    f"قيد term رقم {i} لا يجتاز التحقق: {exc}") from exc
            if e.evidence_digest not in corpus_digests:
                raise LedgerCorrupt(
                    f"قيد term رقم {i}: شاهده ليس صفحة في المخزن")
            if (e.glossary, e.term) != (rec["glossary"], rec["term"]):
                raise LedgerCorrupt(
                    f"قيد term رقم {i}: مفتاح القيد لا يطابق مدخله")
        return True

    def anchored(self) -> bool:
        return self._ledger.anchor_path.exists()

    def anchor(self) -> dict:
        return self._ledger.anchor()
