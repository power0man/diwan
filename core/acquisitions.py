"""سجل الأصول — الحقوق عند الباب (م١، ق٢٠).

لا صفحة تدخل المخزن القانوني (م٢) إلا محيلةً إلى **قيد استحواذٍ** هنا
يحمل وسمَي الحقوق (استخدام داخلي / توزيع مع النسخة المنشورة) ودليلَ
الترخيص — وبوابةُ الفرض `admitted_item`: المادة لا ترث من الحقوق أكثر
مما يملكه قيدُ مصدرها (عيب التدقيق ٨: «موعودة لا مفروضة» — فأُفرضت).

السجل إضافي-فقط: تغيُّر فهم الحقوق لاحقًا **قيدُ نسخةٍ جديد** لا تعديل،
و«الحالي» آخرُ قيدٍ لكل مصدر. ويجوز قيدُ مصدرٍ بوسمين سالبين معًا —
توثيقُ «مصدرٍ فُحص ورُفض» معرفةٌ تمنع إعادة فحصه غدًا — لكن أي مادة
تحيل إليه سترتد من البوابة لأنها لا تجد إذنًا ترثه.

القراءة لا تفشل مفتوحة، وverify يتحقق بعمق (كما سجل العقد).
"""
from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass

from core.canonical import PayloadRejected, check_payload, digest
from core.knowledge import KnowledgeItem, validated_item
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append

SCHEMA_VERSION = 1

_DATE_YMD = re.compile(r"\d{4}-\d{2}-\d{2}")
_RECORD_KEYS = frozenset({"kind", "source_id", "acquisition", "acquisition_digest"})


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


@dataclass(frozen=True)
class Acquisition:
    # الإلزاميّ أوّلًا
    source_id: str          # المعرّف الذي تحيل إليه المواد والصفحات
    title: str
    origin: str             # المنشأ: رابط أو مسار أو جهة
    verified_date: str      # YYYY-MM-DD — يوم التحقق من التوفر والحقوق
    use_internal: bool
    use_distribution: bool
    license_evidence: str   # دليل الترخيص أو سببُ الوسم — لا يُترك فارغًا

    def fingerprint_payload(self) -> dict:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "origin": self.origin,
            "verified_date": self.verified_date,
            "use_internal": self.use_internal,
            "use_distribution": self.use_distribution,
            "license_evidence": self.license_evidence,
            "schema_version": SCHEMA_VERSION,
        }


def validated_acquisition(acq: Acquisition) -> Acquisition:
    """يعيد القيد نفسه إن جاز، وإلا رمى PayloadRejected برمزٍ مُسمّى."""
    if not isinstance(acq, Acquisition):
        _reject("acquisition", "not_an_acquisition",
                f"نوع غير مدعوم: {type(acq).__name__}")

    for f in ("source_id", "title", "origin", "license_evidence"):
        v = getattr(acq, f)
        if not isinstance(v, str) or not v.strip():
            _reject(f"acquisition.{f}", f"{f}_missing", "قيمة نصّية غير فارغة مطلوبة")
    if acq.source_id != acq.source_id.strip():
        _reject("acquisition.source_id", "source_id_whitespace",
                "مسافات طرفية في معرِّف")

    # الصيغة نصًّا قبل التقويم: fromisoformat (بايثون ٣٫١١+) يقبل الصيغ
    # المضغوطة وصيغة الأسبوع — واليومُ الواحد بصيغتين بصمتان، فتنكسر
    # عديمةُ التكرار (عيب التدقيق ٤)
    if not isinstance(acq.verified_date, str):
        _reject("acquisition.verified_date", "verified_date_type", "تاريخ نصّي مطلوب")
    if not _DATE_YMD.fullmatch(acq.verified_date):
        _reject("acquisition.verified_date", "verified_date_invalid",
                f"ليس تاريخًا بصيغة YYYY-MM-DD: {acq.verified_date!r}")
    try:
        datetime.date.fromisoformat(acq.verified_date)
    except ValueError:
        _reject("acquisition.verified_date", "verified_date_invalid",
                f"تاريخ غير صالح: {acq.verified_date!r}")

    for f in ("use_internal", "use_distribution"):
        if not isinstance(getattr(acq, f), bool):
            _reject(f"acquisition.{f}", f"{f}_type",
                    "قيمة منطقية صريحة مطلوبة (True/False)")

    check_payload(acq.fingerprint_payload(), "acquisition.fingerprint_payload")
    return acq


