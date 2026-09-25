#!/usr/bin/env python3
"""مُشغِّل دليل قبول م١ — عقود المعرفة والحقوق عند الباب (ق٢٠).

    python3 acceptance_m1.py

نصّ القبول المعتمد: مادة بلا حقوق/إسناد/نسخة تُرفض؛ نفس المادة ← نفس
البصمة **عبر تشغيلين** (عمليتين منفصلتين فعلًا)؛ سلسلة سجل الأصول سليمة.
لا شبكة ولا مفاتيح ولا إنفاق.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.acquisitions import Acquisition, SourceRegister
from core.canonical import PayloadRejected, digest
from core.knowledge import (
    KnowledgeEvent, KnowledgeItem, NodeManifest,
    validated_event, validated_item, validated_manifest,
)
from core.ledger import Ledger, LedgerCorrupt
from core.registry import NodeRegistry

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def sample_item(**over) -> KnowledgeItem:
    base = dict(
        text="تنطبق هذه اللائحة على السفن التجارية.",
        lang="ar", domain="maritime",
        use_internal=True, use_distribution=False,
        source_id="claude-project-regulations-export",
        locus="المادة 1", originality="original",
    )
    base.update(over)
    return KnowledgeItem(**base)


_SUBPROCESS_DIGEST = """
import sys; sys.path.insert(0, {root!r})
from core.canonical import digest
from core.knowledge import KnowledgeItem, validated_item
i = KnowledgeItem(text="تنطبق هذه اللائحة على السفن التجارية.", lang="ar",
                  domain="maritime", use_internal=True, use_distribution=False,
                  source_id="claude-project-regulations-export",
                  locus="المادة 1", originality="original")
