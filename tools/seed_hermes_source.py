#!/usr/bin/env python3
"""قيد استحواذ نتائج هرمس في سجل الأصول (م٦، ق١٧).

    python3 tools/seed_hermes_source.py

مصدرٌ داخلي-فقط: ملخصات آلية لمصادر عامة، والتوزيع محجوب حتى مراجعة
حقوق كل مصدرٍ ملخَّص على حدة. عاطل التكرار: قيد نافذ مطابق لا يُعاد.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.acquisitions import Acquisition, SourceRegister

ROOT = Path(__file__).resolve().parent.parent

ACQ = Acquisition(
    source_id="hermes-research-local",
    title="نتائج بحث هرمس الذاتي (un-law-scout)",
    origin="~/diwan-work/hermes-research/findings/ — خارج المستودع (ق١٧)",
    verified_date="2026-09-20",
    use_internal=True,
    use_distribution=False,
    license_evidence=(
        "ملخصات آلية لمصادر عامة (سلسلة C.N. لمعاهدات الأمم المتحدة، "
        "قرارات IMO/MEPC) تعبر بوابة services/hermes_gate.py حصرًا؛ "
        "التوزيع محجوب حتى مراجعة حقوق كل مصدر ملخَّص — الميثاق "
        "docs/HERMES-RESEARCH.md وق١٧"),
)


def main() -> int:
    register = SourceRegister(ROOT / "sources" / "acquisitions.jsonl", create=True)
    cur = register.get(ACQ.source_id)
    if cur and cur["acquisition"] == ACQ.fingerprint_payload():
        print(f"قائم: {ACQ.source_id}")
        return 0
    register.acquire(ACQ)
    print(f"قُيّد: {ACQ.source_id} (داخلي فقط، بلا توزيع)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
