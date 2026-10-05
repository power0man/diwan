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
- **الإعلان:** الأداةُ والأمرُ بالمفسِّر الذي شغّله، والبذور، وبصماتُ ملفّات البنك، وبصمةُ النموذج قبل التشغيل وبعده،
  ورخصةُ المحرّك (`licenses`) وإنفاقُه (`spend`) بشكلَي `tools/model_licenses.py` و`tools/probe_spend.py`.
- **قبل التشغيل:** يُرفض نموذجٌ ليس في `registry/model_licenses.json` أو رخصتُه منتظرة، فلا تنتهي ليلةُ قياسٍ بدليلٍ يردّه CI.
  ويُرفض مزوّدٌ غيرُ محليّ (`core.locality`)، فالرقمُ العام على المحرّك المحليّ المعتمد (ق٥٤، ق٧٠)، وكتلةُ الإنفاق
  `local_no_charge` لا تصدق إلا عليه.
  ويُرفض بنكٌ فيه حالةٌ بلا فحصٍ آليّ (شرطُ v1.2: صفرُ حالةٍ بلا فحص)، فلا يُنشر رقمُ م١ على v1.1 ومقامٍ مُنقَص؛ إلا بـ
  `--diagnostic`، فيُوسم التقريرُ `general_number_diagnostic` ولا يُعدّ دليلَ البوابة.

الحدود: فحصُ `exact` صارمٌ بق٥٧ فلا قراءةَ مشذَّبة؛ والبذورُ بحرارة صفر لا تقيس تباينَ العيّنة (`GREEDY_SEED_LIMIT`)؛
والتقريرُ لا يُكتب فوق ملفٍّ قائم.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from datetime import datetime, timezone
import shlex
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.ablation import (GREEDY_SEED_LIMIT, RUNNER_VERSION, AblationError, arm, protocol,  # noqa: E402
                                 run_seeded_arm, seed_values)
from core.locality import is_cloud_model, is_local_provider  # noqa: E402
from evaluation.capabilities import _checks  # noqa: E402
from evaluation.retrieval_general import wilson  # noqa: E402
from tools.evaluate_ablation import bank_cases  # noqa: E402
from evaluation.ablation import auto_checked  # noqa: E402
from tools import model_licenses as ml  # noqa: E402
from tools.measure_engine import _trim_terminal_punctuation  # noqa: E402
from tools.sample_bank import load_capability_suites  # noqa: E402

TOOL = "tools/evaluate_general.py"
GENERAL_VERSION = 1
LIMITS = [
    "the_number_is_the_open_split_through_the_registered_baseline_arm_not_the_sealed_split",
    "cases_without_an_automatic_check_are_excluded_and_counted_in_config",
    "exact_is_strict_by_q57_and_the_trimmed_reading_is_reported_beside_it_never_instead",
    "the_trimmed_reading_rechecks_saved_answers_and_keeps_the_strict_verdict_for_cases_with_a_python_sandbox_check",
    "errored_cases_are_outside_the_denominator_and_counted_in_error_count_not_failures",
    "wilson_intervals_treat_cases_as_independent_and_ignore_clustering_within_a_suite",
    GREEDY_SEED_LIMIT,
]


class LicenseRefused(RuntimeError):
    def __init__(self, code: str, model: str):
        super().__init__(f"{code}: {model}")
        self.code = code


def registered_license(model: str, registry_path: Path = ml.REGISTRY) -> str:
    """رخصةُ المحرّك كما قُرئت في السجلّ؛ وغيابُه أو انتظارُه رفضٌ مسمًّى قبل أيّ نداء."""
    models = json.loads(Path(registry_path).read_text(encoding="utf-8"))["models"]
    entry = models.get(ml.canonical(model))
    if entry is None:
        raise LicenseRefused("model_not_in_registry", model)
    if "pending" in entry:
        raise LicenseRefused("license_not_read", model)
    return entry["license"]