print(digest(validated_item(i).fingerprint_payload()))
"""


def rejected_with(fn, arg, code: str) -> bool:
    try:
        fn(arg)
    except PayloadRejected as e:
        return e.code == code
    return False


def main() -> int:
    root = str(Path(__file__).resolve().parent)

    # ١ — مادة سليمة تجتاز، وبصمتها ثابتة داخل التشغيل
    d_here = digest(validated_item(sample_item()).fingerprint_payload())
    check("مادة بحقوقها وإسنادها تجتاز وتُبصَم",
          d_here == digest(sample_item().fingerprint_payload()), d_here[:16] + "…")

    # ٢ — نفس المادة عبر **تشغيلين**: عملية منفصلة تعيد البصمة نفسها
    out = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_DIGEST.format(root=root)],
        capture_output=True, text=True, timeout=60)
    d_there = out.stdout.strip()
    check("نفس المادة تعطي نفس البصمة عبر تشغيلين منفصلين",
          out.returncode == 0 and d_there == d_here,
          d_there[:16] + "…" if d_there else out.stderr.strip()[:80])

    # ٣ — الغائب يُرفض برمزه: حقوق، إسناد، أصالة، مسرد المعرَّب
    check("مادة بلا أي إذن استخدام تُرفض",
          rejected_with(validated_item,
                        sample_item(use_internal=False, use_distribution=False),
                        "rights_none"), "rights_none")
    check("مادة بلا موضع إسناد تُرفض",
          rejected_with(validated_item, sample_item(locus=""), "locus_missing"),
          "locus_missing")
    check("مادة معرَّبة بلا مسرد محكوم تُرفض",
          rejected_with(validated_item, sample_item(originality="translated"),
                        "glossary_required"), "glossary_required")

    # ٤ — الرسالة: النوع المجهول يُرفض، والنصّ الحرّ يُرفض، والمال بلا سقف يُرفض
    ev = dict(kind="item_published", from_node="maritime", to="broadcast",
              correlation_id="c-1", data_policy="internal",
              payload={"item_digest": "a" * 64}, idempotency_key=None)
    check("رسالة بنوع مجهول تُرفض",
          rejected_with(validated_event,
                        KnowledgeEvent(**{**ev, "kind": "gossip"}), "kind_unknown"),
          "kind_unknown")
    check("حمولة نصّ حرّ تُرفض — لا مادة ولا إشارة",
          rejected_with(validated_event,
                        KnowledgeEvent(**{**ev, "payload": {"free_text": "قول مرسل"}}),
                        "payload_keys_unknown"), "payload_keys_unknown")
    check("رسالة query بلا سقف إنفاق تُرفض",
          rejected_with(validated_event,
                        KnowledgeEvent(**{**ev, "kind": "query",
                                          "payload": {"question": "ما حكم كذا؟",
                                                      "domain": "maritime"},
                                          "idempotency_key": "q-1"}),
                        "budget_cap_required"), "budget_cap_required")

    # ٥ — سجل العقد: تسجيلٌ مبصوم، والمطابق لا يُقيَّد، والنكوص يُرفض
    tmp = Path(tempfile.mkdtemp())
    try:
        reg = NodeRegistry(tmp / "nodes.jsonl")
        m = NodeManifest("maritime", 1, ("maritime",), ("query",), "regulated")
        d1 = reg.register(validated_manifest(m))
        reg.register(m)
        reg.register(NodeManifest("maritime", 2, ("maritime",), ("query",), "regulated"))
        downgrade = rejected_with(reg.register, m2 := NodeManifest(
            "maritime", 1, ("maritime", "law"), ("query",), "internal"),
            "contract_version_reused")
        check("سجل العقد: مبصوم، لا تكرار، ونفس النسخة بمحتوى مختلف تُرفض",
              reg.history("maritime")[0]["manifest_digest"] == d1
              and downgrade and reg.verify(), d1[:16] + "…")
    except LedgerCorrupt as exc:
        check("سجل العقد: مبصوم، لا تكرار، ونفس النسخة بمحتوى مختلف تُرفض",
              False, f"LedgerCorrupt: {exc}")

    # ٦ — التزوير مكشوف: إلحاقٌ ملتفّ على acquire يُسقط الفحص العميق
    forged_path = tmp / "forged.jsonl"
    fr = SourceRegister(forged_path)
    fr.acquire(Acquisition("s-legit", "مصدر", "https://x", "2026-09-20",
                           True, False, "دليل"))
    Ledger(forged_path).append({"kind": "acquired", "source_id": "s-forged",
                                "acquisition": {"use_distribution": True},
                                "acquisition_digest": "deadbeef" * 8})
    try:
        fr.verify()
        check("تزوير قيد استحواذ يُكشف بالفحص العميق", False, "مرّ بلا كشف")
    except LedgerCorrupt as exc:
        check("تزوير قيد استحواذ يُكشف بالفحص العميق", True, str(exc)[:60])

    # ٧ — سجل الأصول الفعلي: القيود قائمة وسليمة عميقًا بمرساتها
    src = SourceRegister(Path(root) / "sources" / "acquisitions.jsonl")
    try:
        cur = src.current()
        check("سجل الأصول: المصادر مقيَّدة وسليمة عميقًا بمرساتها",
              len(cur) >= 4 and src.verify(strict=True),
              f"{len(cur)} مصادر نافذة")
    except LedgerCorrupt as exc:
        check("سجل الأصول: المصادر مقيَّدة وسليمة عميقًا بمرساتها",
              False, f"LedgerCorrupt: {exc}")

    # ٨ — بوابة الاستيعاب: المادة لا ترث أكثر من مصدرها
    check("مادة «توزيع» فوق مصدرٍ «داخلي فقط» تُرَدّ من البوابة",
          rejected_with(src.admitted_item,
                        sample_item(source_id="goldenshamela-201907",
                                    use_distribution=True),
                        "distribution_exceeds_source"),
          "distribution_exceeds_source")
    check("مادة بمصدرٍ لا قيد له تُرَدّ من البوابة",
          rejected_with(src.admitted_item, sample_item(source_id="مجهول"),
                        "source_unacquired"), "source_unacquired")
    check("مادة ضمن حقوق مصدرها تجتاز البوابة",
          src.admitted_item(sample_item()) is not None, "")

    # — الطباعة —
    print("\n— دليل قبول م١ —\n")
    width = max(len(n) for n, _, _ in CHECKS)
    failed = 0
    for name, ok, detail in CHECKS:
        mark = "✓" if ok else "✗"
        if not ok:
            failed += 1
        print(f"  {mark}  {name:<{width}}  {detail}")
    print(f"\n  {len(CHECKS) - failed}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
