#!/usr/bin/env python3
"""إسقاط النشر — النسخة المنشورة استعلامُ حقوق (م٧، ق٢٠).

    python3 tools/publish_projection.py

«النسخة المنشورة = استعلام: كل مادةٍ وسمُ توزيعها يسمح». الإسقاط يمشي
على مخازن المعرفة كلها (النسخ النافذة فقط)، ويُدخل حصرًا المواد
الموسومة `use_distribution=True` **بعد** إعادة فحصها على بوابة
الأصول (`admitted_item`) — فوسمٌ في ملفٍ لا يُصدَّق فوق قيدِ مصدره.
الناتج `publish/` مدفوعٌ لأنه بالتعريف قابل التوزيع، وبيانُه
(`publish/_manifest.jsonl`) سجلٌّ مختوم يحمل بصمة كل مادة منشورة
وأصلَها — وإسقاطٌ **فارغ** صادقٌ حين لا حقوق توزيع بعد (اللوائح
السعودية معلقة على إذن، وIMO محمي النشر — قيود الأصول ناطقة).

معيار القبول السلبي في `acceptance_m7.py`: لا بايت من مادةٍ
داخلية-فقط في مخرجات النشر — مسحًا شاملًا لكل المواد المحجوبة.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisitions import SourceRegister
from core.canonical import PayloadRejected
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger, LedgerCorrupt
from core.seal import sealed_append

ROOT = Path(__file__).resolve().parent.parent
STORES = ("maritime", "lexicons", "lexicons-local")


def iter_current_items(root: Path, missing: list | None = None):
    """(المخزن، الوثيقة، قيد الصفحة) لكل صفحة في النسخ النافذة.

    الفهرس يُتحقق **بجذره** (وجود الملفات وعدّها) ورأسُ كل ملفٍ يُطابَق
    على قيده — الوسيط الضائع كان يترك الربط بلا فحص (تدقيق م٧).
    والمخزن الغائب لا يُتجاوز صمتًا: يُقيَّد في `missing` ويعلنه
    البيان (‏`lexicons-local` محليٌّ بقرار معلن فغيابه في النسخ
    المستنسخة متوقع — لكنه يُذكر لا يُبتلع)."""
    for store in STORES:
        cat_path = root / "corpus" / store / "_catalog.jsonl"
        if not cat_path.exists():
            if missing is not None:
                missing.append(store)
            continue
        catalog = CorpusCatalog(cat_path, create=False)
        catalog.verify(strict=True, root=root)
        for doc_id, rec in sorted(catalog.current().items()):
            cf = CorpusFile(root / rec["file"], create=False)
            if cf.head() != rec["head"]:
                raise LedgerCorrupt(
                    f"رأس {rec['file']} لا يطابق قيد الفهرس — "
                    "لا نشر فوق ربط مكسور")
            cf.verify(strict=True)
            for page in cf.pages():
                yield store, doc_id, page


def _output_name(store, doc_id):
    name = f"{store}__{doc_id}"
    if (not isinstance(doc_id, str) or not doc_id or any(c in name for c in "/\\")
            or any(unicodedata.category(c).startswith("C") for c in name)
            or any(len(unicodedata.normalize(form, name + ".jsonl").encode("utf-8")) > 255
                   for form in ("NFC", "NFD"))):
        raise PayloadRejected("publish.file", "publish_name_invalid", "اسم مخرج النشر ليس مكونًا صالحًا محدودًا")
    return name


def plan(root: Path = ROOT) -> dict:
    """يفحص كل المواد والحقوق قبل أي تغيير في إسقاط النشر.

    النتيجة داخلية وقد تحمل نصوصًا قابلة للتوزيع؛ report فقط للتقرير العام.
    لا يكتب هذا المسار إلى publish أو بيانه، ولا يمنح حقوقًا جديدة.
    """
    register = SourceRegister(root / "sources" / "acquisitions.jsonl", create=False)
    register.verify(strict=True)
    refused = []
    missing: list[str] = []
    published = skipped = rights_refused = 0
    files: dict[str, list] = {}
    folded_names = {}
    for store, doc_id, page in iter_current_items(root, missing):
        it = page["item"]
        if not it.get("use_distribution"):
            skipped += 1
            continue
        # الوسم لا يُصدَّق فوق قيد المصدر — البوابة تعيد الفحص، والحقل
        # الغائب رفضٌ مسمى لا KeyError خام
        try:
            candidate = KnowledgeItem(**{
                k: it[k] for k in ("text", "lang", "domain", "use_internal",
                                   "use_distribution", "source_id", "locus",
                                   "originality", "part", "glossary_ref")})
        except KeyError as exc:
            raise PayloadRejected(
                "publish.item", "item_fields_missing",
                f"صفحة {page['item_digest'][:12]} بلا حقل {exc}") from exc
        try:
            register.admitted_item(candidate)
        except PayloadRejected as exc:
            rights_refused += 1
            refused.append({
                "kind": "publish_refused",
                "store": store, "doc_id": doc_id,
                "item_digest": page["item_digest"], "code": exc.code,
            })
            continue
        name = _output_name(store, doc_id)
        folded = unicodedata.normalize("NFC", name).casefold()
        if folded in folded_names and folded_names[folded] != name:
            raise PayloadRejected("publish.file", "publish_name_collision", "تصادم أسماء مخرجات النشر")
        folded_names[folded] = name
        files.setdefault(name, []).append(page)
        published += 1

    return {"report": {"published": published, "skipped": skipped,
            "rights_refused": rights_refused, "stores_missing": sorted(missing),
            "files": sorted(files)}, "pages": files, "refused": refused}


def recover_staging(out_dir: Path) -> list[str]:
    """ينظف أي مجلدات تجميع مؤقتة معلقة من انقطاع سابق دون مساس بالمخرجات المنشورة."""
    recovered = []
    if not out_dir.exists():
        return recovered
    for entry in out_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(".staging-"):
            shutil.rmtree(entry, ignore_errors=True)
            recovered.append(entry.name)
    return recovered


@contextmanager
def _publish_lock(out_dir: Path):
    out_dir.mkdir(exist_ok=True)
    lock_path = out_dir / ".publish.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def build(root: Path = ROOT) -> dict:
    out_dir = root / "publish"
    out_dir.mkdir(exist_ok=True)
    with _publish_lock(out_dir):
        recover_staging(out_dir)
        # Complete source/rights validation precedes deletion or any manifest append.
        candidate = plan(root)
        report, files = candidate["report"], candidate["pages"]
        manifest = Ledger(out_dir / "_manifest.jsonl", create=True)
        from core.seal import require_seal
        from core.signing import expects_signature, load_key, require_initial_signature
        require_initial_signature(manifest)
        require_seal(manifest, "بيان النشر")
        manifest.verify_chain()
        if expects_signature(manifest):
            load_key()  # Missing signing capability must precede removal of prior output.
        run_seq = 1 + sum(1 for e in manifest.entries()
                          if e["record"].get("kind") == "publish_run")

        # المرحلة الذرية: كتابة الملفات الجديدة في مجلد مؤقت معزول
        staging_dir = out_dir / f".staging-{uuid.uuid4().hex}"
        staging_dir.mkdir(parents=True, exist_ok=False)
        staged_files = []
        try:
            for name, pages in sorted(files.items()):
                staged_path = staging_dir / f"{name}.jsonl"
                with staged_path.open("w", encoding="utf-8") as f:
                    for page in pages:
                        f.write(json.dumps(
                            {"item": page["item"],
                             "item_digest": page["item_digest"]},
                            ensure_ascii=False, sort_keys=True) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                staged_files.append((name, staged_path, pages))

            # الاستبدال الذري: إزالة النواتج القديمة ونقل المخرجات الجديدة
            for old in out_dir.glob("*.jsonl"):
                if not old.name.startswith("_"):
                    old.unlink()
                    old.with_suffix(".jsonl.anchor").unlink(missing_ok=True)
            for name, staged_path, _ in staged_files:
                os.replace(staged_path, out_dir / f"{name}.jsonl")
            shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        for refusal in candidate["refused"]:
            sealed_append(manifest, {**refusal, "run_seq": run_seq}, "بيان النشر")
        for name, _, pages in staged_files:
            dest_name = f"{name}.jsonl"
            for page in pages:
                sealed_append(manifest, {
                    "kind": "published", "run_seq": run_seq, "file": dest_name,
                    "item_digest": page["item_digest"],
                    "source_id": page["item"]["source_id"],
                    "locus": page["item"]["locus"],
                }, "بيان النشر")
        sealed_append(manifest, {
            "kind": "publish_run", "run_seq": run_seq,
            **{key: report[key] for key in ("published", "skipped", "rights_refused", "stores_missing")},
        }, "بيان النشر")
        return {**report, "run_seq": run_seq}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="فحص الحقوق دون تغيير إسقاط النشر")
    parser.add_argument("--recover", action="store_true", help="تنظيف مجلدات النشر المؤقتة العالقة")
    args = parser.parse_args()
    if args.recover:
        recovered = recover_staging(ROOT / "publish")
        print(f"تنظيف النشر: تم تنظيف {len(recovered)} مجلد مؤقت.")
        return 0
    r = plan()["report"] if args.check else build()
    title = "فحص إسقاط النشر دون تطبيق" if args.check else "إسقاط النشر"
    print(f"{title}: مواد مسموحة {r['published']}، حُجب {r['skipped']}، "
          f"رُدّ حقوقًا {r['rights_refused']}"
          + (f" — ملفات: {', '.join(r['files'])}" if r["files"]
             else " — إسقاط فارغ صادق (لا حقوق توزيع بعد)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