def _acquisition_from_payload(payload: dict, where: str) -> Acquisition:
    try:
        return Acquisition(
            source_id=payload["source_id"], title=payload["title"],
            origin=payload["origin"], verified_date=payload["verified_date"],
            use_internal=payload["use_internal"],
            use_distribution=payload["use_distribution"],
            license_evidence=payload["license_evidence"],
        )
    except (KeyError, TypeError) as exc:
        raise LedgerCorrupt(f"{where}: قيدُ استحواذٍ مشوه — {exc}") from exc


class SourceRegister:
    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self._ledger = Ledger(path, create=create)

    def acquire(self, acq: Acquisition) -> str:
        """يتحقق ويبصم ويقيّد، ويعيد بصمة القيد. المطابقُ للحالي لا يُقيَّد."""
        a = validated_acquisition(acq)
        payload = a.fingerprint_payload()
        acq_digest = digest(payload)
        current = self.current().get(a.source_id)
        if current is not None and current["acquisition_digest"] == acq_digest:
            return acq_digest
        sealed_append(self._ledger, {
            "kind": "acquired",
            "source_id": a.source_id,
            "acquisition": payload,
            "acquisition_digest": acq_digest,
        }, "سجل الأصول")
        return acq_digest

    # — قراءة (إسقاطات من سجلٍ مفحوص السلسلة — لا قراءة تفشل مفتوحة) —

    def _checked_records(self) -> list[dict]:
        require_seal(self._ledger, "سجل الأصول")
        self._ledger.verify_chain()
        out = []
        for i, e in enumerate(self._ledger.entries()):
            rec = e.get("record", {})
            if rec.get("kind") != "acquired":
                continue
            missing = _RECORD_KEYS - set(rec)
            if missing:
                raise LedgerCorrupt(
                    f"قيد acquired رقم {i} ناقص الحقول: {sorted(missing)}")
            out.append(rec)
        return out

    def current(self) -> dict[str, dict]:
        """آخر قيدٍ لكل مصدر — وسومُ حقوقه النافذة الآن."""
        out: dict[str, dict] = {}
        for rec in self._checked_records():
            out[rec["source_id"]] = rec
        return out

    def get(self, source_id: str) -> dict | None:
        return self.current().get(source_id)

    # — بوابة الاستيعاب: الحقوق تُفرض لا تُوعد —

    def admitted_item(self, item: KnowledgeItem) -> KnowledgeItem:
        """يعيد المادة إن كان لمصدرها قيدٌ نافذ **يتّسع لوسومها**.

        المادة لا ترث أكثر مما يملك مصدرها: use_internal يتطلبه في القيد،
        وuse_distribution كذلك — فمادةُ «توزيع» فوق مصدرٍ «داخلي فقط»
        تُرَدّ هنا لا تُكتشف يوم النشر.
        """
        it = validated_item(item)
        rec = self.get(it.source_id)
        if rec is None:
            _reject("item.source_id", "source_unacquired",
                    f"لا قيد استحواذ للمصدر: {it.source_id!r}")
        acq = rec["acquisition"]
        if it.use_internal and not acq["use_internal"]:
            _reject("item.use_internal", "internal_exceeds_source",
                    f"المصدر {it.source_id!r} بلا إذن استخدام داخلي")
        if it.use_distribution and not acq["use_distribution"]:
            _reject("item.use_distribution", "distribution_exceeds_source",
                    f"المصدر {it.source_id!r} بلا إذن توزيع")
        return it

    # — حوكمة السجل نفسه —

    def verify(self, strict: bool = False) -> bool:
        """سلسلة الغلاف **ومحتوى القيود**: بصمة كل قيدٍ تُعاد وتُطابَق
        ويُعاد تحققه — فالإلحاق الملتفّ على acquire مكشوف (عيب التدقيق ٧)."""
        self._ledger.verify_chain(strict)
        for i, rec in enumerate(self._checked_records()):
            if digest(rec["acquisition"]) != rec["acquisition_digest"]:
                raise LedgerCorrupt(f"قيد acquired رقم {i}: "
                                    "بصمة القيد الداخلية لا تطابق محتواه")
            payload = rec["acquisition"]
            if payload.get("schema_version") != SCHEMA_VERSION:
                raise LedgerCorrupt(f"قيد acquired رقم {i}: "
                                    f"نسخة مخطط غير مدعومة: {payload.get('schema_version')!r}")
            try:
                validated_acquisition(_acquisition_from_payload(payload, f"قيد رقم {i}"))
            except PayloadRejected as exc:
                raise LedgerCorrupt(
                    f"قيد acquired رقم {i} لا يجتاز التحقق: {exc}") from exc
        return True

    def anchored(self) -> bool:
        return self._ledger.anchor_path.exists()

    def anchor(self) -> dict:
        return self._ledger.anchor()
