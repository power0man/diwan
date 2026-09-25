#!/usr/bin/env python3
"""بذر مسرد الحوكمة البحرية من مواد التعريفات + السوابق التأسيسية (م٣).

    python3 tools/extract_terms.py

المصدر الوحيد للمصطلحات: **صفحات المخزن نفسها** — سطر «مصطلح: تعريف»
داخل مواد التعريفات في اللوائح الأصل (domain=maritime-regulation)،
وشاهدُ كل مدخلٍ بصمةُ صفحته عينها؛ فالمصطلح المعتمد هنا اعتمدته
اللائحةُ نصًّا لا نحن. الاستخلاص حتميّ، والمكرر عبر اللوائح يؤخذ أول
وروده (بترتيب الفهرس) ويُترك الباقي لطور التعارضات.

ويؤسس سجلَّ السوابق بقاعدتي الحسم المعتمدتين في ق٢٠ (نصًّا لا اجتهادًا).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.corpus import CorpusCatalog, CorpusFile
from core.glossary import Glossary, GlossaryEntry
from core.rulings import Ruling, Rulings

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_PATH = ROOT / "glossaries" / "maritime.jsonl"
RULINGS_PATH = ROOT / "rulings" / "precedence.jsonl"

# سطر تعريف: مصطلحٌ قصير (لا يبدأ برقم ولا يحوي نقطتين) ثم «:» ثم تعريف واف
# ملاحظة النمط: مسافات السطر نفسه فقط حول النقطتين — \s تعبر السطر
# فيبتلع سطرُ تمهيدٍ منتهٍ بنقطتين سطرَ المصطلح التالي (لغم مُصاد)
TERM_LINE = re.compile(r"^([^:：\n\d][^:：\n]{1,58}?)[ \t]*[:：][ \t]*(.{15,})$", re.M)
# صفحة تعريفات: افتتاحية «يقصد بـ...» المعيارية في اللوائح
DEFS_MARKER = re.compile(r"يقصد\s+بال")
_BANNED_IN_TERM = ("المادة", "الفصل", "الجزء", "البند", "الفقرة")


def extract_from_page(text: str) -> list[tuple[str, str]]:
    if not DEFS_MARKER.search(text):
        return []
    out = []
    for m in TERM_LINE.finditer(text):
        # صفوف جداول Markdown تحمل «|» طرفية — تُشذَّب؛ والشريطة
        # **الداخلية** (صف متعدد الخلايا) مصطلحٌ مشوه فيُرفض لا يُعتمد
        term = " ".join(m.group(1).split()).strip("|").strip()
        definition = " ".join(m.group(2).split()).strip("|").strip()
        term = re.sub(r"^يقصد\s+ب", "", term).strip()  # حرف الجر وحده — لا «ال» التعريف
        if not term or "|" in term or len(definition) < 15:
            continue
        if any(b in term for b in _BANNED_IN_TERM):
            continue
        out.append((term, definition))
    return out


def main() -> int:
    catalog = CorpusCatalog(ROOT / "corpus" / "maritime" / "_catalog.jsonl")
    catalog.verify(strict=True, root=ROOT)
    # العضوية وجهان (عيب تدقيق م٣/٢): «النافذ» شرطُ الشاهد الجديد،
    # والتاريخ كله يبقي شواهدَ الأمس صحيحةَ العضوية عند الفحص
    all_digests = catalog.page_digests(ROOT)
    current_digests = catalog.page_digests(ROOT, current_only=True)

    glossary = Glossary(GLOSSARY_PATH, create=True)
    if glossary.anchored():
        glossary.verify(all_digests, strict=True)
    elif glossary.current():
        raise SystemExit("مسرد ذو قيود بلا مرساة — يُفحص يدويًّا")

    existing = glossary.current()
    added = updated = 0
    handled: set[str] = set()
    for rec in sorted(catalog.current().values(), key=lambda r: r["doc_id"]):
        pages = CorpusFile(ROOT / rec["file"]).pages()
        latest = {}
        for page in pages:            # الصفحة النافذة لكل موضع فقط
            latest[page["item"]["locus"]] = page
        for page in latest.values():
            it = page["item"]
            if it["domain"] != "maritime-regulation":
                continue
            for term, definition in extract_from_page(it["text"]):
                if term in handled:
                    continue
                handled.add(term)
                cur = existing.get(("maritime", term))
                if cur is not None:
                    # لا يُحدَّث إلا مصطلحٌ شاهدُه صفحةٌ منسوخة —
                    # فتصحيح المخزن يصل المسردَ (عيب تدقيق م٣/٣)
                    if cur["entry"]["evidence_digest"] in current_digests:
                        continue
                    updated += 1
                else:
                    added += 1
                glossary.add(GlossaryEntry(
                    glossary="maritime", term=term, definition=definition,
                    evidence_digest=page["item_digest"], status="approved",
                ), current_digests)
    glossary.verify(all_digests, strict=glossary.anchored())
    glossary.anchor()

    rulings = Rulings(RULINGS_PATH, create=True)
    if rulings.anchored():
        rulings.verify(strict=True)
    rulings.rule(Ruling(
        topic="تقاطع-الاختصاصين",
        text="الاصطلاحُ المجاليُّ يقيِّد اللغويَّ العام: العقدة صاحبة المجال "
             "حجةٌ في مصطلحها، واللغويات تحكم سلامة الصياغة لا اختيار المصطلح.",
        basis="ق٢٠ — المعمارية المعتمدة، خامسًا (سلم التعارض، البند أ)",
        decided_on="2026-09-20"))
    rulings.rule(Ruling(
        topic="سلم-الأصالة",
        text="عند تساوي الاختصاص تُقدَّم المادة الأعلى أصالة: "
             "أصل ثم معرَّب ثم ملخَّص ثم مستنبَط — حقلٌ تفحصه النواة بلا فهم.",
        basis="ق٢٠ — المعمارية المعتمدة، خامسًا (سلم التعارض، البند ب)",
        decided_on="2026-09-20"))
    rulings.verify(strict=rulings.anchored())
    rulings.anchor()

    total = len(glossary.approved("maritime"))
    print(f"أُضيف {added} وحُدِّث {updated} — المعتمد النافذ {total}، "
          f"والسوابق {len(rulings.current())}، والسجلان مختومان.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
