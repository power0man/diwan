#!/usr/bin/env python3
"""مُشغِّل دليل قبول م٤ — أول عقدة: الحوكمة البحرية (ق٢٠).

    python3 acceptance_m4.py

نصّ القبول المعتمد: عشرة استعلامات ذهبية تُجاب موادَّ بشواهد صحيحة
تُفتح يدويًّا؛ كل نداء نموذج مقيد بميزانية محجوزة ومسوّاة.

يتطلب Ollama محليًّا (qwen3:14b) — النداءات حية عبر الحلقة المحكومة،
والأدلة تُكتب في nodes/maritime/evidence/ لتُفتح يدويًّا.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "nodes" / "maritime"))

from core.budget import Budget
from core.corpus import CorpusCatalog
from core.ledger import Ledger
from core.registry import NodeRegistry
from nodes.maritime.node import MANIFEST, MaritimeNode
from providers.ollama import OllamaProvider

ROOT = Path(__file__).resolve().parent
CHECKS: list[tuple[str, bool, str]] = []

GOLDEN_QUESTIONS = (
    "ما المقصود بالسلطة البحرية في اللوائح؟",
    "ما شروط تسجيل السفن وقيدها؟",
    "ما أحكام إدارة مياه الصابورة في السفن؟",
    "ما الحد الأدنى للتطقيم الآمن للسفن؟",
    "ما إجراءات معاينة السفن الصغيرة غير الخاضعة للمعاهدات؟",
    "ما التزامات مزاولة أعمال النقل البحري؟",
    "ما تعريف المخالفة الجسيمة وما أمثلتها؟",
    "ما أحكام شهادة خطوط الشحن الدولية؟",
    "ما أحكام التحقيق في الحوادث البحرية؟",
    "ما اشتراطات أجهزة الإنقاذ على متن السفن؟",
)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def evidence_matches_answer(text: str, evidence: list[dict], catalog, root) -> bool:
    """فحص مستقل للحزمة: كل إحالة تقابل شاهدًا فريدًا يُفتح من المخزن."""
    refs = [p["ref"] for p in evidence]
    pattern = r"\[\s*ش\s*(\d+)\s*\]"
    markers = re.findall(r"\[\s*ش[^\[\]]*(?:\]|(?=\[|$))", text)
    if any(re.fullmatch(pattern, marker) is None for marker in markers):
        return False
    cited = {int(n) for n in re.findall(pattern, text)}
    return (bool(cited) and all(type(ref) is int and ref > 0 for ref in refs)
            and len(refs) == len(set(refs)) and cited == set(refs)
            and all(catalog.find_page(root, p["item_digest"]) is not None
                    for p in evidence))


def main() -> int:
    # ١ — تسجيل عقد العقدة في سجل العقد المختوم (عديم التكرار)
    registry = NodeRegistry(ROOT / "registry" / "nodes.jsonl")
    d = registry.register(MANIFEST)
    check("عقد العقدة مسجل مختومًا في سجل العقد",
          registry.get("maritime")["manifest_digest"] == d
          and registry.verify(strict=True), d[:16] + "…")

    # ٢ — عشرة استعلامات ذهبية عبر الحلقة المحكومة بنموذج حي
    budget = Budget(day_remaining_micros=1_000_000,
                    month_remaining_micros=10_000_000)
    ledger_path = ROOT / "var" / "m4-golden.jsonl"
    ledger_path.parent.mkdir(exist_ok=True)
    ledger_path.unlink(missing_ok=True)
    ledger_path.with_suffix(".jsonl.anchor").unlink(missing_ok=True)
    ledger = Ledger(ledger_path)
    node = MaritimeNode(ROOT, OllamaProvider("qwen3:14b"), budget, ledger)
    catalog = CorpusCatalog(ROOT / "corpus" / "maritime" / "_catalog.jsonl")

    results, failures = [], []
    for q in GOLDEN_QUESTIONS:
        try:
            r = node.answer(q)
            evidence_ok = evidence_matches_answer(
                r["answer"].text, r["evidence"], catalog, ROOT)
            results.append({
                "question": q,
                "answer": r["answer"].text,
                "evidence": [{"ref": p["ref"],
                              "digest": p["item_digest"],
                              "part": p["item"]["part"],
                              "locus": p["item"]["locus"]}
                             for p in r["evidence"]],
                "usage": {"in": r["outcome"].response.usage.input_tokens,
                          "out": r["outcome"].response.usage.output_tokens},
            })
            if not evidence_ok:
                failures.append(f"{q[:30]}: إحالة بلا شاهد مطابق أو شاهد لا يُفتح")
        except Exception as exc:
            failures.append(f"{q[:30]}: {type(exc).__name__}: {exc}")
    check("عشرة استعلامات ذهبية أُجيبت موادَّ مشتقة بشواهد تُفتح",
          len(results) == 10 and not failures,
          f"{len(results)}/10" + (f" — {failures[:2]}" if failures else ""))

    # ٣ — كل نداء نموذج مقيد بميزانية محجوزة ومسوّاة، والسلسلة سليمة
    entries = ledger.entries()
    ok_records = [e["record"] for e in entries
                  if e["record"].get("kind") == "ok"]
    tokens_recorded = all(
        r["response"]["usage"]["input_tokens"] > 0
        and r["response"]["usage"]["output_tokens"] > 0
        and r["settled_micros"] == r["response"]["cost_micros"]
        for r in ok_records)
    retries = len(ok_records) - 10
    check("كل نداء مقيد في السجل بحجزٍ مُسوًّى واستهلاكٍ من عدّادات المزوّد",
          len(ok_records) >= 10 and tokens_recorded
          and budget.outstanding_micros == 0 and ledger.verify_chain(),
          f"{len(ok_records)} قيد ok (منها {retries} تصويب استشهاد)، "
          f"معلّق الحجز {budget.outstanding_micros}")

    # ٤ — الأدلة تُكتب لتُفتح يدويًّا (شرط «تُفتح يدويًّا» في نص القبول)
    evidence_file = ROOT / "nodes" / "maritime" / "evidence" / "golden-answers.json"
    evidence_file.write_text(
        json.dumps({"generated_by": "acceptance_m4", "results": results},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    check("أدلة الأجوبة مكتوبة للفتح اليدوي",
          evidence_file.exists() and len(results) == 10,
          str(evidence_file.relative_to(ROOT)))

    print("\n— دليل قبول م٤ —\n")
    width = max(len(n) for n, _, _ in CHECKS)
    failed = sum(1 for _, ok, _ in CHECKS if not ok)
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name:<{width}}  {detail}")
    print(f"\n  {len(CHECKS) - failed}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
