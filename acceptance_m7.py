#!/usr/bin/env python3
"""قبول م٧: خطة نشر قرائية للمصادر، وكتابة/لقطة/توقيع على بيانات مصطنعة.

لا يبني إسقاط المستودع ولا يغيّر بيانه. حارس تحميل المفاتيح يبدأ قبل
أول قراءة، ومفتاح HMAC الثابت خاص بالبيانات المؤقتة فقط. تحقق تواقيع
الإنتاج العام الصارم فحص مستقل في tools/run_verification.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import Acquisition, SourceRegister
from core.public_export import excluded_streams
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger, LedgerCorrupt
from core.seal import sealed_append
from core.signing import SigningRefused, require_signed_seal, sign_anchor, verify_anchor_signature
from core.snapshot import full_entries, rotate, verify_full
from publish_projection import build, iter_current_items, plan
import core.signing as signing

CHECKS: list[tuple[str, bool, str]] = []
TEST_KEY = b"diwan-m7-public-synthetic-fixture-key-not-for-production"


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def _no_private_key(*args, **kwargs):
    raise SigningRefused("signing_key_missing", "قبول م٧ لا يحمّل مفاتيح الإنتاج")


@contextmanager
def keyless_acceptance():
    """Defense against accidental trusted-test lookup, not a host sandbox.

    Real isolation is supplied by the caller's disposable container. Keep every
    production key loader disabled, including when a synthetic key is in use.
    """
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ))
        for name in ("DIWAN_ANCHOR_KEY", "DIWAN_REQUIRE_SIGNATURE",
                     "DIWAN_ED25519_PRIVATE_KEY", "DIWAN_ED25519_PRIVATE_KEY_PATH"):
            os.environ.pop(name, None)
        stack.enter_context(patch.object(signing, "load_key", _no_private_key))
        stack.enter_context(patch.object(signing, "_keychain_key", _no_private_key))
        if hasattr(signing, "load_ed25519_private_key"):
            stack.enter_context(patch.object(signing, "load_ed25519_private_key", _no_private_key))
        yield


def check_sources(root: Path) -> None:
    """Read-only even when the current projection's manifest is signed."""
    candidate = plan(root)
    report = candidate["report"]
    planned = [p["item"]["text"] for pages in candidate["pages"].values() for p in pages]
    existing = []
    for path in sorted((root / "publish").glob("*.jsonl")):
        if not path.name.startswith("_"):
            with path.open(encoding="utf-8") as f:
                existing.extend(json.loads(line)["item"]["text"]
                                for line in f if line.strip())
    blob = "\n\x00\n".join(planned + existing)
    internal_total = leaked = 0
    for _store, _doc, page in iter_current_items(root):
        item = page["item"]
        if not item.get("use_distribution"):
            internal_total += 1
            is_leak = (item["text"] in blob) if len(item["text"]) > 20 else (f"\n\x00\n{item['text']}\n\x00\n" in f"\n\x00\n{blob}\n\x00\n")
            leaked += bool(item["text"] and is_leak)
    check("المصادر والخطة والنشر القائم: المسح السلبي الشامل بلا كتابة",
          leaked == 0 and internal_total == report["skipped"] and internal_total > 0,
          f"{internal_total:,} مادة محجوبة؛ تسرب {leaked}؛ "
          f"مخازن غائبة معلنة: {','.join(report['stores_missing']) or 'لا شيء'}")
    # A clone may omit local-only sources used by an older publication run.
    # Comparing that run's counts with this clone's plan would be misleading.
    manifest_path = root / "publish" / "_manifest.jsonl"
    if manifest_path.exists():
        manifest = Ledger(manifest_path, create=False)
        from core.seal import require_seal
        state = require_seal(manifest, "بيان النشر القائم")
        check("سلسلة بيان النشر القائم وختمه (التوقيع العام فحص مستقل)",
              manifest.verify_chain(strict=True), state)


def synthetic_fixture(root: Path) -> None:
    reg = SourceRegister(root / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("open-src", "منشور", "https://example.invalid/open",
                            "2026-09-20", True, True, "ترخيص نشر مصطنع"))
    reg.acquire(Acquisition("closed-src", "داخلي", "https://example.invalid/closed",
                            "2026-09-20", True, False, "داخلي فقط"))
    directory = root / "corpus" / "maritime"
    directory.mkdir(parents=True)
    catalog = CorpusCatalog(directory / "_catalog.jsonl")
    for number, text, source, distribution in (
            (1, "نص قابل للنشر في الوثيقة التجريبية.", "open-src", True),
            (2, "نص داخلي محض لا يجوز أن يظهر في النشر.", "closed-src", False),
            (3, "نص وسمه مزور فوق مصدر بلا إذن توزيع.", "closed-src", True)):
        doc = f"{number:03d}__وثيقة"
        corpus = CorpusFile(directory / f"{doc}.jsonl")
        item = KnowledgeItem(text=text, lang="ar", domain="maritime", use_internal=True,
                             use_distribution=distribution, source_id=source, locus="ص1",
                             originality="original", part="وثيقة")
        head = (corpus._locked_ingest(doc, [item]) if number == 3
                else corpus.ingest(doc, [item], reg))
        corpus.anchor()
        catalog.record(doc, f"corpus/maritime/{doc}.jsonl", 1, head)
        catalog.anchor()