class _Counted:
    """يمرّر كلَّ شيءٍ إلى المزوّد ويعدّ توكناتِ ردوده عبر البذور كلِّها، لكتلة `spend`."""

    def __init__(self, inner, tally: dict):
        self._inner, self._tally = inner, tally

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def with_seed(self, seed):
        return _Counted(self._inner.with_seed(seed), self._tally)

    def complete(self, request):
        response = self._inner.complete(request)
        self._tally["calls"] += 1
        self._tally["prompt_tokens"] += response.usage.input_tokens
        self._tally["completion_tokens"] += response.usage.output_tokens
        return response


def _lenient_seed(case: dict, attempt: dict) -> bool | None:
    """قراءةُ exact المشذَّبة لبذرةٍ واحدة (ق٥٧): الفحوصُ نفسُها على الجواب المحفوظ، وexact بلا ترقيمٍ ختاميّ في الطرفين.
    والحالةُ ذاتُ فحص python_sandbox تُبقي حكمَها الصارم، فلا يُعاد تشغيلُ الحاوية لقراءةٍ تفسيرية."""
    if attempt.get("status") != "measured":
        return None
    if attempt["passed"] is True or any(c["kind"] == "python_sandbox" for c in case["checks"]):
        return attempt["passed"] is True
    answer = attempt["answer"]
    for check in case["checks"]:
        if check["kind"] == "exact":
            if _trim_terminal_punctuation(answer) != _trim_terminal_punctuation(check["value"]):
                return False
        elif not _checks(answer, [check])[0]["passed"]:
            return False
    return True


def exact_readings(rows: list[dict], cases: list[dict]) -> dict:
    """الرقمان معًا (ق٥٧): الصارمُ هو الرقم، والمشذَّبُ بجانبه يفصل فارقَ الصيغة عن فارق المعرفة، بأغلبية البذور نفسِها."""
    by_id = {case["case_id"]: case for case in cases}
    strict = lenient = recovered = 0
    measured = [row for row in rows if row["status"] == "measured"]
    for row in measured:
        votes = [_lenient_seed(by_id[row["id"]], attempt) for attempt in row["seed_results"]]
        passed = sum(v is True for v in votes) >= row["majority_threshold"]
        strict += row["passed"] is True
        lenient += passed
        recovered += passed and row["passed"] is not True
    n = len(measured)
    reading = lambda k: {"passes": k, "pass_rate": round(k / n, 4) if n else None,
                         "wilson95": wilson(k, n) if n else None}
    return {"strict": reading(strict), "lenient": reading(lenient), "lost_to_trailing_punctuation": recovered}


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
    # بلا مقيسٍ لا رقمَ ولا فاصل: wilson(0, 0) صفرٌ عريضُه صفر فيُقرأ رسوبًا تامًّا لا انقطاعًا (ملاحظة Codex على #312)
    return {"offered": len(rows), "measured": len(measured), "passes": passes,
            "error_count": len(rows) - len(measured),
            "pass_rate": round(passes / len(measured), 4) if measured else None,
            "wilson95": wilson(passes, len(measured)) if measured else None}


