#!/usr/bin/env python3
"""إثراء المسرد بالمكافئات الأجنبية لمصطلحاتٍ معتمدة (م٥ — بوابة التعريب).

    python3 tools/seed_foreign_equiv.py

قيودُ نسخٍ فوق مدخلاتٍ قائمة: الشاهدُ والتعريفُ والحالة تبقى، ويُلحق
المكافئ الإنجليزي المتعارف في صكوك IMO — مصدرُ المكافئ اصطلاحُ
الاتفاقيات نفسِه الذي عرّبته اللوائح (لا اجتهاد لغوي جديد).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.corpus import CorpusCatalog
from core.glossary import Glossary, GlossaryEntry

ROOT = Path(__file__).resolve().parent.parent

EQUIV = {
    "السفينة": "ship",
    "الربان": "master",
    "المنظمة": "the Organization",
    "السلطة البحرية": "the Administration",
    "مياه الصابورة": "ballast water",
    "الحمولة الكلية": "gross tonnage",
    "الرحلة الدولية": "international voyage",
    "ناقلة الكيميائيات": "chemical tanker",
    "ناقلة الغاز": "gas carrier",
    "سفينة الركاب": "passenger ship",
    "سفينة الشحن": "cargo ship",
    "المالك": "owner",
    "الطاقم": "crew",
}


def main() -> int:
    glossary = Glossary(ROOT / "glossaries" / "maritime.jsonl", create=True)
    catalog = CorpusCatalog(ROOT / "corpus" / "maritime" / "_catalog.jsonl")
    all_digests = catalog.page_digests(ROOT)
    cur = glossary.current()
    updated = missing = 0
    for term, fe in EQUIV.items():
        rec = cur.get(("maritime", term))
        if rec is None:
            missing += 1
            continue
        e = rec["entry"]
        if e["foreign_equiv"] == fe:
            continue
        glossary.add(GlossaryEntry(
            glossary="maritime", term=term, definition=e["definition"],
            evidence_digest=e["evidence_digest"], status=e["status"],
            foreign_equiv=fe), all_digests)
        updated += 1
    print(f"أُلحق المكافئ لـ{updated} مصطلحًا (غائب عن المسرد: {missing}).")
    if updated + sum(1 for t in EQUIV
                     if cur.get(("maritime", t)) is not None) - updated < 8:
        raise SystemExit("أقل من 8 مصطلحات متاحة للربط — يُراجع القاموس")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
