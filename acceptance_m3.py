#!/usr/bin/env python3
"""مُشغِّل دليل قبول م٣ — المسارد المحكومة وسجل السوابق (ق٢٠).

    python3 acceptance_m3.py

نصّ القبول المعتمد: مصطلح بلا شاهد مبصوم يُرفض؛ كتابة مباشرة تُكشف
بفحص السلسلة؛ 50 مصطلحًا بحريًّا معتمدًا كلٌّ بشاهد يُفتح إلى صفحته.
"""
from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.canonical import PayloadRejected, digest
from core.corpus import CorpusCatalog
from core.glossary import Glossary, GlossaryEntry, validated_entry
from core.ledger import Ledger, LedgerCorrupt
from core.rulings import Ruling, Rulings

ROOT = Path(__file__).resolve().parent
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def rejected_with(fn, code: str, *args) -> bool:
    try:
        fn(*args)
    except PayloadRejected as e:
        return e.code == code
    return False


def main() -> int:
    catalog = CorpusCatalog(ROOT / "corpus" / "maritime" / "_catalog.jsonl")
    catalog.verify(strict=True, root=ROOT)
    corpus_digests = catalog.page_digests(ROOT)
    glossary = Glossary(ROOT / "glossaries" / "maritime.jsonl")

    # ١ — المسرد سليم عميقًا بمرساته والعدة فوق الخمسين
    try:
        glossary.verify(corpus_digests, strict=True)
        approved = glossary.approved("maritime")
        check("المسرد سليم عميقًا بمرساته وفيه ≥50 مصطلحًا معتمدًا",
              len(approved) >= 50, f"{len(approved)} مصطلحًا معتمدًا")
    except LedgerCorrupt as exc:
        check("المسرد سليم عميقًا بمرساته وفيه ≥50 مصطلحًا معتمدًا",
              False, str(exc))
        approved = []

    # ٢ — **كل** المعتمد: شاهده يُفتح إلى صفحته والمصطلح وارد في نصها
    # (خريطة بصمة↦صفحة تُبنى مرة — لا مسح لكل مصطلح)
    from core.corpus import CorpusFile
    page_map = {}
    for rec_c in catalog.current().values():
        for page in CorpusFile(ROOT / rec_c["file"]).pages():
            page_map[page["item_digest"]] = page["item"]["text"]
    bad = [rec["entry"]["term"] for rec in approved
           if rec["entry"]["term"] not in page_map.get(
               rec["entry"]["evidence_digest"], "")]
    check("كل المعتمد: شاهده يُفتح إلى صفحته والمصطلح وارد في نصها",
          bool(approved) and not bad,
          f"{len(approved)} فُحصت كلها" + (f" — خالفت: {bad[:3]}" if bad else ""))

    # ٣ — الغائب والمجهول يُرفضان برمزيهما
    sample_evidence = approved[0]["entry"]["evidence_digest"] if approved else "a" * 64
    entry = dict(glossary="maritime", term="مصطلح-تجريبي",
                 definition="تعريف تجريبي كافي الطول للعقد.",
                 evidence_digest="f" * 64, status="proposed")
    iso_reject = Glossary(Path(tempfile.mkdtemp()) / "gr.jsonl")
    check("مصطلح بشاهد ليس صفحةً في المخزن يُرفض",
          rejected_with(iso_reject.add, "evidence_unknown",
                        GlossaryEntry(**entry), corpus_digests)
          and iso_reject._ledger.count() == 0,
          "evidence_unknown — على نسخة معزولة، لا لمسَ للحقيقة المدفوعة")
    check("مصطلح ببصمة شاهد مشوهة يُرفض",
          rejected_with(validated_entry, "evidence_invalid",
                        GlossaryEntry(**{**entry, "evidence_digest": "xyz"})),
          "evidence_invalid")

    # ٤ — الكتابة الملتفّة تُكشف بالفحص العميق (في نسخة معزولة لا في الحقيقة)
    tmp = Path(tempfile.mkdtemp()) / "g.jsonl"
    tmp.write_bytes((ROOT / "glossaries" / "maritime.jsonl").read_bytes())
    forged = Glossary(tmp)
    payload = GlossaryEntry(**{**entry, "evidence_digest": "e" * 64}
                            ).fingerprint_payload()
    Ledger(tmp).append({"kind": "term", "glossary": "maritime",
                        "term": "مصطلح-تجريبي", "entry": payload,
                        "entry_digest": digest(payload)})
    try:
        forged.verify(corpus_digests)
        check("قيد ملتفّ بشاهد خارج المخزن يُكشف بالفحص العميق", False, "مرّ")
    except LedgerCorrupt as exc:
        check("قيد ملتفّ بشاهد خارج المخزن يُكشف بالفحص العميق", True,
              str(exc)[:60])

    # ٥ — التعديل قيدُ نسخةٍ والمطابق لا يُكرَّر (في النسخة المعزولة)
    iso = Glossary(Path(tempfile.mkdtemp()) / "g2.jsonl")
    e1 = GlossaryEntry(**{**entry, "evidence_digest": sample_evidence})
    iso.add(e1, corpus_digests)
    n = iso._ledger.count()
    iso.add(e1, corpus_digests)
    same = iso._ledger.count() == n
    iso.add(GlossaryEntry(**{**entry, "evidence_digest": sample_evidence,
                             "definition": "تعريف منقح بعد مراجعة."}),
            corpus_digests)
    versioned = iso._ledger.count() == n + 1 \
        and iso.get("maritime", "مصطلح-تجريبي")["entry"]["definition"].startswith("تعريف منقح")
    check("المطابق لا يُكرَّر والتعديل قيدُ نسخةٍ جديد", same and versioned, "")

    # ٦ — سجل السوابق: القاعدتان التأسيسيتان نافذتان وسليم بمرساته
    rulings = Rulings(ROOT / "rulings" / "precedence.jsonl")
    try:
        rulings.verify(strict=True)
        r1 = rulings.get("تقاطع-الاختصاصين")
        r2 = rulings.get("سلم-الأصالة")
        check("سجل السوابق: قاعدتا الحسم نافذتان نصًّا وسليم بمرساته",
              r1 is not None and "يقيِّد" in r1["ruling"]["text"]
              and r2 is not None and "أصالة" in r2["ruling"]["text"],
              f"{len(rulings.current())} سابقة")
    except LedgerCorrupt as exc:
        check("سجل السوابق: قاعدتا الحسم نافذتان نصًّا وسليم بمرساته",
              False, str(exc))

    print("\n— دليل قبول م٣ —\n")
    width = max(len(n) for n, _, _ in CHECKS)
    failed = sum(1 for _, ok, _ in CHECKS if not ok)
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name:<{width}}  {detail}")
    print(f"\n  {len(CHECKS) - failed}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