def run_general(provider, *, model: str, model_version: str, bank_open: Path, engine_license: str, date: str,
                sandbox: bool = True, seeds: tuple[int, ...] | None = None, command: str = "",
                diagnostic: bool = False, **options) -> dict:
    seeds = seed_values() if seeds is None else tuple(seeds)
    if seeds != seed_values(len(seeds)):
        raise AblationError("seeds_invalid", "يلزم تسلسل 0..N-1 بعدد فردي لا يقل عن 3")
    # نموذجٌ سحابيّ عبر Ollama المحليّ يُرسل كلَّ حالةٍ إلى السحابة، والكتلةُ تقول local_no_charge: يُرفض قبل أيّ نداء
    # (ملاحظة Codex على #312). والرقمُ العام على المحرّك المحليّ وحده (ق٥٤، ق٧٠).
    if is_cloud_model(model) or not is_local_provider(provider):
        raise AblationError("provider_not_local", model)
    cases, files = bank_cases(bank_open, sample_target=None, salt="general", sandbox=sandbox)
    if not cases:
        raise AblationError("bank_empty", str(bank_open))
    tiers = _tiers(bank_open)
    every = [case for _, suite in load_capability_suites(bank_open) for case in suite.get("cases", [])]
    # حالةٌ بلا فحصٍ آليّ غيرُ حالةٍ فحصُها في حاويةٍ غائبة: تُعدّان منفصلتين (ملاحظة Codex على #312)
    unchecked = sum(not auto_checked(case, sandbox=True) for case in every)
    # بنكٌ فيه حالةٌ بلا فحصٍ آليّ ليس v1.2 المستلَم: يُرفض قبل أيّ نداء، لا يُنقَص مقامُه صامتًا (ملاحظة Codex على #312)
    if unchecked and not diagnostic:
        raise AblationError("bank_has_unchecked_cases", f"{unchecked} حالةً بلا فحصٍ آليّ؛ يلزم v1.2 أو --diagnostic")
    tally = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    rows = run_seeded_arm(cases, _Counted(provider, tally), arm(), seeds, model=model, model_version=model_version,
                          **options)
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_tier[tiers.get(row["id"], "unknown")].append(row)
    return {
        "schema_version": 1, "kind": "general_number_diagnostic" if diagnostic else "general_number", "date": date,
        "tool": TOOL, "command": command, "licenses": {ml.canonical(model): engine_license},
        "spend": {"cloud_calls": 0, "prompt_tokens": tally["prompt_tokens"],
                  "completion_tokens": tally["completion_tokens"], "cost_usd": 0, "cost_basis": "local_no_charge"},
        "config": {"general_version": GENERAL_VERSION, "ablation_runner_version": RUNNER_VERSION,
                   "model": model, "model_version": model_version, "arm": arm(),
                   "protocol_id": protocol()["protocol_id"], "seeds": list(seeds),
                   "seed_aggregation": protocol()["seed_aggregation"], "options": dict(options),
                   "engine_calls": tally["calls"],
                   "bank": {"files": files, "cases_measured": len(cases),
                            "cases_without_automatic_check": unchecked,
                            "sandbox_cases_excluded": len(every) - unchecked - len(cases),
                            "sandbox_cases_included": sandbox}},
        "overall": summarize(rows),
        "exact_readings": exact_readings(rows, cases),
        "by_tier": {tier: summarize(tier_rows) for tier, tier_rows in sorted(by_tier.items())},
        "rows": rows,
        "measurement_limits": LIMITS + ([] if sandbox else ["python_sandbox_cases_excluded_no_check_container"])
                              + (["diagnostic_run_on_a_bank_with_unchecked_cases_not_m1_gate_evidence"] if diagnostic else []),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-version", help="بصمةُ النموذج المتوقَّعة؛ تُقارَن بما يعرضه Ollama")
    parser.add_argument("--bank-open", type=Path, default=ROOT / "evaluation/banks/kimi_v1/open")
    parser.add_argument("--seeds", type=int, default=3, help="عددٌ فردي من البذور (الافتراضي 3؛ تُستخدم 0..N-1)")
    parser.add_argument("--no-sandbox", action="store_true", help="تُستبعد حالاتُ python_sandbox حيث لا حاويةَ فحص")
    parser.add_argument("--diagnostic", action="store_true",
                        help="يُقبل بنكٌ فيه حالاتٌ بلا فحص، ويُوسم التقريرُ تشخيصيًّا لا دليلَ بوابة م١")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    # المفسِّرُ الذي شغّل لا اسمٌ مثبَّت (AGENTS.md §٥، ملاحظة Codex على #312)
    command = shlex.join([sys.executable, TOOL] + list(sys.argv[1:] if argv is None else argv))
    try:
        seeds = seed_values(args.seeds)
    except AblationError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 2
    try:
        engine_license = registered_license(args.model)
    except LicenseRefused as exc:
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
                             bank_open=args.bank_open, engine_license=engine_license,
                             date=datetime.now(timezone.utc).date().isoformat(), sandbox=not args.no_sandbox,
                             seeds=seeds, command=command, diagnostic=args.diagnostic)
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
