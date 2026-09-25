#!/usr/bin/env python3
"""مجس كلفة الإلحاق المختوم — القياس الذي يقرر لقطة م٧ (م٦، ق٢٠).

    python3 tools/probe_ledger_cost.py

`sealed_append` يعيد قراءة السجل كله لكل قيد (فحص الختم) فالكلفة
خطية بالحجم والتراكم تربيعي. المجس يقيس فعليًّا في مجلد مؤقت، ويجرد
أحجام السجلات الحية اليوم، ويسقط الكلفة على أحجام مستقبلية — والناتج
`docs/probe/m6-append-cost.md` مادة قرار «اللقطة والذيل» في م٧.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ledger import Ledger
from core.seal import sealed_append

SIZES = (500, 2000, 8000)
SAMPLE = 30


def measure() -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory(prefix="probe-ledger-") as td:
        led = Ledger(Path(td) / "probe.jsonl")
        rec = {"kind": "probe", "payload": "س" * 120}
        count = 0
        for size in SIZES:
            while count < size:
                sealed_append(led, rec, "مجس")
                count += 1
            t0 = time.perf_counter()
            for _ in range(SAMPLE):
                sealed_append(led, rec, "مجس")
                count += 1
            per_ms = (time.perf_counter() - t0) * 1000 / SAMPLE
            rows.append({"size": size, "ms_per_append": round(per_ms, 2)})
    return rows


def live_sizes() -> list[tuple[str, int]]:
    out = []
    for rel in ("ledger/main.jsonl", "var/maritime-calls.jsonl",
                "var/services/research-m6.jsonl",
                "sources/acquisitions.jsonl", "registry/nodes.jsonl",
                "glossaries/maritime.jsonl", "rulings/precedence.jsonl",
                "corpus/maritime/_catalog.jsonl",
                "corpus/lexicons/_catalog.jsonl"):
        p = ROOT / rel
        if p.exists():
            out.append((rel, sum(1 for _ in p.open(encoding="utf-8"))))
    return out


def main() -> int:
    rows = measure()
    live = live_sizes()
    # إسقاط خطي من آخر نقطتين
    (s1, m1), (s2, m2) = [(r["size"], r["ms_per_append"])
                          for r in rows[-2:]]
    slope = (m2 - m1) / (s2 - s1)
    proj = {n: round(m2 + slope * (n - s2), 1) for n in (20_000, 100_000)}
    max_live_n = max(n for _, n in live)
    max_live_ms = round(max(0.0, m2 + slope * (max_live_n - s2)), 1)
    doc = ROOT / "docs" / "probe" / "m6-append-cost.md"
    lines = [
        "# مجس كلفة الإلحاق المختوم — مادة قرار لقطة م٧",
        "",
        "> يُعاد إنتاجه بـ`python3 tools/probe_ledger_cost.py`. "
        "٢٠ سبتمبر ٢٠٢٦.",
        "",
        "## القياس (مجلد مؤقت، قيد ~150 بايت، متوسط 30 إلحاقًا)",
        "",
        "| حجم السجل (قيد) | زمن الإلحاق المختوم (مللي ثانية) |",
        "|---|---|",
        *[f"| {r['size']:,} | {r['ms_per_append']} |" for r in rows],
        "",
        f"الميل ≈ {slope*1000:.2f} ميكروثانية/قيد إضافي — **خطي بالحجم "
        "كما يقتضي فحص الختم، والتراكم تربيعي**.",
        "",
        "### منهج القياس وحدوده — معلنة",
        "",
        "القيد المقيس ~150 بايت والكلفة الحقيقية بحجم **بايتات** الملف "
        "لا بعدد قيوده (فحص الختم يقرأ الملف كله) — وقيود السجلات الحية "
        "أكبر (قيد نداء نموذج ~1-3KB)، فالإسقاط بالعدد **حدٌّ أدنى** "
        "والألم الفعلي أبكر بمعامل حجم القيد. عينة 30 إلحاقًا لكل نقطة "
        "على قرص هذا الجهاز.",
        "",
        "## الإسقاط",
        "",
        "| حجم مستقبلي | زمن الإلحاق المتوقع |",
        "|---|---|",
        *[f"| {n:,} قيد | ~{v} مللي ثانية |" for n, v in proj.items()],
        "",
        "## السجلات الحية اليوم",
        "",
        "| السجل | القيود |",
        "|---|---|",
        *[f"| `{rel}` | {n:,} |" for rel, n in live],
        "",
        "## الخلاصة لقرار م٧",
        "",
        f"أكبر سجل حي اليوم {max_live_n:,} قيدًا — إلحاقُه "
        f"~{max_live_ms} مللي ثانية: محتمَل الآن لكنه ليس هامشيًّا. "
        f"موجّهٌ دوري كل دقيقة يضيف ~1,440 نبضًا يوميًّا فيبلغ سجلُّه "
        f"الرئيس 20 ألف قيد في أسبوعين (~{proj[20_000]} مللي ثانية "
        f"للقيد، والتراكم تربيعي) و100 ألف في شهور (~{proj[100_000]} "
        "مللي ثانية). **التوصية**: «لقطة + ذيل» في م٧ كما قررت "
        "المعمارية — لقطةٌ مبصومة تُعتمد ختمًا ويبدأ الذيل بعدها، "
        "وإعادة البناء من اللقطة والذيل تطابق الكامل (معيار قبول م٧ "
        "القائم). وإن نُشر الموجّه دوريًّا **قبل** م٧ فالحد الوقائي: "
        "تدوير سجلّه الرئيس عند 10 آلاف قيد بقرارٍ مقيد لا صمتًا.",
    ]
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"rows": rows, "projection": proj},
                     ensure_ascii=False))
    print(f"كُتب: {doc.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
