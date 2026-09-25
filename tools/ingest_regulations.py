#!/usr/bin/env python3
"""استيعاب تصدير مشروع «اللوائح» (117 وثيقة) في المخزن القانوني (م٢).

    python3 tools/ingest_regulations.py [مسار التصدير]

التقطيع **حتميّ** (نفس المصدر ← نفس الصفحات ← نفس البصمات، وهو ما يقيسه
دليل القبول بإعادة الاستخلاص): الوثيقة تُشطر عند رؤوس المواد
(«المادة (N)» بأشكالها)، وما قبل أول مادة «التمهيد»، وما لا موادَّ فيه
(التقارير والمخرجات) يُشطر مقاطعَ فقرات ≤ حدٍّ معلن. كل صفحة مادةٌ
معرفية تمر ببوابة الحقوق قبل التقييد — **البوابة في core والفهمُ
التقطيعيّ هنا** (النواة تفرض ولا تفهم).

عديم التكرار: وثيقة رأسُ سلسلتها مطابقٌ لما في الفهرس تُتخطى.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisitions import SourceRegister
from core.canonical import digest
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem

SOURCE_ID = "claude-project-regulations-export"
# مسارُ تصدير اللوائح على جهاز المالك: من البيئة، أو الافتراضيُّ تحت مجلّد العمل.
# اللوائحُ ملفاتُ المالك الخاصة (ك٢٧) فلا يُثبَّت لها مسارٌ باسم حسابه في المستودع.
DEFAULT_EXPORT = os.environ.get("DIWAN_REGULATIONS_EXPORT",
                                str(Path.home() / "diwan-work" / "regulations-export.json"))
CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus" / "maritime"

# رأس مادة: «المادة» فرقمٌ (بقوسين أو بدونهما) **أو عددٌ مكتوب** — اللوائح
# السعودية كثيرًا ما ترقّم كتابةً («المادة الأولى»، «الحادية عشرة»،
# «الخامسة والعشرون»، «الثانية بعد المائة») — عيب التدقيق ١٢: بدونها فقدت
# 12 لائحة مواضع موادها. المعجم مغلق فلا يلتقط «المادة التالية» ونحوها.
_ORD = (r"(?:الأولى|الحادية|الثانية|الثالثة|الرابعة|الخامسة|السادسة"
        r"|السابعة|الثامنة|التاسعة|العاشرة|عشرة?|والعشرون|العشرون"
        r"|والثلاثون|الثلاثون|والأربعون|الأربعون|والخمسون|الخمسون"
        r"|والستون|الستون|والسبعون|السبعون|والثمانون|الثمانون"
        r"|والتسعون|التسعون|بعد|المائة|المئة)")
ARTICLE_RE = re.compile(
    r"^\s*#{0,6}\s*المادة\s*(?:[\(]?\s*[\d٠-٩]+\s*[\)]?|" + _ORD + r"(?:\s+" + _ORD + r"){0,3})"
    r"\s*[:：]?", re.M)
MAX_CHUNK = 6_000        # فوقه تُشطر المادة «تتماتٍ» مرقّمة
PARA_TARGET = 3_000      # هدف مقطع الفقرات لما لا موادَّ فيه

# المجال والأصالة بحسب مجلد التصدير — اللوائح نصوص أصل، وما سواها مشتق
FOLDER_META = {
    "لوائح": ("maritime-regulation", "original"),
    "تقارير": ("maritime-audit", "derived"),
    "claude": ("maritime-notes", "derived"),
}


def slugify(name: str) -> str:
    base = Path(name).stem
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[^\w؀-ۿ]+", "-", base).strip("-")
    return base[:80] or "doc"


def _subchunks(text: str, locus: str) -> list[tuple[str, str]]:
    if len(text) <= MAX_CHUNK:
        return [(locus, text)]
    out, i = [], 0
    while i < len(text):
        piece = text[i:i + MAX_CHUNK]
        n = len(out) + 1
        out.append((locus if n == 1 else f"{locus} — تتمة {n - 1}", piece))
        i += MAX_CHUNK
    return out


def chunk_document(text: str) -> list[tuple[str, str]]:
    """يعيد [(الموضع، النص)] حتميًّا. الموضع سطرُ رأس المادة كما ورد."""
    text = text.replace("\r\n", "\n")
    heads = list(ARTICLE_RE.finditer(text))
    chunks: list[tuple[str, str]] = []
    if heads:
        if heads[0].start() > 0:
            pre = text[:heads[0].start()].strip()
            if pre:
                chunks += _subchunks(pre, "التمهيد")
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            body = text[m.start():end].strip()
            locus = " ".join(m.group(0).split())[:80].rstrip(":：").strip()
            if body:
                chunks += _subchunks(body, locus)
    else:
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf: list[str] = []
        size = 0
        for p in paras:
            if buf and size + len(p) > PARA_TARGET:
                chunks.append((f"المقطع {len(chunks) + 1}", "\n\n".join(buf).strip()))
                buf, size = [], 0
            buf.append(p)
            size += len(p)
        if buf:
            chunks.append((f"المقطع {len(chunks) + 1}", "\n\n".join(buf).strip()))
    chunks = [(l, t) for l, t in chunks if t]
    # الموضع فريدٌ داخل الوثيقة بالبنية: ترقيمُ الملاحق قد يعيد «المادة (N)»
    # (وقع فعلًا في ماربول) — فالورود الثاني فصاعدًا يُميَّز، وإلا صار
    # الشاهد ملتبسًا والاستشهاد أعمى
    seen: dict[str, int] = {}
    out: list[tuple[str, str]] = []
    for locus, t in chunks:
        seen[locus] = seen.get(locus, 0) + 1
        out.append((locus if seen[locus] == 1
                    else f"{locus} — الورود {seen[locus]}", t))
    return out


def doc_items(filename: str, content: str) -> list[KnowledgeItem]:
    folder = filename.split("/")[0] if "/" in filename else ""
    domain, originality = FOLDER_META.get(folder, ("maritime-notes", "derived"))
    part = Path(filename).stem[:120]
    use_dist = False  # ق٤٨: سحب إذن التوزيع وقصر اللوائح على درايف المالك
    return [KnowledgeItem(
        text=t, lang="ar", domain=domain,
        use_internal=True, use_distribution=use_dist,   # ق٤٨: داخلي فقط
        source_id=SOURCE_ID, locus=locus, originality=originality,
        part=part,
    ) for locus, t in chunk_document(content)]


def main() -> int:
    export = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXPORT).expanduser()
    data = json.loads(export.read_text(encoding="utf-8"))
    docs = data["docs"]
    root = CORPUS_DIR.parent.parent
    register = SourceRegister(root / "sources" / "acquisitions.jsonl", create=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    catalog = CorpusCatalog(CORPUS_DIR / "_catalog.jsonl", create=True)
    if catalog.anchored():
        catalog.verify(strict=True)
    elif catalog.current():
        raise SystemExit("فهرس المخزن ذو قيود بلا مرساة — لا يُبنى فوقه "
                         "(قصُّ ذيلٍ محتمل): يُفحص يدويًّا")
    known = catalog.current()

    total_pages = ingested = skipped = 0
    for idx, doc in enumerate(docs, 1):
        doc_id = f"{idx:03d}__{slugify(doc['filename'])}"
        rel = known[doc_id]["file"] if doc_id in known else f"corpus/maritime/{doc_id}.jsonl"
        cf = CorpusFile(root / rel)
        items = doc_items(doc["filename"], doc["content"])
        if doc_id in known and cf.count() and known[doc_id]["head"] == cf.head():
            fresh = [digest(it.fingerprint_payload()) for it in items]
            latest = {}
            for pg in cf.pages():
                latest[pg["item"]["locus"]] = pg["item_digest"]
            if sorted(fresh) == sorted(latest.values()):
                skipped += 1
                total_pages += known[doc_id]["pages"]
                continue
            # تغيّر اشتقاق الوثيقة بنيويًّا (مواضع جديدة): التاريخ لا
            # يُمَسّ — ملفُّ نسخةٍ جديد يشير إليه الفهرس قيدَ نسخةٍ
            version = sum(1 for r in catalog._checked_records()
                          if r["doc_id"] == doc_id) + 1
            rel = f"corpus/maritime/{doc_id}__v{version}.jsonl"
            cf = CorpusFile(root / rel)
            print(f"  تصحيح بنيوي: {doc_id} → نسخة v{version}")
        if cf.count():
            if doc_id in known:
                raise SystemExit(
                    f"ملف {rel} رأسه لا يطابق فهرسه — لا يُكتب فوقه ولا يُحذف "
                    f"(قد يكون تصحيحًا لم يُفهرس): يُفحص يدويًّا")
            # سلسلة جزئية من فشل I/O قبل الفهرسة: التقطيع حتميّ فالحذف
            # وإعادة الاستيعاب يعيدان نفس السلسلة والرأس (عيب التدقيق ٥)
            print(f"  استرداد: {rel} بقايا دفعة غير مفهرسة — يُحذف ويُعاد")
            (root / rel).unlink()
            anchor = (root / rel).with_suffix(".jsonl.anchor")
            anchor.unlink(missing_ok=True)
            cf = CorpusFile(root / rel)
        head = cf.ingest(doc_id, items, register)
        cf.anchor()
        catalog.record(doc_id, rel, len(items), head)
        known[doc_id] = {"file": rel, "head": head, "pages": len(items)}
        ingested += 1
        total_pages += len(items)
    catalog.verify(strict=catalog.anchored(), root=root)
    catalog.anchor()
    print(f"استُوعب {ingested} وثيقة وتُخطي {skipped} مطابقة — "
          f"{total_pages} صفحة في المخزن، والفهرس سليم بمرساته.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
