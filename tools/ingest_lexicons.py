#!/usr/bin/env python3
"""استيعاب المعاجم من قاعدة الشاملة في مخزن المعاجم (م٥، ق٢٠).

    python3 tools/ingest_lexicons.py

مخزنان بقرارٍ معلن: `corpus/lexicons/` **مدفوع** (القاموس المحيط
والمعجم الوسيط — ‏~15MB)، و`corpus/lexicons-local/` **محليّ غير مدفوع**
(لسان العرب وتاج العروس — ‏~105MB فوق طاقة git بلا LFS؛ فهرسُه المحلي
معه، وإعادةُ إنتاجه حتمية من المصدر المسجل بالأصول). كل صفحةٍ مادةٌ
عبر بوابة الحقوق، وموضعُها **رقم المطبوع بجزئه** كما في القاعدة
(`part`/`page_num`)، والفاقدُ رقمَه (صفحات الترجمة التمهيدية) موضعُه
تسلسليٌّ موسوم.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisitions import SourceRegister
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem

ROOT = Path(__file__).resolve().parent.parent
SRC = Path.home() / "diwan-work" / "sources-raw" / "shamela4-full-db" / "30__الغريب-والمعاجم"
SOURCE_ID = "hf-shamela4-full-db"

# (مجلد المصدر، معرف الوثيقة، العنوان، المخزن الوجهة)
BOOKS = [
    ("2558__القاموس-المحيط", "qamus-muhit", "القاموس المحيط", "lexicons"),
    ("2500__المعجم-الوسيط", "mujam-wasit", "المعجم الوسيط", "lexicons"),
    ("1462__لسان-العرب", "lisan-arab", "لسان العرب", "lexicons-local"),
    ("2502__تاج-العروس-من-جواهر-القاموس", "taj-arus", "تاج العروس",
     "lexicons-local"),
]

_MARKUP = re.compile(r"<[^>]+>")


def page_items(book_dir: Path, title: str) -> list[KnowledgeItem]:
    items = []
    with (book_dir / "pages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            body = _MARKUP.sub("", (d.get("body") or "").replace("\r", "\n")).strip()
            if not body:
                continue
            part = str(d.get("part") or "").strip()
            page_num = d.get("page_num")
            # الموضع مفتاحُ النسخ داخل الوثيقة — فيحمل الجزء في ذاته وإلا
            # تصادمت «ص5» عبر الأجزاء وانهار «النافذ» (لغم مُصاد حيًّا)
            vol = f"ج{part} " if part else ""
            locus = (f"{vol}ص{page_num}" if page_num
                     else f"{vol}تسلسل{d['sequence_num']} (بلا رقم مطبوع)")
            items.append(KnowledgeItem(
                text=body, lang="ar", domain="arabic-lexicon",
                use_internal=True, use_distribution=False,
                source_id=SOURCE_ID, locus=locus,
                originality="original",
                part=f"{title}" + (f" — ج{part}" if part else ""),
            ))
    # فرادة الموضع داخل الوثيقة (كما مقطّع اللوائح): الورود يميَّز
    seen: dict[tuple[str, str], int] = {}
    out = []
    for it in items:
        k = (it.part, it.locus)
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            it = KnowledgeItem(**{**it.__dict__,
                                  "locus": f"{it.locus} — الورود {seen[k]}"})
        out.append(it)
    return out


def main() -> int:
    register = SourceRegister(ROOT / "sources" / "acquisitions.jsonl", create=True)
    for src_dir, doc_id, title, store in BOOKS:
        book_dir = SRC / src_dir
        if not (book_dir / "pages.jsonl").exists():
            raise SystemExit(f"مصدر مفقود: {book_dir} — أكمل التنزيل أولًا")
        store_dir = ROOT / "corpus" / store
        store_dir.mkdir(parents=True, exist_ok=True)
        catalog = CorpusCatalog(store_dir / "_catalog.jsonl", create=True)
        if catalog.anchored():
            catalog.verify(strict=True)
        elif catalog.current():
            raise SystemExit(f"فهرس {store} ذو قيود بلا مرساة — يُفحص يدويًّا")
        known = catalog.current()
        rel = f"corpus/{store}/{doc_id}.jsonl"
        cf = CorpusFile(ROOT / rel)
        if doc_id in known and cf.count() \
                and known[doc_id]["head"] == cf.head():
            print(f"  قائم: {title} ({known[doc_id]['pages']} صفحة)")
            continue
        if cf.count():
            raise SystemExit(f"ملف قائم برأس غير مفهرس: {rel} — يُفحص يدويًّا")
        items = page_items(book_dir, title)
        head = cf.ingest(doc_id, items, register)
        cf.anchor()
        catalog.record(doc_id, rel, len(items), head)
        catalog.anchor()
        print(f"  استُوعب: {title} — {len(items)} صفحة [{store}]")
    for store in ("lexicons", "lexicons-local"):
        cat = CorpusCatalog(ROOT / "corpus" / store / "_catalog.jsonl")
        cat.verify(strict=True, root=ROOT)
        print(f"فهرس {store}: {len(cat.current())} معجم — سليم بمرساته.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
