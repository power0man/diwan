#!/usr/bin/env python3
"""قيد استحواذ عقدة الفلسفة والمنطق في سجل الأصول (م١٣-ب، ق٤٠، ق٤١).

    .venv/bin/python tools/seed_philosophy_source.py

مصدرٌ داخلي للاستدلال الصوري والبرهان المنطقي المحكوم.
عديم التكرار: قيد نافذ مطابق لا يُعاد.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.acquisitions import Acquisition, SourceRegister

ACQ = Acquisition(
    source_id="nodes.philosophy",
    title="محرك الاستدلال والمنطق الصوري لديوان",
    origin="nodes/philosophy/node.py",
    verified_date="2026-09-21",
    use_internal=True,
    use_distribution=False,
    license_evidence=(
        "استدلال صوري حتمي وبرهان منطقي مبني محلياً في النواة (م١٣-ب، ق٤٠)"
    ),
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
