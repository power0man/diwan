"""سجل السوابق — أحكامُ المالك جزءٌ من البنية (م٣، ق٢٠).

ما لا يحسمه الاختصاصُ ثم الأصالةُ يُرفع للمالك، وحكمُه يُبصَم **سابقةً**
يطابقها الموجّه آليًّا قبل فتح تعارضٍ جديد. الحسم البشري بندٌ معماري
لا ثغرة أتمتة. السجل إضافي-فقط و«النافذ» آخرُ قيدٍ لكل موضوع.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core.canonical import PayloadRejected, check_payload, digest
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append

SCHEMA_VERSION = 1
_RECORD_KEYS = frozenset({"kind", "topic", "ruling", "ruling_digest"})
_RULING_FIELDS = frozenset({"topic", "text", "basis", "decided_on",
                            "schema_version"})


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


@dataclass(frozen=True)
class Ruling:
    # الإلزاميّ أوّلًا
    topic: str          # موضوع السابقة — مفتاح المطابقة الآلية
    text: str           # نص الحكم كما قرره المالك (أو كما نصت الوثيقة الحاكمة)
    basis: str          # السند: نص المالك، أو القرار المرقم، أو الدليل
    decided_on: str     # YYYY-MM-DD

    def fingerprint_payload(self) -> dict:
        return {
            "topic": self.topic,
            "text": self.text,
            "basis": self.basis,
            "decided_on": self.decided_on,
            "schema_version": SCHEMA_VERSION,
        }


def validated_ruling(r: Ruling) -> Ruling:
    if not isinstance(r, Ruling):
        _reject("ruling", "not_a_ruling", f"نوع غير مدعوم: {type(r).__name__}")
    if not isinstance(r.topic, str) or not r.topic.strip() \
            or r.topic != r.topic.strip():
        _reject("ruling.topic", "topic_invalid", "موضوع نصّي مشذَّب مطلوب")
    for f in ("text", "basis"):
        v = getattr(r, f)
        if not isinstance(v, str) or not v.strip():
            _reject(f"ruling.{f}", f"{f}_missing", "قيمة نصّية غير فارغة مطلوبة")
    import datetime
    import re as _re
    if not isinstance(r.decided_on, str) \
            or not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.decided_on):
        _reject("ruling.decided_on", "decided_on_invalid",
                f"تاريخ YYYY-MM-DD مطلوب: {r.decided_on!r}")
    try:
        datetime.date.fromisoformat(r.decided_on)
    except ValueError:
        _reject("ruling.decided_on", "decided_on_invalid",
                f"تاريخ غير صالح: {r.decided_on!r}")
    check_payload(r.fingerprint_payload(), "ruling.fingerprint_payload")
    return r


class Rulings:
    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self._ledger = Ledger(path, create=create)

    def rule(self, ruling: Ruling) -> str:
        """يقيّد سابقةً مبصومة. المطابقُ للنافذ لا يُكرَّر، والمختلف نسخة."""
        r = validated_ruling(ruling)
        payload = r.fingerprint_payload()
        ruling_digest = digest(payload)
        cur = self.current().get(r.topic)
        if cur is not None and cur["ruling_digest"] == ruling_digest:
            return ruling_digest
        sealed_append(self._ledger,
                      {"kind": "ruling", "topic": r.topic,
                       "ruling": payload, "ruling_digest": ruling_digest},
                      "سجل السوابق")
        return ruling_digest

    def _checked_records(self) -> list[dict]:
        require_seal(self._ledger, "سجل السوابق")
        self._ledger.verify_chain()
        out = []
        for i, e in enumerate(self._ledger.entries()):
            rec = e.get("record", {})
            if rec.get("kind") != "ruling":
                continue
            if set(rec) != _RECORD_KEYS:
                raise LedgerCorrupt(
                    f"قيد ruling رقم {i}: حقوله لا تطابق العقد حصرًا")
            if frozenset(rec["ruling"]) != _RULING_FIELDS:
                raise LedgerCorrupt(
                    f"قيد ruling رقم {i}: مفاتيح السابقة لا تطابق العقد حصرًا")
            out.append(rec)
        return out

    def current(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for rec in self._checked_records():
            out[rec["topic"]] = rec
        return out

    def get(self, topic: str) -> dict | None:
        return self.current().get(topic)

    def verify(self, strict: bool = False) -> bool:
        self._ledger.verify_chain(strict)
        for i, rec in enumerate(self._checked_records()):
            p = rec["ruling"]
            try:
                r = Ruling(topic=p["topic"], text=p["text"], basis=p["basis"],
                           decided_on=p["decided_on"])
            except (KeyError, TypeError) as exc:
                raise LedgerCorrupt(f"قيد ruling رقم {i}: سابقة مشوهة — {exc}") from exc
            if digest(r.fingerprint_payload()) != rec["ruling_digest"]:
                raise LedgerCorrupt(
                    f"قيد ruling رقم {i}: بصمة السابقة لا تطابق إعادة بنائها")
            try:
                validated_ruling(r)
            except PayloadRejected as exc:
                raise LedgerCorrupt(
                    f"قيد ruling رقم {i} لا يجتاز التحقق: {exc}") from exc
            if r.topic != rec["topic"]:
                raise LedgerCorrupt(f"قيد ruling رقم {i}: مفتاحه لا يطابق سابقته")
        return True

    def anchored(self) -> bool:
        return self._ledger.anchor_path.exists()

    def anchor(self) -> dict:
        return self._ledger.anchor()
