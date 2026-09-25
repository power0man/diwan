"""المخزن القانوني للصفحات (م٢، ق٢٠) — النصوص موادَّ مبصومة لا ملفات.

كل صفحة **مادة معرفية** تجتاز بوابة الحقوق (`admitted_item`) قبل أن
تُقيَّد، وكل وثيقة سلسلةُ قيودٍ إضافية-فقط بنمط Ledger نفسه (فحصها
`Ledger.verify_chain` بلا تعديل)، والفهرسُ فوقها إسقاطٌ يُهمل ويُعاد.
النص المصدر لا يُعدَّل أبدًا — التصحيح قيدُ نسخةٍ في وثيقةٍ لاحقة.

الإلحاق هنا **دفعيّ بذيلٍ محفوظ في الذاكرة**: `Ledger.append` يقرأ الملف
كله لكل قيد (كلفة خطية حذّر منها تحكيم المعمارية)، والاستيعاب يكتب آلاف
الصفحات — فيقرأ `CorpusFile` الذيل مرةً ثم يمدّ السلسلة بنفس الصيغة
حرفًا حرفًا. لا مساس بطبقة القانون: القارئ والفاحص هما `Ledger` القائم.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import unicodedata

from core.acquisitions import SourceRegister
from core.canonical import PayloadRejected, canonical_bytes, digest
from core.knowledge import ITEM_PAYLOAD_FIELDS, KnowledgeItem, validated_item
from core.ledger import GENESIS, Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append, write_lock

_HEX64 = re.compile(r"[0-9a-f]{64}")

_PAGE_KEYS = frozenset({"kind", "doc_id", "item", "item_digest"})

# تصنيفات الملكية الفكرية والسيادة المعرفية (م١٦ — المادة 4 من نظام المؤلف السعودي)
RIGHTS_STATUTORY_PUBLIC_DOMAIN = "statutory_public_domain"
RIGHTS_HERITAGE_PUBLIC_DOMAIN = "heritage_public_domain"
RIGHTS_RESTRICTED_COMMENTARY = "restricted_commentary"
RIGHTS_CLASSES = frozenset({
    RIGHTS_STATUTORY_PUBLIC_DOMAIN,
    RIGHTS_HERITAGE_PUBLIC_DOMAIN,
    RIGHTS_RESTRICTED_COMMENTARY,
})

_STATUTORY_DOC_TYPES = frozenset({
    "regulation", "law", "decision", "judgment", "treaty", "official",
    "لائحة", "نظام", "قرار", "حكم", "معاهدة", "وثيقة_رسمية",
})


def classify_copyright_status(
    doc_type: str,
    author_death_year: int | None = None,
    current_year: int = 2026,
    has_commercial_commentary: bool = False,
) -> str:
    """توصيفٌ تحليلي لحالة الحقوق (Descriptive policy helper) وفق نظام حماية حقوق المؤلف السعودي (م/41).

    تنبيه حوكمي (ق٤٦ / د٣): هذه الدالة أداة توصيف وتصنيف للسياسات المستندية،
    وليست بوابة اعتراض آلية (Automated runtime write gate) تعترض مسار النشر في
    `publish/` أو مسار استيعاب الصفحات في `CorpusFile.ingest`. فبوابة الاستيعاب
    الحتمية للمشروع هي `SourceRegister.admitted_item` (ق١/ق٢٠).

    المادة الرابعة: تسقط الحماية صراحة عن الأنظمة واللوائح والقرارات الإدارية
    والأحكام القضائية والوثائق الرسمية والمعاهدات الدولية (Statutory Public Domain).
    المادة التاسعة عشرة: ينقضي حق المؤلف بمضي 50 عاماً على وفاته (Heritage Public Domain).
    التحقيقات المعاصرة: تبقى مقيدة الاستخدام الداخلي صيانة للحقوق (Restricted Commentary).
    """
    clean_type = doc_type.strip().lower()
    if clean_type in _STATUTORY_DOC_TYPES:
        return RIGHTS_STATUTORY_PUBLIC_DOMAIN

    if author_death_year is not None and (current_year - author_death_year) > 50:
        if has_commercial_commentary:
            return RIGHTS_RESTRICTED_COMMENTARY
        return RIGHTS_HERITAGE_PUBLIC_DOMAIN

    if has_commercial_commentary:
        return RIGHTS_RESTRICTED_COMMENTARY

    # الافتراض التحفظي صيانة للحقوق
    return RIGHTS_RESTRICTED_COMMENTARY


class CorpusFile:
    """سلسلة صفحات وثيقةٍ واحدة — كتابة دفعية، وقراءة وفحص بأدوات Ledger."""

    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self._ledger = Ledger(path, create=create)
        self.path = Path(path)

    # — كتابة دفعية عبر بوابة الحقوق —

    def ingest(self, doc_id: str, items: list[KnowledgeItem],
               register: SourceRegister) -> str:
        """يقيّد صفحات الوثيقة موادَّ **مقبولة من بوابة الحقوق**، ويعيد رأس
        السلسلة. البوابة أولًا كلها ثم الكتابة — فلا وثيقة نصفها داخل."""
        if not isinstance(doc_id, str) or not doc_id.strip() \
                or doc_id != doc_id.strip():
            raise PayloadRejected("corpus.doc_id", "doc_id_invalid",
                                  f"معرّف وثيقة نصّي مشذَّب مطلوب: {doc_id!r}")
        if not items:
            raise PayloadRejected("corpus.items", "doc_empty",
                                  f"وثيقة بلا صفحات لا تُستوعب: {doc_id!r}")
        admitted = [register.admitted_item(it) for it in items]
        with write_lock(self._ledger):
            return self._locked_ingest(doc_id, admitted)

    def _locked_ingest(self, doc_id: str, admitted: list[KnowledgeItem]) -> str:
        prev = self._ledger.head()
        seq = self._ledger.count()
        with self.path.open("a", encoding="utf-8") as f:
            for it in admitted:
                payload = it.fingerprint_payload()
                record = {"kind": "page", "doc_id": doc_id,
                          "item": payload, "item_digest": digest(payload)}
                entry = {"prev": prev, "seq": seq, "record": record}
                entry_digest = digest(entry)
                f.write(canonical_bytes({"digest": entry_digest, **entry})
                        .decode("utf-8") + "\n")
                prev, seq = entry_digest, seq + 1
            f.flush()
            os.fsync(f.fileno())
        return prev if admitted else self._ledger.head()

    # — قراءة (لا تفشل مفتوحة) —

    def _checked_records(self) -> list[dict]:
        self._ledger.verify_chain()
        out = []
        for i, e in enumerate(self._ledger.entries()):
            rec = e.get("record", {})
            if rec.get("kind") != "page":
                continue
            missing = _PAGE_KEYS - set(rec)
            if missing:
                raise LedgerCorrupt(f"قيد page رقم {i} ناقص الحقول: {sorted(missing)}")
            out.append(rec)
        return out

    def pages(self) -> list[dict]:
        return self._checked_records()

    def head(self) -> str:
        return self._ledger.head()

    def count(self) -> int:
        return self._ledger.count()

    # — حوكمة —

    def verify(self, strict: bool = False,
               register: SourceRegister | None = None) -> bool:
        """السلسلة، وبصمة كل مادة، وتحققها — وبسجلٍّ: بوابة حقوقها أيضًا."""
        self._ledger.verify_chain(strict)
        for i, rec in enumerate(self._checked_records()):
            p = rec["item"]
            if not isinstance(p, dict) or frozenset(p) != ITEM_PAYLOAD_FIELDS:
                raise LedgerCorrupt(
                    f"قيد page رقم {i}: مفاتيح الحمولة لا تطابق عقد المادة حصرًا")
            try:
                it = KnowledgeItem(
                    text=p["text"], lang=p["lang"], domain=p["domain"],
                    use_internal=p["use_internal"],
                    use_distribution=p["use_distribution"],
                    source_id=p["source_id"], locus=p["locus"],
                    originality=p["originality"], part=p["part"],
                    glossary_ref=p["glossary_ref"],
                )
            except (KeyError, TypeError) as exc:
                raise LedgerCorrupt(f"قيد page رقم {i}: مادة مشوهة — {exc}") from exc
            # بصمة المادة المعاد بناؤها هي الحكم: تفرض المفاتيح والنسخة
            # والقيم معًا — حمولةٌ ليست fingerprint_payload لمادةٍ ما تسقط هنا
            if digest(it.fingerprint_payload()) != rec["item_digest"]:
                raise LedgerCorrupt(
                    f"قيد page رقم {i}: بصمة المادة لا تطابق إعادة بنائها")
            try:
                if register is not None:
                    register.admitted_item(it)
                else:
                    validated_item(it)
            except Exception as exc:
                raise LedgerCorrupt(
                    f"قيد page رقم {i} لا يجتاز التحقق: {exc}") from exc
        return True

    def anchored(self) -> bool:
        return self._ledger.anchor_path.exists()

    def anchor(self) -> dict:
        return self._ledger.anchor()


class CorpusCatalog:
    """فهرس وثائق المخزن — قيدٌ لكل وثيقة برأس سلسلتها، إضافي-فقط.

    عديم التكرار على (doc_id، head): إعادة استيعابٍ مطابقةٍ لا تضيف قيدًا،
    وتغيُّر الرأس (وثيقة صُححت بقيود جديدة) قيدُ نسخةٍ جديد.
    """

    _KEYS = frozenset({"kind", "doc_id", "file", "pages", "head"})

    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self._ledger = Ledger(path, create=create)

    def record(self, doc_id: str, file: str, pages: int, head: str) -> str:
        for name, v in (("doc_id", doc_id), ("file", file)):
            if not isinstance(v, str) or not v.strip() or v != v.strip():
                raise PayloadRejected(f"catalog.{name}", f"{name}_invalid",
                                      f"قيمة نصّية مشذَّبة مطلوبة: {v!r}")
        if isinstance(pages, bool) or not isinstance(pages, int) or pages <= 0:
            raise PayloadRejected("catalog.pages", "pages_not_positive",
                                  f"عدد صفحات موجب مطلوب: {pages!r}")
        if not isinstance(head, str) or not _HEX64.fullmatch(head) \
                or head == GENESIS:
            raise PayloadRejected("catalog.head", "head_invalid",
                                  f"رأس سلسلة فعلي مطلوب (لا GENESIS): {head!r}")
        cur = self.current().get(doc_id)
        if cur is not None and cur["head"] == head:
            return cur["head"]
        sealed_append(self._ledger,
                      {"kind": "doc_ingested", "doc_id": doc_id,
                       "file": file, "pages": pages, "head": head},
                      "فهرس المخزن")
        return head

    def _checked_records(self) -> list[dict]:
        require_seal(self._ledger, "فهرس المخزن")
        self._ledger.verify_chain()
        out = []
        for i, e in enumerate(self._ledger.entries()):
            rec = e.get("record", {})
            if rec.get("kind") != "doc_ingested":
                continue
            missing = self._KEYS - set(rec)
            if missing:
                raise LedgerCorrupt(
                    f"قيد doc_ingested رقم {i} ناقص الحقول: {sorted(missing)}")
            out.append(rec)
        return out

    def current(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for rec in self._checked_records():
            out[rec["doc_id"]] = rec
        return out

    def verify(self, strict: bool = False, root: Path | None = None) -> bool:
        """السلسلة — وبجذرٍ: مطابقة **الحالي** لكل وثيقة (رأسًا وعددًا)
        لملفها الفعلي، وكشفُ أي ملفٍ دخيل في مجلد المخزن خارج الفهرس.

        «الحالي» لا التاريخ: التصحيح المشروع قيدُ نسخةٍ جديد، والقيود
        القديمة تاريخٌ لا مطالبةٌ دائمة برأسٍ زال (عيب التدقيق القاتل).
        والفحص قراءةٌ لا تُنشئ ملفًا مفقودًا لتفحصه."""
        self._ledger.verify_chain(strict)
        if root is not None:
            current = self.current()
            for rec in current.values():
                path = root / rec["file"]
                if not path.exists():
                    raise LedgerCorrupt(
                        f"وثيقة {rec['doc_id']!r}: ملفها مفقود ({rec['file']})")
                cf = CorpusFile(path, create=False)
                if cf.head() != rec["head"]:
                    raise LedgerCorrupt(
                        f"وثيقة {rec['doc_id']!r}: رأس الملف لا يطابق الفهرس")
                actual = len(cf.pages())
                if actual != rec["pages"]:
                    raise LedgerCorrupt(
                        f"وثيقة {rec['doc_id']!r}: عدد الصفحات {actual} "
                        f"والفهرس يقول {rec['pages']}")
            # الدخيل يُقاس على **كل** ملفات التاريخ: ملفُ نسخةٍ قديم
            # ليس دخيلًا — التاريخ محفوظ لا يُمَسّ
            known = {unicodedata.normalize("NFC", rec["file"]) for rec in self._checked_records()}
            catalog_dir = self._ledger.path.parent
            for p in sorted(catalog_dir.glob("*.jsonl")):
                if p == self._ledger.path:
                    continue
                rel = str(p.relative_to(root)) if p.is_relative_to(root) else p.name
                if unicodedata.normalize("NFC", rel) not in known:
                    raise LedgerCorrupt(f"ملف دخيل في المخزن خارج الفهرس: {rel}")
        return True

    # — حسم الشواهد: البصمة إلى صفحتها (أساسُ م٣: لا شاهد إلا بصفحة قائمة) —

    def _file_pages(self, root: Path, rec: dict) -> list[dict]:
        path = root / rec["file"]
        if not path.exists():   # القراءة لا تُنشئ مفقودًا لتقرأه
            raise LedgerCorrupt(
                f"وثيقة {rec['doc_id']!r}: ملفها مفقود ({rec['file']})")
        return CorpusFile(path, create=False).pages()

    def page_digests(self, root: Path,
                     current_only: bool = False) -> frozenset[str]:
        """بصمات صفحات المخزن — **تاريخ الملفات كله** افتراضًا (شاهدٌ
        قُيّد يومَ كانت صفحتُه نافذةً يبقى صحيحَ العضوية، ولو نُسخ ملفُ
        وثيقته ببنيةٍ أحدث)، وبـcurrent_only «النافذ» فقط: آخر قيدٍ لكل
        موضعٍ في **الملف الحالي** لكل وثيقة — معيار قبول شاهدٍ جديد."""
        out: set[str] = set()
        if current_only:
            for rec in self.current().values():
                latest: dict[str, str] = {}
                for page in self._file_pages(root, rec):
                    latest[page["item"]["locus"]] = page["item_digest"]
                out.update(latest.values())
            return frozenset(out)
        seen_files: set[str] = set()
        for rec in self._checked_records():
            if rec["file"] in seen_files:
                continue
            seen_files.add(rec["file"])
            for page in self._file_pages(root, rec):
                out.add(page["item_digest"])
        return frozenset(out)

    def find_page(self, root: Path, item_digest: str) -> dict | None:
        """يعيد قيد الصفحة لبصمةٍ ما مع علم `superseded` (ليست ضمن
        «النافذ») — يبحث تاريخَ الملفات كله. مسحٌ خطي مقبول بحجم اليوم؛
        يوم يؤلم يُبنى إسقاطُ بصمةٍ↦موضع قابلٌ لإعادة البناء."""
        current = self.page_digests(root, current_only=True)
        seen_files: set[str] = set()
        for rec in self._checked_records():
            if rec["file"] in seen_files:
                continue
            seen_files.add(rec["file"])
            for page in self._file_pages(root, rec):
                if page["item_digest"] == item_digest:
                    return {**page,
                            "superseded": item_digest not in current}
        return None

    def anchored(self) -> bool:
        return self._ledger.anchor_path.exists()

    def anchor(self) -> dict:
        return self._ledger.anchor()
