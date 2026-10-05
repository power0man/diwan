#!/usr/bin/env python3
"""الرقمُ العام لديوان (`جديد-general-number`، ق٧٣ الخطوة 1.7أ): كاملُ الشطر المفتوح عبر ذراع الأساس الوكيل، ببذورٍ وWilson.

    python3 tools/evaluate_general.py --model qwen3.5:9b --model-version <البصمة> \\
        --bank-open evaluation/banks/kimi_v1/open --out docs/probe/k2c-general-v12-<التاريخ>.json

لماذا لا يكفي `tools/measure_engine.py`: لا بذورَ فيه، ويستدعي `evaluate_suite` لا الطريقَ الوكيل الذي يلقاه المستخدم
منذ ج١، ولا يُخرج فاصلَ ثقة. وهذا المُشغِّل يركّب ما هو قائم ولا يعيد كتابته:
- **الحالات:** كلُّ حالةٍ ذاتِ فحصٍ آليّ في الشطر المفتوح (`tools/evaluate_ablation.py::bank_cases`، بلا عيّنة).
- **الطريق:** ذراعُ الأساس من بروتوكول الاستئصال المسجَّل (`evaluation/ablation.py::arm()`): الأدواتُ الافتراضية، والحَجر،
  وبلا تفكير؛ ويُشغَّل ببذورٍ متتابعة بأغلبيةٍ صارمة (`run_seeded_arm`).
- **الرقم:** نسبةُ النجاح على المقيس، وWilson 95٪ لكل طبقة وللمجموع (`evaluation/retrieval_general.py::wilson`)،
  والأعطالُ تُعدّ خارج المقام (`error_count`، ك٢١) ولا تصير رسوبًا.
- **الإعلان:** الأداةُ والأمر، والبذور، وبصماتُ ملفّات البنك، وبصمةُ النموذج قبل التشغيل وبعده.

الحدود: فحصُ `exact` صارمٌ بق٥٧ فلا قراءةَ مشذَّبة؛ والبذورُ بحرارة صفر لا تقيس تباينَ العيّنة (`GREEDY_SEED_LIMIT`)؛
والتقريرُ لا يُكتب فوق ملفٍّ قائم.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.ablation import (GREEDY_SEED_LIMIT, RUNNER_VERSION, AblationError, arm, protocol,  # noqa: E402
                                 run_seeded_arm, seed_values)
from evaluation.retrieval_general import wilson  # noqa: E402
from tools.evaluate_ablation import bank_cases  # noqa: E402
from tools.sample_bank import load_capability_suites  # noqa: E402

TOOL = "tools/evaluate_general.py"
GENERAL_VERSION = 1
LIMITS = [
    "the_number_is_the_open_split_through_the_registered_baseline_arm_not_the_sealed_split",
    "cases_without_an_automatic_check_are_excluded_and_counted_in_config",
    "exact_checks_are_strict_by_q57_so_no_trimmed_reading_is_reported",
    "errored_cases_are_outside_the_denominator_and_counted_in_error_count_not_failures",
    "wilson_intervals_treat_cases_as_independent_and_ignore_clustering_within_a_suite",
    GREEDY_SEED_LIMIT,
]


def _tiers(bank_open: Path) -> dict[str, str]:
    """طبقةُ كل حالة من مجلّد ملفّها (tier_a…tier_c)."""
    tiers = {}
    for path, suite in load_capability_suites(bank_open):
        for case in suite.get("cases", []):
            tiers[case["case_id"]] = path.parent.name
    return tiers


def summarize(rows: list[dict]) -> dict:
    """نسبةٌ على المقيس، وWilson، والأعطالُ خارج المقام."""
    measured = [row for row in rows if row["status"] == "measured"]
    passes = sum(row["passed"] is True for row in measured)
    return {"offered": len(rows), "measured": len(measured), "passes": passes,
            "error_count": len(rows) - len(measured),
            "pass_rate": round(passes / len(measured), 4) if measured else None,
            "wilson95": wilson(passes, len(measured))}


def run_general(provider, *, model: str, model_version: str, bank_open: Path, sandbox: bool = True,
                seeds: tuple[int, ...] | None = None, command: str = "", **options) -> dict:
    seeds = seed_values() if seeds is None else tuple(seeds)
    if seeds != seed_values(len(seeds)):
        raise AblationError("seeds_invalid", "يلزم تسلسل 0..N-1 بعدد فردي لا يقل عن 3")
    cases, files = bank_cases(bank_open, sample_target=None, salt="general", sandbox=sandbox)
    if not cases:
        raise AblationError("bank_empty", str(bank_open))
    tiers = _tiers(bank_open)
    offered_total = sum(len(suite.get("cases", [])) for _, suite in load_capability_suites(bank_open))
    rows = run_seeded_arm(cases, provider, arm(), seeds, model=model, model_version=model_version, **options)
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_tier[tiers.get(row["id"], "unknown")].append(row)
    return {
        "schema_version": 1, "kind": "general_number",
        "tool": TOOL, "command": command,
        "config": {"general_version": GENERAL_VERSION, "ablation_runner_version": RUNNER_VERSION,
                   "model": model, "model_version": model_version, "arm": arm(),
                   "protocol_id": protocol()["protocol_id"], "seeds": list(seeds),
                   "seed_aggregation": protocol()["seed_aggregation"], "options": dict(options),
                   "bank": {"files": files, "cases_with_automatic_check": len(cases),
                            "cases_without_automatic_check": offered_total - len(cases),
                            "sandbox_cases_included": sandbox}},
        "overall": summarize(rows),
        "by_tier": {tier: summarize(tier_rows) for tier, tier_rows in sorted(by_tier.items())},
        "rows": rows,
        "measurement_limits": LIMITS + ([] if sandbox else ["python_sandbox_cases_excluded_no_check_container"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-version", help="بصمةُ النموذج المتوقَّعة؛ تُقارَن بما يعرضه Ollama")
    parser.add_argument("--bank-open", type=Path, default=ROOT / "evaluation/banks/kimi_v1/open")
    parser.add_argument("--seeds", type=int, default=3, help="عددٌ فردي من البذور (الافتراضي 3؛ تُستخدم 0..N-1)")
    parser.add_argument("--no-sandbox", action="store_true", help="تُستبعد حالاتُ python_sandbox حيث لا حاويةَ فحص")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    command = shlex.join(["python3", TOOL] + list(sys.argv[1:] if argv is None else argv))
    try:
        seeds = seed_values(args.seeds)
    except AblationError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 2
    from providers.ollama import OllamaProvider
    from tools.model_digest import ModelDigestError, pin_model_digest, verify_model_digest
    try:
        model_version = pin_model_digest(args.model, args.model_version)
    except ModelDigestError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 1
    try:
        report = run_general(OllamaProvider(args.model), model=args.model, model_version=model_version,
                             bank_open=args.bank_open, sandbox=not args.no_sandbox, seeds=seeds, command=command)
    except AblationError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 2
    try:
        verify_model_digest(args.model, model_version)
    except ModelDigestError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
