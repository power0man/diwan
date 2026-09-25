#!/usr/bin/env python3
"""إسقاط البحث فوق المخزن القانوني (م٢): تطبيعٌ عربي + فهرس FTS5.

    python3 tools/rebuild_index.py rebuild
    python3 tools/rebuild_index.py search "مياه الصابورة"
    python3 tools/rebuild_index.py fingerprint

الفهرس **إسقاطٌ لا مصدر حقيقة**: يُشتق من المخزن (بعد فحص فهرسه
وسلاسله)، ويُهمل ويُعاد بلا خسارة. ومعيار تطابقه المعلن في المعمارية
**بصمة نتائج الاستعلامات** لا بايتات الملف — فملفا SQLite المتطابقان
منطقيًّا لا يتطابقان بايتًا، وهذا ليس عيبًا.

التطبيع عمودٌ مشتق لا تعديل للنص، وقواعده معلنة حصرًا: حذف الحركات
(‏U+064B–0652 وU+0670) والتطويل (ـ)، وتوحيد الألف (أإآٱ→ا) والياء
(ى→ي) والتاء المربوطة (ة→ه). ما سواها يُترك — التوسع قرارٌ يُقيَّد.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisitions import SourceRegister
from core.canonical import PayloadRejected, digest
from core.corpus import CorpusCatalog, CorpusFile
from core.ledger import LedgerCorrupt

ROOT = Path(__file__).resolve().parent.parent
# المخازن المسجلة للإسقاط: الاسم ← (فهرسه، ملف إسقاطه)
CORPORA = {
    "maritime": (ROOT / "corpus" / "maritime" / "_catalog.jsonl",
                 ROOT / "projections" / "maritime-fts.sqlite"),
    "lexicons": (ROOT / "corpus" / "lexicons" / "_catalog.jsonl",
                 ROOT / "projections" / "lexicons-fts.sqlite"),
    "lexicons-local": (ROOT / "corpus" / "lexicons-local" / "_catalog.jsonl",
                       ROOT / "projections" / "lexicons-local-fts.sqlite"),
}
CATALOG, INDEX = CORPORA["maritime"]   # الافتراض البحري — بصمة م٢ محفوظة

_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
                       "ى": "ي", "ة": "ه"})


def normalize(text: str) -> str:
    return _DIACRITICS.sub("", text).translate(_ALEF)


def _corpus_paths(corpus: str) -> tuple:
    """مخزنٌ مجهول يُرَدّ برمزٍ مسمى لا KeyError خام (عيب تدقيق م٥)."""
    if corpus not in CORPORA:
        raise PayloadRejected("corpus", "unknown_corpus",
                              f"مخزن غير مسجل: {corpus!r} — "
                              f"المسجل: {sorted(CORPORA)}")
    return CORPORA[corpus]


def rebuild(corpus: str = "maritime") -> int:
    catalog_path, index_path = _corpus_paths(corpus)
    catalog = CorpusCatalog(catalog_path)
    if not catalog.anchored():
        raise LedgerCorrupt("فهرس المخزن بلا مرساة — الإسقاط لا يُبنى فوق "
                            "فهرسٍ غير مرسو (يُرفض الغائب لا يُخفَّض صمتًا)")
    catalog.verify(strict=True, root=ROOT)
    register = SourceRegister(ROOT / "sources" / "acquisitions.jsonl")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.unlink(missing_ok=True)
    con = sqlite3.connect(index_path)
    con.execute("CREATE VIRTUAL TABLE pages USING fts5("
                "norm, doc_id UNINDEXED, part UNINDEXED, locus UNINDEXED, "
                "item_digest UNINDEXED)")
    n = 0
    for rec in sorted(catalog.current().values(), key=lambda r: r["doc_id"]):
        cf = CorpusFile(ROOT / rec["file"])
        cf.verify(strict=cf.anchored(), register=register)
        latest = {}
        for page in cf.pages():        # الصفحة النافذة لكل موضع فقط
            latest[page["item"]["locus"]] = page
        for page in latest.values():
            it = page["item"]
            con.execute("INSERT INTO pages VALUES (?,?,?,?,?)",
                        (normalize(it["text"]), rec["doc_id"], it["part"],
                         it["locus"], page["item_digest"]))
            n += 1
    con.commit()
    con.close()
    print(f"بُني الإسقاط [{corpus}]: {n} صفحة في {index_path.name}")
    return n


def search(query: str, limit: int = 10, match_any: bool = False,
           corpus: str = "maritime") -> list[dict]:
    """بحث الإسقاط: كل الكلمات (افتراضًا — دقةٌ للاستعلامات الذهبية
    وبصمتها) أو أيّها (`match_any` — استرجاعُ العقد للأسئلة الحرة)."""
    index_path = _corpus_paths(corpus)[1]
    if not index_path.exists():
        raise PayloadRejected("index", "index_missing",
                              f"لا إسقاط بحث — نفّذ rebuild أولًا: {index_path.name}")
    tokens = [t.replace('"', "") for t in normalize(query).split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise PayloadRejected("query", "query_empty_after_normalization",
                              f"استعلام بلا محتوى بعد التطبيع: {query!r}")
    con = sqlite3.connect(index_path)
    joiner = " OR " if match_any else " "
    q = joiner.join('"' + t + '"' for t in tokens)
    rows = con.execute(
        "SELECT doc_id, part, locus, item_digest, bm25(pages) AS r "
        "FROM pages WHERE pages MATCH ? ORDER BY r, item_digest LIMIT ?",
        (q, limit)).fetchall()
    con.close()
    return [{"doc_id": d, "part": p, "locus": l, "item_digest": h}
            for d, p, l, h, _ in rows]


# استعلامات ذهبية ثابتة — بصمتها معيارُ تطابق الإسقاط بعد إعادة البناء
GOLDEN_QUERIES = (
    "مياه الصابورة",
    "تسجيل السفن",
    "المخالفة الجسيمة",
    "التطقيم الآمن",
    "معاينة السفن الصغيرة",
    "سلامة الأرواح في البحار",
)


def results_fingerprint(queries=GOLDEN_QUERIES) -> str:
    payload = {q: [r["item_digest"] for r in search(q, limit=10)]
               for q in queries}
    return digest(payload)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rebuild"
    if cmd == "rebuild":
        rebuild(sys.argv[2] if len(sys.argv) > 2 else "maritime")
    elif cmd == "search":
        for r in search(" ".join(sys.argv[2:]) or "السفن"):
            print(f"  {r['doc_id']}  |  {r['locus']}  [{r['item_digest'][:12]}…]")
    elif cmd == "fingerprint":
        print(results_fingerprint())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
