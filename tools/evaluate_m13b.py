#!/usr/bin/env python3
"""يشغّل م١٣-ب (ك٤٩، سياسات): العقدةُ مقابل المركز بالاسترجاع نفسِه، على الماك حيث المتنُ وبنكُ م١٤.

    python3 tools/evaluate_m13b.py --model qwen3.5:9b --out docs/probe/k49-m13b-<التاريخ>.json

- **ما يُنشر (`--out`):** الأعدادُ والمجالان والقرار وحدها.
- **ما يبقى محليًّا (`--private-out`، افتراضُه تحت var/):** الأجوبة، لأنها قد تقتبس لوائحَ المالك، وبعضُها
  مسوّداتٌ غير منشورة.
- **الحدّ:** بلا بنك م١٤ أو فهرس المتن يُرفض بالاسم. والتقريرُ لا يُكتب فوق ملفٍّ قائم.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.m13b import judge, protocol, run  # noqa: E402


def public_summary(rows: dict[str, list[dict]], verdict: dict, config: dict) -> dict:
    """ما يُنشر: لا جوابَ ولا نصَّ من المتن، بل أعدادٌ وقرار."""
    counts = {name: {"cases": len(items), "errors": sum(r["status"] != "measured" for r in items),
                     "abstained": sum(r.get("abstained", False) for r in items),
                     "passed": sum(r.get("passed", False) for r in items),
                     "attributed": sum(r.get("attributed", False) for r in items)} for name, items in rows.items()}
    return {"schema_version": 1, "kind": "m13b_report", "config": config, "counts": counts,
            "judgment": {k: verdict[k] for k in ("decision", "reason", "answers", "attribution", "min_items",
                                                 "meaning", "protocol_sha256")},
            "measurement_limits": protocol()["limits"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--suite", type=Path, default=ROOT / "evaluation/suites/benchmark_m14.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path, required=True, help="الملخّصُ المنشور: أعدادٌ وقرار")
    parser.add_argument("--private-out", type=Path, help="الصفوفُ بأجوبتها، محليًّا (افتراضُه var/m13b/)")
    args = parser.parse_args(argv)
    private = args.private_out or ROOT / "var" / "m13b" / f"m13b-{time.strftime('%Y%m%d-%H%M%S')}.json"
    for path in (args.out, private):
        if path.exists():
            parser.error(f"التقريرُ قائم: {path}")
    if not args.suite.exists():
        print(json.dumps({"status": "refused", "code": "m14_bank_missing"}, ensure_ascii=False))
        return 2
    from core.budget import Budget
    from core.ledger import Ledger
    from evaluation.benchmark_arms import CenterRetrievalArm, DiwanFullArm
    from providers.ollama import OllamaProvider
    data = protocol()
    cases = [c for c in json.loads(args.suite.read_text(encoding="utf-8"))["cases"]
             if c.get("domain", "maritime") == data["bank"]["domain"]][: args.limit]
    provider = OllamaProvider(args.model)
    private.parent.mkdir(parents=True, exist_ok=True)
    budget, ledger = Budget(100_000_000, 1_000_000_000), Ledger(private.with_suffix(".ledger.jsonl"))
    arms = {"node": DiwanFullArm(ROOT, provider, budget, ledger),
            "center": CenterRetrievalArm(provider, budget, ledger)}
    rows = run(cases, arms, pass_floor=data["pass_floor"])
    verdict = judge(rows["node"], rows["center"])
    config = {"model": args.model, "cases": len(cases), "limit": args.limit, "protocol_id": data["protocol_id"]}
    private.write_text(json.dumps({"config": config, "rows": rows, "judgment": verdict}, ensure_ascii=False,
                                  indent=2) + "\n", encoding="utf-8")
    report = public_summary(rows, verdict, config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": verdict["decision"], "reason": verdict["reason"], "out": str(args.out),
                      "private_out": str(private)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
