"""سجل العقد — الملحقات تُسجَّل بعقدٍ مبصوم النسخة (م١، ق٢٠).

تغييرُ عقد عقدةٍ ليس تعديلًا بل **قيدُ نسخةٍ جديد برقمٍ أعلى**: نفسُ
(الاسم، النسخة) بمحتوى مختلف يُرفض، والنكوصُ إلى نسخةٍ أدنى يُرفض —
فرقمُ النسخة يُعرِّف عقدًا واحدًا أبدًا. والسجل تاريخٌ إضافي-فقط
(Ledger نفسه)، و«الحالي» إسقاطُ آخرِ قيدٍ لكل اسم.

القراءة لا تفشل مفتوحة: current/get/history تفحص السلسلة قبل البناء،
وverify يتحقق **بعمق** — بصمةُ كل عقدٍ داخليٍّ تُعاد وتُطابَق ويُعاد
تمريره على validated_manifest، فالالتفاف على register بالإلحاق المباشر
مكشوفٌ لا بلا أثر (عيبا التدقيق ٥ و٧).
"""
from __future__ import annotations

import os

from core.canonical import PayloadRejected, digest
from core.knowledge import NodeManifest, validated_manifest
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append

_RECORD_KEYS = frozenset({"kind", "name", "manifest", "manifest_digest"})


def _manifest_from_payload(payload: dict, where: str) -> NodeManifest:
    try:
        return NodeManifest(
            name=payload["name"],
            contract_version=payload["contract_version"],
            domains=tuple(payload["domains"]),
            accepts=tuple(payload["accepts"]),
            data_policy_ceiling=payload["data_policy_ceiling"],
        )
    except (KeyError, TypeError) as exc:
        raise LedgerCorrupt(f"{where}: عقدُ عقدةٍ مشوه — {exc}") from exc


class NodeRegistry:
    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self._ledger = Ledger(path, create=create)

    def register(self, manifest: NodeManifest) -> str:
        """يتحقق ويبصم ويقيّد، ويعيد بصمة العقد.

        المطابقُ لنسخته المقيدة لا يُكرَّر؛ ونفس النسخة بمحتوى مختلف أو
        نسخة أدنى من الأعلى المقيد يُرفضان برمزٍ مُسمّى.
        """
        m = validated_manifest(manifest)
        payload = m.fingerprint_payload()
        manifest_digest = digest(payload)

        versions: dict[int, str] = {}
        for rec in self.history(m.name):
            versions[rec["manifest"]["contract_version"]] = rec["manifest_digest"]

        if m.contract_version in versions:
            if versions[m.contract_version] == manifest_digest:
                return manifest_digest
            raise PayloadRejected(
                "manifest.contract_version", "contract_version_reused",
                f"النسخة {m.contract_version} مقيدة لـ{m.name!r} بمحتوى مختلف — "
                "التغيير يرفع النسخة")
        if versions and m.contract_version < max(versions):
            raise PayloadRejected(
                "manifest.contract_version", "contract_version_downgrade",
                f"نكوص من {max(versions)} إلى {m.contract_version} لـ{m.name!r}")

        sealed_append(self._ledger, {
            "kind": "node_registered",
            "name": m.name,
            "manifest": payload,
            "manifest_digest": manifest_digest,
        }, "سجل العقد")
        return manifest_digest

    # — قراءة (إسقاطات من سجلٍ مفحوص السلسلة — لا قراءة تفشل مفتوحة) —

    def _checked_records(self) -> list[dict]:
        require_seal(self._ledger, "سجل العقد")
        self._ledger.verify_chain()
        out = []
        for i, e in enumerate(self._ledger.entries()):
            rec = e.get("record", {})
            if rec.get("kind") != "node_registered":
                continue
            missing = _RECORD_KEYS - set(rec)
            if missing:
                raise LedgerCorrupt(
                    f"قيد node_registered رقم {i} ناقص الحقول: {sorted(missing)}")
            out.append(rec)
        return out

    def current(self) -> dict[str, dict]:
        """آخر قيدٍ لكل اسم — عقدُ كلِّ عقدةٍ النافذ الآن."""
        out: dict[str, dict] = {}
        for rec in self._checked_records():
            out[rec["name"]] = rec
        return out

    def get(self, name: str) -> dict | None:
        return self.current().get(name)

    def history(self, name: str) -> list[dict]:
        """كل نسخ عقد العقدة بترتيب تقييدها."""
        return [rec for rec in self._checked_records() if rec["name"] == name]

    # — حوكمة السجل نفسه —

    def verify(self, strict: bool = False) -> bool:
        """سلسلة الغلاف **ومحتوى القيود**: بصمة كل عقدٍ تُعاد وتُطابَق
        ويُعاد تحققه — فالإلحاق الملتفّ على register مكشوف."""
        self._ledger.verify_chain(strict)
        for i, rec in enumerate(self._checked_records()):
            if digest(rec["manifest"]) != rec["manifest_digest"]:
                raise LedgerCorrupt(f"قيد node_registered رقم {i}: "
                                    "بصمة العقد الداخلية لا تطابق محتواه")
            payload = rec["manifest"]
            if payload.get("schema_version") != 1:
                raise LedgerCorrupt(f"قيد node_registered رقم {i}: "
                                    f"نسخة مخطط غير مدعومة: {payload.get('schema_version')!r}")
            try:
                validated_manifest(_manifest_from_payload(payload, f"قيد رقم {i}"))
            except PayloadRejected as exc:
                raise LedgerCorrupt(
                    f"قيد node_registered رقم {i} لا يجتاز التحقق: {exc}") from exc
        return True

    def anchored(self) -> bool:
        return self._ledger.anchor_path.exists()

    def anchor(self) -> dict:
        return self._ledger.anchor()