def _check_synthetic(root: Path) -> None:
    synthetic_fixture(root)
    manifest = Ledger(root / "publish" / "_manifest.jsonl")
    manifest.anchor()
    # Explicit legacy test key, never a production loader. This also exercises
    # the signed-manifest path that formerly broke keyless acceptance.
    sign_anchor(manifest, key=TEST_KEY)
    with patch.object(signing, "load_key", return_value=TEST_KEY):
        result = build(root)
        assert verify_anchor_signature(manifest, key=TEST_KEY)
    blob = "\n".join(p.read_text(encoding="utf-8") for p in (root / "publish").glob("*.jsonl"))
    check("نشر مصطنع ببيان موقّع: المأذون فقط، ورفض الوسم المزور",
          result["published"] == 1 and result["rights_refused"] == 1
          and result["skipped"] == 1 and "قابل للنشر" in blob
          and "داخلي محض" not in blob and "وسمه مزور" not in blob)
    runs = [e["record"] for e in manifest.entries() if e["record"]["kind"] == "publish_run"]
    check("بيان النشر المصطنع يطابق الحصيلة",
          manifest.verify_chain(strict=True) and runs[-1]["published"] == result["published"]
          and runs[-1]["skipped"] == result["skipped"])

    work = root / "snapshot" / "main.jsonl"
    ledger = Ledger(work)
    for i in range(7):
        sealed_append(ledger, {"kind": "router_run", "read": i}, "سجل مصطنع")
    before = ledger.entries()
    snap = rotate(work)
    sealed_append(Ledger(work), {"kind": "router_run", "read": 8}, "ذيل مصطنع")
    rebuilt = full_entries(work)
    check("لقطة مصطنعة وذيل: إعادة البناء تطابق القيود والبصمات",
          verify_full(work) and rebuilt[:len(before)] == before
          and len(rebuilt) == len(before) + 1)
    data = snap.read_bytes()
    snap.write_bytes(data[:50] + b"X" + data[51:])
    try:
        full_entries(work)
        check("العبث باللقطة مكشوف", False)
    except LedgerCorrupt:
        check("العبث باللقطة مكشوف", True)
    try:
        rotate(root / "streams" / "example" / "outbox.jsonl")
        check("التيارات لا تدوّر", False)
    except LedgerCorrupt as exc:
        check("التيارات لا تدوّر", "لا تُدوَّر" in str(exc))

    signed = Ledger(root / "signature" / "synthetic.jsonl")
    sealed_append(signed, {"kind": "synthetic"}, "سجل توقيع مصطنع")
    try:
        require_signed_seal(signed, "مصطنع")
        check("غياب التوقيع رفض مسمى", False)
    except SigningRefused as exc:
        check("غياب التوقيع رفض مسمى", exc.code == "signature_missing")
    sign_anchor(signed, key=TEST_KEY)
    check("توقيع HMAC المصطنع يتحقق بالمفتاح الصريح",
          verify_anchor_signature(signed, key=TEST_KEY))
    signed.anchor_path.write_bytes(signed.anchor_path.read_bytes() + b" ")
    try:
        verify_anchor_signature(signed, key=TEST_KEY)
        check("تعديل المرساة يفشل التحقق", False)
    except SigningRefused as exc:
        check("تعديل المرساة يفشل التحقق", exc.code == "signature_mismatch")
    try:
        verify_anchor_signature(signed)
        check("غياب مفتاح HMAC رفض مسمى", False)
    except SigningRefused as exc:
        check("غياب مفتاح HMAC رفض مسمى", exc.code == "signing_key_missing")


def check_synthetic(root: Path) -> None:
    # Isolate the fixture's legacy policy from the repository's Ed25519 pin.
    # ROOT is deliberately a separate synthetic namespace: these fixture files
    # are not implicitly added to the production governing-ledger registry.
    with patch.object(signing, "ROOT", root / "policy-root"), \
            patch.object(signing, "PINNED_POLICY_SHA256", None, create=True):
        _check_synthetic(root)


def main() -> int:
    register = ROOT / "sources" / "acquisitions.jsonl"
    if not register.exists() and "sources/acquisitions.jsonl" in excluded_streams(ROOT):
        # اللقطةُ العامة بلا سجلّ مصادر: «تعذّر بحدٍّ معلن» (خروج 3) لا فشل ولا نجاح
        print(json.dumps({"status": "unavailable", "code": "sources_missing",
                          "reason": "سجلُّ المصادر من ملفات المالك الخاصة خارج اللقطة العامة (ك٢٧)"},
                         ensure_ascii=False))
        return 3
    CHECKS.clear()
    with keyless_acceptance():
        check_sources(ROOT)
        with tempfile.TemporaryDirectory(prefix="diwan-m7-") as directory:
            check_synthetic(Path(directory))
    print("\n— دليل قبول م٧ —\n")
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name}  {detail}")
    failed = sum(not ok for _, ok, _ in CHECKS)
    print(f"\n  {len(CHECKS) - failed}/{len(CHECKS)} اجتازت.\n")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
