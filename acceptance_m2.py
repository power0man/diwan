#!/usr/bin/env python3
"""مُشغِّل دليل قبول م٢ — المخزن القانوني والإسقاطات، البحرية أولًا (ق٢٠).

    python3 acceptance_m2.py

نصّ القبول المعتمد: عينة 50 صفحة عشوائية تطابق المصدر؛ حذف الإسقاط
وإعادة بناؤه ← بصمة نتائج استعلامات مطابقة. العشوائية **ببذرة حتمية**
من رأس فهرس المخزن — عينةٌ يعيد أي فاحصٍ إنتاجَها بعينها.

يتطلب وجود تصدير مشروع «اللوائح» محليًّا (مصدر إعادة الاستخلاص).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

from core.acquisitions import SourceRegister
from core.corpus import CorpusCatalog, CorpusFile
from core.ledger import LedgerCorrupt
from ingest_regulations import DEFAULT_EXPORT, doc_items, slugify
from rebuild_index import GOLDEN_QUERIES, INDEX, rebuild, results_fingerprint, search

ROOT = Path(__file__).resolve().parent
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def main() -> int:
    catalog = CorpusCatalog(ROOT / "corpus" / "maritime" / "_catalog.jsonl")
    register = SourceRegister(ROOT / "sources" / "acquisitions.jsonl")

    # ١ — الفهرس سليم بمرساته ورؤوس الملفات تطابقه، والعدّة مكتملة
    try:
        catalog.verify(strict=True, root=ROOT)
        cur = catalog.current()
        pages_total = sum(r["pages"] for r in cur.values())
        check("فهرس المخزن سليم بمرساته ورؤوس الوثائق تطابقه",
              len(cur) == 117, f"{len(cur)} وثيقة، {pages_total} صفحة")
    except LedgerCorrupt as exc:
        check("فهرس المخزن سليم بمرساته ورؤوس الوثائق تطابقه", False, str(exc))
        cur, pages_total = {}, 0

    # ٢ — عينة عشوائية ببذرة حتمية: 10 وثائق فحصًا عميقًا ببوابة الحقوق
    head = catalog._ledger.head()
    rng = random.Random(head)
    sample_docs = rng.sample(sorted(cur), k=min(10, len(cur))) if cur else []
    try:
        for did in sample_docs:
            CorpusFile(ROOT / cur[did]["file"]).verify(strict=True,
                                                       register=register)
        check("عينة 10 وثائق: سلاسل سليمة وكل صفحة تجتاز بوابة الحقوق",
              bool(sample_docs), f"بذرة {head[:12]}…")
    except LedgerCorrupt as exc:
        check("عينة 10 وثائق: سلاسل سليمة وكل صفحة تجتاز بوابة الحقوق",
              False, str(exc))

    # ٣ — عينة 50 صفحة تطابق **إعادة الاستخلاص من المصدر** نصًّا وموضعًا
    export = Path(DEFAULT_EXPORT)
    if not export.exists():
        check("عينة 50 صفحة تطابق إعادة الاستخلاص من المصدر", False,
              f"التصدير غير موجود: {export}")
    elif cur:
      try:
        data = json.loads(export.read_text(encoding="utf-8"))
        by_doc_id = {}
        for idx, doc in enumerate(data["docs"], 1):
            by_doc_id[f"{idx:03d}__{slugify(doc['filename'])}"] = doc
        pool = [(did, i) for did in sorted(cur)
                for i in range(cur[did]["pages"])]
        picks = rng.sample(pool, k=min(50, len(pool)))
        mismatches, checked = [], 0
        by_did: dict[str, list[int]] = {}
        for did, i in picks:
            by_did.setdefault(did, []).append(i)
        for did, idxs in by_did.items():
            pages = CorpusFile(ROOT / cur[did]["file"]).pages()
            fresh = {p_i.fingerprint_payload()["locus"]: p_i.fingerprint_payload()
                     for p_i in doc_items(by_doc_id[did]["filename"],
                                          by_doc_id[did]["content"])}
            for i in idxs:
                it = pages[i]["item"]
                checked += 1
                ref = fresh.get(it["locus"])
                if ref is None or ref["text"] != it["text"]:
                    mismatches.append(f"{did}:{it['locus']}")
        check("عينة 50 صفحة تطابق إعادة الاستخلاص من المصدر نصًّا وموضعًا",
              checked >= 50 and not mismatches,
              f"فُحصت {checked}" + (f" — خالفت: {mismatches[:3]}" if mismatches else ""))
      except Exception as exc:
        check("عينة 50 صفحة تطابق إعادة الاستخلاص من المصدر نصًّا وموضعًا",
              False, f"{type(exc).__name__}: {exc}")

    # ٤ — الإسقاط: بصمة النتائج مطابقة بعد الحذف وإعادة البناء، ولا استعلام صفري
    # (الفشل هنا يُسجَّل ✗ باسمه — لا انهيار بلا تقرير: عيب التدقيق ٩)
    try:
        rebuild()
        f1 = results_fingerprint()
        empty = [q for q in GOLDEN_QUERIES if not search(q, limit=3)]
        INDEX.unlink()
        rebuild()
        f2 = results_fingerprint()
        check("حذف الإسقاط وإعادة بناؤه يعيد بصمة نتائج مطابقة",
              f1 == f2, f1[:16] + "…")
        check("كل الاستعلامات الذهبية تعيد نتائج غير صفرية",
              not empty,
              "؛ ".join(empty) if empty else f"{len(GOLDEN_QUERIES)} استعلامات")
        # ٥ — التطبيع: استعلام بتاء مهاء (الصابوره) يجد الصابورة المفهرسة
        check("التطبيع: «الصابوره» يجد «الصابورة»",
              bool(search("مياه الصابوره", limit=3)), "ة→ه")
    except (LedgerCorrupt, Exception) as exc:
        check("الإسقاط والتطبيع (انهار قبل اكتماله)", False,
              f"{type(exc).__name__}: {exc}")

    print("\n— دليل قبول م٢ —\n")
    width = max(len(n) for n, _, _ in CHECKS)
    failed = sum(1 for _, ok, _ in CHECKS if not ok)
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name:<{width}}  {detail}")
    print(f"\n  {len(CHECKS) - failed}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
