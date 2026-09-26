#!/usr/bin/env python3
"""يشغّل بنكَ الذاكرة المحكومة (ك٤٨) على الطريق الموصول بمحرّكٍ حيّ في Ollama المحلي (جديد-memory-probe).

    python3 tools/evaluate_memory.py --model qwen3.5:9b --agent <معرّفك> --out docs/probe/memory-live-<التاريخ>.json

- كلُّ جولةٍ تذهب إلى النموذج الحقيقيّ عبر `webui.server.LocalApp`، والاقتراحُ وحده مكتوبٌ سلفًا
  (البنكُ يقيس الموافقةَ عليه، لا أن النموذج يقترح).
- المقاييسُ ما بلغ النموذجَ وما بقي على القرص، كما في `docs/MEMORY-DESIGN.md` §٦.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import PayloadRejected  # noqa: E402
from evaluation.memory_bank import validate_memory_bank  # noqa: E402
from evaluation.memory_runner import run_memory_bank  # noqa: E402
from providers.ollama import OllamaProvider  # noqa: E402

REGISTRY = ROOT / "registry" / "agents.json"
DEFAULT_SUITE = ROOT / "evaluation" / "suites" / "memory_v1.json"
# البنوكُ المكلَّف بها بأعدادها كما في تكليفها؛ فتسليمٌ مبتورٌ لا يُقاس «١/١» (ملاحظة Codex على #129)
COMMISSIONED = {
    "memory_kimi_v1": {"total": 40, "forget": 10, "isolation": 8, "consent": 8, "backup": 6, "injection": 8},
}


def commissioned_shortfall(bank: dict) -> str | None:
    wanted = COMMISSIONED.get(bank["suite_id"])
    if wanted is None:
        return "suite_not_commissioned"
    scenarios = bank["scenarios"]
    if len(scenarios) < wanted["total"]:
        return "suite_below_commissioned_total"
    for category, minimum in wanted.items():
        if category != "total" and sum(s["category"] == category for s in scenarios) < minimum:
            return "suite_below_commissioned_category"
    return None

LIMITS = [
    "measures_what_reaches_the_model_and_what_stays_on_disk_not_what_the_model_does_with_a_memory",
    "proposals_are_scripted_the_bank_measures_consent_not_that_the_model_proposes",
    "retrieval_is_lexical_so_a_paraphrased_question_can_miss_a_stored_item",
    "the_context_checked_is_the_first_request_of_each_turn_later_tool_steps_are_not_inspected",
    "single_run_on_one_local_model_no_variance_estimate",
]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="اسمُ النموذج كما يعرفه Ollama")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--agent", required=True, help="معرّفُ من يشغّل القياس، مسجَّلًا في registry/agents.json")
    parser.add_argument("--out", required=True, type=Path, help="مسارُ التقرير؛ لا يُستبدل ملفٌّ قائم")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    if args.agent not in json.loads(REGISTRY.read_text(encoding="utf-8"))["agents"]:
        parser.error(f"agent_unregistered: {args.agent}")
    # بنكٌ لم يُفحص قد يمرّ ١٠٠٪ بخطواتٍ يتجاهلها المُشغِّل؛ فالمدقّقُ قبل أي نداء (ملاحظة Codex على #129)
    raw = args.suite.read_bytes()
    try:
        bank = validate_memory_bank(json.loads(raw.decode("utf-8")))
    except (PayloadRejected, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "code": getattr(exc, "code", "bank_invalid")}, ensure_ascii=False))
        return 2
    # غيرُ البنك المودَع بنكٌ مكلَّفٌ به، بأعداده كاملةً
    shortfall = None if args.suite.resolve() == DEFAULT_SUITE.resolve() else commissioned_shortfall(bank)
    if shortfall:
        print(json.dumps({"status": "refused", "code": shortfall}, ensure_ascii=False))
        return 2
    provider = OllamaProvider(model=args.model)
    report = run_memory_bank(bank, driver="live", delegate=provider)
    report.update(suite_sha256=hashlib.sha256(raw).hexdigest(), date=datetime.date.today().isoformat(), agent=args.agent,
                  engine={"provider": "ollama", "model": args.model}, measurement_limits=LIMITS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": report["metrics"], "meets_thresholds": report["meets_thresholds"],
                      "passed": report["passed"], "total": report["total"], "out": str(args.out)},
                     ensure_ascii=False))
    return 0 if report["meets_thresholds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
