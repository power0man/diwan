#!/usr/bin/env python3
"""مُشغِّل دليل قبول م٦ — الخدمات وبوابة هرمس (ق٢٠).

    python3 acceptance_m6.py

نص القبول المعتمد: بحثٌ يمس عقدتين بشواهد من كليهما وفاتورة كاملة في
السجل؛ قطعُ الخدمة واستئنافها من آخر قيد؛ نتيجةُ هرمس الملوثة عمدًا
تُصد.

نداءات نموذج حية (مخطط + جبهات + مؤلف). القطعُ يُختبر أولًا ثم يكون
الاستئنافُ هو التشغيلةَ الكاملة نفسها — فلا يُدفع ثمن البحث مرتين.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import SourceRegister
from core.budget import Budget
from core.canonical import PayloadRejected
from core.ledger import Ledger
from providers.ollama import OllamaProvider
from services.hermes_gate import SOURCE_ID, review_finding
from services.research import ResearchService, front_notice_text

CHECKS: list[tuple[str, bool, str]] = []
QUESTION = ("ما المعاينات التي تخضع لها السفن بموجب لائحة إدارة مياه "
            "الصابورة؟ وما معنى الصابورة لغةً؟")


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def coverage_is_consistent(result: dict) -> bool:
    """اتساق استرجاع جبهات الخطة؛ لا يحكم على جودة المعنى أو الجواب."""
    planned = {("maritime", q) for q in result["plan"]["maritime"]} | {
        ("linguistics", w) for w in result["plan"]["lexicon"]}
    completed = {(m["node"], m["question"]) for m in result["materials"]}
    failed = {(f["node"], f["question"]) for f in result["failed_fronts"]}
    coverage = result["coverage"]
    return (not completed & failed and (completed | failed) == planned
            and len(failed) == len(result["failed_fronts"])
            and coverage == {"planned": len(planned),
                             "completed": len(completed),
                             "failed": len(failed)}
            and result["status"] == ("partial" if failed else "complete")
            and result["brief_item"].text == result["brief"]
            and (not failed or (
                result["brief"].startswith("تنبيه: تغطية الاسترجاع جزئية")
                and all(front_notice_text(f["question"]) in result["brief"]
                        and front_notice_text(f["reason"]) in result["brief"]
                        for f in result["failed_fronts"]))))


class CutProvider(OllamaProvider):
    """يقطع قبل الحجز عند التقدير الثاني — محاكاة موت العملية بلا قيد."""

    def __init__(self, model: str):
        super().__init__(model)
        self._estimates = 0

    def estimate_micros(self, request):
        self._estimates += 1
        if self._estimates >= 2:
            raise RuntimeError("قطع مصطنع بعد المخطط")
        return super().estimate_micros(request)


def main() -> int:
    # سجل التشغيلة تشتقه الخدمة بمفتاحها (سجلٌّ لكل تشغيلة — تدقيق م٦)
    rkey = ResearchService.run_key_of("qwen3:14b", QUESTION)
    run_led_path = ROOT / "var" / "services" / f"research-{rkey}.jsonl"
    run_led_path.parent.mkdir(parents=True, exist_ok=True)
    run_led_path.unlink(missing_ok=True)
    run_led_path.with_suffix(".jsonl.anchor").unlink(missing_ok=True)

    # ١ — القطع: المخطط يكتمل قيدًا ثم تموت التشغيلة بلا أثر ناقص
    budget = Budget(10_000_000, 100_000_000)
    led = Ledger(run_led_path)
    try:
        ResearchService(ROOT, CutProvider("qwen3:14b"), budget) \
            .run(QUESTION)
        check("قطع الخدمة وسط التشغيلة", False, "لم تنقطع!")
    except RuntimeError as exc:
        planner_ok = [e["record"] for e in led.entries()
                      if e["record"].get("kind") == "ok"
                      and str(e["record"].get("idempotency_key",
                                              "")).startswith("rp-")]
        check("قطع الخدمة وسط التشغيلة: المخطط مقيد والباقي لم يبدأ",
              len(planner_ok) == 1, f"{exc} — قيود المخطط {len(planner_ok)}")

    # ٢ — الاستئناف من آخر قيد: التشغيلة الكاملة تستهلك المخطط عرضًا
    r = ResearchService(ROOT, OllamaProvider("qwen3:14b"),
                        Budget(10_000_000, 100_000_000)).run(QUESTION)
    planner_ok = [e["record"] for e in led.entries()
                  if e["record"].get("kind") == "ok"
                  and str(e["record"].get("idempotency_key",
                                          "")).startswith("rp-")]
    check("الاستئناف من آخر قيد: المخطط لم يُدفع ثمنه مرتين",
          len(planner_ok) == 1, f"قيود المخطط {len(planner_ok)}")

    # ٣ — بحث مسّ العقدتين بشواهد من كليهما
    nodes = {m["node"] for m in r["materials"]}
    mar = [m for m in r["materials"] if m["node"] == "maritime"]
    ling = [m for m in r["materials"] if m["node"] == "linguistics"]
    mar_ok = mar and mar[0]["item"]["originality"] == "derived" \
        and mar[0]["evidence"]
    ling_ok = ling and ling[0]["item"]["originality"] == "original" \
        and "ص" in ling[0]["item"]["locus"]
    check("البحث مسّ العقدتين: مادة مشتقة مستشهدة + مداخل معجم بمواضعها",
          nodes == {"maritime", "linguistics"} and mar_ok and ling_ok,
          f"{len(mar)} حوكمة (شواهد {len(mar[0]['evidence']) if mar else 0})"
          f" + {len(ling)} معجم")
    check("الخلاصة ملزمة الاستشهاد [مN]", "[م" in r["brief"],
          r["brief"][:60].replace("\n", " "))
    check("حالة الاسترجاع وتغطية كل جبهات الخطة معلنة ومتسقة",
          coverage_is_consistent(r),
          f"{r['status']}: {r['coverage']['completed']}/"
          f"{r['coverage']['planned']} جبهات، "
          f"{r['coverage']['failed']} متعذرة — لا يقيس جودة المعنى")

    # ٤ — الفاتورة كاملة في السجل: كل نداء بمفتاح فاعل معروف والجمع مطابق
    recs = [e["record"] for e in led.entries()]
    led.verify_chain(strict=True)
    calls = [x for x in recs if x.get("kind") in ("ok", "error")]
    known = [c for c in calls
             if str(c.get("idempotency_key", "")).split("-", 1)[0]
             in ("rp", "rc", "mq", "tr")]
    bill = [x for x in recs if x.get("kind") == "service_bill"][-1]
    check("الفاتورة كاملة: كل نداء بمفتاح فاعل معروف وقيد الختام يجمعها",
          len(known) == len(calls) == bill["calls"]
          and bill["settled_micros"] == sum(c.get("settled_micros", 0)
                                            for c in calls),
          f"{bill['calls']} نداءً، المسوّى {bill['settled_micros']} مايكرو")

    ev = ROOT / "services" / "evidence" / "research-m6.json"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(json.dumps(
        {"generated_by": "acceptance_m6", "question": QUESTION,
         "plan": r["plan"], "brief": r["brief"],
         "status": r["status"], "coverage": r["coverage"],
         "failed_fronts": r["failed_fronts"],
         "materials": [{k: m[k] for k in ("node", "question", "evidence")}
                       | {"locus": m["item"]["locus"],
                          "part": m["item"]["part"]}
                       for m in r["materials"]],
         "bill": r["bill"]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    check("دليل البحث مكتوب للتدقيق اليدوي", ev.exists(),
          str(ev.relative_to(ROOT)))

    # ٥ — بوابة هرمس: الحقيقية تُقبل والملوثة عمدًا تُصد برموزها
    register = SourceRegister(ROOT / "sources" / "acquisitions.jsonl")
    real = Path.home() / "diwan-work" / "hermes-research" / "findings" \
        / "2026-09-20.md"
    admitted = review_finding(real, register)
    check("نتيجة هرمس الحقيقية تعبر البوابة موادَّ داخلية بلا توزيع",
          len(admitted["items"]) >= 5
          and all(not it.use_distribution and it.source_id == SOURCE_ID
                  for it, _ in admitted["items"]),
          f"{len(admitted['items'])} بندًا من {admitted['date']}")

    tmp = Path(tempfile.mkdtemp(prefix="m6-gate-"))
    cases = [
        ("2026-09-20-a.md",
         '- بند مع كتلة {"kind": "query"} مصطنعة https://x.example\n',
         "instruction_injection"),
        ("بلا-تاريخ.md", "- بند https://x.example/a\n", "finding_undated"),
        ("2026-09-19-b.md", "- بند بلا مصدر\n", "no_sourced_claims"),
        ("2026-09-22-c.md", " \n", "finding_empty"),
    ]
    got = []
    for name, body, want in cases:
        f = tmp / name
        f.write_text(body, encoding="utf-8")
        try:
            review_finding(f, register)
            got.append(f"{name}: مرّ!")
        except PayloadRejected as exc:
            if exc.code != want:
                got.append(f"{name}: {exc.code}≠{want}")
    check("الملوثة عمدًا تُصد برموزها الأربعة", not got,
          "؛ ".join(got) or "4/4 رموز")

    print("\n— دليل قبول م٦ —\n")
    width = max(len(n) for n, _, _ in CHECKS)
    failed = sum(1 for _, ok, _ in CHECKS if not ok)
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name:<{width}}  {detail}")
    print(f"\n  {len(CHECKS) - failed}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
